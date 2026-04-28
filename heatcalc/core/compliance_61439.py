from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Sequence, Tuple

from heatcalc.core.enclosure_thermal_solver import estimate_enclosure_surface_temp_C, estimate_top_side_surface_temp_C

def evaluate_derating(component: Any, T_internal: float) -> float | None:
    """
    Evaluate available current at operating temperature for a component.
    Based on rated_current_A, derating_temp_start_C and derating_function (expression of x).
    """
    rated = getattr(component, "rated_current_A", None)
    func_str = getattr(component, "derating_function", None)
    temp_start = getattr(component, "derating_temp_start_C", None)


    if rated is None:
        return None

    if func_str is None:
        # Fallback to 80% of rated current if no derating function is provided
        return rated * 0.8

    if temp_start is not None and T_internal <= temp_start:
        factor = 1.0
    else:
        try:
            # x = internal operating temperature (°C)
            # Evaluate expression safely
            factor = eval(func_str, {"__builtins__": {}}, {"x": T_internal, "math": math})
            if not isinstance(factor, (int, float)):
                # If function fails, fallback to 80%
                return rated * 0.8
        except Exception:
            # If evaluation fails, fallback to 80%
            return rated * 0.8

    # Clamp: 0.0 to 1.0
    factor = max(0.0, min(1.0, float(factor)))

    I_80 = rated * 0.8
    I_derated = rated * factor

    return min(I_80, I_derated)

@dataclass
class TerminalWorkingRow:
    name: str
    current_A: float
    source_bus: str
    temperature_C: float
    limit_C: float
    compliant: bool


@dataclass
class ComplianceResult:
    tier_id: str

    built_in_ok: bool
    built_in_max_T: float
    built_in_limit_C: float | None = None

    terminals_ok: bool = True
    terminals_max_T: float | None = None
    terminal_rows: List[TerminalWorkingRow] | None = None

    enclosure_ok: bool = True
    enclosure_surface_T_top_side: float = 0.0
    enclosure_surface_T_hotspot: float = 0.0
    enclosure_limit_C: float | None = None

    busbar_ok: bool = True
    busbar_max_T: float = 0.0
    busbar_limit_C: float | None = None

    notes: List[str] | None = None

    debug: Dict[str, Any] | None = None


# -------------------------------------------------
# MAIN ENTRY POINT
# -------------------------------------------------
def evaluate_tier_compliance(
    tier,
    global_sol,
    tier_res,
    ambient_C,
    tier_edges,
    graph,
    debug: bool = False
):
    dbg = {} if debug else None

    from heatcalc.core.enclosure_thermal_solver import (
        estimate_enclosure_surface_temps_with_hotspot
    )

    tier_name = getattr(tier, "name", f"{id(tier)}")
    notes: List[str] = []

    # -----------------------------------------
    # Extract edges for this tier
    # -----------------------------------------
    edge_ids = {e.id for e in tier_edges.get(tier, [])}

    tier_edge_results = [
        er for er in global_sol.edge_results
        if er.edge_id in edge_ids
    ]

    if not tier_edge_results:
        dbg = None
        if debug:
            dbg = {
                "reason": "no_edges",
                "tier": tier_name,
                "ambient_C": float(ambient_C),
            }

        return ComplianceResult(
            tier_id=tier_name,

            built_in_ok=True,
            built_in_max_T=ambient_C,

            terminals_ok=True,
            terminals_max_T=None,

            enclosure_ok=True,
            enclosure_surface_T_top_side=ambient_C,
            enclosure_surface_T_hotspot=ambient_C,

            busbar_ok=True,
            busbar_max_T=ambient_C,

            notes=["No thermal elements in tier"],

            debug=dbg
        )
    # -----------------------------------------
    # BUSBARS
    # -----------------------------------------
    bus_edges = [e for e in tier_edge_results if not e.is_joint]

    bus_temps = [float(e.T_C) for e in bus_edges]

    busbar_max_T = max(bus_temps) if bus_temps else ambient_C
    busbar_limit = 140.0
    busbar_ok = busbar_max_T <= busbar_limit

    if not busbar_ok:
        notes.append(f"Busbar exceeds 140°C ({busbar_max_T:.1f}°C)")

    # -----------------------------------------
    # TERMINALS
    # -----------------------------------------
    tier_node_ids = set()

    for e in tier_edges.get(tier, []):
        tier_node_ids.add(e.u)
        tier_node_ids.add(e.v)

    has_loads = any(
        load.node in tier_node_ids
        for load in graph.loads
    )

    terminals_max_T = None
    terminals_ok = True
    terminal_rows: List[TerminalWorkingRow] = []

    if has_loads and bus_temps:
        from collections import defaultdict

        edge_temp_map = defaultdict(list)
        for er in global_sol.edge_results:
            edge_temp_map[er.edge_id].append(float(er.T_C))

        for load in graph.loads:
            if load.node not in tier_node_ids:
                continue

            connected_edges = [
                e for e in tier_edges.get(tier, [])
                if e.u == load.node or e.v == load.node
            ]

            if not connected_edges:
                continue

            edge = connected_edges[0]

            T_bus_local = max(edge_temp_map.get(edge.id, [ambient_C]))
            I = float(getattr(load, "I_A", 0.0))
            T_limit = float(getattr(load, "max_terminal_temp_c", 105.0))

            load_name = (
                getattr(load, "tag", None)
                or getattr(load, "name", None)
                or f"Node {load.node}"
            )

            source_bus = f"Bus {edge.id}"

            ok = T_bus_local <= T_limit
            if not ok:
                terminals_ok = False
                notes.append(
                    f"Terminal exceeds {T_limit:.1f}°C ({T_bus_local:.1f}°C)"
                )

            terminal_rows.append(
                TerminalWorkingRow(
                    name=str(load_name),
                    current_A=I,
                    source_bus=source_bus,
                    temperature_C=T_bus_local,
                    limit_C=T_limit,
                    compliant=ok,
                )
            )

        if terminal_rows:
            terminals_max_T = max(r.temperature_C for r in terminal_rows)

    # -----------------------------------------
    # BUILT-IN COMPONENTS
    # -----------------------------------------
    T_top = float(tier_res.get("T_top", ambient_C))
    limit_C_tier = float(tier_res.get("limit_C", ambient_C + 70.0))

    # Prefer tier's own effective limit (which typically min(component limits))
    if hasattr(tier, "effective_max_temp_C") and tier.effective_max_temp_C is not None:
        try:
            built_in_limit = float(tier.effective_max_temp_C())
        except TypeError:
            # Case where it's an attribute (e.g. from TierRow)
            built_in_limit = float(tier.effective_max_temp_C)
    elif getattr(tier, "effective_max_temp_C", None) is not None:
        built_in_limit = float(tier.effective_max_temp_C)
    else:
        # Fallback if tier is not a TierItem object (e.g. from a dict-based adapter)
        component_limits = []
        for comp in getattr(tier, "components", []):
            tmax = getattr(comp, "max_temp_C", None)
            if tmax:
                component_limits.append(float(tmax))

        if component_limits:
            built_in_limit = min(component_limits)
        else:
            # Fall back to manual tier limit or default
            built_in_limit = limit_C_tier

    built_in_max_T = T_top
    built_in_ok = built_in_max_T <= built_in_limit

    if not built_in_ok:
        notes.append(
            f"Built-in exceeds limit ({built_in_max_T:.1f}°C > {built_in_limit:.1f}°C)"
        )

    # -----------------------------------------
    # ENCLOSURE (bulk + hotspot)
    # -----------------------------------------
    face_height_m = float(getattr(tier, "height_mm", 2000.0)) / 1000.0

    # Enclosure-scale characteristic side-wall cavity width.
    # Use ~1/3 enclosure depth to represent the average distance from the hot
    # upper air core to the side wall.
    depth_m = float(getattr(tier, "depth_mm", 100.0)) / 1000.0
    internal_gap_m = max(0.3 * depth_m, 1e-6)

    enclosure_surface_T_top_side = estimate_top_side_surface_temp_C(
        T_top_C=T_top,
        T_amb_C=ambient_C,
        internal_gap_m=internal_gap_m,
        face_height_m=face_height_m,
    )

    # --- hotspot (new)
    if bus_edges:
        worst_bus = max(bus_edges, key=lambda e: e.T_C)
        edge_obj = graph.edges.get(worst_bus.edge_id)

        if edge_obj:
            length = float(edge_obj.length_m)

            orientation = getattr(edge_obj, "orientation_to_wall", "width")
            gap_to_wall_mm = float(getattr(edge_obj, "gap_to_wall_mm", 50.0))
            bars = int(getattr(edge_obj, "bars_in_parallel", 1))
            face_to_face = getattr(edge_obj, "face_to_face_dim", "thickness")
            face_height_m = float(getattr(tier, "height_mm", 2000.0)) / 1000.0

            # Infer orientation for hotspot convection mode
            u_p = graph.nodes[edge_obj.u].p
            v_p = graph.nodes[edge_obj.v].p
            dx = abs(u_p.x() - v_p.x())
            dy = abs(u_p.y() - v_p.y())
            bus_run_orientation = "vertical" if dy > dx else "horizontal"

            est = estimate_enclosure_surface_temps_with_hotspot(
                T_bus_C=float(worst_bus.T_C),
                T_air_in_C=T_top,
                T_amb_C=ambient_C,
                bus_length_m=length,
                bar_width_m=edge_obj.width_mm / 1000.0,
                bar_thickness_m=edge_obj.thickness_mm / 1000.0,
                bars_per_phase=bars,
                gap_to_wall_mm=gap_to_wall_mm,
                orientation_to_wall=orientation,
                face_to_face_dim=face_to_face,
                enclosure_depth_m=depth_m,
                face_height_m=face_height_m,
                # Emissivity of the busbar surface for direct radiation exchange to the nearby
                # enclosure wall patch.
                #
                # This is used only in the enclosure hotspot model, not in the bus self-cooling
                # balance. It controls how strongly the hottest busbar can radiate heat toward
                # the inner enclosure wall facing it.
                # -------------------------------------------------------------------------------
                eps_bus_to_wall=0.1,
                # -------------------------------------------------------------------------------
                # Lower eps_bus_to_wall -> weaker radiative coupling to the wall -> lower wall hotspot
                # Higher eps_bus_to_wall -> stronger radiative coupling to the wall -> higher wall hotspot
                #
                # A value of 0.1 is appropriate for bright / shiny tinned or bare copper.
                # Use higher values only if the bus surface is expected to be oxidised, dulled,
                # coated, or intentionally conservative wall-heating assumptions are desired.
                # Analytical effect of eps_bus_to_wall:
                #
                # The hotspot model uses grey-body radiation exchange between the busbar and the
                # wall patch:
                #   Q_rad_bus_to_wall ∝ eps_rel * view_factor * (T_bus^4 - T_wall^4)
                #
                # where eps_rel is based on:
                #   - eps_bus_to_wall   : bus surface emissivity
                #   - eps_wall_inner    : inner wall emissivity
                #
                # Increasing eps_bus_to_wall increases radiative heat transfer from the bus to
                # the wall patch, which increases local wall heating and can increase the outer
                # wall hotspot temperature. Decreasing eps_bus_to_wall reduces this coupling.
                #
                # This parameter affects enclosure hotspot severity, not the electrical loss in
                # the busbar itself.

                debug=False
            )

            enclosure_surface_T_hotspot = est.T_outer_hotspot_C
        else:
            enclosure_surface_T_hotspot = enclosure_surface_T_top_side
    else:
        enclosure_surface_T_hotspot = enclosure_surface_T_top_side

    # --- compliance uses WORST (correct per 61439 intent)
    enclosure_surface_T = max(
        enclosure_surface_T_top_side,
        enclosure_surface_T_hotspot
    )

    enclosure_limit = ambient_C + 30.0
    enclosure_ok = enclosure_surface_T <= enclosure_limit

    if not enclosure_ok:
        notes.append(
            f"Enclosure exceeds limit ({enclosure_surface_T:.1f}°C)"
        )


    # -----------------------------------------
    # RETURN
    # -----------------------------------------
    return ComplianceResult(
        tier_id=tier_name,

        built_in_ok=built_in_ok,
        built_in_max_T=built_in_max_T,
        built_in_limit_C=built_in_limit,

        terminals_ok=terminals_ok,
        terminals_max_T=terminals_max_T,
        terminal_rows=terminal_rows,

        enclosure_ok=enclosure_ok,
        enclosure_surface_T_top_side=enclosure_surface_T_top_side,
        enclosure_surface_T_hotspot=enclosure_surface_T_hotspot,
        enclosure_limit_C=enclosure_limit,

        busbar_ok=busbar_ok,
        busbar_max_T=busbar_max_T,
        busbar_limit_C=busbar_limit,

        notes=notes,

        debug=dbg
    )