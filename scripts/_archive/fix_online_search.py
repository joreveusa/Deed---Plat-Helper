"""
Repair script: fixes the broken api_find_plat_online function in app.py.
The function body got mangled by partial edits. This script rewrites the
entire function (from @app.route decorator to the end of the function)
with the correct code.
"""
import re

APP_PY = r"e:\AI DATA CENTER\AI Agents\Deed & Plat Helper\app.py"

with open(APP_PY, encoding="utf-8") as f:
    content = f.read()

# The broken function starts at the route decorator and ends just before
# the save-plat section. We'll replace everything between these two markers.
START_MARKER = '@app.route("/api/find-plat-online", methods=["POST"])'
END_MARKER   = '\n\n\n# \u2500\u2500 save plat to project folder'

start_idx = content.find(START_MARKER)
end_idx   = content.find(END_MARKER, start_idx)

if start_idx == -1:
    print("ERROR: Could not find START_MARKER"); raise SystemExit(1)
if end_idx == -1:
    print("ERROR: Could not find END_MARKER"); raise SystemExit(1)

print(f"Found broken function at bytes {start_idx}..{end_idx}")

REPLACEMENT = '''@app.route("/api/find-plat-online", methods=["POST"])
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
                fd["FIELD7"]         = "SUR"
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
        return jsonify({"success": False, "error": str(e), "online": []})'''

new_content = content[:start_idx] + REPLACEMENT + content[end_idx:]

with open(APP_PY, "w", encoding="utf-8") as f:
    f.write(new_content)

print("SUCCESS: api_find_plat_online repaired.")

# Quick sanity check
with open(APP_PY, encoding="utf-8") as f:
    text = f.read()
lines = text.splitlines()
print(f"Total lines: {len(lines)}")
# Make sure the function compiles
import ast
try:
    ast.parse(text)
    print("SYNTAX OK: file parses cleanly.")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
