# heatcalc/core/bus_thermal_solver.py
from __future__ import annotations

from collections import defaultdict
import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

from heatcalc.core.busbar_geometry import BusbarGeometry
from heatcalc.core.busbar_physics import (
    compute_busbar_physics,
    resistance_20C_per_m,
    resistance_T_per_m,
    BusbarThermalInputs,
    ALPHA_CU,
)

K_CU = 400.0


def solve_thermal(graph, air_temp_C: float, debug: bool = False, max_iter: int = 40, tol: float = 1e-3):
    """
    Graph-edge thermal solve with fixed ambient air temperature.
    This is NOT the coupled IEC 60890 solve.
    """

    thermal_nodes = []
    thermal_edges = []

    # --------------------------------------------------------
    # Build thermal nodes: one node per graph edge
    # --------------------------------------------------------
    for e in graph.edges.values():
        width_m = e.spec.width_mm / 1000.0
        th_m = e.spec.thickness_mm / 1000.0

        geom = BusbarGeometry(
            name=f"edge-{e.id}",
            width_m=width_m,
            thickness_m=th_m,
            L_char_m=width_m,
            length_m=e.length_m,
            bars_in_parallel=e.spec.bars_in_parallel,
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
            "extra_R20_ohm_per_m": 0.0,
        })

    edge_map = {eid: i for i, eid in enumerate(graph.edges.keys())}

    # --------------------------------------------------------
    # Add join/contact resistance to incident edges
    # --------------------------------------------------------
    if getattr(graph, "joins", None):
        for j in graph.joins:
            incident = []
            for e in graph.edges.values():
                if j.node in (e.u, e.v):
                    incident.append(e.id)

            for eid in incident:
                i = edge_map[eid]
                L = max(thermal_nodes[i]["L"], 1e-9)
                thermal_nodes[i]["extra_R20_ohm_per_m"] += (j.R_contact_20_uohm * 1e-6) / L

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

            A_contact = min(thermal_nodes[ia]["area"], thermal_nodes[ib]["area"])

            # heat travels from centre of segment through joint and to centre of next segment
            L_char = 0.5 * thermal_nodes[ia]["L"] + 0.5 * thermal_nodes[ib]["L"]

            #todo derive this mathematically, the contact thermal resistance
            R_cu = L_char / (K_CU * A_contact)
            # R_contact = j.R_th_contact  # new parameter
            R_contact = 0.002
            G = 1.0 / (R_cu + R_contact)

            thermal_edges.append((ia, ib, G))
            thermal_edges.append((ib, ia, G))

    n = len(thermal_nodes)
    nbrs = [[] for _ in range(n)]
    for i, j, G in thermal_edges:
        nbrs[i].append((j, G))

    therm = BusbarThermalInputs(
        I_total_A=0.0,
        eps_bus=0.7,
        eps_env=0.9,
        v_mps=0.0,
        S_ac=1.0,
    )

    # Initial guess above ambient
    T = np.full(n, air_temp_C + 20.0, dtype=float)

    def residual(Tvec: np.ndarray) -> np.ndarray:
        f = np.zeros(n, dtype=float)

        for i, nd in enumerate(thermal_nodes):
            Ti = float(Tvec[i])

            st = compute_busbar_physics(
                geom=nd["geom"],
                therm=therm,
                T_bus_C=Ti,
                T_air_C=air_temp_C,
                I_override_A=nd["I"],
                debug=True,
            )

            P_out = (st.P_conv_W_per_m + st.P_rad_W_per_m) * nd["L"]

            R20 = resistance_20C_per_m(nd["w"], nd["t"])
            RT = resistance_T_per_m(R20, Ti)
            P_gen = (nd["I"] ** 2) * RT * nd["L"] * therm.S_ac

            if nd["extra_R20_ohm_per_m"] > 0.0:
                Rj20_seg = nd["extra_R20_ohm_per_m"] * nd["L"]
                Rj = Rj20_seg * (1.0 + ALPHA_CU * (Ti - 20.0))
                P_gen += (nd["I"] ** 2) * Rj

            P_cond = 0.0
            for j, G in nbrs[i]:
                P_cond += G * (Tvec[j] - Ti)

            f[i] = P_gen - P_out + P_cond

            if debug:
                print(f"\nEDGE {nd['edge_id']}")
                print(f"I = {nd['I']:.2f} A")
                print(f"T = {Ti:.2f} °C")
                print(f"R20 = {R20:.6f} Ω/m")
                print(f"RT  = {RT:.6f} Ω/m")
                print(f"P_gen  = {P_gen:.6f} W")
                print(f"P_conv = {(st.P_conv_W_per_m * nd['L']):.6f} W")
                print(f"P_rad  = {(st.P_rad_W_per_m * nd['L']):.6f} W")
                print(f"P_cond = {P_cond:.6f} W")
                print(f"Residual = {f[i]:.6f} W")

        return f

    def dPgen_dT(nd, Ti: float) -> float:
        I2 = float(nd["I"]) ** 2
        R20 = resistance_20C_per_m(nd["w"], nd["t"])
        dR_dT = R20 * ALPHA_CU
        dP = I2 * dR_dT * nd["L"] * therm.S_ac

        if nd["extra_R20_ohm_per_m"] > 0.0:
            Rj20_seg = nd["extra_R20_ohm_per_m"] * nd["L"]
            dP += I2 * Rj20_seg * ALPHA_CU

        return dP

    def dPout_dT_fd(nd, Ti: float, h: float = 0.05) -> float:
        st0 = compute_busbar_physics(
            geom=nd["geom"],
            therm=therm,
            T_bus_C=Ti,
            T_air_C=air_temp_C,
            I_override_A=nd["I"],
        )
        st1 = compute_busbar_physics(
            geom=nd["geom"],
            therm=therm,
            T_bus_C=Ti + h,
            T_air_C=air_temp_C,
            I_override_A=nd["I"],
        )

        P0 = (st0.P_conv_W_per_m + st0.P_rad_W_per_m) * nd["L"]
        P1 = (st1.P_conv_W_per_m + st1.P_rad_W_per_m) * nd["L"]
        return (P1 - P0) / h

    def jacobian(Tvec: np.ndarray):
        J = lil_matrix((n, n), dtype=float)
        sumG = np.zeros(n, dtype=float)

        for i in range(n):
            for j, G in nbrs[i]:
                if j == i:
                    continue
                J[i, j] += G
                sumG[i] += G

        for i, nd in enumerate(thermal_nodes):
            Ti = float(Tvec[i])
            J[i, i] = dPgen_dT(nd, Ti) - dPout_dT_fd(nd, Ti) - sumG[i]

        return J

    converged = False

    for it in range(max_iter):
        f = residual(T)
        max_f = float(np.max(np.abs(f)))

        if debug:
            print(f"\n--- Newton iteration {it} ---")
            print(f"Residual norm: {max_f:.6e}")
            print(f"T range: {np.min(T):.3f} to {np.max(T):.3f} °C")

        if max_f < tol:
            converged = True
            break

        J = jacobian(T)

        try:
            dT = np.asarray(spsolve(J.tocsr(), -f), dtype=float)
        except Exception:
            dT = -0.1 * f

        if not np.all(np.isfinite(dT)):
            raise RuntimeError("Thermal Newton step became non-finite.")

        dT = np.clip(dT, -25.0, 25.0)

        if debug:
            print("dT =", dT)

        # Backtracking line search + hard ambient floor
        lam = 1.0
        f_norm = max_f

        while lam > 0.05:
            T_trial = np.maximum(T + lam * dT, air_temp_C)
            f_trial = residual(T_trial)

            if np.max(np.abs(f_trial)) < f_norm:
                T = T_trial
                break

            lam *= 0.5
        else:
            T = np.maximum(T + 0.1 * dT, air_temp_C)

    if debug:
        print("\nFinal temperatures:", T)
        print("Converged:", converged)

    return T