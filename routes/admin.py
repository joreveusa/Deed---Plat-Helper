"""
routes/admin.py — Admin panel Blueprint.

Handles: admin auth, user management, tier changes, config export/import.
Extracted from app.py for maintainability.
"""

import re
import json
import traceback

from flask import Blueprint, request, jsonify, send_from_directory, make_response

from helpers.auth import (
    check_admin_password, list_users_summary, get_user_stats,
    admin_set_tier, admin_toggle_active, admin_reset_searches,
)
from helpers.profiles import get_profile, save_profile
from helpers.subscription import require_auth

admin_bp = Blueprint("admin", __name__)


# ── Admin page ────────────────────────────────────────────────────────────────

@admin_bp.route("/admin")
def admin_page():
    """Serve the admin panel HTML."""
    return send_from_directory('.', 'index.html')


# ── Admin API ─────────────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/auth", methods=["POST"])
def admin_auth():
    """Verify admin password. Body: { password }. Returns { success, stats }."""
    data = request.get_json() or {}
    pwd  = data.get("password", "")
    if not check_admin_password(pwd):
        return jsonify({"success": False, "error": "Invalid admin password."}), 403
    return jsonify({"success": True, "stats": get_user_stats()})


@admin_bp.route("/api/admin/users", methods=["GET"])
def admin_users():
    """List all users. Requires ?password= query param."""
    pwd = request.args.get("password", "")
    if not check_admin_password(pwd):
        return jsonify({"success": False, "error": "Forbidden"}), 403
    return jsonify({"success": True, "users": list_users_summary(), "stats": get_user_stats()})


@admin_bp.route("/api/admin/users/<user_id>", methods=["PATCH"])
def admin_update_user(user_id: str):
    """Update a user (tier, active, reset searches)."""
    data = request.get_json() or {}
    pwd  = data.get("password", "")
    if not check_admin_password(pwd):
        return jsonify({"success": False, "error": "Forbidden"}), 403
    try:
        if "tier" in data:
            admin_set_tier(user_id, data["tier"])
        if "active" in data:
            admin_toggle_active(user_id, bool(data["active"]))
        if data.get("reset_searches"):
            admin_reset_searches(user_id)
        return jsonify({"success": True, "users": list_users_summary()})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ── Config export / import ────────────────────────────────────────────────────

@admin_bp.route("/api/config/export", methods=["GET"])
@require_auth
def config_export():
    """Export current profile config as JSON download."""
    profile_name = request.args.get("profile", "default")
    try:
        profile = get_profile(profile_name)
        export = {
            "_deed_config_version": 1,
            "county_name":  profile.get("county_name", ""),
            "url":          profile.get("url", ""),
            "arcgis_url":   profile.get("arcgis_url", ""),
            "arcgis_fields": profile.get("arcgis_fields", {}),
        }
        resp = make_response(json.dumps(export, indent=2))
        safe_name = re.sub(r'[^\w\-]', '_', profile_name or 'config')
        resp.headers["Content-Disposition"] = f'attachment; filename="deed_config_{safe_name}.json"'
        resp.headers["Content-Type"] = "application/json"
        return resp
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/config/import", methods=["POST"])
@require_auth
def config_import():
    """Import a previously exported county config JSON."""
    data = request.get_json() or {}
    profile_name = data.get("profile", "default")
    cfg = data.get("config", {})
    if not isinstance(cfg, dict):
        return jsonify({"success": False, "error": "config must be an object"}), 400
    try:
        profile = get_profile(profile_name) or {}
        # Only allow safe fields
        if cfg.get("county_name"):
            profile["county_name"] = str(cfg["county_name"])[:100]
        if cfg.get("url"):
            profile["url"] = str(cfg["url"])[:500]
        if cfg.get("arcgis_url"):
            profile["arcgis_url"] = str(cfg["arcgis_url"])[:500]
        if cfg.get("arcgis_fields") and isinstance(cfg["arcgis_fields"], dict):
            profile["arcgis_fields"] = {
                k: str(v)[:100] for k, v in cfg["arcgis_fields"].items()
                if isinstance(k, str) and isinstance(v, str)
            }
        save_profile(profile_name, profile)
        return jsonify({"success": True, "message": f"Config imported for profile '{profile_name}'."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
