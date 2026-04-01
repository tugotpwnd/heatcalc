# heatcalc/core/bus_thermal_solver.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

from heatcalc.core.busbar_geometry import BusbarGeometry
from heatcalc.core.busbar_joint_resistance import bolted_overlap_joint_resistance, clamped_edge_joint_resistance
from heatcalc.core.busbar_physics import (
    compute_busbar_physics,
    resistance_20C_per_m,
    resistance_T_per_m,
    BusbarThermalInputs,
    ALPHA_CU,
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
    tier: object | None
    is_joint: bool
    joint_spec: object | None
    I_A: float
    air_temp_C: float
    gap_to_wall_mm: float
    orientation_to_wall: str
    convection_mode: str


def _shared_node(a, b) -> bool:
    return (
        a.u == b.u or
        a.u == b.v or
        a.v == b.u or
        a.v == b.v
    )


def _axial_conductance(seg_a: ThermalSeg, seg_b: ThermalSeg) -> float:
    """
    Finite axial copper conductance between two continuous copper segments
    sharing a node. This replaces the old G=1e12 shortcut.
    """
    A_a = (float(seg_a.width_mm) / 1000.0) * (float(seg_a.thickness_mm) / 1000.0)
    A_b = (float(seg_b.width_mm) / 1000.0) * (float(seg_b.thickness_mm) / 1000.0)

    La = max(float(seg_a.length_m), 1e-6)
    Lb = max(float(seg_b.length_m), 1e-6)

    R_a = 0.5 * La / (K_CU * max(A_a, 1e-12))
    R_b = 0.5 * Lb / (K_CU * max(A_b, 1e-12))
    R_total = R_a + R_b

    return 1.0 / max(R_total, 1e-12)


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

        # 🔥 THIS WAS MISSING
        first = rows[0]

        out.append(
            ThermalEdgeResult(
                edge_id=int(edge_id),
                T_C=float(Tmax),
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
    # This function estimates the *thermal contact area* between two busbar
    # segments for the purpose of computing thermal conduction across a joint.
    #
    # Important distinction:
    #
    # Electrical and thermal behaviour of parallel busbars are handled differently.
    #
    # Electrical:
    #   Parallel bars create multiple current paths through the joint interface.
    #   The electrical equivalent resistance therefore reduces as:
    #
    #       R_eq = R_single / (N1 * N2)
    #
    #   where N1 and N2 are the number of parallel bars on each side.
    #
    # Thermal:
    #   The thermal model in this solver represents each bus segment as a
    #   *single lumped thermal node*, even if multiple bars exist in parallel.
    #   Because of this lumped representation, multiplying the thermal contact
    #   area by the number of bar interfaces would artificially increase the
    #   thermal conductance between nodes and effectively double-count heat flow.
    #
    #   Therefore:
    #       - Electrical resistance is reduced for parallel bar interfaces
    #       - Thermal contact area is **not multiplied by bar count**
    #
    #   The contact area returned here represents the physical interface area
    #   of a single joint region between the two connected bus segments.
    #
    # Joint types handled:
    #
    #   bolted_overlap:
    #       Contact area is based on the overlap area minus bolt holes,
    #       scaled by the CSA reduction factor.
    #
    #   clamped_edge:
    #       Edge-to-edge contact between bars. The effective patch is taken as
    #       thickness × thickness of the contacting bars.
    #
    # The returned area is used only to compute thermal conductance:
    #
    #       G = 1 / (R_cu + R_contact)
    #
    # where R_contact = 1 / (h_contact * A_contact).
    #
    # ---------------------------------------------------------------------------

    joint_type = getattr(joint_spec, "joint_type", "bolted_overlap")

    if joint_type == "bolted_overlap":
        overlap = float(joint_spec.overlap_m)
        bolt_d = float(joint_spec.bolt_dia_mm) / 1000.0
        bolt_area = math.pi * (bolt_d * 0.5) ** 2
        hole_area = int(joint_spec.bolt_count) * bolt_area
        overlap_area = float(width_m) * overlap

        A_contact = (overlap_area - hole_area) * float(joint_spec.csa_factor)
        return max(A_contact, overlap_area * 0.05)

    elif joint_type == "clamped_edge":
        other_w_mm = getattr(joint_spec, "other_bar_width_mm", None)
        other_t_mm = getattr(joint_spec, "other_bar_thickness_mm", None)

        if other_w_mm is None or other_t_mm is None:
            raise ValueError(
                "Clamped joint requires other_bar_width_mm and other_bar_thickness_mm on joint_spec."
            )

        this_w_mm = float(width_m) * 1000.0
        this_t_mm = float(thickness_m) * 1000.0

        # edge-face patch = thickness × thickness
        a_mm = min(this_t_mm, float(other_t_mm))
        l_mm = max(this_t_mm, float(other_t_mm))

        A_contact = (a_mm * l_mm) * 1e-6  # mm² -> m²
        return max(A_contact, 1e-9)

    else:
        raise ValueError(f"Unsupported joint_type: {joint_type}")

def joint_R20_ohm(nd, debug=False) -> float:
    js = nd["joint_spec"]
    if js is None:
        raise ValueError("Joint edge missing joint_spec.")

    joint_type = getattr(js, "joint_type", "bolted_overlap")
    n1 = max(1, int(nd.get("bars_in_parallel", 1)))

    if joint_type == "bolted_overlap":
        R_single = bolted_overlap_joint_resistance(
            width_m=nd["w"],
            thickness_m=nd["t"],
            overlap_m=js.overlap_m,
            bolt_count=js.bolt_count,
            bolt_dia_mm=js.bolt_dia_mm,
            torque_Nm=js.torque_Nm,
            nut_factor=js.nut_factor,
            e_streamline=js.e_streamline,
            debug=False,
        )
        return R_single / n1

    elif joint_type == "clamped_edge":
        other_w_mm = getattr(js, "other_bar_width_mm", None)
        other_t_mm = getattr(js, "other_bar_thickness_mm", None)
        other_n = max(1, int(getattr(js, "other_bar_count", 1) or 1))

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
            e_streamline=js.e_streamline,
            debug=False,
        )
        return R_single / (n1 * other_n)

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
        int(e.id): float(e.length_m)
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
            face_to_face_dim="thickness",
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

            # Jointed connection: retain contact-based model
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

                tA = thermal_nodes[ia]["t"]
                tB = thermal_nodes[ib]["t"]
                L_char = 0.5 * (tA + tB)
                R_cu = L_char / (K_CU * max(A_contact, 1e-12))

                if thermal_nodes[ia]["is_joint"]:
                    h_c = thermal_nodes[ia]["joint_spec"].h_contact
                elif thermal_nodes[ib]["is_joint"]:
                    h_c = thermal_nodes[ib]["joint_spec"].h_contact
                else:
                    h_c = None

                R_contact = 1.0 / (h_c * A_contact) if h_c is not None else 0.0
                R_total = R_cu + R_contact
                G = 1.0 / max(R_total, 1e-12)
                mode = "joint"

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

    therm = BusbarThermalInputs(
        I_total_A=0.0,
        eps_bus=eps_bus_self_cooling,
        eps_env=0.9,
        v_mps=0.0,
        S_ac=1.2,
    )

    # explicit local ambient only — no neighbour air smearing
    node_air = np.array([nd["air_temp_C"] for nd in thermal_nodes], dtype=float)

    T_floor = node_air.copy()
    T = T_floor + 20.0

    def calc_terms(i: int, Tvec: np.ndarray):
        nd = thermal_nodes[i]
        Ti = float(Tvec[i])
        Tai = float(node_air[i])

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

        if nd["is_joint"]:
            R20 = joint_R20_ohm(nd, debug=joint_debug)
            RT = R20 * (1.0 + ALPHA_CU * (Ti - 20.0))
            P_gen = (nd["I"] ** 2) * RT
        else:
            R20 = resistance_20C_per_m(nd["w"], nd["t"])
            RT = resistance_T_per_m(R20, Ti)
            P_gen = (nd["I"] ** 2) * RT * nd["L"] * therm.S_ac

        P_cond = 0.0
        for j, G, tag in nbrs[i]:
            dT = Tvec[j] - Ti
            q = G * dT
            P_cond += q

            if debug and final_pass:
                cross_link_debug.append({
                    "from_seg": nd["seg_id"],
                    "to_seg": thermal_nodes[j]["seg_id"],
                    "from_edge": nd["base_edge_id"],
                    "to_edge": thermal_nodes[j]["base_edge_id"],
                    "tag": tag,
                    "Ti": Ti,
                    "Tj": float(Tvec[j]),
                    "dT": float(dT),
                    "G": float(G),
                    "Q_W": float(q),
                })

        residual = P_gen - P_out + P_cond
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
        sumG = np.zeros(n, dtype=float)

        for i in range(n):
            for j, G, _ in nbrs[i]:
                if j == i:
                    continue
                J[i, j] += G
                sumG[i] += G

        for i, nd in enumerate(thermal_nodes):
            Ti = float(Tvec[i])
            J[i, i] = dPgen_dT(nd, Ti) - dPout_dT_fd(i, nd, Ti) - sumG[i]

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