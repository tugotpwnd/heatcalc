# heatcalc/core/iec60890_geometry.py
from __future__ import annotations
from typing import Dict, Tuple, List

from ..ui.tier_item import TierItem
from ..ui.designer_view import GRID


MM_PER_GRID = 25
_EPS = 1e-3


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> bool:
    return not (a1 <= b0 or b1 <= a0)


def touching_sides(t: TierItem, tiers: List[TierItem]) -> Dict[str, bool]:
    """
    Determine which faces of tier t are touching another tier.
    Returns: dict with keys top, bottom, left, right
    """
    r = t.shapeRect()

    def any_touch(pred):
        for o in tiers:
            if o is t:
                continue
            if pred(o.shapeRect()):
                return True
        return False

    return {
        "left": any_touch(
            lambda ro: abs(r.left() - ro.right()) < _EPS
            and _overlap_1d(r.top(), r.bottom(), ro.top(), ro.bottom())
        ),
        "right": any_touch(
            lambda ro: abs(r.right() - ro.left()) < _EPS
            and _overlap_1d(r.top(), r.bottom(), ro.top(), ro.bottom())
        ),
        "top": any_touch(
            lambda ro: abs(r.top() - ro.bottom()) < _EPS
            and _overlap_1d(r.left(), r.right(), ro.left(), ro.right())
        ),
        "bottom": any_touch(
            lambda ro: abs(r.bottom() - ro.top()) < _EPS
            and _overlap_1d(r.left(), r.right(), ro.left(), ro.right())
        ),
    }


def b_map_for_tier(t: TierItem, touching: Dict[str, bool]) -> Dict[str, float]:
    """
    IEC 60890 Table III surface factors.
    """

    bmap = {
        # top
        "top": 0.7 if touching["top"] else 1.4,
        # floor not taken into account
        "bottom": 0.0,
        # sides
        "left": 0.5 if touching["left"] else 0.9,
        "right": 0.5 if touching["right"] else 0.9,
        # front always exposed in this model
        "front": 0.9,
        # rear covered if wall-mounted
        "rear": 0.5 if getattr(t, "wall_mounted", False) else 0.9,
    }

    # ---- DEBUG PRINT (SAFE, NO API CHANGES) -----------------------------
    print(
        f"[IEC60890][b-factors] Tier '{getattr(t, 'name', '?')}' | "
        f"top={bmap['top']}, bottom={bmap['bottom']}, "
        f"left={bmap['left']}, right={bmap['right']}, "
        f"front={bmap['front']}, rear={bmap['rear']}"
    )
    print(f"    touching={touching}, wall_mounted={getattr(t, 'wall_mounted', False)}")
    # --------------------------------------------------------------------

    return bmap


def dimensions_m(t: TierItem) -> Tuple[float, float, float]:
    """Return (width, height, depth) in metres."""
    w_mm = max(1, int(t._rect.width() / GRID * MM_PER_GRID))
    h_mm = max(1, int(t._rect.height() / GRID * MM_PER_GRID))
    d_mm = max(1, int(getattr(t, "depth_mm", 400)))
    return w_mm / 1000.0, h_mm / 1000.0, d_mm / 1000.0


def resolved_surfaces(t: TierItem, tiers: list[TierItem]) -> list[tuple[str, float, float, float]]:
    """
    Returns list of (name, dim1_m, dim2_m, b_factor)
    """
    w, h, d = dimensions_m(t)
    touching = touching_sides(t, tiers)
    bmap = b_map_for_tier(t, touching)

    return [
        ("Roof",  w, d, bmap["top"]),
        ("Front", w, h, bmap["front"]),
        ("Rear",  w, h, bmap["rear"]),
        ("Left",  h, d, bmap["left"]),
        ("Right", h, d, bmap["right"]),
    ]



def effective_area_and_fg(
    t: TierItem, bmap: Dict[str, float]
) -> Tuple[float, float, float]:
    """
    Returns:
      Ae — effective cooling surface (m²)
      f  — h^1.35 / Ab
      g  — h / w
    """
    w, h, d = dimensions_m(t)

    A_top = w * d
    A_bottom = w * d
    A_left = h * d
    A_right = h * d
    A_front = w * h
    A_rear = w * h

    Ae = (
        bmap["top"] * A_top
        + bmap["bottom"] * A_bottom
        + bmap["left"] * A_left
        + bmap["right"] * A_right
        + bmap["front"] * A_front
        + bmap["rear"] * A_rear
    )

    Ab = max(1e-9, A_top)
    f = (h ** 1.35) / Ab
    g = h / max(1e-9, w)

    return Ae, f, g


# ---------------------------------------------------------------------
# NEW: Curve number selection (centralised) — matches SwitchboardTab logic
# ---------------------------------------------------------------------

def curve_no_for_tier(t: TierItem, tiers: List[TierItem], wall_mounted: bool) -> int:
    """
    Reproduces your existing SwitchboardTab._recompute_all_curves mapping
    (left/right touching + top covered) so the visual badge and calc agree.
    """
    touch = touching_sides(t, tiers)
    left_touch = bool(touch["left"])
    right_touch = bool(touch["right"])
    top_covered = bool(touch["top"])

    both = left_touch and right_touch
    one = (left_touch ^ right_touch)

    if not left_touch and not right_touch and not top_covered:
        return 3 if wall_mounted else 1
    if one and not top_covered:
        return 4 if wall_mounted else 2
    if both and not top_covered:
        return 5 if wall_mounted else 3
    if wall_mounted and both and top_covered:
        return 4

    return 4 if wall_mounted else 3


def apply_curve_state_to_tiers(
    *,
    tiers: List[TierItem],
    wall_mounted: bool,
    debug: bool = False
) -> None:
    """
    One call updates ALL tiers:
      - tier.wall_mounted
      - tier.curve_no

    This must be called whenever geometry changes and before report export.
    """
    for t in tiers:
        t.wall_mounted = bool(wall_mounted)
        t.curve_no = int(curve_no_for_tier(t, tiers, wall_mounted))

        if debug:
            touch = touching_sides(t, tiers)
            tag = getattr(t, "name", getattr(t, "tag", "<?>"))
            print(
                f"[IEC60890] {tag}: "
                f"L={touch['left']} R={touch['right']} TOP={touch['top']} "
                f"wall={wall_mounted} => curve_no={t.curve_no}"
            )

        try:
            t.update()
        except Exception:
            pass

def tier_geometry(t: TierItem, tiers: list[TierItem]) -> dict:
    w, h, d = dimensions_m(t)
    touching = touching_sides(t, tiers)
    bmap = b_map_for_tier(t, touching)
    Ae, f, g = effective_area_and_fg(t, bmap)

    return {
        "w_m": w,
        "h_m": h,
        "d_m": d,
        "touching": touching,
        "bmap": bmap,
        "Ae": Ae,
        "f": f,
        "g": g,
    }


def compute_face_exposure(
    tier: TierItem,
    tiers: list[TierItem],
    tol_px: float = 1.0
) -> dict[str, bool]:
    """
    Returns which faces are exposed to ambient air.
    Faces blocked by another tier are NOT exposed.
    """
    rect = tier.shapeRect()

    faces = {
        "left": True,
        "right": True,
        "top": True,
    }

    for other in tiers:
        if other is tier:
            continue

        orect = other.shapeRect()

        # LEFT face blocked
        if abs(orect.right() - rect.left()) <= tol_px:
            if orect.top() < rect.bottom() and orect.bottom() > rect.top():
                faces["left"] = False

        # RIGHT face blocked
        if abs(orect.left() - rect.right()) <= tol_px:
            if orect.top() < rect.bottom() and orect.bottom() > rect.top():
                faces["right"] = False

        # TOP face blocked
        if abs(orect.bottom() - rect.top()) <= tol_px:
            if orect.left() < rect.right() and orect.right() > rect.left():
                faces["top"] = False

    return faces

def determine_curve_no(
    tier: TierItem,
    faces: dict[str, bool]
) -> int:
    """
    Maps exposed faces to IEC 60890 curve number.
    """
    exposed = sum(1 for v in faces.values() if v)

    if exposed == 3:
        return 1  # free-standing
    if exposed == 2:
        return 2
    if exposed == 1:
        return 3
    return 4  # fully enclosed / embedded
