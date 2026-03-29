from flask import Flask, request, jsonify, send_from_directory
import requests as req_lib
from bs4 import BeautifulSoup
import os, re, json, traceback, subprocess
from pathlib import Path
import fitz          # PyMuPDF  — PDF → image
import pytesseract
from PIL import Image
import io

# Point pytesseract at the Tesseract binary
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

app = Flask(__name__, static_folder='.', static_url_path='')

BASE_URL = "http://records.1stnmtitle.com"
SURVEY_DATA_PATH  = r"F:\AI DATA CENTER\Survey Data"
CABINET_PATH      = r"F:\AI DATA CENTER\Survey Data\00 COUNTY CLERK SCANS Cabs A-B- C-D - E"

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

JOB_TYPES = ["BDY", "CNS", "TOPO", "ALTA", "LOC", "SPLIT", "CONDO", "EASE", "ROW", "UTIL", "OTHER"]

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
    survey = Path(SURVEY_DATA_PATH)
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

    survey = Path(SURVEY_DATA_PATH)
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
        Path(SURVEY_DATA_PATH)
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
    return send_from_directory(".", "index.html")

# ── config ─────────────────────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        cfg = load_config()
        return jsonify({
            "username": cfg.get("username", ""),
            "has_password": bool(cfg.get("password")),
            "job_types": JOB_TYPES
        })
    data = request.get_json()
    cfg = load_config()
    if "username" in data:
        cfg["username"] = data["username"]
    if "password" in data:
        cfg["password"] = data["password"]
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
        resp = web_session.get(BASE_URL + "/", timeout=15)
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

        post_resp = web_session.post(action, data=form_data, timeout=15)
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

# ── search ─────────────────────────────────────────────────────────────────────

@app.route("/api/search", methods=["POST"])
def api_search():
    try:
        data = request.get_json()
        name = data.get("name", "").strip()
        address = data.get("address", "").strip()
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
            form_data["CROSSNAMEFIELD"] = name
            form_data["CROSSNAMETYPE"] = operator
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

@app.route("/api/document/<doc_no>")
def api_document(doc_no):
    try:
        cfg = load_config()
        username = request.args.get("username") or cfg.get("username", "")
        url = f"{BASE_URL}/scripts/hfpage.asp?Appl=FNM&Doctype=TP&DocNo={doc_no}&FormUser={username}"
        resp = web_session.get(url, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        detail = {"doc_no": doc_no}
        tables = soup.find_all("table")
        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) == 2:
                    label = cells[0].text.strip()
                    value = cells[1].text.strip()
                    if label:
                        detail[label] = value

        # PDF URL
        pdf_link = soup.find("a", string=re.compile(r"pdf all pages", re.I))
        if pdf_link:
            href = pdf_link.get("href", "")
            detail["pdf_url"] = BASE_URL + "/" + href.lstrip("/") if not href.startswith("http") else href
        else:
            detail["pdf_url"] = f"{BASE_URL}/WebTemp/{doc_no}.pdf"

        return jsonify({"success": True, "detail": detail})
    except Exception as e:
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
    Location format: M568-482 → book 568, search ±5 pages around 482.
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
        data     = request.get_json()
        detail   = data.get("detail", {})
        grantor  = data.get("grantor", "")
        location = data.get("location", "")
        doc_no   = data.get("doc_no", "")

        results  = []
        plat_file_used = None

        # ── Strategy 1: OCR the local plat referenced by this deed ───────────────
        cab_refs = parse_cabinet_refs(detail)
        for ref in cab_refs:
            hits = search_local_cabinet(ref["cabinet"], ref["doc"])
            if hits:
                plat_path = hits[0]["path"]   # take first match
                plat_file_used = hits[0]["file"]
                ocr_names = ocr_plat_file(plat_path)
                for name in ocr_names:
                    results.append({
                        "name":   name,
                        "raw":    "",
                        "field":  plat_file_used,
                        "source": "plat_ocr",
                        "plat":   plat_file_used,
                    })
                break   # one plat is enough

        # ── Strategy 2: online location-range search (fallback / supplement) ───
        seen_names = {r["name"].lower() for r in results}
        online = find_adjoiners_online(location, grantor)
        for om in online:
            if om["name"].lower() not in seen_names:
                results.append(om)
                seen_names.add(om["name"].lower())

        return jsonify({
            "success":      True,
            "doc_no":       doc_no,
            "adjoiners":    results,
            "count":        len(results),
            "plat_used":    plat_file_used,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


# ── find plat ──────────────────────────────────────────────────────────────────


def parse_cabinet_refs(detail: dict) -> list[dict]:
    """
    Extract every 'CAB X-NNN' style reference from any field in the deed detail.
    Returns list of {"cabinet": "C", "doc": "191A", "raw": "CAB C-191A"}.
    """
    refs = []
    seen = set()
    # Patterns:  CAB C-191A  /  Cabinet C-191  /  CAB. F-5B  etc.
    pat = re.compile(r'\bCAB(?:INET)?\.?\s*([A-Fa-f])\s*[-–]\s*(\d+[A-Za-z]?)\b', re.I)
    for val in detail.values():
        for m in pat.finditer(str(val)):
            cab = m.group(1).upper()
            doc = m.group(2).upper()
            key = f"{cab}-{doc}"
            if key not in seen:
                seen.add(key)
                refs.append({"cabinet": cab, "doc": doc, "raw": m.group(0)})
    return refs


def search_local_cabinet(cabinet: str, doc_num: str) -> list[dict]:
    """
    Walk the cabinet folder and return files whose name matches `doc_num`.
    Two search strategies:
      A) Leading numeric prefix match  (Cabinet A–E: "100191.001 Name.PDF")
      B) Cabinet page ref in filename  (Cabinet F:   "L3721 Name F-101A.pdf")
         and same pattern for A–E in case filenames carry the ref.
    """
    folder_name = CABINET_FOLDERS.get(cabinet)
    if not folder_name:
        return []
    cab_dir = Path(CABINET_PATH) / folder_name
    if not cab_dir.exists():
        return []

    results = []
    doc_clean    = doc_num.strip()
    # Numeric part only: "191A" → "191"
    doc_num_only = re.sub(r'[A-Za-z]+$', '', doc_clean)
    # Page-ref pattern to look for anywhere in filename: e.g. "C-191A" or "F-191A"
    page_ref_pat = re.compile(
        r'(?<![A-Za-z])' + re.escape(cabinet) + r'[-\s]?' + re.escape(doc_clean) + r'(?![A-Za-z0-9])',
        re.I
    )

    for f in cab_dir.iterdir():
        if not f.is_file() or f.suffix.lower() not in ('.pdf',):
            continue
        name = f.name

        matched = False

        # Strategy A: leading doc-number prefix
        m_num    = re.match(r'^(\d+)', name)
        m_letter = re.match(r'^[A-Za-z](\d+)', name)
        prefix   = (m_num or m_letter)
        if prefix:
            prefix = prefix.group(1)
            if prefix == doc_num_only or prefix.lstrip('0') == doc_num_only.lstrip('0'):
                matched = True

        # Strategy B: cabinet page reference embedded in filename
        if not matched and page_ref_pat.search(name):
            matched = True

        if matched:
            results.append({
                "file":    f.name,
                "path":    str(f),
                "cabinet": cabinet,
                "doc":     doc_clean,
                "size_kb": round(f.stat().st_size / 1024),
            })

    return results


@app.route("/api/find-plat", methods=["POST"])
def api_find_plat():
    """
    Given a deed's detail dict (already fetched), find the related plat(s).
    Strategy:
      1. Parse CAB X-NNN references → search local cabinet folders
      2. Search online records for SURVEY / PLAT type docs matching same grantor or location
    """
    try:
        data       = request.get_json()
        detail     = data.get("detail", {})
        grantor    = data.get("grantor", "")
        location   = data.get("location", "")   # e.g. "M568-482"

        # ── 1. Local cabinet search ──────────────────────────────────────
        cab_refs   = parse_cabinet_refs(detail)
        local_hits = []
        for ref in cab_refs:
            hits = search_local_cabinet(ref["cabinet"], ref["doc"])
            for h in hits:
                h["ref"]  = ref["raw"]
                h["source"] = "local"
            local_hits.extend(hits)

        # ── 2. Online survey/plat search ─────────────────────────────────
        online_hits = []
        search_url  = f"{BASE_URL}/scripts/hfweb.asp?Application=FNM&Database=TP"
        resp        = web_session.get(search_url, timeout=15)

        # Only attempt online search if session is active
        if 'CROSSNAMEFIELD' in resp.text or 'FIELD14' in resp.text:
            soup       = BeautifulSoup(resp.text, "html.parser")
            form       = soup.find("form")
            if form:
                fd = {}
                for inp in form.find_all("input"):
                    nm = inp.get("name")
                    if nm: fd[nm] = inp.get("value", "")
                for sel in form.find_all("select"):
                    nm = sel.get("name")
                    if nm:
                        opt = sel.find("option", selected=True) or sel.find("option")
                        fd[nm] = opt["value"] if opt and opt.get("value") else ""

                # Search by grantor name (last name is usually sufficient)
                last = grantor.split(",")[0].strip() if grantor else ""
                if last:
                    fd["CROSSNAMEFIELD"] = last
                    fd["CROSSNAMETYPE"]  = "begin"
                    # Filter to survey/plat type via FIELD7 (Document Code field)
                    fd["FIELD7"] = "SUR"  # survey plats

                    post = web_session.post(
                        f"{BASE_URL}/scripts/hflook.asp", data=fd, timeout=20
                    )
                    soup2 = BeautifulSoup(post.text, "html.parser")
                    for row in soup2.find_all("tr"):
                        cells = row.find_all("td")
                        if len(cells) < 9:
                            continue
                        doc_link = cells[1].find("a") if len(cells) > 1 else None
                        if not doc_link:
                            continue
                        doc_no = doc_link.text.strip()
                        itype  = cells[5].text.strip() if len(cells) > 5 else ""
                        # Keep only survey/plat types
                        if not re.search(r'survey|plat|map|lot.?line|replat|subdiv', itype, re.I):
                            continue
                        online_hits.append({
                            "doc_no":          doc_no,
                            "instrument_type": itype,
                            "location":        cells[2].text.strip() if len(cells) > 2 else "",
                            "recorded_date":   cells[7].text.strip() if len(cells) > 7 else "",
                            "grantor":         cells[9].text.strip() if len(cells) > 9 else "",
                            "grantee":         cells[10].text.strip() if len(cells) > 10 else "",
                            "pdf_url":         f"{BASE_URL}/WebTemp/{doc_no}.pdf",
                            "source":          "online",
                        })

        return jsonify({
            "success":      True,
            "cabinet_refs": cab_refs,
            "local":        local_hits,
            "online":       online_hits,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


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
            Path(SURVEY_DATA_PATH) / range_folder /
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
                Path(SURVEY_DATA_PATH) / range_folder /
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
        survey = Path(SURVEY_DATA_PATH)
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
      { bearing_raw, bearing_label, azimuth, distance, raw }
    Tries verbose pattern first, falls back to compact pattern.
    """
    if not text:
        return []

    calls = []
    seen_spans = []

    def _add(m, pat_type):
        start, end = m.span()
        # Avoid overlapping matches
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

    # Sort by position in text
    calls.sort(key=lambda c: c['span_start'])
    # Clean up helper key
    for c in calls:
        c.pop('span_start', None)

    return calls


# ── Coordinate computation ─────────────────────────────────────────────────────

def calls_to_coords(calls: list[dict], start_x: float = 0.0, start_y: float = 0.0) -> list[tuple]:
    """
    Chain calls into (x, y) vertices starting at (start_x, start_y).
    Returns list of (x, y) tuples — includes the starting point.
    """
    pts = [(start_x, start_y)]
    x, y = start_x, start_y
    for c in calls:
        az_rad = math.radians(c['azimuth'])
        dx = c['distance'] * math.sin(az_rad)
        dy = c['distance'] * math.cos(az_rad)
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
        Path(SURVEY_DATA_PATH) / range_folder /
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


# ── run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Deed & Plat Helper  —  http://localhost:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False)

