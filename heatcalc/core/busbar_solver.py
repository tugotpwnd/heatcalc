from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from heatcalc.core.busbar_geometry import BusbarGeometry, build_busbar_segments, from_busbarspec_mm
from heatcalc.core.busbar_physics import (
    BusbarThermalInputs,
    BusbarPhysicsState,
    compute_busbar_physics, ALPHA_CU,
)
from heatcalc.core.models import BusbarSpec


@dataclass
class BusbarSolveResult:
    T_bus_C: float
    physics: BusbarPhysicsState
    converged: bool
    method: str
    iterations: int
    trace: list
    T_profile: np.ndarray
    P_total_loss_W: float
# ============================================================
# NUMERICAL UTILITIES
# ============================================================
def solve_tridiagonal(a, b, c, d):

    n = len(d)

    cp = np.zeros(n)
    dp = np.zeros(n)

    if abs(b[0]) < 1e-12:
        raise RuntimeError("Jacobian singular at first node")

    cp[0] = c[0] / b[0]
    dp[0] = d[0] / b[0]

    for i in range(1, n):

        denom = b[i] - a[i] * cp[i-1]

        if abs(denom) < 1e-12:
            raise RuntimeError("Jacobian singular")

        cp[i] = c[i] / denom if i < n-1 else 0
        dp[i] = (d[i] - a[i] * dp[i-1]) / denom

    x = np.zeros(n)
    x[-1] = dp[-1]

    for i in reversed(range(n-1)):
        x[i] = dp[i] - cp[i] * x[i+1]

    return x

def build_tridiagonal_jacobian(T_vec, segments, segment_geoms, therm, air_temp_C):

    n = len(T_vec)

    a = np.zeros(n)
    b = np.zeros(n)
    c = np.zeros(n)

    for i, seg in enumerate(segments):

        geom = segment_geoms[i]
        T = T_vec[i]

        state = compute_busbar_physics(
            geom=geom,
            therm=therm,
            T_bus_C=T,
            T_air_C=air_temp_C,
        )

        # geometry terms
        k_cu = 400.0
        A = seg.width_m * seg.thickness_m
        dx = seg.length_m

        cond = k_cu * A / dx

        dT = 0.01

        state_hi = compute_busbar_physics(
            geom=geom,
            therm=therm,
            T_bus_C=T + dT,
            T_air_C=air_temp_C,
        )

        Pout = (state.P_conv_W_per_m + state.P_rad_W_per_m) * seg.length_m
        Pout_hi = (state_hi.P_conv_W_per_m + state_hi.P_rad_W_per_m) * seg.length_m

        dPout_dT = (Pout_hi - Pout) / dT

        # radiation derivative
        sigma = 5.670e-8
        eps = state.eps_rel
        A = state.As_rad_eff_m2_per_m
        T_k = T + 273.15

        dPrad_dT = 4 * sigma * eps * A * T_k ** 3 * seg.length_m

        # electrical heating derivative
        alpha = 0.00393
        # electrical heating derivative (per-segment)
        # base copper loss: P_gen_seg = P_gen_per_m(T) * seg.length
        dPgen_base_dT = (
                state.P_gen_W_per_m * (ALPHA_CU / (1.0 + ALPHA_CU * (T - 20.0))) * seg.length_m
        )

        # joint/contact loss derivative (per-segment)
        # P_joint = I_bar^2 * R_extra_20 * (1 + ALPHA*(T-20))
        # dP_joint/dT = I_bar^2 * R_extra_20 * ALPHA
        I_bar = therm.I_total_A / max(1, int(geom.bars_in_parallel))
        R_extra_20_seg = seg.extra_R20_ohm_per_m * seg.length_m  # Ω at 20°C for that segment
        dPgen_joint_dT = (I_bar ** 2) * R_extra_20_seg * ALPHA_CU

        dPgen_dT = dPgen_base_dT + dPgen_joint_dT

        # -------------------------------------------------
        # Tridiagonal coefficients (handle boundary nodes)
        # -------------------------------------------------

        if i == 0:
            # first segment → only conducts to the right
            a[i] = 0
            c[i] = cond
            b[i] = -cond - dPout_dT + dPgen_dT

        elif i == n - 1:
            # last segment → only conducts to the left
            a[i] = cond
            c[i] = 0
            b[i] = -cond - dPout_dT + dPgen_dT

        else:
            # interior segment → conducts both directions
            a[i] = cond
            c[i] = cond
            b[i] = -2 * cond - dPout_dT + dPgen_dT

    return a,b,c
# ============================================================
# MAIN SOLVER
# ============================================================

def solve_busbar_temperature(
    bus: BusbarSpec,
    air_temp_C: float,
    max_iter: int = 40,
    tol: float = 1e-3,
):
    """
    Single unified solver.

    Always builds segments and solves the coupled thermal network.
    If no joints exist, segments simply have uniform resistance.
    """

    segments = build_busbar_segments(bus)

    n = len(segments)

    segment_geoms = [
        BusbarGeometry(
            name=bus.name,
            width_m=seg.width_m,
            thickness_m=seg.thickness_m,
            L_char_m=bus.L_char_m,
            length_m=seg.length_m,
            bars_in_parallel=bus.bars_in_parallel,
            face_to_face_dim=bus.face_to_face_dim,
            convection_mode=bus.convection_mode,
        )
        for seg in segments
    ]

    T = np.full(n, air_temp_C + 40.0)

    # Pre-build thermal inputs (same for all segments)
    therm = BusbarThermalInputs(
        I_total_A=bus.I_total_A,
        eps_bus=bus.eps_bus,
        eps_env=bus.eps_env,
        v_mps=bus.v_mps,
        S_ac=bus.S_ac,
    )

    trace = []
    # --------------------------------------------------------
    # Residual function
    # --------------------------------------------------------

    def residual(T_vec):

        f = np.zeros(n)

        for i, seg in enumerate(segments):

            geom = segment_geoms[i]

            state = compute_busbar_physics(
                geom=geom,
                therm=therm,
                T_bus_C=T_vec[i],
                T_air_C=air_temp_C,
            )

            # convert per-m → per-segment
            P_gen = state.P_gen_W_per_m * seg.length_m
            P_conv = state.P_conv_W_per_m * seg.length_m
            P_rad = state.P_rad_W_per_m * seg.length_m

            # joint resistance contribution
            if seg.extra_R20_ohm_per_m > 0:
                I_bar = therm.I_total_A / max(1, geom.bars_in_parallel)

                R_extra = seg.extra_R20_ohm_per_m * (1 + ALPHA_CU * (T_vec[i] - 20)) * seg.length_m

                P_gen += I_bar**2 * R_extra

            P_out = P_conv + P_rad

            # axial copper conduction
            k_cu = 400.0
            A = seg.width_m * seg.thickness_m
            dx = seg.length_m

            conduction = 0.0

            if i > 0:
                conduction += k_cu * A * (T_vec[i - 1] - T_vec[i]) / dx

            if i < n - 1:
                conduction += k_cu * A * (T_vec[i + 1] - T_vec[i]) / dx

            f[i] = P_gen - P_out + conduction

        return f

    # --------------------------------------------------------
    # Newton solve
    # --------------------------------------------------------

    for k in range(max_iter):

        f = residual(T)

        trace.append(T.copy())

        if np.max(np.abs(f)) < tol:
            break

        a, b, c = build_tridiagonal_jacobian(
            T, segments, segment_geoms, therm, air_temp_C
        )

        dT = solve_tridiagonal(a, b, c, -f)

        # ---- instrumentation ----
        max_f = float(np.max(np.abs(f)))
        max_step = float(np.max(np.abs(dT)))
        print(f"[iter {k:02d}] max|f|={max_f:.3e}  max|dT|={max_step:.3e}  "
              f"Tmin={float(np.min(T)):.2f}  Tmax={float(np.max(T)):.2f}")

        # clamp Newton step (prevents explosions)
        dT = np.clip(dT, -25.0, 25.0)

        # apply damping
        T += 0.7 * dT

        # prevent unphysical temps below ambient (optional but helpful)
        T = np.maximum(T, air_temp_C)

        # debug a few nodes: start, jointish, end
        idxs = [0, n // 2, n - 1]
        for ii in idxs:
            if 0 <= ii < n:
                print(f"   node {ii:02d}: T={T[ii]:.2f}  f={f[ii]:.3e}  "
                      f"a={a[ii]:.3e} b={b[ii]:.3e} c={c[ii]:.3e}  "
                      f"Rextra/m={segments[ii].extra_R20_ohm_per_m:.3e}")

    P_total_loss = 0.0

    for i, seg in enumerate(segments):

        geom = segment_geoms[i]

        state = compute_busbar_physics(
            geom=geom,
            therm=therm,
            T_bus_C=T[i],
            T_air_C=air_temp_C,
        )

        P_seg = state.P_gen_W_per_m * seg.length_m

        if seg.extra_R20_ohm_per_m > 0:
            I_bar = therm.I_total_A / max(1, geom.bars_in_parallel)
            R_extra = seg.extra_R20_ohm_per_m * (1 + ALPHA_CU * (T[i] - 20)) * seg.length_m
            P_seg += I_bar ** 2 * R_extra

        P_total_loss += P_seg

    # --------------------------------------------------------
    # Output summary
    # --------------------------------------------------------


    T_max = float(np.max(T))

    geom_final = from_busbarspec_mm(bus)

    physics_final = compute_busbar_physics(
        geom=geom_final,
        therm=therm,
        T_bus_C=T_max,
        T_air_C=air_temp_C,
    )

    return BusbarSolveResult(
        T_bus_C=T_max,
        physics=physics_final,
        converged=True,
        method="vector_newton",
        iterations=k + 1,
        trace=trace,
        T_profile=T,
        P_total_loss_W=P_total_loss
    )