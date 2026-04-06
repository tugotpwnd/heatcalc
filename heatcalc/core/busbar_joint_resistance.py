RHO_CU_OHM_M = 1.724e-8  # Ω·m


RHO_CU_OHM_M = 1.724e-8  # Ω·m


def streamline_resistance_ratio_from_overlap_ratio(overlap_ratio: float) -> float:
    """
    Figure 66 curvefit:
        e(x) = 0.547904 + (5.587766 - 0.547904) / (1 + (x / 0.4058952)^2.200232)

    where:
        x = overlap_ratio = l / b

    Returns the streamline resistance ratio e.
    """
    x = max(float(overlap_ratio), 1e-9)
    return 0.547904 + (5.587766 - 0.547904) / (
        1.0 + (x / 0.4058952) ** 2.200232
    )


def bolted_overlap_joint_resistance(
    width_m: float,
    thickness_m: float,
    overlap_m: float,
    bolt_count: int,
    bolt_dia_mm: float,
    torque_Nm: float,
    nut_factor: float = 0.20,
    e_streamline: float | None = None,
    other_bar_width_m: float | None = None,
    other_bar_thickness_m: float | None = None,
    debug: bool = False,
) -> float:
    """
    Calculate total electrical resistance of a bolted copper overlap joint.

    Host bar:
        width_m, thickness_m

    Other joined bar:
        other_bar_width_m, other_bar_thickness_m
        If omitted, falls back to the host-bar geometry.

    Streamline resistance is based on the overlap-ratio curvefit:
        x = l / b_eff
        e = e(x)

    where for unequal bars:
        a_eff = min(w1, w2)
        b_eff = max(t1, t2)

    Returns
    -------
    Joint resistance in ohms at 20°C.
    """

    # -----------------------------
    # Geometry
    # -----------------------------
    w1_m = float(width_m)
    t1_m = float(thickness_m)
    w2_m = float(other_bar_width_m) if other_bar_width_m is not None else w1_m
    t2_m = float(other_bar_thickness_m) if other_bar_thickness_m is not None else t1_m

    l_m = float(overlap_m)
    d_m = float(bolt_dia_mm) / 1000.0
    n = int(bolt_count)

    if min(w1_m, t1_m, w2_m, t2_m, l_m, d_m) <= 0:
        raise ValueError("Invalid bolted joint geometry supplied.")
    if n <= 0:
        raise ValueError("bolt_count must be >= 1.")
    if nut_factor <= 0:
        raise ValueError("nut_factor must be > 0.")

    # Effective dimensions for unequal bars
    a_eff_m = min(w1_m, w2_m)   # effective overlap width
    b_eff_m = max(t1_m, t2_m)   # effective streamline thickness

    # Number of bolt columns across width
    # - 1 bolt → 1 column
    # - 2+ bolts → max 2 columns (assumption: bolt patterns are always 2-wide)
    columns = min(n, 2)
    net_width_m = a_eff_m - columns * d_m

    if net_width_m <= 1e-12:
        raise ValueError("Joint net width became non-positive after bolt holes.")

    # engineering units
    a_eff_mm = a_eff_m * 1000.0
    b_eff_mm = b_eff_m * 1000.0
    l_mm = l_m * 1000.0
    d_mm = float(bolt_dia_mm)

    # -----------------------------
    # 1) Streamline resistance
    # -----------------------------
    overlap_ratio = l_m / max(b_eff_m, 1e-12)

    if e_streamline is None:
        e_used = streamline_resistance_ratio_from_overlap_ratio(overlap_ratio)
    else:
        e_used = float(e_streamline)

    Rs = (e_used * RHO_CU_OHM_M * l_m) / (net_width_m * b_eff_m)

    # -----------------------------
    # 2) Bolt preload
    # -----------------------------
    F_per_bolt_N = torque_Nm / (nut_factor * d_m)
    F_total_N = F_per_bolt_N * n

    # -----------------------------
    # 3) Pressure in N/mm²
    # -----------------------------
    A_overlap_mm2 = a_eff_mm * l_mm
    P_N_per_mm2 = F_total_N / A_overlap_mm2

    # -----------------------------
    # 4) Y from empirical plot
    #    Y is in micro-ohms
    # -----------------------------
    Y_uohm = 376.0758 + (6084.809 - 376.0758) / (
        1.0 + (P_N_per_mm2 / 9.943826) ** 2.103775
    )

    # -----------------------------
    # 5) Contact resistance
    #    Ri = Y / (a * l)
    # -----------------------------
    Ri_uohm = Y_uohm / A_overlap_mm2
    Ri = Ri_uohm * 1e-6

    if debug:
        print("\n[BOLTED JOINT DEBUG]")
        print(f"w1_mm            = {w1_m * 1000.0:.3f}")
        print(f"t1_mm            = {t1_m * 1000.0:.3f}")
        print(f"w2_mm            = {w2_m * 1000.0:.3f}")
        print(f"t2_mm            = {t2_m * 1000.0:.3f}")
        print(f"a_eff_mm         = {a_eff_mm:.3f}")
        print(f"b_eff_mm         = {b_eff_mm:.3f}")
        print(f"l_mm             = {l_mm:.3f}")
        print(f"d_mm             = {d_mm:.3f}")
        print(f"bolt_count       = {n}")
        print(f"overlap_ratio    = {overlap_ratio:.6f}")
        print(f"e_used           = {e_used:.6f}")
        print(f"net_width_mm     = {net_width_m * 1000.0:.3f}")
        print(f"F_total_N        = {F_total_N:.3f}")
        print(f"A_overlap_mm2    = {A_overlap_mm2:.3f}")
        print(f"P_N_per_mm2      = {P_N_per_mm2:.6f}")
        print(f"Y_uohm           = {Y_uohm:.6f}")
        print(f"Ri_uohm          = {Ri_uohm:.6f}")
        print(f"Rs_ohm           = {Rs:.6e}")
        print(f"Ri_ohm           = {Ri:.6e}")
        print(f"R_total_ohm      = {(Rs + Ri):.6e}")

    return Rs + Ri
RHO_CU_OHM_M = 1.724e-8  # Ω·m


def clamped_edge_joint_resistance(
    bar1_width_m: float,
    bar1_thickness_m: float,
    bar2_width_m: float,
    bar2_thickness_m: float,
    torque_Nm: float,
    clamp_bolt_dia_mm: float,
    nut_factor: float = 0.20,
    bolt_count: int = 1,
    e_streamline: float | None = None,
    debug: bool = False,
) -> float:
    """
    Calculate total electrical resistance of a clamped copper busbar joint.

    Clamped-joint geometric interpretation:
        a = smallest contacting thickness
        l = smallest thickness (streamline path length through contact region)
        b = width of the wider supporting major face

    Streamline ratio for clamped geometry:
        x = l / b

    The same streamline-effect curvefit is then applied using this clamped-joint
    geometry, rather than the bolted-overlap geometry.

    Returns
    -------
    Joint resistance in ohms at 20°C.
    """

    # -----------------------------
    # Geometry
    # -----------------------------
    w1_m = float(bar1_width_m)
    t1_m = float(bar1_thickness_m)
    w2_m = float(bar2_width_m)
    t2_m = float(bar2_thickness_m)

    if min(w1_m, t1_m, w2_m, t2_m) <= 0:
        raise ValueError("Invalid bar geometry supplied.")

    d_m = float(clamp_bolt_dia_mm) / 1000.0
    if d_m <= 0:
        raise ValueError("Clamp bolt diameter must be > 0.")

    if bolt_count <= 0:
        raise ValueError("bolt_count must be >= 1.")

    if nut_factor <= 0:
        raise ValueError("nut_factor must be > 0.")

    # convert to mm
    w1_mm = w1_m * 1000.0
    t1_mm = t1_m * 1000.0
    w2_mm = w2_m * 1000.0
    t2_mm = t2_m * 1000.0

    # -----------------------------
    # Clamped-joint characteristic geometry
    # -----------------------------
    a_mm = min(t1_mm, t2_mm)   # effective contact thickness
    l_mm = min(t1_mm, t2_mm)   # streamline path length
    b_mm = max(w1_mm, w2_mm)   # supporting major-face width

    a_m = a_mm / 1000.0
    l_m = l_mm / 1000.0
    b_m = b_mm / 1000.0

    # -----------------------------
    # 1) Streamline resistance
    # -----------------------------
    streamline_ratio = l_mm / max(b_mm, 1e-12)

    if e_streamline is None:
        e_used = streamline_resistance_ratio_from_overlap_ratio(streamline_ratio)
    else:
        e_used = float(e_streamline)

    Rs = (e_used * RHO_CU_OHM_M * l_m) / (a_m * b_m)

    # -----------------------------
    # 2) Clamp preload from torque
    # F_per_bolt = T / (K D)
    # -----------------------------
    F_per_bolt_N = float(torque_Nm) / (float(nut_factor) * d_m)
    F_total_N = F_per_bolt_N * int(bolt_count)

    # -----------------------------
    # 3) Pressure in N/mm²
    # -----------------------------
    A_pressure_mm2 = a_mm * b_mm
    P_N_per_mm2 = F_total_N / A_pressure_mm2

    # -----------------------------
    # 4) Y from Figure 69 fit
    # Y is in micro-ohms
    # -----------------------------
    Y_uohm = 0.570367 + (6033974.0 - 0.570367) / (
        1.0 + (P_N_per_mm2 / 0.0001665935) ** 1.895038
    )

    # -----------------------------
    # 5) Contact resistance
    # Ri = Y / (contact area)
    # -----------------------------
    Ri_uohm = Y_uohm / (a_mm * b_mm)
    Ri = Ri_uohm * 1e-6

    if debug:
        print("\n[CLAMPED JOINT DEBUG]")
        print(f"bar1_width_mm       = {w1_mm:.3f}")
        print(f"bar1_thickness_mm   = {t1_mm:.3f}")
        print(f"bar2_width_mm       = {w2_mm:.3f}")
        print(f"bar2_thickness_mm   = {t2_mm:.3f}")
        print(f"a_mm                = {a_mm:.3f}")
        print(f"l_mm                = {l_mm:.3f}")
        print(f"b_mm                = {b_mm:.3f}")
        print(f"streamline_ratio    = {streamline_ratio:.6f}")
        print(f"e_used              = {e_used:.6f}")
        print(f"d_m                 = {d_m:.6f}")
        print(f"bolt_count          = {bolt_count}")
        print(f"torque_Nm           = {torque_Nm:.3f}")
        print(f"nut_factor          = {nut_factor:.3f}")
        print(f"F_per_bolt_N        = {F_per_bolt_N:.3f}")
        print(f"F_total_N           = {F_total_N:.3f}")
        print(f"A_pressure_mm2      = {A_pressure_mm2:.3f}")
        print(f"P_N_per_mm2         = {P_N_per_mm2:.6f}")
        print(f"Y_uohm              = {Y_uohm:.6f}")
        print(f"Ri_uohm             = {Ri_uohm:.6f}")
        print(f"Rs_ohm              = {Rs:.6e}")
        print(f"Ri_ohm              = {Ri:.6e}")
        print(f"R_total_ohm         = {(Rs + Ri):.6e}")

    return Rs + Ri