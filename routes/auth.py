"""
routes/auth.py — SaaS authentication Blueprint.

Handles: register, login, logout, password reset, session info, usage stats.
Extracted from app.py for maintainability.
"""

import os
import traceback

from flask import Blueprint, request, jsonify, send_from_directory, g

from helpers.auth import (
    create_user, find_user_by_email,
    verify_password, generate_token, public_user,
    generate_reset_token, reset_password as reset_user_password,
    get_search_history,
    check_login_allowed, record_failed_login, clear_failed_logins,
)
from helpers.subscription import (
    require_auth, get_tier_limits,
)
from helpers.rate_limit import rate_limit

auth_bp = Blueprint("auth", __name__)


# ── Registration ──────────────────────────────────────────────────────────────

@auth_bp.route("/auth/register", methods=["POST"])
@rate_limit(requests=5, window=3600, key="register")  # 5 signups/hour per IP (anti-spam)
def register():
    """Register a new Deed Helper account. Body: {email, password}"""
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip()
    password = data.get("password") or ""
    try:
        user  = create_user(email, password, tier="free")
        token = generate_token(user["id"])
        resp  = jsonify({"success": True, "user": public_user(user)})
        resp.set_cookie("deed_token", token, max_age=60*60*24*30,
                        httponly=True, samesite="Lax")
        # Send welcome email + notify admin (fire & forget)
        try:
            from helpers.email_utils import send_welcome, send_admin_new_user_notification
            _admin_email = os.environ.get("DEED_ADMIN_EMAIL", "")
            send_welcome(email)
            if _admin_email:
                send_admin_new_user_notification(email, _admin_email)
        except Exception:
            pass  # email is best-effort
        return resp
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Login / Logout ────────────────────────────────────────────────────────────

@auth_bp.route("/auth/login", methods=["POST"])
@rate_limit(requests=10, window=60, key="login")   # 10 attempts/minute per IP (brute force guard)
def login():
    """Login to Deed Helper account. Body: {email, password}"""
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip()
    password = data.get("password") or ""
    user     = find_user_by_email(email)

    # Always return the same generic error (prevent user enumeration)
    if not user:
        return jsonify({"success": False, "error": "Invalid email or password."}), 401

    # Lockout check
    allowed, lock_msg = check_login_allowed(user)
    if not allowed:
        return jsonify({"success": False, "error": lock_msg}), 429

    # Inactive account
    if not user.get("active", True):
        return jsonify({"success": False, "error": "Account is inactive. Contact support."}), 403

    # Password check
    if not verify_password(password, user.get("password_hash", "")):
        record_failed_login(user["id"])
        return jsonify({"success": False, "error": "Invalid email or password."}), 401

    # Success
    clear_failed_logins(user["id"])
    token = generate_token(user["id"])
    resp  = jsonify({"success": True, "user": public_user(user)})
    resp.set_cookie("deed_token", token, max_age=60*60*24*30,
                    httponly=True, samesite="Lax")
    return resp


@auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    resp = jsonify({"success": True})
    resp.delete_cookie("deed_token")
    return resp


# ── Session info ──────────────────────────────────────────────────────────────

@auth_bp.route("/auth/me", methods=["GET"])
@require_auth
def me():
    """Return the currently logged-in user's public info + tier limits."""
    user   = g.current_user
    limits = get_tier_limits(user.get("tier", "free"))
    return jsonify({
        "success": True,
        "user":    public_user(user),
        "limits":  limits,
    })


@auth_bp.route("/auth/tier", methods=["GET"])
@require_auth
def tier():
    """Quick tier check endpoint used by the frontend to gate UI elements."""
    user = g.current_user
    return jsonify({
        "success": True,
        "tier":    user.get("tier", "free"),
        "limits":  get_tier_limits(user.get("tier", "free")),
        "usage": {
            "searches_this_month": user.get("search_count_this_month", 0),
        },
    })


# ── Password reset ────────────────────────────────────────────────────────────

@auth_bp.route("/auth/forgot-password", methods=["POST"])
def forgot_password():
    """Request a password-reset email. Body: { email }.
    Always returns success to prevent email enumeration attacks.
    """
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if email and "@" in email:
        user = find_user_by_email(email)
        if user:
            token      = generate_reset_token(email)
            _app_url   = os.environ.get("DEED_APP_URL", "http://localhost:5000")
            reset_link = f"{_app_url}/reset-password?token={token}"
            try:
                from helpers.email_utils import send_password_reset
                send_password_reset(email, reset_link)
            except Exception as exc:
                print(f"[reset] email error: {exc}", flush=True)
    return jsonify({"success": True,
                    "message": "If that email has an account, a reset link has been sent."})


@auth_bp.route("/auth/reset-password", methods=["POST"])
def reset_password():
    """Consume a reset token and set a new password. Body: { token, password }"""
    data     = request.get_json(silent=True) or {}
    token    = (data.get("token") or "").strip()
    password = data.get("password") or ""
    if not token:
        return jsonify({"success": False, "error": "Token is required."}), 400
    try:
        user = reset_user_password(token, password)
        if not user:
            return jsonify({"success": False,
                            "error": "Reset link is invalid or expired. Request a new one."}), 400
        return jsonify({"success": True, "message": "Password updated. You can now log in."})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ── Usage & history ───────────────────────────────────────────────────────────

@auth_bp.route("/auth/history", methods=["GET"])
@require_auth
def search_history():
    """Return the current user's recent search history (last 20 queries)."""
    history = get_search_history(g.current_user["id"])
    return jsonify({"success": True, "history": history})


@auth_bp.route("/auth/usage", methods=["GET"])
@require_auth
def usage():
    """Return the current user's search usage stats for the quota widget."""
    user   = g.current_user
    tier   = user.get("tier", "free")
    limits = get_tier_limits(tier)
    used   = user.get("search_count_this_month", 0)
    limit  = limits["searches_per_month"]   # None = unlimited
    return jsonify({
        "success":    True,
        "tier":       tier,
        "used":       used,
        "limit":      limit,
        "reset_date": user.get("search_reset_date", ""),
        "pct":        min(100, round(used / limit * 100)) if limit else 0,
    })


# ── Reset password page ──────────────────────────────────────────────────────

@auth_bp.route("/reset-password")
def reset_password_page():
    """Serve the SPA for /reset-password?token= links sent in emails."""
    return send_from_directory(".", "index.html")
