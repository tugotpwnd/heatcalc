from typing import Dict, List, Any, Tuple
from PyQt5.QtCore import QRectF, QPointF, pyqtSignal, Qt, QSizeF
from PyQt5.QtGui import QPen, QBrush, QFont, QColor, QFontMetrics
from PyQt5.QtWidgets import (
    QGraphicsObject, QStyleOptionGraphicsItem, QWidget,
    QGraphicsRectItem, QMenu, QGraphicsItem
)

from .designer_view import GRID, snap
from ..core.component_library import DEFAULT_COMPONENTS  # <-- you said core
# tier_item.py  (add near the other imports)
from dataclasses import dataclass, asdict

from ..core.louvre_calc import effective_louvre_area_cm2

HANDLE = 10  # px
CORNERS = ("tl", "tr", "bl", "br")

# --- Lovure helper  ----------------------------------------
def tier_effective_inlet_area_cm2(
    *,
    tier,
    louvre_def: dict,
    ip_rating_n: int,
) -> float:
    """
    Computes total effective inlet area for a tier, including:
    - bottom louvres
    - top louvres (+1 chimney row)
    - IP mesh derating

    Returns cm²
    """

    # Hard IP gate
    if not tier.is_ventilated:
        return 0.0
    if int(ip_rating_n) >= 5:
        return 0.0  # ventilation not permitted

    per_louvre = effective_louvre_area_cm2(
        louvre_def,
        ip_rating_n=ip_rating_n,
    )

    cols = max(1, int(getattr(tier, "vent_cols", 1)))
    rows_bottom = max(1, int(getattr(tier, "vent_rows", 1)))
    rows_top = rows_bottom + 1  # chimney effect

    total_louvres = cols * (rows_bottom + rows_top)

    return total_louvres * per_louvre


# -------------------------------------------

from PyQt5.QtWidgets import QGraphicsItem
from PyQt5.QtGui import (
    QPainter, QFont, QFontMetrics, QColor
)
from PyQt5.QtCore import QRectF, Qt


class TierOverlayItem(QGraphicsItem):
    def __init__(self, tier):
        super().__init__(tier)  # 👈 child of TierItem
        self.tier = tier
        self.setZValue(tier.zValue() + 1)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        # same local coord system as TierItem
        return self.tier._rect

    def paint(self, painter: QPainter, option, widget=None):
        lt = self.tier.live_thermal
        if not lt or not self.tier.show_live_overlay:
            return

        # 🔒 HARD GATE: no heat → no overlay
        if (
                not lt
                or not self.tier.show_live_overlay
                or self.tier.total_heat() <= 0.0
        ):
            return

        PAD = 6
        font_main = QFont("Consolas", 9)
        painter.setFont(font_main)

        # ---- build lines ----
        curvefit = lt.get("curvefit") or {}
        k_meta = curvefit.get("k") or {}
        c_meta = curvefit.get("c") or {}

        Ae_raw = lt.get("Ae")
        Ae_snap = k_meta.get("used_ae")

        lines = [
            f"Ae: {Ae_raw:.2f} m²" if Ae_snap is None else
            f"Ae: {Ae_raw:.2f} → {Ae_snap:.2f} m²",

            f"ΔT(1.0t): {lt.get('dt_top', 0.0):.1f} K",
            f"Temp (Top): {lt.get('T_top', 0.0):.1f} °C",
            f"Temp Limit: {lt.get('limit_C', 0.0):.1f} °C",
        ]

        P890 = lt.get("P_890", 0.0)
        Pcool = lt.get("P_cooling", 0.0)

        lines.append(f"P890: {P890:.1f} W")

        if Pcool > 0.0:
            lines.append(f"Excess (Fan): {Pcool:.1f} W")
        else:
            lines.append("Cooling: NOT REQUIRED")

        # --- Vent recommendation ---
        if lt.get("vent_recommended"):
            lines.append("Ventilation: RECOMMENDED")

        # --- Installed ventilation info ---
        if lt.get("ventilated"):
            Ain = lt.get("inlet_area_cm2", 0.0)
            if Ain > 0:
                lines.append(f"Vent Ae(in): {Ain:.0f} cm²")

        # --- Airflow ---
        airflow = lt.get("airflow_m3h")
        if airflow:
            lines.append(f"Air: {airflow:.0f} m³/h")

        # =====================================================
        # Annex K transparency (ONLY when it actually applies)
        # =====================================================
        ak = lt.get("annex_k") or {}

        special_annex_k_case = (
                lt.get("ventilated", False)
                and Pcool > 0.0
                and ak.get("vents_ignored", False)
        )

        if special_annex_k_case:
            ctx = [
                "Annex K: SEALED ENCLOSURE",
                f"k(K): {ak.get('k', 0.0):.3f}",
                f"c(K): {ak.get('c', 0.0):.3f}",
                f"x(K): {ak.get('x', 0.0):.3f}",
            ]
        else:
            # ---- context / coefficients ----
            ctx = [
                f"k={lt.get('k', 0.0):.3f}",
                f"c={lt.get('c', 0.0):.3f}",
                f"x={lt.get('x', 0.0):.3f}",
            ]

        if special_annex_k_case:
            ctx.append("⚠ Vents ignored (Annex K)")

        if lt.get("g") is not None:
            ctx.append(f"g={lt['g']:.3f}")

        f_raw = lt.get("f")
        f_snap = c_meta.get("used_f")
        if f_raw is not None:
            ctx.append(
                f"f={f_raw:.3f}" if not f_snap
                else f"f={f_raw:.3f} → {f_snap:.2f}"
            )

        if lt.get("d") is not None:
            ctx.append(f"d={lt['d']:.3f}")

        all_lines = lines + [""] + ctx

        fm = QFontMetrics(font_main)
        lh = fm.height()
        block_w = min(
            max(fm.horizontalAdvance(s) for s in all_lines) + 2 * PAD,
            self.boundingRect().width() - 8,
        )
        block_h = len(all_lines) * lh + 2 * PAD

        x = self.boundingRect().left() + 4
        y = self.boundingRect().bottom() - block_h - 4

        # ---- background ----
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawRoundedRect(QRectF(x, y, block_w, block_h), 6, 6)

        # ---- text ----
        painter.setPen(QColor(20, 20, 20))
        ty = y + PAD
        for line in all_lines:
            painter.drawText(
                QRectF(x + PAD, ty, block_w - 2 * PAD, lh),
                Qt.AlignLeft | Qt.AlignVCenter,
                line,
            )
            ty += lh

        # -------------------------------------------------
        # Active cooling indicator (top filters + bottom fan)
        # -------------------------------------------------
        if lt.get("P_cooling", 0.0) > 0.0:
            tier = self.tier
            r = tier._rect
            cx = r.center().x()

            # ---- layout tuning ----
            ARC_W = 36
            ARC_H = 12
            ARC_GAP = 6

            FAN_R = 18

            TOP_MARGIN = 26     # ↑ increased to clear spacing text
            BOTTOM_MARGIN = 34  # ↑ increased to clear curve / overlays

            pen = QPen(QColor("#ff9f1c"), 2.2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)

            # ---- FILTER ARCS (TOP-ANCHORED) ----
            arc_top = r.top() + TOP_MARGIN

            for i in range(3):
                painter.drawArc(
                    QRectF(
                        cx - ARC_W / 2,
                        arc_top + i * ARC_GAP,
                        ARC_W,
                        ARC_H,
                    ),
                    0 * 16,
                    180 * 16,
                )

            # ---- FAN (BOTTOM-ANCHORED) ----
            cy_fan = r.bottom() - (FAN_R + BOTTOM_MARGIN)

            # outer circle
            painter.drawEllipse(QPointF(cx, cy_fan), FAN_R, FAN_R)

            # blades
            for angle in (0, 120, 240):
                painter.save()
                painter.translate(cx, cy_fan)
                painter.rotate(angle)
                painter.drawLine(0, 0, FAN_R - 2, 0)
                painter.restore()


        # -------------------------------------------------
        # Touching / spacing indicators (IEC authoritative)
        # -------------------------------------------------
        tier = self.tier
        lt = tier.live_thermal or {}
        covered = lt.get("covered_sides", tier.covered_sides)

        inset = 6.0
        pen_touch = QPen(QColor("#FFD166"), 2)
        pen_text = QPen(QColor(120, 120, 120))

        r = tier._rect

        painter.save()

        # ---------- touching faces ----------
        painter.setPen(pen_touch)

        if covered.get("left"):
            painter.drawLine(
                QPointF(r.left() + inset, r.top() + inset),
                QPointF(r.left() + inset, r.bottom() - inset),
            )

        if covered.get("right"):
            painter.drawLine(
                QPointF(r.right() - inset, r.top() + inset),
                QPointF(r.right() - inset, r.bottom() - inset),
            )

        if covered.get("top"):
            painter.drawLine(
                QPointF(r.left() + inset, r.top() + inset),
                QPointF(r.right() - inset, r.top() + inset),
            )

        if covered.get("bottom"):
            painter.drawLine(
                QPointF(r.left() + inset, r.bottom() - inset),
                QPointF(r.right() - inset, r.bottom() - inset),
            )

        # ---------- non-touching annotation ----------
        LABEL = "SPACING > 100 MM"

        painter.setPen(pen_text)
        painter.setFont(QFont("Segoe UI", 8))

        TEXT_W = 140
        TEXT_H = 16
        MARGIN = 8

        # LEFT wall — extend rightwards
        if not covered.get("left"):
            painter.drawText(
                QRectF(
                    r.left() + MARGIN,
                    r.center().y() - TEXT_H / 2,
                    TEXT_W,
                    TEXT_H,
                ),
                Qt.AlignLeft | Qt.AlignVCenter,
                LABEL,
            )

        # RIGHT wall — extend leftwards
        if not covered.get("right"):
            painter.drawText(
                QRectF(
                    r.right() - TEXT_W - MARGIN,
                    r.center().y() - TEXT_H / 2,
                    TEXT_W,
                    TEXT_H,
                ),
                Qt.AlignRight | Qt.AlignVCenter,
                LABEL,
            )

        # TOP wall only — centred, horizontal
        if not covered.get("top"):
            painter.drawText(
                QRectF(
                    r.center().x() - TEXT_W / 2,
                    r.top() + MARGIN,
                    TEXT_W,
                    TEXT_H,
                ),
                Qt.AlignHCenter | Qt.AlignVCenter,
                LABEL,
            )


class _Handle(QGraphicsRectItem):
    def __init__(self, parent, role: str):
        super().__init__(-HANDLE/2, -HANDLE/2, HANDLE, HANDLE, parent)
        self.role = role
        self.setBrush(QBrush(QColor("#eeeeee")))
        self.setPen(QPen(QColor("#222222")))
        # ❌ NOT movable – we will not let Qt move it
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setZValue(10)
        if role in ("tl", "br"):
            self.setCursor(Qt.SizeFDiagCursor)
        else:  # "tr","bl"
            self.setCursor(Qt.SizeBDiagCursor)

        self.face_exposure = {
            "left": True,
            "right": True,
            "top": True,
        }

    def mousePressEvent(self, event):
        # lock parent movement while resizing
        parent = self.parentItem()
        if parent is not None:
            parent.setFlag(QGraphicsItem.ItemIsMovable, False)
            parent._begin_resize(self.role)
        event.accept()

    def mouseMoveEvent(self, event):
        # drive resize from the cursor position (scene coords)
        parent = self.parentItem()
        if parent is not None:
            parent._resize_from_handle(self.role, event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event):
        parent = self.parentItem()
        if parent is not None:
            parent._end_resize(self.role)
            parent.setFlag(QGraphicsItem.ItemIsMovable, True)
        event.accept()
        try:
            parent.geometryCommitted.emit()
        except Exception:
            pass
        event.accept()


class ResizableBox(QGraphicsObject):
    """Moveable, resizable rect with four corner handles; snaps to grid on release."""
    rectChanged = pyqtSignal()

    def __init__(self, x=0, y=0, w=GRID*8, h=GRID*6, color=QColor("#00aaff")):
        super().__init__()
        self._rect = QRectF(0, 0, w, h)
        self.setPos(snap(x), snap(y))
        self._pen = QPen(color, 2)
        self._brush = QBrush(Qt.NoBrush)
        self.setFlag(QGraphicsObject.ItemIsMovable, True)
        self.setFlag(QGraphicsObject.ItemIsSelectable, True)
        self.setFlag(QGraphicsObject.ItemSendsGeometryChanges, True)

        self._updating_handles = False
        self._handles: Dict[str, _Handle] = {r: _Handle(self, r) for r in CORNERS}
        self._layout_handles()

    # --- geometry -----------------------------------------------------------
    def boundingRect(self) -> QRectF:
        m = 12
        return self._rect.adjusted(-m, -m, m, m)

    def _layout_handles(self):
        r = self._rect
        positions = {
            "tl": QPointF(r.left(), r.top()),
            "tr": QPointF(r.right(), r.top()),
            "bl": QPointF(r.left(), r.bottom()),
            "br": QPointF(r.right(), r.bottom()),
        }
        self._updating_handles = True
        try:
            for role, h in self._handles.items():
                h.setPos(positions[role])  # local coords
        finally:
            self._updating_handles = False

    def _begin_resize(self, role: str):
        # cache opposite corner (in LOCAL coords)
        r = self._rect
        self._resize_anchor = {
            "tl": QPointF(r.right(), r.bottom()),
            "tr": QPointF(r.left(), r.bottom()),
            "bl": QPointF(r.right(), r.top()),
            "br": QPointF(r.left(), r.top()),
        }[role]

    def _resize_from_handle(self, role: str, scene_pt: QPointF):
        # map cursor to LOCAL coords and rebuild rect from anchor -> cursor
        p = self.mapFromScene(scene_pt)
        a = self._resize_anchor
        left, right = sorted((a.x(), p.x()))
        top, bottom = sorted((a.y(), p.y()))
        # snap and min size
        left = snap(left);
        right = snap(right)
        top = snap(top);
        bottom = snap(bottom)
        if right - left < GRID: right = left + GRID
        if bottom - top < GRID: bottom = top + GRID
        self.prepareGeometryChange()
        self._rect = QRectF(QPointF(left, top), QPointF(right, bottom))
        self._layout_handles()
        self.update()
        self.rectChanged.emit()

    def _end_resize(self, role: str):
        # optional: snap the whole item origin after resize (keeps consistent)
        pass

    def itemChange(self, change, value):
        # Fire on moves too (resizes already emit in _resize_from_handle)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.rectChanged.emit()
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        # snap whole item after move drag (not during handle drag)
        p = self.pos()
        sp = QPointF(snap(p.x()), snap(p.y()))
        if sp != p:
            self.setPos(sp)
        super().mouseReleaseEvent(event)

    def shapeRect(self) -> QRectF:
        return self.mapRectToScene(self._rect)



    # --- painting -----------------------------------------------------------
    def paint(self, painter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        painter.setPen(self._pen)
        painter.setBrush(self._brush)
        painter.drawRect(self._rect)


# tier_item.py  (add above TierItem class)
@dataclass
class CableEntry:
    name: str
    csa_mm2: float
    installation: str
    air_temp_C: int
    current_A: float
    length_m: float
    In_A: float
    Pn_Wpm: float
    P_Wpm: float
    total_W: float
    install_type: int = 1     # NEW
    factor_install: float = 1.0

    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, d): return cls(**d)

@dataclass
class ComponentEntry:
    key: str
    category: str
    part_number: str
    description: str
    heat_each_w: float
    qty: int
    max_temp_C: int = 70  # NEW: per-component temperature rating

class TierItem(ResizableBox):
    """A tier: draggable/resizable, components, curve tag, context menu delete."""
    requestDelete = pyqtSignal(object)
    geometryCommitted = pyqtSignal()  # <- emit once after a resize gesture
    positionCommitted = pyqtSignal()  # <- emit once after a move gesture

    def __init__(self, name: str, x=0, y=0, w=GRID * 8, h=GRID * 6, depth_mm: int = 200):
        super().__init__(x, y, w, h, QColor("#00aaff"))

        self.name = name

        # --- Ventilation ----------------------------------------------------
        # Ventilation (IEC 60890 uses cm²)
        self.vent_area_cm2: float | None = None
        self.vent_label: str | None = None
        self.is_ventilated: bool = False
        self.vent_rows = 1
        self.vent_cols = 1
        self.get_louvre_definition = None  # callable injected by owner

        # --- Contents -------------------------------------------------------
        self.component_entries: list[ComponentEntry] = []
        self.cables: list[CableEntry] = []

        # --- Geometry / IEC inputs -----------------------------------------
        self.wall_mounted = False
        self.curve_no = 1
        self.depth_mm = int(depth_mm)

        # --- Temperature limits --------------------------------------------
        self.max_temp_C = 70
        self.use_auto_component_temp = False

        # --- Interaction ---------------------------------------------------
        self.setZValue(5)
        self._last_pos_for_commit = QPointF(self.pos())

        self.covered_sides = {
            "left": False,
            "right": False,
            "top": False,
            "bottom": False,
        }

        # --- Live IEC overlay ----------------------------------------------
        self.live_thermal: dict | None = None
        self.show_live_overlay: bool = True
        self.overlay_item = TierOverlayItem(self)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        if self.pos() != self._last_pos_for_commit:
            self._last_pos_for_commit = QPointF(self.pos())
            self.positionCommitted.emit()

    def set_depth_mm(self, mm: int):
        self.depth_mm = max(1, int(mm))
        self.update()

    def set_max_temp_C(self, val: int):
        self.max_temp_C = max(1, int(val))  # guard against nonsense
        self.update()

    def set_auto_limit(self, on: bool):
        self.use_auto_component_temp = bool(on)
        self.update()

    def itemChange(self, change, value):
        return super().itemChange(change, value)

    # ------------------------------------------------------------------ Vent

    def clear_vent(self):
        self.is_ventilated = False
        self.vent_label = None
        self.vent_area_cm2 = None
        self.update()

    def vent_area_for_iec(self) -> float:
        return float(self.vent_area_cm2 or 0.0)

    def vent_louvre_count(self) -> int:
        # bottom vents
        bottom = self.vent_rows * self.vent_cols
        # top vents (+1 row for chimney)
        top = (self.vent_rows + 1) * self.vent_cols
        return bottom + top

    # ----- heat helpers -----------------------------------------------------

    def cables_total_heat_W(self) -> float:
        # cables (sum actual totals saved on the tier)
        cable_w = sum(c.total_W for c in self.cables)
        return cable_w

    def components_total_heat_W(self) -> float:
        return sum(ce.heat_each_w * ce.qty for ce in self.component_entries)

    def total_heat(self) -> float:
        return self.components_total_heat_W() + self.cables_total_heat_W()

    def set_component_count(self, comp: str, n: int):
        if n <= 0:
            self.components.pop(comp, None)
        else:
            self.components[comp] = n
        self.update()

    def contextMenuEvent(self, event):
        menu = QMenu()

        act_copy = menu.addAction("Copy tier contents")
        act_paste = menu.addAction("Paste tier contents")
        menu.addSeparator()
        act_delete = menu.addAction("Delete tier")

        chosen = menu.exec_(event.screenPos())
        if not chosen:
            return

        # Walk up parent chain to find SwitchboardTab
        switchboard = None
        for view in self.scene().views():
            w = view
            while w is not None:
                if w.__class__.__name__ == "SwitchboardTab":
                    switchboard = w
                    break
                w = w.parent()
            if switchboard:
                break

        if chosen == act_copy and switchboard:
            switchboard.copy_tier_contents(self)

        elif chosen == act_paste and switchboard:
            switchboard.paste_tier_contents(self)

        elif chosen == act_delete:
            self.requestDelete.emit(self)

    def max_louvre_grid(self, d: dict) -> tuple[int, int]:
        """
        Returns (max_rows, max_cols) allowed by tier geometry.
        rows refers to BOTTOM rows (top will be rows + 1).
        """
        # Convert mm → scene units
        w = d["draw_width_mm"] / 25.0 * GRID
        h = d["draw_height_mm"] / 25.0 * GRID
        edge = d["edge_margin_mm"] / 25.0 * GRID
        gap = d["louvre_spacing_mm"] / 25.0 * GRID

        tier_w = self._rect.width()
        tier_h = self._rect.height()

        # ---------------- Horizontal ----------------
        usable_w = tier_w - 2 * edge
        max_cols = 0
        while True:
            test = max_cols * w + max(0, max_cols - 1) * gap
            if test > usable_w:
                break
            max_cols += 1
        max_cols = max(1, max_cols - 1)

        # ---------------- Vertical (per face) ----------------
        usable_h = tier_h / 2.0 - edge

        max_rows = 0
        while True:
            # TOP governs (rows + 1)
            rows_top = max_rows + 1
            test = rows_top * h + max(0, rows_top - 1) * gap
            if test > usable_h:
                break
            max_rows += 1
        max_rows = max(1, max_rows - 1)

        return max_rows, max_cols

    def _draw_louvres(self, painter):
        if not self.is_ventilated:
            return

        if not callable(self.get_louvre_definition):
            return

        d = self.get_louvre_definition()
        if not d:
            return

        # Convert mm → item units
        try:
            w = float(d["draw_width_mm"]) / 25.0 * GRID
            h = float(d["draw_height_mm"]) / 25.0 * GRID
            edge = float(d["edge_margin_mm"]) / 25.0 * GRID
            gap = float(d["louvre_spacing_mm"]) / 25.0 * GRID
        except Exception:
            return

        rect = self._rect  # LOCAL coords

        cols = max(1, int(self.vent_cols))
        rows_bottom = max(1, int(self.vent_rows))
        rows_top = rows_bottom + 1  # chimney row

        total_w = cols * w + (cols - 1) * gap
        total_h_bot = rows_bottom * h + (rows_bottom - 1) * gap
        total_h_top = rows_top * h + (rows_top - 1) * gap

        x0 = rect.center().x() - total_w / 2.0
        y_bottom = rect.bottom() - edge - total_h_bot
        y_top = rect.top() + edge

        painter.save()
        painter.setPen(QPen(QColor(190, 190, 190), 1.5))  # light grey
        painter.setBrush(Qt.NoBrush)

        def draw_block(y0, rows):
            for r in range(rows):
                y = y0 + r * (h + gap)
                for c in range(cols):
                    x = x0 + c * (w + gap)
                    painter.drawRect(QRectF(x, y, w, h))

        draw_block(y_bottom, rows_bottom)
        draw_block(y_top, rows_top)

        painter.restore()

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)


        # 2. LOUVRES ON TOP
        if self.is_ventilated:
            painter.save()
            painter.setPen(QPen(Qt.darkGray, 1))
            painter.setBrush(Qt.NoBrush)
            self._draw_louvres(painter)
            painter.restore()

        # --- cooling state (authoritative) ---
        cooling_required = (
                bool(self.live_thermal)
                and self.live_thermal.get("P_cooling", 0.0) > 0.0
        )

        # --- title ---
        title_font = QFont("Segoe UI")  # or Arial
        title_font.setPointSize(18)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QPen(Qt.white))  # or your colour logic

        painter.setPen(
            QPen(QColor("#ff9f1c") if cooling_required else QColor("#2ec4b6"))
        )

        painter.drawText(
            QRectF(
                self._rect.left(),
                self._rect.center().y() - 18,
                self._rect.width(),
                22,
            ),
            Qt.AlignCenter,
            self.name,
        )

        # --- total heat ---
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(
            QRectF(
                self._rect.left(),
                self._rect.center().y() + 2,
                self._rect.width(),
                16,
            ),
            Qt.AlignCenter,
            f"{self.total_heat():.1f} W",
        )

        # --- curve tag ---
        tag = QRectF(self._rect.right() - 40, self._rect.top() + 8, 32, 24)
        painter.setPen(QPen(QColor("#222")))
        painter.setBrush(QBrush(QColor("#ffd166")))
        painter.drawRoundedRect(tag, 4, 4)
        painter.setPen(QPen(QColor("#111")))
        painter.setFont(QFont("", 12))
        painter.drawText(tag, Qt.AlignCenter, str(self.curve_no))

    # ----- Cables API -----
    def add_cable(self, payload: Dict[str, Any]) -> CableEntry:
        """
        payload comes directly from CableAdderWidget.cableAdded (dict with fields of CableEntry).
        """
        ce = CableEntry(**payload)
        self.cables.append(ce)
        self.update()
        return ce

    # ----- Components API -----
    def add_component_entry(
            self,
            key: str,
            category: str,
            part_number: str,
            description: str,
            heat_each_w: float,
            qty: int = 1,
            max_temp_C: int = 70,  # NEW
    ):
        # Merge only when both heat_each and rating match (so mixed ratings remain distinct)
        for ce in self.component_entries:
            if ce.key == key and ce.heat_each_w == float(heat_each_w) and ce.max_temp_C == int(max_temp_C):
                ce.qty += int(qty)
                self.update()
                return
        self.component_entries.append(
            ComponentEntry(
                key=key,
                category=category,
                part_number=part_number or "",
                description=description or key,
                heat_each_w=float(heat_each_w),
                qty=int(qty),
                max_temp_C=int(max_temp_C),
            )
        )
        self.update()

    # ----- Effective limit --------------------------------------------------
    def effective_max_temp_C(self) -> int:
        """Tier limit used by calculations."""
        if self.use_auto_component_temp:
            # If no components yet, fall back to manual value to avoid surprising 0
            temps = [ce.max_temp_C for ce in self.component_entries] or [self.max_temp_C]
            return min(int(t) for t in temps)
        return int(self.max_temp_C)

    def contents_rows(self) -> List[Tuple[str, str, float, object]]:
        """
        Return [(category, description, heat_W, backing), ...]
          - category: "Component" or "Cable"
          - description: display text
          - heat_W: numeric heat (W)
          - backing: ("component", name)  OR  ("cable", CableEntry)
        """
        rows: List[Tuple[str, str, float, object]] = []

        # components (from library)
        from ..core.component_library import DEFAULT_COMPONENTS
        for name, qty in self.components.items():
            heat_each = float(DEFAULT_COMPONENTS.get(name, 0.0))
            desc = f"{name} ×{qty}"
            rows.append(("Component", desc, heat_each * qty, ("component", name)))

        # cables (persisted detailed entries)
        for ce in self.cables:
            desc = (f"{ce.name} — {ce.csa_mm2:.0f}mm², {ce.length_m:.1f} m, "
                    f"{ce.current_A:.1f} A @ {ce.air_temp_C}°C "
                    f"(Pn={ce.Pn_Wpm:.2f} W/m, In={ce.In_A:.1f} A)")
            rows.append(("Cable", desc, float(ce.total_W), ("cable", ce)))

        return rows

    # ----- JSON (de)serialisation -----
    def to_dict(self) -> dict:
        return {
            "name": self.name,

            "vent": {
                "enabled": self.is_ventilated,
                "area_cm2": self.vent_area_cm2,
                "label": self.vent_label,
                "rows": int(getattr(self, "vent_rows", 1)),
                "cols": int(getattr(self, "vent_cols", 1)),
            },

            # Contents
            "component_entries": [asdict(ce) for ce in self.component_entries],
            "cables": [asdict(c) for c in self.cables],

            # Geometry / IEC
            "wall_mounted": self.wall_mounted,
            "curve_no": self.curve_no,
            "depth_mm": int(self.depth_mm),

            # Limits
            "max_temp_C": self.max_temp_C,
            "use_auto_component_temp": self.use_auto_component_temp,

            # Position
            "x": float(self.pos().x()),
            "y": float(self.pos().y()),
            "w": float(self._rect.width()),
            "h": float(self._rect.height()),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TierItem":
        t = cls(
            name=d.get("name", "Tier"),
            x=float(d.get("x", 0.0)),
            y=float(d.get("y", 0.0)),
            w=float(d.get("w", GRID * 8)),
            h=float(d.get("h", GRID * 6)),
            depth_mm=int(d.get("depth_mm", 200)),
        )

        v = d.get("vent", {})

        t.is_ventilated = bool(v.get("enabled", False))
        t.vent_area_cm2 = v.get("area_cm2")
        t.vent_label = v.get("label")

        # NEW: vent grid persistence
        t.vent_rows = int(v.get("rows", 1))
        t.vent_cols = int(v.get("cols", 1))

        # Backward compatibility
        if t.is_ventilated and t.vent_area_cm2 is None:
            t.vent_label = "Unspecified"

        # IEC / geometry
        t.wall_mounted = bool(d.get("wall_mounted", False))
        t.curve_no = int(d.get("curve_no", 1))
        t.max_temp_C = int(d.get("max_temp_C", 70))
        t.use_auto_component_temp = bool(d.get("use_auto_component_temp", False))

        # Components
        t.component_entries = [
            ComponentEntry(
                key=ce.get("key", ""),
                category=ce.get("category", "Component"),
                part_number=ce.get("part_number", ""),
                description=ce.get("description", ce.get("key", "")),
                heat_each_w=float(ce.get("heat_each_w", 0.0)),
                qty=int(ce.get("qty", 1)),
                max_temp_C=int(ce.get("max_temp_C", 70)),
            )
            for ce in d.get("component_entries", [])
        ]

        t.cables = [CableEntry.from_dict(c) for c in d.get("cables", [])]

        t.update()
        return t
