# heatcalc/core/busbar_physics.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np

from heatcalc.core.busbar_geometry import (
    BusbarGeometry,
    effective_radiating_area_per_m,
    surface_area_per_m,
)

SIGMA = 5.670e-8
ALPHA_CU = 0.00393
RHO_20 = 1.724e-8

FORCED_CONV_COEFF = 120.0
NATURAL_CONV_VERTICAL = 7.66
NATURAL_CONV_HORIZONTAL = 5.92


def relative_emissivity(e1: float, e2: float) -> float:
    denom = (e1 + e2) - (e1 * e2)
    if denom <= 0.0:
        return 0.0
    return (e1 * e2) / denom


def resistance_20C_per_m(width_m: float, thickness_m: float) -> float:
    A = width_m * thickness_m
    if A <= 0.0:
        raise ValueError("Busbar cross-sectional area must be > 0")
    return RHO_20 / A


def resistance_T_per_m(R20: float, T_bus_C: float) -> float:
    return R20 * (1.0 + ALPHA_CU * (T_bus_C - 20.0))


@dataclass(frozen=True)
class BusbarThermalInputs:
    I_total_A: float
    eps_bus: float = 0.4 # Varies depending on cooling / heating
    eps_env: float = 0.90
    v_mps: float = 0.0
    S_ac: float = 1.3


@dataclass(frozen=True)
class BusbarPhysicsState:
    T_bus_C: float
    T_air_C: float

    I_bar_A: float
    R20_ohm_per_m: float
    R_T_ohm_per_m: float
    P_gen_W_per_m: float

    As_conv_m2_per_m: float
    As_rad_raw_m2_per_m: float
    As_rad_eff_m2_per_m: float
    rad_blockage_frac: float

    theta_K: float
    W_conv_major_W_m2: float
    W_conv_minor_W_m2: float
    P_conv_W_per_m: float

    eps_rel: float
    W_rad_W_m2: float
    P_rad_W_per_m: float

    f_W_per_m: float


def compute_busbar_physics(
    *,
    geom: BusbarGeometry,
    therm: BusbarThermalInputs,
    T_bus_C: float,
    T_air_C: float,
    I_override_A: Optional[float] = None,
    debug: bool = True,
) -> BusbarPhysicsState:
    """
    Canonical busbar heat-balance physics.

    Important convention:
    This routine is intended for passive heated-conductor solving,
    so convection/radiation are treated as LOSS terms only.
    That means theta = max(T_bus - T_air, 0).
    Solver should enforce T_bus >= T_air.
    """

    N = max(1, int(geom.bars_in_parallel))

    if I_override_A is None:
        I_total = float(therm.I_total_A)
    else:
        I_total = float(I_override_A)

    I_bar = I_total / float(N)

    R20 = resistance_20C_per_m(geom.width_m, geom.thickness_m)
    R_T = resistance_T_per_m(R20, T_bus_C)

    P_gen_per_bar = (I_bar ** 2) * R_T * therm.S_ac
    P_gen_total = float(N) * P_gen_per_bar

    As_conv = surface_area_per_m(geom.width_m, geom.thickness_m)

    As_rad_raw, As_rad_eff, blockage = effective_radiating_area_per_m(
        geom.width_m,
        geom.thickness_m,
        bars_in_parallel=N,
        face_to_face_dim=geom.face_to_face_dim,
    )

    # -------------------------------------------------------------------------
    # FACE-BASED NATURAL CONVECTION (RECTANGULAR BUSBAR)
    #
    # We treat the busbar as 4 faces:
    #   - 2 major faces (width x length)
    #   - 2 minor faces (thickness x length)
    #
    # Each face uses its own:
    #   - characteristic length (L)
    #   - convection correlation (vertical / horizontal)
    #
    # This avoids applying a single W_conv to the full perimeter, which is
    # physically incorrect for rectangular sections.
    # -------------------------------------------------------------------------

    theta = max(T_bus_C - T_air_C, 0.0)

    # Dimensions
    w = geom.width_m
    t = geom.thickness_m

    # Convert to mm for Copper Handbook correlations
    L_major_mm = w * 1000.0
    L_minor_mm = t * 1000.0

    # Areas per metre length
    A_major = 2.0 * w  # two large faces
    A_minor = 2.0 * t  # two thin edges

    # -----------------------------
    # Major faces (usually dominant)
    # -----------------------------
    if geom.convection_mode == "vertical":
        W_major = NATURAL_CONV_VERTICAL * (theta ** 1.25) / (L_major_mm ** 0.25)
    else:
        W_major = NATURAL_CONV_HORIZONTAL * (theta ** 1.25) / (L_major_mm ** 0.25)

    P_major = N * W_major * A_major

    # -----------------------------
    # Minor faces (less important)
    # -----------------------------
    # These are usually less effective; treat as horizontal surfaces by default
    W_minor = NATURAL_CONV_HORIZONTAL * (theta ** 1.25) / (L_minor_mm ** 0.25)

    P_minor = N * W_minor * A_minor

    # -----------------------------
    # Total convection
    # -----------------------------
    P_conv_total = P_major + P_minor
    A_conv_total = N * (A_major + A_minor)
    W_conv_eff = P_conv_total / max(A_conv_total, 1e-12)

    eps_rel = relative_emissivity(therm.eps_bus, therm.eps_env)
    T_K = T_bus_C + 273.15
    Ta_K = T_air_C + 273.15

    # Radiation is also treated as a loss term in this passive solve.
    # Once solver clamps T >= Ta, this stays non-negative.
    W_rad = SIGMA * eps_rel * max(T_K**4 - Ta_K**4, 0.0)
    P_rad_total = float(N) * W_rad * As_rad_eff

    f = P_gen_total - (P_conv_total + P_rad_total)

    state = BusbarPhysicsState(
        T_bus_C=float(T_bus_C),
        T_air_C=float(T_air_C),

        I_bar_A=float(I_bar),
        R20_ohm_per_m=float(R20),
        R_T_ohm_per_m=float(R_T),
        P_gen_W_per_m=float(P_gen_total),

        As_conv_m2_per_m=float(As_conv),
        As_rad_raw_m2_per_m=float(As_rad_raw),
        As_rad_eff_m2_per_m=float(As_rad_eff),
        rad_blockage_frac=float(blockage),

        theta_K=float(theta),
        W_conv_major_W_m2=float(W_major),
        W_conv_minor_W_m2=float(W_minor),
        P_conv_W_per_m=float(P_conv_total),

        eps_rel=float(eps_rel),
        W_rad_W_m2=float(W_rad),
        P_rad_W_per_m=float(P_rad_total),

        f_W_per_m=float(f),
    )
    if debug:
        print(
            f"[busbar_physics] {geom.name} | "
            f"Tbus={state.T_bus_C:.3f} C  Tair={state.T_air_C:.3f} C  "
            f"Ibar={state.I_bar_A:.3f} A  "
            f"Pgen={state.P_gen_W_per_m:.6f} W/m  "
            f"Pconv={state.P_conv_W_per_m:.6f} W/m  "
            f"Prad={state.P_rad_W_per_m:.6f} W/m  "
            f"f={state.f_W_per_m:.6f} W/m"
        )

    return state