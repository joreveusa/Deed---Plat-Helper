"""
routes/search.py — Portal login, search, chain-search, and document detail Blueprint.

Handles all county records portal interaction: login/logout, deed searches (basic,
enriched, chain-of-title), and document detail scraping.
"""

import re
import traceback

from bs4 import BeautifulSoup
from flask import Blueprint, request, jsonify, g

from helpers.auth import increment_search_count, add_search_history
from helpers.metes_bounds import extract_trs
from helpers.subscription import require_auth, require_pro, check_search_quota
from helpers.rate_limit import rate_limit
from services.config import load_config, save_config
from services.portal import get_portal_url, get_session, scrape_form_data
from services.arcgis import arcgis_lookup_upc
from services.drive import get_survey_data_path
import xml_processor

search_bp = Blueprint("search", __name__)


# ── relevance scoring ─────────────────────────────────────────────────────────

# Instrument types most relevant to boundary survey research
_DEED_TYPES = {'deed', 'warranty', 'quitclaim', 'grant', 'conveyance', 'bargain'}
_PLAT_TYPES = {'plat', 'survey', 'partition', 'subdivision'}
_LOW_TYPES  = {'mortgage', 'lien', 'release', 'assignment', 'satisfaction', 'ucc'}


def _score_search_result(
    result: dict,
    client_trs: str = "",
    client_name: str = "",
    adjoiner_names: list = None,
    client_subdivision: str = "",
) -> dict:
    """Score a single 1stNMTitle search result for relevance to the client property.

    Modifies the result dict in-place, adding:
      - relevance_score (int 0-100)
      - relevance_tags  (list of tag strings)

    Returns the modified result.
    """
    score = 0
    tags = []
    adjoiner_set = {n.upper().split(",")[0].strip() for n in (adjoiner_names or []) if n}
    client_last = client_name.split(",")[0].strip().upper() if client_name else ""

    inst_type = (result.get("instrument_type") or "").lower()
    grantor   = (result.get("grantor") or "").upper()
    grantee   = (result.get("grantee") or "").upper()
    location  = (result.get("location") or "").upper()

    # ── TRS match (highest value — same section as client) ─────────────
    if client_trs:
        trs_up = client_trs.upper()
        trs_parts = re.findall(r'SEC(?:TION)?\s*(\d+)', location, re.I)
        client_sec = re.search(r'SEC\s*(\d+)', trs_up)
        if client_sec and trs_parts:
            if client_sec.group(1) in trs_parts:
                score += 40
                tags.append("trs_match")

    # ── Subdivision match ─────────────────────────────────────────────
    if client_subdivision:
        subdiv_up = client_subdivision.upper()
        if subdiv_up in location or subdiv_up in grantor or subdiv_up in grantee:
            score += 25
            tags.append("same_subdivision")

    # ── Name match (client or adjoiner) ───────────────────────────────
    if client_last and len(client_last) >= 2:
        if client_last in grantor or client_last in grantee:
            score += 30
            tags.append("client_name")

    if adjoiner_set:
        for adj_last in adjoiner_set:
            if adj_last and len(adj_last) >= 3:
                if adj_last in grantor or adj_last in grantee:
                    score += 20
                    tags.append("adjoiner")
                    break

    # ── Instrument type priority ──────────────────────────────────────
    inst_words = set(inst_type.split())
    if _DEED_TYPES & inst_words:
        score += 15
        tags.append("deed")
    elif _PLAT_TYPES & inst_words:
        score += 12
        tags.append("plat")
    elif _LOW_TYPES & inst_words:
        score -= 5  # deprioritize mortgages/liens

    # ── Recency bonus ─────────────────────────────────────────────────
    rec_date = result.get("recorded_date") or result.get("instrument_date") or ""
    if rec_date:
        try:
            year = int(rec_date.split("-")[0]) if "-" in rec_date else int(rec_date[-4:])
            if year >= 2015:
                score += 5
            elif year >= 2000:
                score += 3
        except (ValueError, IndexError):
            pass

    result["relevance_score"] = max(0, min(100, score))
    result["relevance_tags"]  = tags
    return result


def _parse_search_results(soup) -> tuple[list, str]:
    """Parse results table rows from a BeautifulSoup of the search response."""
    results = []
    count_text = ""
    for tag in soup.find_all(string=re.compile(r'\d+ records? found', re.I)):
        count_text = tag.strip()
        break

    rows = soup.find_all("tr")
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
    return results, count_text


# ── login ──────────────────────────────────────────────────────

@search_bp.route("/api/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json()
        username = data.get("username", "")
        password = data.get("password", "")
        remember = data.get("remember", False)

        # Fetch login page to discover form
        sess = get_session()
        resp = sess.get(get_portal_url() + "/", timeout=8)
        soup = BeautifulSoup(resp.text, "lxml")
        form = soup.find("form")
        if not form:
            return jsonify({"success": False, "error": "Login form not found"})

        action = form.get("action", "/")
        if not action.startswith("http"):
            action = get_portal_url() + "/" + action.lstrip("/")

        form_data = {}
        for inp in form.find_all('input'):
            nm = inp.get('name')
            itype = (inp.get('type') or 'text').lower()
            if nm:
                if itype == 'image':
                    form_data[nm + '.x'] = '1'
                    form_data[nm + '.y'] = '1'
                else:
                    form_data[nm] = inp.get('value', '')

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

        post_resp = sess.post(action, data=form_data, timeout=8)
        portal_root = get_portal_url().lower().rstrip('/')
        landed_url = post_resp.url.lower()
        success = ('hfweb' in landed_url or 'new search' in post_resp.text.lower() or
                   ('logout' in post_resp.text.lower() and landed_url.rstrip('/') != portal_root))

        if success:
            if remember:
                cfg = load_config()
                cfg["firstnm_user"] = username
                cfg["firstnm_pass"] = password
                cfg.pop("username", None)
                cfg.pop("password", None)
                save_config(cfg)
            return jsonify({"success": True, "username": username})
        else:
            return jsonify({"success": False, "error": "Invalid credentials or login failed"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ── logout ─────────────────────────────────────────────────────────────────────

@search_bp.route("/api/logout", methods=["POST"])
def api_logout():
    get_session().cookies.clear()
    return jsonify({"success": True})


# ── search ─────────────────────────────────────────────────────────────────────

@search_bp.route("/api/search", methods=["POST"])
@require_auth
@rate_limit(requests=30, window=60)
def api_search():
    # ── Quota gate ──────────────────────────────────────────────────────────
    allowed, quota_msg = check_search_quota(g.current_user)
    if not allowed:
        return jsonify({
            "success":        False,
            "error":          quota_msg,
            "upgrade_required": True,
            "current_tier":   g.current_user.get("tier", "free"),
        }), 403
    try:
        data = request.get_json()
        name      = data.get("name", "").strip()
        address   = data.get("address", "").strip()
        name_type = data.get("name_type", "grantor")
        op_map = {"contains": "contains", "begins with": "begin", "exact match": "exact", "equals": "exact"}
        operator = op_map.get(data.get("operator", "contains"), "contains")

        search_url = f"{get_portal_url()}/scripts/hfweb.asp?Application=FNM&Database=TP"
        sess = get_session()
        resp = sess.get(search_url, timeout=15)

        # Detect redirect back to login page
        landed = resp.url.lower().rstrip('/')
        if landed == get_portal_url().lower().rstrip('/') or 'login' in landed:
            return jsonify({"success": False, "error": "Session expired — please log in again."})

        if 'CROSSNAMEFIELD' not in resp.text and 'FIELD14' not in resp.text:
            return jsonify({"success": False, "error": "Session expired — please log in again."})

        soup = BeautifulSoup(resp.text, "html.parser")
        action = get_portal_url() + "/scripts/hflook.asp"

        form_data = scrape_form_data(soup)
        if not form_data:
            return jsonify({"success": False, "error": "Search form not found"})

        if name:
            if name_type == "grantee":
                form_data["CROSSNAMEFIELD"] = name
                form_data["CROSSNAMETYPE"]  = operator
                form_data["CROSSTYPE"]       = "GE"
            else:
                form_data["CROSSNAMEFIELD"] = name
                form_data["CROSSNAMETYPE"]  = operator
                form_data["CROSSTYPE"]       = "GR"
        if address:
            form_data["FIELD19"] = address
            form_data["SEARCHTYPE19"] = operator

        post_resp = sess.post(action, data=form_data, timeout=20)
        soup2 = BeautifulSoup(post_resp.text, "html.parser")
        results, count_text = _parse_search_results(soup2)

        # Increment monthly search counter for tier tracking + save to history
        increment_search_count(g.current_user)
        _query = name or address
        if _query:
            add_search_history(g.current_user["id"], _query, len(results))
        return jsonify({"success": True, "results": results, "count": len(results), "count_text": count_text})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


# ── enriched search ───────────────────────────────────────────────────────────

@search_bp.route("/api/search-enriched", methods=["POST"])
@require_auth
@require_pro
def api_search_enriched():
    """Enriched search: standard 1stNMTitle search + ArcGIS relevance scoring."""
    try:
        data = request.get_json() or {}
        client_upc  = (data.get("client_upc") or "").strip()
        client_name = (data.get("client_name") or "").strip()
        adj_names   = data.get("adjoiner_names") or []
        sort_by     = data.get("sort_by", "relevance")

        name      = (data.get("name") or "").strip()
        address   = (data.get("address") or "").strip()
        name_type = data.get("name_type", "grantor")
        op_map = {"contains": "contains", "begins with": "begin",
                  "exact match": "exact", "equals": "exact"}
        operator = op_map.get(data.get("operator", "contains"), "contains")

        if not name and not address:
            return jsonify({"success": False, "error": "No search criteria provided"})

        sess = get_session()
        search_url = f"{get_portal_url()}/scripts/hfweb.asp?Application=FNM&Database=TP"
        resp = sess.get(search_url, timeout=15)

        landed = resp.url.lower().rstrip('/')
        if landed == get_portal_url().lower().rstrip('/') or 'login' in landed:
            return jsonify({"success": False, "error": "Session expired — please log in again."})

        if 'CROSSNAMEFIELD' not in resp.text and 'FIELD14' not in resp.text:
            return jsonify({"success": False, "error": "Session expired — please log in again."})

        soup = BeautifulSoup(resp.text, "html.parser")
        action = get_portal_url() + "/scripts/hflook.asp"
        form_data = scrape_form_data(soup)
        if not form_data:
            return jsonify({"success": False, "error": "Search form not found"})

        if name:
            form_data["CROSSNAMEFIELD"] = name
            form_data["CROSSNAMETYPE"]  = operator
            form_data["CROSSTYPE"]      = "GE" if name_type == "grantee" else "GR"
        if address:
            form_data["FIELD19"] = address
            form_data["SEARCHTYPE19"] = operator

        post_resp = sess.post(action, data=form_data, timeout=20)
        soup2 = BeautifulSoup(post_resp.text, "html.parser")
        results, count_text = _parse_search_results(soup2)

        # Step 2: Get client's ArcGIS context for scoring
        client_trs = ""
        client_subdivision = ""
        if client_upc:
            arc = arcgis_lookup_upc(client_upc)
            if arc and arc.get("success"):
                client_trs = arc.get("trs", "")
                client_subdivision = arc.get("subdivision", "")

        # Also try from KML index if no ArcGIS TRS
        if not client_trs and client_upc:
            survey = get_survey_data_path()
            idx = xml_processor.load_index(survey)
            if idx:
                for p in idx.get("parcels", []):
                    if p.get("upc") == client_upc:
                        client_trs = p.get("trs", "") or (
                            p.get("arcgis", {}).get("trs", "") if p.get("arcgis") else ""
                        )
                        client_subdivision = (
                            p.get("arcgis", {}).get("subdivision", "") if p.get("arcgis") else ""
                        )
                        break

        # Step 3: Score each result
        for r in results:
            _score_search_result(
                r,
                client_trs=client_trs,
                client_name=client_name,
                adjoiner_names=adj_names,
                client_subdivision=client_subdivision,
            )

        # Step 4: Sort by requested order
        if sort_by == "relevance":
            results.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)
        elif sort_by == "date":
            results.sort(
                key=lambda r: r.get("recorded_date") or r.get("instrument_date") or "",
                reverse=True
            )
        elif sort_by == "type":
            type_order = {"deed": 0, "plat": 1, "survey": 2}
            results.sort(key=lambda r: type_order.get(
                (r.get("instrument_type") or "").lower().split()[0] if r.get("instrument_type") else "",
                99
            ))

        return jsonify({
            "success":    True,
            "results":    results,
            "count":      len(results),
            "count_text": count_text,
            "sort_by":    sort_by,
            "client_trs":        client_trs,
            "client_subdivision": client_subdivision,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


# ── chain-of-title search ─────────────────────────────────────────────────────

@search_bp.route("/api/chain-search", methods=["POST"])
@require_auth
@require_pro
def api_chain_search():
    """Trace ownership backward by recursively searching the grantor as grantee."""
    try:
        data = request.get_json()
        start_grantor = data.get("start_grantor", "").strip()
        max_hops = min(int(data.get("max_hops", 10)), 20)

        if not start_grantor:
            return jsonify({"success": False, "error": "No starting grantor provided"})

        chain = []
        seen_docs = set()
        current_name = start_grantor
        stop_reason = ""
        plat_re = re.compile(r'(?:plat|cabinet|cab\.?|survey|plat\s+book)', re.I)

        for hop in range(max_hops):
            search_url = f"{get_portal_url()}/scripts/hfweb.asp?Application=FNM&Database=TP"
            try:
                resp = get_session().get(search_url, timeout=15)
            except Exception:
                stop_reason = "Network error during search"
                break

            if 'CROSSNAMEFIELD' not in resp.text:
                stop_reason = "Session expired — could not continue chain search"
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            form_data = scrape_form_data(soup)
            if not form_data:
                stop_reason = "Search form not found"
                break

            form_data["CROSSNAMEFIELD"] = current_name
            form_data["CROSSNAMETYPE"] = "begin"
            form_data["CROSSTYPE"] = "GE"

            action = get_portal_url() + "/scripts/hflook.asp"
            try:
                post_resp = get_session().post(action, data=form_data, timeout=20)
            except Exception:
                stop_reason = f"Network error searching for {current_name}"
                break

            soup2 = BeautifulSoup(post_resp.text, "html.parser")
            rows = soup2.find_all("tr")

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

            candidates.sort(key=lambda c: (not c["is_deed"], c["date"]), reverse=False)
            best = candidates[0]
            seen_docs.add(best["doc_no"])
            chain.append(best)

            if best["has_plat_ref"]:
                stop_reason = f"Plat reference found in deed {best['doc_no']}"
                break

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

@search_bp.route("/api/document/<doc_no>", methods=["GET", "POST"])
@require_auth
def api_document(doc_no):
    try:
        cfg = load_config()
        username = request.args.get("username") or cfg.get("firstnm_user", "")

        search_row = {}
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            search_row = body.get("search_result", {})

        url = f"{get_portal_url()}/scripts/hfpage.asp?Appl=FNM&Doctype=TP&DocNo={doc_no}&FormUser={username}"
        resp = get_session().get(url, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        detail = {"doc_no": doc_no}

        # ── Strategy 1: 2-column <td> tables ─────
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
        if len(detail) <= 1:
            for b in soup.find_all(["b", "strong"]):
                label = b.text.strip().rstrip(":")
                if not label or len(label) > 40:
                    continue
                nxt = b.next_sibling
                if nxt and isinstance(nxt, str) and nxt.strip():
                    detail[label] = nxt.strip()

        # ── Strategy 3: pull all visible text for TRS ──────────
        page_text = soup.get_text(" ", strip=True)

        # ── Merge search_row data ──────────
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
            detail["pdf_url"] = (get_portal_url() + "/" + href.lstrip("/")
                                 if not href.startswith("http") else href)
        else:
            detail["pdf_url"] = f"{get_portal_url()}/WebTemp/{doc_no}.pdf"

        # ── TRS extraction ────────────────────────────────────────────────────
        all_text = page_text + " " + " ".join(str(v) for v in detail.values())
        trs_refs = extract_trs(all_text)
        if trs_refs:
            detail["_trs"] = trs_refs

        return jsonify({"success": True, "detail": detail})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})
