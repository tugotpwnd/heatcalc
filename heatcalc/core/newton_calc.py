from __future__ import annotations

from dataclasses import dataclass

from heatcalc.core.busbar_network_solver_ss import solve_busbar_network
from heatcalc.core.iec60890_calc import calc_tier_iec60890
from heatcalc.core.busbar_geometry import from_busbarspec_mm
from heatcalc.core.busbar_physics import BusbarThermalInputs
from heatcalc.core.busbar_solver import solve_busbar_temperature


def _select_busbar_ambient(result_60890: dict, which: str) -> float:
    """
    Choose which enclosure air temperature drives busbar convection/radiation.

    Supported:
      "mid", "top", "075"
    """
    w = (which or "top").lower().strip()
    if w == "mid":
        return float(result_60890["T_mid"])
    if w == "top":
        return float(result_60890["T_top"])
    if w in ("075", "t075", "0.75", "t_075"):
        t = result_60890.get("T_075", None)
        return float(t) if t is not None else float(result_60890["T_top"])
    raise ValueError("use_air_temp must be one of: 'mid', 'top', '075'")


def _compute_busbar_loss_W(
    *,
    P_gen_W_per_m: float,
    length_m: float,
) -> float:
    """
    Total electrical loss in W for the bar arrangement = (W/m) * length.
    """
    return float(P_gen_W_per_m) * float(length_m)


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
    debug: bool = False,
):
    """
    Fully coupled enclosure ↔ busbar solver.

    Algorithm
    ---------
    Iterate until convergence:
      1) Run IEC 60890 enclosure model with total heat = base_heat + busbar_heat
      2) Pick busbar ambient (top/mid/0.75H)
      3) Solve busbar temperature(s) via steady-state heat balance
      4) Compute busbar electrical losses based on solved T
      5) Update total heat and repeat

    Returns a dict based on calc_tier_iec60890 output, plus:
      - result["busbars"] : list of per-busbar detailed results
      - result["coupling"]: convergence + iteration history
    """

    # No busbars -> just run enclosure calc
    if not getattr(tier, "busbars", None):
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

    P_base = float(getattr(tier, "total_heat_w", 0.0))
    P_bus = 0.0
    Ta_prev = float(ambient_C)

    history = []

    last_bus_results = []

    # --------------------------------------------------
    # Validate busbar specifications once
    # --------------------------------------------------
    for b in tier.busbars:
        b.validate()

    for k in range(max_iter):
        P_total = P_base + P_bus

        # 1) Enclosure
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

        # Ensure these are visible in the returned result for reporting
        res["ambient_C"] = float(ambient_C)

        # 2) Solve busbars with selected enclosure air temperature for each bus
        P_bus_new = 0.0
        bus_results = []

        for b in tier.busbars:
            # --------------------------------------------------
            # Convert spec → geometry + thermal inputs
            # --------------------------------------------------
            geom = from_busbarspec_mm(b)

            therm = BusbarThermalInputs(
                I_total_A=b.I_total_A,
                eps_bus=b.eps_bus,
                eps_env=b.eps_env,
                v_mps=b.v_mps,
                S_ac=b.S_ac,
            )

            # --------------------------------------------------
            # Select enclosure air temperature
            # --------------------------------------------------
            Ta_bus = _select_busbar_ambient(res, b.use_air_temp)

            # --------------------------------------------------
            # Solve busbar temperature
            # --------------------------------------------------
            if b.branches:
                solve = solve_busbar_network(
                    bus=b,
                    air_temp_C=Ta_bus
                )
            else:
                solve = solve_busbar_temperature(
                    bus=b,
                    air_temp_C=Ta_bus
                )

            # The canonical physics at solution
            phys = solve.physics

            # Electrical loss W = (P_gen per m) * length
            P_loss_W = solve.P_total_loss_W
            P_bus_new += P_loss_W

            bus_results.append(
                {
                    # identity
                    "name": geom.name,

                    # inputs
                    "I_total_A": float(b.I_total_A),
                    "bars_in_parallel": int(geom.bars_in_parallel),
                    "I_bar_A": float(phys.I_bar_A),
                    "use_air_temp": getattr(b, "use_air_temp", "top"),

                    # geometry (retain mm for UI readability)
                    "width_mm": float(b.width_mm),
                    "thickness_mm": float(b.thickness_mm),
                    "L_char_mm": float(b.L_char_mm),
                    "length_m": float(geom.length_m),

                    # layout
                    "convection_mode": geom.convection_mode,
                    "face_to_face_dim": geom.face_to_face_dim,

                    # solved temps
                    "T_air_C": float(phys.T_air_C),
                    "T_bus_C": float(phys.T_bus_C),

                    # losses (total for bar arrangement)
                    "P_loss_W": float(P_loss_W),

                    # canonical physics (per m, total arrangement)
                    "R20_ohm_per_m": float(phys.R20_ohm_per_m),
                    "R_T_ohm_per_m": float(phys.R_T_ohm_per_m),
                    "P_gen_W_per_m": float(phys.P_gen_W_per_m),
                    "P_total_loss_W": float(P_loss_W),
                    "P_conv_W_per_m": float(phys.P_conv_W_per_m),
                    "P_rad_W_per_m": float(phys.P_rad_W_per_m),
                    "W_conv_W_m2": float(phys.W_conv_W_m2),
                    "W_rad_W_m2": float(phys.W_rad_W_m2),
                    "As_conv_m2_per_m": float(phys.As_conv_m2_per_m),
                    "As_rad_raw_m2_per_m": float(phys.As_rad_raw_m2_per_m),
                    "As_rad_eff_m2_per_m": float(phys.As_rad_eff_m2_per_m),
                    "rad_blockage_frac": float(phys.rad_blockage_frac),

                    # solver info
                    "solver_converged": bool(solve.converged),
                    "solver_method": solve.method,
                    "solver_iterations": int(solve.iterations),

                    # Optional deep trace
                    "trace": [
                        {
                            "T_bus_C": s.T_bus_C,
                            "T_air_C": s.T_air_C,
                            "P_gen_W_per_m": s.P_gen_W_per_m,
                            "P_conv_W_per_m": s.P_conv_W_per_m,
                            "P_rad_W_per_m": s.P_rad_W_per_m,
                            "f_W_per_m": s.f_W_per_m,
                        }
                        for s in solve.trace
                    ]
                    if debug
                    else None,
                }
            )

        last_bus_results = bus_results

        # Pick a single enclosure air temp to track in coupling history (use top for conservatism)
        Ta_top = float(res["T_top"])

        history.append(
            {
                "iter": k + 1,
                "P_total_W": float(P_total),
                "P_base_W": float(P_base),
                "P_bus_W": float(P_bus_new),
                "T_air_top_C": Ta_top,
                "max_busbar_T_C": max(b["T_bus_C"] for b in bus_results),
            }
        )

        # 3) Convergence check: enclosure air & bus heat
        if abs(Ta_top - Ta_prev) < tol_T and abs(P_bus_new - P_bus) < tol_P:
            res["busbars"] = last_bus_results
            res["coupling"] = {"converged": True, "iterations": k + 1, "history": history}
            return res

        Ta_prev = Ta_top
        P_bus = P_bus_new

    # Not converged
    res["busbars"] = last_bus_results
    res["coupling"] = {"converged": False, "iterations": max_iter, "history": history}
    return res