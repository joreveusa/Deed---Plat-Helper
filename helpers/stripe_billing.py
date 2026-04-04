"""
helpers/stripe_billing.py — Stripe Checkout + Webhook integration.

Environment variables required (set in your shell / launch .bat):
  STRIPE_SECRET_KEY          sk_test_... or sk_live_...
  STRIPE_WEBHOOK_SECRET      whsec_... (from Stripe CLI or dashboard)
  STRIPE_PRO_PRICE_ID        price_1TIZrNR0DNgIZfm7dVBvQuJi
  STRIPE_TEAM_PRICE_ID       price_1TIZtZR0DNgIZfm77NN3RBxA
  DEED_APP_URL               http://localhost:5000  (or your public domain)

To test webhooks locally:
  stripe listen --forward-to localhost:5000/api/stripe/webhook
"""

from __future__ import annotations

import os

import stripe  # pip install stripe


# ── Config from environment ───────────────────────────────────────────────────

STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
APP_URL               = os.environ.get("DEED_APP_URL", "http://localhost:5000")

# Product price IDs — override via env vars if needed
PRICE_IDS: dict[str, str] = {
    "pro":  os.environ.get("STRIPE_PRO_PRICE_ID",  "price_1TIZrNR0DNgIZfm7dVBvQuJi"),
    "team": os.environ.get("STRIPE_TEAM_PRICE_ID", "price_1TIZtZR0DNgIZfm77NN3RBxA"),
}

# Reverse: price_id → tier
_PRICE_TO_TIER: dict[str, str] = {v: k for k, v in PRICE_IDS.items()}


def _client() -> stripe.Stripe:
    """Return a configured Stripe client."""
    if not STRIPE_SECRET_KEY:
        raise RuntimeError(
            "STRIPE_SECRET_KEY environment variable is not set. "
            "Add it to your .env or launch .bat file."
        )
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


def create_checkout_session(user_email: str, tier: str,
                            user_id: str) -> str:
    """Create a Stripe Checkout Session for the given tier.

    Returns the Checkout session URL to redirect the user to.
    Raises ValueError for unknown tiers, RuntimeError if Stripe is not configured.
    """
    price_id = PRICE_IDS.get(tier)
    if not price_id:
        raise ValueError(f"Unknown tier: {tier!r}. Valid tiers: {list(PRICE_IDS)}")

    s = _client()
    session = s.checkout.Session.create(
        mode="subscription",
        customer_email=user_email,
        line_items=[{"price": price_id, "quantity": 1}],
        # Pass the user_id so the webhook can find the account to upgrade
        client_reference_id=user_id,
        metadata={"user_id": user_id, "tier": tier},
        success_url=APP_URL + "/?upgraded=1",
        cancel_url=APP_URL + "/?upgrade=cancelled",
    )
    return session.url


def create_customer_portal_session(stripe_customer_id: str) -> str:
    """Return a Stripe Customer Portal URL so the user can manage/cancel."""
    s = _client()
    session = s.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=APP_URL + "/",
    )
    return session.url


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify and parse a Stripe webhook event.

    Returns { "event_type": str, "user_id": str, "tier": str,
              "stripe_customer_id": str, "stripe_subscription_id": str }
    on actionable events, or { "event_type": str, "skip": True } otherwise.

    Raises stripe.error.SignatureVerificationError on invalid signatures.
    """
    s = _client()

    event = s.Webhook.construct_event(
        payload, sig_header, STRIPE_WEBHOOK_SECRET
    )

    etype = event["type"]

    # ── Subscription activated / updated ──────────────────────────────────────
    if etype in ("checkout.session.completed",
                 "customer.subscription.updated"):

        if etype == "checkout.session.completed":
            obj     = event["data"]["object"]
            user_id = (obj.get("client_reference_id")
                       or obj.get("metadata", {}).get("user_id", ""))
            tier    = obj.get("metadata", {}).get("tier", "pro")
            sub_id  = obj.get("subscription", "")
            cus_id  = obj.get("customer", "")

        else:  # subscription.updated
            obj     = event["data"]["object"]
            user_id = obj.get("metadata", {}).get("user_id", "")
            # Determine tier from price ID
            items   = obj.get("items", {}).get("data", [])
            price_id = items[0]["price"]["id"] if items else ""
            tier    = _PRICE_TO_TIER.get(price_id, "pro")
            sub_id  = obj.get("id", "")
            cus_id  = obj.get("customer", "")

        return {
            "event_type":             etype,
            "user_id":                user_id,
            "tier":                   tier,
            "stripe_customer_id":     cus_id,
            "stripe_subscription_id": sub_id,
            "skip": False,
        }

    # ── Subscription cancelled / payment failed → downgrade to free ───────────
    if etype in ("customer.subscription.deleted",
                 "invoice.payment_failed"):
        obj    = event["data"]["object"]
        cus_id = obj.get("customer", "")
        sub_id = obj.get("id", obj.get("subscription", ""))
        # We look up the user by stripe_customer_id in the webhook route
        return {
            "event_type":             etype,
            "tier":                   "free",
            "stripe_customer_id":     cus_id,
            "stripe_subscription_id": sub_id,
            "user_id":                "",   # caller will find by customer ID
            "skip": False,
        }

    # ── Ignore everything else ────────────────────────────────────────────────
    return {"event_type": etype, "skip": True}
