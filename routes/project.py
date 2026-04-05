"""
routes/project.py - Job setup, download, research session, drive, and file ops Blueprint.
"""

import os
import re
import subprocess
import traceback
from pathlib import Path

from flask import Blueprint, request, jsonify

from helpers.cabinet import parse_cabinet_refs
from helpers.pdf_extract import extract_pdf_text as _extract_pdf_text_impl
from helpers.subscription import require_auth
from services.config import load_config, save_config
from services.drive import detect_survey_drive, get_survey_data_path, get_cabinet_path
from services.portal import get_portal_url, get_session
from services.session import (
    next_job_info, create_project_folders, job_base_path,
    load_research, save_research, is_safe_path,
)

bp = Blueprint("project", __name__)


def _extract_pdf_text(pdf_path: str) -> tuple[str, str]:
    return _extract_pdf_text_impl(pdf_path)


@bp.route("/api/next-job-number")
def api_next_job():
    num, range_folder = next_job_info()
    return jsonify({"next_job_number": num, "range_folder": range_folder})


@bp.route("/api/create-project", methods=["POST"])
@require_auth
def api_create_project():
    try:
        data = request.get_json()
        job_number = data.get("job_number")
        client_name = data.get("client_name", "")
        job_type = data.get("job_type", "BDY")

        if not job_number:
            job_number, _ = next_job_info()

        project_path, deeds_path = create_project_folders(job_number, client_name, job_type)
        return jsonify({"success": True, "job_number": job_number,
                        "project_path": project_path, "deeds_path": deeds_path})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/download", methods=["POST"])
def api_download():
    try:
        data        = request.get_json()
        doc_no      = data.get("doc_no", "")
        grantor     = data.get("grantor", "")
        grantee     = data.get("grantee", "")
        location    = data.get("location", "")
        job_number  = data.get("job_number")
        client_name = data.get("client_name", "")
        job_type    = data.get("job_type", "BDY")
        create_new  = data.get("create_project", False)
        is_adjoiner = data.get("is_adjoiner", False)
        adj_name    = data.get("adjoiner_name", "").strip()
        subject_id  = data.get("subject_id", "client")

        if not job_number:
            job_number, _ = next_job_info()

        if create_new:
            _, deeds_path = create_project_folders(job_number, client_name, job_type)
        else:
            deeds_path = str(job_base_path(job_number, client_name, job_type)
                             / "E Research" / "A Deeds")
            Path(deeds_path).mkdir(parents=True, exist_ok=True)
            (Path(deeds_path) / "Adjoiners").mkdir(exist_ok=True)

        # Route to Adjoiners subfolder if needed
        dest_dir = Path(deeds_path) / "Adjoiners" if is_adjoiner else Path(deeds_path)

        # Build filename
        loc_clean = re.sub(r'^[A-Z]', '', location.strip())
        def clean_name(n):
            parts = n.split(",")
            return parts[0].strip().title() if parts else n.title()

        grantor_short = clean_name(grantor)
        grantee_short = clean_name(grantee)
        filename = f"{loc_clean} {grantor_short} to {grantee_short}.pdf"
        filename = re.sub(r'[<>:"/\\|?*]', '', filename).strip()
        if not filename.endswith(".pdf"):
            filename += ".pdf"

        save_path = dest_dir / filename

        # ── Duplicate check ───────────────────────────────────────────────────
        if save_path.exists():
            return jsonify({
                "success":      True,
                "skipped":      True,
                "reason":       "File already exists in destination folder",
                "saved_to":     str(save_path),
                "filename":     filename,
                "job_number":   job_number,
                "subject_id":   subject_id,
            })

        # Download
        pdf_url  = f"{get_portal_url()}/WebTemp/{doc_no}.pdf"
        pdf_resp = get_session().get(pdf_url, stream=True, timeout=30)
        if pdf_resp.status_code != 200:
            return jsonify({"success": False, "error": f"PDF fetch failed: {pdf_resp.status_code}"})

        with open(save_path, "wb") as f:
            for chunk in pdf_resp.iter_content(8192):
                f.write(chunk)

        # ── Mark deed saved in research session ───────────────────────────────
        try:
            rs = load_research(job_number, client_name, job_type)
            for subj in rs.get("subjects", []):
                if subj["id"] == subject_id:
                    subj["deed_saved"] = True
                    subj["deed_path"]  = str(save_path)
                    break
            save_research(job_number, client_name, job_type, rs)
        except Exception:
            pass  # non-fatal

        return jsonify({
            "success":    True,
            "skipped":    False,
            "saved_to":   str(save_path),
            "saved_path": str(save_path),
            "filename":   filename,
            "job_number": job_number,
            "subject_id": subject_id,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/extract-deed-info", methods=["POST"])
def api_extract_deed_info():
    """
    Extract basic deed metadata from a saved PDF for plat search targeting.

    This is a lightweight alternative to full deed analysis — it extracts
    Grantor, Grantee, Location (book/page) from the filename pattern, and
    cabinet references from the PDF text.

    Filename pattern:  {book-page} {Grantor} to {Grantee}.pdf
    Example:           568-482 Martinez to Rael.pdf

    Body:  { pdf_path: str }
    Returns: { success, detail: { Grantor, Grantee, Location, ... } }
    """
    try:
        data     = request.get_json() or {}
        pdf_path = data.get("pdf_path", "").strip()
        if not pdf_path or not os.path.isfile(pdf_path):
            return jsonify({"success": False, "error": "File not found"})

        fname = Path(pdf_path).stem  #  e.g. "568-482 Martinez to Rael"
        detail: dict = {}

        # ── Parse filename for Location / Grantor / Grantee ──────────────────
        # Pattern:  "{book-page} {Grantor} to {Grantee}"
        m = re.match(
            r'^(\d+-\d+)\s+(.+?)\s+to\s+(.+)$', fname, re.I
        )
        if m:
            detail["Location"] = m.group(1).strip()
            detail["Grantor"]  = m.group(2).strip()
            detail["Grantee"]  = m.group(3).strip()
        else:
            # Fall back: just split on " to " if present
            if " to " in fname.lower():
                parts = re.split(r'\s+to\s+', fname, maxsplit=1, flags=re.I)
                if len(parts) == 2:
                    detail["Grantor"] = parts[0].strip()
                    detail["Grantee"] = parts[1].strip()

        # ── Scan PDF text for cabinet references (first 2 pages) ─────────────
        try:
            text, _ = _extract_pdf_text(pdf_path)
            if text:
                cab_refs = parse_cabinet_refs({"text": text[:5000]})
                if cab_refs:
                    detail["_cab_refs"] = cab_refs
                    # Also inject as text so parse_cabinet_refs works downstream
                    detail["Reference"] = " ".join(r["raw"] for r in cab_refs)
        except Exception:
            pass

        print(f"[extract-deed-info] {Path(pdf_path).name} → {detail}", flush=True)
        return jsonify({"success": True, "detail": detail})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/drive-status")
def api_drive_status():
    """Return detected removable drive info.  Add ?rescan=1 to force re-scan."""
    try:
        force  = request.args.get("rescan", "0") == "1"
        drive  = detect_survey_drive(force=force)
        survey = get_survey_data_path()
        cabinet= get_cabinet_path()
        return jsonify({
            "success":      True,
            "drive":        drive,          # None if not found
            "survey_path":  survey,
            "cabinet_path": cabinet,
            "drive_ok":     drive is not None and Path(survey).exists(),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/drive-override", methods=["POST"])
def api_drive_override():
    """Manually pin a drive letter (saved to config.json as 'survey_drive')."""
    try:
        data   = request.get_json()
        letter = (data.get("drive") or "").strip().upper()
        cfg    = load_config()
        if letter:
            cfg["survey_drive"] = letter
        else:
            cfg.pop("survey_drive", None)
        save_config(cfg)
        drive = detect_survey_drive(force=True)
        return jsonify({"success": True, "drive": drive, "drive_ok": drive is not None})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/research-session", methods=["GET"])
def api_research_get():
    try:
        job_number  = request.args.get("job_number", "")
        client_name = request.args.get("client_name", "")
        job_type    = request.args.get("job_type", "BDY")
        if not job_number or not client_name:
            return jsonify({"success": False, "error": "job_number + client_name required"})
        data = load_research(job_number, client_name, job_type)
        # (field migration is now handled inside load_research)

        # Progress summary
        subjects = data.get("subjects", [])
        total    = len(subjects)
        deeds    = sum(1 for s in subjects if s.get("deed_saved"))
        plats    = sum(1 for s in subjects if s.get("plat_saved"))
        done     = sum(1 for s in subjects if s.get("deed_saved") and s.get("plat_saved"))
        data["progress"] = {"total": total, "deeds": deeds, "plats": plats, "done": done}

        return jsonify({"success": True, "session": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/research-session", methods=["POST"])
def api_research_post():
    try:
        body        = request.get_json()
        job_number  = body.get("job_number")
        client_name = body.get("client_name", "")
        job_type    = body.get("job_type", "BDY")
        session     = body.get("session", {})
        if not job_number or not client_name:
            return jsonify({"success": False, "error": "job_number + client_name required"})
        save_research(job_number, client_name, job_type, session)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    try:
        path = request.get_json().get("path", "")
        if not is_safe_path(path):
            return jsonify({"success": False, "error": "Path not within allowed directories"})
        if os.path.exists(path):
            subprocess.Popen(["explorer", path])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/open-file", methods=["POST"])
def api_open_file():
    """Open a specific file with the default Windows application."""
    try:
        path = request.get_json().get("path", "")
        if not is_safe_path(path):
            return jsonify({"success": False, "error": "Path not within allowed directories"})
        if os.path.exists(path):
            os.startfile(path)
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "File not found"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/recent-jobs")
def api_recent_jobs():
    """Scan Survey Data for the 10 most recently modified job folders."""
    try:
        survey_str = get_survey_data_path()
        if not survey_str or not Path(survey_str).exists():
            return jsonify({"success": True, "jobs": []})
        survey = Path(survey_str)
        jobs   = []
        for range_dir in survey.iterdir():
            if not range_dir.is_dir() or range_dir.name.startswith("00"):
                continue
            for job_dir in range_dir.iterdir():
                if not job_dir.is_dir():
                    continue
                m = re.match(r'^(\d{4})\s+(.*)', job_dir.name)
                if not m:
                    continue
                job_num    = int(m.group(1))
                client     = m.group(2).strip()
                job_type   = "BDY"
                for sub in job_dir.iterdir():
                    mt = re.match(r'^\d+-01-([A-Z]+)\s', sub.name)
                    if mt:
                        job_type = mt.group(1)
                        break
                jobs.append({
                    "job_number":  job_num,
                    "client_name": client,
                    "job_type":    job_type,
                    "modified":    job_dir.stat().st_mtime,
                })
        jobs.sort(key=lambda j: j["modified"], reverse=True)
        for j in jobs:
            del j["modified"]
        return jsonify({"success": True, "jobs": jobs[:10]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/export-session", methods=["POST"])
@require_auth
def api_export_session():
    """Generate a plain-text research summary from the current session."""
    try:
        body = request.get_json()
        rs   = body.get("session", {})
        if not rs:
            return jsonify({"success": False, "error": "No session provided"})

        lines = [
            "DEED & PLAT RESEARCH SUMMARY",
            f"{'='*50}",
            f"Job #:    {rs.get('job_number')}",
            f"Client:   {rs.get('client_name')}",
            f"Type:     {rs.get('job_type')}",
            "",
        ]
        subjects = rs.get("subjects", [])
        prog     = rs.get("progress", {})
        if prog:
            lines.append(f"Progress: {prog.get('done',0)}/{prog.get('total',0)} complete  "
                         f"| Deeds: {prog.get('deeds',0)}  Plats: {prog.get('plats',0)}")
            lines.append("")

        lines.append(f"SUBJECTS ({len(subjects)}):")
        lines.append("-"*40)
        for s in subjects:
            deed_mark = "[✓]" if s.get("deed_saved") else "[ ]"
            plat_mark = "[✓]" if s.get("plat_saved") else "[ ]"
            status    = s.get("status", "pending").upper()
            lines.append(f"  {s['type'].upper():<10} {s['name']}")
            lines.append(f"    Deed {deed_mark}  Plat {plat_mark}  Status: {status}")
            if s.get("notes"):
                lines.append(f"    Notes: {s['notes']}")
            lines.append("")

        text = "\n".join(lines)
        return jsonify({"success": True, "text": text})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
