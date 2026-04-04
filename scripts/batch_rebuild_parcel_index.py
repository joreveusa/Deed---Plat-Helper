"""
scripts/batch_rebuild_parcel_index.py — Rebuild KML/KMZ parcel index.

Usage:
    python scripts/batch_rebuild_parcel_index.py <survey_data_path>
"""

import sys
import time

sys.path.insert(0, ".")
import xml_processor


def main():
    if len(sys.argv) < 2:
        print("Usage: batch_rebuild_parcel_index.py <survey_data_path>")
        sys.exit(1)

    survey_path = sys.argv[1]
    print(f"[batch] Starting index build for: {survey_path}")
    t0 = time.time()

    def progress(current, total, msg):
        pct = round(current / total * 100) if total else 0
        print(f"  [{pct:3d}%] {msg}", flush=True)

    try:
        result = xml_processor.build_index(survey_path, progress_callback=progress)
        elapsed = round(time.time() - t0, 1)
        print(f"[batch] Index build complete in {elapsed}s")
        print(f"  Total parcels: {result.get('total', 0)}")
        print(f"  ArcGIS enriched: {result.get('arcgis_enriched', 0)}")
        print(f"  Sources: {len(result.get('sources', []))}")
        for s in result.get("sources", []):
            print(f"    - {s['file']}: {s['records']} records")
    except Exception as e:
        print(f"[batch] ERROR: Index build failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
