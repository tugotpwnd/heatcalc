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

def evaluate_tier_compliance(
    tier,
    global_sol,
    tier_res,
    ambient_C,
    tier_edges,
    graph
):
    tier_name = getattr(tier, "name", f"{id(tier)}")
    notes: List[str] = []

    # -----------------------------------------
    # Extract edges for this tier
    # -----------------------------------------
    edge_ids = {e.id for e in tier_edges.get(tier, [])}

    tier_edge_results = [
        er for er in global_sol.edge_results
        if er.edge_id in edge_ids
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
    busbar_limit = 140.0

    busbar_ok = busbar_max_T <= busbar_limit

    if not busbar_ok:
        notes.append(f"Busbar exceeds 140°C ({busbar_max_T:.1f}°C)")

    # -----------------------------------------
    # TERMINALS (graph-based)
    # -----------------------------------------

    tier_node_ids = set()

    for e in tier_edges.get(tier, []):
        tier_node_ids.add(e.u)
        tier_node_ids.add(e.v)

    has_loads = any(
        load.node in tier_node_ids
        for load in graph.loads
    )
    terminals_max_T = None
    terminals_ok = True

    if has_loads:
        terminal_temps = [t + 15.0 for t in bus_temps] if bus_temps else []

        if terminal_temps:
            terminals_max_T = max(terminal_temps)
            terminals_limit = 105.0

            terminals_ok = terminals_max_T <= terminals_limit

            if not terminals_ok:
                notes.append(
                    f"Terminal exceeds 105°C ({terminals_max_T:.1f}°C)"
                )

    # -----------------------------------------
    # BUILT-IN COMPONENTS
    # -----------------------------------------
    T_top = float(tier_res.get("T_top", ambient_C))

    # pull component limits from tier
    component_limits = []

    for comp in getattr(tier, "components", []):
        tmax = getattr(comp, "max_temp_C", None)
        if tmax:
            component_limits.append(float(tmax))

    if component_limits:
        built_in_limit = min(component_limits)
    else:
        built_in_limit = ambient_C + 70.0  # fallback

    built_in_max_T = T_top
    built_in_ok = built_in_max_T <= built_in_limit

    if not built_in_ok:
        notes.append(
            f"Built-in exceeds limit ({built_in_max_T:.1f}°C > {built_in_limit:.1f}°C)"
        )

    # -----------------------------------------
    # ENCLOSURE
    # -----------------------------------------
    T_top = float(tier_res.get("T_top", ambient_C))

    enclosure_surface_T = estimate_enclosure_surface_temp_C(
        T_air_in_C=T_top,
        T_amb_C=ambient_C,
    )
    enclosure_limit = ambient_C + 30.0
    enclosure_ok = enclosure_surface_T <= enclosure_limit

    if not enclosure_ok:
        notes.append(
            f"Enclosure exceeds limit ({enclosure_surface_T:.1f}°C)"
        )

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
    Estimate the external enclosure surface temperature (touch temperature)
    from internal air temperature using a 1D steady-state thermal resistance model.

    ------------------------------------------------------------------------
    MODEL OVERVIEW
    ------------------------------------------------------------------------

    This model represents heat transfer from internal air → enclosure wall →
    external ambient using three thermal resistances in series:

        Internal convection  +  Wall conduction  +  External convection

    Heat flux per unit area:

        q'' = (T_air_in - T_amb) / (R_in + R_wall + R_out)

    Outer surface temperature:

        T_surface = T_amb + q'' * R_out

    ------------------------------------------------------------------------
    INPUTS
    ------------------------------------------------------------------------

    T_air_in_C : float
        Internal air temperature adjacent to enclosure wall (°C)
        → Use worst-case (typically T_top from IEC 60890)

    T_amb_C : float
        External ambient air temperature (°C)

    ------------------------------------------------------------------------
    PARAMETERS (with engineering defaults)
    ------------------------------------------------------------------------

    h_in_W_m2K : float (default = 5.0)
        Internal natural convection heat transfer coefficient [W/m²K]

    h_out_W_m2K : float (default = 8.0)
        External natural convection heat transfer coefficient [W/m²K]

    wall_k_W_mK : float (default = 45.0)
        Thermal conductivity of enclosure wall material [W/mK]
        → ~45 W/mK for mild steel

    wall_t_m : float (default = 1.6e-3)
        Wall thickness [m]
        → Typical switchboard sheet steel ~1.5–2.0 mm

    ------------------------------------------------------------------------
    ASSUMPTIONS
    ------------------------------------------------------------------------

    1. Steady-state thermal conditions
    2. 1D heat flow through enclosure wall
    3. Uniform internal air temperature at wall (no local hotspots)
    4. Natural convection on both internal and external surfaces
    5. Radiation effects are lumped into convection coefficients
    6. Thin metal wall → conduction resistance is small vs convection
    7. Heat transfer dominated by air film resistances

    ------------------------------------------------------------------------
    LIMITATIONS
    ------------------------------------------------------------------------

    - Does NOT account for:
        • solar loading
        • forced ventilation / fans
        • directional heat sources near walls
        • louvre jet effects
        • multi-zone CFD behaviour

    - IEC 60890 provides INTERNAL air temperature only.
      This model bridges to EXTERNAL touch temperature.

    ------------------------------------------------------------------------
    ENGINEERING INTERPRETATION
    ------------------------------------------------------------------------

    - If h_out is low → surface gets hotter (poor cooling)
    - If h_in is low → internal air poorly couples to wall
    - Wall conduction usually negligible for steel enclosures

    Typical outcome:
        T_amb < T_surface < T_air_in

    ------------------------------------------------------------------------
    RETURNS
    ------------------------------------------------------------------------

    float : Estimated external enclosure surface temperature (°C)

    ------------------------------------------------------------------------
    """

    # --- Thermal resistances per unit area (m²K/W) ---
    Rpp_in = 1.0 / max(h_in_W_m2K, 1e-9)
    Rpp_wall = wall_t_m / max(wall_k_W_mK, 1e-9)
    Rpp_out = 1.0 / max(h_out_W_m2K, 1e-9)

    # --- Heat flux (W/m²) ---
    qpp = (T_air_in_C - T_amb_C) / (Rpp_in + Rpp_wall + Rpp_out)

    # --- Outer surface temperature ---
    T_surface_C = T_amb_C + qpp * Rpp_out

    return float(T_surface_C)