from flask import Flask, request, jsonify, send_from_directory, Response, make_response
import requests as req_lib
from bs4 import BeautifulSoup
import os, re, json, traceback, subprocess, gzip, math
from pathlib import Path
import fitz          # PyMuPDF  — PDF → image
import pytesseract
from PIL import Image
import io
import xml_processor
import ezdxf

# ── Helper modules (extracted from this file for maintainability) ─────────────
from helpers.metes_bounds import (
    parse_metes_bounds, calls_to_coords, _bearing_to_azimuth,
    extract_trs, detect_monuments, classify_description_type,
    shoelace_area, has_pob,
    _BEARING_PAT, _BEARING_VERBOSE, _CURVE_PAT,
    _MONUMENT_PATTERNS, _LOT_BLOCK_RE, _TRACT_RE, _POB_RE,
)
from helpers.pdf_extract import (
    extract_pdf_text as _extract_pdf_text_impl,
    setup_tesseract, ocr_plat_file as _ocr_plat_file_impl,
    _ocr_cache_path,
)
from helpers.adjoiner import (
    parse_adjoiner_names as _parse_adjoiner_names_impl,
    _ADJ_PATTERNS, _NOISE_WORDS,
)
from helpers.cabinet import (
    CABINET_FOLDERS, parse_cabinet_refs,
    extract_plat_name_tokens as _extract_plat_name_tokens,
    extract_cabinet_display_name as _extract_cabinet_display_name,
    extract_cabinet_doc_number as _extract_cabinet_doc_number,
    search_local_cabinet as _search_local_cabinet_impl,
    _cab_scan_cache,
)
from helpers.deed_analysis import (
    analyze_deed as _analyze_deed_impl,
    isolate_legal_description as _isolate_legal_description_impl,
)
from helpers.dxf import generate_boundary_dxf as _generate_dxf_impl

# Point pytesseract at the Tesseract binary (delegated to helpers/pdf_extract.py)
setup_tesseract()

app = Flask(__name__, static_folder='.', static_url_path='')

BASE_URL = "http://records.1stnmtitle.com"

# ── Removable-drive detection ───────────────────────────────────────────────────
# The Survey Data folder lives on a removable drive whose letter changes
# between computers.  We scan all available drive letters at startup and
# cache the result; the /api/drive-status endpoint lets you re-scan live.

_SURVEY_RELATIVE   = os.path.join("AI DATA CENTER", "Survey Data")
_CABINET_RELATIVE  = os.path.join("AI DATA CENTER", "Survey Data",
                                   "00 COUNTY CLERK SCANS Cabs A-B- C-D - E")
_detected_drive: str | None = None   # e.g. "F"


def detect_survey_drive(force: bool = False) -> str | None:
    """Scan all drive letters for the Survey Data folder.
    Returns the drive letter (e.g. 'F') or None if not found.
    Caches the result; pass force=True to rescan.
    """
    global _detected_drive
    if _detected_drive and not force:
        # Verify cached drive is still present
        if Path(f"{_detected_drive}:\\").exists():
            return _detected_drive
    # Try config override first
    cfg = load_config()
    override = cfg.get("survey_drive", "").strip().upper()
    if override and len(override) == 1 and Path(f"{override}:\\").exists():
        candidate = Path(f"{override}:\\") / _SURVEY_RELATIVE
        if candidate.exists():
            _detected_drive = override
            return _detected_drive
    # Scan all drive letters
    import string
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if not root.exists():
            continue
        candidate = root / _SURVEY_RELATIVE
        if candidate.exists():
            _detected_drive = letter
            print(f"[drive] Survey Data found on {letter}:\\", flush=True)
            return _detected_drive
    _detected_drive = None
    print("[drive] Survey Data NOT found on any drive.", flush=True)
    return None


def get_survey_data_path() -> str:
    """Return the current Survey Data path, auto-detecting the drive."""
    drive = detect_survey_drive()
    if drive:
        return str(Path(f"{drive}:\\") / _SURVEY_RELATIVE)
    return ""  # drive not found — caller should check for empty string and warn user


def get_cabinet_path() -> str:
    """Return the current Cabinet path, auto-detecting the drive."""
    drive = detect_survey_drive()
    if drive:
        return str(Path(f"{drive}:\\") / _CABINET_RELATIVE)
    return r"F:\AI DATA CENTER\Survey Data\00 COUNTY CLERK SCANS Cabs A-B- C-D - E"


# Kick off detection at startup (non-blocking — just sets module-level cache)
try:
    detect_survey_drive()
except Exception:
    pass


# CABINET_FOLDERS — imported from helpers.cabinet
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

# Must match the <select> options in index.html
JOB_TYPES = ["BDY", "ILR", "SE", "SUB", "TIE", "TOPO", "ELEV", "ALTA", "CONS", "OTHER"]

# One persistent session per server run
web_session = req_lib.Session()
web_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

# ── helpers ────────────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def next_job_info():
    """Scan Survey Data to find the next job number and its range folder."""
    survey_str = get_survey_data_path()
    if not survey_str or not Path(survey_str).exists():
        # Drive not connected — return safe defaults
        return 2938, "2900-2999"
    survey = Path(survey_str)
    max_num = 0
    for child in survey.iterdir():
        if not child.is_dir():
            continue
        for job_dir in child.iterdir():
            if not job_dir.is_dir():
                continue
            m = re.match(r'^(\d{4})', job_dir.name)
            if m:
                max_num = max(max_num, int(m.group(1)))
    if max_num == 0:
        max_num = 2937  # fallback
    next_num = max_num + 1
    rstart = (next_num // 100) * 100
    return next_num, f"{rstart}-{rstart + 99}"

def create_project_folders(job_number, client_name, job_type):
    """Create the full folder tree and return the deeds path."""
    rstart = (int(job_number) // 100) * 100
    range_folder = f"{rstart}-{rstart + 99}"
    last_name = client_name.split(",")[0].strip()

    survey = Path(get_survey_data_path())
    client_path = survey / range_folder / f"{job_number} {client_name}"
    sub_path = client_path / f"{job_number}-01-{job_type} {last_name}"

    for folder in ["A Office", "B Drafting", "C Survey", "D Correspondence", "E Research", "F PROOFING"]:
        (sub_path / folder).mkdir(parents=True, exist_ok=True)
    for rf in ["A Deeds", "B Plats", "C Other"]:
        (sub_path / "E Research" / rf).mkdir(exist_ok=True)
    # Adjoiner subfolders
    (sub_path / "E Research" / "A Deeds" / "Adjoiners").mkdir(exist_ok=True)
    (sub_path / "E Research" / "B Plats" / "Adjoiners").mkdir(exist_ok=True)
    (client_path / "XXXX-00-LEGACY").mkdir(exist_ok=True)

    deeds_path = sub_path / "E Research" / "A Deeds"
    return str(client_path), str(deeds_path)

def _job_base_path(job_number, client_name: str, job_type: str) -> Path:
    """Return the canonical job subfolder path (does NOT create folders).

    Pattern: SurveyData / {range} / {job_number} {client_name} /
             {job_number}-01-{job_type} {last_name}
    """
    rstart    = (int(job_number) // 100) * 100
    last_name = client_name.split(",")[0].strip()
    return (
        Path(get_survey_data_path())
        / f"{rstart}-{rstart + 99}"
        / f"{job_number} {client_name}"
        / f"{job_number}-01-{job_type} {last_name}"
    )


def _extract_pdf_text(pdf_path: str) -> tuple[str, str]:
    """Delegates to helpers.pdf_extract.extract_pdf_text."""
    return _extract_pdf_text_impl(pdf_path)



def _scrape_form_data(soup) -> dict:
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


def _research_path(job_number, client_name, job_type) -> Path:
    return _job_base_path(job_number, client_name, job_type) / "E Research" / "research.json"


def load_research(job_number, client_name, job_type) -> dict:
    p = _research_path(job_number, client_name, job_type)
    data = None
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    if data is None:
        # Default skeleton
        data = {
            "job_number":  job_number,
            "client_name": client_name,
            "job_type":    job_type,
            "subjects": [
                {"id": "client", "type": "client", "name": client_name,
                 "deed_saved": False, "plat_saved": False,
                 "status": "pending", "notes": "",
                 "deed_path": "", "plat_path": ""}
            ]
        }
    # Migrate subjects that pre-date new fields (idempotent)
    for s in data.get("subjects", []):
        s.setdefault("status",    "pending")
        s.setdefault("notes",     "")
        s.setdefault("deed_path", "")
        s.setdefault("plat_path", "")
    return data


def save_research(job_number, client_name, job_type, data: dict):
    p = _research_path(job_number, client_name, job_type)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# ── static ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    resp = make_response(send_from_directory(".", "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@app.route("/app.js")
def serve_appjs():
    # No-cache headers are applied by the @after_request hook below
    return send_from_directory(".", "app.js")

@app.route("/style.css")
def serve_css():
    # No-cache headers are applied by the @after_request hook below
    return send_from_directory(".", "style.css")

@app.route("/favicon.png")
def serve_favicon():
    return send_from_directory(".", "favicon.png")

@app.after_request
def add_no_cache(response):
    """Prevent browser from caching any local dev files."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ── config ─────────────────────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        cfg = load_config()
        return jsonify({
            "success": True,
            "config": {
                "firstnm_user": cfg.get("firstnm_user") or cfg.get("username", ""),
                "firstnm_pass": cfg.get("firstnm_pass") or cfg.get("password", ""),
                "firstnm_url":  cfg.get("firstnm_url", ""),
                "last_session": cfg.get("last_session"),
            }
        })
    data = request.get_json()
    cfg = load_config()
    if "firstnm_user" in data:
        cfg["firstnm_user"] = data["firstnm_user"]
        cfg["username"]     = data["firstnm_user"]
    if "firstnm_pass" in data:
        cfg["firstnm_pass"] = data["firstnm_pass"]
        cfg["password"]     = data["firstnm_pass"]
    if "firstnm_url" in data:
        cfg["firstnm_url"] = data["firstnm_url"]
    if "username" in data:
        cfg["username"] = data["username"]
    if "password" in data:
        cfg["password"] = data["password"]
    if "last_session" in data:
        cfg["last_session"] = data["last_session"]
    save_config(cfg)
    return jsonify({"success": True})

# ── login ──────────────────────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json()
        username = data.get("username", "")
        password = data.get("password", "")
        remember = data.get("remember", False)

        # Fetch login page to discover form
        resp = web_session.get(BASE_URL + "/", timeout=8)
        soup = BeautifulSoup(resp.text, "lxml")
        form = soup.find("form")
        if not form:
            return jsonify({"success": False, "error": "Login form not found"})

        action = form.get("action", "/")
        if not action.startswith("http"):
            action = BASE_URL + "/" + action.lstrip("/")

        form_data = {}
        for inp in form.find_all('input'):
            nm = inp.get('name')
            itype = (inp.get('type') or 'text').lower()
            if nm:
                if itype == 'image':
                    # Image-type submit buttons need .x/.y coords
                    form_data[nm + '.x'] = '1'
                    form_data[nm + '.y'] = '1'
                else:
                    form_data[nm] = inp.get('value', '')

        # Map credentials using known e.halFILE field names first,
        # then fall back to heuristic detection
        mapped = False
        for inp in form.find_all('input'):
            nm  = inp.get('name', '')
            itype = (inp.get('type') or 'text').lower()
            if nm == 'FormUser':
                form_data['FormUser'] = username
                mapped = True
            elif nm == 'FormPassword':
                form_data['FormPassword'] = password
                mapped = True
        if not mapped:
            for inp in form.find_all('input'):
                itype = (inp.get('type') or 'text').lower()
                iname = (inp.get('name') or '').lower()
                if itype == 'password':
                    form_data[inp['name']] = password
                elif itype == 'text' and ('user' in iname or 'login' in iname):
                    form_data[inp['name']] = username

        post_resp = web_session.post(action, data=form_data, timeout=8)
        # Success = we landed on the search/welcome page, NOT back on login
        landed_url = post_resp.url.lower()
        success = ('hfweb' in landed_url or 'new search' in post_resp.text.lower() or
                   ('logout' in post_resp.text.lower() and 'records.1stnmtitle.com/' not in landed_url.rstrip('/')))

        if success:
            if remember:
                cfg = load_config()
                cfg["username"] = username
                cfg["password"] = password
                save_config(cfg)
            return jsonify({"success": True, "username": username})
        else:
            return jsonify({"success": False, "error": "Invalid credentials or login failed"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ── logout ─────────────────────────────────────────────────────────────────────

@app.route("/api/logout", methods=["POST"])
def api_logout():
    web_session.cookies.clear()
    return jsonify({"success": True})

# extract_trs — imported from helpers.metes_bounds


# ── search ─────────────────────────────────────────────────────────────────────

@app.route("/api/search", methods=["POST"])
def api_search():
    try:
        data = request.get_json()
        name      = data.get("name", "").strip()
        address   = data.get("address", "").strip()
        name_type = data.get("name_type", "grantor")  # "grantor" | "grantee"
        # Map UI operator labels to site's actual option values
        op_map = {"contains": "contains", "begins with": "begin", "exact match": "exact", "equals": "exact"}
        operator = op_map.get(data.get("operator", "contains"), "contains")

        search_url = f"{BASE_URL}/scripts/hfweb.asp?Application=FNM&Database=TP"
        resp = web_session.get(search_url, timeout=15)

        # Detect redirect back to login page
        landed = resp.url.lower().rstrip('/')
        if landed == BASE_URL.lower().rstrip('/') or 'login' in landed:
            return jsonify({"success": False, "error": "Session expired — please log in again."})

        # The site has malformed HTML (form appears after </html>),
        # so use html.parser which tolerates this. Also detect auth via raw text.
        if 'CROSSNAMEFIELD' not in resp.text and 'FIELD14' not in resp.text:
            return jsonify({"success": False, "error": "Session expired — please log in again."})

        soup = BeautifulSoup(resp.text, "html.parser")

        # Action is set via JS; we know it's always hflook.asp
        action = BASE_URL + "/scripts/hflook.asp"

        form = soup.find("form")
        if not form:
            return jsonify({"success": False, "error": "Search form not found"})

        form_data = {}
        for inp in form.find_all("input"):
            nm = inp.get("name")
            if nm:
                form_data[nm] = inp.get("value", "")
        for sel in form.find_all("select"):
            nm = sel.get("name")
            if nm:
                opt = sel.find("option", selected=True) or sel.find("option")
                form_data[nm] = opt["value"] if opt and opt.get("value") else ""

        # Apply search criteria
        if name:
            if name_type == "grantee":
                # Grantee index: FIELD20 is typically the grantee cross-reference field
                form_data["CROSSNAMEFIELD"] = name
                form_data["CROSSNAMETYPE"]  = operator
                form_data["CROSSTYPE"]       = "GE"   # GE = grantee, GR = grantor
            else:
                form_data["CROSSNAMEFIELD"] = name
                form_data["CROSSNAMETYPE"]  = operator
                form_data["CROSSTYPE"]       = "GR"
        if address:
            form_data["FIELD19"] = address
            form_data["SEARCHTYPE19"] = operator

        post_resp = web_session.post(action, data=form_data, timeout=20)
        soup2 = BeautifulSoup(post_resp.text, "html.parser")

        # Parse results table
        results = []
        # Find count
        count_text = ""
        for tag in soup2.find_all(string=re.compile(r'\d+ records? found', re.I)):
            count_text = tag.strip()
            break

        rows = soup2.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 9:
                continue
            doc_link = cells[1].find("a") if len(cells) > 1 else None
            if not doc_link:
                continue
            doc_no = doc_link.text.strip()
            if not doc_no or not re.match(r'^[A-Z0-9]+$', doc_no):
                continue
            results.append({
                "doc_no":           doc_no,
                "location":         cells[2].text.strip() if len(cells) > 2 else "",
                "document_code":    cells[3].text.strip() if len(cells) > 3 else "",
                "gf_number":        cells[4].text.strip() if len(cells) > 4 else "",
                "instrument_type":  cells[5].text.strip() if len(cells) > 5 else "",
                "document_no":      cells[6].text.strip() if len(cells) > 6 else "",
                "recorded_date":    cells[7].text.strip() if len(cells) > 7 else "",
                "instrument_date":  cells[8].text.strip() if len(cells) > 8 else "",
                "grantor":          cells[9].text.strip() if len(cells) > 9 else "",
                "grantee":          cells[10].text.strip() if len(cells) > 10 else "",
            })

        return jsonify({"success": True, "results": results, "count": len(results), "count_text": count_text})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})

# ── chain-of-title search ─────────────────────────────────────────────────────

@app.route("/api/chain-search", methods=["POST"])
def api_chain_search():
    """
    Trace ownership backward by recursively searching the grantor as grantee.
    Returns a chain of {doc_no, grantor, grantee, location, date, has_plat_ref}.
    Stop when: no results, cycle detected, plat reference found, or max_hops reached.
    """
    try:
        data = request.get_json()
        start_grantor = data.get("start_grantor", "").strip()
        max_hops = min(int(data.get("max_hops", 10)), 20)  # cap at 20

        if not start_grantor:
            return jsonify({"success": False, "error": "No starting grantor provided"})

        chain = []
        seen_docs = set()
        current_name = start_grantor
        stop_reason = ""

        # Plat reference patterns
        plat_re = re.compile(r'(?:plat|cabinet|cab\.?|survey|plat\s+book)', re.I)

        for hop in range(max_hops):
            # Search for current_name as GRANTEE (find deed where they received property)
            search_url = f"{BASE_URL}/scripts/hfweb.asp?Application=FNM&Database=TP"
            try:
                resp = web_session.get(search_url, timeout=15)
            except Exception:
                stop_reason = "Network error during search"
                break

            if 'CROSSNAMEFIELD' not in resp.text:
                stop_reason = "Session expired — could not continue chain search"
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            form = soup.find("form")
            if not form:
                stop_reason = "Search form not found"
                break

            form_data = {}
            for inp in form.find_all("input"):
                nm = inp.get("name")
                if nm:
                    form_data[nm] = inp.get("value", "")
            for sel in form.find_all("select"):
                nm = sel.get("name")
                if nm:
                    opt = sel.find("option", selected=True) or sel.find("option")
                    form_data[nm] = opt["value"] if opt and opt.get("value") else ""

            # Search as grantee
            form_data["CROSSNAMEFIELD"] = current_name
            form_data["CROSSNAMETYPE"] = "begin"
            form_data["CROSSTYPE"] = "GE"  # GE = grantee

            action = BASE_URL + "/scripts/hflook.asp"
            try:
                post_resp = web_session.post(action, data=form_data, timeout=20)
            except Exception:
                stop_reason = f"Network error searching for {current_name}"
                break

            soup2 = BeautifulSoup(post_resp.text, "html.parser")
            rows = soup2.find_all("tr")

            # Find deed results (prioritize warranty deeds)
            candidates = []
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 9:
                    continue
                doc_link = cells[1].find("a") if len(cells) > 1 else None
                if not doc_link:
                    continue
                doc_no = doc_link.text.strip()
                if not doc_no or not re.match(r'^[A-Z0-9]+$', doc_no):
                    continue
                if doc_no in seen_docs:
                    continue

                inst_type = cells[5].text.strip() if len(cells) > 5 else ""
                grantor = cells[9].text.strip() if len(cells) > 9 else ""
                grantee = cells[10].text.strip() if len(cells) > 10 else ""
                location = cells[2].text.strip() if len(cells) > 2 else ""
                rec_date = cells[7].text.strip() if len(cells) > 7 else ""

                # Prefer deeds over mortgages/liens
                is_deed = any(kw in inst_type.lower() for kw in ['deed', 'warranty', 'quitclaim', 'grant', 'convey'])
                candidates.append({
                    "doc_no": doc_no,
                    "grantor": grantor,
                    "grantee": grantee,
                    "location": location,
                    "date": rec_date,
                    "instrument_type": inst_type,
                    "is_deed": is_deed,
                    "has_plat_ref": bool(plat_re.search(location + " " + grantor)),
                })

            if not candidates:
                stop_reason = f"No prior deeds found for {current_name}"
                break

            # Sort: deeds first, then by date descending (most recent first)
            candidates.sort(key=lambda c: (not c["is_deed"], c["date"]), reverse=False)
            best = candidates[0]

            seen_docs.add(best["doc_no"])
            chain.append(best)

            if best["has_plat_ref"]:
                stop_reason = f"Plat reference found in deed {best['doc_no']}"
                break

            # Continue chain with the grantor of this deed
            next_name = best["grantor"]
            if not next_name or next_name.lower() == current_name.lower():
                stop_reason = f"Grantor same as previous — chain ends at {best['doc_no']}"
                break

            current_name = next_name

        if not stop_reason and len(chain) >= max_hops:
            stop_reason = f"Reached maximum depth ({max_hops} hops)"

        return jsonify({
            "success": True,
            "chain": chain,
            "hops": len(chain),
            "stop_reason": stop_reason,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


# ── document detail ────────────────────────────────────────────────────────────

@app.route("/api/document/<doc_no>", methods=["GET", "POST"])
def api_document(doc_no):
    try:
        cfg = load_config()
        username = request.args.get("username") or cfg.get("username", "")

        # Accept an optional search_result passthrough from the frontend
        # so we can merge rich search row data with scrape results.
        search_row = {}
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            search_row = body.get("search_result", {})

        url = f"{BASE_URL}/scripts/hfpage.asp?Appl=FNM&Doctype=TP&DocNo={doc_no}&FormUser={username}"
        resp = web_session.get(url, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        detail = {"doc_no": doc_no}

        # ── Strategy 1: 2-column <td> tables (standard e.halFILE layout) ─────
        tables = soup.find_all("table")
        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) == 2:
                    label = cells[0].text.strip().rstrip(":")
                    value = cells[1].text.strip()
                    if label and value:
                        detail[label] = value

        # ── Strategy 2: <b> or <strong> label followed by text ───────────────
        # Some pages use patterns like: <b>Grantor:</b> SMITH, JOHN
        if len(detail) <= 1:   # only doc_no found so far
            for b in soup.find_all(["b", "strong"]):
                label = b.text.strip().rstrip(":")
                if not label or len(label) > 40:
                    continue
                # Get the next sibling text
                nxt = b.next_sibling
                if nxt and isinstance(nxt, str) and nxt.strip():
                    detail[label] = nxt.strip()

        # ── Strategy 3: pull all visible text as a raw dump for TRS ──────────
        page_text = soup.get_text(" ", strip=True)

        # ── Merge search_row data (always trust it for known fields) ──────────
        field_map = {
            "location":        "Location",
            "grantor":         "Grantor",
            "grantee":         "Grantee",
            "instrument_type": "Instrument Type",
            "recorded_date":   "Recorded Date",
            "instrument_date": "Instrument Date",
            "document_no":     "Document Number",
            "gf_number":       "GF Number",
            "document_code":   "Document Code",
        }
        for sr_key, detail_key in field_map.items():
            if search_row.get(sr_key) and not detail.get(detail_key):
                detail[detail_key] = search_row[sr_key]

        # ── PDF URL ───────────────────────────────────────────────────────────
        pdf_link = soup.find("a", string=re.compile(r"pdf all pages", re.I))
        if pdf_link:
            href = pdf_link.get("href", "")
            detail["pdf_url"] = (BASE_URL + "/" + href.lstrip("/")
                                 if not href.startswith("http") else href)
        else:
            detail["pdf_url"] = f"{BASE_URL}/WebTemp/{doc_no}.pdf"

        # ── TRS extraction ────────────────────────────────────────────────────
        all_text = page_text + " " + " ".join(str(v) for v in detail.values())
        trs_refs = extract_trs(all_text)
        if trs_refs:
            detail["_trs"] = trs_refs

        return jsonify({"success": True, "detail": detail})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})

# ── find adjoiners ─────────────────────────────────────────────────────────────

# _ADJ_PATTERNS, _NOISE_WORDS, parse_adjoiner_names — imported from helpers.adjoiner

def parse_adjoiner_names(detail: dict) -> list[dict]:
    """Delegates to helpers.adjoiner.parse_adjoiner_names."""
    return _parse_adjoiner_names_impl(detail)



def find_adjoiners_online(location: str, grantor: str) -> list[dict]:
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
        search_url = f"{BASE_URL}/scripts/hfweb.asp?Application=FNM&Database=TP"
        resp = web_session.get(search_url, timeout=12)
        if "FIELD14" not in resp.text and "CROSSNAMEFIELD" not in resp.text:
            return results  # not logged in

        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form")
        if not form:
            return results

        fd = {}
        for inp in form.find_all("input"):
            nm = inp.get("name")
            if nm: fd[nm] = inp.get("value", "")
        for sel in form.find_all("select"):
            nm = sel.get("name")
            if nm:
                opt = sel.find("option", selected=True) or sel.find("option")
                fd[nm] = opt["value"] if opt and opt.get("value") else ""

        # Search by location book prefix
        # FIELD14 is typically the "Location" search field on this site
        fd["FIELD14"]    = book + "-"
        fd["FIELD14TYPE"] = "begin"

        post = web_session.post(f"{BASE_URL}/scripts/hflook.asp", data=fd, timeout=20)
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

# _ocr_cache_path — imported from helpers.pdf_extract

def ocr_plat_file(pdf_path: str) -> list[str]:
    """Delegates to helpers.pdf_extract.ocr_plat_file."""
    return _ocr_plat_file_impl(pdf_path)



@app.route("/api/find-adjoiners", methods=["POST"])
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
        #   TUNING: A typical property has 6-8 adjoiners at most (sides + across road).
        #   We tighten thresholds and cap each sub-strategy to avoid returning 30+ parcels.
        MAX_KML_PER_SUBSTRATEGY = 10  # cap UPC + proximity each
        kml_upc_count = 0
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

                # Step A: find the client parcel by grantor last name + book/page
                client_parcel = None
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
                        print(f"[adjoiners][kml] client parcel: {p.get('owner')} UPC={p.get('upc')} centroid={p.get('centroid')}", flush=True)
                        break

                if client_parcel:
                    client_upc     = client_parcel.get("upc", "")
                    client_centroid = client_parcel.get("centroid")  # [lng, lat]

                    # Step B: UPC-prefix neighbors (same parcel group, adjacent numbers)
                    #   Tightened from ±20 → ±5 — only immediate neighboring lot numbers
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
                                    if diff <= 5:   # within 5 UPC steps = immediate neighbors
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

                    # Step C: centroid proximity — only touching parcels
                    #   Tightened from 0.0015° (~167m) → 0.0008° (~89m)
                    #   A typical rural NM lot is ~60-80m across, so 89m catches
                    #   immediate neighbors but not parcels 2+ lots away.
                    if client_centroid:
                        clng, clat = client_centroid
                        RADIUS_DEG = 0.0008   # ~89 m — touching parcels only
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
            online = find_adjoiners_online(location, grantor)
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
                "kml_upc": 3, "kml_proximity": 4, "online_range": 5,
            }
            results.sort(key=lambda r: SOURCE_PRIORITY.get(r.get("source", ""), 9))
            results = results[:MAX_ADJOINERS]

        # Log breakdown by source for debugging
        from collections import Counter
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

# _extract_plat_name_tokens — imported from helpers.cabinet
# parse_cabinet_refs — imported from helpers.cabinet




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



# _extract_cabinet_display_name, _extract_cabinet_doc_number — imported from helpers.cabinet
# _cab_scan_cache — imported from helpers.cabinet

def search_local_cabinet(cabinet: str, doc_num: str,
                          grantor: str = "", grantee: str = "") -> list[dict]:
    """Delegates to helpers.cabinet.search_local_cabinet, injecting the cabinet path."""
    return _search_local_cabinet_impl(
        cabinet, doc_num, cabinet_path=get_cabinet_path(),
        grantor=grantor, grantee=grantee
    )



@app.route("/api/find-plat", methods=["POST"])
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


@app.route("/api/find-plat-kml", methods=["POST"])
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
        kml_matches = []
        idx = xml_processor._cached_index
        if idx is None:
            survey = get_survey_data_path()
            idx    = xml_processor.load_index(survey)
        if idx:
            survey = get_survey_data_path()
            # If we have a deed, use the full cross-reference (grantor/grantee/book/page/cab)
            if detail and (detail.get("Grantor") or detail.get("Grantee") or detail.get("Location")):
                kml_results = xml_processor.cross_reference_deed(survey, detail)
            elif client_name:
                # No deed yet — search by client name (owner contains last name)
                last_name = client_name.split(",")[0].strip().upper()
                if len(last_name) >= 2:
                    raw = xml_processor.search_parcels_in_index(idx, owner=last_name, operator="contains", limit=15)
                    for p in raw:
                        p["_match_reason"] = f"Client name: {client_name}"
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


@app.route("/api/find-plat-local", methods=["POST"])
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
        print(f"[local] {targeting_reason}", flush=True)


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

            # b) KML owner name — the current owner whose name IS on cabinet files.
            #    e.g. KML parcel owner = "ADELA RAEL" → matches "Rael Adela.pdf"
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
                                h["strategy"] = "kml_cab_ref"   # top-priority display
                                h["_tok_len"] = len(tok) + 190
                                local_hits.append(h)
                    except Exception:
                        pass

            # b2) KML PLAT field name tokens — secondary name-based strategy.
            #     Cabinet filenames (e.g. "Rael Adela.pdf") do NOT contain
            #     cabinet refs, so we strip the ref prefix from the KML PLAT
            #     string and use the remaining name for matching.
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
                                h["strategy"] = "kml_cab_ref"   # top-priority display
                                h["_tok_len"] = len(tok) + 180  # rank below owner hits
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

        # ── Sort: doc_number → kml_cab_ref → deed_cab_ref → client_name → prior_owner → name_match ─
        # doc_number = exact plat doc number match from file's leading number (highest confidence)
        strategy_order = {"doc_number": 0, "kml_cab_ref": 1, "deed_cab_ref": 2,
                          "client_name": 3, "prior_owner": 4, "name_match": 5, "page_ref": 6}
        local_hits.sort(key=lambda r: (
            strategy_order.get(r.get("strategy", ""), 9),
            -(r.get("_tok_len") or 0)
        ))

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



@app.route("/api/find-plat-online", methods=["POST"])
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
                search_url = f"{BASE_URL}/scripts/hfweb.asp?Application=FNM&Database=TP"
                resp = web_session.get(search_url, timeout=8)
                if "CROSSNAMEFIELD" not in resp.text and "FIELD14" not in resp.text:
                    return  # not logged in
                soup = BeautifulSoup(resp.text, "html.parser")
                form = soup.find("form")
                if not form:
                    return
                fd = {}
                for inp in form.find_all("input"):
                    nm = inp.get("name")
                    if nm:
                        fd[nm] = inp.get("value", "")
                for sel in form.find_all("select"):
                    nm = sel.get("name")
                    if nm:
                        opt = sel.find("option", selected=True) or sel.find("option")
                        fd[nm] = opt["value"] if opt and opt.get("value") else ""
                fd["CROSSNAMEFIELD"] = name_last
                fd["CROSSNAMETYPE"]  = "begin"
                # NOTE: Do NOT set FIELD7="SUR" here — it conflicts with the form
                # and may suppress all results. Instrument-type filtering is done
                # by the regex below after results are returned.
                post  = web_session.post(f"{BASE_URL}/scripts/hflook.asp", data=fd, timeout=10)
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
                        "pdf_url":         f"{BASE_URL}/WebTemp/{doc_no}.pdf",
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


# ── save plat to project folder ────────────────────────────────────────────────

@app.route("/api/save-plat", methods=["POST"])
def api_save_plat():
    """Copy a local cabinet file OR download an online plat PDF into the project's B Plats folder."""
    import shutil
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

        plats_root = (_job_base_path(job_number, client_name, job_type)
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
            pdf_url  = data.get("pdf_url", f"{BASE_URL}/WebTemp/{doc_no}.pdf")
            pdf_resp = web_session.get(pdf_url, stream=True, timeout=30)
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


# ── PDF preview for plats ──────────────────────────────────────────────────────

@app.route("/api/preview-pdf", methods=["GET", "POST"])
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
    drive = detect_survey_drive()
    allowed_roots = [Path(drive + ":\\") if drive else None]
    if allowed_roots[0] is None:
        allowed_roots = []
    # Also allow project directories (job folders)
    try:
        resolved = p.resolve()
    except Exception:
        return jsonify({"success": False, "error": "Invalid path"}), 400

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
        # Render at 200 DPI for good quality without being too slow
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("jpeg", jpg_quality=85)
        doc.close()

        response = make_response(img_bytes)
        response.headers["Content-Type"] = "image/jpeg"
        response.headers["Cache-Control"] = "public, max-age=3600"
        response.headers["X-Page-Count"] = str(doc.page_count if hasattr(doc, 'page_count') else 1)
        return response

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Failed to render PDF: {str(e)}"}), 500


# ── next job number ────────────────────────────────────────────────────────────

@app.route("/api/next-job-number")
def api_next_job():
    num, range_folder = next_job_info()
    return jsonify({"next_job_number": num, "range_folder": range_folder})

# ── create project ─────────────────────────────────────────────────────────────

@app.route("/api/create-project", methods=["POST"])
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

# ── download deed PDF ──────────────────────────────────────────────────────────

@app.route("/api/download", methods=["POST"])
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
            deeds_path = str(_job_base_path(job_number, client_name, job_type)
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
        pdf_url  = f"{BASE_URL}/WebTemp/{doc_no}.pdf"
        pdf_resp = web_session.get(pdf_url, stream=True, timeout=30)
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


# ── drive status ────────────────────────────────────────────────────────────────

@app.route("/api/drive-status")
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


@app.route("/api/drive-override", methods=["POST"])
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


# ── research session ───────────────────────────────────────────────────────────

@app.route("/api/research-session", methods=["GET"])
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


@app.route("/api/research-session", methods=["POST"])
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

# ── open folder in Explorer ────────────────────────────────────────────────────

@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    try:
        path = request.get_json().get("path", "")
        if path and os.path.exists(path):
            subprocess.Popen(["explorer", path])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/open-file", methods=["POST"])
def api_open_file():
    """Open a specific file with the default Windows application."""
    try:
        path = request.get_json().get("path", "")
        if path and os.path.exists(path):
            os.startfile(path)
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "File not found"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/recent-jobs")
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


@app.route("/api/export-session", methods=["POST"])
def api_export_session():
    """Generate a plain-text research summary from the current session."""
    try:
        body = request.get_json()
        rs   = body.get("session", {})
        if not rs:
            return jsonify({"success": False, "error": "No session provided"})

        lines = [
            f"DEED & PLAT RESEARCH SUMMARY",
            f"{'='*50}",
            f"Job #:    {rs.get('job_number')}",
            f"Client:   {rs.get('client_name')}",
            f"Type:     {rs.get('job_type')}",
            f"",
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


# (api_chain_search removed — callers now do `grantor.split(',')[0].strip()` locally)


# ══════════════════════════════════════════════════════════════════════════════
# BOUNDARY LINES & DXF GENERATION
# ══════════════════════════════════════════════════════════════════════════════

# All metes-and-bounds parsing functions are imported from helpers.metes_bounds





# ── DXF generator ─────────────────────────────────────────────────────────────

def _ensure_dwg_folder(job_number, client_name, job_type) -> Path:
    """Return (and create) the B Drafting/dwg folder for the job."""
    dwg_dir = _job_base_path(job_number, client_name, job_type) / "B Drafting" / "dwg"
    dwg_dir.mkdir(parents=True, exist_ok=True)
    return dwg_dir


def generate_boundary_dxf(
    parcels:     list[dict],
    job_number,
    client_name: str,
    job_type:    str,
    options:     dict = None,
) -> tuple[str, list[dict]]:
    """Delegates to helpers.dxf.generate_boundary_dxf, resolving output_dir."""
    dwg_dir = _ensure_dwg_folder(job_number, client_name, job_type)
    return _generate_dxf_impl(parcels, dwg_dir, job_number, client_name, job_type, options)



# ── /api/parse-calls ──────────────────────────────────────────────────────────

@app.route("/api/parse-calls", methods=["POST"])
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



# ── /api/extract-calls-from-pdf ───────────────────────────────────────────────

@app.route("/api/extract-calls-from-pdf", methods=["POST"])
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


# ── /api/generate-dxf ─────────────────────────────────────────────────────────

@app.route("/api/generate-dxf", methods=["POST"])
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


# ── cabinet browser ────────────────────────────────────────────────────────────

@app.route("/api/cabinet-browse")
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


# ══════════════════════════════════════════════════════════════════════════════
# XML / KML PARCEL DATA
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/xml/status")
def api_xml_status():
    """Return status of the parcel index (exists, record count, age, source files)."""
    try:
        survey = get_survey_data_path()
        status = xml_processor.index_status(survey)
        return jsonify({"success": True, **status})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/xml/build-index", methods=["POST"])
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


@app.route("/api/xml/search", methods=["POST"])
def api_xml_search():
    """
    Search parcel index by owner name, UPC, book/page, or cabinet reference.

    Body: { owner, upc, book, page, cabinet_ref, operator, limit }
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
            operator=data.get("operator", "contains"),
            limit=int(data.get("limit", 50)),
        )
        return jsonify({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/xml/parcel/<upc>")
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


@app.route("/api/xml/cross-reference", methods=["POST"])
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


# ── /api/parcel-search  (Step 1 Property Picker) ─────────────────────────────

@app.route("/api/parcel-search", methods=["POST"])
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


# ── /api/xml/map-geojson  (Leaflet map data) ─────────────────────────────────

@app.route("/api/xml/map-geojson", methods=["POST"])
def api_xml_map_geojson():
    """
    Return a GeoJSON FeatureCollection for Leaflet rendering.
    Body: { highlight_upcs: [str], max_features: int }
    Response is gzip-compressed when the client accepts it (browsers always do).
    """
    try:
        data           = request.get_json() or {}
        highlight_upcs = data.get("highlight_upcs", [])
        max_features   = int(data.get("max_features", 100000))
        source_filter  = data.get("source_filter", "")

        survey  = get_survey_data_path()
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



# ══════════════════════════════════════════════════════════════════════════════
# PROPERTY ADDRESS LOOKUP  (ArcGIS + Nominatim fallback)
# ══════════════════════════════════════════════════════════════════════════════

# In-memory cache: { "upc:12345" or "ll:lat,lon" : { address dict } }
_address_cache: dict = {}
_nominatim_last_call: float = 0.0   # monotonic timestamp of last Nominatim call

# ── NM State ArcGIS Parcel Service (primary) ──────────────────────────────────
# Free, no API key needed, returns official situs addresses tied to UPC
ARCGIS_TAOS_QUERY_URL = (
    "https://gis.ose.nm.gov/server_s/rest/services/"
    "Parcels/County_Parcels_2025/MapServer/29/query"
)
ARCGIS_OUT_FIELDS = (
    "UPC,OwnerAll,SitusAddressAll,SitusAddress1,SitusAddress2,"
    "SitusStreetNumber,SitusStreetName,SitusCity,SitusZipCode,"
    "LegalDescription,LandArea"
)

# ── Nominatim (fallback) ─────────────────────────────────────────────────────
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_HEADERS = {
    "User-Agent": "DeedPlatHelper/1.0 (land-survey-research-tool)",
    "Accept": "application/json",
}


def _arcgis_lookup_upc(upc: str) -> dict:
    """Query the NM ArcGIS parcel service by UPC.

    Returns a dict with address fields, or None on failure.
    """
    if not upc:
        return None

    cache_key = f"upc:{upc}"
    if cache_key in _address_cache:
        return _address_cache[cache_key]

    try:
        resp = req_lib.get(
            ARCGIS_TAOS_QUERY_URL,
            params={
                "where":            f"UPC='{upc}'",
                "outFields":        ARCGIS_OUT_FIELDS,
                "returnGeometry":   "false",
                "f":                "json",
            },
            headers={"User-Agent": "DeedPlatHelper/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[address] ArcGIS returned {resp.status_code} for UPC {upc}", flush=True)
            return None

        data = resp.json()
        features = data.get("features", [])
        if not features:
            print(f"[address] ArcGIS: no features for UPC {upc}", flush=True)
            return None

        attrs = features[0].get("attributes", {})

        # Build short_address from official situs fields
        situs_all = (attrs.get("SitusAddressAll") or "").strip()
        situs1    = (attrs.get("SitusAddress1") or "").strip()
        street_no = (attrs.get("SitusStreetNumber") or "").strip()
        street_nm = (attrs.get("SitusStreetName") or "").strip()
        city      = (attrs.get("SitusCity") or "").strip()
        zipcode   = (attrs.get("SitusZipCode") or "").strip()

        # Prefer the most complete address representation
        if situs1:
            short_addr = situs1
            if city:
                short_addr += f", {city}"
        elif street_no and street_nm:
            short_addr = f"{street_no} {street_nm}"
            if city:
                short_addr += f", {city}"
        elif situs_all and situs_all != zipcode:
            short_addr = situs_all
        else:
            short_addr = ""   # no usable street address

        result = {
            "success":          True,
            "source":           "arcgis",
            "short_address":    short_addr or "(no street address on file)",
            "situs_full":       situs_all,
            "situs_address1":   situs1,
            "street_number":    street_no,
            "street_name":      street_nm,
            "city":             city,
            "zipcode":          zipcode,
            "owner_official":   (attrs.get("OwnerAll") or "").strip(),
            "legal_description": (attrs.get("LegalDescription") or "").strip(),
            "land_area":        (attrs.get("LandArea") or ""),
            "upc":              upc,
            "has_street_address": bool(situs1 or (street_no and street_nm)),
        }

        _address_cache[cache_key] = result
        print(f"[address] ArcGIS UPC {upc} → {result['short_address']}", flush=True)
        return result

    except Exception as e:
        print(f"[address] ArcGIS error for UPC {upc}: {e}", flush=True)
        return None


def _nominatim_reverse(lat: float, lon: float) -> dict:
    """Call Nominatim reverse-geocode for a single lat/lon pair.

    Returns a dict with address fields, or an error dict.
    Enforces 1-second rate limiting and caches results.
    """
    import time as _time
    global _nominatim_last_call

    cache_key = f"ll:{round(lat, 5)},{round(lon, 5)}"
    if cache_key in _address_cache:
        return _address_cache[cache_key]

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

        _address_cache[cache_key] = result
        print(f"[address] Nominatim {lat},{lon} → {result['short_address']}", flush=True)
        return result

    except Exception as e:
        _nominatim_last_call = _time.monotonic() if '_time' in dir() else 0
        print(f"[address] Nominatim error for {lat},{lon}: {e}", flush=True)
        return {"success": False, "source": "nominatim", "error": str(e)}


@app.route("/api/property-address", methods=["POST"])
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
        if upc:
            result = _arcgis_lookup_upc(upc)
            if result and result.get("success"):
                return jsonify(result)

        # Strategy 2: Nominatim reverse geocode from coordinates (fallback)
        if lat != 0 or lon != 0:
            result = _nominatim_reverse(lat, lon)
            return jsonify(result)

        return jsonify({"success": False, "error": "No UPC or coordinates provided"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/batch-property-address", methods=["POST"])
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
                result = _arcgis_lookup_upc(upc)
            if not result or not result.get("success"):
                if lat != 0 or lon != 0:
                    result = _nominatim_reverse(lat, lon)
            if not result:
                result = {"success": False, "error": "No UPC or coordinates"}
            results.append(result)

        return jsonify({"success": True, "results": results})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# DEEP DEED ANALYSIS & HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════


def analyze_deed(detail: dict, pdf_path: str = "") -> dict:
    """Delegates to helpers.deed_analysis.analyze_deed."""
    return _analyze_deed_impl(detail, pdf_path, pdf_extractor=_extract_pdf_text)




@app.route("/api/extract-deed-description", methods=["POST"])
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
                pdf_url  = f"{BASE_URL}/WebTemp/{doc_no}.pdf"
                pdf_resp = web_session.get(pdf_url, stream=True, timeout=20)
                if pdf_resp.status_code == 200:
                    import tempfile
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


def _isolate_legal_description(text: str) -> str:
    """Delegates to helpers.deed_analysis.isolate_legal_description."""
    return _isolate_legal_description_impl(text)


@app.route("/api/analyze-deed", methods=["POST"])
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


# ── run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Deed & Plat Helper  —  http://localhost:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
