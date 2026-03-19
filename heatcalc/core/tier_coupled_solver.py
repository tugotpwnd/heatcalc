# heatcalc/core/tier_coupled_solver.py
from __future__ import annotations

from dataclasses import dataclass

from heatcalc.core.iec60890_calc import calc_tier_iec60890
from heatcalc.core.bus_thermal_solver import solve_thermal


def _select_busbar_ambient(result_60890: dict, which: str) -> float:
    w = (which or "top").lower().strip()
    if w == "mid":
        return float(result_60890["T_mid"])
    if w == "top":
        return float(result_60890["T_top"])
    if w in ("075", "t075", "0.75", "t_075"):
        t = result_60890.get("T_075", None)
        return float(t) if t is not None else float(result_60890["T_top"])
    raise ValueError("use_air_temp must be one of: 'mid', 'top', '075'")


@dataclass
class CoupledGraphResult:
    name: str
    air_ref: str
    air_temp_C: float
    total_loss_W: float
    max_T_C: float
    min_T_C: float
    converged: bool
    iterations: int
    solve: object  # ThermalSolveResult


def calc_tier_iec60890_coupled(
    *,
    tier,
    tiers,
    graphs,   # list of thermal graph objects belonging to this tier
    wall_mounted: bool,
    inlet_area_cm2: float,
    ambient_C: float,
    altitude_m: float,
    ip_rating_n: int,
    vent_test_area_cm2: float | None = None,
    solar_delta_K: float = 0.0,
    max_iter: int = 30,
    tol_T: float = 0.05,
    tol_P: float = 0.5,
    relax: float = 0.5,
    debug: bool = False,
):
    """
    Coupled tier-level enclosure ↔ graph-busbar solve.

    Outer loop:
      IEC 60890 tier air -> graph thermal solve -> updated graph watts -> IEC 60890 ...
    """

    P_base = float(getattr(tier, "total_heat_w", 0.0))

    if not graphs:
        res = calc_tier_iec60890(
            tier=tier,
            tiers=tiers,
            wall_mounted=wall_mounted,
            inlet_area_cm2=inlet_area_cm2,
            ambient_C=ambient_C,
            altitude_m=altitude_m,
            ip_rating_n=ip_rating_n,
            vent_test_area_cm2=vent_test_area_cm2,
            solar_delta_K=solar_delta_K,
        )
        res["coupling"] = {
            "converged": True,
            "iterations": 0,
            "history": [],
            "P_base_W": P_base,
            "P_bus_W": 0.0,
        }
        res["graphs"] = []
        return res

    P_bus = 0.0
    Ta_prev = float(ambient_C)
    history = []
    last_graph_results = []
    last_res = None

    for k in range(max_iter):
        P_total = P_base + P_bus

        res = calc_tier_iec60890(
            tier=tier,
            tiers=tiers,
            wall_mounted=wall_mounted,
            inlet_area_cm2=inlet_area_cm2,
            ambient_C=ambient_C,
            altitude_m=altitude_m,
            ip_rating_n=ip_rating_n,
            vent_test_area_cm2=vent_test_area_cm2,
            solar_delta_K=solar_delta_K,
            P_override_W=P_total,
        )
        res["ambient_C"] = float(ambient_C)
        last_res = res

        graph_results = []
        P_bus_raw = 0.0

        for idx, graph in enumerate(graphs):
            air_ref = getattr(graph, "use_air_temp", "top")
            Ta_graph = _select_busbar_ambient(res, air_ref)

            sol = solve_thermal(
                graph=graph,
                air_temp_C=Ta_graph,
                debug=debug,
            )

            P_bus_raw += sol.total_loss_W

            graph_results.append(
                CoupledGraphResult(
                    name=getattr(graph, "name", f"graph-{idx}"),
                    air_ref=air_ref,
                    air_temp_C=float(Ta_graph),
                    total_loss_W=float(sol.total_loss_W),
                    max_T_C=float(sol.max_T_C),
                    min_T_C=float(sol.min_T_C),
                    converged=bool(sol.converged),
                    iterations=int(sol.iterations),
                    solve=sol,
                )
            )

        # relaxed outer update
        P_bus_new = (1.0 - relax) * P_bus + relax * P_bus_raw

        Ta_top = float(res["T_top"])
        max_graph_T = max(g.max_T_C for g in graph_results) if graph_results else ambient_C

        history.append(
            {
                "iter": k + 1,
                "P_base_W": float(P_base),
                "P_bus_raw_W": float(P_bus_raw),
                "P_bus_relaxed_W": float(P_bus_new),
                "P_total_W": float(P_base + P_bus_new),
                "T_air_mid_C": float(res["T_mid"]),
                "T_air_top_C": float(res["T_top"]),
                "T_air_075_C": float(res.get("T_075") or res.get("T_top", 0.0)),
                "max_graph_T_C": float(max_graph_T),
            }
        )

        last_graph_results = graph_results

        if abs(Ta_top - Ta_prev) < tol_T and abs(P_bus_new - P_bus) < tol_P:
            last_res["graphs"] = _serialise_graph_results(last_graph_results)
            last_res["coupling"] = {
                "converged": True,
                "iterations": k + 1,
                "history": history,
                "P_base_W": float(P_base),
                "P_bus_W": float(P_bus_new),
            }

            print("\n" + "=" * 60)
            print(f"[COUPLED SOLVER] Tier: {getattr(tier, 'name', id(tier))}")
            print(f"Iterations: {k + 1}")
            print(f"P_base = {P_base:.2f} W | P_bus = {P_bus_new:.2f} W")
            print("-" * 60)

            for g in last_res.get("graphs", []):
                print(f"\n[GRAPH] {g['name']}")
                print(f"  Air Ref: {g['use_air_temp']}")
                print(f"  Air Temp: {g['T_air_C']:.2f} °C")
                print(f"  Total Loss: {g['P_loss_W']:.2f} W")
                print(f"  Max/Min Temp: {g['max_T_C']:.2f} / {g['min_T_C']:.2f} °C")
                print(f"  Converged: {g['solver_converged']} ({g['solver_iterations']} iters)")

                print("  --- EDGES ---")
                for e in g["edges"]:
                    print(
                        f"    [{e['kind'].upper()}] "
                        f"id={e['edge_id']} | "
                        f"I={e['I_A']:.1f}A | "
                        f"T={e['T_C']:.1f}°C | "
                        f"P={e['P_gen_W']:.2f}W "
                        f"(conv={e['P_conv_W']:.2f}, rad={e['P_rad_W']:.2f}, cond={e['P_cond_W']:.2f})"
                    )

            print("=" * 60 + "\n")
            return last_res

        Ta_prev = Ta_top
        P_bus = P_bus_new

    last_res["graphs"] = _serialise_graph_results(last_graph_results)
    last_res["coupling"] = {
        "converged": False,
        "iterations": max_iter,
        "history": history,
        "P_base_W": float(P_base),
        "P_bus_W": float(P_bus),
    }

    return last_res


def _serialise_graph_results(graph_results):
    out = []
    for g in graph_results:
        out.append(
            {
                "name": g.name,
                "use_air_temp": g.air_ref,
                "T_air_C": g.air_temp_C,
                "P_loss_W": g.total_loss_W,
                "max_T_C": g.max_T_C,
                "min_T_C": g.min_T_C,
                "solver_converged": g.converged,
                "solver_iterations": g.iterations,
                "edges": [
                    {
                        "edge_id": e.edge_id,
                        "T_C": e.T_C,
                        "I_A": e.I_A,
                        "length_m": e.length_m,
                        "is_joint": bool(e.is_joint),
                        "kind": "joint" if e.is_joint else "bus",
                        "P_gen_W": e.P_gen_W,
                        "P_conv_W": e.P_conv_W,
                        "P_rad_W": e.P_rad_W,
                        "P_cond_W": e.P_cond_W,
                        "residual_W": e.residual_W,
                    }
                    for e in g.solve.edge_results
                ],
            }
        )
    return out