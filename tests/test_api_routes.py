"""
tests/test_api_routes.py
=========================
Integration tests for critical Flask API routes.

Uses Flask's test client so no actual network calls are made to 1stnmtitle.com.
Tests validate route wiring, JSON response shapes, error handling, and session
isolation via the profile_id cookie.

Run with:  py -m pytest tests/test_api_routes.py -v
"""

import pytest

# Import the Flask app
import app as app_module


@pytest.fixture
def client():
    """Create a Flask test client."""
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def client_with_profile():
    """Test client with a profile_id cookie set."""
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        c.set_cookie('profile_id', 'test_user_01')
        yield c


# ══════════════════════════════════════════════════════════════════════════════
# STATIC ROUTES
# ══════════════════════════════════════════════════════════════════════════════

class TestStaticRoutes:
    def test_index_html_served(self, client):
        """GET / should return the main HTML page."""
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'Deed' in resp.data or b'deed' in resp.data

    def test_app_js_served(self, client):
        """GET /app.js should return the frontend JavaScript."""
        resp = client.get('/app.js')
        assert resp.status_code == 200
        assert b'function' in resp.data

    def test_style_css_served(self, client):
        """GET /style.css should return the CSS stylesheet."""
        resp = client.get('/style.css')
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_get_config(self, client):
        """GET /api/config should return a JSON config object."""
        resp = client.get('/api/config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)


# ══════════════════════════════════════════════════════════════════════════════
# PROFILES
# ══════════════════════════════════════════════════════════════════════════════

class TestProfiles:
    def test_list_profiles(self, client):
        """GET /api/profiles should return a list."""
        resp = client.get('/api/profiles')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True
        assert isinstance(data.get("profiles"), list)


# ══════════════════════════════════════════════════════════════════════════════
# LOGIN / LOGOUT
# ══════════════════════════════════════════════════════════════════════════════

class TestLogin:
    def test_login_missing_form(self, client):
        """POST /api/login with no server should fail gracefully."""
        resp = client.post('/api/login', json={
            "username": "test", "password": "test", "remember": False
        })
        assert resp.status_code == 200
        data = resp.get_json()
        # Should fail (can't connect to 1stnmtitle.com from tests)
        # but shouldn't crash the server
        assert "success" in data

    def test_logout(self, client):
        """POST /api/logout should return success."""
        resp = client.post('/api/logout')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True


# ══════════════════════════════════════════════════════════════════════════════
# SESSION ISOLATION
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionIsolation:
    def test_different_profiles_get_different_sessions(self):
        """Two different profile_ids should produce different session objects."""
        from services.portal import _get_web_session
        s1 = _get_web_session("user_a")
        s2 = _get_web_session("user_b")
        assert s1 is not s2

    def test_same_profile_gets_same_session(self):
        """Same profile_id should always return the same session object."""
        from services.portal import _get_web_session
        s1 = _get_web_session("user_same")
        s2 = _get_web_session("user_same")
        assert s1 is s2

    def test_session_respects_cookie(self, client_with_profile):
        """_session() within a request context should read the profile_id cookie."""
        # We test this indirectly: the logout endpoint should clear
        # the per-user session's cookies, not the global default
        resp = client_with_profile.post('/api/logout')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True


# ══════════════════════════════════════════════════════════════════════════════
# DRIVE STATUS
# ══════════════════════════════════════════════════════════════════════════════

class TestDriveStatus:
    def test_drive_status_endpoint(self, client):
        """GET /api/drive-status should return drive detection info."""
        resp = client.get('/api/drive-status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert "drive_ok" in data


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH (requires network — test error handling only)
# ══════════════════════════════════════════════════════════════════════════════

class TestSearch:
    def test_search_requires_auth(self, client):
        """POST /api/search without auth should return 401."""
        resp = client.post('/api/search', json={"name": "", "operator": "contains"})
        assert resp.status_code == 401
        data = resp.get_json()
        assert data.get("success") is False

    def test_search_unauthenticated_returns_json(self, client):
        """POST /api/search without auth should still return valid JSON, never crash."""
        resp = client.post('/api/search', json={
            "name": "GARCIA", "operator": "begins with"
        })
        assert resp.status_code == 401
        assert resp.content_type.startswith('application/json')
        data = resp.get_json()
        assert "success" in data


# ══════════════════════════════════════════════════════════════════════════════
# PARSE CALLS
# ══════════════════════════════════════════════════════════════════════════════

class TestParseCalls:
    def test_parse_calls_basic(self, client):
        """POST /api/parse-calls should parse metes and bounds text."""
        resp = client.post('/api/parse-calls', json={
            "text": "N 45°30'00\" E, 125.50 feet then S 45°30'00\" W, 125.50 feet"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True
        assert len(data.get("calls", [])) >= 2

    def test_parse_calls_empty(self, client):
        """POST /api/parse-calls with no bearings should return empty calls."""
        resp = client.post('/api/parse-calls', json={"text": "lot 5 block 3"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True
        assert data.get("calls") == []


# ══════════════════════════════════════════════════════════════════════════════
# RECENT JOBS
# ══════════════════════════════════════════════════════════════════════════════

class TestRecentJobs:
    def test_recent_jobs_endpoint(self, client):
        """GET /api/recent-jobs should return a list."""
        resp = client.get('/api/recent-jobs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data.get("jobs", []), list)


# ══════════════════════════════════════════════════════════════════════════════
# KML / XML INDEX
# ══════════════════════════════════════════════════════════════════════════════

class TestXmlEndpoints:
    def test_index_status(self, client):
        """GET /api/xml/index-status should return status info."""
        resp = client.get('/api/xml/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert "success" in data

    def test_parcel_search_no_query(self, client):
        """POST /api/parcel-search with empty query should handle gracefully."""
        resp = client.post('/api/parcel-search', json={"query": ""})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "success" in data


# ══════════════════════════════════════════════════════════════════════════════
# ANALYZE DEED
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def authed_pro_client(tmp_path, monkeypatch):
    """Test client with a Pro user session cookie."""
    import helpers.auth as auth_mod
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(auth_mod, "_USERS_FILE", users_file)

    from helpers.auth import create_user, update_user, generate_token
    user = create_user("testpro@example.com", "password123")
    update_user(user["id"], tier="pro")
    token = generate_token(user["id"])

    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        c.set_cookie('deed_token', token)
        yield c


class TestAnalyzeDeed:
    def test_analyze_deed_requires_auth(self, client):
        """POST /api/analyze-deed without auth should return 401."""
        resp = client.post('/api/analyze-deed', json={"detail": {}})
        assert resp.status_code == 401
        data = resp.get_json()
        assert data.get("auth_required") is True

    def test_analyze_deed_endpoint(self, authed_pro_client):
        """POST /api/analyze-deed with pro auth should return a health-check result."""
        resp = authed_pro_client.post('/api/analyze-deed', json={
            "detail": {
                "Grantor": "GARCIA, JUAN",
                "Grantee": "RAEL, ADELA",
                "Location": "M568-482",
                "doc_no": "12345",
                "Recorded Date": "01/15/2020",
                "Other_Legal": "T5N R5E Section 12",
            }
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True
        result = data.get("analysis", {})
        assert "score" in result
        assert "grade" in result
        assert result["grade"] in ("good", "fair", "poor")
        assert "issues" in result
        assert "categories" in result

    def test_analyze_deed_empty_detail(self, authed_pro_client):
        """POST /api/analyze-deed with empty detail should flag missing parties."""
        resp = authed_pro_client.post('/api/analyze-deed', json={"detail": {}})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True
        # Should flag missing grantor + grantee as critical
        critical_count = sum(
            1 for i in data["analysis"]["issues"] if i["severity"] == "critical"
        )
        assert critical_count >= 2
