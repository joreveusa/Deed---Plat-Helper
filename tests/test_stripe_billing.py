"""
tests/test_stripe_billing.py
==============================
Unit tests for the Stripe billing module.

Tests cover:
  - Price ID → tier mapping
  - Checkout session creation (mocked Stripe API)
  - Webhook event parsing and tier dispatch
  - Edge cases: unknown tiers, missing keys, cancellation

Run with:  python -m pytest tests/test_stripe_billing.py -v
"""

import pytest
from unittest.mock import patch, MagicMock


# ── Fixture: import with env vars set ────────────────────────────────────────

@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    """Set required env vars before importing the module."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake_key_for_testing")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fake_secret")
    monkeypatch.setenv("STRIPE_PRO_PRICE_ID", "price_test_pro_123")
    monkeypatch.setenv("STRIPE_TEAM_PRICE_ID", "price_test_team_456")
    monkeypatch.setenv("DEED_APP_URL", "http://localhost:5000")


def _reload_sb():
    """Reload stripe_billing to pick up env changes."""
    import importlib
    import helpers.stripe_billing as sb
    importlib.reload(sb)
    return sb


# ── Price mapping ────────────────────────────────────────────────────────────

class TestPriceMapping:
    def test_price_ids_loaded_from_env(self, stripe_env):
        sb = _reload_sb()
        assert sb.PRICE_IDS["pro"] == "price_test_pro_123"
        assert sb.PRICE_IDS["team"] == "price_test_team_456"

    def test_reverse_mapping(self, stripe_env):
        sb = _reload_sb()
        assert sb._PRICE_TO_TIER["price_test_pro_123"] == "pro"
        assert sb._PRICE_TO_TIER["price_test_team_456"] == "team"


# ── Checkout session ─────────────────────────────────────────────────────────

class TestCheckoutSession:
    def test_unknown_tier_raises(self, stripe_env):
        sb = _reload_sb()
        with pytest.raises(ValueError, match="Unknown tier"):
            sb.create_checkout_session("user@test.com", "enterprise", "u_123")

    def test_checkout_creates_session(self, stripe_env):
        sb = _reload_sb()
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/test_session"

        with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
            url = sb.create_checkout_session("user@test.com", "pro", "u_abc123")

        assert url == "https://checkout.stripe.com/test_session"
        mock_create.assert_called_once()

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["mode"] == "subscription"
        assert call_kwargs["customer_email"] == "user@test.com"
        assert call_kwargs["client_reference_id"] == "u_abc123"
        assert call_kwargs["metadata"]["tier"] == "pro"


# ── Webhook parsing ──────────────────────────────────────────────────────────

class TestWebhookHandler:
    def _make_event(self, event_type, data_object):
        """Build a fake Stripe event dict."""
        return {
            "type": event_type,
            "data": {"object": data_object},
        }

    def test_checkout_completed_returns_user_and_tier(self, stripe_env):
        sb = _reload_sb()
        event = self._make_event("checkout.session.completed", {
            "client_reference_id": "u_test_user",
            "metadata": {"user_id": "u_test_user", "tier": "pro"},
            "customer": "cus_abc",
            "subscription": "sub_xyz",
        })

        with patch("stripe.Webhook.construct_event", return_value=event):
            result = sb.handle_webhook(b'{}', "t=123,v1=sig")

        assert result["skip"] is False
        assert result["user_id"] == "u_test_user"
        assert result["tier"] == "pro"
        assert result["stripe_customer_id"] == "cus_abc"
        assert result["stripe_subscription_id"] == "sub_xyz"

    def test_subscription_deleted_returns_free_tier(self, stripe_env):
        sb = _reload_sb()
        event = self._make_event("customer.subscription.deleted", {
            "customer": "cus_to_downgrade",
            "id": "sub_cancelled",
        })

        with patch("stripe.Webhook.construct_event", return_value=event):
            result = sb.handle_webhook(b'{}', "t=123,v1=sig")

        assert result["skip"] is False
        assert result["tier"] == "free"
        assert result["stripe_customer_id"] == "cus_to_downgrade"

    def test_unhandled_event_skipped(self, stripe_env):
        sb = _reload_sb()
        event = self._make_event("charge.refunded", {"id": "ch_123"})

        with patch("stripe.Webhook.construct_event", return_value=event):
            result = sb.handle_webhook(b'{}', "t=123,v1=sig")

        assert result["skip"] is True
        assert result["event_type"] == "charge.refunded"

    def test_payment_failed_returns_free(self, stripe_env):
        sb = _reload_sb()
        event = self._make_event("invoice.payment_failed", {
            "customer": "cus_broke",
            "subscription": "sub_failed",
        })

        with patch("stripe.Webhook.construct_event", return_value=event):
            result = sb.handle_webhook(b'{}', "t=123,v1=sig")

        assert result["skip"] is False
        assert result["tier"] == "free"
