# Deed & Plat Helper

A local research assistant for **Red Tail Surveying** that streamlines the deed and plat research process for land boundary surveys.

---

## What It Does

Guides surveyors through a 6-step research workflow:

| Step | Purpose |
|---|---|
| **1 — Job Setup** | Select the client's property from a KML parcel map or type the name manually |
| **2 — Client Deed** | Search the 1stNMTitle county records database and save the client's deed |
| **3 — Client Plat** | Locate the client's survey plat in local scanner cabinets or online records |
| **4 — Find Adjoiners** | Auto-discover neighboring property owners via OCR, KML parcel data, and online records |
| **5 — Research Board** | Search and save deeds/plats for each adjoining property |
| **6 — Draw Boundary** | Parse metes-and-bounds calls from the deed, sketch the closure, and export a DXF file |

---

## Prerequisites

- **Python 3.11** — [python.org](https://python.org)
- **Tesseract OCR** — [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki)  
  Install to the default path: `C:\Program Files\Tesseract-OCR\`
- **Survey Data drive** — The app expects to find an `AI DATA CENTER\Survey Data` folder on a removable drive (auto-detected at startup). Drive letter is configurable in Settings.
- **1stNMTitle credentials** — Username and password for `records.1stnmtitle.com` (Taos County records portal)

---

## Installation

```sh
# 1. Clone or unzip the project
cd "Deed & Plat Helper"

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Running

Double-click **`Launch Deed & Plat Helper.bat`**  
or run manually:

```sh
python app.py
```

Then open **http://localhost:5000** in your browser.

---

## Configuration

On first run, click **⚙️ Settings** to enter:
- Your 1stNMTitle URL, username, and password
- Override the Survey Data drive letter if auto-detection fails

Settings are saved to `config.json` (excluded from git).

---

## KML Parcel Map

The property picker and adjoiner maps use a Taos County KML/KMZ parcel file.

**To set up:**
1. Place `Parcel_Maintenance.kmz` (or `TC_Parcels_2024.kml`) in:  
   `[Survey Drive]:\AI DATA CENTER\Survey Data\XML\`
2. Go to Step 3 → click **🗺️ KML Index** → **⚡ Build / Rebuild Index**

The index build takes ~60–90 seconds once; subsequent searches are instant.

---

## File Structure

```
Deed & Plat Helper/
├── app.py              # Flask backend — all API routes
├── app.js              # Frontend JavaScript
├── index.html          # Single-page HTML shell
├── style.css           # Dark glassmorphism theme
├── xml_processor.py    # KML/KMZ parcel data engine
├── config.json         # Local settings (credentials — not in git)
├── requirements.txt    # Python dependencies
├── Launch Deed & Plat Helper.bat
└── scripts/            # Development utility scripts (not part of app)
```

---

## How Survey Data is Organized

The app creates and reads files in this folder structure on the Survey Data drive:

```
Survey Data/
└── 3000-3099/                    # Job range folder
    └── 3001 Garza, Veronica/      # Client folder
        └── 3001-01-BDY Garza/     # Job sub-folder
            ├── A Office/
            ├── B Drafting/
            ├── C Survey/
            ├── D Correspondence/
            ├── E Research/
            │   ├── A Deeds/        # Client deed + Adjoiners/
            │   ├── B Plats/        # Client plat + Adjoiners/
            │   └── research.json   # Session state
            └── F PROOFING/
```
