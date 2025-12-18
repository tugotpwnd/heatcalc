from typing import Dict, List
from . import curvefit
from ..ui.tier_item import TierItem
from ..ui.designer_view import GRID

MM_PER_GRID = 25

def _overlap_x(a: TierItem, b: TierItem) -> bool:
    ra, rb = a.shapeRect(), b.shapeRect()
    return not (ra.right() <= rb.left() or rb.right() <= ra.left())


def _overlap_y(a: TierItem, b: TierItem) -> bool:
    ra, rb = a.shapeRect(), b.shapeRect()
    return not (ra.bottom() <= rb.top() or rb.bottom() <= ra.top())


def compute_face_bs(
    t: TierItem,
    tiers: List[TierItem],
    wall: bool
) -> Dict[str, float]:
    left_touch  = any(abs(t.shapeRect().left()  - o.shapeRect().right()) < 1e-3 and _overlap_y(t, o) for o in tiers if o is not t)
    right_touch = any(abs(t.shapeRect().right() - o.shapeRect().left())  < 1e-3 and _overlap_y(t, o) for o in tiers if o is not t)
    top_covered = any(abs(t.shapeRect().top()   - o.shapeRect().bottom()) < 1e-3 and _overlap_x(t, o) for o in tiers if o is not t)

    b_left  = 0.5 if left_touch  else 0.9
    b_right = 0.5 if right_touch else 0.9
    b_top   = 0.7 if (wall and top_covered) else 1.4

    return {"left": b_left, "right": b_right, "top": b_top}


def calc_tier_iec60890(
    *,
    tier: TierItem,
    tiers: List[TierItem],
    wall_mounted: bool,
    inlet_area_cm2: float,
    ambient_C: float,
) -> Dict:
    # Geometry
    w_m = (tier._rect.width()  / GRID) * (MM_PER_GRID / 1000.0)
    h_m = (tier._rect.height() / GRID) * (MM_PER_GRID / 1000.0)
    d_m = max(0.001, (tier.depth_mm or 400) / 1000.0)

    # Effective cooling surface
    b_faces = compute_face_bs(tier, tiers, wall_mounted)
    areas = {
        "top":   w_m * d_m,
        "bot":   w_m * d_m,
        "left":  h_m * d_m,
        "right": h_m * d_m,
        "front": w_m * h_m,
        "back":  w_m * h_m,
    }
    b_used = {
        "top":   b_faces["top"],
        "bot":   0.0,
        "left":  b_faces["left"],
        "right": b_faces["right"],
        "front": 0.9,
        "back":  0.5 if wall_mounted else 0.9,
    }
    Ae = sum(areas[k] * b_used[k] for k in areas)

    # Power and mode
    P = max(0.0, tier.total_heat())
    vent = tier.is_ventilated
    curve_no = tier.curve_no
    x = 0.715 if vent else 0.804
    d_fac = 1.0

    if vent:
        Ab = max(1e-9, w_m * d_m)
        f = (h_m ** 1.35) / Ab
        k = curvefit.k_vents(ae=max(1.0, min(14.0, Ae)), opening_area_cm2=inlet_area_cm2)
        c = curvefit.c_vents(f=f, opening_area_cm2=inlet_area_cm2)
        g = None
    else:
        if Ae <= 1.25:
            k = curvefit.k_small_no_vents(Ae)
            g = h_m / max(1e-9, w_m)
            c = curvefit.c_small_no_vents(g)
            f = None
        else:
            k = curvefit.k_no_vents(Ae)
            Ab = max(1e-9, w_m * d_m)
            f = (h_m ** 1.35) / Ab
            c = curvefit.c_no_vents(curve_no, f)
            g = None

    dt_mid = k * d_fac * (P ** x)

    # "Raw" top rise per the usual relationship
    dt_top_raw = c * dt_mid

    # IEC 60890 Fig.2 adjustment for small enclosures:
    # Δt0.75 is midpoint between Δt0.5 and Δt1.0(raw),
    # and Δt1.0(effective) is vertically above Δt0.75 => same x-value.
    if (not vent) and (Ae <= 1.25):
        dt_075 = 0.5 * (dt_mid + dt_top_raw)
        dt_top = dt_075  # effective top (plotted + reported at 1.0 height)
    else:
        dt_075 = None
        dt_top = dt_top_raw

    T_mid = ambient_C + dt_mid
    T_top = ambient_C + dt_top
    T_075 = (ambient_C + dt_075) if dt_075 is not None else None

    limit_C = tier.effective_max_temp_C()

    return {
        "ambient_C": ambient_C,
        "Ae": Ae,
        "P": P,
        "k": k,
        "c": c,
        "x": x,
        "f": f,
        "g": g,

        # rises (Δt)
        "dt_mid": dt_mid,            # Δt at 0.5
        "dt_top": dt_top,            # Δt at 1.0 (effective)
        "dt_075": dt_075,            # Δt at 0.75 (only small enclosures)
        "dt_top_raw": dt_top_raw,    # raw c*dt_mid (for trace/debug)

        # absolute temps
        "T_mid": T_mid,
        "T_top": T_top,
        "T_075": T_075,

        "limit_C": limit_C,
        "compliant_mid": T_mid <= limit_C,
        "compliant_top": T_top <= limit_C,
    }


def temperature_profile(delta_t_mid: float, delta_t_top: float, Ae: float) -> dict:
    """
    IEC 60890 temperature-rise profile construction.

    Returns:
        {
            "dt_0_5": Δt at mid-height (0.5),
            "dt_0_75": Δt at 0.75 height (ONLY if Ae <= 1.25),
            "dt_1_0": Δt at top (1.0)
        }
    """
    profile = {
        "dt_0_5": float(delta_t_mid),
        "dt_1_0": float(delta_t_top),
    }

    if Ae <= 1.25:
        # IEC 60890 Fig. 2 geometric construction
        profile["dt_0_75"] = 0.5 * (delta_t_mid + delta_t_top)

    return profile
