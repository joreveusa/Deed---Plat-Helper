"""
AI Integration for Deed & Plat Helper
=======================================
Brings AI Surveyor smarts directly into the Deed Helper:
  - LLM chat via Ollama (ai.client)
  - ML predictions (ai.predictions)
  - Knowledge graph (ai.knowledge_graph)
  - Anomaly detection (ai.anomaly)
  - Legal description embeddings (ai.embeddings)

All modules are lazy-loaded and gracefully degrade if deps are missing.
"""

import json
from pathlib import Path

from loguru import logger


# ── Config ──────────────────────────────────────────────────────────────────

_CONFIG_FILE = Path(__file__).resolve().parents[1] / "config.json"

def load_ai_config() -> dict:
    """Return the 'ai' section from the Deed Helper config.json."""
    try:
        cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        return cfg.get("ai", {})
    except Exception:
        return {}


# ── Data directory ──────────────────────────────────────────────────────────

AI_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "ai"
AI_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Lazy singletons ────────────────────────────────────────────────────────

_ai_client = None
_predictor = None
_knowledge_graph = None
_anomaly_detector = None
_embeddings = None


def get_ai_client():
    """Return the singleton AIClient (Ollama LLM). Returns None if unavailable."""
    global _ai_client
    if _ai_client is not None:
        return _ai_client
    try:
        from ai.client import AIClient
        _ai_client = AIClient()
        return _ai_client
    except Exception as e:
        logger.debug(f"AI client not available: {e}")
        return None


def get_predictor():
    """Return the singleton SurveyPredictor (ML). Returns None if unavailable."""
    global _predictor
    if _predictor is not None:
        return _predictor
    try:
        from ai.predictions import SurveyPredictor
        _predictor = SurveyPredictor()
        return _predictor
    except Exception as e:
        logger.debug(f"ML predictor not available: {e}")
        return None


def get_knowledge_graph():
    """Return the singleton SurveyKnowledgeGraph. Returns None if unavailable."""
    global _knowledge_graph
    if _knowledge_graph is not None:
        return _knowledge_graph
    try:
        from ai.knowledge_graph import SurveyKnowledgeGraph
        _knowledge_graph = SurveyKnowledgeGraph()
        return _knowledge_graph
    except Exception as e:
        logger.debug(f"Knowledge graph not available: {e}")
        return None


def get_anomaly_detector():
    """Return the singleton AnomalyDetector. Returns None if unavailable."""
    global _anomaly_detector
    if _anomaly_detector is not None:
        return _anomaly_detector
    try:
        from ai.anomaly import AnomalyDetector
        _anomaly_detector = AnomalyDetector()
        return _anomaly_detector
    except Exception as e:
        logger.debug(f"Anomaly detector not available: {e}")
        return None


def get_embeddings():
    """Return the singleton LegalEmbeddingIndex. Returns None if unavailable."""
    global _embeddings
    if _embeddings is not None:
        return _embeddings
    try:
        from ai.embeddings import LegalEmbeddingIndex
        cfg = load_ai_config()
        ollama_url = cfg.get("ollama_url", "http://localhost:11434")
        _embeddings = LegalEmbeddingIndex(ollama_url=ollama_url)
        return _embeddings
    except Exception as e:
        logger.debug(f"Embeddings not available: {e}")
        return None
