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


ai_bp = Blueprint('ai', __name__, url_prefix='/api/ai')


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

    Body: { "pairs": [{"keep": node_id, "merge": node_id}, ...] }
    OR:   { "auto": true, "threshold": 0.92 }  — auto-merge high-confidence dupes.

    Returns: { merged: int, skipped: int, graph_nodes: int, graph_edges: int }
    """
    from ai import get_knowledge_graph

    kg = get_knowledge_graph()
    if not kg:
        return jsonify({"available": False}), 503

    data = request.get_json(silent=True) or {}
    merged = 0
    skipped = 0

    if data.get("auto"):
        # Auto-merge: find all pairs above threshold and merge them
        threshold = float(data.get("threshold", 0.92))
        pairs = kg.find_duplicates(threshold=threshold, limit=500)
    else:
        pairs = data.get("pairs", [])

    for pair in pairs:
        keep_id = pair.get("keep", "")
        merge_id = pair.get("merge", "")
        if not keep_id or not merge_id or keep_id == merge_id:
            skipped += 1
            continue
        try:
            ok = kg.merge_duplicates(keep_id, merge_id)
            if ok:
                merged += 1
            else:
                skipped += 1
        except Exception as e:
            logger.warning(f"Merge failed ({keep_id} ← {merge_id}): {e}")
            skipped += 1

    if merged > 0:
        kg.save()

    return jsonify({
        "merged": merged,
        "skipped": skipped,
        "graph_nodes": kg.G.number_of_nodes(),
        "graph_edges": kg.G.number_of_edges(),
    })


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


