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
            "available": emb_status.get("model_loaded", False),
            "documents": emb_status.get("document_count", 0),
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
    return jsonify({"answer": answer, "model": "mistral:7b"})


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

    return jsonify(kg.graph_stats())


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
