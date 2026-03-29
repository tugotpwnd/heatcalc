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

@dataclass
class JointScheduleRow:
    joint_id: int
    bus_id: int

    I_A: float
    T_C: float
    P_W: float

    segment_count: int

def build_bus_schedule(graph, thermal_result):
    """
    Builds a per-bus schedule from ThermalSolveResult.

    Groups segmented solver results back to original edge_id.
    """

    grouped = defaultdict(list)

    for er in thermal_result.edge_results:
        if er.is_joint:
            continue
        grouped[er.edge_id].append(er)

    rows = []

    for edge_id, results in grouped.items():
        edge = graph.edges[edge_id]

        temps = [r.T_C for r in results]
        currents = [r.I_A for r in results]
        losses = [r.P_gen_W for r in results]

        rows.append(
            BusScheduleRow(
                bus_id=edge_id,
                width_mm=float(edge.width_mm),
                thickness_mm=float(edge.thickness_mm),
                bars=int(edge.bars_in_parallel),
                length_m=float(edge.length_m),

                I_max_A=max(currents) if currents else 0.0,
                T_max_C=max(temps) if temps else 0.0,
                T_min_C=min(temps) if temps else 0.0,
                P_total_W=sum(losses),

                segment_count=len(results),
            )
        )

    return rows

def build_joint_schedule(graph, thermal_result):
    """
    Extract joint-level thermal results.
    """

    from collections import defaultdict

    grouped = defaultdict(list)

    for er in thermal_result.edge_results:
        if not er.is_joint:
            continue
        grouped[er.edge_id].append(er)

    rows = []

    for edge_id, results in grouped.items():
        edge = graph.edges[edge_id]

        temps = [r.T_C for r in results]
        currents = [r.I_A for r in results]
        losses = [r.P_gen_W for r in results]

        rows.append(
            JointScheduleRow(
                joint_id=edge_id,
                bus_id=edge_id,  # (you can improve later if you track parent bus)

                I_A=max(currents) if currents else 0.0,
                T_C=max(temps) if temps else 0.0,
                P_W=sum(losses),

                segment_count=len(results),
            )
        )

    return rows