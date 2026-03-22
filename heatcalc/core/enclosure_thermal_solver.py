from __future__ import annotations
import math
from dataclasses import dataclass

SIGMA = 5.670374419e-8


def _relative_emissivity(e1: float, e2: float) -> float:
    denom = (e1 + e2) - (e1 * e2)
    if denom <= 0.0:
        return 0.0
    return (e1 * e2) / denom


@dataclass
class EnclosureSurfaceEstimate:
    # Bulk / area-averaged outer skin, same spirit as your current model
    T_surface_bulk_C: float

    # Local hotspot on wall facing the busbar
    T_inner_hotspot_C: float
    T_outer_hotspot_C: float

    # Helpful diagnostics
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
) -> float:
    """
    Bulk / area-averaged outer enclosure skin temperature.
    Keep this exactly as the 'smeared wall' estimate.
    """
    Rpp_in = 1.0 / max(h_in_W_m2K, 1e-9)
    Rpp_wall = wall_t_m / max(wall_k_W_mK, 1e-9)
    Rpp_out = 1.0 / max(h_out_W_m2K, 1e-9)

    qpp = (T_air_in_C - T_amb_C) / (Rpp_in + Rpp_wall + Rpp_out)
    T_surface_C = T_amb_C + qpp * Rpp_out
    return float(T_surface_C)


def estimate_enclosure_surface_temps_with_hotspot(
    *,
    T_bus_C: float,
    T_air_in_C: float,
    T_amb_C: float,
    eps_bus_to_wall: float,

    # Geometry of the hot bus facing the wall
    bus_length_m: float,
    bus_face_width_m: float,
    bars_facing_wall: int = 1,

    # How much wall area the hotspot spreads across.
    # 1.0 = only projected bus face area, conservative.
    # 2.0 to 4.0 = more realistic spreading patch
    patch_spread_factor: float = 1,

    # View factor from bus face to nearby wall patch.
    # For a nearby large wall, 0.7–1.0 is a reasonable engineering band.
    view_factor_bus_to_wall: float = 0.85,

    # Surface properties
    eps_wall_inner: float = 0.85,

    # Wall / ambient transfer
    h_in_W_m2K: float = 5.0,
    h_out_W_m2K: float = 5.0,
    wall_k_W_mK: float = 45.0,
    wall_t_m: float = 2e-3,

    # Numerics
    max_iter: int = 100,
    tol_C: float = 1e-4,

    debug: bool = False,

) -> EnclosureSurfaceEstimate:
    """
    Returns:
      - bulk average outer skin temperature
      - local hotspot inner/outer wall temperatures on the wall facing the bus

    Model:
      Busbar radiates directly to a wall patch.
      That same wall patch also gets convection from the internal hot air.
      Heat then passes through the thin steel wall to outside ambient.

    This is intentionally a compact engineering model, not CFD.
    """
    if debug:
        print("\n[ENCLOSURE HOTSPOT INPUT]")
        print(f"T_bus={T_bus_C:.2f}C, T_air={T_air_in_C:.2f}C, T_amb={T_amb_C:.2f}C")
        print(f"L={bus_length_m:.3f} m, W={bus_face_width_m:.4f} m, bars={bars_facing_wall}")

    # 1) Bulk wall estimate (your original model)
    T_surface_bulk_C = estimate_enclosure_surface_temp_C(
        T_air_in_C=T_air_in_C,
        T_amb_C=T_amb_C,
        h_in_W_m2K=h_in_W_m2K,
        h_out_W_m2K=h_out_W_m2K,
        wall_k_W_mK=wall_k_W_mK,
        wall_t_m=wall_t_m,
    )

    # 2) Local hotspot patch geometry
    bus_face_area_m2 = max(
        float(bus_length_m) * float(bus_face_width_m) * max(1, int(bars_facing_wall)),
        1e-9,
    )
    patch_area_m2 = max(bus_face_area_m2 * float(patch_spread_factor), 1e-9)

    if debug:
        print("\n[GEOMETRY]")
        print(f"bus_face_area = {bus_face_area_m2:.6f} m²")
        print(f"patch_area    = {patch_area_m2:.6f} m²")
        print(f"area_ratio    = {bus_face_area_m2 / patch_area_m2:.4f}")

    # 3) Thermal resistances
    R_wall = wall_t_m / max(wall_k_W_mK, 1e-12)
    R_out = 1.0 / max(h_out_W_m2K, 1e-12)

    # Inner wall node temperature unknown: Tsi
    # Outer wall node comes from conduction = outside convection:
    # q'' = (Tsi - Tamb) / (R_wall + R_out)
    #
    # Energy balance at inner wall patch:
    # q''_out(Tsi) = q''_conv_from_air(Tsi) + q''_rad_from_bus(Tsi)

    eps_rel = _relative_emissivity(eps_bus_to_wall, eps_wall_inner)
    F = max(0.0, min(1.0, float(view_factor_bus_to_wall)))

    if debug:
        print("\n[RADIATION MODEL]")
        print(f"eps_rel = {eps_rel:.4f}")
        print(f"view_factor = {F:.3f}")

    # Radiation uses bus projected face area, then distributed over patch area
    area_ratio = bus_face_area_m2 / patch_area_m2

    Tsi_C = max(T_air_in_C, T_surface_bulk_C)

    converged = False
    Q_rad_W = 0.0
    Q_conv_W = 0.0

    for it in range(1, max_iter + 1):
        Tsi_K = Tsi_C + 273.15
        Tbus_K = T_bus_C + 273.15

        qpp_out = (Tsi_C - T_amb_C) / (R_wall + R_out)
        qpp_conv_in = h_in_W_m2K * max(T_air_in_C - Tsi_C, 0.0)

        qpp_rad_in = (
            F
            * eps_rel
            * SIGMA
            * max(Tbus_K**4 - Tsi_K**4, 0.0)
            * area_ratio
        )

        resid = qpp_out - (qpp_conv_in + qpp_rad_in)

        # Numerical derivative
        dT = 0.05
        Tsi2_C = Tsi_C + dT
        Tsi2_K = Tsi2_C + 273.15

        qpp_out_2 = (Tsi2_C - T_amb_C) / (R_wall + R_out)
        qpp_conv_in_2 = h_in_W_m2K * max(T_air_in_C - Tsi2_C, 0.0)
        qpp_rad_in_2 = (
            F
            * eps_rel
            * SIGMA
            * max(Tbus_K**4 - Tsi2_K**4, 0.0)
            * area_ratio
        )

        resid2 = qpp_out_2 - (qpp_conv_in_2 + qpp_rad_in_2)
        dresid_dT = (resid2 - resid) / dT

        if abs(dresid_dT) < 1e-9:
            break

        step = resid / dresid_dT
        Tsi_new_C = Tsi_C - step

        # Clamp to physically sane range
        Tsi_new_C = max(T_amb_C, min(T_bus_C, Tsi_new_C))

        if abs(Tsi_new_C - Tsi_C) < tol_C:
            Tsi_C = Tsi_new_C
            converged = True
            qpp_conv_in = h_in_W_m2K * max(T_air_in_C - Tsi_C, 0.0)
            qpp_rad_in = (
                F
                * eps_rel
                * SIGMA
                * max((T_bus_C + 273.15) ** 4 - (Tsi_C + 273.15) ** 4, 0.0)
                * area_ratio
            )
            Q_conv_W = qpp_conv_in * patch_area_m2
            Q_rad_W = qpp_rad_in * patch_area_m2
            break

        Tsi_C = Tsi_new_C

        if debug and (it == 1 or it % 10 == 0):
            print(f"[ITER {it}] Tsi={Tsi_C:.2f}C resid={resid:.4f}")

    qpp_final = (Tsi_C - T_amb_C) / (R_wall + R_out)
    Tso_C = T_amb_C + qpp_final * R_out

    if not converged:
        qpp_conv_in = h_in_W_m2K * max(T_air_in_C - Tsi_C, 0.0)
        qpp_rad_in = (
            F
            * eps_rel
            * SIGMA
            * max((T_bus_C + 273.15) ** 4 - (Tsi_C + 273.15) ** 4, 0.0)
            * area_ratio
        )
        Q_conv_W = qpp_conv_in * patch_area_m2
        Q_rad_W = qpp_rad_in * patch_area_m2

    if debug:
        print("\n[RESULT]")
        print(f"T_inner = {Tsi_C:.2f}C")
        print(f"T_outer = {Tso_C:.2f}C")
        print(f"Q_rad   = {Q_rad_W:.2f} W")
        print(f"Q_conv  = {Q_conv_W:.2f} W")
        print(f"Converged = {converged} in {it} iterations")

    if debug:
        if Q_rad_W < 1.0:
            print("WARNING: Radiation extremely low → likely geometry issue")

        if Q_rad_W > 1e5:
            print("WARNING: Radiation extremely high → likely area or view factor issue")

    return EnclosureSurfaceEstimate(
        T_surface_bulk_C=float(T_surface_bulk_C),
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