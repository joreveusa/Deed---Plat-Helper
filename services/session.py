"""
services/session.py — Research session I/O, project folder management, job numbering.

Handles the creation and reading of per-job research.json files,
project folder tree creation, and job numbering logic.
"""

import json
import os
import re
from pathlib import Path

from services.drive import get_survey_data_path, detect_survey_drive


def next_job_info() -> tuple[int, str]:
    """Scan Survey Data to find the next job number and its range folder."""
    survey_str = get_survey_data_path()
    if not survey_str or not Path(survey_str).exists():
        # Drive not connected — return safe defaults
        return 2938, "2900-2999"
    survey = Path(survey_str)
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


def create_project_folders(job_number, client_name, job_type) -> tuple[str, str]:
    """Create the full folder tree and return (project_path, deeds_path)."""
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


def job_base_path(job_number, client_name: str, job_type: str) -> Path:
    """Return the canonical job subfolder path (does NOT create folders).

    Pattern: SurveyData / {range} / {job_number} {client_name} /
             {job_number}-01-{job_type} {last_name}
    """
    rstart    = (int(job_number) // 100) * 100
    last_name = client_name.split(",")[0].strip()
    return (
        Path(get_survey_data_path())
        / f"{rstart}-{rstart + 99}"
        / f"{job_number} {client_name}"
        / f"{job_number}-01-{job_type} {last_name}"
    )


def _research_path(job_number, client_name, job_type) -> Path:
    return job_base_path(job_number, client_name, job_type) / "E Research" / "research.json"


def load_research(job_number, client_name, job_type) -> dict:
    """Load the research session JSON for a given job."""
    p = _research_path(job_number, client_name, job_type)
    data = None
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    if data is None:
        # Default skeleton
        data = {
            "job_number":  job_number,
            "client_name": client_name,
            "job_type":    job_type,
            "subjects": [
                {"id": "client", "type": "client", "name": client_name,
                 "deed_saved": False, "plat_saved": False,
                 "status": "pending", "notes": "",
                 "deed_path": "", "plat_path": ""}
            ]
        }
    # Migrate subjects that pre-date new fields (idempotent)
    for s in data.get("subjects", []):
        s.setdefault("status",    "pending")
        s.setdefault("notes",     "")
        s.setdefault("deed_path", "")
        s.setdefault("plat_path", "")
    return data


def save_research(job_number, client_name, job_type, data: dict):
    """Save the research session JSON for a given job."""
    p = _research_path(job_number, client_name, job_type)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_safe_path(path: str) -> bool:
    """Validate that a path is within the survey drive or project directory."""
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
        # Allow paths on the survey drive
        drive = detect_survey_drive()
        if drive and str(resolved).upper().startswith(f"{drive}:\\"):
            return True
        # Allow paths within the project directory itself
        project_dir = Path(os.path.dirname(os.path.dirname(__file__))).resolve()
        if str(resolved).startswith(str(project_dir)):
            return True
        return False
    except Exception:
        return False


def ensure_dwg_folder(job_number, client_name, job_type) -> Path:
    """Return (and create) the B Drafting/dwg folder for the job."""
    dwg_dir = job_base_path(job_number, client_name, job_type) / "B Drafting" / "dwg"
    dwg_dir.mkdir(parents=True, exist_ok=True)
    return dwg_dir
