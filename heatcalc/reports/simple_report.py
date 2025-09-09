# reports/simple_report.py — header/footer + merged title+layout + labels under plot
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional, Any, Sequence, Tuple
from pathlib import Path
import os
from PyPDF2 import PdfMerger
from reportlab.pdfgen import canvas

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtCore import QRectF
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

import matplotlib

from heatcalc.utils.resources import get_resource_path

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    Table, TableStyle, Paragraph, Spacer, PageBreak, Image as RLImage,
    BaseDocTemplate, Frame, PageTemplate
)
from reportlab.lib.styles import getSampleStyleSheet
matplotlib.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "Liberation Serif"],
    "mathtext.fontset": "cm",   # Computer Modern, LaTeX-style maths
    "axes.unicode_minus": False,
})
FONT = "Times-Roman"
FONT_B = "Times-Bold"

# ---------------- Times font helper for matplotlib ----------------
def _times_rc():
    return {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "Liberation Serif", "DejaVu Serif"],
        "mathtext.fontset": "dejavuserif",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.titlesize": 14,
        "axes.labelsize": 12,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
    }

# ---------------- Report rows ----------------
@dataclass
class ProjectMeta:
    job_number: str
    project_title: str
    enclosure: str
    designer: str
    revision: str
    date: str

@dataclass
class ComponentRow:
    description: str
    part_no: str
    qty: int
    heat_each_w: float
    heat_total_w: float

@dataclass
class CableRow:
    name: str
    csa_mm2: float
    installation: str
    length_m: float
    current_A: float
    P_Wpm: float
    total_W: float

@dataclass
class TierRow:
    tag: str
    width_mm: int
    height_mm: int
    depth_mm: int
    components: List[ComponentRow]
    cables: List[CableRow]
    @property
    def heat_w(self) -> float:
        return sum(c.heat_total_w for c in self.components) + sum(cb.total_W for cb in self.cables)

@dataclass
class TierThermal:
    tag: str
    Ae: float
    P_W: float
    k: float
    c: float
    x: float
    f: Optional[float]
    g: Optional[float]
    vent: bool
    curve: int
    ambient_C: float
    dt_mid: float
    dt_top: float
    T_mid: float
    T_top: float
    max_C: int
    compliant_mid: bool
    compliant_top: bool
    airflow_m3h: Optional[float]

# ---------------- Helpers ----------------

def _make_disclaimer_page(path: Path):
    styles = getSampleStyleSheet()
    style = styles["Normal"]
    style.fontName = FONT
    style.fontSize = 11
    style.leading = 14

    disclaimer = """Disclaimer:<br/><br/>
    This document has been prepared by Maxwell Industries based on information and conditions available at the time of its creation. All recommendations and findings contained herein reflect the best available data, engineering principles, and professional judgment as of the date of completion. Any changes to the circumstances or information may affect the validity of the conclusions and recommendations presented.<br/><br/>
    The content of this document is confidential and has been prepared solely for the intended purpose as outlined in the contract between the client and Maxwell Industries. Any party using this document without obtaining the most current revision acknowledges that the information may be outdated or inaccurate. Maxwell Industries assumes no liability for any consequences arising from the misuse or misinterpretation of this document by third parties.<br/><br/>
    ©Copyright Maxwell Industries Pty Ltd.<br/>
    This document is the intellectual property of Maxwell Industries and is protected by copyright. No part of this document, whether in whole or substantial portion, may be reproduced or distributed without the prior written authorisation of Maxwell Industries. Unauthorised use or reproduction constitutes a violation of copyright law.
    """

    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=40, rightMargin=40,
                            topMargin=60, bottomMargin=40)
    flow = [Paragraph(disclaimer, style)]
    doc.build(flow)
    return path

# --- NEW: helper to render a per-tier components table ---
def _components_table_for_tier(tier: TierRow) -> Table:
    # Header row
    rows = [["Description", "Part #", "Qty", "W each", "W total"]]
    # Data rows
    for c in tier.components:
        rows.append([
            str(c.description or ""),
            str(c.part_no or ""),
            f"{int(c.qty)}",
            f"{float(c.heat_each_w):.1f}",
            f"{float(c.heat_total_w):.1f}",
        ])
    # Simple subtotal bar at bottom
    if tier.components:
        rows.append(["", "", "", "Subtotal (W):", f"{tier.heat_w:.1f}"])

    t = Table(rows, colWidths=[70*mm, 35*mm, 12*mm, 20*mm, 25*mm])
    t.setStyle(TableStyle([
        ("FONT", (0,0), (-1,-1), FONT, 9),
        ("FONT", (0,0), (-1,0), FONT_B, 9),
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("LINEBELOW", (0,0), (-1,-1), 0.25, colors.whitesmoke),
        ("ALIGN", (2,1), (4,-1), "RIGHT"),
        ("LEFTPADDING", (0,0), (-1,-1), 2),
        ("RIGHTPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    return t


def _ensure_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])

def render_scene_to_png(scene: Any, out_path: Path) -> Path:
    _ensure_app()
    br = scene.itemsBoundingRect()
    w = max(200, int(br.width()) + 40)
    h = max(200, int(br.height()) + 40)
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    scene.render(p, target=QRectF(0, 0, w, h), source=br)
    p.end()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path))
    return out_path

def render_temp_profile_png(title: str, ambient_C: float, T_mid: float, T_top: float, out_path: Path) -> Path:
    """Plot absolute temperature vs normalised height (no on-plot labels)."""
    with plt.rc_context(_times_rc()):
        xs = [ambient_C, T_mid, T_top]
        ys = [0.0, 0.5, 1.0]
        fig = plt.figure(figsize=(6.0, 3.2), dpi=144)
        ax = plt.gca()
        ax.plot(xs, ys, linewidth=2.2, marker="o")
        ax.set_xlim(min(xs) - 2, max(xs) + 2)
        ax.set_ylim(-0.02, 1.04)
        ax.set_xlabel("Temperature (°C)")
        ax.set_ylabel("Normalised Height")
        ax.grid(True, linestyle=":", linewidth=0.6)
        ax.set_title(title)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_path), format="png", bbox_inches="tight")
        plt.close(fig)
    return out_path

def _scale_to_fit(img_w_px: int, img_h_px: int, max_w_mm: float, max_h_mm: float, dpi: int = 144) -> Tuple[float, float]:
    max_w_pt = max_w_mm * mm
    max_h_pt = max_h_mm * mm
    w_pt = img_w_px * 72.0 / dpi
    h_pt = img_h_px * 72.0 / dpi
    scale = min(max_w_pt / w_pt, max_h_pt / h_pt)
    return w_pt * scale, h_pt * scale

# --------------- Header / Footer drawing ---------------
def _draw_header_footer(canvas, doc, meta: ProjectMeta, header_logo: Optional[Path], footer_img: Optional[Path]):
    w, h = A4
    canvas.saveState()

    # Thin blue bar ~1 cm from the top
    blue_y = h - 4*mm
    canvas.setFillColor(colors.HexColor("#0D4FA2"))
    canvas.rect(0, blue_y, w, 8*mm, stroke=0, fill=1)

    # White band below (logo & text area)
    white_h = 18*mm
    canvas.setFillColor(colors.white)
    canvas.rect(0, blue_y - white_h, w, white_h, stroke=0, fill=1)

    # Place logo inside the white band
    logo_x = 12*mm
    logo_y = blue_y - 15*mm
    if header_logo and Path(header_logo).exists():
        try:
            canvas.drawImage(str(header_logo), logo_x, logo_y, width=38*mm, height=12*mm,
                             preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    # (3) Tagline under the logo (light, small)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.setFont(FONT, 8)
    canvas.drawString(logo_x, logo_y - 4*mm, "IEC 60890 Temperature Rise Report")

    # Project title + date at the right (black text on white band)
    canvas.setFillColor(colors.black)
    canvas.setFont(FONT_B, 10)
    canvas.drawRightString(w - 12*mm, blue_y - 6*mm, (meta.project_title or "")[:90])
    canvas.setFont(FONT, 9)
    if getattr(meta, "date", ""):
        canvas.drawRightString(w - 12*mm, blue_y - 12*mm, meta.date)

    # Green line beneath the white band
    canvas.setFillColor(colors.HexColor("#009640"))
    canvas.rect(0, blue_y - white_h - 6*mm, w, 3*mm, stroke=0, fill=1)

    # (5) Dotted separator line under header band
    canvas.setStrokeColor(colors.HexColor("#B5B5B5"))
    canvas.setLineWidth(0.3)
    canvas.setDash(1, 2)  # dot pattern
    y_sep = blue_y - white_h - 3*mm - 1.2*mm
    canvas.line(12*mm, y_sep, w - 12*mm, y_sep)
    canvas.setDash()  # back to solid

    # Footer image (bottom-left) if provided
    if footer_img and Path(footer_img).exists():
        try:
            canvas.drawImage(str(footer_img), 10*mm, 8*mm, width=40*mm, height=10*mm,
                             preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    # (1) Thin grey line at footer (above page number / footer area)
    canvas.setStrokeColor(colors.HexColor("#B5B5B5"))
    canvas.setLineWidth(0.5)
    footer_rule_y = 20*mm
    canvas.line(12*mm, footer_rule_y, w - 12*mm, footer_rule_y)

    # Footer page number (bottom-right)
    canvas.setFillColor(colors.grey)
    canvas.setFont(FONT, 9)
    canvas.drawRightString(w - 12*mm, 10*mm, f"Page {doc.page}")

    canvas.restoreState()

# ---------------- Temperature rise coloured image. ----------------

def render_temp_slice_png(
    out_path: Path,
    ambient_C: float,
    T_mid: float,
    T_top: float,
    max_C: float | None,
    aspect_wh: float = 0.8,       # width / height ratio of tier (visual)
    title: str = "Vertical Temperature Slice",
    *, show_max_label: bool = True,  # <— default: do not annotate in-plot
    max_in_title: bool = True  # <— default: append “Max …°C” to title
) -> Path:
    """
    Draw a vertical “slice” of the enclosure as a coloured rectangle.
    y=0.0 -> ambient_C; y=0.5 -> T_mid; y=1.0 -> T_top (piecewise linear).
    """
    import numpy as np
    fig = plt.figure(figsize=(3.2, 3.2), dpi=144)  # square render, ignore aspect_wh
    ax = plt.gca()

    ax.set_title(
    f"{title}" + (f" — Max {max_C:.1f}°C" if (max_in_title and max_C is not None) else ""),
    fontsize = 11)

    # Build 1D temperature profile (0..1 height)
    y = np.linspace(0, 1, 300)
    T = np.empty_like(y)
    mid_idx = y <= 0.5
    # bottom→mid
    T[mid_idx] = ambient_C + (T_mid - ambient_C) * (y[mid_idx] / 0.5)
    # mid→top
    T[~mid_idx] = T_mid + (T_top - T_mid) * ((y[~mid_idx]-0.5) / 0.5)

    # Expand to 2D so imshow can draw a rectangle
    Z = np.tile(T[:, None], (1, 50))

    im = ax.imshow(
        Z, origin="lower", aspect="auto",
        extent=(0, 1, 0, 1),  # x from 0..1, y from 0..1
        cmap="plasma"
    )

    # Y-axis ticks at bottom/mid/top with temperatures
    ax.set_yticks([0.0, 0.5, 1.0])
    ax.set_yticklabels([
        f"Bottom (0.0t) — {ambient_C:.1f}°C",
        f"Mid (0.5t) — {T_mid:.1f}°C",
        f"Top (1.0t) — {T_top:.1f}°C",
    ])
    ax.set_xticks([])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_title(title, fontsize=11)

    # Optional max temperature line
    if max_C is not None:
        # convert max_C to a y-level on our piecewise profile (invert)
        # do a numeric search on y to find where T(y) == max_C
        idx = (np.abs(T - max_C)).argmin()
        y_max = float(y[idx])
        if 0.0 <= y_max <= 1.0:
            ax.axhline(y_max, ls="--", lw=1.2, color="k")

            if show_max_label:
                # put the label INSIDE the plot, just above the dashed line
                y_anno = min(0.98, y_max + 0.04)
                ax.text(0.5, y_anno, f"{max_C:.1f}°C",
                ha = "center", va = "bottom", fontsize = 8,
                        bbox = dict(boxstyle="round,pad=0.2", fc="white", ec="0.7", lw=0.5))

    # Colour bar (temperature)
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Temperature (°C)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), bbox_inches="tight")
    plt.close(fig)
    return out_path


# ---------------- Main: export_simple_report ----------------
def export_simple_report(
    out_pdf: Path,
    meta: ProjectMeta,
    enclosure_type: str,
    tiers: List[TierRow],
    totals: Dict[str, float] | None = None,
    scene: Any = None,
    diagram_png_path: Optional[Path] = None,
    curve_xs: Optional[Sequence[float]] = None,
    curve_ys: Optional[Sequence[float]] = None,
    curve_png_path: Optional[Path] = None,
    *,
    ambient_C: Optional[float] = None,
    tier_thermals: Optional[List[TierThermal]] = None,
    header_logo_path: Optional[Path] = None,   # optional assets
    footer_image_path: Optional[Path] = None,  # optional assets
) -> Path:

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    assets = out_pdf.parent / ".assets"
    assets.mkdir(exist_ok=True)

    if diagram_png_path is None and scene is not None:
        diagram_png_path = render_scene_to_png(scene, assets / "diagram.png")

    # Build per-tier temperature profile images + text lines (if thermal results present)
    tier_curve_images: List[Tuple[str, Path, str, str]] = []
    if tier_thermals:
        for th in tier_thermals:
            title = f"Temperature Profile — {th.tag}"
            subtitle = (
                f"P = {th.P_W:.1f} W · k = {th.k:.3f} · c = {th.c:.3f} · x = {th.x:.3f}"
                + (f" · f = {th.f:.3f}" if th.f is not None else "")
                + (f" · g = {th.g:.3f}" if th.g is not None else "")
                + f" · Ae = {th.Ae:.3f} m² · Ambient = {th.ambient_C:.1f} °C"
            )
            temps_line = (
                f"· Ambient = {th.ambient_C:.1f} °C<br/>"
                f"· Temperature at Half Height of Enclosure (0.5t) = {th.T_mid:.1f} °C<br/>"
                f"· Temperature at Full Height of Enclosure (1.0t) = {th.T_top:.1f} °C"
            )
            out_png = assets / f"tier_curve_{th.tag.replace(' ', '_')}.png"
            render_temp_profile_png(title, th.ambient_C, th.T_mid, th.T_top, out_png)
            tier_curve_images.append((title, out_png, subtitle, temps_line, th))

    if totals is None:
        total_w = sum(t.heat_w for t in tiers)
        totals = {"heat_total_w": round(total_w, 3)}

    # Styles
    styles = getSampleStyleSheet()
    H1 = styles["Title"];      H1.fontName = FONT_B
    H2 = styles["Heading2"];   H2.fontName = FONT_B
    H3 = styles["Heading3"];   H3.fontName = FONT_B
    Body = styles["BodyText"]; Body.fontName = FONT

    # Document with header/footer using a PageTemplate
    # Increase top margin so content clears the deeper header.
    doc = BaseDocTemplate(
        str(out_pdf), pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm, topMargin=40*mm, bottomMargin=20*mm,
        title=f"HeatCalc Report — {meta.project_title}", author=meta.designer,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='normal')
    template = PageTemplate(
        id='with-header-footer',
        frames=[frame],
        onPage=lambda c, d: _draw_header_footer(c, d, meta, header_logo_path, footer_image_path)
    )
    doc.addPageTemplates([template])

    flow = []

    # Title block (shares page with layout image)
    flow.append(Paragraph("Temperature Rise Report (IEC 60890)", H1))
    flow.append(Spacer(1, 6))
    kv = [["Job #", meta.job_number], ["Project", meta.project_title], ["Designer", meta.designer],
          ["Revision", meta.revision], ["Date", meta.date], ["Enclosure", meta.enclosure]]
    if ambient_C is not None:
        kv.append(["Ambient (°C)", f"{ambient_C:.1f}"])
    t = Table(kv, colWidths=[35*mm, 110*mm])
    t.setStyle(TableStyle([
        ("FONT", (0,0), (-1,-1), FONT, 10),
        ("TEXTCOLOR", (0,0), (0,-1), colors.grey),
        ("LINEBELOW", (0,0), (-1,-1), 0.25, colors.whitesmoke),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 2),
        ("RIGHTPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 10))

    # Switchboard Layout on the SAME page + subtext
    if diagram_png_path and Path(diagram_png_path).exists():
        try:
            from PIL import Image as PILImage
            img = PILImage.open(diagram_png_path)
            img_w, img_h = img.size
            dpi = int((img.info.get("dpi", (144,144))[0]) or 144)
        except Exception:
            img_w, img_h, dpi = 1200, 800, 144
        target_w_pt, target_h_pt = _scale_to_fit(img_w, img_h, max_w_mm=180, max_h_mm=110, dpi=dpi)
        flow.append(Paragraph("Switchboard Layout", H2))
        flow.append(Spacer(1, 4))
        flow.append(RLImage(str(diagram_png_path), width=target_w_pt, height=target_h_pt))
        flow.append(Spacer(1, 4))
        sub = (
            "This calculation was performed using the layout shown above. Tier sizes are indicative in the diagram "
            "and correspond to the dimensional inputs used in the thermal model."
        )
        flow.append(Paragraph(sub, Body))

    flow.append(Paragraph("Summary", H2))
    sum_rows = [["Total Heat Loss (W)", f"{totals.get('heat_total_w', 0.0):.1f}"],
                ["Number of Tiers", f"{len(tiers)}"]]
    t2 = Table(sum_rows, colWidths=[60*mm, 85*mm])
    t2.setStyle(TableStyle([
        ("FONT", (0,0), (-1,-1), FONT, 10),
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("LINEBELOW", (0,0), (-1,-1), 0.25, colors.whitesmoke),
        ("LEFTPADDING", (0,0), (-1,-1), 3),
        ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    flow.append(t2)
    flow.append(Spacer(1, 8))
    # Summary page
    flow.append(PageBreak())

    # Temperature compliance + per-tier tables (compact)
    thermal_by_tag = {th.tag: th for th in (tier_thermals or [])}
    flow.append(Paragraph("Tiers & Details", H2))
    for tier in tiers:
        flow.append(Paragraph(f"{tier.tag} — {tier.width_mm}×{tier.height_mm}×{tier.depth_mm} mm", H3))
        # --- NEW: per-tier components list below the thermal summary ---
        if tier.components:
            flow.append(Paragraph("Components", H3))
            flow.append(_components_table_for_tier(tier))
            flow.append(Spacer(1, 8))
        sub_tbl = Table([["Tier subtotal (W)", f"{tier.heat_w:.1f}"]], colWidths=[60*mm, 30*mm])
        sub_tbl.setStyle(TableStyle([
            ("FONT", (0,0), (-1,-1), FONT_B, 9),
            ("TEXTCOLOR", (0,0), (-1,-1), colors.black),
            ("ALIGN", (1,0), (1,0), "RIGHT"),
            ("LINEABOVE", (0,0), (-1,0), 0.5, colors.black),
            ("TOPPADDING", (0,0), (-1,-1), 3),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ]))
        flow.append(sub_tbl)
        flow.append(Spacer(1, 6))

    # 6) One Temperature Rise page per tier (image + temps line under plot)
    if tier_curve_images:
        for title, img_path, subtitle, temps_line, th in tier_curve_images:
            if not Path(img_path).exists():
                continue
            flow.append(PageBreak())
            flow.append(Paragraph(f"Temperature Rise Summary — {title}", H2))
            flow.append(Paragraph("Design Variables", H3))
            flow.append(Paragraph(subtitle, Body))
            flow.append(Paragraph("Temperature Rise Curve & Slice", H3))
            try:
                from PIL import Image as PILImage
                img = PILImage.open(img_path)
                img_w, img_h = img.size
                dpi = int((img.info.get("dpi", (144,144))[0]) or 144)
            except Exception:
                img_w, img_h, dpi = 1200, 800, 144
            target_w_pt, target_h_pt = _scale_to_fit(img_w, img_h, 180, 240, dpi)
            scale_factor = 0.6  # change to 0.8 if you prefer 80%
            target_w_pt *= scale_factor
            target_h_pt *= scale_factor
            flow.append(Spacer(1, 4))
            flow.append(RLImage(str(img_path), width=target_w_pt, height=target_h_pt))

            # Coloured vertical slice
            # Get a sensible aspect ratio from your tier geometry, if available
            # (fallback to 0.8 if not found)
            ratio = 0.8
            try:
                tr = next(t for t in tiers if t.tag == th.tag)
                if tr.height_mm > 0:
                    ratio = max(0.2, min(2.0, tr.width_mm / tr.height_mm))
            except Exception:
                pass

            slice_png = assets / f"temp_slice_{th.tag.replace(' ', '_')}.png"
            render_temp_slice_png(
                out_path=slice_png,
                ambient_C=th.ambient_C, T_mid=th.T_mid, T_top=th.T_top,
                max_C=getattr(th, "max_C", None),
                aspect_wh=ratio,
                title=f"Temperature Slice — {th.tag}"
            )
            # scale the slice image a touch smaller, too
            try:
                s = PILImage.open(slice_png);
                sw, sh = s.size
                s_dpi = int((s.info.get("dpi", (144, 144))[0]) or 144)
            except Exception:
                sw, sh, s_dpi = 600, 600, 144
            target_sw_pt, target_sh_pt = _scale_to_fit(sw, sh, 120, 140, s_dpi)
            scale_factor_slice = 0.7
            target_sw_pt *= scale_factor_slice
            target_sh_pt *= scale_factor_slice
            flow.append(Spacer(1, 6))
            flow.append(RLImage(str(slice_png), width=target_sw_pt, height=target_sh_pt))

            flow.append(Spacer(1, 6))

            # NEW — move the compliance/thermal summary table here (same page as the curves)
            comp_mid = "Compliant" if th.compliant_mid else "Cooling Required"
            comp_top = "Compliant" if th.compliant_top else "Cooling Required"
            comp_mid_color = colors.green if th.compliant_mid else colors.red
            comp_top_color = colors.green if th.compliant_top else colors.red

            main_rows = [
                ["Ambient (°C)", f"{th.ambient_C:.1f}"],
                ["dT @ 0.5t (K)", f"{th.dt_mid:.2f}"],
                ["dT @ 1.0t (K)", f"{th.dt_top:.2f}"],
                ["Final Temp @ 0.5t (°C)", f"{th.T_mid:.1f}"],
                ["Final Temp @ 1.0t (°C)", f"{th.T_top:.1f}"],
                ["Maximum Allowed (°C)", f"{th.max_C:d}"],
                ["Compliance @ 0.5t", comp_mid],
                ["Compliance @ 1.0t", comp_top],
            ]
            tbl = Table(main_rows, colWidths=[60 * mm, 85 * mm])
            tbl.setStyle(TableStyle([
                ("FONT", (0, 0), (-1, -1), FONT, 9),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.whitesmoke),
                ("TEXTCOLOR", (1, 6), (1, 6), comp_mid_color),
                ("TEXTCOLOR", (1, 7), (1, 7), comp_top_color),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            flow.append(Spacer(1, 6))
            flow.append(tbl)

            # If the top isn’t compliant and we’ve computed airflow, show the requirement under the table
            if not th.compliant_top and th.airflow_m3h is not None:
                flow.append(Spacer(1, 3))
                flow.append(Paragraph(
                    f"<b>Cooling required:</b> Provide ≥ {th.airflow_m3h:.0f} m^3/h of airflow to limit the top temperature to {th.max_C} °C.",
                    Body,
                ))
            flow.append(Spacer(1, 8))

    doc.build(flow)

    # Now merge cover + report + disclaimer
    final_path = out_pdf
    tmp_report = out_pdf.with_name(out_pdf.stem + "_body.pdf")
    out_pdf.rename(tmp_report)

    assets = out_pdf.parent / ".assets"
    disclaimer_pdf = _make_disclaimer_page(assets / "disclaimer.pdf")

    merger = PdfMerger()
    cover_path = Path(get_resource_path("heatcalc/assets/coverpage.pdf"))
    if cover_path.exists():
        merger.append(str(cover_path))
    merger.append(str(tmp_report))
    merger.append(str(disclaimer_pdf))
    merger.write(str(final_path))
    merger.close()

    tmp_report.unlink(missing_ok=True)
    return final_path


