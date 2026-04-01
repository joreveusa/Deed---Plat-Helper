"""
helpers/dxf.py
==============
DXF boundary drawing generation.

Provides:
  - generate_boundary_dxf(parcels, output_dir, job_number, client_name,
                           job_type, options) → (saved_path, closure_errors)

Extracted from app.py.  Depends on ezdxf (lazy-imported) and
helpers.metes_bounds.calls_to_coords.
"""

import math, re
from pathlib import Path

from helpers.metes_bounds import calls_to_coords


def generate_boundary_dxf(
    parcels:     list[dict],
    output_dir:  str | Path,
    job_number,
    client_name: str,
    job_type:    str,
    options:     dict = None,
) -> tuple[str, list[dict]]:
    """
    Build a DXF file from one or more parcel call-lists.

    Args:
        parcels:     [{label, calls:[{bearing_label,azimuth,distance}], color}]
        output_dir:  folder to write the .dxf into (will be created).
        job_number:  project job number.
        client_name: client name for labelling.
        job_type:    e.g. "BDY".
        options:     optional dict of drawing flags (see below).

    Returns (saved_file_path, closure_errors) where closure_errors is
    [{label: str, error: float}] per parcel.

    options keys (all default True/on):
      draw_boundary    – draw closed polyline for each parcel
      draw_labels      – add bearing/distance MTEXT labels on each course
      draw_endpoints   – add a POINT at each vertex
      label_size       – text height in drawing units (default 2.0)
      close_tolerance  – if closure error < this value, force close (default 0.5 ft)
    """
    import ezdxf  # lazy import — heavy dependency

    opts = {
        "draw_boundary":  True,
        "draw_labels":    True,
        "draw_endpoints": False,
        "label_size":     2.0,
        "close_tolerance": 0.5,
    }
    if options:
        opts.update(options)

    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 2     # 2 = feet
    doc.header['$MEASUREMENT'] = 0  # imperial

    msp = doc.modelspace()

    # Layer definitions
    layer_defs = [
        ("CLIENT",    2,  "CONTINUOUS"),   # yellow
        ("ADJOINERS", 3,  "DASHED"),       # green, dashed
        ("LABELS",    7,  "CONTINUOUS"),   # white
        ("ENDPOINTS", 6,  "CONTINUOUS"),   # magenta
        ("INFO",      8,  "CONTINUOUS"),   # grey
    ]
    for name, color, lt in layer_defs:
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    text_h = float(opts.get("label_size", 2.0))
    closure_errors = []

    for parcel in parcels:
        label    = parcel.get("label", "Parcel")
        calls    = parcel.get("calls", [])
        p_color  = parcel.get("color", None)
        start_x  = float(parcel.get("start_x", 0.0))
        start_y  = float(parcel.get("start_y", 0.0))
        layer    = parcel.get("layer", "CLIENT")

        if not calls:
            continue

        pts = calls_to_coords(calls, start_x, start_y)

        # Check closure
        err = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        closure_errors.append({"label": label, "error": round(err, 4)})
        closed = err <= float(opts.get("close_tolerance", 0.5))

        if opts["draw_boundary"]:
            verts = [(p[0], p[1]) for p in pts]
            attribs = {"layer": layer, "closed": closed}
            if p_color:
                attribs["color"] = p_color
            pline = msp.add_lwpolyline(verts, dxfattribs=attribs)

        if opts["draw_endpoints"]:
            for px, py in pts:
                msp.add_point((px, py, 0), dxfattribs={"layer": "ENDPOINTS"})

        if opts["draw_labels"]:
            for i, c in enumerate(calls):
                x0, y0 = pts[i]
                x1, y1 = pts[i + 1]
                mx = (x0 + x1) / 2.0
                my = (y0 + y1) / 2.0

                az_rad   = math.radians(c.get('azimuth', c.get('azimuth_deg', 0)))
                perp_rad = az_rad + math.pi / 2
                offset   = text_h * 1.2
                lx = mx + offset * math.sin(perp_rad)
                ly = my + offset * math.cos(perp_rad)

                bearing_txt = c.get("bearing_label", "")
                dist_txt    = f"{c['distance']:.2f}'"
                txt = f"{bearing_txt}\\P{dist_txt}"

                msp.add_mtext(txt, dxfattribs={
                    "layer":       "LABELS",
                    "char_height": text_h,
                    "insert":      (lx, ly, 0),
                    "attachment_point": 5,
                })

        # Closure annotation
        if not closed and err > 0.01:
            note = (
                f"! Closure error: {err:.3f} ft\n"
                f"  Parcel: {label}"
            )
            msp.add_mtext(note, dxfattribs={
                "layer":       "INFO",
                "char_height": text_h,
                "insert":      (pts[0][0], pts[0][1] - text_h * 4, 0),
            })

    # Job info block at origin
    info_txt = (
        f"Job #{job_number}  {client_name}\\P"
        f"Type: {job_type}\\P"
        f"Generated: {__import__('datetime').date.today()}"
    )
    msp.add_mtext(info_txt, dxfattribs={
        "layer":       "INFO",
        "char_height": text_h * 0.8,
        "insert":      (0, -text_h * 8, 0),
    })

    # Save file
    dwg_dir = Path(output_dir)
    dwg_dir.mkdir(parents=True, exist_ok=True)
    last_name = client_name.split(",")[0].strip().title()
    filename = re.sub(r'[<>:"/\\|?*]', '', f"{job_number} {last_name} Boundary.dxf").strip()
    out_path = dwg_dir / filename
    doc.saveas(str(out_path))
    return str(out_path), closure_errors
