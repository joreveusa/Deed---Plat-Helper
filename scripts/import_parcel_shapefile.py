"""
import_parcel_shapefile.py
==========================
Read Taos County parcel shapefile → extract owner names + acreage + parcel IDs.
Fuzzy-match owner names against KG person nodes.
Add parcel nodes linked to matched/new person nodes.

4 workers for fuzzy name matching.

Run:
    python scripts/import_parcel_shapefile.py
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime

from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SHP_PATH = Path(
    r"Z:\07 GIS\Spatial Data\Taos County Parcel DATA"
    r"\PARCEL MAIN 20241025 SHAPE FILE\Parcel_Main_20241025.shp"
)
OUT_DIR  = ROOT / "data" / "ai" / "training_data"
OUT_FILE = OUT_DIR / "parcel_shapefile.json"

MAX_WORKERS      = 4
FUZZY_THRESHOLD  = 0.82   # Jaro-Winkler score for name match


# ── Shapefile reader ──────────────────────────────────────────────────────────

def _read_shapefile() -> list[dict]:
    """Read all records from the shapefile DBF using pyshp."""
    import shapefile

    logger.info(f"[parcel] Reading {SHP_PATH.name} ...")
    sf = shapefile.Reader(str(SHP_PATH))
    fields = [f[0] for f in sf.fields[1:]]   # skip DeletionFlag
    logger.info(f"[parcel] Fields: {fields}")

    # Map field names to canonical names
    field_lower = [f.lower() for f in fields]

    def _find(candidates: list[str]) -> int | None:
        for c in candidates:
            if c in field_lower:
                return field_lower.index(c)
        return None

    idx_owner    = _find(["owner", "owner_name", "ownername", "grantee", "name"])
    idx_parcel   = _find(["parcel", "parcelid", "parcel_id", "pid", "account", "acct"])
    idx_acreage  = _find(["acres", "acreage", "gis_acres", "area_acres", "calc_acres"])
    idx_address  = _find(["situs", "address", "site_addr", "location", "situsaddr"])
    idx_legal    = _find(["legal", "legal_desc", "legaldesc", "description"])

    logger.info(
        f"[parcel] Mapped: owner={idx_owner}, parcel={idx_parcel}, "
        f"acres={idx_acreage}, addr={idx_address}, legal={idx_legal}"
    )

    records = []
    for shaperec in sf.iterShapeRecords():
        vals = shaperec.record

        def _v(idx):
            if idx is None or idx >= len(vals):
                return ""
            v = vals[idx]
            return str(v).strip() if v is not None else ""

        owner   = _v(idx_owner)
        parcel  = _v(idx_parcel)
        acreage = _v(idx_acreage)
        address = _v(idx_address)
        legal   = _v(idx_legal)

        # Centroid from shape
        lat = lon = None
        try:
            pts = shaperec.shape.points
            if pts:
                lon = round(sum(p[0] for p in pts) / len(pts), 6)
                lat = round(sum(p[1] for p in pts) / len(pts), 6)
        except Exception:
            pass

        if not owner or len(owner) < 2:
            continue

        records.append({
            "owner_name":  owner,
            "parcel_id":   parcel,
            "acreage":     acreage,
            "address":     address,
            "legal":       legal[:200] if legal else "",
            "lat":         lat,
            "lon":         lon,
            "source":      "tc_parcel_shapefile_2024",
        })

    logger.info(f"[parcel] Read {len(records)} parcel records")
    return records


# ── Name normalizer ───────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Normalize owner name for matching: uppercase, strip punctuation."""
    return re.sub(r"[^A-Z0-9 ]", "", name.upper()).strip()


# ── Fuzzy match batch (runs in thread pool) ───────────────────────────────────

def _match_batch(
    batch: list[dict],
    kg_persons: dict[str, str],   # {person_id: normalized_name}
) -> list[tuple[dict, str | None]]:
    """
    For each parcel record, find best matching KG person node.
    Returns list of (record, matched_person_id or None).
    """
    try:
        from jellyfish import jaro_winkler_similarity as jw
    except ImportError:
        # Fallback: simple token overlap ratio
        def jw(a, b):
            a_set = set(a.split())
            b_set = set(b.split())
            if not a_set or not b_set:
                return 0.0
            return len(a_set & b_set) / max(len(a_set), len(b_set))

    results = []
    for rec in batch:
        norm_owner = _normalize_name(rec["owner_name"])
        best_id    = None
        best_score = 0.0

        for person_id, norm_person in kg_persons.items():
            score = jw(norm_owner, norm_person)
            if score > best_score:
                best_score = score
                best_id    = person_id

        if best_score >= FUZZY_THRESHOLD:
            results.append((rec, best_id))
        else:
            results.append((rec, None))  # No match — will create new node

    return results


# ── KG injection ──────────────────────────────────────────────────────────────

def _inject_into_kg(records: list[dict]) -> dict:
    try:
        from ai import get_knowledge_graph
        kg = get_knowledge_graph()
        if not kg:
            return {"available": False}

        # Build lookup of existing person nodes (normalized name → node_id)
        kg_persons = {}
        for node_id, data in kg.G.nodes(data=True):
            if data.get("type") == "person":
                raw_name = data.get("name", node_id)
                kg_persons[node_id] = _normalize_name(raw_name)

        logger.info(f"[parcel] {len(kg_persons)} existing KG person nodes to match against")

        # Split records into batches for parallel fuzzy matching
        batch_size = max(1, len(records) // MAX_WORKERS)
        batches = [
            records[i:i + batch_size]
            for i in range(0, len(records), batch_size)
        ]

        all_matched = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(_match_batch, b, kg_persons) for b in batches]
            for f in futures:
                all_matched.extend(f.result())

        # Now inject into KG
        matched   = 0
        new_nodes = 0
        parcels   = 0
        edges     = 0

        for i, (rec, person_id) in enumerate(all_matched):
            parcel_id = f"parcel_{rec['parcel_id'] or i}"

            # Person node
            if person_id:
                matched += 1
                # Enrich existing node with address/acreage if missing
                node = kg.G.nodes[person_id]
                if not node.get("address") and rec.get("address"):
                    node["address"] = rec["address"]
                if not node.get("acreage") and rec.get("acreage"):
                    node["acreage"] = rec["acreage"]
            else:
                # Create new person node from parcel owner
                person_id = f"person_{_normalize_name(rec['owner_name']).replace(' ', '_')}"
                if not kg.G.has_node(person_id):
                    kg.G.add_node(person_id, type="person",
                                  name=rec["owner_name"],
                                  address=rec.get("address", ""),
                                  source="parcel_shapefile")
                    new_nodes += 1

            # Parcel node
            if not kg.G.has_node(parcel_id):
                kg.G.add_node(parcel_id, type="parcel",
                              parcel_id=rec["parcel_id"],
                              acreage=rec.get("acreage"),
                              address=rec.get("address"),
                              legal=rec.get("legal"),
                              lat=rec.get("lat"),
                              lon=rec.get("lon"),
                              source="tc_parcel_shapefile_2024")
                parcels += 1

            # Link person → parcel
            if not kg.G.has_edge(person_id, parcel_id):
                kg.G.add_edge(person_id, parcel_id, relation="owns_parcel")
                edges += 1

        kg.save()
        logger.success(
            f"[parcel] KG: {matched} matched, +{new_nodes} new persons, "
            f"+{parcels} parcels, +{edges} edges"
        )
        return {
            "matched_persons": matched,
            "new_persons":     new_nodes,
            "parcels_added":   parcels,
            "edges_added":     edges,
        }

    except Exception as e:
        logger.error(f"[parcel] KG injection failed: {e}")
        return {"error": str(e)}


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    t0 = time.time()

    if not SHP_PATH.exists():
        return {"success": False, "error": f"Shapefile not found: {SHP_PATH}"}

    records = _read_shapefile()
    if not records:
        return {"success": False, "error": "No records read from shapefile"}

    # Write JSON snapshot
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    export = {
        "exported_at":   datetime.now().isoformat(),
        "source":        str(SHP_PATH),
        "total_records": len(records),
        "records":       records,
    }
    OUT_FILE.write_text(json.dumps(export, indent=2, default=str), encoding="utf-8")
    logger.info(f"[parcel] Wrote {OUT_FILE.name}")

    kg_result = _inject_into_kg(records)
    elapsed   = round(time.time() - t0, 1)

    logger.success(f"[parcel] Done in {elapsed}s — {len(records)} parcels processed")

    return {
        "success":        True,
        "total_records":  len(records),
        "elapsed_seconds": elapsed,
        "output_file":    str(OUT_FILE),
        "kg":             kg_result,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
