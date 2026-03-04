from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from heatcalc.core.busbar_geometry import BusbarGeometry
from heatcalc.core.busbar_physics import BusbarThermalInputs, BusbarPhysicsState, compute_busbar_physics


@dataclass
class BusbarSolveResult:
    """
    Result of solving one busbar arrangement at steady state.
    """
    T_bus_C: float
    physics: BusbarPhysicsState
    converged: bool
    method: str
    iterations: int
    trace: list[BusbarPhysicsState]   # full per-iteration physics snapshots


def solve_busbar_temperature(
    *,
    geom: BusbarGeometry,
    therm: BusbarThermalInputs,
    T_air_C: float,
    T0_C: Optional[float] = None,
    tol_T: float = 0.01,
    tol_f_W_per_m: float = 1e-4,
    max_iter: int = 50,
    newton_damping: float = 1.0,     # 1.0 = full Newton, 0.5 = damped
    max_step_C: float = 50.0,        # clamp to prevent crazy Newton jumps
) -> BusbarSolveResult:
    """
    Solve f(T)=0 for steady-state busbar temperature using:
      - Newton (finite-difference derivative)
      - fallback bisection if Newton fails

    The returned trace is a list of full physics snapshots each iteration.
    """

    # Initial guess
    T = (T_air_C + 40.0) if T0_C is None else float(T0_C)

    trace: list[BusbarPhysicsState] = []

    # ---- Newton loop ----
    for k in range(max_iter):
        state = compute_busbar_physics(geom=geom, therm=therm, T_bus_C=T, T_air_C=T_air_C)
        trace.append(state)

        f = state.f_W_per_m

        # Convergence
        if abs(f) < tol_f_W_per_m:
            return BusbarSolveResult(
                T_bus_C=float(T),
                physics=state,
                converged=True,
                method="newton",
                iterations=k + 1,
                trace=trace,
            )

        # Finite difference derivative
        dT = max(0.01, 0.001 * max(abs(T), 1.0))
        state_plus = compute_busbar_physics(geom=geom, therm=therm, T_bus_C=T + dT, T_air_C=T_air_C)
        df = (state_plus.f_W_per_m - f) / dT

        if abs(df) < 1e-12:
            break  # trigger fallback

        step = (f / df)
        step = max(-max_step_C, min(max_step_C, step))  # clamp
        T_new = T - newton_damping * step

        if abs(T_new - T) < tol_T:
            # Close enough in temperature, but ensure f is not insane
            state_new = compute_busbar_physics(geom=geom, therm=therm, T_bus_C=T_new, T_air_C=T_air_C)
            trace.append(state_new)
            if abs(state_new.f_W_per_m) < tol_f_W_per_m:
                return BusbarSolveResult(
                    T_bus_C=float(T_new),
                    physics=state_new,
                    converged=True,
                    method="newton",
                    iterations=k + 1,
                    trace=trace,
                )

        T = T_new

    # ---- Fallback: bisection ----
    # Find a bracket [T_low, T_high] such that f changes sign.
    T_low = float(T_air_C)
    T_high = float(T_air_C + 300.0)

    f_low = compute_busbar_physics(geom=geom, therm=therm, T_bus_C=T_low, T_air_C=T_air_C).f_W_per_m
    f_high = compute_busbar_physics(geom=geom, therm=therm, T_bus_C=T_high, T_air_C=T_air_C).f_W_per_m

    # If no sign change, widen once (rare)
    if f_low * f_high > 0:
        T_high = float(T_air_C + 600.0)
        f_high = compute_busbar_physics(geom=geom, therm=therm, T_bus_C=T_high, T_air_C=T_air_C).f_W_per_m

    for k in range(100):
        T_mid = 0.5 * (T_low + T_high)
        state_mid = compute_busbar_physics(geom=geom, therm=therm, T_bus_C=T_mid, T_air_C=T_air_C)
        trace.append(state_mid)

        if abs(state_mid.f_W_per_m) < tol_f_W_per_m or abs(T_high - T_low) < tol_T:
            return BusbarSolveResult(
                T_bus_C=float(T_mid),
                physics=state_mid,
                converged=True,
                method="bisection",
                iterations=len(trace),
                trace=trace,
            )

        # bisection step
        f_mid = state_mid.f_W_per_m
        if f_low * f_mid < 0:
            T_high = T_mid
            f_high = f_mid
        else:
            T_low = T_mid
            f_low = f_mid

    # If we get here, it didn't converge (should be rare)
    last = trace[-1] if trace else compute_busbar_physics(geom=geom, therm=therm, T_bus_C=T, T_air_C=T_air_C)
    return BusbarSolveResult(
        T_bus_C=float(last.T_bus_C),
        physics=last,
        converged=False,
        method="bisection",
        iterations=len(trace),
        trace=trace,
    )