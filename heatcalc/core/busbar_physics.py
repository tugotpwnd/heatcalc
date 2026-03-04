from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from heatcalc.core.busbar_geometry import (
    BusbarGeometry,
    effective_radiating_area_per_m,
    surface_area_per_m,
)

# ============================================================
# PHYSICAL CONSTANTS (Copper + radiation)
# ============================================================

SIGMA = 5.670e-8       # Stefan–Boltzmann (W/m^2/K^4)
ALPHA_CU = 0.00393     # Copper temperature coefficient (1/°C)
RHO_20 = 1.724e-8      # Copper resistivity at 20°C (Ω·m)

# ============================================================
# Convection correlation coefficients (make them explicit)
# ============================================================

FORCED_CONV_COEFF = 120.0          # W/(m^2*K) * sqrt(m/s)  (your model)
NATURAL_CONV_VERTICAL = 7.66       # IEC-style correlation constant
NATURAL_CONV_HORIZONTAL = 5.92     # IEC-style correlation constant


def relative_emissivity(e1: float, e2: float) -> float:
    """
    Effective emissivity between two surfaces (busbar vs environment).
    """
    denom = (e1 + e2) - (e1 * e2)
    if denom <= 0:
        return 0.0
    return (e1 * e2) / denom


def resistance_20C_per_m(width_m: float, thickness_m: float) -> float:
    """
    DC resistance per metre at 20°C.
    """
    A = width_m * thickness_m
    if A <= 0:
        raise ValueError("Busbar cross-sectional area must be > 0")
    return RHO_20 / A


def resistance_T_per_m(R20: float, T_bus_C: float) -> float:
    """
    Copper resistance at temperature T (°C).
    """
    return R20 * (1.0 + ALPHA_CU * (T_bus_C - 20.0))


@dataclass(frozen=True)
class BusbarThermalInputs:
    """
    Inputs that are not pure geometry.
    """
    I_total_A: float
    eps_bus: float = 0.10
    eps_env: float = 0.90
    v_mps: float = 0.0          # forced air velocity (0 = natural convection)
    S_ac: float = 1.0           # AC correction factor for loss


@dataclass(frozen=True)
class BusbarPhysicsState:
    """
    Canonical physics snapshot at a specific bus temperature.

    All powers are per-metre unless otherwise noted.
    """
    # Temperatures
    T_bus_C: float
    T_air_C: float

    # Electrical
    I_bar_A: float
    R20_ohm_per_m: float
    R_T_ohm_per_m: float
    P_gen_W_per_m: float

    # Areas
    As_conv_m2_per_m: float
    As_rad_raw_m2_per_m: float
    As_rad_eff_m2_per_m: float
    rad_blockage_frac: float

    # Convection
    theta_K: float
    W_conv_W_m2: float
    P_conv_W_per_m: float

    # Radiation
    eps_rel: float
    W_rad_W_m2: float
    P_rad_W_per_m: float

    # Residual
    f_W_per_m: float  # P_gen - (P_conv + P_rad)


def compute_busbar_physics(
    *,
    geom: BusbarGeometry,
    therm: BusbarThermalInputs,
    T_bus_C: float,
    T_air_C: float,
) -> BusbarPhysicsState:
    """
    Single source of truth for all busbar thermal physics.

    This function is used by:
    - the solver residual f(T)
    - the reporting/diagnostics layer

    That eliminates "solver vs report" divergence.
    """

    # ---- Current sharing ----
    N = max(1, int(geom.bars_in_parallel))
    I_bar = therm.I_total_A / float(N)

    # ---- Resistance ----
    R20 = resistance_20C_per_m(geom.width_m, geom.thickness_m)
    R_T = resistance_T_per_m(R20, T_bus_C)

    # ---- Electrical generation ----
    P_gen = (I_bar ** 2) * R_T * therm.S_ac  # W per m (per bar)
    # total per m for the phase arrangement:
    P_gen_total = float(N) * P_gen

    # ---- Areas ----
    As_conv = surface_area_per_m(geom.width_m, geom.thickness_m)

    As_rad_raw, As_rad_eff, blockage = effective_radiating_area_per_m(
        geom.width_m,
        geom.thickness_m,
        bars_in_parallel=N,
        face_to_face_dim=geom.face_to_face_dim,
    )

    # ---- Convection ----
    theta = max(T_bus_C - T_air_C, 0.0)  # (°C) treated as K difference
    L = max(geom.L_char_m, 1e-6)

    if therm.v_mps > 0.0:
        W_conv = FORCED_CONV_COEFF * np.sqrt(therm.v_mps) * theta
    else:
        if geom.convection_mode == "vertical":
            W_conv = NATURAL_CONV_VERTICAL * (theta ** 1.25) / (L ** 0.25)
        else:
            W_conv = NATURAL_CONV_HORIZONTAL * (theta ** 1.25) / (L ** 0.25)

    # convection uses full external area (per your original approach)
    P_conv = W_conv * As_conv

    # ---- Radiation ----
    eps_rel = relative_emissivity(therm.eps_bus, therm.eps_env)
    T_K = T_bus_C + 273.15
    Ta_K = T_air_C + 273.15

    W_rad = SIGMA * eps_rel * (T_K**4 - Ta_K**4)
    P_rad = W_rad * As_rad_eff

    # ---- Residual (per m for the whole arrangement) ----
    P_out_total = float(N) * (P_conv + P_rad)
    f = P_gen_total - P_out_total

    return BusbarPhysicsState(
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
        W_conv_W_m2=float(W_conv),
        P_conv_W_per_m=float(float(N) * P_conv),

        eps_rel=float(eps_rel),
        W_rad_W_m2=float(W_rad),
        P_rad_W_per_m=float(float(N) * P_rad),

        f_W_per_m=float(f),
    )