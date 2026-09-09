"""A single-page Maxwell fan-sizing sheet, without a cover or contents page."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib import font_manager, rc_context
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A3, A2, A1, A0
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from ..utils.resources import get_resource_path
from .fan_sizing_plot import draw_fan_sizing_figure, BLUE, GREEN
from .table_styles import standard_table_style


def export_fan_sizing_report(path, data, meta, fan_name=""):
    """Write exactly one page, enlarging the sheet for a large device schedule."""
    for name, filename in (("Arial", "arial.ttf"), ("Arial-Bold", "arialbd.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, get_resource_path(f"heatcalc/assets/fonts/{filename}")))
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold")
    blue, green = colors.HexColor(BLUE), colors.HexColor(GREEN)
    body = ParagraphStyle("FanBody", fontName="Arial", fontSize=8, leading=11)
    cell = ParagraphStyle("FanCell", parent=body, fontSize=9, leading=11, alignment=1)
    header_cell = ParagraphStyle("FanHeaderCell", parent=cell,
                                fontName="Arial-Bold", textColor=colors.white)
    heading = ParagraphStyle("FanHeading", parent=body, fontName="Arial-Bold",
                             fontSize=16, leading=20, textColor=blue)
    subhead = ParagraphStyle("FanSubhead", parent=body, fontName="Arial-Bold",
                             fontSize=10, leading=13, textColor=green)
    r = data.result

    def p(text, style=body):
        return Paragraph(text, style)

    def blocks(width):
        title = p(f"Fan sizing | {escape(data.tier_name)}", heading)
        details = p(f"<b>Fan + filter:</b> {escape(fan_name or 'Unspecified model')}", body)
        minimum = (f"{r.get('airflow_m3h', 0):.1f} m³/h" if r.get("cooling_possible", True)
                   else "Not achievable")
        cells = [
            ("DELIVERED AIRFLOW", f"{data.airflow_m3h:.1f} m³/h"),
            ("MINIMUM REQUIRED", minimum),
            ("TOP AIR", f"{r['T_top']:.1f} °C"), ("MID AIR", f"{r['T_mid']:.1f} °C"),
            ("HEAT LOAD", f"{r['P']:.1f} W"), ("AMBIENT / SOLAR", f"{r['ambient_C']:.1f} °C / +{r.get('solar_dt', 0):.1f} K"),
            ("TIER LIMIT", f"{r['limit_C']:.1f} °C"),
            ("TEMPERATURE MARGIN", f"{r['limit_C'] - r['T_top']:+.1f} °C"),
        ]
        metrics = [p(f'<font color="{BLUE}" size="6.5">{label}</font><br/>'
                     f'<font color="{GREEN}" size="10">{value}</font>') for label, value in cells]
        summary = Table([metrics[:4], metrics[4:]], colWidths=[width / 4] * 4)
        summary.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        schedule = [[p(name, header_cell) for name in
                     ("ID", "Device", "Qty", "Rated<br/>(A)", "Output<br/>(A/device)",
                      "Delivered<br/>(m³/h)", "Device limit<br/>(°C)")]]
        for device in data.devices:
            exceeded = r["T_top"] > device.max_temp_C
            limit = f"{device.max_temp_C:.1f}" + ("<br/><font color='#bc4242'>EXCEEDED</font>" if exceeded else "")
            schedule.append([p(value, cell) for value in (
                escape(device.label), escape(device.name), str(device.quantity),
                f"{device.rated_A:.1f}", f"{device.current_A:.1f}",
                f"{data.airflow_m3h:.1f}", limit,
            )])
        table = Table(schedule, colWidths=[12*mm, width-106*mm, 9*mm, 18*mm, 20*mm, 24*mm, 23*mm])
        table.setStyle(standard_table_style())
        return title, details, summary, table

    # Keep names and all device values legible on one sheet; use a larger ISO
    # sheet for unusually large schedules instead of clipping or dropping rows.
    chart_height = max(100*mm, (20 + 15 * len(data.devices))*mm)
    for page_size in (A4, A3, A2, A1, A0):
        width, height = page_size
        available = width - 24*mm
        title, details, summary, table = blocks(available)
        elements = (title, details, summary, table)
        measured = [element.wrap(available, height)[1] for element in elements]
        needed = sum(measured) + chart_height + 68*mm
        if needed <= height:
            break
    else:
        page_size = (width, needed)
        height = needed

    plot_buffer = BytesIO()
    # Use the same bundled Arial as the PDF, independent of the main report's
    # Matplotlib settings or which report the user exported first.
    font_manager.fontManager.addfont(str(get_resource_path("heatcalc/assets/fonts/arial.ttf")))
    with rc_context({"font.family": "Arial", "font.weight": "normal", "axes.labelweight": "normal"}):
        figure = Figure(figsize=(available / 72, chart_height / 72), dpi=200)
        FigureCanvasAgg(figure)
        draw_fan_sizing_figure(figure, data)
        figure.savefig(plot_buffer, format="png", dpi=200, facecolor="white")
    plot_buffer.seek(0)
    # Build in memory so a generation failure never truncates an existing PDF.
    pdf_buffer = BytesIO()
    canvas = Canvas(pdf_buffer, pagesize=page_size)
    canvas.setTitle(f"Fan sizing - {data.tier_name}")
    canvas.setAuthor(str(meta.designer_name or "Maxwell Industries"))
    project = p(f'<font color="{BLUE}"><b>Project:</b></font> '
                f'<font color="{GREEN}">{escape(str(meta.project_title or "-"))}</font>')
    _, project_h = project.wrap(available - 60*mm, 30*mm)
    project.drawOn(canvas, 12*mm, height - 13*mm - project_h)
    canvas.setFont("Arial", 8)
    canvas.setFillColor(blue)
    canvas.drawRightString(width - 12*mm, height - 17*mm, "FAN SIZING / OPERATING POINT")
    y = height - 28*mm

    def draw(element, gap=3*mm):
        nonlocal y
        _, h = element.wrap(available, height)
        element.drawOn(canvas, 12*mm, y-h)
        y -= h + gap

    draw(title)
    draw(details)
    draw(summary, 2*mm)
    canvas.drawImage(ImageReader(plot_buffer), 12*mm, y-chart_height,
                     width=available, height=chart_height)
    y -= chart_height + 3*mm
    draw(p("Device operating points", subhead), 2*mm)
    if data.devices:
        draw(table)
    else:
        draw(p("No devices with valid derating curves in this tier."))
    logo = get_resource_path("heatcalc/data/company_logo.png")
    if logo.exists():
        canvas.drawImage(str(logo), 12*mm, 8*mm, width=45*mm, height=13*mm,
                         preserveAspectRatio=True, mask="auto")
    canvas.setFont("Arial-Bold", 9)
    canvas.setFillColor(blue)
    canvas.drawCentredString(width/2, 12*mm, "Temperature Rise Calculation")
    canvas.setFont("Arial", 8)
    canvas.setFillColor(green)
    canvas.drawRightString(width-12*mm, 10*mm, "Page 1 of 1")
    canvas.showPage()
    canvas.save()
    path = Path(path)
    path.write_bytes(pdf_buffer.getvalue())
    return path
