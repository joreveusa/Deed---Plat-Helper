"""
tests/test_rate_limit.py — Unit tests for the in-memory rate limiter.
"""
import time
import pytest

from helpers.rate_limit import rate_limit, rate_limit_ip, _windows


@pytest.fixture(autouse=True)
def clear_windows():
    """Reset rate-limit state before every test."""
    _windows.clear()
    yield
    _windows.clear()


# ── rate_limit_ip (programmatic API) ─────────────────────────────────────────

class TestRateLimitIp:
    def test_allows_under_limit(self):
        for _ in range(4):
            allowed, retry = rate_limit_ip("1.2.3.4", requests=5, window=60)
        assert allowed is True
        assert retry == 0

    def test_blocks_at_limit(self):
        for _ in range(5):
            rate_limit_ip("1.2.3.4", requests=5, window=60)
        allowed, retry = rate_limit_ip("1.2.3.4", requests=5, window=60)
        assert allowed is False
        assert retry > 0

    def test_different_ips_are_independent(self):
        for _ in range(5):
            rate_limit_ip("1.1.1.1", requests=5, window=60)
        allowed, _ = rate_limit_ip("2.2.2.2", requests=5, window=60)
        assert allowed is True

    def test_window_expiry(self):
        # Fill up with a 1-second window
        for _ in range(3):
            rate_limit_ip("3.3.3.3", requests=3, window=1)
        blocked, _ = rate_limit_ip("3.3.3.3", requests=3, window=1)
        assert blocked is False

        # Wait for window to expire
        time.sleep(1.1)
        allowed, _ = rate_limit_ip("3.3.3.3", requests=3, window=1)
        assert allowed is True


# ── @rate_limit decorator ─────────────────────────────────────────────────────

class TestRateLimitDecorator:
    """Tests using a minimal Flask test app."""

    @pytest.fixture()
    def app(self):
        from flask import Flask, jsonify
        a = Flask(__name__)
        a.config["TESTING"] = True

        @a.route("/ping", methods=["GET", "POST"])
        @rate_limit(requests=3, window=60, key="test_ping")
        def ping():
            return jsonify({"ok": True})

        return a

    def test_allows_under_limit(self, app):
        with app.test_client() as c:
            for _ in range(3):
                r = c.get("/ping")
                assert r.status_code == 200

    def test_blocks_on_limit(self, app):
        with app.test_client() as c:
            for _ in range(3):
                c.get("/ping")
            r = c.get("/ping")
            assert r.status_code == 429

    def test_429_response_body(self, app):
        with app.test_client() as c:
            for _ in range(3):
                c.get("/ping")
            r = c.get("/ping")
            data = r.get_json()
            assert data["success"] is False
            assert "retry_after" in data
            assert data["retry_after"] > 0

    def test_retry_after_header(self, app):
        with app.test_client() as c:
            for _ in range(3):
                c.get("/ping")
            r = c.get("/ping")
            assert "Retry-After" in r.headers

    def test_post_and_get_share_bucket(self, app):
        """GET and POST to same endpoint share the same rate-limit bucket."""
        with app.test_client() as c:
            c.get("/ping")
            c.get("/ping")
            c.post("/ping")          # 3rd request — fills bucket
            r = c.get("/ping")       # 4th — should be blocked
            assert r.status_code == 429
