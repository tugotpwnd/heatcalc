from __future__ import annotations
import math
from dataclasses import dataclass

SIGMA = 5.670374419e-8


def _relative_emissivity(e1: float, e2: float) -> float:
    denom = (e1 + e2) - (e1 * e2)
    if denom <= 0.0:
        return 0.0
    return (e1 * e2) / denom

def _air_properties_film(Tf_K: float):
    """
    Approx air properties as function of film temperature.

    Tf_K : float
        Film temperature in Kelvin

    Returns
    -------
    k_air, nu_air, alpha_air, Pr, beta
    """

    Tf_K = max(250.0, min(Tf_K, 400.0))  # clamp to sane range

    # Thermal conductivity (W/m·K)
    k_air = 0.024 + 7.0e-5 * (Tf_K - 273.15)

    # Kinematic viscosity (m²/s)
    nu_air = 1.3e-5 * (Tf_K / 273.15) ** 1.75

    # Thermal diffusivity (m²/s)
    alpha_air = 1.9e-5 * (Tf_K / 273.15) ** 1.5

    Pr = nu_air / alpha_air

    # Thermal expansion coefficient (ideal gas)
    beta = 1.0 / Tf_K

    return k_air, nu_air, alpha_air, Pr, beta

def _vertical_cavity_h_barrett(
    T_hot_C: float,
    T_cold_C: float,
    height_m: float,
    gap_m: float,
    *,
    h_min: float = 0.5,
    h_max: float = 25.0,
):
    """
    Average convection coefficient for a vertical cavity / side-wall enclosure surface.

    Based on the Barrett thesis vertical cavity formulation:
        Ra = g * beta * dT * G^3 / (nu * alpha)
        Nu = 0.22 * ((Pr * Ra / (0.2 + Pr))^0.28) * (H / G)^(-1/4)

    Notes
    -----
    - Uses gap width G as the conduction/convection length scale in h = Nu*k/G,
      matching the accompanying Matlab implementation.
    - Intended for average enclosure side-wall absorption, not the local bus-to-wall
      hotspot slot term (which you already model separately).
    """
    g_grav = 9.81
    H = max(float(height_m), 1e-6)
    G = max(float(gap_m), 1e-6)

    Tf_K = ((float(T_hot_C) + 273.15) + (float(T_cold_C) + 273.15)) / 2.0
    deltaT = max(float(T_hot_C) - float(T_cold_C), 1e-9)

    k_air, nu_air, alpha_air, Pr, beta = _air_properties_film(Tf_K)

    Ra = g_grav * beta * deltaT * (G ** 3) / (nu_air * alpha_air)

    aspect = H / G
    Nu = 0.22 * (((Pr * Ra) / (0.2 + Pr)) ** 0.28) * (aspect ** (-0.25))

    Nu = max(1.0, min(Nu, 50.0))

    h = Nu * k_air / G
    h = max(h_min, min(h, h_max))

    return h, Ra, Nu

def _rect_rect_parallel_view_factor_numeric(
    width1_m: float,
    height1_m: float,
    width2_m: float,
    height2_m: float,
    gap_m: float,
    *,
    nx: int = 12,
    ny: int = 24,
) -> float:
    """
    Numerical view factor from rectangle 1 to a parallel facing rectangle 2.

    Geometry:
      - both rectangles are centred on the same axis
      - both lie in parallel planes
      - separation is gap_m
      - width = horizontal span
      - height = vertical span

    Returns
    -------
    F_12 : float
        View factor from surface 1 to surface 2
    """
    w1 = max(float(width1_m), 1e-9)
    h1 = max(float(height1_m), 1e-9)
    w2 = max(float(width2_m), 1e-9)
    h2 = max(float(height2_m), 1e-9)
    g = max(float(gap_m), 1e-9)

    nx = max(2, int(nx))
    ny = max(2, int(ny))

    dx1 = w1 / nx
    dy1 = h1 / ny
    dx2 = w2 / nx
    dy2 = h2 / ny

    dA1 = dx1 * dy1
    dA2 = dx2 * dy2
    A1 = w1 * h1

    x1_min = -0.5 * w1 + 0.5 * dx1
    y1_min = -0.5 * h1 + 0.5 * dy1
    x2_min = -0.5 * w2 + 0.5 * dx2
    y2_min = -0.5 * h2 + 0.5 * dy2

    total = 0.0

    for i in range(nx):
        x1 = x1_min + i * dx1
        for j in range(ny):
            y1 = y1_min + j * dy1

            for m in range(nx):
                x2 = x2_min + m * dx2
                dx = x2 - x1

                for n in range(ny):
                    y2 = y2_min + n * dy2
                    dy = y2 - y1

                    R2 = dx * dx + dy * dy + g * g
                    kernel = (g * g) / (math.pi * (R2 ** 2))

                    total += kernel * dA1 * dA2

    F12 = total / A1
    return max(0.0, min(1.0, F12))


def _bus_to_wall_patch_view_factor(
    *,
    bus_length_m: float,
    bus_face_width_m: float,
    patch_height_m: float,
    patch_width_m: float,
    gap_m: float,
    debug: bool = False,
) -> float:
    """
    View factor from a single rectangular bus face to a parallel wall patch.

    Bus face:
      width  = bus_face_width_m
      height = bus_length_m

    Wall patch:
      width  = patch_width_m
      height = patch_height_m
    """
    F = _rect_rect_parallel_view_factor_numeric(
        width1_m=bus_face_width_m,
        height1_m=bus_length_m,
        width2_m=patch_width_m,
        height2_m=patch_height_m,
        gap_m=gap_m,
        nx=12,
        ny=24,
    )

    if debug:
        print("\n[VIEW FACTOR]")
        print(f"bus_face_width     = {bus_face_width_m:.6f} m")
        print(f"bus_face_height    = {bus_length_m:.6f} m")
        print(f"patch_width        = {patch_width_m:.6f} m")
        print(f"patch_height       = {patch_height_m:.6f} m")
        print(f"gap                = {gap_m:.6f} m")
        print(f"F_bus_to_patch     = {F:.6f}")

    return F

@dataclass
class EnclosureSurfaceEstimate:
    # Bulk / area-averaged outer skin
    T_surface_top_side_C: float

    # Local hotspot on wall facing the busbar cluster
    T_inner_hotspot_C: float
    T_outer_hotspot_C: float

    # Diagnostics
    Q_rad_bus_to_wall_W: float
    Q_conv_air_to_patch_W: float
    patch_area_m2: float
    bus_face_area_m2: float
    view_factor: float
    converged: bool
    iterations: int


def estimate_enclosure_surface_temp_C(
    T_air_in_C: float,
    T_amb_C: float,
    *,
    h_in_W_m2K: float = 5.0,
    h_out_W_m2K: float = 8.0,
    wall_k_W_mK: float = 45.0,
    wall_t_m: float = 1.6e-3,
    eps_wall_outer: float = 0.90,
    max_iter: int = 50,
    tol_C: float = 1e-4,
    debug: bool = False,
) -> float:
    """
    Estimate area-averaged outer enclosure surface temperature.

    Physics included:
      1) Internal convection from enclosure air to inner wall
      2) Conduction through wall thickness
      3) External natural/forced convection to ambient air
      4) External long-wave radiation to surroundings

    Assumptions:
      - steady-state
      - 1D through-thickness transfer
      - outer radiative surroundings represented by a single effective temperature
      - convection and radiation on the outer surface act in parallel

    Returns
    -------
    T_surface_outer_C : float
        Area-averaged outer wall skin temperature in °C
    """
    sigma = SIGMA

    T_air_in_C = float(T_air_in_C)
    T_amb_C = float(T_amb_C)
    h_in = max(float(h_in_W_m2K), 1e-9)
    h_out = max(float(h_out_W_m2K), 1e-9)
    k_wall = max(float(wall_k_W_mK), 1e-9)
    t_wall = max(float(wall_t_m), 1e-9)
    eps_out = max(0.0, min(float(eps_wall_outer), 1.0))


    T_surround_K = float(T_amb_C) + 273.15

    Rpp_in = 1.0 / h_in
    Rpp_wall = t_wall / k_wall

    # Initial guess: old convection-only solution
    Rpp_out0 = 1.0 / h_out
    qpp = (T_air_in_C - T_amb_C) / (Rpp_in + Rpp_wall + Rpp_out0)
    Tso_C = T_amb_C + qpp * Rpp_out0

    converged = False

    for it in range(max_iter):
        Tso_K = Tso_C + 273.15

        # Linearised external radiation coefficient
        h_rad_out = eps_out * sigma * (
            (Tso_K + T_surround_K) * (Tso_K**2 + T_surround_K**2)
        )

        h_out_eff = h_out + h_rad_out
        Rpp_out_eff = 1.0 / max(h_out_eff, 1e-9)

        qpp_new = (T_air_in_C - T_amb_C) / (Rpp_in + Rpp_wall + Rpp_out_eff)
        Tso_new_C = T_amb_C + qpp_new * Rpp_out_eff

        if abs(Tso_new_C - Tso_C) < tol_C:
            Tso_C = Tso_new_C
            converged = True
            break

        Tso_C = Tso_new_C

    if debug:
        Tso_K = Tso_C + 273.15
        h_rad_out = eps_out * sigma * (
            (Tso_K + T_surround_K) * (Tso_K**2 + T_surround_K**2)
        )
        q_conv_out = h_out * (Tso_C - T_amb_C)
        q_rad_out = eps_out * sigma * (Tso_K**4 - T_surround_K**4)

        print("\n[BULK ENCLOSURE SURFACE]")
        print(f"T_air_in_C        = {T_air_in_C:.3f}")
        print(f"T_amb_C           = {T_amb_C:.3f}")
        print(f"T_surface_out_C   = {Tso_C:.3f}")
        print(f"h_in_W_m2K        = {h_in:.3f}")
        print(f"h_out_W_m2K       = {h_out:.3f}")
        print(f"h_rad_out_W_m2K   = {h_rad_out:.3f}")
        print(f"q_conv_out_W_m2   = {q_conv_out:.3f}")
        print(f"q_rad_out_W_m2    = {q_rad_out:.3f}")
        print(f"converged         = {converged}")
        print(f"iterations        = {it + 1}")

    return float(Tso_C)

def estimate_top_side_surface_temp_C(
    T_top_C: float,
    T_amb_C: float,
    *,
    internal_gap_m: float,
    face_height_m: float = 2.0,
    wall_k_W_mK: float = 45.0,
    wall_t_m: float = 2e-3,
    eps_wall_outer: float = 0.90,
    max_iter: int = 50,
    tol_C: float = 1e-4,
    debug: bool = False,
) -> float:
    """
    Estimate outer temperature of the upper SIDE WALL using T_top and a
    Barrett-style internal vertical cavity convection model.

    Parameters
    ----------
    T_top_C : float
        Internal top air temperature from the enclosure model / IEC 60890.
    T_amb_C : float
        Ambient room temperature.
    internal_gap_m : float
        Characteristic internal gap from the hot enclosure air core to the side wall.
        For a first pass, use ~half enclosure depth or another representative cavity width.
    face_height_m : float
        Vertical enclosure side height.
    """

    T_top_C = float(T_top_C)
    T_amb_C = float(T_amb_C)
    H = max(float(face_height_m), 1e-6)
    G = max(float(internal_gap_m), 1e-6)
    k_wall = max(float(wall_k_W_mK), 1e-9)
    t_wall = max(float(wall_t_m), 1e-9)
    eps_out = max(0.0, min(float(eps_wall_outer), 1.0))

    R_wall = t_wall / k_wall

    # Initial guess: old simple estimate
    h_out0, _, _ = _vertical_plate_h_out_natural(
        T_surface_C=T_top_C,
        T_amb_C=T_amb_C,
        face_height_m=H,
    )
    Tso_C = estimate_enclosure_surface_temp_C(
        T_air_in_C=T_top_C,
        T_amb_C=T_amb_C,
        h_in_W_m2K=5.0,
        h_out_W_m2K=h_out0,
        wall_k_W_mK=k_wall,
        wall_t_m=t_wall,
        eps_wall_outer=eps_out,
        debug=False,
    )

    converged = False

    for it in range(max_iter):
        # Inner surface guess from previous outer surface and wall conduction
        # We'll iterate the coupled inner and outer balances.
        Tsi_C = Tso_C

        for _ in range(20):
            # Internal vertical cavity convection: top air core -> inner side wall
            h_in, Ra_in, Nu_in = _vertical_cavity_h_barrett(
                T_hot_C=T_top_C,
                T_cold_C=Tsi_C,
                height_m=H,
                gap_m=G,
            )

            # External convection/radiation: outer side wall -> ambient
            h_out, Ra_out, Nu_out = _vertical_plate_h_out_natural(
                T_surface_C=Tso_C,
                T_amb_C=T_amb_C,
                face_height_m=H,
            )

            q_out, _, _ = _outer_surface_flux_W_m2(
                T_surface_C=Tso_C,
                T_amb_C=T_amb_C,
                h_out_W_m2K=h_out,
                eps_wall_outer=eps_out,
                T_surround_C=T_amb_C,
            )

            # Match conduction through wall to outer loss
            Tsi_new = Tso_C + q_out * R_wall

            # Match inner convection to conduction
            q_in = h_in * max(T_top_C - Tsi_new, 0.0)
            Tso_new = Tsi_new - q_in * R_wall

            if abs(Tso_new - Tso_C) < tol_C and abs(Tsi_new - Tsi_C) < tol_C:
                Tso_C = Tso_new
                Tsi_C = Tsi_new
                converged = True
                break

            Tso_C = Tso_new
            Tsi_C = Tsi_new

        if converged:
            break

    if debug:
        h_in, Ra_in, Nu_in = _vertical_cavity_h_barrett(
            T_hot_C=T_top_C,
            T_cold_C=Tsi_C,
            height_m=H,
            gap_m=G,
        )
        h_out, Ra_out, Nu_out = _vertical_plate_h_out_natural(
            T_surface_C=Tso_C,
            T_amb_C=T_amb_C,
            face_height_m=H,
        )
        q_out, q_conv_out, q_rad_out = _outer_surface_flux_W_m2(
            T_surface_C=Tso_C,
            T_amb_C=T_amb_C,
            h_out_W_m2K=h_out,
            eps_wall_outer=eps_out,
            T_surround_C=T_amb_C,
        )

        print("\n[TOP SIDE WALL SURFACE]")
        print(f"T_top              = {T_top_C:.2f} C")
        print(f"T_inner_side       = {Tsi_C:.2f} C")
        print(f"T_outer_side       = {Tso_C:.2f} C")
        print(f"internal_gap_m     = {G:.4f} m")
        print(f"h_in_cavity        = {h_in:.2f} W/m²K")
        print(f"Ra_in              = {Ra_in:.3e}")
        print(f"Nu_in              = {Nu_in:.3f}")
        print(f"h_out              = {h_out:.2f} W/m²K")
        print(f"q_out_total        = {q_out:.2f} W/m²")
        print(f"q_out_conv         = {q_conv_out:.2f} W/m²")
        print(f"q_out_rad          = {q_rad_out:.2f} W/m²")
        print(f"converged          = {converged} in {it + 1} iterations")

    return float(Tso_C)


def _gap_convection_h_enclosed_vertical(
    T_hot_C: float,
    T_cold_C: float,
    gap_m: float,
    height_m: float,
    gap_conv_factor: float = 1.0,
):
    """
    Natural convection coefficient for a vertical enclosed air layer (slot).

    Geometry:
      - two parallel vertical surfaces
      - one hot surface (bus face)
      - one cooler surface (wall patch)
      - gap_m is the clear air gap between them
      - height_m is the vertical run / characteristic slot height

    Returns
    -------
    h_gap_W_m2K, Ra_gap, Nu_gap

    Notes
    -----
    This is a compact engineering correlation for a buoyancy-driven enclosed gap.
    It improves on a free-plate heuristic by:
      - using the physical gap as the conduction / convection length scale
      - including slot aspect ratio H/g
      - preserving Nu >= 1 (pure conduction lower bound)

    The correlation is intentionally conservative and stable for switchboard-style
    vertical slots rather than being a CFD-grade enclosure flow model.
    """
    g_grav = 9.81
    gap = max(float(gap_m), 1e-6)
    H = max(float(height_m), gap)

    # film temperature
    Tf_K = ((float(T_hot_C) + 273.15) + (float(T_cold_C) + 273.15)) / 2.0
    deltaT = max(float(T_hot_C) - float(T_cold_C), 1e-9)

    # Approx air properties at warm film temperatures
    # Keep these simple for now; Step 5 will make them temperature-dependent.
    k_air, nu_air, alpha_air, Pr, beta = _air_properties_film(Tf_K)

    # Rayleigh number based on gap width
    Ra_gap = g_grav * beta * deltaT * (gap ** 3) / (nu_air * alpha_air)

    # Slot aspect ratio
    aspect = H / gap

    # Enclosed vertical layer correlation:
    # - Nu = 1 at very low Ra (pure conduction)
    # - buoyancy enhancement grows with Ra
    # - aspect-ratio suppression limits unrealistic over-prediction
    #
    # This form is deliberately stable and bounded for engineering use.
    buoyancy_term = 0.0605 * (Ra_gap ** (1.0 / 3.0))
    aspect_suppression = 1.0 / (1.0 + (50.0 / max(aspect, 1.0)) ** 1.2)

    Nu_gap = 1.0 + buoyancy_term * aspect_suppression

    # Keep within a sane engineering range
    Nu_gap = max(1.0, min(Nu_gap, 20.0))

    h_gap = gap_conv_factor * Nu_gap * k_air / gap
    h_gap = max(0.5, min(h_gap, 25.0))

    return h_gap, Ra_gap, Nu_gap


def _vertical_plate_h_out_natural(T_surface_C, T_amb_C, face_height_m):
    """
    External natural convection coefficient for a vertical plate in still indoor air.

    Uses a Churchill-Chu style correlation for natural convection from a vertical surface.

    Parameters
    ----------
    T_surface_C : float
        Outer surface temperature in °C
    T_amb_C : float
        Ambient air temperature in °C
    face_height_m : float
        Vertical height of the external face in m

    Returns
    -------
    h_out_W_m2K : float
        External convection coefficient in W/m²K
    Ra_L : float
        Rayleigh number based on face height
    Nu_L : float
        Nusselt number based on face height
    """
    L = max(float(face_height_m), 1e-6)

    # Use film temperature for air properties
    Tf_K = ((float(T_surface_C) + 273.15) + (float(T_amb_C) + 273.15)) / 2.0
    deltaT = max(abs(float(T_surface_C) - float(T_amb_C)), 1e-6)

    # Approx air properties near room-to-warm indoor conditions

    k_air, nu_air, alpha_air, Pr, beta = _air_properties_film(Tf_K)

    # Rayleigh number based on vertical height
    Ra_L = 9.81 * beta * deltaT * (L ** 3) / (nu_air * alpha_air)

    # Churchill-Chu correlation for vertical plate, broad validity range
    Nu_L = (
        0.68
        + (0.670 * (Ra_L ** 0.25))
        / ((1.0 + (0.492 / Pr) ** (9.0 / 16.0)) ** (4.0 / 9.0))
    )

    h_out = Nu_L * k_air / L

    # Clamp to sane still-air vertical-surface range
    h_out = max(2.0, min(10.0, h_out))

    return h_out, Ra_L, Nu_L

def _outer_surface_flux_W_m2(
    T_surface_C: float,
    T_amb_C: float,
    h_out_W_m2K: float,
    eps_wall_outer: float,
    T_surround_C: float | None = None,
):
    """
    Total outer-surface heat rejection flux (convection + radiation), W/m².
    Positive value means heat leaving the wall.
    """
    if T_surround_C is None:
        T_surround_C = T_amb_C

    Tsurf_K = float(T_surface_C) + 273.15
    Tamb_K = float(T_amb_C) + 273.15
    Tsurr_K = float(T_surround_C) + 273.15

    q_conv = float(h_out_W_m2K) * (float(T_surface_C) - float(T_amb_C))
    q_rad = float(eps_wall_outer) * SIGMA * (Tsurf_K**4 - Tsurr_K**4)

    return q_conv + q_rad, q_conv, q_rad



def estimate_enclosure_surface_temps_with_hotspot(
    *,
    T_bus_C: float,
    T_air_in_C: float,
    T_amb_C: float,
    eps_bus_to_wall: float,

    # Bus geometry
    bus_length_m: float,
    bar_width_m: float,
    bar_thickness_m: float,
    bars_per_phase: int = 1,
    gap_to_wall_mm: float = 50.0,
    orientation_to_wall: str = "width",

    # Cluster assumptions
    phase_pitch_m: float = 0.075,   # centre-to-centre spacing between phases
    patch_spread_factor: float = 1.0,

    # Surface properties
    eps_wall_inner: float = 0.85,
    eps_wall_outer: float = 0.90,

    # Wall / ambient transfer
    h_in_W_m2K: float = 5.0,        # bulk enclosure air to wall
    face_height_m: float = 2.0,     # external vertical face height for h_out calc
    wall_k_W_mK: float = 45.0,
    wall_t_m: float = 2e-3,
    enclosure_depth_m: float | None = None,  # enclosure-scale cavity depth for average side-wall convection

    # Local gap convection model
    gap_conv_factor: float = 1.0,

    # Numerics
    max_iter: int = 100,
    tol_C: float = 1e-4,

    debug: bool = False,
) -> EnclosureSurfaceEstimate:
    """
    Hybrid local hotspot model.

    Paths included:
      1. Radiation from 3 phase bars (and any parallel bars per phase) to wall
      2. Gap/plume convection from bus cluster to wall
      3. Bulk internal air convection to wall
      4. Wall conduction + outside convection to ambient

    Assumptions:
      - bars_per_phase means parallel bars in each phase
      - fixed 3-phase system
      - bus cluster runs vertically/horizontally along a wall over bus_length_m
      - this is still a compact engineering model, not CFD
    """
    if debug:
        print("\n[ENCLOSURE HOTSPOT INPUT]")
        print(f"T_bus={T_bus_C:.2f}C, T_air={T_air_in_C:.2f}C, T_amb={T_amb_C:.2f}C")
        print(f"L={bus_length_m:.3f} m, bar_w={bar_width_m:.4f} m, bar_t={bar_thickness_m:.4f} m")
        print(f"bars_per_phase={bars_per_phase}, gap={gap_to_wall_mm:.1f} mm, orient={orientation_to_wall}")

    # -------------------------------------------------
    # 1) Top-side wall surface (using T_top instead of mean air)
    # -------------------------------------------------
    # First-pass assumption:
    #   average hot upper air core is approximately mid-depth,
    #   so side-wall cavity width is ~ 1/3 enclosure internal depth.
    internal_gap_m = max(0.3 * float(enclosure_depth_m), 1e-6)

    T_surface_top_side_C = estimate_top_side_surface_temp_C(
        T_top_C=T_air_in_C,
        T_amb_C=T_amb_C,
        internal_gap_m=internal_gap_m,
        face_height_m=face_height_m,
        wall_k_W_mK=wall_k_W_mK,
        wall_t_m=wall_t_m,
        eps_wall_outer=eps_wall_outer,
        debug=False,
    )
    # -------------------------------------------------
    # 2) Geometry
    # -------------------------------------------------
    phase_count = 3
    bars_per_phase = max(1, int(bars_per_phase))
    total_bars = phase_count * bars_per_phase

    if orientation_to_wall == "width":
        projected_face_width_m = float(bar_width_m)
        bar_depth_to_wall_m = float(bar_thickness_m)
    else:
        projected_face_width_m = float(bar_thickness_m)
        bar_depth_to_wall_m = float(bar_width_m)

    # Total directly-radiating bar face area toward the wall
    bus_face_area_m2 = max(
        float(bus_length_m) * projected_face_width_m * total_bars,
        1e-9,
    )

    # Physical span of the 3-phase cluster across the wall
    # For now, parallel bars within a phase are assumed stacked behind / close packed
    cluster_width_m = max(
        (phase_count - 1) * float(phase_pitch_m) + projected_face_width_m,
        projected_face_width_m,
    )

    # Hotspot patch on wall: based on physical cluster span, not source area
    patch_width_m = max(cluster_width_m * float(patch_spread_factor), 1e-6)
    patch_area_m2 = max(float(bus_length_m) * patch_width_m, 1e-9)

    area_ratio = bus_face_area_m2 / patch_area_m2

    if debug:
        print("\n[GEOMETRY]")
        print(f"projected_face_width = {projected_face_width_m:.4f} m")
        print(f"bar_depth_to_wall    = {bar_depth_to_wall_m:.4f} m")
        print(f"total_bars           = {total_bars}")
        print(f"bus_face_area        = {bus_face_area_m2:.6f} m²")
        print(f"cluster_width        = {cluster_width_m:.6f} m")
        print(f"patch_width          = {patch_width_m:.6f} m")
        print(f"patch_area           = {patch_area_m2:.6f} m²")
        print(f"area_ratio           = {area_ratio:.4f}")


    # -------------------------------------------------
    # 3) Wall resistances
    # -------------------------------------------------
    R_wall = wall_t_m / max(wall_k_W_mK, 1e-12)

    # -------------------------------------------------
    # 4) Radiation model
    # -------------------------------------------------
    eps_rel = _relative_emissivity(eps_bus_to_wall, eps_wall_inner)
    gap_mm = float(gap_to_wall_mm)
    gap_m = max(gap_mm / 1000.0, 1e-6)

    # Geometry-based view factor:
    # one bus face -> wall patch
    F_single = _bus_to_wall_patch_view_factor(
        bus_length_m=float(bus_length_m),
        bus_face_width_m=float(projected_face_width_m),
        patch_height_m=float(bus_length_m),
        patch_width_m=float(patch_width_m),
        gap_m=gap_m,
        debug=False,
    )

    # For multiple bars facing the same patch, use the same single-face view factor.
    # The total radiative effect is still scaled by total radiating area through area_ratio.
    F = F_single
    # -------------------------------------------------
    # 5) Local enclosed-gap convection model
    # -------------------------------------------------
    gap_m = max(gap_mm / 1000.0, 1e-6)

    # For the enclosed vertical slot model, use the actual hot and cold bounding
    # surfaces rather than an assumed intermediate plume temperature.
    #
    # Hot side  = bus face
    # Cold side = inner wall patch
    #
    # This is more physically aligned with the slot/cavity correlation and is
    # more defensible for thesis purposes than using a heuristic plume fraction.
    #
    # Effective heat-transfer area for slot convection:
    # use cluster span × length, not full multiplied bar area
    A_gap_conv_m2 = max(float(bus_length_m) * cluster_width_m, 1e-9)

    # -------------------------------------------------
    # 6) Solve for inner wall hotspot temperature
    # -------------------------------------------------
    Tsi_C = max(T_air_in_C, T_surface_top_side_C)

    converged = False
    Q_rad_W = 0.0
    Q_conv_W = 0.0

    for it in range(1, max_iter + 1):
        Tsi_K = Tsi_C + 273.15
        Tbus_K = T_bus_C + 273.15

        # --- solve outer surface temperature implicitly ---

        # initial guess: assume Tso ~ Tsi
        Tso_C = Tsi_C

        for _ in range(10):  # small inner iteration is enough
            # external convection
            h_out_W_m2K, _, _ = _vertical_plate_h_out_natural(
                T_surface_C=Tso_C,
                T_amb_C=T_amb_C,
                face_height_m=face_height_m,
            )

            # outer heat flux (conv + rad)
            q_out_W_m2, q_conv_out_W_m2, q_rad_out_W_m2 = _outer_surface_flux_W_m2(
                T_surface_C=Tso_C,
                T_amb_C=T_amb_C,
                h_out_W_m2K=h_out_W_m2K,
                eps_wall_outer=eps_wall_outer,
                T_surround_C=T_amb_C,
            )

            # conduction through wall must match outer loss
            # q = (Tsi - Tso) / R_wall
            Tso_new = Tsi_C - q_out_W_m2 * R_wall

            if abs(Tso_new - Tso_C) < 1e-4:
                Tso_C = Tso_new
                break

            Tso_C = Tso_new

        # final consistent outer flux
        qpp_out = q_out_W_m2

        # Path 1: background enclosure-air to wall convection
        # Use enclosure-scale vertical cavity convection rather than a fixed h_in.
        h_bulk_cavity_W_m2K, Ra_bulk_cavity, Nu_bulk_cavity = _vertical_cavity_h_barrett(
            T_hot_C=T_air_in_C,
            T_cold_C=Tsi_C,
            height_m=float(face_height_m),
            gap_m=internal_gap_m,
        )
        qpp_conv_bulk = h_bulk_cavity_W_m2K * max(T_air_in_C - Tsi_C, 0.0)

        # --- Rayleigh-based gap convection ---
        h_gap_W_m2K, Ra_gap, Nu_gap = _gap_convection_h_enclosed_vertical(
            T_hot_C=T_bus_C,
            T_cold_C=Tsi_C,
            gap_m=gap_m,
            height_m=float(bus_length_m),
            gap_conv_factor=gap_conv_factor,
        )

        # Path 2: local plume/gap convection to wall
        # convert from total gap-convection power to wall-patch heat flux
        Q_gap_conv_W = h_gap_W_m2K * A_gap_conv_m2 * max(T_bus_C - Tsi_C, 0.0)
        qpp_conv_gap = Q_gap_conv_W / patch_area_m2

        # Path 3: radiation from all bus faces to wall patch
        qpp_rad_in = (
            F
            * eps_rel
            * SIGMA
            * max(Tbus_K**4 - Tsi_K**4, 0.0)
            * area_ratio
        )

        qpp_in = qpp_conv_bulk + qpp_conv_gap + qpp_rad_in
        resid = qpp_out - qpp_in

        # Numerical derivative
        dT = 0.05
        Tsi2_C = Tsi_C + dT
        Tsi2_K = Tsi2_C + 273.15

        Tso2_C = Tsi2_C

        for _ in range(10):
            h_out_2, _, _ = _vertical_plate_h_out_natural(
                T_surface_C=Tso2_C,
                T_amb_C=T_amb_C,
                face_height_m=face_height_m,
            )

            q_out2_W_m2, _, _ = _outer_surface_flux_W_m2(
                T_surface_C=Tso2_C,
                T_amb_C=T_amb_C,
                h_out_W_m2K=h_out_2,
                eps_wall_outer=eps_wall_outer,
                T_surround_C=T_amb_C,
            )

            Tso2_new = Tsi2_C - q_out2_W_m2 * R_wall

            if abs(Tso2_new - Tso2_C) < 1e-4:
                Tso2_C = Tso2_new
                break

            Tso2_C = Tso2_new

        qpp_out_2 = q_out2_W_m2

        h_bulk_cavity_2, _, _ = _vertical_cavity_h_barrett(
            T_hot_C=T_air_in_C,
            T_cold_C=Tsi2_C,
            height_m=float(face_height_m),
            gap_m=internal_gap_m,
        )
        qpp_conv_bulk_2 = h_bulk_cavity_2 * max(T_air_in_C - Tsi2_C, 0.0)

        h_gap_2, _, _ = _gap_convection_h_enclosed_vertical(
            T_hot_C=T_bus_C,
            T_cold_C=Tsi2_C,
            gap_m=gap_m,
            height_m=float(bus_length_m),
            gap_conv_factor=gap_conv_factor,
        )

        Q_gap_conv_W_2 = h_gap_2 * A_gap_conv_m2 * max(T_bus_C - Tsi2_C, 0.0)

        qpp_conv_gap_2 = Q_gap_conv_W_2 / patch_area_m2

        qpp_rad_in_2 = (
            F
            * eps_rel
            * SIGMA
            * max(Tbus_K**4 - Tsi2_K**4, 0.0)
            * area_ratio
        )

        resid2 = qpp_out_2 - (qpp_conv_bulk_2 + qpp_conv_gap_2 + qpp_rad_in_2)
        dresid_dT = (resid2 - resid) / dT

        if abs(dresid_dT) < 1e-9:
            break

        step = resid / dresid_dT
        Tsi_new_C = Tsi_C - step
        Tsi_new_C = max(T_amb_C, min(T_bus_C, Tsi_new_C))

        if abs(Tsi_new_C - Tsi_C) < tol_C:
            Tsi_C = Tsi_new_C
            converged = True

            # --- Rayleigh-based gap convection ---
            h_gap_W_m2K, Ra_gap, Nu_gap = _gap_convection_h_enclosed_vertical(
                T_hot_C=T_bus_C,
                T_cold_C=Tsi_C,
                gap_m=gap_m,
                height_m=float(bus_length_m),
                gap_conv_factor=gap_conv_factor,
            )

            h_bulk_cavity_W_m2K, Ra_bulk_cavity, Nu_bulk_cavity = _vertical_cavity_h_barrett(
                T_hot_C=T_air_in_C,
                T_cold_C=Tsi_C,
                height_m=float(face_height_m),
                gap_m=internal_gap_m,
            )
            qpp_conv_bulk = h_bulk_cavity_W_m2K * max(T_air_in_C - Tsi_C, 0.0)

            Q_gap_conv_W = h_gap_W_m2K * A_gap_conv_m2 * max(T_bus_C - Tsi_C, 0.0)
            qpp_conv_gap = Q_gap_conv_W / patch_area_m2
            qpp_rad_in = (
                F
                * eps_rel
                * SIGMA
                * max((T_bus_C + 273.15) ** 4 - (Tsi_C + 273.15) ** 4, 0.0)
                * area_ratio
            )

            Q_conv_W = (qpp_conv_bulk + qpp_conv_gap) * patch_area_m2
            Q_rad_W = qpp_rad_in * patch_area_m2
            break

        Tsi_C = Tsi_new_C

        if debug and (it == 1 or it % 10 == 0):
            print(
                f"[ITER {it}] "
                f"Tsi={Tsi_C:.2f}C "
                f"h_gap={h_gap_W_m2K:.2f} "
                f"Ra={Ra_gap:.2e} "
                f"q_out={qpp_out:.2f} "
                f"q_in={qpp_in:.2f} "
                f"q_gap={qpp_conv_gap:.2f} "
                f"q_rad_in={qpp_rad_in:.2f} "
                f"resid={resid:.4f}"
            )

    Tso_C = Tsi_C
    Ra_out = None
    Nu_out = None
    h_out_W_m2K = None

    for _ in range(10):
        h_out_W_m2K, Ra_out, Nu_out = _vertical_plate_h_out_natural(
            T_surface_C=Tso_C,
            T_amb_C=T_amb_C,
            face_height_m=face_height_m,
        )

        q_out_W_m2, _, _ = _outer_surface_flux_W_m2(
            T_surface_C=Tso_C,
            T_amb_C=T_amb_C,
            h_out_W_m2K=h_out_W_m2K,
            eps_wall_outer=eps_wall_outer,
            T_surround_C=T_amb_C,
        )

        Tso_new = Tsi_C - q_out_W_m2 * R_wall

        if abs(Tso_new - Tso_C) < 1e-4:
            Tso_C = Tso_new
            break

        Tso_C = Tso_new

    if not converged:
        h_bulk_cavity_W_m2K, Ra_bulk_cavity, Nu_bulk_cavity = _vertical_cavity_h_barrett(
            T_hot_C=T_air_in_C,
            T_cold_C=Tsi_C,
            height_m=float(face_height_m),
            gap_m=internal_gap_m,
        )
        qpp_conv_bulk = h_bulk_cavity_W_m2K * max(T_air_in_C - Tsi_C, 0.0)

        Q_gap_conv_W = h_gap_W_m2K * A_gap_conv_m2 * max(T_bus_C - Tsi_C, 0.0)
        qpp_conv_gap = Q_gap_conv_W / patch_area_m2
        qpp_rad_in = (
            F
            * eps_rel
            * SIGMA
            * max((T_bus_C + 273.15) ** 4 - (Tsi_C + 273.15) ** 4, 0.0)
            * area_ratio
        )
        Q_conv_W = (qpp_conv_bulk + qpp_conv_gap) * patch_area_m2
        Q_rad_W = qpp_rad_in * patch_area_m2

    # Outer flux breakdown (final state)
    q_out_W_m2, q_conv_out_W_m2, q_rad_out_W_m2 = _outer_surface_flux_W_m2(
        T_surface_C=Tso_C,
        T_amb_C=T_amb_C,
        h_out_W_m2K=h_out_W_m2K,
        eps_wall_outer=eps_wall_outer,
        T_surround_C=T_amb_C,
    )

    if debug:
        # --- Energy balance check ---
        h_bulk_cavity_final, Ra_bulk_cavity, Nu_bulk_cavity = _vertical_cavity_h_barrett(
            T_hot_C=T_air_in_C,
            T_cold_C=Tsi_C,
            height_m=float(face_height_m),
            gap_m=internal_gap_m,
        )
        qpp_conv_bulk_final = h_bulk_cavity_final * max(T_air_in_C - Tsi_C, 0.0)
        Q_gap_conv_W_final = h_gap_W_m2K * A_gap_conv_m2 * max(T_bus_C - Tsi_C, 0.0)
        qpp_conv_gap_final = Q_gap_conv_W_final / patch_area_m2
        qpp_rad_in_final = (
            F
            * eps_rel
            * SIGMA
            * max((T_bus_C + 273.15) ** 4 - (Tsi_C + 273.15) ** 4, 0.0)
            * area_ratio
        )
        qpp_in_total = qpp_conv_bulk_final + qpp_conv_gap_final + qpp_rad_in_final

        print("\n[TRANSFER COEFFICIENTS]")
        print(f"eps_rel            = {eps_rel:.4f}")
        print(f"view_factor_geom   = {F:.3f}")
        print(f"T_hot_gap          = {T_bus_C:.2f} C")
        print(f"T_cold_gap         = {Tsi_C:.2f} C")
        print(f"A_gap_conv         = {A_gap_conv_m2:.6f} m²")
        print(f"h_gap_slot         = {h_gap_W_m2K:.2f} W/m²K")
        print(f"Ra_gap             = {Ra_gap:.3e}")
        print(f"Nu_gap_slot        = {Nu_gap:.3f}")
        print(f"H_over_g           = {bus_length_m / max(gap_m, 1e-9):.1f}")
        print(f"internal_gap_m     = {internal_gap_m:.4f} m")
        print(f"h_bulk_cavity      = {h_bulk_cavity_final:.2f} W/m²K")
        print(f"Ra_bulk_cavity     = {Ra_bulk_cavity:.3e}")
        print(f"Nu_bulk_cavity     = {Nu_bulk_cavity:.3f}")

        print("\n[OUTER BOUNDARY]")
        print(f"h_out_conv         = {h_out_W_m2K:.2f} W/m²K")
        print(f"q_out_total        = {q_out_W_m2:.2f} W/m²")
        print(f"q_out_conv         = {q_conv_out_W_m2:.2f} W/m²")
        print(f"q_out_rad          = {q_rad_out_W_m2:.2f} W/m²")
        print(f"rad_fraction       = {q_rad_out_W_m2 / max(q_out_W_m2, 1e-9):.3f}")
        print(f"Ra_out             = {Ra_out:.3e}")
        print(f"Nu_out             = {Nu_out:.3f}")

        print("\n[ENERGY BALANCE]")
        print(f"q_in_total         = {qpp_in_total:.2f} W/m²")
        print(f"q_out_total        = {q_out_W_m2:.2f} W/m²")
        print(f"energy_error       = {q_out_W_m2 - qpp_in_total:.6f} W/m²")

        print("\n[RESULT]")
        print(f"T_inner            = {Tsi_C:.2f} C")
        print(f"T_outer            = {Tso_C:.2f} C")
        print(f"Q_rad_bus_to_wall  = {Q_rad_W:.2f} W")
        print(f"Q_conv_total       = {Q_conv_W:.2f} W")
        print(f"Converged          = {converged} in {it} iterations")


    return EnclosureSurfaceEstimate(
        T_surface_top_side_C=float(T_surface_top_side_C),
        T_inner_hotspot_C=float(Tsi_C),
        T_outer_hotspot_C=float(Tso_C),
        Q_rad_bus_to_wall_W=float(Q_rad_W),
        Q_conv_air_to_patch_W=float(Q_conv_W),
        patch_area_m2=float(patch_area_m2),
        bus_face_area_m2=float(bus_face_area_m2),
        view_factor=float(F),
        converged=bool(converged),
        iterations=it if "it" in locals() else 0,
    )