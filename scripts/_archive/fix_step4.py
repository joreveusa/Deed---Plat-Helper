"""Repair the corrupted saveClientPlatOnline / runAdjoinerDiscovery section in app.js."""

GOOD_BLOCK = '''\
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
      if (res.saved_path) clientSubj.plat_path = res.saved_path;
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
      grid.innerHTML = `<div class="empty-state col-span-full">No adjoiners found automatically. Add manually below.</div>`;
    } else {
      let html = state.discoveredAdjoiners.map(j => `
        <div class="adjoiner-chip">
          <div class="flex-col">
            <span class="adjoiner-chip-name">${j.name}</span>
            <span class="source-tag text-text3">${j.source}</span>
          </div>
          <button class="btn btn-outline btn-sm" onclick="addFoundAdjoiner(\\'${j.name.replace(/\\'/g,"\\\\\\'")}\\')">+ Add</button>
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
'''

with open('app.js', 'r', encoding='utf-8') as f:
    src = f.read()

# Find the corrupted section: from async function saveClientPlatOnline to just before addAllDiscoveredToBoard
start_marker = 'async function saveClientPlatOnline'
end_marker = 'async function addAllDiscoveredToBoard'

si = src.find(start_marker)
ei = src.find(end_marker)

if si == -1:
    print('ERROR: saveClientPlatOnline not found')
elif ei == -1:
    print('ERROR: addAllDiscoveredToBoard not found')
else:
    new_src = src[:si] + GOOD_BLOCK + '\n' + src[ei:]
    with open('app.js', 'w', encoding='utf-8') as f:
        f.write(new_src)
    print(f'OK: replaced {ei - si} corrupted chars with {len(GOOD_BLOCK)} clean chars')
    print(f'New total length: {len(new_src)} chars')
