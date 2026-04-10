"""
resolve_kg_entities.py
======================
Entity resolution pass over the Knowledge Graph.

Problem: after bulk imports the KG has many duplicate person nodes:
  - "garcia, maria"  vs  "GARCIA MARIA"  vs  "garcia m."
  - "Taos Baptist Church" vs "Taos Valley Baptist Church"

Strategy:
  1. Load all person/surveyor nodes
  2. Normalize names (uppercase, strip punctuation)
  3. Build token-set fingerprints for fast candidate generation
  4. For candidates with Jaro-Winkler >= MERGE_THRESHOLD, merge the smaller
     node into the larger (more connected) node — preserving all edges
  5. Delete the merged-away nodes
  6. Save the graph

Uses 4 workers for the fuzzy matching phase.

Run:
    python scripts/resolve_kg_entities.py
"""

import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime

from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAX_WORKERS      = 4
MERGE_THRESHOLD  = 0.92   # Jaro-Winkler — conservative to avoid false merges
MIN_TOKEN_OVERLAP = 2     # Must share ≥2 tokens to even try fuzzy compare


# ── Name normalizer ───────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Uppercase, strip titles/punctuation, collapse whitespace."""
    name = name.upper()
    # Remove honorifics and legal suffixes
    name = re.sub(
        r"\b(MR|MRS|MS|DR|TRUST|LLC|INC|CORP|ET\s*AL|ET\s*UX|ESTATE\s*OF"
        r"|LIVING\s*TRUST|REVOCABLE|IRREVOCABLE|FAMILY)\b",
        "", name
    )
    name = re.sub(r"[^A-Z0-9 ]", " ", name)
    return " ".join(name.split())


def _tokens(norm_name: str) -> frozenset[str]:
    """Return set of meaningful tokens (len >= 2)."""
    return frozenset(t for t in norm_name.split() if len(t) >= 2)


# ── Fuzzy similarity ──────────────────────────────────────────────────────────

def _jaro_winkler(a: str, b: str) -> float:
    """Jaro-Winkler similarity. Falls back to token-overlap if jellyfish unavailable."""
    try:
        from jellyfish import jaro_winkler_similarity
        return jaro_winkler_similarity(a, b)
    except ImportError:
        # Token overlap fallback
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(len(ta), len(tb))


# ── Candidate generation ──────────────────────────────────────────────────────

def _build_candidates(
    nodes: list[tuple[str, str, frozenset]]   # (node_id, norm_name, tokens)
) -> list[tuple[str, str]]:
    """
    Return pairs of node_ids that share >= MIN_TOKEN_OVERLAP tokens.
    Only considers pairs within the same token-bucket (inverted index).
    """
    # Build inverted index: token → list of node indices
    inv: dict[str, list[int]] = defaultdict(list)
    for i, (_, _, toks) in enumerate(nodes):
        for tok in toks:
            inv[tok].append(i)

    # Collect pairs that share enough tokens
    pair_counts: dict[tuple[int, int], int] = defaultdict(int)
    for _, node_list in inv.items():
        if len(node_list) > 500:   # skip ultra-common tokens (e.g. "TAOS")
            continue
        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                key = (min(node_list[i], node_list[j]),
                       max(node_list[i], node_list[j]))
                pair_counts[key] += 1

    candidates = []
    for (i, j), count in pair_counts.items():
        if count >= MIN_TOKEN_OVERLAP:
            candidates.append((nodes[i][0], nodes[j][0]))

    logger.info(f"[resolve] {len(candidates):,} candidate pairs from shared tokens")
    return candidates


# ── Fuzzy match batch ─────────────────────────────────────────────────────────

def _score_batch(
    batch: list[tuple[str, str]],
    norm_map: dict[str, str],
) -> list[tuple[str, str, float]]:
    """Score a batch of candidate pairs. Returns (id_a, id_b, score) above threshold."""
    results = []
    for id_a, id_b in batch:
        score = _jaro_winkler(norm_map[id_a], norm_map[id_b])
        if score >= MERGE_THRESHOLD:
            results.append((id_a, id_b, score))
    return results


# ── Merge operation ───────────────────────────────────────────────────────────

def _merge_nodes(kg, keep_id: str, drop_id: str) -> int:
    """
    Merge drop_id into keep_id in an undirected Graph.
    Rewire all neighbor edges, delete drop_id.
    Returns number of edges rewired.
    """
    G = kg.G
    rewired = 0

    # Rewire all edges from drop's neighbors to keep
    for neighbor in list(G.neighbors(drop_id)):
        if neighbor == keep_id:
            continue
        edge_data = dict(G.edges[drop_id, neighbor])
        if not G.has_edge(keep_id, neighbor):
            G.add_edge(keep_id, neighbor, **edge_data)
            rewired += 1

    # Merge node attributes (fill blanks on keep)
    drop_attrs = G.nodes[drop_id]
    keep_attrs = G.nodes[keep_id]
    for k, v in drop_attrs.items():
        if k not in keep_attrs or not keep_attrs[k]:
            keep_attrs[k] = v

    # Track aliases
    aliases = list(keep_attrs.get("aliases") or [])
    drop_name = drop_attrs.get("name", "")
    if drop_name and drop_name not in aliases:
        aliases.append(drop_name)
    keep_attrs["aliases"] = aliases

    G.remove_node(drop_id)
    return rewired


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    t0 = time.time()

    from ai import get_knowledge_graph
    kg = get_knowledge_graph()
    if not kg:
        return {"success": False, "error": "KG not available"}

    G = kg.G
    nodes_before = G.number_of_nodes()
    edges_before = G.number_of_edges()

    # ── 1. Collect person + surveyor nodes ──────────────────────────────────
    target_types = {"person", "surveyor"}
    person_nodes: list[tuple[str, str, frozenset]] = []

    for node_id, data in G.nodes(data=True):
        if data.get("type") in target_types:
            raw = data.get("name", node_id)
            norm = _norm(str(raw))
            if len(norm) >= 4:
                person_nodes.append((node_id, norm, _tokens(norm)))

    logger.info(f"[resolve] {len(person_nodes):,} person/surveyor nodes to resolve")

    # Build norm_map for quick lookup
    norm_map = {nid: norm for nid, norm, _ in person_nodes}

    # ── 2. Generate candidates ───────────────────────────────────────────────
    candidates = _build_candidates(person_nodes)
    if not candidates:
        return {"success": True, "merged": 0, "message": "No candidates found"}

    # ── 3. Parallel fuzzy scoring ────────────────────────────────────────────
    batch_size = max(1, len(candidates) // MAX_WORKERS)
    batches    = [candidates[i:i+batch_size]
                  for i in range(0, len(candidates), batch_size)]

    matches: list[tuple[str, str, float]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_score_batch, b, norm_map) for b in batches]
        for f in futures:
            matches.extend(f.result())

    logger.info(f"[resolve] {len(matches):,} merge candidates (score >= {MERGE_THRESHOLD})")

    # ── 4. Merge — keep the node with more connections ───────────────────────
    merged      = 0
    rewired_total = 0
    already_gone  = set()

    # Sort by score desc so highest-confidence merges happen first
    matches.sort(key=lambda x: x[2], reverse=True)

    for id_a, id_b, score in matches:
        if id_a in already_gone or id_b in already_gone:
            continue
        if not G.has_node(id_a) or not G.has_node(id_b):
            continue

        # Keep whichever has more edges
        deg_a = G.degree(id_a)
        deg_b = G.degree(id_b)
        keep_id = id_a if deg_a >= deg_b else id_b
        drop_id = id_b if keep_id == id_a else id_a

        rewired = _merge_nodes(kg, keep_id, drop_id)
        rewired_total += rewired
        merged += 1
        already_gone.add(drop_id)

        if merged % 500 == 0:
            logger.info(f"[resolve]   {merged} merges done ...")

    # ── 5. Save ─────────────────────────────────────────────────────────────
    kg.save()
    elapsed = round(time.time() - t0, 1)

    nodes_after = G.number_of_nodes()
    edges_after = G.number_of_edges()

    logger.success(
        f"[resolve] Done in {elapsed}s — merged {merged} duplicate nodes, "
        f"{rewired_total} edges rewired | "
        f"nodes: {nodes_before:,} → {nodes_after:,} | "
        f"edges: {edges_before:,} → {edges_after:,}"
    )

    return {
        "success":       True,
        "merged":        merged,
        "rewired_edges": rewired_total,
        "nodes_before":  nodes_before,
        "nodes_after":   nodes_after,
        "edges_before":  edges_before,
        "edges_after":   edges_after,
        "elapsed_seconds": elapsed,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
