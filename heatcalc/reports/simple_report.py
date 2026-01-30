# reports/simple_report.py — header/footer + merged title+layout + labels under plot
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional, Any, Sequence, Tuple
from pathlib import Path
import os

from PIL import Image, ImageEnhance, ImageFilter
from PyPDF2 import PdfMerger
from reportlab.pdfgen import canvas

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtCore import QRectF
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import tableofcontents

import matplotlib
from reportlab.platypus.tableofcontents import TableOfContents

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
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from heatcalc.utils.resources import get_resource_path

# ---------------- Font registration (Arial) ----------------
pdfmetrics.registerFont(
    TTFont("Arial", get_resource_path("heatcalc/assets/fonts/arial.ttf"))
)
pdfmetrics.registerFont(
    TTFont("Arial-Bold", get_resource_path("heatcalc/assets/fonts/arialbd.ttf"))
)
pdfmetrics.registerFont(
    TTFont("Arial-Italic", get_resource_path("heatcalc/assets/fonts/ariali.ttf"))
)
pdfmetrics.registerFont(
    TTFont("Arial-BoldItalic", get_resource_path("heatcalc/assets/fonts/arialbi.ttf"))
)

FONT = "Arial"
FONT_B = "Arial-Bold"
FONT_I = "Arial-Italic"
FONT_BI = "Arial-BoldItalic"
blue = colors.HexColor("#215096")
green = colors.HexColor("#007F4D")

from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, KeepTogether
matplotlib.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "Liberation Serif"],
    "mathtext.fontset": "cm",   # Computer Modern, LaTeX-style maths
    "axes.unicode_minus": False,
})

IEC_HEAD = "IEC 60890 Preconditions (Clause 4)"

from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib import colors

# ------------------------------------------------------------------
# Paragraph styles
# ------------------------------------------------------------------
_styles = getSampleStyleSheet()

H2 = ParagraphStyle(
    name="H2",
    parent=_styles["Heading2"],
    fontName=FONT_B,          # Arial Bold
    fontSize=14,
    leading=18,
    spaceBefore=6,
    spaceAfter=6,
    alignment=TA_LEFT,
)

Body = ParagraphStyle(
    name="Body",
    parent=_styles["BodyText"],
    fontName=FONT,            # Arial
    fontSize=10,
    leading=14,
    spaceBefore=4,
    spaceAfter=4,
    alignment=TA_LEFT,
)

BodySmall = ParagraphStyle(
    name="BodySmall",
    parent=_styles["BodyText"],
    fontName=FONT,            # Arial
    fontSize=8.5,
    leading=11,
    spaceBefore=2,
    spaceAfter=2,
    alignment=TA_LEFT,
    textColor=colors.grey,
)

# ---------------- Numbered Heading Styles ----------------


H1_NUM = ParagraphStyle(
    name="H1_NUM",
    parent=_styles["Heading1"],
    fontName=FONT_B,
    fontSize=18,
    leading=22,
    spaceBefore=12,
    spaceAfter=10,
    textColor=blue,          # ✅ big header blue
)

H2_NUM = ParagraphStyle(
    name="H2_NUM",
    parent=_styles["Heading2"],
    fontName=FONT_B,
    fontSize=14,
    leading=18,
    spaceBefore=10,
    spaceAfter=6,
    textColor=green,         # ✅ little header green
)

H3_NUM = ParagraphStyle(
    name="H3_NUM",
    parent=_styles["Heading3"],
    fontName=FONT_B,
    fontSize=12,
    leading=16,
    spaceBefore=8,
    spaceAfter=4,
    textColor=colors.black,  # ✅ little little header black
)
class SectionCounter:
    def __init__(self):
        self.h1 = 0
        self.h2 = 0
        self.h3 = 0

    def h1_num(self):
        self.h1 += 1
        self.h2 = 0
        self.h3 = 0
        return f"{self.h1}"

    def h2_num(self):
        self.h2 += 1
        self.h3 = 0
        return f"{self.h1}.{self.h2}"

    def h3_num(self):
        self.h3 += 1
        return f"{self.h1}.{self.h2}.{self.h3}"


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
    ip_rating_n: str

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
    # --- Identification / geometry ---
    tag: str
    Ae: float
    P_W: float

    # --- IEC 60890 scalar factors (as applied) ---
    k: float
    c: float
    x: float
    f: Optional[float]
    g: Optional[float]
    vent: bool
    curve: int
    ambient_C: float

    # --- Temperature rise results ---
    dt_mid: float
    dt_top: float
    T_mid: float
    T_top: float
    max_C: float
    compliant_mid: bool
    compliant_top: bool

    # Optional 0.75t values (only present when IEC requires/uses them)
    T_075: Optional[float] = None
    dt_075: Optional[float] = None

    # --- Cooling / dissipation breakdown (returned by IEC60890 calc) ---
    airflow_m3h: Optional[float] = None
    P_material_W: Optional[float] = None
    P_cooling_W: Optional[float] = None
    vent_recommended: bool = False
    inlet_area_cm2: float = 0.0
    P_890: Optional[float] = None
    solar_dt: Optional[float] = None

    # --- Natural ventilation (user-defined openings on the tier) ---
    naturally_vented: bool = False
    natural_vent_area_cm2: float = 0.0
    natural_vent_label: Optional[str] = None

    # --- Diagnostics / appendix ---
    dims_m: tuple[float, float, float] | None = None
    surfaces: list[dict] | None = None
    figures_used: list[str] | None = None



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
                            topMargin=20, bottomMargin=40)
    flow = [Paragraph(disclaimer, style)]
    doc.build(flow)
    return path

# --- NEW: helper to render a per-tier components table ---
def _components_table_for_tier(tier: TierRow) -> Table:
    styles = getSampleStyleSheet()

    Cell = ParagraphStyle(
        "Cell",
        fontName=FONT,
        fontSize=9,
        leading=11,
        spaceAfter=0,
        spaceBefore=0,
    )

    CellRight = ParagraphStyle(
        "CellRight",
        parent=Cell,
        alignment=2,  # TA_RIGHT
    )

    header = ["Description", "Part No.", "Qty", "W each", "W total"]
    rows = [header]

    for c in tier.components:
        rows.append([
            Paragraph(c.description, Cell),
            Paragraph(c.part_no or "", Cell),
            Paragraph(str(c.qty), CellRight),
            Paragraph(f"{c.heat_each_w:.1f}", CellRight),
            Paragraph(f"{c.heat_total_w:.1f}", CellRight),
        ])

    t = Table(rows, colWidths=[60*mm, 35*mm, 12*mm, 16*mm, 16*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0,0), (-1,0), FONT_B, 9),
        ("FONT", (0,1), (-1,-1), FONT, 9),
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("LINEBELOW", (0,0), (-1,0), 0.5, colors.grey),
        ("LINEBELOW", (0,1), (-1,-1), 0.25, colors.whitesmoke),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 3),
        ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    return t
from reportlab.platypus import Paragraph, Table, TableStyle, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

GREEN = colors.HexColor("#007F4D")

# Annex C reference values (fallback only)
ANNEX_C_SOLAR_DELTA_K = {
    "white": 10.0,
    "cream": 12.0,
    "yellow": 12.9,
    "light": 16.5,     # light grey / blue / green
    "medium": 21.0,    # medium grey / blue / green
    "dark": 24.4,      # dark grey / blue / green
    "black": 25.0,
}

def build_iec60890_checklist_section(
    iec60890_checklist,
    *,
    tier_thermals=None,
):
    styles = getSampleStyleSheet()
    elements = []

    body_style = ParagraphStyle(
        name="IECBody",
        parent=styles["BodyText"],
        fontName="Arial",
        fontSize=9,
        leading=11,
    )

    header_style = ParagraphStyle(
        name="IECHeader",
        parent=styles["BodyText"],
        fontName="Arial-Bold",
        fontSize=9,
        textColor=colors.white,
        alignment=1,
    )

    table_data = [[
        Paragraph("Item", header_style),
        Paragraph("Assessment Condition", header_style),
        Paragraph("Compliance", header_style),
    ]]

    solar_non_compliant = False

    for i, row in enumerate(iec60890_checklist, start=1):
        item = row.get("item", "")
        condition = row.get("condition", "")
        result = row.get("result", "")

        is_solar_row = item == "5.1-12"
        is_non_compliant = result == "Non-Compliant"

        display_result = result
        if is_solar_row and is_non_compliant:
            display_result = "Non-Compliant*"
            solar_non_compliant = True

        result_color = GREEN if result in ("Compliant", "N/A") else colors.red

        table_data.append([
            Paragraph(item, body_style),
            Paragraph(condition, body_style),
            Paragraph(
                display_result,
                ParagraphStyle(
                    name=f"ResultStyle_{i}",
                    parent=body_style,
                    textColor=result_color,
                    alignment=1,
                ),
            ),
        ])

    table = Table(
        table_data,
        colWidths=[55, 380, 90],
        repeatRows=1,
    )

    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("BACKGROUND", (0,0), (-1,0), GREEN),
        ("ALIGN", (0,0), (0,-1), "CENTER"),
        ("ALIGN", (-1,1), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.transparent]),
    ]))

    elements.append(table)

    # ---------------- Annex C justification (derived from results) ----------------
    if solar_non_compliant and tier_thermals:
        solar_dts = [
            float(getattr(th, "solar_dt", 0.0))
            for th in tier_thermals
            if getattr(th, "solar_dt", 0.0) > 0.0
        ]

        if solar_dts:
            applied_dt = max(solar_dts)

            justification = (
                "<b>* Solar radiation consideration (IEC TR 60890:2022 – Annex C)</b><br/>"
                "The enclosure is exposed to solar radiation and therefore does not "
                "meet the base assumption of IEC 60890 Clause 5.1. "
                "In accordance with IEC TR 60890:2022 Annex C, an additional internal "
                f"air temperature rise of <b>{applied_dt:.1f} K</b> has been applied "
                "to the calculated internal air temperature. "
                "This increase is based on enclosure surface characteristics and "
                "has been added to the temperature rise due to internal power losses "
                "within the IEC 60890 thermal model."
            )

            elements.extend([
                Spacer(1, 8),
                Paragraph(justification, body_style),
            ])

    return elements



# --- NEW: helper to render a per-tier cables table ---
def _cables_table_for_tier(tier: TierRow) -> Table:
    styles = getSampleStyleSheet()

    Cell = ParagraphStyle(
        "CableCell",
        fontName=FONT,
        fontSize=9,
        leading=11,
        spaceAfter=0,
        spaceBefore=0,
    )

    CellRight = ParagraphStyle(
        "CableCellRight",
        parent=Cell,
        alignment=2,  # TA_RIGHT
    )

    header = ["Cable", "CSA (mm²)", "Inst.", "Len (m)", "I (A)", "W/m", "Total (W)"]
    rows = [header]

    for cb in tier.cables:
        rows.append([
            Paragraph(str(cb.name or ""), Cell),
            Paragraph(f"{cb.csa_mm2:.1f}", CellRight),
            Paragraph(str(cb.installation or ""), Cell),
            Paragraph(f"{cb.length_m:.2f}", CellRight),
            Paragraph(f"{cb.current_A:.1f}", CellRight),
            Paragraph(f"{cb.P_Wpm:.2f}", CellRight),
            Paragraph(f"{cb.total_W:.1f}", CellRight),
        ])

    tbl = Table(
        rows,
        colWidths=[45*mm, 18*mm, 18*mm, 16*mm, 14*mm, 14*mm, 18*mm],
        repeatRows=1,
    )
    tbl.setStyle(TableStyle([
        ("FONT", (0,0), (-1,0), FONT_B, 9),
        ("FONT", (0,1), (-1,-1), FONT, 9),
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("LINEBELOW", (0,0), (-1,0), 0.5, colors.grey),
        ("LINEBELOW", (0,1), (-1,-1), 0.25, colors.whitesmoke),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 3),
        ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    return tbl



def _ensure_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if QApplication.instance() is None:
        _ = QApplication([])


def boost_png_contrast(path: Path,
                       gamma: float = 0.6,
                       contrast: float = 3.0,
                       sharpen: bool = True) -> None:
    """
    Aggressively increase contrast so faint UI lines/text become dark.

    gamma < 1 darkens midtones (0.5–0.7 is effective)
    contrast > 1 increases separation (2.5–3.5 works well)
    """
    img = Image.open(path).convert("L")  # grayscale

    # ---- Gamma correction ----
    inv_gamma = 1.0 / gamma
    lut = [int((i / 255.0) ** inv_gamma * 255) for i in range(256)]
    img = img.point(lut)

    # ---- Contrast boost ----
    img = ImageEnhance.Contrast(img).enhance(contrast)

    # ---- Optional sharpen (helps thin lines) ----
    if sharpen:
        img = img.filter(ImageFilter.SHARPEN)

    # Convert back to RGB for ReportLab compatibility
    img = img.convert("RGB")
    img.save(path)

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

    # Aggressively boost contrast for report readability
    boost_png_contrast(out_path)

    return out_path


def _scale_to_fit(img_w_px, img_h_px, max_w_mm, max_h_mm, dpi=144):
    max_w_pt = max_w_mm * mm
    max_h_pt = max_h_mm * mm
    w_pt = img_w_px / dpi * 72
    h_pt = img_h_px / dpi * 72
    scale = min(max_w_pt / w_pt, max_h_pt / h_pt, 1.0)
    return w_pt * scale, h_pt * scale

def render_temp_profile_png(
    title: str,
    ambient_C: float,
    T_mid: float,
    T_top: float,
    out_path: Path,
    *,
    T_075: float | None = None,
):
    """
    Plot absolute temperature vs normalised height (IEC 60890).
    Straight-line construction, matching UI behaviour.
    """
    with plt.rc_context(_times_rc()):
        fig = plt.figure(figsize=(6.0, 3.2), dpi=144)
        ax = plt.gca()

        # Ambient → mid-height
        xs = [ambient_C, T_mid]
        ys = [0.0, 0.5]

        ax.plot(xs, ys, linewidth=2.2)

        if T_075 is not None:
            # Ae ≤ 1.25 m² (Fig. 2)
            ax.plot([T_mid, T_075], [0.5, 0.75], linewidth=2.2)
            ax.plot([T_075, T_075], [0.75, 1.0], linewidth=2.2)

            ax.scatter(
                [T_mid, T_075, T_top],
                [0.5, 0.75, 1.0],
                zorder=5
            )
        else:
            # Ae > 1.25 m² (Fig. 1)
            ax.plot([T_mid, T_top], [0.5, 1.0], linewidth=2.2)
            ax.scatter([T_mid, T_top], [0.5, 1.0], zorder=5)

        ax.set_xlim(min(xs) - 2, max(T_top, T_mid) + 2)
        ax.set_ylim(-0.02, 1.04)
        ax.set_xlabel("Temperature (°C)")
        ax.set_ylabel("Normalised Height")
        ax.grid(True, linestyle=":", linewidth=0.6)
        ax.set_title(title)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_path), format="png", bbox_inches="tight")
        plt.close(fig)

    return out_path

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

# --------------- Header / Footer drawing ---------------
# --------------- Header / Footer drawing ---------------
def _draw_header_footer(
    canvas,
    doc,
    meta: ProjectMeta,
    header_logo: Optional[Path],
    footer_img: Optional[Path],
):
    w, h = A4
    canvas.saveState()

    blue = colors.HexColor("#215096")
    green = colors.HexColor("#007F4D")

    # ---------------- HEADER ----------------
    y = h - 18 * mm

    # LEFT: "Project:" + project name (no spacing)
    canvas.setFont(FONT_B, 10)
    canvas.setFillColor(blue)
    label_left = "Project:"
    canvas.drawString(12 * mm, y, label_left)

    # Measure label width so value starts immediately after it
    label_left_w = canvas.stringWidth(label_left, FONT_B, 10)

    canvas.setFont(FONT, 10)
    canvas.setFillColor(green)
    canvas.drawString(12 * mm + label_left_w, y, meta.project_title or "")

    # RIGHT: "Doc ID:" + doc id (no spacing), with value GREEN
    doc_id_value = "MI-DT-EN-028"
    label_right = "Doc ID:"

    # Compute widths so the whole "Doc ID:<value>" is right-aligned to margin
    label_right_w = canvas.stringWidth(label_right, FONT_B, 10)
    value_right_w = canvas.stringWidth(doc_id_value, FONT, 10)
    x_right = w - 12 * mm - (label_right_w + value_right_w)

    canvas.setFont(FONT_B, 10)
    canvas.setFillColor(blue)
    canvas.drawString(x_right, y, label_right)

    canvas.setFont(FONT, 10)
    canvas.setFillColor(green)  # ✅ value green
    canvas.drawString(x_right + label_right_w, y, doc_id_value)

    # ❌ Remove header bar/rule (do not draw any line)

    # ---------------- FOOTER ----------------
    if header_logo and Path(header_logo).exists():
        try:
            canvas.drawImage(
                str(header_logo),
                12 * mm,
                8 * mm,
                width=52.5 * mm,  # 35 × 1.5
                height=15 * mm,  # 10 × 1.5
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

    # Footer centre title — blue + bold
    canvas.setFont(FONT_B, 9)
    canvas.setFillColor(blue)
    canvas.drawCentredString(
        w / 2,
        12 * mm,
        "Temperature Rise Calculation",
    )

    page_label = "Page "
    page_value = f"C-{doc.page}"

    # Measure widths for right alignment
    label_w = canvas.stringWidth(page_label, FONT_B, 9)
    value_w = canvas.stringWidth(page_value, FONT, 9)

    x = w - 12 * mm - (label_w + value_w)
    y = 8 * mm

    # "Page" — bold blue
    canvas.setFont(FONT_B, 9)
    canvas.setFillColor(blue)
    canvas.drawString(x, y, page_label)

    # "C-n" — light green
    canvas.setFont(FONT, 9)
    canvas.setFillColor(green)
    canvas.drawString(x + label_w, y, page_value)

    canvas.restoreState()




def _iec_status_color(status: str):
    s = (status or "").strip().upper()
    if s == "PASS":
        return colors.lightgreen
    if s == "FAIL":
        return colors.salmon
    return colors.lightgrey

def _iec_bool_cell(val: bool) -> str:
    return "PASS" if val else "FAIL"

def iec60890_preconditions_section(iec60890_checklist):
    if not iec60890_checklist:
        return []

    title = Paragraph(IEC_HEAD, H2)
    intro_para = Paragraph(
        "The following checklist summarises key preconditions specified by IEC 60890 Clause 4, "
        "to ensure the calculation method is applicable to the enclosure configuration and "
        "installation conditions.",
        BodySmall
    )

    answers = []
    for item in iec60890_checklist:
        answers.append({
            "question": item.get("question", ""),
            "result": "PASS" if item.get("ok", False) else "FAIL",
            "note": item.get("note", ""),
        })

    data = [["#", "Requirement", "Status", "Notes"]]
    for i, a in enumerate(answers, start=1):
        data.append([str(i), a["question"], Paragraph(f"<b>{a['result']}</b>", Body), a["note"]])

    tbl = Table(data, colWidths=[8*mm, 92*mm, 18*mm, 62*mm])
    base = TableStyle([
        ("FONT", (0,0), (-1,0), FONT_B),
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), 0.3, colors.black),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ])
    tbl.setStyle(base)

    # Color the status cells
    for r in range(1, len(data)):
        status_text = str(answers[r - 1]["result"])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (2, r), (2, r), _iec_status_color(status_text)),
            ("TEXTCOLOR", (2, r), (2, r), colors.black),
        ]))

    return [
        KeepTogether([
            title,
            Spacer(1, 4),
            intro_para,
            Spacer(1, 6),
            tbl,
            Spacer(1, 12)
        ])
    ]

# ---------------- IEC60890 Template Table Helpers ----------------

def iec60890_tab_sheet(th: TierThermal) -> Table:
    rows = [
        ["Surface", "Dimensions (m)", "A0 (m²)", "b", "Ae (m²)"]
    ]

    for s in th.surfaces or []:
        rows.append([
            s["name"],
            f"{s['w']:.2f} × {s['h']:.2f}",
            f"{s['A0']:.2f}",
            f"{s['b']:.2f}",
            f"{s['Ae']:.2f}",
        ])

    rows.append(["", "", "", "Σ Ae", f"{th.Ae:.2f}"])

    tbl = Table(rows, colWidths=[40*mm, 40*mm, 25*mm, 20*mm, 25*mm])
    tbl.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.25, colors.black),
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("ALIGN", (2,1), (-1,-1), "RIGHT"),
        ("FONT", (0,0), (-1,0), FONT_B),
        ("FONT", (0,1), (-1,-1), FONT),
    ]))
    return tbl

def tier_cooling_summary(th) -> tuple[str, str, bool]:
    """
    Returns (arrangement, cooling_text, mitigation_required)
    """

    # 1. IEC 60890 compliant — no mitigation
    if th.compliant_top:
        return (
            "Natural convection",
            "–",
            False,
        )

    # 2. Forced ventilation required (mitigation)
    if th.airflow_m3h and th.airflow_m3h > 0:
        return (
            "Forced ventilation",
            f"{th.airflow_m3h:.0f}",
            True,
        )

    # 3. Genuinely non-compliant
    return (
        "Non-compliant",
        "Mitigation required",
        True,
    )



def build_tier_summary_page(tier_thermals):
    rows = [
        ["Tier", "Compliance", "Cooling Arrangement", "Cooling (m³/h)"]
    ]

    style_cmds = []

    for i, th in enumerate(tier_thermals, start=1):
        arrangement, cooling_req, mitigation = tier_cooling_summary(th)

        compliance = "Non-compliant" if mitigation else "Compliant"

        rows.append([
            th.tag,
            compliance,
            arrangement,
            cooling_req,
        ])

        # Compliance colouring (report-level compliance)
        if mitigation:
            style_cmds.append(
                ("TEXTCOLOR", (1, i), (1, i), colors.HexColor("#C62828"))  # red
            )
        else:
            style_cmds.append(
                ("TEXTCOLOR", (1, i), (1, i), colors.HexColor("#009640"))  # green
            )

        style_cmds.append(("FONTNAME", (1, i), (1, i), FONT_B))

    tbl = Table(
        rows,
        colWidths=[
            28 * mm,   # Tier
            32 * mm,   # Compliance
            60 * mm,   # Cooling Arrangement (reduced)
            30 * mm,   # Cooling (m³/h)
        ],
        repeatRows=1,
    )

    tbl.setStyle(TableStyle([
        # Header row
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#007F4D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), FONT_B),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),

        # Tier column
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#215096")),
        ("TEXTCOLOR", (0, 1), (0, -1), colors.white),
        ("FONTNAME", (0, 1), (0, -1), FONT_B),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),

        # Internal grid — black, thin
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),

        # Thick outer border
        ("BOX", (0, 0), (-1, -1), 1.5, colors.black),

        # General spacing
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),

        *style_cmds,
    ]))

    flow = []
    flow.append(Paragraph("Temperature Rise – Tier Summary", H1_NUM))
    flow.append(Spacer(1, 10))
    flow.append(tbl)

    return flow

def enclosure_dissipation_table(th: TierThermal) -> Table:
    def fmt(v, unit="", ndash="–"):
        if v is None:
            return ndash
        if isinstance(v, (int, float)):
            return f"{v:.1f}{unit}"
        return str(v)

    is_vented = bool(th.vent)

    rows = [
        ["Is vented (openings specified)", "Yes" if is_vented else "No"],

        [
            "Vent inlet opening area (cm²)",
            f"{th.inlet_area_cm2:.0f}"
            if is_vented and th.inlet_area_cm2 > 0
            else "N/A",
        ],

        [
            "Maximum natural enclosure dissipation P890 (W)",
            fmt(th.P_890, " W"),
        ],

        [
            "Heat for ventilation / cooling (W)",
            fmt(th.P_cooling_W, " W"),
        ],

        [
            "Required airflow (m³/h)",
            fmt(th.airflow_m3h, ""),
        ],
    ]

    tbl = Table(rows, colWidths=[95 * mm, 30 * mm])
    tbl.setStyle(TableStyle([
        # Label column
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#215096")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTNAME", (0, 0), (0, -1), FONT),

        # Values
        ("FONTNAME", (1, 0), (1, -1), FONT),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),

        # Borders
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BOX", (0, 0), (-1, -1), 1.2, colors.black),

        # Padding
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    return tbl


def render_tier_details(flow, tier):
    flow.append(Paragraph("Tier Details", H3_NUM))
    flow.append(Spacer(1, 4))

    if tier.components:
        flow.append(Paragraph("Components", H3_NUM))
        flow.append(_components_table_for_tier(tier))
        flow.append(Spacer(1, 6))

    if tier.cables:
        flow.append(Paragraph("Cables", H3_NUM))
        flow.append(_cables_table_for_tier(tier))
        flow.append(Spacer(1, 6))

    sub_tbl = Table(
        [["Tier heat subtotal (W)", f"{tier.heat_w:.1f}"]],
        colWidths=[60 * mm, 30 * mm],
    )
    sub_tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), FONT, 9),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.whitesmoke),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    flow.append(sub_tbl)
    flow.append(Spacer(1, 10))



    # ------------------------------------------------------------------
    # IEC Calculation
    # ------------------------------------------------------------------

def iec_scalar_table(th: TierThermal) -> Table:
    rows = [
        ["Effective area Ae (m²)", f"{th.Ae:.3f}"],
        ["k (enclosure constant)", f"{th.k:.3f}"],
        ["c (distribution factor)", f"{th.c:.3f}"],
        ["x (exponent factor)", f"{th.x:.3f}"],
    ]

    if th.f is not None:
        rows.append(["f (Ae > 1.25 m²)", f"{th.f:.3f}"])
    if th.g is not None:
        rows.append(["g (Ae ≤ 1.25 m²)", f"{th.g:.3f}"])

    if th.figures_used:
        rows.append([
            "IEC 60890 figures applied",
            ", ".join(th.figures_used)
        ])

    rows.append([
        "Ambient temperature (°C)",
        f"{th.ambient_C:.1f}"
    ])

    tbl = Table(rows, colWidths=[70*mm, 40*mm])
    tbl.setStyle(TableStyle([
        # Label column (blue)
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#215096")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTNAME", (0, 0), (0, -1), FONT),

        # Values
        ("FONTNAME", (1, 0), (1, -1), FONT),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),

        # Borders
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BOX", (0, 0), (-1, -1), 1.2, colors.black),

        # Spacing
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl

def iec_calc_banner(title: str) -> Table:
    rows = [[
        Paragraph(
            f"<b>{title}</b>",
            ParagraphStyle(
                "IEC_BANNER_TITLE",
                parent=H2,
                fontName=FONT_B,
                fontSize=18,          # ~2× larger
                leading=22,
                textColor=colors.white,
            ),
        ),
        Paragraph(
            "Effective cooling surfaces, IEC correction factors, "
            "and ventilation balance",
            ParagraphStyle(
                "IEC_BANNER_SUB",
                parent=BodySmall,
                fontName=FONT,
                fontSize=9,
                leading=12,
                textColor=colors.white,   # white text
            ),
        ),
    ]]

    tbl = Table(rows, colWidths=[110 * mm, 65 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#007F4D")),  # green
        ("BOX", (0, 0), (-1, -1), 1.5, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return tbl

def section_box(title: str, inner) -> KeepTogether:
    header = Table(
        [[
            Paragraph(
                f"<b>{title}</b>",
                ParagraphStyle(
                    "SECTION_HDR",
                    parent=Body,
                    fontName=FONT_B,
                    fontSize=11,
                    leading=14,
                    textColor=colors.white,
                ),
            )
        ]],
        colWidths=[155 * mm],
    )

    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#007F4D")),
        ("BOX", (0, 0), (-1, -1), 1.25, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    body = Table([[inner]], colWidths=[155 * mm])
    body.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.0, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    return KeepTogether([header, body])

# ---------------------------------------------------------------------
# Remaining plotting + report assembly functions
# (unchanged from your current version except where tiers now include cables,
#  and ventilation/dissipation is sourced from TierThermal fields returned by IEC calc)
# ---------------------------------------------------------------------

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
    iec60890_checklist=None
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
            title = f"{th.tag}"

            out_png = assets / f"tier_curve_{th.tag.replace(' ', '_')}.png"
            render_temp_profile_png(
                title,
                th.ambient_C,
                th.T_mid,
                th.T_top,
                out_png,
                T_075=getattr(th, "T_075", None),
            )
            tier_curve_images.append((title, out_png, th))

    if totals is None:
        total_w = sum(t.heat_w for t in tiers)
        totals = {"heat_total_w": round(total_w, 3)}


    # Document with header/footer using a PageTemplate
    class TOCDocTemplate(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if isinstance(flowable, Paragraph):
                text = flowable.getPlainText()
                style = flowable.style.name

                if style == "H1_NUM":
                    self.notify("TOCEntry", (0, text, self.page))
                elif style == "H2_NUM":
                    self.notify("TOCEntry", (1, text, self.page))
                elif style == "H3_NUM":
                    self.notify("TOCEntry", (2, text, self.page))

    doc = TOCDocTemplate(
        str(out_pdf),
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"HeatCalc Report — {meta.project_title}",
        author=meta.designer,
    )

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='normal')
    template = PageTemplate(
        id='with-header-footer',
        frames=[frame],
        onPage=lambda c, d: _draw_header_footer(c, d, meta, header_logo_path, footer_image_path)
    )
    doc.addPageTemplates([template])

    flow = []

    sec = SectionCounter()

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            name="TOCLevel1",
            fontName=FONT,
            fontSize=10,
            leftIndent=20,
            firstLineIndent=-20,
            spaceBefore=4,
        ),
        ParagraphStyle(
            name="TOCLevel2",
            fontName=FONT,
            fontSize=9,
            leftIndent=40,
            firstLineIndent=-20,
            spaceBefore=2,
        ),
        ParagraphStyle(
            name="TOCLevel3",
            fontName=FONT,
            fontSize=9,
            leftIndent=60,
            firstLineIndent=-20,
            spaceBefore=1,
        ),
    ]

    ################################## TABLE OF CONTENTS PAGE ########################################

    flow.append(Paragraph("Calculation Table of Contents", H1_NUM))
    flow.append(Spacer(1, 12))
    flow.append(toc)

    ################################## TABLE OF CONTENTS PAGE ########################################

    flow.append(PageBreak())

    ################################## HEADER PAGE ########################################
    # Title block (shares page with layout image)
    flow.append(Paragraph("Temperature Rise Report (IEC 60890)", H1_NUM))
    flow.append(Spacer(1, 6))

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

    ################################## HEADER PAGE ########################################

    flow.append(PageBreak())

    ################################## ASSUMPTIONS PAGE ########################################
    flow.append(Paragraph(
        "IEC 60890 – Calculation Preconditions and Compliance",
        H1_NUM
    ))
    flow.append(Spacer(1, 8))
    flow.extend(
        build_iec60890_checklist_section(
            iec60890_checklist,
            tier_thermals=tier_thermals,
        )
    )

    ################################## ASSUMPTIONS PAGE ########################################

    flow.append(PageBreak())

    ################################## SUMMARY PAGE ########################################

    # ---- Tier thermal summary (NEW) ----
    if tier_thermals:
        flow.extend(build_tier_summary_page(tier_thermals))

    ################################## SUMMARY PAGE ########################################

    ################################## PER TIER CALCULATION ########################################

    # One Temperature Rise page per tier (image + temps line under plot)
    if tier_curve_images:
        for title, img_path, th in tier_curve_images:

            flow.append(PageBreak())
            flow.append(Paragraph(
                f"{sec.h1_num()} Tier — {th.tag}",
                H1_NUM
            ))

            # Tier geometry lookup
            tier = next((t for t in tiers if t.tag == th.tag), None)
            if tier:
                render_tier_details(flow, tier)

            if not Path(img_path).exists():
                continue
            flow.append(Paragraph(
                f"{sec.h2_num()} Temperature Rise Summary — {title}",
                H1_NUM
            ))

            flow.append(Paragraph(
                f"{sec.h3_num()} Temperature Rise Curve",
                H3_NUM
            ))
            try:
                from PIL import Image as PILImage
                img = PILImage.open(img_path)
                img_w, img_h = img.size
                dpi = int((img.info.get("dpi", (144,144))[0]) or 144)
            except Exception:
                img_w, img_h, dpi = 1200, 800, 144
            target_w_pt, target_h_pt = _scale_to_fit(img_w, img_h, 180, 240, dpi)
            scale_factor = 0.6
            target_w_pt *= scale_factor
            target_h_pt *= scale_factor
            flow.append(Spacer(1, 4))
            flow.append(RLImage(str(img_path), width=target_w_pt, height=target_h_pt))

            # Determine aspect ratio from tier geometry (if available)
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

            # scale the slice image a touch smaller
            try:
                s = PILImage.open(slice_png)
                sw, sh = s.size
                dpi2 = int((s.info.get("dpi", (160,160))[0]) or 160)
                sw_pt, sh_pt = _scale_to_fit(sw, sh, 65, 170, dpi2)
            except Exception:
                sw_pt, sh_pt = 60*mm, 140*mm
            flow.append(Spacer(1, 6))
            flow.append(Paragraph(
                f"{sec.h3_num()} Temperature Slice",
                H3_NUM
            ))
            flow.append(Spacer(1, 4))

            flow.append(RLImage(str(slice_png), width=sw_pt, height=sh_pt))
            flow.append(Spacer(1, 6))

            # Summary table on the same page
            flow.append(Spacer(1, 8))
            comp_mid = "Compliant" if th.compliant_mid else "Not compliant"
            comp_top = "Compliant" if th.compliant_top else "Not compliant"
            comp_mid_color = colors.green if th.compliant_mid else colors.red
            comp_top_color = colors.green if th.compliant_top else colors.red

            main_rows = [
                ["Tier heat load (W)", f"{th.P_W:.1f}"],
                ["Effective cooling area Ae (m²)", f"{th.Ae:.3f}"],
                ["Final Temp @ 0.5t (°C)", f"{th.T_mid:.1f}"],
            ]

            if getattr(th, "T_075", None) is not None:
                main_rows.append(["Final Temp @ 0.75t (°C)", f"{th.T_075:.1f}"])

            main_rows += [
                ["Final Temp @ 1.0t (°C)", f"{th.T_top:.1f}"],
                ["Maximum Allowed (°C)", f"{th.max_C:.1f}"],
                ["Compliance @ 0.5t", comp_mid],
                ["Compliance @ 1.0t", comp_top],
            ]

            tbl = Table(main_rows, colWidths=[60 * mm, 85 * mm])
            style_cmds = [
                ("FONT", (0, 0), (-1, -1), FONT, 9),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.whitesmoke),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
            for i, row in enumerate(main_rows):
                label = row[0]
                if label == "Compliance @ 0.5t":
                    style_cmds.append(("TEXTCOLOR", (1, i), (1, i), comp_mid_color))
                elif label == "Compliance @ 1.0t":
                    style_cmds.append(("TEXTCOLOR", (1, i), (1, i), comp_top_color))
            tbl.setStyle(TableStyle(style_cmds))

            flow.append(Spacer(1, 8))
            flow.append(Paragraph(
                f"{sec.h3_num()} Temperature Rise Results",
                H3_NUM
            ))
            flow.append(Spacer(1, 4))

            flow.append(tbl)

            # ---------------------------------------------------------
            # PAGE 2 — IEC 60890 CALCULATION SHEET (PER TIER)
            # ---------------------------------------------------------
            flow.append(PageBreak())

            flow.append(Paragraph(
                f"{sec.h3_num()} IEC 60890 Calculation Sheet — {th.tag}",
                H3_NUM
            ))

            flow.append(Spacer(1, 6))
            flow.append(iec_calc_banner(f"IEC 60890 Calculation Sheet — {th.tag}"))
            flow.append(Spacer(1, 10))

            flow.append(
                section_box(
                    "Effective cooling surfaces and area factors",
                    iec60890_tab_sheet(th)
                )
            )

            flow.append(Spacer(1, 12))

            flow.append(
                section_box(
                    "IEC 60890 design variables",
                    iec_scalar_table(th)
                )
            )

            flow.append(Spacer(1, 12))

            flow.append(
                section_box(
                    "Enclosure heat dissipation and ventilation",
                    enclosure_dissipation_table(th)
                )
            )

            flow.append(Spacer(1, 14))

    doc.multiBuild(flow)
    return out_pdf


