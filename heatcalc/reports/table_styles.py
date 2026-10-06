"""Shared Maxwell report table styling."""
from reportlab.lib import colors
from reportlab.platypus import TableStyle


def standard_table_style():
    return TableStyle([
        ("FONT", (0, 0), (-1, 0), "Arial-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Arial", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#007F4D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.45, colors.black),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F5F7")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ])
