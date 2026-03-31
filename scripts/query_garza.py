import sys, json
sys.path.insert(0, r'E:\AI DATA CENTER\AI Agents\Deed & Plat Helper')
import xml_processor

survey = r'E:\AI DATA CENTER\Survey Data'
idx = xml_processor.load_index(survey)
if not idx:
    print('NO INDEX FOUND - build it first via the KML Index button in the app')
    sys.exit()

print("Index: {} parcels, built {}".format(idx['total'], idx['built_at']))
print()

hits = xml_processor.search_parcels_in_index(idx, owner='GARZA', operator='contains', limit=20)
print("GARZA matches: {}".format(len(hits)))
print("=" * 60)
for h in hits:
    print("  Owner:    {}".format(h.get('owner','')))
    print("  UPC:      {}".format(h.get('upc','')))
    print("  Book/Pg:  {}-{}".format(h.get('book',''), h.get('page','')))
    print("  PLAT:     {}".format(h.get('plat','')))
    print("  Cab refs: {}".format(h.get('cab_refs',[])))
    print("  Source:   {}".format(h.get('source','')))
    print()
