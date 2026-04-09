"""
routes/research.py — arXiv Research Intelligence Tab
======================================================
A lightweight Blueprint that wires the Algorithm Discovery Engine's
arXiv miner directly into Deed & Plat Helper.

Endpoints:
  GET  /api/research/query?q=<topic>&max=10
       → Search arXiv and return structured paper list + key concepts

  GET  /api/research/domains
       → List available pre-mined domain graphs (pathfinding, neuro, etc.)

  GET  /api/research/novelty?q=<algorithm_description>
       → Quick novelty check: how similar is a description to arXiv papers?

Rate-limited: 2 req/s max to respect arXiv's guidelines.
Non-blocking: uses httpx with a short timeout so it never hangs the app.

Usage in app.py (already configured if you registered this blueprint):
    from routes.research import research_bp
    app.register_blueprint(research_bp)
"""

import re
import time
from pathlib import Path
from collections import Counter

from flask import Blueprint, request, jsonify
from loguru import logger

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False
    logger.warning("[research] httpx not installed — arXiv queries disabled")

research_bp = Blueprint("research", __name__)

# ── arXiv config ──────────────────────────────────────────────────────────────
ARXIV_BASE   = "http://export.arxiv.org/api/query"
MAX_RESULTS  = 15       # cap per query to stay light
REQUEST_TO   = 8.0      # seconds timeout
_last_call   = 0.0
_MIN_GAP     = 0.5      # minimum seconds between outbound arXiv calls

# ── Noise words to strip when extracting concepts ─────────────────────────────
_STOP = {
    "the", "a", "an", "of", "in", "for", "and", "or", "with", "to", "on",
    "is", "are", "this", "that", "via", "using", "based", "novel", "new",
    "approach", "method", "model", "framework", "paper", "study", "we",
    "our", "show", "propose", "present", "results", "data", "from", "by",
    "at", "as", "was", "be", "it", "its", "which", "that", "has", "can",
    "between", "into", "than", "through", "about", "over", "more", "also",
    "both", "each", "their", "these", "such", "other", "when", "where",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rate_limited_get(url: str, params: dict) -> dict | None:
    """Fetch arXiv with rate limiting + timeout. Returns parsed XML or None."""
    global _last_call
    if not _HTTPX_OK:
        return None

    # Enforce minimum gap between calls
    gap = time.time() - _last_call
    if gap < _MIN_GAP:
        time.sleep(_MIN_GAP - gap)

    try:
        resp = httpx.get(url, params=params, timeout=REQUEST_TO)
        _last_call = time.time()
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"[research] arXiv request failed: {e}")
        return None


def _parse_arxiv(xml_text: str) -> list[dict]:
    """Parse arXiv Atom XML into a list of paper dicts."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(xml_text, "xml")
    papers = []
    for entry in soup.find_all("entry"):
        title   = (entry.find("title") or {}).get_text(strip=True)
        summary = (entry.find("summary") or {}).get_text(strip=True)[:300]
        link    = (entry.find("id") or {}).get_text(strip=True)
        authors = [a.find("name").get_text(strip=True)
                   for a in entry.find_all("author")
                   if a.find("name")][:3]
        published = (entry.find("published") or {}).get_text(strip=True)[:10]

        if title:
            papers.append({
                "title":     title,
                "summary":   summary,
                "url":       link,
                "authors":   authors,
                "published": published,
            })
    return papers


def _extract_concepts(papers: list[dict], top_n: int = 12) -> list[str]:
    """Extract the most common meaningful words across all abstracts."""
    words = []
    for p in papers:
        text = (p.get("title", "") + " " + p.get("summary", "")).lower()
        words += [w for w in re.findall(r'\b[a-z]{4,}\b', text)
                  if w not in _STOP]
    counts = Counter(words)
    return [w for w, _ in counts.most_common(top_n)]


def _novelty_score(query_terms: list[str], papers: list[dict]) -> float:
    """
    Simple novelty: 0-100% based on how UNlike the query terms are
    to the retrieved papers. Higher = more novel.
    """
    if not papers or not query_terms:
        return 50.0

    match_counts = []
    for paper in papers:
        text   = (paper["title"] + " " + paper["summary"]).lower()
        hits   = sum(1 for t in query_terms if t.lower() in text)
        match_counts.append(hits / max(len(query_terms), 1))

    avg_match = sum(match_counts) / len(match_counts)
    return round((1.0 - avg_match) * 100, 1)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@research_bp.route("/api/research/query", methods=["GET"])
def api_research_query():
    """
    Search arXiv for papers related to a surveying/GIS topic.

    Query params:
      q    — search term (required)
      max  — max results (default 10, capped at 15)

    Returns:
      { papers: [...], concepts: [...], query: "...", count: N }
    """
    q   = (request.args.get("q") or "").strip()
    max_r = min(int(request.args.get("max", 10)), MAX_RESULTS)

    if not q:
        return jsonify({"success": False, "error": "q parameter required"}), 400

    if not _HTTPX_OK:
        return jsonify({"success": False, "error": "httpx not installed"}), 503

    logger.info(f"[research] arXiv query: '{q}' (max={max_r})")

    xml = _rate_limited_get(ARXIV_BASE, {
        "search_query": f"all:{q}",
        "max_results":  max_r,
        "sortBy":       "relevance",
    })

    if xml is None:
        return jsonify({"success": False, "error": "arXiv unreachable"}), 503

    papers   = _parse_arxiv(xml)
    concepts = _extract_concepts(papers)

    return jsonify({
        "success":  True,
        "query":    q,
        "count":    len(papers),
        "papers":   papers,
        "concepts": concepts,
    })


@research_bp.route("/api/research/novelty", methods=["GET"])
def api_research_novelty():
    """
    Check how novel a description/algorithm is against arXiv literature.

    Query params:
      q — description to check (required)

    Returns:
      { novelty_pct, verdict, closest_paper, all_papers }
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"success": False, "error": "q parameter required"}), 400

    # Extract search terms from the query
    terms = [w for w in re.findall(r'\b[a-z]{4,}\b', q.lower())
             if w not in _STOP][:6]

    if not terms:
        return jsonify({"success": False, "error": "No meaningful terms found"}), 400

    search_q = " ".join(terms[:4])
    xml = _rate_limited_get(ARXIV_BASE, {
        "search_query": f"all:{search_q}",
        "max_results":  10,
        "sortBy":       "relevance",
    })

    if xml is None:
        return jsonify({"success": False, "error": "arXiv unreachable"}), 503

    papers  = _parse_arxiv(xml)
    novelty = _novelty_score(terms, papers)

    if novelty >= 80:
        verdict = "HIGH NOVELTY — does not strongly resemble known published work"
        color   = "green"
    elif novelty >= 60:
        verdict = "MODERATE NOVELTY — shares traits with existing work but distinct"
        color   = "yellow"
    else:
        verdict = "LOW NOVELTY — closely resembles published approaches"
        color   = "red"

    closest = papers[0] if papers else None

    return jsonify({
        "success":      True,
        "query":        q,
        "novelty_pct":  novelty,
        "verdict":      verdict,
        "color":        color,
        "closest_paper": closest,
        "papers_checked": len(papers),
        "search_terms": terms,
    })


@research_bp.route("/api/research/domains", methods=["GET"])
def api_research_domains():
    """
    List which pre-mined domain graph JSON files are available locally,
    alongside their basic stats.
    Optional: pass ?engine_path=J:/AI Crap/Math Dude to specify where.
    """
    engine_path = Path(
        request.args.get("engine_path", r"J:\AI Crap\Math Dude")
    )

    domains = {
        "pathfinding": {"label": "Pathfinding Algorithms", "emoji": "🗺️"},
        "neuro":       {"label": "Neuroscience",           "emoji": "🧠"},
        "particles":   {"label": "Particle Physics",       "emoji": "⚛️"},
        "history":     {"label": "Ancient History",        "emoji": "🏺"},
        "materials":   {"label": "Materials Science",      "emoji": "🔬"},
    }

    results = []
    for domain, meta in domains.items():
        gfile = engine_path / f"{domain}_graph_data.json"
        if gfile.exists():
            try:
                import json
                data = json.loads(gfile.read_text(encoding="utf-8"))
                results.append({
                    "domain":   domain,
                    "label":    meta["label"],
                    "emoji":    meta["emoji"],
                    "nodes":    len(data.get("nodes", [])),
                    "papers":   len(data.get("paper_nodes", [])),
                    "edges":    len(data.get("edges", [])),
                    "file":     str(gfile),
                    "available": True,
                })
            except Exception as e:
                results.append({"domain": domain, "available": False, "error": str(e)})
        else:
            results.append({"domain": domain, "label": meta["label"],
                            "emoji": meta["emoji"], "available": False})

    available = sum(1 for r in results if r.get("available"))
    return jsonify({
        "success":   True,
        "domains":   results,
        "available": available,
        "total":     len(results),
        "engine_path": str(engine_path),
    })
