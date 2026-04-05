"""
services/config.py — Application configuration I/O.

Centralises config.json reading and writing so Blueprints never need to
know the file path or deal with JSON encoding directly.
"""

import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

# Must match the <select> options in index.html
JOB_TYPES = ["BDY", "ILR", "SE", "SUB", "TIE", "TOPO", "ELEV", "ALTA", "CONS", "OTHER"]


def load_config() -> dict:
    """Load config.json and return its contents as a dict.
    Returns an empty dict if the file doesn't exist or is malformed."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(data: dict):
    """Write *data* to config.json (overwrites the entire file)."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
