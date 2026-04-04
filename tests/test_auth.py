"""
tests/test_auth.py — Unit tests for SaaS auth + subscription gating.

Runs in CI without Flask server or external services.
"""

import os
import json
import tempfile
import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def temp_users_file(monkeypatch, tmp_path):
    """Redirect users.json to a fresh temp file for each test."""
    users_file = tmp_path / "users.json"
    # Monkeypatch the path in the auth module
    import helpers.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_USERS_FILE", users_file)
    yield users_file


@pytest.fixture()
def test_user(temp_users_file):
    """Create a standard free-tier user and return it."""
    from helpers.auth import create_user
    return create_user("test@example.com", "password123")


# ── Auth: create_user ─────────────────────────────────────────────────────────

class TestCreateUser:
    def test_creates_user(self, temp_users_file):
        from helpers.auth import create_user, find_user_by_email
        u = create_user("alice@example.com", "password123")
        assert u["email"] == "alice@example.com"
        assert u["tier"] == "free"
        assert "password_hash" in u
        # Should be findable by email
        found = find_user_by_email("alice@example.com")
        assert found is not None

    def test_rejects_duplicate_email(self, temp_users_file):
        from helpers.auth import create_user
        create_user("alice@example.com", "password123")
        with pytest.raises(ValueError, match="already exists"):
            create_user("ALICE@EXAMPLE.COM", "different")

    def test_rejects_short_password(self, temp_users_file):
        from helpers.auth import create_user
        with pytest.raises(ValueError, match="8 characters"):
            create_user("bob@example.com", "short")

    def test_rejects_invalid_email(self, temp_users_file):
        from helpers.auth import create_user
        with pytest.raises(ValueError, match="Invalid email"):
            create_user("notanemail", "password123")

    def test_email_normalized_to_lowercase(self, temp_users_file):
        from helpers.auth import create_user
        u = create_user("UpperCase@Example.COM", "password123")
        assert u["email"] == "uppercase@example.com"


# ── Auth: verify_password ─────────────────────────────────────────────────────

class TestVerifyPassword:
    def test_correct_password(self, test_user):
        from helpers.auth import verify_password
        assert verify_password("password123", test_user["password_hash"]) is True

    def test_wrong_password(self, test_user):
        from helpers.auth import verify_password
        assert verify_password("wrongpassword", test_user["password_hash"]) is False

    def test_empty_password(self, test_user):
        from helpers.auth import verify_password
        assert verify_password("", test_user["password_hash"]) is False


# ── Auth: tokens ──────────────────────────────────────────────────────────────

class TestTokens:
    def test_generate_and_verify(self, test_user):
        from helpers.auth import generate_token, verify_token
        token = generate_token(test_user["id"])
        assert verify_token(token) == test_user["id"]

    def test_invalid_token(self):
        from helpers.auth import verify_token
        assert verify_token("invalid.garbage.token") is None

    def test_tampered_token(self, test_user):
        from helpers.auth import generate_token, verify_token
        token = generate_token(test_user["id"])
        tampered = token[:-5] + "XXXXX"
        assert verify_token(tampered) is None


# ── Auth: update_user ─────────────────────────────────────────────────────────

class TestUpdateUser:
    def test_update_tier(self, test_user):
        from helpers.auth import update_user, get_user
        update_user(test_user["id"], tier="pro")
        updated = get_user(test_user["id"])
        assert updated["tier"] == "pro"

    def test_cannot_change_id(self, test_user):
        from helpers.auth import update_user, get_user
        update_user(test_user["id"], id="hacked_id")
        u = get_user(test_user["id"])
        assert u["id"] == test_user["id"]  # unchanged

    def test_update_nonexistent_user(self):
        from helpers.auth import update_user
        result = update_user("u_nonexistent", tier="pro")
        assert result is None


# ── Subscription: check_search_quota ─────────────────────────────────────────

class TestSearchQuota:
    def test_free_user_has_quota(self, test_user):
        from helpers.subscription import check_search_quota
        allowed, msg = check_search_quota(test_user)
        assert allowed is True

    def test_free_user_exhausted_quota(self, test_user):
        from helpers.auth import update_user
        from helpers.subscription import check_search_quota
        # Free = 10 searches/month
        update_user(test_user["id"], search_count_this_month=10)
        test_user["search_count_this_month"] = 10
        allowed, msg = check_search_quota(test_user)
        assert allowed is False
        assert "10" in msg

    def test_pro_user_unlimited(self, test_user):
        from helpers.auth import update_user
        from helpers.subscription import check_search_quota
        update_user(test_user["id"], tier="pro", search_count_this_month=999)
        user = {**test_user, "tier": "pro", "search_count_this_month": 999}
        allowed, msg = check_search_quota(user)
        assert allowed is True
        assert msg == ""


# ── Subscription: has_feature ─────────────────────────────────────────────────

class TestHasFeature:
    def test_free_cannot_do_ocr(self, test_user):
        from helpers.subscription import has_feature
        assert has_feature(test_user, "ocr") is False

    def test_free_cannot_do_dxf(self, test_user):
        from helpers.subscription import has_feature
        assert has_feature(test_user, "dxf_export") is False

    def test_pro_can_do_all(self, test_user):
        from helpers.auth import update_user
        from helpers.subscription import has_feature
        update_user(test_user["id"], tier="pro")
        pro_user = {**test_user, "tier": "pro"}
        for feat in ("ocr", "dxf_export", "parcel_map", "adjoiners"):
            assert has_feature(pro_user, feat) is True, f"Pro missing: {feat}"


# ── Stripe webhook: dispatch_event ────────────────────────────────────────────

class TestStripeWebhook:
    def test_checkout_completed_no_user(self):
        """Should return 400 if user not found (but not crash)."""
        from helpers.stripe_webhook import handle_checkout_completed
        event_data = {
            "object": {
                "customer": "cus_XXXX",
                "subscription": "sub_XXXX",
                "customer_email": "nobody@example.com",
                "metadata": {"tier": "pro"},
            }
        }
        ok, msg = handle_checkout_completed(event_data)
        assert ok is False
        assert "not found" in msg.lower()

    def test_checkout_completed_upgrades_user(self, test_user):
        """checkout.session.completed should upgrade the user."""
        from helpers.stripe_webhook import handle_checkout_completed
        from helpers.auth import get_user
        event_data = {
            "object": {
                "customer": "cus_TESTOK",
                "subscription": "sub_TESTOK",
                "client_reference_id": test_user["id"],
                "customer_email": test_user["email"],
                "metadata": {"tier": "pro"},
            }
        }
        ok, msg = handle_checkout_completed(event_data)
        assert ok is True
        upgraded = get_user(test_user["id"])
        assert upgraded["tier"] == "pro"
        assert upgraded["stripe_customer_id"] == "cus_TESTOK"

    def test_subscription_deleted_downgrades_user(self, test_user):
        """subscription.deleted should downgrade the user to free."""
        from helpers.auth import update_user
        from helpers.stripe_webhook import handle_subscription_deleted
        from helpers.auth import get_user
        # First make them pro with a customer ID
        update_user(test_user["id"], tier="pro", stripe_customer_id="cus_TESTDOWN")
        event_data = {
            "object": {"customer": "cus_TESTDOWN", "id": "sub_TESTDOWN"}
        }
        ok, msg = handle_subscription_deleted(event_data)
        assert ok is True
        u = get_user(test_user["id"])
        assert u["tier"] == "free"

    def test_unknown_event_type_ignored(self):
        from helpers.stripe_webhook import dispatch_event
        status, msg = dispatch_event({"type": "payment_method.attached", "data": {}})
        assert status == 200
        assert "Ignored" in msg

    def test_verify_unsigned_dev_mode(self):
        """In dev mode (no secret), should parse JSON without signature check."""
        from helpers.stripe_webhook import verify_and_parse
        payload = json.dumps({"type": "ping", "data": {}}).encode()
        event = verify_and_parse(payload, "", secret="")  # no secret = dev mode
        assert event["type"] == "ping"

    def test_verify_bad_signature(self):
        """With a secret set, invalid signatures should raise ValueError."""
        from helpers.stripe_webhook import verify_and_parse
        payload = b'{"type":"ping"}'
        with pytest.raises(ValueError):
            verify_and_parse(payload, "bad_sig", secret="whsec_testsecret")


# ── Monthly reset ─────────────────────────────────────────────────────────────

class TestMonthlyReset:
    def test_resets_count_when_past_date(self, test_user):
        from helpers.auth import update_user, get_user
        from helpers.auth import reset_monthly_counts_if_needed
        # Set reset date to yesterday (already past)
        update_user(test_user["id"],
                    search_count_this_month=5,
                    search_reset_date="2020-01-01")
        test_user["search_count_this_month"] = 5
        test_user["search_reset_date"] = "2020-01-01"
        updated = reset_monthly_counts_if_needed(test_user)
        u = get_user(test_user["id"])
        assert u["search_count_this_month"] == 0

    def test_no_reset_when_future_date(self, test_user):
        from helpers.auth import update_user, get_user
        from helpers.auth import reset_monthly_counts_if_needed
        update_user(test_user["id"],
                    search_count_this_month=7,
                    search_reset_date="2099-01-01")
        test_user["search_count_this_month"] = 7
        test_user["search_reset_date"] = "2099-01-01"
        reset_monthly_counts_if_needed(test_user)
        u = get_user(test_user["id"])
        assert u["search_count_this_month"] == 7
