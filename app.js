/* ═══════════════════  Deed & Plat Helper — Frontend Logic  ═══════════════════ */

const API = "/api";

let state = {
  loggedIn:       false,
  username:       "",
  results:        [],
  selectedDoc:    null,
  selectedDetail: null,
  nextJobNum:     null,
  jobTypes:       [],
  platResults:    null,
  discoveredAdjoiners: null,
  // Research Board
  researchSession:  null,
  saveSubject:      "client",
};

// ── init ───────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await loadConfig();
  prefillPreview();

  // Update preview on modal field changes
  ["modalJobNum", "modalClientName", "modalJobType"].forEach(id => {
    document.getElementById(id)?.addEventListener("input", prefillPreview);
    document.getElementById(id)?.addEventListener("change", prefillPreview);
  });

  // Enter key on search fields
  ["searchName", "searchAddress"].forEach(id => {
    document.getElementById(id).addEventListener("keydown", e => {
      if (e.key === "Enter" && !document.getElementById("searchBtn").disabled) doSearch();
    });
  });
  ["username", "password"].forEach(id => {
    document.getElementById(id).addEventListener("keydown", e => {
      if (e.key === "Enter") doLogin();
    });
  });
});

// ── config load ────────────────────────────────────────────────────────────────
async function loadConfig() {
  try {
    const cfg = await apiFetch("/config");
    if (cfg.username) document.getElementById("username").value = cfg.username;
    if (cfg.has_password) document.getElementById("remember").checked = true;
    state.jobTypes = cfg.job_types || ["BDY", "CNS", "TOPO", "ALTA", "LOC", "OTHER"];
    // Populate both job-type selects
    ["modalJobType", "boardJobType"].forEach(selId => {
      const sel = document.getElementById(selId);
      if (!sel) return;
      sel.innerHTML = "";
      state.jobTypes.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t; opt.textContent = t;
        sel.appendChild(opt);
      });
    });
  } catch (_) {}
}

// ── login ──────────────────────────────────────────────────────────────────────
async function doLogin() {
  const btn = document.getElementById("loginBtn");
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const remember = document.getElementById("remember").checked;

  if (!username || !password) { showToast("Enter username and password", "warn"); return; }

  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:16px;height:16px;border-width:2px"></div> Connecting…';
  setStatusDot("loading", "Connecting…");

  try {
    const res = await apiFetch("/login", "POST", { username, password, remember });
    if (res.success) {
      state.loggedIn = true;
      state.username = username;
      setStatusDot("online", `Connected as ${username}`);
      document.getElementById("loginForm").classList.add("hidden");
      document.getElementById("loggedInPanel").classList.remove("hidden");
      document.getElementById("loggedInUser").textContent = username;
      document.getElementById("searchBtn").disabled = false;
      showToast("Connected to Taos County records", "success");
    } else {
      setStatusDot("offline", "Not connected");
      showToast(res.error || "Login failed", "error");
    }
  } catch (e) {
    setStatusDot("offline", "Connection error");
    showToast("Cannot reach server: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">🔑</span> Connect';
  }
}

async function doLogout() {
  await apiFetch("/logout", "POST");
  state.loggedIn = false;
  state.results = [];
  setStatusDot("offline", "Not connected");
  document.getElementById("loginForm").classList.remove("hidden");
  document.getElementById("loggedInPanel").classList.add("hidden");
  document.getElementById("searchBtn").disabled = true;
  showView("empty");
  document.getElementById("resultCount").classList.add("hidden");
  document.getElementById("topbarTitle").textContent = "Property Records Search";
  showToast("Logged out", "info");
}

// ── search ─────────────────────────────────────────────────────────────────────
async function doSearch() {
  const name     = document.getElementById("searchName").value.trim();
  const address  = document.getElementById("searchAddress").value.trim();
  const operator = document.getElementById("searchOperator").value;

  if (!name && !address) { showToast("Enter a name or address to search", "warn"); return; }

  const btn = document.getElementById("searchBtn");
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:16px;height:16px;border-width:2px"></div> Searching…';
  document.getElementById("loadingText").textContent = "Searching Taos County records…";
  showView("loading");

  try {
    const res = await apiFetch("/search", "POST", { name, address, operator });
    if (!res.success) {
      showToast(res.error || "Search failed", "error");
      showView("empty");
      return;
    }

    state.results = res.results;
    renderResults(res.results);

    const cnt = document.getElementById("resultCount");
    cnt.textContent = `${res.results.length} record${res.results.length !== 1 ? "s" : ""}`;
    cnt.classList.remove("hidden");

    const label = [name, address].filter(Boolean).join(" · ");
    document.getElementById("topbarTitle").textContent = `Results for "${label}"`;

    if (res.results.length === 0) {
      showToast("No records found", "warn");
      showView("empty");
    } else {
      showView("results");
    }
  } catch (e) {
    showToast("Search error: " + e.message, "error");
    showView("empty");
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">🔍</span> Search Records';
  }
}

// ── render results ─────────────────────────────────────────────────────────────
function renderResults(results) {
  const tbody = document.getElementById("resultsBody");
  tbody.innerHTML = "";

  results.forEach((r, idx) => {
    const tr = document.createElement("tr");
    tr.dataset.idx = idx;
    tr.onclick = () => loadDocument(r.doc_no, idx);

    const typeClass = getTypeClass(r.instrument_type);
    tr.innerHTML = `
      <td><a class="doc-no-link">${escHtml(r.doc_no)}</a></td>
      <td><span class="badge ${typeClass}">${escHtml(shortType(r.instrument_type))}</span></td>
      <td>${escHtml(r.location)}</td>
      <td>${escHtml(r.recorded_date)}</td>
      <td>${escHtml(r.grantor)}</td>
      <td>${escHtml(r.grantee)}</td>
      <td style="font-family:monospace;font-size:11px">${escHtml(r.gf_number)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function getTypeClass(type) {
  const t = (type || "").toLowerCase();
  if (t.includes("deed") || t.includes("warranty") || t.includes("quitclaim")) return "badge-deed";
  if (t.includes("mortgage") || t.includes("assignment")) return "badge-mortgage";
  return "badge-other";
}

function shortType(type) {
  if (!type) return "—";
  const t = type.toUpperCase();
  if (t.includes("WARRANTY")) return "WARRANTY DEED";
  if (t.includes("QUITCLAIM")) return "QUITCLAIM";
  if (t.includes("MORTGAGE: ASSIGN")) return "MTG ASSIGN";
  if (t.includes("MORTGAGE")) return "MORTGAGE";
  if (t.includes("DEED")) return "DEED";
  if (t.includes("DOMESTIC")) return "DV";
  return type.length > 20 ? type.substring(0, 20) + "…" : type;
}

// ── document detail ────────────────────────────────────────────────────────────
async function loadDocument(docNo, idx) {
  // Highlight selected row
  document.querySelectorAll("#resultsBody tr").forEach(r => r.classList.remove("selected"));
  const row = document.querySelector(`#resultsBody tr[data-idx="${idx}"]`);
  if (row) row.classList.add("selected");

  state.selectedDoc = state.results[idx];

  // Show panel with loading state
  const panel = document.getElementById("detailPanel");
  const overlay = document.getElementById("detailOverlay");
  document.getElementById("detailTitle").textContent = shortType(state.selectedDoc?.instrument_type || "Document");
  document.getElementById("detailDocNo").textContent = docNo;
  document.getElementById("detailBody").innerHTML = `
    <div class="detail-loading"><div class="spinner"></div><p>Loading document…</p></div>
  `;
  panel.classList.remove("hidden");
  overlay.classList.remove("hidden");
  requestAnimationFrame(() => panel.classList.add("open"));

  try {
    const res = await apiFetch(`/document/${docNo}?username=${encodeURIComponent(state.username)}`);
    if (!res.success) {
      document.getElementById("detailBody").innerHTML = `<p style="color:var(--danger);padding:20px">${escHtml(res.error)}</p>`;
      return;
    }
    state.selectedDetail = res.detail;
    renderDetail(res.detail);
  } catch (e) {
    document.getElementById("detailBody").innerHTML = `<p style="color:var(--danger);padding:20px">Error: ${escHtml(e.message)}</p>`;
  }
}

const FIELD_ORDER = [
  "Document Number", "Location", "Document_Code", "GF_Number",
  "Instrument_Type", "Document_No", "Recorded_Date", "Instrument_Date",
  "Grantor", "Grantee", "Subdivision_Legal", "Township_Legal",
  "Other_Legal", "Address", "Comments", "Reference",
  "Title_Company", "Amount"
];
const HIGHLIGHT_KEYS = new Set(["Grantor", "Grantee", "Instrument_Type", "Document Number"]);

function renderDetail(detail) {
  const body = document.getElementById("detailBody");
  const grid = document.createElement("div");
  grid.className = "detail-grid";

  const keys = [...FIELD_ORDER, ...Object.keys(detail).filter(k =>
    !FIELD_ORDER.includes(k) && !["doc_no", "pdf_url"].includes(k)
  )];

  keys.forEach(k => {
    const val = detail[k];
    if (val === undefined || val === "" || val === null) return;
    const row = document.createElement("div");
    row.className = "detail-row";
    row.innerHTML = `
      <div class="detail-key">${escHtml(k.replace(/_/g, " "))}</div>
      <div class="detail-val${HIGHLIGHT_KEYS.has(k) ? " highlight" : ""}">${escHtml(val)}</div>
    `;
    grid.appendChild(row);
  });

  body.innerHTML = "";
  body.appendChild(grid);
}

function closeDetail() {
  const panel = document.getElementById("detailPanel");
  const overlay = document.getElementById("detailOverlay");
  panel.classList.remove("open");
  setTimeout(() => {
    panel.classList.add("hidden");
    overlay.classList.add("hidden");
    document.querySelectorAll("#resultsBody tr").forEach(r => r.classList.remove("selected"));
  }, 260);
  state.selectedDetail = null;
}

function openPdfNewTab() {
  const url = state.selectedDetail?.pdf_url ||
    (state.selectedDoc ? `http://records.1stnmtitle.com/WebTemp/${state.selectedDoc.doc_no}.pdf` : null);
  if (url) window.open(url, "_blank");
}

// ── chain of title ─────────────────────────────────────────────────────────────
function doChainSearch() {
  const grantor = state.selectedDetail?.["Grantor"] || state.selectedDoc?.grantor || "";
  if (!grantor) { showToast("No grantor on this deed", "warn"); return; }
  // Get the last name (before the comma)
  const last = grantor.split(",")[0].trim();
  document.getElementById("searchName").value     = last;
  document.getElementById("searchOperator").value = "contains";
  closeDetail();
  showToast(`Searching chain: "${last}"…`, "info");
  if (!document.getElementById("searchBtn").disabled) doSearch();
  else showToast("Connect to records first", "warn");
}

// ── adjoiner discovery ─────────────────────────────────────────────────────────
async function doFindAdjoiners() {
  if (!state.selectedDoc) return;

  const panel   = document.getElementById("adjPanel");
  const overlay = document.getElementById("adjOverlay");
  const body    = document.getElementById("adjBody");
  document.getElementById("adjDocRef").textContent = state.selectedDoc.doc_no || "";

  body.innerHTML = `<div class="detail-loading"><div class="spinner"></div><p>Scanning deed text &amp; online records…</p></div>`;
  panel.classList.remove("hidden");
  overlay.classList.remove("hidden");
  requestAnimationFrame(() => panel.classList.add("open"));

  try {
    const res = await apiFetch("/find-adjoiners", "POST", {
      detail:   state.selectedDetail || {},
      grantor:  state.selectedDetail?.["Grantor"] || state.selectedDoc?.grantor || "",
      location: state.selectedDetail?.["Location"] || state.selectedDoc?.location || "",
      doc_no:   state.selectedDoc?.doc_no || "",
    });
    if (!res.success) {
      body.innerHTML = `<p style="color:var(--danger);padding:20px">${escHtml(res.error)}</p>`;
      return;
    }
    state.discoveredAdjoiners = res.adjoiners || [];
    renderAdjoiners(res);
  } catch (e) {
    body.innerHTML = `<p style="color:var(--danger);padding:20px">Error: ${escHtml(e.message)}</p>`;
  }
}

function renderAdjoiners(res) {
  const body      = document.getElementById("adjBody");
  const adjoiners = res.adjoiners || [];

  if (!adjoiners.length) {
    body.innerHTML = `
      <div class="plat-empty">
        <div style="font-size:32px;margin-bottom:12px">🔍</div>
        No adjoiner names found.<br>
        <span style="font-size:11px;margin-top:8px;display:block">
          No cabinet plat was matched for this deed, or the plat scan<br>
          could not be read. Try finding the plat first.
        </span>
      </div>`;
    return;
  }

  const plat_ocr = adjoiners.filter(a => a.source === "plat_ocr");
  const online   = adjoiners.filter(a => a.source === "online_range");

  const onBoard = new Set(
    (state.researchSession?.subjects || [])
      .filter(s => s.type === "adjoiner")
      .map(s => s.name.toLowerCase())
  );

  const platName = res.plat_used || "";
  let html = `<div style="font-size:11px;color:var(--text3);padding:8px 0 12px">
    Found <strong style="color:var(--text)">${adjoiners.length}</strong> potential adjoiner${adjoiners.length !== 1 ? "s" : ""}.
    Click <strong style="color:var(--text)">Add to Board</strong> to track them.
  </div>`;

  if (plat_ocr.length) {
    const shortPlat = platName.length > 45 ? platName.slice(0,42)+"…" : platName;
    const platLabel = platName ? ` <span style="color:var(--text3);font-weight:400">${escHtml(shortPlat)}</span>` : "";
    html += `<div class="plat-section-title">🗺️ From Plat OCR (${plat_ocr.length})${platLabel}</div>`;
    plat_ocr.forEach((a, i) => { html += adjCard(a, i, "plat_ocr", onBoard.has(a.name.toLowerCase())); });
  }
  if (online.length) {
    html += `<div class="plat-section-title">🌐 Nearby Online Records (${online.length})</div>`;
    online.forEach((a, i) => { html += adjCard(a, plat_ocr.length + i, "online", onBoard.has(a.name.toLowerCase())); });
  }

  body.innerHTML = html;
}


function adjCard(a, idx, type, alreadyAdded) {
  const pill = type === "plat_ocr"
    ? `<span class="adj-source-pill adj-source-legal">🗺️ Plat OCR</span>`
    : `<span class="adj-source-pill adj-source-online">🌐 Online — ${escHtml(a.location || "")}</span>`;
  const addBtn = alreadyAdded
    ? `<button class="btn btn-outline" style="font-size:10px;padding:4px 10px" disabled>✓ On Board</button>`
    : `<button class="btn btn-success" style="font-size:11px;padding:5px 10px" onclick="addAdjoinerFromDiscovery(${idx})">+ Add to Board</button>`;
  const searchBtn = `<button class="btn btn-outline" style="font-size:10px;padding:4px 10px"
    onclick="searchForSubject('${escHtml(a.name.split(",")[0]).replace(/'/g,"\\'")}')">🔍 Search</button>`;
  return `<div class="plat-card">${pill}<div class="plat-card-title">${escHtml(a.name)}</div><div class="plat-card-actions">${addBtn}${searchBtn}</div></div>`;
}


async function addAdjoinerFromDiscovery(idx) {
  const a = state.discoveredAdjoiners?.[idx];
  if (!a) return;
  if (!state.researchSession) {
    showToast("Open the Research Board and load a session first.", "warn");
    return;
  }
  const exists = state.researchSession.subjects.some(
    s => s.type === "adjoiner" && s.name.toLowerCase() === a.name.toLowerCase()
  );
  if (exists) { showToast(`${a.name} is already on the board`, "warn"); return; }

  state.researchSession.subjects.push({
    id:         "adj_" + Date.now(),
    type:       "adjoiner",
    name:       a.name,
    deed_saved: false,
    plat_saved: false,
  });
  await persistSession();
  renderAdjoiners({ adjoiners: state.discoveredAdjoiners });
  renderBoard();
  showToast(`${a.name} added to Research Board`, "success");
}

function closeAdjPanel() {
  const panel   = document.getElementById("adjPanel");
  const overlay = document.getElementById("adjOverlay");
  panel.classList.remove("open");
  setTimeout(() => { panel.classList.add("hidden"); overlay.classList.add("hidden"); }, 260);
}

// ── plat finder ────────────────────────────────────────────────────────────────
async function doFindPlat() {
  if (!state.selectedDetail && !state.selectedDoc) return;

  const panel   = document.getElementById("platPanel");
  const overlay = document.getElementById("platOverlay");
  const body    = document.getElementById("platBody");
  const ref     = document.getElementById("platDocRef");

  ref.textContent = state.selectedDoc?.doc_no || "";
  body.innerHTML  = `<div class="detail-loading"><div class="spinner"></div><p>Searching cabinet files & online records…</p></div>`;

  panel.classList.remove("hidden");
  overlay.classList.remove("hidden");
  requestAnimationFrame(() => panel.classList.add("open"));

  try {
    const res = await apiFetch("/find-plat", "POST", {
      detail:   state.selectedDetail || {},
      grantor:  state.selectedDetail?.["Grantor"] || state.selectedDoc?.grantor || "",
      location: state.selectedDetail?.["Location"] || state.selectedDoc?.location || "",
    });

    if (!res.success) {
      body.innerHTML = `<p style="color:var(--danger);padding:20px">${escHtml(res.error)}</p>`;
      return;
    }
    renderPlatResults(res);
  } catch (e) {
    body.innerHTML = `<p style="color:var(--danger);padding:20px">Error: ${escHtml(e.message)}</p>`;
  }
}

function renderPlatResults(res) {
  const body = document.getElementById("platBody");
  let html   = "";

  // Cabinet references detected
  if (res.cabinet_refs?.length) {
    const pills = res.cabinet_refs.map(r =>
      `<span class="plat-ref-pill">${escHtml(r.raw)}</span>`
    ).join(" ");
    html += `<div style="margin-bottom:12px;display:flex;gap:6px;flex-wrap:wrap">${pills}</div>`;
  }

  // ── Local results ─────────────────────────────────────────────────────────
  html += `<div class="plat-section-title">📁 Local Cabinet Files (${res.local?.length || 0})</div>`;
  if (res.local?.length) {
    res.local.forEach((h, i) => {
      const shortName = h.file.length > 60 ? h.file.substring(0, 57) + "…" : h.file;
      html += `
        <div class="plat-card">
          <span class="plat-card-badge badge-local">📂 Cabinet ${escHtml(h.cabinet)}</span>
          <div class="plat-card-title">${escHtml(shortName)}</div>
          <div class="plat-card-meta">Doc ${escHtml(h.doc)} &nbsp;·&nbsp; ${escHtml(String(h.size_kb))} KB</div>
          <div class="plat-card-actions">
            <button class="btn btn-outline" style="font-size:11px;padding:6px 10px"
              onclick="openLocalFile('${escHtml(h.path.replace(/'/g,"\\'"))}')">
              📂 Open Folder
            </button>
            <button class="btn btn-success" style="font-size:11px;padding:6px 10px"
              onclick="savePlatLocal(${i})">
              💾 Save to Plats
            </button>
          </div>
        </div>`;
    });
  } else {
    html += `<div class="plat-empty">No local cabinet files matched the deed references.</div>`;
  }

  // ── Online results ────────────────────────────────────────────────────────
  html += `<div class="plat-section-title">🌐 Online Records – Surveys/Plats (${res.online?.length || 0})</div>`;
  if (res.online?.length) {
    res.online.forEach((h, i) => {
      html += `
        <div class="plat-card">
          <span class="plat-card-badge badge-online">🌐 Online</span>
          <div class="plat-card-title">${escHtml(h.instrument_type)}</div>
          <div class="plat-card-meta">
            Doc ${escHtml(h.doc_no)} &nbsp;·&nbsp; ${escHtml(h.recorded_date)}<br>
            ${escHtml(h.grantor)} → ${escHtml(h.grantee)}
          </div>
          <div class="plat-card-actions">
            <button class="btn btn-outline" style="font-size:11px;padding:6px 10px"
              onclick="window.open('${escHtml(h.pdf_url)}','_blank')">
              📄 View PDF
            </button>
            <button class="btn btn-success" style="font-size:11px;padding:6px 10px"
              onclick="savePlatOnline(${i})">
              💾 Save to Plats
            </button>
          </div>
        </div>`;
    });
  } else {
    html += `<div class="plat-empty">No survey/plat documents found online for this grantor.</div>`;
  }

  body.innerHTML = html;

  // Stash results for save actions
  state.platResults = res;
}

function closePlatPanel() {
  const panel   = document.getElementById("platPanel");
  const overlay = document.getElementById("platOverlay");
  panel.classList.remove("open");
  setTimeout(() => {
    panel.classList.add("hidden");
    overlay.classList.add("hidden");
  }, 260);
}

function openLocalFile(filePath) {
  // Open the folder containing the file using the OS file explorer via a backend call
  const dir = filePath.substring(0, filePath.lastIndexOf("\\"));
  apiFetch("/open-folder", "POST", { path: dir }).catch(() => {});
  showToast("Opening folder in Explorer…", "info");
}

async function savePlatLocal(idx) {
  const hit = state.platResults?.local?.[idx];
  if (!hit) return;
  await _savePlat({
    source:    "local",
    file_path: hit.path,
    filename:  hit.file,
  });
}

async function savePlatOnline(idx) {
  const hit = state.platResults?.online?.[idx];
  if (!hit) return;
  await _savePlat({
    source:  "online",
    doc_no:  hit.doc_no,
    pdf_url: hit.pdf_url,
    filename: `${hit.doc_no} ${hit.grantor.split(",")[0].trim()} to ${hit.grantee.split(",")[0].trim()}.pdf`,
  });
}

async function _savePlat(platData) {
  // Need job context — use modal fields if open, otherwise prompt
  const jobNum  = document.getElementById("modalJobNum")?.value || state.nextJobNum;
  const client  = document.getElementById("modalClientName")?.value?.trim();
  const jobType = document.getElementById("modalJobType")?.value || "BDY";

  if (!client) {
    showToast("Open the Save Deed modal first to set job number & client name, then save the plat.", "warn");
    return;
  }

  try {
    const res = await apiFetch("/save-plat", "POST", {
      ...platData,
      job_number:  parseInt(jobNum) || state.nextJobNum,
      client_name: client,
      job_type:    jobType,
    });
    if (res.success) {
      showToast(`Plat saved → ${res.filename}`, "success");
    } else {
      showToast("Save failed: " + res.error, "error");
    }
  } catch (e) {
    showToast("Error: " + e.message, "error");
  }
}

// ── save modal ─────────────────────────────────────────────────────────────────
async function openSaveModal() {
  const modal   = document.getElementById("saveModal");
  const overlay = document.getElementById("modalOverlay");
  const resultSection = document.getElementById("saveResultSection");

  resultSection.style.display = "none";
  document.getElementById("saveSuccess").innerHTML = "";
  document.getElementById("confirmSaveBtn").disabled = false;
  document.getElementById("confirmSaveBtn").innerHTML = '<span>💾</span> Save Deed & Create Folders';

  // If research board has a job loaded, pre-fill from it
  if (state.researchSession) {
    document.getElementById("modalJobNum").value      = state.researchSession.job_number;
    document.getElementById("modalClientName").value  = state.researchSession.client_name;
    document.getElementById("modalJobType").value     = state.researchSession.job_type;
  } else if (state.selectedDetail) {
    const grantee = state.selectedDetail["Grantee"] || state.selectedDoc?.grantee || "";
    document.getElementById("modalClientName").value = toTitleCase(grantee);
  }

  // Reset subject to client
  setSubject("client");

  // Populate adjoiner datalist from research session
  const dl = document.getElementById("adjNameSuggestions");
  dl.innerHTML = "";
  state.researchSession?.subjects?.filter(s => s.type === "adjoiner").forEach(s => {
    const o = document.createElement("option"); o.value = s.name; dl.appendChild(o);
  });

  // Get next job number if no session loaded
  if (!state.researchSession) {
    try {
      const res = await apiFetch("/next-job-number");
      state.nextJobNum = res.next_job_number;
      document.getElementById("modalJobNum").placeholder = `Auto (${res.next_job_number})`;
      document.getElementById("modalJobNum").value = "";
    } catch (_) {}
  }

  updatePreview();
  modal.classList.remove("hidden");
  overlay.classList.remove("hidden");
  requestAnimationFrame(() => modal.classList.add("show"));
}

function setSubject(type) {
  state.saveSubject = type;
  document.getElementById("stogClient").classList.toggle("active",   type === "client");
  document.getElementById("stogAdjoiner").classList.toggle("active", type === "adjoiner");
  const adjGroup = document.getElementById("adjNameGroup");
  adjGroup.classList.toggle("hidden", type !== "adjoiner");
  updatePreview();
}

function updatePreview() {
  const jobNum  = document.getElementById("modalJobNum")?.value || state.nextJobNum || "????";
  const client  = document.getElementById("modalClientName")?.value || "Client, Name";
  const jobType = document.getElementById("modalJobType")?.value || "BDY";
  const isAdj   = state.saveSubject === "adjoiner";

  const rstart  = Math.floor(parseInt(jobNum) / 100) * 100 || 2900;
  const rangeF  = `${rstart}-${rstart + 99}`;
  const last    = client.split(",")[0]?.trim() || client;
  const adjSub  = isAdj ? "\\Adjoiners" : "";

  const folder  = `Survey Data\\${rangeF}\\${jobNum} ${client}\\${jobNum}-01-${jobType} ${last}\\E Research\\A Deeds${adjSub}`;

  const loc     = (state.selectedDetail?.["Location"] || state.selectedDoc?.location || "???").replace(/^[A-Za-z]/, "");
  const grantor = (state.selectedDetail?.["Grantor"] || state.selectedDoc?.grantor || "Grantor").split(",")[0].trim();
  const grantee = (state.selectedDetail?.["Grantee"] || state.selectedDoc?.grantee || "Grantee").split(",")[0].trim();
  const filename = `${loc} ${toTitleCase(grantor)} to ${toTitleCase(grantee)}.pdf`;

  document.getElementById("previewFolder").textContent   = folder;
  document.getElementById("previewFilename").textContent = filename;
}

// keep old name working too
function prefillPreview() { updatePreview(); }

function closeSaveModal() {
  const modal   = document.getElementById("saveModal");
  const overlay = document.getElementById("modalOverlay");
  modal.classList.remove("show");
  setTimeout(() => {
    modal.classList.add("hidden");
    overlay.classList.add("hidden");
  }, 200);
}

async function doSave() {
  const jobNum    = parseInt(document.getElementById("modalJobNum").value) || state.nextJobNum;
  const client    = document.getElementById("modalClientName").value.trim();
  const jobType   = document.getElementById("modalJobType").value;
  const isAdj     = state.saveSubject === "adjoiner";
  const adjName   = document.getElementById("modalAdjName")?.value?.trim() || "";

  if (!client) { showToast("Enter client name", "warn"); return; }
  if (isAdj && !adjName) { showToast("Enter adjoiner name", "warn"); return; }

  // Resolve subject_id from research session
  let subjectId = "client";
  if (isAdj && state.researchSession) {
    const found = state.researchSession.subjects.find(
      s => s.type === "adjoiner" && s.name.toLowerCase() === adjName.toLowerCase()
    );
    subjectId = found?.id || ("adj_" + Date.now());
  }

  const btn = document.getElementById("confirmSaveBtn");
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:16px;height:16px;border-width:2px"></div> Saving…';

  try {
    const res = await apiFetch("/download", "POST", {
      doc_no:         state.selectedDoc?.doc_no,
      grantor:        state.selectedDetail?.["Grantor"] || state.selectedDoc?.grantor || "",
      grantee:        state.selectedDetail?.["Grantee"] || state.selectedDoc?.grantee || "",
      location:       state.selectedDetail?.["Location"] || state.selectedDoc?.location || "",
      job_number:     jobNum,
      client_name:    client,
      job_type:       jobType,
      create_project: true,
      is_adjoiner:    isAdj,
      adjoiner_name:  adjName,
      subject_id:     subjectId,
    });

    const resultSection = document.getElementById("saveResultSection");
    const box = document.getElementById("saveSuccess");
    resultSection.style.display = "block";

    if (res.success) {
      const folder = isAdj ? "A Deeds\\Adjoiners" : "A Deeds";
      const skippedNote = res.skipped ? " <em>(already existed — not re-downloaded)</em>" : "";
      const openBtn = res.saved_path
        ? `<button class="btn btn-outline" style="font-size:11px;padding:5px 10px;margin-top:8px"
             onclick="openSavedFile('${escHtml(res.saved_path).replace(/\\/g,'\\\\')}')">📂 Open File</button>`
        : "";
      box.innerHTML = `
        ${res.skipped ? "⚠️" : "✅"} <strong>${res.skipped ? "Already saved" : "Deed saved!"}${skippedNote}</strong><br><br>
        📁 <strong>Job #${escHtml(String(res.job_number))}</strong> — ${escHtml(client)}<br>
        📄 <strong>${escHtml(res.filename)}</strong><br>
        <span style="font-size:11px;color:#8b949e">→ ${escHtml(folder)}</span><br>
        ${openBtn}
      `;
      showToast(res.skipped ? "File already exists — skipped" : "Deed saved!", res.skipped ? "warn" : "success");
      btn.innerHTML = res.skipped ? "⚠️ Already Existed" : "✅ Saved!";
      state.nextJobNum = res.job_number + 1;

      // Auto-create/sync research session
      if (!state.researchSession && res.job_number) {
        state.researchSession = null; // will load fresh
        const autoJobNum  = res.job_number;
        const autoClient  = client;
        const autoType    = jobType;
        apiFetch(`/research-session?job_number=${autoJobNum}&client_name=${encodeURIComponent(autoClient)}&job_type=${autoType}`)
          .then(r => {
            if (r.success) {
              state.researchSession = r.session;
              document.getElementById("boardJobNum").value    = autoJobNum;
              document.getElementById("boardClientName").value = autoClient;
              document.getElementById("boardJobType").value   = autoType;
              document.getElementById("boardJobLabel").textContent = `Job #${autoJobNum}`;
              showToast("Research session auto-loaded for this job", "info");
            }
          }).catch(() => {});
      }

      // Update research board if active
      if (state.researchSession && res.subject_id) {
        const subj = state.researchSession.subjects.find(s => s.id === res.subject_id);
        if (subj) {
          subj.deed_saved = true;
          if (res.saved_path) subj.deed_path = res.saved_path;
          renderBoard();
        }
      }
    } else {
      box.innerHTML = `❌ <strong>Error:</strong> ${escHtml(res.error)}`;
      box.style.background = "#2d1015";
      box.style.borderColor = "#da3633";
      box.style.color = "#ff7b72";
      btn.disabled = false;
      btn.innerHTML = '<span>💾</span> Retry';
    }
  } catch (e) {
    showToast("Save error: " + e.message, "error");
    btn.disabled = false;
    btn.innerHTML = '<span>💾</span> Save Deed & Create Folders';
  }
}

// ── research board ─────────────────────────────────────────────────────────────
function toggleResearchBoard() {
  const panel = document.getElementById("boardPanel");
  const isOpen = panel.classList.contains("open");
  if (isOpen) closeResearchBoard(); else openResearchBoard();
}

function openResearchBoard() {
  const panel   = document.getElementById("boardPanel");
  const overlay = document.getElementById("boardOverlay");
  panel.classList.remove("hidden");
  overlay.classList.remove("hidden");
  requestAnimationFrame(() => panel.classList.add("open"));
  document.getElementById("boardBtn").classList.add("active");

  // Auto-sync field values from research session
  if (state.researchSession) {
    document.getElementById("boardJobNum").value    = state.researchSession.job_number;
    document.getElementById("boardClientName").value = state.researchSession.client_name;
    document.getElementById("boardJobType").value   = state.researchSession.job_type;
    renderBoard();
  }
}

function closeResearchBoard() {
  const panel   = document.getElementById("boardPanel");
  const overlay = document.getElementById("boardOverlay");
  panel.classList.remove("open");
  document.getElementById("boardBtn").classList.remove("active");
  setTimeout(() => {
    panel.classList.add("hidden");
    overlay.classList.add("hidden");
  }, 260);
}

function boardJobChanged() {
  // Just clear any stale session indicator
  document.getElementById("boardJobLabel").textContent = "";
}

async function loadResearchSession() {
  const jobNum  = document.getElementById("boardJobNum").value.trim();
  const client  = document.getElementById("boardClientName").value.trim();
  const jobType = document.getElementById("boardJobType").value;

  if (!jobNum || !client) { showToast("Enter job number and client name", "warn"); return; }

  try {
    const res = await apiFetch(
      `/research-session?job_number=${jobNum}&client_name=${encodeURIComponent(client)}&job_type=${jobType}`
    );
    if (!res.success) { showToast(res.error, "error"); return; }
    state.researchSession = res.session;
    document.getElementById("boardJobLabel").textContent = `Job #${jobNum}`;
    renderBoard();
    showToast(`Research session loaded — Job #${jobNum}`, "success");
  } catch (e) {
    showToast("Error loading session: " + e.message, "error");
  }
}

async function persistSession() {
  if (!state.researchSession) return;
  const { job_number, client_name, job_type } = state.researchSession;
  try {
    await apiFetch("/research-session", "POST", {
      job_number, client_name, job_type,
      session: state.researchSession
    });
  } catch (_) {}
}

async function addAdjoiner() {
  const nameEl = document.getElementById("boardAdjName");
  const name   = nameEl.value.trim();
  if (!name) { showToast("Enter adjoiner name", "warn"); return; }
  if (!state.researchSession) { showToast("Load a session first", "warn"); return; }

  // Avoid duplicates
  const exists = state.researchSession.subjects.some(
    s => s.type === "adjoiner" && s.name.toLowerCase() === name.toLowerCase()
  );
  if (exists) { showToast("Adjoiner already on board", "warn"); return; }

  const newSubj = {
    id:          "adj_" + Date.now(),
    type:        "adjoiner",
    name:        name,
    deed_saved:  false,
    plat_saved:  false,
    status:      "pending",
    notes:       "",
    deed_path:   "",
    plat_path:   "",
  };
  state.researchSession.subjects.push(newSubj);
  nameEl.value = "";
  renderBoard();
  await persistSession();
  showToast(`Adjoiner added: ${name}`, "success");
}

async function removeSubject(id) {
  if (!state.researchSession) return;
  state.researchSession.subjects = state.researchSession.subjects.filter(s => s.id !== id);
  renderBoard();
  await persistSession();
}

async function toggleSubjectStatus(id, field) {
  if (!state.researchSession) return;
  const subj = state.researchSession.subjects.find(s => s.id === id);
  if (subj) {
    subj[field] = !subj[field];
    renderBoard();
    await persistSession();
  }
}

function searchForSubject(name) {
  // Pre-fill search form and trigger search
  document.getElementById("searchName").value = name;
  document.getElementById("searchOperator").value = "contains";
  closeResearchBoard();
  if (!document.getElementById("searchBtn").disabled) doSearch();
  else showToast("Connect to records first, then search will run", "warn");
}

function openFolderForJob() {
  if (!state.researchSession) { showToast("No session loaded", "warn"); return; }
  const { job_number, client_name, job_type } = state.researchSession;
  const rstart = Math.floor(parseInt(job_number) / 100) * 100;
  const last   = client_name.split(",")[0].trim();
  const path   = `F:\\AI DATA CENTER\\Survey Data\\${rstart}-${rstart+99}\\${job_number} ${client_name}\\${job_number}-01-${job_type} ${last}\\E Research`;
  apiFetch("/open-folder", "POST", { path }).catch(() => {});
  showToast("Opening E Research folder…", "info");
}

function renderBoard() {
  const body = document.getElementById("boardBody");
  const rs   = state.researchSession;
  if (!rs) { body.innerHTML = '<div class="plat-empty">No session loaded.</div>'; return; }

  // ─ Progress bar ─────────────────────────────────────────────────
  const prog    = rs.progress || {};
  const total   = prog.total   || rs.subjects.length;
  const done    = prog.done    || 0;
  const deeds   = prog.deeds   || 0;
  const plats   = prog.plats   || 0;
  const pct     = total > 0 ? Math.round((done / total) * 100) : 0;
  const barColor = pct === 100 ? "#56d3a0" : pct > 50 ? "#79a8e0" : "#c9a227";

  let html = `
    <div style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text3);margin-bottom:4px">
        <span>Progress: <strong style="color:var(--text)">${done}/${total}</strong> complete</span>
        <span style="color:var(--text2)">Deeds: ${deeds} &nbsp;·&nbsp; Plats: ${plats}</span>
      </div>
      <div style="height:5px;background:var(--bg3);border-radius:3px;overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${barColor};border-radius:3px;transition:width .4s ease"></div>
      </div>
    </div>`;

  // ─ Subject rows ───────────────────────────────────────────────
  rs.subjects.forEach(s => {
    const isClient = s.type === "client";
    const st       = s.status || "pending";  // pending | done | na

    // Deed chip — show open-file button if path exists
    const deedFileBtn = s.deed_path
      ? `<button class="btn btn-outline" style="font-size:9px;padding:2px 6px;margin-left:4px" title="Open deed PDF"
           onclick="openSavedFile('${escHtml(s.deed_path).replace(/\\/g,'\\\\')}')">📂</button>` : "";
    const platFileBtn = s.plat_path
      ? `<button class="btn btn-outline" style="font-size:9px;padding:2px 6px;margin-left:4px" title="Open plat PDF"
           onclick="openSavedFile('${escHtml(s.plat_path).replace(/\\/g,'\\\\')}')">📂</button>` : "";

    const deedChip = s.deed_saved
      ? `<span class="status-chip chip-done">✓ Deed</span>${deedFileBtn}`
      : `<span class="status-chip chip-todo">○ Deed</span>`;
    const platChip = s.plat_saved
      ? `<span class="status-chip chip-done">✓ Plat</span>${platFileBtn}`
      : `<span class="status-chip chip-todo">○ Plat</span>`;

    // Status badge (cycles pending → done → na)
    const stBadgeStyle = st === "done" ? `background:#1a3028;color:#56d3a0;border-color:#2d8a6e66`
                        : st === "na"   ? `background:#281a1a;color:#888;border-color:#66444466`
                        :                 `background:var(--bg);color:var(--text3);border-color:var(--border)`;
    const stLabel = st === "done" ? "✓ Done" : st === "na" ? "⊘ N/A" : "● Pending";
    const statusBadge = `<button class="status-chip" style="border:1px solid;border-radius:10px;
      padding:2px 8px;font-size:10px;font-weight:600;cursor:pointer;${stBadgeStyle}"
      onclick="cycleStatus('${s.id}')" title="Click to cycle status">${stLabel}</button>`;

    // Inline notes
    const noteVal  = escHtml(s.notes || "");
    const notePh   = "Add note…";
    const notesHtml = `<input class="inp" style="font-size:10px;padding:4px 8px;height:24px;margin-top:6px"
      placeholder="${notePh}" value="${noteVal}"
      onchange="saveNote('${s.id}', this.value)" />`;

    const badge = isClient
      ? `<span class="subject-type-badge badge-client">👤 Client</span>`
      : `<span class="subject-type-badge badge-adjoiner">🏘️ Adjoiner</span>`;

    html += `
      <div class="subject-row ${isClient ? 'is-client' : 'is-adjoiner'}">
        <div class="subject-name">${escHtml(s.name)}</div>
        ${badge}
        <div style="grid-column:1/-1;display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:2px">
          ${deedChip}${platChip}
          <span style="margin-left:auto">${statusBadge}</span>
        </div>
        ${notesHtml}
        <div class="subject-actions" style="grid-column:1/-1;margin-top:6px">
          <button class="btn btn-outline" style="font-size:10px;padding:4px 8px"
            onclick="searchForSubject('${escHtml(s.name.split(',')[0]).replace(/'/g,"\\'")}')"🔍 Search</button>
          <button class="btn btn-outline" style="font-size:10px;padding:4px 8px"
            onclick="toggleSubjectStatus('${s.id}','deed_saved')">📄 Deed</button>
          <button class="btn btn-outline" style="font-size:10px;padding:4px 8px"
            onclick="toggleSubjectStatus('${s.id}','plat_saved')">🗺️ Plat</button>
          ${!isClient ? `<button class="btn btn-outline" style="font-size:10px;padding:4px 8px;color:#ff7b72"
            onclick="removeSubject('${s.id}')">✕</button>` : ""}
        </div>
      </div>`;
  });

  body.innerHTML = html || '<div class="plat-empty">No subjects yet.</div>';
}

async function cycleStatus(id) {
  if (!state.researchSession) return;
  const subj = state.researchSession.subjects.find(s => s.id === id);
  if (!subj) return;
  const order = ["pending", "done", "na"];
  const cur   = subj.status || "pending";
  subj.status = order[(order.indexOf(cur) + 1) % order.length];
  renderBoard();
  await persistSession();
}

async function saveNote(id, text) {
  if (!state.researchSession) return;
  const subj = state.researchSession.subjects.find(s => s.id === id);
  if (!subj) return;
  subj.notes = text;
  // No re-render needed — just persist
  await persistSession();
}

function openSavedFile(filePath) {
  apiFetch("/open-file", "POST", { path: filePath })
    .then(r => { if (!r.success) showToast("File not found: " + filePath.split("\\").pop(), "error"); })
    .catch(() => showToast("Could not open file", "error"));
}

async function exportSession() {
  if (!state.researchSession) { showToast("No session loaded", "warn"); return; }
  try {
    const res = await apiFetch("/export-session", "POST", { session: state.researchSession });
    if (!res.success) { showToast("Export failed", "error"); return; }
    // Download as .txt file
    const blob = new Blob([res.text], { type: "text/plain" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `Job${state.researchSession.job_number}_Research.txt`;
    a.click();
    URL.revokeObjectURL(url);
    showToast("Research summary exported", "success");
  } catch (e) {
    showToast("Export error: " + e.message, "error");
  }
}

async function loadRecentJobs() {
  try {
    const res = await apiFetch("/recent-jobs");
    if (!res.success || !res.jobs.length) { showToast("No recent jobs found", "info"); return; }
    showRecentJobs(res.jobs);
  } catch (e) {
    showToast("Error: " + e.message, "error");
  }
}

function showRecentJobs(jobs) {
  // Render a quick-pick list above the board body
  const body = document.getElementById("boardBody");
  const existingPicker = document.getElementById("recentJobsPicker");
  if (existingPicker) { existingPicker.remove(); return; }  // toggle off

  const picker = document.createElement("div");
  picker.id   = "recentJobsPicker";
  picker.style.cssText = "background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:12px";
  picker.innerHTML = `
    <div style="font-size:11px;font-weight:600;color:var(--text2);margin-bottom:8px">🕒 Recent Jobs</div>
    ${jobs.map(j => `
      <button onclick="quickLoadJob(${j.job_number},'${escHtml(j.client_name).replace(/'/g,"\\'")}','${j.job_type}')"
        style="display:block;width:100%;text-align:left;background:transparent;border:none;padding:5px 4px;
               cursor:pointer;font-size:12px;color:var(--text2);font-family:inherit;
               border-radius:4px;" onmouseover="this.style.background='var(--bg2)'" onmouseout="this.style.background='transparent'">
        <strong style="color:var(--text)">#${j.job_number}</strong> &mdash; ${escHtml(j.client_name)}
        <span style="font-size:10px;color:var(--text3);margin-left:4px">${j.job_type}</span>
      </button>`).join("")}
  `;
  body.insertBefore(picker, body.firstChild);
}

async function quickLoadJob(num, client, type) {
  document.getElementById("boardJobNum").value    = num;
  document.getElementById("boardClientName").value = client;
  document.getElementById("boardJobType").value   = type;
  document.getElementById("recentJobsPicker")?.remove();
  await loadResearchSession();
}


// ── boundary panel ─────────────────────────────────────────────────────────────

// State for boundary panel
state.parsedCalls     = [];   // [{bearing_label, azimuth, distance, bearing_raw}]
state.adjoinParcels   = [];   // [{label, layer, calls:[]}]
state.activeBndTab    = "calls";

// ── open / close ───────────────────────────────────────────────────────────────
async function doDrawBoundary() {
  if (!state.selectedDoc) return;

  const panel   = document.getElementById("boundaryPanel");
  const overlay = document.getElementById("boundaryOverlay");
  document.getElementById("boundaryDocRef").textContent = state.selectedDoc.doc_no || "";

  // Pre-fill job fields from session or modal
  _prefillBndJobFields();

  // Populate job-type select
  const sel = document.getElementById("bndJobType");
  if (!sel.options.length) {
    state.jobTypes.forEach(t => {
      const o = document.createElement("option"); o.value = t; o.textContent = t; sel.appendChild(o);
    });
  }
  if (state.researchSession) sel.value = state.researchSession.job_type;

  panel.classList.remove("hidden");
  overlay.classList.remove("hidden");
  requestAnimationFrame(() => panel.classList.add("open"));
  switchBndTab("calls");

  // Auto-parse from current deed detail if available
  if (state.selectedDetail) {
    await _runParseCalls({ detail: state.selectedDetail });
  }
}

function closeBoundaryPanel() {
  const panel   = document.getElementById("boundaryPanel");
  const overlay = document.getElementById("boundaryOverlay");
  panel.classList.remove("open");
  setTimeout(() => { panel.classList.add("hidden"); overlay.classList.add("hidden"); }, 260);
}

function _prefillBndJobFields() {
  const rs = state.researchSession;
  if (rs) {
    document.getElementById("bndJobNum").value    = rs.job_number;
    document.getElementById("bndClientName").value = rs.client_name;
  } else {
    document.getElementById("bndJobNum").value    = document.getElementById("modalJobNum")?.value || "";
    document.getElementById("bndClientName").value = document.getElementById("modalClientName")?.value || "";
  }
}

// ── tabs ───────────────────────────────────────────────────────────────────────
function switchBndTab(tab) {
  state.activeBndTab = tab;
  ["calls", "parcels", "options"].forEach(t => {
    document.getElementById(`bndTab${t.charAt(0).toUpperCase()+t.slice(1)}`)?.classList.toggle("active", t === tab);
    document.getElementById(`bndPane${t.charAt(0).toUpperCase()+t.slice(1)}`)?.classList.toggle("hidden", t !== tab);
  });
  if (tab === "parcels") renderParcelList();
}

// ── call parsing ───────────────────────────────────────────────────────────────
async function _runParseCalls(body) {
  setBndStatus("Parsing calls…", "loading");
  document.getElementById("bndCallsWrap").innerHTML =
    `<div class="detail-loading"><div class="spinner"></div><p>Parsing deed text…</p></div>`;

  try {
    const res = await apiFetch("/parse-calls", "POST", body);
    if (!res.success) { setBndStatus("Parse error: " + res.error, "error"); return; }
    state.parsedCalls = res.calls || [];
    renderCallsTable(res);
    if (res.count === 0) {
      setBndStatus("No calls found — paste deed text manually", "warn");
    } else {
      setBndStatus(`${res.count} call${res.count !== 1 ? "s" : ""} parsed`, "ok");
    }
  } catch (e) {
    setBndStatus("Error: " + e.message, "error");
  }
}

async function reparseCalls() {
  if (!state.selectedDetail) { showToast("No deed detail loaded", "warn"); return; }
  await _runParseCalls({ detail: state.selectedDetail });
}

async function parseFromTextbox() {
  const txt = document.getElementById("bndPasteText").value.trim();
  if (!txt) { showToast("Paste some deed text first", "warn"); return; }
  await _runParseCalls({ text: txt });
}

// ── calls table ────────────────────────────────────────────────────────────────
function renderCallsTable(res) {
  const wrap = document.getElementById("bndCallsWrap");
  const calls = state.parsedCalls;

  // Closure bar
  const closureBar  = document.getElementById("bndClosureBar");
  const closureTxt  = document.getElementById("bndClosureText");
  if (calls.length) {
    closureBar.style.display = "flex";
    const err = res.closure_err ?? 0;
    const cls = err < 0.5 ? "closure-good" : err < 2 ? "closure-warn" : "closure-bad";
    closureTxt.innerHTML =
      `<span class="${cls}">` +
      (err < 0.01 ? "✓ Perfect closure" : `⚡ Closure error: ${err.toFixed(4)} ft`) +
      `</span>` +
      `&nbsp;·&nbsp; ${calls.length} call${calls.length !== 1 ? "s" : ""}` +
      (res.coords?.length ? ` &nbsp;·&nbsp; ${res.coords.length - 1} segments` : "");
  } else {
    closureBar.style.display = "none";
  }

  if (!calls.length) {
    wrap.innerHTML = `<div class="plat-empty">No calls found. Paste deed text above or click "+ Add Call".</div>`;
    return;
  }

  let html = `
    <table class="bnd-calls-table">
      <thead>
        <tr>
          <th style="width:28px">#</th>
          <th>Bearing</th>
          <th>Distance (ft)</th>
          <th style="width:28px">Del</th>
        </tr>
      </thead>
      <tbody id="bndCallsTbody">
  `;
  calls.forEach((c, i) => {
    html += `
      <tr class="bnd-call-row" id="callRow${i}">
        <td style="color:var(--text3);text-align:center">${i+1}</td>
        <td>
          <input class="inp bnd-cell-inp" value="${escHtml(c.bearing_label)}"
            onchange="updateCallField(${i},'bearing_label',this.value)"
            style="width:100%;font-size:11px;padding:3px 6px;font-family:monospace" />
        </td>
        <td>
          <input class="inp bnd-cell-inp" type="number" step="0.001" value="${c.distance}"
            onchange="updateCallField(${i},'distance',parseFloat(this.value)||0)"
            style="width:100%;font-size:11px;padding:3px 6px" />
        </td>
        <td style="text-align:center">
          <button class="bnd-del-btn" onclick="deleteCall(${i})" title="Delete">✕</button>
        </td>
      </tr>
    `;
  });
  html += `</tbody></table>`;
  wrap.innerHTML = html;
}

function updateCallField(idx, field, value) {
  if (!state.parsedCalls[idx]) return;
  state.parsedCalls[idx][field] = value;
  // Re-compute azimuth if bearing_label changed
  if (field === "bearing_label") {
    const lbl = value.trim().toUpperCase();
    const m = lbl.match(/^([NS])(\d+)°(\d+)'(\d+)"([EW])$/);
    if (m) {
      const ns = m[1], deg = +m[2], mn = +m[3], sec = +m[4], ew = m[5];
      let az = deg + mn/60 + sec/3600;
      if (ns==='N' && ew==='E') az = az;
      else if (ns==='S' && ew==='E') az = 180 - az;
      else if (ns==='S' && ew==='W') az = 180 + az;
      else if (ns==='N' && ew==='W') az = 360 - az;
      state.parsedCalls[idx].azimuth = +az.toFixed(6);
    }
  }
}

function deleteCall(idx) {
  state.parsedCalls.splice(idx, 1);
  renderCallsTable({ closure_err: 0 });
  recalcClosure();
}

function clearAllCalls() {
  state.parsedCalls = [];
  renderCallsTable({});
  document.getElementById("bndClosureBar").style.display = "none";
  setBndStatus("Calls cleared", "ok");
}

function addManualCall() {
  state.parsedCalls.push({
    bearing_label: "N00°00'00\"E",
    azimuth: 0,
    distance: 0,
    bearing_raw: "",
  });
  renderCallsTable({ closure_err: 0 });
  recalcClosure();
}

function recalcClosure() {
  const calls = state.parsedCalls;
  if (!calls.length) return;
  let x = 0, y = 0;
  calls.forEach(c => {
    const az = c.azimuth * Math.PI / 180;
    x += c.distance * Math.sin(az);
    y += c.distance * Math.cos(az);
  });
  const err = Math.hypot(x, y);
  const closureTxt = document.getElementById("bndClosureText");
  const cls = err < 0.5 ? "closure-good" : err < 2 ? "closure-warn" : "closure-bad";
  closureTxt.innerHTML =
    `<span class="${cls}">` +
    (err < 0.01 ? "✓ Perfect closure" : `⚡ Closure error: ${err.toFixed(4)} ft`) +
    `</span> &nbsp;·&nbsp; ${calls.length} call${calls.length !== 1 ? "s" : ""}`;
  document.getElementById("bndClosureBar").style.display = "flex";
}

// ── parcel management ──────────────────────────────────────────────────────────
function renderParcelList() {
  const wrap = document.getElementById("bndParcelList");
  let html   = "";

  // Client parcel (always first, using parsedCalls)
  const clientCount = state.parsedCalls.length;
  html += `
    <div class="bnd-parcel-card" style="border-color:var(--accent-teal,#1abc9c)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="subject-type-badge badge-client">👤 Client Boundary</span>
        <span style="font-size:11px;color:var(--text3)">${clientCount} call${clientCount!==1?'s':''} (from Calls tab)</span>
      </div>
      <div style="margin-top:6px;font-size:11px;color:var(--text3)">Layer: <strong style="color:#ffee00">CLIENT</strong></div>
    </div>
  `;

  // Adjoiner parcels
  state.adjoinParcels.forEach((p, pi) => {
    const cnt = p.calls.length;
    html += `
      <div class="bnd-parcel-card">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span class="subject-type-badge badge-adjoiner">🏘️ ${escHtml(p.label)}</span>
          <button class="bnd-del-btn" onclick="removeAdjoinerParcel(${pi})" title="Remove">✕</button>
        </div>
        <div style="margin-top:6px;font-size:11px;color:var(--text3)">Layer: <strong style="color:#00aa00">ADJOINERS</strong></div>
        <div style="margin-top:6px;display:flex;gap:6px;align-items:center">
          <input class="inp bnd-cell-inp" placeholder="Label…" value="${escHtml(p.label)}"
            style="flex:1;font-size:11px;padding:3px 8px"
            onchange="state.adjoinParcels[${pi}].label=this.value;renderParcelList()" />
          <span style="font-size:11px;color:var(--text3)">${cnt} call${cnt!==1?'s':''}</span>
        </div>
        <div style="margin-top:6px">
          <textarea class="inp bnd-cell-inp" rows="2"
            style="width:100%;font-size:10px;font-family:monospace;resize:vertical"
            placeholder="Paste adjoiner deed text to parse calls…"
            id="adjText${pi}"></textarea>
          <button class="btn btn-outline" style="font-size:10px;padding:3px 8px;margin-top:4px"
            onclick="parseAdjoinerText(${pi})">Parse</button>
        </div>
      </div>
    `;
  });

  wrap.innerHTML = html || `<div class="plat-empty" style="margin:0">No parcels defined yet.</div>`;
}

function addAdjoinerParcel() {
  // Suggest from discovered adjoiners
  const suggestions = (state.researchSession?.subjects || [])
    .filter(s => s.type === "adjoiner")
    .map(s => s.name);

  const label = prompt("Adjoiner parcel label (e.g. \"Rael, Carlos\"):",
    suggestions.length ? suggestions[0] : "Adjoiner");
  if (!label) return;

  state.adjoinParcels.push({ label, layer: "ADJOINERS", calls: [], start_x: 0, start_y: 0 });
  renderParcelList();
}

function removeAdjoinerParcel(idx) {
  state.adjoinParcels.splice(idx, 1);
  renderParcelList();
}

async function parseAdjoinerText(idx) {
  const txt = document.getElementById(`adjText${idx}`)?.value.trim();
  if (!txt) { showToast("Paste some text first", "warn"); return; }
  try {
    const res = await apiFetch("/parse-calls", "POST", { text: txt });
    if (!res.success) { showToast("Parse error: " + res.error, "error"); return; }
    state.adjoinParcels[idx].calls = res.calls;
    renderParcelList();
    showToast(`${res.count} call${res.count!==1?"s":""} parsed for ${state.adjoinParcels[idx].label}`, "success");
  } catch (e) {
    showToast("Error: " + e.message, "error");
  }
}

// ── generate DXF ───────────────────────────────────────────────────────────────
async function doGenerateDxf() {
  const jobNum  = parseInt(document.getElementById("bndJobNum").value);
  const client  = document.getElementById("bndClientName").value.trim();
  const jobType = document.getElementById("bndJobType").value || "BDY";

  if (!client) {
    showToast("Enter client name in the Parcels tab", "warn");
    switchBndTab("parcels");
    return;
  }
  if (!state.parsedCalls.length && !state.adjoinParcels.some(p => p.calls.length)) {
    showToast("No boundary calls to generate — parse or add calls first", "warn");
    switchBndTab("calls");
    return;
  }

  // Build parcels array
  const parcels = [];
  if (state.parsedCalls.length) {
    parcels.push({
      label:  `Client — ${client}`,
      layer:  "CLIENT",
      calls:  state.parsedCalls,
      start_x: 0,
      start_y: 0,
    });
  }
  state.adjoinParcels.forEach(p => {
    if (p.calls.length) {
      parcels.push({
        label:   p.label,
        layer:   p.layer || "ADJOINERS",
        calls:   p.calls,
        start_x: p.start_x || 0,
        start_y: p.start_y || 0,
      });
    }
  });

  // Collect options from UI
  const options = {
    draw_boundary:   document.getElementById("optDrawBoundary")?.checked ?? true,
    draw_labels:     document.getElementById("optDrawLabels")?.checked   ?? true,
    draw_endpoints:  document.getElementById("optDrawEndpoints")?.checked ?? false,
    label_size:      parseFloat(document.getElementById("optLabelSize")?.value) || 2.0,
    close_tolerance: parseFloat(document.getElementById("optCloseTol")?.value)  || 0.5,
  };

  const btn = document.getElementById("bndGenerateBtn");
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px"></div> Generating…';
  setBndStatus("Generating DXF…", "loading");

  try {
    const resolvedJob = jobNum || (await apiFetch("/next-job-number")).next_job_number;
    const res = await apiFetch("/generate-dxf", "POST", {
      job_number:  resolvedJob,
      client_name: client,
      job_type:    jobType,
      parcels,
      options,
    });

    if (!res.success) {
      setBndStatus("Error: " + res.error, "error");
      showToast("DXF generation failed: " + res.error, "error");
      return;
    }

    // Show closure errors if any
    const badClosure = (res.closure_errors || []).filter(e => e.error > options.close_tolerance);
    if (badClosure.length) {
      const msg = badClosure.map(e => `${e.label}: ${e.error.toFixed(3)} ft`).join(", ");
      showToast(`⚠️ Closure issues: ${msg}`, "warn");
    }

    setBndStatus(`✓ Saved: ${res.filename}`, "ok");
    showToast(`DXF saved → ${res.filename}`, "success");

    // Offer to open the folder
    setTimeout(() => {
      const dir = res.saved_to.substring(0, res.saved_to.lastIndexOf("\\"));
      apiFetch("/open-folder", "POST", { path: dir }).catch(() => {});
    }, 500);

  } catch (e) {
    setBndStatus("Error: " + e.message, "error");
    showToast("Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>💾</span> Generate &amp; Save DXF';
  }
}

// ── status helper ──────────────────────────────────────────────────────────────
function setBndStatus(msg, type) {
  const el = document.getElementById("bndStatus");
  if (!el) return;
  const colors = { ok:"#56d3a0", error:"#ff7b72", warn:"#e3c55a", loading:"#79a8e0" };
  el.style.color    = colors[type] || "#ccc";
  el.style.fontSize = "11px";
  el.textContent    = msg;
}

// ── helpers ────────────────────────────────────────────────────────────────────
function showView(view) {
  document.getElementById("emptyState").classList.toggle("hidden",   view !== "empty");
  document.getElementById("loadingState").classList.toggle("hidden", view !== "loading");
  document.getElementById("resultsWrap").classList.toggle("hidden",  view !== "results");
}

function setStatusDot(mode, text) {
  const dot  = document.querySelector(".status-dot");
  const span = document.getElementById("statusText");
  dot.className = `status-dot ${mode}`;
  span.textContent = text;
}

function escHtml(str) {
  return String(str ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function toTitleCase(str) {
  return str.toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
}

async function apiFetch(path, method = "GET", body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── toast notifications ────────────────────────────────────────────────────────
let toastContainer;
function showToast(msg, type = "info") {
  if (!toastContainer) {
    toastContainer = document.createElement("div");
    toastContainer.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:340px";
    document.body.appendChild(toastContainer);
  }
  const colors = {
    success: ["#1a3028","#2d8a6e","#56d3a0"],
    error:   ["#2d1015","#da3633","#ff7b72"],
    warn:    ["#2a2108","#c9a227","#e3c55a"],
    info:    ["#1c2340","#3b5e99","#79a8e0"],
  };
  const [bg, border, color] = colors[type] || colors.info;
  const toast = document.createElement("div");
  toast.style.cssText = `background:${bg};border:1px solid ${border};color:${color};padding:10px 14px;border-radius:8px;font-size:13px;font-weight:500;box-shadow:0 4px 20px rgba(0,0,0,.5);animation:toastIn .25s ease;max-width:340px;word-break:break-word`;
  toast.textContent = msg;
  toastContainer.appendChild(toast);
  setTimeout(() => { toast.style.opacity = "0"; toast.style.transition = "opacity .3s"; setTimeout(() => toast.remove(), 300); }, 3500);
}

const style = document.createElement("style");
style.textContent = `@keyframes toastIn { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }`;
document.head.appendChild(style);

