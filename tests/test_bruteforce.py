"""
tests/test_bruteforce.py — Unit tests for login brute-force protection.
"""
import pytest
import helpers.auth as auth_mod


@pytest.fixture(autouse=True)
def temp_users(monkeypatch, tmp_path):
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(auth_mod, "_USERS_FILE", users_file)
    yield


@pytest.fixture()
def user():
    return auth_mod.create_user("locked@example.com", "password123")


class TestCheckLoginAllowed:
    def test_fresh_user_is_allowed(self, user):
        allowed, msg = auth_mod.check_login_allowed(user)
        assert allowed is True
        assert msg == ""

    def test_locked_user_is_blocked(self, user):
        from datetime import datetime, timedelta
        auth_mod.update_user(
            user["id"],
            failed_login_locked_until=(
                datetime.utcnow() + timedelta(minutes=15)
            ).isoformat()
        )
        u = auth_mod.get_user(user["id"])
        allowed, msg = auth_mod.check_login_allowed(u)
        assert allowed is False
        assert "locked" in msg.lower()
        assert "minute" in msg.lower()

    def test_expired_lockout_is_allowed(self, user):
        from datetime import datetime, timedelta
        auth_mod.update_user(
            user["id"],
            failed_login_locked_until=(
                datetime.utcnow() - timedelta(minutes=1)
            ).isoformat()
        )
        u = auth_mod.get_user(user["id"])
        allowed, _ = auth_mod.check_login_allowed(u)
        assert allowed is True


class TestRecordFailedLogin:
    def test_increments_counter(self, user):
        auth_mod.record_failed_login(user["id"])
        u = auth_mod.get_user(user["id"])
        assert u["failed_login_count"] == 1

    def test_locks_after_max_attempts(self, user):
        for _ in range(auth_mod._MAX_ATTEMPTS):
            auth_mod.record_failed_login(user["id"])
        u = auth_mod.get_user(user["id"])
        assert u.get("failed_login_locked_until") is not None

    def test_no_lock_before_max_attempts(self, user):
        for _ in range(auth_mod._MAX_ATTEMPTS - 1):
            auth_mod.record_failed_login(user["id"])
        u = auth_mod.get_user(user["id"])
        assert u.get("failed_login_locked_until") is None

    def test_ignores_unknown_user(self):
        # Should not raise
        auth_mod.record_failed_login("u_nonexistent")


class TestClearFailedLogins:
    def test_clears_counter_and_lockout(self, user):
        auth_mod.record_failed_login(user["id"])
        auth_mod.record_failed_login(user["id"])
        auth_mod.clear_failed_logins(user["id"])
        u = auth_mod.get_user(user["id"])
        assert "failed_login_count" not in u
        assert "failed_login_locked_until" not in u

    def test_ignores_unknown_user(self):
        auth_mod.clear_failed_logins("u_nonexistent")


class TestLoginFlowIntegration:
    """End-to-end: repeated bad password → lockout → 429 from the API route."""
    def test_lockout_via_api(self, monkeypatch, tmp_path):
        import app as app_module
        users_file = tmp_path / "users.json"
        monkeypatch.setattr(auth_mod, "_USERS_FILE", users_file)
        auth_mod.create_user("victim@example.com", "correct-pass")

        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            # Hammer with wrong password until lockout
            for _ in range(auth_mod._MAX_ATTEMPTS):
                r = c.post("/auth/login",
                           json={"email": "victim@example.com", "password": "wrong"})
                assert r.status_code == 401

            # Next attempt should be 429
            r = c.post("/auth/login",
                       json={"email": "victim@example.com", "password": "correct-pass"})
            assert r.status_code == 429
            data = r.get_json()
            assert data["success"] is False
            assert "locked" in data["error"].lower()

    def test_success_clears_counter_via_api(self, monkeypatch, tmp_path):
        import app as app_module
        users_file = tmp_path / "users.json"
        monkeypatch.setattr(auth_mod, "_USERS_FILE", users_file)
        auth_mod.create_user("bob@example.com", "right-pass")

        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            # Two bad attempts
            c.post("/auth/login", json={"email": "bob@example.com", "password": "bad"})
            c.post("/auth/login", json={"email": "bob@example.com", "password": "bad"})

            # Correct password — should succeed AND clear counter
            r = c.post("/auth/login",
                       json={"email": "bob@example.com", "password": "right-pass"})
            assert r.status_code == 200

            u = auth_mod.get_user(auth_mod.find_user_by_email("bob@example.com")["id"])
            assert "failed_login_count" not in u
