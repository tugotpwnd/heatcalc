from dataclasses import dataclass
from collections import defaultdict


@dataclass
class BusScheduleRow:
    bus_id: int
    width_mm: float
    thickness_mm: float
    bars: int
    length_m: float

    I_max_A: float
    T_max_C: float
    T_min_C: float
    P_total_W: float

    segment_count: int
    edge_ids: list[int] = None

@dataclass
class JointScheduleRow:
    joint_id: int
    bus_id: int
    graph_edge_id: int
    owner_tier: object | None

    I_A: float
    T_C: float
    P_W: float

    segment_count: int

def build_bus_schedule(graph, thermal_result):
    """
    Builds a per-bus schedule from ThermalSolveResult.

    Groups segmented solver results back to physical runs (collinear edges of same tier/geom).
    """

    # Group by physical_run_id if available, else by edge_id
    run_to_items = defaultdict(list)

    for er in thermal_result.edge_results:
        if er.is_joint:
            continue
        edge = graph.edges.get(er.edge_id)
        if edge is None:
            continue

        # Prefer physical_run_id for grouping collinear segments
        run_id = getattr(edge, "physical_run_id", None)
        if run_id is None:
            run_id = f"e{er.edge_id}"

        run_to_items[run_id].append((edge, er))

    rows = []

    for run_id, items in run_to_items.items():
        edges = [it[0] for it in items]
        results = [it[1] for it in items]

        # Representative geometry from first edge
        first = edges[0]

        temps_max = [r.T_C for r in results]
        temps_min = [r.T_min_C for r in results]
        currents = [r.I_A for r in results]
        losses = [r.P_gen_W for r in results]

        # bus_id for display - if it's a physical run ID, use it,
        # else try to parse the edge ID back.
        display_id = run_id if isinstance(run_id, int) else int(str(run_id)[1:])

        rows.append(
            BusScheduleRow(
                bus_id=display_id,
                width_mm=float(first.width_mm),
                thickness_mm=float(first.thickness_mm),
                bars=int(first.bars_in_parallel),
                length_m=float(sum(e.length_m for e in edges)),

                I_max_A=max(currents) if currents else 0.0,
                T_max_C=max(temps_max) if temps_max else 0.0,
                T_min_C=min(temps_min) if temps_min else 0.0,
                P_total_W=sum(losses),

                segment_count=len(results),
                edge_ids=[e.id for e in edges],
            )
        )

    # sort by bus_id for consistent report order
    rows.sort(key=lambda x: x.bus_id)
    return rows

def build_joint_schedule(graph, thermal_result):
    """
    Extract physical joint results.

    Uses graph.joins as the source of truth, rather than every thermal edge
    marked is_joint. This prevents solver edge IDs being presented as physical
    joint IDs.
    """

    result_by_edge_id = {
        er.edge_id: er
        for er in thermal_result.edge_results
        if er.is_joint
    }

    rows = []

    for idx, j in enumerate(graph.joins, start=1):

        er = result_by_edge_id.get(j.edge_id)
        if er is None:
            continue
        er.joint_number = idx

        ui_join = getattr(j, "ui_item", None)
        if ui_join is not None and hasattr(ui_join, "set_joint_number"):
            ui_join.set_joint_number(idx)

        host_bus_id = getattr(
            j.spec,
            "_host_edge_id_before_joint_split",
            j.edge_id,
        )
        owner_tier = getattr(j, "owner_tier", None)
        if owner_tier is None:
            edge = graph.edges.get(j.edge_id)
            owner_tier = getattr(edge, "tier", None) if edge is not None else None

        rows.append(
            JointScheduleRow(
                joint_id=idx,
                bus_id=host_bus_id,
                graph_edge_id=j.edge_id,
                owner_tier=owner_tier,

                I_A=float(er.I_A),
                T_C=float(er.T_C),
                P_W=float(er.P_gen_W),

                segment_count=1,
            )
        )

    return rows
