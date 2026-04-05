"""
services/arcgis.py — ArcGIS parcel layer configuration, lookup, and geocoding.

Manages the dynamic ArcGIS REST FeatureService/MapServer config per
user/profile, provides parcel lookups, spatial queries, and Nominatim
reverse-geocode fallback.
"""

import json
import time as _time

import requests as req_lib
from flask import request

from helpers.profiles import get_profile
from services.config import load_config

# ── ArcGIS Parcel Layer — configurable per user ────────────────────────────────
# Built-in preset: NM OSE statewide parcel service, Taos County layer
ARCGIS_PRESETS = {
    "taos_nm": {
        "label": "Taos County, NM (default)",
        "url": "https://gis.ose.nm.gov/server_s/rest/services/Parcels/County_Parcels_2025/MapServer/29/query",
        "fields": {
            "parcel_id":   "UPC",
            "owner":       "OwnerAll",
            "address_all": "SitusAddressAll",
            "address1":    "SitusAddress1",
            "street_no":   "SitusStreetNumber",
            "street_name": "SitusStreetName",
            "city":        "SitusCity",
            "zipcode":     "SitusZipCode",
            "legal":       "LegalDescription",
            "area":        "LandArea",
            "subdivision": "Subdivision",
            "zoning":      "ZoningDescription",
            "land_use":    "LandUseDescription",
            "township":    "Township",
            "twp_dir":     "TownshipDirection",
            "range":       "Range",
            "rng_dir":     "RangeDirection",
            "section":     "Section",
            "struct_count":"StructureCount",
            "struct_type": "StructureType",
            "owner_type":  "OwnerType",
            "mail_addr":   "MailAddressAll",
        },
    },
}

# Concepts that are optional — silently skipped if the field is not configured
ARCGIS_OPTIONAL_FIELDS = {
    "address1", "street_no", "street_name", "city", "zipcode",
    "zoning", "land_use", "struct_count", "struct_type", "owner_type",
    "mail_addr", "twp_dir", "rng_dir",
}


# ── In-memory caches ────────────────────────────────────────────────────────
address_cache: dict = {}  # { "upc:12345" or "ll:lat,lon" : { address dict } }
_nominatim_last_call: float = 0.0  # monotonic timestamp of last Nominatim call

# ── Nominatim (fallback) ─────────────────────────────────────────────────────
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_HEADERS = {
    "User-Agent": "DeedPlatHelper/1.0 (land-survey-research-tool)",
    "Accept": "application/json",
}


def get_arcgis_config() -> dict:
    """Return the ArcGIS layer config for the current request.

    Reads from active profile → global config → Taos NM default.
    Returns dict: { url, fields: { concept: field_name } }
    """
    try:
        pid = request.cookies.get('profile_id')
    except RuntimeError:
        pid = None

    stored = None
    if pid:
        p = get_profile(pid)
        if p and p.get('arcgis_url'):
            stored = p
    if not stored:
        cfg = load_config()
        if cfg.get('arcgis_url'):
            stored = cfg

    if stored:
        # Merge user-supplied fields over the default field map
        default_fields = dict(ARCGIS_PRESETS['taos_nm']['fields'])
        user_fields = stored.get('arcgis_fields') or {}
        default_fields.update({k: v for k, v in user_fields.items() if v})
        return {
            'url':    stored['arcgis_url'].rstrip('/'),
            'fields': default_fields,
        }

    # No user config — use built-in Taos NM default
    preset = ARCGIS_PRESETS['taos_nm']
    return {'url': preset['url'], 'fields': dict(preset['fields'])}


def arcgis_field(cfg: dict, concept: str) -> str:
    """Return the actual ArcGIS attribute field name for a logical concept.
    Falls back to the Taos default if not configured."""
    return cfg['fields'].get(concept) or ARCGIS_PRESETS['taos_nm']['fields'].get(concept, '')


def arcgis_out_fields(cfg: dict, concepts: list) -> str:
    """Build a comma-separated outFields string for a given list of concepts."""
    fields = []
    for c in concepts:
        f = arcgis_field(cfg, c)
        if f and f not in fields:
            fields.append(f)
    return ','.join(fields)


def arcgis_lookup_upc(upc: str, arcgis_cfg: dict = None) -> dict:
    """Query the configured ArcGIS parcel layer by parcel ID.

    Uses the dynamic ArcGIS config (URL + field mapping) so any county's
    layer works, not just Taos NM.
    Returns a normalised dict with address fields, or None on failure.
    """
    if not upc:
        return None

    cache_key = f"upc:{upc}"
    if cache_key in address_cache:
        return address_cache[cache_key]

    cfg = arcgis_cfg or get_arcgis_config()
    fld = lambda c: arcgis_field(cfg, c)  # shorthand

    pid_field = fld('parcel_id')
    out_concepts = [
        'parcel_id', 'owner',
        'address_all', 'address1', 'street_no', 'street_name', 'city', 'zipcode',
        'legal', 'area', 'subdivision', 'zoning', 'land_use',
        'township', 'twp_dir', 'range', 'rng_dir', 'section',
        'struct_count', 'struct_type', 'owner_type', 'mail_addr',
    ]
    out_flds = arcgis_out_fields(cfg, out_concepts)

    try:
        resp = req_lib.get(
            cfg['url'],
            params={
                "where":          f"{pid_field}='{upc}'",
                "outFields":      out_flds,
                "returnGeometry": "false",
                "f":              "json",
            },
            headers={"User-Agent": "DeedPlatHelper/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[address] ArcGIS returned {resp.status_code} for {pid_field}={upc}", flush=True)
            return None

        data = resp.json()
        features = data.get("features", [])
        if not features:
            print(f"[address] ArcGIS: no features for {pid_field}={upc}", flush=True)
            return None

        attrs = features[0].get("attributes", {})
        g = lambda c: (attrs.get(fld(c)) or "").strip() if isinstance(attrs.get(fld(c)), str) else (attrs.get(fld(c)) or "")

        # Build short address — prefer address1, then street_no+name, then address_all
        situs_all = g('address_all')
        situs1    = g('address1')
        street_no = g('street_no')
        street_nm = g('street_name')
        city      = g('city')
        zipcode   = g('zipcode')

        if situs1:
            short_addr = situs1 + (f", {city}" if city else "")
        elif street_no and street_nm:
            short_addr = f"{street_no} {street_nm}" + (f", {city}" if city else "")
        elif situs_all and situs_all != zipcode:
            short_addr = situs_all
        else:
            short_addr = ""

        # Build TRS string
        twp     = str(g('township'))
        twp_dir = str(g('twp_dir'))
        rng     = str(g('range'))
        rng_dir = str(g('rng_dir'))
        sec     = str(g('section'))
        trs_str = ""
        if twp and rng:
            trs_str = f"T{twp}{twp_dir} R{rng}{rng_dir}"
            if sec:
                trs_str += f" Sec {sec}"

        # land_area may be numeric
        area_raw = attrs.get(fld('area'))
        area_val = area_raw if area_raw is not None else ""

        result = {
            "success":           True,
            "source":            "arcgis",
            "short_address":     short_addr or "(no street address on file)",
            "situs_full":        situs_all,
            "situs_address1":    situs1,
            "street_number":     street_no,
            "street_name":       street_nm,
            "city":              city,
            "zipcode":           zipcode,
            "owner_official":    g('owner'),
            "legal_description": g('legal'),
            "land_area":         area_val,
            "upc":               upc,
            "has_street_address": bool(situs1 or (street_no and street_nm)),
            "subdivision":       g('subdivision'),
            "zoning":            g('zoning'),
            "land_use":          g('land_use'),
            "trs":               trs_str,
            "structure_count":   attrs.get(fld('struct_count')) or 0,
            "structure_type":    g('struct_type'),
            "owner_type":        g('owner_type'),
            "mail_address":      g('mail_addr'),
            # Pass through the config so callers know what layer was used
            "arcgis_url":        cfg['url'],
        }

        address_cache[cache_key] = result
        print(f"[address] ArcGIS {pid_field}={upc} → {result['short_address']}", flush=True)
        return result

    except Exception as e:
        print(f"[address] ArcGIS error for {upc}: {e}", flush=True)
        return None


def nominatim_reverse(lat: float, lon: float) -> dict:
    """Call Nominatim reverse-geocode for a single lat/lon pair.

    Returns a dict with address fields, or an error dict.
    Enforces 1-second rate limiting and caches results.
    """
    global _nominatim_last_call

    cache_key = f"ll:{round(lat, 5)},{round(lon, 5)}"
    if cache_key in address_cache:
        return address_cache[cache_key]

    # Rate-limit: min 1 second between Nominatim calls
    now = _time.monotonic()
    wait = 1.05 - (now - _nominatim_last_call)
    if wait > 0:
        _time.sleep(wait)

    try:
        resp = req_lib.get(
            NOMINATIM_URL,
            params={
                "format": "json",
                "lat": lat,
                "lon": lon,
                "addressdetails": 1,
                "zoom": 18,
            },
            headers=NOMINATIM_HEADERS,
            timeout=10,
        )
        _nominatim_last_call = _time.monotonic()

        if resp.status_code != 200:
            return {"success": False, "source": "nominatim", "error": f"HTTP {resp.status_code}"}

        data = resp.json()
        addr = data.get("address", {})

        # Build short human-readable address
        parts = []
        if addr.get("house_number"):
            parts.append(addr["house_number"])
        if addr.get("road"):
            parts.append(addr["road"])
        if not parts and addr.get("hamlet"):
            parts.append(addr["hamlet"])
        if not parts and addr.get("village"):
            parts.append(addr["village"])
        locality = addr.get("town") or addr.get("city") or addr.get("village") or addr.get("hamlet") or ""
        if locality and locality not in parts:
            parts.append(locality)

        result = {
            "success":           True,
            "source":            "nominatim",
            "short_address":     ", ".join(parts) if parts else "(no address found)",
            "road":              addr.get("road", ""),
            "house_number":      addr.get("house_number", ""),
            "hamlet":            addr.get("hamlet", ""),
            "village":           addr.get("village", ""),
            "town":              addr.get("town", ""),
            "city":              addr.get("city", ""),
            "county":            addr.get("county", ""),
            "state":             addr.get("state", ""),
            "postcode":          addr.get("postcode", ""),
            "has_street_address": bool(addr.get("house_number") and addr.get("road")),
            "lat":               lat,
            "lon":               lon,
        }

        address_cache[cache_key] = result
        print(f"[address] Nominatim {lat},{lon} → {result['short_address']}", flush=True)
        return result

    except Exception as e:
        _nominatim_last_call = _time.monotonic()
        print(f"[address] Nominatim error for {lat},{lon}: {e}", flush=True)
        return {"success": False, "source": "nominatim", "error": str(e)}


def arcgis_get_parcel_geometry(upc: str, arcgis_cfg: dict = None) -> dict | None:
    """Fetch the polygon geometry for a parcel from ArcGIS by parcel ID.

    Uses the dynamic ArcGIS config so any county layer works.
    Returns { "rings": [...], "spatialReference": {...} } or None.
    """
    if not upc:
        return None
    cfg = arcgis_cfg or get_arcgis_config()
    pid_field = arcgis_field(cfg, 'parcel_id')
    owner_field = arcgis_field(cfg, 'owner')
    try:
        resp = req_lib.get(
            cfg['url'],
            params={
                "where":          f"{pid_field}='{upc}'",
                "outFields":      f"{pid_field},{owner_field}",
                "returnGeometry": "true",
                "outSR":          "4326",
                "f":              "json",
            },
            headers={"User-Agent": "DeedPlatHelper/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        features = resp.json().get("features", [])
        if not features:
            return None
        return features[0].get("geometry")
    except Exception as e:
        print(f"[arcgis-adj] Geometry fetch error for {upc}: {e}", flush=True)
        return None


def arcgis_find_touching_parcels(geometry: dict, arcgis_cfg: dict = None) -> list:
    """Query ArcGIS for all parcels that spatially touch the given polygon.

    Uses esriSpatialRelTouches first, falls back to esriSpatialRelIntersects.
    Returns a list of normalised dicts regardless of the county's field names.
    """
    cfg = arcgis_cfg or get_arcgis_config()
    fld = lambda c: arcgis_field(cfg, c)
    adj_concepts = [
        'parcel_id', 'owner', 'area', 'subdivision', 'legal',
        'address_all', 'township', 'twp_dir', 'range', 'rng_dir', 'section',
    ]
    out_flds = arcgis_out_fields(cfg, adj_concepts)

    results = []
    for spatial_rel in ("esriSpatialRelTouches", "esriSpatialRelIntersects"):
        try:
            resp = req_lib.get(
                cfg['url'],
                params={
                    "geometry":          json.dumps(geometry),
                    "geometryType":      "esriGeometryPolygon",
                    "spatialRel":        spatial_rel,
                    "inSR":              "4326",
                    "outSR":             "4326",
                    "outFields":         out_flds,
                    "returnGeometry":    "false",
                    "resultRecordCount": "100",
                    "f":                 "json",
                },
                headers={"User-Agent": "DeedPlatHelper/1.0"},
                timeout=20,
            )
            if resp.status_code != 200:
                continue
            features = resp.json().get("features", [])
            if features:
                for feat in features:
                    a = feat.get("attributes", {})
                    g = lambda c: (a.get(fld(c)) or "").strip() if isinstance(a.get(fld(c)), str) else str(a.get(fld(c)) or "")
                    twp = g('township'); twp_dir = g('twp_dir')
                    rng = g('range');    rng_dir = g('rng_dir')
                    sec = g('section')
                    trs = f"T{twp}{twp_dir} R{rng}{rng_dir}" if twp and rng else ""
                    if trs and sec:
                        trs += f" Sec {sec}"
                    legal_raw = a.get(fld('legal')) or ""
                    results.append({
                        "upc":         g('parcel_id'),
                        "owner":       g('owner'),
                        "land_area":   a.get(fld('area')) or 0,
                        "subdivision": g('subdivision'),
                        "legal":       (str(legal_raw).strip())[:200],
                        "address":     g('address_all'),
                        "trs":         trs,
                        "source":      "arcgis_spatial",
                        "spatial_rel": spatial_rel,
                    })
                break
        except Exception as e:
            print(f"[arcgis-adj] Spatial query error ({spatial_rel}): {e}", flush=True)
            continue
    return results
