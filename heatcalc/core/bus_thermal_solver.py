# heatcalc/core/bus_thermal_solver.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

from heatcalc.core.busbar_geometry import BusbarGeometry, axial_conductance
from heatcalc.core.busbar_joint_resistance import (
    bolted_overlap_joint_resistance,
    clamped_edge_joint_resistance,
    sandwich_joint_resistance,
)
from heatcalc.core.busbar_physics import (
    compute_busbar_physics,
    resistance_20C_per_m,
    resistance_T_per_m,
    BusbarThermalInputs,
    ALPHA_CU, compute_joint_self_cooling,
)

K_CU = 400.0

import math

@dataclass
class ThermalEdgeResult:
    edge_id: int
    T_C: float
    I_A: float
    length_m: float
    is_joint: bool
    P_gen_W: float
    P_conv_W: float
    P_rad_W: float
    P_cond_W: float
    residual_W: float
    width_mm: float
    thickness_mm: float
    bars_in_parallel: int
    gap_to_wall_mm: float = 50.0
    orientation_to_wall: str = "width"
    joint_id: str | None = None
    joint_number: int | None = None
    T_min_C: float = 0.0


@dataclass
class ThermalSolveResult:
    converged: bool
    iterations: int
    air_temp_C: object   # float OR dict[edge_id -> air temp]
    total_loss_W: float
    max_T_C: float
    min_T_C: float
    edge_results: list[ThermalEdgeResult]
    T_vector_C: np.ndarray
    segment_rows: list[dict] | None = None

from typing import Any


@dataclass
class ThermalSeg:
    seg_id: str
    base_edge_id: int
    u: Any
    v: Any
    length_m: float
    width_mm: float
    thickness_mm: float
    bars_in_parallel: int
    face_to_face_dim: str
    tier: object | None
    is_joint: bool
    joint_spec: object | None
    I_A: float
    air_temp_C: float
    gap_to_wall_mm: float
    orientation_to_wall: str
    convection_mode: str


def _shared_node(a, b) -> bool:
    if hasattr(a, "_shared_node"):
        return a._shared_node(b)
    return (
        a.u == b.u or
        a.u == b.v or
        a.v == b.u or
        a.v == b.v
    )


def _axial_conductance(seg_a: ThermalSeg, seg_b: ThermalSeg) -> float:
    return axial_conductance(
        seg_a.width_mm, seg_a.thickness_mm, seg_a.length_m,
        seg_b.width_mm, seg_b.thickness_mm, seg_b.length_m,
        K_CU
    )


def _build_segmented_thermal_edges(
    graph,
    edge_air_fn,
    interface_len_m: float = 0.025,
    min_core_len_m: float = 0.010,
):
    """
    Create a thermal-only segmented graph.

    Rules:
      - joints are kept whole
      - a non-joint edge is split near an endpoint if that endpoint is a
        cross-tier interface node
      - interface stub gets mixed ambient
      - core segment keeps original owning-tier ambient
    """
    node_to_edges = defaultdict(list)
    for e in graph.edges.values():
        node_to_edges[e.u].append(e)
        node_to_edges[e.v].append(e)

    interface_nodes = set()
    interface_air_by_node = {}

    # Detect true cross-tier interface nodes
    for node, incident in node_to_edges.items():
        non_joint = [e for e in incident if not getattr(e, "is_joint", False)]
        tiers = {getattr(e, "tier", None) for e in non_joint if getattr(e, "tier", None) is not None}

        if len(tiers) < 2:
            continue

        interface_nodes.add(node)

        temps = [float(edge_air_fn(e.id)) for e in non_joint]
        interface_air_by_node[node] = sum(temps) / len(temps) if temps else 0.0

    segs: list[ThermalSeg] = []

    for e in graph.edges.values():
        L = float(e.length_m)
        base_air = float(edge_air_fn(e.id))

        # Infer convection mode from node coordinates
        u_p = graph.nodes[e.u].p
        v_p = graph.nodes[e.v].p
        dx = abs(u_p.x() - v_p.x())
        dy = abs(u_p.y() - v_p.y())
        edge_conv_mode = "vertical" if dy > dx else "horizontal"

        # keep joints whole
        if getattr(e, "is_joint", False):
            segs.append(
                ThermalSeg(
                    seg_id=f"{e.id}:full",
                    base_edge_id=int(e.id),
                    u=e.u,
                    v=e.v,
                    length_m=L,
                    width_mm=float(e.width_mm),
                    thickness_mm=float(e.thickness_mm),
                    bars_in_parallel=int(e.bars_in_parallel),
                    face_to_face_dim=getattr(e, "face_to_face_dim", "thickness"),
                    tier=getattr(e, "tier", None),
                    is_joint=bool(e.is_joint),
                    joint_spec=getattr(e, "joint_spec", None),
                    I_A=float(e.I_A),
                    air_temp_C=base_air,
                    gap_to_wall_mm=float(getattr(e, "gap_to_wall_mm", 50.0)),
                    orientation_to_wall=getattr(e, "orientation_to_wall", "width"),
                    convection_mode=edge_conv_mode,
                )
            )
            continue

        split_u = e.u in interface_nodes
        split_v = e.v in interface_nodes

        if not split_u and not split_v:
            segs.append(
                ThermalSeg(
                    seg_id=f"{e.id}:full",
                    base_edge_id=int(e.id),
                    u=e.u,
                    v=e.v,
                    length_m=L,
                    width_mm=float(e.width_mm),
                    thickness_mm=float(e.thickness_mm),
                    bars_in_parallel=int(e.bars_in_parallel),
                    face_to_face_dim=getattr(e, "face_to_face_dim", "thickness"),
                    tier=getattr(e, "tier", None),
                    is_joint=bool(e.is_joint),
                    joint_spec=getattr(e, "joint_spec", None),
                    I_A=float(e.I_A),
                    air_temp_C=base_air,
                    gap_to_wall_mm=float(getattr(e, "gap_to_wall_mm", 50.0)),
                    orientation_to_wall=getattr(e, "orientation_to_wall", "width"),
                    convection_mode=edge_conv_mode,
                )
            )
            continue

        stub_u = min(interface_len_m, 0.2 * L) if split_u else 0.0
        stub_v = min(interface_len_m, 0.2 * L) if split_v else 0.0

        # preserve a real middle section where possible
        if stub_u + stub_v > max(L - min_core_len_m, 0.0):
            total_stub = stub_u + stub_v
            if total_stub > 0.0:
                scale = max((L - min_core_len_m), 0.0) / total_stub
                stub_u *= scale
                stub_v *= scale

        core_len = max(L - stub_u - stub_v, 0.0)

        cursor = e.u

        if stub_u > 1e-9:
            n_u = ("if", int(e.id), "u")
            segs.append(
                ThermalSeg(
                    seg_id=f"{e.id}:u_if",
                    base_edge_id=int(e.id),
                    u=e.u,
                    v=n_u,
                    length_m=stub_u,
                    width_mm=float(e.width_mm),
                    thickness_mm=float(e.thickness_mm),
                    bars_in_parallel=int(e.bars_in_parallel),
                    face_to_face_dim=getattr(e, "face_to_face_dim", "thickness"),
                    tier=getattr(e, "tier", None),
                    is_joint=False,
                    joint_spec=getattr(e, "joint_spec", None),
                    I_A=float(e.I_A),
                    air_temp_C=float(interface_air_by_node[e.u]),
                    gap_to_wall_mm=float(getattr(e, "gap_to_wall_mm", 50.0)),
                    orientation_to_wall=getattr(e, "orientation_to_wall", "width"),
                    convection_mode=edge_conv_mode,
                )
            )
            cursor = n_u

        if core_len > 1e-9:
            next_node = e.v if stub_v <= 1e-9 else ("if", int(e.id), "v")
            segs.append(
                ThermalSeg(
                    seg_id=f"{e.id}:core",
                    base_edge_id=int(e.id),
                    u=cursor,
                    v=next_node,
                    length_m=core_len,
                    width_mm=float(e.width_mm),
                    thickness_mm=float(e.thickness_mm),
                    bars_in_parallel=int(e.bars_in_parallel),
                    face_to_face_dim=getattr(e, "face_to_face_dim", "thickness"),
                    tier=getattr(e, "tier", None),
                    is_joint=False,
                    joint_spec=getattr(e, "joint_spec", None),
                    I_A=float(e.I_A),
                    air_temp_C=base_air,
                    gap_to_wall_mm=float(getattr(e, "gap_to_wall_mm", 50.0)),
                    orientation_to_wall=getattr(e, "orientation_to_wall", "width"),
                    convection_mode=edge_conv_mode,
                )
            )
            cursor = next_node

        if stub_v > 1e-9:
            segs.append(
                ThermalSeg(
                    seg_id=f"{e.id}:v_if",
                    base_edge_id=int(e.id),
                    u=cursor,
                    v=e.v,
                    length_m=stub_v,
                    width_mm=float(e.width_mm),
                    thickness_mm=float(e.thickness_mm),
                    bars_in_parallel=int(e.bars_in_parallel),
                    face_to_face_dim=getattr(e, "face_to_face_dim", "thickness"),
                    tier=getattr(e, "tier", None),
                    is_joint=False,
                    joint_spec=getattr(e, "joint_spec", None),
                    I_A=float(e.I_A),
                    air_temp_C=float(interface_air_by_node[e.v]),
                    gap_to_wall_mm=float(getattr(e, "gap_to_wall_mm", 50.0)),
                    orientation_to_wall=getattr(e, "orientation_to_wall", "width"),
                    convection_mode=edge_conv_mode,
                )
            )

    return segs


from collections import defaultdict

def _aggregate_segment_results(segment_rows):
    """
    Collapse segmented thermal results back to original edge ids so the UI
    and existing apply path can remain unchanged.
    """
    grouped = defaultdict(list)

    for row in segment_rows:
        grouped[row["base_edge_id"]].append(row)

    out = []

    for edge_id, rows in grouped.items():
        total_len = sum(r["length_m"] for r in rows)
        total_pgen = sum(r["P_gen_W"] for r in rows)
        total_pconv = sum(r["P_conv_W"] for r in rows)
        total_prad = sum(r["P_rad_W"] for r in rows)
        total_pcond = sum(r["P_cond_W"] for r in rows)
        total_resid = sum(r["residual_W"] for r in rows)

        Tmax = max(r["T_C"] for r in rows)
        Tmin = min(r["T_C"] for r in rows)

        first = rows[0]

        out.append(
            ThermalEdgeResult(
                edge_id=int(edge_id),
                T_C=float(Tmax),
                T_min_C=float(Tmin),
                I_A=float(first["I_A"]),
                length_m=float(total_len),
                is_joint=bool(first["is_joint"]),
                P_gen_W=float(total_pgen),
                P_conv_W=float(total_pconv),
                P_rad_W=float(total_prad),
                P_cond_W=float(total_pcond),
                residual_W=float(total_resid),
                width_mm=float(first.get("width_mm", 0.0)),
                thickness_mm=float(first.get("thickness_mm", 0.0)),
                bars_in_parallel=int(first.get("bars_in_parallel", 1)),
                gap_to_wall_mm=float(first.get("gap_to_wall_mm", 50.0)),
                orientation_to_wall=str(first.get("orientation_to_wall", "width")),
                joint_id=first.get("joint_id"),
            )
        )

    out.sort(key=lambda x: x.edge_id)
    return out


def joint_contact_area(width_m, thickness_m, joint_spec):
    """
    Estimate effective thermal contact area of a joint.
    """
    # ---------------------------------------------------------------------------
    # Thermal contact area model for busbar joints
    # ---------------------------------------------------------------------------
    #
    # This function returns the effective *thermal contact area* used to conduct
    # heat between an explicit joint thermal segment and the adjoining busbar
    # thermal segments.
    #
    # IMPORTANT:
    # Electrical and thermal parallel-path behaviour are intentionally treated
    # differently.
    #
    # Electrical resistance:
    #   The joint-resistance functions already account for multiple parallel
    #   current paths through the joint interface(s). The returned electrical
    #   resistance is therefore an equivalent resistance for the full connected
    #   parallel set.
    #
    # Thermal conduction:
    #   Here we are modelling the *physical area available for heat transfer*
    #   across the joint interfaces. Where multiple bars physically contact
    #   multiple bars (for example N1 parallel bars clamped to N2 parallel bars),
    #   the total conductive contact area increases with the number of real
    #   interfaces.
    #
    #   Therefore, for joint types where each bar on one side physically mates
    #   with each bar on the other side, the thermal contact area is multiplied by:
    #
    #       n_interfaces = N1 * N2
    #
    #   This is appropriate for clamped-edge and sandwich-style joints where the
    #   parallel bar sets form multiple real conduction paths through contact.
    #
    # The returned area is used only in the thermal link conductance:
    #
    #       G = 1 / (R_cu + R_contact)
    #
    # where:
    #
    #       R_cu      = L_char / (k_cu * A_contact)
    #       R_contact = 1 / (h_contact * A_contact)
    #
    # Joint types handled:
    #
    #   bolted_overlap:
    #       Contact area is based on effective overlap area, reduced by bolt-hole
    #       area and optionally scaled by csa_factor.
    #
    #   clamped_edge:
    #       Contact patch is approximated as a thickness-based edge/face region:
    #           a = min(t1, t2)
    #           l = max(t1, t2)
    #       and multiplied by N1 * N2 interfaces.
    #
    #   sandwich_joint:
    #       Thermal contact area is currently approximated using the same
    #       thickness-based contact form as the clamped case, again multiplied by
    #       N1 * N2 interfaces.
    # ---------------------------------------------------------------------------

    joint_type = getattr(joint_spec, "joint_type", "bolted_overlap")

    if joint_type == "bolted_overlap":
        overlap = float(joint_spec.overlap_m)

        this_w_m = float(width_m)
        other_w_mm = getattr(joint_spec, "other_bar_width_mm", None)
        other_w_m = float(other_w_mm) / 1000.0 if other_w_mm is not None else this_w_m

        eff_w_m = min(this_w_m, other_w_m)

        bolt_d = float(joint_spec.bolt_dia_mm) / 1000.0
        bolt_area = math.pi * (bolt_d * 0.5) ** 2
        hole_area = int(joint_spec.bolt_count) * bolt_area
        overlap_area = eff_w_m * overlap

        A_contact = (overlap_area - hole_area) * float(joint_spec.csa_factor)
        return max(A_contact, overlap_area * 0.05)

    elif joint_type == "clamped_edge":
        other_w_mm = getattr(joint_spec, "other_bar_width_mm", None)
        other_t_mm = getattr(joint_spec, "other_bar_thickness_mm", None)
        other_n = max(1, int(getattr(joint_spec, "other_bar_count", 1) or 1))
        this_n = max(1, int(getattr(joint_spec, "bars_in_parallel", 1) or 1))

        if other_w_mm is None or other_t_mm is None:
            raise ValueError(
                "Clamped joint requires other_bar_width_mm and other_bar_thickness_mm on joint_spec."
            )

        this_t_mm = float(thickness_m) * 1000.0

        # edge-face patch = thickness × thickness
        a_mm = min(this_t_mm, float(other_t_mm))
        l_mm = max(this_t_mm, float(other_t_mm))

        # Effective thermal contact area for parallel bars
        n_interfaces = this_n * other_n
        A_contact = (a_mm * l_mm * n_interfaces) * 1e-6  # mm² -> m²
        return max(A_contact, 1e-9)

    elif joint_type == "sandwich_joint":
        this_w_mm = float(width_m) * 1000.0
        other_w_mm = getattr(joint_spec, "other_bar_width_mm", None)
        other_t_mm = getattr(joint_spec, "other_bar_thickness_mm", None)
        other_n = max(1, int(getattr(joint_spec, "other_bar_count", 1) or 1))
        this_n = max(1, int(getattr(joint_spec, "bars_in_parallel", 1) or 1))

        this_t_mm = float(thickness_m) * 1000.0
        other_t_mm_val = float(other_t_mm) if other_t_mm is not None else this_t_mm

        # Sandwich joint thermal contact area - using similar logic to clamped for now
        a_mm = min(this_w_mm, other_w_mm)
        l_mm = min(this_t_mm, other_t_mm_val)

        n_interfaces = this_n * other_n
        A_contact = (a_mm * l_mm * n_interfaces) * 1e-6  # mm² -> m²
        return max(A_contact, 1e-9)

    else:
        raise ValueError(f"Unsupported joint_type: {joint_type}")

def joint_R20_ohm(nd, debug=False) -> float:
    js = nd["joint_spec"]
    if js is None:
        raise ValueError("Joint edge missing joint_spec.")

    joint_type = getattr(js, "joint_type", "bolted_overlap")
    n1 = max(1, int(nd.get("bars_in_parallel", 1)))

    other_w_mm = getattr(js, "other_bar_width_mm", None)
    other_t_mm = getattr(js, "other_bar_thickness_mm", None)
    other_n = max(1, int(getattr(js, "other_bar_count", 1) or 1))

    if joint_type == "bolted_overlap":
        R_single = bolted_overlap_joint_resistance(
            width_m=nd["w"],
            thickness_m=nd["t"],
            overlap_m=js.overlap_m,
            bolt_count=js.bolt_count,
            bolt_dia_mm=js.bolt_dia_mm,
            torque_Nm=js.torque_Nm,
            nut_factor=js.nut_factor,
            # Force actual overlap-ratio evaluation for bolted joints
            e_streamline=None,
            other_bar_width_m=(float(other_w_mm) / 1000.0) if other_w_mm is not None else None,
            other_bar_thickness_m=(float(other_t_mm) / 1000.0) if other_t_mm is not None else None,
            bar1_parallel_count=n1,
            bar2_parallel_count=other_n,
            debug=debug,
        )
        # The resistance is already calculated for the parallel set
        return R_single

    elif joint_type == "clamped_edge":
        if other_w_mm is None or other_t_mm is None:
            raise ValueError(
                "Clamped joint requires other_bar_width_mm and other_bar_thickness_mm on joint_spec."
            )

        R_single = clamped_edge_joint_resistance(
            bar1_width_m=nd["w"],
            bar1_thickness_m=nd["t"],
            bar2_width_m=float(other_w_mm) / 1000.0,
            bar2_thickness_m=float(other_t_mm) / 1000.0,
            torque_Nm=js.torque_Nm,
            clamp_bolt_dia_mm=js.bolt_dia_mm,
            nut_factor=js.nut_factor,
            bolt_count=js.bolt_count,
            bar1_parallel_count=n1,
            bar2_parallel_count=other_n,
            e_streamline=None,
            debug=debug,
        )
        # The resistance is already calculated for the parallel set
        return R_single

    elif joint_type == "sandwich_joint":
        R_single = sandwich_joint_resistance(
            bar1_width_m=nd["w"],
            bar1_thickness_m=nd["t"],
            bar2_width_m=(float(other_w_mm) / 1000.0),
            bar2_thickness_m=(float(other_t_mm) / 1000.0),
            torque_Nm=js.torque_Nm,
            clamp_bolt_dia_mm=js.bolt_dia_mm,
            nut_factor=js.nut_factor,
            bolt_count=js.bolt_count,
            bar1_parallel_count=n1,
            bar2_parallel_count=other_n,
            e_streamline=None,
            debug=debug,
        )
        # The resistance is already calculated for the parallel set
        return R_single

    else:
        raise ValueError(f"Unsupported joint_type: {joint_type}")

def solve_thermal(
    graph,
    air_temp_C,
    debug: bool = False,
    max_iter: int = 40,
    tol: float = 1e-3,
    # Busbar surface emissivity used for self-cooling radiation in the bus thermal solver.
    #
    # This controls how effectively the busbar can reject heat by thermal radiation
    # to its surrounding enclosure/air cavity. Lower values represent bright / shiny
    # metallic surfaces and reduce radiative cooling, resulting in a higher solved
    # busbar temperature. Higher values represent more oxidised / dull surfaces and
    # increase radiative cooling, resulting in a lower solved busbar temperature.
    #
    # Engineering intent:
    #   - 0.05 to 0.10 : bright / shiny copper or tinned copper
    #   - 0.20 to 0.40 : lightly to moderately oxidised copper
    #   - >0.50        : heavily oxidised / coated surfaces
    #
    # A default of 0.1 is used here as a reasonable assumption for bright tinned
    # or clean copper busbars, giving slightly less radiative cooling than the more
    # oxidised surfaces typically assumed in handbook reference plots.
    # -------------------------------------------------------------------------------
    eps_bus_self_cooling: float = 0.1,
    # -------------------------------------------------------------------------------
    # Analytical effect of eps_bus_self_cooling:
    #
    # Radiation is calculated from:
    #   Q_rad ∝ eps_rel * (T_bus^4 - T_sur^4)
    #
    # where eps_rel depends on both the bus emissivity and the enclosure/environment
    # emissivity. Increasing eps_bus_self_cooling increases eps_rel, which increases
    # radiative heat loss from the busbar.
    #
    # Practical effect:
    #   - higher eps_bus_self_cooling -> higher Prad, lower solved T_bus
    #   - lower eps_bus_self_cooling  -> lower Prad, higher solved T_bus
    #
    # This parameter changes the split between convection and radiation cooling.
    # It does not change electrical I²R generation directly, but it changes the
    # equilibrium bus temperature required for total heat loss to match Pgen.


):
    """
    Segmented thermal solve.

    Supports either:
        - scalar ambient air temperature for all edges, or
        - dict: {edge_id: air_temp_C} for per-edge ambient temperatures

    Real edge temperatures are solved on a thermal-only segmented graph and
    then collapsed back to original edge ids for the UI.
    """

    cross_link_debug = []
    physics_debug = False
    joint_debug = False
    joint_cooling_debug = False
    conduction_debug = False
    joint_link_debug = False
    joint_balance_debug = False

    def edge_air(edge_id):
        if isinstance(air_temp_C, dict):
            if edge_id in air_temp_C:
                return float(air_temp_C[edge_id])
            return float(next(iter(air_temp_C.values())))
        return float(air_temp_C)

    thermal_segs = _build_segmented_thermal_edges(
        graph=graph,
        edge_air_fn=edge_air,
        interface_len_m=0.025,
        min_core_len_m=0.010,
    )

    thermal_nodes = []
    thermal_edges = []

    # -------------------------------------------------------------------------
    # Build lookup of original edge lengths (physical conductor lengths)
    #
    # IMPORTANT:
    # Thermal segments are artificial (created for solver coupling between tiers).
    # However, natural convection depends on the *physical size of the conductor*,
    # not the numerical segment length.
    #
    # Therefore, we store the original edge length so all segments belonging to
    # the same physical busbar use a consistent convection characteristic length.
    # -------------------------------------------------------------------------
    edge_length_map = {
        int(e.id): float(
            getattr(e, "physical_run_length_m", None)
            if getattr(e, "physical_run_length_m", None) not in (None, 0.0)
            else e.length_m
        )
        for e in graph.edges.values()
    }

    # --------------------------------------------------------
    # Build thermal nodes: one node per THERMAL SEGMENT
    # --------------------------------------------------------
    for seg in thermal_segs:
        width_m = seg.width_mm / 1000.0
        th_m = seg.thickness_mm / 1000.0

        # -------------------------------------------------------------------------
        # Characteristic length for convection (L_char)
        #
        # IMPORTANT DISTINCTION:
        # - seg.length_m → numerical segment length (used for heat integration)
        # - L_char_m     → physical convection length (used in heat transfer correlation)
        #
        # For vertical busbars:
        #   Using seg.length_m would artificially increase convection for small
        #   segments (e.g., interface stubs), because the correlation scales as:
        #
        #       W_conv ∝ 1 / L_char^0.25
        #
        #   This would result in non-physical "over-cooling" at segmentation points.
        #
        #   To fix this, we use the original full edge length (physical conductor height)
        #   for ALL segments belonging to the same edge.
        #
        # For horizontal busbars:
        #   Characteristic length is approximated as bar width (standard simplification).
        # -------------------------------------------------------------------------
        if seg.convection_mode == "vertical":
            l_char_m = edge_length_map.get(seg.base_edge_id, seg.length_m)
        else:
            l_char_m = width_m

        geom = BusbarGeometry(
            name=f"seg-{seg.seg_id}",
            width_m=width_m,
            thickness_m=th_m,
            L_char_m=l_char_m,
            length_m=seg.length_m,
            bars_in_parallel=seg.bars_in_parallel,
            face_to_face_dim=seg.face_to_face_dim,
            convection_mode=seg.convection_mode,
        )

        thermal_nodes.append({
            "seg_id": seg.seg_id,
            "base_edge_id": seg.base_edge_id,
            "geom": geom,
            "I": float(seg.I_A),
            "L": float(seg.length_m),
            "w": width_m,
            "t": th_m,
            "area": width_m * th_m,
            "tier": seg.tier,
            "is_joint": seg.is_joint,
            "joint_spec": seg.joint_spec,
            "bars_in_parallel": seg.bars_in_parallel,
            "air_temp_C": float(seg.air_temp_C),
            "u": seg.u,
            "v": seg.v,
        })

    seg_map = {seg.seg_id: i for i, seg in enumerate(thermal_segs)}

    if debug:
        print("\n================ THERMAL SOLVER INPUT DEBUG ================")
        print(f"Thermal segments: {len(thermal_nodes)}")
        print(f"Graph edges      : {len(graph.edges)}")
        for nd in thermal_nodes:
            tier_id = id(nd["tier"]) if nd["tier"] is not None else None
            print(
                f"{nd['seg_id']} | base=E{nd['base_edge_id']} | tier={tier_id} | "
                f"is_joint={nd['is_joint']} | "
                f"L={nd['L']:.4f} m | "
                f"w={nd['w'] * 1000:.1f} mm | t={nd['t'] * 1000:.1f} mm | "
                f"air={nd['air_temp_C']:.2f} C | "
                f"nodes=({nd['u']},{nd['v']})"
            )

    # --------------------------------------------------------
    # Thermal conduction between segments sharing a node
    # --------------------------------------------------------
    for sa in thermal_segs:
        for sb in thermal_segs:
            if sa.seg_id >= sb.seg_id:
                continue

            if not _shared_node(sa, sb):
                continue

            ia = seg_map[sa.seg_id]
            ib = seg_map[sb.seg_id]

            # Jointed connection:
            # If either segment is an explicit joint element, do not use the normal axial
            # copper conductance model. Instead, represent the thermal path between the
            # joined elements as a series combination of:
            #
            #   1) copper spreading/constriction resistance through the local contact zone
            #   2) thermal contact resistance across the mating interface
            #
            # so that:
            #
            #       G_joint = 1 / (R_cu + R_contact)
            #
            # with:
            #
            #       R_cu      = L_char / (K_CU * A_contact)
            #       R_contact = 1 / (h_contact * A_contact)
            #
            # The contact area A_contact is joint-type dependent and may scale with the
            # number of physical bar-to-bar interfaces for parallel bar sets.

            if thermal_nodes[ia]["is_joint"] or thermal_nodes[ib]["is_joint"]:
                if thermal_nodes[ia]["is_joint"]:
                    A_contact = joint_contact_area(
                        thermal_nodes[ia]["w"],
                        thermal_nodes[ia]["t"],
                        thermal_nodes[ia]["joint_spec"]
                    )
                elif thermal_nodes[ib]["is_joint"]:
                    A_contact = joint_contact_area(
                        thermal_nodes[ib]["w"],
                        thermal_nodes[ib]["t"],
                        thermal_nodes[ib]["joint_spec"]
                    )
                else:
                    A_contact = min(thermal_nodes[ia]["area"], thermal_nodes[ib]["area"])

                # ---------------------------------------------------------
                # Joint thermal conductance derived from electrical joint resistance
                # ---------------------------------------------------------
                # The joint electrical resistance is used for Joule generation elsewhere:
                #
                #     P_gen = I^2 * R_joint(T)
                #
                # For the thermal coupling between the explicit joint node and adjacent
                # busbar nodes, do not use the electrical resistance directly. Convert it
                # to an approximate thermal conductance using the Wiedemann-Franz relation:
                #
                #     G_th = L0 * T_K / R_e
                #
                # where:
                #     L0  = Lorenz number ≈ 2.44e-8 WΩ/K²
                #     T_K = representative absolute joint temperature
                #     R_e = electrical joint resistance at that temperature
                #
                # This replaces the earlier generic L/(kA) + 1/(hA) contact model.
                #
                # NOTE: Since T and R_e(T) are both temperature dependent, G_th is also
                # temperature dependent. We initially build the matrix with a constant G
                # (using a reasonable T_ref), but in the Newton solver we update the
                # residual and jacobian to use the current solved temperature.
                L0_W_OHM_PER_K2 = 2.44e-8

                if thermal_nodes[ia]["is_joint"]:
                    joint_nd = thermal_nodes[ia]
                elif thermal_nodes[ib]["is_joint"]:
                    joint_nd = thermal_nodes[ib]
                else:
                    joint_nd = None

                if joint_nd is None:
                    G = _axial_conductance(sa, sb)
                    mode = "axial"
                else:
                    # Initial estimate for building the conductance matrix.
                    T_ref_C = 80.0
                    T_ref_K = T_ref_C + 273.15

                    R20_elec = joint_R20_ohm(joint_nd, debug=False)
                    R_elec_T = R20_elec * (1.0 + ALPHA_CU * (T_ref_C - 20.0))

                    thermal_link_factor = float(
                        getattr(joint_nd["joint_spec"], "thermal_link_factor")
                    )

                    G_full_joint = thermal_link_factor * (L0_W_OHM_PER_K2 * T_ref_K) / max(R_elec_T, 1e-12)
                    G = 0.5 * G_full_joint

                    G = G * thermal_link_factor
                    mode = "joint_wf"

            else:
                G = _axial_conductance(sa, sb)
                mode = "axial"

            thermal_edges.append((ia, ib, G, mode))
            thermal_edges.append((ib, ia, G, mode))

            if debug:
                print(
                    f"THERM LINK: {sa.seg_id} <-> {sb.seg_id} | "
                    f"mode={mode} | G={G:.6e}"
                )

    n = len(thermal_nodes)
    nbrs = [[] for _ in range(n)]
    for i, j, G, tag in thermal_edges:
        nbrs[i].append((j, G, tag))

    if joint_link_debug:
        print("\n================ JOINT THERMAL CONNECTIVITY DEBUG ================")

        for i, nd in enumerate(thermal_nodes):
            if not nd["is_joint"]:
                continue

            print(
                f"\n[joint_node] idx={i} | seg={nd['seg_id']} | "
                f"edge={nd['base_edge_id']} | I={nd['I']:.2f} A | "
                f"L={nd['L']:.6f} m | w={nd['w'] * 1000:.1f} mm | t={nd['t'] * 1000:.1f} mm"
            )

            if not nbrs[i]:
                print("  !! NO THERMAL NEIGHBOURS FOUND FOR THIS JOINT !!")
                continue

            for j, G_fixed, tag in nbrs[i]:
                nb = thermal_nodes[j]
                print(
                    f"  -> neighbour idx={j} | seg={nb['seg_id']} | "
                    f"edge={nb['base_edge_id']} | is_joint={nb['is_joint']} | "
                    f"tag={tag} | G_initial={G_fixed:.6e} W/K | "
                    f"shared_nodes=({nd['u']},{nd['v']}) <-> ({nb['u']},{nb['v']})"
                )



    therm = BusbarThermalInputs(
        I_total_A=0.0,
        eps_bus=eps_bus_self_cooling,
        eps_env=0.9,
        v_mps=0.0,
        S_ac=1.1,
    )

    # explicit local ambient only — no neighbour air smearing
    node_air = np.array([nd["air_temp_C"] for nd in thermal_nodes], dtype=float)

    T_floor = node_air.copy()
    T = T_floor + 20.0

    def _joint_side_edges(base_edge_id: int, joint_spec, host_geom_mm, other_geom_mm):
        return graph.get_joint_side_edges(base_edge_id)

    def _side_vertical_run_below_from_joint(side_edges, joint_edge):
        runs = []
        for e in side_edges:
            for shared_node in e.shared_endpoints(joint_edge):
                run = graph.get_downward_vertical_run_from_node(
                    current_node_id=shared_node,
                    visited_edge_ids={joint_edge.id},
                )
                if run > 0.0:
                    runs.append(run)
        return float(max(runs)) if runs else None

    def _infer_side_convection_from_edges(
        edges,
        *,
        default_mode: str,
        default_width_m: float,
        default_lchar_m: float,
        joint_cooling_debug: bool = False,
    ):
        """
        Infer a side-specific convection mode and characteristic length from the
        actual adjoining graph edges on that side of the joint.

        Vertical side:
            use the MEAN adjacent vertical run length.
            - bottom/end joint -> available run above/below
            - midpoint joint   -> average of upper/lower adjacent runs

        Horizontal side:
            use bar width, consistent with the existing horizontal bar model.
        """
        if not edges:
            mode = str(default_mode)
            if mode == "horizontal":
                return mode, max(float(default_width_m), 1e-12)
            return mode, max(float(default_lchar_m), 1e-12)

        vert_lengths = []
        horiz_lengths = []
        horiz_widths = []

        for e in edges:
            mode_e = graph.get_edge_convection_mode(e)
            if mode_e == "vertical":
                vert_lengths.append(float(e.length_m))
            else:
                horiz_lengths.append(float(e.length_m))
                horiz_widths.append(float(e.width_mm) / 1000.0)

        # Choose dominant orientation on this side by total attached run length
        sum_vert = sum(vert_lengths)
        sum_horiz = sum(horiz_lengths)

        if sum_vert >= sum_horiz and len(vert_lengths) > 0:
            mode = "vertical"
            # midpoint joint on a continuous run naturally tends toward half-height
            l_char = sum(vert_lengths) / len(vert_lengths)
            if joint_cooling_debug:
                 print(f"    - Dominant mode: vertical (avg of {len(vert_lengths)} edges: {l_char:.4f}m)")
        else:
            mode = "horizontal"
            l_char = (
                sum(horiz_widths) / len(horiz_widths)
                if len(horiz_widths) > 0 else
                float(default_width_m)
            )
            if joint_cooling_debug:
                 print(f"    - Dominant mode: horizontal (lchar={l_char:.4f}m)")

        return mode, max(float(l_char), 1e-12)

    def _infer_side_convection_for_joint(
        side_edges,
        joint_edge,
        *,
        default_mode: str,
        default_width_m: float,
        default_lchar_m: float,
        side_name: str = "unknown",
    ):
        """
        Joint-side convection inference.
        """
        if joint_cooling_debug:
            print(f"  Inference for side {side_name}:")

        run_below = _side_vertical_run_below_from_joint(side_edges, joint_edge)
        if joint_cooling_debug and run_below is not None:
             print(f"    - Found vertical run below joint: {run_below:.4f}m")

        if run_below is not None and run_below > 0.0:
            return "vertical", max(float(run_below), 1e-12)

        return _infer_side_convection_from_edges(
            side_edges,
            default_mode=default_mode,
            default_width_m=default_width_m,
            default_lchar_m=default_lchar_m,
        )

    def joint_cooling_state(nd, Ti: float, Tai: float):
        js = nd["joint_spec"]
        if js is None:
            raise ValueError("Joint edge missing joint_spec for self-cooling model.")

        other_w_mm = getattr(js, "other_bar_width_mm", None)
        other_t_mm = getattr(js, "other_bar_thickness_mm", None)
        other_n = max(1, int(getattr(js, "other_bar_count", 1) or 1))

        w2 = (float(other_w_mm) / 1000.0) if other_w_mm is not None else nd["w"]
        t2 = (float(other_t_mm) / 1000.0) if other_t_mm is not None else nd["t"]

        host_geom_mm = (
            float(nd["w"]) * 1000.0,
            float(nd["t"]) * 1000.0,
            max(1, int(nd.get("bars_in_parallel", 1))),
        )

        other_geom_mm = None
        if other_w_mm is not None and other_t_mm is not None:
            other_geom_mm = (
                float(other_w_mm),
                float(other_t_mm),
                other_n,
            )

        ej = graph.edges[int(nd["base_edge_id"])]

        host_edges, other_edges = _joint_side_edges(
            base_edge_id=int(nd["base_edge_id"]),
            joint_spec=js,
            host_geom_mm=host_geom_mm,
            other_geom_mm=other_geom_mm,
        )

        # ---------------------------------------------------------
        # Host-side convection metadata
        #
        # Final rule:
        #   If this side is connected to any vertical bus run below the joint,
        #   the characteristic length is the TOTAL contiguous vertical run
        #   below the joint, stopping at any horizontal turn.
        # ---------------------------------------------------------
        mode1, lchar1 = _infer_side_convection_for_joint(
            host_edges,
            ej,
            default_mode=nd["geom"].convection_mode,
            default_width_m=nd["w"],
            default_lchar_m=nd["geom"].L_char_m,
            side_name="host",
        )

        # ---------------------------------------------------------
        # Other-side convection metadata
        #
        # Same rule applies here as well. If the other connected side also
        # sits on a vertical bus run below the joint, use that full downward
        # contiguous vertical run. Otherwise fall back sensibly.
        # ---------------------------------------------------------
        mode2, lchar2 = _infer_side_convection_for_joint(
            other_edges,
            ej,
            default_mode=mode1,
            default_width_m=w2,
            default_lchar_m=lchar1 if mode1 == "vertical" else w2,
            side_name="other",
        )

        if joint_cooling_debug:
            print(f"[joint_cooling_state] {f'seg-{nd['seg_id']}'}")
            print(f"  T_joint={Ti:.2f} C | T_air={Tai:.2f} C | dT={Ti - Tai:.2f} K")
            print(f"  Side1 (host): mode={mode1}, Lchar={lchar1:.4f}m")
            print(f"  Side2 (other): mode={mode2}, Lchar={lchar2:.4f}m")

        return compute_joint_self_cooling(
            joint_type=getattr(js, "joint_type", "bolted_overlap"),
            width1_m=nd["w"],
            thickness1_m=nd["t"],
            count1=max(1, int(nd.get("bars_in_parallel", 1))),
            width2_m=w2,
            thickness2_m=t2,
            count2=other_n,
            length_m=nd["L"],  # local joint length for exposed-area evaluation
            T_bus_C=Ti,
            T_air_C=Tai,
            eps_bus=therm.eps_bus,
            eps_env=therm.eps_env,
            convection_mode1=mode1,
            convection_mode2=mode2,
            L_char1_m=lchar1,
            L_char2_m=lchar2,
            name=f"seg-{nd['seg_id']}",
            debug=joint_cooling_debug,
        )

    def calc_terms(i: int, Tvec: np.ndarray):
        nd = thermal_nodes[i]
        Ti = float(Tvec[i])
        Tai = float(node_air[i])
        st = None  # <-- add this

        if nd["is_joint"]:
            # Joints expel heat ONLY via conduction in this solver model.
            # Convection and radiation terms are excluded from the joint power balance.
            P_conv = 0.0
            P_rad = 0.0

            if joint_cooling_debug:
                # Still calculate for debug logging if requested
                cool = joint_cooling_state(nd, Ti, Tai)
                print(f"[joint_thermal_loss] {f'seg-{nd['seg_id']}'}")
                print(f"  P_conv_actual={cool.P_conv_W:.4f} W | P_rad_actual={cool.P_rad_W:.4f} W")
                print(f"  P_total_loss_applied=0.0000 W (conduction only)")
        else:
            st = compute_busbar_physics(
                geom=nd["geom"],
                therm=therm,
                T_bus_C=Ti,
                T_air_C=Tai,
                I_override_A=nd["I"],
                debug=physics_debug,
            )
            P_conv = st.P_conv_W_per_m * nd["L"]
            P_rad = st.P_rad_W_per_m * nd["L"]

        P_out = P_conv + P_rad

        # Explicit joint heat generation:
        # The joint replaces the equivalent straight bus section over this local
        # region. Its Joule heating is therefore based on the solved joint resistance
        # itself, not on an added straight-bus resistance term.
        #
        #       P_gen_joint = I^2 * R_joint(T)
        #
        # where R_joint(T) is the temperature-adjusted equivalent resistance of the
        # full joint, including any parallel-bar interface effects already embedded in
        # the joint-resistance function.
        if nd["is_joint"]:
            R20 = joint_R20_ohm(nd, debug=joint_debug)
            RT = R20 * (1.0 + ALPHA_CU * (Ti - 20.0))
            P_gen = (nd["I"] ** 2) * RT

            if joint_cooling_debug:
                # Comparison with equivalent straight bus section
                R20_bus = resistance_20C_per_m(nd["w"], nd["t"])
                n_bars = max(1, int(nd.get("bars_in_parallel", 1)))
                R20_bus_total = (R20_bus * nd["L"] * therm.S_ac) / n_bars
                RT_bus = R20_bus_total * (1.0 + ALPHA_CU * (Ti - 20.0))
                P_gen_bus = (nd["I"] ** 2) * RT_bus

                print(f"[joint_heat_gen] {f'seg-{nd['seg_id']}'} (edge {nd['base_edge_id']})")
                print(f"  T_joint={Ti:.2f} C | I={nd['I']:.1f} A")
                print(f"  Resistance (20C): Joint={R20:.4e} Ω | Bus_equiv={R20_bus_total:.4e} Ω | Ratio={R20/max(R20_bus_total, 1e-18):.2f}")
                print(f"  Resistance (T):   Joint={RT:.4e} Ω  | Bus_equiv={RT_bus:.4e} Ω  | Ratio={RT/max(RT_bus, 1e-18):.2f}")
                print(f"  Heat Gen:         Joint={P_gen:.4f} W  | Bus_equiv={P_gen_bus:.4f} W  | Extra={P_gen - P_gen_bus:.4f} W")
        else:
            R20 = resistance_20C_per_m(nd["w"], nd["t"])
            RT = resistance_T_per_m(R20, Ti)
            P_gen = (nd["I"] ** 2) * RT * nd["L"] * therm.S_ac

        P_cond = 0.0
        joint_link_rows = []

        for j, G_fixed, tag in nbrs[i]:
            Tj = float(Tvec[j])
            dT = Tj - Ti

            if tag == "joint_wf":
                # Temperature-dependent Wiedemann-Franz conductance.
                # The link conductance is based on the temperature of the joint node.
                if nd["is_joint"]:
                    joint_node_ref = nd
                    T_joint_C = Ti
                else:
                    joint_node_ref = thermal_nodes[j]
                    T_joint_C = Tj

                T_joint_K = T_joint_C + 273.15

                R20_joint = joint_R20_ohm(joint_node_ref, debug=False)
                R_elec_T = R20_joint * (1.0 + ALPHA_CU * (T_joint_C - 20.0))

                # ---------------------------------------------------------
                # Joint thermal coupling adjustment
                #
                # The joint-to-bus thermal conductance is derived using the
                # Wiedemann–Franz relationship:
                #
                #     G_th ≈ (L0 * T) / R_e
                #
                # which assumes an ideal metallic conduction path.
                #
                # However, real busbar joints exhibit non-ideal behaviour due to:
                #   • partial contact at microscopic asperities
                #   • surface oxidation and contamination
                #   • non-uniform clamping pressure
                #   • constriction and spreading resistance effects
                #
                # These effects reduce the effective thermal conductance relative
                # to the idealised WF prediction.
                #
                # To account for this, a thermal link factor is applied:
                #
                #     G_eff = thermal_link_factor · G_WF
                #
                # where:
                #     thermal_link_factor < 1 reduces heat transfer into adjacent busbars
                #
                # This improves representation of:
                #   • localised joint heating
                #   • realistic temperature gradients
                #   • reduced thermal smearing into the busbar network
                #
                # A value of thermal_link_factor = 0.5 has been adopted to introduce moderate
                # conservatism while maintaining physical plausibility.
                #
                thermal_link_factor = float(
                    getattr(joint_node_ref["joint_spec"], "thermal_link_factor")
                )
                # This parameter may be varied to represent joint condition:
                #   thermal_link_factor ≈ 1.0 → ideal, well-formed joint
                #   thermal_link_factor ≈ 0.5 → typical practical joint (adopted)
                #   thermal_link_factor < 0.3 → degraded or poorly installed joint
                # ---------------------------------------------------------

                G_full_joint = (
                        thermal_link_factor
                        * (L0_W_OHM_PER_K2 * T_joint_K)
                        / max(R_elec_T, 1e-12)
                )

                # Each explicit joint usually connects to two busbar neighbours.
                # This is the per-link half conductance.
                G_actual = 0.5 * G_full_joint

            else:
                joint_node_ref = None
                T_joint_C = None
                R20_joint = None
                R_elec_T = None
                thermal_link_factor = None
                G_full_joint = None
                G_actual = G_fixed

            q = G_actual * dT
            P_cond += q

            if nd["is_joint"] or thermal_nodes[j]["is_joint"]:
                joint_link_rows.append({
                    "from_idx": i,
                    "to_idx": j,
                    "from_seg": nd["seg_id"],
                    "to_seg": thermal_nodes[j]["seg_id"],
                    "from_edge": nd["base_edge_id"],
                    "to_edge": thermal_nodes[j]["base_edge_id"],
                    "from_is_joint": nd["is_joint"],
                    "to_is_joint": thermal_nodes[j]["is_joint"],
                    "tag": tag,
                    "Ti": Ti,
                    "Tj": Tj,
                    "dT": dT,
                    "G_fixed": G_fixed,
                    "G_actual": G_actual,
                    "q_W": q,
                    "R20_joint": R20_joint,
                    "R_elec_T": R_elec_T,
                    "thermal_link_factor": thermal_link_factor,
                    "G_full_joint": G_full_joint,
                })

            if conduction_debug and final_pass:
                cross_link_debug.append({
                    "from_seg": nd["seg_id"],
                    "to_seg": thermal_nodes[j]["seg_id"],
                    "from_edge": nd["base_edge_id"],
                    "to_edge": thermal_nodes[j]["base_edge_id"],
                    "tag": tag,
                    "Ti": Ti,
                    "Tj": Tj,
                    "dT": float(dT),
                    "G": float(G_actual),
                    "Q_W": float(q),
                })

            if conduction_debug and final_pass:
                cross_link_debug.append({
                    "from_seg": nd["seg_id"],
                    "to_seg": thermal_nodes[j]["seg_id"],
                    "from_edge": nd["base_edge_id"],
                    "to_edge": thermal_nodes[j]["base_edge_id"],
                    "tag": tag,
                    "Ti": Ti,
                    "Tj": Tj,
                    "dT": float(dT),
                    "G": float(G_actual),
                    "Q_W": float(q),
                })

        residual = P_gen - P_out + P_cond

        if final_pass and nd["is_joint"] and joint_balance_debug:
            seg_name = f"seg-{nd['seg_id']}"

            print(f"\n================ JOINT BALANCE DEBUG: {seg_name} ================")
            print(
                f"edge={nd['base_edge_id']} | T_joint={Ti:.4f} C | "
                f"T_air={Tai:.4f} C | I={nd['I']:.2f} A"
            )
            print(
                f"P_gen={P_gen:.6f} W | "
                f"P_conv={P_conv:.6f} W | "
                f"P_rad={P_rad:.6f} W | "
                f"P_out={P_out:.6f} W | "
                f"P_cond={P_cond:.6f} W | "
                f"residual={residual:.9f} W"
            )
            print(
                "Expected conduction-only balance: "
                "P_gen + P_cond ≈ 0, with P_cond < 0 meaning heat leaves the joint."
            )

            if not joint_link_rows:
                print("  !! No joint thermal link rows found in calc_terms().")
            else:
                print("  Link-by-link conduction:")

            for row in joint_link_rows:
                direction = "into_joint" if row["q_W"] > 0 else "out_of_joint"

                print(
                    f"    {row['from_seg']} -> {row['to_seg']} | "
                    f"tag={row['tag']} | "
                    f"T_from={row['Ti']:.4f} C | T_to={row['Tj']:.4f} C | "
                    f"dT=T_to-T_from={row['dT']:.6f} K | "
                    f"G={row['G_actual']:.6e} W/K | "
                    f"q={row['q_W']:.6f} W ({direction})"
                )

                if row["tag"] == "joint_wf":
                    print(
                        f"      WF details: "
                        f"R20_joint={row['R20_joint']:.6e} Ω | "
                        f"R_T_joint={row['R_elec_T']:.6e} Ω | "
                        f"G_full={row['G_full_joint']:.6e} W/K | "
                        f"factor={row['thermal_link_factor']:.3f}"
                    )
        
        if joint_debug and nd["is_joint"]:
             print(f"  P_net={residual:.6f} W (gen={P_gen:.4f}, out={P_out:.4f}, cond={P_cond:.4f})")

        return st, P_gen, P_conv, P_rad, P_cond, residual

    def residual(Tvec: np.ndarray) -> np.ndarray:
        f = np.zeros(n, dtype=float)
        for i in range(n):
            _, _, _, _, _, f[i] = calc_terms(i, Tvec)
        return f

    def dPgen_dT(nd, Ti: float) -> float:
        I2 = float(nd["I"]) ** 2
        if nd["is_joint"]:
            R20 = joint_R20_ohm(nd, debug=joint_debug)
            return I2 * R20 * ALPHA_CU
        else:
            R20 = resistance_20C_per_m(nd["w"], nd["t"])
            return I2 * R20 * ALPHA_CU * nd["L"] * therm.S_ac

    def dPout_dT_fd(i: int, nd, Ti: float, h: float = 0.05) -> float:
        Tai = float(node_air[i])

        if nd["is_joint"]:
            # Heat loss (out) is 0 for joints, so its derivative is 0.
            return 0.0

        st0 = compute_busbar_physics(
            geom=nd["geom"],
            therm=therm,
            T_bus_C=Ti,
            T_air_C=Tai,
            I_override_A=nd["I"],
            debug=physics_debug,
        )
        st1 = compute_busbar_physics(
            geom=nd["geom"],
            therm=therm,
            T_bus_C=Ti + h,
            T_air_C=Tai,
            I_override_A=nd["I"],
            debug=physics_debug,
        )

        P0 = (st0.P_conv_W_per_m + st0.P_rad_W_per_m) * nd["L"]
        P1 = (st1.P_conv_W_per_m + st1.P_rad_W_per_m) * nd["L"]
        return (P1 - P0) / h

    def jacobian(Tvec: np.ndarray):
        J = lil_matrix((n, n), dtype=float)
        sum_dPcond_dTi = np.zeros(n, dtype=float)

        for i in range(n):
            nd = thermal_nodes[i]
            Ti = float(Tvec[i])
            for j, G_fixed, tag in nbrs[i]:
                if j == i:
                    continue
                
                Tj = float(Tvec[j])
                
                if tag == "joint_wf":
                    # Temperature-dependent G_wf = C * T_K / R_e(T)
                    # Q = G_wf(T_joint) * (Tj - Ti)
                    
                    joint_node_ref = nd if nd["is_joint"] else thermal_nodes[j]
                    R20_joint = joint_R20_ohm(joint_node_ref, debug=False)
                    
                    thermal_link_factor = float(
                        getattr(joint_node_ref["joint_spec"], "thermal_link_factor")
                    )
                    # C includes 0.5 factor because link is split
                    C = 0.5 * thermal_link_factor * L0_W_OHM_PER_K2
                    
                    if nd["is_joint"]:
                        # T_joint = Ti
                        # G(Ti) = C * (Ti + 273.15) / (R20 * (1 + alpha*(Ti-20)))
                        # Q = G(Ti) * (Tj - Ti)
                        
                        T_K = Ti + 273.15
                        denom = R20_joint * (1.0 + ALPHA_CU * (Ti - 20.0))
                        G_actual = C * T_K / max(denom, 1e-12)
                        
                        # dG/dTi = C * [1*denom - T_K*(R20*alpha)] / denom^2
                        dG_dTi = C * (denom - T_K * R20_joint * ALPHA_CU) / max(denom**2, 1e-24)
                        
                        dQ_dTi = dG_dTi * (Tj - Ti) - G_actual
                        dQ_dTj = G_actual
                        
                    else:
                        # T_joint = Tj
                        # Q = G(Tj) * (Tj - Ti)
                        T_K = Tj + 273.15
                        denom = R20_joint * (1.0 + ALPHA_CU * (Tj - 20.0))
                        G_actual = C * T_K / max(denom, 1e-12)
                        
                        dG_dTj = C * (denom - T_K * R20_joint * ALPHA_CU) / max(denom**2, 1e-24)
                        
                        dQ_dTi = -G_actual
                        dQ_dTj = dG_dTj * (Tj - Ti) + G_actual
                else:
                    G_actual = G_fixed
                    dQ_dTi = -G_actual
                    dQ_dTj = G_actual

                J[i, j] += dQ_dTj
                sum_dPcond_dTi[i] += dQ_dTi

        for i, nd in enumerate(thermal_nodes):
            Ti = float(Tvec[i])
            J[i, i] = dPgen_dT(nd, Ti) - dPout_dT_fd(i, nd, Ti) + sum_dPcond_dTi[i]

        return J

    converged = False
    iterations_used = max_iter
    final_pass = False

    for it in range(max_iter):
        f = residual(T)
        max_f = float(np.max(np.abs(f)))

        if max_f < tol:
            converged = True
            iterations_used = it + 1
            final_pass = True
            break

        J = jacobian(T)

        try:
            dT = np.asarray(spsolve(J.tocsr(), -f), dtype=float)
        except Exception:
            dT = -0.1 * f

        if not np.all(np.isfinite(dT)):
            raise RuntimeError("Thermal Newton step became non-finite.")

        dT = np.clip(dT, -25.0, 25.0)

        lam = 1.0
        f_norm = max_f
        while lam > 0.05:
            T_trial = np.maximum(T + lam * dT, T_floor)
            f_trial = residual(T_trial)
            if np.max(np.abs(f_trial)) < f_norm:
                T = T_trial
                break
            lam *= 0.5
        else:
            T = np.maximum(T + 0.1 * dT, T_floor)

    # ---------------- Final post-processing on SEGMENTS ----------------
    segment_rows = []
    total_loss_W = 0.0

    for i, nd in enumerate(thermal_nodes):
        _, P_gen, P_conv, P_rad, P_cond, f_i = calc_terms(i, T)
        total_loss_W += P_gen

        segment_rows.append({
            "seg_id": nd["seg_id"],
            "base_edge_id": int(nd["base_edge_id"]),
            "T_C": float(T[i]),
            "I_A": float(nd["I"]),
            "length_m": float(nd["L"]),
            "is_joint": bool(nd["is_joint"]),
            "joint_id": nd["joint_spec"].joint_id if nd["is_joint"] and nd["joint_spec"] else None,
            "P_gen_W": float(P_gen),
            "P_conv_W": float(P_conv),
            "P_rad_W": float(P_rad),
            "P_cond_W": float(P_cond),
            "residual_W": float(f_i),
            "width_mm": nd["w"] * 1000,
            "thickness_mm": nd["t"] * 1000,
            "bars_in_parallel": nd["bars_in_parallel"],
            "gap_to_wall_mm": nd.get("gap_to_wall_mm", 50.0),
            "orientation_to_wall": nd.get("orientation_to_wall", "width"),
        })

    final_pass = True
    cross_link_debug.clear()
    for i in range(n):
        calc_terms(i, T)

    edge_results = _aggregate_segment_results(segment_rows)
    if debug:
        print("\n================ FINAL SEGMENT TEMPERATURES ================")
        for row in segment_rows:
            print(
                f"{row['seg_id']} | base=E{row['base_edge_id']} | "
                f"T={row['T_C']:.3f} C | "
                f"L={row['length_m']:.3f} m | "
                f"Pgen={row['P_gen_W']:.4f} W"
            )

        print("\n================ AGGREGATED EDGE TEMPERATURES ================")
        for er in edge_results:
            print(
                f"E{er.edge_id} | "
                f"T={er.T_C:.3f} C | "
                f"I={er.I_A:.1f} A | "
                f"L={er.length_m:.3f} m | "
                f"Pgen={er.P_gen_W:.4f} W"
            )

    return ThermalSolveResult(
        converged=bool(converged),
        iterations=int(iterations_used),
        air_temp_C=air_temp_C,
        total_loss_W=float(total_loss_W),
        max_T_C=float(np.max(T)),
        min_T_C=float(np.min(T)),
        edge_results=edge_results,
        T_vector_C=T.copy(),
    )
