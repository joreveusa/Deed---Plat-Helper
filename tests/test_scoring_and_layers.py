"""Tests for relevance scoring and map overlay layer endpoints."""
import pytest
import json
import sys
import os

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════════
# SCORING TESTS — _score_search_result
# ═══════════════════════════════════════════════════════════════════════════════

from app import _score_search_result


class TestScoreBasic:
    """Verify that _score_search_result returns a score and tags."""

    def test_returns_score_and_tags(self):
        result = {"grantor": "DOE, JOHN", "instrument_type": "Warranty Deed"}
        scored = _score_search_result(result)
        assert "relevance_score" in scored
        assert "relevance_tags" in scored
        assert isinstance(scored["relevance_score"], (int, float))
        assert isinstance(scored["relevance_tags"], list)

    def test_empty_result_scores_zero(self):
        result = {}
        scored = _score_search_result(result)
        assert scored["relevance_score"] == 0
        assert scored["relevance_tags"] == []


class TestScoreDeeds:
    """Instrument type scoring: deeds > plats > mortgages."""

    def test_warranty_deed_gets_bonus(self):
        result = {"instrument_type": "Warranty Deed"}
        scored = _score_search_result(result)
        assert scored["relevance_score"] >= 15
        assert "deed" in scored["relevance_tags"]

    def test_plat_gets_bonus(self):
        result = {"instrument_type": "Plat Survey"}
        scored = _score_search_result(result)
        assert scored["relevance_score"] >= 12
        assert "plat" in scored["relevance_tags"]

    def test_mortgage_gets_penalty(self):
        result = {"instrument_type": "Mortgage"}
        scored = _score_search_result(result)
        assert scored["relevance_score"] <= 0  # clamped to 0 minimum

    def test_deed_outscores_mortgage(self):
        deed = _score_search_result({"instrument_type": "Warranty Deed"})
        mort = _score_search_result({"instrument_type": "Mortgage"})
        assert deed["relevance_score"] > mort["relevance_score"]


class TestScoreNames:
    """Client and adjoiner name matching."""

    def test_client_name_match_grantor(self):
        result = {"grantor": "MARTINEZ, JOSE", "instrument_type": "deed"}
        scored = _score_search_result(result, client_name="Martinez, Jose")
        assert "client_name" in scored["relevance_tags"]
        assert scored["relevance_score"] >= 30

    def test_client_name_match_grantee(self):
        result = {"grantee": "SMITH, WILLIAM", "instrument_type": "deed"}
        scored = _score_search_result(result, client_name="Smith")
        assert "client_name" in scored["relevance_tags"]

    def test_no_name_no_match(self):
        result = {"grantor": "JONES, ALICE", "instrument_type": "deed"}
        scored = _score_search_result(result, client_name="Martinez")
        assert "client_name" not in scored["relevance_tags"]

    def test_adjoiner_name_match(self):
        result = {"grantor": "VALDEZ, MARIA", "instrument_type": "deed"}
        scored = _score_search_result(
            result, adjoiner_names=["Valdez, Maria", "Lopez, Carlos"]
        )
        assert "adjoiner" in scored["relevance_tags"]
        assert scored["relevance_score"] >= 20

    def test_combined_client_and_deed_type(self):
        result = {"grantor": "RAEL, ADELA", "instrument_type": "Warranty Deed"}
        scored = _score_search_result(result, client_name="Rael, Adela")
        # Should get both client_name + deed bonuses
        assert scored["relevance_score"] >= 45
        assert "client_name" in scored["relevance_tags"]
        assert "deed" in scored["relevance_tags"]


class TestScoreSubdivision:
    """Subdivision match scoring."""

    def test_subdivision_match_in_location(self):
        result = {
            "location": "PINECREST SUBDIVISION LOT 12",
            "instrument_type": "deed",
        }
        scored = _score_search_result(result, client_subdivision="Pinecrest Subdivision")
        assert "same_subdivision" in scored["relevance_tags"]
        assert scored["relevance_score"] >= 25

    def test_no_subdivision_no_match(self):
        result = {"location": "M568-482", "instrument_type": "deed"}
        scored = _score_search_result(result, client_subdivision="Pinecrest")
        assert "same_subdivision" not in scored["relevance_tags"]


class TestScoreRecency:
    """Recency bonus."""

    def test_recent_date_gets_bonus(self):
        result = {"recorded_date": "2020-05-14", "instrument_type": "deed"}
        scored = _score_search_result(result)
        # Should get both deed (+15) and recency (+5 for post-2015)
        assert scored["relevance_score"] >= 20

    def test_old_date_no_recency(self):
        result = {"recorded_date": "1985-01-01", "instrument_type": "deed"}
        scored = _score_search_result(result)
        # Should get deed bonus but no recency
        assert scored["relevance_score"] == 15


class TestScoreTRS:
    """TRS matching between client and result."""

    def test_trs_section_match(self):
        result = {
            "location": "SECTION 12 T25N R12E",
            "instrument_type": "deed",
        }
        scored = _score_search_result(result, client_trs="T25N R12E Sec 12")
        assert "trs_match" in scored["relevance_tags"]
        assert scored["relevance_score"] >= 40

    def test_trs_section_no_match(self):
        result = {
            "location": "SECTION 15 T25N R12E",
            "instrument_type": "deed",
        }
        scored = _score_search_result(result, client_trs="T25N R12E Sec 12")
        assert "trs_match" not in scored["relevance_tags"]


class TestScoreCompound:
    """Test that multiple matching criteria compound correctly."""

    def test_full_match_high_score(self):
        """Client name + deed type + recent date = very high relevance."""
        result = {
            "grantor": "GARZA, VERONICA",
            "instrument_type": "Warranty Deed",
            "recorded_date": "2022-03-15",
        }
        scored = _score_search_result(result, client_name="Garza, Veronica")
        # client_name=30 + deed=15 + recency=5 = 50
        assert scored["relevance_score"] >= 50

    def test_adjoiner_deed_moderate_score(self):
        """Adjoiner name + deed type = moderate relevance."""
        result = {
            "grantor": "TRUJILLO, FRANK",
            "instrument_type": "Quitclaim Deed",
        }
        scored = _score_search_result(
            result, adjoiner_names=["Trujillo, Frank"]
        )
        # adjoiner=20 + deed=15 = 35
        assert scored["relevance_score"] >= 35


# ═══════════════════════════════════════════════════════════════════════════════
# MAP OVERLAY ENDPOINT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


class TestWaterRightsEndpoint:
    """Test /api/map-layers/water-rights proxy."""

    def test_returns_json_structure(self, client):
        resp = client.get("/api/map-layers/water-rights?minLat=36.3&maxLat=36.4&minLon=-105.7&maxLon=-105.5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "success" in data
        assert "features" in data

    def test_bbox_too_large_returns_empty(self, client):
        resp = client.get("/api/map-layers/water-rights?minLat=35.0&maxLat=37.0&minLon=-107.0&maxLon=-104.0")
        data = resp.get_json()
        assert data["success"] is True
        assert data["count"] == 0
        assert "message" in data


class TestSurveyMarksEndpoint:
    """Test /api/map-layers/survey-marks proxy."""

    def test_returns_json_structure(self, client):
        resp = client.get("/api/map-layers/survey-marks?lat=36.4&lon=-105.6&radius=1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "success" in data
        assert "marks" in data

    def test_radius_capped(self, client):
        """Radius is capped at 10 miles server-side, so huge radius shouldn't crash."""
        resp = client.get("/api/map-layers/survey-marks?lat=36.4&lon=-105.6&radius=500")
        assert resp.status_code == 200
