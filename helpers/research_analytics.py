"""
helpers/research_analytics.py — Historical research session analytics.

Scans all completed research.json files across job folders to extract
patterns and predict complexity for new jobs. Addresses the "no learning
from history" gap identified in the AI capabilities audit.

Capabilities:
  - Aggregate statistics across all completed jobs
  - Most common plat cabinets per TRS/area
  - Average adjoiner counts by job type
  - Job complexity prediction for new research sessions
  - Research completeness scoring
"""

import json
import re
import os
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime


# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH SESSION SCANNER
# ══════════════════════════════════════════════════════════════════════════════

def scan_all_research(survey_data_path: str) -> list[dict]:
    """Scan all job folders for research.json files.

    Returns a list of dicts, each representing one research session with
    normalized metadata from the file and folder structure.

    This is the raw data that feeds all analytics functions.
    """
    if not survey_data_path or not Path(survey_data_path).exists():
        return []

    sessions = []
    survey = Path(survey_data_path)

    for range_dir in survey.iterdir():
        if not range_dir.is_dir() or range_dir.name.startswith("00"):
            continue

        for job_dir in range_dir.iterdir():
            if not job_dir.is_dir():
                continue

            # Parse job number from folder name: "2937 SMITH, JOHN"
            m = re.match(r'^(\d{4})\s+(.*)', job_dir.name)
            if not m:
                continue

            job_number = int(m.group(1))
            client_name = m.group(2).strip()

            # Find sub-type folder: {job}-01-{TYPE} {last_name}
            for sub_dir in job_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                mt = re.match(r'^\d+-01-([A-Z]+)\s', sub_dir.name)
                if not mt:
                    continue

                job_type = mt.group(1)
                research_file = sub_dir / "E Research" / "research.json"

                if not research_file.exists():
                    continue

                try:
                    data = json.loads(research_file.read_text(encoding="utf-8"))
                    subjects = data.get("subjects", [])

                    # Compute metrics
                    total_subjects = len(subjects)
                    client_subjects = [s for s in subjects if s.get("type") == "client"]
                    adj_subjects = [s for s in subjects if s.get("type") == "adjoiner"]

                    deeds_saved = sum(1 for s in subjects if s.get("deed_saved"))
                    plats_saved = sum(1 for s in subjects if s.get("plat_saved"))
                    both_saved = sum(1 for s in subjects if s.get("deed_saved") and s.get("plat_saved"))

                    # Extract plat paths for cabinet analysis
                    plat_cabinets = []
                    for s in subjects:
                        plat_path = s.get("plat_path", "")
                        if plat_path:
                            cab_match = re.search(r'Cab(?:inet)?\s*([A-Fa-f])', plat_path, re.I)
                            if cab_match:
                                plat_cabinets.append(cab_match.group(1).upper())

                    # File modification time as proxy for "when was this job worked"
                    file_mtime = research_file.stat().st_mtime
                    file_date = datetime.fromtimestamp(file_mtime).strftime("%Y-%m-%d")

                    sessions.append({
                        "job_number":       job_number,
                        "client_name":      client_name,
                        "job_type":         job_type,
                        "total_subjects":   total_subjects,
                        "client_count":     len(client_subjects),
                        "adjoiner_count":   len(adj_subjects),
                        "deeds_saved":      deeds_saved,
                        "plats_saved":      plats_saved,
                        "both_saved":       both_saved,
                        "completion_pct":   round(both_saved / total_subjects * 100, 1) if total_subjects else 0,
                        "plat_cabinets":    plat_cabinets,
                        "file_date":        file_date,
                        "file_mtime":       file_mtime,
                        "range_folder":     range_dir.name,
                    })
                except Exception:
                    continue  # Skip corrupt files silently

    # Sort by job number (most recent first)
    sessions.sort(key=lambda s: s["job_number"], reverse=True)
    return sessions


# ══════════════════════════════════════════════════════════════════════════════
# AGGREGATE STATISTICS
# ══════════════════════════════════════════════════════════════════════════════

def compute_aggregate_stats(sessions: list[dict]) -> dict:
    """Compute aggregate statistics across all scanned research sessions.

    Returns a dict with:
      - total_jobs, total_subjects, total_deeds, total_plats
      - avg_adjoiners, median_adjoiners, max_adjoiners
      - avg_completion_pct
      - jobs_by_type: { BDY: 50, ILR: 12, ... }
      - cabinet_distribution: { A: 120, B: 95, C: 200, ... }
      - monthly_activity: [{ month: "2025-01", jobs: 5 }, ...]
      - completion_tiers: { excellent: n, good: n, partial: n, minimal: n }
    """
    if not sessions:
        return {"total_jobs": 0}

    total = len(sessions)
    adj_counts = [s["adjoiner_count"] for s in sessions]
    completions = [s["completion_pct"] for s in sessions]

    # Cabinet distribution
    all_cabinets = Counter()
    for s in sessions:
        for cab in s["plat_cabinets"]:
            all_cabinets[cab] += 1

    # Jobs by type
    type_counter = Counter(s["job_type"] for s in sessions)

    # Monthly activity (last 12 months)
    month_counter: dict[str, int] = defaultdict(int)
    for s in sessions:
        month = s["file_date"][:7]  # "YYYY-MM"
        month_counter[month] += 1
    monthly = [{"month": k, "jobs": v}
               for k, v in sorted(month_counter.items(), reverse=True)[:12]]

    # Completion tiers
    tiers = {"excellent": 0, "good": 0, "partial": 0, "minimal": 0}
    for c in completions:
        if c >= 90:
            tiers["excellent"] += 1
        elif c >= 60:
            tiers["good"] += 1
        elif c >= 25:
            tiers["partial"] += 1
        else:
            tiers["minimal"] += 1

    # Sort adj_counts for median
    adj_sorted = sorted(adj_counts)
    median_adj = (adj_sorted[total // 2] if total % 2 == 1
                  else round((adj_sorted[total // 2 - 1] + adj_sorted[total // 2]) / 2, 1))

    return {
        "total_jobs":           total,
        "total_subjects":       sum(s["total_subjects"] for s in sessions),
        "total_deeds":          sum(s["deeds_saved"] for s in sessions),
        "total_plats":          sum(s["plats_saved"] for s in sessions),
        "avg_adjoiners":        round(sum(adj_counts) / total, 1),
        "median_adjoiners":     median_adj,
        "max_adjoiners":        max(adj_counts),
        "avg_completion_pct":   round(sum(completions) / total, 1),
        "jobs_by_type":         dict(type_counter.most_common()),
        "cabinet_distribution": dict(all_cabinets.most_common()),
        "monthly_activity":     monthly,
        "completion_tiers":     tiers,
        "date_range": {
            "oldest": min(s["file_date"] for s in sessions),
            "newest": max(s["file_date"] for s in sessions),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# PREDICTIVE ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

def predict_job_complexity(
    sessions: list[dict],
    job_type: str = "BDY",
    trs: str = "",
) -> dict:
    """Predict complexity for a new job based on historical patterns.

    Uses the distribution of adjoiner counts, completion rates, and cabinet
    usage from similar past jobs (same type, optionally same TRS area).

    Returns:
      - predicted_adjoiners: expected count (mean of similar jobs)
      - predicted_complexity: "simple" | "moderate" | "complex"
      - likely_cabinets: top 3 cabinet letters by frequency
      - confidence: "high" (>20 similar jobs) | "medium" (5-20) | "low" (<5)
      - similar_jobs_count: how many jobs inform the prediction
    """
    if not sessions:
        return {
            "predicted_adjoiners": 6,
            "predicted_complexity": "moderate",
            "likely_cabinets": ["C", "B", "A"],
            "confidence": "none",
            "similar_jobs_count": 0,
        }

    # Filter to similar jobs
    similar = [s for s in sessions if s["job_type"] == job_type]

    # If TRS provided, boost weight for same-area jobs
    # (we don't have TRS in research.json directly, but we can use range_folder as proxy)
    if not similar:
        similar = sessions  # fall back to all jobs

    count = len(similar)
    adj_counts = [s["adjoiner_count"] for s in similar]
    avg_adj = round(sum(adj_counts) / count, 1) if count else 6

    # Complexity based on average adjoiners
    if avg_adj <= 4:
        complexity = "simple"
    elif avg_adj <= 10:
        complexity = "moderate"
    else:
        complexity = "complex"

    # Most likely cabinet letters
    cab_counter = Counter()
    for s in similar:
        for cab in s["plat_cabinets"]:
            cab_counter[cab] += 1
    likely_cabs = [c for c, _ in cab_counter.most_common(3)] or ["C", "B", "A"]

    # Confidence
    if count >= 20:
        confidence = "high"
    elif count >= 5:
        confidence = "medium"
    else:
        confidence = "low"

    # Percentile ranges
    adj_sorted = sorted(adj_counts)
    p25 = adj_sorted[int(count * 0.25)] if count >= 4 else adj_sorted[0]
    p75 = adj_sorted[int(count * 0.75)] if count >= 4 else adj_sorted[-1]

    return {
        "predicted_adjoiners":    avg_adj,
        "adjoiner_range":        {"p25": p25, "p75": p75},
        "predicted_complexity":   complexity,
        "likely_cabinets":        likely_cabs,
        "confidence":             confidence,
        "similar_jobs_count":     count,
        "avg_completion_pct":     round(sum(s["completion_pct"] for s in similar) / count, 1) if count else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH SESSION SCORING
# ══════════════════════════════════════════════════════════════════════════════

def score_session_completeness(session_data: dict) -> dict:
    """Score a current research session's completeness against historical norms.

    Returns:
      - overall_score: 0-100
      - deed_score: 0-100
      - plat_score: 0-100
      - status: "complete" | "in_progress" | "just_started"
      - missing_items: list of subjects still needing deeds/plats
    """
    subjects = session_data.get("subjects", [])
    if not subjects:
        return {"overall_score": 0, "status": "just_started", "missing_items": []}

    total = len(subjects)
    deeds = sum(1 for s in subjects if s.get("deed_saved"))
    plats = sum(1 for s in subjects if s.get("plat_saved"))
    both  = sum(1 for s in subjects if s.get("deed_saved") and s.get("plat_saved"))

    deed_score = round(deeds / total * 100) if total else 0
    plat_score = round(plats / total * 100) if total else 0
    overall    = round(both / total * 100) if total else 0

    # Missing items
    missing = []
    for s in subjects:
        needs = []
        if not s.get("deed_saved"):
            needs.append("deed")
        if not s.get("plat_saved"):
            needs.append("plat")
        if needs:
            missing.append({"name": s.get("name", "?"), "type": s.get("type", "?"), "needs": needs})

    if overall >= 90:
        status = "complete"
    elif overall >= 10:
        status = "in_progress"
    else:
        status = "just_started"

    return {
        "overall_score": overall,
        "deed_score":    deed_score,
        "plat_score":    plat_score,
        "status":        status,
        "total":         total,
        "deeds_found":   deeds,
        "plats_found":   plats,
        "both_found":    both,
        "missing_items": missing[:20],  # cap display
    }


# ══════════════════════════════════════════════════════════════════════════════
# CACHED ANALYTICS (avoid re-scanning for every request)
# ══════════════════════════════════════════════════════════════════════════════

_analytics_cache: dict | None = None
_analytics_cache_time: float = 0.0
_CACHE_TTL = 300.0  # 5 minutes

def get_analytics(survey_data_path: str, force_refresh: bool = False) -> dict:
    """Return cached analytics, refreshing if stale or forced.

    Returns a dict with:
      - stats: aggregate statistics
      - predictions: default predictions (BDY type)
      - scanned_jobs: count of jobs scanned
      - cache_age_sec: how old the cache is
    """
    import time
    global _analytics_cache, _analytics_cache_time

    now = time.time()
    if _analytics_cache and not force_refresh and (now - _analytics_cache_time) < _CACHE_TTL:
        _analytics_cache["cache_age_sec"] = round(now - _analytics_cache_time)
        return _analytics_cache

    sessions = scan_all_research(survey_data_path)
    stats = compute_aggregate_stats(sessions)
    prediction = predict_job_complexity(sessions)

    result = {
        "stats":        stats,
        "predictions":  prediction,
        "scanned_jobs": len(sessions),
        "cache_age_sec": 0,
    }

    _analytics_cache = result
    _analytics_cache_time = now
    return result
