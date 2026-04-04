"""
helpers/teams.py — Team seat management for Deed & Plat Helper SaaS.

Allows Team tier users (up to 5 seats) to:
  - Invite members by email (sends a join link)
  - Accept invitations (join a team)
  - View team roster + seat usage
  - Remove members

Data model (additions to users.json user record):
  team_id:    str | None   — UUID identifying the team (same for all members)
  team_role:  "owner" | "member" | None
  team_invite_token: str | None  — pending invite token (cleared on join)

Invitation flow:
  1. Owner calls POST /api/team/invite  { email }
     → generates token, stores on invited user (or creates stub), sends email
  2. Invited user clicks join link → GET /api/team/join?token=XXX
     → sets team_id, team_role=member, clears token
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

log = logging.getLogger(__name__)

_SECRET_KEY       = os.environ.get("DEED_SECRET_KEY", "dev-secret-change-in-production-!!!")
_INVITE_MAX_AGE   = 60 * 60 * 72   # 72 hours
_INVITE_SERIALIZER = URLSafeTimedSerializer(_SECRET_KEY)

MAX_TEAM_SEATS = 5   # Team plan: up to 5 seats


# ── Token helpers ─────────────────────────────────────────────────────────────

def generate_invite_token(team_id: str, invitee_email: str) -> str:
    """Generate a signed team invite token."""
    return _INVITE_SERIALIZER.dumps(
        {"team_id": team_id, "email": invitee_email.lower().strip()},
        salt="team-invite",
    )


def verify_invite_token(token: str) -> dict | None:
    """Return { team_id, email } if valid, None if expired/invalid."""
    try:
        return _INVITE_SERIALIZER.loads(token, salt="team-invite", max_age=_INVITE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ── Team operations ────────────────────────────────────────────────────────────

def get_team_members(owner_id: str) -> list[dict]:
    """Return all users that belong to the same team as owner_id."""
    from helpers.auth import _load_users
    users = _load_users()
    owner = users.get(owner_id)
    if not owner:
        return []
    team_id = owner.get("team_id")
    if not team_id:
        return []
    members = []
    for u in users.values():
        if u.get("team_id") == team_id:
            members.append({
                "id":        u["id"],
                "email":     u["email"],
                "role":      u.get("team_role", "member"),
                "tier":      u.get("tier", "free"),
                "joined_at": u.get("team_joined_at", ""),
                "active":    u.get("active", True),
            })
    members.sort(key=lambda m: (m["role"] != "owner", m.get("joined_at", "")))
    return members


def get_seat_count(team_id: str) -> int:
    """Return number of active seats on a team."""
    from helpers.auth import _load_users
    return sum(
        1 for u in _load_users().values()
        if u.get("team_id") == team_id and u.get("active", True)
    )


def invite_member(owner: dict, invitee_email: str) -> tuple[bool, str, str]:
    """Invite a user to the team.

    Returns (success, message, invite_token).
    Creates a stub user record if the email doesn't have an account yet.
    """
    from helpers.auth import (
        find_user_by_email, create_user
    )

    email = invitee_email.strip().lower()
    if not email or "@" not in email:
        return False, "Invalid email address.", ""

    # Ensure owner has a team_id
    team_id = owner.get("team_id")
    if not team_id:
        team_id = "team_" + uuid.uuid4().hex[:12]
        from helpers.auth import update_user
        update_user(owner["id"], team_id=team_id, team_role="owner",
                    team_joined_at=datetime.utcnow().isoformat())
        owner["team_id"] = team_id

    # Seat check
    seats = get_seat_count(team_id)
    if seats >= MAX_TEAM_SEATS:
        return False, f"Your team is full ({MAX_TEAM_SEATS} seats max). Remove a member first.", ""

    # Don't invite yourself
    if email == owner.get("email", "").lower():
        return False, "You can't invite yourself.", ""

    # Check if already on the team
    existing = find_user_by_email(email)
    if existing and existing.get("team_id") == team_id:
        return False, f"{email} is already on your team.", ""

    # Generate token
    token = generate_invite_token(team_id, email)

    # If the user doesn't have an account, create a stub (team member with no password)
    if not existing:
        import secrets
        stub_pw = secrets.token_urlsafe(32)   # random unusable password
        existing = create_user(email, stub_pw, tier="team")

    # Store pending invite token on user
    from helpers.auth import update_user
    update_user(existing["id"], team_invite_token=token, team_id=None, team_role=None)

    return True, f"Invite sent to {email}.", token


def accept_invite(token: str) -> tuple[bool, str]:
    """Accept a team invite. Returns (success, message)."""
    from helpers.auth import update_user, find_user_by_email

    payload = verify_invite_token(token)
    if not payload:
        return False, "Invite link is invalid or has expired."

    team_id       = payload["team_id"]
    invitee_email = payload["email"]

    user = find_user_by_email(invitee_email)
    if not user:
        return False, "No account found for this invite. Please register first."

    # Check token still matches (prevents token reuse)
    stored_token = user.get("team_invite_token", "")
    if stored_token != token:
        return False, "This invite has already been used or was cancelled."

    # Seat check again
    if get_seat_count(team_id) >= MAX_TEAM_SEATS:
        return False, "The team is now full — ask your team owner to add a seat."

    update_user(
        user["id"],
        team_id=team_id,
        team_role="member",
        team_invite_token=None,
        team_joined_at=datetime.utcnow().isoformat(),
        tier="team",   # grant team tier on accept
    )
    return True, "You've joined the team!"


def remove_member(owner: dict, member_id: str) -> tuple[bool, str]:
    """Remove a member from the team (owner only)."""
    from helpers.auth import _load_users, update_user

    team_id = owner.get("team_id")
    if not team_id:
        return False, "You don't have an active team."

    users = _load_users()
    target = users.get(member_id)
    if not target:
        return False, "User not found."
    if target.get("team_id") != team_id:
        return False, "That user is not on your team."
    if target["id"] == owner["id"]:
        return False, "Team owners cannot remove themselves."

    # Downgrade to free and detach from team
    update_user(member_id, team_id=None, team_role=None, tier="free", team_joined_at=None)
    return True, f"{target['email']} removed from team."


def leave_team(user: dict) -> tuple[bool, str]:
    """A member voluntarily leaves a team."""
    from helpers.auth import update_user
    if not user.get("team_id"):
        return False, "You are not on a team."
    if user.get("team_role") == "owner":
        return False, "Team owners cannot leave — you must transfer ownership or delete the team first."
    update_user(user["id"], team_id=None, team_role=None, tier="free", team_joined_at=None)
    return True, "You've left the team."
