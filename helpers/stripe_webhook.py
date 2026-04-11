"""
helpers/stripe_webhook.py — Stripe webhook handler for Deed & Plat Helper SaaS.

Handles:
  - checkout.session.completed  → activate subscription, set tier to 'pro' or 'team'
  - customer.subscription.updated → handle plan changes (pro ↔ team)
  - customer.subscription.deleted → downgrade back to 'free'
  - invoice.payment_failed        → log (don't immediately downgrade)
"""

import os
import json
import logging
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# ── Idempotency cache ─────────────────────────────────────────────────────────
# Stripe retries failed webhooks up to ~87 hours with exponential backoff.
# We cache processed event IDs in memory to silently skip duplicates.
# TTL of 24 hours covers Stripe's retry window without unbounded growth.
# For multi-process deployments, swap this for Redis SET NX with TTL.

_PROCESSED_EVENTS: dict[str, float] = {}   # event_id → timestamp
_PROCESSED_LOCK   = threading.Lock()
_EVENT_TTL_SECS   = 24 * 60 * 60           # 24 hours


def _is_duplicate_event(event_id: str) -> bool:
    """Return True if this event has already been processed. Registers it if not."""
    now = time.time()
    with _PROCESSED_LOCK:
        # Purge expired entries to keep memory bounded
        expired = [eid for eid, ts in _PROCESSED_EVENTS.items()
                   if now - ts > _EVENT_TTL_SECS]
        for eid in expired:
            del _PROCESSED_EVENTS[eid]

        if event_id in _PROCESSED_EVENTS:
            return True  # Already handled

        _PROCESSED_EVENTS[event_id] = now
        return False

# ── Stripe price → tier mapping ───────────────────────────────────────────────
# Add Team price ID when you create it in Stripe
_PRO_PRICE_ID  = os.environ.get("STRIPE_PRO_PRICE_ID",  "price_1TIZrNR0DNgIZfm7dVBvQuJi")
_TEAM_PRICE_ID = os.environ.get("STRIPE_TEAM_PRICE_ID", "price_1TIZtZR0DNgIZfm77NN3RBxA")
_LEGACY_PRO_ID = "price_1TIXykR0DNgIZfm7F3QjUJ92"  # legacy product — still honour it

PRICE_TIER_MAP: dict[str, str] = {
    _PRO_PRICE_ID:  "pro",
    _TEAM_PRICE_ID: "team",
    _LEGACY_PRO_ID: "pro",   # legacy customers keep working
}

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")


def _find_user_by_customer_id(customer_id: str) -> Optional[dict]:
    """Look up a user whose stripe_customer_id matches."""
    from helpers.auth import _load_users
    for user in _load_users().values():
        if user.get("stripe_customer_id") == customer_id:
            return user
    return None


def _find_user_by_email(email: str) -> Optional[dict]:
    from helpers.auth import find_user_by_email
    return find_user_by_email(email)


def _upgrade_user(user_id: str, tier: str, customer_id: str,
                  subscription_id: str) -> None:
    from helpers.auth import update_user
    update_user(
        user_id,
        tier=tier,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        active=True,
    )
    log.info(f"[Stripe] Upgraded user {user_id} → {tier}")


def _downgrade_user(user_id: str) -> None:
    from helpers.auth import update_user
    update_user(user_id, tier="free", stripe_subscription_id=None)
    log.info(f"[Stripe] Downgraded user {user_id} → free")


# ── Event handlers ────────────────────────────────────────────────────────────

def handle_checkout_completed(event_data: dict) -> tuple[bool, str]:
    """Payment succeeded — activate subscription and upgrade tier."""
    session = event_data.get("object", {})
    customer_id      = session.get("customer")
    subscription_id  = session.get("subscription")
    customer_email   = (session.get("customer_details") or {}).get("email") or \
                       session.get("customer_email", "")
    client_ref_id    = session.get("client_reference_id")  # we'll store user_id here
    metadata         = session.get("metadata", {})

    # Determine new tier from line items metadata, or default to 'pro'
    tier = metadata.get("tier", "pro")

    # Find the user: try client_reference_id first (most reliable), then email
    user = None
    if client_ref_id:
        from helpers.auth import get_user
        user = get_user(client_ref_id)
    if not user and customer_email:
        user = _find_user_by_email(customer_email)
    if not user and customer_id:
        user = _find_user_by_customer_id(customer_id)

    if not user:
        log.warning(f"[Stripe] checkout.completed: no user found for customer={customer_id} email={customer_email}")
        return False, "User not found"

    _upgrade_user(user["id"], tier, customer_id, subscription_id)
    return True, f"Upgraded {user['email']} → {tier}"


def handle_subscription_updated(event_data: dict) -> tuple[bool, str]:
    """Plan changed (e.g., pro → team, or reactivated)."""
    sub = event_data.get("object", {})
    customer_id     = sub.get("customer")
    subscription_id = sub.get("id")
    status          = sub.get("status")         # active, past_due, canceled, etc.
    items           = sub.get("items", {}).get("data", [])

    # Determine tier from the first plan item's price
    tier = "pro"  # default
    for item in items:
        price_id = (item.get("price") or {}).get("id")
        if price_id and price_id in PRICE_TIER_MAP:
            tier = PRICE_TIER_MAP[price_id]
            break

    user = _find_user_by_customer_id(customer_id)
    if not user:
        log.warning(f"[Stripe] subscription.updated: no user found for customer={customer_id}")
        return False, "User not found"

    if status in ("active", "trialing"):
        _upgrade_user(user["id"], tier, customer_id, subscription_id)
        return True, f"Updated {user['email']} → {tier} (status={status})"
    elif status in ("canceled", "unpaid", "incomplete_expired"):
        _downgrade_user(user["id"])
        return True, f"Downgraded {user['email']} (status={status})"
    else:
        log.info(f"[Stripe] subscription.updated: ignored status={status} for {user['email']}")
        return True, f"Ignored status={status}"


def handle_subscription_deleted(event_data: dict) -> tuple[bool, str]:
    """Subscription canceled — downgrade to free immediately."""
    sub         = event_data.get("object", {})
    customer_id = sub.get("customer")

    user = _find_user_by_customer_id(customer_id)
    if not user:
        log.warning(f"[Stripe] subscription.deleted: no user found for customer={customer_id}")
        return False, "User not found"

    _downgrade_user(user["id"])

    # Notify the user by email
    try:
        from helpers.email_utils import send_subscription_cancelled
        send_subscription_cancelled(user["email"])
    except Exception as e:
        log.warning(f"[Stripe] cancellation email failed: {e}")

    return True, f"Downgraded {user['email']} → free (subscription canceled)"


def handle_payment_failed(event_data: dict) -> tuple[bool, str]:
    """Invoice payment failed — log it, don't immediately downgrade.
    Stripe will retry; subscription.deleted fires if it gives up."""
    inv = event_data.get("object", {})
    customer_id = inv.get("customer")
    attempt     = inv.get("attempt_count", 1)
    log.warning(f"[Stripe] payment_failed: customer={customer_id} attempt={attempt}")
    return True, f"Logged payment failure (attempt {attempt})"


# ── Main dispatcher ───────────────────────────────────────────────────────────

def dispatch_event(event: dict) -> tuple[int, str]:
    """
    Dispatch a verified Stripe event dict to the right handler.
    Returns (http_status, message).

    Idempotent: duplicate event IDs (Stripe retries) are silently skipped
    with a 200 so Stripe stops retrying.
    """
    ev_type  = event.get("type", "")
    event_id = event.get("id", "")
    data     = event.get("data", {})

    # Dedup check — return 200 immediately so Stripe stops retrying
    if event_id and _is_duplicate_event(event_id):
        log.info(f"[Stripe] Duplicate event skipped: {ev_type} id={event_id}")
        return 200, f"Duplicate event ignored: {event_id}"

    handlers = {
        "checkout.session.completed":       handle_checkout_completed,
        "customer.subscription.updated":    handle_subscription_updated,
        "customer.subscription.deleted":    handle_subscription_deleted,
        "invoice.payment_failed":           handle_payment_failed,
    }

    handler = handlers.get(ev_type)
    if handler is None:
        log.debug(f"[Stripe] Unhandled event type: {ev_type}")
        return 200, f"Ignored: {ev_type}"

    try:
        ok, msg = handler(data)
        if ok:
            log.info(f"[Stripe] {ev_type}: {msg}")
            return 200, msg
        else:
            log.error(f"[Stripe] {ev_type} error: {msg}")
            return 400, msg
    except Exception as e:
        log.exception(f"[Stripe] Exception handling {ev_type}: {e}")
        return 500, str(e)


def verify_and_parse(raw_body: bytes, sig_header: str,
                     secret: str = STRIPE_WEBHOOK_SECRET) -> dict:
    """
    Verify Stripe webhook signature and return parsed event.
    Raises ValueError if signature is invalid.
    Falls back to unsigned JSON parsing if secret is empty (local dev).
    """
    if not secret:
        # Dev mode: accept unsigned events (never do this in production!)
        log.warning("[Stripe] No STRIPE_WEBHOOK_SECRET set — skipping signature verification (dev mode)")
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")

    # Production: verify Stripe-Signature header
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(raw_body, sig_header, secret)
        return event
    except Exception as e:
        raise ValueError(f"Signature verification failed: {e}")
