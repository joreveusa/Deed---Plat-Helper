// @ts-nocheck
console.log("[app.js] EXECUTING - top of file");
const API = "/api";  // Use relative URL to avoid CORS issues


const state = {
  currentStep: 1,
  loggedIn: false,
  nextJobNum: null,
  researchSession: null,
  selectedDoc: null,
  selectedDetail: null,
  discoveredAdjoiners: [],
  parsedCalls: [],
  adjoinParcels: [],
  searchResults: []
};

// 
// INIT & BOOTSTRAP
// 
document.addEventListener("DOMContentLoaded", async () => {
  // Load config and recent jobs immediately  do NOT await checkLogin first
  // checkLogin hits 1stnmtitle.com which can block for up to 30s
  await loadConfig();
  loadRecentJobs(); // fire immediately, no await
  checkLogin();     // fire in background, no await

  // Restore last session fields
  if (state.lastSession) {
    document.getElementById("setupJobNum").value = state.lastSession.job_number || "";
    document.getElementById("setupClient").value = state.lastSession.client_name || "";
    document.getElementById("setupJobType").value = state.lastSession.job_type || "BDY";
  } else {
    apiFetch("/next-job-number").then(r => {
      if (r.success) {
        state.nextJobNum = r.next_job_number;
        document.getElementById("setupJobNum").placeholder = "Auto: " + r.next_job_number;
      }
    }).catch(() => {});
  }

  updateStepUI();
});

async function loadConfig() {
  try {
    const res = await apiFetch("/config");
    if (res.success) {
      if (res.config.firstnm_user) document.getElementById("cfgUser").value = res.config.firstnm_user;
      if (res.config.firstnm_pass) document.getElementById("cfgPass").value = res.config.firstnm_pass;
      if (res.config.firstnm_url)  document.getElementById("cfgUrl").value  = res.config.firstnm_url;
      state.lastSession = res.config.last_session;
    }
  } catch (e) {
    console.error("Config load failed", e);
  }
}

// 
// WORKFLOW STEPPER LOGIC
// 
function goToStep(step) {
  if (step > 1 && !state.researchSession) {
    showToast("Please start a research session first", "warn");
    return;
  }
  state.currentStep = step;
  updateStepUI();
}

function updateStepUI() {
  // Update nav buttons
  document.querySelectorAll(".step-btn").forEach(btn => {
    const s = parseInt(btn.dataset.step);
    btn.classList.toggle("active", s === state.currentStep);
    btn.classList.toggle("completed", s < state.currentStep);
    
    // Unlock logic
    if (state.researchSession) {
      btn.disabled = false;
    }
  });

  // Update progress bar
  const pct = ((state.currentStep - 1) / 5) * 100;
  document.getElementById("stepProgressFill").style.width = pct + "%";

  // Show active panel
  for (let i = 1; i <= 6; i++) {
    const panel = document.getElementById(`step${i}Panel`);
    if (panel) {
      if (i === state.currentStep) {
        panel.classList.remove("hidden");
        // small delay to let display:block apply before opacity transition
        requestAnimationFrame(() => panel.classList.add("active"));
      } else {
        panel.classList.remove("active");
        panel.classList.add("hidden");
      }
    }
  }

  // Trigger step-specific logic
  if (state.currentStep === 2 && state.researchSession) {
    if (!document.getElementById("s2SearchName").value) {
      document.getElementById("s2SearchName").value = state.researchSession.client_name;
    }
  }
  if (state.currentStep === 3) {
    doStep3Search();
  }
  if (state.currentStep === 5) {
    renderResearchBoard();
  }
  if (state.currentStep === 6) {
    switchS6Tab('calls');
  }
}

function updateJobContext() {
  const bar = document.getElementById("jobContextBar");
  if (!state.researchSession) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  document.getElementById("ctxJobNum").textContent = state.researchSession.job_number;
  document.getElementById("ctxClient").textContent = state.researchSession.client_name;
  document.getElementById("ctxType").textContent = state.researchSession.job_type;
}

// 
// STEP 1: JOB SETUP
// 
async function startSession() {
  const numInput = document.getElementById("setupJobNum").value;
  const num = parseInt(numInput) || state.nextJobNum;
  const client = document.getElementById("setupClient").value.trim();
  const type = document.getElementById("setupJobType").value;

  if (!client) { showToast("Client name is required", "error"); return; }
  if (!num)    { showToast("Job number is required", "error"); return; }

  const btn = document.getElementById("btnStartSession");
  btn.disabled = true;
  btn.innerHTML = "Loading...";

  try {
    const res = await apiFetch(`/research-session?job_number=${num}&client_name=${encodeURIComponent(client)}&job_type=${type}`);
    if (res.success) {
      state.researchSession = res.session;
      
      // Ensure client is in the subjects list
      let cSubj = state.researchSession.subjects.find(s => s.type === "client");
      if (!cSubj) {
        state.researchSession.subjects.unshift({
          id: "client_" + Date.now(),
          type: "client",
          name: client,
          deed_saved: false, plat_saved: false, status: "pending", notes: ""
        });
        await persistSession();
      }

      updateJobContext();
      showToast(`Session loaded for Job #${num}`, "success");
      
      // Save last session
      apiFetch("/config", "POST", {
        last_session: { job_number: num, client_name: client, job_type: type }
      }).catch(()=>{});

      // Move to Step 2
      goToStep(2);
    } else {
      showToast(res.error, "error");
    }
  } catch (e) {
    showToast("Failed to load session: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Start Research Session &rarr;";
  }
}

async function loadRecentJobs() {
  const container = document.getElementById("setupRecentJobs");
  try {
    const res = await apiFetch("/recent-jobs");
    if (!res.success || !res.jobs.length) {
      container.innerHTML = `<div class="empty-state">No recent jobs found.</div>`;
      return;
    }
    container.innerHTML = res.jobs.map(j => `
      <div class="recent-job-row" onclick="quickLoadJob(${j.job_number}, '${escHtml(j.client_name).replace(/'/g,"\\'")}','${j.job_type}')">
        <div><strong class="text-accent2">#${j.job_number}</strong> &nbsp; ${escHtml(j.client_name)}</div>
        <div class="job-type">${j.job_type}</div>
      </div>
    `).join("");
  } catch (e) {
    container.innerHTML = `<div class="text-danger">Failed to load recent jobs.</div>`;
  }
}

function quickLoadJob(num, client, type) {
  document.getElementById("setupJobNum").value = num;
  document.getElementById("setupClient").value = client;
  document.getElementById("setupJobType").value = type;
  startSession();
}

async function persistSession() {
  if (!state.researchSession) return;
  const { job_number, client_name, job_type } = state.researchSession;
  try {
    await apiFetch("/research-session", "POST", {
      job_number, client_name, job_type,
      session: state.researchSession
    });
    updateGlobalProgress();
  } catch (e) { console.error("Session persist failed", e); }
}
// 
// STEP 2: CLIENT DEED
// 
async function doStep2Search() {
  const name = document.getElementById("s2SearchName").value.trim();
  const op = document.getElementById("s2SearchOp").value;

  if (!state.loggedIn) { showToast("Not connected to records. Connecting...", "warn"); await checkLogin(); return; }
  if (!name || name.length < 2) { showToast("Enter a longer name", "warn"); return; }

  const btn = document.getElementById("btnS2Search");
  const tbody = document.getElementById("s2ResultsBody");
  btn.disabled = true;
  btn.innerHTML = "Search";
  tbody.innerHTML = `<tr><td colspan="5" class="empty-cell"><div class="loading-state">Searching records...</div></td></tr>`;
  document.getElementById("s2ResultCount").textContent = "0";

  try {
    const res = await apiFetch("/search", "POST", { name, operator: op });
    if (!res.success) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell text-danger">Error: ${res.error}</td></tr>`;
      return;
    }

    if (!res.results.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell text-text3">No records found for "${name}"</td></tr>`;
      return;
    }

    document.getElementById("s2ResultCount").textContent = res.results.length;
    state.searchResults = res.results;
    tbody.innerHTML = res.results.map((r, i) => `
      <tr class="row-${getTypeClass(r.instrument_type)}" onclick="loadS2Detail('${r.doc_no}', ${i}, this)">
        <td class="mono font-bold text-accent2">${r.doc_no || ''}</td>
        <td title="${escHtml(r.grantor||'')}">${escHtml((r.grantor||'').split(",")[0] || r.grantor||'')}</td>
        <td><span class="badge ${getTypeClass(r.instrument_type)}">${r.instrument_type||'Deed'}</span></td>
        <td class="text-xs text-text3">${escHtml(r.location||'')}</td>
        <td class="text-xs text-text3">${(r.recorded_date||r.instrument_date||'').split("-")[0] || r.date||''}</td>
      </tr>
    `).join("");

  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="text-danger p-3">Search error: ${e.message}</td></tr>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Search";
  }
}

async function loadS2Detail(docNo, idx, trEl) {
  // Highlight row
  document.querySelectorAll("#s2ResultsBody tr").forEach(tr => tr.classList.remove("selected"));
  if (trEl) trEl.classList.add("selected");

  const container = document.getElementById("s2DetailContainer");
  container.innerHTML = `<div class="loading-state flex-col gap-2"><div class="spinner"></div> Loading ${docNo}...</div>`;

  try {
    // Set selectedDoc before fetching so we have the search row immediately
    state.selectedDoc = state.searchResults?.[idx] || { doc_no: docNo };

    // POST the search result so the backend can merge it into the detail
    const res = await apiFetch(`/document/${encodeURIComponent(docNo)}`, 'POST', {
      search_result: state.selectedDoc
    });
    if (!res.success) {
      container.innerHTML = `<div class="empty-state text-danger">Error: ${res.error}</div>`;
      return;
    }

    state.selectedDetail = res.detail;

    const d = res.detail;
    const extracted = extractDeedData(d, docNo, state.selectedDoc);
    const pdfUrl = d.pdf_url || '';

    container.innerHTML = `
      <div class="deed-viewer-header">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <div>
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:var(--text3);margin-bottom:4px">Document</div>
            <div style="font-size:22px;font-weight:800;font-family:'JetBrains Mono',monospace;color:#e3c55a;letter-spacing:-0.5px">${escHtml(docNo)}</div>
            <div style="font-size:12px;color:var(--text2);margin-top:3px">
              ${escHtml(extracted.instrumentType)} &nbsp;&middot;&nbsp; ${escHtml(extracted.recordedDate)}
            </div>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            ${pdfUrl ? `<a href="${escHtml(pdfUrl)}" target="_blank" class="btn btn-outline btn-sm">&#128279; View PDF</a>` : ''}
          </div>
        </div>
      </div>

      <div class="deed-viewer-tabs">
        <button class="deed-viewer-tab active" onclick="switchDeedTab('summary')" id="dtab-summary">&#128203; Summary</button>
        <button class="deed-viewer-tab" onclick="switchDeedTab('fields')" id="dtab-fields">&#128194; All Fields</button>
        ${pdfUrl ? `<button class="deed-viewer-tab" onclick="switchDeedTab('pdf')" id="dtab-pdf">&#128196; PDF</button>` : ''}
      </div>

      <div class="deed-viewer-body" id="deedTabSummary">
        ${buildDeedSummaryTab(extracted, d)}
      </div>

      <div class="deed-viewer-body hidden" id="deedTabFields">
        ${buildDeedAllFieldsTab(d)}
      </div>

      ${pdfUrl ? `
      <div class="deed-viewer-body hidden" id="deedTabPdf" style="display:flex;flex-direction:column;padding:0;min-height:420px">
        <div class="pdf-preview-bar">
          &#128196; <span style="font-family:monospace;color:var(--accent2)">${escHtml(docNo)}.pdf</span>
          <a href="${escHtml(pdfUrl)}" target="_blank" class="btn btn-outline btn-sm ml-auto">Open in Tab &#8599;</a>
        </div>
        <iframe src="${escHtml(pdfUrl)}" class="pdf-preview-frame" title="Deed PDF"></iframe>
      </div>` : ''}

      <div class="detail-actions">
        <button class="btn btn-primary flex-1" id="btnS2Save" onclick="saveClientDeed('${docNo}')">
          &#11015; Save Client Deed &rarr;</button>
      </div>
    `;

  } catch (e) {
    container.innerHTML = `<div class="empty-state text-danger">Error: ${e.message}</div>`;
  }
}

/** Extract well-known fields from deed detail object */
function extractDeedData(d, docNo, searchRow) {
  const docNumbers = [{ label: 'Doc #', value: docNo, type: 'docnum' }];
  ['Document Number','Document No','Instrument Number','Instrument No'].forEach(k => {
    if (d[k] && d[k] !== docNo) docNumbers.push({ label: k, value: d[k], type: 'docnum' });
  });
  ['GF Number','GF#','GF No','File Number'].forEach(k => {
    if (d[k]) docNumbers.push({ label: k, value: d[k], type: 'docnum' });
  });
  if (searchRow?.doc_no && searchRow.document_no && searchRow.document_no !== docNo)
    docNumbers.push({ label: 'Instrument No', value: searchRow.document_no, type: 'docnum' });
  if (searchRow?.gf_number) docNumbers.push({ label: 'GF#', value: searchRow.gf_number, type: 'docnum' });

  const locationSources = [];
  ['Location','Book/Page','Recorded Book','Reception No'].forEach(k => {
    if (d[k]) locationSources.push({ label: k, value: d[k], type: 'location' });
  });
  if (searchRow?.location && !locationSources.find(l => l.value === searchRow.location))
    locationSources.push({ label: 'Location', value: searchRow.location, type: 'location' });

  const partySeen = new Set();
  const parties = [];
  const addParty = (label, val) => {
    if (!val) return;
    const key = label + ':' + val;
    if (partySeen.has(key)) return;
    partySeen.add(key);
    parties.push({ label, value: val, type: 'person' });
  };
  addParty('Grantor', d['Grantor']);
  addParty('Grantee', d['Grantee']);
  if (searchRow) { addParty('Grantor', searchRow.grantor); addParty('Grantee', searchRow.grantee); }

  const dateSeen = new Set();
  const dates = [];
  const addDate = (label, val) => {
    if (!val || dateSeen.has(val)) return;
    dateSeen.add(val);
    dates.push({ label, value: val, type: 'date' });
  };
  ['Recorded Date','Record Date','Instrument Date','Filed Date'].forEach(k => addDate(k, d[k]));
  if (searchRow) { addDate('Recorded', searchRow.recorded_date); addDate('Instrument', searchRow.instrument_date); }

  const trsRefs = (d._trs || []).map(t => ({ label: 'TRS', value: t.trs, type: 'trs' }));

  const money = [];
  ['Consideration','Amount','Sale Price','Value'].forEach(k => {
    if (d[k] && d[k] !== '$0' && d[k] !== '0') money.push({ label: k, value: d[k], type: 'money' });
  });

  const legalKeys = ['Legal Description','Other Legal','Other_Legal','Subdivision Legal','Subdivision_Legal','Legal','Section','Comments','Remarks'];
  const legalText = legalKeys.map(k => d[k]).filter(Boolean).join('\n\n');

  const instrumentType = searchRow?.instrument_type || d['Document Type'] || d['Type'] || d['Instrument Type'] || 'Deed';
  const recordedDate = searchRow?.recorded_date || d['Recorded Date'] || d['Record Date'] || d['Instrument Date'] || '';

  return { docNumbers, locationSources, parties, dates, trsRefs, money, legalText, instrumentType, recordedDate };
}

function deedPill(item) {
  return `<div class="data-pill pill-${item.type}">
    <div class="data-pill-label">${escHtml(item.label)}</div>
    <div class="data-pill-value">${escHtml(item.value)}</div>
  </div>`;
}

function buildDeedSummaryTab(ex, d) {
  let html = '';
  if (ex.docNumbers.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128290; Document Numbers</div>
    <div class="data-pills">${ex.docNumbers.map(deedPill).join('')}</div>
  </div>`;

  if (ex.locationSources.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128205; Location (Book / Page)</div>
    <div class="data-pills">${ex.locationSources.map(deedPill).join('')}</div>
  </div>`;

  if (ex.parties.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128100; Parties</div>
    <div class="data-pills">${ex.parties.map(deedPill).join('')}</div>
  </div>`;

  if (ex.dates.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128197; Dates</div>
    <div class="data-pills">${ex.dates.map(deedPill).join('')}</div>
  </div>`;

  if (ex.trsRefs.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128506; Township / Range / Section</div>
    <div class="data-pills">${ex.trsRefs.map(deedPill).join('')}</div>
  </div>`;

  if (ex.money.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128176; Consideration</div>
    <div class="data-pills">${ex.money.map(deedPill).join('')}</div>
  </div>`;

  if (ex.legalText) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128212; Legal Description</div>
    <div class="legal-block">${escHtml(ex.legalText)}</div>
  </div>`;

  return html || `<div class="empty-state"><div class="empty-icon">&#128196;</div><p>No structured data found.<br>Check the All Fields tab.</p></div>`;
}

function buildDeedAllFieldsTab(d) {
  const skip = ['doc_no','_trs','pdf_url'];
  let html = `<div class="detail-grid">`;
  Object.entries(d).forEach(([k,v]) => {
    if (skip.includes(k) || !v) return;
    const isLoc = k === 'Location';
    html += `<div class="detail-label">${escHtml(k)}</div>
      <div class="detail-val" style="${isLoc ? 'color:var(--accent2);font-family:monospace' : ''}">${escHtml(String(v))}</div>`;
  });
  if (d._trs && d._trs.length) html += `<div class="detail-label">TRS Refs</div>
    <div class="detail-val" style="color:#79a8e0;font-family:monospace">${d._trs.map(t=>escHtml(t.trs)).join(' | ')}</div>`;
  html += `</div>`;
  return html;
}

function switchDeedTab(name) {
  document.querySelectorAll('.deed-viewer-tab').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('dtab-' + name);
  if (btn) btn.classList.add('active');
  const map = { summary:'deedTabSummary', fields:'deedTabFields', pdf:'deedTabPdf' };
  Object.entries(map).forEach(([n,id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (n === name) { el.classList.remove('hidden'); if(n==='pdf') el.style.display='flex'; }
    else { el.classList.add('hidden'); if(n==='pdf') el.style.display=''; }
  });
}

async function saveClientDeed(docNo) {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "error"); return; }

  const clientSubj = rs.subjects.find(s => s.type === "client");
  if (!clientSubj) { showToast("Client subject disconnected", "error"); return; }

  const btn = document.getElementById("btnS2Save");
  if (btn) { btn.disabled = true; btn.innerHTML = "Saving..."; }

  try {
    const res = await apiFetch("/download", "POST", {
      doc_no:         docNo,
      grantor:        state.selectedDetail["Grantor"] || "",
      grantee:        state.selectedDetail["Grantee"] || "",
      location:       state.selectedDetail["Location"] || "",
      job_number:     rs.job_number,
      client_name:    rs.client_name,
      job_type:       rs.job_type,
      create_project: true,
      is_adjoiner:    false,
      subject_id:     clientSubj.id,
    });

    if (res.success) {
      showToast(res.skipped ? "Client deed already exists (skipped)" : "Client deed saved!", "success");
      clientSubj.deed_saved = true;
      if (res.saved_to) clientSubj.deed_path = res.saved_to;
      await persistSession();

      // Auto-open if configured
      if (!res.skipped && res.saved_to && document.getElementById("optAutoOpen")?.checked) {
        setTimeout(() => {
          apiFetch("/open-file", "POST", { path: res.saved_to }).catch(()=>{});
        }, 500);
      }
      // Automatically move to Step 3
      setTimeout(() => goToStep(3), 800);
    } else {
      showToast("Save failed: " + res.error, "error");
    }
  } catch (e) {
    showToast("Error saving: " + e.message, "error");
  } finally {
    // Always re-enable the button so the user is never stuck
    if (btn) { btn.disabled = false; btn.innerHTML = "&#11015; Save Client Deed &rarr;"; }
  }
}

/** Skip deed download and jump directly to the plat step */
function skipToStep3() {
  goToStep(3);
}

// ============================================================
// STEP 3: CLIENT PLAT
// ============================================================
async function doStep3Search() {
  const locCards = document.getElementById('s3LocalPlats');
  const kmlCards = document.getElementById('s3KmlPlats');
  const onlCards = document.getElementById('s3OnlinePlats');

  if (!state.selectedDetail) {
    const msg = '<div class="empty-state text-text3">No deed selected in Step 2. Go back and select a deed first.</div>';
    locCards.innerHTML = msg;
    if (kmlCards) kmlCards.innerHTML = msg;
    onlCards.innerHTML = msg;
    return;
  }

  locCards.innerHTML = '<div class="loading-state">Scanning local cabinets...</div>';
  if (kmlCards) kmlCards.innerHTML = '<div class="loading-state">Querying KML parcel index...</div>';
  onlCards.innerHTML  = '<div class="loading-state">Searching 1stnmtitle.com...</div>';

  try {
    const res = await apiFetch('/find-plat', 'POST', { detail: state.selectedDetail });
    if (!res.success) throw new Error(res.error);

    // ── Local Cabinet Hits ──────────────────────────────────────────────
    const cabinetHits = res.local || res.cabinet_hits || [];
    if (!cabinetHits.length) {
      locCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
        '<div class="text-3xl mb-2">\u{1F5C4}\uFE0F</div>No cabinet plats found by deed reference or name.<br><br>' +
        '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">Browse Cabinets Manually</button></div>';
    } else {
      state._cabinetHits = cabinetHits;
      locCards.innerHTML = cabinetHits.map((f, fi) =>
        '<div class="plat-item">' +
          '<div class="plat-info">' +
            '<span class="plat-name text-xs" title="' + escHtml(f.path) + '">' + escHtml(f.file) + '</span>' +
            '<span class="plat-meta">Cabinet ' + (f.cabinet||'') + ' &nbsp;\u00B7&nbsp; ' + (f.strategy||'match') + '</span>' +
          '</div>' +
          '<button class="btn btn-success btn-sm" onclick="savePlatByIndex(' + fi + ')">\u2B07 Save</button>' +
        '</div>'
      ).join('');
    }

    // ── KML Parcel Hits ─────────────────────────────────────────────────
    if (kmlCards) {
      const kmlHits = res.kml_matches || [];
      if (!kmlHits.length) {
        kmlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
          '<div class="text-3xl mb-2">\u{1F5FA}\uFE0F</div>No parcel records found in KML index.<br><br>' +
          '<span class="text-xs opacity-60">Use the \u{1F5FA}\uFE0F KML Index button to build the index from county data.</span></div>';
      } else {
        state._kmlHits = kmlHits;
        kmlCards.innerHTML = kmlHits.map((p, pi) => {
          const ct = p.centroid ? 'Lat: ' + p.centroid[1].toFixed(5) + ', Lng: ' + p.centroid[0].toFixed(5) : '';
          const btns = (p.local_files && p.local_files.length)
            ? p.local_files.map((lf, lfi) =>
                '<button class="btn btn-success btn-sm" style="font-size:10px;padding:3px 7px;white-space:nowrap" ' +
                'onclick="saveKmlLocalFile(' + pi + ',' + lfi + ')" title="' + escHtml(lf.file) + '">' +
                '\u2B07 ' + escHtml(lf.cab_ref || (lf.cabinet + '-' + lf.doc)) + '</button>'
              ).join('')
            : '<span class="text-xs text-text3 italic">No local file</span>';
          return '<div class="plat-item kml-parcel-item">' +
            '<div class="plat-info" style="flex:1">' +
              '<span class="plat-name" title="' + escHtml(ct) + '">' + escHtml(p.owner) + '</span>' +
              '<div class="kml-meta-row">' +
                (p.upc   ? '<span class="kml-chip chip-upc">UPC: ' + escHtml(p.upc) + '</span>' : '') +
                (p.book  ? '<span class="kml-chip chip-book">Bk/Pg: ' + escHtml(p.book) + '/' + escHtml(p.page) + '</span>' : '') +
                (p.cab_refs_str ? '<span class="kml-chip chip-cab">' + escHtml(p.cab_refs_str) + '</span>' : '') +
              '</div>' +
              (p.match_reason ? '<span class="plat-meta text-xs" style="color:var(--accent2)">' + escHtml(p.match_reason) + '</span>' : '') +
              (p.plat ? '<span class="plat-meta text-xs" title="' + escHtml(p.plat) + '">' +
                escHtml(p.plat.substring(0,60)) + (p.plat.length > 60 ? '\u2026' : '') + '</span>' : '') +
            '</div>' +
            '<div class="flex-col gap-1" style="min-width:80px;align-items:flex-end">' + btns + '</div>' +
          '</div>';
        }).join('');
      }
    }

    // ── Online Survey Hits ──────────────────────────────────────────────
    const surveyHits = res.online || res.survey_hits || [];
    if (!surveyHits.length) {
      onlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">No online survey records found for this grantor name.</div>';
    } else {
      onlCards.innerHTML = surveyHits.map(r =>
        '<div class="plat-item">' +
          '<div class="plat-info">' +
            '<span class="plat-name" title="' + escHtml(r.location||'') + '">' +
              escHtml((r.grantor||'').split(',')[0] || r.grantor||'') + '</span>' +
            '<span class="plat-meta">' + escHtml(r.instrument_type||'') +
              ' &nbsp;&nbsp; ' + escHtml(r.recorded_date||r.date||'') +
              ' &nbsp;&nbsp; Doc <span class="text-accent2">' + escHtml(r.doc_no) + '</span></span>' +
          '</div>' +
          '<button class="btn btn-outline btn-sm" ' +
            'onclick="saveClientPlatOnline(\'' + r.doc_no + '\',\'' + escHtml(r.location||'') + '\')">' +
            '\u2B07 Download</button>' +
        '</div>'
      ).join('');
    }

  } catch (e) {
    const errMsg = '<div class="text-danger p-3">Error: ' + e.message + '</div>';
    locCards.innerHTML = errMsg;
    if (kmlCards) kmlCards.innerHTML = errMsg;
    onlCards.innerHTML  = errMsg;
  }
}

async function saveKmlLocalFile(kmlIdx, fileIdx) {
  const rs = state.researchSession;
  if (!rs) { showToast('No active session', 'error'); return; }
  const p  = state._kmlHits && state._kmlHits[kmlIdx];
  if (!p || !p.local_files || !p.local_files[fileIdx]) { showToast('File not found', 'error'); return; }
  const lf = p.local_files[fileIdx];
  const clientSubj = rs.subjects.find(s => s.type === 'client');
  try {
    const res = await apiFetch('/save-plat', 'POST', {
      source: 'local', file_path: lf.path, filename: lf.file,
      job_number: rs.job_number, client_name: rs.client_name, job_type: rs.job_type,
      subject_id: clientSubj ? clientSubj.id : 'client'
    });
    if (res.success) {
      showToast(res.skipped ? 'Plat already exists in project' : 'Plat saved: ' + res.filename, 'success');
      if (clientSubj && !res.skipped) {
        clientSubj.plat_saved = true;
        if (res.saved_to) clientSubj.plat_path = res.saved_to;
        await persistSession();
      }
      setTimeout(() => goToStep(4), 800);
    } else { showToast('Save failed: ' + res.error, 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── KML Index Status Modal ───────────────────────────────────────────────────
async function showKmlIndexModal() {
  document.getElementById('kmlIndexOverlay').classList.remove('hidden');
  const body = document.getElementById('kmlIndexBody');
  body.innerHTML = '<div class="loading-state">Loading index status...</div>';
  try {
    const res = await apiFetch('/xml/status');
    if (!res.success) throw new Error(res.error);
    const srcRows = (res.sources || []).map(s =>
      '<tr><td class="text-xs">' + escHtml(s.file) + '</td>' +
      '<td class="text-xs text-accent2 text-right">' + s.records.toLocaleString() + '</td></tr>'
    ).join('');
    const fileRows = (res.xml_files || []).map(f =>
      '<tr><td class="text-xs">' + escHtml(f.name) + '</td>' +
      '<td class="text-xs text-text3">' + f.format.toUpperCase() + '</td>' +
      '<td class="text-xs text-right">' + f.size_mb + ' MB</td></tr>'
    ).join('');
    body.innerHTML =
      '<div class="kml-status-block ' + (res.exists ? 'kml-ok' : 'kml-warn') + '">' +
      (res.exists
        ? '<strong style="color:var(--accent2)">\u2705 Index Ready</strong> &nbsp;\u2014&nbsp; ' + (res.total||0).toLocaleString() + ' parcels<br>' +
          '<span class="text-xs text-text3">Built: ' + (res.built_at||'unknown') + ' &nbsp;\u00B7&nbsp; ' + (res.size_mb||'?') + ' MB</span>'
        : '<strong style="color:#ff7b72">\u26A0 No Index Yet</strong> \u2014 Build it below to enable KML parcel search.') +
      '</div>' +
      (res.exists && srcRows
        ? '<div class="mt-3"><div class="text-xs font-bold uppercase text-text3 mb-1">Indexed Sources</div>' +
          '<table class="data-table"><tbody>' + srcRows + '</tbody></table></div>' : '') +
      (fileRows
        ? '<div class="mt-3"><div class="text-xs font-bold uppercase text-text3 mb-1">KML / KMZ Files Available</div>' +
          '<table class="data-table"><tbody>' + fileRows + '</tbody></table></div>'
        : '<div class="empty-state text-sm mt-3">No KML/KMZ files found in Survey Data\\XML folder.</div>');
  } catch(e) {
    body.innerHTML = '<div class="text-danger p-3">Error: ' + e.message + '</div>';
  }
}

function closeKmlModal() {
  document.getElementById('kmlIndexOverlay').classList.add('hidden');
}

async function buildKmlIndex() {
  const btn = document.getElementById('btnBuildIndex');
  btn.disabled = true; btn.innerHTML = '\u23F3 Building...';
  document.getElementById('kmlIndexBody').innerHTML = '<div class="loading-state">Parsing KML/KMZ files \u2014 this may take a minute.</div>';
  try {
    const res = await apiFetch('/xml/build-index', 'POST', {});
    if (!res.success) throw new Error(res.error);
    showToast('KML index built: ' + (res.total||0).toLocaleString() + ' parcels in ' + res.elapsed_sec + 's', 'success');
    await showKmlIndexModal();
  } catch(e) {
    showToast('Build failed: ' + e.message, 'error');
    document.getElementById('kmlIndexBody').innerHTML = '<div class="text-danger p-3">Error: ' + e.message + '</div>';
  } finally {
    btn.disabled = false; btn.innerHTML = '\u26A1 Build / Rebuild Index';
  }
}


function savePlatByIndex(idx) {
  const f = state._cabinetHits && state._cabinetHits[idx];
  if (!f) { showToast('Plat not found', 'error'); return; }
  saveClientPlatLocal(f.path, f.file);
}

async function saveClientPlatLocal(filePath, filename) {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "error"); return; }
  const clientSubj = rs.subjects.find(s => s.type === "client");

  try {
    const res = await apiFetch("/save-plat", "POST", {
      source: "local", file_path: filePath, filename: filename,
      job_number: rs.job_number, client_name: rs.client_name, job_type: rs.job_type,
      subject_id: clientSubj?.id
    });
    if (res.success) {
      showToast(`Plat saved to project: ${res.filename}`, "success");
      if (clientSubj) { clientSubj.plat_saved = true; if (res.saved_to) clientSubj.plat_path = res.saved_to; }
      await persistSession();
      goToStep(4);
    } else {
      showToast("Save failed: " + res.error, "error");
    }
  } catch (e) {
    showToast("Error saving plat: " + e.message, "error");
  }
}

async function saveClientPlatOnline(docNo, loc) {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "error"); return; }
  
  const clientSubj = rs.subjects.find(s => s.type === "client");

  try {
    const res = await apiFetch("/save-plat", "POST", {
      source: "online", doc_no: docNo, location: loc,
      job_number: rs.job_number, client_name: rs.client_name, job_type: rs.job_type,
      subject_id: clientSubj.id
    });

    if (res.success) {
      showToast(res.skipped ? "Plat file already exists" : `Plat saved to project: ${res.filename}`, "success");
      clientSubj.plat_saved = true;
      if (res.saved_to) clientSubj.plat_path = res.saved_to;
      await persistSession();
      goToStep(4);
    } else {
      showToast("Download failed: " + res.error, "error");
    }
  } catch (e) {
    showToast("Error downloading plat: " + e.message, "error");
  }
}

// 
// STEP 4: ADJOINER DISCOVERY
// 
async function runAdjoinerDiscovery() {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "error"); return; }
  
  const clientSubj = rs.subjects.find(s => s.type === "client");
  if (!clientSubj || !clientSubj.deed_saved) {
    showToast("Save the client deed first", "warn");
    return;
  }

  const btn = document.getElementById("btnDiscoverAdjoiners");
  const grid = document.getElementById("s4AdjoinerGrid");
  const resArea = document.getElementById("s4DiscoveryResults");

  btn.disabled = true;
  btn.innerHTML = `<div class="spinner"></div> Scanning...`;
  resArea.classList.remove("hidden");
  grid.innerHTML = `<div class="loading-state col-span-full">Running OCR on plat and scanning online records...</div>`;
  document.getElementById("s4CountText").textContent = "...";

  try {
    const res = await apiFetch("/find-adjoiners", "POST", {
      detail: state.selectedDetail || {},
      deed_path: clientSubj.deed_path || "",
      job_number: rs.job_number,
      client_name: rs.client_name,
      job_type: rs.job_type,
    });

    if (!res.success) throw new Error(res.error);

    state.discoveredAdjoiners = res.adjoiners || [];
    state.ocrRawText = res.ocr_text || "";

    const count = state.discoveredAdjoiners.length;
    document.getElementById("s4CountText").textContent = count;

    if (!count) {
      const noDetailHint = !state.selectedDetail
        ? `<br><br><span class="text-xs" style="color:var(--text3)">&#128161; Tip: Go to <button class="link-btn" onclick="goToStep(2)">Step 2</button> and select the client deed to enable full-text scanning.</span>`
        : "";
      grid.innerHTML = `<div class="empty-state col-span-full">No adjoiners found automatically. Add manually below.${noDetailHint}</div>`;
    } else {
      let html = state.discoveredAdjoiners.map(j => `
        <div class="adjoiner-chip">
          <div class="flex-col">
            <span class="adjoiner-chip-name">${j.name}</span>
            <span class="source-tag text-text3">${j.source}</span>
          </div>
          <button class="btn btn-outline btn-sm" onclick="addFoundAdjoiner(\'${j.name.replace(/\'/g,"\\\'")}\')">+ Add</button>
        </div>
      `).join("");
      grid.innerHTML = html;
    }

  } catch (e) {
    grid.innerHTML = `<div class="text-danger col-span-full p-4">Discovery failed: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = `⚡ Re-run Scan`;
  }
}

async function addFoundAdjoiner(name) {
  const rs = state.researchSession;
  if (!rs) return;

  const exists = rs.subjects.some(s => s.type === "adjoiner" && s.name.toLowerCase() === name.toLowerCase());
  if (exists) { showToast("Already on board", "info"); return; }

  rs.subjects.push({
    id: "adj_" + Date.now() + Math.random().toString(36).substr(2, 5),
    type: "adjoiner",
    name: name,
    deed_saved: false, plat_saved: false, status: "pending", notes: ""
  });
  
  await persistSession();
  showToast(`Added ${name} to research board`, "success");
}

async function addAllDiscoveredToBoard() {
  let count = 0;
  for (const j of state.discoveredAdjoiners) {
    const exists = state.researchSession.subjects.some(s => s.type === "adjoiner" && s.name.toLowerCase() === j.name.toLowerCase());
    if (!exists) {
      state.researchSession.subjects.push({
        id: "adj_" + Date.now() + "_" + count,
        type: "adjoiner",
        name: j.name,
        deed_saved: false, plat_saved: false, status: "pending", notes: ""
      });
      count++;
    }
  }
  
  if (count > 0) {
    await persistSession();
    showToast(`Added ${count} adjoiners to Research Board`, "success");
    goToStep(5);
  } else {
    showToast("All discovered adjoiners already on board", "info");
  }
}

async function manualAddAdjoiner() {
  const inp = document.getElementById("s4ManualName");
  const name = inp.value.trim();
  if (!name || name.length < 2) return;
  
  await addFoundAdjoiner(name);
  inp.value = "";
}

// Expose Cabinet browser globally
function openGlobalCabinetBrowser() {
  document.getElementById('cabinetOverlay').classList.remove('hidden');
  browseCabinet('A'); // default
}
function closeCabinetBrowser() {
  document.getElementById('cabinetOverlay').classList.add('hidden');
}

let _cabState = { cab: 'C', filter: '', page: 1 };
async function browseCabinet(cab, page=1) {
  _cabState.cab = cab; _cabState.page = page;
  
  document.querySelectorAll('.cab-tab').forEach(b => b.classList.toggle('active', b.dataset.cab === cab));
  const body = document.getElementById('cabinetBody');
  body.innerHTML = `<div class="loading-state">Loading...</div>`;
  
  try {
    const res = await apiFetch(`/cabinet-browse?cabinet=${cab}&filter=${encodeURIComponent(_cabState.filter)}&page=${page}&per_page=50`);
    if (!res.success) throw new Error(res.error);
    
    document.getElementById('cabinetCount').textContent = `Total: ${res.total} files`;
    
    if (!res.files.length) {
      body.innerHTML = `<div class="empty-state text-sm">No files match.</div>`;
      return;
    }
    
    let html = `<table class="data-table"><tbody>`;
    res.files.forEach(f => {
      html += `
        <tr>
          <td class="text-xs">${escHtml(f.file)}</td>
          <td class="text-text3 text-xs w-16">${f.size_kb} KB</td>
          <td class="w-20"><button class="btn btn-outline btn-sm" onclick="apiFetch('/open-file','POST',{path:'${f.path.replace(/\\/g,"\\\\")}'})">Open</button></td>
        </tr>`;
    });
    html += `</tbody></table>`;
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="text-danger p-2">${e.message}</div>`;
  }
}
function filterCabinet() {
  _cabState.filter = document.getElementById('cabinetFilter').value.trim();
  browseCabinet(_cabState.cab, 1);
}
// 
// STEP 5: ADJOINER RESEARCH BOARD
// 
function renderResearchBoard() {
  const grid = document.getElementById("s5ResearchGrid");
  const rs = state.researchSession;
  if (!rs || !rs.subjects.length) {
    grid.innerHTML = `<div class="empty-state">No subjects on board. Add adjoiners in Step 4.</div>`;
    return;
  }

  const adjoiners = rs.subjects.filter(s => s.type === "adjoiner");
  const client    = rs.subjects.find(s => s.type === "client");
  let html = "";

  // Client card first
  if (client) html += buildSubjectCard(client, rs);

  adjoiners.forEach(s => { html += buildSubjectCard(s, rs); });
  grid.innerHTML = html;
}

function buildSubjectCard(s, rs) {
  const isClient = s.type === "client";
  const st = s.status || "pending";
  const accentColor = isClient ? "var(--accent)" : "#7a4f9a";

  const deedChip = s.deed_saved
    ? `<span class="chip chip-done"> Deed</span>${s.deed_path ? `<button class="btn-icon-sm ml-1" title="Open deed" onclick="openFile('${s.deed_path.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}')"></button>` : ""}`
    : `<span class="chip chip-todo"> Deed</span>`;
  const platChip = s.plat_saved
    ? `<span class="chip chip-done"> Plat</span>${s.plat_path ? `<button class="btn-icon-sm ml-1" title="Open plat" onclick="openFile('${s.plat_path.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}')"></button>` : ""}`
    : `<span class="chip chip-todo"> Plat</span>`;

  const statusColors = { done: "#1a3028;color:#56d3a0", na: "#281a1a;color:#888", pending: "var(--bg3);color:var(--text3)" };
  const statusLabel  = { done: " Done", na: " N/A", pending: " Pending" }[st];

  return `
    <div class="adjoiner-card status-${st}" id="card_${s.id}" style="border-top-color:${accentColor}">
      <div class="adjoiner-card-header">
        <div class="flex-col gap-1">
          <strong style="font-size:15px">${escHtml(s.name)}</strong>
          <span style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:${isClient ? 'var(--accent2)' : '#b080e0'}">
            ${isClient ? "★ Client" : " Adjoiner"}
          </span>
        </div>
        <button class="chip" style="background:${statusColors[st]};border-color:transparent;cursor:pointer;padding:4px 12px;border-radius:12px;font-size:11px;font-weight:700"
          onclick="cycleSubjectStatus('${s.id}')">${statusLabel}</button>
      </div>

      <div class="adjoiner-card-body">
        <!-- Status chips -->
        <div class="row-layout gap-2 flex-wrap">
          ${deedChip}
          ${platChip}
          ${buildExceptionFlags(s)}
        </div>

        <!-- Notes -->
        <input class="inp" style="padding:6px 10px;font-size:12px"
          placeholder="Notes..." value="${escHtml(s.notes || "")}"
          onchange="saveNote('${s.id}', this.value)">

        <!-- Chain tracker -->
        ${buildChainTracker(s)}

        <!-- Actions -->
        <div class="row-layout gap-2 flex-wrap border-t pt-2" style="border-color:var(--border)">
          <button class="btn btn-outline btn-sm flex-1" onclick="searchForSubject('${escHtml(s.name.split(",")[0]).replace(/'/g,"\\'")}')">
             Search
          </button>
          ${!isClient ? `
          <button class="btn btn-outline btn-sm flex-1" onclick="saveAdjDeed('${s.id}')">
            ⬇ Save Deed
          </button>
          <button class="btn btn-outline btn-sm flex-1" onclick="saveAdjPlat('${s.id}')">
             Find Plat
          </button>
          <button class="btn btn-outline btn-sm" style="color:#ff7b72" onclick="removeSubject('${s.id}')">✗</button>
          ` : ""}
        </div>
      </div>
    </div>`;
}

function buildExceptionFlags(s) {
  const FLAGS = [
    { key: "mineral", label: " Mineral" },
    { key: "easement", label: " Easement" },
    { key: "roe", label: " ROW" },
    { key: "access", label: " Access" },
  ];
  const exc = s.exceptions || {};
  return `<div class="row-layout gap-1 flex-wrap">` +
    FLAGS.map(f => `<span class="exc-chip ${exc[f.key] ? "exc-active" : "exc-off"}"
      onclick="toggleException('${s.id}','${f.key}')" title="Toggle ${f.label}">${f.label}</span>`).join("") +
  `</div>`;
}

function buildChainTracker(s) {
  const years = (s.chain_years || []).sort((a,b) => b-a);
  const goal  = s.chain_goal || null;
  const reached = goal && years.length && Math.min(...years) <= goal;

  return `<div class="chain-box">
    <div class="row-layout justify-between mb-1">
      <span class="text-xs text-text3 font-bold uppercase">Chain of Title</span>
      <div class="row-layout gap-2">
        ${goal
          ? `<span class="text-xs" style="color:${reached ? "#56d3a0" : "#e3c55a"}">${reached ? "" : ""} Goal: ${goal}</span>`
          : `<button class="link-btn" onclick="setChainGoal('${s.id}')">+ Set goal year</button>`}
        <button class="link-btn text-accent2" onclick="addChainYear('${s.id}')">+ Add year</button>
      </div>
    </div>
    ${years.length
      ? `<div class="chain-years">${years.map(y => `<span class="year-chip" onclick="removeChainYear('${s.id}',${y})" title="Remove">${y}</span>`).join("")}</div>`
      : `<div class="text-xs text-text3 italic">No years logged</div>`}
  </div>`;
}

//  Search from board 
function searchForSubject(name) {
  if (document.getElementById("s2SearchName")) {
    document.getElementById("s2SearchName").value = name;
  }
  goToStep(2);
  setTimeout(() => doStep2Search(), 300);
}

async function saveAdjDeed(subjId) {
  if (!state.selectedDoc || !state.selectedDetail) {
    showToast("Select a deed in Step 2 first, then return here", "warn");
    goToStep(2);
    return;
  }
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  if (!subj) return;

  try {
    const res = await apiFetch("/download", "POST", {
      doc_no:         state.selectedDoc.doc_no,
      grantor:        state.selectedDetail["Grantor"] || "",
      grantee:        state.selectedDetail["Grantee"] || "",
      location:       state.selectedDetail["Location"] || "",
      job_number:     rs.job_number,
      client_name:    rs.client_name,
      job_type:       rs.job_type,
      create_project: true,
      is_adjoiner:    true,
      adjoiner_name:  subj.name,
      subject_id:     subjId,
    });

    if (res.success) {
      subj.deed_saved = true;
      if (res.saved_to) subj.deed_path = res.saved_to;
      await persistSession();
      showToast(`Deed saved for ${subj.name}`, "success");
      renderResearchBoard();
    } else {
      showToast("Save failed: " + res.error, "error");
    }
  } catch(e) {
    showToast("Error: " + e.message, "error");
  }
}

async function saveAdjPlat(subjId) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  if (!subj) return;

  if (!state.selectedDetail) {
    showToast("Go to Step 2, search & select the adjoiner's deed, then come back", "warn");
    return;
  }

  try {
    const res = await apiFetch("/find-plat", "POST", { detail: state.selectedDetail });
    if (!res.success) { showToast("Plat search error: " + res.error, "error"); return; }

    // Try to auto-save — first check direct cabinet hits, then KML-linked local files
    const _localHits = res.local || [];
    if (_localHits.length) {
      const f = _localHits[0];
      const saveRes = await apiFetch("/save-plat", "POST", {
        source: "local", file_path: f.path, filename: f.file,
        job_number: rs.job_number, client_name: rs.client_name,
        job_type: rs.job_type, subject_id: subjId, is_adjoiner: true, adjoiner_name: subj.name
      });
      if (saveRes.success) {
        subj.plat_saved = true;
        if (saveRes.saved_to) subj.plat_path = saveRes.saved_to;
        await persistSession();
        showToast(`Plat saved for ${subj.name}`, "success");
        renderResearchBoard();
        return;
      }
    }
    // Also try KML-matched local cabinet files
    for (const km of (res.kml_matches || [])) {
      if (km.local_files && km.local_files.length) {
        const f = km.local_files[0];
        const saveRes = await apiFetch("/save-plat", "POST", {
          source: "local", file_path: f.path, filename: f.file,
          job_number: rs.job_number, client_name: rs.client_name,
          job_type: rs.job_type, subject_id: subjId, is_adjoiner: true, adjoiner_name: subj.name
        });
        if (saveRes.success) {
          subj.plat_saved = true;
          if (saveRes.saved_to) subj.plat_path = saveRes.saved_to;
          await persistSession();
          showToast(`Plat saved for ${subj.name} (KML match)`, "success");
          renderResearchBoard();
          return;
        }
      }
    }
    showToast("No local plat found. Go to Step 3 to search manually.", "warn");
  } catch(e) {
    showToast("Error: " + e.message, "error");
  }
}

//  Board persistence helpers 
async function removeSubject(id) {
  state.researchSession.subjects = state.researchSession.subjects.filter(s => s.id !== id);
  await persistSession();
  renderResearchBoard();
}

async function cycleSubjectStatus(id) {
  const subj = state.researchSession?.subjects.find(s => s.id === id);
  if (!subj) return;
  const order = ["pending", "done", "na"];
  const cur = subj.status || "pending";
  subj.status = order[(order.indexOf(cur) + 1) % order.length];
  await persistSession();
  renderResearchBoard();
}

async function saveNote(id, text) {
  const subj = state.researchSession?.subjects.find(s => s.id === id);
  if (!subj) return;
  subj.notes = text;
  await persistSession();
}

async function toggleException(subjId, key) {
  const subj = state.researchSession?.subjects.find(s => s.id === subjId);
  if (!subj) return;
  if (!subj.exceptions) subj.exceptions = {};
  subj.exceptions[key] = !subj.exceptions[key];
  await persistSession();
  renderResearchBoard();
}

function addChainYear(subjId) {
  const y = parseInt(prompt("Enter deed year to add to chain:"));
  if (!y || y < 1600 || y > 2100) return;
  const subj = state.researchSession?.subjects.find(s => s.id === subjId);
  if (!subj) return;
  if (!subj.chain_years) subj.chain_years = [];
  if (!subj.chain_years.includes(y)) subj.chain_years.push(y);
  persistSession().then(() => renderResearchBoard());
}

function setChainGoal(subjId) {
  const y = parseInt(prompt("Need chain back to year:"));
  if (!y || y < 1600 || y > 2100) return;
  const subj = state.researchSession?.subjects.find(s => s.id === subjId);
  if (!subj) return;
  subj.chain_goal = y;
  persistSession().then(() => renderResearchBoard());
}

function removeChainYear(subjId, yr) {
  const subj = state.researchSession?.subjects.find(s => s.id === subjId);
  if (!subj || !subj.chain_years) return;
  subj.chain_years = subj.chain_years.filter(y => y !== yr);
  persistSession().then(() => renderResearchBoard());
}

async function bulkSearchAdjoiners() {
  const rs = state.researchSession;
  if (!rs) return;
  const pending = rs.subjects.filter(s => s.type === "adjoiner" && !s.deed_saved);
  if (!pending.length) { showToast("No pending adjoiners", "info"); return; }
  if (!state.loggedIn) { showToast("Not connected to records", "warn"); return; }

  showToast(`Searching ${pending.length} adjoiners...`, "info");
  for (const subj of pending) {
    const ln = subj.name.split(",")[0].trim();
    try {
      const res = await apiFetch("/search", "POST", { name: ln, operator: "begins with" });
      const count = res.results?.length || 0;
      const card = document.getElementById(`card_${subj.id}`);
      if (card) {
        const indicator = document.createElement("div");
        indicator.className = "text-xs mt-1 " + (count > 0 ? "text-accent2" : "text-text3");
        indicator.textContent = count > 0 ? ` ${count} record${count !== 1 ? "s" : ""} found` : "No records found";
        const header = card.querySelector(".adjoiner-card-header");
        if (header) header.appendChild(indicator);
      }
    } catch {}
    await new Promise(r => setTimeout(r, 300));
  }
  showToast("Bulk search complete", "success");
}

async function openFolderForContext() {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "warn"); return; }
  try {
    const drv    = await apiFetch("/drive-status");
    const drive  = (drv.drive_ok && drv.drive) ? drv.drive : "F";
    const rstart = Math.floor(parseInt(rs.job_number) / 100) * 100;
    const last   = rs.client_name.split(",")[0].trim();
    const path   = `${drive}:\\AI DATA CENTER\\Survey Data\\${rstart}-${rstart+99}\\${rs.job_number} ${rs.client_name}\\${rs.job_number}-01-${rs.job_type} ${last}\\E Research`;
    apiFetch("/open-folder", "POST", { path }).catch(()=>{});
    showToast("Opening E Research folder...", "info");
  } catch(e) {
    showToast("Could not resolve drive path", "warn");
  }
}

function openFile(filePath) {
  apiFetch("/open-file", "POST", { path: filePath })
    .then(r => { if (!r.success) showToast("File not found", "error"); })
    .catch(() => showToast("Could not open file", "error"));
}
// 
// STEP 6: BOUNDARY LINES (DXF)
// 
function switchS6Tab(tab) {
  ["calls","parcels","options"].forEach(t => {
    document.getElementById(`s6Tab${t.charAt(0).toUpperCase()+t.slice(1)}`)?.classList.toggle("hidden", t !== tab);
    const btn = document.querySelector(`[onclick="switchS6Tab('${t}')"]`);
    if (btn) btn.classList.toggle("active", t === tab);
  });
  if (tab === "parcels") renderS6ParcelList();
}

async function reparseClientCallsFromSession() {
  if (!state.selectedDetail) {
    showToast("No deed detail loaded  search in Step 2 first", "warn");
    return;
  }
  try {
    const res = await apiFetch("/parse-calls", "POST", { detail: state.selectedDetail });
    if (!res.success) { showToast("Parse error: " + res.error, "error"); return; }
    state.parsedCalls = res.calls || [];
    renderS6CallsTable(res);
    showToast(`${res.count} call${res.count !== 1 ? "s" : ""} parsed from deed`, res.count ? "success" : "warn");
  } catch(e) {
    showToast("Error: " + e.message, "error");
  }
}

async function parseS6Text() {
  const txt = document.getElementById("s6PasteText").value.trim();
  if (!txt) { showToast("Paste deed text first", "warn"); return; }
  try {
    const res = await apiFetch("/parse-calls", "POST", { text: txt });
    if (!res.success) { showToast("Parse error: " + res.error, "error"); return; }
    state.parsedCalls = res.calls || [];
    renderS6CallsTable(res);
    showToast(`${res.count} calls parsed`, res.count ? "success" : "warn");
  } catch(e) {
    showToast("Error: " + e.message, "error");
  }
}

function renderS6CallsTable(res) {
  const tbody = document.getElementById("s6CallsTbody");
  const closureBar = document.getElementById("s6ClosureBar");
  const closureText = document.getElementById("s6ClosureText");
  const calls = state.parsedCalls;

  if (!calls.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">No calls parsed yet.</td></tr>`;
    closureBar.classList.add("hidden");
    updateS6Sketch();
    return;
  }

  const err = res.closure_err ?? 0;
  const cls = err < 0.5 ? "text-accent2" : err < 2 ? "text-gold" : "text-danger";
  closureBar.classList.remove("hidden");
  closureBar.className = `closure-bar ${err < 0.5 ? "bg-green" : err < 2 ? "bg-gold" : "bg-red"}`;
  closureText.innerHTML = `<span class="${cls}">${err < 0.01 ? " Perfect closure" : ` ${err.toFixed(4)} ft error`}</span> &nbsp;&nbsp; ${calls.length} calls`;

  tbody.innerHTML = calls.map((c, i) => `
    <tr class="call-row">
      <td class="text-text3 text-center">${i+1}</td>
      <td><input class="inp" style="font-family:monospace;font-size:11px;padding:4px 6px" value="${escHtml(c.bearing_label)}"
        onchange="updateCallField(${i},'bearing_label',this.value)"></td>
      <td><input class="inp" type="number" step="0.001" style="font-size:11px;padding:4px 6px" value="${c.distance}"
        onchange="updateCallField(${i},'distance',parseFloat(this.value)||0)"></td>
      <td><button class="btn btn-outline btn-sm" style="color:#ff7b72;padding:2px 8px" onclick="deleteCall(${i})">✗</button></td>
    </tr>
  `).join("");

  updateS6Sketch();
}

function updateCallField(idx, field, value) {
  if (!state.parsedCalls[idx]) return;
  state.parsedCalls[idx][field] = value;
  if (field === "bearing_label") {
    const m = value.trim().toUpperCase().match(/^([NS])(\d+)°(\d+)'(\d+)"([EW])$/);
    if (m) {
      const [,ns,deg,mn,sec,ew] = m;
      let az = +deg + +mn/60 + +sec/3600;
      if (ns==="S"&&ew==="E") az=180-az;
      else if(ns==="S"&&ew==="W") az=180+az;
      else if(ns==="N"&&ew==="W") az=360-az;
      state.parsedCalls[idx].azimuth = +az.toFixed(6);
    }
  }
  recalcS6Closure();
  updateS6Sketch();
}

function deleteCall(idx) {
  state.parsedCalls.splice(idx, 1);
  renderS6CallsTable({ closure_err: 0 });
}

function clearAllCalls() {
  state.parsedCalls = [];
  renderS6CallsTable({});
}

function addManualCall() {
  state.parsedCalls.push({ bearing_label: "N00°00'00\"E", azimuth: 0, distance: 0, bearing_raw: "" });
  renderS6CallsTable({ closure_err: 0 });
}

function recalcS6Closure() {
  const calls = state.parsedCalls;
  if (!calls.length) return;
  let x=0, y=0;
  calls.forEach(c => { const az=c.azimuth*Math.PI/180; x+=c.distance*Math.sin(az); y+=c.distance*Math.cos(az); });
  const err = Math.hypot(x,y);
  const txt = document.getElementById("s6ClosureText");
  if (txt) {
    const cls = err<0.5 ? "text-accent2" : err<2 ? "text-gold" : "text-danger";
    txt.innerHTML = `<span class="${cls}">${err<0.01 ? " Perfect closure" : ` ${err.toFixed(4)} ft`}</span> &nbsp;&nbsp; ${calls.length} calls`;
  }
}

//  Parcels (Adjoiner boundaries) 
function renderS6ParcelList() {
  const wrap = document.getElementById("s6ParcelList");
  let html = `
    <div class="parcel-client-card">
      <div class="row-layout justify-between">
        <span class="badge badge-local">★ Client</span>
        <span class="text-xs text-text3">${state.parsedCalls.length} calls</span>
      </div>
      <div class="text-xs text-text3 mt-1">Source: Calls tab (above)</div>
    </div>`;

  state.adjoinParcels.forEach((p, pi) => {
    html += `
      <div class="parcel-card">
        <div class="row-layout justify-between mb-2">
          <span class="badge" style="background:rgba(122,79,154,.15);color:#b080e0;border-color:#7a4f9a66"> ${escHtml(p.label)}</span>
          <div class="row-layout gap-2">
            <span class="text-xs text-text3">${p.calls.length} calls ${p.calls.length ? "" : ""}</span>
            <button class="btn btn-outline btn-sm" style="color:#ff7b72;padding:2px 8px" onclick="removeAdjoinerParcel(${pi})">✗</button>
          </div>
        </div>
        ${p.deed_path
          ? `<button class="btn btn-outline btn-sm w-full mb-1" onclick="extractCallsFromPdf(${pi},'${p.deed_path.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}')"> Extract Calls from Deed PDF</button>`
          : `<span class="text-xs text-text3">No deed saved yet</span>`}
        <textarea class="inp mt-1" rows="2" id="adjText${pi}" style="font-size:11px;font-family:monospace;resize:vertical"
          placeholder="Or paste deed text here..."></textarea>
        <button class="btn btn-outline btn-sm mt-1 w-full" onclick="parseAdjoinerText(${pi})">Parse Text</button>
      </div>`;
  });

  if (!state.adjoinParcels.length) {
    html += `<div class="empty-state text-sm mt-2">No adjoiner parcels. Click "Auto-populate from Board" above.</div>`;
  }

  wrap.innerHTML = html;
}

function autoPopulateAdjoiners() {
  if (!state.researchSession) { showToast("Load a session first", "warn"); return; }
  const adjs = state.researchSession.subjects.filter(s => s.type === "adjoiner");
  if (!adjs.length) { showToast("No adjoiners on the research board", "info"); return; }
  let added = 0;
  adjs.forEach(subj => {
    if (!state.adjoinParcels.some(p => p.label.toLowerCase() === subj.name.toLowerCase())) {
      state.adjoinParcels.push({ label: subj.name, layer: "ADJOINERS", calls: [], start_x: 0, start_y: 0, deed_path: subj.deed_path||"", plat_path: subj.plat_path||"", extracting: false });
      added++;
    }
  });
  renderS6ParcelList();
  showToast(added ? `${added} parcels added` : "All adjoiners already in list", added ? "success" : "info");
}

function addAdjoinerParcel() {
  const suggestions = (state.researchSession?.subjects||[]).filter(s=>s.type==="adjoiner").map(s=>s.name);
  const label = prompt("Adjoiner label:\n" + (suggestions.length ? "Suggestions: " + suggestions.join(", ") : "(none)"), suggestions[0]||"Adjoiner");
  if (!label) return;
  const subj = (state.researchSession?.subjects||[]).find(s=>s.type==="adjoiner"&&s.name.toLowerCase()===label.toLowerCase());
  state.adjoinParcels.push({ label, layer:"ADJOINERS", calls:[], start_x:0, start_y:0, deed_path:subj?.deed_path||"", plat_path:subj?.plat_path||"", extracting:false });
  renderS6ParcelList();
}

function clearAllParcels() {
  if (!state.adjoinParcels.length) return;
  if (!confirm(`Remove all ${state.adjoinParcels.length} parcel(s)?`)) return;
  state.adjoinParcels = [];
  renderS6ParcelList();
}

function removeAdjoinerParcel(idx) {
  state.adjoinParcels.splice(idx, 1);
  renderS6ParcelList();
}

async function parseAdjoinerText(idx) {
  const txt = document.getElementById(`adjText${idx}`)?.value.trim();
  if (!txt) { showToast("Paste text first", "warn"); return; }
  const res = await apiFetch("/parse-calls", "POST", { text: txt });
  if (!res.success) { showToast("Parse error: " + res.error, "error"); return; }
  state.adjoinParcels[idx].calls = res.calls;
  renderS6ParcelList();
  showToast(`${res.count} calls parsed for ${state.adjoinParcels[idx].label}`, "success");
}

async function extractCallsFromPdf(idx, pdfPath) {
  if (!pdfPath) { showToast("No saved PDF path", "warn"); return; }
  state.adjoinParcels[idx].extracting = true;
  renderS6ParcelList();
  try {
    const res = await apiFetch("/extract-calls-from-pdf", "POST", { pdf_path: pdfPath });
    if (!res.success) { showToast("Extract failed: " + res.error, "error"); return; }
    state.adjoinParcels[idx].calls = res.calls;
    showToast(res.count ? `${res.count} calls from ${res.filename}` : "No metes & bounds found  paste manually", res.count ? "success" : "warn");
  } catch(e) {
    showToast("Error: " + e.message, "error");
  } finally {
    state.adjoinParcels[idx].extracting = false;
    renderS6ParcelList();
  }
}

//  DXF Generation 
async function doGenerateDxf() {
  const rs = state.researchSession;
  if (!rs) { showToast("Load a session first", "warn"); return; }
  if (!state.parsedCalls.length && !state.adjoinParcels.some(p=>p.calls.length)) {
    showToast("No boundary calls to generate", "warn"); return;
  }

  const btn = document.getElementById("btnGenerateDxf");
  const status = document.getElementById("s6GenerateStatus");
  btn.disabled = true;
  btn.innerHTML = "Generating...";
  status.textContent = "";

  const parcels = [];
  if (state.parsedCalls.length) {
    parcels.push({ label:`Client  ${rs.client_name}`, layer:"CLIENT", calls:state.parsedCalls, start_x:0, start_y:0 });
  }
  state.adjoinParcels.forEach(p => {
    if (p.calls.length) parcels.push({ label:p.label, layer:p.layer||"ADJOINERS", calls:p.calls, start_x:p.start_x||0, start_y:p.start_y||0 });
  });

  const options = {
    draw_boundary:   document.getElementById("optDrawBoundary")?.checked ?? true,
    draw_labels:     document.getElementById("optDrawLabels")?.checked   ?? true,
    draw_endpoints:  document.getElementById("optDrawEndpoints")?.checked ?? false,
    label_size:      parseFloat(document.getElementById("optLabelSize")?.value) || 2.0,
    close_tolerance: parseFloat(document.getElementById("optCloseTol")?.value)  || 0.5,
  };

  try {
    const res = await apiFetch("/generate-dxf", "POST", {
      job_number: rs.job_number, client_name: rs.client_name, job_type: rs.job_type, parcels, options
    });
    if (!res.success) { showToast("DXF failed: " + res.error, "error"); status.textContent = "Error: " + res.error; return; }

    showToast(` DXF saved: ${res.filename}`, "success");
    status.innerHTML = `<span class="text-accent2"> ${escHtml(res.filename)}</span>`;
    setTimeout(() => {
      const dir = res.saved_to.substring(0, res.saved_to.lastIndexOf("\\"));
      apiFetch("/open-folder", "POST", { path: dir }).catch(()=>{});
    }, 500);
  } catch(e) {
    showToast("Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Generate &amp; Save DXF";
  }
}

//  SVG Sketch 
function updateS6Sketch() {
  const calls = state.parsedCalls;
  const sketchWrap = document.getElementById("s6SketchWrap");
  const noSketch   = document.getElementById("s6NoSketch");

  if (!calls.length) {
    sketchWrap.classList.add("hidden");
    noSketch.classList.remove("hidden");
    document.getElementById("s6AreaStats").textContent = "";
    return;
  }

  sketchWrap.classList.remove("hidden");
  noSketch.classList.add("hidden");

  let x=0, y=0;
  const pts = [[0,0]];
  calls.forEach(c => {
    const az = c.azimuth*Math.PI/180;
    x += c.distance*Math.sin(az);
    y += c.distance*Math.cos(az);
    pts.push([+x.toFixed(4), +y.toFixed(4)]);
  });

  // Area (Shoelace) & Perimeter
  let area=0, perim=0;
  for (let i=0; i<pts.length-1; i++) {
    area += pts[i][0]*pts[i+1][1] - pts[i+1][0]*pts[i][1];
    perim += Math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1]);
  }
  const last = pts[pts.length-1];
  area += last[0]*pts[0][1] - pts[0][0]*last[1];
  area = Math.abs(area)/2;

  document.getElementById("s6AreaStats").innerHTML =
    `<span class="text-accent2 font-bold">${(area/43560).toFixed(4)} ac</span> &nbsp;&nbsp; ${area.toFixed(0)} sq ft &nbsp;&nbsp; Perim: ${perim.toFixed(1)} ft`;

  // SVG
  const svg = document.getElementById("s6SketchSvg");
  const W = svg.clientWidth||420, H=300;
  svg.setAttribute("viewBox",`0 0 ${W} ${H}`);

  const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const pad=28, scaleX=(maxX===minX)?1:(W-pad*2)/(maxX-minX), scaleY=(maxY===minY)?1:(H-pad*2)/(maxY-minY);
  const scale=Math.min(scaleX,scaleY);
  const tx=p=>(p[0]-minX)*scale+pad, ty=p=>H-((p[1]-minY)*scale+pad);

  const polyPts = pts.map(p=>`${tx(p).toFixed(1)},${ty(p).toFixed(1)}`).join(" ");
  const isClosed = Math.hypot(last[0]-pts[0][0],last[1]-pts[0][1])<0.5;

  let s = `<rect width="${W}" height="${H}" fill="rgba(0,0,0,0.1)" rx="4"/>`;
  s += `<line x1="0" y1="${H/2}" x2="${W}" y2="${H/2}" stroke="#ffffff06" stroke-width="1"/>`;
  s += `<line x1="${W/2}" y1="0" x2="${W/2}" y2="${H}" stroke="#ffffff06" stroke-width="1"/>`;
  if (!isClosed) s += `<line x1="${tx(last).toFixed(1)}" y1="${ty(last).toFixed(1)}" x2="${tx(pts[0]).toFixed(1)}" y2="${ty(pts[0]).toFixed(1)}" stroke="#ff7b72" stroke-width="1.5" stroke-dasharray="4,3" opacity=".7"/>`;
  s += `<polygon points="${polyPts}" fill="rgba(45,138,110,0.1)" stroke="#2d8a6e" stroke-width="2" stroke-linejoin="round"/>`;
  s += `<circle cx="${tx(pts[0]).toFixed(1)}" cy="${ty(pts[0]).toFixed(1)}" r="5" fill="#2d8a6e" stroke="#56d3a0" stroke-width="1.5"/>`;
  if (!isClosed) s += `<circle cx="${tx(last).toFixed(1)}" cy="${ty(last).toFixed(1)}" r="5" fill="#ff7b72" opacity=".8"/>`;
  // North arrow
  s += `<text x="${W-16}" y="22" font-size="11" fill="#79a8e0" font-family="monospace" text-anchor="middle">N</text>`;
  s += `<line x1="${W-16}" y1="26" x2="${W-16}" y2="42" stroke="#79a8e0" stroke-width="1.5"/>`;
  s += `<polygon points="${W-16},26 ${W-20},34 ${W-12},34" fill="#79a8e0"/>`;
  svg.innerHTML = s;
}
// 
// SETTINGS MODAL
// 
function showSettingsModal() {
  document.getElementById("settingsOverlay").classList.remove("hidden");
  loadDriveStatus(); // refresh drive status every time modal opens
}
function closeSettingsModal() {
  document.getElementById("settingsOverlay").classList.add("hidden");
}

async function loadDriveStatus() {
  const dot  = document.getElementById("driveStatusDot");
  const text = document.getElementById("driveStatusText");
  if (!dot || !text) return;
  text.textContent = "Checking...";
  dot.style.background = "var(--text3)";
  try {
    const res = await apiFetch("/drive-status");
    updateDriveStatusUI(res);
  } catch(e) {
    text.textContent = "Cannot reach server";
    dot.style.background = "var(--danger)";
  }
}

function updateDriveStatusUI(res) {
  const dot  = document.getElementById("driveStatusDot");
  const text = document.getElementById("driveStatusText");
  if (!dot || !text) return;
  if (res.drive_ok) {
    dot.style.background = "var(--success2)";
    dot.style.boxShadow  = "0 0 6px var(--success2)";
    text.innerHTML = `<span style="color:var(--accent2);font-weight:700">${res.drive}:\\</span> &nbsp; <span style="color:var(--text3);font-size:12px">${res.survey_path}</span>`;
    document.getElementById("driveOverrideInput").value = res.drive || "";
  } else {
    dot.style.background = "var(--danger)";
    dot.style.boxShadow  = "none";
    text.innerHTML = `<span style="color:#ff7b72">Drive not found</span> <span style="color:var(--text3);font-size:12px"> — plug in the drive then click ⟳ Rescan</span>`;
  }
}

async function rescanDrive() {
  const btn  = document.getElementById("btnRescanDrive");
  const text = document.getElementById("driveStatusText");
  btn.disabled = true;
  btn.textContent = "Scanning...";
  text.textContent = "Scanning all drives...";
  try {
    const res = await apiFetch("/drive-status?rescan=1");
    updateDriveStatusUI(res);
    if (res.drive_ok) showToast(`Drive found: ${res.drive}:\\`, "success");
    else showToast("Drive not found. Plug it in and try again.", "warn");
  } catch(e) {
    showToast("Scan error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "⟳ Rescan";
  }
}

async function pinDrive(clear = false) {
  const letter = clear ? "" : (document.getElementById("driveOverrideInput")?.value.trim() || "");
  try {
    const res = await apiFetch("/drive-override", "POST", { drive: letter });
    if (res.success) {
      await loadDriveStatus();
      showToast(letter ? `Drive pinned to ${letter}:\\` : "Drive set to auto-detect", "success");
    } else {
      showToast("Override failed: " + res.error, "error");
    }
  } catch(e) {
    showToast("Error: " + e.message, "error");
  }
}

async function saveConfig() {
  const url  = document.getElementById("cfgUrl").value.trim();
  const user = document.getElementById("cfgUser").value.trim();
  const pass = document.getElementById("cfgPass").value;
  const status = document.getElementById("cfgStatus");

  if (!user || !pass) { showToast("Enter username and password", "warn"); return; }

  const btn = document.getElementById("btnSaveConfig");
  btn.disabled = true;
  btn.innerHTML = "Connecting...";
  status.textContent = "";

  try {
    const res = await apiFetch("/config", "POST", {
      firstnm_url: url, firstnm_user: user, firstnm_pass: pass
    });
    if (!res.success) { showToast("Config save failed: " + res.error, "error"); return; }

    // Now login
    const loginRes = await apiFetch("/login", "POST", { url, username: user, password: pass });
    if (loginRes.success) {
      state.loggedIn = true;
      setStatusDot("online", "Connected");
      showToast("Connected to records!", "success");
      status.textContent = " Connected";
      closeSettingsModal();
    } else {
      showToast("Login failed: " + loginRes.error, "error");
      status.textContent = "Login failed: " + loginRes.error;
    }
  } catch(e) {
    showToast("Connection error: " + e.message, "error");
    status.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Connect";
  }
}

// 
// LOGIN & CONNECTION
// 
async function checkLogin() {
  try {
    const url  = document.getElementById("cfgUrl").value.trim();
    const user = document.getElementById("cfgUser").value.trim();
    const pass = document.getElementById("cfgPass").value;
    if (!user || !pass) {
      setStatusDot("offline", "Click Settings to connect");
      // Only auto-open modal on truly first run (no stored credentials)
      showSettingsModal();
      return;
    }
    setStatusDot("loading", "Connecting...");
    const res = await apiFetch("/login", "POST", { url, username: user, password: pass });
    if (res.success) {
      state.loggedIn = true;
      setStatusDot("online", "Connected");
    } else {
      setStatusDot("offline", "Login failed  check Settings");
      // Don't auto-open modal; user can click Settings button
      showToast("Login failed: " + (res.error || "Check username/password in Settings"), "warn");
    }
  } catch(e) {
    setStatusDot("offline", "Offline");
  }
}

function setStatusDot(mode, text) {
  const dot  = document.querySelector(".status-dot");
  const span = document.getElementById("statusText");
  if (dot)  dot.className = `status-dot ${mode}`;
  if (span) span.textContent = text;
}

// 
// GLOBAL PROGRESS FOOTER
// 
function updateGlobalProgress() {
  const rs = state.researchSession;
  if (!rs) return;

  const all    = rs.subjects;
  const deeds  = all.filter(s => s.deed_saved).length;
  const plats  = all.filter(s => s.plat_saved).length;
  const total  = all.length * 2; // each subject needs deed + plat
  const done   = deeds + plats;
  const pct    = total > 0 ? Math.round((done / total) * 100) : 0;

  document.getElementById("statDeeds").textContent = `${deeds}/${all.length}`;
  document.getElementById("statPlats").textContent = `${plats}/${all.length}`;
  document.getElementById("globalProgressFill").style.width = pct + "%";
}

// 
// EXPORT
// 
async function exportSession() {
  const rs = state.researchSession;
  if (!rs) { showToast("No session loaded", "warn"); return; }
  const headers = ["Job#","Name","Type","Deed","Plat","Status","Notes"];
  const rows = rs.subjects.map(s => [
    rs.job_number, s.name, s.type,
    s.deed_saved?"Yes":"No", s.plat_saved?"Yes":"No",
    s.status||"pending", (s.notes||"").replace(/"/g,'""')
  ]);
  const csv = [headers,...rows].map(r=>r.map(v=>`"${v}"`).join(",")).join("\n");
  const blob = new Blob([csv],{type:"text/csv"});
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href=url; a.download=`Job${rs.job_number}_Research.csv`; a.click();
  URL.revokeObjectURL(url);
  showToast("CSV exported", "success");
}

// 
// UTILITIES
// 
function escHtml(str) {
  return String(str ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function getTypeClass(type) {
  const t = (type||"").toLowerCase();
  if (t.includes("deed")||t.includes("warranty")||t.includes("quitclaim")) return "badge-deed";
  if (t.includes("mortgage")||t.includes("assignment")) return "badge-online";
  return "badge-other";
}

async function apiFetch(path, method="GET", body=null) {
  const opts = { method, headers: {"Content-Type":"application/json"} };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

//  Toast 
let _toastEl;
function showToast(msg, type="info") {
  if (!_toastEl) {
    _toastEl = document.createElement("div");
    _toastEl.style.cssText = "position:fixed;bottom:80px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:360px;pointer-events:none";
    document.body.appendChild(_toastEl);
  }
  const c = { success:["#1a3028","#2d8a6e","#56d3a0"], error:["#2d1015","#da3633","#ff7b72"], warn:["#2a2108","#c9a227","#e3c55a"], info:["#1c2340","#3b5e99","#79a8e0"] };
  const [bg,border,color] = c[type]||c.info;
  const t = document.createElement("div");
  t.style.cssText = `background:${bg};border:1px solid ${border};color:${color};padding:12px 16px;border-radius:10px;font-size:13px;font-weight:500;box-shadow:0 4px 24px rgba(0,0,0,.5);animation:toastIn .25s ease;pointer-events:auto`;
  t.textContent = msg;
  _toastEl.appendChild(t);
  setTimeout(()=>{ t.style.opacity="0"; t.style.transition="opacity .3s"; setTimeout(()=>t.remove(),300); }, 3500);
}

// Inject toast animation
const _toastStyle = document.createElement("style");
_toastStyle.textContent = `
  @keyframes toastIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
  .badge-deed   { background:rgba(86,211,160,.15); color:#56d3a0; border:1px solid rgba(86,211,160,.3); }
  .badge-other  { background:rgba(201,162,39,.15);  color:#c9a227; border:1px solid rgba(201,162,39,.3); }
  .bg-green { background:rgba(45,138,110,.1); }
  .bg-gold  { background:rgba(201,162,39,.1); }
  .bg-red   { background:rgba(218,54,51,.1);  }
  .text-gold { color:#e3c55a; }
  .chip { display:inline-flex;align-items:center;gap:4px;font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px; }
  .chip-done { background:rgba(45,138,110,.15);color:#56d3a0;border:1px solid rgba(45,138,110,.3); }
  .chip-todo { background:rgba(0,0,0,.2);color:var(--text3);border:1px solid var(--border); }
  .btn-icon-sm { background:none;border:none;cursor:pointer;font-size:13px;padding:0 4px; }
  .exc-chip { display:inline-flex;align-items:center;font-size:9px;font-weight:700;padding:2px 6px;border-radius:6px;cursor:pointer;transition:all .15s;border:1px solid transparent; }
  .exc-off   { background:var(--bg);color:var(--text3);border-color:var(--border); }
  .exc-active { background:rgba(201,162,39,.15);color:#c9a227;border-color:rgba(201,162,39,.3); }
  .chain-box { background:rgba(0,0,0,.2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:11px; }
  .chain-years { display:flex;gap:4px;flex-wrap:wrap;margin-top:5px; }
  .year-chip { background:rgba(45,138,110,.15);color:var(--accent2);border:1px solid var(--accent);border-radius:4px;font-size:10px;padding:1px 6px;font-family:monospace;cursor:pointer; }
  .link-btn { background:none;border:none;cursor:pointer;color:var(--text3);font-size:10px;padding:0;font-family:inherit; }
  .parcel-client-card,.parcel-card { background:rgba(0,0,0,.2);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px; }
  .parcel-client-card { border-left:3px solid var(--accent); }
  .parcel-card  { border-left:3px solid #7a4f9a; }
  .status-dot.loading { background:var(--gold);animation:pulse 1s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .spinner { border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;width:18px;height:18px;animation:spin .8s linear infinite;display:inline-block; }
  @keyframes spin { to{transform:rotate(360deg)} }
  .font-bold { font-weight:700; }
  .highlight  { color:var(--accent2) !important; }
`;
document.head.appendChild(_toastStyle);
