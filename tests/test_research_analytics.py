"""
tests/test_research_analytics.py — Unit tests for research history analytics.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers.research_analytics import (
    compute_aggregate_stats, predict_job_complexity,
    score_session_completeness,
)


def _make_sessions(n=10, job_type="BDY", adj_count=6, completion=80):
    """Generate mock research session data for testing."""
    sessions = []
    for i in range(n):
        total = 1 + adj_count  # client + adjoiners
        both = int(total * completion / 100)
        sessions.append({
            "job_number":       2900 + i,
            "client_name":      f"CLIENT_{i}",
            "job_type":         job_type,
            "total_subjects":   total,
            "client_count":     1,
            "adjoiner_count":   adj_count,
            "deeds_saved":      both,
            "plats_saved":      both,
            "both_saved":       both,
            "completion_pct":   completion,
            "plat_cabinets":    ["C", "B"] if i % 2 == 0 else ["A"],
            "file_date":        f"2025-{(i % 12) + 1:02d}-15",
            "file_mtime":       1700000000 + i * 86400,
            "range_folder":     "2900-2999",
        })
    return sessions


class TestAggregateStats:
    def test_basic_stats(self):
        sessions = _make_sessions(10)
        stats = compute_aggregate_stats(sessions)
        assert stats["total_jobs"] == 10
        assert stats["avg_adjoiners"] == 6.0
        assert stats["median_adjoiners"] == 6
        assert stats["max_adjoiners"] == 6

    def test_empty_sessions(self):
        stats = compute_aggregate_stats([])
        assert stats["total_jobs"] == 0

    def test_jobs_by_type(self):
        sessions = _make_sessions(5, job_type="BDY") + _make_sessions(3, job_type="ILR")
        stats = compute_aggregate_stats(sessions)
        assert stats["jobs_by_type"]["BDY"] == 5
        assert stats["jobs_by_type"]["ILR"] == 3

    def test_cabinet_distribution(self):
        sessions = _make_sessions(10)
        stats = compute_aggregate_stats(sessions)
        dist = stats["cabinet_distribution"]
        assert "C" in dist
        assert "B" in dist
        assert "A" in dist

    def test_completion_tiers(self):
        sessions = (
            _make_sessions(3, completion=95) +   # excellent
            _make_sessions(2, completion=70) +   # good
            _make_sessions(2, completion=30) +   # partial
            _make_sessions(1, completion=5)      # minimal
        )
        stats = compute_aggregate_stats(sessions)
        assert stats["completion_tiers"]["excellent"] == 3
        assert stats["completion_tiers"]["good"] == 2
        assert stats["completion_tiers"]["partial"] == 2
        assert stats["completion_tiers"]["minimal"] == 1

    def test_date_range(self):
        sessions = _make_sessions(10)
        stats = compute_aggregate_stats(sessions)
        assert "oldest" in stats["date_range"]
        assert "newest" in stats["date_range"]


class TestPredictions:
    def test_simple_job(self):
        sessions = _make_sessions(20, adj_count=3)
        pred = predict_job_complexity(sessions, job_type="BDY")
        assert pred["predicted_complexity"] == "simple"
        assert pred["predicted_adjoiners"] == 3.0

    def test_moderate_job(self):
        sessions = _make_sessions(20, adj_count=7)
        pred = predict_job_complexity(sessions, job_type="BDY")
        assert pred["predicted_complexity"] == "moderate"

    def test_complex_job(self):
        sessions = _make_sessions(20, adj_count=15)
        pred = predict_job_complexity(sessions, job_type="BDY")
        assert pred["predicted_complexity"] == "complex"

    def test_confidence_high(self):
        sessions = _make_sessions(25)
        pred = predict_job_complexity(sessions)
        assert pred["confidence"] == "high"
        assert pred["similar_jobs_count"] == 25

    def test_confidence_medium(self):
        sessions = _make_sessions(10)
        pred = predict_job_complexity(sessions)
        assert pred["confidence"] == "medium"

    def test_confidence_low(self):
        sessions = _make_sessions(3)
        pred = predict_job_complexity(sessions)
        assert pred["confidence"] == "low"

    def test_no_sessions(self):
        pred = predict_job_complexity([])
        assert pred["confidence"] == "none"
        assert pred["predicted_adjoiners"] == 6  # safe default

    def test_type_filtering(self):
        sessions = _make_sessions(10, job_type="BDY", adj_count=5) + \
                   _make_sessions(5, job_type="ILR", adj_count=2)
        pred = predict_job_complexity(sessions, job_type="ILR")
        assert pred["similar_jobs_count"] == 5
        assert pred["predicted_adjoiners"] == 2.0

    def test_likely_cabinets(self):
        sessions = _make_sessions(20)
        pred = predict_job_complexity(sessions)
        assert isinstance(pred["likely_cabinets"], list)
        assert len(pred["likely_cabinets"]) > 0

    def test_adjoiner_range(self):
        sessions = _make_sessions(20, adj_count=8)
        pred = predict_job_complexity(sessions)
        assert "p25" in pred["adjoiner_range"]
        assert "p75" in pred["adjoiner_range"]


class TestSessionScoring:
    def test_complete_session(self):
        session = {
            "subjects": [
                {"name": "Client", "type": "client", "deed_saved": True, "plat_saved": True},
                {"name": "Adj 1", "type": "adjoiner", "deed_saved": True, "plat_saved": True},
            ]
        }
        score = score_session_completeness(session)
        assert score["overall_score"] == 100
        assert score["status"] == "complete"
        assert len(score["missing_items"]) == 0

    def test_partial_session(self):
        session = {
            "subjects": [
                {"name": "Client", "type": "client", "deed_saved": True, "plat_saved": True},
                {"name": "Adj 1", "type": "adjoiner", "deed_saved": True, "plat_saved": False},
                {"name": "Adj 2", "type": "adjoiner", "deed_saved": False, "plat_saved": False},
            ]
        }
        score = score_session_completeness(session)
        assert 0 < score["overall_score"] < 100
        assert score["status"] == "in_progress"
        assert len(score["missing_items"]) == 2

    def test_empty_session(self):
        score = score_session_completeness({"subjects": []})
        assert score["overall_score"] == 0
        assert score["status"] == "just_started"

    def test_missing_items_detail(self):
        session = {
            "subjects": [
                {"name": "Client", "type": "client", "deed_saved": True, "plat_saved": False},
            ]
        }
        score = score_session_completeness(session)
        assert len(score["missing_items"]) == 1
        assert "plat" in score["missing_items"][0]["needs"]
        assert "deed" not in score["missing_items"][0]["needs"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
