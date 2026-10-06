# heatcalc/core/busbar_physics.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np

from heatcalc.core.busbar_geometry import (
    BusbarGeometry,
    effective_radiating_area_per_m,
    surface_area_per_m,
)

SIGMA = 5.670e-8
ALPHA_CU = 0.00393
RHO_20 = 1.724e-8

FORCED_CONV_COEFF = 120.0
NATURAL_CONV_VERTICAL = 7.66
NATURAL_CONV_HORIZONTAL = 5.92


def relative_emissivity(e1: float, e2: float) -> float:
    denom = (e1 + e2) - (e1 * e2)
    if denom <= 0.0:
        return 0.0
    return (e1 * e2) / denom


def resistance_20C_per_m(width_m: float, thickness_m: float) -> float:
    A = width_m * thickness_m
    if A <= 0.0:
        raise ValueError("Busbar cross-sectional area must be > 0")
    return RHO_20 / A


def resistance_T_per_m(R20: float, T_bus_C: float) -> float:
    return R20 * (1.0 + ALPHA_CU * (T_bus_C - 20.0))


@dataclass(frozen=True)
class BusbarThermalInputs:
    I_total_A: float
    eps_bus: float = 0.4 # Varies depending on cooling / heating
    eps_env: float = 0.90
    v_mps: float = 0.0
    S_ac: float = 1.1


@dataclass(frozen=True)
class BusbarPhysicsState:
    T_bus_C: float
    T_air_C: float

    I_bar_A: float
    R20_ohm_per_m: float
    R_T_ohm_per_m: float
    P_gen_W_per_m: float

    As_conv_m2_per_m: float
    As_rad_raw_m2_per_m: float
    As_rad_eff_m2_per_m: float
    rad_blockage_frac: float

    theta_K: float
    W_conv_major_W_m2: float
    W_conv_minor_W_m2: float
    P_conv_W_per_m: float

    eps_rel: float
    W_rad_W_m2: float
    P_rad_W_per_m: float

    f_W_per_m: float

@dataclass(frozen=True)
class JointSelfCoolingState:
    name: str
    joint_type: str

    # Dimensions for side 1
    width1_m: float
    thickness1_m: float
    count1: int
    convection_mode1: str
    L_char1_m: float

    # Dimensions for side 2
    width2_m: float
    thickness2_m: float
    count2: int
    convection_mode2: str
    L_char2_m: float

    A_raw_m2: float
    A_buried_single_side_m2: float
    A_exposed_m2: float

    W_conv_eff_W_m2: float
    P_conv_W: float

    eps_rel: float
    W_rad_W_m2: float
    P_rad_W: float

def _face_fluxes_natural_convection(
    *,
    width_m: float,
    thickness_m: float,
    L_char_m: float,
    convection_mode: str,
    theta_K: float,
) -> tuple[float, float]:
    """
    Return face heat fluxes (W/m²) for:
        - major faces
        - minor faces

    This mirrors the same convection logic used in compute_busbar_physics(),
    but without any bar-count or per-metre assumptions.
    """
    w = max(float(width_m), 1e-12)
    t = max(float(thickness_m), 1e-12)
    Lc = max(float(L_char_m), 1e-12)

    if convection_mode == "vertical":
        L_major_mm = Lc * 1000.0
        L_minor_mm = Lc * 1000.0

        W_major = NATURAL_CONV_VERTICAL * (theta_K ** 1.25) / (L_major_mm ** 0.25)
        W_minor = NATURAL_CONV_VERTICAL * (theta_K ** 1.25) / (L_minor_mm ** 0.25)

    else:
        # Horizontal busbar:
        # - major faces treated as vertical side faces
        # - minor faces treated as horizontal top/bottom faces
        L_major_mm = w * 1000.0
        L_minor_mm = t * 1000.0

        W_major = NATURAL_CONV_VERTICAL * (theta_K ** 1.25) / (L_major_mm ** 0.25)
        W_minor = NATURAL_CONV_HORIZONTAL * (theta_K ** 1.25) / (L_minor_mm ** 0.25)

    return float(W_major), float(W_minor)


def _surface_area_split_for_length(
    *,
    width_m: float,
    thickness_m: float,
    length_m: float,
    count: int = 1,
) -> tuple[float, float, float]:
    """
    External surface areas over a finite local length:
        A_major = two wide faces
        A_minor = two thin faces
    """
    n = max(1, int(count))
    L = max(float(length_m), 0.0)
    w = max(float(width_m), 0.0)
    t = max(float(thickness_m), 0.0)

    A_major = 2.0 * w * L * n
    A_minor = 2.0 * t * L * n
    return float(A_major), float(A_minor), float(A_major + A_minor)


def _joint_buried_contact_area_single_side(
    *,
    joint_type: str,
    width1_m: float,
    thickness1_m: float,
    width2_m: float,
    thickness2_m: float,
    length_m: float,
    n_interfaces: int,
) -> float:
    """
    Physical buried contact area on ONE side of the joint across all interfaces.

    This is the area to subtract once from side A and once from side B, i.e.
    total exposed-area reduction = 2 * A_buried_single_side.
    """
    jt = (joint_type or "bolted_overlap").strip().lower()
    n_int = max(1, int(n_interfaces))

    w1 = max(float(width1_m), 0.0)
    t1 = max(float(thickness1_m), 0.0)
    w2 = max(float(width2_m), 0.0)
    t2 = max(float(thickness2_m), 0.0)
    L = max(float(length_m), 0.0)

    if jt == "bolted_overlap":
        # Face-to-face overlap over the local joint length
        A_single = min(w1, w2) * L

    elif jt == "sandwich_joint":
        # Treat sandwich as face-to-face buried area over the local joint length
        a = min(w1, w2)
        l = min(t1, t2)
        A_single = a * l

    elif jt == "clamped_edge":
        # Local edge/face patch using the same characteristic contact form
        # currently used for the clamped thermal contact model
        a = min(t1, t2)
        l = max(t1, t2)
        A_single = a * l

    else:
        raise ValueError(f"Unsupported joint_type: {joint_type}")

    return float(A_single * n_int)

# NOT USED AS IT OVER-REPRESENTS JOINT COOLING
# SHAME.
def compute_joint_self_cooling(
    *,
    joint_type: str,
    width1_m: float,
    thickness1_m: float,
    count1: int,
    width2_m: float,
    thickness2_m: float,
    count2: int,
    length_m: float,
    T_bus_C: float,
    T_air_C: float,
    eps_bus: float,
    eps_env: float,
    convection_mode1: str,
    convection_mode2: str,
    L_char1_m: float,
    L_char2_m: float,
    name: str = "joint",
    debug: bool = False,
) -> JointSelfCoolingState:
    """
    Joint self-cooling model with SIDE-SPECIFIC convection behaviour.

    Interpretation
    --------------
    The joint consists of two participating bar sets:
        side 1 = host-side bar set
        side 2 = other-side bar set

    Each side may have its own:
        - convection orientation (vertical / horizontal)
        - convection characteristic length

    Method
    ------
    1) Compute the normal external surface area of each participating bar set
       over the local joint length.
    2) Subtract the buried contact area from both mating sides:
           A_exposed = A_raw_total - 2 * A_contact_single_side
    3) Compute raw convection on each side using that side's own convection mode
       and characteristic length.
    4) Collapse to an effective convection flux over the raw total area.
    5) Apply that effective flux to the reduced exposed area.
    6) Apply radiation using the same exposed area.

    Notes
    -----
    - length_m is the LOCAL JOINT LENGTH used for exposed-area evaluation.
    - L_char1_m / L_char2_m are the SIDE-SPECIFIC convection characteristic
      lengths used in the natural convection correlations.
    """

    n1 = max(1, int(count1))
    n2 = max(1, int(count2))
    L = max(float(length_m), 1e-12)

    theta = max(float(T_bus_C) - float(T_air_C), 0.0)

    # ------------------------------------------------------------------
    # Raw external surface areas over the local joint region
    # ------------------------------------------------------------------
    A1_major, A1_minor, A1_total = _surface_area_split_for_length(
        width_m=width1_m,
        thickness_m=thickness1_m,
        length_m=L,
        count=n1,
    )

    A2_major, A2_minor, A2_total = _surface_area_split_for_length(
        width_m=width2_m,
        thickness_m=thickness2_m,
        length_m=L,
        count=n2,
    )

    A_raw_total = A1_total + A2_total

    # ------------------------------------------------------------------
    # Buried contact area
    # ------------------------------------------------------------------
    jt = (joint_type or "bolted_overlap").strip().lower()

    if jt == "bolted_overlap":
        n_interfaces = min(n1, n2)
    elif jt in ("clamped_edge", "sandwich_joint"):
        n_interfaces = n1 * n2
    else:
        raise ValueError(f"Unsupported joint_type: {joint_type}")

    A_buried_single_side = _joint_buried_contact_area_single_side(
        joint_type=jt,
        width1_m=width1_m,
        thickness1_m=thickness1_m,
        width2_m=width2_m,
        thickness2_m=thickness2_m,
        length_m=L,
        n_interfaces=n_interfaces,
    )

    # remove buried area from BOTH mating sides
    A_exposed_total = max(A_raw_total - 2.0 * A_buried_single_side, 1e-12)

    # ------------------------------------------------------------------
    # Raw convection behaviour under "normal" separate-bar conditions
    # ------------------------------------------------------------------
    W1_major, W1_minor = _face_fluxes_natural_convection(
        width_m=width1_m,
        thickness_m=thickness1_m,
        L_char_m=L_char1_m,
        convection_mode=convection_mode1,
        theta_K=theta,
    )

    W2_major, W2_minor = _face_fluxes_natural_convection(
        width_m=width2_m,
        thickness_m=thickness2_m,
        L_char_m=L_char2_m,
        convection_mode=convection_mode2,
        theta_K=theta,
    )

    P1_conv_raw = W1_major * A1_major + W1_minor * A1_minor
    P2_conv_raw = W2_major * A2_major + W2_minor * A2_minor
    P_conv_raw_total = P1_conv_raw + P2_conv_raw

    # Effective raw convection flux over the total raw area
    W_conv_eff = P_conv_raw_total / max(A_raw_total, 1e-12)

    # Apply the same mean flux to the reduced exposed area
    P_conv = W_conv_eff * A_exposed_total

    # ------------------------------------------------------------------
    # Radiation using the same exposed area
    # ------------------------------------------------------------------
    eps_rel = relative_emissivity(eps_bus, eps_env)
    T_K = float(T_bus_C) + 273.15
    Ta_K = float(T_air_C) + 273.15

    W_rad = SIGMA * eps_rel * max(T_K**4 - Ta_K**4, 0.0)
    P_rad = W_rad * A_exposed_total

    state = JointSelfCoolingState(
        name=str(name),
        joint_type=jt,
        width1_m=float(width1_m),
        thickness1_m=float(thickness1_m),
        count1=int(n1),
        convection_mode1=str(convection_mode1),
        L_char1_m=float(L_char1_m),
        width2_m=float(width2_m),
        thickness2_m=float(thickness2_m),
        count2=int(n2),
        convection_mode2=str(convection_mode2),
        L_char2_m=float(L_char2_m),
        A_raw_m2=float(A_raw_total),
        A_buried_single_side_m2=float(A_buried_single_side),
        A_exposed_m2=float(A_exposed_total),
        W_conv_eff_W_m2=float(W_conv_eff),
        P_conv_W=float(P_conv),
        eps_rel=float(eps_rel),
        W_rad_W_m2=float(W_rad),
        P_rad_W=float(P_rad),
    )

    if debug:
        print(
            f"[joint_self_cooling] {state.name} | type={state.joint_type} | dT={theta:.2f}K\n"
            f"  Side1: {state.count1}x({state.width1_m*1000:.1f}x{state.thickness1_m*1000:.1f}mm) | "
            f"mode={state.convection_mode1} | Lchar={state.L_char1_m:.3f}m\n"
            f"  Side2: {state.count2}x({state.width2_m*1000:.1f}x{state.thickness2_m*1000:.1f}mm) | "
            f"mode={state.convection_mode2} | Lchar={state.L_char2_m:.3f}m\n"
            f"  Areas: Araw={state.A_raw_m2:.6f} m² | Aburied(1side)={state.A_buried_single_side_m2:.6f} m² | Aexp={state.A_exposed_m2:.6f} m²\n"
            f"  Heat: Wconv_eff={state.W_conv_eff_W_m2:.3f} W/m² | Pconv={state.P_conv_W:.4f} W | Prad={state.P_rad_W:.4f} W"
        )

    return state

def compute_busbar_physics(
    *,
    geom: BusbarGeometry,
    therm: BusbarThermalInputs,
    T_bus_C: float,
    T_air_C: float,
    I_override_A: Optional[float] = None,
    debug: bool = True,
) -> BusbarPhysicsState:
    """
    Canonical busbar heat-balance physics.

    Important convention:
    This routine is intended for passive heated-conductor solving,
    so convection/radiation are treated as LOSS terms only.
    That means theta = max(T_bus - T_air, 0).
    Solver should enforce T_bus >= T_air.
    """

    N = max(1, int(geom.bars_in_parallel))

    if I_override_A is None:
        I_total = float(therm.I_total_A)
    else:
        I_total = float(I_override_A)

    I_bar = I_total / float(N)

    R20 = resistance_20C_per_m(geom.width_m, geom.thickness_m)
    R_T = resistance_T_per_m(R20, T_bus_C)

    P_gen_per_bar = (I_bar ** 2) * R_T * therm.S_ac
    P_gen_total = float(N) * P_gen_per_bar

    As_conv = surface_area_per_m(geom.width_m, geom.thickness_m)

    As_rad_raw, As_rad_eff, blockage = effective_radiating_area_per_m(
        geom.width_m,
        geom.thickness_m,
        bars_in_parallel=N,
        face_to_face_dim=geom.face_to_face_dim,
    )

    # -------------------------------------------------------------------------
    # FACE-BASED NATURAL CONVECTION (RECTANGULAR BUSBAR)
    #
    # The busbar is modelled as four distinct convecting faces:
    #   - 2 major faces: width × length
    #   - 2 minor faces: thickness × length
    #
    # Rather than applying a single convection coefficient to the full perimeter,
    # each face is treated independently with its own:
    #   - characteristic length (L)
    #   - convection correlation (vertical or horizontal)
    #
    # This is important because natural convection is governed by boundary layer
    # development, which depends on BOTH:
    #   - the orientation of the surface (vertical vs horizontal)
    #   - the characteristic length in the direction of buoyancy-driven flow
    #
    # The empirical correlation used:
    #
    #   W = C * (ΔT^1.25) / (L^0.25)
    #
    # represents heat flux (W/m²), where:
    #   - ΔT drives buoyancy
    #   - L controls boundary layer growth
    #
    # Key physical interpretation:
    #   - Larger L → thicker boundary layer → LOWER heat transfer per m²
    #   - Smaller L → thinner boundary layer → HIGHER heat transfer per m²
    #
    # Importantly:
    #   - L does NOT represent surface size (area handles that)
    #   - L represents the distance over which the thermal boundary layer develops
    #
    # Therefore:
    #   - Vertical surfaces → L = vertical height of the surface
    #   - Horizontal surfaces → L = characteristic horizontal dimension
    #
    # This distinction is critical to avoid artificially increasing convection
    # when segmenting busbars or using incorrect geometric dimensions.
    # -------------------------------------------------------------------------

    theta = max(T_bus_C - T_air_C, 0.0)

    # Dimensions
    w = geom.width_m
    t = geom.thickness_m
    L_char = getattr(geom, "L_char_m", None) or geom.length_m

    # Areas per metre length
    # (Area determines total heat transfer once W is known)
    A_major = 2.0 * w  # two wide faces
    A_minor = 2.0 * t  # two thin faces

    if geom.convection_mode == "vertical":
        # ---------------------------------------------------------------------
        # VERTICAL BUSBAR
        #
        # All faces extend along the vertical axis, meaning buoyant airflow
        # rises along the full height of each face.
        #
        # Therefore:
        #   - Boundary layer develops over full busbar height
        #   - Characteristic length MUST be the full bar height
        #
        # This is critical when segmentation is used, as using segment length
        # would artificially increase convection (shorter L → higher W).
        # ---------------------------------------------------------------------

        L_major_mm = L_char * 1000.0
        L_minor_mm = L_char * 1000.0

        W_major = NATURAL_CONV_VERTICAL * (theta ** 1.25) / (L_major_mm ** 0.25)
        W_minor = NATURAL_CONV_VERTICAL * (theta ** 1.25) / (L_minor_mm ** 0.25)

    else:
        # ---------------------------------------------------------------------
        # HORIZONTAL BUSBAR (TALL SIDE UP ASSUMPTION)
        #
        # Orientation:
        #   - Wide faces are vertical surfaces
        #   - Thin faces are horizontal surfaces (top and bottom)
        #
        # Major faces (vertical):
        #   - Air rises along face height
        #   - Characteristic length = vertical face dimension (w)
        #
        # Minor faces (horizontal):
        #   - Heat transfer occurs via upward buoyant plume
        #   - Characteristic length = horizontal face width (t)
        #
        # This separation captures the reduced effectiveness of horizontal
        # surfaces compared to vertical ones.
        # ---------------------------------------------------------------------

        L_major_mm = w * 1000.0
        L_minor_mm = t * 1000.0

        W_major = NATURAL_CONV_VERTICAL * (theta ** 1.25) / (L_major_mm ** 0.25)
        W_minor = NATURAL_CONV_HORIZONTAL * (theta ** 1.25) / (L_minor_mm ** 0.25)

    # -------------------------------------------------------------------------
    # Total convection
    #
    # Heat transfer from each face is:
    #   P = W * Area
    #
    # Total convection is the sum of contributions from all faces and all
    # parallel bars.
    # -------------------------------------------------------------------------

    P_major = N * W_major * A_major
    P_minor = N * W_minor * A_minor

    P_conv_total = P_major + P_minor
    A_conv_total = N * (A_major + A_minor)

    eps_rel = relative_emissivity(therm.eps_bus, therm.eps_env)
    T_K = T_bus_C + 273.15
    Ta_K = T_air_C + 273.15

    # Radiation is also treated as a loss term in this passive solve.
    # Once solver clamps T >= Ta, this stays non-negative.
    W_rad = SIGMA * eps_rel * max(T_K**4 - Ta_K**4, 0.0)
    P_rad_total = float(N) * W_rad * As_rad_eff

    f = P_gen_total - (P_conv_total + P_rad_total)

    state = BusbarPhysicsState(
        T_bus_C=float(T_bus_C),
        T_air_C=float(T_air_C),

        I_bar_A=float(I_bar),
        R20_ohm_per_m=float(R20),
        R_T_ohm_per_m=float(R_T),
        P_gen_W_per_m=float(P_gen_total),

        As_conv_m2_per_m=float(As_conv),
        As_rad_raw_m2_per_m=float(As_rad_raw),
        As_rad_eff_m2_per_m=float(As_rad_eff),
        rad_blockage_frac=float(blockage),

        theta_K=float(theta),
        W_conv_major_W_m2=float(W_major),
        W_conv_minor_W_m2=float(W_minor),
        P_conv_W_per_m=float(P_conv_total),

        eps_rel=float(eps_rel),
        W_rad_W_m2=float(W_rad),
        P_rad_W_per_m=float(P_rad_total),

        f_W_per_m=float(f),
    )
    if debug:
        print(
            f"[busbar_physics] {geom.name} | "
            f"Tbus={state.T_bus_C:.3f} C  Tair={state.T_air_C:.3f} C  "
            f"Ibar={state.I_bar_A:.3f} A  "
            f"Pgen={state.P_gen_W_per_m:.6f} W/m  "
            f"Pconv={state.P_conv_W_per_m:.6f} W/m  "
            f"Prad={state.P_rad_W_per_m:.6f} W/m  "
            f"f={state.f_W_per_m:.6f} W/m"
        )

        if debug:
            print("\n" + "=" * 60)
            print("[BUSBAR CONVECTION DEBUG]")
            print(f"Geom: {geom.name}")
            print(f"Mode: {geom.convection_mode}")
            print(f"Bars in parallel: {N}")
            print("-" * 60)

            print(f"Dimensions:")
            print(f"  width (w):        {w * 1000:.1f} mm")
            print(f"  thickness (t):    {t * 1000:.1f} mm")
            print(f"  L_char:           {L_char:.3f} m")

            print("-" * 60)

            print(f"Temperatures:")
            print(f"  T_bus:            {T_bus_C:.2f} °C")
            print(f"  T_air:            {T_air_C:.2f} °C")
            print(f"  theta:            {theta:.2f} K")

            print("-" * 60)

            print(f"Characteristic Lengths:")
            print(f"  L_major:          {L_major_mm:.1f} mm")
            print(f"  L_minor:          {L_minor_mm:.1f} mm")

            print("-" * 60)

            print(f"Areas (per metre):")
            print(f"  A_major:          {A_major:.4f} m²/m")
            print(f"  A_minor:          {A_minor:.4f} m²/m")
            print(f"  A_total:          {(A_major + A_minor):.4f} m²/m")

            print("-" * 60)

            print(f"Convection Coefficients:")
            print(f"  W_major:          {W_major:.2f} W/m²")
            print(f"  W_minor:          {W_minor:.2f} W/m²")

            print("-" * 60)

            print(f"Heat Flow (per metre):")
            print(f"  P_major:          {P_major:.2f} W/m")
            print(f"  P_minor:          {P_minor:.2f} W/m")
            print(f"  P_conv_total:     {P_conv_total:.2f} W/m")

            print("=" * 60 + "\n")

    return state