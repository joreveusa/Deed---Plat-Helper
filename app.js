// @ts-nocheck
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
    const nameField = document.getElementById("s2SearchName");
    if (!nameField.value) {
      nameField.value = state.researchSession.client_name;
    }
    // Auto-fire search if name is pre-populated (avoid redundant searches)
    if (nameField.value && nameField.value.length >= 2 && !state._step2Searched) {
      state._step2Searched = true;
      setTimeout(() => doStep2Search(), 400);
    }
  }
  if (state.currentStep === 3) {
    doStep3Search();
  }
  if (state.currentStep === 4 && state.researchSession) {
    // Auto-run discovery scan if the client deed has been saved
    const clientSubj = state.researchSession.subjects.find(s => s.type === 'client');
    if (clientSubj && clientSubj.deed_saved && !state._adjDiscoveryRan) {
      state._adjDiscoveryRan = true;
      setTimeout(() => runAdjoinerDiscovery(true), 600);
    }
  }
  if (state.currentStep === 5) {
    renderResearchBoard();
  }
  if (state.currentStep === 6) {
    switchS6Tab('calls');
    // Auto-import calls from deed if not already done
    if (state.selectedDetail && !state.parsedCalls.length) {
      setTimeout(() => reparseClientCallsFromSession(true), 400);
    }
    // Auto-populate adjoiner parcels from board
    if (state.researchSession && !state.adjoinParcels.length) {
      setTimeout(() => autoPopulateAdjoiners(true), 600);
    }
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

      // Clear any stale deed/plat state from a previous session so Step 3
      // doesn't use the prior client's deed detail when searching for this client.
      state.selectedDoc    = null;
      state.selectedDetail = null;
      state._kmlHits       = null;
      state._cabinetHits   = null;
      // Reset per-session automation flags
      state._step2Searched    = false;
      state._adjDiscoveryRan  = false;

      // If the user selected a property from the KML map in Step 1, pre-seed
      // the client_upc in the session. Steps 3 & 4 will use it for parcel matching.
      if (_propPicker.confirmedParcel) {
        state.researchSession.client_upc      = _propPicker.confirmedParcel.upc || '';
        state.researchSession.client_parcel   = _propPicker.confirmedParcel;
        // Pre-seed _kmlHits so Step 3 KML search already has the client's parcel
        state._kmlHits = [{
          owner:        _propPicker.confirmedParcel.owner || client,
          upc:          _propPicker.confirmedParcel.upc   || '',
          plat:         _propPicker.confirmedParcel.plat  || '',
          book:         _propPicker.confirmedParcel.book  || '',
          page:         _propPicker.confirmedParcel.page  || '',
          cab_refs:     _propPicker.confirmedParcel.cab_refs || [],
          cab_refs_str: (_propPicker.confirmedParcel.cab_refs || []).join(', '),
          centroid:     _propPicker.confirmedParcel.centroid,
          match_reason: 'Selected from KML Map Picker',
          source:       'kml',
          local_files:  [],
        }];
      }

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

  if (!state.loggedIn) {
    showToast("Not connected to 1stNMTitle — click Settings to log in", "warn");
    await checkLogin();
    if (!state.loggedIn) return;
  }
  if (!name || name.length < 2) { showToast("Enter a longer name", "warn"); return; }

  const btn = document.getElementById("btnS2Search");
  const tbody = document.getElementById("s2ResultsBody");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:4px"></span>Searching…`;
  tbody.innerHTML = `<tr><td colspan="5" class="empty-cell"><div class="loading-state">Searching records for <strong>${escHtml(name)}</strong>…</div></td></tr>`;
  document.getElementById("s2ResultCount").textContent = "0";

  try {
    const res = await apiFetch("/search", "POST", { name, operator: op });
    if (!res.success) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell text-danger">Error: ${res.error}</td></tr>`;
      return;
    }

    if (!res.results.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell text-text3">No records found for "${escHtml(name)}"</td></tr>`;
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

    // Auto-select if only one result
    if (res.results.length === 1) {
      const onlyRow = tbody.querySelector('tr');
      if (onlyRow) {
        showToast(`1 record found — loading automatically`, 'info');
        setTimeout(() => loadS2Detail(res.results[0].doc_no, 0, onlyRow), 300);
      }
    }

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
        <div id="deedPlatHintArea"></div>
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

    // Async: extract plat hints from deed in background — don't block UI
    extractPlatHintsFromDeed(res.detail);

  } catch (e) {
    container.innerHTML = `<div class="empty-state text-danger">Error: ${e.message}</div>`;
  }
}

/** Extract well-known fields from deed detail object */

/**
 * Called immediately after a deed is loaded in Step 2.
 * Calls /find-plat to extract cabinet refs and plat hints from the deed text,
 * then injects a highlighted insight card at the top of the summary tab.
 */
async function extractPlatHintsFromDeed(detail) {
  const hintArea = document.getElementById('deedPlatHintArea');
  if (!hintArea) return;

  try {
    // Call the fast (zero I/O) deed parser
    const res = await apiFetch('/find-plat', 'POST', { detail });
    const cabRefs = (res && res.cabinet_refs) || [];

    // Also scan legal description client-side for any plat/book/page mentions
    const legalText = [
      detail['Legal Description'] || '',
      detail['Other Legal'] || '',
      detail['Legal'] || '',
      detail['Comments'] || '',
      detail['Remarks'] || '',
    ].join(' ');

    // Match patterns like: Cabinet C-191A, C-191-A, Book 5 Page 12, Plat Book, Cab. D-22
    const bookPageMatches = [...legalText.matchAll(/(?:book|bk)[.\s]*?(\d+)[\s,]*(?:page|pg)[.\s]*?(\d+[A-Z]?)/gi)]
      .map(m => `Book ${m[1]} Page ${m[2]}`);

    const platNameMatches = [...legalText.matchAll(/(?:plat\s+(?:of|entitled?|called?|named?)[:\s]+)([^,;\n]{4,60})/gi)]
      .map(m => m[1].trim());

    const surveyorRefs = [...legalText.matchAll(/(?:survey(?:ed)?\s+by|surveyor)[:\s]+([A-Z][a-zA-Z\s.&,]{3,50})/gi)]
      .map(m => m[1].trim());

    // Prior owners = grantor names from the deed (the plat may be filed under their name)
    const grantorRaw = detail['Grantor'] || state.selectedDoc?.grantor || '';
    // Split on common multi-grantor separators: " and ", " & ", ";"
    const priorOwners = grantorRaw
      .split(/\s*(?:;|\band\b|&)\s*/i)
      .map(n => n.trim())
      .filter(n => n.length > 2);

    // Store all hints in state for Step 3 to use
    state._platHint = {
      cabRefs,
      bookPageMatches: [...new Set(bookPageMatches)],
      platNameMatches: [...new Set(platNameMatches)],
      surveyorRefs:    [...new Set(surveyorRefs)],
      priorOwners,
    };

    const hasAny = cabRefs.length || bookPageMatches.length || platNameMatches.length || priorOwners.length;
    if (!hasAny) {
      hintArea.innerHTML = `<div class="plat-hint-card plat-hint-none">
        <span style="opacity:.6;font-size:12px">&#128270; No plat references found directly in this deed.</span>
      </div>`;
      return;
    }

    // Build the hint card
    let rows = [];

    if (cabRefs.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#128230; Cabinet Refs</span>
        <span class="plat-hint-values">${cabRefs.map(r =>
          `<span class="badge badge-local" style="cursor:pointer" onclick="jumpToPlat('${r.cabinet}','${r.doc}')" title="Search Cabinet ${r.cabinet} for ${r.doc}">`+
          `${escHtml('C-'+r.cabinet+'-'+r.doc)} <span style="opacity:.6;font-size:9px">▶ Plat</span></span>`
        ).join(' ')}</span>
      </div>`);
    }

    if (bookPageMatches.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#128213; Book / Page</span>
        <span class="plat-hint-values">${bookPageMatches.map(b =>
          `<span class="badge badge-online">${escHtml(b)}</span>`
        ).join(' ')}</span>
      </div>`);
    }

    if (platNameMatches.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#128196; Plat Name</span>
        <span class="plat-hint-values">${platNameMatches.map(p =>
          `<span class="badge" style="background:rgba(121,168,224,.15);color:#79a8e0">${escHtml(p)}</span>`
        ).join(' ')}</span>
      </div>`);
    }

    if (surveyorRefs.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#9998; Surveyor</span>
        <span class="plat-hint-values">${surveyorRefs.map(s =>
          `<span class="badge" style="background:rgba(108,71,255,.15);color:#a78bfa">${escHtml(s)}</span>`
        ).join(' ')}</span>
      </div>`);
    }

    if (priorOwners.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#128100; Prior Owners</span>
        <span class="plat-hint-values">${priorOwners.map(n =>
          `<span class="badge" style="background:rgba(227,197,90,.12);color:#e3c55a;cursor:pointer"
            onclick="jumpToPlatByOwner('${escHtml(n).replace(/'/g,'&#39;')}')"
            title="Search plat under prior owner: ${escHtml(n)}">` +
          `${escHtml(n.split(',')[0])} <span style="opacity:.5;font-size:9px">▶ Search</span></span>`
        ).join(' ')}</span>
      </div>`);
    }

    hintArea.innerHTML = `
      <div class="plat-hint-card">
        <div class="plat-hint-header">
          <span>&#128269; Plat Info Found in Deed</span>
          <button class="btn btn-success btn-sm" onclick="saveClientDeedAndGoToPlat()" style="font-size:11px;padding:3px 10px">
            Save &amp; Find Plat &rarr;
          </button>
        </div>
        ${rows.join('')}
      </div>`;

  } catch(e) {
    // Non-fatal — just don't show the hint
    console.warn('extractPlatHintsFromDeed failed:', e.message);
  }
}

/**
 * Save the client deed and immediately jump to Step 3 plat search.
 * Used by the "Save & Find Plat →" button in the plat hint card.
 */
async function saveClientDeedAndGoToPlat() {
  const detail = state.selectedDetail;
  const docNo  = detail?.doc_no || state.selectedDoc?.doc_no;
  if (!docNo) { showToast('No deed selected', 'warn'); return; }
  await saveClientDeed(docNo);
  // saveClientDeed already calls goToStep(3) after 800ms — we're done
}

/**
 * Jump to plat search pre-targeted at a specific cabinet + doc from the deed.
 */
function jumpToPlat(cabinet, doc) {
  // Pre-set the cabinet override so Step 3 auto-targets it
  const sel = document.getElementById('s3CabinetSelect');
  if (sel) sel.value = cabinet;
  showToast(`Opening plat search — targeting Cabinet ${cabinet} for ${doc}`, 'info');
  goToStep(3);
}

/**
 * Jump to plat search using a prior owner name as the primary search token.
 * Sets state._platHint.activeOwnerSearch so doStep3Search picks it up.
 */
function jumpToPlatByOwner(ownerName) {
  if (!state._platHint) state._platHint = {};
  state._platHint.activeOwnerSearch = ownerName;
  showToast(`Searching plat under prior owner: ${ownerName.split(',')[0]}`, 'info');
  goToStep(3);
}

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

/** Returns the user-selected cabinet letter from the Step 3 dropdown, or '' for Auto. */
function getS3CabinetOverride() {
  const sel = document.getElementById('s3CabinetSelect');
  return sel ? sel.value.trim().toUpperCase() : '';
}

/**
 * Called whenever the cabinet dropdown changes.
 * If a deed detail is already loaded, immediately re-run the local cabinet scan
 * so the user sees the results without having to click Refresh.
 */
async function onCabinetSelectChange(val) {
  if (!state.selectedDetail) return;  // nothing to search yet
  const locCards = document.getElementById('s3LocalPlats');
  const cabLabel = val ? `Cabinet ${val}` : 'auto-detected cabinet';
  locCards.innerHTML = `<div class="loading-state">Scanning ${cabLabel}...</div>`;

  const kmlHits = state._kmlHits || [];
  const override = val.toUpperCase();
  const forcedCabs = override ? [override] : null;

  try {
    // Rebuild cabinet refs from deed (zero I/O)
    let cabRefs = [];
    try {
      const fastRes = await apiFetch('/find-plat', 'POST', { detail: state.selectedDetail });
      cabRefs = (fastRes && fastRes.cabinet_refs) || [];
    } catch(e) {}

    const payload = {
      detail:         state.selectedDetail,
      cabinet_refs:   forcedCabs ? [] : cabRefs,
      kml_matches:    forcedCabs ? [] : kmlHits,
      client_name:    state.researchSession?.client_name || '',
    };
    if (forcedCabs) payload.forced_cabinets = forcedCabs;

    const res             = await apiFetch('/find-plat-local', 'POST', payload);
    const localHits       = (res && res.local)            || [];
    const targetCabs      = (res && res.target_cabinets)  || [];
    const targetingReason = (res && res.targeting_reason) || '';
    const cabLabelRes     = targetCabs.length && targetCabs.length < 6
      ? `Cabinet${targetCabs.length > 1 ? 's' : ''} ${targetCabs.join(', ')}`
      : 'all cabinets';

    if (!localHits.length) {
      locCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
        '<div class="text-3xl mb-2">\uD83D\uDDC4\uFE0F</div>' +
        `No cabinet plats matched in ${cabLabelRes}.<br>` +
        (targetingReason ? `<span class="text-xs opacity-50">${escHtml(targetingReason)}</span><br><br>` : '<br>') +
        '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">Browse Cabinets Manually</button></div>';
    } else {
      state._cabinetHits = localHits;
      locCards.innerHTML = localHits.map((f, fi) => {
        const stratLabel = f.strategy === 'kml_cab_ref'  ? '\u2605 KML Match'
          : f.strategy === 'deed_cab_ref' ? '\u2B50 Deed Ref'
          : f.strategy === 'client_name'  ? '\u2B50 Client'
          : f.strategy === 'name_match'   ? 'Name Match'
          : f.strategy === 'page_ref'     ? 'Page Ref'
          : (f.strategy || 'match');
        const isTop = f.strategy === 'kml_cab_ref' || f.strategy === 'deed_cab_ref' || f.strategy === 'client_name';
        return '<div class="plat-item' + (isTop ? ' plat-item-client' : '') + '">' +
          '<div class="plat-info">' +
            '<span class="plat-name text-xs" title="' + escHtml(f.file) + '" style="font-size:13px;font-weight:600">' + escHtml(f.display_name || f.file) + '</span>' +
            '<span class="plat-meta">Cabinet ' + (f.cabinet||'') + ' \u00A0\u00B7\u00A0 ' + stratLabel + '</span>' +
          '</div>' +
          '<button class="btn btn-success btn-sm" onclick="savePlatByIndex(' + fi + ')">\u2B07 Save</button>' +
        '</div>';
      }).join('');
      if (targetingReason) {
        locCards.insertAdjacentHTML('afterbegin',
          `<div class="text-xs text-text3 p-2 pb-0 opacity-60">\uD83C\uDFAF ${escHtml(targetingReason)}</div>`);
      }
    }
  } catch(e) {
    locCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">Cabinet scan failed: ' + escHtml(e.message) + '</div>';
  }
}

async function doStep3Search() {
  const locCards = document.getElementById('s3LocalPlats');
  const kmlCards = document.getElementById('s3KmlPlats');
  const onlCards = document.getElementById('s3OnlinePlats');

  const clientName = state.researchSession && state.researchSession.client_name
    ? state.researchSession.client_name : '';

  // If no deed was selected, still run a name-only search using client_name.
  // Show a soft warning banner, but don't block the search.
  const noDeed = !state.selectedDetail;
  const noDeedBanner = noDeed
    ? '<div class="text-xs p-2" style="background:rgba(227,197,90,.08);border-bottom:1px solid rgba(227,197,90,.2);color:var(--accent2)">' +
      '⚠ No deed selected — searching by client name only. Select a deed in Step 2 for more targeted results.</div>'
    : '';

  // Use an empty detail object when no deed is available so we can still call
  // the backend endpoints (they all handle an empty detail gracefully).
  const detail = state.selectedDetail || {};

  // Set all columns to loading state
  locCards.innerHTML = noDeedBanner + '<div class="loading-state">Identifying target cabinet...</div>';
  if (kmlCards) kmlCards.innerHTML = '<div class="loading-state">Querying KML parcel index...</div>';
  onlCards.innerHTML = '<div class="loading-state">Searching 1stnmtitle.com...</div>';

  // ── A: Instant deed parse (returns cabinet refs, zero I/O) ────────────────
  let cabRefs = [];
  if (!noDeed) {
    try {
      const fastRes = await apiFetch('/find-plat', 'POST', { detail });
      cabRefs = (fastRes && fastRes.cabinet_refs) || [];
    } catch(e) { /* ignore */ }
  }

  // Pull grantor/grantee from the deed detail (prefer detail, fall back to selectedDoc search row)
  const grantor = detail?.Grantor || detail?.grantor
    || state.selectedDoc?.grantor || '';
  const grantee = detail?.Grantee || detail?.grantee
    || state.selectedDoc?.grantee || '';

  // Prior owners from the deed hint (extracted when deed was loaded).
  // These are grantor names — the plat may be filed under any of these.
  const hint = state._platHint || {};
  const priorOwners = hint.priorOwners || (grantor ? [grantor] : []);
  // If the user clicked a specific prior owner, prioritise that name
  const activeOwner = hint.activeOwnerSearch || '';
  // Clear the one-shot override so it only applies to this search run
  if (hint.activeOwnerSearch) { hint.activeOwnerSearch = null; }

  // ── Online search — runs in parallel, uses client_name as primary ──────────
  apiFetch('/find-plat-online', 'POST', {
    detail,
    client_name: activeOwner || clientName,
    grantee,
    grantor:     activeOwner || grantor,
    prior_owners: priorOwners,
  })
    .then(res => {
      const surveyHits = (res && res.online) || [];
      if (!surveyHits.length) {
        onlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">No online survey records found for this client/grantor name.</div>';
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
    })
    .catch(() => {
      onlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">Online search unavailable.</div>';
    });

  // ── KML → then chain Local (so kml_matches with cab_refs are passed) ──────
  // Always query KML index: with deed → full cross-reference; without → name-only search
  const kmlPromise = apiFetch('/find-plat-kml', 'POST', { detail, client_name: clientName })
    .then(res => {
      const kmlHits = (res && res.kml_matches) || [];
      if (!kmlCards) return kmlHits;
      if (!kmlHits.length) {
        const noIdxHint = '<span class="text-xs opacity-60">Use the \u{1F5FA}\uFE0F KML Index button to build the index from county data.</span>';
        const noMatchMsg = noDeed
          ? 'No KML parcels found for <strong>' + escHtml(clientName) + '</strong>.<br><br>' + noIdxHint
          : 'No parcel records found in KML index.<br><br>' + noIdxHint;
        kmlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
          '<div class="text-3xl mb-2">\u{1F5FA}\uFE0F</div>' + noMatchMsg + '</div>';
      } else {
        state._kmlHits = kmlHits;
        _renderKmlHits(kmlCards, kmlHits);
      }
      return kmlHits;
    })
    .catch(() => {
      if (kmlCards) kmlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">KML index unavailable.</div>';
      return [];
    });

  kmlPromise.then(kmlHits => {
    // ── Local cabinet scan — fires AFTER KML resolves
    const cabinetOverride = getS3CabinetOverride();
    const reason = cabinetOverride
      ? `Scanning Cabinet ${cabinetOverride} (manual selection)...`
      : (kmlHits.length
        ? 'Targeting cabinet from KML...'
        : (cabRefs.length ? 'Targeting cabinet from deed text...'
          : (clientName ? `Scanning all cabinets for "${clientName}"...` : 'Scanning all cabinets...')));
    locCards.innerHTML = noDeedBanner + `<div class="loading-state">${reason}</div>`;

    const payload = {
      detail,
      cabinet_refs:  cabinetOverride ? [] : cabRefs,
      kml_matches:   cabinetOverride ? [] : kmlHits,
      client_name:   activeOwner || clientName,
      grantor:       activeOwner || grantor,
      grantee,
      prior_owners:  priorOwners,   // also search under prior owner names
    };
    if (cabinetOverride) payload.forced_cabinets = [cabinetOverride];

    return apiFetch('/find-plat-local', 'POST', payload);
  })
  .then(res => {
    const localHits = (res && res.local) || [];
    const targetCabs = (res && res.target_cabinets) || [];
    const targetingReason = (res && res.targeting_reason) || '';
    const cabLabel = targetCabs.length && targetCabs.length < 6
      ? `Cabinet${targetCabs.length > 1 ? 's' : ''} ${targetCabs.join(', ')}`
      : 'all cabinets';

    if (!localHits.length) {
      locCards.innerHTML = noDeedBanner +
        '<div class="empty-state text-text3 text-sm p-4">' +
        '<div class="text-3xl mb-2">\u{1F5C4}\uFE0F</div>' +
        `No cabinet plats matched in ${cabLabel}.<br>` +
        (targetingReason ? `<span class="text-xs opacity-50">${escHtml(targetingReason)}</span><br><br>` : '<br>') +
        '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">Browse Cabinets Manually</button></div>';
    } else {
      state._cabinetHits = localHits;
      const hitRows = localHits.map((f, fi) => {
        const stratLabel = f.strategy === 'kml_cab_ref'  ? '\u2605 KML Ref Match'
          : f.strategy === 'deed_cab_ref' ? '\u2B50 Deed Ref Match'
          : f.strategy === 'client_name'  ? '\u2B50 Client Match'
          : f.strategy === 'prior_owner'  ? '\uD83D\uDC64 Prior Owner'
          : f.strategy === 'name_match'   ? 'Name Match'
          : f.strategy === 'page_ref'     ? 'Page Ref'
          : (f.strategy || 'match');
        const isTop = f.strategy === 'kml_cab_ref' || f.strategy === 'deed_cab_ref' || f.strategy === 'client_name' || f.strategy === 'prior_owner';
        return '<div class="plat-item' + (isTop ? ' plat-item-client' : '') + '">' +

            '<span class="plat-name text-xs" title="' + escHtml(f.file) + '" style="font-size:13px;font-weight:600">' + escHtml(f.display_name || f.file) + '</span>' +
            '<span class="plat-meta">Cabinet ' + (f.cabinet||'') + ' \u00A0\u00B7\u00A0 ' + stratLabel + '</span>' +
          '</div>' +
          '<button class="btn btn-success btn-sm" onclick="savePlatByIndex(' + fi + ')">\u2B07 Save</button>' +
        '</div>';
      }).join('');

      locCards.innerHTML = noDeedBanner + hitRows;

      if (targetingReason) {
        locCards.insertAdjacentHTML('afterbegin',
          `<div class="text-xs text-text3 p-2 pb-0 opacity-60">\u{1F3AF} ${escHtml(targetingReason)}</div>`);
      }
    }
  })
  .catch(() => {
    locCards.innerHTML = noDeedBanner +
      '<div class="empty-state text-text3 text-sm p-4">Cabinet scan unavailable.</div>';
  });
}


function _renderKmlHits(container, kmlHits, selectedIdx) {
  container.innerHTML = kmlHits.map((p, pi) => {
    const ct        = p.centroid ? 'Lat: ' + p.centroid[1].toFixed(5) + ', Lng: ' + p.centroid[0].toFixed(5) : '';
    const isSelected = pi === selectedIdx;
    const saveBtns  = (p.local_files && p.local_files.length)
      ? p.local_files.map((lf, lfi) =>
          '<button class="btn btn-success btn-sm" style="font-size:10px;padding:3px 7px;white-space:nowrap" ' +
          'onclick="saveKmlLocalFile(' + pi + ',' + lfi + ')" title="' + escHtml(lf.file) + '">' +
          '\u2B07 ' + escHtml(lf.cab_ref || (lf.cabinet + '-' + lf.doc)) + '</button>'
        ).join('')
      : '';
    const searchBtn =
      '<button class="btn btn-sm kml-search-cab-btn' + (isSelected ? ' kml-search-cab-active' : '') + '" ' +
      'onclick="searchCabinetFromKml(' + pi + ')" title="Search local cabinet for this parcel">' +
      '\uD83D\uDD0D Cabinet</button>';
    return '<div class="plat-item kml-parcel-item' + (isSelected ? ' kml-parcel-selected' : '') + '" id="kml-parcel-' + pi + '">' +
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
      '<div class="flex-col gap-1" style="min-width:100px;align-items:flex-end">' +
        searchBtn +
        (saveBtns ? '<div style="margin-top:3px;display:flex;flex-direction:column;gap:2px">' + saveBtns + '</div>' : '') +
      '</div>' +
    '</div>';
  }).join('');
}

// Strip cabinet ref prefix from a KML PLAT string to get the filename-searchable name.
// Mirrors backend _extract_plat_name_tokens().
// "C-191-A ADELA RAEL" → "ADELA RAEL"  |  "CAB C-84-B TORRES" → "TORRES"
function _extractPlatName(platStr) {
  if (!platStr) return '';
  return platStr
    .replace(/(?:CAB(?:INET)?\.?\s*)?[A-Fa-f]\s*-\s*\d{1,4}(?:-[A-Za-z])?\s*/i, '')
    .trim();
}

// ── Phase 1: show filter panel ────────────────────────────────────────────────
function searchCabinetFromKml(pi) {
  const kmlHits = state._kmlHits;
  if (!kmlHits || !kmlHits[pi]) { showToast('KML parcel not found', 'error'); return; }
  const p        = kmlHits[pi];
  const kmlCards = document.getElementById('s3KmlPlats');
  const locCards = document.getElementById('s3LocalPlats');

  // Highlight the selected parcel in the KML column
  if (kmlCards) _renderKmlHits(kmlCards, kmlHits, pi);
  const selEl = document.getElementById('kml-parcel-' + pi);
  if (selEl) selEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // Pre-fill values from this KML parcel
  const cabRef     = p.cab_refs_str || '';   // e.g. "C-191-A" — used for folder targeting only
  const platHint   = p.plat         || '';   // full PLAT field (shown as tooltip)
  // Cabinet files are named after the current owner (e.g. "Rael Adela.pdf"),
  // NOT after the original surveyor in the PLAT field. Use p.owner for matching.
  const nameDefault = p.owner || _extractPlatName(p.plat || '') || '';

  // Build filter panel in the local cabinet column
  locCards.innerHTML =
    '<div class="cab-filter-panel">' +

      // Header: show cabinet ref as context chip, name as the primary label
      '<div class="cab-filter-header">' +
        '<span class="cab-filter-title">\uD83D\uDD0D Cabinet Search' +
          (cabRef ? ' &nbsp;<span style="font-family:monospace;font-size:10px;' +
            'background:rgba(176,128,224,0.15);color:#b080e0;padding:1px 6px;' +
            'border-radius:6px;border:1px solid rgba(176,128,224,0.3)">' +
            escHtml(cabRef) + '</span>' : '') +
        '</span>' +
        '<span class="cab-filter-subtitle" title="' + escHtml(platHint) + '">' +
          escHtml(nameDefault || 'Unknown parcel') +
        '</span>' +
      '</div>' +

      // Fields
      '<div class="cab-filter-body">' +

        '<div class="cab-filter-row">' +
          '<label class="cab-filter-label" for="cabFilterRef">Cabinet Ref</label>' +
          '<input id="cabFilterRef" class="inp cab-filter-inp" ' +
            'value="' + escHtml(cabRef) + '" ' +
            'placeholder="e.g. C-191-A" />' +
          '<span class="cab-filter-hint">Targets the cabinet folder (C, B, etc.) — not used for filename matching</span>' +
        '</div>' +

        '<div class="cab-filter-row">' +
          '<label class="cab-filter-label" for="cabFilterName">Name <span style="opacity:.5">(matched against filenames)</span></label>' +
          '<input id="cabFilterName" class="inp cab-filter-inp" ' +
            'value="' + escHtml(nameDefault) + '" ' +
            'placeholder="e.g. ADELA RAEL" />' +
          '<span class="cab-filter-hint">Current owner name — edit if the file is named differently</span>' +
        '</div>' +

        '<div class="cab-filter-row" style="flex-direction:row;align-items:center;gap:8px;padding-top:2px">' +
          '<input type="checkbox" id="cabFilterAllCabs" style="accent-color:var(--accent2);width:14px;height:14px">' +
          '<label for="cabFilterAllCabs" style="font-size:12px;color:var(--text2);cursor:pointer">' +
            'Search all cabinets (ignore cabinet ref for targeting)' +
          '</label>' +
        '</div>' +

      '</div>' +

      // Actions
      '<div class="cab-filter-actions">' +
        '<button class="btn btn-primary flex-1" ' +
          'onclick="_executeKmlCabinetSearch(' + pi + ')">' +
          'Search Cabinet \u2192' +
        '</button>' +
        '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">' +
          'Browse Manually' +
        '</button>' +
      '</div>' +

    '</div>';
}

// ── Phase 2: run the search with filter panel values ─────────────────────────
async function _executeKmlCabinetSearch(pi) {
  const kmlHits = state._kmlHits;
  if (!kmlHits || !kmlHits[pi]) return;
  const p = kmlHits[pi];

  // Read filter panel values
  const cabRefInput  = (document.getElementById('cabFilterRef')  || {}).value || '';
  const nameInput    = (document.getElementById('cabFilterName') || {}).value || '';
  const searchAllCabs = document.getElementById('cabFilterAllCabs')?.checked || false;
  const clientName   = state.researchSession?.client_name || '';

  // Label for display: just the name being searched (cabinet ref shown separately in header)
  const searchName   = nameInput.trim() || p.owner || '';
  const parcelLabel  = searchName;
  const locCards     = document.getElementById('s3LocalPlats');

  // Build a modified parcel object for the backend.
  // Cabinet ref is ONLY used to target the correct folder (letter = "C", "B", etc.).
  // The number/suffix (e.g. "191A") doesn't appear in cabinet filenames and is ignored.
  let cabRefs = [];
  if (cabRefInput.trim()) {
    const letter = cabRefInput.trim().match(/^([A-Fa-f])/i);
    if (letter) cabRefs = [letter[1].toUpperCase()];   // just "C", not "C-191A"
  } else {
    // Fall back to the original parcel's cab_refs, but strip to letters only
    cabRefs = (p.cab_refs || []).map(r => r.split('-')[0].toUpperCase()).filter(Boolean);
  }

  const overrideParcel = {
    ...p,
    cab_refs:     cabRefs,
    cab_refs_str: cabRefs.join(', '),
    // If user left name blank, clear owner so backend doesn't name-match on it
    owner: nameInput.trim() || '',
  };

  locCards.innerHTML = '<div class="loading-state">Searching cabinet for <strong>' +
    escHtml(parcelLabel) + '</strong>\u2026</div>';

  try {
    const payload = {
      detail:       state.selectedDetail,
      cabinet_refs: [],
      kml_matches:  searchAllCabs ? [] : [overrideParcel],
      client_name:  clientName,
    };
    // If the user typed a name override, pass it as grantor for name matching
    if (nameInput.trim()) {
      payload.grantor = nameInput.trim();
    }

    const res             = await apiFetch('/find-plat-local', 'POST', payload);
    const localHits       = (res && res.local)            || [];
    const targetCabs      = (res && res.target_cabinets)  || [];
    const targetingReason = (res && res.targeting_reason) || '';
    const cabLabel        = targetCabs.length && targetCabs.length < 6
      ? 'Cabinet' + (targetCabs.length > 1 ? 's' : '') + ' ' + targetCabs.join(', ')
      : 'all cabinets';

    if (!localHits.length) {
      locCards.innerHTML =
        '<div class="empty-state text-text3 text-sm p-4">' +
        '<div class="text-3xl mb-2">\uD83D\uDDC4\uFE0F</div>' +
        'No cabinet plats matched in ' + cabLabel + ' for <strong>' + escHtml(parcelLabel) + '</strong>.<br>' +
        (targetingReason ? '<span class="text-xs opacity-50">' + escHtml(targetingReason) + '</span><br><br>' : '<br>') +
        '<button class="btn btn-outline btn-sm" style="margin-top:8px" ' +
          'onclick="searchCabinetFromKml(' + pi + ')">\u21A9 Adjust Filters</button>' +
        ' &nbsp; ' +
        '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">Browse Manually</button>' +
        '</div>';
    } else {
      state._cabinetHits = localHits;
      const adjLink =
        '<button class="btn btn-sm cab-filter-adj-btn" onclick="searchCabinetFromKml(' + pi + ')">' +
        '\u21A9 Adjust</button>';
      const header =
        '<div class="text-xs p-2 pb-1" style="display:flex;justify-content:space-between;align-items:center;' +
        'color:var(--accent2);border-bottom:1px solid var(--border)">' +
        '<span>\uD83D\uDD0D Results for: <strong>' + escHtml(parcelLabel) + '</strong></span>' +
        adjLink +
        '</div>';
      locCards.innerHTML = header + localHits.map((f, fi) => {
        const stratLabel = f.strategy === 'kml_cab_ref'  ? '\u2605 KML Ref Match'
          : f.strategy === 'deed_cab_ref' ? '\u2B50 Deed Ref Match'
          : f.strategy === 'client_name'  ? '\u2B50 Client Match'
          : f.strategy === 'name_match'   ? 'Name Match'
          : f.strategy === 'name_match'   ? 'Name Match'
          : (f.strategy || 'match');
        const isTop = f.strategy === 'kml_cab_ref' || f.strategy === 'deed_cab_ref' || f.strategy === 'client_name' || f.strategy === 'prior_owner';
        return '<div class="plat-item' + (isTop ? ' plat-item-client' : '') + '">' +
          '<div class="plat-info">' +
            '<span class="plat-name text-xs" title="' + escHtml(f.file) + '" style="font-size:13px;font-weight:600">' + escHtml(f.display_name || f.file) + '</span>' +
            '<span class="plat-meta">Cabinet ' + (f.cabinet||'') + ' \u00A0\u00B7\u00A0 ' + stratLabel + '</span>' +
          '</div>' +
          '<button class="btn btn-success btn-sm" onclick="savePlatByIndex(' + fi + ')">\u2B07 Save</button>' +
        '</div>';
      }).join('');

      if (targetingReason) {
        locCards.insertAdjacentHTML('afterbegin',
          '<div class="text-xs text-text3 p-2 pb-0 opacity-60">\uD83C\uDFAF ' + escHtml(targetingReason) + '</div>');
      }
    }
  } catch(e) {
    locCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">Cabinet scan failed: ' +
      escHtml(e.message) + '<br><br>' +
      '<button class="btn btn-outline btn-sm" onclick="searchCabinetFromKml(' + pi + ')">\u21A9 Adjust Filters</button>' +
      '</div>';
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
async function runAdjoinerDiscovery(autoMode = false) {
  const rs = state.researchSession;
  if (!rs) { if (!autoMode) showToast("No active session", "error"); return; }
  
  const clientSubj = rs.subjects.find(s => s.type === "client");
  if (!clientSubj || !clientSubj.deed_saved) {
    if (!autoMode) showToast("Save the client deed first", "warn");
    return;
  }

  const btn     = document.getElementById("btnDiscoverAdjoiners");
  const grid    = document.getElementById("s4AdjoinerGrid");
  const resArea = document.getElementById("s4DiscoveryResults");
  const countEl = document.getElementById("s4CountText");

  if (btn)     { btn.disabled = true; btn.innerHTML = `<div class="spinner"></div> Scanning...`; }
  if (resArea) resArea.classList.remove("hidden");
  if (grid)    grid.innerHTML = `<div class="loading-state col-span-full">Running OCR on plat and scanning online records\u2026</div>`;
  if (countEl) countEl.textContent = "...";

  if (autoMode) showToast("\ud83d\udd0d Auto-running adjoiner discovery\u2026", "info");

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
    if (countEl) countEl.textContent = count;

    if (!count) {
      const noDetailHint = !state.selectedDetail
        ? `<br><br><span class="text-xs" style="color:var(--text3)">&#128161; Tip: Go to <button class="link-btn" onclick="goToStep(2)">Step 2</button> and select the client deed to enable full-text scanning.</span>`
        : "";
      if (grid) grid.innerHTML = `<div class="empty-state col-span-full">No adjoiners found automatically. Add manually below.${noDetailHint}</div>`;
      if (autoMode) showToast("Discovery scan found no adjoiners — add manually or use map picker", "info");
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
    if (grid) grid.innerHTML = `<div class="text-danger col-span-full p-4">Discovery failed: ${e.message}</div>`;
    if (!autoMode) showToast("Discovery failed: " + e.message, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = `⚡ Re-run Scan`; }
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
  await addAllAndContinue();
}

/**
 * One-click: add ALL discovered adjoiners to the board, then navigate to Step 5.
 * Safe to call even if no adjoiners were discovered (won't navigate if none on board).
 */
async function addAllAndContinue() {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "warn"); return; }

  let added = 0;
  const discovered = state.discoveredAdjoiners || [];
  for (const j of discovered) {
    const exists = rs.subjects.some(s => s.type === "adjoiner" && s.name.toLowerCase() === j.name.toLowerCase());
    if (!exists) {
      rs.subjects.push({
        id: "adj_" + Date.now() + "_" + Math.random().toString(36).substr(2,5),
        type: "adjoiner",
        name: j.name,
        deed_saved: false, plat_saved: false, status: "pending", notes: ""
      });
      added++;
    }
  }

  if (added > 0) await persistSession();

  const totalAdj = rs.subjects.filter(s => s.type === "adjoiner").length;
  if (totalAdj === 0) {
    showToast("No adjoiners on board yet — add some above or pick from map", "warn");
    return;
  }

  showToast(
    added > 0 ? `✓ Added ${added} adjoiners — opening Research Board` : `Going to Research Board (adjoiners already on board)`,
    "success"
  );
  setTimeout(() => goToStep(5), 300);
}

/**
 * Skip deed research and go directly to Client Plat step.
 */
function skipToStep3() {
  const rs = state.researchSession;
  if (!rs) { showToast("Start a session first", "warn"); return; }
  // Ensure client subject exists even without a deed
  const clientSubj = rs.subjects.find(s => s.type === "client");
  if (clientSubj && !clientSubj.deed_saved) {
    // Mark deed as skipped so Step 4 still functions
    showToast("Skipping deed — going to Client Plat search", "info");
  }
  goToStep(3);
}

async function manualAddAdjoiner() {
  const inp = document.getElementById("s4ManualName");
  const name = inp.value.trim();
  if (!name || name.length < 2) return;
  
  await addFoundAdjoiner(name);
  inp.value = "";
}

// Expose Cabinet browser globally
function openGlobalCabinetBrowser(defaultCab) {
  document.getElementById('cabinetOverlay').classList.remove('hidden');
  browseCabinet(defaultCab || 'A');
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
      const escapedPath = f.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      const escapedFile = (f.file || '').replace(/'/g, "\\'");
      html += `
        <tr>
          <td class="text-xs" title="${escHtml(f.file)}">${escHtml(f.display_name || f.file)}</td>
          <td class="text-text3 text-xs w-16">${f.size_kb} KB</td>
          <td style="white-space:nowrap">
            <button class="btn btn-outline btn-sm" style="margin-right:4px" onclick="apiFetch('/open-file','POST',{path:'${escapedPath}'})">&#128065; Open</button>
            <button class="btn btn-success btn-sm" onclick="saveFromCabinetBrowser('${escapedPath}','${escapedFile}')">&#11015; Save as Plat</button>
          </td>
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

/**
 * Save a file chosen directly from the Cabinet Browser as the client plat.
 * Closes the browser modal on success.
 */
async function saveFromCabinetBrowser(filePath, filename) {
  const rs = state.researchSession;
  if (!rs) { showToast('No active research session', 'error'); return; }
  const clientSubj = rs.subjects.find(s => s.type === 'client');

  try {
    const res = await apiFetch('/save-plat', 'POST', {
      source:      'local',
      file_path:   filePath,
      filename:    filename,
      job_number:  rs.job_number,
      client_name: rs.client_name,
      job_type:    rs.job_type,
      subject_id:  clientSubj ? clientSubj.id : 'client'
    });
    if (res.success) {
      showToast(res.skipped ? 'Plat already exists in project (skipped)' : '\u2B07 Plat saved: ' + res.filename, 'success');
      if (clientSubj && !res.skipped) {
        clientSubj.plat_saved = true;
        if (res.saved_to) clientSubj.plat_path = res.saved_to;
        await persistSession();
      }
      closeCabinetBrowser();
      setTimeout(() => goToStep(4), 600);
    } else {
      showToast('Save failed: ' + res.error, 'error');
    }
  } catch(e) {
    showToast('Error: ' + e.message, 'error');
  }
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

// ── Adjoiner: auto-search deeds and show a pick modal ──────────────────────────
async function saveAdjDeed(subjId) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  if (!subj) return;

  // If a deed is already loaded in Step 2 AND matches the adjoiner's last name, use it.
  const adjLast = subj.name.split(',')[0].trim().toLowerCase();
  const loadedGrantor = (state.selectedDetail?.['Grantor'] || '').toLowerCase();
  const loadedGrantee = (state.selectedDetail?.['Grantee'] || '').toLowerCase();
  if (state.selectedDoc && state.selectedDetail &&
      (loadedGrantor.includes(adjLast) || loadedGrantee.includes(adjLast))) {
    await _doSaveAdjDeedFromLoaded(subjId, rs, subj);
    return;
  }

  // Otherwise, auto-search by last name and show a pick dialog
  const lastName = subj.name.split(',')[0].trim();
  if (!lastName || lastName.length < 2) {
    showToast('Adjoiner name too short to search', 'warn');
    return;
  }

  if (!state.loggedIn) {
    showToast('Not connected to records — searching anyway...', 'warn');
  }

  showToast(`Searching records for "${lastName}"...`, 'info');
  try {
    const res = await apiFetch('/search', 'POST', { name: lastName, operator: 'begins with' });
    if (!res.success) { showToast('Search error: ' + res.error, 'error'); return; }
    if (!res.results || !res.results.length) {
      showToast(`No deed records found for "${lastName}". Try searching manually in Step 2.`, 'warn');
      return;
    }
    _showAdjDeedPickModal(subjId, subj.name, res.results);
  } catch(e) {
    showToast('Search failed: ' + e.message, 'error');
  }
}

function _showAdjDeedPickModal(subjId, adjName, results) {
  // Build or reuse a simple pick modal
  let overlay = document.getElementById('adjDeedPickOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'adjDeedPickOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center';
    document.body.appendChild(overlay);
  }

  const rows = results.slice(0, 15).map((r, i) => `
    <tr style="cursor:pointer" onclick="_pickAdjDeed('${subjId}', ${i})" id="adjrow_${i}">
      <td class="mono text-xs" style="color:var(--accent2);padding:6px 8px">${escHtml(r.doc_no || '')}</td>
      <td style="padding:6px 8px;font-size:12px">${escHtml((r.grantor || '').split(',')[0] || r.grantor || '')}</td>
      <td style="padding:6px 8px;font-size:12px">${escHtml((r.grantee || '').split(',')[0] || r.grantee || '')}</td>
      <td style="padding:6px 8px"><span class="badge ${getTypeClass(r.instrument_type)}">${escHtml(r.instrument_type || 'Deed')}</span></td>
      <td class="text-xs" style="padding:6px 8px;color:var(--text3)">${escHtml(r.location || '')}</td>
      <td class="text-xs" style="padding:6px 8px;color:var(--text3)">${(r.recorded_date || r.date || '').split('-')[0] || ''}</td>
    </tr>`).join('');

  // Store results in state temporarily
  state._adjPickResults = results;
  state._adjPickSubjId  = subjId;

  overlay.innerHTML = `
    <div class="glass-card" style="width:min(960px,95vw);max-height:80vh;display:flex;flex-direction:column;overflow:hidden">
      <div style="padding:16px 20px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border)">
        <div>
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.6px;color:var(--text3)">Select deed for adjoiner</div>
          <div style="font-size:18px;font-weight:700;color:var(--accent2)">${escHtml(adjName)}</div>
        </div>
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('adjDeedPickOverlay').remove()">✕ Cancel</button>
      </div>
      <div style="overflow-y:auto;flex:1">
        <table class="data-table" style="width:100%">
          <thead><tr>
            <th style="padding:6px 8px;font-size:10px">Doc #</th>
            <th style="padding:6px 8px;font-size:10px">Grantor</th>
            <th style="padding:6px 8px;font-size:10px">Grantee</th>
            <th style="padding:6px 8px;font-size:10px">Type</th>
            <th style="padding:6px 8px;font-size:10px">Location</th>
            <th style="padding:6px 8px;font-size:10px">Year</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div style="padding:10px 20px;border-top:1px solid var(--border);font-size:11px;color:var(--text3);text-align:center">
        Click a row to save that deed for this adjoiner &nbsp;|&nbsp;
        <button class="link-btn" onclick="document.getElementById('adjDeedPickOverlay').remove();searchForSubject('${escHtml(adjName.split(',')[0]).replace(/'/g, "\\'")}')">Search manually in Step 2 →</button>
      </div>
    </div>`;
}

async function _pickAdjDeed(subjId, idx) {
  const rs    = state.researchSession;
  const subj  = rs.subjects.find(s => s.id === subjId);
  const r     = state._adjPickResults?.[idx];
  if (!subj || !r) return;

  // Highlight selected row
  document.querySelectorAll('#adjDeedPickOverlay tr[id^=adjrow_]').forEach(tr => tr.style.background = '');
  const row = document.getElementById('adjrow_' + idx);
  if (row) row.style.background = 'rgba(46,160,67,0.15)';

  showToast(`Downloading deed ${r.doc_no}...`, 'info');
  try {
    const res = await apiFetch('/download', 'POST', {
      doc_no:         r.doc_no,
      grantor:        r.grantor || '',
      grantee:        r.grantee || '',
      location:       r.location || '',
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
      showToast(res.skipped ? `Deed already exists for ${subj.name}` : `Deed saved for ${subj.name}!`, 'success');
      document.getElementById('adjDeedPickOverlay')?.remove();
      renderResearchBoard();
    } else {
      showToast('Save failed: ' + res.error, 'error');
    }
  } catch(e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function _doSaveAdjDeedFromLoaded(subjId, rs, subj) {
  try {
    const res = await apiFetch('/download', 'POST', {
      doc_no:         state.selectedDoc.doc_no,
      grantor:        state.selectedDetail['Grantor'] || '',
      grantee:        state.selectedDetail['Grantee'] || '',
      location:       state.selectedDetail['Location'] || '',
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
      showToast(res.skipped ? `Deed already exists for ${subj.name}` : `Deed saved for ${subj.name}!`, 'success');
      renderResearchBoard();
    } else {
      showToast('Save failed: ' + res.error, 'error');
    }
  } catch(e) {
    showToast('Error: ' + e.message, 'error');
  }
}

// ── Adjoiner: find plat using full 3-way parallel search ─────────────────────
async function saveAdjPlat(subjId) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  if (!subj) return;

  const clientName = rs.client_name;
  const adjName    = subj.name;
  const lastName   = adjName.split(',')[0].trim();

  // Synthesize a minimal deed detail using the adjoiner name
  const searchDetail = { 'Grantor': adjName, 'Grantee': '' };

  showToast(`🔍 Searching all sources for "${lastName}" plat…`, 'info');

  // Run the same 3-way parallel search as Step 3
  try {
    // 1. Fast deed parse (cabinet refs from synthesized deed)
    let cabRefs = [];
    try {
      const fastRes = await apiFetch('/find-plat', 'POST', { detail: searchDetail });
      cabRefs = (fastRes && fastRes.cabinet_refs) || [];
    } catch(e) {}

    // 2. KML lookup (by name)
    const kmlRes = await apiFetch('/find-plat-kml', 'POST', { detail: searchDetail, client_name: adjName });
    const kmlHits = (kmlRes && kmlRes.kml_matches) || [];

    // 3. Local cabinet scan (fires after KML so we pass kml_matches)
    const localRes = await apiFetch('/find-plat-local', 'POST', {
      detail: searchDetail,
      cabinet_refs: cabRefs,
      kml_matches:  kmlHits,
      client_name:  adjName,
      grantor:      adjName,
      grantee:      '',
    });
    const localHits = (localRes && localRes.local) || [];

    // Collect all candidates in priority order
    const allCandidates = [
      ...localHits,
      ...(kmlHits.flatMap(k => (k.local_files || []).map(lf => ({...lf, strategy: 'kml_local'})))),
    ];

    if (allCandidates.length) {
      // Show a quick pick modal with all candidates
      _showAdjPlatPickModal(subjId, subj, allCandidates);
    } else {
      // 4. Fall back: online search
      const onlineRes = await apiFetch('/find-plat-online', 'POST', {
        detail: searchDetail, client_name: adjName, grantee: '', grantor: adjName
      });
      const onlineHits = (onlineRes && onlineRes.online) || [];
      if (onlineHits.length) {
        _showAdjPlatPickModal(subjId, subj, [], onlineHits);
      } else {
        showToast(`No plat found for "${lastName}" in cabinet, KML, or online records.`, 'warn');
      }
    }
  } catch(e) {
    showToast('Plat search error: ' + e.message, 'error');
  }
}

/** Show a compact modal to pick the best adjoiner plat candidate */
function _showAdjPlatPickModal(subjId, subj, localHits, onlineHits = []) {
  const rs = state.researchSession;
  let overlay = document.getElementById('adjPlatPickOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'adjPlatPickOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center';
    document.body.appendChild(overlay);
  }

  const localRows = localHits.map((f, i) => `
    <tr style="cursor:pointer" onclick="_pickAdjPlat('${subjId}', 'local', ${i})">
      <td style="padding:7px 10px;font-size:12px;font-weight:600">${escHtml(f.display_name || f.file)}</td>
      <td style="padding:7px 10px;font-size:11px;color:var(--text3)">Cabinet ${escHtml(f.cabinet || '')}</td>
      <td style="padding:7px 10px"><span class="badge badge-local">${escHtml(f.strategy || 'Cabinet')}</span></td>
    </tr>`);

  const onlineRows = onlineHits.map((r, i) => `
    <tr style="cursor:pointer" onclick="_pickAdjPlatOnline('${subjId}', ${i})">
      <td style="padding:7px 10px;font-size:12px;font-weight:600">${escHtml((r.grantor || '').split(',')[0] || r.grantor || r.doc_no)}</td>
      <td style="padding:7px 10px;font-size:11px;color:var(--text3)">${escHtml(r.recorded_date || '')}</td>
      <td style="padding:7px 10px"><span class="badge badge-online">Online Doc ${escHtml(r.doc_no || '')}</span></td>
    </tr>`);

  state._adjPlatSubjId    = subjId;
  state._adjPlatLocalHits = localHits;
  state._adjPlatOnlineHits = onlineHits;

  overlay.innerHTML = `
    <div class="glass-card" style="width:min(760px,95vw);max-height:75vh;display:flex;flex-direction:column;overflow:hidden">
      <div style="padding:14px 20px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border)">
        <div>
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--text3)">Select plat for adjoiner</div>
          <div style="font-size:17px;font-weight:700;color:var(--accent2)">${escHtml(subj.name)}</div>
        </div>
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('adjPlatPickOverlay').remove()">✕ Cancel</button>
      </div>
      <div style="overflow-y:auto;flex:1">
        <table class="data-table" style="width:100%">
          <thead><tr>
            <th style="padding:6px 10px;font-size:10px">File / Name</th>
            <th style="padding:6px 10px;font-size:10px">Location</th>
            <th style="padding:6px 10px;font-size:10px">Source</th>
          </tr></thead>
          <tbody>${localRows.join('') || onlineRows.join('') || '<tr><td colspan="3" class="empty-cell">No candidates found</td></tr>'}
          ${localRows.length && onlineRows.length ? '<tr><td colspan="3" style="padding:4px 10px;background:var(--bg3);font-size:10px;font-weight:700;color:var(--text3);text-transform:uppercase">Online Records</td></tr>' + onlineRows.join('') : ''}
          </tbody>
        </table>
      </div>
      <div style="padding:10px 20px;border-top:1px solid var(--border);font-size:11px;color:var(--text3);text-align:center">
        Click a row to save that plat for this adjoiner
      </div>
    </div>`;
}

async function _pickAdjPlat(subjId, type, idx) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  const f = state._adjPlatLocalHits && state._adjPlatLocalHits[idx];
  if (!subj || !f) return;
  try {
    const res = await apiFetch('/save-plat', 'POST', {
      source: 'local', file_path: f.path, filename: f.file,
      job_number: rs.job_number, client_name: rs.client_name,
      job_type: rs.job_type, subject_id: subjId, is_adjoiner: true, adjoiner_name: subj.name
    });
    if (res.success) {
      subj.plat_saved = true;
      if (res.saved_to) subj.plat_path = res.saved_to;
      await persistSession();
      showToast(`Plat saved for ${subj.name}`, 'success');
      document.getElementById('adjPlatPickOverlay')?.remove();
      renderResearchBoard();
    } else { showToast('Save failed: ' + res.error, 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

async function _pickAdjPlatOnline(subjId, idx) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  const r = state._adjPlatOnlineHits && state._adjPlatOnlineHits[idx];
  if (!subj || !r) return;
  try {
    const res = await apiFetch('/save-plat', 'POST', {
      source: 'online', doc_no: r.doc_no, location: r.location || '',
      job_number: rs.job_number, client_name: rs.client_name,
      job_type: rs.job_type, subject_id: subjId, is_adjoiner: true, adjoiner_name: subj.name
    });
    if (res.success) {
      subj.plat_saved = true;
      if (res.saved_to) subj.plat_path = res.saved_to;
      await persistSession();
      showToast(`Plat downloaded for ${subj.name}`, 'success');
      document.getElementById('adjPlatPickOverlay')?.remove();
      renderResearchBoard();
    } else { showToast('Download failed: ' + res.error, 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
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

async function reparseClientCallsFromSession(silent = false) {
  if (!state.selectedDetail) {
    if (!silent) showToast("No deed detail loaded — search in Step 2 first", "warn");
    return;
  }
  try {
    const res = await apiFetch("/parse-calls", "POST", { detail: state.selectedDetail });
    if (!res.success) { if (!silent) showToast("Parse error: " + res.error, "error"); return; }
    state.parsedCalls = res.calls || [];
    renderS6CallsTable(res);
    if (!silent) showToast(`${res.count} call${res.count !== 1 ? "s" : ""} parsed from deed`, res.count ? "success" : "warn");
    else if (res.count) showToast(`✓ ${res.count} boundary calls imported from deed`, "success");
  } catch(e) {
    if (!silent) showToast("Error: " + e.message, "error");
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

function autoPopulateAdjoiners(silent = false) {
  if (!state.researchSession) { if (!silent) showToast("Load a session first", "warn"); return; }
  const adjs = state.researchSession.subjects.filter(s => s.type === "adjoiner");
  if (!adjs.length) { if (!silent) showToast("No adjoiners on the research board", "info"); return; }
  let added = 0;
  adjs.forEach(subj => {
    if (!state.adjoinParcels.some(p => p.label.toLowerCase() === subj.name.toLowerCase())) {
      state.adjoinParcels.push({ label: subj.name, layer: "ADJOINERS", calls: [], start_x: 0, start_y: 0, deed_path: subj.deed_path||"", plat_path: subj.plat_path||"", extracting: false });
      added++;
    }
  });
  renderS6ParcelList();
  if (!silent) showToast(added ? `${added} parcels added` : "All adjoiners already in list", added ? "success" : "info");
  else if (added) showToast(`✓ ${added} adjoiner parcel${added !== 1 ? 's' : ''} auto-populated`, "success");
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

  // Reveal the footer stats bar (hidden on first load until a session exists)
  const statsBar = document.getElementById("footerStats");
  if (statsBar) statsBar.classList.remove("hidden");

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
  showToast("Research summary exported as CSV", "success");
}

// ─────────────────────────────────────────────────────────────────────────────
// STEP 1: PROPERTY PICKER  (Leaflet.js — separate from Step 4 adjoiner picker)
// ─────────────────────────────────────────────────────────────────────────────

const _propPicker = {
  map:           null,   // Leaflet map instance
  parcelLayer:   null,   // GeoJSON layer
  selectedLayer: null,   // currently clicked polygon layer
  selectedProps: null,   // properties of selected feature
  geojsonData:   null,   // cached GeoJSON FeatureCollection
  searchTimer:   null,
  // Stores the chosen parcel so startSession can read it
  confirmedParcel: null,
};

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Called when the user types in the manual client name field */
function onClientNameTyped() {
  // Clear any map-confirmed selection so the typed name takes precedence
  const typed = document.getElementById('setupClient').value.trim();
  if (typed) {
    _propPicker.confirmedParcel = null;
    document.getElementById('selectedParcelCard').classList.add('hidden');
  }
}

/** Clear the KML-confirmed parcel selection */
function clearPropertySelection() {
  _propPicker.confirmedParcel = null;
  document.getElementById('selectedParcelCard').classList.add('hidden');
  document.getElementById('setupClient').value = '';
}

// ── Open / close modal ────────────────────────────────────────────────────────

async function showPropertyPicker() {
  document.getElementById('propPickerOverlay').classList.remove('hidden');

  if (!_propPicker.map) {
    _initPropPickerMap();
  } else {
    setTimeout(() => _propPicker.map && _propPicker.map.invalidateSize(), 150);
  }

  await _loadPropPickerMapData();
}

function closePropPicker() {
  document.getElementById('propPickerOverlay').classList.add('hidden');
}

// ── Initialise Leaflet ────────────────────────────────────────────────────────

function _initPropPickerMap() {
  const container = document.getElementById('propPickerLeafletMap');
  const canvasRenderer = L.canvas({ padding: 0.5 });

  _propPicker.map = L.map(container, {
    center: [36.6, -105.5],
    zoom:   11,
    preferCanvas: true,
    zoomControl: true,
  });
  _propPicker.renderer = canvasRenderer;

  // Voyager — clear road labels, neutral basemap, no API key needed
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager_labels_under/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 20,
  }).addTo(_propPicker.map);
}

// ── Load GeoJSON from backend ─────────────────────────────────────────────────

async function _loadPropPickerMapData() {
  const countEl  = document.getElementById('propPickerSearchCount');
  const loaderEl = document.getElementById('propPickerMapLoader');
  countEl.textContent = 'Loading…';
  if (loaderEl) loaderEl.classList.remove('hidden');
  try {
    const res = await apiFetch('/xml/map-geojson', 'POST', {
      highlight_upcs: [],
      max_features: 100000,
    });
    if (!res.success) {
      countEl.textContent = 'Error: ' + (res.error || 'Unknown');
      return;
    }
    if (!res.total) {
      countEl.textContent = 'No parcels in index. Build KML index first.';
      return;
    }
    _propPicker.geojsonData = res.geojson;
    countEl.textContent = res.total.toLocaleString() + ' parcels';
    _renderPropPickerLayer();
  } catch (e) {
    countEl.textContent = 'Load failed: ' + e.message;
  } finally {
    if (loaderEl) loaderEl.classList.add('hidden');
  }
}

// ── Build GeoJSON layer ───────────────────────────────────────────────────────

function _renderPropPickerLayer() {
  if (_propPicker.parcelLayer) {
    _propPicker.map.removeLayer(_propPicker.parcelLayer);
    _propPicker.parcelLayer = null;
  }

  _propPicker.parcelLayer = L.geoJSON(_propPicker.geojsonData, {
    renderer: _propPicker.renderer,
    style: () => ({
      fillColor:   '#1a7fd4',
      fillOpacity: 0.12,        // low fill — lets street labels show through
      color:       '#2196f3',   // vivid blue stroke stays crisp on light basemap
      weight:      1.5,
    }),
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
      radius: 5,
      fillColor: 'rgba(79,172,254,0.55)',
      color: '#4facfe',
      weight: 1.5,
      fillOpacity: 0.75,
    }),
    onEachFeature: (feature, layer) => {
      layer.on({
        click: e => { L.DomEvent.stopPropagation(e); _onPropParcelClick(feature, layer); },
        mouseover: () => {
          if (layer !== _propPicker.selectedLayer) {
            layer.setStyle && layer.setStyle({ fillOpacity: 0.30, weight: 2.5, color: '#79c8f8' });
          }
        },
        mouseout: () => {
          if (layer !== _propPicker.selectedLayer) {
            layer.setStyle && layer.setStyle({ fillColor: '#1a7fd4', fillOpacity: 0.12, color: '#2196f3', weight: 1.5 });
          }
        },
      });
      const p = feature.properties;
      const ttLines = [`<b>${p.owner || '(no name)'}</b>`];
      if (p.upc)  ttLines.push(`<span style="font-size:10px;opacity:.7">UPC: ${p.upc}</span>`);
      if (p.book || p.page) ttLines.push(`<span style="font-size:10px;opacity:.65">Bk ${p.book || ''}/${p.page || ''}</span>`);
      if (p.cab_refs_str) ttLines.push(`<span style="font-size:10px;opacity:.65">Cab: ${p.cab_refs_str}</span>`);
      layer.bindTooltip(ttLines.join('<br>'), { sticky: true, className: 'kml-tooltip', opacity: 0.97 });
    },
  }).addTo(_propPicker.map);

  try {
    const bounds = _propPicker.parcelLayer.getBounds();
    if (bounds.isValid()) _propPicker.map.fitBounds(bounds, { padding: [20, 20] });
  } catch (_) {}

  setTimeout(() => _propPicker.map && _propPicker.map.invalidateSize(), 200);
}

// ── Parcel click handler ──────────────────────────────────────────────────────

function _onPropParcelClick(feature, layer) {
  // Deselect previous
  if (_propPicker.selectedLayer && _propPicker.selectedLayer !== layer) {
    _propPicker.selectedLayer.setStyle && _propPicker.selectedLayer.setStyle({
      fillColor: '#1a7fd4', fillOpacity: 0.12, color: '#2196f3', weight: 1.5,
    });
  }

  layer.setStyle && layer.setStyle({
    fillColor: '#56d3a0', fillOpacity: 0.65, color: '#56d3a0', weight: 2.5,
  });

  _propPicker.selectedLayer = layer;
  _propPicker.selectedProps = feature.properties;

  const p = feature.properties;
  document.getElementById('propPickerOwner').textContent = p.owner || '(No Name)';

  let details = '';
  if (p.upc)          details += `<b>UPC:</b> ${escHtml(p.upc)}<br>`;
  if (p.book || p.page) details += `<b>Book/Page:</b> ${escHtml(p.book)}/${escHtml(p.page)}<br>`;
  if (p.cab_refs_str) details += `<b>Cabinet:</b> ${escHtml(p.cab_refs_str)}<br>`;
  if (p.plat)         details += `<b>Plat:</b> ${escHtml((p.plat || '').substring(0, 60))}${(p.plat || '').length > 60 ? '…' : ''}<br>`;
  if (!details)       details = '<span style="color:var(--text3);font-style:italic">No extended data.</span>';

  document.getElementById('propPickerDetails').innerHTML = details;
  document.getElementById('btnConfirmProperty').disabled = false;

  // Also highlight this item in the side list (if visible from search)
  document.querySelectorAll('.prop-picker-result-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.upc === p.upc);
  });
}

// ── Owner name search ─────────────────────────────────────────────────────────

function onPropPickerSearch(query) {
  clearTimeout(_propPicker.searchTimer);
  _propPicker.searchTimer = setTimeout(() => _doPropPickerSearch(query.trim()), 300);
}

async function _doPropPickerSearch(q) {
  const countEl = document.getElementById('propPickerSearchCount');
  const listEl  = document.getElementById('propPickerList');

  if (!q || q.length < 2) {
    // Reset map styles
    _propPicker.parcelLayer && _propPicker.parcelLayer.eachLayer(layer => {
      if (layer.setStyle) layer.setStyle({ fillColor: '#1a7fd4', fillOpacity: 0.12, color: '#2196f3', weight: 1.5 });
    });
    countEl.textContent = _propPicker.geojsonData ? _propPicker.geojsonData.features.length.toLocaleString() + ' parcels' : '—';
    listEl.innerHTML = '<div style="padding:12px 14px;font-size:11px;color:var(--text3);font-style:italic">Search or click a parcel on the map.</div>';
    return;
  }

  countEl.textContent = 'Searching…';
  listEl.innerHTML = '<div style="padding:16px;font-size:11px;color:var(--text3)">Searching…</div>';

  try {
    const res = await apiFetch('/parcel-search', 'POST', { query: q, operator: 'contains', limit: 40 });

    if (!res.success) {
      countEl.textContent = 'Error';
      listEl.innerHTML = `<div style="padding:12px;color:#ff7b72;font-size:12px">${escHtml(res.error || 'Search failed')}</div>`;
      return;
    }

    countEl.textContent = res.count + ' match' + (res.count !== 1 ? 'es' : '');

    // Highlight matching parcels on map
    const matchUpcs = new Set(res.results.map(r => r.upc).filter(Boolean));
    _propPicker.parcelLayer && _propPicker.parcelLayer.eachLayer(layer => {
      if (!layer.setStyle) return;
      const f = layer.feature;
      const upc = f && f.properties && f.properties.upc;
      if (matchUpcs.has(upc)) {
        layer.setStyle({ fillColor: '#56d3a0', fillOpacity: 0.45, color: '#56d3a0', weight: 2.0 });
      } else {
        layer.setStyle({ fillColor: '#aac4e0', fillOpacity: 0.04, color: '#aac4e0', weight: 0.4 });
      }
    });

    // Pan to first match centroid
    if (res.results.length && res.results[0].centroid) {
      const [lng, lat] = res.results[0].centroid;
      _propPicker.map.setView([lat, lng], 15, { animate: true });
    }

    // Populate side list
    if (!res.results.length) {
      listEl.innerHTML = `<div style="padding:16px;font-size:12px;color:var(--text3)">No parcels found for "<strong>${escHtml(q)}</strong>".<br><span style="font-size:10px;opacity:.6">Check that the KML index is built.</span></div>`;
    } else {
      listEl.innerHTML = res.results.map((p, pi) => `
        <div class="prop-picker-result-item" data-upc="${escHtml(p.upc || '')}" data-idx="${pi}" onclick="selectPropPickerResult(${pi})">
          <div class="prop-picker-result-name">${escHtml(p.owner)}</div>
          <div class="prop-picker-result-meta">${p.upc ? 'UPC: ' + escHtml(p.upc) : ''}${p.book ? ' · Bk ' + escHtml(p.book) : ''}${p.page ? '/' + escHtml(p.page) : ''}</div>
        </div>
      `).join('');

      // Store search results for click handler
      _propPicker._searchResults = res.results;
    }
  } catch (e) {
    countEl.textContent = 'Error';
    listEl.innerHTML = `<div style="padding:12px;color:#ff7b72;font-size:12px">Search error: ${escHtml(e.message)}</div>`;
  }
}

function selectPropPickerResult(idx) {
  const p = _propPicker._searchResults && _propPicker._searchResults[idx];
  if (!p) return;

  // Select corresponding parcel on map
  let found = false;
  _propPicker.parcelLayer && _propPicker.parcelLayer.eachLayer(layer => {
    if (found) return;
    const f = layer.feature;
    if (f && f.properties && f.properties.upc === p.upc) {
      _onPropParcelClick(f, layer);
      found = true;
      // Zoom to parcel
      try {
        const b = layer.getBounds ? layer.getBounds() : null;
        if (b && b.isValid()) _propPicker.map.fitBounds(b, { padding: [60, 60], maxZoom: 18 });
        else if (p.centroid) _propPicker.map.setView([p.centroid[1], p.centroid[0]], 17);
      } catch (_) {}
    }
  });

  // If parcel not on map but still want to select it
  if (!found && p.upc) {
    _propPicker.selectedProps = { owner: p.owner, upc: p.upc, book: p.book, page: p.page, plat: p.plat, cab_refs_str: (p.cab_refs || []).join(', ') };
    document.getElementById('propPickerOwner').textContent = p.owner || '—';
    document.getElementById('propPickerDetails').innerHTML = `<b>UPC:</b> ${escHtml(p.upc || '')}`;
    document.getElementById('btnConfirmProperty').disabled = false;
    if (p.centroid) _propPicker.map.setView([p.centroid[1], p.centroid[0]], 16);
  }

  // Update list highlighting
  document.querySelectorAll('.prop-picker-result-item').forEach((el, i) => {
    el.classList.toggle('selected', i === idx);
  });
}

// ── Reset map view ────────────────────────────────────────────────────────────

function propPickerResetView() {
  if (!_propPicker.parcelLayer || !_propPicker.map) return;
  document.getElementById('propPickerSearch').value = '';
  // Restore all polygon styles to default before calling search reset
  _propPicker.parcelLayer.eachLayer(layer => {
    if (layer !== _propPicker.selectedLayer && layer.setStyle) {
      layer.setStyle({ fillColor: '#1a7fd4', fillOpacity: 0.12, color: '#2196f3', weight: 1.5 });
    }
  });
  onPropPickerSearch('');
  try {
    const bounds = _propPicker.parcelLayer.getBounds();
    if (bounds.isValid()) _propPicker.map.fitBounds(bounds, { padding: [20, 20] });
  } catch (_) {}
}

// ── Confirm selection → fills Step 1 form ────────────────────────────────────

function confirmPropertySelection() {
  const p = _propPicker.selectedProps;
  if (!p || !p.owner) { showToast('No parcel selected', 'warn'); return; }

  // Convert "GARZA VERONICA" → "Garza, Veronica" style (Last, First) if possible
  const ownerRaw = p.owner.trim();
  let clientName = ownerRaw;
  if (!ownerRaw.includes(',') && ownerRaw.includes(' ')) {
    const parts = ownerRaw.split(/\s+/);
    if (parts.length >= 2) {
      clientName = parts[0] + ', ' + parts.slice(1).join(' ');
    }
  }
  // Title-case
  clientName = clientName.split(/\b/).map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join('');

  // Store confirmed parcel and update form
  _propPicker.confirmedParcel = p;
  document.getElementById('setupClient').value = clientName;

  // Show the selected parcel card
  document.getElementById('selectedParcelName').textContent = clientName;
  const meta = [
    p.upc ? 'UPC: ' + p.upc : '',
    (p.book && p.page) ? 'Bk ' + p.book + '/' + p.page : '',
  ].filter(Boolean).join('  ·  ');
  document.getElementById('selectedParcelMeta').textContent = meta;
  document.getElementById('selectedParcelCard').classList.remove('hidden');

  // Auto-zoom: briefly fly to the selected parcel so user sees it confirmed
  if (_propPicker.selectedLayer && _propPicker.map) {
    try {
      const b = _propPicker.selectedLayer.getBounds ? _propPicker.selectedLayer.getBounds() : null;
      if (b && b.isValid()) {
        _propPicker.map.flyToBounds(b, { padding: [80, 80], maxZoom: 18, duration: 0.6 });
      }
    } catch (_) {}
  }

  closePropPicker();
  showToast(`✓ Property selected: ${clientName}`, 'success');
}

// ─────────────────────────────────────────────────────────────────────────────
// KML PARCEL MAP PICKER  (Leaflet.js)
// ─────────────────────────────────────────────────────────────────────────────

const _kmlMap = {
  map:          null,   // Leaflet map instance
  parcelLayer:  null,   // GeoJSON layer for all parcels
  selectedLayer:null,   // currently selected polygon layer
  selectedProps:null,   // properties of selected feature
  geojsonData:  null,   // cached GeoJSON FeatureCollection
  highlightUpcs:[],     // UPCs to mark as "client"
  mapAddedNames:[],     // names added this session via the picker
};

// ── Open modal ──────────────────────────────────────────────────────────────
async function showKmlMapPicker() {
  document.getElementById('kmlMapPickerOverlay').classList.remove('hidden');

  // Gather UPCs already matched to the client deed (from KML hits in step 3)
  const clientUpcs = (state._kmlHits || []).map(h => h.upc).filter(Boolean);
  _kmlMap.highlightUpcs = clientUpcs;

  // Init map only once
  if (!_kmlMap.map) {
    _initKmlLeafletMap();
  } else {
    // Re-size in case the modal was resized or re-opened
    setTimeout(() => _kmlMap.map && _kmlMap.map.invalidateSize(), 120);
  }

  // Load / reload map data
  await _loadKmlMapData();
}

function closeKmlMapPicker() {
  document.getElementById('kmlMapPickerOverlay').classList.add('hidden');
}

// ── Initialise Leaflet ───────────────────────────────────────────────────────
function _initKmlLeafletMap() {
  const container = document.getElementById('kmlLeafletMap');

  // Canvas renderer — MUCH faster than default SVG for 60k+ polygons
  const canvasRenderer = L.canvas({ padding: 0.5 });

  _kmlMap.map = L.map(container, {
    center: [36.6, -105.5],   // Taos County, NM
    zoom:   11,
    zoomControl: true,
    attributionControl: true,
    preferCanvas: true,
  });
  _kmlMap.renderer = canvasRenderer;

  // Voyager — clear road labels, neutral basemap
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager_labels_under/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 20,
  }).addTo(_kmlMap.map);
}

// ── Load GeoJSON from backend ────────────────────────────────────────────────
async function _loadKmlMapData() {
  const statusEl  = document.getElementById('kmlMapStatus');
  const loaderEl  = document.getElementById('kmlMapLoader');
  const countEl   = document.getElementById('kmlMapSearchCount');
  statusEl.textContent = 'Loading parcels…';
  if (loaderEl) loaderEl.classList.remove('hidden');
  if (countEl)  countEl.textContent = '';

  try {
    const res = await apiFetch('/xml/map-geojson', 'POST', {
      highlight_upcs: _kmlMap.highlightUpcs,
      max_features:   100000,
    });

    if (!res.success) {
      statusEl.textContent = 'Error: ' + (res.error || 'Unknown');
      return;
    }

    if (!res.total) {
      statusEl.textContent = 'No parcels in index. Build KML index first.';
      return;
    }

    _kmlMap.geojsonData = res.geojson;
    statusEl.textContent = `${res.total.toLocaleString()} parcels loaded`;
    if (countEl) countEl.textContent = res.total.toLocaleString() + ' parcels';

    _renderKmlParcelLayer();
  } catch (e) {
    statusEl.textContent = 'Load failed: ' + e.message;
  } finally {
    if (loaderEl) loaderEl.classList.add('hidden');
  }
}

// ── Build / replace Leaflet GeoJSON layer ────────────────────────────────────
function _renderKmlParcelLayer() {
  if (_kmlMap.parcelLayer) {
    _kmlMap.map.removeLayer(_kmlMap.parcelLayer);
    _kmlMap.parcelLayer = null;
  }

  // Names already on the research board (for colouring)
  const boardNames = new Set(
    (state.researchSession?.subjects || []).map(s => s.name.toLowerCase())
  );

  _kmlMap.parcelLayer = L.geoJSON(_kmlMap.geojsonData, {
    renderer: _kmlMap.renderer,
    style: feature => _kmlParcelStyle(feature, boardNames),
    pointToLayer: (feature, latlng) => {
      const fill = _kmlParcelFill(feature, boardNames);
      return L.circleMarker(latlng, {
        radius:      6,
        fillColor:   fill,
        color:       fill,
        weight:      1.5,
        fillOpacity: 0.75,
      });
    },
    onEachFeature: (feature, layer) => {
      layer.on({
        click:     e => { L.DomEvent.stopPropagation(e); _onKmlParcelClick(feature, layer); },
        mouseover: e => {
          if (layer !== _kmlMap.selectedLayer) {
            layer.setStyle && layer.setStyle({ fillOpacity: 0.80, weight: 2.5 });
          }
        },
        mouseout: e => {
          if (layer !== _kmlMap.selectedLayer) {
            layer.setStyle && layer.setStyle(_kmlParcelStyle(feature, boardNames));
          }
        },
      });

      // Enriched tooltip with owner, UPC, book/page, cabinet
      const p = feature.properties;
      const ttLines2 = [`<b>${p.owner || '(no name)'}</b>`];
      if (p.upc)  ttLines2.push(`<span style="font-size:10px;opacity:.7">UPC: ${p.upc}</span>`);
      if (p.book || p.page) ttLines2.push(`<span style="font-size:10px;opacity:.65">Bk ${p.book || ''}/${p.page || ''}</span>`);
      if (p.cab_refs_str) ttLines2.push(`<span style="font-size:10px;opacity:.65">Cab: ${p.cab_refs_str}</span>`);
      layer.bindTooltip(ttLines2.join('<br>'), { sticky: true, className: 'kml-tooltip', opacity: 0.97 });
    },
  }).addTo(_kmlMap.map);

  // Fit map to data bounds
  try {
    const bounds = _kmlMap.parcelLayer.getBounds();
    if (bounds.isValid()) _kmlMap.map.fitBounds(bounds, { padding: [20, 20] });
  } catch (_) {}

  // Invalidate size now that modal is fully displayed
  setTimeout(() => _kmlMap.map && _kmlMap.map.invalidateSize(), 150);

  // Add pulse rings for any client / highlighted parcels
  setTimeout(_addClientPulseRings, 400);
}

// ── Styling helpers ──────────────────────────────────────────────────────────
function _kmlParcelFill(feature, boardNames) {
  const p = feature.properties;
  if (p.highlight) return '#e3c55a';                                      // client / highlighted → gold
  if (boardNames.has((p.owner || '').toLowerCase())) return '#b080e0';   // on research board → purple
  return '#4facfe';                                                        // regular parcel → blue
}

function _kmlParcelStyle(feature, boardNames) {
  const fill      = _kmlParcelFill(feature, boardNames);
  const highlight = feature.properties.highlight;
  const onBoard   = boardNames.has((feature.properties.owner || '').toLowerCase());
  return {
    fillColor:   fill,
    fillOpacity: highlight ? 0.50 : onBoard ? 0.35 : 0.12,  // low fill lets labels show through
    color:       highlight ? '#f0c040' : onBoard ? '#b080e0' : '#2196f3',  // vivid strokes on light basemap
    weight:      highlight ? 2.5 : onBoard ? 2.0 : 1.5,
  };
}

// ── Add a DOM pulse ring over the centroid of highlighted (client) parcels ───
function _addClientPulseRings() {
  // Remove any old rings first
  document.querySelectorAll('.parcel-pulse-ring').forEach(el => el.remove());
  if (!_kmlMap.parcelLayer || !_kmlMap.map) return;

  _kmlMap.parcelLayer.eachLayer(layer => {
    if (!layer.feature || !layer.feature.properties.highlight) return;
    try {
      let latlng;
      if (layer.getLatLng) {
        latlng = layer.getLatLng();
      } else if (layer.getBounds) {
        latlng = layer.getBounds().getCenter();
      } else return;

      const pt = _kmlMap.map.latLngToContainerPoint(latlng);
      const ring = document.createElement('div');
      ring.className = 'parcel-pulse-ring';
      ring.style.left = pt.x + 'px';
      ring.style.top  = pt.y + 'px';
      // Find the map container element
      const pane = _kmlMap.map.getPanes().overlayPane;
      pane.appendChild(ring);
    } catch (_) {}
  });
}

// ── Click handler ────────────────────────────────────────────────────────────
function _onKmlParcelClick(feature, layer) {
  // Deselect previous
  if (_kmlMap.selectedLayer && _kmlMap.selectedLayer !== layer) {
    const boardNames = new Set(
      (state.researchSession?.subjects || []).map(s => s.name.toLowerCase())
    );
    _kmlMap.selectedLayer.setStyle &&
      _kmlMap.selectedLayer.setStyle(_kmlParcelStyle(
        { properties: _kmlMap.selectedLayer.feature?.properties || {} },
        boardNames
      ));
  }

  // Highlight selected
  layer.setStyle && layer.setStyle({
    fillColor:   '#56d3a0',
    fillOpacity: 0.65,
    color:       '#56d3a0',
    weight:      2.5,
  });

  _kmlMap.selectedLayer = layer;
  _kmlMap.selectedProps = feature.properties;

  // Update info panel
  const p = feature.properties;
  document.getElementById('kmlInfoOwner').textContent = p.owner || '(No Name)';

  let details = '';
  if (p.upc)          details += `<b>UPC:</b> ${escHtml(p.upc)}<br>`;
  if (p.book || p.page) details += `<b>Book/Page:</b> ${escHtml(p.book)}/${escHtml(p.page)}<br>`;
  if (p.cab_refs_str) details += `<b>Cabinet:</b> ${escHtml(p.cab_refs_str)}<br>`;
  if (p.plat)         details += `<b>Plat:</b> ${escHtml(p.plat.substring(0, 80))}${p.plat.length > 80 ? '…' : ''}<br>`;
  if (!details)       details = '<span style="color:var(--text3);font-style:italic">No extended data.</span>';

  document.getElementById('kmlInfoDetails').innerHTML = details;

  // Enable action buttons
  document.getElementById('btnKmlAddAdjoiner').disabled = false;
  document.getElementById('btnKmlMarkClient').disabled  = false;
}

// ── Action: Add as Adjoiner ──────────────────────────────────────────────────
async function kmlAddSelectedAsAdjoiner() {
  if (!_kmlMap.selectedProps) return;
  const name = (_kmlMap.selectedProps.owner || '').trim();
  if (!name) { showToast('No owner name for this parcel', 'warn'); return; }

  await addFoundAdjoiner(name);

  // Track in picker's added list
  if (!_kmlMap.mapAddedNames.includes(name)) {
    _kmlMap.mapAddedNames.push(name);
    _updateKmlAddedList();
  }

  // Re-colour the selected polygon to board colour
  const boardNames = new Set(
    (state.researchSession?.subjects || []).map(s => s.name.toLowerCase())
  );
  _kmlMap.selectedLayer && _kmlMap.selectedLayer.setStyle &&
    _kmlMap.selectedLayer.setStyle({
      fillColor:   '#b080e0',
      fillOpacity: 0.55,
      color:       '#b080e0',
      weight:      2,
    });
}

// ── Action: Mark as Client Parcel ────────────────────────────────────────────
function kmlMarkSelectedAsClient() {
  if (!_kmlMap.selectedProps) return;
  const upc = _kmlMap.selectedProps.upc || '';

  // Add UPC to highlight set
  if (upc && !_kmlMap.highlightUpcs.includes(upc)) {
    _kmlMap.highlightUpcs.push(upc);
  }

  // Re-colour just this selected layer
  _kmlMap.selectedLayer && _kmlMap.selectedLayer.setStyle && _kmlMap.selectedLayer.setStyle({
    fillColor:   '#e3c55a',
    fillOpacity: 0.55,
    color:       '#e3c55a',
    weight:      2.5,
  });

  // Patch the feature properties so future re-renders respect it
  if (_kmlMap.selectedProps) _kmlMap.selectedProps.highlight = true;

  showToast(`Marked "${_kmlMap.selectedProps.owner}" as client parcel`, 'success');
  document.getElementById('btnKmlMarkClient').disabled = true;
}

// ── Owner name search / filter ───────────────────────────────────────────────
let _kmlSearchTimer = null;
function kmlMapOwnerSearch(query) {
  clearTimeout(_kmlSearchTimer);
  _kmlSearchTimer = setTimeout(() => _doKmlOwnerSearch(query.trim().toLowerCase()), 280);
}

function _doKmlOwnerSearch(q) {
  if (!_kmlMap.parcelLayer) return;
  const statusEl  = document.getElementById('kmlMapStatus');
  const countEl   = document.getElementById('kmlMapSearchCount');

  if (!q) {
    // Reset all styles
    const boardNames = new Set(
      (state.researchSession?.subjects || []).map(s => s.name.toLowerCase())
    );
    _kmlMap.parcelLayer.eachLayer(layer => {
      const f = layer.feature;
      if (f && layer.setStyle) layer.setStyle(_kmlParcelStyle(f, boardNames));
    });
    const total = (_kmlMap.geojsonData?.features?.length || 0).toLocaleString();
    statusEl.textContent = `${total} parcels`;
    if (countEl) countEl.textContent = '';
    return;
  }

  let hits = 0;
  const firstHitBounds = [];

  _kmlMap.parcelLayer.eachLayer(layer => {
    const f = layer.feature;
    if (!f || !layer.setStyle) return;
    const owner = (f.properties?.owner || '').toLowerCase();
    const match  = owner.includes(q);
    if (match) {
      hits++;
      layer.setStyle({ fillColor:'#56d3a0', fillOpacity:0.75, color:'#56d3a0', weight:2 });
      try {
        const b = layer.getBounds?.();
        if (b && firstHitBounds.length < 5) firstHitBounds.push(b);
      } catch (_) {}
    } else {
      // Dim-out style — visible outline on dark tiles, very low fill
      layer.setStyle({ fillColor:'#4facfe', fillOpacity:0.04, color:'rgba(79,172,254,0.15)', weight:0.8 });
    }
  });

  const matchLabel = `${hits} match${hits !== 1 ? 'es' : ''}`;
  statusEl.textContent = `${matchLabel} for "${q}"`;
  if (countEl) countEl.textContent = matchLabel;

  // Pan to first match
  if (firstHitBounds.length) {
    try {
      let combined = firstHitBounds[0];
      firstHitBounds.slice(1).forEach(b => { combined = combined.extend(b); });
      _kmlMap.map.fitBounds(combined, { padding: [40, 40], maxZoom: 16 });
    } catch (_) {}
  }
}

// ── Reset map view ───────────────────────────────────────────────────────────
function kmlMapResetView() {
  if (!_kmlMap.parcelLayer || !_kmlMap.map) return;
  document.getElementById('kmlMapSearch').value = '';
  const countEl = document.getElementById('kmlMapSearchCount');
  if (countEl) countEl.textContent = '';
  kmlMapOwnerSearch('');
  try {
    const bounds = _kmlMap.parcelLayer.getBounds();
    if (bounds.isValid()) _kmlMap.map.fitBounds(bounds, { padding: [20, 20] });
  } catch (_) {}
}

// ── "Added this session" sidebar list ───────────────────────────────────────
function _updateKmlAddedList() {
  const el = document.getElementById('kmlMapAddedList');
  if (!_kmlMap.mapAddedNames.length) {
    el.innerHTML = '<div style="font-size:11px;color:var(--text3);font-style:italic">None yet</div>';
    return;
  }
  el.innerHTML = _kmlMap.mapAddedNames.map(n =>
    `<div style="font-size:11px;padding:4px 8px;background:rgba(176,128,224,.12);border:1px solid rgba(176,128,224,.25);border-radius:6px;color:#b080e0">${escHtml(n)}</div>`
  ).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// UTILITIES
// ─────────────────────────────────────────────────────────────────────────────

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
