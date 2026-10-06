# reports/simple_report.py — header/footer + merged title+layout + labels under plot
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional, Any, Sequence, Tuple
from pathlib import Path
import os
import hashlib
import math
from xml.sax.saxutils import escape

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

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

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
from matplotlib import font_manager
for _font_file in ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"):
    font_manager.fontManager.addfont(str(get_resource_path(f"heatcalc/assets/fonts/{_font_file}")))
pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_B, italic=FONT_I, boldItalic=FONT_BI)

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


# ---------------- Maxwell plot styling (matches the harmonic report) ----------------
def _maxwell_rc():
    return {
        "font.family": "sans-serif",
        "font.sans-serif": [FONT, "Liberation Sans", "DejaVu Sans"],
        "font.size": 8,
        "mathtext.fontset": "dejavusans",
        "axes.unicode_minus": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.labelsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
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
    use_manufacturer_derating: bool = False

@dataclass
class ComponentRow:
    description: str
    part_no: str
    qty: int
    heat_each_w: float
    heat_total_w: float
    max_temp_C: float | None = 70.0
    rated_current_A: float | None = None
    derating_temp_start_C: float | None = None
    derating_function: str | None = None
    derated_current_A: float | None = None
    key: str | None = None
    category: str | None = None

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
class BusRow:
    name: str
    width_mm: float
    thickness_mm: float
    parallel_bars: int
    length_m: float
    current_A: float
    total_W: float
    T_max_C: float
    T_min_C: float

@dataclass
class TierRow:
    tag: str
    width_mm: int
    height_mm: int
    depth_mm: int

    components: List[ComponentRow]
    cables: List[CableRow]
    buses: List[BusRow]

    # NEW
    joints: List = None
    loads: List = None   # future-proof
    max_temp_C: float = 70.0
    effective_max_temp_C: float | None = None

    h_partitions_enabled: bool = False
    h_partitions_count: int = 1

    # Includes drawn loads/sources even when there is no solved bus schedule.
    has_bus_elements: bool = False

    @property
    def heat_w(self) -> float:
        return (
            sum(c.heat_total_w for c in self.components) +
            sum(cb.total_W for cb in self.cables) +
            sum(b.total_W for b in self.buses) +
            sum(j.P_W for j in (self.joints or []))
        )

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
    selected_airflow_m3h: float = 0.0
    selected_fan_name: str = ""
    use_manufacturer_derating: bool = False
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

    # --- Horizontal partitions ---
    h_partitions_enabled: bool = False
    h_partitions_count: int = 1

    # --- Diagnostics / appendix ---
    dims_m: tuple[float, float, float] | None = None
    surfaces: list[dict] | None = None
    figures_used: list[str] | None = None

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
        fontSize=8,
        leading=10,
    )

    header_style = ParagraphStyle(
        name="IECHeader",
        parent=styles["BodyText"],
        fontName="Arial-Bold",
        fontSize=8,
        leading=10,
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
        colWidths=[18 * mm, 125 * mm, 32 * mm],
        repeatRows=1,
    )

    table.setStyle(_standard_table_style())

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
                "<b>Solar radiation consideration (IEC TR 60890:2022 - Annex C)</b><br/>"
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
                _note_callout(justification, prefix="*"),
            ])

    return elements
#### TABLES ######################################################################

TABLE_WIDTH = 175 * mm  # ✅ increased width


from heatcalc.core.compliance_61439 import evaluate_derating

def _standard_table_style():
    from .table_styles import standard_table_style
    return standard_table_style()


def _note_callout(text: str, *, prefix: str = "Note:") -> Table:
    note_style = ParagraphStyle("Note", fontName=FONT, fontSize=8.5, leading=12)
    content = Paragraph(f'<font color="#007F4D"><b>{prefix}</b></font> {text}', note_style)
    table = Table([[content]], colWidths=[TABLE_WIDTH], hAlign="CENTER")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#DDECD5")),
        ("LINEBEFORE", (0, 0), (0, 0), 3.2, green),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _report_table(rows, widths) -> Table:
    """Wrap text so long component names and headings stay inside their cells."""
    body = ParagraphStyle("ReportCell", fontName=FONT, fontSize=8, leading=10)
    header = ParagraphStyle("ReportHeader", parent=body, fontName=FONT_B,
                            textColor=colors.white, alignment=1)
    data = [[cell if isinstance(cell, Paragraph) else
             Paragraph(escape(str(cell)), header if index == 0 else body)
             for cell in row] for index, row in enumerate(rows)]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(_standard_table_style())
    return table

# ---------------- COMPONENTS ----------------
def _components_table_for_tier(tier: TierRow) -> Table:
    Cell = ParagraphStyle(
        "Cell",
        fontName=FONT,
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
    )

    header = ["Description", "Part No.", "Qty", "W each", "Total (W)"]
    rows = [header]

    for c in tier.components:
        rows.append([
            Paragraph(c.description, Cell),
            Paragraph(c.part_no or "", Cell),
            Paragraph(str(c.qty), Cell),
            Paragraph(f"{c.heat_each_w:.1f}", Cell),
            Paragraph(f"{c.heat_total_w:.1f}", Cell),
        ])

    colWidths = [72*mm, 36*mm, 14*mm, 20*mm, 33*mm]  # ≈175mm

    t = _report_table(rows, colWidths)
    t.setStyle(_standard_table_style())
    return t


# ---------------- CABLES ----------------
def _cables_table_for_tier(tier: TierRow) -> Table:
    Cell = ParagraphStyle(
        "Cell",
        fontName=FONT,
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
    )

    header = ["Cable", "CSA (mm²)", "Inst.", "Len (m)", "I (A)", "W/m", "Total (W)"]
    rows = [header]

    for cb in tier.cables:
        rows.append([
            Paragraph(str(cb.name or ""), Cell),
            Paragraph(f"{cb.csa_mm2:.1f}", Cell),
            Paragraph(str(cb.installation or ""), Cell),
            Paragraph(f"{cb.length_m:.2f}", Cell),
            Paragraph(f"{cb.current_A:.1f}", Cell),
            Paragraph(f"{cb.P_Wpm:.2f}", Cell),
            Paragraph(f"{cb.total_W:.1f}", Cell),
        ])

    colWidths = [38*mm, 18*mm, 18*mm, 17*mm, 17*mm, 17*mm, 50*mm]  # ≈175mm

    tbl = _report_table(rows, colWidths)
    tbl.setStyle(_standard_table_style())
    return tbl


# ---------------- BUS ----------------
def _bus_table_for_tier(tier: TierRow) -> Table:
    Cell = ParagraphStyle(
        "Cell",
        fontName=FONT,
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
    )

    header = ["Bus Line", "Dimensions (mm)", "Bars/Ph", "Len (m)", "I (A)", "Total (W)"]
    rows = [header]

    for b in tier.buses:
        rows.append([
            Paragraph(str(b.name or ""), Cell),
            Paragraph(f"{b.width_mm:.0f} x {b.thickness_mm:.0f}", Cell),
            Paragraph(f"{b.parallel_bars}", Cell),
            Paragraph(f"{b.length_m:.2f}", Cell),
            Paragraph(f"{b.current_A:.1f}", Cell),
            Paragraph(f"{b.total_W:.1f}", Cell),
        ])

    colWidths = [38*mm, 35*mm, 15*mm, 20*mm, 15*mm, 52*mm]  # ≈175mm

    tbl = _report_table(rows, colWidths)
    tbl.setStyle(_standard_table_style())
    return tbl


# ---------------- JOINTS ----------------
def _joint_table_for_tier(tier) -> Table:
    Cell = ParagraphStyle(
        "Cell",
        fontName=FONT,
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
    )

    header = ["Joint ID", "I (A)", "Total (W)"]
    rows = [header]

    for j in getattr(tier, "joints", []):
        rows.append([
            Paragraph(str(j.joint_id), Cell),
            Paragraph(f"{j.I_A:.1f}", Cell),
            Paragraph(f"{j.P_W:.1f}", Cell),
        ])

    colWidths = [55*mm, 40*mm, 80*mm]  # ≈175mm

    tbl = _report_table(rows, colWidths)
    tbl.setStyle(_standard_table_style())
    return tbl


#### TABLES ABOVE ######################################################################

def _ensure_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if QApplication.instance() is None:
        _ = QApplication([])


def boost_png_contrast(path: Path,
                       gamma: float = 0.85,
                       contrast: float = 1.6,
                       sharpen: bool = True) -> None:
    """
    Enhance contrast WITHOUT destroying colour.

    gamma < 1 slightly deepens midtones
    contrast > 1 increases separation
    """

    img = Image.open(path).convert("RGB")  # ✅ KEEP COLOUR

    # ---- Gamma correction (per channel) ----
    inv_gamma = 1.0 / gamma
    lut = [int((i / 255.0) ** inv_gamma * 255) for i in range(256)]
    img = img.point(lut * 3)  # apply to RGB channels

    # ---- Contrast boost ----
    img = ImageEnhance.Contrast(img).enhance(contrast)

    # ---- Optional sharpen ----
    if sharpen:
        img = img.filter(ImageFilter.SHARPEN)

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
    title: str, ambient_C: float, T_mid: float, T_top: float, out_path: Path,
    *, T_075: float | None = None,
):
    """Plot the solved temperatures using the Maxwell harmonic-report theme."""
    with matplotlib.rc_context(_maxwell_rc()):
        fig = Figure(figsize=(6.9, 2.6), dpi=200)
        FigureCanvasAgg(fig)
        ax = fig.subplots()
        temperatures = [ambient_C, T_mid]
        heights = [0.0, 0.5]
        if T_075 is not None:
            temperatures.append(T_075)
            heights.append(0.75)
        temperatures.append(T_top)
        heights.append(1.0)
        ax.plot(temperatures, heights, color="#215096", linewidth=2.2)
        ax.scatter(temperatures[1:], heights[1:], color="#007F4D", s=28, zorder=5)
        ax.set_xlim(min(temperatures) - 2, max(temperatures) + 2)
        ax.set_ylim(-0.02, 1.04)
        ax.set_xlabel("Temperature (°C)")
        ax.set_ylabel("Normalised height")
        ax.set_title(title, color="#215096", pad=10)
        ax.grid(True, color="#D9DEE5", linewidth=0.55, alpha=0.9)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("#3F4650")
            ax.spines[side].set_linewidth(0.8)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(str(out_path), format="png", facecolor="white")
        fig.clear()
    return out_path


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

    rows.append(["", "", "", "Total Ae", f"{th.Ae:.2f}"])

    tbl = Table(
        rows,
        colWidths=[45*mm, 45*mm, 25*mm, 20*mm, 40*mm],  # ~175mm
        repeatRows=1
    )

    tbl.setStyle(_standard_table_style())

    return tbl

def tier_cooling_summary(th) -> tuple[str, str, bool]:
    """Return arrangement, delivered/required airflow, and failed compliance."""
    selected = float(getattr(th, "selected_airflow_m3h", 0.0) or 0.0)
    compliant = bool(th.compliant_top and th.compliant_mid)
    if selected > 0:
        return "Forced ventilation", f"{selected:g}", not compliant
    if compliant:
        return "Natural convection", "-", False
    required = getattr(th, "airflow_m3h", None)
    if required and required > 0:
        return "Forced ventilation required", f"{required:.1f}", True
    return "Mitigation required", "-", True


def _selected_fan_text(th) -> str:
    selected = float(getattr(th, "selected_airflow_m3h", 0.0) or 0.0)
    if selected <= 0:
        return ""
    return (f"Selected fan: {escape(th.selected_fan_name or 'Unspecified model')}; "
            f"delivers {selected:g} m³/h of airflow.")


def _application_text(tier_thermals) -> str:
    return (
        "A compliant result means the calculated internal air temperatures are within the tier limits "
        "for the cooling arrangement shown. Natural-convection results use enclosure heat dissipation "
        "under the specified conditions. Forced-ventilation results include the selected fan and are "
        "conditional on providing and maintaining the stated delivered airflow through that tier. "
        "Enclosure temperature compliance does not establish full rated-current capacity for components. "
        "Apply the component current capacities and temperature limits reported for each tier."
    )


def _application_results(flow, tier_thermals, *, detailed=False):
    heading_style = ParagraphStyle("ApplicationHeading", parent=H2, textColor=green)
    blocks = [Spacer(1, 8), Paragraph("Important application of results", heading_style)]
    if detailed:
        text = _application_text(tier_thermals)
    else:
        th = tier_thermals[0]
        text = ("Compliance is conditional on providing and maintaining "
                "the stated delivered airflow through the tier. " if th.selected_airflow_m3h > 0 else
                "The calculated temperatures use natural enclosure heat dissipation under the specified conditions. ")
        text += ("A non-compliant result requires additional cooling or a design change. "
                 "Enclosure temperature compliance does not establish full rated-current capacity; "
                 "component current capacities and temperature limits also apply.")
    blocks.append(Paragraph(text, Body))
    fan_style = ParagraphStyle("ApplicationFan", parent=Body, leftIndent=12,
                               bulletIndent=0, bulletFontName=FONT, bulletFontSize=10)
    for th in tier_thermals:
        if getattr(th, "selected_airflow_m3h", 0) > 0:
            blocks.append(Paragraph(
                f'<font color="#007F4D"><b>{escape(th.tag)}:</b></font> {_selected_fan_text(th)}',
                fan_style, bulletText="•"))
    flow.extend(blocks)


def build_tier_summary_page(tier_thermals):
    rows = [["Tier", "Compliance", "Cooling arrangement", "Airflow (m³/h)"]]
    for th in tier_thermals:
        arrangement, airflow, mitigation = tier_cooling_summary(th)
        status_style = ParagraphStyle("SummaryStatus", fontName=FONT_B, fontSize=8,
                                      leading=10, textColor=colors.red if mitigation else green)
        rows.append([th.tag, Paragraph(_compliance_label(not mitigation), status_style),
                     arrangement, airflow])
    tbl = _report_table(rows, [28 * mm, 35 * mm, 77 * mm, 35 * mm])
    flow = [Paragraph("Temperature Rise - Tier Summary", H1_NUM), Spacer(1, 10), tbl]
    _application_results(flow, tier_thermals, detailed=True)
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

        [
            "Horizontal partitions",
            f"Enabled ({th.h_partitions_count} tiers)"
            if th.h_partitions_enabled
            else "Disabled",
        ],
    ]

    if th.selected_airflow_m3h > 0:
        rows.extend([
            ["Selected fan", th.selected_fan_name or "Unspecified model"],
            ["Delivered airflow (m³/h)", f"{th.selected_airflow_m3h:g}"],
        ])
    return _report_table([["Parameter", "Value"], *rows], [100 * mm, 75 * mm])


def render_tier_details(flow, tier, tier_thermal: Optional[TierThermal] = None):
    if tier.components:
        flow.append(Paragraph("Components", H3_NUM))
        flow.append(_components_table_for_tier(tier))
        flow.append(Spacer(1, 6))

    if tier.cables:
        flow.append(Paragraph("Cables", H3_NUM))
        flow.append(_cables_table_for_tier(tier))
        flow.append(Spacer(1, 6))

    if tier.buses:
        flow.append(Paragraph("Bus", H3_NUM))
        flow.append(_bus_table_for_tier(tier))
        flow.append(Spacer(1, 6))

    if getattr(tier, "joints", None):
        flow.append(Paragraph("Joints", H3_NUM))
        flow.append(_joint_table_for_tier(tier))
        flow.append(Spacer(1, 6))

    heat_val = tier_thermal.P_W if tier_thermal else tier.heat_w

    sub_tbl = Table(
        [["Tier heat subtotal (W)", f"{heat_val:.1f}"]],
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

def _compliance_label(ok: bool) -> str:
    return "Compliant" if ok else "Non-compliant"


def _apply_compliance_colours(tbl: Table, rows: list[list], compliance_col: int):
    cmds = []
    for r in range(1, len(rows)):
        txt = str(rows[r][compliance_col]).strip().lower()
        ok = txt.startswith("compliant")
        cell = tbl._cellvalues[r][compliance_col]
        if isinstance(cell, Paragraph):
            tbl._cellvalues[r][compliance_col] = Paragraph(cell.text, ParagraphStyle(
                "ComplianceCell", parent=cell.style, fontName=FONT_B,
                textColor=GREEN if ok else colors.red))
        cmds.append((
            "TEXTCOLOR",
            (compliance_col, r), (compliance_col, r),
            GREEN if ok else colors.red
        ))
        cmds.append((
            "FONTNAME",
            (compliance_col, r), (compliance_col, r),
            FONT_B
        ))
    tbl.setStyle(TableStyle(cmds))


def working_summary_table(th: TierThermal, comp=None) -> Table:
    # 1. Start with the calculated compliance limit if it exists
    built_in_limit = getattr(comp, "built_in_limit_C", None)

    # 2. Fall back to the tier-level thermal limit if missing from result
    if built_in_limit is None:
        built_in_limit = getattr(th, "max_C", 70.0)

    built_in_ok = getattr(comp, "built_in_ok", th.T_top <= built_in_limit)

    enclosure_top = getattr(comp, "enclosure_surface_T_top_side", None)
    enclosure_hot = getattr(comp, "enclosure_surface_T_hotspot", None)

    enclosure_limit = getattr(comp, "enclosure_limit_C", None)
    if enclosure_limit is None:
        enclosure_limit = th.ambient_C + 30.0

    enclosure_ok = getattr(comp, "enclosure_ok", True)

    terminals_max = getattr(comp, "terminals_max_T", None)
    terminals_ok = getattr(comp, "terminals_ok", True)

    # --- BUSBAR (SAFE + CONDITIONAL) ---
    busbar_max = getattr(comp, "busbar_max_T", None)

    # If comp doesn't provide it, look into th.buses
    if busbar_max is None:
        buses = getattr(th, "buses", []) or []
        if buses:
            busbar_max = max((b.T_max_C for b in buses), default=None)

    busbar_limit = getattr(comp, "busbar_limit_C", None)
    if busbar_limit is None:
        busbar_limit = 140.0

    busbar_ok = None
    if busbar_max is not None and busbar_limit is not None:
        busbar_ok = getattr(comp, "busbar_ok", None)
        if busbar_ok is None:
            busbar_ok = busbar_max <= busbar_limit

    rows = [
        ["Assessment Item", "Working Temp (°C)", "Limit (°C)", "Compliance"],
        ["Internal air @ top (1.0t)", f"{th.T_top:.1f}", f"{built_in_limit:.1f}", _compliance_label(built_in_ok)],
    ]

    if enclosure_top is not None:
        rows.append([
            "Enclosure top-side surface",
            f"{enclosure_top:.1f}",
            f"{enclosure_limit:.1f}",
            _compliance_label(enclosure_top <= enclosure_limit)
        ])

    if enclosure_hot is not None:
        rows.append([
            "Enclosure hotspot surface",
            f"{enclosure_hot:.1f}",
            f"{enclosure_limit:.1f}",
            _compliance_label(enclosure_ok)
        ])

    if terminals_max is not None:
        rows.append([
            "Maximum terminal temperature",
            f"{terminals_max:.1f}",
            "Load-specific",
            _compliance_label(terminals_ok)
        ])

    if busbar_max is not None and busbar_limit is not None:
        rows.append([
            "Maximum busbar temperature",
            f"{busbar_max:.1f}",
            f"{busbar_limit:.1f}",
            _compliance_label(busbar_ok)
        ])

    tbl = _report_table(rows, [75 * mm, 35 * mm, 30 * mm, 35 * mm])
    tbl.setStyle(_standard_table_style())
    _apply_compliance_colours(tbl, rows, 3)
    return tbl


def bus_working_temperature_table(tier: TierRow, comp=None) -> Table:
    limit_C = getattr(comp, "busbar_limit_C", 140.0)

    header = ["Bus", "I (A)", "T min (°C)", "Hotspot (°C)", "Limit (°C)", "Compliance"]
    rows = [header]

    for b in tier.buses:
        hotspot = float(getattr(b, "T_max_C", 0.0))
        tmin = float(getattr(b, "T_min_C", hotspot))
        ok = hotspot <= limit_C
        rows.append([
            str(b.name),
            f"{b.current_A:.1f}",
            f"{tmin:.1f}",
            f"{hotspot:.1f}",
            f"{limit_C:.1f}",
            _compliance_label(ok),
        ])

    tbl = _report_table(rows, [45 * mm, 20 * mm, 25 * mm, 30 * mm, 25 * mm, 30 * mm])
    tbl.setStyle(_standard_table_style())
    _apply_compliance_colours(tbl, rows, 5)
    return tbl


def joint_working_temperature_table(tier: TierRow, comp=None) -> Table:
    limit_C = getattr(comp, "busbar_limit_C", 140.0)

    header = ["Joint", "I (A)", "Temp (°C)", "Limit (°C)", "Compliance"]
    rows = [header]

    for j in getattr(tier, "joints", []) or []:
        Tj = float(getattr(j, "T_C", 0.0))
        ok = Tj <= limit_C
        rows.append([
            str(getattr(j, "joint_id", "")),
            f"{float(getattr(j, 'I_A', 0.0)):.1f}",
            f"{Tj:.1f}",
            f"{limit_C:.1f}",
            _compliance_label(ok),
        ])

    tbl = _report_table(rows, [55 * mm, 25 * mm, 30 * mm, 30 * mm, 35 * mm])
    tbl.setStyle(_standard_table_style())
    _apply_compliance_colours(tbl, rows, 4)
    return tbl


def terminal_working_temperature_table(comp) -> Table:
    header = ["Terminal / Load", "I (A)", "Source", "Temp (°C)", "Limit (°C)", "Compliance"]
    rows = [header]

    for tr in getattr(comp, "terminal_rows", []) or []:
        rows.append([
            str(tr.name),
            f"{tr.current_A:.1f}",
            str(tr.source_bus),
            f"{tr.temperature_C:.1f}",
            f"{tr.limit_C:.1f}",
            _compliance_label(bool(tr.compliant)),
        ])

    tbl = _report_table(rows, [45 * mm, 20 * mm, 30 * mm, 25 * mm, 25 * mm, 30 * mm])
    tbl.setStyle(_standard_table_style())
    _apply_compliance_colours(tbl, rows, 5)
    return tbl


def _component_derating_results(tier, th):
    from ..core.compliance_61439 import has_valid_derating_curve
    results = []
    for component in tier.components:
        rated = getattr(component, "rated_current_A", None)
        if rated is None or not math.isfinite(float(rated)) or float(rated) <= 0:
            continue
        use_curve = bool(getattr(th, "use_manufacturer_derating", False))
        derated = evaluate_derating(component, th.T_top, use_manufacturer_curve=use_curve)
        if derated is None:
            continue
        component.derated_current_A = derated
        manufacturer = use_curve and has_valid_derating_curve(component, [th.T_top])
        results.append((component, derated, manufacturer))
    return results


def component_derating_table(tier: TierRow, th: TierThermal, *, results=None) -> Table | None:
    results = _component_derating_results(tier, th) if results is None else results
    if not results:
        return None
    rows = [["Component", "Rated (A)", "Internal temp (°C)", "Derated output (A)"]]
    for component, derated, manufacturer in results:
        rows.append([str(component.description) + ("*" if manufacturer else ""),
                     f"{float(component.rated_current_A):.1f}", f"{th.T_top:.1f}",
                     f"{derated:.1f}"])
    return _report_table(rows, [85 * mm, 30 * mm, 30 * mm, 30 * mm])


def _has_bus_elements(tier, comp=None) -> bool:
    return bool(tier and (
        getattr(tier, "has_bus_elements", False) or tier.buses
        or getattr(tier, "joints", None) or getattr(tier, "loads", None)
        or getattr(tier, "sources", None) or getattr(comp, "terminal_rows", None)
    ))


def render_working_temperature_page(flow, sec, tier: TierRow, th: TierThermal, comp=None,
                                    *, derating_results=None):
    if _has_bus_elements(tier, comp):
        flow.append(Paragraph(
            f"{sec.h3_num()} AS/NZS 61439 Table 6 - Working Temperature Assessment", H3_NUM))
        flow.append(Paragraph(
            "The following tables summarise the steady-state working temperatures used for "
            "AS/NZS 61439 Table 6 assessment for this tier, including enclosure, busbar, joint "
            "and terminal temperatures where applicable.", BodySmall))
        flow.append(Paragraph(f"{sec.h3_num()} Tier / enclosure conditions", H3_NUM))
        flow.append(working_summary_table(th, comp))
        flow.append(Spacer(1, 8))
        if tier.buses:
            flow.append(Paragraph(f"{sec.h3_num()} Busbar working temperatures", H3_NUM))
            flow.append(bus_working_temperature_table(tier, comp))
            flow.append(Spacer(1, 8))
        if getattr(tier, "joints", None):
            flow.append(Paragraph(f"{sec.h3_num()} Joint working temperatures", H3_NUM))
            flow.append(joint_working_temperature_table(tier, comp))
            flow.append(Spacer(1, 8))
        if getattr(comp, "terminal_rows", None):
            flow.append(Paragraph(f"{sec.h3_num()} Terminal working temperatures", H3_NUM))
            flow.append(terminal_working_temperature_table(comp))
            flow.append(Spacer(1, 8))

    results = _component_derating_results(tier, th) if derating_results is None else derating_results
    if results:
        flow.append(Paragraph(f"{sec.h3_num()} Component derated current capacity", H3_NUM))
        flow.append(component_derating_table(tier, th, results=results))
        flow.append(Spacer(1, 6))
        if any(manufacturer for _, _, manufacturer in results):
            text = "Component(s) temperature deratings were solved directly using manufacturer data."
            fan_text = _selected_fan_text(th)
            if fan_text:
                text += " " + fan_text
            flow.append(_note_callout(text, prefix="*"))
            flow.append(Spacer(1, 6))
        basis = f"Current capacity evaluated at solved top air temperature ({th.T_top:.1f}°C). "
        if any(not manufacturer for _, _, manufacturer in results):
            basis += ("The 80% rated-current maximum applies to components where the manufacturer has not specified "
                      "a derating curve.")
        basis += "Component temperature ratings must also be satisfied."
        flow.append(Paragraph(basis, BodySmall))


# ------------------------------------------------------------------
# IEC Calculation
# ------------------------------------------------------------------

def iec_scalar_table(th: TierThermal) -> Table:
    rows = [
        ["Parameter", "Value"],
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

    tbl = _report_table(rows, [100 * mm, 75 * mm])

    tbl.setStyle(_standard_table_style())  # ✅ unified styling

    return tbl

def iec_calc_banner(title: str) -> Table:
    rows = [[
        Paragraph(
            f"<b>{title}</b>",
            ParagraphStyle(
                "IEC_BANNER_TITLE",
                parent=H2,
                fontName=FONT_B,
                fontSize=16,
                leading=20,
                textColor=colors.white,
                alignment=1,
            ),
        )
    ]]

    tbl = Table(rows, colWidths=[175 * mm])

    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), green),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
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
                    fontSize=10,
                    leading=12,
                    textColor=colors.white,
                    alignment=1,
                ),
            )
        ]],
        colWidths=[175 * mm],
    )

    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), green),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))

    body = Table([[inner]], colWidths=[175 * mm])
    body.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
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
    tier_compliance_results: Optional[List[Any]] = None,
    header_logo_path: Optional[Path] = None,
    footer_image_path: Optional[Path] = None,
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

    comp_by_tier = {
        str(getattr(c, "tier_id", "")): c
        for c in (tier_compliance_results or [])
    }

    # Document with header/footer using a PageTemplate
    class TOCDocTemplate(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if isinstance(flowable, Paragraph):
                level = {"H1_NUM": 0, "H2_NUM": 1, "H3_NUM": 2}.get(flowable.style.name)
                if level is not None:
                    text = flowable.getPlainText()
                    position = self.frame._y + flowable.height
                    key = f"toc_{self.page}_{position:.3f}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"
                    self.canv.bookmarkHorizontalAbsolute(key, position)
                    self.notify("TOCEntry", (level, escape(text), self.page, key))


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
        ParagraphStyle("TOCLevel1", fontName=FONT_B, fontSize=10, leading=14,
                       leftIndent=0, spaceBefore=7, textColor=colors.black),
        ParagraphStyle("TOCLevel2", fontName=FONT, fontSize=9, leading=12,
                       leftIndent=20, firstLineIndent=-20, spaceBefore=2, textColor=colors.black),
        ParagraphStyle("TOCLevel3", fontName=FONT, fontSize=8.5, leading=11,
                       leftIndent=38, firstLineIndent=-18, spaceBefore=1, textColor=colors.black),
    ]


    ################################## TABLE OF CONTENTS PAGE ########################################

    flow.append(Paragraph("Calculation Table of Contents", ParagraphStyle("ContentsTitle", parent=H1_NUM)))
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
            with PILImage.open(diagram_png_path) as img:
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

    if diagram_png_path and Path(diagram_png_path).exists():
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

            has_buses = bool(tier.buses) if tier else False
            has_components = bool(tier.components) if tier else False
            has_cables = bool(tier.cables) if tier else False
            has_joints = bool(getattr(tier, "joints", [])) if tier else False

            if tier and not any([has_buses, has_components, has_cables, has_joints, _has_bus_elements(tier)]):
                flow.append(Spacer(1, 10))
                flow.append(Paragraph(
                    "No heat-generating equipment or current-carrying conductors are present within this tier. "
                    "Accordingly, the internal heat dissipation (P) is effectively zero and, in accordance with the "
                    "IEC 60890 calculation framework, no temperature rise (ΔT) above ambient is produced. "
                    "The internal air temperature therefore remains equal to ambient conditions, and no thermal "
                    "verification is required for this tier as it does not contribute to the overall thermal behaviour "
                    "of the assembly.",
                    ParagraphStyle(
                        "NoEquipment",
                        parent=BodySmall,
                        textColor=colors.grey,
                        italic=True
                    )
                ))
                continue

            if tier:
                render_tier_details(flow, tier, tier_thermal=th)

            if not Path(img_path).exists():
                continue
            flow.append(Paragraph(
                f"{sec.h2_num()} Temperature Rise Summary — {title}",
                H2_NUM
            ))

            flow.append(Paragraph(
                f"{sec.h3_num()} Temperature Rise Curve",
                H3_NUM
            ))
            try:
                from PIL import Image as PILImage
                with PILImage.open(img_path) as img:
                    img_w, img_h = img.size
                    dpi = int((img.info.get("dpi", (144,144))[0]) or 144)
            except Exception:
                img_w, img_h, dpi = 1200, 800, 144
            target_w_pt, target_h_pt = _scale_to_fit(img_w, img_h, 180, 240, dpi)
            flow.append(Spacer(1, 4))
            flow.append(RLImage(str(img_path), width=target_w_pt, height=target_h_pt))



            # Keep the results and their application text together if the equipment schedule is long.
            results_start = len(flow)
            comp_mid = "Compliant" if th.compliant_mid else "Not compliant"
            comp_top = "Compliant" if th.compliant_top else "Not compliant"

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

            for row in main_rows:
                if row[0].startswith("Compliance @"):
                    colour = green if row[1] == "Compliant" else colors.red
                    row[1] = Paragraph(row[1], ParagraphStyle(
                        "TemperatureStatus", fontName=FONT_B, fontSize=8, leading=10, textColor=colour))
            tbl = _report_table([["Parameter", "Value"], *main_rows], [100 * mm, 75 * mm])


            flow.append(Paragraph(
                f"{sec.h3_num()} Temperature Rise Results",
                H3_NUM
            ))
            flow.append(Spacer(1, 4))

            flow.append(tbl)

            _application_results(flow, [th])
            flow[results_start:] = [KeepTogether(flow[results_start:])]

            # ---------------------------------------------------------
            # PAGE 2 — IEC 60890 CALCULATION SHEET (PER TIER)
            # ---------------------------------------------------------
            flow.append(PageBreak())

            flow.append(Paragraph(
                f"{sec.h2_num()} IEC 60890 Calculation Sheet — {th.tag}",
                H2_NUM
            ))
            flow.append(Spacer(1, 6))

            flow.append(Paragraph(
                "The following tables summarise the IEC 60890 enclosure thermal calculation "
                "including effective cooling surfaces, enclosure constants, and heat dissipation balance.",
                BodySmall
            ))
            flow.append(Spacer(1, 8))

            # -------------------------
            # Cooling surfaces
            # -------------------------
            flow.append(Paragraph(
                f"{sec.h3_num()} Effective cooling surfaces",
                H3_NUM
            ))
            flow.append(iec60890_tab_sheet(th))
            flow.append(Spacer(1, 8))

            # -------------------------
            # IEC variables
            # -------------------------
            flow.append(Paragraph(
                f"{sec.h3_num()} IEC 60890 calculation parameters",
                H3_NUM
            ))

            flow.append(iec_scalar_table(th))
            flow.append(Spacer(1, 8))

            # -------------------------
            # Dissipation
            # -------------------------
            flow.append(Paragraph(
                f"{sec.h3_num()} Enclosure heat dissipation and ventilation",
                H3_NUM
            ))
            flow.append(enclosure_dissipation_table(th))
            flow.append(Spacer(1, 10))

            # ---------------------------------------------------------
            # PAGE 3 — AS/NZS 61439 WORKING TEMPERATURE ASSESSMENT
            # ---------------------------------------------------------
            comp = comp_by_tier.get(str(th.tag))

            derating_results = _component_derating_results(tier, th) if tier else []
            has_bus_elements = _has_bus_elements(tier, comp)
            if has_bus_elements or derating_results:
                if has_bus_elements:
                    flow.append(PageBreak())
                heading = ("AS/NZS 61439 Working Temperature Assessment" if has_bus_elements
                           else "Component Current Capacity Assessment")
                flow.append(Paragraph(f"{sec.h2_num()} {heading} - {escape(th.tag)}", H2_NUM))
                flow.append(Spacer(1, 6))
                render_working_temperature_page(flow, sec, tier, th, comp,
                                                derating_results=derating_results)


    doc.multiBuild(flow)
    return out_pdf


