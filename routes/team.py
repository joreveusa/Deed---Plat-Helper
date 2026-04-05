"""
routes/team.py — Team management Blueprint.

Handles: team members list, invite, join, leave, remove member.
Extracted from app.py for maintainability.
"""

import os
import traceback

from flask import Blueprint, request, jsonify, g, send_from_directory

from helpers.subscription import require_auth, require_team
from helpers.auth import get_user as get_saas_user, public_user
from helpers.teams import (
    get_team_members, get_seat_count, invite_member,
    accept_invite, remove_member, leave_team,
)
from helpers.admin import check_admin_password
from helpers.backup import list_backups, restore_backup

team_bp = Blueprint("team", __name__)


# ── Team members ──────────────────────────────────────────────────────────────

@team_bp.route("/api/team/members", methods=["GET"])
@require_auth
def members():
    """List team members + seat count for the current user's team."""
    user    = g.current_user
    members = get_team_members(user["id"])
    team_id = user.get("team_id") or ""
    seats   = get_seat_count(team_id) if team_id else (1 if user.get("team_role") else 0)
    return jsonify({
        "success":    True,
        "members":    members,
        "seats_used": seats,
        "seats_max":  5,
        "team_id":    team_id,
        "role":       user.get("team_role"),
    })


@team_bp.route("/api/team/invite", methods=["POST"])
@require_auth
@require_team
def invite():
    """Invite a member to the team. Body: { email }. Requires team tier."""
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"success": False, "error": "Email is required."}), 400
    try:
        ok, msg, token = invite_member(g.current_user, email)
        if not ok:
            return jsonify({"success": False, "error": msg}), 400
        # Send invite email
        if token:
            _app_url  = os.environ.get("DEED_APP_URL", "http://localhost:5000")
            join_link = f"{_app_url}/team/join?token={token}"
            try:
                from helpers.email_utils import _send_email
                _send_email(
                    email,
                    f"{g.current_user['email']} invited you to Deed & Plat Helper",
                    f"You've been invited to join a Deed & Plat Helper team.\n\nJoin link (expires in 72 hours):\n{join_link}",
                    f'<p><strong>{g.current_user["email"]}</strong> invited you to join their Deed &amp; Plat Helper team.</p>'
                    f'<p><a href="{join_link}" style="background:#4facfe;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none">Join Team</a></p>'
                    f'<p style="color:#999;font-size:11px">Link expires in 72 hours.</p>',
                )
            except Exception as exc:
                print(f"[team] invite email error: {exc}", flush=True)
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@team_bp.route("/api/team/join", methods=["POST"])
@require_auth
def join():
    """Accept a team invite. Body: { token }."""
    data  = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"success": False, "error": "Token is required."}), 400
    ok, msg = accept_invite(token)
    if ok:
        updated = get_saas_user(g.current_user["id"])
        return jsonify({"success": True, "message": msg,
                        "user": public_user(updated) if updated else {}})
    return jsonify({"success": False, "error": msg}), 400


@team_bp.route("/api/team/members/<member_id>", methods=["DELETE"])
@require_auth
@require_team
def remove(member_id: str):
    """Remove a team member (owner only)."""
    ok, msg = remove_member(g.current_user, member_id)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "error": msg}), 400


@team_bp.route("/api/team/leave", methods=["POST"])
@require_auth
def leave():
    """Leave the current team (members only, not owner)."""
    ok, msg = leave_team(g.current_user)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "error": msg}), 400


@team_bp.route("/team/join")
def join_page():
    """Serve the SPA for /team/join?token= links."""
    return send_from_directory(".", "index.html")


# ── Backup / restore (admin) ─────────────────────────────────────────────────

@team_bp.route("/api/admin/backups", methods=["GET"])
def admin_backups():
    """List available users.json backups. Requires ?password=."""
    pwd = request.args.get("password", "")
    if not check_admin_password(pwd):
        return jsonify({"success": False, "error": "Forbidden"}), 403
    return jsonify({"success": True, "backups": list_backups()})


@team_bp.route("/api/admin/backups/restore", methods=["POST"])
def admin_restore():
    """Restore users.json from a named backup. Body: { password, filename }."""
    data     = request.get_json(silent=True) or {}
    pwd      = data.get("password", "")
    filename = data.get("filename", "")
    if not check_admin_password(pwd):
        return jsonify({"success": False, "error": "Forbidden"}), 403
    if not filename:
        return jsonify({"success": False, "error": "filename is required."}), 400
    try:
        restore_backup(filename)
        return jsonify({"success": True, "message": f"Restored from {filename}."})
    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
