"""Curve-fit helpers for IEC 60890 charts (placeholders).

Later we will implement polynomial or spline fits from digitized curves.
Provide a stable API now so UI can call into it.
"""
from typing import Tuple


def fit_factor_from_curve(curve_name: str, x: float) -> float:
    # TODO: real fitting; for now simple placeholder relationships
    if curve_name == "b":
        return max(0.5, min(1.2, 0.8 + 0.001 * x))
    if curve_name == "k":
        return max(0.9, min(1.5, 1.0 + 0.0005 * x))
    return 1.0


def estimate_surface_area(width_mm: float, height_mm: float, depth_mm: float) -> float:
    # Basic rectangular box area (m^2)
    w = width_mm / 1000.0
    h = height_mm / 1000.0
    d = depth_mm / 1000.0
    return 2.0 * (w * h + w * d + h * d)
