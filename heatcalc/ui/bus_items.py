from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Literal
import uuid
from PyQt5.QtCore import QPointF, Qt, QRect, QRectF
from PyQt5.QtGui import QColor, QPen, QBrush, QPainterPath, QPainterPathStroker
from PyQt5.QtWidgets import (
    QGraphicsLineItem,
    QGraphicsEllipseItem,
    QGraphicsSimpleTextItem,
    QGraphicsItem,
    QGraphicsDropShadowEffect, QDoubleSpinBox, QComboBox,
)

from heatcalc.core.models import BusbarJointSpec
from heatcalc.ui.color_utils import temperature_to_color
from heatcalc.ui.geometry import GRID

# --------- QGraphicsItem "type" ids ----------
BUS_LINE_TYPE = 10001
BUS_LOAD_TYPE = 10002
BUS_JOIN_TYPE = 10003
BUS_SOURCE_TYPE = 10004

def get_tier_constraints(item: QGraphicsItem) -> tuple[QRectF, float, QGraphicsItem] | None:
    """Helper to find the parent tier and its bounding constraints."""
    # Bus items are children of bus_layer (QGraphicsRectItem), which is a child of TierItem.
    bus_layer = item.parentItem()
    if not bus_layer:
        return None
        
    # Some items like loads/joins might be children of BusLineItem
    if bus_layer.type() == BUS_LINE_TYPE:
        bus_line = bus_layer
        bus_layer = bus_line.parentItem()
        if not bus_layer:
            return None

    from heatcalc.ui.tier_item import TierItem
    tier = bus_layer.parentItem()
    if isinstance(tier, TierItem):
        return tier.boundingRect(), GRID, tier
    return None

def format_thermal_tooltip(results, point_temp=None) -> str:
    """
    HTML tooltip showing all solver segments belonging to a bus.

    Accepts either:
      - ThermalEdgeResult-like objects with attributes
      - dicts with equivalent keys
    """

    if not isinstance(results, (list, tuple)):
        results = [results]

    if not results:
        return ""

    def _get(res, key, default=None):
        if isinstance(res, dict):
            return res.get(key, default)
        return getattr(res, key, default)

    first = results[0]

    orient = _get(first, "orientation_to_wall", "width")
    orient_txt = "Wide face" if orient == "width" else "Edge face"

    gap = _get(first, "gap_to_wall_mm", None)
    gap_txt = f"{float(gap):.0f} mm" if gap is not None else "-"

    geom_row = f"""
    <tr>
        <td style='color:#8b949e;'>Orientation:</td>
        <td>{orient_txt}</td>
    </tr>
    <tr>
        <td style='color:#8b949e;'>Gap to wall:</td>
        <td>{gap_txt}</td>
    </tr>
    """

    point_row = ""
    if point_temp is not None:
        point_row = f"""
        <tr>
            <td style='color:#8b949e;'>Point temp:</td>
            <td style='color:#ffa657; font-weight:bold;'>{point_temp:.1f} °C</td>
        </tr>
        """
    # Prefer true segment max if available
    segment_max_T = None
    hot_s = None

    # Try get UI item from first result (works because you injected it)
    ui_item = results[0].get("ui_item") if isinstance(results[0], dict) else getattr(results[0], "ui_item", None)

    if ui_item and hasattr(ui_item, "_thermal_segments"):
        segs = ui_item._thermal_segments
        if segs:
            hot_s, hot_T = max(segs, key=lambda x: x[1])
            segment_max_T = hot_T

    if segment_max_T is not None:
        max_T = segment_max_T
    else:
        max_T = max(float(_get(r, "T_C", 0.0)) for r in results)
    max_I = max(float(_get(r, "I_A", 0.0)) for r in results)
    total_P = sum(float(_get(r, "P_gen_W", 0.0)) for r in results)

    hotspot_row = ""

    if hot_s is not None:
        hotspot_row = f"""
        <tr>
            <td style='color:#8b949e;'>Hotspot:</td>
            <td style='color:#ff7b72; font-weight:bold;'>
                {hot_T:.1f} °C @ {hot_s * 100:.0f}%
            </td>
        </tr>
        """

    html = f"""
    <div style='font-family: sans-serif; min-width: 220px;'>

        <b style='color:#58a6ff;'>Bus Result</b><br/>

        <table style='border-spacing:4px; margin-top:4px;'>
        {point_row}
        {geom_row}
        {hotspot_row}
        <tr>
            <td style='color:#8b949e;'>Max current:</td>
            <td style='font-weight:bold;'>{max_I:.1f} A</td>
        </tr>
        <tr>
            <td style='color:#8b949e;'>Max temp:</td>
            <td style='color:#d19a66; font-weight:bold;'>{max_T:.1f} °C</td>
        </tr>
        <tr>
            <td style='color:#8b949e;'>Bus loss:</td>
            <td style='color:#ff7b72; font-weight:bold;'>{total_P:.1f} W</td>
        </tr>
        </table>

        <hr style='border:none; border-top:1px solid #30363d; margin:4px 0;'/>

        <b style='color:#8b949e;'>Segments</b>

        <table style='border-collapse:separate; border-spacing:20px 10px;'>
        <tr>
            <th style='padding-right:24px; text-align:left;'>Seg</th>
            <th style='padding:0 24px; text-align:right;'>I</th>
            <th style='padding:0 24px; text-align:right;'>T</th>
            <th style='padding-left:24px; text-align:right;'>ΔT</th>
        </tr>
    """

    for i, res in enumerate(results):
        temp = float(_get(res, "T_C", 0.0))
        current = float(_get(res, "I_A", 0.0))
        ambient = float(_get(res, "ambient_C", 0.0))
        dT = temp - ambient

        html += f"""
        <tr>
            <td style='padding-right:24px;'>{i}</td>
            <td align='right' style='padding-left:24px; padding-right:24px;'>{current:.1f} A</td>
            <td align='right' style='padding-left:24px; padding-right:24px;'>{temp:.1f} °C</td>
            <td align='right' style='padding-left:24px;'>{dT:.1f} K</td>
        </tr>
        """

    html += "</table></div>"
    return html


def format_joint_tooltip(result) -> str:
    """
    Joint-specific tooltip in the same visual style as bus results.
    Expects a ThermalEdgeResult-like dict with at least keys: T_C, I_A, P_gen_W,
    gap_to_wall_mm, orientation_to_wall, ambient_C.
    """
    if result is None:
        return ""

    def _get(res, key, default=None):
        if isinstance(res, dict):
            return res.get(key, default)
        return getattr(res, key, default)

    orient = _get(result, "orientation_to_wall", "width")
    orient_txt = "Wide face" if orient == "width" else "Edge face"

    gap = _get(result, "gap_to_wall_mm", None)
    gap_txt = f"{float(gap):.0f} mm" if gap is not None else "-"

    T = float(_get(result, "T_C", 0.0))
    I = float(_get(result, "I_A", 0.0))
    P = float(_get(result, "P_gen_W", 0.0))
    Ta = float(_get(result, "ambient_C", 0.0))
    dT = T - Ta
    joint_number = _get(result, "joint_number", None)
    if joint_number not in (None, ""):
        joint_ref = f"Joint {joint_number}"
    else:
        jid = _get(result, "joint_id", "")
        joint_ref = str(jid)[:8] if jid else "-"

    html = f"""
    <div style='font-family: sans-serif; min-width: 220px;'>

        <b style='color:#58a6ff;'>Joint Result ({joint_ref})</b><br/>

        <table style='border-spacing:4px; margin-top:4px;'>
        <tr>
            <td style='color:#8b949e;'>Orientation:</td>
            <td>{orient_txt}</td>
        </tr>
        <tr>
            <td style='color:#8b949e;'>Gap to wall:</td>
            <td>{gap_txt}</td>
        </tr>
        <tr>
            <td style='color:#8b949e;'>Temp:</td>
            <td style='color:#d19a66; font-weight:bold;'>{T:.1f} °C</td>
        </tr>
        <tr>
            <td style='color:#8b949e;'>ΔT vs air:</td>
            <td style='font-weight:bold;'>{dT:.1f} K</td>
        </tr>
        <tr>
            <td style='color:#8b949e;'>Current:</td>
            <td style='font-weight:bold;'>{I:.1f} A</td>
        </tr>
        <tr>
            <td style='color:#8b949e;'>Joint loss:</td>
            <td style='color:#ff7b72; font-weight:bold;'>{P:.1f} W</td>
        </tr>
        </table>
    </div>
    """
    return html

@dataclass
class BusSpecUI:
    width_mm: float = 100.0
    thickness_mm: float = 10.0
    bars_in_parallel: int = 1
    phases: int = 3
    face_to_face_dim: Literal["width", "thickness"] = "thickness"
    gap_to_wall_mm: float = 50.0
    orientation_to_wall: Literal["width", "thickness"] = "width"

# =========================================================
# BUS LINE
# =========================================================

class BusLineItem(QGraphicsLineItem):

    def __init__(self, p0: QPointF, p1: QPointF, spec: Optional[BusSpecUI] = None):
        super().__init__(p0.x(), p0.y(), p1.x(), p1.y())

        self.spec = spec or BusSpecUI()

        # ---- canonical endpoints (local coordinates) ----
        self._p0_local = QPointF(p0)
        self._p1_local = QPointF(p1)

        # ---- attachment storage ----
        self.endpoint_a_items = []   # loads / sources
        self.endpoint_b_items = []   # loads / sources
        self.segment_items = []      # joins

        # ---- appearance ----
        self._base_pen = QPen(QColor("#d19a66"), 4)
        self.setPen(self._base_pen)
        self.temperature_C = None
        self.thermal_results = []

        self.setZValue(0)

        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(20)
        glow.setOffset(0)

        self.setGraphicsEffect(glow)
        self._glow = glow

        self.bus_id = uuid.uuid4().hex
        self.disconnected = False

        self._temperature_segments = []

        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self._hover = False
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def type(self):
        return BUS_LINE_TYPE

    from PyQt5.QtGui import QPainterPath, QPainterPathStroker

    def shape(self):
        path = QPainterPath()
        path.moveTo(self.line().p1())
        path.lineTo(self.line().p2())

        stroker = QPainterPathStroker()
        stroker.setWidth(8)  # hover hit width (pixels)
        return stroker.createStroke(path)


    def hoverEnterEvent(self, event):
        self._hover = True

        if getattr(self, "thermal_results", None):
            self.setToolTip(format_thermal_tooltip(self.thermal_results))

        else:
            self.setToolTip("")

        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event):
        scene = self.scene()

        p = event.scenePos()

        T = self._temperature_at_point_from_segments(p)

        if getattr(self, "thermal_results", None):
            html = format_thermal_tooltip(self.thermal_results, point_temp=T)
            self.setToolTip(html)

    def _temperature_at_point_from_segments(self, p_scene):
        if not hasattr(self, "_thermal_segments") or not self._thermal_segments:
            return None

        line_geom = self.line()
        p0 = self.mapToScene(line_geom.p1())
        p1 = self.mapToScene(line_geom.p2())

        dx = p1.x() - p0.x()
        dy = p1.y() - p0.y()
        L2 = dx * dx + dy * dy

        if L2 < 1e-12:
            return self._thermal_segments[0][1]

        # projection
        t = ((p_scene.x() - p0.x()) * dx +
             (p_scene.y() - p0.y()) * dy) / L2

        t = max(0.0, min(1.0, t))

        segs = self._thermal_segments

        # ---- find enclosing segment ----
        for i in range(len(segs) - 1):
            s0, T0 = segs[i]
            s1, T1 = segs[i + 1]

            if s0 <= t <= s1:
                # linear interp BETWEEN SEGMENTS (not nodes)
                if abs(s1 - s0) < 1e-9:
                    return T0
                alpha = (t - s0) / (s1 - s0)
                return (1 - alpha) * T0 + alpha * T1

        # outside bounds → clamp
        if t <= segs[0][0]:
            return segs[0][1]

        return segs[-1][1]

    def contextMenuEvent(self, event):
        from PyQt5.QtWidgets import QMenu, QDialog, QFormLayout, QSpinBox, QDialogButtonBox

        menu = QMenu()
        act_edit = menu.addAction("Edit bus")

        # Map position to screen for menu.exec_
        if hasattr(event, "screenPos"):
            screen_pos = event.screenPos()
        else:
            # Fallback if the event is being passed from somewhere else or is not a QContextMenuEvent
            from PyQt5.QtGui import QCursor
            screen_pos = QCursor.pos()

        action = menu.exec_(screen_pos)

        if action != act_edit:
            return

        # -----------------------------
        # Dialog
        # -----------------------------
        dlg = QDialog()
        dlg.setWindowTitle("Edit Bus")

        layout = QFormLayout(dlg)

        sp_w = QSpinBox()
        sp_w.setRange(10, 500)
        sp_w.setValue(self.spec.width_mm)

        sp_t = QSpinBox()
        sp_t.setRange(2, 50)
        sp_t.setValue(self.spec.thickness_mm)

        sp_n = QSpinBox()
        sp_n.setRange(1, 10)
        sp_n.setValue(self.spec.bars_in_parallel)

        sp_gap = QDoubleSpinBox()
        sp_gap.setRange(0.0, 500.0)
        sp_gap.setDecimals(1)
        sp_gap.setValue(getattr(self.spec, "gap_to_wall_mm", 50.0))

        cb_orient = QComboBox()
        cb_orient.addItems(["Wide face to wall", "Edge face to wall"])

        cb_face = QComboBox()
        # Type 2 is thickness to thickness, Type 1 is width to width
        cb_face.addItems(["Type 2", "Type 1"])

        current_orient = getattr(self.spec, "orientation_to_wall", "width")
        cb_orient.setCurrentIndex(0 if current_orient == "width" else 1)

        current_face = getattr(self.spec, "face_to_face_dim", "thickness")
        cb_face.setCurrentIndex(0 if current_face == "thickness" else 1)

        layout.addRow("Width (mm)", sp_w)
        layout.addRow("Thickness (mm)", sp_t)
        layout.addRow("Bars", sp_n)
        layout.addRow("Gap to wall (mm)", sp_gap)
        layout.addRow("Orientation", cb_orient)
        layout.addRow("Bus installation type", cb_face)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addRow(btns)

        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        if dlg.exec_():
            from copy import deepcopy
            self.spec = deepcopy(self.spec)

            self.spec.width_mm = sp_w.value()
            self.spec.thickness_mm = sp_t.value()
            self.spec.bars_in_parallel = sp_n.value()
            self.spec.gap_to_wall_mm = sp_gap.value()
            self.spec.orientation_to_wall = (
                "width" if cb_orient.currentIndex() == 0 else "thickness"
            )
            self.spec.face_to_face_dim = (
                "thickness" if cb_face.currentIndex() == 0 else "width"
            )

            self.update()

            if self.scene():
                self.scene().update()
    # ---------------------------------------------------------
    # Attach children
    # ---------------------------------------------------------

    def attach_child(self, item, scene_pos: QPointF):

        # convert scene coordinate into bus local coordinate
        local_pos = self.mapFromScene(scene_pos)

        item.setParentItem(self)
        item.setPos(local_pos)
        item.setZValue(10000)

        # determine attachment type
        if isinstance(item, BusJoinItem):
            self.segment_items.append(item)

        elif isinstance(item, (BusLoadItem, BusSourceItem)):

            # decide which endpoint is closest
            p0 = self.mapToScene(self._p0_local)
            p1 = self.mapToScene(self._p1_local)

            d0 = math.hypot(scene_pos.x() - p0.x(), scene_pos.y() - p0.y())
            d1 = math.hypot(scene_pos.x() - p1.x(), scene_pos.y() - p1.y())

            if d0 < d1:
                self.endpoint_a_items.append(item)
            else:
                self.endpoint_b_items.append(item)

    def delete_child(self, item):
        """Removes a child item from internal tracking lists."""
        if item in self.endpoint_a_items:
            self.endpoint_a_items.remove(item)
        elif item in self.endpoint_b_items:
            self.endpoint_b_items.remove(item)
        elif item in self.segment_items:
            self.segment_items.remove(item)

    # ---------------------------------------------------------
    # Endpoint helpers
    # ---------------------------------------------------------

    def scene_endpoint_a(self) -> QPointF:
        line = self.line()
        return self.mapToScene(line.p1())

    def scene_endpoint_b(self) -> QPointF:
        line = self.line()
        return self.mapToScene(line.p2())

    def scene_endpoints(self):
        return self.scene_endpoint_a(), self.scene_endpoint_b()

    # ---------------------------------------------------------
    # Attachment helpers
    # ---------------------------------------------------------

    def loads(self):
        return [i for i in self.endpoint_a_items + self.endpoint_b_items if isinstance(i, BusLoadItem)]

    def sources(self):
        return [i for i in self.endpoint_a_items + self.endpoint_b_items if isinstance(i, BusSourceItem)]

    def joins(self):
        return list(self.segment_items)

    # ---------------------------------------------------------
    # Boundary detection
    # ---------------------------------------------------------

    def boundary_ports(self, tier_rect_scene, tol_px=2.0):

        ports = []

        p0, p1 = self.scene_endpoints()

        for name, p in [("a", p0), ("b", p1)]:

            if abs(p.x() - tier_rect_scene.left()) < tol_px:
                ports.append(("left", p, self, name))

            elif abs(p.x() - tier_rect_scene.right()) < tol_px:
                ports.append(("right", p, self, name))

            elif abs(p.y() - tier_rect_scene.top()) < tol_px:
                ports.append(("top", p, self, name))

            elif abs(p.y() - tier_rect_scene.bottom()) < tol_px:
                ports.append(("bottom", p, self, name))

        return ports
    # ---------------------------------------------------------
    # Temperature overlay
    # ---------------------------------------------------------

    def set_temperature_segments(self, segments):
        self._temperature_segments = segments
        self.update()

    def clear_temperature_overlay(self):
        self._temperature_segments = []
        self.update()

    def set_disconnected(self, disconnected: bool):
        self.disconnected = disconnected
        if disconnected:
            self.update_glow(Qt.transparent)
            self.clear_temperature_overlay()
            self.temperature_C = None
        self.update()

    # ---------------------------------------------------------
    # Drawing
    # ---------------------------------------------------------

    def paint(self, painter, option, widget=None):
        line = self.line()

        # -----------------------------------------
        # Bus label (size / bars)
        # -----------------------------------------
        try:
            spec = self.spec  # BusSpecUI

            if spec:
                text = f"{spec.width_mm}x{spec.thickness_mm}x{spec.bars_in_parallel}"

                # line geometry
                p1 = line.p1()
                p2 = line.p2()

                dx = p2.x() - p1.x()
                dy = p2.y() - p1.y()
                angle = math.degrees(math.atan2(dy, dx))

                mid = (p1 + p2) * 0.5

                painter.save()

                # move + rotate into line frame
                painter.translate(mid)
                painter.rotate(angle)

                # subtle styling
                painter.setPen(QColor(150, 150, 150, 180))
                font = painter.font()
                font.setPointSize(7)
                painter.setFont(font)

                # offset slightly off the line
                painter.drawText(QPointF(0, -6), text)

                painter.restore()

        except Exception:
            pass
        # ---------------------------------------------------------
        # Draw base line (thermal gradient or plain bus)
        # ---------------------------------------------------------

        # Selection highlighting if parent tier is active
        is_active = False
        parent = self.parentItem()
        if parent: # BusLineItem's parent is likely a QGraphicsRectItem (bus_layer)
            tier = parent.parentItem()
            if hasattr(tier, "_active") and tier._active:
                is_active = True

        if not self._temperature_segments:

            pen = QPen(self._base_pen)

            if self.disconnected:
                pen.setColor(QColor("#f85149"))
                pen.setWidth(self._base_pen.width() + 2)
            elif self._hover:
                pen.setColor(QColor("#ffd33d"))
                pen.setWidth(self._base_pen.width() + 2)
            elif is_active:
                pen.setColor(QColor("#00ffea"))
                pen.setWidth(self._base_pen.width() + 1)

            painter.setPen(pen)
            painter.drawLine(line)
            return

        # ---- draw temperature gradient ----

        from PyQt5.QtGui import QLinearGradient

        grad = QLinearGradient(line.x1(), line.y1(), line.x2(), line.y2())

        # Build a continuous gradient using segment boundaries

        for s, T in self._temperature_segments:
            color = temperature_to_color(T, self._Tmin, self._Tmax)

            # clamp just in case (Qt requires 0–1)
            s = max(0.0, min(1.0, s))

            grad.setColorAt(s, color)

        painter.setPen(QPen(grad, self._base_pen.width()))
        painter.drawLine(line)

        # ---------------------------------------------------------
        # Hover overlay (draw AFTER gradient)
        # ---------------------------------------------------------

        if self._hover:
            hover_pen = QPen(QColor("#ffd33d"), self._base_pen.width() + 3)
            hover_pen.setCapStyle(Qt.RoundCap)

            painter.setPen(hover_pen)
            painter.drawLine(line)
        elif is_active:
            active_pen = QPen(QColor("#00ffea"), self._base_pen.width() + 1)
            active_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(active_pen)
            painter.drawLine(line)
    # ---------------------------------------------------------

    def update_glow(self, color):
        if color == Qt.transparent:
            self._glow.setEnabled(False)
            return

        self._glow.setEnabled(True)
        glow_color = QColor(color)
        glow_color.setAlpha(150)
        self._glow.setColor(glow_color)

    # ---------------------------------------------------------

    def itemChange(self, change, value):

        scene = self.scene()

        if scene is None:
            return super().itemChange(change, value)

        if change == QGraphicsItem.ItemPositionChange:
            from .designer_view import snap

            # Proposed new position in parent (bus_layer) coordinates
            new_pos = QPointF(
                snap(value.x()),
                snap(value.y())
            )

            # --- Tier boundary constraint ---
            constraints = get_tier_constraints(self)
            if constraints:
                tr, margin, tier = constraints
                
                # My endpoints in TIER coordinates at the proposed new position
                p1_local = self.line().p1()
                p2_local = self.line().p2()
                
                # Points in bus_layer (which is same as Tier local since bus_layer at 0,0)
                p1_tier = new_pos + p1_local
                p2_tier = new_pos + p2_local
                
                # Bounds of the line itself in Tier local
                min_x = min(p1_tier.x(), p2_tier.x())
                max_x = max(p1_tier.x(), p2_tier.x())
                min_y = min(p1_tier.y(), p2_tier.y())
                max_y = max(p1_tier.y(), p2_tier.y())
                
                # Valid range for line endpoints (can touch edges)
                v_min_x = tr.left()
                v_max_x = tr.right()
                v_min_y = tr.top()
                v_max_y = tr.bottom()
                
                # Valid range for children (must be within margin)
                c_min_x = tr.left() + margin
                c_max_x = tr.right() - margin
                c_min_y = tr.top() + margin
                c_max_y = tr.bottom() - margin

                # Clamp new_pos if it would push us outside
                dx = 0.0
                if min_x < v_min_x:
                    dx = v_min_x - min_x
                elif max_x > v_max_x:
                    dx = v_max_x - max_x
                    
                dy = 0.0
                if min_y < v_min_y:
                    dy = v_min_y - min_y
                elif max_y > v_max_y:
                    dy = v_max_y - max_y
                    
                new_pos += QPointF(dx, dy)

                # Now check all children (loads, sources, joints)
                # They are already children of 'self', so their pos() is relative to 'self'.
                # We need to ensure new_pos + child.pos() is within [c_min, c_max].
                for child in self.childItems():
                    if child.type() in (BUS_LOAD_TYPE, BUS_SOURCE_TYPE, BUS_JOIN_TYPE):
                        cp_tier = new_pos + child.pos()
                        
                        cdx = 0.0
                        if cp_tier.x() < c_min_x:
                            cdx = c_min_x - cp_tier.x()
                        elif cp_tier.x() > c_max_x:
                            cdx = c_max_x - cp_tier.x()
                            
                        cdy = 0.0
                        if cp_tier.y() < c_min_y:
                            cdy = c_min_y - cp_tier.y()
                        elif cp_tier.y() > c_max_y:
                            cdy = c_max_y - cp_tier.y()
                            
                        new_pos += QPointF(cdx, cdy)
                
                # Final snap again after clamping to be sure
                new_pos = QPointF(snap(new_pos.x()), snap(new_pos.y()))
            
            return new_pos

        if change == QGraphicsItem.ItemPositionHasChanged:


            scene = self.scene()

            if scene:
                views = scene.views()

                if views:
                    views[0].update_bus_connections()

        return super().itemChange(change, value)

    def to_dict(self):

        line = self.line()
        tier = None
        parent = self.parentItem()

        if parent and parent.parentItem():
            tier = parent.parentItem()

        tier_id = getattr(tier, "tier_id", None)
        children = []

        for child in self.childItems():

            if hasattr(child, "to_dict"):
                children.append(child.to_dict())

        return {
            "type": "line",
            "id": self.bus_id,
            "tier_id": tier_id,

            "x": self.x(),
            "y": self.y(),

            "x1": line.x1(),
            "y1": line.y1(),
            "x2": line.x2(),
            "y2": line.y2(),

            "spec": {
                "width_mm": self.spec.width_mm,
                "thickness_mm": self.spec.thickness_mm,
                "bars_in_parallel": self.spec.bars_in_parallel,
                "face_to_face_dim": self.spec.face_to_face_dim,
                "phases": self.spec.phases,
            },

            "children": children,
        }

    @classmethod
    def from_dict(cls, d):

        spec = BusSpecUI(**d["spec"])

        bus = cls(
            QPointF(d["x1"], d["y1"]),
            QPointF(d["x2"], d["y2"]),
            spec,
        )

        bus.setPos(d.get("x", 0.0), d.get("y", 0.0))
        bus.bus_id = d.get("id")
        # Restore tier_id from dict if present
        if "tier_id" in d:
            bus.tier_id = d["tier_id"]

        # ---- restore children ----
        for child in d.get("children", []):

            t = child["type"]

            if t == "load":
                item = BusLoadItem.from_dict(child)

            elif t == "join":
                item = BusJoinItem.from_dict(child)

            elif t == "source":
                # Ensure only one source exists globally by removing any existing one
                # when a new one is being restored from dict.
                # Since BusLineItem.from_dict is called during loading,
                # we assume the saved state should have at most one, but we enforce it.
                scene = bus.scene()
                if scene:
                    for existing in scene.items():
                        if isinstance(existing, BusSourceItem):
                            p = existing.parentItem()
                            if isinstance(p, BusLineItem):
                                p.delete_child(existing)
                            scene.removeItem(existing)
                item = BusSourceItem.from_dict(child)

            else:
                continue

            item.setParentItem(bus)
            item.setPos(child["x"], child["y"])

        return bus

# =========================================================
# LOAD
# =========================================================

class BusLoadItem(QGraphicsEllipseItem):

    def __init__(self, center: QPointF, I_load_A: float = 0, max_terminal_temp_c: float = 105.0):

        r = 7
        super().__init__(-r, -r, 2*r, 2*r)   # <-- FIX

        self.setBrush(QBrush(QColor("#3fb950")))
        self.setPen(QPen(QColor("#1f6f3a"), 2))

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setZValue(100)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self._hover = False

        self.I_load_A = float(I_load_A)
        self.max_terminal_temp_c = float(max_terminal_temp_c)
        self.disconnected = False

        self._label = QGraphicsSimpleTextItem(f"{self.I_load_A:.0f}A", self)
        self._label.setBrush(QBrush(QColor("#d0d7de")))
        self._label.setPos(10, -10)

        self.node_id: int | None = None
        self._node_label = QGraphicsSimpleTextItem("", self)
        self._node_label.setBrush(QBrush(QColor("#58a6ff")))
        self._node_label.setPos(10, 5)

    def set_node_id(self, nid: int | None):
        self.node_id = nid
        if nid is not None:
            self._node_label.setText(f"Node: {nid}")
        else:
            self._node_label.setText("")

    def set_disconnected(self, disconnected: bool):
        self.disconnected = disconnected
        if disconnected:
            self.setBrush(QBrush(QColor("#f85149")))
            self.setPen(QPen(QColor("#ffffff"), 2))
        else:
            self.setBrush(QBrush(QColor("#3fb950")))
            self.setPen(QPen(QColor("#1f6f3a"), 2))
        self.update()

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def contextMenuEvent(self, event):
        from PyQt5.QtWidgets import QMenu, QInputDialog, QFormLayout, QDialog, QDialogButtonBox, QVBoxLayout, QDoubleSpinBox

        menu = QMenu()

        act_edit = menu.addAction("Edit load...")

        if hasattr(event, "screenPos"):
            screen_pos = event.screenPos()
        else:
            from PyQt5.QtGui import QCursor
            screen_pos = QCursor.pos()

        action = menu.exec_(screen_pos)

        if action == act_edit:
            dialog = QDialog()
            dialog.setWindowTitle("Edit Load")
            layout = QVBoxLayout(dialog)
            form = QFormLayout()

            spin_i = QDoubleSpinBox()
            spin_i.setRange(0, 10000)
            spin_i.setDecimals(2)
            spin_i.setValue(self.I_load_A)
            spin_i.setSuffix(" A")
            form.addRow("Load current:", spin_i)

            spin_t = QDoubleSpinBox()
            spin_t.setRange(0, 500)
            spin_t.setDecimals(1)
            spin_t.setValue(self.max_terminal_temp_c)
            spin_t.setSuffix(" °C")
            form.addRow("Max terminal temp:", spin_t)

            layout.addLayout(form)

            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)

            if dialog.exec_() == QDialog.Accepted:
                self.I_load_A = spin_i.value()
                self.max_terminal_temp_c = spin_t.value()
                self._label.setText(f"{self.I_load_A:.0f}A")

                # trigger re-solve / refresh
                if self.scene():
                    self.scene().update()

    def paint(self, painter, option, widget=None):
        # Base circle
        super().paint(painter, option, widget)

        # Selection highlighting if parent tier is active
        is_active = False
        parent = self.parentItem()
        if parent: # BusLineItem's parent is likely a QGraphicsRectItem (bus_layer)
            tier = parent.parentItem()
            if hasattr(tier, "_active") and tier._active:
                is_active = True

        # Hover highlight
        if self._hover:
            hover_pen = QPen(QColor("#ffd33d"), 3)
            painter.setPen(hover_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self.rect())
        elif is_active:
            active_pen = QPen(QColor("#00ffea"), 2)
            painter.setPen(active_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self.rect())

    def type(self):
        return BUS_LOAD_TYPE

    def center(self):
        return self.mapToScene(QPointF(0, 0))

    def itemChange(self, change, value):
        scene = self.scene()

        if scene is None:
            return super().itemChange(change, value)

        if change == QGraphicsItem.ItemPositionChange:

            parent = self.parentItem()
            if parent is None:
                return value

            line = parent.line()

            # convert endpoints to SCENE coordinates
            p1 = parent.mapToScene(line.p1())
            p2 = parent.mapToScene(line.p2())

            # value is in parent coordinates → convert to scene
            scene_value = parent.mapToScene(value)

            d1 = math.hypot(scene_value.x() - p1.x(), scene_value.y() - p1.y())
            d2 = math.hypot(scene_value.x() - p2.x(), scene_value.y() - p2.y())

            snap_scene = p1 if d1 < d2 else p2
            dist = min(d1, d2)

            if dist > 20:
                return self.pos()

            # --- Edge constraint: must be >25mm (GRID) from TierItem edges ---
            constraints = get_tier_constraints(self)
            if constraints:
                tr, margin, tier = constraints
                
                # Target point in Tier local coordinates
                snap_tier = tier.mapFromScene(snap_scene)
                
                # If snapping to this endpoint would place it on the edge, refuse.
                if (snap_tier.x() < tr.left() + margin - 0.1 or 
                    snap_tier.x() > tr.right() - margin + 0.1 or
                    snap_tier.y() < tr.top() + margin - 0.1 or 
                    snap_tier.y() > tr.bottom() - margin + 0.1):
                    return self.pos()

            # Check if another attachment exists at this endpoint
            # We must ignore self during the check
            for item in self.scene().items():
                if item is self:
                    continue
                if item.type() in (BUS_LOAD_TYPE, BUS_SOURCE_TYPE, BUS_JOIN_TYPE):
                    # We check if it's near the target snap_scene point
                    c = item.mapToScene(QPointF(0, 0))
                    if math.hypot(c.x() - snap_scene.x(), c.y() - snap_scene.y()) < 8:
                        return self.pos()

            # Update parent tracking if endpoint changed
            if self in parent.endpoint_a_items and snap_scene == p2:
                parent.endpoint_a_items.remove(self)
                parent.endpoint_b_items.append(self)
            elif self in parent.endpoint_b_items and snap_scene == p1:
                parent.endpoint_b_items.remove(self)
                parent.endpoint_a_items.append(self)

            # convert snap back into parent coordinates
            return parent.mapFromScene(snap_scene)

        return super().itemChange(change, value)

    def to_dict(self):
        p = self.pos()
        return {
            "type": "load",
            "x": p.x(),
            "y": p.y(),
            "I_load_A": self.I_load_A,
            "max_terminal_temp_c": self.max_terminal_temp_c,
        }

    @classmethod
    def from_dict(cls, d):
        p = QPointF(d["x"], d["y"])
        return cls(p, d.get("I_load_A", 0.0), d.get("max_terminal_temp_c", 105.0))

# =========================================================
# JOIN
# =========================================================

class BusJoinItem(QGraphicsEllipseItem):

    def __init__(self, center: QPointF, spec: BusbarJointSpec | None = None, join_id: str | None = None):
        r = 6
        super().__init__(-r, -r, 2 * r, 2 * r)

        self.setBrush(QBrush(QColor("#f85149")))
        self.setPen(QPen(QColor("#a40e26"), 2))

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setZValue(100)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self._hover = False

        # use the passed spec directly
        self.spec = spec or BusbarJointSpec()

        self.join_id = join_id or self.spec.joint_id or str(uuid.uuid4())[:8]
        self.spec.joint_id = self.join_id

        # mirror spec fields onto UI-facing attributes
        self.overlap_m = float(self.spec.overlap_m)
        self.bolt_count = int(self.spec.bolt_count)
        self.bolt_dia_mm = float(self.spec.bolt_dia_mm)
        self.torque_Nm = float(self.spec.torque_Nm)
        self.disconnected = False
        self.joint_type = str(self.spec.joint_type)
        self.nut_factor = float(self.spec.nut_factor)
        self.e_streamline = float(self.spec.e_streamline)
        self.csa_factor = float(self.spec.csa_factor)
        self.h_contact = float(self.spec.h_contact)
        self.joint_number: int | None = None

        self._label = QGraphicsSimpleTextItem(self._label_text(), self)
        self._label.setBrush(QBrush(QColor("#d0d7de")))
        self._label.setPos(10, -10)

        self.setPos(center)
        self.temperature_C: float | None = None
        self.thermal_result: dict | None = None

    def set_disconnected(self, disconnected: bool):
        self.disconnected = disconnected
        if disconnected:
            self.setBrush(QBrush(QColor("#f85149")))
            self.setPen(QPen(QColor("#ffffff"), 2))
        else:
            self.setBrush(QBrush(QColor("#f85149")))
            self.setPen(QPen(QColor("#a40e26"), 2))
        self.update()

    def _label_text(self) -> str:
        heading = (
            f"Joint {self.joint_number}"
            if self.joint_number is not None
            else f"ID: {self.join_id[:8]}"
        )
        return (
            f"{heading}\n"
            f"{self.joint_type}\n"
            f"{self.bolt_count}x M{self.bolt_dia_mm:.0f}\n"
            f"{self.torque_Nm:.1f} Nm"
        )

    def set_joint_number(self, joint_number: int | None):
        self.joint_number = None if joint_number is None else int(joint_number)
        self._label.setText(self._label_text())

    def refresh_spec(self):
        self.spec.x_m = 0.0
        self.spec.overlap_m = self.overlap_m
        self.spec.bolt_count = self.bolt_count
        self.spec.bolt_dia_mm = self.bolt_dia_mm
        self.spec.torque_Nm = self.torque_Nm
        self.spec.joint_type = self.joint_type
        self.spec.nut_factor = self.nut_factor
        self.spec.e_streamline = self.e_streamline
        self.spec.csa_factor = self.csa_factor
        self.spec.h_contact = self.h_contact
        self._label.setText(self._label_text())

    def hoverEnterEvent(self, event):
        self._hover = True

        if self.thermal_result:
            self.setToolTip(format_joint_tooltip(self.thermal_result))
        else:
            self.setToolTip("")

        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        from PyQt5.QtWidgets import QToolTip
        QToolTip.hideText()
        super().hoverLeaveEvent(event)

    def paint(self, painter, option, widget=None):
        # Base circle
        super().paint(painter, option, widget)

        # Selection highlighting if parent tier is active
        is_active = False
        parent = self.parentItem()
        if parent: # BusLineItem's parent is likely a QGraphicsRectItem (bus_layer)
            tier = parent.parentItem()
            if hasattr(tier, "_active") and tier._active:
                is_active = True

        # Hover highlight
        if self._hover:
            hover_pen = QPen(QColor("#ffd33d"), 3)
            painter.setPen(hover_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self.rect())
        elif is_active:
            active_pen = QPen(QColor("#00ffea"), 2)
            painter.setPen(active_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self.rect())

    def contextMenuEvent(self, event):
        from PyQt5.QtWidgets import QMenu, QInputDialog, QAction
        menu = QMenu()

        act_type = menu.addMenu("Change Type")
        t1 = act_type.addAction("Bolted Overlap")
        t2 = act_type.addAction("Clamped Edge")
        t3 = act_type.addAction("Sandwich Joint")

        act_overlap = menu.addAction(f"Change Overlap ({self.overlap_m * 1000:.0f} mm)...")
        if self.joint_type in ["clamped_edge", "sandwich_joint"]:
            act_overlap.setEnabled(False)
        act_bolts = menu.addAction(f"Change Bolt Count ({self.bolt_count})...")
        act_dia = menu.addAction(f"Change Bolt Diameter (M{self.bolt_dia_mm:.0f})...")
        act_torque = menu.addAction(f"Change Torque ({self.torque_Nm:.1f} Nm)...")

        menu.addSeparator()
        act_delete = menu.addAction("Delete Joint")

        action = menu.exec_(event.screenPos())
        if not action:
            return

        if action == t1:
            self.set_joint_parameters(joint_type="bolted_overlap")
        elif action == t2:
            self.set_joint_parameters(joint_type="clamped_edge")
        elif action == t3:
            self.set_joint_parameters(joint_type="sandwich_joint")
        elif action == act_overlap:
            val, ok = QInputDialog.getDouble(None, "Joint Overlap", "Overlap (mm):", self.overlap_m * 1000, 1, 1000, 1)
            if ok:
                self.set_joint_parameters(overlap_m=val / 1000.0)
        elif action == act_bolts:
            val, ok = QInputDialog.getInt(None, "Bolt Count", "Count:", self.bolt_count, 1, 100, 1)
            if ok:
                self.set_joint_parameters(bolt_count=val)
        elif action == act_dia:
            val, ok = QInputDialog.getDouble(None, "Bolt Diameter", "Diameter (mm):", self.bolt_dia_mm, 1, 100, 1)
            if ok:
                self.set_joint_parameters(bolt_dia_mm=val)
        elif action == act_torque:
            val, ok = QInputDialog.getDouble(None, "Joint Torque", "Torque (Nm):", self.torque_Nm, 1, 1000, 1)
            if ok:
                self.set_joint_parameters(torque_Nm=val)
        elif action == act_delete:
            if self.scene():
                # Notify parent if needed, or just remove
                p = self.parentItem()
                if isinstance(p, BusLineItem):
                    p.delete_child(self)
                self.scene().removeItem(self)
        
        # Trigger re-solve if needed (through the project dirty signal or similar)
        if self.scene():
            self.scene().update()
        
        # Find SwitchboardTab and mark dirty
        view = None
        if self.scene() and self.scene().views():
            view = self.scene().views()[0]
        
        if view:
            # Walk up to find SwitchboardTab
            w = view
            while w:
                if w.__class__.__name__ == "SwitchboardTab":
                    if hasattr(w, "_mark_project_dirty"):
                        w._mark_project_dirty()
                    break
                w = w.parent()

    def type(self):
        return BUS_JOIN_TYPE

    def center(self):
        return self.mapToScene(QPointF(0, 0))

    def set_joint_parameters(
        self,
        *,
        overlap_m: float | None = None,
        bolt_count: int | None = None,
        bolt_dia_mm: float | None = None,
        torque_Nm: float | None = None,
        joint_type: str | None = None,
        nut_factor: float | None = None,
        e_streamline: float | None = None,
        csa_factor: float | None = None,
        h_contact: float | None = None,
    ):
        if overlap_m is not None:
            self.overlap_m = float(overlap_m)
        if bolt_count is not None:
            self.bolt_count = int(bolt_count)
        if bolt_dia_mm is not None:
            self.bolt_dia_mm = float(bolt_dia_mm)
        if torque_Nm is not None:
            self.torque_Nm = float(torque_Nm)
        if joint_type is not None:
            self.joint_type = str(joint_type)
        if nut_factor is not None:
            self.nut_factor = float(nut_factor)
        if e_streamline is not None:
            self.e_streamline = float(e_streamline)
        if csa_factor is not None:
            self.csa_factor = float(csa_factor)
        if h_contact is not None:
            self.h_contact = float(h_contact)

        self.refresh_spec()

    def itemChange(self, change, value):
        scene = self.scene()

        if scene is None:
            return super().itemChange(change, value)

        if change == QGraphicsItem.ItemPositionChange:
            parent = self.parentItem()
            if parent is None:
                return value

            line = parent.line()

            a_scene = parent.mapToScene(line.p1())
            b_scene = parent.mapToScene(line.p2())
            p_scene = parent.mapToScene(value)

            ax, ay = a_scene.x(), a_scene.y()
            bx, by = b_scene.x(), b_scene.y()
            px, py = p_scene.x(), p_scene.y()

            vx = bx - ax
            vy = by - ay
            vv = vx * vx + vy * vy

            if vv < 1e-9:
                return self.pos()

            wx = px - ax
            wy = py - ay

            s = (wx * vx + wy * vy) / vv
            s = max(0.0, min(1.0, s))

            # --- Edge constraint: must be >25mm (GRID) from TierItem edges ---
            constraints = get_tier_constraints(self)
            if constraints:
                tr, margin, tier = constraints
                # Valid range for s on this line segment
                s_min, s_max = 0.0, 1.0
                
                # Bus endpoints in Tier local coordinates
                p1_tier = tier.mapFromItem(parent, line.p1())
                p2_tier = tier.mapFromItem(parent, line.p2())
                
                # Line: P(s) = p1_tier + s * (p2_tier - p1_tier)
                dp = p2_tier - p1_tier
                
                # For each dimension (x, y), check the 25mm boundary
                for val, dval, low, high in [
                    (p1_tier.x(), dp.x(), tr.left() + margin, tr.right() - margin),
                    (p1_tier.y(), dp.y(), tr.top() + margin, tr.bottom() - margin)
                ]:
                    if abs(dval) < 1e-9:
                        if val < low or val > high:
                            pass
                    else:
                        s_low = (low - val) / dval
                        s_high = (high - val) / dval
                        
                        s_start = min(s_low, s_high)
                        s_end = max(s_low, s_high)
                        
                        s_min = max(s_min, s_start)
                        s_max = min(s_max, s_end)

                if s_min <= s_max:
                    s = max(s_min, min(s_max, s))

            proj_scene = QPointF(
                ax + s * vx,
                ay + s * vy
            )

            dist = math.hypot(px - proj_scene.x(), py - proj_scene.y())

            if dist > 20:
                return self.pos()

            # Check if another attachment exists at this projected point
            for item in self.scene().items():
                if item is self:
                    continue
                if item.type() in (BUS_LOAD_TYPE, BUS_SOURCE_TYPE, BUS_JOIN_TYPE):
                    c = item.mapToScene(QPointF(0, 0))
                    if math.hypot(c.x() - proj_scene.x(), c.y() - proj_scene.y()) < 8:
                        return self.pos()

            return parent.mapFromScene(proj_scene)

        return super().itemChange(change, value)

    def to_dict(self):

        p = self.pos()

        return {
            "type": "join",
            "x": p.x(),
            "y": p.y(),
            "overlap_m": self.overlap_m,
            "bolt_count": self.bolt_count,
            "bolt_dia_mm": self.bolt_dia_mm,
            "torque_Nm": self.torque_Nm,
            "joint_type": self.joint_type,
            "joint_id": self.join_id,
            "nut_factor": self.nut_factor,
            "e_streamline": self.e_streamline,
            "csa_factor": self.csa_factor,
            "h_contact": self.h_contact,
        }

    @classmethod
    def from_dict(cls, d):

        p = QPointF(d["x"], d["y"])

        spec = BusbarJointSpec(
            joint_id=d.get("joint_id"),
            overlap_m=d.get("overlap_m", 0.05),
            bolt_count=d.get("bolt_count", 4),
            bolt_dia_mm=d.get("bolt_dia_mm", 10.0),
            torque_Nm=d.get("torque_Nm", 45.0),
            joint_type=d.get("joint_type", "bolted_overlap"),
            nut_factor=d.get("nut_factor", 0.20),
            e_streamline=d.get("e_streamline", 0.5),
            csa_factor=d.get("csa_factor", 1.0),
            h_contact=d.get("h_contact", 5000.0),
        )

        return cls(p, spec=spec)
# =========================================================
# SOURCE
# =========================================================

class BusSourceItem(QGraphicsEllipseItem):

    def __init__(self, center: QPointF):

        r = 8
        super().__init__(-r, -r, 2 * r, 2 * r)

        self.setBrush(QBrush(QColor("#58a6ff")))
        self.setPen(QPen(QColor("#1f6feb"), 2))

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setZValue(100)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self._hover = False

        self._label = QGraphicsSimpleTextItem("SRC", self)
        self._label.setBrush(QBrush(QColor("#d0d7de")))
        self._label.setPos(10, -10)
        self.disconnected = False

        self.node_id: int | None = None
        self._node_label = QGraphicsSimpleTextItem("", self)
        self._node_label.setBrush(QBrush(QColor("#58a6ff")))
        self._node_label.setPos(10, 5)

    def set_node_id(self, nid: int | None):
        self.node_id = nid
        if nid is not None:
            self._node_label.setText(f"Node: {nid}")
        else:
            self._node_label.setText("")

    def set_disconnected(self, disconnected: bool):
        self.disconnected = disconnected
        if disconnected:
            self.setBrush(QBrush(QColor("#f85149")))
            self.setPen(QPen(QColor("#ffffff"), 2))
        else:
            self.setBrush(QBrush(QColor("#58a6ff")))
            self.setPen(QPen(QColor("#1f6feb"), 2))
        self.update()

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter, option, widget=None):
        # Base circle
        super().paint(painter, option, widget)

        # Selection highlighting if parent tier is active
        is_active = False
        parent = self.parentItem()
        if parent: # BusLineItem's parent is likely a QGraphicsRectItem (bus_layer)
            tier = parent.parentItem()
            if hasattr(tier, "_active") and tier._active:
                is_active = True

        # Hover highlight
        if self._hover:
            hover_pen = QPen(QColor("#ffd33d"), 3)
            painter.setPen(hover_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self.rect())
        elif is_active:
            active_pen = QPen(QColor("#00ffea"), 2)
            painter.setPen(active_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self.rect())

    def type(self):
        return BUS_SOURCE_TYPE

    def center(self):
        return self.mapToScene(QPointF(0, 0))

    def itemChange(self, change, value):

        scene = self.scene()

        if scene is None:
            return super().itemChange(change, value)

        if change == QGraphicsItem.ItemPositionChange:

            parent = self.parentItem()
            if parent is None:
                return value

            line = parent.line()

            p1 = parent.mapToScene(line.p1())
            p2 = parent.mapToScene(line.p2())

            scene_value = parent.mapToScene(value)

            d1 = math.hypot(scene_value.x() - p1.x(), scene_value.y() - p1.y())
            d2 = math.hypot(scene_value.x() - p2.x(), scene_value.y() - p2.y())

            snap_scene = p1 if d1 < d2 else p2
            dist = min(d1, d2)

            if dist > 20:
                return self.pos()

            # --- Edge constraint: must be >25mm (GRID) from TierItem edges ---
            constraints = get_tier_constraints(self)
            if constraints:
                tr, margin, tier = constraints
                
                # Target point in Tier local coordinates
                snap_tier = tier.mapFromScene(snap_scene)
                
                # If snapping to this endpoint would place it on the edge, refuse.
                if (snap_tier.x() < tr.left() + margin - 0.1 or 
                    snap_tier.x() > tr.right() - margin + 0.1 or
                    snap_tier.y() < tr.top() + margin - 0.1 or 
                    snap_tier.y() > tr.bottom() - margin + 0.1):
                    return self.pos()

            # Check if another attachment exists at this endpoint
            for item in self.scene().items():
                if item is self:
                    continue
                if item.type() in (BUS_LOAD_TYPE, BUS_SOURCE_TYPE, BUS_JOIN_TYPE):
                    c = item.mapToScene(QPointF(0, 0))
                    if math.hypot(c.x() - snap_scene.x(), c.y() - snap_scene.y()) < 8:
                        return self.pos()

            # Update parent tracking if endpoint changed
            if self in parent.endpoint_a_items and snap_scene == p2:
                parent.endpoint_a_items.remove(self)
                parent.endpoint_b_items.append(self)
            elif self in parent.endpoint_b_items and snap_scene == p1:
                parent.endpoint_b_items.remove(self)
                parent.endpoint_a_items.append(self)

            return parent.mapFromScene(snap_scene)

        return super().itemChange(change, value)

    def to_dict(self):
        p = self.pos()
        return {
            "type": "source",
            "x": p.x(),
            "y": p.y(),
        }

    @classmethod
    def from_dict(cls, d):
        p = QPointF(d["x"], d["y"])
        return cls(p)
