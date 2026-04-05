"""
routes/parcel_data.py - KML/XML parcel data, ArcGIS address/adjoiners, cabinet browse Blueprint.
"""

import gzip
import json
import re
import traceback
from pathlib import Path

from flask import Blueprint, request, jsonify, Response

from helpers.cabinet import (
    CABINET_FOLDERS,
    extract_cabinet_display_name as _extract_cabinet_display_name,
    extract_cabinet_doc_number as _extract_cabinet_doc_number,
)
from helpers.subscription import require_auth, require_pro
from services.arcgis import (
    arcgis_lookup_upc, nominatim_reverse,
    arcgis_get_parcel_geometry, arcgis_find_touching_parcels,
)
from services.drive import get_survey_data_path, get_cabinet_path
import xml_processor

bp = Blueprint("parcel_data", __name__)


@bp.route("/api/cabinet-browse")
def api_cabinet_browse():
    """
    List files in a cabinet folder, with optional name filter.
    Query params: cabinet (A-F), filter (substring, case-insensitive), page, per_page
    """
    try:
        cabinet  = (request.args.get("cabinet") or "").upper().strip()
        filt     = (request.args.get("filter") or "").lower().strip()
        page     = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))

        if not cabinet:
            # Return list of available cabinets
            return jsonify({"success": True, "cabinets": list(CABINET_FOLDERS.keys())})

        folder_name = CABINET_FOLDERS.get(cabinet)
        if not folder_name:
            return jsonify({"success": False, "error": f"Unknown cabinet '{cabinet}'"})

        cab_dir = Path(get_cabinet_path()) / folder_name
        if not cab_dir.exists():
            return jsonify({"success": False, "error": "Cabinet folder not found on disk"})

        files = []
        for f in sorted(cab_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() != '.pdf':
                continue
            if filt and filt not in f.name.lower():
                continue
            files.append({
                "file":         f.name,
                "path":         str(f),
                "display_name": _extract_cabinet_display_name(f.name),
                "doc_number":   _extract_cabinet_doc_number(f.name),
                "size_kb":      round(f.stat().st_size / 1024),
            })

        total = len(files)
        start = (page - 1) * per_page
        paged = files[start:start + per_page]

        return jsonify({
            "success":  True,
            "cabinet":  cabinet,
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "files":    paged,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/xml/status")
def api_xml_status():
    """Return status of the parcel index (exists, record count, age, source files)."""
    try:
        survey = get_survey_data_path()
        status = xml_processor.index_status(survey)
        return jsonify({"success": True, **status})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/index-health")
def api_index_health():
    """Return comprehensive health metrics about the parcel index.

    Reports completeness percentages (UPC, polygon, ArcGIS enrichment, owner names),
    index freshness (age, stale warning, newer XML files), and source breakdown.
    """
    try:
        survey = get_survey_data_path()
        health = xml_processor.compute_index_health(survey)
        return jsonify({"success": True, **health})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/data-conflicts")
def api_data_conflicts():
    """Detect cross-source anomalies between KML parcel data and ArcGIS enrichment.

    Flags owner mismatches, area discrepancies, TRS conflicts, and parcels
    missing ArcGIS data. Returns a summary and up to 200 individual conflicts.

    Query params: max_conflicts (int, default 200)
    """
    try:
        survey = get_survey_data_path()
        max_c  = int(request.args.get("max_conflicts", 200))
        result = xml_processor.detect_data_conflicts(survey, max_conflicts=max_c)
        return jsonify({"success": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/xml/build-index", methods=["POST"])
def api_xml_build_index():
    """Parse all KML/KMZ files in the XML folder and build/rebuild the parcel index."""
    try:
        survey = get_survey_data_path()
        result = xml_processor.build_index(survey)
        if "error" in result:
            return jsonify({"success": False, "error": result["error"]})
        return jsonify({"success": True, **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/xml/enrich-index", methods=["POST"])
def api_xml_enrich_index():
    """Trigger ArcGIS enrichment of the existing parcel index.

    Enriches parcels with TRS, legal description, subdivision, zoning, etc.
    without requiring a full KML/KMZ re-parse. The enriched data is saved
    back to the index JSON file.
    """
    try:
        survey = get_survey_data_path()
        idx = xml_processor.load_index(survey, force=True)
        if not idx:
            return jsonify({"success": False, "error": "No parcel index found. Build the index first."})

        stats = xml_processor.enrich_index_with_arcgis(idx)

        # Save updated index back to disk
        idx_path = xml_processor._index_path(survey)
        idx_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")

        # Force cache refresh
        xml_processor._cached_index = idx
        xml_processor._cached_index_mtime = idx_path.stat().st_mtime

        return jsonify({"success": True, **stats})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/xml/search", methods=["POST"])
def api_xml_search():
    """
    Search parcel index by owner name, UPC, book/page, cabinet ref, TRS, or subdivision.

    Body: { owner, upc, book, page, cabinet_ref, trs, subdivision, operator, limit }
    """
    try:
        survey = get_survey_data_path()
        data   = request.get_json()
        results = xml_processor.search_parcels(
            survey,
            owner=data.get("owner", ""),
            upc=data.get("upc", ""),
            book=data.get("book", ""),
            page=data.get("page", ""),
            cabinet_ref=data.get("cabinet_ref", ""),
            trs=data.get("trs", ""),
            subdivision=data.get("subdivision", ""),
            operator=data.get("operator", "contains"),
            limit=int(data.get("limit", 50)),
        )
        return jsonify({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/xml/parcel/<upc>")
def api_xml_parcel(upc):
    """Get full parcel detail including polygon coordinates for a given UPC."""
    try:
        survey = get_survey_data_path()

        # Search index for the record
        results = xml_processor.search_parcels(survey, upc=upc, limit=1)
        if not results:
            return jsonify({"success": False, "error": "Parcel not found"})

        parcel = results[0]

        # Extract full polygon coordinates (re-scans KML — slower but accurate)
        polygon = xml_processor.extract_parcel_polygon(survey, upc)
        parcel["polygon"] = polygon

        return jsonify({"success": True, "parcel": parcel})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/xml/cross-reference", methods=["POST"])
def api_xml_cross_reference():
    """
    Given a deed detail dict, find matching parcels via name, book/page, cabinet refs.
    Body: { detail: {...deed detail...} }
    """
    try:
        survey = get_survey_data_path()
        data   = request.get_json()
        detail = data.get("detail", {})
        if not detail:
            return jsonify({"success": False, "error": "No deed detail provided"})

        results = xml_processor.cross_reference_deed(survey, detail)
        return jsonify({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/parcel-search", methods=["POST"])
def api_parcel_search():
    """
    Search KML parcel index by owner name or UPC for the Step 1 property picker.

    Body: { query: str, operator: str ("contains"|"begins"|"exact"), limit: int }
    Returns: { success, results: [{owner, upc, book, page, plat, cab_refs, centroid, polygon}], count }
    """
    try:
        data     = request.get_json() or {}
        query    = data.get("query", "").strip()
        operator = data.get("operator", "contains")
        limit    = int(data.get("limit", 30))

        if not query or len(query) < 2:
            return jsonify({"success": True, "results": [], "count": 0,
                            "hint": "Enter at least 2 characters to search"})

        survey = get_survey_data_path()
        idx = xml_processor._cached_index
        if idx is None:
            idx = xml_processor.load_index(survey)
        if not idx:
            return jsonify({"success": False, "error": "Parcel index not built yet. Use the KML Index button to build it.",
                            "results": [], "count": 0})

        # Search by owner name
        results = xml_processor.search_parcels_in_index(
            idx, owner=query, operator=operator, limit=limit
        )

        # If no name hits and query looks like a UPC (all digits), try UPC search
        if not results and re.match(r'^\d+$', query):
            results = xml_processor.search_parcels_in_index(
                idx, upc=query, limit=limit
            )

        # Return minimal fields (strip heavy polygon data for list view)
        out = []
        for p in results:
            out.append({
                "owner":    p.get("owner", ""),
                "upc":      p.get("upc", ""),
                "book":     p.get("book", ""),
                "page":     p.get("page", ""),
                "plat":     p.get("plat", ""),
                "cab_refs": p.get("cab_refs", []),
                "centroid": p.get("centroid"),
            })

        return jsonify({"success": True, "results": out, "count": len(out)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "results": [], "count": 0})


@bp.route("/api/xml/map-geojson", methods=["POST"])
def api_xml_map_geojson():
    """
    Return a GeoJSON FeatureCollection for Leaflet rendering.
    Body: { highlight_upcs: [str], max_features: int }
    Response is gzip-compressed when the client accepts it (browsers always do).

    Auto-builds the index if KML/KMZ files exist but the index hasn't been created yet.
    """
    try:
        data           = request.get_json() or {}
        highlight_upcs = data.get("highlight_upcs", [])
        max_features   = int(data.get("max_features", 100000))
        source_filter  = data.get("source_filter", "")

        survey  = get_survey_data_path()

        # Auto-build index if it doesn't exist but KML/KMZ files are available
        idx = xml_processor.load_index(survey)
        if not idx:
            xml_files = xml_processor.discover_xml_files(survey)
            if xml_files:
                print(f"[map-geojson] No index found — auto-building from {len(xml_files)} XML/KML/KMZ files...", flush=True)
                build_result = xml_processor.build_index(survey)
                print(f"[map-geojson] Auto-build complete: {build_result.get('total', 0)} parcels in {build_result.get('elapsed_sec', '?')}s", flush=True)
            else:
                print("[map-geojson] No index and no KML/KMZ files found in XML folder", flush=True)

        geojson = xml_processor.get_map_geojson(
            survey, highlight_upcs, max_features, source_filter=source_filter
        )
        total   = len(geojson.get("features", []))
        sources = geojson.pop("sources", [])  # separate from FeatureCollection

        payload = json.dumps({"success": True, "geojson": geojson, "total": total, "sources": sources},
                             separators=(",", ":"))  # compact JSON — no spaces

        # Compress if client supports it (all modern browsers do)
        accept_enc = request.headers.get("Accept-Encoding", "")
        if "gzip" in accept_enc:
            compressed = gzip.compress(payload.encode("utf-8"), compresslevel=6)
            return Response(
                compressed,
                status=200,
                mimetype="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length":   str(len(compressed)),
                    "Vary":             "Accept-Encoding",
                },
            )
        return Response(payload, status=200, mimetype="application/json")

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "total": 0})


@bp.route("/api/property-address", methods=["POST"])
@require_auth
def api_property_address():
    """Look up property address info.

    Dual strategy:
      1. If UPC provided → query NM ArcGIS parcel database (official situs address)
      2. Fallback → Nominatim reverse geocode from lat/lon centroid

    Body: { "upc": str, "lat": float, "lon": float }
    Returns: { success, short_address, source, ... }
    """
    try:
        data = request.get_json() or {}
        upc  = (data.get("upc") or "").strip()
        lat  = float(data.get("lat", 0))
        lon  = float(data.get("lon", 0))

        # Strategy 1: ArcGIS by UPC (preferred — official govt data, no rate limit)
        arcgis_result = None
        if upc:
            arcgis_result = arcgis_lookup_upc(upc)
            if arcgis_result and arcgis_result.get("success") and arcgis_result.get("has_street_address"):
                # ArcGIS has a real situs address — use it directly
                return jsonify(arcgis_result)

        # Strategy 2: Nominatim reverse geocode from coordinates (fallback)
        # Also triggers when ArcGIS returned data but no street address
        if lat != 0 or lon != 0:
            result = nominatim_reverse(lat, lon)
            # Merge supplemental ArcGIS data (owner, legal desc) into Nominatim result
            if arcgis_result and arcgis_result.get("success"):
                for key in ("owner_official", "legal_description", "land_area", "upc"):
                    if arcgis_result.get(key) and not result.get(key):
                        result[key] = arcgis_result[key]
            return jsonify(result)

        # No coordinates either — return ArcGIS result as-is (even without street addr)
        if arcgis_result and arcgis_result.get("success"):
            return jsonify(arcgis_result)

        return jsonify({"success": False, "error": "No UPC or coordinates provided"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/batch-property-address", methods=["POST"])
@require_auth
def api_batch_property_address():
    """Look up addresses for up to 10 parcels.

    Body: { "parcels": [ { "upc": str, "lat": float, "lon": float }, ... ] }
    Returns: { success, results: [ { short_address, source, ... }, ... ] }
    """
    try:
        data    = request.get_json() or {}
        parcels = data.get("parcels", [])[:10]

        if not parcels:
            return jsonify({"success": False, "error": "No parcels provided"})

        results = []
        for p in parcels:
            upc = (p.get("upc") or "").strip()
            lat = float(p.get("lat", 0))
            lon = float(p.get("lon", 0))

            result = None
            if upc:
                result = arcgis_lookup_upc(upc)
            if not result or not result.get("success"):
                if lat != 0 or lon != 0:
                    result = nominatim_reverse(lat, lon)
            if not result:
                result = {"success": False, "error": "No UPC or coordinates"}
            results.append(result)

        return jsonify({"success": True, "results": results})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/arcgis-adjoiners", methods=["POST"])
@require_auth
@require_pro
def api_arcgis_adjoiners():
    """Find adjacent parcels using ArcGIS spatial queries.

    Strategy:
      1. If UPC provided → fetch parcel geometry from ArcGIS
      2. If geometry provided directly (from KML index) → use that
      3. Query ArcGIS for all parcels touching that geometry
      4. Filter out the client's own parcel

    Body: { "upc": str, "geometry": { "rings": [...] } (optional),
            "client_name": str (optional, for filtering) }
    Returns: { success, adjoiners: [...], count, source }
    """
    try:
        data = request.get_json() or {}
        upc = (data.get("upc") or "").strip()
        geometry = data.get("geometry")  # Optional pre-supplied geometry
        client_name = (data.get("client_name") or "").strip().lower()

        # Step 1: Get geometry
        if not geometry and upc:
            print(f"[arcgis-adj] Fetching geometry for UPC {upc}...", flush=True)
            geometry = arcgis_get_parcel_geometry(upc)

        if not geometry:
            # Try from local KML index as fallback
            survey = get_survey_data_path()
            if upc:
                polygon = xml_processor.extract_parcel_polygon(survey, upc)
                if polygon and polygon.get("coordinates"):
                    # Convert KML coords [[lng,lat], ...] to ArcGIS rings format
                    coords = polygon["coordinates"]
                    geometry = {
                        "rings": [[[c[0], c[1]] for c in coords]],
                        "spatialReference": {"wkid": 4326}
                    }
                    print(f"[arcgis-adj] Using KML geometry for UPC {upc}", flush=True)

        if not geometry:
            return jsonify({
                "success": False,
                "error": "Could not find parcel geometry. Try selecting the parcel on the map first.",
                "adjoiners": [], "count": 0,
            })

        # Step 2: Spatial query
        print("[arcgis-adj] Running spatial query for touching parcels...", flush=True)
        raw = arcgis_find_touching_parcels(geometry)

        # Step 3: Filter out the client's own parcel
        adjoiners = []
        seen_upcs = set()
        for adj in raw:
            # Skip client's own parcel
            if upc and adj["upc"] == upc:
                continue
            if client_name and adj["owner"].lower() == client_name:
                continue
            # Deduplicate by UPC
            if adj["upc"] in seen_upcs:
                continue
            seen_upcs.add(adj["upc"])
            adjoiners.append(adj)

        print(f"[arcgis-adj] Found {len(adjoiners)} adjacent parcels", flush=True)

        return jsonify({
            "success":   True,
            "adjoiners": adjoiners,
            "count":     len(adjoiners),
            "source":    "arcgis_spatial",
            "client_upc": upc,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "adjoiners": [], "count": 0})
