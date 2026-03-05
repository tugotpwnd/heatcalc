from __future__ import annotations

from heatcalc.services.logger import get_logger

from heatcalc.core.iec60890_calc import calc_tier_iec60890
from heatcalc.core.busbar_geometry import from_busbarspec_mm
from heatcalc.core.busbar_physics import BusbarThermalInputs
from heatcalc.core.busbar_solver import solve_busbar_temperature
from heatcalc.core.busbar_physics import resistance_20C_per_m

log = get_logger()


def _select_busbar_ambient(result_60890: dict, which: str) -> float:
    w = (which or "top").lower().strip()
    if w == "mid":
        return float(result_60890["T_mid"])
    if w == "top":
        return float(result_60890["T_top"])
    if w in ("075", "t075", "0.75", "t_075"):
        t = result_60890.get("T_075", None)
        return float(t) if t is not None else float(result_60890["T_top"])
    raise ValueError("use_air_temp must be one of: 'mid','top','075'")


def _compute_busbar_loss_W(P_gen_W_per_m: float, length_m: float) -> float:
    return float(P_gen_W_per_m) * float(length_m)

def _estimate_busbar_heat_R20(busbars) -> float:
    """
    Rough electrical heat estimate using I^2 * R20.

    Notes:
    - Uses DC R20 for copper (temperature-independent seed).
    - Includes parallel bars correctly (I splits, losses add).
    - Includes S_ac multiplier because your model uses it for electrical heating.
    """
    P_total = 0.0

    for b in busbars:
        geom = from_busbarspec_mm(b)

        N = max(1, int(geom.bars_in_parallel))
        I_bar = float(b.I_total_A) / float(N)

        R20_per_m = resistance_20C_per_m(geom.width_m, geom.thickness_m)  # ohm/m
        S_ac = float(getattr(b, "S_ac", 1.0))

        # Per-bar electrical loss over length
        P_bar_W = (I_bar ** 2) * R20_per_m * S_ac * float(geom.length_m)

        # Total for all parallel bars
        P_total += float(N) * P_bar_W

    return float(P_total)

# ============================================================
# ONE FULL COUPLED EVALUATION
# ============================================================

def _evaluate_for_bus_heat(
    *,
    P_bus_guess,
    tier,
    tiers,
    wall_mounted,
    inlet_area_cm2,
    ambient_C,
    altitude_m,
    ip_rating_n,
    solar_delta_K,
    debug=False,
):

    P_base = float(getattr(tier, "total_heat_w", 0.0))
    P_total = P_base + P_bus_guess

    log.info("")
    log.info("----------------------------------------------------")
    log.info(f"COUPLED EVAL : Guess busbar heat = {P_bus_guess:.2f} W")
    log.info(f"Total enclosure heat = {P_total:.2f} W")
    log.info("Running IEC 60890 enclosure model")

    res = calc_tier_iec60890(
        tier=tier,
        tiers=tiers,
        wall_mounted=wall_mounted,
        inlet_area_cm2=inlet_area_cm2,
        ambient_C=ambient_C,
        altitude_m=altitude_m,
        ip_rating_n=ip_rating_n,
        solar_delta_K=solar_delta_K,
        P_override_W=P_total,
    )

    res["ambient_C"] = ambient_C

    log.info(
        f"60890 Results: T_top={res['T_top']:.2f}°C "
        f"T_mid={res['T_mid']:.2f}°C "
        f"T_075={res.get('T_075',0):.2f}°C"
    )

    # ------------------------------------------------
    # Solve busbars
    # ------------------------------------------------

    P_bus_calc = 0.0
    bus_results = []

    for b in tier.busbars:

        geom = from_busbarspec_mm(b)

        therm = BusbarThermalInputs(
            I_total_A=b.I_total_A,
            eps_bus=b.eps_bus,
            eps_env=b.eps_env,
            v_mps=b.v_mps,
            S_ac=b.S_ac,
        )

        Ta_bus = _select_busbar_ambient(res, b.use_air_temp)

        log.info("")
        log.info(f"Solving busbar: {b.name}")
        log.info(f"Air temperature used = {Ta_bus:.2f} °C")

        solve = solve_busbar_temperature(
            geom=geom,
            therm=therm,
            T_air_C=Ta_bus,
            tol_T=0.01,
            tol_f_W_per_m=1e-4,
            max_iter=50,
        )

        phys = solve.physics

        P_loss_W = _compute_busbar_loss_W(
            P_gen_W_per_m=phys.P_gen_W_per_m,
            length_m=geom.length_m,
        )

        P_bus_calc += P_loss_W

        log.info(
            f"Busbar solved: T_bus={phys.T_bus_C:.2f}°C "
            f"P_loss={P_loss_W:.2f} W "
            f"iterations={solve.iterations}"
        )

        bus_results.append({

            # -------------------------
            # identity
            # -------------------------
            "name": geom.name,

            # -------------------------
            # electrical
            # -------------------------
            "I_total_A": b.I_total_A,
            "I_bar_A": phys.I_bar_A,
            "bars_in_parallel": geom.bars_in_parallel,

            # -------------------------
            # geometry
            # -------------------------
            "width_mm": b.width_mm,
            "thickness_mm": b.thickness_mm,
            "L_char_mm": b.L_char_mm,
            "length_m": geom.length_m,

            # -------------------------
            # layout
            # -------------------------
            "convection_mode": geom.convection_mode,
            "face_to_face_dim": geom.face_to_face_dim,
            "use_air_temp": b.use_air_temp,

            # -------------------------
            # temperatures
            # -------------------------
            "T_air_C": phys.T_air_C,
            "T_bus_C": phys.T_bus_C,

            # -------------------------
            # losses
            # -------------------------
            "P_loss_W": P_loss_W,

            # -------------------------
            # resistance
            # -------------------------
            "R20_ohm_per_m": phys.R20_ohm_per_m,
            "R_T_ohm_per_m": phys.R_T_ohm_per_m,

            # -------------------------
            # heat balance terms
            # -------------------------
            "P_gen_W_per_m": phys.P_gen_W_per_m,
            "P_conv_W_per_m": phys.P_conv_W_per_m,
            "P_rad_W_per_m": phys.P_rad_W_per_m,

            # -------------------------
            # heat flux
            # -------------------------
            "W_conv_W_m2": phys.W_conv_W_m2,
            "W_rad_W_m2": phys.W_rad_W_m2,

            # -------------------------
            # areas
            # -------------------------
            "As_conv_m2_per_m": phys.As_conv_m2_per_m,
            "As_rad_raw_m2_per_m": phys.As_rad_raw_m2_per_m,
            "As_rad_eff_m2_per_m": phys.As_rad_eff_m2_per_m,
            "rad_blockage_frac": phys.rad_blockage_frac,

            # -------------------------
            # solver info
            # -------------------------
            "solver_method": solve.method,
            "solver_iterations": solve.iterations,
            "solver_converged": solve.converged,

            # optional debug trace
            "trace": solve.trace if debug else None,
        })

    log.info("")
    log.info(f"Computed busbar heat = {P_bus_calc:.2f} W")

    return res, bus_results, P_bus_calc


# ============================================================
# ROOT SOLVER (SECANT METHOD)
# ============================================================

def calc_tier_iec60890_coupled(
    *,
    tier,
    tiers,
    wall_mounted,
    inlet_area_cm2,
    ambient_C,
    altitude_m,
    ip_rating_n,
    solar_delta_K=0.0,
    max_iter=20,
    tol_P=0.5,
    debug=False,
):

    log.info("")
    log.info("====================================================")
    log.info("START COUPLED BUSBAR + IEC60890 SOLVER")
    log.info("====================================================")

    if not getattr(tier, "busbars", None):
        return calc_tier_iec60890(
            tier=tier,
            tiers=tiers,
            wall_mounted=wall_mounted,
            inlet_area_cm2=inlet_area_cm2,
            ambient_C=ambient_C,
            altitude_m=altitude_m,
            ip_rating_n=ip_rating_n,
            solar_delta_K=solar_delta_K,
        )

    for b in tier.busbars:
        b.validate()

    # ------------------------------------------------------------
    # Initial guesses for secant:
    #   P0 = 0 W (cold enclosure seed)
    #   P1 = 1.2 * I^2 R20 seed (more realistic than hard-coded values and biased slightly above operating point)
    # ------------------------------------------------------------
    P0 = 0.0
    P1 = 1.2 * _estimate_busbar_heat_R20(tier.busbars)

    # Protect against silly-zero (e.g. bad geometry) and ensure separation
    P1 = max(P1, 1.0)

    res0, bus0, Pcalc0 = _evaluate_for_bus_heat(
        P_bus_guess=P0,
        tier=tier,
        tiers=tiers,
        wall_mounted=wall_mounted,
        inlet_area_cm2=inlet_area_cm2,
        ambient_C=ambient_C,
        altitude_m=altitude_m,
        ip_rating_n=ip_rating_n,
        solar_delta_K=solar_delta_K,
    )

    F0 = P0 - Pcalc0

    res1, bus1, Pcalc1 = _evaluate_for_bus_heat(
        P_bus_guess=P1,
        tier=tier,
        tiers=tiers,
        wall_mounted=wall_mounted,
        inlet_area_cm2=inlet_area_cm2,
        ambient_C=ambient_C,
        altitude_m=altitude_m,
        ip_rating_n=ip_rating_n,
        solar_delta_K=solar_delta_K,
    )

    F1 = P1 - Pcalc1

    thermal_gain = (Pcalc1 - Pcalc0) / (P1 - P0) if abs(P1 - P0) > 1e-9 else 0.0

    log.info(f"Thermal feedback gain ~= {thermal_gain:.3f}")
    # ------------------------------------------------------------
    # Ensure the two starting points give a usable secant slope
    # ------------------------------------------------------------
    expand_count = 0

    while abs(F1 - F0) < 1e-9 and expand_count < 6:
        log.info("Expanding initial guess range")

        P1 *= 2.0

        res1, bus1, Pcalc1 = _evaluate_for_bus_heat(
            P_bus_guess=P1,
            tier=tier,
            tiers=tiers,
            wall_mounted=wall_mounted,
            inlet_area_cm2=inlet_area_cm2,
            ambient_C=ambient_C,
            altitude_m=altitude_m,
            ip_rating_n=ip_rating_n,
            solar_delta_K=solar_delta_K,
        )

        F1 = P1 - Pcalc1
        expand_count += 1

    F1 = P1 - Pcalc1

    history = []

    history.append({
        "iteration": 0,
        "P_guess": P0,
        "P_calc": Pcalc0,
        "residual": F0,
        "T_top": res0["T_top"],
    })

    history.append({
        "iteration": 1,
        "P_guess": P1,
        "P_calc": Pcalc1,
        "residual": F1,
        "T_top": res1["T_top"],
    })

    for k in range(max_iter):

        if abs(F1 - F0) < 1e-9:
            break

        P2 = P1 - F1 * (P1 - P0) / (F1 - F0)

        log.info("")
        log.info(f"SECANT ITERATION {k+1}")
        log.info(f"P_guess = {P2:.3f}")

        res2, bus2, Pcalc2 = _evaluate_for_bus_heat(
            P_bus_guess=P2,
            tier=tier,
            tiers=tiers,
            wall_mounted=wall_mounted,
            inlet_area_cm2=inlet_area_cm2,
            ambient_C=ambient_C,
            altitude_m=altitude_m,
            ip_rating_n=ip_rating_n,
            solar_delta_K=solar_delta_K,
        )

        F2 = P2 - Pcalc2

        history.append(
            {
                "iteration": k + 1,
                "P_guess": P2,
                "P_calc": Pcalc2,
                "residual": F2,
                "T_top": res2["T_top"],
            }
        )

        log.info(
            f"Residual = {F2:.4f}  | "
            f"T_top={res2['T_top']:.2f}°C"
        )

        if abs(F2) < tol_P:
            log.info("")
            log.info("COUPLED SOLUTION CONVERGED")

            res2["busbars"] = bus2
            res2["coupling"] = {
                "method": "secant",
                "iterations": k + 1,
                "history": history,
                "converged": True,
            }

            return res2

        P0, F0 = P1, F1
        P1, F1 = P2, F2


    log.warning("COUPLED SOLVER DID NOT CONVERGE")

    res2["busbars"] = bus2
    res2["coupling"] = {
        "method": "secant",
        "iterations": max_iter,
        "history": history,
        "converged": False,
    }

    return res2