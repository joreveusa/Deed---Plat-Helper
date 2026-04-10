"""
import_kmz_locations.py
========================
Parse 1,869 KMZ job placemarks from:
    Z:\\06 Google Earth Placemarks\\Logged -- do not delete\\
Extract: job_number, client_name, lat, lon
Add location data to KG job nodes.

6 workers (I/O-bound, Z: drive).

Run:
    python scripts/import_kmz_locations.py
"""

import json
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KMZ_DIR  = Path(r"Z:\06 Google Earth Placemarks\Logged -- do not delete")
OUT_DIR  = ROOT / "data" / "ai" / "training_data"
OUT_FILE = OUT_DIR / "kmz_job_locations.json"

MAX_WORKERS = 6

# KML namespace
KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


# ── Filename parser ──────────────────────────────────────────────────────────

def _parse_filename(stem: str) -> tuple[str, str]:
    """
    Extract job_number and client_name from KMZ filename stem.
    Examples:
      '1234 Garcia'          → ('1234', 'Garcia')
      '2056-02-TR3-B-LS'     → ('2056', '')
      '179 Blea'             → ('179', 'Blea')
      '1741 JacobsSmith'     → ('1741', 'JacobsSmith')
    """
    m = re.match(r"^(\d{3,4})(?:[.\-](\d+))?(?:\s+(.+))?$", stem.strip())
    if m:
        job_num    = m.group(1)
        client_raw = (m.group(3) or "").strip()
        # Strip type suffixes like ILR, BDY, TPG, etc.
        client = re.sub(
            r"\b(ILR|BDY|TPG|LLA|FNF|EAS|SUB|SKT|STK|CST|POL|CNS|AB|LS|TS|T[0-9]?)\b",
            "", client_raw, flags=re.IGNORECASE
        ).strip(" .-")
        return job_num, client
    return "", ""


# ── KMZ coordinate extractor ─────────────────────────────────────────────────

def _extract_coords(kmz_path: Path) -> tuple[float, float] | tuple[None, None]:
    """Open KMZ (zip), parse doc.kml, return (lat, lon) of first Placemark.

    Google Earth KMZs store the camera view in <LookAt> with explicit
    <latitude>/<longitude> children — not in <coordinates>. We try LookAt
    first, then fall back to Point/coordinates for older formats.
    """
    try:
        with zipfile.ZipFile(kmz_path, "r") as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                return None, None
            kml_text = zf.read(kml_names[0]).decode("utf-8", errors="replace")

        root = ET.fromstring(kml_text)
        ns   = "http://www.opengis.net/kml/2.2"

        def _find_el(tag: str):
            """Find element by tag with or without KML namespace."""
            el = root.find(f".//{{{ns}}}{tag}")
            if el is None:
                el = root.find(f".//{tag}")
            return el

        def _find_text(tag: str):
            el = _find_el(tag)
            if el is not None and el.text:
                return el.text.strip()
            return None

        # Strategy 1: <LookAt><latitude>/<longitude> (Google Earth standard)
        lat_txt = _find_text("latitude")
        lon_txt = _find_text("longitude")
        if lat_txt and lon_txt:
            lat, lon = float(lat_txt), float(lon_txt)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return round(lat, 6), round(lon, 6)

        # Strategy 2: <Point><coordinates>lon,lat,alt</coordinates>
        coords_el = _find_el(f"{{{ns}}}Point/{{{ns}}}coordinates")
        if coords_el is None:
            coords_el = _find_el("Point/coordinates")
        if coords_el is None:
            coords_el = _find_el(f"{{{ns}}}coordinates")
        if coords_el is None:
            coords_el = _find_el("coordinates")
        if coords_el is not None and coords_el.text:
            parts = coords_el.text.strip().split(",")
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                if -180 <= lon <= 180 and -90 <= lat <= 90:
                    return round(lat, 6), round(lon, 6)

    except (zipfile.BadZipFile, ET.ParseError, ValueError):
        pass
    except Exception:
        pass

    return None, None


# ── Per-file worker ──────────────────────────────────────────────────────────

def _process_kmz(kmz_path: Path) -> dict | None:
    job_num, client = _parse_filename(kmz_path.stem)
    if not job_num:
        return None

    lat, lon = _extract_coords(kmz_path)

    return {
        "job_number":   job_num,
        "client_name":  client,
        "lat":          lat,
        "lon":          lon,
        "kmz_filename": kmz_path.name,
        "source":       "kmz_placemark",
    }


# ── KG injection ─────────────────────────────────────────────────────────────

def _inject_into_kg(records: list[dict]) -> dict:
    try:
        from ai import get_knowledge_graph
        kg = get_knowledge_graph()
        if not kg:
            return {"available": False}

        updated  = 0
        created  = 0
        persons  = 0
        edges    = 0

        for rec in records:
            job_num = rec["job_number"]
            client  = rec.get("client_name", "")
            lat     = rec.get("lat")
            lon     = rec.get("lon")

            job_id = f"job_{job_num}"
            loc_data = {k: v for k, v in {
                "lat": lat, "lon": lon, "source": "kmz"
            }.items() if v is not None}

            if kg.G.has_node(job_id):
                kg.G.nodes[job_id].update(loc_data)
                updated += 1
            else:
                kg.G.add_node(job_id, type="job",
                              job_number=job_num, **loc_data)
                created += 1

            if client and len(client) >= 2:
                person_id = f"person_{client.lower().replace(' ', '_')}"
                if not kg.G.has_node(person_id):
                    kg.G.add_node(person_id, type="person", name=client)
                    persons += 1
                if not kg.G.has_edge(person_id, job_id):
                    kg.G.add_edge(person_id, job_id, relation="client_of")
                    edges += 1

        kg.save()
        logger.success(
            f"[kmz] KG: {updated} jobs updated, +{created} new jobs, "
            f"+{persons} persons, +{edges} edges"
        )
        return {"updated": updated, "created": created,
                "persons": persons, "edges": edges}

    except Exception as e:
        logger.warning(f"[kmz] KG inject failed: {e}")
        return {"error": str(e)}


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    t0 = time.time()

    if not KMZ_DIR.exists():
        return {"success": False, "error": f"KMZ dir not found: {KMZ_DIR}"}

    kmz_files = list(KMZ_DIR.glob("*.kmz"))
    logger.info(f"[kmz] Found {len(kmz_files)} KMZ files — processing with {MAX_WORKERS} workers")

    records   = []
    skipped   = 0
    no_coords = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_process_kmz, f): f for f in kmz_files}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 200 == 0:
                logger.info(f"[kmz]   {done}/{len(kmz_files)} processed ...")
            try:
                rec = future.result()
                if rec is None:
                    skipped += 1
                else:
                    if rec["lat"] is None:
                        no_coords += 1
                    records.append(rec)
            except Exception as e:
                logger.warning(f"[kmz] Worker error: {e}")
                skipped += 1

    logger.info(
        f"[kmz] Parsed: {len(records)} records, {no_coords} missing coords, "
        f"{skipped} skipped"
    )

    # Write output
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    export = {
        "exported_at":  datetime.now().isoformat(),
        "source":       str(KMZ_DIR),
        "total_files":  len(kmz_files),
        "total_records": len(records),
        "no_coords":    no_coords,
        "skipped":      skipped,
        "records":      records,
    }
    OUT_FILE.write_text(json.dumps(export, indent=2), encoding="utf-8")
    logger.info(f"[kmz] Wrote {OUT_FILE.name}")

    # KG injection
    kg_result = _inject_into_kg(records)

    elapsed = round(time.time() - t0, 1)
    logger.success(f"[kmz] Done in {elapsed}s")

    return {
        "success":        True,
        "total_files":    len(kmz_files),
        "parsed":         len(records),
        "no_coords":      no_coords,
        "skipped":        skipped,
        "elapsed_seconds": elapsed,
        "output_file":    str(OUT_FILE),
        "kg":             kg_result,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
