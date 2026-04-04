"""
helpers/auth.py — SaaS user authentication for Deed & Plat Helper.

Handles:
  - User account creation (email + bcrypt password)
  - JWT-style session tokens via itsdangerous
  - users.json persistence (no external DB required)
  - Tier management (free / pro / team)
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# ── Config ────────────────────────────────────────────────────────────────────
_HERE       = Path(__file__).parent.parent          # repo root
_USERS_FILE = _HERE / "users.json"
_SECRET_KEY = os.environ.get("DEED_SECRET_KEY", "dev-secret-change-in-production-!!!")
_TOKEN_MAX_AGE = 60 * 60 * 24 * 30                 # 30 days in seconds

_serializer = URLSafeTimedSerializer(_SECRET_KEY)


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_users() -> dict:
    if _USERS_FILE.exists():
        try:
            return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_users(users: dict) -> None:
    _USERS_FILE.write_text(
        json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── User CRUD ─────────────────────────────────────────────────────────────────

def get_user(user_id: str) -> dict | None:
    users = _load_users()
    return users.get(user_id)


def find_user_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    for user in _load_users().values():
        if user.get("email", "").lower() == email:
            return user
    return None


def create_user(email: str, password: str, tier: str = "free") -> dict:
    """Create a new user account. Raises ValueError if email already taken."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Invalid email address.")
    if find_user_by_email(email):
        raise ValueError("An account with this email already exists.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user_id = "u_" + uuid.uuid4().hex[:12]
    now     = datetime.utcnow().isoformat()

    user = {
        "id":                       user_id,
        "email":                    email,
        "password_hash":            pw_hash,
        "tier":                     tier,       # free | pro | team
        "stripe_customer_id":       None,
        "stripe_subscription_id":   None,
        "created_at":               now,
        "search_count_this_month":  0,
        "search_reset_date":        _next_month_str(),
        "active":                   True,
    }

    users = _load_users()
    users[user_id] = user
    _save_users(users)
    return user


def update_user(user_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a user record."""
    users = _load_users()
    if user_id not in users:
        return None
    for k, v in fields.items():
        if k not in ("id",):         # id is immutable
            users[user_id][k] = v
    _save_users(users)
    return users[user_id]


# ── Password helpers ──────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── Session tokens ────────────────────────────────────────────────────────────

def generate_token(user_id: str) -> str:
    return _serializer.dumps(user_id, salt="auth-token")


def verify_token(token: str) -> str | None:
    """Return user_id if token is valid and not expired, else None."""
    try:
        user_id = _serializer.loads(token, salt="auth-token", max_age=_TOKEN_MAX_AGE)
        return user_id
    except (BadSignature, SignatureExpired):
        return None


# ── Usage tracking ────────────────────────────────────────────────────────────

def _next_month_str() -> str:
    now   = datetime.utcnow()
    if now.month == 12:
        nxt = now.replace(year=now.year + 1, month=1, day=1)
    else:
        nxt = now.replace(month=now.month + 1, day=1)
    return nxt.strftime("%Y-%m-%d")


def reset_monthly_counts_if_needed(user: dict) -> dict:
    """Auto-reset search counter at the start of a new month."""
    reset_date = user.get("search_reset_date", "")
    if reset_date and datetime.utcnow().strftime("%Y-%m-%d") >= reset_date:
        user = update_user(
            user["id"],
            search_count_this_month=0,
            search_reset_date=_next_month_str(),
        ) or user
    return user


def increment_search_count(user: dict) -> dict:
    new_count = user.get("search_count_this_month", 0) + 1
    return update_user(user["id"], search_count_this_month=new_count) or user


# ── Safe user dict (no password hash) ────────────────────────────────────────

def public_user(user: dict) -> dict:
    """Return a copy of the user dict safe to send to the frontend."""
    return {k: v for k, v in user.items() if k != "password_hash"}
