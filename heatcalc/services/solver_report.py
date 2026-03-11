from __future__ import annotations

from heatcalc.services.logger import get_logger

log = get_logger()


def _fmt(v, nd=2):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


def dump_solver_state(result: dict, *, show_trace: bool = False) -> None:
    """
    Prints a full engineering snapshot of the solver result.

    Safe to call anywhere after calc_tier_iec60890_coupled().
    """

    if not result:
        log.warning("dump_solver_state called with empty result")
        return

    log.info("")
    log.info("====================================================")
    log.info("              HEATCALC SOLVER REPORT")
    log.info("====================================================")

    # --------------------------------------------------
    # ENCLOSURE TEMPERATURES
    # --------------------------------------------------
    log.info("ENCLOSURE TEMPERATURES")
    log.info("----------------------")

    log.info(f"Ambient temperature      : {_fmt(result.get('ambient_C'),2)} C")
    log.info(f"Mid air temperature      : {_fmt(result.get('T_mid'),2)} C")
    log.info(f"0.75H air temperature    : {_fmt(result.get('T_075'),2)} C")
    log.info(f"Top air temperature      : {_fmt(result.get('T_top'),2)} C")

    log.info("")

    # --------------------------------------------------
    # BUSBAR RESULTS
    # --------------------------------------------------
    busbars = result.get("busbars") or []

    if busbars:

        log.info("BUSBAR RESULTS")
        log.info("--------------")

        total_bus_loss = 0.0

        for b in busbars:

            log.info("--------------------------------------------------")
            log.info(f"Busbar: {b.get('name','BUS')}")

            log.info(
                f"Geometry               : {_fmt(b.get('width_mm'),1)} mm x {_fmt(b.get('thickness_mm'),1)} mm"
            )
            log.info(f"Length                 : {_fmt(b.get('length_m'),3)} m")
            log.info(f"L_char                 : {_fmt(b.get('L_char_mm'),1)} mm")

            log.info(f"Bars in parallel       : {b.get('bars_in_parallel')}")
            log.info(f"Current total          : {_fmt(b.get('I_total_A'),2)} A")
            log.info(f"Current per bar        : {_fmt(b.get('I_bar_A'),2)} A")

            log.info(f"Convection mode        : {b.get('convection_mode')}")
            log.info(f"Face-to-face dimension : {b.get('face_to_face_dim')}")
            log.info(
                f"Air temp reference     : {b.get('use_air_temp')} (T_air={_fmt(b.get('T_air_C'),2)} C)"
            )

            log.info("")
            log.info(f"Busbar temperature     : {_fmt(b.get('T_bus_C'),2)} C")
            log.info(f"Total busbar loss      : {_fmt(b.get('P_loss_W'),2)} W")

            total_bus_loss += float(b.get("P_loss_W") or 0.0)

            log.info("")
            log.info(
                f"R20                    : {float(b.get('R20_ohm_per_m') or 0.0):.6e} Ohm/m"
            )
            log.info(
                f"R(T)                   : {float(b.get('R_T_ohm_per_m') or 0.0):.6e} Ohm/m"
            )

            log.info("")
            log.info(f"P_gen                  : {_fmt(b.get('P_gen_W_per_m'),3)} W/m")
            log.info(f"P_conv                 : {_fmt(b.get('P_conv_W_per_m'),3)} W/m")
            log.info(f"P_rad                  : {_fmt(b.get('P_rad_W_per_m'),3)} W/m")

            log.info("")
            log.info(f"W_conv                 : {_fmt(b.get('W_conv_W_m2'),3)} W/m^2")
            log.info(f"W_rad                  : {_fmt(b.get('W_rad_W_m2'),3)} W/m^2")

            log.info("")
            log.info(f"As_conv                : {_fmt(b.get('As_conv_m2_per_m'),5)} m^2/m")
            log.info(f"As_rad_raw             : {_fmt(b.get('As_rad_raw_m2_per_m'),5)} m^2/m")
            log.info(f"As_rad_effective       : {_fmt(b.get('As_rad_eff_m2_per_m'),5)} m^2/m")

            blk = float(b.get("rad_blockage_frac") or 0.0) * 100.0
            log.info(f"Radiation blockage     : {blk:.1f} %")

            log.info("")
            log.info(
                f"Busbar solver          : {b.get('solver_method')} | converged={b.get('solver_converged')} | iters={b.get('solver_iterations')}"
            )

            if show_trace and b.get("trace"):

                log.info("")
                log.info("Trace (Newton iterations):")

                for i, t in enumerate(b["trace"], start=1):

                    log.info(
                        f"  {i:02d}: "
                        f"T_bus={_fmt(t.get('T_bus_C'),2)} C | "
                        f"f={_fmt(t.get('f_W_per_m'),4)} W/m"
                    )

        log.info("")
        log.info(f"Total busbar loss        : {total_bus_loss:.2f} W")
        log.info("")

    # --------------------------------------------------
    # COUPLING SOLVER
    # --------------------------------------------------
    coupling = result.get("coupling")

    if coupling:

        log.info("COUPLING SOLVER")
        log.info("---------------")

        log.info(f"Converged                : {coupling.get('converged')}")
        log.info(f"Iterations               : {coupling.get('iterations')}")

        hist = coupling.get("history") or []

        if hist:

            first = hist[0]
            last = hist[-1]

            log.info("")
            log.info("Coupling summary:")

            log.info(f"  Start T_air_top        : {_fmt(first.get('T_top'),2)} C")
            log.info(f"  Final T_air_top        : {_fmt(last.get('T_top'),2)} C")

            log.info(f"  Final P_guess          : {_fmt(last.get('P_guess'),2)} W")
            log.info(f"  Final P_calc           : {_fmt(last.get('P_calc'),2)} W")

            res = last.get("residual")
            if res is not None:
                log.info(f"  Final residual         : {_fmt(res,4)} W")

        if busbars:
            max_bus_temp = max(b.get("T_bus_C", 0.0) for b in busbars)
            avg_bus_temp = sum(b.get("T_bus_C", 0.0) for b in busbars) / len(busbars)

            log.info("")
            log.info("BUSBAR OPERATING TEMPERATURE")
            log.info("----------------------------")
            log.info(f"Maximum busbar temperature : {_fmt(max_bus_temp, 2)} C")
            log.info(f"Average busbar temperature : {_fmt(avg_bus_temp, 2)} C")

            if "T_top" in result:
                rise = max_bus_temp - result["T_top"]
                log.info(f"Rise above air (max)      : {_fmt(rise, 2)} C")

    log.info("")
    log.info("====================================================")
    log.info("")