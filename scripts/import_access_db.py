"""
import_access_db.py
==================
Export Red Tail Main Database (Access .accdb) → JSON training data.

Single-threaded (COM/Access limitation). Discovers all tables automatically.
Writes to: data/ai/training_data/access_db_export.json
Also injects directly into the KG via populate_from_jobs().

Run:
    python scripts/import_access_db.py
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

from loguru import logger

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB_PATH = Path(r"Z:\02 Red Tail Database\Red Tail Main Database COPY 06 Mar 2024.accdb")
OUT_DIR  = ROOT / "data" / "ai" / "training_data"
OUT_FILE = OUT_DIR / "access_db_export.json"

# Fields we care about — mapped from common Access column name variants
FIELD_MAP = {
    # Job identifier
    "job_number":  ["job number", "job_number", "jobnumber", "job no", "job#", "jobno"],
    "job_type":    ["job type", "job_type", "jobtype", "type"],
    "client_name": ["client name", "client_name", "clientname", "client", "owner", "grantee"],
    "location":    ["location", "loc", "area", "address", "situs"],
    "acreage":     ["acreage", "acres", "area acres", "size"],
    "section":     ["section", "sec"],
    "township":    ["township", "twp", "t"],
    "range":       ["range", "rng", "r"],
    "subdivision": ["subdivision", "subdiv", "sub", "plat"],
    "date":        ["date", "survey date", "job date", "date completed"],
    "surveyor":    ["surveyor", "surveyed by", "by"],
    "notes":       ["notes", "remarks", "comments", "description"],
}


def _connect(db_path: Path):
    """Connect to Access DB via pyodbc + Microsoft ACE OLEDB driver."""
    import pyodbc
    conn_str = (
        r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
        f"DBQ={db_path};"
        r"ExtendedAnsiSQL=1;"
    )
    try:
        return pyodbc.connect(conn_str, timeout=10)
    except pyodbc.Error as e:
        # Try ACE 16.0 driver if 15.0 not found
        conn_str2 = (
            r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb, *.mdb)};"
            f"DBQ={db_path};"
        )
        try:
            return pyodbc.connect(conn_str2, timeout=10)
        except pyodbc.Error:
            raise RuntimeError(
                f"Cannot connect to Access DB. Install Microsoft Access Database Engine:\n"
                f"https://www.microsoft.com/en-us/download/details.aspx?id=54920\n"
                f"Original error: {e}"
            )


def _normalize_col(col_name: str) -> str | None:
    """Map raw column name → our canonical field name."""
    c = col_name.lower().strip()
    for canon, variants in FIELD_MAP.items():
        if c in variants or c == canon:
            return canon
    return None


def _list_tables(cursor) -> list[str]:
    """Return all user table names from the Access DB."""
    tables = []
    for row in cursor.tables(tableType="TABLE"):
        name = row.table_name
        if not name.startswith("~") and not name.startswith("MSys"):
            tables.append(name)
    return tables


def _export_table(cursor, table: str) -> list[dict]:
    """Export all rows from a table, normalizing column names."""
    cursor.execute(f"SELECT * FROM [{table}]")
    cols = [desc[0] for desc in cursor.description]

    # Build col→canonical mapping
    col_map = {}
    for col in cols:
        canon = _normalize_col(col)
        if canon:
            col_map[col] = canon

    rows = []
    for row in cursor.fetchall():
        rec = {"_source_table": table}
        for col, val in zip(cols, row):
            canon = col_map.get(col, col.lower().replace(" ", "_"))
            if val is not None:
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                rec[canon] = str(val).strip() if isinstance(val, str) else val
        rows.append(rec)
    return rows


def run() -> dict:
    logger.info(f"[access_db] Connecting to {DB_PATH.name} ...")
    t0 = time.time()

    if not DB_PATH.exists():
        return {"success": False, "error": f"DB not found: {DB_PATH}"}

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        conn = _connect(DB_PATH)
    except RuntimeError as e:
        logger.error(str(e))
        return {"success": False, "error": str(e)}

    cursor = conn.cursor()
    tables = _list_tables(cursor)
    logger.info(f"[access_db] Found {len(tables)} tables: {tables}")

    all_records = []
    table_stats = {}

    for table in tables:
        try:
            rows = _export_table(cursor, table)
            table_stats[table] = len(rows)
            all_records.extend(rows)
            logger.info(f"[access_db]   {table}: {len(rows)} rows")
        except Exception as e:
            logger.warning(f"[access_db]   {table}: SKIPPED — {e}")
            table_stats[table] = 0

    conn.close()

    # Write output
    export = {
        "exported_at": datetime.now().isoformat(),
        "source": str(DB_PATH),
        "table_stats": table_stats,
        "total_records": len(all_records),
        "records": all_records,
    }
    OUT_FILE.write_text(json.dumps(export, indent=2, default=str), encoding="utf-8")
    elapsed = round(time.time() - t0, 1)

    logger.success(
        f"[access_db] Done: {len(all_records)} records from {len(tables)} tables "
        f"→ {OUT_FILE.name} ({elapsed}s)"
    )

    # ── Inject into KG ──────────────────────────────────────────────────────
    kg_result = _inject_into_kg(all_records)

    return {
        "success": True,
        "total_records": len(all_records),
        "table_stats": table_stats,
        "output_file": str(OUT_FILE),
        "elapsed_seconds": elapsed,
        "kg": kg_result,
    }


def _inject_into_kg(records: list[dict]) -> dict:
    """Push extracted records into the Survey Knowledge Graph."""
    try:
        from ai import get_knowledge_graph
        kg = get_knowledge_graph()
        if not kg:
            return {"success": False, "reason": "KG not available"}

        stats = {
            "rts_plats":    0,
            "rgss_plats":   0,
            "other_plats":  0,
            "subdivisions": 0,
            "surveyors":    0,
            "contacts":     0,
            "edges":        0,
        }

        # Group records by source table
        by_table: dict[str, list[dict]] = {}
        for rec in records:
            t = rec.get("_source_table", "")
            by_table.setdefault(t, []).append(rec)

        # ── 1. Plats of Red Tail Surveying ───────────────────────────────────
        # Cols: project_#, title_of_plat, date_of_plat, cabinet, pages,
        #       section, township, range, acreage, location
        for rec in by_table.get("Plats of Red Tail Surveying", []):
            proj    = str(rec.get("project_#", "")).strip()
            title   = str(rec.get("title_of_plat", "")).strip()
            cabinet = str(rec.get("cabinet", "")).strip()
            pages   = str(rec.get("pages", "")).strip()
            acreage = str(rec.get("acreage", "")).strip()
            section = str(rec.get("section", "")).strip()
            twp     = str(rec.get("township", "")).strip()
            rng     = str(rec.get("range", "")).strip()
            date    = str(rec.get("date_of_plat", "")).strip()

            if not proj or not title:
                continue

            # job node
            job_id = f"job_{proj.replace('.', '_')}"
            if not kg.G.has_node(job_id):
                kg.G.add_node(job_id, type="job",
                              job_number=proj, title=title,
                              cabinet=cabinet, pages=pages,
                              acreage=acreage, section=section,
                              township=twp, range=rng, date=date,
                              source="rts_plat_index")
                stats["rts_plats"] += 1

            # Extract client name from plat title
            # Titles are usually "Grantor to Grantee" or just "Client Name"
            client = _parse_title_client(title)
            if client:
                _add_person_job_edge(kg, client, job_id,
                                     "client_of", stats)

        # ── 2. Plats of Rio Grande Surveying ─────────────────────────────────
        # Cols: job_#_in_sequence_(no_prefix/suffix), title_of__plat,
        #       date_of_survey, section, township_1, range_1, acreage, location
        for rec in by_table.get("Plats of Rio Grande Surveying", []):
            job_num = rec.get("job_#_in_sequence_(no_prefix/suffix)")
            title   = str(rec.get("title_of__plat", "")).strip()
            acreage = str(rec.get("acreage", "")).strip()
            section = str(rec.get("section", "")).strip()
            twp     = str(rec.get("township_1", "")).strip()
            rng     = str(rec.get("range_1", "")).strip()
            date    = str(rec.get("date_of_survey", "")).strip()

            if not title:
                continue

            job_id = f"rgss_{job_num}" if job_num else f"rgss_title_{hash(title) % 99999}"
            if not kg.G.has_node(job_id):
                kg.G.add_node(job_id, type="job",
                              job_number=str(job_num or ""),
                              title=title, acreage=acreage,
                              section=section, township=twp, range=rng,
                              date=date, source="rgss_plat_index")
                stats["rgss_plats"] += 1

            client = _parse_title_client(title)
            if client:
                _add_person_job_edge(kg, client, job_id,
                                     "client_of_rgss", stats)

        # ── 3. Plats of Other Surveyors ───────────────────────────────────────
        # Cols: title_of__plat (owner name), date_of_survey, section,
        #       township, range, acreage, red_tail_project_reference_a (RTS job#)
        for rec in by_table.get("Plats of Other Surveyors", []):
            owner   = str(rec.get("title_of__plat", "")).strip()
            acreage = str(rec.get("acreage", "")).strip()
            section = str(rec.get("section", "")).strip()
            twp     = str(rec.get("township", "")).strip()
            rng     = str(rec.get("range", "")).strip()
            rts_ref = str(rec.get("red_tail_project_reference_a", "")).strip()
            rts_id  = str(rec.get("rts_id#", "")).strip()
            date    = str(rec.get("date_of_survey", "")).strip()

            if not owner or len(owner) < 3:
                continue

            plat_id   = f"other_plat_{rts_id or hash(owner + section) % 99999}"
            person_id = f"person_{owner.lower().replace(' ', '_')[:50]}"

            if not kg.G.has_node(person_id):
                kg.G.add_node(person_id, type="person", name=owner,
                              source="other_surveyor_plat")

            if not kg.G.has_node(plat_id):
                kg.G.add_node(plat_id, type="plat",
                              owner=owner, acreage=acreage,
                              section=section, township=twp, range=rng,
                              date=date, rts_reference=rts_ref,
                              source="other_surveyor_plat_index")
                stats["other_plats"] += 1

            if not kg.G.has_edge(person_id, plat_id):
                kg.G.add_edge(person_id, plat_id, relation="owns_plat")
                stats["edges"] += 1

            # If there's an RTS job reference, link person → RTS job too
            if rts_ref and rts_ref not in ("0", ""):
                rts_job_id = f"job_{rts_ref.replace('.', '_')}"
                if kg.G.has_node(rts_job_id):
                    if not kg.G.has_edge(person_id, rts_job_id):
                        kg.G.add_edge(person_id, rts_job_id,
                                      relation="adjoiner_in")
                        stats["edges"] += 1

        # ── 4. Subdivisions ───────────────────────────────────────────────────
        # Cols: subdivision_title, date_of_survey, section_1, township_1,
        #       range_1, acreage, filed_in_cabinet:, filed_in_page:
        for rec in by_table.get("Subdivisions", []):
            title   = str(rec.get("subdivision_title", "")).strip()
            cabinet = str(rec.get("filed_in_cabinet:", "")).strip()
            page    = str(rec.get("filed_in_page:", "")).strip()
            acreage = str(rec.get("acreage", "")).strip()
            section = str(rec.get("section_1", "")).strip()
            twp     = str(rec.get("township_1", "")).strip()
            rng     = str(rec.get("range_1", "")).strip()
            sid     = str(rec.get("subdivision_id_number", "")).strip()

            if not title:
                continue

            sub_id = f"subdiv_{sid or hash(title) % 99999}"
            if not kg.G.has_node(sub_id):
                kg.G.add_node(sub_id, type="subdivision",
                              name=title, cabinet=cabinet, page=page,
                              acreage=acreage, section=section,
                              township=twp, range=rng,
                              source="subdivision_index")
                stats["subdivisions"] += 1

            # Subdivisions often have "Owner to Buyer" in title — extract
            client = _parse_title_client(title)
            if client:
                _add_person_job_edge(kg, client, sub_id,
                                     "owner_in_subdivision", stats)

        # ── 5. Surveyors ──────────────────────────────────────────────────────
        # Cols: first_name, last_name, company, nmps_#, town_and_state
        for rec in by_table.get("Surveyors", []):
            first   = str(rec.get("first_name", "")).strip()
            last    = str(rec.get("last_name", "")).strip()
            company = str(rec.get("company", "")).strip()
            nmps    = str(rec.get("nmps_#", "")).strip()
            town    = str(rec.get("town_and_state", "")).strip()

            if not last and not company:
                continue

            full_name = f"{last}, {first}".strip(", ") if last else company
            sid_key   = f"surveyor_{full_name.lower().replace(' ', '_')[:50]}"

            if not kg.G.has_node(sid_key):
                kg.G.add_node(sid_key, type="surveyor",
                              name=full_name, company=company,
                              nmps=nmps, location=town,
                              source="surveyors_table")
                stats["surveyors"] += 1

        # ── 6. Contacts + Customer addresses ─────────────────────────────────
        for table in ("Contacts", "Customer addresses 99-2001"):
            for rec in by_table.get(table, []):
                first = str(rec.get("first_name", "")).strip()
                last  = str(rec.get("last_name", "")).strip()
                biz   = str(rec.get("business_name", "")).strip()
                addr  = str(rec.get("location", "")).strip()
                city  = str(rec.get("city", "")).strip()

                if not last and not biz:
                    continue

                full_name = f"{last}, {first}".strip(", ") if last else biz
                cid       = f"contact_{full_name.lower().replace(' ', '_')[:50]}"

                if not kg.G.has_node(cid):
                    kg.G.add_node(cid, type="person",
                                  name=full_name, address=addr,
                                  city=city, source=table.lower())
                    stats["contacts"] += 1

        kg.save()
        total_new = sum(stats.values())
        logger.success(
            f"[access_db] KG injection complete — "
            f"rts_plats={stats['rts_plats']}, rgss_plats={stats['rgss_plats']}, "
            f"other_plats={stats['other_plats']}, subdivisions={stats['subdivisions']}, "
            f"surveyors={stats['surveyors']}, contacts={stats['contacts']}, "
            f"edges={stats['edges']} | total new={total_new}"
        )
        return {"success": True, **stats, "total_new": total_new}

    except Exception as e:
        logger.error(f"[access_db] KG injection failed: {e}")
        import traceback; logger.debug(traceback.format_exc())
        return {"success": False, "reason": str(e)}


def _parse_title_client(title: str) -> str:
    """
    Extract the primary client/owner name from a plat title.
    Most titles are 'Grantor to Grantee' or just 'Owner Name'.
    We take the LAST name segment (usually the new owner/client).
    """
    import re
    title = title.strip()
    # Strip common suffixes
    title = re.sub(
        r"\s+(survey|plat|subdivision|sub|boundary|topo|sketch|ilr|"
        r"easement|lot split|lot cons|condo).*$",
        "", title, flags=re.IGNORECASE
    ).strip()
    # Split on " to " — take last segment (grantee)
    parts = re.split(r"\s+to\s+", title, flags=re.IGNORECASE)
    client = parts[-1].strip()
    # Must be ≥4 chars and look like a name
    if len(client) >= 4 and re.search(r"[A-Za-z]{3}", client):
        return client
    return ""


def _add_person_job_edge(kg, name: str, job_id: str,
                          relation: str, stats: dict) -> None:
    """Add a person node (if new) and an edge to job_id."""
    person_id = f"person_{name.lower().replace(' ', '_')[:60]}"
    if not kg.G.has_node(person_id):
        kg.G.add_node(person_id, type="person", name=name, source="access_db")
    if not kg.G.has_edge(person_id, job_id):
        kg.G.add_edge(person_id, job_id, relation=relation)
        stats["edges"] += 1


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
