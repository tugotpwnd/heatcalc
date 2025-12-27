# heatcalc/core/curvefit.py
from __future__ import annotations

import math
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple, Literal

# ---------------------------------------------------------------------
# Geometry helpers (authoritative)
# ---------------------------------------------------------------------
from heatcalc.core.iec60890_geometry import (
    touching_sides,
    b_map_for_tier,
    dimensions_m,
    effective_area_and_fg,
)

# ---------------------------------------------------------------------
# CSV-backed curve loading
# ---------------------------------------------------------------------
from heatcalc.core.curve_loader import load_curve_folder

# NOTE:
# This module lives in heatcalc/core, so parents[1] == heatcalc/
# and heatcalc/data is siblings with core/.
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

FIG5_CURVES = load_curve_folder(DATA_DIR / "figure_5")  # key = Ae (m²) curve family
FIG6_CURVES = load_curve_folder(DATA_DIR / "figure_6")  # key = f curve family

# ---------------------------------------------------------------------
# Generic interpolation (IEC style)
# ---------------------------------------------------------------------
def interp_1d(x: float, pts: List[Tuple[float, float]]) -> float:
    if not pts:
        return 0.0
    pts = sorted(pts)

    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]

    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            # guard dx=0
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    return pts[-1][1]


def interp_between_curves(
    curves: Dict[float, List[Tuple[float, float]]],
    family_key: float,
    x: float,
) -> float:
    """
    Interpolates:
      - within a single curve (x-axis interpolation)
      - and between two neighbouring family curves (family_key interpolation)
    """
    if not curves:
        return 0.0

    keys = sorted(curves)

    if family_key <= keys[0]:
        return interp_1d(x, curves[keys[0]])
    if family_key >= keys[-1]:
        return interp_1d(x, curves[keys[-1]])

    for k0, k1 in zip(keys, keys[1:]):
        if k0 <= family_key <= k1:
            y0 = interp_1d(x, curves[k0])
            y1 = interp_1d(x, curves[k1])
            if k1 == k0:
                return y1
            return y0 + (y1 - y0) * (family_key - k0) / (k1 - k0)

    return interp_1d(x, curves[keys[-1]])

# ---------------------------------------------------------------------
# Monotone spline for scanned IEC curves
# ---------------------------------------------------------------------
@dataclass
class _SplineSeg:
    x0: float
    x1: float
    y0: float
    y1: float
    m0: float
    m1: float


class _MonotoneSpline:
    """Fritsch–Carlson monotone cubic Hermite spline."""

    def __init__(self, pts: List[Tuple[float, float]]):
        pts = sorted(pts)
        self._segs: List[_SplineSeg] = []

        if len(pts) < 2:
            return

        xs, ys = zip(*pts)
        n = len(xs)

        dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
        dy = [ys[i + 1] - ys[i] for i in range(n - 1)]
        m = [dy[i] / dx[i] if dx[i] else 0.0 for i in range(n - 1)]

        d = [0.0] * n
        d[0] = m[0]
        d[-1] = m[-1]

        for i in range(1, n - 1):
            if m[i - 1] * m[i] <= 0:
                d[i] = 0.0
            else:
                w1 = 2 * dx[i] + dx[i - 1]
                w2 = dx[i] + 2 * dx[i - 1]
                d[i] = (w1 + w2) / (w1 / m[i - 1] + w2 / m[i])

        for i in range(n - 1):
            self._segs.append(
                _SplineSeg(xs[i], xs[i + 1], ys[i], ys[i + 1], d[i], d[i + 1])
            )

    def __call__(self, x: float) -> float:
        if not self._segs:
            return 0.0

        if x <= self._segs[0].x0:
            return self._segs[0].y0
        if x >= self._segs[-1].x1:
            return self._segs[-1].y1

        for s in self._segs:
            if s.x0 <= x <= s.x1:
                t = (x - s.x0) / (s.x1 - s.x0)
                h00 = 2 * t**3 - 3 * t**2 + 1
                h10 = t**3 - 2 * t**2 + t
                h01 = -2 * t**3 + 3 * t**2
                h11 = t**3 - t**2
                return (
                    h00 * s.y0
                    + h10 * (s.x1 - s.x0) * s.m0
                    + h01 * s.y1
                    + h11 * (s.x1 - s.x0) * s.m1
                )

        return self._segs[-1].y1

# ---------------------------------------------------------------------
# IEC figure implementations (viewer-friendly names)
# ---------------------------------------------------------------------

# ---- Fig.3 (k vs Ae, no ventilation, Ae > 1.25) ----
_FIG3_SPL = _MonotoneSpline(
    [
        (0.5, 0.98),
        (1.0, 0.60),
        (2.0, 0.35),
        (3.0, 0.25),
        (4.0, 0.18),
        (5.0, 0.16),
        (6.0, 0.14),
        (6.64, 0.135),
        (7.0, 0.13),
        (8.0, 0.12),
        (9.0, 0.11),
        (10.0, 0.10),
        (11.0, 0.09),
        (12.0, 0.08),
    ]
)

def k_fig3(ae_m2: float) -> float:
    return max(0.06, min(1.0, _FIG3_SPL(ae_m2)))

# ---- Fig.4 (c vs f, no ventilation) ----
_FIG4_BASE = _MonotoneSpline(
    [
        (1.0, 1.23),
        (2.0, 1.275),
        (3.0, 1.325),
        (4.0, 1.375),
        (5.0, 1.415),
        (6.0, 1.45),
        (7.0, 1.48),
        (11.0, 1.575),
    ]
)
_FIG4_OFFSETS = {1: 0.0, 2: -0.015, 3: -0.03, 4: -0.055, 5: -0.10}

def c_fig4(curve_no: int, f: float) -> float:
    return _FIG4_BASE(f) + _FIG4_OFFSETS.get(int(curve_no), 0.0)

# ---- Fig.5 (k vs inlet area, ventilated) ----
def k_fig5(ae_m2: float, inlet_area_cm2: float) -> float:
    return interp_between_curves(FIG5_CURVES, float(ae_m2), float(inlet_area_cm2))

# ---- Fig.6 (c vs inlet area, ventilated) ----
def c_fig6(f: float, inlet_area_cm2: float) -> float:
    return interp_between_curves(FIG6_CURVES, float(f), float(inlet_area_cm2))

# ---- Fig.7 (k vs Ae, no ventilation, Ae ≤ 1.25) ----
_A1, _K1 = 0.1, 3.5
_A2, _K2 = 1.0, 0.63
_B = (math.log(_K2) - math.log(_K1)) / (math.log(_A2) - math.log(_A1))
_C = _K1 / (_A1**_B)

def k_fig7(ae_m2: float) -> float:
    ae = max(0.05, min(1.30, float(ae_m2)))
    return _C * (ae**_B)

# ---- Fig.8 (c vs g, no ventilation, Ae ≤ 1.25) ----
_FIG8_SPL = _MonotoneSpline(
    [
        (0.0, 1.00),
        (0.5, 1.10),
        (1.0, 1.19),
        (1.5, 1.23),
        (2.0, 1.25),
        (2.5, 1.255),
    ]
)

def c_fig8(g: float) -> float:
    return _FIG8_SPL(float(g))

# ---------------------------------------------------------------------
# Backward-compatible API for existing calc code
# (THIS is what fixes the live thermal overlay)
# ---------------------------------------------------------------------

def k_no_vents(ae_m2: float) -> float:
    """IEC Fig.3 (Ae>1.25, no ventilation)."""
    return k_fig3(ae_m2)

def c_no_vents(curve_no: int, f: float) -> float:
    """IEC Fig.4 (no ventilation)."""
    return c_fig4(curve_no, f)

def k_small_no_vents(ae_m2: float) -> float:
    """IEC Fig.7 (Ae<=1.25, no ventilation)."""
    return k_fig7(ae_m2)

def c_small_no_vents(g: float) -> float:
    """IEC Fig.8 (Ae<=1.25, no ventilation)."""
    return c_fig8(g)

def k_vents(*, ae: float, opening_area_cm2: float) -> float:
    """IEC Fig.5 (ventilated)."""
    return k_fig5(ae, opening_area_cm2)

def c_vents(*, f: float, opening_area_cm2: float) -> float:
    """IEC Fig.6 (ventilated)."""
    return c_fig6(f, opening_area_cm2)

# ---------------------------------------------------------------------
# Tier evaluation API (used by the curve viewer)
# ---------------------------------------------------------------------
@dataclass
class CurvePoint:
    figure: Literal["Fig3", "Fig4", "Fig5", "Fig6", "Fig7", "Fig8"]
    x: float
    y: float
    value: float


def evaluate_tier(
    tier,
    all_tiers,
    inlet_area_cm2: float = 300.0,
) -> Dict[str, CurvePoint]:
    """
    Returns explicit curve usage for a tier.
    Viewer-friendly, report-friendly, debuggable.

    NOTE: Ventilation is only "effective" for Ae > 1.25.
    """
    touch = touching_sides(tier, all_tiers)
    bmap = b_map_for_tier(tier, touch)
    Ae, f, g = effective_area_and_fg(tier, bmap)

    vent_requested = bool(getattr(tier, "is_ventilated", False))
    curve_no = int(getattr(tier, "curve_no", 3))

    result: Dict[str, CurvePoint] = {}

    vent_effective = vent_requested and (Ae > 1.25)

    if vent_effective:
        k = k_fig5(Ae, inlet_area_cm2)
        c = c_fig6(f, inlet_area_cm2)
        result["k"] = CurvePoint("Fig5", inlet_area_cm2, Ae, k)
        result["c"] = CurvePoint("Fig6", inlet_area_cm2, f, c)
    else:
        if Ae <= 1.25:
            k = k_fig7(Ae)
            c = c_fig8(g)
            result["k"] = CurvePoint("Fig7", Ae, k, k)
            result["c"] = CurvePoint("Fig8", g, c, c)
        else:
            k = k_fig3(Ae)
            c = c_fig4(curve_no, f)
            result["k"] = CurvePoint("Fig3", Ae, k, k)
            result["c"] = CurvePoint("Fig4", f, c, c)

    return result
