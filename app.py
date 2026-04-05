from flask import Flask, send_from_directory, make_response, Response
import os

# ── Setup pytesseract ──────────────────────────────────────────────────────────
from helpers.pdf_extract import setup_tesseract
setup_tesseract()

# ── Stripe billing (optional) ─────────────────────────────────────────────────
try:
    from helpers.stripe_billing import (
        create_checkout_session, create_customer_portal_session, handle_webhook,
        STRIPE_SECRET_KEY as _STRIPE_KEY_SET,
    )
    _STRIPE_AVAILABLE = bool(_STRIPE_KEY_SET)
except ImportError:
    _STRIPE_AVAILABLE = False
    create_checkout_session = create_customer_portal_session = handle_webhook = None

from helpers.stripe_webhook import verify_and_parse as _stripe_verify, dispatch_event as _stripe_dispatch

# ── Create Flask app ──────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='.', static_url_path='')

# ── Security configuration ────────────────────────────────────────────────────
_is_production = os.environ.get("DEED_APP_URL", "").startswith("https://")
app.config.update(
    SECRET_KEY=os.environ.get("DEED_SECRET_KEY", "dev-insecure-key-change-me"),
    SESSION_COOKIE_SECURE=_is_production,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# ── Register Blueprints ───────────────────────────────────────────────────────
from routes.auth import auth_bp
from routes.stripe import stripe_bp, init_stripe
from routes.admin import admin_bp
from routes.team import team_bp
from routes.config import config_bp
from routes.search import search_bp
from routes.plat import plat_bp
from routes.project import bp as project_bp
from routes.parcel_data import bp as parcel_data_bp
from routes.analysis import bp as analysis_bp

app.register_blueprint(auth_bp)
app.register_blueprint(stripe_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(team_bp)
app.register_blueprint(config_bp)
app.register_blueprint(search_bp)
app.register_blueprint(plat_bp)
app.register_blueprint(project_bp)
app.register_blueprint(parcel_data_bp)
app.register_blueprint(analysis_bp)

# Inject Stripe functions into the Stripe Blueprint (avoids circular import)
init_stripe(
    available=_STRIPE_AVAILABLE,
    checkout_fn=create_checkout_session,
    portal_fn=create_customer_portal_session,
    verify_fn=_stripe_verify,
    dispatch_fn=_stripe_dispatch,
)

# ── Static file routes ────────────────────────────────────────────────────────

@app.route("/")
def landing():
    """Public marketing/landing page."""
    resp = make_response(send_from_directory(".", "landing.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@app.route("/app")
def index():
    """The main SPA."""
    resp = make_response(send_from_directory(".", "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@app.route("/app.js")
def serve_appjs():
    return send_from_directory(".", "app.js")

@app.route("/style.css")
def serve_css():
    return send_from_directory(".", "style.css")

@app.route("/favicon.png")
def serve_favicon():
    return send_from_directory(".", "favicon.png")

@app.route("/robots.txt")
def robots_txt():
    """Block search engine crawlers from API, auth, and admin routes."""
    content = (
        "User-agent: *\n"
        "Disallow: /api/\n"
        "Disallow: /auth/\n"
        "Disallow: /admin/\n"
        "Disallow: /api/admin/\n"
        "Disallow: /api/stripe/\n"
        "Allow: /\n"
    )
    return app.response_class(content, mimetype="text/plain")

@app.route("/.well-known/security.txt")
def security_txt():
    """Standard security.txt."""
    _app_url = os.environ.get("DEED_APP_URL", "https://deedplathelper.netlify.app")
    content = (
        f"Contact: mailto:support@deedplathelper.com\n"
        f"Expires: 2027-01-01T00:00:00.000Z\n"
        f"Preferred-Languages: en\n"
        f"Canonical: {_app_url}/.well-known/security.txt\n"
        f"Policy: Please report security vulnerabilities responsibly via email before public disclosure.\n"
    )
    return app.response_class(content, mimetype="text/plain")


# ── Security headers ──────────────────────────────────────────────────────────

@app.after_request
def add_security_headers(response):
    """Add HTTP security headers and no-cache directives to every response."""
    if "max-age" not in response.headers.get("Cache-Control", ""):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if _is_production:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import socket as _sock
    def _get_lan_ip():
        try:
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
    _lan = _get_lan_ip()
    print("=" * 60)
    print("  Deed & Plat Helper")
    print("  Local:   http://localhost:5000")
    print(f"  Network: http://{_lan}:5000")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
