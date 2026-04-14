"""
AI Routes — Flask Blueprint
==============================
All AI API endpoints for the Deed & Plat Helper.
Registered as a Blueprint so app.py stays clean.

Every endpoint gracefully returns {"available": false} if its
subsystem is missing — the app never crashes from AI issues.
"""

from flask import Blueprint, request, jsonify
from loguru import logger
import json
import os
from datetime import datetime
from pathlib import Path

ai_bp = Blueprint('ai', __name__, url_prefix='/api/ai')

MERGE_LOG = Path(__file__).parent.parent / 'data' / 'ai' / 'merge_audit_log.jsonl'


def _log_merge(keep_id, keep_name, merge_id, merge_name, similarity, trigger):
    """Append one merge event to the audit log."""
    try:
        MERGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            'ts':         datetime.now().isoformat(),
            'trigger':    trigger,
            'keep_id':    keep_id,
            'keep_name':  keep_name,
            'merge_id':   merge_id,
            'merge_name': merge_name,
            'similarity': round(float(similarity), 4) if similarity is not None else None,
        }
        with open(MERGE_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception as e:
        logger.warning(f'Could not write merge audit log: {e}')


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH / STATUS
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/status', methods=['GET'])
def ai_status():
    """AI subsystem health check — works even if everything is down."""
    from ai import (
        get_ai_client, get_predictor, get_knowledge_graph,
        get_anomaly_detector, get_embeddings,
    )

    status = {
        "available": True,
        "ollama": {"available": False},
        "ml": {"available": False},
        "knowledge_graph": {"available": False},
        "anomaly_detector": {"available": False},
        "embeddings": {"available": False},
    }

    # Ollama / LLM
    client = get_ai_client()
    if client:
        health = client.health()
        status["ollama"] = {
            "available": health.get("ollama", False),
            "url": health.get("ollama_url", ""),
            "model": health.get("model", ""),
            "models": health.get("models_available", []),
        }

    # ML Predictor
    predictor = get_predictor()
    if predictor:
        stats = predictor.get_training_stats()
        status["ml"] = {
            "available": True,
            "adj_model_loaded": predictor.adj_model is not None,
            "cab_model_loaded": predictor.cab_model is not None,
            "time_model_loaded": predictor.time_model is not None,
            "last_trained": stats.get("trained_at", "never"),
            "training_jobs": stats.get("training_jobs", 0),
        }

    # Knowledge Graph
    kg = get_knowledge_graph()
    if kg:
        status["knowledge_graph"] = {
            "available": True,
            "nodes": kg.G.number_of_nodes(),
            "edges": kg.G.number_of_edges(),
        }

    # Anomaly Detector
    detector = get_anomaly_detector()
    if detector:
        status["anomaly_detector"] = {
            "available": True,
            "baselines": len(detector.get_baselines()),
        }

    # Embeddings
    emb = get_embeddings()
    if emb:
        emb_status = emb.status()
        status["embeddings"] = {
            "available":        emb_status.get("available", False),
            "backend":          emb_status.get("backend", "none"),
            "st_available":     emb_status.get("st_available", False),
            "ollama_available": emb_status.get("ollama_available", False),
            "documents":        emb_status.get("document_count", 0),
        }

    return jsonify(status)


# ══════════════════════════════════════════════════════════════════════════════
# LLM CHAT
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/ask', methods=['POST'])
def ai_ask():
    """Ask the AI a natural language question. Requires Ollama."""
    from ai import get_ai_client

    client = get_ai_client()
    if not client:
        return jsonify({"available": False,
                        "error": "AI client not available"}), 503

    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    context = data.get("context", "").strip()

    if not question:
        return jsonify({"error": "No question provided"}), 400

    answer = client.ask_about_research(question, context=context)
    from ai.client import _MODEL
    return jsonify({"answer": answer, "model": _MODEL})


@ai_bp.route('/summarize', methods=['POST'])
def ai_summarize():
    """Summarize a legal description in plain English."""
    from ai import get_ai_client

    client = get_ai_client()
    if not client:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "No text provided"}), 400

    summary = client.summarize_legal_description(text)
    return jsonify({"summary": summary})


@ai_bp.route('/extract', methods=['POST'])
def ai_extract():
    """Extract structured entities from deed text."""
    from ai import get_ai_client

    client = get_ai_client()
    if not client:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "No text provided"}), 400

    entities = client.extract_entities_from_deed(text)
    return jsonify({"entities": entities})


# ══════════════════════════════════════════════════════════════════════════════
# ML PREDICTIONS
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/predict', methods=['POST'])
def ai_predict():
    """Predict job complexity/adjoiners/cabinet. No Ollama needed."""
    from ai import get_predictor

    predictor = get_predictor()
    if not predictor:
        return jsonify({"available": False,
                        "error": "ML predictor not available"}), 503

    data = request.get_json(silent=True) or {}
    job_type = data.get("job_type", "BDY")
    client_name = data.get("client_name", "")

    result = predictor.predict_complexity(job_type, client_name)
    return jsonify(result)


@ai_bp.route('/predict/adjoiners', methods=['POST'])
def ai_predict_adjoiners():
    """Predict adjoiner count specifically."""
    from ai import get_predictor

    predictor = get_predictor()
    if not predictor:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    result = predictor.predict_adjoiners(
        job_type=data.get("job_type", "BDY"),
        client_name=data.get("client_name", ""),
    )
    return jsonify(result)


@ai_bp.route('/predict/cabinet', methods=['POST'])
def ai_predict_cabinet():
    """Predict cabinet letter."""
    from ai import get_predictor

    predictor = get_predictor()
    if not predictor:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    result = predictor.predict_cabinet(
        job_type=data.get("job_type", "BDY"),
        client_name=data.get("client_name", ""),
    )
    return jsonify(result)


@ai_bp.route('/train', methods=['POST'])
def ai_train():
    """Trigger ML model training."""
    from ai import get_predictor

    predictor = get_predictor()
    if not predictor:
        return jsonify({"available": False}), 503

    result = predictor.train()
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# EMBEDDINGS / SIMILARITY
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/similar', methods=['POST'])
def ai_similar():
    """Find similar legal descriptions via embeddings."""
    from ai import get_embeddings

    emb = get_embeddings()
    if not emb:
        return jsonify({"available": False,
                        "error": "Embeddings not available. "
                                 "Install sentence-transformers and chromadb."}
                       ), 503

    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    top_k = data.get("top_k", 10)

    if not query:
        return jsonify({"error": "No query provided"}), 400

    results = emb.find_similar(query, top_k=top_k)
    return jsonify({"results": results, "count": len(results)})


@ai_bp.route('/embed', methods=['POST'])
def ai_embed():
    """Add a document to the embeddings index (fire-and-forget background indexing)."""
    from ai import get_embeddings

    emb = get_embeddings()
    if not emb:
        # Silently accept — client doesn't need to know embeddings are unavailable
        return jsonify({"queued": False, "reason": "embeddings unavailable"}), 202

    data = request.get_json(silent=True) or {}
    doc_id = data.get("id", "").strip()
    text = data.get("text", "").strip()
    metadata = data.get("metadata", {})

    if not text or len(text) < 10:
        return jsonify({"queued": False, "reason": "text too short"}), 202

    try:
        emb.add_document(doc_id or f"doc_{hash(text)}", text, metadata)
        return jsonify({"queued": True}), 202
    except Exception as e:
        logger.warning(f"Embed add failed: {e}")
        return jsonify({"queued": False, "reason": str(e)}), 202


# Shared progress tracker for the bulk-index job
_embed_session_job: dict = {"running": False, "indexed": 0, "total": 0,
                            "skipped": 0, "error": ""}


@ai_bp.route('/embed/sessions', methods=['POST'])
def ai_embed_sessions():
    """Bulk-index deed descriptions from all live research.json sessions.

    Runs in a background thread so the response returns immediately.
    Body: { limit: int (default 0 = unlimited) }
    Returns immediately: { started: true, message: str }
    Poll /api/ai/embed/sessions/status for progress.
    """
    import threading
    from ai import get_embeddings

    global _embed_session_job

    if _embed_session_job.get("running"):
        return jsonify({
            "started": False,
            "message": "A bulk index job is already running.",
            "progress": _embed_session_job,
        }), 409

    emb = get_embeddings()
    if not emb:
        return jsonify({"available": False,
                        "error": "sentence-transformers / chromadb not installed"}), 503

    data = request.get_json(silent=True) or {}
    limit = int(data.get("limit", 0))

    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app import get_survey_data_path
        survey_path = get_survey_data_path()
    except Exception as e:
        return jsonify({"success": False, "error": f"Could not get survey path: {e}"}), 500

    if not survey_path:
        return jsonify({"success": False, "error": "Survey drive not found"}), 404

    def _run():
        global _embed_session_job
        _embed_session_job = {"running": True, "indexed": 0,
                              "total": 0, "skipped": 0, "error": ""}
        try:
            result = emb.build_from_research_sessions(survey_path, limit=limit)
            _embed_session_job.update({
                "running":  False,
                "indexed":  result.get("indexed", 0),
                "skipped":  result.get("skipped", 0),
                "total":    result.get("total_in_collection", 0),
                "elapsed":  result.get("elapsed_seconds", 0),
                "error":    "" if result.get("success") else result.get("error", ""),
            })
        except Exception as e:
            _embed_session_job.update({"running": False, "error": str(e)})
            logger.error(f"Embed sessions job failed: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({
        "started": True,
        "message": f"Bulk embedding started — survey path: {survey_path}",
        "limit": limit or "unlimited",
    })


@ai_bp.route('/embed/sessions/status', methods=['GET'])
def ai_embed_sessions_status():
    """Poll the bulk embedding job status."""
    return jsonify(_embed_session_job)


# ══════════════════════════════════════════════════════════════════════════════
# ANOMALY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/analyze', methods=['POST'])
def ai_analyze():
    """Run anomaly detection on research data. No Ollama needed."""
    from ai import get_anomaly_detector

    detector = get_anomaly_detector()
    if not detector:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    job_type = data.get("job_type", "BDY")
    client_name = data.get("client_name", "")

    research = {
        "adjoiners_found": data.get("adjoiners_found", 0),
        "deed_found": data.get("deed_found", True),
        "plat_found": data.get("plat_found", True),
        "subjects": data.get("subjects", []),
    }

    flags = detector.check_job(research, job_type, client_name)
    return jsonify({"flags": flags, "count": len(flags)})


@ai_bp.route('/analyze/batch', methods=['POST'])
def ai_analyze_batch():
    """Batch anomaly audit."""
    from ai import get_anomaly_detector

    detector = get_anomaly_detector()
    if not detector:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 50)
    result = detector.batch_audit(limit=limit)
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE GRAPH
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/graph/stats', methods=['GET'])
def ai_graph_stats():
    """Knowledge graph statistics."""
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    stats = kg.graph_stats()

    # Add embedding index count
    try:
        from ai import get_embeddings
        emb = get_embeddings()
        if emb and hasattr(emb, "_collection"):
            stats["embedding_count"] = emb._collection.count()
        else:
            stats["embedding_count"] = 0
    except Exception:
        stats["embedding_count"] = 0

    stats["available"] = True
    return jsonify(stats)


@ai_bp.route('/graph/adjoiners/<path:name>', methods=['GET'])
def ai_graph_adjoiners(name: str):
    """Find adjoiners for a person in the knowledge graph."""
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    adjoiners = kg.get_adjoiners(name)
    return jsonify({"name": name, "adjoiners": adjoiners,
                    "count": len(adjoiners)})


@ai_bp.route('/graph/jobs/<path:name>', methods=['GET'])
def ai_graph_jobs(name: str):
    """Find jobs involving a person."""
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    jobs = kg.get_person_jobs(name)
    return jsonify({"name": name, "jobs": jobs, "count": len(jobs)})


@ai_bp.route('/graph/chain/<path:name>', methods=['GET'])
def ai_graph_chain(name: str):
    """Get adjacency chain for a person."""
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    depth = request.args.get("depth", 2, type=int)
    chain = kg.get_adjacency_chain(name, depth=depth)
    return jsonify(chain)


@ai_bp.route('/graph/search', methods=['POST'])
def ai_graph_search():
    """Search for persons in the knowledge graph."""
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    limit = data.get("limit", 20)

    if not query:
        return jsonify({"error": "No query provided"}), 400

    results = kg.search_persons(query, limit=limit)
    return jsonify({"results": results, "count": len(results)})


@ai_bp.route('/graph/populate', methods=['POST'])
def ai_graph_populate():
    """Populate the knowledge graph from archive data."""
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    result = kg.populate_from_archive()
    return jsonify(result)


@ai_bp.route('/graph/populate/sessions', methods=['POST'])
def ai_graph_populate_sessions():
    """Populate the knowledge graph from live research.json sessions on the J: drive.

    This is the primary learning path — reads every completed research.json
    in the Survey Data folder and adds client/adjoiner/job relationships to the
    graph.  Safe to call multiple times (upserts existing nodes).
    """
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app import get_survey_data_path
        survey_path = get_survey_data_path()
    except Exception as e:
        return jsonify({"success": False, "error": f"Could not get survey path: {e}"}), 500

    if not survey_path:
        return jsonify({"success": False, "error": "Survey drive not found"}), 404

    result = kg.populate_from_research_sessions(survey_path)
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# ENTITY RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/graph/duplicates', methods=['GET'])
def ai_graph_duplicates():
    """Find likely duplicate person nodes in the knowledge graph.

    Uses Jaro-Winkler string similarity on normalized names.
    Query params:
      threshold  float  (default 0.88) — similarity cutoff (0.0–1.0)
      limit      int    (default 100)  — max pairs to return
    Returns: { duplicates: [{node_a, name_a, node_b, name_b, similarity, keep, merge}], count }
    """
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    threshold = float(request.args.get("threshold", 0.88))
    limit = int(request.args.get("limit", 100))

    duplicates = kg.find_duplicates(threshold=threshold, limit=limit)
    return jsonify({"duplicates": duplicates, "count": len(duplicates)})


@ai_bp.route('/graph/merge', methods=['POST'])
def ai_graph_merge():
    """Merge duplicate person nodes (entity resolution).

    Body: { "pairs": [{"keep": node_id, "merge": node_id, "similarity": float, "name_a": str, "name_b": str}] }
    OR:   { "auto": true, "threshold": 0.92 }  -- auto-merge high-confidence dupes.

    Returns: { merged: int, skipped: int, graph_nodes: int, graph_edges: int }
    """
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    merged = 0
    skipped = 0
    trigger = 'auto' if data.get('auto') else 'ui'

    if data.get('auto'):
        # Auto-merge: find all pairs above threshold and merge them
        threshold = float(data.get('threshold', 0.92))
        pairs = kg.find_duplicates(threshold=threshold, limit=500)
    else:
        pairs = data.get('pairs', [])

    for pair in pairs:
        keep_id  = pair.get('keep', '')
        merge_id = pair.get('merge', '')
        if not keep_id or not merge_id or keep_id == merge_id:
            skipped += 1
            continue
        try:
            # Capture names before merge (merge node will be deleted)
            keep_name  = (kg.G.nodes[keep_id].get('name', keep_id)
                          if kg.G.has_node(keep_id) else keep_id)
            merge_name = (kg.G.nodes[merge_id].get('name', merge_id)
                          if kg.G.has_node(merge_id) else merge_id)
            similarity = pair.get('similarity') or pair.get('score')

            ok = kg.merge_duplicates(keep_id, merge_id)
            if ok:
                _log_merge(keep_id, keep_name, merge_id, merge_name, similarity, trigger)
                merged += 1
            else:
                skipped += 1
        except Exception as e:
            logger.warning(f'Merge failed ({keep_id} <- {merge_id}): {e}')
            skipped += 1

    if merged > 0:
        kg.save()

    return jsonify({
        'merged':      merged,
        'skipped':     skipped,
        'graph_nodes': kg.G.number_of_nodes(),
        'graph_edges': kg.G.number_of_edges(),
    })


@ai_bp.route('/graph/merge-log', methods=['GET'])
def ai_graph_merge_log():
    """Return the merge audit log. Query params: limit (default 100), offset (default 0)."""
    limit  = int(request.args.get('limit',  100))
    offset = int(request.args.get('offset', 0))
    if not MERGE_LOG.exists():
        return jsonify({'entries': [], 'total': 0})
    entries = []
    with open(MERGE_LOG, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    total = len(entries)
    page  = entries[offset: offset + limit]
    return jsonify({'entries': page, 'total': total})



@ai_bp.route('/graph/visualize', methods=['GET'])
def ai_graph_visualize():
    """Visualize the knowledge graph using PyVis with rich Deep Space styling."""
    from ai import get_knowledge_graph
    import os
    import tempfile

    kg = get_knowledge_graph()
    if not kg:
        return """<html><body style="background:#0F172A;color:#94A3B8;font-family:sans-serif;
            display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center"><div style="font-size:48px">🕸️</div>
            <h2>Knowledge Graph Not Available</h2>
            <p>The graph has not been populated yet. Run the AI bootstrap script first.</p></div>
        </body></html>"""

    try:
        from pyvis.network import Network
        import networkx as nx

        G = kg.G
        total_nodes = G.number_of_nodes()
        total_edges = G.number_of_edges()

        # Limit to top 300 most-connected nodes for browser performance
        if total_nodes > 300:
            degrees = dict(G.degree())
            top_nodes = sorted(degrees, key=degrees.get, reverse=True)[:300]
            G_sub = G.subgraph(top_nodes).copy()
        else:
            G_sub = G.copy()

        shown_nodes = G_sub.number_of_nodes()
        shown_edges = G_sub.number_of_edges()

        # Count node types
        type_counts = {"person": 0, "job": 0, "other": 0}
        for node in G_sub.nodes():
            n_type = G.nodes[node].get("type", "other")
            if n_type not in type_counts:
                n_type = "other"
            type_counts[n_type] += 1

        # Build PyVis network
        net = Network(height='calc(100vh - 60px)', width='100%',
                      bgcolor='#0D1117', font_color='#E2E8F0')
        net.barnes_hut(gravity=-8000, central_gravity=0.3,
                       spring_length=120, spring_strength=0.05,
                       damping=0.09, overlap=0)

        degrees_sub = dict(G_sub.degree())
        max_deg = max(degrees_sub.values()) if degrees_sub else 1

        for node in G_sub.nodes():
            n_type = G.nodes[node].get("type", "other")
            deg = degrees_sub.get(node, 1)
            # Scale node size by connectivity
            size = 12 + (deg / max_deg) * 30

            if n_type == "person":
                color = {"background": "#38BDF8", "border": "#7DD3FC",
                         "highlight": {"background": "#7DD3FC", "border": "#BAE6FD"}}
                shape = "dot"
                title = f"<b>👤 {node}</b><br>Type: Person<br>Connections: {deg}"
            elif n_type == "job":
                color = {"background": "#818CF8", "border": "#A5B4FC",
                         "highlight": {"background": "#A5B4FC", "border": "#C7D2FE"}}
                shape = "diamond"
                size = max(size, 20)
                title = f"<b>📋 {node}</b><br>Type: Job<br>Connections: {deg}"
            else:
                color = {"background": "#34D399", "border": "#6EE7B7",
                         "highlight": {"background": "#6EE7B7", "border": "#A7F3D0"}}
                shape = "square"
                title = f"<b>📦 {node}</b><br>Type: {n_type}<br>Connections: {deg}"

            # Only show label for high-degree nodes to reduce clutter
            label = node if deg >= max(2, max_deg * 0.1) else ""

            net.add_node(node, label=label, title=title,
                         color=color, size=size, shape=shape,
                         font={"size": 11, "color": "#E2E8F0", "strokeWidth": 2,
                               "strokeColor": "#0D1117"})

        for u, v, data in G_sub.edges(data=True):
            u_type = G.nodes[u].get("type", "other")
            v_type = G.nodes[v].get("type", "other")
            # Color edges by relationship type
            if "person" in (u_type, v_type) and "job" in (u_type, v_type):
                edge_color = "rgba(129,140,248,0.6)"  # indigo — person-job link
                width = 2
            elif u_type == "person" and v_type == "person":
                edge_color = "rgba(56,189,248,0.5)"   # sky — person-person (adjoiners)
                width = 1.5
            else:
                edge_color = "rgba(148,163,184,0.35)"  # slate — other
                width = 1

            net.add_edge(u, v, color=edge_color, width=width,
                         smooth={"type": "curvedCW", "roundness": 0.1})

        tmp_dir = tempfile.gettempdir()
        tmp_file = os.path.join(tmp_dir, "survey_kg.html")
        net.save_graph(tmp_file)

        with open(tmp_file, "r", encoding="utf-8") as f:
            pyvis_html = f.read()

        # Extract just the body content and inject our own styled shell
        import re as _re
        body_match = _re.search(r'<body[^>]*>(.*?)</body>', pyvis_html, _re.DOTALL)
        body_inner = body_match.group(1) if body_match else pyvis_html
        head_match = _re.search(r'<head[^>]*>(.*?)</head>', pyvis_html, _re.DOTALL)
        head_inner = head_match.group(1) if head_match else ""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>🕸️ Survey Knowledge Graph — Red Tail Surveying</title>
  {head_inner}
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0D1117; font-family: 'Inter', 'Segoe UI', sans-serif; overflow: hidden; }}

    /* ── TOP BAR ── */
    .kg-topbar {{
      height: 60px;
      background: linear-gradient(135deg, rgba(56,189,248,0.08), rgba(129,140,248,0.06));
      border-bottom: 1px solid rgba(255,255,255,0.08);
      display: flex;
      align-items: center;
      padding: 0 24px;
      gap: 20px;
      backdrop-filter: blur(8px);
    }}
    .kg-title {{
      font-size: 16px;
      font-weight: 800;
      color: #E2E8F0;
      letter-spacing: 0.3px;
    }}
    .kg-sub {{
      font-size: 11px;
      color: #64748B;
    }}
    .kg-stats {{
      display: flex;
      gap: 16px;
      margin-left: auto;
    }}
    .kg-stat {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 1px;
    }}
    .kg-stat-value {{
      font-size: 18px;
      font-weight: 800;
      color: #38BDF8;
      line-height: 1;
    }}
    .kg-stat-label {{
      font-size: 9px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: #475569;
    }}

    /* ── LEGEND PANEL ── */
    .kg-legend {{
      position: fixed;
      bottom: 24px;
      left: 24px;
      background: rgba(15,23,42,0.92);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 12px;
      padding: 14px 18px;
      backdrop-filter: blur(12px);
      z-index: 9999;
      min-width: 180px;
    }}
    .kg-legend-title {{
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: #475569;
      margin-bottom: 10px;
    }}
    .kg-legend-item {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
      font-size: 12px;
      color: #94A3B8;
    }}
    .kg-legend-item:last-child {{ margin-bottom: 0; }}
    .kg-dot {{
      width: 12px; height: 12px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .kg-diamond {{
      width: 12px; height: 12px;
      transform: rotate(45deg);
      border-radius: 2px;
      flex-shrink: 0;
    }}
    .kg-square {{
      width: 12px; height: 12px;
      border-radius: 2px;
      flex-shrink: 0;
    }}
    .kg-line {{
      width: 20px; height: 2px;
      border-radius: 2px;
      flex-shrink: 0;
    }}
    .kg-legend-sep {{
      border: none;
      border-top: 1px solid rgba(255,255,255,0.06);
      margin: 8px 0;
    }}
    .kg-legend-count {{
      font-size: 10px;
      color: #38BDF8;
      font-weight: 700;
      margin-left: auto;
    }}

    /* ── HINT ── */
    .kg-hint {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: rgba(15,23,42,0.85);
      border: 1px solid rgba(255,255,255,0.07);
      border-radius: 10px;
      padding: 10px 14px;
      font-size: 10px;
      color: #475569;
      z-index: 9999;
      line-height: 1.7;
    }}

    /* ── GRAPH CONTAINER ── */
    #mynetwork {{
      width: 100% !important;
      height: calc(100vh - 60px) !important;
      border: none !important;
    }}
  </style>
</head>
<body>
  <!-- Top Bar -->
  <div class="kg-topbar">
    <span style="font-size:22px">🕸️</span>
    <div>
      <div class="kg-title">Survey Knowledge Graph</div>
      <div class="kg-sub">Red Tail Surveying — Taos County, NM</div>
    </div>
    <div class="kg-stats">
      <div class="kg-stat">
        <div class="kg-stat-value" style="color:#38BDF8">{shown_nodes}</div>
        <div class="kg-stat-label">Nodes Shown</div>
      </div>
      <div class="kg-stat">
        <div class="kg-stat-value" style="color:#818CF8">{shown_edges}</div>
        <div class="kg-stat-label">Connections</div>
      </div>
      <div class="kg-stat">
        <div class="kg-stat-value" style="color:#34D399">{total_nodes}</div>
        <div class="kg-stat-label">Total Nodes</div>
      </div>
      <div class="kg-stat">
        <div class="kg-stat-value" style="color:#F59E0B">{type_counts['person']}</div>
        <div class="kg-stat-label">People</div>
      </div>
      <div class="kg-stat">
        <div class="kg-stat-value" style="color:#C084FC">{type_counts['job']}</div>
        <div class="kg-stat-label">Jobs</div>
      </div>
    </div>
  </div>

  <!-- Graph -->
  {body_inner}

  <!-- Legend -->
  <div class="kg-legend">
    <div class="kg-legend-title">Legend</div>
    <div class="kg-legend-item">
      <div class="kg-dot" style="background:#38BDF8"></div>
      <span>Person / Owner</span>
      <span class="kg-legend-count">{type_counts['person']}</span>
    </div>
    <div class="kg-legend-item">
      <div class="kg-diamond" style="background:#818CF8"></div>
      <span>Survey Job</span>
      <span class="kg-legend-count">{type_counts['job']}</span>
    </div>
    <div class="kg-legend-item">
      <div class="kg-square" style="background:#34D399"></div>
      <span>Other Entity</span>
      <span class="kg-legend-count">{type_counts['other']}</span>
    </div>
    <hr class="kg-legend-sep">
    <div class="kg-legend-item">
      <div class="kg-line" style="background:rgba(129,140,248,0.8)"></div>
      <span>Person ↔ Job</span>
    </div>
    <div class="kg-legend-item">
      <div class="kg-line" style="background:rgba(56,189,248,0.7)"></div>
      <span>Adjoiner Link</span>
    </div>
    <div class="kg-legend-item">
      <div class="kg-line" style="background:rgba(148,163,184,0.5)"></div>
      <span>Other Relation</span>
    </div>
    <hr class="kg-legend-sep">
    <div style="font-size:9px;color:#334155">
      Node size = number of connections<br>
      Labels shown for key nodes only
    </div>
  </div>

  <!-- Interaction hint -->
  <div class="kg-hint">
    🖱 Drag nodes &nbsp;|&nbsp; Scroll to zoom<br>
    🖱 Hover node for details &nbsp;|&nbsp; Click to highlight
  </div>
</body>
</html>"""

        return html

    except Exception as e:
        logger.error(f"Failed to generate visualization: {e}")
        import traceback
        return f"""<html><body style="background:#0D1117;color:#F87171;font-family:monospace;padding:40px">
            <h2>⚠️ Visualization Error</h2><pre>{traceback.format_exc()}</pre></body></html>"""




@ai_bp.route('/graph/entity-review', methods=['GET'])
def ai_graph_entity_review():
    """Full-page entity resolution review UI - Deep Space theme."""
    return open(__file__.replace('routes.py', 'entity_review.html'), encoding='utf-8').read()


# ══════════════════════════════════════════════════════════════════════════════
# GAP 7: ML PERFORMANCE METRICS
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/performance', methods=['GET'])
def ai_performance():
    """
    ML performance metrics — model accuracy trends, training history, drift events.

    Reads:
      - J:/Under Development/AI Surveyor/data/models/training_history.json
      - data/ai/prediction_accuracy.jsonl
      - J:/Under Development/AI Surveyor/data/ml_accuracy_report.json (if run)

    Returns: {
      training_history: [...],
      accuracy: { overall_mae, recent_mae, trend, improving, by_job_type },
      models: { adj: {...}, cabinet: {...} },
      drift_events: [...],
      predictions_logged: int,
    }
    """
    result = {
        "available": True,
        "generated": datetime.now().isoformat(),
        "training_history": [],
        "accuracy": {},
        "models": {},
        "drift_events": [],
        "predictions_logged": 0,
    }

    # ── Training history (from AI Surveyor models) ────────────────────────
    history_paths = [
        Path("J:/Under Development/AI Surveyor/data/models/training_history.json"),
        Path(__file__).parent.parent / "data" / "models" / "training_history.json",
    ]
    for hp in history_paths:
        if hp.exists():
            try:
                history = json.loads(hp.read_text(encoding="utf-8"))
                result["training_history"] = history[-50:]  # last 50 runs
                # Extract latest model metrics
                if history:
                    latest = history[-1]
                    result["models"] = latest.get("metrics", {})
                    # Collect drift events
                    result["drift_events"] = [
                        h for h in history
                        if h.get("drift", {}).get("drifted")
                    ][-10:]
            except Exception as e:
                logger.debug(f"[performance] training history read error: {e}")
            break

    # ── Accuracy report (from weekend_learn) ─────────────────────────────
    acc_report_paths = [
        Path("J:/Under Development/AI Surveyor/data/ml_accuracy_report.json"),
        Path(__file__).parent.parent / "data" / "ml_accuracy_report.json",
    ]
    for arp in acc_report_paths:
        if arp.exists():
            try:
                result["accuracy"] = json.loads(arp.read_text(encoding="utf-8"))
            except Exception:
                pass
            break

    # ── Raw prediction accuracy log ───────────────────────────────────────
    acc_log = Path(__file__).parent.parent / "data" / "ai" / "prediction_accuracy.jsonl"
    if acc_log.exists():
        try:
            entries = []
            with open(acc_log, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            pass
            result["predictions_logged"] = len(entries)

            # If accuracy report doesn't exist yet, compute inline
            if not result["accuracy"] and entries:
                errors = [e.get("error", 0) for e in entries]
                overall_mae = round(sum(errors) / len(errors), 3) if errors else 0
                recent = errors[-30:]
                recent_mae = round(sum(recent) / len(recent), 3) if recent else 0
                result["accuracy"] = {
                    "total_predictions": len(entries),
                    "overall_mae": overall_mae,
                    "recent_mae": recent_mae,
                    "note": "MAE = mean abs error in predicted vs actual adjoiners",
                }
        except Exception as e:
            logger.debug(f"[performance] accuracy log read error: {e}")

    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# CORRECTION FEEDBACK LOOP
# ══════════════════════════════════════════════════════════════════════════════

CORRECTION_LOG = Path(__file__).parent.parent / 'data' / 'ai' / 'survey_corrections.jsonl'

@ai_bp.route('/correction', methods=['POST'])
def ai_log_correction():
    """
    Record a surveyor correction — what the AI produced vs. what was correct.

    Purpose: Every correction becomes a high-weight training example.
    The system learns from its mistakes automatically via weekend_learn.py.

    POST body (JSON):
    {
        "job_number":      "2942",
        "correction_type": "bearing|area|adjoiner_count|monument|closure|other",
        "field":           "call_3_bearing",       # which specific field was wrong
        "ai_value":        "N45°30'00\"E",          # what the AI said
        "correct_value":   "N44°58'32\"E",          # what was actually correct
        "magnitude":       "0.026",                 # magnitude of error (optional)
        "notes":           "Off by 1'28\" — old deed uses astronomic north",
        "job_type":        "BDY",
        "client_name":     "Garcia Sandoval",
        "surveyor":        "Tina"
    }
    """
    try:
        data = request.get_json(force=True) or {}
        required = ("job_number", "correction_type", "correct_value")
        missing  = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"success": False, "error": f"Missing: {missing}"}), 400

        entry = {
            "ts":              datetime.now().isoformat(),
            "job_number":      str(data.get("job_number", "")),
            "correction_type": data.get("correction_type", "other"),
            "field":           data.get("field", ""),
            "ai_value":        str(data.get("ai_value", "")),
            "correct_value":   str(data.get("correct_value", "")),
            "magnitude":       data.get("magnitude"),
            "notes":           data.get("notes", ""),
            "job_type":        data.get("job_type", ""),
            "client_name":     data.get("client_name", ""),
            "surveyor":        data.get("surveyor", ""),
            # Weight: surveyor-confirmed corrections are 5× normal training data
            "training_weight": 5.0,
        }

        CORRECTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CORRECTION_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

        logger.info(
            f"[correction] job={entry['job_number']}  "
            f"type={entry['correction_type']}  "
            f"was={entry['ai_value']!r}  correct={entry['correct_value']!r}"
        )
        return jsonify({"success": True, "logged": True, "entry": entry})

    except Exception as e:
        logger.error(f"[correction] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route('/corrections/summary', methods=['GET'])
def ai_corrections_summary():
    """
    Summary of all logged corrections — for the dashboard.

    Returns: {
        total, by_type, by_job_type, recent: [...last 20],
        most_common_error, trend_improving: bool
    }
    """
    try:
        if not CORRECTION_LOG.exists():
            return jsonify({"total": 0, "by_type": {}, "recent": [],
                            "available": True, "note": "No corrections logged yet"})

        corrections = []
        with open(CORRECTION_LOG, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        corrections.append(json.loads(line))
                    except Exception:
                        pass

        if not corrections:
            return jsonify({"total": 0, "by_type": {}, "recent": [], "available": True})

        from collections import Counter
        by_type     = dict(Counter(c.get("correction_type", "other") for c in corrections))
        by_job_type = dict(Counter(c.get("job_type", "?") for c in corrections))

        # Trend: is the rate of corrections decreasing over time?
        half = len(corrections) // 2
        trend_improving = len(corrections[:half]) > len(corrections[half:]) if half > 0 else None

        # Most common error field
        fields       = [c.get("field") for c in corrections if c.get("field")]
        most_common  = Counter(fields).most_common(1)[0] if fields else ("none", 0)

        return jsonify({
            "available":       True,
            "total":           len(corrections),
            "by_type":         by_type,
            "by_job_type":     by_job_type,
            "most_common_error": {"field": most_common[0], "count": most_common[1]},
            "trend_improving": trend_improving,
            "recent":          corrections[-20:][::-1],  # newest first
        })

    except Exception as e:
        logger.error(f"[corrections/summary] {e}")
        return jsonify({"available": False, "error": str(e)}), 500


@ai_bp.route('/monuments/nearby', methods=['GET'])
def ai_monuments_nearby():
    """
    Query the monument database for monuments near a lat/lon or State Plane coordinate.

    GET params:
        lat, lon         — WGS84 (preferred)
        x_sp, y_sp       — NM State Plane West ftUS (alternative)
        radius_ft        — search radius in feet (default 200)
        limit            — max results (default 20)
    """
    try:
        import sys as _sys
        surveyor_path = 'J:/Under Development/AI Surveyor'
        if surveyor_path not in _sys.path:
            _sys.path.insert(0, surveyor_path)
        from plat.monuments import MonumentDB

        db = MonumentDB()
        radius_ft = float(request.args.get('radius_ft', 200))
        limit     = int(request.args.get('limit', 20))

        lat  = request.args.get('lat')
        lon  = request.args.get('lon')
        x_sp = request.args.get('x_sp')
        y_sp = request.args.get('y_sp')

        if lat and lon:
            results = db.find_near_latlon(float(lat), float(lon), radius_ft, limit)
        elif x_sp and y_sp:
            results = db.find_near(float(x_sp), float(y_sp), radius_ft, limit)
        else:
            return jsonify({"error": "Provide lat/lon or x_sp/y_sp"}), 400

        return jsonify({
            "available": True,
            "count":     len(results),
            "radius_ft": radius_ft,
            "monuments": results,
        })

    except Exception as e:
        logger.error(f"[monuments/nearby] {e}")
        return jsonify({"available": False, "error": str(e)}), 500


@ai_bp.route('/monuments/stats', methods=['GET'])
def ai_monuments_stats():
    """Monument database statistics."""
    try:
        import sys as _sys
        if 'J:/Under Development/AI Surveyor' not in _sys.path:
            _sys.path.insert(0, 'J:/Under Development/AI Surveyor')
        from plat.monuments import MonumentDB
        return jsonify({"available": True, **MonumentDB().stats()})
    except Exception as e:
        return jsonify({"available": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# FIELD DATA INGEST
# ══════════════════════════════════════════════════════════════════════════════

@ai_bp.route('/field-data/ingest', methods=['POST'])
def ai_field_data_ingest():
    """
    Ingest raw field data from a total station or GPS data collector.

    Accepts:
      • multipart/form-data  with file= field (RW5, CSV, GSI, SDR)
      • application/json     with { text, fmt, job_number, job_type, job_date }
      • text/plain           raw file content

    Returns:
    {
        success: bool,
        format_detected: str,
        total_points: int,
        monuments: int,         # set + found
        topo: int,
        control: int,
        ingested_to_db: int,    # monument records added to DB
        traverse_closure: { precision, error_ft, ok },
        desc_counts: { CODE: count, ... },
        points: [ { pt, n, e, z, desc, desc_full, mon_type, condition }, ... ]
    }
    """
    try:
        import sys as _sys
        if 'J:/Under Development/AI Surveyor' not in _sys.path:
            _sys.path.insert(0, 'J:/Under Development/AI Surveyor')

        from plat.field_ingest import (
            parse_field_data, detect_format,
            ingest_monuments_from_field_points,
            compute_traverse_closure, ingest_report,
        )

        # ── Extract raw text from request ────────────────────────────────
        text       = ""
        job_number = ""
        job_type   = ""
        job_date   = ""
        fmt        = "auto"

        ct = request.content_type or ""
        if "multipart" in ct:
            f = request.files.get("file")
            if not f:
                return jsonify({"success": False, "error": "No file in request"}), 400
            text = f.read().decode("utf-8", errors="replace")
            # Hint from filename extension
            ext = Path(f.filename).suffix.lower()
            if ext in (".rw5", ".raw", ".r5"):   fmt = "rw5"
            elif ext == ".gsi":                   fmt = "gsi"
            elif ext == ".sdr":                   fmt = "sdr"
            elif ext in (".csv", ".txt"):         fmt = "csv"
            job_number = request.form.get("job_number", "")
            job_type   = request.form.get("job_type",   "")
            job_date   = request.form.get("job_date",   "")
        elif "json" in ct:
            body       = request.get_json(force=True) or {}
            text       = body.get("text", "")
            fmt        = body.get("fmt",  "auto")
            job_number = str(body.get("job_number", ""))
            job_type   = body.get("job_type",  "")
            job_date   = body.get("job_date",  "")
        else:
            # Plain text body
            text = request.get_data(as_text=True)

        if not text.strip():
            return jsonify({"success": False, "error": "No field data content"}), 400

        # ── Parse ────────────────────────────────────────────────────────
        fmt_detected = fmt if fmt != "auto" else detect_format(text)
        points = parse_field_data(text=text, fmt=fmt_detected)

        if not points:
            return jsonify({
                "success": False,
                "error": f"No points parsed from {fmt_detected.upper()} data",
                "format_detected": fmt_detected,
            }), 422

        # ── Monument DB ingest ───────────────────────────────────────────
        ingested = 0
        if job_number:
            ingested = ingest_monuments_from_field_points(
                points, job_number=job_number,
                job_type=job_type, survey_date=job_date,
            )

        # ── Reports ──────────────────────────────────────────────────────
        rep      = ingest_report(points)
        closure  = compute_traverse_closure(points)

        logger.info(
            f"[field-data/ingest] job={job_number or '?'}  fmt={fmt_detected}  "
            f"pts={rep['total']}  monuments={rep['monuments']}  "
            f"ingested={ingested}"
        )

        return jsonify({
            "success":          True,
            "format_detected":  fmt_detected,
            "job_number":       job_number,
            "total_points":     rep["total"],
            "monuments":        rep["monuments"],
            "set":              rep["set"],
            "found":            rep["found"],
            "not_found":        rep["not_found"],
            "topo":             rep["topo"],
            "control":          rep["control"],
            "ingested_to_db":   ingested,
            "elev_range":       rep["elev_range"],
            "traverse_closure": closure,
            "desc_counts":      rep["desc_counts"],
            "points": [p.to_dict() for p in points[:500]],  # cap for payload size
        })

    except Exception as e:
        logger.error(f"[field-data/ingest] {e}")
        import traceback as _tb
        return jsonify({"success": False, "error": str(e),
                        "traceback": _tb.format_exc()[-500:]}), 500


@ai_bp.route('/field-data/desc-codes', methods=['GET'])
def ai_field_desc_codes():
    """Return the description code registry for the UI autocomplete."""
    try:
        import sys as _sys
        if 'J:/Under Development/AI Surveyor' not in _sys.path:
            _sys.path.insert(0, 'J:/Under Development/AI Surveyor')
        from plat.field_ingest import DESC_CODES, MONUMENT_CODES, TOPO_CODES, CONTROL_CODES
        return jsonify({
            "available":     True,
            "codes":         DESC_CODES,
            "monument_codes": list(MONUMENT_CODES),
            "topo_codes":    list(TOPO_CODES),
            "control_codes": list(CONTROL_CODES),
        })
    except Exception as e:
        return jsonify({"available": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# BRAIN — Pipeline Orchestrator, QC, AI Decision Log
# ══════════════════════════════════════════════════════════════════════════════

def _brain_path():
    import sys as _sys
    p = 'J:/Under Development/AI Surveyor'
    if p not in _sys.path:
        _sys.path.insert(0, p)


@ai_bp.route('/pipeline/status', methods=['GET'])
def ai_pipeline_status():
    """GET /api/ai/pipeline/status — all active jobs and their pipeline stage."""
    try:
        _brain_path()
        from brain.pipeline import pipeline_status, start_orchestrator
        start_orchestrator(poll_interval_sec=300)
        return jsonify({"success": True, **pipeline_status()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route('/pipeline/job/<job_number>', methods=['GET'])
def ai_pipeline_job(job_number):
    """GET /api/ai/pipeline/job/<n> — state + AI timeline for one job."""
    try:
        _brain_path()
        from brain.pipeline import get_job
        from brain.ai_log import job_timeline
        return jsonify({
            "success":  True,
            "job":      get_job(str(job_number)),
            "timeline": job_timeline(str(job_number)),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route('/pipeline/register', methods=['POST'])
def ai_pipeline_register():
    """POST /api/ai/pipeline/register — register inquiry with orchestrator."""
    try:
        _brain_path()
        body = request.get_json(force=True) or {}
        from brain.pipeline import register_inquiry, start_orchestrator
        start_orchestrator(poll_interval_sec=300)
        job = register_inquiry(
            job_number   = str(body.get("job_number", "")),
            client_name  = body.get("client_name", ""),
            job_type     = body.get("job_type", ""),
            inquiry_text = body.get("inquiry_text", ""),
            inquiry_id   = body.get("inquiry_id", ""),
            upc          = body.get("upc", ""),
            job_date     = body.get("job_date", ""),
        )
        return jsonify({"success": True, "job": job})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route('/pipeline/advance', methods=['POST'])
def ai_pipeline_advance():
    """POST /api/ai/pipeline/advance — manually advance a job stage."""
    try:
        _brain_path()
        body       = request.get_json(force=True) or {}
        job_number = str(body.get("job_number", ""))
        stage      = body.get("stage", "")
        note       = body.get("note", "Manual override")
        if not job_number or not stage:
            return jsonify({"success": False, "error": "job_number and stage required"}), 400
        from brain.pipeline import advance_stage, get_job
        advance_stage(job_number, stage, note)
        return jsonify({"success": True, "job": get_job(job_number)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route('/ai-decisions', methods=['GET'])
def ai_decisions_log():
    """GET /api/ai/ai-decisions?job=2942&stage=qc_check&limit=50"""
    try:
        _brain_path()
        from brain.ai_log import get_decisions, stage_summary
        job_number  = request.args.get("job", "")
        stage       = request.args.get("stage", "")
        limit       = int(request.args.get("limit", 100))
        errors_only = request.args.get("errors", "").lower() == "true"
        summary_hrs = float(request.args.get("summary_hours", 0))
        if summary_hrs:
            return jsonify({"success": True, "summary": stage_summary(summary_hrs)})
        decisions = get_decisions(job_number=job_number, stage=stage,
                                  limit=limit, errors_only=errors_only)
        return jsonify({"success": True, "count": len(decisions), "decisions": decisions})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route('/qc/<job_number>', methods=['GET', 'POST'])
def ai_run_qc(job_number):
    """GET last QC report / POST to run QC now for a job."""
    try:
        _brain_path()
        from brain.quality_check import run_qc
        from brain.pipeline import get_job, update_job
        job = get_job(str(job_number))

        if request.method == 'GET':
            qc = job.get("qc_report")
            if qc:
                return jsonify({"success": True, "qc": qc})
            return jsonify({"success": False, "error": "No QC report yet"})

        body        = request.get_json(force=True) or {}
        job_type    = body.get("job_type", job.get("job_type", "BDY"))
        client_name = body.get("client_name", job.get("client_name", ""))
        qc = run_qc(
            job_number      = str(job_number),
            job_type        = job_type,
            client_name     = client_name,
            closure_results = body.get("closure", []),
            area_results    = body.get("area", []),
            adjoiner_report = body.get("adjoiner_report", {}),
            field_closure   = body.get("field_closure", {}),
            job_date        = body.get("job_date", ""),
        )
        update_job(str(job_number), qc_report=qc.to_dict(), qc_score=qc.score)
        return jsonify({"success": True, "passed": qc.passed, "score": qc.score,
                        "qc": qc.to_dict(), "summary": qc.summary_text()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ai_bp.route('/reconcile', methods=['POST'])
def ai_reconcile():
    """POST /api/ai/reconcile — compare field monuments to deed endpoints."""
    try:
        _brain_path()
        body       = request.get_json(force=True) or {}
        job_number = str(body.get("job_number", ""))
        calls      = body.get("calls", [])
        coords     = body.get("coords", [])
        raw_pts    = body.get("field_points", [])
        if not calls or not coords or not raw_pts:
            return jsonify({"success": False,
                            "error": "calls, coords, and field_points required"}), 400
        from plat.field_ingest import _make_point
        pts = []
        for p in raw_pts:
            try:
                pts.append(_make_point(str(p.get("pt","")), float(p.get("n",0)),
                                       float(p.get("e",0)), float(p.get("z",0)),
                                       str(p.get("desc",""))))
            except Exception:
                pass
        from brain.reconcile import reconcile
        rep = reconcile(calls=calls, field_points=pts, deed_coords=coords,
                        job_number=job_number)
        return jsonify({"success": True, "overall_ok": rep.overall_ok,
                        "summary": rep.summary(), "report": rep.to_dict()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
