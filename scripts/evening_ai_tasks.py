"""
Evening AI Tasks — Entity Resolution + Embedding Index Build
Scheduled to run at 5:30 PM, then hands off to OCR batch.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 60)
print("  Evening AI Tasks — Starting")
print("=" * 60, flush=True)

# ── 1. Entity Resolution ──────────────────────────────────────
print("\n[1/3] Loading Knowledge Graph...", flush=True)
t0 = time.time()
from ai.knowledge_graph import SurveyKnowledgeGraph
kg = SurveyKnowledgeGraph()
print(f"  Loaded in {time.time()-t0:.1f}s: {kg.G.number_of_nodes()} nodes, {kg.G.number_of_edges()} edges", flush=True)

print("[1/3] Finding duplicates (threshold=0.88)...", flush=True)
dupes = kg.find_duplicates(threshold=0.88, limit=200)
print(f"  Found {len(dupes)} duplicate candidates", flush=True)

merged = 0
for d in dupes:
    keep, drop = d['keep'], d['merge']
    if not kg.G.has_node(keep) or not kg.G.has_node(drop):
        continue
    for nb in list(kg.G.neighbors(drop)):
        if nb != keep and not kg.G.has_edge(keep, nb):
            kg.G.add_edge(keep, nb, **kg.G.edges[drop, nb])
    kg.G.remove_node(drop)
    merged += 1

kg.save()
print(f"  ✅ Merged {merged} duplicates", flush=True)
print(f"  After: {kg.G.number_of_nodes()} nodes, {kg.G.number_of_edges()} edges", flush=True)

if dupes:
    print("  Top merges:", flush=True)
    for d in dupes[:10]:
        print(f"    {d['name_a']} <-> {d['name_b']} (sim={d['similarity']:.2f})", flush=True)

# ── 2. Test Ollama Embeddings ─────────────────────────────────
print("\n[2/3] Testing Ollama embeddings...", flush=True)
try:
    import httpx
    r = httpx.post('http://localhost:11434/api/embeddings',
                   json={'model': 'nomic-embed-text', 'prompt': 'Lot 1 Block 2 Ranchos de Taos'},
                   timeout=30)
    data = r.json()
    dim = len(data.get('embedding', []))
    if dim > 100:
        print(f"  ✅ Embedding model working (dim={dim})", flush=True)
    else:
        print(f"  ❌ Embedding returned dim={dim}", flush=True)
except Exception as e:
    print(f"  ❌ Ollama not available: {e}", flush=True)

# ── 3. Retrain ML models with deduped KG ─────────────────────
print("\n[3/3] Retraining ML models...", flush=True)
try:
    from ai.predictions import SurveyPredictor
    predictor = SurveyPredictor()
    result = predictor.train()
    print(f"  ✅ Trained on {result.get('training_records', '?')} records", flush=True)
    print(f"  Adjoiners MAE: {result.get('adjoiners', {}).get('mae', '?')}", flush=True)
except Exception as e:
    print(f"  ⚠️ Training skipped: {e}", flush=True)

print("\n" + "=" * 60)
print("  ✅ Evening AI Tasks Complete")
print("=" * 60, flush=True)
