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


def _calc_p_890_from_allowable_top_rise(
    *,
    dt_top_allow: float,
    k_iec: float,
    c: float,
    x: float,
    d_fac: float = 1.0,
) -> float:
    """
    Compute P_890 (IEC TR 60890 Annex K concept):
    the internal power loss that would cause the enclosure to reach the allowable
    top air temperature rise using the IEC 60890 air-rise model (no forced ventilation).

    IEC forward model in this code:
        dt_mid = k_iec * d_fac * P^x
        dt_top = c * dt_mid

    Invert for P such that dt_top == dt_top_allow:
        P_890 = ( dt_top_allow / (c * k_iec * d_fac) )^(1/x)

    Returns 0.0 if inputs are not physically valid.
    """
    dt_top_allow = float(dt_top_allow)
    k_iec = float(k_iec)
    c = float(c)
    x = float(x)
    d_fac = float(d_fac)

    if dt_top_allow <= 0.0:
        return 0.0
    if k_iec <= 0.0 or c <= 0.0 or x <= 0.0 or d_fac <= 0.0:
        return 0.0

    denom = c * k_iec * d_fac
    if denom <= 0.0:
        return 0.0

    base = dt_top_allow / denom
    if base <= 0.0:
        return 0.0

    return base ** (1.0 / x)

def annex_k_sealed_p890(
    *,
    Ae: float,
    h_m: float,
    w_m: float,
    d_m: float,
    curve_no: int,
    delta_allow_K: float,
) -> Dict:
    """
    IEC TR 60890:2022 Annex K
    Calculate P_890 assuming NO natural ventilation (sealed enclosure).

    Returns:
        {
            "P_890": float,
            "k": float,
            "c": float,
            "x": float,
            "f": Optional[float],
            "g": Optional[float],
            "coeff_sources": List[str],
        }
    """

    # Annex K MUST use unventilated exponent
    x_ak = 0.804
    d_fac = 1.0

    coeff_sources = []
    f_ak = None
    g_ak = None

    # --- Select unventilated IEC coefficients ---
    if Ae <= 1.25:
        # Small enclosure, no vents
        k_ak = curvefit.k_small_no_vents(Ae)
        g_ak = h_m / max(1e-9, w_m)
        c_ak = curvefit.c_small_no_vents(g_ak)
        coeff_sources += ["Fig. 7", "Fig. 8"]
    else:
        # Normal enclosure, no vents
        k_ak = curvefit.k_no_vents(Ae)
        Ab = max(1e-9, w_m * d_m)
        f_ak = (h_m ** 1.35) / Ab
        c_ak = curvefit.c_no_vents(curve_no, f_ak)
        coeff_sources += ["Fig. 3", "Fig. 4"]

    # --- Invert IEC air-rise model to get P_890 ---
    if delta_allow_K > 0.0 and k_ak > 0.0 and c_ak > 0.0:
        denom = c_ak * k_ak * d_fac
        base = delta_allow_K / max(1e-12, denom)
        P_890 = (base ** (1.0 / x_ak)) if base > 0.0 else 0.0
    else:
        P_890 = 0.0

    return {
        "P_890": float(P_890),
        "k": float(k_ak),
        "c": float(c_ak),
        "x": float(x_ak),
        "f": f_ak,
        "g": g_ak,
        "coeff_sources": sorted(set(coeff_sources)),
    }


def calc_tier_iec60890(
    *,
    tier: TierItem,
    tiers: List[TierItem],
    wall_mounted: bool,
    inlet_area_cm2: float,
    ambient_C: float,
    altitude_m: float,
    ip_rating_n: int,
    vent_test_area_cm2: float | None = None,
    solar_delta_K: float = 0.0,
) -> Dict:

    # ---------------- Geometry ----------------
    w_m, h_m, d_m = dimensions_m(tier)
    geom = tier_geometry(tier, tiers)

    Ae = geom["Ae"]

    # ---------------- IP protection rule ----------------
    vents_permitted_by_ip = int(ip_rating_n) < 5

    # ---------------- Per-surface breakdown ----------------
    surfaces = []

    def _add_surface(name: str, w: float, h: float, b: float):
        A0 = float(w) * float(h)
        surfaces.append({"name": name, "w": w, "h": h, "A0": A0, "b": b, "Ae": A0 * b})

    from .iec60890_geometry import resolved_surfaces
    for name, a, b, bf in resolved_surfaces(tier, tiers):
        _add_surface(name, a, b, bf)

    # ---------------- Solar contribution ----------------
    solar_dt = float(max(0.0, solar_delta_K))

    # ---------------- Power + mode ----------------
    P = max(0.0, float(tier.total_heat()))
    curve_no = int(getattr(tier, "curve_no", 1))

    vent_requested = bool(tier.is_ventilated)
    vent_effective = (
        vent_requested
        and (Ae > 1.25)
        and vents_permitted_by_ip
    )

    x = 0.715 if vent_effective else 0.804
    d_fac = 1.0

    coeff_sources = []

    vent_curvefit_info = {"k": None, "c": None, "snapped": False}

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

        k_iec = k_res.value
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
            k_iec = curvefit.k_small_no_vents(Ae)
            g = h_m / max(1e-9, w_m)
            c = curvefit.c_small_no_vents(g)
            coeff_sources += ["Fig. 7", "Fig. 8"]
            f = None
        else:
            k_iec = curvefit.k_no_vents(Ae)
            Ab = max(1e-9, w_m * d_m)
            f = (h_m ** 1.35) / Ab
            c = curvefit.c_no_vents(curve_no, f)
            coeff_sources += ["Fig. 3", "Fig. 4"]
            g = None

    # ---------------- IEC temperature rise (forward) ----------------
    dt_mid = k_iec * d_fac * (P ** x)
    dt_top_raw = c * dt_mid

    if (not vent_effective) and (Ae <= 1.25):
        dt_top = dt_top_raw
        dt_075 = dt_top
        profile_source = "Fig. 2"
    else:
        dt_075 = None
        dt_top = dt_top_raw
        profile_source = "Fig. 1"

    T_mid = ambient_C + dt_mid + solar_dt
    T_top = ambient_C + dt_top + solar_dt
    T_075 = (ambient_C + dt_075 + solar_dt) if dt_075 is not None else None

    limit_C = float(tier.effective_max_temp_C())
    delta_allow = max(0.0, limit_C - ambient_C - solar_dt)
    P_890_installed = _calc_p_890_from_allowable_top_rise(
        dt_top_allow=delta_allow,
        k_iec=k_iec,
        c=c,
        x=x,
        d_fac=d_fac,
    )

    # ============================================================
    # STAGE 0: Thermal feasibility check (external conditions)
    # ============================================================

    external_dt = max(0.0, solar_dt)

    delta_allow_total = limit_C - ambient_C - external_dt

    thermal_impossible = delta_allow_total <= 0.0

    blockers = []
    if ambient_C >= limit_C:
        blockers.append("AMBIENT")
    if solar_dt > 0 and (ambient_C + solar_dt) >= limit_C:
        blockers.append("SOLAR")


    if thermal_impossible:
        return {
            "ambient_C": ambient_C,
            "solar_dt": solar_dt,

            "w_m": w_m, "h_m": h_m, "d_m": d_m, "Ae": Ae,
            "P": P,

            # coefficients for transparency
            "k": k_iec,
            "c": c,
            "x": x,
            "f": f,
            "g": g,

            "curve_no": curve_no,
            "wall_mounted": bool(wall_mounted),
            "ventilated": False,  # explicitly meaningless here

            # ---- cooling disabled ----
            "P_890": 0.0,
            "P_fan": 0.0,
            "P_cooling": 0.0,
            "airflow_m3h": 0.0,
            "vent_recommended": False,

            # ---- temperatures (external conditions dominate) ----
            "dt_mid": 0.0,
            "dt_top": 0.0,
            "dt_075": None,

            "T_mid": ambient_C + external_dt,
            "T_top": ambient_C + external_dt,
            "T_075": None,

            "limit_C": limit_C,
            "compliant_mid": False,
            "compliant_top": False,

            "cooling_possible": False,
            "thermal_blockers": blockers,

            "delta_allow_K": delta_allow_total,

            "surfaces": surfaces,
            "coeff_sources": sorted(set(coeff_sources)),
            "profile_source": "Externally dominated",
            "curvefit": vent_curvefit_info,
            "inlet_area_cm2": inlet_area_cm2,
        }

    # ============================================================
    # STAGE 1: IEC compliant -> stop
    # ============================================================
    compliant_top = T_top <= limit_C

    if compliant_top:
        return {
            "ambient_C": ambient_C,
            "solar_dt": solar_dt,
            "w_m": w_m, "h_m": h_m, "d_m": d_m, "Ae": Ae,
            "P": P,
            "k": k_iec, "c": c, "x": x, "f": f, "g": g,
            "curve_no": curve_no,
            "wall_mounted": bool(wall_mounted),
            "ventilated": bool(vent_effective),


            # standards-aligned outputs
            "P_890": float(P_890_installed),  # ✅ limit, not actual
            "P_fan": 0.0,
            "P_material": 0.0,       # no longer used in standards path
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
            "inlet_area_cm2": inlet_area_cm2,
        }

    # ============================================================
    # STAGE 2: Installed-condition power limit (diagnostic only)
    # ============================================================
    # This is NOT Annex K P_890.
    # It is the power that would just reach the temperature limit
    # using the installed-condition IEC model (vents included if present).

    P_limit_installed_raw = _calc_p_890_from_allowable_top_rise(
        dt_top_allow=delta_allow,
        k_iec=k_iec,
        c=c,
        x=x,
        d_fac=d_fac,
    )

    P_limit_installed = max(0.0, min(P, float(P_limit_installed_raw)))

    # DO NOT compute fan power here
    # DO NOT use this value for sizing

    # ============================================================
    # STAGE 3: Vent recommendation (installed-condition only)
    # ============================================================
    vent_recommended = False

    if (
            vents_permitted_by_ip
            and (not vent_effective)  # vents not already installed
            and (Ae > 1.25)
            and (not compliant_top)  # enclosure currently too hot
    ):
        rec_area_cm2 = (
            float(vent_test_area_cm2)
            if vent_test_area_cm2 is not None
            else None
        )

        if rec_area_cm2 and rec_area_cm2 > 0.0 and f is not None:
            # --- Forward IEC calc WITH vents at full P ---
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

            # IEC vented exponent
            x_v = 0.715

            dt_mid_v = k_v * (P ** x_v)
            dt_top_v = c_v * dt_mid_v
            T_top_v = ambient_C + dt_top_v + solar_dt

            if T_top_v <= limit_C:
                vent_recommended = True

    # ============================================================
    # STAGE 4: Active cooling airflow (IEC TR 60890:2022 Annex K)
    # ============================================================

    delta_allow = max(0.0, limit_C - ambient_C - solar_dt)

    ak = annex_k_sealed_p890(
        Ae=Ae,
        h_m=h_m,
        w_m=w_m,
        d_m=d_m,
        curve_no=curve_no,
        delta_allow_K=delta_allow,
    )

    P_890 = min(P, ak["P_890"])
    P_fan = max(0.0, P - P_890)
    P_cooling = P_fan

    # print("\n========== IEC 60890 vs Annex K DEBUG ==========")
    # print(f"Tier: {getattr(tier, 'name', '—')}")
    # print(f"Ae = {Ae:.3f} m²")
    # print(f"Ambient = {ambient_C:.1f} °C")
    # print(f"Solar dt = {solar_dt:.1f} °C")
    # print(f"Limit = {limit_C:.1f} °C")
    # print(f"ΔT_allow = {delta_allow:.1f} K")
    # print(f"Input Power P = {P:.1f} W")
    #
    # print("\n--- Installed-condition IEC model ---")
    # print(f"  Ventilated: {vent_effective}")
    # print(f"  k_iec = {k_iec:.5f}")
    # print(f"  c_iec = {c:.5f}")
    # print(f"  x_iec = {x:.3f}")
    # print(f"  ΔT_top = {dt_top:.2f} K")
    # print(f"  T_top = {T_top:.2f} °C")
    # print(f"  P_limit_installed = {P_limit_installed:.2f} W")
    #
    # print("\n--- Annex K sealed-enclosure model ---")
    # print("  (Natural ventilation IGNORED)")
    # print(f"  k_ak = {ak['k']:.5f}")
    # print(f"  c_ak = {ak['c']:.5f}")
    # print(f"  x_ak = {ak['x']:.3f}")
    # print(f"  P_890 (Annex K) = {ak['P_890']:.2f} W")
    #
    # print("\n--- Fan sizing (Annex K governs) ---")
    # print(f"  P_fan = {P_fan:.2f} W")
    # print("===============================================\n")

    k_alt = air_k_factor_from_altitude_m(altitude_m)
    VOL_HEAT_CAP_J_M3K = 1160.0 * k_alt

    airflow_m3h = (
            (P_cooling / (VOL_HEAT_CAP_J_M3K * delta_allow)) * 3600.0
    ) if (delta_allow > 0.0 and P_cooling > 0.0) else 0.0

    return {
        "ambient_C": ambient_C,
        "solar_dt": solar_dt,
        "w_m": w_m, "h_m": h_m, "d_m": d_m, "Ae": Ae,
        "P": P,
        "k": k_iec, "c": c, "x": x, "f": f, "g": g,
        "curve_no": curve_no,
        "wall_mounted": bool(wall_mounted),
        "ventilated": bool(vent_effective),

        # standards-aligned outputs
        "P_890": P_890,
        "P_fan": P_fan,
        "P_material": 0.0,     # deprecated in standards path
        "P_cooling": P_cooling,
        "airflow_m3h": airflow_m3h,
        "vent_recommended": vent_recommended,

        # Annex K transparency
        "annex_k": {
            "vents_ignored": True,
            "k": ak["k"],
            "c": ak["c"],
            "x": ak["x"],
            "coeff_sources": ak["coeff_sources"],
        },

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
        "inlet_area_cm2": inlet_area_cm2,
    }
