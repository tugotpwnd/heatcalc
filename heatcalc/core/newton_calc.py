import numpy as np
import matplotlib.pyplot as plt

from heatcalc.core.iec60890_calc import calc_tier_iec60890

# ============================================================
# PHYSICAL CONSTANTS
# ============================================================

SIGMA = 5.670e-8  # Stefan–Boltzmann (W/m²K⁴)
ALPHA_CU = 0.00393  # Copper temperature coefficient (1/°C)
RHO_20 = 1.724e-8  # Copper resistivity at 20°C (Ω·m)


# ============================================================
# BUSBAR MODEL
# ============================================================

def busbar_R20_per_m(width_m, thickness_m):
    """DC resistance per metre at 20°C"""
    A = width_m * thickness_m
    return RHO_20 / A


def surface_area_per_m(width_m, thickness_m):
    """External surface area per metre (ignores ends)"""
    return 2 * (width_m + thickness_m)


def relative_emissivity(e1, e2):
    return (e1 * e2) / ((e1 + e2) - (e1 * e2))


def heat_balance_function(
    T,
    I,
    Ta,
    R20,
    As_conv,
    As_rad,
    L,
    eps1,
    eps2,
    mode="vertical",
    v=0.0,
    S_ac=1.0,
):
    """
    Returns f(T) = Pgen - (Pconv + Prad)
    """

    # ----- Electrical heat generation -----
    R_T = R20 * (1 + ALPHA_CU * (T - 20.0))
    P_gen = I**2 * R_T * S_ac

    # ----- Convection -----
    theta = max(T - Ta, 0.0)

    if v > 0.0:
        W_conv = 120.0 * np.sqrt(v) * theta
    else:
        if mode == "vertical":
            W_conv = 7.66 * (theta ** 1.25) / (L ** 0.25)
        else:
            W_conv = 5.92 * (theta ** 1.25) / (L ** 0.25)

    P_conv = W_conv * As_conv

    # ----- Radiation -----
    e_rel = relative_emissivity(eps1, eps2)

    T_K = T + 273.15
    Ta_K = Ta + 273.15

    W_rad = SIGMA * e_rel * (T_K**4 - Ta_K**4)
    P_rad = W_rad * As_rad
    return P_gen - (P_conv + P_rad)


def effective_radiating_area_per_m(width_m, thickness_m, N, face_dim):
    As = 2 * (width_m + thickness_m)

    if N <= 1:
        return As

    if face_dim == "width":
        d = width_m
    else:
        d = thickness_m

    A_blocked_avg = 2 * d * (1 - 1 / N)
    A_eff = As - A_blocked_avg

    return max(A_eff, 0.0)

# ============================================================
# NEWTON–RAPHSON SOLVER
# ============================================================

def solve_busbar_temperature(
    *,
    I_total,
    bars_in_parallel,
    face_to_face_dim,
    Ta,
    width_m,
    thickness_m,
    L,
    eps1=0.10,
    eps2=0.90,
    mode="vertical",
    v=0.0,
    S_ac=1.0,
    T0=None,
    tol_T=0.01,
    tol_P=1e-6,
    max_iter=50,
    verbose=False,
):
    """
    Solve for steady-state busbar temperature using Newton–Raphson.
    """

    R20 = busbar_R20_per_m(width_m, thickness_m)
    As_conv = surface_area_per_m(width_m, thickness_m)

    As_rad = effective_radiating_area_per_m(
        width_m,
        thickness_m,
        bars_in_parallel,
        face_to_face_dim,
    )

    if T0 is None:
        T = Ta + 40.0
    else:
        T = T0

    history = []

    for k in range(max_iter):

        I_bar = I_total / bars_in_parallel

        f = heat_balance_function(
            T,
            I_bar,
            Ta,
            R20,
            As_conv,
            As_rad,
            L,
            eps1,
            eps2,
            mode,
            v,
            S_ac,
        )

        # Numerical derivative
        dT = 0.01

        f_plus = heat_balance_function(
            T + dT,
            I_bar,
            Ta,
            R20,
            As_conv,
            As_rad,
            L,
            eps1,
            eps2,
            mode,
            v,
            S_ac,
        )

        df = (f_plus - f) / dT

        if abs(df) < 1e-12:
            break

        T_new = T - f / df

        history.append(T_new)

        if verbose:
            print(f"Iter {k+1}: T = {T_new:.4f} °C, f(T) = {f:.6f}")

        if abs(T_new - T) < tol_T and abs(f) < tol_P:
            return T_new, history

        T = T_new

    # ----- Fallback: Bisection -----
    T_low = Ta
    T_high = Ta + 300

    for _ in range(100):
        T_mid = 0.5 * (T_low + T_high)

        I_bar = I_total / bars_in_parallel

        f_low = heat_balance_function(
            T_low,
            I_bar,
            Ta,
            R20,
            As_conv,
            As_rad,
            L,
            eps1,
            eps2,
            mode,
            v,
            S_ac,
        )

        f_mid = heat_balance_function(
            T_mid,
            I_bar,
            Ta,
            R20,
            As_conv,
            As_rad,
            L,
            eps1,
            eps2,
            mode,
            v,
            S_ac,
        )

        if f_low * f_mid < 0:
            T_high = T_mid
        else:
            T_low = T_mid

        if abs(T_high - T_low) < tol_T:
            return T_mid, history

    raise RuntimeError("Solver did not converge.")


import copy
from dataclasses import dataclass

# ---- You already have these from the busbar script ----
# solve_busbar_temperature(...)
# busbar_R20_per_m(...)
# etc.

@dataclass
class BusbarModelInputs:
    I_A: float
    width_m: float
    thickness_m: float
    L_m: float
    eps_bus: float = 0.10
    eps_env: float = 0.90
    mode: str = "vertical"     # "vertical" or "horizontal"
    v_mps: float = 0.0         # forced air velocity (0 = natural)
    S_ac: float = 1.0          # AC correction factor


def _select_busbar_ambient(result_60890: dict, which: str) -> float:
    """
    Choose which enclosure air temperature drives the busbar convection/radiation.
    """
    which = which.lower().strip()
    if which == "mid":
        return float(result_60890["T_mid"])
    if which == "top":
        return float(result_60890["T_top"])
    if which in ("075", "t075", "0.75", "t_075"):
        t = result_60890.get("T_075", None)
        if t is None:
            # fall back if model path doesn't output it
            return float(result_60890["T_top"])
        return float(t)
    raise ValueError("which must be one of: 'mid', 'top', '075'")


def _compute_busbar_loss_W_per_m(
    *,
    I_total_A: float,
    bars_in_parallel: int,
    T_bus_C: float,
    width_m: float,
    thickness_m: float,
    S_ac: float = 1.0,
    rho20: float = 1.724e-8,
    alpha: float = 0.00393,
) -> float:
    """
    Electrical loss per metre = I^2 * R(T) * S_ac
    """
    A = width_m * thickness_m
    R20 = rho20 / A

    R_T = R20 * (1.0 + alpha * (T_bus_C - 20.0))

    I_bar = I_total_A / bars_in_parallel

    P_single = (I_bar ** 2) * R_T * S_ac

    return bars_in_parallel * P_single


def calc_tier_iec60890_coupled(
    *,
    tier,
    tiers,
    wall_mounted: bool,
    inlet_area_cm2: float,
    ambient_C: float,
    altitude_m: float,
    ip_rating_n: int,
    solar_delta_K: float = 0.0,
    max_iter: int = 30,
    tol_T: float = 0.05,
    tol_P: float = 0.5,
):
    """
    Fully coupled enclosure ↔ busbar solver.
    Calls the EXISTING IEC60890 calc iteratively.
    """

    if not getattr(tier, "busbars", None):
        # No busbars -> just run normal calc
        return calc_tier_iec60890(
            tier=tier,
            tiers=tiers,
            wall_mounted=wall_mounted,
            inlet_area_cm2=inlet_area_cm2,
            ambient_C=ambient_C,
            altitude_m=altitude_m,
            ip_rating_n=ip_rating_n,
            solar_delta_K=solar_delta_K,
        )

    P_base = float(tier.total_heat_w)
    P_bus = 0.0
    Ta_prev = ambient_C

    history = []

    for k in range(max_iter):

        P_total = P_base + P_bus

        # ---- Run IEC enclosure model ----
        res = calc_tier_iec60890(
            tier=tier,
            tiers=tiers,
            wall_mounted=wall_mounted,
            inlet_area_cm2=inlet_area_cm2,
            ambient_C=ambient_C,
            altitude_m=altitude_m,
            ip_rating_n=ip_rating_n,
            solar_delta_K=solar_delta_K,
            P_override_W=P_total,
        )

        Ta = float(res["T_top"])  # conservative choice

        # ---- Solve busbars ----
        P_bus_new = 0.0
        bus_results = []

        for b in tier.busbars:
            T_bus, _ = solve_busbar_temperature(
                I_total=b.I_total_A,
                bars_in_parallel=b.bars_in_parallel,
                face_to_face_dim=b.face_to_face_dim,
                Ta=Ta,
                width_m=b.width_mm / 1000.0,
                thickness_m=b.thickness_mm / 1000.0,
                L=b.L_char_mm / 1000.0,
                eps1=b.eps_bus,
                eps2=b.eps_env,
                mode=b.convection_mode,
                v=b.v_mps,
                S_ac=b.S_ac,
            )

            P_loss_per_m = _compute_busbar_loss_W_per_m(
                I_total_A=b.I_total_A,
                bars_in_parallel=b.bars_in_parallel,
                T_bus_C=T_bus,
                width_m=b.width_mm / 1000.0,
                thickness_m=b.thickness_mm / 1000.0,
                S_ac=b.S_ac,
            )

            P_loss = P_loss_per_m * b.length_m
            P_bus_new += P_loss

            bus_results.append({
                "name": b.name,
                "T_bus_C": T_bus,
                "P_loss_W": P_loss,
            })

        # ---- Store iteration history ONCE ----
        history.append({
            "iter": k + 1,
            "T_air_C": Ta,
            "busbars": bus_results,
        })

        # ---- Convergence check ----
        if abs(Ta - Ta_prev) < tol_T and abs(P_bus_new - P_bus) < tol_P:
            res["busbars"] = bus_results
            res["coupling"] = {"converged": True, "iterations": k+1, "history": history}
            return res

        Ta_prev = Ta
        P_bus = P_bus_new

    res["busbars"] = bus_results
    res["coupling"] = {"converged": False, "iterations": max_iter, "history": history}
    return res