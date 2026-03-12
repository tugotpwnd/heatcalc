RHO_CU_OHM_M = 1.724e-8  # Ω·m


def bolted_overlap_joint_resistance(
    width_m: float,
    thickness_m: float,
    overlap_m: float,
    bolt_count: int,
    bolt_dia_mm: float,
    torque_Nm: float,
    nut_factor: float = 0.20,
    e_streamline: float = 0.5,
    debug=False,
) -> float:
    """
    Calculate total electrical resistance of a bolted copper overlap joint.

    Source-consistent interpretation:
      - geometric dimensions handled in mm where needed
      - pressure in N/mm²
      - Y from empirical plot in micro-ohms

    Returns:
        Joint resistance in ohms.
    """

    # -----------------------------
    # Geometry
    # -----------------------------
    a_m = float(width_m)
    b_m = float(thickness_m)
    l_m = float(overlap_m)
    d_m = float(bolt_dia_mm) / 1000.0
    n = int(bolt_count)

    if a_m <= 0 or b_m <= 0 or l_m <= 0 or d_m <= 0 or n < 0:
        raise ValueError("Invalid joint geometry supplied.")

    # engineering units for empirical relations
    a_mm = a_m * 1000.0
    b_mm = b_m * 1000.0
    l_mm = l_m * 1000.0
    d_mm = float(bolt_dia_mm)

    net_width_m = a_m - n * d_m
    if net_width_m <= 1e-12:
        raise ValueError("Joint net width became non-positive after bolt holes.")

    # -----------------------------
    # 1) Streamline resistance
    #    keep this in SI directly
    # -----------------------------
    Rs = (float(e_streamline) * RHO_CU_OHM_M * l_m) / (net_width_m * b_m)

    # -----------------------------
    # 2) Bolt preload
    # -----------------------------
    if nut_factor <= 0:
        raise ValueError("nut_factor must be > 0.")

    # F = T / (K d) per bolt, d in m
    F_per_bolt_N = torque_Nm / (nut_factor * d_m)
    F_total_N = F_per_bolt_N * n

    # -----------------------------
    # 3) Pressure in N/mm²
    # -----------------------------
    A_overlap_mm2 = a_mm * l_mm
    P_N_per_mm2 = F_total_N / A_overlap_mm2

    # -----------------------------
    # 4) Y from empirical plot
    #    Y is in micro-ohms
    # -----------------------------
    Y_uohm = 376.0758 + (6084.809 - 376.0758) / (
        1.0 + (P_N_per_mm2 / 9.943826) ** 2.103775
    )

    # contact resistance in micro-ohms:
    # Ri = Y / (a*l)
    Ri_uohm = Y_uohm / A_overlap_mm2

    # convert µΩ -> Ω
    Ri = Ri_uohm * 1e-6

    if debug:
        print("\n[JOINT DEBUG]")
        print(f"a_mm         = {a_mm:.3f}")
        print(f"b_mm         = {b_mm:.3f}")
        print(f"l_mm         = {l_mm:.3f}")
        print(f"d_mm         = {d_mm:.3f}")
        print(f"bolt_count   = {n}")
        print(f"F_total_N    = {F_total_N:.3f}")
        print(f"A_overlap_mm2= {A_overlap_mm2:.3f}")
        print(f"P_N_per_mm2  = {P_N_per_mm2:.6f}")
        print(f"Y_uohm       = {Y_uohm:.6f}")
        print(f"Ri_uohm      = {Ri_uohm:.6f}")
        print(f"Rs_ohm       = {Rs:.6e}")
        print(f"Ri_ohm       = {Ri:.6e}")
        print(f"R_total_ohm  = {(Rs + Ri):.6e}")

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
    e_streamline: float = 0.5,
    debug: bool = False,
) -> float:
    """
    Calculate total electrical resistance of a clamped copper busbar joint.

    Uses torque to estimate clamping force:
        T = K F D  ->  F = T / (K D)

    Contact resistance:
        Ri = Y / (a l)

    Streamline resistance:
        Rs = e rho l / (a b)

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
    w1_mm = w1_m * 1000
    t1_mm = t1_m * 1000
    w2_mm = w2_m * 1000
    t2_mm = t2_m * 1000

    # -----------------------------
    # Contact patch (edge clamp)
    # -----------------------------
    a_mm = min(t1_mm, t2_mm)
    l_mm = max(t1_mm, t2_mm)

    # streamline thickness
    b_mm = a_mm

    a_m = a_mm / 1000
    b_m = b_mm / 1000
    l_m = l_mm / 1000

    # -----------------------------
    # 1) Streamline resistance
    # Rs = e rho l / (a b)
    # -----------------------------
    Rs = (float(e_streamline) * RHO_CU_OHM_M * l_m) / (a_m * b_m)

    # -----------------------------
    # 2) Clamp preload from torque
    # F_per_bolt = T / (K D)
    # -----------------------------
    F_per_bolt_N = float(torque_Nm) / (float(nut_factor) * d_m)
    F_total_N = F_per_bolt_N * int(bolt_count)

    # -----------------------------
    # 3) Pressure in N/mm²
    # -----------------------------
    A_pressure_mm2 = a_mm * l_mm
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
    # Ri = Y / (a l)
    # -----------------------------
    Ri_uohm = Y_uohm / (a_mm * l_mm)
    Ri = Ri_uohm * 1e-6

    if debug:
        print("\n[CLAMPED JOINT DEBUG]")
        print(f"bar1_width_mm      = {w1_mm:.3f}")
        print(f"bar1_thickness_mm  = {t1_mm:.3f}")
        print(f"bar2_width_mm      = {w2_mm:.3f}")
        print(f"bar2_thickness_mm  = {t2_mm:.3f}")
        print(f"a_mm               = {a_mm:.3f}")
        print(f"b_mm               = {b_mm:.3f}")
        print(f"l_mm               = {l_mm:.3f}")
        print(f"d_mm               = {d_mm:.3f}")
        print(f"bolt_count         = {bolt_count}")
        print(f"torque_Nm          = {torque_Nm:.3f}")
        print(f"nut_factor         = {nut_factor:.3f}")
        print(f"F_per_bolt_N       = {F_per_bolt_N:.3f}")
        print(f"F_total_N          = {F_total_N:.3f}")
        print(f"A_pressure_mm2     = {A_pressure_mm2:.3f}")
        print(f"P_N_per_mm2        = {P_N_per_mm2:.6f}")
        print(f"Y_uohm             = {Y_uohm:.6f}")
        print(f"Ri_uohm            = {Ri_uohm:.6f}")
        print(f"Rs_ohm             = {Rs:.6e}")
        print(f"Ri_ohm             = {Ri:.6e}")
        print(f"R_total_ohm        = {(Rs + Ri):.6e}")

    return Rs + Ri