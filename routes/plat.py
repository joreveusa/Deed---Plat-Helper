"""
routes/plat.py - Plat discovery, adjoiner discovery, save, and preview Blueprint.

Handles: find-adjoiners, find-plat (instant, KML, local, online), save-plat, preview-pdf.
"""

import json
import os
import re
import shutil
import traceback
from collections import Counter
from pathlib import Path

import requests as req_lib
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
from flask import Blueprint, request, jsonify, make_response

from helpers.adjoiner import parse_adjoiner_names as _parse_adjoiner_names_impl
from helpers.cabinet import (
    CABINET_FOLDERS, parse_cabinet_refs,
    extract_plat_name_tokens as _extract_plat_name_tokens,
    search_local_cabinet as _search_local_cabinet_impl,
)
from helpers.pdf_extract import (
    extract_pdf_text as _extract_pdf_text_impl,
    ocr_plat_file as _ocr_plat_file_impl,
    _ocr_cache_path,
)
from helpers.subscription import require_auth, require_pro
from services.drive import get_survey_data_path, get_cabinet_path, detect_survey_drive
from services.portal import get_portal_url, get_session, scrape_form_data
from services.session import job_base_path, load_research, save_research
import xml_processor

plat_bp = Blueprint("plat", __name__)


# -- Thin wrappers --

def _extract_pdf_text(pdf_path: str) -> tuple[str, str]:
    return _extract_pdf_text_impl(pdf_path)

def parse_adjoiner_names(detail: dict) -> list[dict]:
    return _parse_adjoiner_names_impl(detail)

def ocr_plat_file(pdf_path: str) -> list[str]:
    return _ocr_plat_file_impl(pdf_path)

def search_local_cabinet(cabinet: str, doc_num: str,
                         grantor: str = "", grantee: str = "") -> list[dict]:
    return _search_local_cabinet_impl(
        cabinet, doc_num, cabinet_path=get_cabinet_path(),
        grantor=grantor, grantee=grantee
    )


def find_adjoiners_online(location: str, grantor: str, sess: req_lib.Session | None = None) -> list[dict]:
    """
    Search online records for deeds in the same location book/page range.
    Location format: M568-482 → book 568, search 5 pages around 482.
    Returns list of {name, doc_no, instrument_type, location, source}.
    """
    results = []
    if not location:
        return results

    # Parse book & page: handles "M568-482", "568-482", "L1053-BC" etc.
    m = re.match(r'^[A-Za-z]?(\d+)-', location.strip())
    if not m:
        return results
    book = m.group(1)

    # Search online by location prefix (book number)
    try:
        search_url = f"{get_portal_url()}/scripts/hfweb.asp?Application=FNM&Database=TP"
        _s = sess or get_session()
        resp = _s.get(search_url, timeout=12)
        if "FIELD14" not in resp.text and "CROSSNAMEFIELD" not in resp.text:
            return results  # not logged in


        soup = BeautifulSoup(resp.text, "html.parser")
        fd = scrape_form_data(soup)
        if not fd:
            return results

        # Search by location book prefix
        # FIELD14 is typically the "Location" search field on this site
        fd["FIELD14"]    = book + "-"
        fd["FIELD14TYPE"] = "begin"

        post = _s.post(f"{get_portal_url()}/scripts/hflook.asp", data=fd, timeout=20)
        soup2 = BeautifulSoup(post.text, "html.parser")

        # Collect unique grantors/grantees from nearby records (skip our own grantor)
        own_last = grantor.split(",")[0].strip().upper()
        seen_names = set()
        if own_last:
            seen_names.add(own_last)

        for row in soup2.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 10:
                continue
            doc_link = cells[1].find("a") if len(cells) > 1 else None
            if not doc_link:
                continue
            itype   = cells[5].text.strip()  if len(cells) > 5  else ""
            # Only deeds / conveyances
            if not re.search(r'deed|warranty|quitclaim|convey|grant', itype, re.I):
                continue
            g_name  = cells[9].text.strip()   if len(cells) > 9  else ""
            ee_name = cells[10].text.strip()  if len(cells) > 10 else ""
            for name in [g_name, ee_name]:
                if not name:
                    continue
                last = name.split(",")[0].strip().upper()
                if last in seen_names or len(last) < 2:
                    continue
                seen_names.add(last)
                display = name.title()
                results.append({
                    "name":            display,
                    "doc_no":          doc_link.text.strip(),
                    "instrument_type": itype,
                    "location":        cells[2].text.strip() if len(cells) > 2 else "",
                    "source":          "online_range",
                })
                if len(results) >= 8:
                    break
            if len(results) >= 8:
                break
    except Exception:
        pass  # online search is best-effort

    return results


def _extract_target_cabinets(detail: dict, kml_matches: list = None) -> tuple[list[str], str]:
    """
    Determine which cabinet letter(s) to search for a plat, using:

    Priority 1 — KML matches already returned by /find-plat-kml.
                  Extract cab_refs from those hits (most reliable since
                  the KML PLAT field is tied to the parcel, not the owner name).

    Priority 2 — Deed Location (book/page) → query KML index directly.
                  The recording book/page is a stable reference that doesn't
                  change with ownership, unlike owner names on deeds/KML.

    Priority 3 — Cabinet refs embedded in the deed text itself (e.g. "Cabinet C-191A").

    Fallback    — Search all cabinets if none of the above yield a cabinet letter.

    Returns (cabinet_letters, reason_string) so callers can log / display why.

    NOTE: Name-based KML targeting is intentionally skipped — deeds and KML may
    reflect different eras of ownership, making name matches unreliable for targeting.
    """
    all_cabs = list(CABINET_FOLDERS.keys())

    # ── Priority 1: cab_refs from KML matches already resolved ─────────────────
    if kml_matches:
        letters = set()
        for hit in kml_matches:
            for cr in hit.get("cab_refs", []):
                letter = cr.split("-")[0].upper()
                if letter in CABINET_FOLDERS:
                    letters.add(letter)
        if letters:
            return sorted(letters), f"KML parcel cab_refs → Cabinet(s) {', '.join(sorted(letters))}"

    # ── Priority 2: deed Location book/page → KML index lookup ─────────────────
    location = detail.get("Location", "")
    if location:
        m = re.match(r'^[A-Za-z]?(\d+)-(\d+)', location.strip())
        if m:
            book_num = m.group(1)
            page_num = m.group(2)
            idx = xml_processor._cached_index
            if idx is None:
                try:
                    survey = get_survey_data_path()
                    idx = xml_processor.load_index(survey)
                except Exception:
                    idx = None
            if idx:
                hits = xml_processor.search_parcels_in_index(idx, book=book_num, page=page_num, limit=10)
                letters = set()
                for h in hits:
                    for cr in h.get("cab_refs", []):
                        letter = cr.split("-")[0].upper()
                        if letter in CABINET_FOLDERS:
                            letters.add(letter)
                if letters:
                    return sorted(letters), f"Deed location {book_num}-{page_num} → KML → Cabinet(s) {', '.join(sorted(letters))}"

    # ── Priority 3: cabinet refs directly in deed text ──────────────────────────
    deed_refs = parse_cabinet_refs(detail)
    if deed_refs:
        letters = list(dict.fromkeys(r["cabinet"] for r in deed_refs if r["cabinet"] in CABINET_FOLDERS))
        if letters:
            return letters, f"Deed text ref(s) → Cabinet(s) {', '.join(letters)}"

    # ── Fallback: search all ─────────────────────────────────────────────────────
    return all_cabs, "No cabinet target found — searching all cabinets"


@plat_bp.route("/api/find-adjoiners", methods=["POST"])
@require_auth
@require_pro
def api_find_adjoiners():
    """
    Automatically discover adjoiner names from:
      1. OCR the local plat file referenced in the deed (primary — plat maps show adjoiners)
      2. Online records search for nearby location/book entries (fallback)
    """
    try:
        data   = request.get_json()
        detail = data.get("detail", {})
        # Guard: detail may arrive as a JSON string or some non-dict — normalize to dict
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}
        if not isinstance(detail, dict):
            detail = {}
        # Extract key fields from detail when not sent as top-level keys
        grantor   = data.get("grantor", "") or detail.get("Grantor", "") or detail.get("grantor", "")
        location  = data.get("location", "") or detail.get("Location", "") or detail.get("location", "")
        doc_no    = data.get("doc_no", "")   or detail.get("doc_no", "")
        deed_path = data.get("deed_path", "")  # saved deed PDF path from session
        client_upc = data.get("client_upc", "").strip()  # from map picker selection

        results  = []
        seen_names: set = set()
        plat_file_used = None
        raw_ocr_text = ""

        # ── Strategy 1: scan deed legal-description text for 'Lands of' names ──
        legal_adj = parse_adjoiner_names(detail)
        for item in legal_adj:
            key = item["name"].lower()
            if key not in seen_names:
                seen_names.add(key)
                results.append(item)

        # ── Strategy 2: OCR the local plat referenced by this deed ─────────────
        #   Use ocr_plat_file() which has a disk cache — avoids re-rendering pages
        #   on repeat calls. The raw text is stored in the cache file alongside.
        cab_refs = parse_cabinet_refs(detail)
        print(f"[adjoiners] cabinet refs found: {cab_refs}", flush=True)
        for ref in cab_refs:
            hits = search_local_cabinet(ref["cabinet"], ref["doc"])
            if hits:
                plat_path = hits[0]["path"]
                plat_file_used = hits[0]["file"]
                # ocr_plat_file() handles caching — only renders pages once
                ocr_names = ocr_plat_file(plat_path)
                # Capture raw OCR text from cache file for the response
                try:
                    cache = _ocr_cache_path(plat_path)
                    if cache.exists():
                        cached = json.loads(cache.read_text(encoding="utf-8"))
                        raw_ocr_text = " ".join(cached.get("names", []))
                except Exception:
                    pass
                for name in ocr_names:
                    key = name.lower()
                    if key not in seen_names:
                        seen_names.add(key)
                        results.append({
                            "name":   name,
                            "raw":    "",
                            "field":  plat_file_used,
                            "source": "plat_ocr",
                            "plat":   plat_file_used,
                        })
                break  # only OCR the first matched plat

        # ── Strategy 2b: extract text from saved deed PDF (text layer first, OCR fallback) ──
        if deed_path and os.path.isfile(deed_path):
            print(f"[adjoiners] reading deed text from: {deed_path}", flush=True)
            try:
                deed_text, deed_src = _extract_pdf_text(deed_path)
                print(f"[adjoiners] deed text source={deed_src} chars={len(deed_text.strip())}", flush=True)
                print(f"[adjoiners] deed text sample: {deed_text[:300]!r}", flush=True)
                # Parse for adjoiner names
                deed_detail_fake = {"Legal": deed_text}
                for item in parse_adjoiner_names(deed_detail_fake):
                    key = item["name"].lower()
                    if key not in seen_names:
                        seen_names.add(key)
                        item["source"] = "deed_text"
                        results.append(item)
            except Exception as e2:
                print(f"[adjoiners] deed text error: {e2}", flush=True)


        # ── Strategy 3: KML/XML parcel index — neighboring parcels ─────────────
        #   Uses geometry-based adjacency (polygon edge proximity) as primary,
        #   with UPC-prefix and centroid-distance fallbacks.
        MAX_KML_PER_SUBSTRATEGY = 12  # cap each sub-strategy
        kml_upc_count = 0
        kml_geom_count = 0
        kml_prox_count = 0
        # Filter out non-person owner names (roads, easements, government, numeric-only)
        _SKIP_OWNER_PATS = re.compile(
            r'^(?:\d+|upc\s*\d|road|street|highway|hwy|county|state|'
            r'new\s*mexico|nm\s*dot|pueblo|blm|usfs|forest\s*service|'
            r'right.?of.?way|easement|vacant|unknown|none)$',
            re.I
        )
        def _is_valid_owner(name: str) -> bool:
            """Return False for garbage / non-person owner entries."""
            if not name or len(name.strip()) < 3:
                return False
            clean = name.strip()
            # All digits or mostly digits = bad
            if re.fullmatch(r'[\d\s\-]+', clean):
                return False
            if _SKIP_OWNER_PATS.search(clean):
                return False
            return True
        try:
            survey_path = get_survey_data_path()
            kml_idx = xml_processor.load_index(survey_path)
            if kml_idx:
                parcels = kml_idx.get("parcels", [])

                # Step A: find the client parcel — prefer client_upc (from map
                #   picker), fall back to grantor name + book/page heuristic.
                client_parcel = None

                # Priority 1: exact UPC match from map selection
                if client_upc:
                    for p in parcels:
                        if p.get("upc", "") == client_upc:
                            client_parcel = p
                            print(f"[adjoiners][kml] client parcel (UPC match): {p.get('owner')} UPC={client_upc} centroid={p.get('centroid')}", flush=True)
                            break

                # Priority 2: grantor name + book/page fallback
                if not client_parcel:
                    grantor_last  = grantor.split(",")[0].strip().upper() if grantor else ""
                    loc_m = re.match(r'^[A-Za-z]?(\d+)-(\d+)', location.strip()) if location else None
                    book_num = loc_m.group(1) if loc_m else ""
                    page_num = loc_m.group(2) if loc_m else ""

                    for p in parcels:
                        p_owner = p.get("owner", "").upper()
                        owner_match = grantor_last and grantor_last in p_owner
                        book_match  = book_num and p.get("book", "") == book_num and p.get("page", "") == page_num
                        if owner_match or book_match:
                            client_parcel = p
                            print(f"[adjoiners][kml] client parcel (name/book fallback): {p.get('owner')} UPC={p.get('upc')} centroid={p.get('centroid')}", flush=True)
                            break

                if client_parcel:
                    client_upc     = client_parcel.get("upc", "")
                    client_centroid = client_parcel.get("centroid")  # [lng, lat]

                    # Step B: Geometry-based adjacency (most accurate)
                    #   Uses polygon edge proximity — finds parcels that actually share
                    #   a boundary or are within ~33m of the client parcel's edges.
                    if client_upc:
                        try:
                            adj_parcels = xml_processor.find_adjacent_parcels(
                                survey_path, client_upc,
                                max_results=MAX_KML_PER_SUBSTRATEGY,
                                edge_threshold_deg=0.0003  # ~33m
                            )
                            for p in adj_parcels:
                                if kml_geom_count >= MAX_KML_PER_SUBSTRATEGY:
                                    break
                                name = p.get("owner", "").title()
                                if not _is_valid_owner(name):
                                    continue
                                key = name.lower()
                                if key not in seen_names:
                                    seen_names.add(key)
                                    adj_type = p.get("_adjacency_type", "edge")
                                    results.append({
                                        "name":   name,
                                        "raw":    f"edge dist: {p.get('_adjacency_dist', 0):.5f}°",
                                        "field":  f"KML {adj_type} adjacency",
                                        "source": "kml_geometry",
                                        "upc":    p.get("upc", ""),
                                        "plat":   p.get("plat", ""),
                                    })
                                    kml_geom_count += 1
                            print(f"[adjoiners][kml] geometry adjacency: {kml_geom_count} found", flush=True)
                        except Exception as geom_err:
                            print(f"[adjoiners][kml] geometry adjacency error: {geom_err}", flush=True)

                    # Step C: UPC-prefix neighbors (same parcel group, adjacent numbers)
                    if client_upc:
                        upc_prefix = client_upc[:10]
                        try:
                            upc_num = int(client_upc)
                            for p in parcels:
                                if kml_upc_count >= MAX_KML_PER_SUBSTRATEGY:
                                    break
                                p_upc = p.get("upc", "")
                                if not p_upc or p_upc == client_upc:
                                    continue
                                if not p_upc.startswith(upc_prefix):
                                    continue
                                try:
                                    diff = abs(int(p_upc) - upc_num)
                                    if diff <= 8:   # within 8 UPC steps = nearby lots
                                        name = p.get("owner", "").title()
                                        if not _is_valid_owner(name):
                                            continue
                                        key  = name.lower()
                                        if key not in seen_names:
                                            seen_names.add(key)
                                            results.append({
                                                "name":   name,
                                                "raw":    p_upc,
                                                "field":  "KML UPC neighbor",
                                                "source": "kml_upc",
                                                "upc":    p_upc,
                                                "plat":   p.get("plat", ""),
                                            })
                                            kml_upc_count += 1
                                except ValueError:
                                    pass
                        except ValueError:
                            pass

                    # Step D: centroid proximity — fallback for parcels without polygons
                    #   Widened from 0.0008° (~89m) → 0.002° (~222m) to catch
                    #   irregularly shaped parcels whose centroids are far apart.
                    if client_centroid:
                        clng, clat = client_centroid
                        RADIUS_DEG = 0.002   # ~222m — wider to catch more neighbors
                        BOX = RADIUS_DEG * 1.5
                        for p in parcels:
                            if kml_prox_count >= MAX_KML_PER_SUBSTRATEGY:
                                break
                            pc = p.get("centroid")
                            if not pc or p.get("upc") == client_upc:
                                continue
                            if abs(pc[0] - clng) > BOX or abs(pc[1] - clat) > BOX:
                                continue
                            dlng = abs(pc[0] - clng)
                            dlat = abs(pc[1] - clat)
                            if dlng < RADIUS_DEG and dlat < RADIUS_DEG:
                                name = p.get("owner", "").title()
                                if not _is_valid_owner(name):
                                    continue
                                key  = name.lower()
                                if key not in seen_names:
                                    seen_names.add(key)
                                    results.append({
                                        "name":   name,
                                        "raw":    f"{pc[1]:.5f},{pc[0]:.5f}",
                                        "field":  "KML proximity",
                                        "source": "kml_proximity",
                                        "upc":    p.get("upc", ""),
                                        "plat":   p.get("plat", ""),
                                    })
                                    kml_prox_count += 1
                else:
                    print(f"[adjoiners][kml] no client parcel found for grantor={grantor_last!r} book={book_num!r}", flush=True)
            else:
                print("[adjoiners][kml] index not loaded (build it first)", flush=True)
        except Exception as kml_err:
            print(f"[adjoiners][kml] error: {kml_err}", flush=True)


        # ── Strategy 4: online location-range search (supplement) ───────────────
        #   Only add a few — these are same-book deeds, NOT geographic neighbors.
        #   Cap at 8 and only run if we have fewer than 10 results so far.
        MAX_ONLINE = 8
        online_count = 0
        if len(results) < 10:
            print(f"[adjoiners] online search: location={location!r} grantor={grantor!r}", flush=True)
            online = find_adjoiners_online(location, grantor, sess=get_session())
            for om in online:
                if online_count >= MAX_ONLINE:
                    break
                if om["name"].lower() not in seen_names:
                    results.append(om)
                    seen_names.add(om["name"].lower())
                    online_count += 1
        else:
            print(f"[adjoiners] skipping online search — already have {len(results)} results", flush=True)

        # ── Hard cap: keep best results, drop lowest-priority sources ──────────
        #   Priority: legal_desc > deed_text > plat_ocr > kml_upc > kml_proximity > online_range
        MAX_ADJOINERS = 15
        if len(results) > MAX_ADJOINERS:
            SOURCE_PRIORITY = {
                "legal_desc": 0, "deed_text": 1, "plat_ocr": 2,
                "kml_geometry": 3, "kml_upc": 4, "kml_proximity": 5, "online_range": 6,
            }
            results.sort(key=lambda r: SOURCE_PRIORITY.get(r.get("source", ""), 9))
            results = results[:MAX_ADJOINERS]

        # Log breakdown by source for debugging
        src_counts = Counter(r.get("source", "?") for r in results)
        print(f"[adjoiners] total: {len(results)}  breakdown: {dict(src_counts)}", flush=True)

        return jsonify({
            "success":      True,
            "doc_no":       doc_no,
            "adjoiners":    results,
            "count":        len(results),
            "plat_used":    plat_file_used,
            "ocr_raw_text": raw_ocr_text[:8000] if raw_ocr_text else "",  # cap size
        })
    except Exception as e:

        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@plat_bp.route("/api/find-plat", methods=["POST"])
@require_auth
def api_find_plat():
    """
    INSTANT: Returns parsed cabinet refs from deed text — zero I/O.
    All slow I/O work is split into separate async endpoints:
      /api/find-plat-kml   — KML parcel index lookup
      /api/find-plat-local — local cabinet folder scan (name-based primary)
      /api/find-plat-online— online survey record search
    """
    try:
        data   = request.get_json() or {}
        detail = data.get("detail", {})
        cab_refs_list = parse_cabinet_refs(detail)
        return jsonify({
            "success":      True,
            "cabinet_refs": cab_refs_list,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@plat_bp.route("/api/find-plat-kml", methods=["POST"])
@require_auth
def api_find_plat_kml():
    """
    KML parcel index cross-reference.
    Requires the index to be built first (via /api/xml/build-index).

    When deed detail is empty but client_name is provided, performs a
    name-only owner search so Step 3 can show KML results without a deed.
    """
    try:
        data        = request.get_json() or {}
        detail      = data.get("detail", {})
        client_name = data.get("client_name", "").strip()
        client_upc  = data.get("client_upc", "").strip()
        kml_matches = []
        idx = xml_processor._cached_index
        if idx is None:
            survey = get_survey_data_path()
            idx    = xml_processor.load_index(survey)
        if idx:
            survey = get_survey_data_path()
            # If we have a deed, use the full cross-reference (grantor/grantee/book/page/cab)
            if detail and (detail.get("Grantor") or detail.get("Grantee") or detail.get("Location")):
                kml_results = xml_processor.cross_reference_deed(survey, detail, client_upc=client_upc)
            elif client_name:
                # No deed yet — search by client name (owner contains last name)
                last_name = client_name.split(",")[0].strip().upper()
                if len(last_name) >= 2:
                    raw = xml_processor.search_parcels_in_index(idx, owner=last_name, operator="contains", limit=15)
                    for p in raw:
                        p["_match_reason"] = f"Client name: {client_name}"
                    # If we have a client_upc, boost that parcel to the front
                    if client_upc:
                        upc_hit = xml_processor.search_parcels_in_index(idx, upc=client_upc, limit=1)
                        if upc_hit:
                            upc_hit[0]["_match_reason"] = "Map selection"
                            # Remove if already in raw results, then prepend
                            raw = [p for p in raw if p.get("upc") != client_upc]
                            raw = upc_hit + raw
                    kml_results = raw
                else:
                    kml_results = []
            else:
                kml_results = []
            for p in kml_results:
                kml_matches.append({
                    "owner":        p.get("owner", ""),
                    "upc":          p.get("upc", ""),
                    "plat":         p.get("plat", ""),
                    "book":         p.get("book", ""),
                    "page":         p.get("page", ""),
                    "cab_refs":     p.get("cab_refs", []),
                    "cab_refs_str": ", ".join(p.get("cab_refs", [])) or p.get("plat", ""),
                    "centroid":     p.get("centroid"),
                    "match_reason": p.get("_match_reason", ""),
                    "local_files":  [],
                    "source":       "kml",
                })
        return jsonify({"success": True, "kml_matches": kml_matches})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "kml_matches": []})


@plat_bp.route("/api/find-plat-local", methods=["POST"])
@require_auth
def api_find_plat_local():
    """
    Targeted cabinet scan — locates plat PDFs by finding the correct cabinet
    first via KML parcel data or deed book/page, then scanning only there.

    Search priority:
      1. KML kml_matches.cab_refs  → target specific cabinet letter(s)
      2. Deed Location book/page   → KML index lookup → cabinet letter(s)
      3. Deed text cabinet refs    → cabinet letter(s) from deed itself
      4. Fallback                  → all cabinets (same as before)

    Within targeted cabinet(s), files are matched by:
      a. Exact cab_ref string  (e.g. "C-191A" in filename)   — most precise
      b. Client name token     (job subject name in filename) — owner-aware
      c. Deed grantor/grantee name in filename               — secondary
    """
    try:
        data           = request.get_json() or {}
        detail         = data.get("detail", {})
        grantor        = data.get("grantor", "") or detail.get("Grantor", "")
        grantee        = data.get("grantee", "") or detail.get("Grantee", "")
        client_name    = data.get("client_name", "")
        prior_owners   = data.get("prior_owners", [])   # grantor names from deed hint
        kml_matches    = data.get("kml_matches", [])   # may be populated if frontend chains requests
        forced_cabinets = [c.upper() for c in data.get("forced_cabinets", []) if c]

        # ── Determine which cabinet(s) to search ────────────────────────────────
        if forced_cabinets:
            # User explicitly selected a cabinet from the dropdown — override everything
            target_cabs    = forced_cabinets
            targeting_reason = f"Manual selection: Cabinet{'s' if len(forced_cabinets) > 1 else ''} {', '.join(forced_cabinets)}"
        else:
            target_cabs, targeting_reason = _extract_target_cabinets(detail, kml_matches)

        # Detect whether we have real deed detail (Location/book-page) or just a name.
        # Name-only mode = adjoiner plat search from Step 5 where no deed has been
        # looked up yet. In this mode we need to be MORE restrictive with matches.
        has_deed_detail = bool(
            detail.get("Location") or detail.get("Reference") or
            detail.get("_cab_refs")
        )

        print(f"[local] {targeting_reason}  (deed_detail={'yes' if has_deed_detail else 'NO — name-only mode'})", flush=True)


        local_hits       = []
        seen_local_paths = set()

        # ── Build cab_ref → doc map from KML hits for precise filename matching ─
        # e.g. kml says "C-191A" → we can look for "191A" in Cabinet C filenames
        kml_cab_refs = {}   # {"C": ["191A", "84"], ...}
        for hit in kml_matches:
            for cr in hit.get("cab_refs", []):
                parts = cr.split("-", 1)
                if len(parts) == 2:
                    letter, doc = parts[0].upper(), parts[1]
                    kml_cab_refs.setdefault(letter, [])
                    if doc not in kml_cab_refs[letter]:
                        kml_cab_refs[letter].append(doc)

        # ── Also pull cab_refs from deed text for the targeted cabinets ─────────
        deed_refs = parse_cabinet_refs(detail)
        deed_cab_refs = {}  # {"C": ["191A"], ...}
        for r in deed_refs:
            deed_cab_refs.setdefault(r["cabinet"], [])
            if r["doc"] not in deed_cab_refs[r["cabinet"]]:
                deed_cab_refs[r["cabinet"]].append(r["doc"])

        # ── Build client name tokens for cabinet filename matching ───────────────
        # The client IS the current property owner, so their name appears on cabinet
        # files (e.g. "Rael Adela.pdf" or "Adela Rael.pdf").
        # We build tokens from client_name AND the deed grantee (same person).
        #
        #  client_name format examples: "Rael, Adela"  "ADELA RAEL"  "rael adela"
        #  Cabinet file name examples:  "Adela Rael.pdf"  "Rael Adela.pdf"
        #    → We need both "Rael" and "Adela" to reliably find the file.
        client_tokens: list[str] = []
        for raw_name in [client_name, grantee]:
            if not raw_name or not raw_name.strip():
                continue
            raw = raw_name.strip()
            # Add the full string (handles "Adela Rael" matching "Adela Rael.pdf")
            if len(raw) >= 3 and raw.lower() not in [t.lower() for t in client_tokens]:
                client_tokens.append(raw)
            # Swap "Last, First" → "First Last" (handles "Rael, Adela" → "Adela Rael")
            if ',' in raw:
                parts = [p.strip() for p in raw.split(',', 1) if p.strip()]
                if len(parts) == 2:
                    swapped = f"{parts[1]} {parts[0]}"
                    if swapped.lower() not in [t.lower() for t in client_tokens]:
                        client_tokens.append(swapped)
                # Also add just the last name (before comma)
                last = parts[0]
                if len(last) >= 3 and last.lower() not in [t.lower() for t in client_tokens]:
                    client_tokens.append(last)
            # Add individual words >= 4 chars
            for w in re.split(r'[\s,]+', raw):
                if len(w) >= 4 and w.lower() not in [t.lower() for t in client_tokens]:
                    client_tokens.append(w)

        # ── In name-only mode (no deed detail), restrict tokens to multi-word ─────
        # Single words like "Rael" (4 chars) would match "Israel", "Rafael", etc.
        # and cause hundreds of false positives across all cabinets.
        # Require at least "first last" (multi-word) or "last, first" (comma) tokens.
        if not has_deed_detail and client_tokens:
            multi_word_tokens = [t for t in client_tokens if ' ' in t or ',' in t]
            if multi_word_tokens:
                client_tokens = multi_word_tokens
                print(f"[local] name-only mode: restricted to multi-word tokens: {client_tokens}", flush=True)
            else:
                # If we only have single-word tokens (e.g. just a last name), keep them
                # but log a warning — results may be broad.
                print(f"[local] name-only mode: only single-word tokens available: {client_tokens}", flush=True)

        print(f"[local] client_tokens for cabinet search: {client_tokens}", flush=True)

        # ── Scan each target cabinet ─────────────────────────────────────────────
        for cab_letter in target_cabs:
            if not CABINET_FOLDERS.get(cab_letter):
                continue

            # a) Exact cab_ref match from KML or deed text
            #    NOTE: Most cabinet files do NOT embed the cabinet ref in their
            #    filename (e.g. files are named "Rael Adela.pdf", not "C-191A.pdf").
            #    kml_cab_refs are used exclusively for cabinet *targeting* above;
            #    name-based strategies below are what actually find the file.
            for doc in kml_cab_refs.get(cab_letter, []):
                try:
                    for h in search_local_cabinet(cab_letter, doc, "", ""):
                        if h["path"] not in seen_local_paths:
                            seen_local_paths.add(h["path"])
                            h["source"]   = "local"
                            h["ref"]      = f"{cab_letter}-{doc}"
                            h["strategy"] = "kml_cab_ref"
                            h["_tok_len"] = 200
                            local_hits.append(h)
                except Exception:
                    pass

            for doc in deed_cab_refs.get(cab_letter, []):
                try:
                    for h in search_local_cabinet(cab_letter, doc, "", ""):
                        if h["path"] not in seen_local_paths:
                            seen_local_paths.add(h["path"])
                            h["source"]   = "local"
                            h["ref"]      = f"{cab_letter}-{doc}"
                            h["strategy"] = "deed_cab_ref"
                            h["_tok_len"] = 150
                            local_hits.append(h)
                except Exception:
                    pass

            # b) KML PLAT field name tokens — PRIMARY name-based strategy.
            #     Cabinet files are named after the ORIGINAL plat filer / subdivider
            #     (e.g. "Adela Rael.pdf"), NOT the current owner. The KML PLAT
            #     field contains that original name, so it's the most reliable
            #     way to find the correct cabinet file by name.
            #     Example: "C-191-A ADELA RAEL" → tokens ["ADELA RAEL", "ADELA", "RAEL"]
            for hit in kml_matches:
                plat_tokens = _extract_plat_name_tokens(hit.get("plat", ""))
                for tok in plat_tokens:
                    try:
                        for h in search_local_cabinet(cab_letter, "", tok, ""):
                            if h["path"] not in seen_local_paths:
                                seen_local_paths.add(h["path"])
                                h["source"]   = "local"
                                h["ref"]      = "kml_plat_name"
                                h["strategy"] = "kml_plat_name"  # distinct strategy for sorting/display
                                h["_tok_len"] = len(tok) + 200   # highest KML name score
                                local_hits.append(h)
                    except Exception:
                        pass

            # b2) KML owner name — SECONDARY name-based strategy.
            #    The current parcel owner (e.g. GARZA, VERONICA) may or may not
            #    match the cabinet file name. Ownership changes over time but
            #    cabinet files don't get renamed, so this is a fallback.
            for hit in kml_matches:
                owner = hit.get("owner", "").strip()
                if not owner:
                    continue
                # Try full owner string, then each word >= 4 chars
                owner_tokens = [owner]
                for w in re.split(r'[\s,]+', owner):
                    if len(w) >= 4 and w not in owner_tokens:
                        owner_tokens.append(w)
                for tok in owner_tokens:
                    try:
                        for h in search_local_cabinet(cab_letter, "", tok, ""):
                            if h["path"] not in seen_local_paths:
                                seen_local_paths.add(h["path"])
                                h["source"]   = "local"
                                h["ref"]      = "kml_owner"
                                h["strategy"] = "kml_cab_ref"   # secondary KML match
                                h["_tok_len"] = len(tok) + 180
                                local_hits.append(h)
                    except Exception:
                        pass

            # c) Client name tokens — HIGH priority for Step 3 (client plat).
            #    The client IS the current owner, so their name IS on the cabinet file
            #    (e.g. "Rael, Adela" → finds "Adela Rael.pdf" or "Rael Adela.pdf").
            #    This also covers the grantee since they were pre-merged into client_tokens.
            for tok in client_tokens:
                try:
                    for h in search_local_cabinet(cab_letter, "", tok, ""):
                        if h["path"] not in seen_local_paths:
                            seen_local_paths.add(h["path"])
                            h["source"]   = "local"
                            h["ref"]      = "client_name"
                            h["strategy"] = "client_name"
                            h["_tok_len"] = len(tok) + 100
                            local_hits.append(h)
                except Exception:
                    pass

            # d) Prior owner name tokens — the plat may be filed under a previous owner.
            #    Build tokens the same way as client_tokens: full name + individual words.
            #    Strategy = 'prior_owner' ranks below client hits but above generic grantor.
            for raw_name in prior_owners:
                if not raw_name or not raw_name.strip():
                    continue
                raw = raw_name.strip()
                prior_tokens = [raw]
                if ',' in raw:
                    parts = [p.strip() for p in raw.split(',', 1) if p.strip()]
                    if len(parts) == 2:
                        prior_tokens.append(f"{parts[1]} {parts[0]}")
                    prior_tokens.append(parts[0])  # last name only
                for w in re.split(r'[\s,]+', raw):
                    if len(w) >= 4 and w not in prior_tokens:
                        prior_tokens.append(w)

                for tok in prior_tokens:
                    try:
                        for h in search_local_cabinet(cab_letter, "", tok, ""):
                            if h["path"] not in seen_local_paths:
                                seen_local_paths.add(h["path"])
                                h["source"]   = "local"
                                h["ref"]      = "prior_owner"
                                h["strategy"] = "prior_owner"
                                h["_tok_len"] = len(tok) + 60
                                local_hits.append(h)
                    except Exception:
                        pass

            # e) Grantor name from deed — lowest priority, may not match
            #    if ownership has changed since the deed was recorded.
            if grantor and grantor not in prior_owners:
                try:
                    for h in search_local_cabinet(cab_letter, "", grantor, ""):
                        if h["path"] not in seen_local_paths:
                            seen_local_paths.add(h["path"])
                            h["source"] = "local"
                            h["ref"]    = "grantor_search"
                            local_hits.append(h)
                except Exception:
                    pass

        # ── Auto-broaden: if 0 results and we targeted specific cabinets, retry ALL ──
        # DISABLED in name-only mode (no deed detail) to prevent flooding with
        # hundreds of false-positive name matches across all 6 cabinets.
        all_cabs = list(CABINET_FOLDERS.keys())
        if (not local_hits and set(target_cabs) != set(all_cabs) and client_tokens
                and has_deed_detail):   # ← name-only mode skips broadening
            print(f"[local] 0 results in target cabinet(s) {target_cabs} — broadening to all cabinets", flush=True)
            broadened_reason = f"Broadened search: no results in {', '.join(target_cabs)} — scanning all cabinets"
            for cab_letter in all_cabs:
                if cab_letter in target_cabs:
                    continue  # already searched
                if not CABINET_FOLDERS.get(cab_letter):
                    continue
                for tok in client_tokens:
                    try:
                        for h in search_local_cabinet(cab_letter, "", tok, ""):
                            if h["path"] not in seen_local_paths:
                                seen_local_paths.add(h["path"])
                                h["source"]   = "local"
                                h["ref"]      = "broadened_search"
                                h["strategy"] = "client_name"
                                h["_tok_len"] = len(tok) + 50  # lower than direct matches
                                local_hits.append(h)
                    except Exception:
                        pass
            if local_hits:
                targeting_reason = broadened_reason
        elif not local_hits and not has_deed_detail:
            print("[local] 0 results (name-only mode — auto-broaden disabled)", flush=True)

        # ── Sort: doc_number → kml_plat_name → kml_cab_ref → deed_cab_ref → client_name → prior_owner → name_match ─
        # doc_number = exact plat doc number match from file's leading number (highest confidence)
        # kml_plat_name = PLAT field name match (original filer — most reliable name match)
        strategy_order = {"doc_number": 0, "kml_plat_name": 1, "kml_cab_ref": 2, "deed_cab_ref": 3,
                          "client_name": 4, "prior_owner": 5, "name_match": 6, "page_ref": 7}
        local_hits.sort(key=lambda r: (
            strategy_order.get(r.get("strategy", ""), 9),
            -(r.get("_tok_len") or 0)
        ))

        # ── Cap total results to prevent UI flooding ──────────────────────────────
        MAX_LOCAL_HITS = 25
        if len(local_hits) > MAX_LOCAL_HITS:
            print(f"[local] Capping {len(local_hits)} results to {MAX_LOCAL_HITS}", flush=True)
            local_hits = local_hits[:MAX_LOCAL_HITS]

        return jsonify({
            "success":          True,
            "local":            local_hits,
            "target_cabinets":  target_cabs,
            "targeting_reason": targeting_reason,
            "kml_matches":      [],   # KML enrichment now handled by find-plat-kml
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "local": [], "kml_matches": []})


@plat_bp.route("/api/find-plat-online", methods=["POST"])
@require_auth
def api_find_plat_online():
    """
    Slow online survey search (separate endpoint so it doesn't block the UI).
    Searches by client name (current owner), grantee, and deed grantor — in
    that order — since the survey plat lists the current owner as the grantor.
    """
    try:
        data        = request.get_json() or {}
        detail      = data.get("detail", {})
        grantor     = data.get("grantor", "") or detail.get("Grantor", "")
        grantee     = data.get("grantee", "") or detail.get("Grantee", "")
        client_name = data.get("client_name", "")
        hits      = []
        seen_docs = set()

        def _do_online_search(name_last, label):
            """Run one surname search and append any survey hits found."""
            if not name_last or len(name_last) < 2:
                return
            try:
                search_url = f"{get_portal_url()}/scripts/hfweb.asp?Application=FNM&Database=TP"
                resp = get_session().get(search_url, timeout=8)
                if "CROSSNAMEFIELD" not in resp.text and "FIELD14" not in resp.text:
                    return  # not logged in
                soup = BeautifulSoup(resp.text, "html.parser")
                fd = scrape_form_data(soup)
                if not fd:
                    return
                fd["CROSSNAMEFIELD"] = name_last
                fd["CROSSNAMETYPE"]  = "begin"
                # NOTE: Do NOT set FIELD7="SUR" here — it conflicts with the form
                # and may suppress all results. Instrument-type filtering is done
                # by the regex below after results are returned.
                post  = get_session().post(f"{get_portal_url()}/scripts/hflook.asp", data=fd, timeout=10)
                soup2 = BeautifulSoup(post.text, "html.parser")
                for row in soup2.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) < 9:
                        continue
                    doc_link = cells[1].find("a") if len(cells) > 1 else None
                    if not doc_link:
                        continue
                    doc_no = doc_link.text.strip()
                    if doc_no in seen_docs:
                        continue
                    itype = cells[5].text.strip() if len(cells) > 5 else ""
                    if not re.search(r"survey|plat|map|lot.?line|replat|subdiv", itype, re.I):
                        continue
                    seen_docs.add(doc_no)
                    hits.append({
                        "doc_no":          doc_no,
                        "instrument_type": itype,
                        "location":        cells[2].text.strip() if len(cells) > 2 else "",
                        "recorded_date":   cells[7].text.strip() if len(cells) > 7 else "",
                        "grantor":         cells[9].text.strip() if len(cells) > 9 else "",
                        "grantee":         cells[10].text.strip() if len(cells) > 10 else "",
                        "pdf_url":         f"{get_portal_url()}/WebTemp/{doc_no}.pdf",
                        "source":          "online",
                        "search_label":    label,
                    })
            except Exception:
                pass

        # Build ordered list of last names to search (most specific first)
        names_to_search = []   # list of (last_name, label) tuples
        seen_lasts = set()

        def _add_last(raw, label):
            if not raw:
                return
            last = raw.split(",")[0].strip().upper()
            if last and len(last) >= 2 and last not in seen_lasts:
                seen_lasts.add(last)
                names_to_search.append((last, label))

        # Priority 1: client_name (current owner — most likely survey grantor)
        _add_last(client_name, "client")
        # Priority 2: grantee from deed (same person as client in Step 3)
        _add_last(grantee, "grantee")
        # Priority 3: grantor from deed (the seller — less likely to have a survey here)
        _add_last(grantor, "grantor")

        for last, label in names_to_search:
            _do_online_search(last, label)

        return jsonify({"success": True, "online": hits})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "online": []})


@plat_bp.route("/api/save-plat", methods=["POST"])
def api_save_plat():
    """Copy a local cabinet file OR download an online plat PDF into the project's B Plats folder."""
    # shutil imported at module top level
    try:
        data        = request.get_json()
        source      = data.get("source")         # "local" or "online"
        file_path   = data.get("file_path", "")  # for local
        doc_no      = data.get("doc_no", "")     # for online
        job_number  = data.get("job_number")
        client_name = data.get("client_name", "")
        job_type    = data.get("job_type", "BDY")
        filename    = data.get("filename", "")
        is_adjoiner = data.get("is_adjoiner", False)
        subject_id  = data.get("subject_id", "client")

        if not job_number or not client_name:
            return jsonify({"success": False, "error": "job_number and client_name required"})

        plats_root = (job_base_path(job_number, client_name, job_type)
                      / "E Research" / "B Plats")
        plats_root.mkdir(parents=True, exist_ok=True)
        (plats_root / "Adjoiners").mkdir(exist_ok=True)

        dest_dir = plats_root / "Adjoiners" if is_adjoiner else plats_root

        if not filename:
            filename = f"{doc_no or Path(file_path).stem}.pdf"
        filename = re.sub(r'[<>:"/\\|?*]', '', filename).strip()
        if not filename.endswith(".pdf"):
            filename += ".pdf"

        dest = dest_dir / filename

        # ── Duplicate check ───────────────────────────────────────────────────
        if dest.exists():
            return jsonify({
                "success":    True,
                "skipped":    True,
                "reason":     "File already exists in destination folder",
                "saved_to":   str(dest),
                "filename":   filename,
                "subject_id": subject_id,
            })

        if source == "local":
            shutil.copy2(file_path, dest)
        elif source == "online":
            pdf_url  = data.get("pdf_url", f"{get_portal_url()}/WebTemp/{doc_no}.pdf")
            pdf_resp = get_session().get(pdf_url, stream=True, timeout=30)
            if pdf_resp.status_code != 200:
                return jsonify({"success": False, "error": f"PDF fetch failed: {pdf_resp.status_code}"})
            with open(dest, "wb") as f:
                for chunk in pdf_resp.iter_content(8192):
                    f.write(chunk)
        else:
            return jsonify({"success": False, "error": "source must be 'local' or 'online'"})

        # ── Mark plat saved in research session ───────────────────────────────
        try:
            rs = load_research(job_number, client_name, job_type)
            for subj in rs.get("subjects", []):
                if subj["id"] == subject_id:
                    subj["plat_saved"] = True
                    break
            save_research(job_number, client_name, job_type, rs)
        except Exception:
            pass

        return jsonify({"success": True, "skipped": False, "saved_to": str(dest), "filename": filename, "subject_id": subject_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@plat_bp.route("/api/preview-pdf", methods=["GET", "POST"])
def api_preview_pdf():
    """
    Render the first page of a local PDF as a JPEG image for in-app preview.
    Accepts either GET ?path=... or POST { path: "..." }.
    Security: validates the file is within the survey drive directory.
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        pdf_path = data.get("path", "")
    else:
        pdf_path = request.args.get("path", "")

    if not pdf_path:
        return jsonify({"success": False, "error": "No path provided"}), 400

    p = Path(pdf_path)
    if not p.exists() or not p.is_file():
        return jsonify({"success": False, "error": "File not found"}), 404

    # Security: only allow files within the survey drive or project directories
    try:
        resolved = p.resolve()
    except Exception:
        return jsonify({"success": False, "error": "Invalid path"}), 400

    drive = detect_survey_drive()
    project_dir = Path(__file__).resolve().parent
    allowed = False
    if drive and str(resolved).upper().startswith(f"{drive}:\\"):
        allowed = True
    elif str(resolved).startswith(str(project_dir)):
        allowed = True
    if not allowed:
        return jsonify({"success": False, "error": "Path not within allowed directories"}), 403

    try:
        doc = fitz.open(str(p))
        if doc.page_count == 0:
            doc.close()
            return jsonify({"success": False, "error": "PDF has no pages"}), 400

        # Get requested page (default: first page)
        page_num = 0
        if request.method == "POST":
            page_num = min(int((request.get_json(silent=True) or {}).get("page", 0)), doc.page_count - 1)
        else:
            page_num = min(int(request.args.get("page", 0)), doc.page_count - 1)

        page = doc[page_num]
        total_pages = doc.page_count  # capture before closing
        # Render at 200 DPI for good quality without being too slow
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("jpeg", jpg_quality=85)
        doc.close()

        response = make_response(img_bytes)
        response.headers["Content-Type"] = "image/jpeg"
        response.headers["Cache-Control"] = "public, max-age=3600"
        response.headers["X-Page-Count"] = str(total_pages)
        return response

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Failed to render PDF: {str(e)}"}), 500
