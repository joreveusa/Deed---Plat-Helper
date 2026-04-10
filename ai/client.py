"""
AI Client — Ollama LLM Interface
===================================
Adapted from AI Surveyor bridge/bluebridge_client.py.
Stripped down: no Blue-Bridge direct imports, no field mode, no TTS.

Uses httpx (per project conventions) to talk to Ollama.
"""

import json
from typing import Optional

import httpx
from loguru import logger

from ai import load_ai_config


# ── Config ──────────────────────────────────────────────────────────────────

_AI_CFG = load_ai_config()
_OLLAMA_URL = _AI_CFG.get("ollama_url", "http://localhost:11434")
_MODEL = _AI_CFG.get("model", "mistral:7b")
_EMBED_MODEL = _AI_CFG.get("embed_model", "nomic-embed-text")

# System prompt — tuned for Red Tail Surveying context
_SYSTEM_PROMPT = (
    "You are an AI surveying assistant for Red Tail Surveying in Taos County, NM. "
    "You help with deed and plat research, legal descriptions, metes-and-bounds "
    "parsing, adjoiner discovery, and boundary surveys. Be concise, accurate, "
    "and practical — this is for professional field use."
)


# ══════════════════════════════════════════════════════════════════════════════
# LOW-LEVEL OLLAMA FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_model(preferred: str) -> str:
    """Resolve the model name — if 'preferred' isn't available, find the closest match."""
    try:
        resp = httpx.get(f"{_OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            available = [m.get("name", "") for m in resp.json().get("models", [])]
            if preferred in available:
                return preferred
            # Try prefix match (e.g. "mistral:7b" → "mistral:7b-instruct-q4_K_M")
            base = preferred.split(":")[0]
            for name in available:
                if name.startswith(base + ":"):
                    logger.info(f"[AI] Model '{preferred}' not found — using '{name}' instead")
                    return name
    except Exception:
        pass
    return preferred


def ollama_chat(prompt: str, system: str = "", model: str = "",
                timeout: float = 120.0) -> str:
    """Send a chat completion request to Ollama.

    Tries /api/chat first (Ollama ≥ 0.1.14). Falls back to /api/generate
    for older or alternative Ollama binaries that don't expose /api/chat.
    """
    model = model or _MODEL
    model = _resolve_model(model)

    # ── Try /api/chat (modern Ollama) ─────────────────────────────────────
    chat_payload = {
        "model": model,
        "messages": [
            *([{"role": "system", "content": system}] if system else []),
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    try:
        resp = httpx.post(f"{_OLLAMA_URL}/api/chat", json=chat_payload, timeout=timeout)
        if resp.status_code != 404:
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
        # 404 → fall through to /api/generate
        logger.warning("[AI] /api/chat returned 404 — falling back to /api/generate")
    except httpx.ConnectError:
        return "[Ollama not running — start it with `ollama serve`]"
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404:
            return f"[Ollama error: {e}]"
        logger.warning("[AI] /api/chat returned 404 — falling back to /api/generate")
    except Exception as e:
        return f"[Ollama error: {e}]"

    # ── Fallback: /api/generate (older Ollama / AI Surveyor instance) ─────
    system_prefix = f"{system}\n\n" if system else ""
    generate_payload = {
        "model": model,
        "prompt": f"{system_prefix}{prompt}",
        "stream": False,
    }
    try:
        resp = httpx.post(f"{_OLLAMA_URL}/api/generate", json=generate_payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except httpx.ConnectError:
        return "[Ollama not running — start it with `ollama serve`]"
    except Exception as e:
        return f"[Ollama error: {e}]"


def ollama_embed(texts: list[str]) -> list[list[float]]:
    """Embed texts using Ollama's embedding API."""
    try:
        resp = httpx.post(
            f"{_OLLAMA_URL}/api/embed",
            json={"model": _EMBED_MODEL, "input": texts},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]
    except Exception:
        # Fallback to legacy single-text endpoint
        embeddings = []
        for text in texts:
            try:
                resp = httpx.post(
                    f"{_OLLAMA_URL}/api/embeddings",
                    json={"model": _EMBED_MODEL, "prompt": text},
                    timeout=30.0,
                )
                resp.raise_for_status()
                embeddings.append(resp.json()["embedding"])
            except Exception:
                embeddings.append([0.0] * 768)
        return embeddings


def ollama_healthy() -> bool:
    """Quick check — is Ollama responding?"""
    try:
        resp = httpx.get(f"{_OLLAMA_URL}/api/tags", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# AI CLIENT CLASS
# ══════════════════════════════════════════════════════════════════════════════

class AIClient:
    """High-level AI client for the Deed & Plat Helper."""

    def __init__(self):
        self._chroma_client = None
        self._rag_collection = None

    # ── Chat ─────────────────────────────────────────────────────────────

    def ask(self, prompt: str, system: str = "",
            model: str = "", timeout: float = 120.0) -> str:
        """Ask the LLM a question."""
        return ollama_chat(prompt, system=system or _SYSTEM_PROMPT,
                           model=model, timeout=timeout)

    def ask_about_research(self, question: str,
                           context: str = "") -> str:
        """Ask a question with optional research context."""
        full_prompt = question
        if context:
            full_prompt = (
                f"Research Context:\n{context}\n\n"
                f"Question: {question}"
            )
        return self.ask(full_prompt)

    # ── Specialized LLM Tasks ────────────────────────────────────────────

    def summarize_legal_description(self, text: str) -> str:
        """Ask the LLM to summarize a legal description in plain English."""
        return ollama_chat(
            f"Summarize this legal description in plain English. "
            f"Identify: property location, boundaries, area, and any easements.\n\n"
            f"Legal Description:\n{text}",
            system="You are a land surveying expert. Be precise and concise.",
        )

    def extract_entities_from_deed(self, text: str) -> dict:
        """Use LLM to extract structured entities from deed text."""
        result = ollama_chat(
            f"Extract the following from this deed text and respond as JSON:\n"
            f'{{"grantor": "...", "grantee": "...", "date": "...", '
            f'"consideration": "...", "property_type": "...", '
            f'"trs": "...", "subdivision": "...", '
            f'"lot": "...", "block": "...", '
            f'"easements": [...], "exceptions": [...], '
            f'"monuments_mentioned": [...]}}\n\n'
            f"Deed text:\n{text[:3000]}",
            system="You are a legal document parser. Output ONLY valid JSON.",
            timeout=60.0,
        )
        try:
            start = result.find("{")
            end = result.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(result[start:end])
        except Exception:
            pass
        return {}

    def suggest_adjoiners(self, legal_desc: str,
                          known_adjoiners: list[str] = None) -> str:
        """Ask LLM to identify potential adjoiners from a legal description."""
        known = ""
        if known_adjoiners:
            known = f"\n\nAlready-known adjoiners: {', '.join(known_adjoiners)}"
        return ollama_chat(
            f"Based on this legal description, identify any neighboring "
            f"property owners or adjoining properties mentioned. "
            f"List each one with a brief reason.{known}\n\n"
            f"Legal Description:\n{legal_desc[:3000]}",
            system=_SYSTEM_PROMPT,
        )

    # ── Health ───────────────────────────────────────────────────────────

    def health(self) -> dict:
        """Check AI subsystem health."""
        status = {
            "ollama": False,
            "ollama_url": _OLLAMA_URL,
            "model": _MODEL,
            "models_available": [],
        }
        try:
            resp = httpx.get(f"{_OLLAMA_URL}/api/tags", timeout=5)
            if resp.status_code == 200:
                status["ollama"] = True
                models = resp.json().get("models", [])
                status["models_available"] = [
                    m.get("name", "") for m in models
                ]
        except Exception:
            pass
        return status
