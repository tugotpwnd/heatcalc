from dataclasses import dataclass
from typing import List, Dict, Any

from heatcalc.core.enclosure_thermal_solver import estimate_enclosure_surface_temp_C


@dataclass
class ComplianceResult:
    tier_id: str

    built_in_ok: bool
    built_in_max_T: float

    terminals_ok: bool
    terminals_max_T: float | None

    enclosure_ok: bool
    enclosure_surface_T_bulk: float
    enclosure_surface_T_hotspot: float

    busbar_ok: bool
    busbar_max_T: float

    notes: List[str]

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
            enclosure_surface_T_bulk=ambient_C,
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

    if has_loads and bus_temps:
        terminal_temps = [t + 15.0 for t in bus_temps]

        terminals_max_T = max(terminal_temps)
        terminals_limit = 105.0

        terminals_ok = terminals_max_T <= terminals_limit

        if not terminals_ok:
            notes.append(
                f"Terminal exceeds 105°C ({terminals_max_T:.1f}°C)"
            )

    # -----------------------------------------
    # BUILT-IN COMPONENTS
    # -----------------------------------------
    T_top = float(tier_res.get("T_top", ambient_C))

    component_limits = []

    for comp in getattr(tier, "components", []):
        tmax = getattr(comp, "max_temp_C", None)
        if tmax:
            component_limits.append(float(tmax))

    built_in_limit = min(component_limits) if component_limits else ambient_C + 70.0

    built_in_max_T = T_top
    built_in_ok = built_in_max_T <= built_in_limit

    if not built_in_ok:
        notes.append(
            f"Built-in exceeds limit ({built_in_max_T:.1f}°C > {built_in_limit:.1f}°C)"
        )

    # -----------------------------------------
    # ENCLOSURE (bulk + hotspot)
    # -----------------------------------------

    # --- bulk (existing behaviour)
    enclosure_surface_T_bulk = estimate_enclosure_surface_temp_C(
        T_air_in_C=T_top,
        T_amb_C=ambient_C,
    )

    # --- hotspot (new)
    if bus_edges:
        worst_bus = max(bus_edges, key=lambda e: e.T_C)
        edge_obj = graph.edges.get(worst_bus.edge_id)

        if edge_obj:
            length = float(edge_obj.length_m)

            orientation = getattr(edge_obj, "orientation_to_wall", "width")

            if orientation == "width":
                face_width = edge_obj.width_mm / 1000.0
            else:
                face_width = edge_obj.thickness_mm / 1000.0

            bars = int(getattr(edge_obj, "bars_in_parallel", 1))

            est = estimate_enclosure_surface_temps_with_hotspot(
                T_bus_C=float(worst_bus.T_C),
                T_air_in_C=T_top,
                T_amb_C=ambient_C,
                bus_length_m=length,
                bus_face_width_m=face_width,
                bars_facing_wall=bars,
                eps_bus_to_wall=0.4,
                debug=True
            )

            enclosure_surface_T_hotspot = est.T_outer_hotspot_C
        else:
            enclosure_surface_T_hotspot = enclosure_surface_T_bulk
    else:
        enclosure_surface_T_hotspot = enclosure_surface_T_bulk

    # --- compliance uses WORST (correct per 61439 intent)
    enclosure_surface_T = max(
        enclosure_surface_T_bulk,
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

        terminals_ok=terminals_ok,
        terminals_max_T=terminals_max_T,

        enclosure_ok=enclosure_ok,
        enclosure_surface_T_bulk=enclosure_surface_T_bulk,
        enclosure_surface_T_hotspot=enclosure_surface_T_hotspot,

        busbar_ok=busbar_ok,
        busbar_max_T=busbar_max_T,

        notes=notes,

        debug=dbg
    )