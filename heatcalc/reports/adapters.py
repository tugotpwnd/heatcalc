# reports/adapters.py
from __future__ import annotations
from typing import Iterable, Tuple, List
from .simple_report import TierRow, ComponentRow, CableRow, BusRow

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
                heat_total_w=total_w,
                max_temp_C=getattr(c, "max_temp_C", 70),
                rated_current_A=getattr(c, "rated_current_A", None),
                derating_temp_start_C=getattr(c, "derating_temp_start_C", None),
                derating_function=getattr(c, "derating_function", None),
                key=getattr(c, "key", None),
                category=getattr(c, "category", None),
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

        # Buses
        buses = []
        for bl in getattr(t, "bus_items", lambda: [])():
            # bl is a BusLineItem
            if not hasattr(bl, "spec"):
                continue

            # Calculate length in meters.
            # In your UI, 1 unit = GRID pixels. 25mm = GRID pixels.
            from heatcalc.ui.geometry import GRID
            line = bl.line()
            dx = line.p2().x() - line.p1().x()
            dy = line.p2().y() - line.p1().y()
            length_px = (dx**2 + dy**2)**0.5
            length_m = (length_px / GRID * 25.0) / 1000.0

            # Get current and loss from thermal results if available
            current_A = 0.0
            total_W = 0.0
            if getattr(bl, "thermal_results", None):
                # thermal_results is usually a list of ThermalEdgeResult objects
                # print(f"[DEBUG] Processing bus {getattr(bl, 'bus_id', '???')[:4]} with {len(bl.thermal_results)} results")
                for res in bl.thermal_results:
                    # Use attributes instead of .get() as they are ThermalEdgeResult objects
                    current_A = max(current_A, getattr(res, "I_A", 0.0))
                    total_W += getattr(res, "P_gen_W", 0.0)
                    # print(f"  Result: I={getattr(res, 'I_A', 0.0)}, P={getattr(res, 'P_gen_W', 0.0)}")

            buses.append(BusRow(
                name=f"Bus {bl.bus_id[:4]}",
                width_mm=float(bl.spec.width_mm),
                thickness_mm=float(bl.spec.thickness_mm),
                parallel_bars=int(bl.spec.bars_in_parallel),
                length_m=length_m,
                current_A=current_A,
                total_W=total_W
            ))

        # Explicit Joints (new)
        joints = []
        from types import SimpleNamespace
        from heatcalc.ui.bus_items import BusJoinItem
        # we can use t.bus_items() but it might filter. safer to use childItems() of the bus_layer if it exists
        # or just scene().items() filtered by tier
        all_child_items = []
        if hasattr(t, "bus_layer"):
            all_child_items = t.bus_layer.childItems()

        for item in all_child_items:
            if isinstance(item, BusJoinItem):
                res = getattr(item, "thermal_result", None)
                if res:
                    joints.append(SimpleNamespace(
                        joint_id=getattr(item, "join_id", "Joint")[:8],
                        I_A=float(getattr(res, "I_A", 0.0)),
                        P_W=float(getattr(res, "P_gen_W", 0.0))
                    ))

        tier = TierRow(
            tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),
            width_mm=width_mm, height_mm=height_mm, depth_mm=depth_mm,
            components=comps, cables=cabs, buses=buses, joints=joints,
            max_temp_C=float(getattr(t, "max_temp_C", 70.0)),
            effective_max_temp_C=float(t.effective_max_temp_C()) if hasattr(t, "effective_max_temp_C") else None,
            h_partitions_enabled=bool(getattr(t, "h_partitions_enabled", False)),
            h_partitions_count=int(getattr(t, "h_partitions_count", 1)),
        )
        out.append(tier)
        total += tier.heat_w

    return out, total
