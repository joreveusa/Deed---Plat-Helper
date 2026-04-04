"""
routes/stripe.py — Stripe billing Blueprint.

Handles: checkout session creation, customer portal, webhook receiver, upgrade success.
Extracted from app.py for maintainability.
"""

import traceback

from flask import Blueprint, request, jsonify, redirect, g

from helpers.subscription import require_auth

stripe_bp = Blueprint("stripe", __name__)

# These are set at registration time by app.py (avoids circular imports)
_stripe_available = False
_create_checkout = None
_create_portal = None
_stripe_verify = None
_stripe_dispatch = None


def init_stripe(available, checkout_fn, portal_fn, verify_fn, dispatch_fn):
    """Called by app.py after import to inject Stripe functions."""
    global _stripe_available, _create_checkout, _create_portal
    global _stripe_verify, _stripe_dispatch
    _stripe_available = available
    _create_checkout = checkout_fn
    _create_portal = portal_fn
    _stripe_verify = verify_fn
    _stripe_dispatch = dispatch_fn


# ── Checkout ──────────────────────────────────────────────────────────────────

@stripe_bp.route("/api/stripe/checkout", methods=["POST"])
@require_auth
def checkout():
    """Create a Stripe Checkout Session for a tier upgrade.

    Body: { "tier": "pro" | "team" }
    Returns: { success, checkout_url }
    """
    if not _stripe_available:
        return jsonify({
            "success": False,
            "error": "Stripe is not configured on this server. Set STRIPE_SECRET_KEY."
        }), 503

    user = g.current_user
    data = request.get_json() or {}
    tier = data.get("tier", "pro")

    if tier not in ("pro", "team"):
        return jsonify({"success": False, "error": "Invalid tier. Choose 'pro' or 'team'."})

    try:
        url = _create_checkout(
            user_email=user["email"],
            tier=tier,
            user_id=user["id"],
        )
        return jsonify({"success": True, "checkout_url": url})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


# ── Customer Portal ───────────────────────────────────────────────────────────

@stripe_bp.route("/api/stripe/portal", methods=["POST"])
@require_auth
def portal():
    """Create a Stripe Customer Portal session so the user can manage billing."""
    if not _stripe_available:
        return jsonify({"success": False, "error": "Stripe not configured."}), 503

    user = g.current_user
    cus_id = user.get("stripe_customer_id")
    if not cus_id:
        return jsonify({
            "success": False,
            "error": "No Stripe customer found. Subscribe first via the upgrade flow."
        })

    try:
        url = _create_portal(cus_id)
        return jsonify({"success": True, "portal_url": url})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


# ── Webhook ───────────────────────────────────────────────────────────────────

@stripe_bp.route("/api/stripe/webhook", methods=["POST"])
def webhook():
    """Stripe webhook receiver — verifies signature and dispatches to tier handler."""
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = _stripe_verify(payload, sig_header)
    except ValueError as e:
        print(f"[stripe-webhook] Bad signature: {e}", flush=True)
        return jsonify({"error": str(e)}), 400

    status, msg = _stripe_dispatch(event)
    print(f"[stripe-webhook] {event.get('type','?')} → {status}: {msg}", flush=True)
    return jsonify({"received": True, "message": msg}), status


# ── Upgrade success ───────────────────────────────────────────────────────────

@stripe_bp.route("/upgrade-success")
def upgrade_success():
    """After Stripe checkout, redirect to the app with upgrade flag."""
    return redirect("/?upgraded=1")
