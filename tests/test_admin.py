"""
tests/test_admin.py — Unit tests for the admin panel helpers and API routes.

All tests use a temp users.json — no server required for helper tests.
API route tests use Flask's test client with an admin password query param.
"""

import pytest
import app as app_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def temp_users_file(monkeypatch, tmp_path):
    """Redirect users.json to a fresh temp file for each test."""
    import helpers.auth as auth_mod
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(auth_mod, "_USERS_FILE", users_file)
    yield users_file


@pytest.fixture()
def two_users(temp_users_file):
    """Create one free and one pro user."""
    from helpers.auth import create_user, update_user
    free_user = create_user("free@example.com", "password123")
    pro_user  = create_user("pro@example.com", "password123")
    update_user(pro_user["id"], tier="pro",
                stripe_customer_id="cus_TEST", stripe_subscription_id="sub_TEST")
    return free_user, pro_user


@pytest.fixture()
def admin_client(monkeypatch, tmp_path):
    """Flask test client with admin password wired in via env."""
    import helpers.auth as auth_mod
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(auth_mod, "_USERS_FILE", users_file)
    monkeypatch.setenv("DEED_ADMIN_PASSWORD", "test-admin-pass-1234")

    # Re-import admin so it picks up the monkeypatched env var
    import importlib
    import helpers.admin as adm
    importlib.reload(adm)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ── check_admin_password ──────────────────────────────────────────────────────

class TestCheckAdminPassword:
    def test_correct_password(self, monkeypatch):
        monkeypatch.setenv("DEED_ADMIN_PASSWORD", "secret123")
        import importlib
        import helpers.admin as adm
        importlib.reload(adm)
        assert adm.check_admin_password("secret123") is True

    def test_wrong_password(self, monkeypatch):
        monkeypatch.setenv("DEED_ADMIN_PASSWORD", "secret123")
        import importlib
        import helpers.admin as adm
        importlib.reload(adm)
        assert adm.check_admin_password("wrongpass") is False

    def test_empty_password_rejected(self, monkeypatch):
        monkeypatch.setenv("DEED_ADMIN_PASSWORD", "secret123")
        import importlib
        import helpers.admin as adm
        importlib.reload(adm)
        assert adm.check_admin_password("") is False


# ── list_users_summary ────────────────────────────────────────────────────────

class TestListUsersSummary:
    def test_returns_all_users(self, two_users):
        from helpers.admin import list_users_summary
        summaries = list_users_summary()
        assert len(summaries) == 2

    def test_no_password_hash_exposed(self, two_users):
        from helpers.admin import list_users_summary
        for u in list_users_summary():
            assert "password_hash" not in u

    def test_pro_user_has_stripe(self, two_users):
        from helpers.admin import list_users_summary
        pro = next(u for u in list_users_summary() if u["tier"] == "pro")
        assert pro["has_stripe"] is True
        assert pro["stripe_cus_id"] == "cus_TEST"

    def test_free_user_no_stripe(self, two_users):
        from helpers.admin import list_users_summary
        free = next(u for u in list_users_summary() if u["tier"] == "free")
        assert free["has_stripe"] is False


# ── get_user_stats ────────────────────────────────────────────────────────────

class TestGetUserStats:
    def test_mrr_calculation(self, two_users):
        from helpers.admin import get_user_stats
        stats = get_user_stats()
        assert stats["total_users"] == 2
        assert stats["by_tier"]["pro"] == 1
        assert stats["by_tier"]["free"] == 1
        # 1 pro × $29 = $29
        assert stats["mrr_usd"] == 29

    def test_all_tiers_present(self, two_users):
        from helpers.admin import get_user_stats
        stats = get_user_stats()
        assert "free" in stats["by_tier"]
        assert "pro"  in stats["by_tier"]
        assert "team" in stats["by_tier"]


# ── admin_set_tier ────────────────────────────────────────────────────────────

class TestAdminSetTier:
    def test_upgrade_to_pro(self, two_users):
        from helpers.admin import admin_set_tier
        from helpers.auth import get_user
        free_user, _ = two_users
        admin_set_tier(free_user["id"], "pro")
        u = get_user(free_user["id"])
        assert u["tier"] == "pro"

    def test_invalid_tier_raises(self, two_users):
        from helpers.admin import admin_set_tier
        free_user, _ = two_users
        with pytest.raises(ValueError, match="Invalid tier"):
            admin_set_tier(free_user["id"], "enterprise")


# ── admin_toggle_active ───────────────────────────────────────────────────────

class TestAdminToggleActive:
    def test_deactivate_user(self, two_users):
        from helpers.admin import admin_toggle_active
        from helpers.auth import get_user
        free_user, _ = two_users
        admin_toggle_active(free_user["id"], False)
        u = get_user(free_user["id"])
        assert u["active"] is False

    def test_reactivate_user(self, two_users):
        from helpers.admin import admin_toggle_active
        from helpers.auth import get_user, update_user
        free_user, _ = two_users
        update_user(free_user["id"], active=False)
        admin_toggle_active(free_user["id"], True)
        u = get_user(free_user["id"])
        assert u["active"] is True


# ── admin_reset_searches ──────────────────────────────────────────────────────

class TestAdminResetSearches:
    def test_resets_counter(self, two_users):
        from helpers.admin import admin_reset_searches
        from helpers.auth import get_user, update_user
        free_user, _ = two_users
        update_user(free_user["id"], search_count_this_month=8)
        admin_reset_searches(free_user["id"])
        u = get_user(free_user["id"])
        assert u["search_count_this_month"] == 0


# ── API Routes ────────────────────────────────────────────────────────────────

class TestAdminApiAuth:
    def test_correct_password_succeeds(self, admin_client, monkeypatch):
        resp = admin_client.post("/api/admin/auth",
                                 json={"password": "test-admin-pass-1234"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "stats" in data

    def test_wrong_password_rejected(self, admin_client):
        resp = admin_client.post("/api/admin/auth",
                                 json={"password": "wrong"})
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["success"] is False

    def test_empty_password_rejected(self, admin_client):
        resp = admin_client.post("/api/admin/auth", json={})
        assert resp.status_code == 403


class TestAdminApiUsers:
    def test_list_users_requires_password(self, admin_client):
        resp = admin_client.get("/api/admin/users?password=wrong")
        assert resp.status_code == 403

    def test_list_users_with_correct_password(self, admin_client, two_users):
        resp = admin_client.get("/api/admin/users?password=test-admin-pass-1234")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["users"]) == 2

    def test_patch_user_tier(self, admin_client, two_users):
        free_user, _ = two_users
        resp = admin_client.patch(f"/api/admin/users/{free_user['id']}",
                                  json={"password": "test-admin-pass-1234",
                                        "tier": "pro"})
        assert resp.status_code == 200
        # Should return updated users list
        from helpers.auth import get_user
        u = get_user(free_user["id"])
        assert u["tier"] == "pro"

    def test_patch_invalid_tier_returns_400(self, admin_client, two_users):
        free_user, _ = two_users
        resp = admin_client.patch(f"/api/admin/users/{free_user['id']}",
                                  json={"password": "test-admin-pass-1234",
                                        "tier": "enterprise"})
        assert resp.status_code == 400

    def test_patch_reset_searches(self, admin_client, two_users):
        from helpers.auth import update_user, get_user
        free_user, _ = two_users
        update_user(free_user["id"], search_count_this_month=7)
        resp = admin_client.patch(f"/api/admin/users/{free_user['id']}",
                                  json={"password": "test-admin-pass-1234",
                                        "reset_searches": True})
        assert resp.status_code == 200
        u = get_user(free_user["id"])
        assert u["search_count_this_month"] == 0
