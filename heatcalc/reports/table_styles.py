"""Shared Maxwell report table styling."""
from reportlab.lib import colors
from reportlab.platypus import TableStyle


def standard_table_style():
    return TableStyle([
        ("FONT", (0, 0), (-1, 0), "Arial-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Arial", 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#007F4D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ])
