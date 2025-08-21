# reports/adapters.py
from __future__ import annotations
from typing import Iterable, Tuple, List
from .simple_report import TierRow, ComponentRow, CableRow

def tiers_from_items(tier_items: Iterable) -> Tuple[List[TierRow], float]:
    """Convert live TierItem objects into TierRow + return total heat."""
    out: List[TierRow] = []
    total = 0.0
    for t in tier_items:
        # Dimensions from your TierItem (pixels→mm handled earlier in your app; here they’re already mm fields)
        width_mm  = int(getattr(t, "width_mm",  0) or getattr(t, "w_mm", 0) or 0)
        height_mm = int(getattr(t, "height_mm", 0) or getattr(t, "h_mm", 0) or 0)
        depth_mm  = int(getattr(t, "depth_mm",  0) or 0)

        # Components
        comps = []
        for c in getattr(t, "component_entries", []):
            qty   = int(getattr(c, "qty", 1) or 1)
            each  = float(getattr(c, "heat_each_w", 0.0) or 0.0)
            total_w = qty * each
            comps.append(ComponentRow(
                description=getattr(c, "description", getattr(c, "key", "Component")),
                part_no=getattr(c, "part_number", ""),
                qty=qty,
                heat_each_w=each,
                heat_total_w=total_w
            ))

        # Cables
        cabs = []
        for cb in getattr(t, "cables", []):
            cabs.append(CableRow(
                name=getattr(cb, "name", "Cable"),
                csa_mm2=float(getattr(cb, "csa_mm2", 0.0) or 0.0),
                installation=str(getattr(cb, "installation", "")),
                length_m=float(getattr(cb, "length_m", 0.0) or 0.0),
                current_A=float(getattr(cb, "current_A", 0.0) or 0.0),
                P_Wpm=float(getattr(cb, "P_Wpm", getattr(cb, "Pn_Wpm", 0.0)) or 0.0),
                total_W=float(getattr(cb, "total_W", 0.0) or 0.0),
            ))

        tier = TierRow(
            tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),
            width_mm=width_mm, height_mm=height_mm, depth_mm=depth_mm,
            components=comps, cables=cabs
        )
        out.append(tier)
        total += tier.heat_w

    return out, total
