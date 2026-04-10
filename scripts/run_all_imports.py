"""
run_all_imports.py
==================
Master runner — executes all 4 Z: drive import scripts in the correct order:

  1. import_access_db.py       (1 core, sequential — Access COM limitation)
  2. import_plats_index.py     (2 cores)
  3. import_kmz_locations.py   (6 cores)  ─┐ run simultaneously
  4. import_parcel_shapefile.py (4 cores)  ─┘ after #1 and #2 finish

Total wall-clock time: ~15-30 min depending on Z: drive speed.
Progress is printed live. Final summary written to:
    data/ai/training_data/import_summary.json

Run:
    python scripts/run_all_imports.py
    python scripts/run_all_imports.py --skip-access   (if Access driver not installed)
    python scripts/run_all_imports.py --only kmz      (run just one)
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR     = ROOT / "data" / "ai" / "training_data"
SUMMARY_OUT = OUT_DIR / "import_summary.json"


def _run_script(name: str) -> dict:
    """Import and run a script by module name, return its result dict."""
    logger.info(f"\n{'='*60}")
    logger.info(f"  Starting: {name}")
    logger.info(f"{'='*60}")
    t0 = time.time()
    try:
        import importlib
        mod = importlib.import_module(f"scripts.{name}")
        result = mod.run()
        result["script"]  = name
        result["elapsed"] = round(time.time() - t0, 1)
        return result
    except Exception as e:
        logger.error(f"[runner] {name} FAILED: {e}")
        return {"script": name, "success": False,
                "error": str(e), "elapsed": round(time.time() - t0, 1)}


def main():
    parser = argparse.ArgumentParser(description="Run all Z: drive data imports")
    parser.add_argument("--skip-access", action="store_true",
                        help="Skip Access DB import (use if ACE driver not installed)")
    parser.add_argument("--only",         type=str, default=None,
                        choices=["access", "plats", "kmz", "parcel"],
                        help="Run only one specific import")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_results = []
    grand_t0    = time.time()

    logger.info(f"\n{'#'*60}")
    logger.info(f"  Z: Drive Import Suite — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"{'#'*60}\n")

    # ── --only mode ──────────────────────────────────────────────────────────
    if args.only:
        script_map = {
            "access": "import_access_db",
            "plats":  "import_plats_index",
            "kmz":    "import_kmz_locations",
            "parcel": "import_parcel_shapefile",
        }
        result = _run_script(script_map[args.only])
        all_results.append(result)

    else:
        # ── Phase 1: Access DB (single-threaded, must finish before KG filling) ──
        if not args.skip_access:
            result = _run_script("import_access_db")
            all_results.append(result)
            if not result.get("success"):
                logger.warning(
                    "[runner] Access DB import failed — continuing with other imports.\n"
                    "         If missing ACE driver, re-run with --skip-access"
                )
        else:
            logger.info("[runner] Skipping Access DB import (--skip-access)")

        # ── Phase 2: Plats index (small, fast — run before parallel phase) ───
        result = _run_script("import_plats_index")
        all_results.append(result)

        # ── Phase 3: KMZ + Parcel shapefile in parallel ──────────────────────
        logger.info("\n[runner] Starting KMZ + Parcel shapefile in parallel ...")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(_run_script, "import_kmz_locations"):    "kmz",
                pool.submit(_run_script, "import_parcel_shapefile"): "parcel",
            }
            for future in as_completed(futures):
                name   = futures[future]
                result = future.result()
                all_results.append(result)
                status = "✅" if result.get("success") else "❌"
                logger.info(f"[runner] {status} {name} completed in {result.get('elapsed', '?')}s")

    # ── Summary ──────────────────────────────────────────────────────────────
    grand_elapsed = round(time.time() - grand_t0, 1)

    summary = {
        "run_at":          datetime.now().isoformat(),
        "total_elapsed_s": grand_elapsed,
        "results":         all_results,
    }
    SUMMARY_OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    logger.info(f"\n{'#'*60}")
    logger.info(f"  IMPORT COMPLETE — {grand_elapsed}s total")
    logger.info(f"{'#'*60}")

    for r in all_results:
        status = "✅" if r.get("success") else "❌"
        script = r.get("script", "?")
        elapsed = r.get("elapsed", "?")
        extra = ""
        if r.get("total_records"):
            extra = f" — {r['total_records']:,} records"
        if r.get("kg"):
            kg = r["kg"]
            kg_bits = []
            for k in ["persons", "jobs", "edges", "matched_persons",
                      "new_persons", "parcels_added", "updated", "created"]:
                if kg.get(k):
                    kg_bits.append(f"+{kg[k]} {k}")
            if kg_bits:
                extra += f" | KG: {', '.join(kg_bits)}"
        logger.info(f"  {status} {script} ({elapsed}s){extra}")

    if any(not r.get("success") for r in all_results):
        logger.warning(
            "\n  ⚠️  Some imports failed. Check logs above.\n"
            "     Access DB: install Microsoft ACE OLEDB driver if needed.\n"
            "     Shapefile: ensure Z: drive is connected.\n"
        )

    logger.info(f"\n  Summary written → {SUMMARY_OUT}\n")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
