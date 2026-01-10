from __future__ import annotations

IP_MESH_TABLE = {
    2: (None, 1.00),
    3: (2.5, 0.65),
    4: (1.0, 0.45),
}

def ip_open_area_factor(ip_rating_n: int) -> float:
    # IP5X+ vents not permitted anyway; return 0 so math stays honest.
    if int(ip_rating_n) >= 5:
        return 0.0
    return float(IP_MESH_TABLE.get(int(ip_rating_n), (None, 1.0))[1])

def effective_louvre_area_cm2(defn: dict, *, ip_rating_n: int) -> float:
    """
    Effective free inlet area per louvre (cm²):
      manufacturer inlet area × IP mesh open-area factor
    """
    base = float(defn.get("inlet_area_cm2", 0.0))
    return base * ip_open_area_factor(ip_rating_n)

def tier_effective_inlet_area_cm2(*, tier, louvre_def: dict, ip_rating_n: int) -> float:
    """
    Effective total opening area for the tier’s CURRENT vent grid.
    Includes chimney row (+1 at top).
    """
    if int(ip_rating_n) >= 5:
        return 0.0
    if not getattr(tier, "is_ventilated", False):
        return 0.0

    cols = max(1, int(getattr(tier, "vent_cols", 1)))
    rows_bottom = max(1, int(getattr(tier, "vent_rows", 1)))
    total_louvres = cols * (2 * rows_bottom + 1)  # bottom block + top block (+1)

    per = effective_louvre_area_cm2(louvre_def, ip_rating_n=ip_rating_n)
    return total_louvres * per

def tier_max_effective_inlet_area_cm2(
    *,
    tier,
    louvre_def: dict,
    ip_rating_n: int,
) -> float:
    """
    Maximum achievable effective inlet area for this tier
    based on geometric constraints.
    """

    if int(ip_rating_n) >= 5:
        return 0.0

    max_rows_bottom, max_cols = tier.max_louvre_grid(louvre_def)
    max_rows_bottom = max(1, int(max_rows_bottom))
    max_cols = max(1, int(max_cols))

    rows_top = max_rows_bottom + 1
    total_louvres = max_cols * (max_rows_bottom + rows_top)

    per_louvre = effective_louvre_area_cm2(
        louvre_def,
        ip_rating_n=ip_rating_n,
    )

    return total_louvres * per_louvre
