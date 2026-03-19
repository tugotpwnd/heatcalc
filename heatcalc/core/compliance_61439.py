from dataclasses import dataclass
from typing import List, Dict, Any


@dataclass
class ComplianceResult:
    tier_id: str

    built_in_ok: bool
    built_in_max_T: float

    terminals_ok: bool
    terminals_max_T: float | None

    enclosure_ok: bool
    enclosure_surface_T: float

    busbar_ok: bool
    busbar_max_T: float

    notes: List[str]


# -------------------------------------------------
# MAIN ENTRY POINT
# -------------------------------------------------

def evaluate_tier_compliance(tier, global_sol, tier_res, ambient_C: float) -> ComplianceResult:
    """
    Evaluate IEC 61439 Table 6 compliance for a tier.
    Uses:
        - global thermal solution (true temps)
        - tier IEC result (air temps, power)
    """

    tier_name = getattr(tier, "name", f"{id(tier)}")
    notes: List[str] = []

    # -----------------------------------------
    # Extract edge temps belonging to this tier
    # -----------------------------------------
    tier_edge_results = [
        e for e in global_sol.edge_results
        if getattr(e, "tier", None) is tier
    ]

    if not tier_edge_results:
        return ComplianceResult(
            tier_id=tier_name,
            built_in_ok=True,
            built_in_max_T=ambient_C,
            terminals_ok=True,
            terminals_max_T=None,
            enclosure_ok=True,
            enclosure_surface_T=ambient_C,
            busbar_ok=True,
            busbar_max_T=ambient_C,
            notes=["No thermal elements in tier"]
        )

    # -----------------------------------------
    # BUSBARS
    # -----------------------------------------
    bus_temps = [
        float(e.T_C) for e in tier_edge_results
        if not e.is_joint
    ]

    busbar_max_T = max(bus_temps) if bus_temps else ambient_C
    busbar_limit = 140.0  # conservative cap

    busbar_ok = busbar_max_T <= busbar_limit

    if not busbar_ok:
        notes.append(f"Busbar temp exceeds {busbar_limit}°C")

    # -----------------------------------------
    # TERMINALS (simplified for now)
    # -----------------------------------------
    # Assume worst case = hottest joint
    joint_temps = [
        float(e.T_C) for e in tier_edge_results
        if e.is_joint
    ]

    terminals_max_T = max(joint_temps) if joint_temps else None

    terminals_limit = ambient_C + 70.0  # 70K rise

    terminals_ok = True
    if terminals_max_T is not None:
        terminals_ok = terminals_max_T <= terminals_limit
        if not terminals_ok:
            notes.append("Terminal temperature rise exceeds 70K")

    # -----------------------------------------
    # BUILT-IN COMPONENTS (placeholder logic)
    # -----------------------------------------
    # Use bus temp as proxy until devices are modeled
    built_in_max_T = busbar_max_T
    built_in_limit = ambient_C + 70.0  # placeholder

    built_in_ok = built_in_max_T <= built_in_limit

    if not built_in_ok:
        notes.append("Built-in component temp exceeds assumed limit")

    # -----------------------------------------
    # ENCLOSURE SURFACE TEMP (simple model)
    # -----------------------------------------
    T_air_top = float(tier_res.get("T_top", ambient_C))
    P_total_W = float(
        tier_res.get("coupling", {}).get("P_base_W", 0.0)
        + tier_res.get("coupling", {}).get("P_bus_W", 0.0)
    )

    # crude area estimate (you can refine later)
    surface_area_m2 = getattr(tier, "surface_area_m2", 1.0)

    h = 7.0  # W/m²K (natural convection assumption)

    q = P_total_W / max(surface_area_m2, 0.1)

    enclosure_surface_T = T_air_top - (q / h)

    enclosure_limit = ambient_C + 30.0  # metal surfaces

    enclosure_ok = enclosure_surface_T <= enclosure_limit

    if not enclosure_ok:
        notes.append("Enclosure surface exceeds 30K rise")

    # -----------------------------------------
    # RETURN
    # -----------------------------------------
    return ComplianceResult(
        tier_id=tier_name,
        built_in_ok=built_in_ok,
        built_in_max_T=built_in_max_T,

        terminals_ok=terminals_ok,
        terminals_max_T=terminals_max_T,

        enclosure_ok=enclosure_ok,
        enclosure_surface_T=enclosure_surface_T,

        busbar_ok=busbar_ok,
        busbar_max_T=busbar_max_T,

        notes=notes
    )