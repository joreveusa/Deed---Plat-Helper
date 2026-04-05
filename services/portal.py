"""
services/portal.py — County records portal session management.

Manages per-user requests.Session objects (cookies don't collide),
portal URL resolution, and HTML form scraping helpers.
"""

import threading

import requests as req_lib
from flask import request

from helpers.profiles import get_profile
from services.config import load_config

# Default portal URL — overridden per-user via Settings → URL field.
_DEFAULT_PORTAL_URL = "http://records.1stnmtitle.com"

# ── Per-user web sessions (multi-user support) ──────────────────────────────
_user_sessions: dict[str, req_lib.Session] = {}  # profile_id -> Session
_user_sessions_lock = threading.Lock()

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _get_web_session(profile_id: str | None = None) -> req_lib.Session:
    """Return a per-user requests.Session.  Falls back to a shared one."""
    key = profile_id or "__default__"
    with _user_sessions_lock:
        if key not in _user_sessions:
            s = req_lib.Session()
            s.headers.update(_DEFAULT_HEADERS)
            _user_sessions[key] = s
        return _user_sessions[key]


# Backward-compat: callers that still reference `web_session` get the default
web_session = _get_web_session()


def get_request_profile_id() -> str | None:
    """Extract profile_id from request cookie or query param."""
    return request.cookies.get("profile_id") or request.args.get("profile_id")


def get_request_session() -> req_lib.Session:
    """Return the web session for the current request's profile."""
    return _get_web_session(get_request_profile_id())


def get_session() -> req_lib.Session:
    """Return the web session for the current request's profile.

    Reads the profile_id cookie set by the frontend and dispatches to
    the correct per-user requests.Session.  Falls back to the shared
    default session if no cookie is present.
    """
    try:
        pid = request.cookies.get('profile_id')
    except RuntimeError:
        pid = None  # called outside request context
    return _get_web_session(pid)


def get_portal_url() -> str:
    """Return the county records portal base URL for the current request.

    Reads from the active profile first, falls back to global config, then
    falls back to the compiled-in default.  Strips trailing slashes.
    """
    try:
        pid = request.cookies.get('profile_id')
    except RuntimeError:
        pid = None  # called outside request context

    url = ""
    if pid:
        p = get_profile(pid)
        if p:
            url = p.get("firstnm_url", "").strip()
    if not url:
        cfg = load_config()
        url = cfg.get("firstnm_url", "").strip()
    return (url or _DEFAULT_PORTAL_URL).rstrip("/")


def scrape_form_data(soup) -> dict:
    """Pull all input/select default values from the first <form> in soup.

    Returns a flat dict of {name: value} ready for a POST.
    """
    form = soup.find("form")
    if not form:
        return {}
    fd: dict = {}
    for inp in form.find_all("input"):
        nm = inp.get("name")
        if nm:
            fd[nm] = inp.get("value", "")
    for sel in form.find_all("select"):
        nm = sel.get("name")
        if nm:
            opt = sel.find("option", selected=True) or sel.find("option")
            fd[nm] = opt["value"] if opt and opt.get("value") else ""
    return fd
