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
            debug=debug,
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
            debug=debug,
        )
        return R_single / (n1 * other_n)

    else:
        raise ValueError(f"Unsupported joint_type: {joint_type}")

def solve_thermal(
    graph,
    air_temp_C,
    debug: bool = True,
    max_iter: int = 40,
    tol: float = 1e-3,
):
    """
    Graph-edge thermal solve.

    Supports either:
        - scalar ambient air temperature for all edges, or
        - dict: {edge_id: air_temp_C} for per-edge ambient temperatures

    This allows one global copper network solve while still applying
    tier-specific enclosure air temperatures to each edge.
    """

    cross_link_debug = []
    debug=True
    physics_debug=False
    joint_debug=False

    def edge_air(edge_id: int) -> float:
        if isinstance(air_temp_C, dict):
            return float(air_temp_C[edge_id])
        return float(air_temp_C)

    thermal_nodes = []
    thermal_edges = []

    # --------------------------------------------------------
    # Build thermal nodes: one node per graph edge
    # --------------------------------------------------------
    for e in graph.edges.values():
        width_m = e.width_mm / 1000.0
        th_m = e.thickness_mm / 1000.0

        geom = BusbarGeometry(
            name=f"edge-{e.id}",
            width_m=width_m,
            thickness_m=th_m,
            L_char_m=width_m,
            length_m=e.length_m,
            bars_in_parallel=e.bars_in_parallel,
            face_to_face_dim="thickness",
            convection_mode="horizontal",
        )

        thermal_nodes.append({
            "edge_id": e.id,
            "geom": geom,
            "I": float(e.I_A),
            "L": float(e.length_m),
            "w": width_m,
            "t": th_m,
            "area": width_m * th_m,
            "tier": e.tier,
            "is_joint": e.is_joint,
            "joint_spec": e.joint_spec,
            "bars_in_parallel": e.bars_in_parallel,
            "air_temp_C": edge_air(e.id),
        })

    edge_map = {eid: i for i, eid in enumerate(graph.edges.keys())}

    if debug:
        print("\n================ THERMAL SOLVER INPUT DEBUG ================")
        print(f"Thermal nodes: {len(thermal_nodes)}")
        print(f"Graph edges   : {len(graph.edges)}")
        print(f"Cross-tier links on graph: {len(getattr(graph, 'cross_tier_links', []) or [])}")

        for nd in thermal_nodes:
            tier_id = id(nd["tier"]) if nd["tier"] is not None else None
            print(
                f"E{nd['edge_id']} | tier={tier_id} | "
                f"is_joint={nd['is_joint']} | "
                f"L={nd['L']:.4f} m | "
                f"w={nd['w'] * 1000:.1f} mm | t={nd['t'] * 1000:.1f} mm | "
                f"air={nd['air_temp_C']:.2f} C"
            )

    # --------------------------------------------------------
    # Thermal conduction between graph edges sharing a node
    # --------------------------------------------------------
    for ea in graph.edges.values():
        for eb in graph.edges.values():
            if ea.id >= eb.id:
                continue

            shared = (
                    ea.u == eb.u or
                    ea.u == eb.v or
                    ea.v == eb.u or
                    ea.v == eb.v
            )
            if not shared:
                continue

            ia = edge_map[ea.id]
            ib = edge_map[eb.id]

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

            # ---------------------------------------------
            # Continuous copper vs jointed connection
            # ---------------------------------------------
            if not (thermal_nodes[ia]["is_joint"] or thermal_nodes[ib]["is_joint"]):
                G = 1e12
                mode = "continuous"
            else:
                tA = thermal_nodes[ia]["t"]
                tB = thermal_nodes[ib]["t"]

                L_char = 0.5 * (tA + tB)
                R_cu = L_char / (K_CU * A_contact)

                if thermal_nodes[ia]["is_joint"]:
                    h_c = thermal_nodes[ia]["joint_spec"].h_contact
                elif thermal_nodes[ib]["is_joint"]:
                    h_c = thermal_nodes[ib]["joint_spec"].h_contact
                else:
                    h_c = None

                R_contact = 1.0 / (h_c * A_contact) if h_c is not None else 0.0
                R_total = R_cu + R_contact
                G = 1.0 / R_total
                mode = "joint"

            thermal_edges.append((ia, ib, G, "node"))
            thermal_edges.append((ib, ia, G, "node"))

            if debug:
                print(
                    f"THERM LINK: E{ea.id} <-> E{eb.id} | "
                    f"shared_nodes=({ea.u},{ea.v})<->({eb.u},{eb.v}) | "
                    f"mode={mode} | A={A_contact:.6e} m2 | G={G:.6e}"
                )

    # --------------------------------------------------------
    # Deprecated cross-tier explicit links
    # --------------------------------------------------------
    if debug:
        xlinks = getattr(graph, "cross_tier_links", None) or []
        print(f"Explicit graph.cross_tier_links count: {len(xlinks)}")
        for ea_id, eb_id in xlinks:
            print(f"GRAPH XLINK: E{ea_id} <-> E{eb_id}")

    n = len(thermal_nodes)
    nbrs = [[] for _ in range(n)]
    for entry in thermal_edges:
        if len(entry) == 3:
            i, j, G = entry
            tag = "normal"
        else:
            i, j, G, tag = entry

        nbrs[i].append((j, G, tag))

    if debug:
        print("\n================ THERMAL NEIGHBOUR LIST ================")
        for i, nd in enumerate(thermal_nodes):
            print(f"E{nd['edge_id']} neighbours: {len(nbrs[i])}")
            for j, G, tag in nbrs[i]:
                print(
                    f"    -> E{thermal_nodes[j]['edge_id']} | "
                    f"tag={tag} | G={G:.6e}"
                )

    therm = BusbarThermalInputs(
        I_total_A=0.0,
        eps_bus=0.7,
        eps_env=0.9,
        v_mps=0.0,
        S_ac=1.0,
    )

    # --- BUILD NODE AMBIENT TEMPERATURES ---
    node_air = [0.0] * n
    node_air_count = [0] * n

    for i, nd in enumerate(thermal_nodes):
        node_air[i] += nd["air_temp_C"]
        node_air_count[i] += 1

    # Average (defensive, future-proof if multiple contributions)
    for i in range(n):
        if node_air_count[i] > 0:
            node_air[i] /= node_air_count[i]

    T_floor = np.array([nd["air_temp_C"] for nd in thermal_nodes], dtype=float)
    T = T_floor + 20.0

    # --- ENFORCE AMBIENT CONTINUITY (iterate to convergence) ---
    for _ in range(5):  # small fixed iteration, cheap and effective
        for i in range(n):
            for j, _, _ in nbrs[i]:
                avg_air = 0.5 * (node_air[i] + node_air[j])
                node_air[i] = avg_air
                node_air[j] = avg_air

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

            # Only log AFTER convergence (final iteration)
            if debug and final_pass:
                cross_link_debug.append({
                    "from_edge": nd["edge_id"],
                    "to_edge": thermal_nodes[j]["edge_id"],
                    "from_tier": id(nd["tier"]) if nd["tier"] is not None else None,
                    "to_tier": id(thermal_nodes[j]["tier"]) if thermal_nodes[j]["tier"] is not None else None,
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

    def dPout_dT_fd(nd, Ti: float, h: float = 0.05) -> float:
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
            J[i, i] = dPgen_dT(nd, Ti) - dPout_dT_fd(nd, Ti) - sumG[i]

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

    # ---------------- Final post-processing ----------------
    edge_results = []
    total_loss_W = 0.0

    for i, nd in enumerate(thermal_nodes):
        _, P_gen, P_conv, P_rad, P_cond, f_i = calc_terms(i, T)
        total_loss_W += P_gen

        edge_results.append(
            ThermalEdgeResult(
                edge_id=int(nd["edge_id"]),
                T_C=float(T[i]),
                I_A=float(nd["I"]),
                length_m=float(nd["L"]),
                is_joint=bool(nd["is_joint"]),
                P_gen_W=float(P_gen),
                P_conv_W=float(P_conv),
                P_rad_W=float(P_rad),
                P_cond_W=float(P_cond),
                residual_W=float(f_i),
            )
        )

    # --------------------------------------------------------
    # Final evaluation pass (for clean debug + outputs)
    # --------------------------------------------------------
    final_pass = True
    cross_link_debug.clear()  # wipe transient junk

    for i in range(n):
        calc_terms(i, T)

    if debug:
        print("\n================ FINAL EDGE TEMPERATURES ================")
        for er in edge_results:
            print(
                f"E{er.edge_id} | "
                f"T={er.T_C:.3f} C | "
                f"I={er.I_A:.1f} A | "
                f"L={er.length_m:.3f} m | "
                f"Pgen={er.P_gen_W:.4f} W"
            )

    # ✅ ADD THIS BLOCK HERE
    if debug:
        print("\n================ TEMPERATURE DISCONTINUITIES ================")
        for i in range(n):
            for j, _, _ in nbrs[i]:
                dT = abs(T[i] - T[j])
                if dT > 0.1:
                    print(
                        f"WARNING: E{thermal_nodes[i]['edge_id']} ↔ "
                        f"E{thermal_nodes[j]['edge_id']} | ΔT={dT:.3f} K"
                    )
    if debug:
        print("\n================ FINAL HEAT FLOW DEBUG ================")

        shown = 0
        seen = set()

        for entry in cross_link_debug:
            key = (
                min(entry["from_edge"], entry["to_edge"]),
                max(entry["from_edge"], entry["to_edge"]),
                entry["tag"],
            )
            if key in seen:
                continue
            seen.add(key)

            print(
                f"[{entry['tag']}] "
                f"E{entry['from_edge']} (tier {entry['from_tier']}) -> "
                f"E{entry['to_edge']} (tier {entry['to_tier']}) | "
                f"T: {entry['Ti']:.2f} -> {entry['Tj']:.2f} | "
                f"dT={entry['dT']:.4f} K | "
                f"G={entry['G']:.6e} | "
                f"Q={entry['Q_W']:.4f} W"
            )

            shown += 1
            if shown >= 100:
                break

        if shown == 0:
            print("No heat-flow neighbour entries were recorded.")
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