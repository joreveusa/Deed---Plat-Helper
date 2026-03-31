"""
Rebuild parcel_index.json from all KML/KMZ files in the XML folder.
Parcel_Maintenance.kmz wins over TC_Parcels_2024.kml for shared UPCs.
Run with:  python rebuild_index.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import xml_processor

def progress(fi, total, msg):
    pct = int((fi / total) * 100) if total else 0
    print(f"  [{pct:3d}%] {msg}", flush=True)

# Auto-detect the survey drive
from app import get_survey_data_path
survey_path = get_survey_data_path()
print(f"\nSurvey Data path: {survey_path}")

# Show what files will be parsed
files = xml_processor.discover_xml_files(survey_path)
print(f"\nFound {len(files)} KML/KMZ file(s):")
for f in files:
    print(f"  • {f['name']}  ({f['size_mb']} MB)")

print("\nBuilding index (this may take a few minutes for large files)...\n")

result = xml_processor.build_index(survey_path, progress_callback=progress)

if "error" in result:
    print(f"\n❌ Error: {result['error']}")
    sys.exit(1)

print(f"\n✅ Done!")
print(f"   Total parcels : {result['total']:,}")
print(f"   Build time    : {result['elapsed_sec']}s")
print(f"   Index saved   : {result['index_path']}")
print(f"\nSources:")
for s in result.get("sources", []):
    print(f"   • {s['file']}: {s['records']:,} records")
