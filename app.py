from flask import Flask, request, jsonify, send_from_directory, Response
import requests as req_lib
from bs4 import BeautifulSoup
import os, re, json, traceback, subprocess, gzip
from pathlib import Path
import fitz          # PyMuPDF  — PDF → image
import pytesseract
from PIL import Image
import io
import xml_processor

# Point pytesseract at the Tesseract binary
# Auto-detect Tesseract in common locations
def _find_tesseract():
    """Locate the Tesseract binary. Checks standard install paths first, then PATH."""
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # Try PATH (covers custom installs, Linux/Mac, conda environments)
    import shutil
    found = shutil.which("tesseract")
    return found or candidates[0]  # fall back to default path even if missing

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()

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


# Cabinet folder name mapping  (letter → folder name on disk)
CABINET_FOLDERS = {
    "A": "Cabinet A",
    "B": "Cabinet B",
    "C": "Cabinet C",
    "D": "Cabinet D",
    "E": "Cabinet E",
    "F": "Cabinet F (from RGSS scans & 1st NM website)",
}
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
    survey = Path(get_survey_data_path())
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


def _research_path(job_number, client_name, job_type) -> Path:
    rstart = (int(job_number) // 100) * 100
    last_name = client_name.split(",")[0].strip()
    return (
        Path(get_survey_data_path())
        / f"{rstart}-{rstart + 99}"
        / f"{job_number} {client_name}"
        / f"{job_number}-01-{job_type} {last_name}"
        / "E Research" / "research.json"
    )


def load_research(job_number, client_name, job_type) -> dict:
    p = _research_path(job_number, client_name, job_type)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Default skeleton — includes new fields: status, notes, saved_path
    return {
        "job_number": job_number,
        "client_name": client_name,
        "job_type": job_type,
        "subjects": [
            {"id": "client", "type": "client", "name": client_name,
             "deed_saved": False, "plat_saved": False,
             "status": "pending", "notes": "",
             "deed_path": "", "plat_path": ""}
        ]
    }


def save_research(job_number, client_name, job_type, data: dict):
    p = _research_path(job_number, client_name, job_type)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# ── static ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    from flask import make_response
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

# ── TRS detection ──────────────────────────────────────────────────────────────

def extract_trs(text: str) -> list[dict]:
    """
    Extract Township / Range / Section references from deed text.
    Handles: T5N R5E S12, T 5 N R 5 E Sec 12, etc.
    Returns list of {trs, township, range, section} dicts.
    """
    pat = re.compile(
        r'\bT\.?\s*(\d+)\s*([NS])\b'
        r'[\s,]*'
        r'\bR\.?\s*(\d+)\s*([EW])\b'
        r'(?:[\s,]*\bSec(?:tion)?\.?\s*(\d+)\b)?',
        re.I
    )
    results = []
    seen = set()
    for m in pat.finditer(text):
        t_num = m.group(1); t_dir = m.group(2).upper()
        r_num = m.group(3); r_dir = m.group(4).upper()
        sec   = m.group(5) or ""
        trs   = f"T{t_num}{t_dir} R{r_num}{r_dir}" + (f" S{sec}" if sec else "")
        if trs not in seen:
            seen.add(trs)
            results.append({"trs": trs, "township": f"T{t_num}{t_dir}",
                            "range": f"R{r_num}{r_dir}", "section": sec})
    return results


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

# Common New Mexico legal description patterns that name adjoining owners
_ADJ_PATTERNS = [
    # "LANDS OF RAEL, CARLOS A"  /  "LAND OF GARCIA"
    re.compile(
        r'\blands?\s+of\s+(?:the\s+(?:heirs?\s+of\s+)?)?'
        r'([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence|and\s+on|to\s+a)|$)',
        re.I | re.MULTILINE
    ),
    # "PROPERTY OF GARCIA, JUAN"
    re.compile(
        r'\bproperty\s+of\s+([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence)|$)',
        re.I | re.MULTILINE
    ),
    # "ADJOINS GARCIA, JUAN"
    re.compile(
        r'\badjoins?\s+([A-Z][A-Z\s,\.\'-]{2,40}?)(?=\s*[,;]|\s+(?:on|bounded|thence)|$)',
        re.I | re.MULTILINE
    ),
]

_NOISE_WORDS = {
    'the', 'said', 'above', 'herein', 'grantor', 'grantee', 'county', 'state',
    'new', 'mexico', 'united', 'states', 'government', 'public', 'road',
    'street', 'acequia', 'ditch', 'right', 'way', 'river', 'creek', 'arroyo',
    'unknown', 'parties', 'record', 'described', 'following', 'certain',
}


def parse_adjoiner_names(detail: dict) -> list[dict]:
    """
    Scan all text fields in the deed detail for 'Lands of [Name]' patterns.
    Returns list of {name, raw, field} dicts, de-duplicated.
    """
    found = []
    seen  = set()

    # Fields most likely to contain legal description text
    priority_fields = [
        "Other_Legal", "Subdivision_Legal", "Comments",
        "Reference", "Legal Description", "Legal", "Description",
    ]
    # Build ordered list: priority first, then everything else
    all_fields = priority_fields + [k for k in detail if k not in priority_fields]

    for field in all_fields:
        val = detail.get(field, "")
        if not val or not isinstance(val, str):
            continue
        for pat in _ADJ_PATTERNS:
            for m in pat.finditer(val):
                raw  = m.group(0).strip()
                name = m.group(1).strip().rstrip(".,;")
                # Clean: collapse whitespace, title-case
                name = re.sub(r'\s+', ' ', name).title()
                name_key = name.lower()
                # Filter noise
                if any(w in name_key for w in _NOISE_WORDS):
                    continue
                if len(name) < 3 or name_key in seen:
                    continue
                seen.add(name_key)
                found.append({"name": name, "raw": raw, "field": field, "source": "legal_desc"})

    return found


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
                if len(results) >= 20:
                    break
            if len(results) >= 20:
                break
    except Exception:
        pass  # online search is best-effort

    return results


def _ocr_cache_path(pdf_path: str) -> Path:
    return Path(pdf_path).with_suffix(".ocr.json")


def ocr_plat_file(pdf_path: str) -> list[str]:
    """
    OCR a plat PDF and return a de-duplicated list of adjoiner name strings.

    Pipeline:
      1. Check for a cached .ocr.json result alongside the PDF — return instantly if found.
      2. Render each page at 250 DPI with PyMuPDF.
      3. Pre-process with PIL: grayscale → contrast boost → Otsu binarization.
         This dramatically improves Tesseract accuracy on old/yellowed scans.
      4. Run Tesseract with --oem 3 --psm 6 (assume uniform block of text).
      5. Parse combined text for "Lands of / Property of / Adjoins [Name]" patterns.
      6. Write cache file so future calls are instant.
    """
    from PIL import ImageEnhance, ImageFilter

    cache = _ocr_cache_path(pdf_path)
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            return cached.get("names", [])
        except Exception:
            pass

    try:
        doc       = fitz.open(pdf_path)
        full_text = ""
        for page in doc:
            pix = page.get_pixmap(dpi=250)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")  # grayscale

            # Boost contrast then binarize (helps with faded/yellowed scans)
            img = ImageEnhance.Contrast(img).enhance(2.0)
            # Simple threshold at 128 — Otsu-style via point()
            img = img.point(lambda x: 255 if x > 128 else 0, "1")

            text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
            full_text += text + "\n"
        doc.close()
    except Exception as e:
        print(f"[OCR] Failed to read {pdf_path}: {e}")
        return []

    found    = []
    seen     = set()
    patterns = [
        re.compile(
            r"\blands?\s+of\s+(?:the\s+(?:heirs?\s+of\s+)?)?([A-Z][A-Za-z'\-]+(?:[,\s]+[A-Za-z'\-]+){0,4})",
            re.I
        ),
        re.compile(r"\bproperty\s+of\s+([A-Z][A-Za-z'\-]+(?:[,\s]+[A-Za-z'\-]+){0,3})", re.I),
        re.compile(r"\badjoins?\s+([A-Z][A-Za-z'\-]+(?:[,\s]+[A-Za-z'\-]+){0,3})", re.I),
    ]
    noise = {
        "the", "said", "above", "grantor", "grantee", "new", "mexico",
        "county", "state", "united", "states", "government", "public",
        "road", "street", "acequia", "ditch", "river", "creek", "arroyo",
        "forest", "national", "carson", "section", "township", "range",
        "unknown", "parties", "record", "boundary", "corner", "tract",
        "survey", "plat", "map", "parcel", "lot", "block",
    }
    for pat in patterns:
        for m in pat.finditer(full_text):
            raw  = m.group(1).strip().rstrip(".,;:")
            raw  = re.sub(r"\s+", " ", raw)
            name = raw.title()
            key  = name.lower()
            first_word = key.split()[0] if key.split() else ""
            if first_word in noise or len(name) < 4 or key in seen:
                continue
            seen.add(key)
            found.append(name)

    # Write cache
    try:
        cache.write_text(
            json.dumps({"names": found, "source": str(pdf_path)}, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass

    return found



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
                deed_pdf  = fitz.open(deed_path)
                deed_text = ""
                # Try native text layer first (fast — works for digital PDFs)
                for page in deed_pdf:
                    deed_text += page.get_text("text") + "\n"
                char_count = len(deed_text.strip())
                print(f"[adjoiners] deed text layer chars: {char_count}", flush=True)
                # If text layer is empty/sparse, fall back to Tesseract OCR
                if char_count < 80:
                    print("[adjoiners] text layer sparse — running OCR on deed", flush=True)
                    from PIL import ImageEnhance
                    deed_text = ""
                    for page in deed_pdf:
                        pix = page.get_pixmap(dpi=200)
                        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
                        img = ImageEnhance.Contrast(img).enhance(1.8)
                        img = img.point(lambda x: 255 if x > 128 else 0, "1")
                        deed_text += pytesseract.image_to_string(img, config="--oem 3 --psm 6") + "\n"
                    print(f"[adjoiners] deed OCR chars: {len(deed_text.strip())}", flush=True)
                deed_pdf.close()
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
                    if client_upc:
                        # Taos UPCs look like "1234567890123" — prefix is first 10 chars
                        upc_prefix = client_upc[:10]
                        try:
                            upc_num = int(client_upc)
                            for p in parcels:
                                p_upc = p.get("upc", "")
                                if not p_upc or p_upc == client_upc:
                                    continue
                                if not p_upc.startswith(upc_prefix):
                                    continue
                                try:
                                    diff = abs(int(p_upc) - upc_num)
                                    if diff <= 20:   # within 20 UPC steps = likely adjacent
                                        name = p.get("owner", "").title()
                                        key  = name.lower()
                                        if name and key not in seen_names:
                                            seen_names.add(key)
                                            results.append({
                                                "name":   name,
                                                "raw":    p_upc,
                                                "field":  "KML UPC neighbor",
                                                "source": "kml_upc",
                                                "upc":    p_upc,
                                                "plat":   p.get("plat", ""),
                                            })
                                except ValueError:
                                    pass
                        except ValueError:
                            pass

                    # Step C: centroid proximity (~300 m radius)
                    if client_centroid:
                        clng, clat = client_centroid
                        # 1 degree lat ≈ 111 km → 0.0015° ≈ 167 m (touching parcels only)
                        RADIUS_DEG = 0.0015
                        for p in parcels:
                            pc = p.get("centroid")
                            if not pc or p.get("upc") == client_upc:
                                continue
                            dlng = abs(pc[0] - clng)
                            dlat = abs(pc[1] - clat)
                            if dlng < RADIUS_DEG and dlat < RADIUS_DEG:
                                name = p.get("owner", "").title()
                                key  = name.lower()
                                if name and key not in seen_names:
                                    seen_names.add(key)
                                    results.append({
                                        "name":   name,
                                        "raw":    f"{pc[1]:.5f},{pc[0]:.5f}",
                                        "field":  "KML proximity",
                                        "source": "kml_proximity",
                                        "upc":    p.get("upc", ""),
                                        "plat":   p.get("plat", ""),
                                    })
                else:
                    print(f"[adjoiners][kml] no client parcel found for grantor={grantor_last!r} book={book_num!r}", flush=True)
            else:
                print("[adjoiners][kml] index not loaded (build it first)", flush=True)
        except Exception as kml_err:
            print(f"[adjoiners][kml] error: {kml_err}", flush=True)

        # ── Strategy 4: online location-range search (supplement) ───────────────
        print(f"[adjoiners] online search: location={location!r} grantor={grantor!r}", flush=True)
        online = find_adjoiners_online(location, grantor)
        for om in online:
            if om["name"].lower() not in seen_names:
                results.append(om)
                seen_names.add(om["name"].lower())

        print(f"[adjoiners] total found: {len(results)}", flush=True)
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


def _extract_plat_name_tokens(plat_str: str) -> list[str]:
    """Extract searchable name tokens from a KML PLAT field.

    Removes the cabinet reference prefix and returns individual name strings
    so that search_local_cabinet can do name-based file matching.

    Examples::
      'C-191-A ADELA RAEL'        -> ['ADELA RAEL', 'ADELA', 'RAEL']
      'CAB C-84-B TORRES, GARCIA' -> ['TORRES, GARCIA', 'TORRES', 'GARCIA']
    """
    if not plat_str:
        return []
    # Strip leading cabinet ref (CAB. X-NNN-A or X-NNN-A format)
    name_part = re.sub(
        r'(?:CAB(?:INET)?\.?\s*)?[A-Fa-f]\s*-\s*\d{1,4}(?:-[A-Za-z])?\s*',
        '', plat_str, count=1, flags=re.I
    ).strip()
    if not name_part or len(name_part) < 3:
        return []
    tokens = []
    tokens.append(name_part)   # full name as substring (e.g. 'ADELA RAEL')
    # Also add last-name portion (before comma if present)
    last = name_part.split(',')[0].strip()
    if last and last != name_part and len(last) >= 3:
        tokens.append(last)
    # Add individual words >= 4 chars, excluding noise words
    _NOISE = {'AND', 'THE', 'DEL', 'LOS', 'LAS', 'DES', 'EST', 'CORP', 'LLC'}
    for word in re.split(r'[\s,;&]+', name_part):
        w = word.strip()
        if len(w) >= 4 and w.upper() not in _NOISE and w not in tokens:
            tokens.append(w)
    return tokens

# ── find plat ──────────────────────────────────────────────────────────────────


def parse_cabinet_refs(detail: dict) -> list[dict]:
    """
    Extract every cabinet reference from any field in the deed detail.
    Handles both long form (CAB C-191A) and short form (C-191-A / C-191A).
    Returns list of {"cabinet": "C", "doc": "191A", "raw": "..."}.
    """
    refs = []
    seen = set()
    # Long form:  CAB C-191A  /  Cabinet C-191  /  CAB. F-5B
    pat_long = re.compile(r'\bCAB(?:INET)?[\s.]?([A-Fa-f])\s*[-–]\s*(\d+[A-Za-z]?)\b', re.I)
    # Short form: C-191-A  /  C-191A  (standalone, not part of a longer word)
    pat_short = re.compile(r'(?<![A-Za-z0-9])([A-Fa-f])[-–](\d{1,4})[-.–]?([A-Za-z]?)(?![A-Za-z0-9])')
    for val in detail.values():
        text = str(val)
        for m in pat_long.finditer(text):
            cab = m.group(1).upper()
            doc = m.group(2).upper()
            key = f"{cab}-{doc}"
            if key not in seen:
                seen.add(key)
                refs.append({"cabinet": cab, "doc": doc, "raw": m.group(0)})
        for m in pat_short.finditer(text):
            cab = m.group(1).upper()
            num = m.group(2)
            suffix = m.group(3).upper()
            doc = num + suffix  # e.g. "191A"
            key = f"{cab}-{doc}"
            if key not in seen:
                seen.add(key)
                refs.append({"cabinet": cab, "doc": doc, "raw": m.group(0)})
    return refs


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



def _extract_cabinet_display_name(filename: str) -> str:
    """
    Strip the leading numeric index prefix from a cabinet filename to expose
    just the owner name portion, which is the only meaningful part.

    Filename pattern:  195554.001   Adela Rael.PDF
                       ^^^^^^^^^^   ^^^^^^^^^^^^^^^
                       file index   owner name  ← this is what we want

    Examples:
      '195554.001   Adela Rael.PDF'  → 'Adela Rael'
      '100191.001 Rael Adela.pdf'    → 'Rael Adela'
      '003721 Torres C-191A.pdf'     → 'Torres C-191A'
      'Rael Adela.PDF'               → 'Rael Adela'   (no prefix — unchanged)
    """
    # Strip extension
    stem = Path(filename).stem.strip()
    # Remove leading numeric block:  digits, optional dots/digits, optional spaces
    # Pattern: one or more digit groups separated by dots (e.g. 195554.001 or 003721)
    clean = re.sub(r'^\d+(?:\.\d+)?\s+', '', stem).strip()
    return clean or stem   # fall back to full stem if nothing left


def search_local_cabinet(cabinet: str, doc_num: str,
                          grantor: str = "", grantee: str = "") -> list[dict]:
    """
    Walk the cabinet folder and return files matching either an owner name
    or a cabinet page-ref (e.g. C-191A embedded in the filename).

    Cabinet files follow the naming convention:
        195554.001   Adela Rael.PDF
                     ^^^^^^^^^^^^^^  ← owner name (only relevant part)

    PRIMARY  — Name-based:  token appears in the filename's name portion.
               e.g. "Rael"     matches "195554.001   Adela Rael.PDF"
               e.g. "Adela Rael" matches "195554.001   Adela Rael.PDF"
    SECONDARY — Page-ref:   cabinet letter + doc_num found in filename,
               e.g. "C-191A" matches "L3721 Torres C-191A.pdf".

    At least one of doc_num or grantor/grantee must be provided.
    """
    folder_name = CABINET_FOLDERS.get(cabinet)
    if not folder_name:
        return []
    cab_dir = Path(get_cabinet_path()) / folder_name
    if not cab_dir.exists():
        return []

    results   = []
    doc_clean = (doc_num or "").strip()

    # Pattern for cabinet page-ref embedded in filename, e.g. "C-191A" or "C 191A"
    page_ref_pat = re.compile(
        r'(?<![A-Za-z])' + re.escape(cabinet) + r'[\-\s]?' + re.escape(doc_clean) + r'(?![A-Za-z0-9])',
        re.I
    ) if doc_clean else None

    # Name tokens — accept either "last" from "Last, First" format OR full raw token
    name_tokens = []
    for person in [grantor, grantee]:
        if not person:
            continue
        person = person.strip()
        # Add the full token (handles "Adela Rael", "RAEL UNITY LLC", etc.)
        if len(person) >= 3 and person.lower() not in [t.lower() for t in name_tokens]:
            name_tokens.append(person)
        # Also add last-name portion from "Last, First" format
        last = person.split(',')[0].strip()
        if last and last != person and len(last) >= 3 and last.lower() not in [t.lower() for t in name_tokens]:
            name_tokens.append(last)
        # Add individual words >= 4 chars
        for w in re.split(r'[\s,]+', person):
            if len(w) >= 4 and w.lower() not in [t.lower() for t in name_tokens]:
                name_tokens.append(w)

    if not doc_clean and not name_tokens:
        return []   # nothing to search with

    for f in cab_dir.iterdir():
        if not f.is_file() or f.suffix.lower() not in ('.pdf',):
            continue
        fname        = f.name
        fname_lower  = fname.lower()
        display_name = _extract_cabinet_display_name(fname)
        name_lower   = display_name.lower()   # just the owner-name portion

        match_strategy = ""
        tok_len        = 0

        # PRIMARY: token appears anywhere in filename (both full and name-stripped)
        for tok in name_tokens:
            tok_l = tok.lower()
            if tok_l in fname_lower or tok_l in name_lower:
                match_strategy = "name_match"
                tok_len = len(tok)
                break

        # SECONDARY: cabinet page-ref like "C-191A" in filename
        if not match_strategy and page_ref_pat and page_ref_pat.search(fname):
            match_strategy = "page_ref"

        if match_strategy:
            results.append({
                "file":         fname,
                "display_name": display_name,   # e.g. "Adela Rael"
                "path":         str(f),
                "cabinet":      cabinet,
                "doc":          doc_clean,
                "size_kb":      round(f.stat().st_size / 1024),
                "strategy":     match_strategy,
                "_tok_len":     tok_len,
            })

    # Sort: name_match (longer token = more specific) first; page_ref after
    results.sort(key=lambda r: (0 if r['strategy'] == 'name_match' else 1,
                                -r.get('_tok_len', 0)))
    return results




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

            # d) Grantor name from deed — lowest priority, may not match
            #    if ownership has changed since the deed was recorded.
            if grantor:
                try:
                    for h in search_local_cabinet(cab_letter, "", grantor, ""):
                        if h["path"] not in seen_local_paths:
                            seen_local_paths.add(h["path"])
                            h["source"] = "local"
                            h["ref"]    = "grantor_search"
                            local_hits.append(h)
                except Exception:
                    pass

        # ── Sort: kml_cab_ref → deed_cab_ref → client_name → name_match ─────────
        strategy_order = {"kml_cab_ref": 0, "deed_cab_ref": 1, "client_name": 2,
                          "name_match": 3, "page_ref": 4}
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

        rstart       = (int(job_number) // 100) * 100
        range_folder = f"{rstart}-{rstart + 99}"
        last_name    = client_name.split(",")[0].strip()
        plats_root   = (
            Path(get_survey_data_path()) / range_folder /
            f"{job_number} {client_name}" /
            f"{job_number}-01-{job_type} {last_name}" /
            "E Research" / "B Plats"
        )
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
            rstart       = (int(job_number) // 100) * 100
            last_name    = client_name.split(",")[0].strip()
            range_folder = f"{rstart}-{rstart + 99}"
            deeds_path   = str(
                Path(get_survey_data_path()) / range_folder /
                f"{job_number} {client_name}" /
                f"{job_number}-01-{job_type} {last_name}" /
                "E Research" / "A Deeds"
            )
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
            save_config(cfg)
            drive = detect_survey_drive(force=True)
            return jsonify({"success": True, "drive": drive,
                            "drive_ok": drive is not None})
        else:
            cfg.pop("survey_drive", None)
            save_config(cfg)
            drive = detect_survey_drive(force=True)
            return jsonify({"success": True, "drive": drive,
                            "drive_ok": drive is not None})
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

        # Migrate old subjects that lack new fields
        for s in data.get("subjects", []):
            s.setdefault("status",    "pending")
            s.setdefault("notes",     "")
            s.setdefault("deed_path", "")
            s.setdefault("plat_path", "")

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
        survey = Path(get_survey_data_path())
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
                # Find sub-folder for job type
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
        # Remove modified timestamp before sending
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


@app.route("/api/chain-search", methods=["POST"])
def api_chain_search():
    """Return the last name from a grantor string for chain-of-title searching."""
    try:
        data   = request.get_json()
        grantor = data.get("grantor", "")
        last   = grantor.split(",")[0].strip() if grantor else ""
        return jsonify({"success": True, "search_term": last})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# BOUNDARY LINES & DXF GENERATION
# ══════════════════════════════════════════════════════════════════════════════

import math
import ezdxf
from ezdxf.enums import TextEntityAlignment

# ── Metes-and-bounds parser ────────────────────────────────────────────────────

# Matches patterns like:
#   S 45°30'00" E, 125.50 feet
#   N45-30-00E 125.50'
#   N 45 30 00 E 125.50 ft
#   N45°W 87.20
#   S45E 125.50
_BEARING_PAT = re.compile(
    r'\b([NS])\s*'                                  # N or S
    r'(\d{1,3})'                                    # degrees
    r'[°\-\s]*(\d{0,2})[\'′\-\s]*(\d{0,2})["\″\-\s]*'  # opt min/sec
    r'([EW])\b'                                     # E or W
    r'[,\s]*'
    r'([\d,]+\.?\d*)'                               # distance
    r'\s*(?:feet|foot|ft|\')?',
    re.IGNORECASE
)

# Also catch written-out degrees: "45 degrees 30 minutes 00 seconds"
_BEARING_VERBOSE = re.compile(
    r'\b([NS])\s*'
    r'(\d+)\s*(?:deg(?:rees?)?)?\s*'
    r'(\d*)\s*(?:min(?:utes?)?)?\s*'
    r'(\d*)\s*(?:sec(?:onds?)?)?\s*'
    r'([EW])\b'
    r'[,\s]*'
    r'([\d,]+\.?\d*)'
    r'\s*(?:feet|foot|ft|\')?',
    re.IGNORECASE
)

# Curve / arc call pattern
# Handles: "curve to the left, radius 150 feet, arc length 75.23 feet, chord bears N45°30'00"E"
_CURVE_PAT = re.compile(
    r'\bcurve\s+to\s+the\s*(left|right)'
    r'[^.]{0,200}?'
    r'radius[\s:]+([\d.]+)\s*(?:feet|ft)?'
    r'[^.]{0,200}?'
    r'(?:arc\s+(?:length|len)[\s:]+([\d.]+)\s*(?:feet|ft)?)?'
    r'[^.]{0,200}?'
    r'(?:delta[\s:=]+([\d.]+)(?:°|\s*deg(?:rees?)?)?)?'
    r'[^.]{0,200}?'
    r'(?:chord\s+(?:bears?\s+|bearing\s+)([NS]\s*\d+[^,;.]{0,30}?[EW]))?',
    re.I | re.S
)


def _bearing_to_azimuth(ns: str, deg: float, mn: float, sec: float, ew: str) -> float:
    """Convert quadrant bearing to azimuth (0=N, clockwise positive)."""
    dd = deg + mn / 60.0 + sec / 3600.0
    ns = ns.upper(); ew = ew.upper()
    if ns == 'N' and ew == 'E':
        return dd
    if ns == 'S' and ew == 'E':
        return 180.0 - dd
    if ns == 'S' and ew == 'W':
        return 180.0 + dd
    if ns == 'N' and ew == 'W':
        return 360.0 - dd
    return 0.0


def parse_metes_bounds(text: str) -> list[dict]:
    """
    Parse metes-and-bounds calls from deed text.
    Returns list of dicts:
      Straight: { type='straight', bearing_raw, bearing_label, azimuth, distance }
      Curve:    { type='curve', direction, radius, arc_length, delta, chord_bearing,
                  chord_label, chord_azimuth, chord_length }
    Tries verbose pattern first, falls back to compact pattern.
    Also detects curve/arc calls.
    """
    if not text:
        return []

    calls = []
    seen_spans = []

    def _add(m, pat_type):
        start, end = m.span()
        for s, e in seen_spans:
            if not (end <= s or start >= e):
                return
        seen_spans.append((start, end))

        ns  = m.group(1).upper()
        deg = float(m.group(2) or 0)
        mn  = float(m.group(3) or 0)
        sec = float(m.group(4) or 0)
        ew  = m.group(5).upper()
        dist_str = m.group(6).replace(',', '')
        try:
            dist = float(dist_str)
        except ValueError:
            return

        az    = _bearing_to_azimuth(ns, deg, mn, sec, ew)
        label = f"{ns}{int(deg):02d}°{int(mn):02d}'{int(sec):02d}\"{ew}"

        calls.append({
            "type":          "straight",
            "bearing_raw":   m.group(0).strip(),
            "bearing_label": label,
            "azimuth":       round(az, 6),
            "distance":      round(dist, 3),
            "ns":            ns,
            "ew":            ew,
            "deg":           deg,
            "min":           mn,
            "sec":           sec,
            "span_start":    start,
        })

    for m in _BEARING_VERBOSE.finditer(text):
        _add(m, 'verbose')
    for m in _BEARING_PAT.finditer(text):
        _add(m, 'compact')

    # Curve / arc calls
    for m in _CURVE_PAT.finditer(text):
        start, end = m.span()
        # Skip if overlapping with a straight call
        overlap = any(not (end <= s or start >= e) for s, e in seen_spans)
        if overlap:
            continue
        seen_spans.append((start, end))

        direction  = (m.group(1) or "left").lower()
        radius     = float(m.group(2) or 0)
        arc_length = float(m.group(3)) if m.group(3) else None
        delta_deg  = float(m.group(4)) if m.group(4) else None

        # Calculate arc_length from delta if not given, and vice-versa
        if arc_length and radius and not delta_deg:
            delta_deg = math.degrees(arc_length / radius)
        elif delta_deg and radius and not arc_length:
            arc_length = math.radians(delta_deg) * radius

        # Chord length from arc_length and radius
        chord_len = 2 * radius * math.sin(math.radians(delta_deg) / 2) if delta_deg and radius else (arc_length or 0)

        # Chord bearing (raw string, best-effort)
        chord_raw = (m.group(5) or "").strip()
        chord_az  = 0.0
        chord_lbl = chord_raw
        cb = re.match(r'([NS])\s*(\d+)[^\d]*(\d*)[\'′]?\s*(\d*)["]?\s*([EW])', chord_raw, re.I)
        if cb:
            chord_az = _bearing_to_azimuth(
                cb.group(1).upper(), float(cb.group(2) or 0),
                float(cb.group(3) or 0), float(cb.group(4) or 0), cb.group(5).upper()
            )
            chord_lbl = f"{cb.group(1).upper()}{int(float(cb.group(2))):02d}°{int(float(cb.group(3) or 0)):02d}'{int(float(cb.group(4) or 0)):02d}\"{cb.group(5).upper()}"

        calls.append({
            "type":           "curve",
            "direction":      direction,
            "radius":         round(radius, 3),
            "arc_length":     round(arc_length, 3) if arc_length else 0,
            "delta":          round(delta_deg, 6)  if delta_deg  else 0,
            "chord_bearing":  chord_raw,
            "chord_label":    chord_lbl,
            "chord_azimuth":  round(chord_az, 6),
            "chord_length":   round(chord_len, 3),
            # Expose as bearing_label for table display compatibility
            "bearing_label":  f"Curve {direction}, R={radius}\', Δ={delta_deg:.4f}°" if delta_deg else f"Curve {direction}, R={radius}\'",
            "distance":       round(arc_length, 3) if arc_length else round(chord_len, 3),
            "azimuth":        round(chord_az, 6),
            "span_start":     start,
        })

    # Sort by position in text
    calls.sort(key=lambda c: c['span_start'])
    for c in calls:
        c.pop('span_start', None)

    return calls


# ── Coordinate computation ─────────────────────────────────────────────────────

def calls_to_coords(calls: list[dict], start_x: float = 0.0, start_y: float = 0.0) -> list[tuple]:
    """
    Chain calls into (x, y) vertices starting at (start_x, start_y).
    For curves, uses the chord displacement (chord_azimuth, chord_length).
    Returns list of (x, y) tuples — includes the starting point.
    """
    pts = [(start_x, start_y)]
    x, y = start_x, start_y
    for c in calls:
        if c.get("type") == "curve":
            az_rad = math.radians(c.get("chord_azimuth", c.get("azimuth", 0)))
            dist   = c.get("chord_length", c.get("distance", 0))
        else:
            az_rad = math.radians(c['azimuth'])
            dist   = c['distance']
        dx = dist * math.sin(az_rad)
        dy = dist * math.cos(az_rad)
        x += dx
        y += dy
        pts.append((round(x, 4), round(y, 4)))
    return pts


# ── DXF generator ─────────────────────────────────────────────────────────────

def _ensure_dwg_folder(job_number, client_name, job_type) -> Path:
    """Return (and create) the B Drafting/dwg folder for the job."""
    rstart       = (int(job_number) // 100) * 100
    range_folder = f"{rstart}-{rstart + 99}"
    last_name    = client_name.split(",")[0].strip()
    dwg_dir = (
        Path(get_survey_data_path()) / range_folder /
        f"{job_number} {client_name}" /
        f"{job_number}-01-{job_type} {last_name}" /
        "B Drafting" / "dwg"
    )
    dwg_dir.mkdir(parents=True, exist_ok=True)
    return dwg_dir


def generate_boundary_dxf(
    parcels:     list[dict],   # [{label, calls:[{bearing_label,azimuth,distance}], color}]
    job_number,
    client_name: str,
    job_type:    str,
    options:     dict = None,  # user-selectable flags
) -> str:
    """
    Build a DXF file from one or more parcel call-lists.
    Returns the saved file path string.

    options keys (all default True/on):
      draw_boundary    – draw closed polyline for each parcel
      draw_labels      – add bearing/distance MTEXT labels on each course
      draw_endpoints   – add a POINT at each vertex
      label_size       – text height in drawing units (default 2.0)
      close_tolerance  – if closure error < this value, force close (default 0.5 ft)
    """
    opts = {
        "draw_boundary":  True,
        "draw_labels":    True,
        "draw_endpoints": False,
        "label_size":     2.0,
        "close_tolerance": 0.5,
    }
    if options:
        opts.update(options)

    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 2   # 2 = feet
    doc.header['$MEASUREMENT'] = 0  # imperial

    msp = doc.modelspace()

    # Layer definitions
    layer_defs = [
        ("CLIENT",    2,  "CONTINUOUS"),   # yellow
        ("ADJOINERS", 3,  "DASHED"),       # green, dashed
        ("LABELS",    7,  "CONTINUOUS"),   # white
        ("ENDPOINTS", 6,  "CONTINUOUS"),   # magenta
        ("INFO",      8,  "CONTINUOUS"),   # grey
    ]
    for name, color, lt in layer_defs:
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    text_h = float(opts.get("label_size", 2.0))

    for parcel in parcels:
        label    = parcel.get("label", "Parcel")
        calls    = parcel.get("calls", [])
        p_color  = parcel.get("color", None)  # optional per-parcel color override
        start_x  = float(parcel.get("start_x", 0.0))
        start_y  = float(parcel.get("start_y", 0.0))
        layer    = parcel.get("layer", "CLIENT")

        if not calls:
            continue

        pts = calls_to_coords(calls, start_x, start_y)

        # Check closure
        err = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        closed = err <= float(opts.get("close_tolerance", 0.5))

        if opts["draw_boundary"]:
            # Build 2D polyline vertices
            verts = [(p[0], p[1]) for p in pts]
            attribs = {"layer": layer, "closed": closed}
            if p_color:
                attribs["color"] = p_color
            pline = msp.add_lwpolyline(verts, dxfattribs=attribs)

        if opts["draw_endpoints"]:
            for px, py in pts:
                msp.add_point((px, py, 0), dxfattribs={"layer": "ENDPOINTS"})

        if opts["draw_labels"]:
            for i, c in enumerate(calls):
                # Midpoint of each course
                x0, y0 = pts[i]
                x1, y1 = pts[i + 1]
                mx = (x0 + x1) / 2.0
                my = (y0 + y1) / 2.0

                # Perpendicular offset for readability
                az_rad   = math.radians(c['azimuth'])
                perp_rad = az_rad + math.pi / 2
                offset   = text_h * 1.2
                lx = mx + offset * math.sin(perp_rad)
                ly = my + offset * math.cos(perp_rad)

                bearing_txt = c.get("bearing_label", "")
                dist_txt    = f"{c['distance']:.2f}'"
                txt = f"{bearing_txt}\\P{dist_txt}"   # \\P = MTEXT line break

                msp.add_mtext(txt, dxfattribs={
                    "layer":       "LABELS",
                    "char_height": text_h,
                    "insert":      (lx, ly, 0),
                    "attachment_point": 5,  # middle-center
                })

        # Closure annotation
        if not closed and err > 0.01:
            note = (
                f"! Closure error: {err:.3f} ft\n"
                f"  Parcel: {label}"
            )
            msp.add_mtext(note, dxfattribs={
                "layer":       "INFO",
                "char_height": text_h,
                "insert":      (pts[0][0], pts[0][1] - text_h * 4, 0),
            })

    # Job info block at origin
    info_txt = (
        f"Job #{job_number}  {client_name}\\P"
        f"Type: {job_type}\\P"
        f"Generated: {__import__('datetime').date.today()}"
    )
    msp.add_mtext(info_txt, dxfattribs={
        "layer":       "INFO",
        "char_height": text_h * 0.8,
        "insert":      (0, -text_h * 8, 0),
    })

    # Save file
    dwg_dir  = _ensure_dwg_folder(job_number, client_name, job_type)
    last_name = client_name.split(",")[0].strip().title()
    filename = re.sub(r'[<>:"/\\|?*]', '', f"{job_number} {last_name} Boundary.dxf").strip()
    out_path = dwg_dir / filename
    doc.saveas(str(out_path))
    return str(out_path)


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

        filename = Path(pdf_path).name
        text     = ""
        source   = "text"

        # ── Step 1: Try native text extraction ────────────────────────────────
        try:
            doc = fitz.open(pdf_path)
            for page in doc:
                text += page.get_text() + "\n"
            doc.close()
        except Exception:
            pass

        # ── Step 2: If no useful text, fall back to OCR ───────────────────────
        if len(text.strip()) < 50:
            source = "ocr"
            from PIL import ImageEnhance
            try:
                doc = fitz.open(pdf_path)
                for page in doc:
                    pix = page.get_pixmap(dpi=250)
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
                    img = ImageEnhance.Contrast(img).enhance(2.0)
                    img = img.point(lambda x: 255 if x > 128 else 0, "1")
                    text += pytesseract.image_to_string(img, config="--oem 3 --psm 6") + "\n"
                doc.close()
            except Exception as e:
                return jsonify({"success": False, "error": f"OCR failed: {e}"})

        # ── Step 3: Parse metes & bounds ─────────────────────────────────────
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

        saved_path = generate_boundary_dxf(parcels, job_number, client_name, job_type, options)
        filename   = Path(saved_path).name

        # Compute closure errors per parcel for the response
        closure_errs = []
        for p in parcels:
            pts = calls_to_coords(p.get("calls", []),
                                  p.get("start_x", 0.0), p.get("start_y", 0.0))
            if len(pts) >= 2:
                err = round(math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]), 4)
            else:
                err = 0.0
            closure_errs.append({"label": p.get("label", "Parcel"), "error": err})

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
                "file":    f.name,
                "path":    str(f),
                "size_kb": round(f.stat().st_size / 1024),
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

        survey  = get_survey_data_path()
        geojson = xml_processor.get_map_geojson(survey, highlight_upcs, max_features)
        total   = len(geojson.get("features", []))

        payload = json.dumps({"success": True, "geojson": geojson, "total": total},
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



# ── run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Deed & Plat Helper  —  http://localhost:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
