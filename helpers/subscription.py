"""
helpers/subscription.py — Tier-based feature gating for Deed & Plat Helper SaaS.

Tiers:  free  →  pro  →  team
Each tier inherits all permissions of the tier below it.
"""

from functools import wraps
from flask import request, jsonify, g
from helpers.auth import verify_token, get_user, reset_monthly_counts_if_needed


# ── Tier definitions ──────────────────────────────────────────────────────────

TIERS = ["free", "pro", "team"]

TIER_LIMITS: dict[str, dict] = {
    "free": {
        "searches_per_month": 10,
        "ocr":                False,
        "parcel_map":         False,
        "adjoiners":          False,
        "dxf_export":         False,
        "max_sessions":       1,
        "max_seats":          1,
    },
    "pro": {
        "searches_per_month": None,   # None = unlimited
        "ocr":                True,
        "parcel_map":         True,
        "adjoiners":          True,
        "dxf_export":         True,
        "max_sessions":       None,
        "max_seats":          1,
    },
    "team": {
        "searches_per_month": None,
        "ocr":                True,
        "parcel_map":         True,
        "adjoiners":          True,
        "dxf_export":         True,
        "max_sessions":       None,
        "max_seats":          5,
    },
}

TIER_LABELS = {
    "free":  "Free",
    "pro":   "Pro — $29/mo",
    "team":  "Team — $79/mo",
}

UPGRADE_MESSAGES = {
    "ocr":         "OCR text extraction is a Pro feature. Upgrade to Pro to extract text from scanned deeds.",
    "parcel_map":  "Live parcel maps require a Pro subscription.",
    "adjoiners":   "Adjoiner auto-discovery requires a Pro subscription.",
    "dxf_export":  "DXF boundary export requires a Pro subscription.",
    "searches":    "You've used all {used} of your {limit} free searches this month. Upgrade to Pro for unlimited searches.",
}


def get_tier_limits(tier: str) -> dict:
    return TIER_LIMITS.get(tier, TIER_LIMITS["free"])


def tier_rank(tier: str) -> int:
    return TIERS.index(tier) if tier in TIERS else 0


def has_feature(user: dict, feature: str) -> bool:
    """Check if a user's tier grants access to a given feature."""
    limits = get_tier_limits(user.get("tier", "free"))
    return bool(limits.get(feature, False))


def check_search_quota(user: dict) -> tuple[bool, str]:
    """Returns (allowed, error_message). Checks monthly search limit."""
    user  = reset_monthly_counts_if_needed(user)
    tier  = user.get("tier", "free")
    limit = TIER_LIMITS[tier]["searches_per_month"]
    if limit is None:
        return True, ""
    used = user.get("search_count_this_month", 0)
    if used >= limit:
        msg = UPGRADE_MESSAGES["searches"].format(used=used, limit=limit)
        return False, msg
    return True, ""


# ── Flask decorators ──────────────────────────────────────────────────────────

def _get_token_from_request() -> str | None:
    # 1. Cookie (preferred — set on login)
    token = request.cookies.get("deed_token")
    if token:
        return token
    # 2. Authorization header (API clients)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def require_auth(f):
    """Decorator: requires a valid deed_token cookie or Bearer header.
    Attaches g.current_user on success."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token   = _get_token_from_request()
        user_id = verify_token(token) if token else None
        user    = get_user(user_id) if user_id else None
        if not user or not user.get("active", True):
            return jsonify({
                "success":   False,
                "error":     "Authentication required.",
                "auth_required": True,
            }), 401
        g.current_user = reset_monthly_counts_if_needed(user)
        return f(*args, **kwargs)
    return wrapper


def require_pro(f):
    """Decorator: requires pro or team tier. Must be used AFTER @require_auth."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = getattr(g, "current_user", None)
        if not user or tier_rank(user.get("tier", "free")) < tier_rank("pro"):
            return jsonify({
                "success":        False,
                "error":          "This feature requires a Pro subscription.",
                "upgrade_required": True,
                "current_tier":   user.get("tier") if user else "free",
                "required_tier":  "pro",
            }), 403
        return f(*args, **kwargs)
    return wrapper


def require_team(f):
    """Decorator: requires team tier. Must be used AFTER @require_auth."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = getattr(g, "current_user", None)
        if not user or tier_rank(user.get("tier", "free")) < tier_rank("team"):
            return jsonify({
                "success":        False,
                "error":          "This feature requires a Team subscription.",
                "upgrade_required": True,
                "current_tier":   user.get("tier") if user else "free",
                "required_tier":  "team",
            }), 403
        return f(*args, **kwargs)
    return wrapper
