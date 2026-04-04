"""
helpers/admin.py — Simple admin utilities for Deed & Plat Helper SaaS.

Provides read-only and write operations over users.json for the admin panel.
No external dependencies — pure Python.
"""

from __future__ import annotations

import os
from datetime import datetime
from helpers.auth import _load_users, update_user
from helpers.subscription import TIER_LIMITS, TIERS

# Admin password — set via env var, falls back to SECRET_KEY-derived value
_ADMIN_PASSWORD = os.environ.get(
    "DEED_ADMIN_PASSWORD",
    os.environ.get("DEED_SECRET_KEY", "dev-secret-change-in-production-!!!")[:16],
)


def check_admin_password(password: str) -> bool:
    """Return True if the supplied password matches the admin password."""
    return bool(password) and password == _ADMIN_PASSWORD


def list_users_summary() -> list[dict]:
    """Return a summary list of all users safe to expose in the admin panel."""
    users = _load_users()
    now = datetime.utcnow().strftime("%Y-%m-%d")
    result = []
    for uid, u in users.items():
        tier = u.get("tier", "free")
        limit = TIER_LIMITS.get(tier, TIER_LIMITS["free"])["searches_per_month"]
        result.append({
            "id":            uid,
            "email":         u.get("email", ""),
            "tier":          tier,
            "active":        u.get("active", True),
            "created_at":    u.get("created_at", ""),
            "searches_used": u.get("search_count_this_month", 0),
            "search_limit":  limit,          # None = unlimited
            "reset_date":    u.get("search_reset_date", ""),
            "has_stripe":    bool(u.get("stripe_customer_id")),
            "stripe_cus_id": u.get("stripe_customer_id") or "",
            "stripe_sub_id": u.get("stripe_subscription_id") or "",
            "overdue":       (u.get("search_reset_date", "") or "") < now,
        })
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return result


def get_user_stats() -> dict:
    """High-level stats for the admin dashboard."""
    users = _load_users()
    total = len(users)
    by_tier: dict[str, int] = {}
    active = 0
    for u in users.values():
        t = u.get("tier", "free")
        by_tier[t] = by_tier.get(t, 0) + 1
        if u.get("active", True):
            active += 1
    mrr = (by_tier.get("pro", 0) * 29) + (by_tier.get("team", 0) * 79)
    return {
        "total_users":  total,
        "active_users": active,
        "by_tier":      {t: by_tier.get(t, 0) for t in TIERS},
        "mrr_usd":      mrr,
    }


def admin_set_tier(user_id: str, tier: str) -> dict | None:
    """Manually set a user's tier. Returns updated user or None if not found."""
    if tier not in TIERS:
        raise ValueError(f"Invalid tier {tier!r} — must be one of {TIERS}")
    return update_user(user_id, tier=tier)


def admin_toggle_active(user_id: str, active: bool) -> dict | None:
    """Enable or disable a user account."""
    return update_user(user_id, active=active)


def admin_reset_searches(user_id: str) -> dict | None:
    """Reset the monthly search counter for a user."""
    from helpers.auth import _next_month_str
    return update_user(user_id, search_count_this_month=0,
                       search_reset_date=_next_month_str())
