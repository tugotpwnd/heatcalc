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
AIR_RHO_KG_M3 = 1.2
AIR_CP_J_KG_K = 1005.0

def air_k_factor_from_altitude_m(alt_m: float) -> float:
    """
    IEC TR 60890:2022 Annex K, Table K.1
    Altitude derating factor for air volumetric heat capacity.
    Linear interpolation between tabulated points.
    """
    table = [
        (0,    1.00),
        (500,  0.95),
        (1000, 0.89),
        (1500, 0.84),
        (2000, 0.80),
        (2500, 0.75),
        (3000, 0.71),
    ]

    if alt_m is None:
        return 1.0

    alt_m = max(0.0, float(alt_m))

    if alt_m <= table[0][0]:
        return table[0][1]
    if alt_m >= table[-1][0]:
        return table[-1][1]

    for (a0, k0), (a1, k1) in zip(table[:-1], table[1:]):
        if a0 <= alt_m <= a1:
            t = (alt_m - a0) / (a1 - a0)
            return k0 + t * (k1 - k0)

    return 1.0


def calc_tier_iec60890(
    *,
    tier: TierItem,
    tiers: List[TierItem],
    wall_mounted: bool,
    inlet_area_cm2: float,
    ambient_C: float,
    enclosure_k_W_m2K: float,
    allow_material_dissipation: bool,
    default_vent_area_cm2: float,
) -> Dict:

    # ---------------- Geometry ----------------
    w_m, h_m, d_m = dimensions_m(tier)
    geom = tier_geometry(tier, tiers)

    Ae = geom["Ae"]
    f = geom["f"]
    g = geom["g"]

    # ---------------- Per-surface breakdown ----------------
    surfaces = []

    def _add_surface(name: str, w: float, h: float, b: float):
        A0 = float(w) * float(h)
        surfaces.append(
            {"name": name, "w": w, "h": h, "A0": A0, "b": b, "Ae": A0 * b}
        )

    from .iec60890_geometry import resolved_surfaces
    for name, a, b, bf in resolved_surfaces(tier, tiers):
        _add_surface(name, a, b, bf)

    # ---------------- Power + mode ----------------
    P = max(0.0, float(tier.total_heat()))
    curve_no = int(getattr(tier, "curve_no", 1))

    vent_requested = bool(tier.is_ventilated)
    vent_effective = vent_requested and (Ae > 1.25)

    x = 0.715 if vent_effective else 0.804
    d_fac = 1.0

    coeff_sources = []
    profile_source = None

    vent_curvefit_info = {
        "k": None,
        "c": None,
        "snapped": False,
    }

    # ---------------- IEC 60890 coefficients ----------------
    if vent_effective:
        Ab = max(1e-9, w_m * d_m)
        f = (h_m ** 1.35) / Ab
        k_res = curvefit.k_vents(
            ae=max(1.0, min(14.0, Ae)),
            opening_area_cm2=inlet_area_cm2,
        )
        c_res = curvefit.c_vents(
            f=f,
            opening_area_cm2=inlet_area_cm2,
        )

        k = k_res.value
        c = c_res.value

        vent_curvefit_info = {
            "k": k_res.meta,
            "c": c_res.meta,
            "snapped": k_res.snapped or c_res.snapped,
        }

        coeff_sources += ["Fig. 5", "Fig. 6"]
        g = None
    else:
        if Ae <= 1.25:
            k = curvefit.k_small_no_vents(Ae)
            g = h_m / max(1e-9, w_m)
            c = curvefit.c_small_no_vents(g)
            coeff_sources += ["Fig. 7", "Fig. 8"]
            f = None
        else:
            k = curvefit.k_no_vents(Ae)
            Ab = max(1e-9, w_m * d_m)
            f = (h_m ** 1.35) / Ab
            c = curvefit.c_no_vents(curve_no, f)
            coeff_sources += ["Fig. 3", "Fig. 4"]
            g = None

    # ---------------- IEC temperature rise ----------------
    dt_mid = k * d_fac * (P ** x)
    dt_top_raw = c * dt_mid

    if (not vent_effective) and (Ae <= 1.25):
        dt_075 = 0.5 * (dt_mid + dt_top_raw)
        dt_top = dt_075
        profile_source = "Fig. 2"
    else:
        dt_075 = None
        dt_top = dt_top_raw
        profile_source = "Fig. 1"

    T_mid = ambient_C + dt_mid
    T_top = ambient_C + dt_top
    T_075 = (ambient_C + dt_075) if dt_075 is not None else None

    limit_C = float(tier.effective_max_temp_C())

    # ============================================================
    # ORDER OF PRECEDENCE LOGIC (FIXED)
    # ============================================================

    compliant_top = T_top <= limit_C

    # -------- STAGE 1: IEC 60890 compliant → STOP --------
    if compliant_top:
        return {
            "ambient_C": ambient_C,
            "w_m": w_m, "h_m": h_m, "d_m": d_m, "Ae": Ae,
            "P": P,
            "k": k, "c": c, "x": x, "f": f, "g": g,
            "curve_no": curve_no,
            "wall_mounted": bool(wall_mounted),
            "ventilated": bool(vent_effective),
            "allow_material_dissipation": bool(getattr(tier, "allow_material_dissipation", True)),
            "enclosure_k_W_m2K": float(getattr(tier, "enclosure_k_W_m2K", 0.0)),
            "P_material": 0.0,
            "P_cooling": 0.0,
            "airflow_m3h": 0.0,
            "vent_recommended": False,
            "dt_mid": dt_mid,
            "dt_top": dt_top,
            "dt_075": dt_075,
            "dt_top_raw": dt_top_raw,
            "T_mid": T_mid,
            "T_top": T_top,
            "T_075": T_075,
            "limit_C": limit_C,
            "compliant_mid": True,
            "compliant_top": True,
            "surfaces": surfaces,
            "coeff_sources": sorted(set(coeff_sources)),
            "profile_source": profile_source,
            "curvefit": vent_curvefit_info,
        }

    # -------- STAGE 2: Material dissipation --------
    k_mat = float(enclosure_k_W_m2K)
    allow_mat = bool(allow_material_dissipation)

    delta_allow = max(0.0, limit_C - ambient_C)

    if allow_mat and k_mat > 0.0 and Ae > 0.0 and delta_allow > 0.0:
        P_material = min(P, k_mat * Ae * delta_allow)
    else:
        P_material = 0.0

    P_cooling = max(0.0, P - P_material)

    if P_cooling == 0.0:
        return {
            "ambient_C": ambient_C,
            "w_m": w_m, "h_m": h_m, "d_m": d_m, "Ae": Ae,
            "P": P,
            "k": k, "c": c, "x": x, "f": f, "g": g,
            "curve_no": curve_no,
            "wall_mounted": bool(wall_mounted),
            "ventilated": bool(vent_effective),
            "allow_material_dissipation": allow_mat,
            "enclosure_k_W_m2K": k_mat,
            "P_material": P_material,
            "P_cooling": 0.0,
            "airflow_m3h": 0.0,
            "vent_recommended": False,
            "dt_mid": dt_mid,
            "dt_top": dt_top,
            "dt_075": dt_075,
            "dt_top_raw": dt_top_raw,
            "T_mid": T_mid,
            "T_top": T_top,
            "T_075": T_075,
            "limit_C": limit_C,
            "compliant_mid": False,
            "compliant_top": False,
            "surfaces": surfaces,
            "coeff_sources": sorted(set(coeff_sources)),
            "profile_source": profile_source,
            "curvefit": vent_curvefit_info,
        }

    # -------- STAGE 3: Vent recommendation --------
    vent_recommended = False

    if not vent_effective and Ae > 1.25 and P_cooling > 0.0:
        print("[VENT-CHK] Preconditions met → testing hypothetical vents")

        rec_area_cm2 = float(default_vent_area_cm2)

        print(f"[VENT-CHK] Using project default vent area = {rec_area_cm2:.1f} cm²")

        if rec_area_cm2 > 0.0 and f is not None:
            k_v_res = curvefit.k_vents(
                ae=max(1.0, min(14.0, Ae)),
                opening_area_cm2=rec_area_cm2,
            )
            c_v_res = curvefit.c_vents(
                f=f,
                opening_area_cm2=rec_area_cm2,
            )

            k_v = k_v_res.value
            c_v = c_v_res.value

            dt_mid_v = k_v * (P ** 0.715)
            dt_top_v = c_v * dt_mid_v
            T_top_v = ambient_C + dt_top_v

            print(f"Variables k : {k_v} , c : {c_v} , amb : {ambient_C}, dt_mid : {dt_mid_v}, dt_top : {dt_top_v}")

            print(
                f"[VENT-CHK] Vent test | "
                f"T_top_v={T_top_v:.2f} °C | limit={limit_C:.2f} °C"
            )

            if T_top_v <= limit_C:
                vent_recommended = True
                print("[VENT-CHK] ✔ Vent recommended")
            else:
                print("[VENT-CHK] ✖ Vent insufficient")
        else:
            print("[VENT-CHK] ✖ Invalid vent test (area<=0 or f undefined)")

    # -------- STAGE 4: Active cooling (IEC TR 60890:2022 Annex K) --------
    # P_cooling = P - P_890
    # delta_allow = (T_int,max - T_a)

    project_altitude_m = float(getattr(tier, "project_altitude_m", 0.0))

    k_alt = air_k_factor_from_altitude_m(project_altitude_m)

    # Volumetric heat capacity per Annex K
    # Assumes 35 °C ambient, 50 % RH, adjusted for altitude
    VOL_HEAT_CAP_J_M3K = 1160.0 * k_alt

    airflow_m3h = (
            (P_cooling / (VOL_HEAT_CAP_J_M3K * delta_allow)) * 3600.0
    ) if (delta_allow > 0.0 and P_cooling > 0.0) else 0.0

    return {
        "ambient_C": ambient_C,
        "w_m": w_m, "h_m": h_m, "d_m": d_m, "Ae": Ae,
        "P": P,
        "k": k, "c": c, "x": x, "f": f, "g": g,
        "curve_no": curve_no,
        "wall_mounted": bool(wall_mounted),
        "ventilated": bool(vent_effective),
        "allow_material_dissipation": allow_mat,
        "enclosure_k_W_m2K": k_mat,
        "P_material": P_material,
        "P_cooling": P_cooling,
        "airflow_m3h": airflow_m3h,
        "vent_recommended": vent_recommended,
        "dt_mid": dt_mid,
        "dt_top": dt_top,
        "dt_075": dt_075,
        "dt_top_raw": dt_top_raw,
        "T_mid": T_mid,
        "T_top": T_top,
        "T_075": T_075,
        "limit_C": limit_C,
        "compliant_mid": False,
        "compliant_top": False,
        "surfaces": surfaces,
        "coeff_sources": sorted(set(coeff_sources)),
        "profile_source": profile_source,
        "curvefit": vent_curvefit_info,
    }
