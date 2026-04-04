"""
helpers/rate_limit.py — In-memory sliding-window rate limiter for Flask.

No Redis or external services required. State is per-process, per-IP.
Resets automatically on process restart (acceptable for local deployments).

Usage:
    from helpers.rate_limit import rate_limit

    @app.route("/api/search", methods=["POST"])
    @require_auth
    @rate_limit(requests=30, window=60)   # 30 searches per 60 seconds per IP
    def api_search():
        ...
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from functools import wraps

from flask import request, jsonify


# ip → { endpoint_key → deque of timestamps }
_windows: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))


def _get_ip() -> str:
    """Return the request's real IP, honoring X-Forwarded-For if present."""
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or "unknown"


def rate_limit(requests: int = 60, window: int = 60, key: str | None = None):
    """Decorator: allow at most `requests` calls per `window` seconds per IP.

    Args:
        requests: Maximum number of allowed requests in the window.
        window:   Sliding window size in seconds.
        key:      Optional override for the bucket key (defaults to endpoint name).
    """
    def decorator(f):
        bucket_key = key or f.__name__

        @wraps(f)
        def wrapper(*args, **kwargs):
            ip    = _get_ip()
            now   = time.monotonic()
            dq    = _windows[ip][bucket_key]

            # Evict timestamps outside the current window
            while dq and now - dq[0] > window:
                dq.popleft()

            if len(dq) >= requests:
                oldest      = dq[0]
                retry_after = max(1, int(window - (now - oldest)) + 1)
                resp = jsonify({
                    "success":     False,
                    "error":       f"Too many requests — please wait {retry_after}s before trying again.",
                    "retry_after": retry_after,
                })
                resp.status_code = 429
                resp.headers["Retry-After"] = str(retry_after)
                return resp

            dq.append(now)
            return f(*args, **kwargs)

        return wrapper
    return decorator


def rate_limit_ip(ip_key: str = "", requests: int = 5, window: int = 60) -> tuple[bool, int]:
    """Non-decorator version: check rate limit programmatically.

    Returns (allowed: bool, retry_after: int).
    Useful for login/register endpoints where you want custom error messages.
    """
    now = time.monotonic()
    dq  = _windows[ip_key or _get_ip()]["__manual__"]

    while dq and now - dq[0] > window:
        dq.popleft()

    if len(dq) >= requests:
        retry_after = max(1, int(window - (now - dq[0])) + 1)
        return False, retry_after

    dq.append(now)
    return True, 0
