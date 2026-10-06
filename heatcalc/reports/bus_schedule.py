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
    Build a per-parent-busbar schedule from ThermalSolveResult.

    The thermal solver may split one drawn BusLineItem into many graph edges
    due to:
      - thermal discretisation,
      - endpoint/intersection splitting,
      - explicit joint splitting.

    For reporting, those solver edges are grouped back to the actual parent
    BusLineItem using edge.ui_item. This means each drawn busbar appears as
    one report line item.
    """

    def edge_sort_key(edge):
        """
        Stable visual-ish ordering for bus numbering.
        Sort by upper/left position of the edge in the scene, then edge ID.
        """
        try:
            p0 = graph.nodes[edge.u].p
            p1 = graph.nodes[edge.v].p
            return (
                min(p0.y(), p1.y()),
                min(p0.x(), p1.x()),
                int(edge.id),
            )
        except Exception:
            return (0.0, 0.0, int(getattr(edge, "id", 0)))

    def group_key_for_edge(edge):
        """
        Preferred grouping:
            parent BusLineItem -> one drawn busbar.

        Fallbacks:
            physical_run_id -> legacy grouping,
            edge.id          -> final safe fallback.
        """
        ui_item = getattr(edge, "ui_item", None)

        if ui_item is not None:
            # bus_id is persistent through save/load; id(ui_item) is runtime fallback.
            parent_id = getattr(ui_item, "bus_id", None)
            if parent_id:
                return ("ui", str(parent_id))
            return ("ui_obj", id(ui_item))

        run_id = getattr(edge, "physical_run_id", None)
        if run_id is not None:
            return ("run", run_id)

        return ("edge", int(edge.id))

    group_to_items = defaultdict(list)
    group_sort_keys = {}

    for er in getattr(thermal_result, "edge_results", []) or []:
        if getattr(er, "is_joint", False):
            continue

        edge = graph.edges.get(er.edge_id)
        if edge is None:
            continue

        key = group_key_for_edge(edge)

        group_to_items[key].append((edge, er))

        if key not in group_sort_keys:
            group_sort_keys[key] = edge_sort_key(edge)
        else:
            group_sort_keys[key] = min(group_sort_keys[key], edge_sort_key(edge))

    rows = []

    ordered_keys = sorted(
        group_to_items.keys(),
        key=lambda k: group_sort_keys.get(k, (0.0, 0.0, 0)),
    )

    for display_id, key in enumerate(ordered_keys, start=1):
        items = group_to_items[key]

        edges = [edge for edge, _ in items]
        results = [er for _, er in items]

        if not edges:
            continue

        first = edges[0]

        temps_max = [
            float(getattr(r, "T_C", 0.0) or 0.0)
            for r in results
        ]

        temps_min = [
            float(getattr(r, "T_min_C", getattr(r, "T_C", 0.0)) or 0.0)
            for r in results
        ]

        currents = [
            float(getattr(r, "I_A", 0.0) or 0.0)
            for r in results
        ]

        losses = [
            float(getattr(r, "P_gen_W", 0.0) or 0.0)
            for r in results
        ]

        rows.append(
            BusScheduleRow(
                bus_id=display_id,
                width_mm=float(first.width_mm),
                thickness_mm=float(first.thickness_mm),
                bars=int(first.bars_in_parallel),
                length_m=float(sum(float(e.length_m) for e in edges)),

                I_max_A=max(currents) if currents else 0.0,
                T_max_C=max(temps_max) if temps_max else 0.0,
                T_min_C=min(temps_min) if temps_min else 0.0,
                P_total_W=sum(losses),

                segment_count=len(results),
                edge_ids=[int(e.id) for e in edges],
            )
        )

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
