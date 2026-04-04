"""
scripts/generate_batch_report.py — Generate HTML summary report for weekend batch.

Usage:
    python scripts/generate_batch_report.py <timestamp> <error_count> <warn_count>

Reads data/analytics_snapshot.json (if available) and produces
logs/weekend_report_<timestamp>.html
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime


def main():
    if len(sys.argv) < 4:
        print("Usage: generate_batch_report.py <timestamp> <errors> <warnings>")
        sys.exit(1)

    timestamp = sys.argv[1]
    errs = int(sys.argv[2])
    warns = int(sys.argv[3])

    status_emoji = "✅" if errs == 0 else "❌"
    status_text = "All Clear" if errs == 0 and warns == 0 else f"{errs} Errors, {warns} Warnings"

    # Load analytics snapshot if available
    analytics = {}
    snap_path = Path("data/analytics_snapshot.json")
    if snap_path.exists():
        try:
            analytics = json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    stats = analytics.get("stats", {})
    incomplete = analytics.get("incomplete_jobs", [])

    now = datetime.now()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Weekend Batch Report — {now.strftime('%B %d, %Y')}</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 24px; }}
  .container {{ max-width: 720px; margin: 0 auto; }}
  h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 12px; font-size: 22px; }}
  h2 {{ color: #8b949e; font-size: 15px; text-transform: uppercase; letter-spacing: 1px; margin-top: 28px; }}
  .status {{ display: inline-block; padding: 6px 16px; border-radius: 20px; font-weight: 600; font-size: 14px;
             background: {'#238636' if errs == 0 else '#da3633'}; color: #fff; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin: 8px 0; }}
  .stat {{ display: inline-block; width: 140px; text-align: center; margin: 8px; }}
  .stat-val {{ font-size: 28px; font-weight: 700; color: #58a6ff; }}
  .stat-label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; color: #8b949e; padding: 6px 8px; border-bottom: 1px solid #30363d; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #21262d; }}
  .pct {{ color: #f0883e; font-weight: 600; }}
  .footer {{ text-align: center; color: #484f58; font-size: 11px; margin-top: 32px; }}
</style>
</head>
<body>
<div class="container">
  <h1>{status_emoji} Weekend Batch Report</h1>
  <p class="status">{status_text}</p>
  <p style="color:#8b949e;font-size:13px">Generated {now.strftime('%A, %B %d, %Y at %I:%M %p')}</p>

  <h2>Research Analytics</h2>
  <div class="card" style="text-align:center">
    <div class="stat"><div class="stat-val">{stats.get('total_jobs', 0)}</div><div class="stat-label">Jobs Scanned</div></div>
    <div class="stat"><div class="stat-val">{stats.get('total_deeds', 0)}</div><div class="stat-label">Deeds Saved</div></div>
    <div class="stat"><div class="stat-val">{stats.get('total_plats', 0)}</div><div class="stat-label">Plats Saved</div></div>
    <div class="stat"><div class="stat-val">{stats.get('avg_completion_pct', 0)}%</div><div class="stat-label">Avg Completion</div></div>
  </div>
"""

    if stats.get("jobs_by_type"):
        html += """
  <h2>Jobs by Type</h2>
  <div class="card">
    <table>
      <tr><th>Type</th><th>Count</th><th></th></tr>
"""
        max_count = max(stats["jobs_by_type"].values()) if stats["jobs_by_type"] else 1
        for jtype, count in stats["jobs_by_type"].items():
            bar_width = int(count / max_count * 200)
            html += f'      <tr><td><strong>{jtype}</strong></td><td>{count}</td><td><div style="background:#238636;height:12px;width:{bar_width}px;border-radius:3px"></div></td></tr>\n'
        html += """    </table>
  </div>
"""

    if incomplete:
        html += """
  <h2>⚠ Incomplete Jobs (follow-up needed)</h2>
  <div class="card">
    <table>
      <tr><th>Job #</th><th>Client</th><th>Completion</th><th>Adjoiners</th></tr>
"""
        for j in incomplete[:15]:
            pct = j["pct"]
            color = "#da3633" if pct < 25 else "#f0883e" if pct < 50 else "#e3b341"
            html += f'      <tr><td>{j["job"]}</td><td>{j["client"]}</td><td class="pct" style="color:{color}">{pct}%</td><td>{j["adj"]}</td></tr>\n'
        if len(incomplete) > 15:
            html += f'      <tr><td colspan="4" style="color:#484f58;text-align:center">... and {len(incomplete) - 15} more</td></tr>\n'
        html += """    </table>
  </div>
"""

    if stats.get("completion_tiers"):
        tiers = stats["completion_tiers"]
        total_t = sum(tiers.values()) or 1
        html += """
  <h2>Completion Distribution</h2>
  <div class="card" style="display:flex;gap:12px;justify-content:center">
"""
        tier_colors = {"excellent": "#238636", "good": "#2ea043", "partial": "#f0883e", "minimal": "#da3633"}
        tier_labels = {"excellent": "≥90%", "good": "60-89%", "partial": "25-59%", "minimal": "<25%"}
        for tier, count in tiers.items():
            pct = round(count / total_t * 100)
            html += f'    <div style="text-align:center;flex:1"><div style="font-size:24px;font-weight:700;color:{tier_colors.get(tier, "#8b949e")}">{count}</div><div style="font-size:10px;color:#8b949e;text-transform:uppercase">{tier}<br>{tier_labels.get(tier, "")}</div></div>\n'
        html += """  </div>
"""

    html += f"""
  <div class="footer">
    Deed &amp; Plat Helper — Weekend Batch Maintenance<br>
    Log file: logs/weekend_batch_{timestamp}.log
  </div>
</div>
</body>
</html>"""

    # Write report
    os.makedirs("logs", exist_ok=True)
    report_path = f"logs/weekend_report_{timestamp}.html"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Report saved: {report_path}")


if __name__ == "__main__":
    main()
