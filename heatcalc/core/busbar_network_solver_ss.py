from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve
import numpy as np

from heatcalc.core.busbar_geometry import BusbarGeometry, build_busbar_segments
from heatcalc.core.busbar_physics import BusbarThermalInputs, compute_busbar_physics, ALPHA_CU
from heatcalc.core.models import BusbarSpec, BusbarBranchSpec
# busbar_network_solver_ss.py
from heatcalc.core.busbar_physics import (
    compute_busbar_physics,
    resistance_20C_per_m,
    resistance_T_per_m,
    ALPHA_CU,
)

K_CU = 400.0  # W/mK (your current constant)

@dataclass
class Node:

    tier: object
    edge_id: int
    seg_index: int

    length_m: float
    width_m: float
    thickness_m: float

    area_m2: float

    extra_R20_ohm_per_m: float
    geom: BusbarGeometry

    I_seg_A: float

@dataclass
class Edge:
    i: int
    j: int
    G_W_per_K: float  # conduction "conductance" = k*A/L

@dataclass
class BusbarNetworkSolveResult:
    T_max_C: float
    T_vec_C: np.ndarray
    converged: bool
    iterations: int
    P_total_loss_W: float
    node_meta: List[Node]
    trace: List[np.ndarray]

def _build_current_profile_for_bus(bus: BusbarSpec, segments) -> np.ndarray:
    """
    Returns I_seg for each segment on this bus based on branches.
    Assumes each branch takes I_branch_A starting at x_m onward.
    """
    n = len(segments)
    I = np.full(n, float(bus.I_total_A), dtype=float)

    # segment start x positions
    xs = np.zeros(n)
    x = 0.0
    for i, seg in enumerate(segments):
        xs[i] = x
        x += seg.length_m

    # Apply each branch: after x_m, parent current reduced by I_branch_A
    for br in getattr(bus, "branches", []) or []:

        # Prefer current defined on the branch bus itself
        branch_I = getattr(br.branch, "I_total_A", None)

        # Fallback for backward compatibility
        if branch_I is None:
            branch_I = float(getattr(br, "I_branch_A", 0.0))

        branch_I = float(branch_I)

        for i in range(n):
            if xs[i] >= br.x_m:
                I[i] -= branch_I
    # guard
    if np.any(I < -1e-9):
        raise ValueError(f"Branch currents exceed parent current on bus '{bus.name}'")

    I = np.maximum(I, 0.0)
    return I

def build_busbar_network(bus: BusbarSpec, air_temp_C: float) -> Tuple[List[Node], List[Edge]]:
    """
    Build nodes and edges for a bus + arbitrarily nested branches.

    Key changes:
      - Recursive traversal of branches
      - Uses a unique bus_key (path) so repeated names don't collide
      - Branch attach uses the *parent bus's* segment midpoints at the correct depth
    """
    nodes: List[Node] = []
    edges: List[Edge] = []

    # Map (bus_key, local_seg_index) -> global node index
    node_index: Dict[Tuple[str, int], int] = {}

    # Cache segments by bus_key so attachment can reference the right parent's segmentation
    segments_by_key: Dict[str, List] = {}

    def _bus_key(parent_key: str, child_bus: BusbarSpec, child_idx: int) -> str:
        # Ensures uniqueness even if names repeat
        if parent_key:
            return f"{parent_key}/{child_bus.name}[{child_idx}]"
        return f"{child_bus.name}[0]"

    def _attach_local_index(segments: List, x_m: float) -> int:
        # Choose segment whose midpoint is closest to x_m
        parent_x = 0.0
        mids = []
        for seg in segments:
            mids.append(parent_x + 0.5 * seg.length_m)
            parent_x += seg.length_m
        return int(np.argmin(np.abs(np.array(mids) - float(x_m))))

    def add_bus_nodes_and_edges(this_bus: BusbarSpec, this_key: str) -> Tuple[int, int, List]:
        segments = build_busbar_segments(this_bus)
        segments_by_key[this_key] = segments

        I_profile = _build_current_profile_for_bus(this_bus, segments)  # handles this_bus.branches

        start_idx = len(nodes)

        for i, seg in enumerate(segments):
            geom = BusbarGeometry(
                name=this_bus.name,
                width_m=seg.width_m,
                thickness_m=seg.thickness_m,
                L_char_m=this_bus.L_char_m,
                length_m=seg.length_m,
                bars_in_parallel=this_bus.bars_in_parallel,
                face_to_face_dim=this_bus.face_to_face_dim,
                convection_mode=this_bus.convection_mode,
            )
            nd = Node(
                bus_name=this_key,  # IMPORTANT: unique key, not raw bus.name
                seg_index=i,
                length_m=seg.length_m,
                width_m=seg.width_m,
                thickness_m=seg.thickness_m,
                area_m2=seg.width_m * seg.thickness_m,
                extra_R20_ohm_per_m=getattr(seg, "extra_R20_ohm_per_m", 0.0),
                geom=geom,
                I_seg_A=float(I_profile[i]) / max(1, int(this_bus.bars_in_parallel)),  # per bar
            )
            node_index[(this_key, i)] = len(nodes)
            nodes.append(nd)

        # axial conduction (chain)
        for i, seg in enumerate(segments):
            gi = node_index[(this_key, i)]
            A = seg.width_m * seg.thickness_m
            dx = seg.length_m
            G = K_CU * A / max(dx, 1e-9)
            if i < len(segments) - 1:
                gj = node_index[(this_key, i + 1)]
                edges.append(Edge(gi, gj, G))
                edges.append(Edge(gj, gi, G))

        end_idx = len(nodes) - 1
        return start_idx, end_idx, segments

    def connect_parent_to_child(parent_key: str, child_key: str, x_m: float):
        parent_segments = segments_by_key[parent_key]
        child_segments = segments_by_key[child_key]

        tee_local = _attach_local_index(parent_segments, x_m)
        tee_global = node_index[(parent_key, tee_local)]
        first_child = node_index[(child_key, 0)]

        # Simple copper-equivalent conduction across tee (you can later replace with contact conductance)
        Atee = min(
            parent_segments[tee_local].width_m * parent_segments[tee_local].thickness_m,
            child_segments[0].width_m * child_segments[0].thickness_m,
        )
        # empirical busbar contact conductance
        H_CONTACT = 20000.0  # W/m²K typical bolted copper joint

        overlap_width = min(
            parent_segments[tee_local].width_m,
            child_segments[0].width_m,
        )

        # assume overlap length equal to thickness for now
        overlap_length = min(
            parent_segments[tee_local].thickness_m,
            child_segments[0].thickness_m,
        )

        A_contact = overlap_width * overlap_length

        Gtee = H_CONTACT * A_contact

        edges.append(Edge(tee_global, first_child, Gtee))
        edges.append(Edge(first_child, tee_global, Gtee))

    def traverse(this_bus: BusbarSpec, this_key: str):
        add_bus_nodes_and_edges(this_bus, this_key)

        # recurse children
        for child_idx, br in enumerate(getattr(this_bus, "branches", []) or []):
            child_bus = br.branch
            child_key = _bus_key(this_key, child_bus, child_idx)

            traverse(child_bus, child_key)
            connect_parent_to_child(this_key, child_key, br.x_m)

    root_key = _bus_key("", bus, 0)
    traverse(bus, root_key)

    return nodes, edges


def solve_busbar_network(bus: BusbarSpec, air_temp_C: float, max_iter: int = 40, tol: float = 1e-3) -> BusbarNetworkSolveResult:
    nodes, edges = build_busbar_network(bus, air_temp_C)
    n = len(nodes)

    # adjacency list
    nbrs: List[List[Tuple[int, float]]] = [[] for _ in range(n)]
    for e in edges:
        nbrs[e.i].append((e.j, e.G_W_per_K))

    # shared thermal inputs (air speed, emissivities, AC factor). Current handled per-node.
    therm = BusbarThermalInputs(
        I_total_A=0.0,  # NOT used for I²R in this solver
        eps_bus=bus.eps_bus,
        eps_env=bus.eps_env,
        v_mps=bus.v_mps,
        S_ac=bus.S_ac,
    )

    T = np.full(n, air_temp_C + 40.0, dtype=float)
    trace: List[np.ndarray] = []

    physics_cache: List[object | None] = [None] * n
    physics_cache_T = np.full(n, np.nan, dtype=float)

    def residual(Tvec: np.ndarray) -> np.ndarray:
        f = np.zeros(n, dtype=float)

        for i, nd in enumerate(nodes):
            Ti = float(Tvec[i])

            # Cache st only if same Ti (exact float match)
            # (Good enough for line search step testing; keeps it simple)
            if physics_cache[i] is None or physics_cache_T[i] != Ti:
                physics_cache[i] = compute_busbar_physics(
                    geom=nd.geom,
                    therm=therm,
                    T_bus_C=Ti,
                    T_air_C=air_temp_C,
                    I_override_A=nd.I_seg_A,
                )
                physics_cache_T[i] = Ti

            st = physics_cache[i]
            P_out = (st.P_conv_W_per_m + st.P_rad_W_per_m) * nd.length_m

            # copper + joint electrical heating using per-node current
            R20_per_m = resistance_20C_per_m(nd.width_m, nd.thickness_m)  # Ω/m @20
            Rcu_per_m = resistance_T_per_m(R20_per_m, Ti)  # Ω/m @Ti
            Rcu_seg = Rcu_per_m * nd.length_m
            P_gen = (nd.I_seg_A ** 2) * Rcu_seg * therm.S_ac

            if nd.extra_R20_ohm_per_m > 0.0:
                Rj20 = nd.extra_R20_ohm_per_m * nd.length_m
                Rj = resistance_T_per_m(Rj20, Ti)
                P_gen += (nd.I_seg_A ** 2) * Rj

            # conduction to neighbours
            P_cond = 0.0
            for j, G in nbrs[i]:
                P_cond += G * (Tvec[j] - Tvec[i])

            f[i] = P_gen - P_out + P_cond

        return f

    def _dPgen_dT_W_per_node(nd: Node, Ti_C: float, therm: BusbarThermalInputs) -> float:
        """
        Analytic derivative of electrical heating wrt temperature at node i.

        Copper:
          R(T) = R20 * (1 + alpha*(T-20))
          P = I^2 * R(T)*L * S_ac
          dP/dT = I^2 * (R20*alpha)*L * S_ac

        Joint/contact (extra_R20_ohm_per_m):
          Rj(T) = Rj20 * (1 + alpha*(T-20))
          Pj = I^2 * Rj(T)
          dPj/dT = I^2 * Rj20 * alpha
        """
        alpha = ALPHA_CU
        I2 = float(nd.I_seg_A) ** 2

        # copper portion
        R20_per_m = resistance_20C_per_m(nd.width_m, nd.thickness_m)
        dRcu_per_m_dT = R20_per_m * alpha
        dP_cu = I2 * (dRcu_per_m_dT * nd.length_m) * therm.S_ac

        # joint portion (note: in your residual you do NOT multiply joint loss by S_ac)
        dP_j = 0.0
        if nd.extra_R20_ohm_per_m > 0.0:
            Rj20_seg = float(nd.extra_R20_ohm_per_m) * nd.length_m
            dP_j = I2 * Rj20_seg * alpha

        return dP_cu + dP_j

    def _dPout_dT_fd(nd: Node, Ti_C: float, dT: float = 0.05) -> float:
        """
        Local finite-difference derivative for surface losses (convection+radiation),
        aligned to compute_busbar_physics().
        """
        st0 = compute_busbar_physics(
            geom=nd.geom,
            therm=therm,
            T_bus_C=Ti_C,
            T_air_C=air_temp_C,
            I_override_A=nd.I_seg_A,
        )
        st1 = compute_busbar_physics(
            geom=nd.geom,
            therm=therm,
            T_bus_C=Ti_C + dT,
            T_air_C=air_temp_C,
            I_override_A=nd.I_seg_A,
        )
        Pout0 = (st0.P_conv_W_per_m + st0.P_rad_W_per_m) * nd.length_m
        Pout1 = (st1.P_conv_W_per_m + st1.P_rad_W_per_m) * nd.length_m
        return (Pout1 - Pout0) / dT

    def jacobian_structured(Tvec: np.ndarray):
        """
        Jacobian exploiting graph sparsity:
          f_i = Pgen_i(Ti) - Pout_i(Ti) + sum_j G_ij*(Tj - Ti)

        => df_i/dTj = G_ij for j != i
           df_i/dTi = dPgen/dTi - dPout/dTi - sum_j G_ij
        """
        J = lil_matrix((n, n), dtype=float)

        # Off-diagonals from conduction, and accumulate diag -sum(G)
        sumG = np.zeros(n, dtype=float)
        for i in range(n):
            for j, G in nbrs[i]:
                if j == i:
                    continue
                J[i, j] += G
                sumG[i] += G

        # Diagonal from local physics derivatives
        for i, nd in enumerate(nodes):
            Ti = float(Tvec[i])
            dPgen = _dPgen_dT_W_per_node(nd, Ti, therm)
            dPout = _dPout_dT_fd(nd, Ti)
            J[i, i] = dPgen - dPout - sumG[i]

        return J

    def jacobian_fd(Tvec: np.ndarray, f0: np.ndarray, h: float = 1e-3) -> np.ndarray:
        J = np.zeros((n, n), dtype=float)
        for k in range(n):
            Tp = Tvec.copy()
            Tp[k] += h
            fp = residual(Tp)
            J[:, k] = (fp - f0) / h
        return J

    converged = False
    for it in range(max_iter):
        f = residual(T)
        trace.append(T.copy())

        max_f = float(np.max(np.abs(f)))
        if max_f < tol:
            converged = True
            break

        J = jacobian_structured(T)

        try:
            dT = spsolve(J.tocsr(), -f)
        except np.linalg.LinAlgError:
            # crude fallback: damped step in negative residual direction
            dT = -0.1 * f

        # clamp + damp
        dT = np.clip(dT, -25.0, 25.0)

        lam = 1.0
        f_norm = np.max(np.abs(f))

        while lam > 0.05:
            T_trial = np.maximum(T + lam * dT, air_temp_C)
            f_trial = residual(T_trial)

            if np.max(np.abs(f_trial)) < f_norm:
                T = T_trial
                break

            lam *= 0.5
        else:
            T = np.maximum(T + 0.1 * dT, air_temp_C)

        print(f"[net iter {it:02d}] max|f|={max_f:.3e}  Tmin={float(np.min(T)):.2f}  Tmax={float(np.max(T)):.2f}")

    # compute total electrical loss at final T
    P_total = 0.0
    for i, nd in enumerate(nodes):
        Ti = float(T[i])
        R20_per_m = resistance_20C_per_m(nd.width_m, nd.thickness_m)
        Rcu_per_m = resistance_T_per_m(R20_per_m, Ti)
        Rcu_seg = Rcu_per_m * nd.length_m
        P_seg = (nd.I_seg_A ** 2) * Rcu_seg * therm.S_ac
        if nd.extra_R20_ohm_per_m > 0.0:
            Rj20 = nd.extra_R20_ohm_per_m * nd.length_m
            Rj = resistance_T_per_m(Rj20, Ti)
            P_seg += (nd.I_seg_A ** 2) * Rj
        P_total += P_seg

    return BusbarNetworkSolveResult(
        T_max_C=float(np.max(T)),
        T_vec_C=T,
        converged=converged,
        iterations=it + 1,
        P_total_loss_W=float(P_total),
        node_meta=nodes,
        trace=trace,
    )