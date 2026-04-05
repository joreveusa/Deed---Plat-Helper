"""
routes/config.py — Configuration & profile management Blueprint.

Handles: user profiles CRUD, app config read/write, ArcGIS discover/test.
"""

import traceback

import requests as req_lib
from flask import Blueprint, request, jsonify

from helpers.profiles import (
    list_profiles, get_profile, save_profile, create_profile,
    delete_profile, migrate_from_config,
)
from helpers.subscription import require_auth, require_pro
from services.config import load_config, save_config
from services.arcgis import (
    get_arcgis_config, arcgis_field, arcgis_out_fields,
    ARCGIS_PRESETS, arcgis_lookup_upc, address_cache,
)
from services.portal import get_request_profile_id

config_bp = Blueprint("config", __name__)


# ── profiles ──────────────────────────────────────────────────────

@config_bp.route("/api/profiles", methods=["GET"])
def api_profiles_list():
    """Return all user profiles."""
    profiles = list_profiles()
    # If no profiles exist, auto-migrate from config.json
    if not profiles:
        cfg = load_config()
        p = migrate_from_config(cfg)
        profiles = [p]
    return jsonify({"success": True, "profiles": profiles})


@config_bp.route("/api/profiles", methods=["POST"])
def api_profiles_create():
    """Create a new user profile.  Body: { display_name }"""
    data = request.get_json(silent=True) or {}
    name = data.get("display_name", "").strip()
    if not name:
        return jsonify({"success": False, "error": "display_name is required"})
    p = create_profile(name)
    # Optionally copy shared credentials + portal URL into the new profile
    cfg = load_config()
    if cfg.get("firstnm_user"):
        p["firstnm_user"] = cfg["firstnm_user"]
        p["firstnm_pass"] = cfg.get("firstnm_pass", "")
        p["firstnm_url"]  = cfg.get("firstnm_url", "")
        save_profile(p)
    return jsonify({"success": True, "profile": p})


@config_bp.route("/api/profiles/<profile_id>", methods=["GET"])
def api_profile_get(profile_id):
    p = get_profile(profile_id)
    if p is None:
        return jsonify({"success": False, "error": "Profile not found"}), 404
    return jsonify({"success": True, "profile": p})


@config_bp.route("/api/profiles/<profile_id>", methods=["PUT"])
def api_profile_update(profile_id):
    """Update fields on a profile.  Body: { field: value, ... }"""
    p = get_profile(profile_id)
    if p is None:
        return jsonify({"success": False, "error": "Profile not found"}), 404
    data = request.get_json(silent=True) or {}
    for k, v in data.items():
        if k != "id":  # id is immutable
            p[k] = v
    save_profile(p)
    return jsonify({"success": True, "profile": p})


@config_bp.route("/api/profiles/<profile_id>", methods=["DELETE"])
def api_profile_delete(profile_id):
    ok = delete_profile(profile_id)
    return jsonify({"success": ok})


# ── config ─────────────────────────────────────────────────────────────────────

@config_bp.route("/api/config", methods=["GET", "POST"])
def api_config():
    profile_id = get_request_profile_id()
    profile = get_profile(profile_id) if profile_id else None

    if request.method == "GET":
        cfg = load_config()
        # Prefer profile-level credentials, fall back to server config
        if profile:
            user = profile.get("firstnm_user") or cfg.get("firstnm_user", "")
            pwd  = profile.get("firstnm_pass") or cfg.get("firstnm_pass", "")
            sess = profile.get("last_session") or cfg.get("last_session")
        else:
            user = cfg.get("firstnm_user") or cfg.get("username", "")
            pwd  = cfg.get("firstnm_pass") or cfg.get("password", "")
            sess = cfg.get("last_session")
        # ArcGIS layer — prefer profile, fall back to global config, then default
        arcgis_cfg = get_arcgis_config()
        return jsonify({
            "success": True,
            "config": {
                "firstnm_user": user,
                "firstnm_pass": pwd,
                "firstnm_url":  cfg.get("firstnm_url", ""),
                "last_session": sess,
                # ArcGIS layer config
                "arcgis_url":    arcgis_cfg["url"],
                "arcgis_fields": arcgis_cfg["fields"],
                "arcgis_is_default": not bool(
                    (profile and profile.get("arcgis_url")) or cfg.get("arcgis_url")
                ),
                # Expose presets so the frontend can offer a selector
                "arcgis_presets": [
                    {"id": k, "label": v["label"], "url": v["url"],
                     "fields": v["fields"]}
                    for k, v in ARCGIS_PRESETS.items()
                ],
            }
        })
    data = request.get_json()
    # Save user-specific fields to profile if available, otherwise config
    if profile:
        if "firstnm_user" in data or "username" in data:
            profile["firstnm_user"] = data.get("firstnm_user", data.get("username", ""))
        if "firstnm_pass" in data or "password" in data:
            profile["firstnm_pass"] = data.get("firstnm_pass", data.get("password", ""))
        if "last_session" in data:
            profile["last_session"] = data["last_session"]
        if "arcgis_url" in data:
            new_url = (data["arcgis_url"] or "").strip()
            if new_url:  # Only overwrite if user actually supplied a URL
                profile["arcgis_url"] = new_url
        if "arcgis_fields" in data and isinstance(data["arcgis_fields"], dict) and profile.get("arcgis_url"):
            profile["arcgis_fields"] = data["arcgis_fields"]
        if "firstnm_url" in data:
            profile["firstnm_url"] = data["firstnm_url"]
        save_profile(profile)
    else:
        cfg = load_config()
        if "firstnm_user" in data:
            cfg["firstnm_user"] = data["firstnm_user"]
        elif "username" in data:
            cfg["firstnm_user"] = data["username"]
        if "firstnm_pass" in data:
            cfg["firstnm_pass"] = data["firstnm_pass"]
        elif "password" in data:
            cfg["firstnm_pass"] = data["password"]
        if "firstnm_url" in data:
            cfg["firstnm_url"] = data["firstnm_url"]
        if "arcgis_url" in data:
            new_url = (data["arcgis_url"] or "").strip()
            if new_url:  # Only overwrite if user actually supplied a URL
                cfg["arcgis_url"] = new_url
        if "arcgis_fields" in data and isinstance(data["arcgis_fields"], dict) and cfg.get("arcgis_url"):
            cfg["arcgis_fields"] = data["arcgis_fields"]
        if "last_session" in data:
            cfg["last_session"] = data["last_session"]
        cfg.pop("username", None)
        cfg.pop("password", None)
        save_config(cfg)
    # Invalidate ArcGIS address cache so new settings take effect immediately
    address_cache.clear()
    return jsonify({"success": True})


# ── ArcGIS discover & test ──────────────────────────────────────────────────────

@config_bp.route("/api/arcgis-discover", methods=["POST"])
@require_auth
@require_pro
def api_arcgis_discover():
    """Probe an ArcGIS REST layer URL and return all available field names.

    Body: { "url": "https://...query" }  (or just the base layer URL)
    Returns: { success, fields: [{name, type, alias}], layer_info: {...} }
    """
    try:
        data = request.get_json() or {}
        raw_url = (data.get("url") or "").strip().rstrip("/")
        if not raw_url:
            return jsonify({"success": False, "error": "No URL provided"})

        # Strip /query suffix if present so we hit the layer metadata endpoint
        base_url = raw_url[:-6] if raw_url.lower().endswith("/query") else raw_url

        resp = req_lib.get(
            base_url,
            params={"f": "json"},
            headers={"User-Agent": "DeedPlatHelper/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            return jsonify({"success": False, "error": f"HTTP {resp.status_code} from ArcGIS server"})

        info = resp.json()
        if "error" in info:
            msg = info["error"].get("message", str(info["error"]))
            return jsonify({"success": False, "error": f"ArcGIS error: {msg}"})

        raw_fields = info.get("fields", [])
        if not raw_fields:
            return jsonify({"success": False,
                           "error": "No fields found. Make sure the URL points to a specific layer (ending in /0, /1, /29, etc.) not the service root."})

        fields = [
            {
                "name":  f.get("name", ""),
                "alias": f.get("alias") or f.get("name", ""),
                "type":  f.get("type", ""),
            }
            for f in raw_fields
            if f.get("name")
        ]

        return jsonify({
            "success": True,
            "fields":  fields,
            "layer_info": {
                "name":        info.get("name", ""),
                "description": info.get("description", ""),
                "geometry_type": info.get("geometryType", ""),
                "feature_count": info.get("maxRecordCount"),
            },
            "query_url": base_url + "/query",
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@config_bp.route("/api/arcgis-test", methods=["POST"])
def api_arcgis_test():
    """Run a sample parcel lookup with the provided ArcGIS config.

    Body: {
        "url":    "https://.../query",
        "fields": { concept: field_name, ... },   // field mapping to test
        "sample_id": "ABC123",                     // optional parcel ID to look up
    }
    Returns: { success, sample_result: {...}, matched_fields: [...] }
    """
    try:
        data = request.get_json() or {}
        url    = (data.get("url") or "").strip().rstrip("/")
        fields = data.get("fields") or {}
        sample_id = (data.get("sample_id") or "").strip()

        if not url:
            return jsonify({"success": False, "error": "No URL provided"})

        # Ensure URL ends with /query
        query_url = url if url.lower().endswith("/query") else url + "/query"

        # Build a test config
        test_cfg = {
            "url":    query_url,
            "fields": {
                **ARCGIS_PRESETS["taos_nm"]["fields"],  # defaults
                **{k: v for k, v in fields.items() if v},   # user overrides
            },
        }

        pid_field = arcgis_field(test_cfg, "parcel_id")

        if sample_id:
            # Try to look up the provided parcel ID
            result = arcgis_lookup_upc(sample_id, arcgis_cfg=test_cfg)
            if result:
                return jsonify({"success": True, "sample_result": result,
                               "tested_with": sample_id})
            return jsonify({"success": False,
                           "error": f"Parcel '{sample_id}' not found using field '{pid_field}'. Try a different sample ID."})

        # No sample ID — fetch the first record from the layer to verify connectivity
        out_flds = arcgis_out_fields(test_cfg, list(test_cfg["fields"].keys()))
        resp = req_lib.get(
            query_url,
            params={
                "where":             "1=1",
                "outFields":         out_flds,
                "returnGeometry":    "false",
                "resultRecordCount": "1",
                "f":                 "json",
            },
            headers={"User-Agent": "DeedPlatHelper/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            return jsonify({"success": False, "error": f"HTTP {resp.status_code} from ArcGIS"})

        resp_data = resp.json()
        if "error" in resp_data:
            return jsonify({"success": False,
                           "error": resp_data["error"].get("message", "ArcGIS query error")})

        features = resp_data.get("features", [])
        if not features:
            return jsonify({"success": False, "error": "Layer returned no features. Check the URL and ensure the layer has data."})

        sample_attrs = features[0].get("attributes", {})
        # Show which configured fields actually have data
        matched_fields = [
            {"concept": c, "field": f, "value": str(sample_attrs.get(f, "(not found)"))[:80]}
            for c, f in test_cfg["fields"].items()
            if f
        ]
        return jsonify({
            "success":        True,
            "sample_attrs":   {k: str(v)[:80] for k, v in sample_attrs.items()},
            "matched_fields": matched_fields,
            "record_count":   resp_data.get("exceededTransferLimit", False),
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})
