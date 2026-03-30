"""Patch app.js: replace loadS2Detail with the enhanced deed viewer."""
import re

NEW_CODE = r'''async function loadS2Detail(docNo, idx, trEl) {
  // Highlight row
  document.querySelectorAll("#s2ResultsBody tr").forEach(tr => tr.classList.remove("selected"));
  if (trEl) trEl.classList.add("selected");

  const container = document.getElementById("s2DetailContainer");
  container.innerHTML = `<div class="loading-state flex-col gap-2"><div class="spinner"></div> Loading ${docNo}...</div>`;

  try {
    const res = await apiFetch(`/document/${encodeURIComponent(docNo)}`);
    if (!res.success) {
      container.innerHTML = `<div class="empty-state text-danger">Error: ${res.error}</div>`;
      return;
    }

    state.selectedDoc = state.searchResults?.[idx] || { doc_no: docNo };
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
}'''

with open('app.js', 'r', encoding='utf-8', newline='') as f:
    src = f.read()

# Locate and replace the original loadS2Detail function
start_marker = 'async function loadS2Detail(docNo, idx, trEl) {'
end_marker_after = '}\r\n\r\nasync function saveClientDeed'

si = src.find(start_marker)
ei = src.find(end_marker_after, si)

if si == -1:
    print('ERROR: start marker not found')
elif ei == -1:
    print('ERROR: end marker not found')
else:
    # Replace from start_marker through the closing brace before saveClientDeed
    # ei points to "}\r\n\r\nasync function saveClientDeed", we keep "\r\n\r\nasync function"
    replacement = NEW_CODE + '\r\n\r\n'
    new_src = src[:si] + replacement + src[ei+len('}\r\n\r\n'):]
    with open('app.js', 'w', encoding='utf-8', newline='') as f:
        f.write(new_src)
    print(f'OK: replaced {ei - si} chars with {len(NEW_CODE)} chars')
    print(f'New file length: {len(new_src)} chars')
