"""services/ — Stateful service layer for Deed & Plat Helper.

These modules wrap helpers/ with Flask-request-aware context (profile cookies,
config file I/O, module-level caches) so that Blueprints can import from here
instead of depending on app.py module-level state.
"""
