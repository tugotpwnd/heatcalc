# heatcalc/core/iec60890_calc.py
from __future__ import annotations

from typing import Dict, List

from . import curvefit
from ..ui.tier_item import TierItem
from ..ui.designer_view import GRID

from .iec60890_geometry import (
    touching_sides,
    b_map_for_tier,
    dimensions_m,
    effective_area_and_fg, tier_geometry,
)

MM_PER_GRID = 25


def calc_tier_iec60890(
    *,
    tier: TierItem,
    tiers: List[TierItem],
    wall_mounted: bool,
    inlet_area_cm2: float,
    ambient_C: float,
) -> Dict:
    # ---------------- Geometry (dimensions) ----------------
    from .iec60890_geometry import dimensions_m

    w_m, h_m, d_m = dimensions_m(tier)

    geom = tier_geometry(tier, tiers)

    Ae = geom["Ae"]
    f = geom["f"]
    g = geom["g"]

    # ---------------- Per-surface breakdown (for report) ----------------
    surfaces = []

    def _add_surface(name: str, w: float, h: float, b: float):
        A0 = float(w) * float(h)
        surfaces.append(
            {
                "name": name,
                "w": float(w),
                "h": float(h),
                "A0": A0,
                "b": float(b),
                "Ae": A0 * float(b),
            }
        )

    from .iec60890_geometry import resolved_surfaces

    for name, a, b, bf in resolved_surfaces(tier, tiers):
        _add_surface(name, a, b, bf)

    # ---------------- Power and mode ----------------
    P = max(0.0, float(tier.total_heat()))
    vent = bool(tier.is_ventilated)
    curve_no = int(getattr(tier, "curve_no", 1))

    x = 0.715 if vent else 0.804
    d_fac = 1.0

    figures_used: list[str] = []

    if vent:
        Ab = max(1e-9, w_m * d_m)
        f = (h_m ** 1.35) / Ab
        k = curvefit.k_vents(ae=max(1.0, min(14.0, Ae)), opening_area_cm2=inlet_area_cm2)
        c = curvefit.c_vents(f=f, opening_area_cm2=inlet_area_cm2)
        figures_used += ["Fig. 5", "Fig. 6"]
        g = None
    else:
        if Ae <= 1.25:
            k = curvefit.k_small_no_vents(Ae)
            g = h_m / max(1e-9, w_m)
            c = curvefit.c_small_no_vents(g)
            figures_used += ["Fig. 7", "Fig. 8"]
            f = None
        else:
            k = curvefit.k_no_vents(Ae)
            Ab = max(1e-9, w_m * d_m)
            f = (h_m ** 1.35) / Ab
            c = curvefit.c_no_vents(curve_no, f)
            figures_used += ["Fig. 3", "Fig. 4"]
            g = None

    dt_mid = k * d_fac * (P ** x)
    dt_top_raw = c * dt_mid

    if (not vent) and (Ae <= 1.25):
        dt_075 = 0.5 * (dt_mid + dt_top_raw)
        dt_top = dt_075
        figures_used.append("Fig. 2")
    else:
        dt_075 = None
        dt_top = dt_top_raw
        figures_used.append("Fig. 1")

    T_mid = ambient_C + dt_mid
    T_top = ambient_C + dt_top
    T_075 = (ambient_C + dt_075) if dt_075 is not None else None

    limit_C = float(tier.effective_max_temp_C())

    return {
        "ambient_C": ambient_C,

        # geometry
        "w_m": w_m,
        "h_m": h_m,
        "d_m": d_m,
        "Ae": Ae,

        # thermal model
        "P": P,
        "k": k,
        "c": c,
        "x": x,
        "f": f,
        "g": g,
        "curve_no": curve_no,
        "wall_mounted": bool(wall_mounted),
        "ventilated": bool(vent),

        # rises (Δt)
        "dt_mid": dt_mid,
        "dt_top": dt_top,
        "dt_075": dt_075,
        "dt_top_raw": dt_top_raw,

        # absolute temps
        "T_mid": T_mid,
        "T_top": T_top,
        "T_075": T_075,

        "limit_C": limit_C,
        "compliant_mid": T_mid <= limit_C,
        "compliant_top": T_top <= limit_C,

        # reporting
        "surfaces": surfaces,
        "figures_used": sorted(set(figures_used)),
    }
