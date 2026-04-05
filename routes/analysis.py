"""
routes/analysis.py - DXF generation and deep deed analysis Blueprint.
"""

import json
import math
import os
import tempfile
import traceback
from pathlib import Path

from flask import Blueprint, request, jsonify

from helpers.adjoiner import parse_adjoiner_names
from helpers.cabinet import parse_cabinet_refs
from helpers.deed_analysis import (
    analyze_deed as _analyze_deed_impl,
    isolate_legal_description as _isolate_legal_description_impl,
)
from helpers.dxf import generate_boundary_dxf as _generate_dxf_impl
from helpers.metes_bounds import (
    parse_metes_bounds, calls_to_coords, extract_trs,
    detect_monuments, classify_description_type,
    shoelace_area, has_pob,
)
from helpers.pdf_extract import extract_pdf_text as _extract_pdf_text_impl
from helpers.subscription import require_auth, require_pro
from services.portal import get_portal_url, get_session
from services.session import ensure_dwg_folder

bp = Blueprint("analysis", __name__)


def _extract_pdf_text(pdf_path: str) -> tuple[str, str]:
    return _extract_pdf_text_impl(pdf_path)


def analyze_deed(detail: dict, pdf_path: str = "") -> dict:
    return _analyze_deed_impl(detail, pdf_path, pdf_extractor=_extract_pdf_text)


def _isolate_legal_description(text: str) -> str:
    return _isolate_legal_description_impl(text)


def generate_boundary_dxf(parcels, job_number, client_name, job_type, options=None):
    dwg_dir = ensure_dwg_folder(job_number, client_name, job_type)
    return _generate_dxf_impl(parcels, dwg_dir, job_number, client_name, job_type, options)


@bp.route("/api/parse-calls", methods=["POST"])
def api_parse_calls():
    """
    Parse metes-and-bounds calls from arbitrary text.
    Body: { text: str, fields: [str]  (optional list of deed fields to scan) }
    Returns: { success, calls: [{bearing_label, azimuth, distance, bearing_raw}] }
    """
    try:
        data   = request.get_json()
        text   = data.get("text", "")
        detail = data.get("detail", {})  # full deed detail dict (optional bonus scan)

        # Combine: explicit text + any provided deed detail fields
        combined = text
        for field in ["Other_Legal", "Subdivision_Legal", "Comments",
                      "Legal Description", "Legal", "Reference", "Description"]:
            val = detail.get(field, "")
            if val and isinstance(val, str):
                combined += "\n" + val

        calls = parse_metes_bounds(combined)
        pts   = calls_to_coords(calls) if calls else []

        # Closure stats
        closure_err = 0.0
        if len(pts) >= 2:
            closure_err = round(math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]), 4)

        return jsonify({
            "success":     True,
            "calls":       calls,
            "count":       len(calls),
            "closure_err": closure_err,
            "coords":      pts,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/extract-calls-from-pdf", methods=["POST"])
def api_extract_calls_from_pdf():
    """
    Given a path to a saved deed or plat PDF, extract metes-and-bounds calls.

    Strategy:
      1. Try fitz text extraction first (fast, works on text-based PDFs).
      2. Fall back to Tesseract OCR if no text is found (scanned image PDFs).
      3. Run parse_metes_bounds() on the combined text.

    Body:  { pdf_path: str }
    Returns: { success, calls, count, closure_err, coords, filename, source }
    """
    try:
        data     = request.get_json()
        pdf_path = data.get("pdf_path", "").strip()
        if not pdf_path or not os.path.exists(pdf_path):
            return jsonify({"success": False, "error": "File not found"})

        filename     = Path(pdf_path).name
        text, source = _extract_pdf_text(pdf_path)  # text layer first, OCR fallback


        calls = parse_metes_bounds(text)
        pts   = calls_to_coords(calls) if calls else []
        closure_err = 0.0
        if len(pts) >= 2:
            closure_err = round(math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]), 4)

        return jsonify({
            "success":     True,
            "calls":       calls,
            "count":       len(calls),
            "closure_err": closure_err,
            "coords":      pts,
            "filename":    filename,
            "source":      source,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/generate-dxf", methods=["POST"])
@require_auth
@require_pro
def api_generate_dxf():
    """
    Generate a DXF file from one or more parcel call-lists.

    Body:
    {
      job_number:  int,
      client_name: str,
      job_type:    str,
      parcels: [
        {
          label:   str,          // e.g. "Client" or "Rael Adjoiner"
          layer:   str,          // "CLIENT" | "ADJOINERS" (optional, default CLIENT)
          start_x: float,        // optional, default 0
          start_y: float,        // optional, default 0
          calls: [
            { bearing_label, azimuth, distance }
          ]
        }
      ],
      options: {
        draw_boundary:   bool,
        draw_labels:     bool,
        draw_endpoints:  bool,
        label_size:      float,
        close_tolerance: float,
      }
    }
    Returns: { success, saved_to, filename, closure_errors: [{label, error}] }
    """
    try:
        data        = request.get_json()
        job_number  = data.get("job_number")
        client_name = data.get("client_name", "")
        job_type    = data.get("job_type", "BDY")
        parcels     = data.get("parcels", [])
        options     = data.get("options", {})

        if not job_number or not client_name:
            return jsonify({"success": False, "error": "job_number and client_name are required"})
        if not parcels:
            return jsonify({"success": False, "error": "No parcels provided"})

        saved_path, closure_errs = generate_boundary_dxf(
            parcels, job_number, client_name, job_type, options)
        filename = Path(saved_path).name

        return jsonify({
            "success":        True,
            "saved_to":       saved_path,
            "filename":       filename,
            "closure_errors": closure_errs,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/extract-deed-description", methods=["POST"])
@require_auth
@require_pro
def api_extract_deed_description():
    """
    Extract the full property description from a deed PDF.

    Body: {
        pdf_path: str            – Path to the saved deed PDF
        detail:   dict (optional) – Scraped metadata from the website (fallback)
        doc_no:   str  (optional) – Document number for the PDF URL fallback
    }

    Returns: {
        success: bool,
        description: {
            full_text:        str   – Complete text extracted from the PDF
            legal_description: str  – Isolated legal description section
            source:           str   – 'text' or 'ocr'
            trs_refs:         list  – Township/Range/Section references
            calls_count:      int   – Number of metes-and-bounds calls found
            calls:            list  – Parsed bearing/distance calls
            desc_type:        str   – 'metes_and_bounds', 'lot_block', 'tract', 'trs_only', 'unknown'
            grantor:          str   – Extracted grantor name
            grantee:          str   – Extracted grantee name
            area_acres:       float – Computed area if metes-and-bounds
            perimeter_ft:     float – Computed perimeter if metes-and-bounds
            monuments:        list  – Monument types referenced
            adjoiners:        list  – Adjoiner names found in description
            pob_found:        bool  – Whether Point of Beginning was found
            cab_refs:         list  – Cabinet references found
        }
    }
    """
    try:
        data     = request.get_json(silent=True) or {}
        pdf_path = data.get("pdf_path", "")
        detail   = data.get("detail", {})
        doc_no   = data.get("doc_no", "")

        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}

        full_text = ""
        source    = "none"

        # ── 1. Extract text from saved PDF ────────────────────────────────
        if pdf_path and os.path.isfile(pdf_path):
            print(f"[extract-desc] Reading PDF: {pdf_path}", flush=True)
            full_text, source = _extract_pdf_text(pdf_path)
            print(f"[extract-desc] source={source} chars={len(full_text.strip())}", flush=True)

        # ── 2. Fallback: if no PDF, use URL or metadata only ──────────────
        if not full_text.strip() and doc_no:
            # Try to download the PDF from the online source into a temp file
            try:
                pdf_url  = f"{get_portal_url()}/WebTemp/{doc_no}.pdf"
                pdf_resp = get_session().get(pdf_url, stream=True, timeout=20)
                if pdf_resp.status_code == 200:
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                        for chunk in pdf_resp.iter_content(8192):
                            tf.write(chunk)
                        tmp_path = tf.name
                    full_text, source = _extract_pdf_text(tmp_path)
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
                    print(f"[extract-desc] Downloaded PDF from URL, source={source} chars={len(full_text.strip())}", flush=True)
            except Exception as e:
                print(f"[extract-desc] PDF URL fallback failed: {e}", flush=True)

        # ── 3. Merge with scraped metadata fields ─────────────────────────
        metadata_text = ""
        legal_fields = [
            "Legal Description", "Legal", "Other Legal", "Other_Legal",
            "Subdivision Legal", "Subdivision_Legal", "Description",
            "Comments", "Remarks", "Reference",
        ]
        for field in legal_fields:
            val = detail.get(field, "")
            if val:
                metadata_text += val + "\n"

        combined = full_text + "\n" + metadata_text

        # ── 4. Isolate the legal description section ──────────────────────
        legal_desc = _isolate_legal_description(combined)

        # ── 5. Parse structured data from the text ────────────────────────
        trs_refs = extract_trs(combined)
        calls    = parse_metes_bounds(combined)

        # Description type
        desc_type = classify_description_type(combined, calls, trs_refs)

        # Area / perimeter if metes-and-bounds
        pts = calls_to_coords(calls) if calls else []
        perimeter = sum(c.get("distance", 0) for c in calls)
        area_sqft = shoelace_area(pts)

        # Monuments
        monuments = detect_monuments(combined)

        # Adjoiner names
        adjoiners = []
        if detail:
            adj_items = parse_adjoiner_names({"Legal": combined})
            adjoiners = [a["name"] for a in adj_items]

        # POB
        pob_found = has_pob(combined)

        # Cabinet refs
        cab_refs = parse_cabinet_refs({"Legal": combined, **detail})

        # Grantor / Grantee from detail or PDF text
        grantor = detail.get("Grantor", "") or detail.get("grantor", "")
        grantee = detail.get("Grantee", "") or detail.get("grantee", "")

        # Format calls for frontend
        calls_formatted = []
        for c in calls:
            if c.get("type") == "straight":
                calls_formatted.append({
                    "bearing": c.get("bearing_label", ""),
                    "distance": c.get("distance", 0),
                    "raw": c.get("bearing_raw", ""),
                })
            elif c.get("type") == "curve":
                calls_formatted.append({
                    "bearing": f"Curve {c.get('direction', '').title()} R={c.get('radius', 0):.1f}'",
                    "distance": round(c.get("arc_length", 0) or c.get("chord_length", 0), 2),
                    "raw": c.get("bearing_raw", ""),
                    "curve": True,
                })

        return jsonify({
            "success": True,
            "description": {
                "full_text":         full_text.strip()[:10000],  # Cap to avoid huge payloads
                "legal_description": legal_desc.strip()[:5000],
                "source":            source,
                "trs_refs":          [t["trs"] for t in trs_refs],
                "calls_count":       len(calls),
                "calls":             calls_formatted[:100],
                "desc_type":         desc_type,
                "grantor":           grantor,
                "grantee":           grantee,
                "area_acres":        round(area_sqft / 43560.0, 3) if area_sqft else 0,
                "perimeter_ft":      round(perimeter, 2),
                "monuments":         monuments,
                "adjoiners":         adjoiners[:30],
                "pob_found":         pob_found,
                "cab_refs":          [{"cabinet": r["cabinet"], "doc": r["doc"]} for r in cab_refs],
            }
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/analyze-deed", methods=["POST"])
@require_auth
@require_pro
def api_analyze_deed():
    """
    Deep deed analysis endpoint.
    Body: { detail: dict, pdf_path: str (optional) }
    Returns: { success, analysis: {...} }
    """
    try:
        data     = request.get_json(silent=True) or {}
        detail   = data.get("detail", {})
        pdf_path = data.get("pdf_path", "")

        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}

        result = analyze_deed(detail, pdf_path)
        return jsonify({"success": True, "analysis": result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})
