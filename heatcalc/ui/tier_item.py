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

HANDLE = 10  # px
CORNERS = ("tl", "tr", "bl", "br")
# --- Vent presets (IEC area in cm²) ----------------------------------------
STANDARD_VENTS_CM2 = {
    "50×50": 25.0,     # 50 mm × 50 mm = 25 cm²
    "75×75": 56.25,
    "100×100": 100.0,
    "150×150": 225.0,
    "200×200": 400.0,
}



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

    def set_vent_preset(self, label: str):
        if label not in STANDARD_VENTS_CM2:
            raise ValueError(f"Unknown vent preset: {label}")

        self.is_ventilated = True
        self.vent_label = label
        self.vent_area_cm2 = float(STANDARD_VENTS_CM2[label])
        self.update()

    def set_custom_vent_cm2(self, area_cm2: float):
        self.is_ventilated = area_cm2 > 0
        self.vent_label = "Custom"
        self.vent_area_cm2 = max(0.0, float(area_cm2)) or None
        self.update()

    def clear_vent(self):
        self.is_ventilated = False
        self.vent_label = None
        self.vent_area_cm2 = None
        self.update()

    def vent_area_for_iec(self) -> float:
        return float(self.vent_area_cm2 or 0.0)


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

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)

        # --- cooling state (authoritative) ---
        cooling_required = False
        if self.live_thermal:
            cooling_required = self.live_thermal.get("P_cooling", 0.0) > 0.0

        # --- passive vent indicator (if tier has vents) ---
        if self.is_ventilated:
            cx = self._rect.center().x()
            y = self._rect.top() + 22  # further down

            pen = QPen(QColor("#a8dadc"), 2.5)  # thicker stroke
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)

            # ~4× larger vent symbol
            wave_w = 40
            wave_h = 14
            gap = 10

            for i in range(3):
                x0 = cx - wave_w / 2
                y0 = y + i * gap
                painter.drawArc(
                    QRectF(x0, y0, wave_w, wave_h),
                    0 * 16,
                    180 * 16
                )

        # --- title (big, state-coloured) ---
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        painter.setFont(title_font)

        if cooling_required:
            painter.setPen(QPen(QColor("#ff9f1c")))  # orange
        else:
            painter.setPen(QPen(QColor("#2ec4b6")))  # green

        # draw name centered slightly above middle
        name_rect = QRectF(self._rect.left(), self._rect.center().y() - 18,
                           self._rect.width(), 22)
        painter.drawText(name_rect, Qt.AlignCenter, self.name)

        # --- total heat (small) ---
        heat_text = f"{self.total_heat():.1f} W"
        small_font = QFont()
        small_font.setPointSize(10)  # ~half of 18
        small_font.setBold(False)
        painter.setFont(small_font)

        heat_rect = QRectF(self._rect.left(), self._rect.center().y() + 2,
                           self._rect.width(), 16)
        painter.drawText(heat_rect, Qt.AlignCenter, heat_text)

        # --- curve tag (top-right) ---
        tag = QRectF(self._rect.right() - 40, self._rect.top() + 8, 32, 24)
        painter.setPen(QPen(QColor("#222")))
        painter.setBrush(QBrush(QColor("#ffd166")))
        painter.drawRoundedRect(tag, 4, 4)
        painter.setPen(QPen(QColor("#111")))
        painter.setFont(QFont("", 12))
        painter.drawText(tag, Qt.AlignCenter, str(self.curve_no))


        # --- touching faces indicator ---
        inset = 6.0
        pen = QPen(QColor("#FFD166"), 2)
        painter.setPen(pen)

        r = self._rect

        if self.covered_sides.get("left"):
            painter.drawLine(
                QPointF(r.left() + inset, r.top() + inset),
                QPointF(r.left() + inset, r.bottom() - inset),
            )

        if self.covered_sides.get("right"):
            painter.drawLine(
                QPointF(r.right() - inset, r.top() + inset),
                QPointF(r.right() - inset, r.bottom() - inset),
            )

        if self.covered_sides.get("top"):
            painter.drawLine(
                QPointF(r.left() + inset, r.top() + inset),
                QPointF(r.right() - inset, r.top() + inset),
            )

        # --- live IEC 60890 overlay (readable on dark background) ---
        if self.show_live_overlay and self.live_thermal:
            lt = self.live_thermal

            lines = [
                f"Ae: {lt.get('Ae', 0.0):.2f} m²",
                f"ΔT(1.0t): {lt.get('dt_top', 0.0):.1f} K",
            ]

            Pmat = lt.get("P_material", 0.0)
            Pcool = lt.get("P_cooling", 0.0)

            if Pcool > 0.0:
                lines.append(f"Pmat: {Pmat:.1f} W")
                lines.append(f"Pcool: {Pcool:.1f} W")
            else:
                lines.append("Cooling: NOT REQUIRED")

            airflow = lt.get("airflow_m3h")
            if airflow is not None and airflow > 0:
                lines.append(f"Air: {airflow:.0f} m³/h")

            # --- font: fixed-width, crisp ---
            font = QFont("Consolas")  # Windows-safe, monospaced
            font.setPointSize(9)
            painter.setFont(font)

            fm = QFontMetrics(font)
            line_h = fm.height()

            # --- text position ---
            x = self._rect.left() + 6
            y = self._rect.bottom() - (len(lines) * line_h) - 6

            # --- shadow (dark outline for contrast) ---
            painter.setPen(QColor(0, 0, 0, 200))
            for i, line in enumerate(lines):
                painter.drawText(
                    QRectF(x + 1, y + i * line_h + 1, self._rect.width(), line_h),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    line,
                )

            # --- main text (white) ---
            painter.setPen(QColor(255, 255, 255, 235))
            for i, line in enumerate(lines):
                painter.drawText(
                    QRectF(x, y + i * line_h, self._rect.width(), line_h),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    line,
                )

            ctx_lines = [
                f"Curve: {lt.get('curve_no', '—')}",
                f"k={lt.get('k', 0.0):.3f}",
                f"c={lt.get('c', 0.0):.3f}",
                f"x={lt.get('x', 0.0):.3f}",
            ]

            g = lt.get("g")
            if g is not None:
                ctx_lines.append(f"g={g:.3f}")
            f = lt.get("f")
            if f is not None:
                ctx_lines.append(f"f={f:.3f}")

            # d is not always present (wall-mounted cases)
            if lt.get("d") is not None:
                ctx_lines.append(f"d={lt.get('d'):.3f}")

            coeffs = lt.get("coeff_sources", [])
            if coeffs:
                ctx_lines.append("Coeff: " + ", ".join(coeffs))

            profile = lt.get("profile_source")
            if profile:
                ctx_lines.append("Temp Rise: " + profile)

            font = QFont("Consolas")
            font.setPointSize(8)
            painter.setFont(font)

            fm = QFontMetrics(font)
            lh = fm.height()

            # Position: bottom-right, inside tier
            PAD = 4
            BLOCK_W = min(160, self._rect.width() - 2 * PAD)

            x = self._rect.right() - BLOCK_W - PAD

            y = self._rect.bottom() - (len(ctx_lines) * lh) - 6

            # Shadow
            painter.setPen(QColor(0, 0, 0, 200))
            for i, line in enumerate(ctx_lines):
                painter.drawText(
                    QRectF(x + 1, y + i * lh + 1, BLOCK_W, lh),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    line,
                )

            # Text
            painter.setPen(QColor(255, 255, 255, 220))
            for i, line in enumerate(ctx_lines):
                painter.drawText(
                    QRectF(x, y + i * lh, BLOCK_W, lh),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    line,
                )

            vent_rec = lt.get("vent_recommended", False)

            if vent_rec:
                font = QFont("Consolas")
                font.setPointSize(8)
                font.setBold(True)
                painter.setFont(font)

                text = "⚠ VENT RECOMMENDED"

                fm = QFontMetrics(font)
                w = fm.horizontalAdvance(text) + 8
                h = fm.height() + 4

                x = self._rect.left() + 16  # move right
                y = self._rect.top() + 14  # move down

                # Background (red, semi-transparent)
                painter.setBrush(QColor(180, 40, 40, 200))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(QRectF(x, y, w, h), 4, 4)

                # Text
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(
                    QRectF(x + 4, y + 2, w, h),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    text,
                )

            # --- fan indicator (active cooling required) ---
            if cooling_required:
                cx = self._rect.center().x()
                y = self._rect.bottom() - 26  # lift slightly off bottom edge

                pen = QPen(QColor("#ff9f1c"), 2.5)  # orange, matches title
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)

                radius = 20

                # outer circle
                painter.drawEllipse(
                    QPointF(cx, y),
                    radius,
                    radius
                )

                # fan blades (simple 3-blade symbol)
                for angle_deg in (0, 120, 240):
                    painter.save()
                    painter.translate(cx, y)
                    painter.rotate(angle_deg)
                    painter.drawLine(0, 0, radius, 0)
                    painter.restore()

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
            },

            # Contents
            "component_entries": [asdict(ce) for ce in self.component_entries],
            "cables": [asdict(c) for c in self.cables],

            # Geometry / IEC
            "wall_mounted": self.wall_mounted,
            "curve_no": self.curve_no,
            "depth_mm": int(self.depth_mm),

            # Limits
            "max_temp_C": int(self.max_temp_C),
            "use_auto_component_temp": bool(self.use_auto_component_temp),

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
