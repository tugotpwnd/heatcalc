# heatcalc/core/curvefit.py
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Literal

from heatcalc.core.iec60890_geometry import touching_sides, b_map_for_tier, effective_area_and_fg

# IEC-defined curve families
FIG5_AE_CURVES = [1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14]
FIG6_F_CURVES  = [1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10]


# ---------------------------------------------------------------------
# Utility: strict clamping to IEC defined curves
# ---------------------------------------------------------------------

def snap_to_nearest(value: float, allowed: list[float]) -> float:
    return min(allowed, key=lambda v: abs(v - value))


# ---------------------------------------------------------------------
# Utility: strict clamping to IEC boundary conditions
# ---------------------------------------------------------------------
def clamp(x: float, xmin: float, xmax: float) -> float:
    return max(xmin, min(xmax, x))


# ---------------------------------------------------------------------
# FIGURE 3
# Enclosure constant k
# No ventilation, Ae > 1.25 m²
# ---------------------------------------------------------------------
def k_fig3(Ae: float) -> float:
    Ae = clamp(Ae, 1.25, 12.0)
    return 0.58 * (Ae ** -0.795)

# ---------------------------------------------------------------------
# FIGURE 4
# Temperature distribution factor c
# No ventilation, Ae > 1.25 m²
# ---------------------------------------------------------------------
def c_fig4(curve_no: int, f: float) -> float:
    f = clamp(f, 0.3, 16.0)

    base = -0.0017 * f**2 + 0.055 * f
    offsets = {
        1: 1.182,
        2: 1.164,
        3: 1.146,
        4: 1.125,
        5: 1.087,
    }

    return base + offsets.get(int(curve_no), offsets[3])


# ---------------------------------------------------------------------
# FIGURE 5
# Enclosure constant k
# With ventilation openings, Ae > 1.25 m²
# ---------------------------------------------------------------------
def k_fig5(Ae: float, S_air_cm2: float) -> tuple[float, float]:
    S_air_cm2 = clamp(S_air_cm2, 10.0, 1000.0)

    Ae_snap = snap_to_nearest(Ae, FIG5_AE_CURVES)

    Ak = 2.83e-2 * math.log(Ae_snap) - 10.39e-2
    Bk = 19.52e-2 * math.log(Ae_snap) - 76.56e-2

    k = Ak * math.log(S_air_cm2) - Bk
    return k, Ae_snap

# ---------------------------------------------------------------------
# FIGURE 6
# Temperature distribution factor c
# With ventilation openings, Ae > 1.25 m²
# ---------------------------------------------------------------------
def c_fig6(f: float, S_air_cm2: float) -> tuple[float, float]:
    S_air_cm2 = clamp(S_air_cm2, 10.0, 1000.0)

    f_snap = snap_to_nearest(f, FIG6_F_CURVES)

    Ac = 7.6 * f_snap + 69.0
    Bc = 5.1e-4 * f_snap**2 - 1.35e-2 * f_snap + 0.14931

    c = 0.01 * Ac * (S_air_cm2 ** Bc)
    return c, f_snap

# ---------------------------------------------------------------------
# FIGURE 7
# Enclosure constant k
# No ventilation, Ae ≤ 1.25 m²
# ---------------------------------------------------------------------
def k_fig7(Ae: float) -> float:
    Ae = clamp(Ae, 0.01, 1.25)

    if Ae < 0.08:
        return 4.0
    return 0.626 * (Ae ** -0.737)


# ---------------------------------------------------------------------
# FIGURE 8
# Temperature distribution factor c
# No ventilation, Ae ≤ 1.25 m²
# ---------------------------------------------------------------------
def c_fig8(g: float) -> float:
    g = clamp(g, 0.0, 3.0)

    if g > 0.8147:
        return (
            0.324055 * (1 - math.exp(-1.8827 * g + 0.38579))
            + 0.93643
        )

    return 0.19354 * g + 1.0


# ---------------------------------------------------------------------
# Backward-compatible public API
# ---------------------------------------------------------------------
def k_no_vents(Ae: float) -> float:
    return k_fig3(Ae)


def c_no_vents(curve_no: int, f: float) -> float:
    return c_fig4(curve_no, f)


def k_small_no_vents(Ae: float) -> float:
    return k_fig7(Ae)


def c_small_no_vents(g: float) -> float:
    return c_fig8(g)


def k_vents(*, ae: float, opening_area_cm2: float) -> CurveResult:
    k, ae_snap = k_fig5(ae, opening_area_cm2)

    snapped = not math.isclose(ae, ae_snap, rel_tol=1e-6)

    return CurveResult(
        value=k,
        snapped=snapped,
        snapped_to=ae_snap,
        meta={
            "figure": "Fig. 5",
            "input_ae": ae,
            "used_ae": ae_snap,
            "opening_area_cm2": opening_area_cm2,
        },
    )


def c_vents(*, f: float, opening_area_cm2: float) -> CurveResult:
    c, f_snap = c_fig6(f, opening_area_cm2)

    snapped = not math.isclose(f, f_snap, rel_tol=1e-6)

    return CurveResult(
        value=c,
        snapped=snapped,
        snapped_to=f_snap,
        meta={
            "figure": "Fig. 6",
            "input_f": f,
            "used_f": f_snap,
            "opening_area_cm2": opening_area_cm2,
        },
    )


# ---------------------------------------------------------------------
# Optional: explicit reporting / debugging structure
# ---------------------------------------------------------------------
@dataclass
class CurvePoint:
    figure: Literal["Fig3", "Fig4", "Fig5", "Fig6", "Fig7", "Fig8"]
    x: float
    y: float
    snapped_param: float | None = None  # Ae for Fig5, f for Fig6

from typing import Any, Dict

@dataclass
class CurveResult:
    value: float
    snapped: bool
    snapped_to: float | None
    meta: Dict[str, Any]


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
        k, ae_used = k_fig5(Ae, inlet_area_cm2)
        c, f_used = c_fig6(f, inlet_area_cm2)

        result["k"] = CurvePoint(
            figure="Fig5",
            x=inlet_area_cm2,
            y=k,
            snapped_param=ae_used,
        )

        result["c"] = CurvePoint(
            figure="Fig6",
            x=inlet_area_cm2,
            y=c,
            snapped_param=f_used,
        )


    else:
        if Ae <= 1.25:
            k = k_fig7(Ae)
            c = c_fig8(g)
            result["k"] = CurvePoint("Fig7", Ae, k)
            result["c"] = CurvePoint("Fig8", g, c)

        else:
            k = k_fig3(Ae)
            c = c_fig4(curve_no, f)
            result["k"] = CurvePoint("Fig3", Ae, k)
            result["c"] = CurvePoint("Fig4", f, c)

    return result
