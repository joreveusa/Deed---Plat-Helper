"""
import_plats_index.py
=====================
Parse Z:\\02 Red Tail Database\\Plats of Other Surveyors1.txt
→ structured JSON + KG injection.

2 workers: one parses, one writes/injects.

Run:
    python scripts/import_plats_index.py
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

SRC_FILE = Path(r"Z:\02 Red Tail Database\Plats of Other Surveyors1.txt")
OUT_DIR  = ROOT / "data" / "ai" / "training_data"
OUT_FILE = OUT_DIR / "plats_other_surveyors.json"


# ── Parser ───────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return " ".join(s.split()).strip()


def _parse_line(line: str) -> dict | None:
    """Parse a fixed-width / tab-delimited row from the plats index."""
    # Skip blank or header lines
    stripped = line.strip()
    if not stripped or len(stripped) < 10:
        return None

    # Try tab-split first (some rows are tab-delimited)
    parts = line.split("\t")
    if len(parts) >= 4:
        rec = {
            "index":       _clean(parts[0]) if len(parts) > 0 else "",
            "cabinet":     _clean(parts[1]) if len(parts) > 1 else "",
            "owner_name":  _clean(parts[2]) if len(parts) > 2 else "",
            "doc_number":  _clean(parts[3]) if len(parts) > 3 else "",
            "section":     _clean(parts[4]) if len(parts) > 4 else "",
            "township":    _clean(parts[5]) if len(parts) > 5 else "",
            "range":       _clean(parts[6]) if len(parts) > 6 else "",
            "acreage":     _clean(parts[9]) if len(parts) > 9 else "",
            "map_ref":     _clean(parts[10]) if len(parts) > 10 else "",
            "notes":       _clean(" ".join(parts[11:])) if len(parts) > 11 else "",
            "source":      "plats_other_surveyors",
        }
    else:
        # Fall back: try to extract key fields with regex
        # Look for owner name (longest text cluster before numbers)
        name_match = re.search(r"([A-Z][a-zA-Z ,\-\']{5,50})", stripped)
        acreage_match = re.search(r"(\d+\.?\d*)\s*acres?", stripped, re.IGNORECASE)
        trs_match = re.search(
            r"(\d{1,2})[,\s]+(\d{2,3}N)[,\s]+([\d]{1,2}E)", stripped, re.IGNORECASE
        )
        rec = {
            "raw":        stripped,
            "owner_name": _clean(name_match.group(1)) if name_match else "",
            "acreage":    acreage_match.group(1) if acreage_match else "",
            "section":    trs_match.group(1) if trs_match else "",
            "township":   trs_match.group(2) if trs_match else "",
            "range":      trs_match.group(3) if trs_match else "",
            "source":     "plats_other_surveyors",
        }

    # Skip if no meaningful name
    if not rec.get("owner_name") or len(rec["owner_name"]) < 3:
        return None

    return rec


def _parse_file() -> list[dict]:
    logger.info(f"[plats_idx] Reading {SRC_FILE.name} ...")
    records = []
    try:
        text = SRC_FILE.read_text(encoding="latin-1", errors="replace")
    except FileNotFoundError:
        logger.error(f"[plats_idx] File not found: {SRC_FILE}")
        return []

    lines = text.splitlines()
    logger.info(f"[plats_idx] {len(lines)} lines to parse")

    for line in lines:
        rec = _parse_line(line)
        if rec:
            records.append(rec)

    logger.info(f"[plats_idx] Parsed {len(records)} valid records")
    return records


def _write_and_inject(records: list[dict]) -> dict:
    """Write JSON output and inject into KG."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    export = {
        "exported_at": datetime.now().isoformat(),
        "source": str(SRC_FILE),
        "total_records": len(records),
        "records": records,
    }
    OUT_FILE.write_text(json.dumps(export, indent=2), encoding="utf-8")
    logger.info(f"[plats_idx] Wrote {OUT_FILE.name}")

    # KG injection
    try:
        from ai import get_knowledge_graph
        kg = get_knowledge_graph()
        if not kg:
            return {"saved": len(records), "kg": "unavailable"}

        added_persons = 0
        added_plats   = 0
        added_edges   = 0

        for i, rec in enumerate(records):
            name = rec.get("owner_name", "").strip()
            if not name or len(name) < 3:
                continue

            person_id = f"person_{name.lower().replace(' ', '_')}"
            plat_id   = f"plat_os_{i}"

            if not kg.G.has_node(person_id):
                kg.G.add_node(person_id, type="person", name=name)
                added_persons += 1

            kg.G.add_node(plat_id, type="plat",
                          doc_number=rec.get("doc_number", ""),
                          cabinet=rec.get("cabinet", ""),
                          acreage=rec.get("acreage", ""),
                          section=rec.get("section", ""),
                          township=rec.get("township", ""),
                          range=rec.get("range", ""),
                          map_ref=rec.get("map_ref", ""),
                          source="other_surveyor")
            added_plats += 1

            if not kg.G.has_edge(person_id, plat_id):
                kg.G.add_edge(person_id, plat_id, relation="plat_owner")
                added_edges += 1

        kg.save()
        logger.success(
            f"[plats_idx] KG: +{added_persons} persons, +{added_plats} plats, "
            f"+{added_edges} edges"
        )
        return {"saved": len(records), "kg_persons": added_persons,
                "kg_plats": added_plats, "kg_edges": added_edges}

    except Exception as e:
        logger.warning(f"[plats_idx] KG injection failed: {e}")
        return {"saved": len(records), "kg_error": str(e)}


def run() -> dict:
    t0 = time.time()

    # 2 workers: parse on thread 1, write+inject on thread 2 (sequential — parse first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_parse = pool.submit(_parse_file)
        records = future_parse.result()

        if not records:
            return {"success": False, "error": "No records parsed"}

        future_write = pool.submit(_write_and_inject, records)
        result = future_write.result()

    elapsed = round(time.time() - t0, 1)
    result["success"] = True
    result["elapsed_seconds"] = elapsed
    logger.success(f"[plats_idx] Done in {elapsed}s")
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
