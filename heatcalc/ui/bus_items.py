from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional
import uuid
from PyQt5.QtCore import QPointF, Qt, QRect
from PyQt5.QtGui import QColor, QPen, QBrush, QPainterPath, QPainterPathStroker
from PyQt5.QtWidgets import (
    QGraphicsLineItem,
    QGraphicsEllipseItem,
    QGraphicsSimpleTextItem,
    QGraphicsItem,
    QGraphicsDropShadowEffect,
)

from heatcalc.core.models import BusbarJointSpec
from heatcalc.ui.color_utils import temperature_to_color

# --------- QGraphicsItem "type" ids ----------
BUS_LINE_TYPE = 10001
BUS_LOAD_TYPE = 10002
BUS_JOIN_TYPE = 10003
BUS_SOURCE_TYPE = 10004

def format_thermal_tooltip(results, point_temp=None) -> str:
    """
    HTML tooltip showing all solver segments belonging to a bus.
    """

    if not isinstance(results, (list, tuple)):
        results = [results]

    point_row = ""
    if point_temp is not None:
        point_row = f"""
        <tr>
            <td style='color:#8b949e;'>Point temp:</td>
            <td style='color:#ffa657; font-weight:bold;'>{point_temp:.1f} °C</td>
        </tr>
        """

    max_I = max(float(r.get("I_A", 0.0)) for r in results)
    max_T = max(float(r.get("T_C", 0.0)) for r in results)

    html = f"""
    <div style='font-family: sans-serif; min-width: 200px;'>

        <b style='color:#58a6ff;'>Bus Result</b><br/>
        

        <table style='border-spacing:4px; margin-top:4px;'>
        {point_row}
        <tr>
            <td style='color:#8b949e;'>Max current:</td>
            <td style='font-weight:bold;'>{max_I:.1f} A</td>
        </tr>
        <tr>
            <td style='color:#8b949e;'>Max temp:</td>
            <td style='color:#d19a66; font-weight:bold;'>{max_T:.1f} °C</td>
        </tr>
        </table>

        <hr style='border:none;border-top:1px solid #30363d;margin:4px 0;'/>

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

        temp = float(res.get("T_C", 0.0))
        current = float(res.get("I_A", 0.0))
        ambient = float(res.get("ambient_C", 40.0))
        dt = temp - ambient

        html += f"""
        <tr>
            <td style='padding-right:24px;'>{i}</td>
            <td align='right' style='padding-left:24px; padding-right:24px;'>{current:.1f} A</td>
            <td align='right' style='padding-left:24px; padding-right:24px;'>{temp:.1f} °C</td>
            <td align='right' style='padding-left:24px;'>+{dt:.1f} K</td>
        </tr>
        """

    html += "</table></div>"

    return html

@dataclass
class BusSpecUI:
    width_mm: float = 100.0
    thickness_mm: float = 10.0
    bars_in_parallel: int = 1
    phases: int = 3


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

        if not hasattr(scene, "thermal_graph"):
            return
        if not hasattr(self, "thermal_edge"):
            return

        edge = self.thermal_edge
        graph = scene.thermal_graph
        node_T = scene.node_temperatures

        p = event.scenePos()

        T = self._temperature_at_edge_point(edge, graph, node_T, p)

        if getattr(self, "thermal_results", None):
            html = format_thermal_tooltip(self.thermal_results, point_temp=T)
            self.setToolTip(html)
        else:
            self.setToolTip(f"T = {T:.1f} °C")

    def _temperature_at_edge_point(self, edge, graph, node_T, p_scene):
        a = graph.nodes[edge.u].p
        b = graph.nodes[edge.v].p

        dx = b.x() - a.x()
        dy = b.y() - a.y()
        L2 = dx * dx + dy * dy

        if L2 < 1e-12:
            return node_T.get(edge.u, 40.0)

        t = ((p_scene.x() - a.x()) * dx +
             (p_scene.y() - a.y()) * dy) / L2

        t = max(0.0, min(1.0, t))

        Ta = node_T.get(edge.u, 40.0)
        Tb = node_T.get(edge.v, 40.0)

        return (1 - t) * Ta + t * Tb
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

            return QPointF(
                snap(value.x()),
                snap(value.y())
            )

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

            "x1": line.x1(),
            "y1": line.y1(),
            "x2": line.x2(),
            "y2": line.y2(),

            "spec": {
                "width_mm": self.spec.width_mm,
                "thickness_mm": self.spec.thickness_mm,
                "bars_in_parallel": self.spec.bars_in_parallel,
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

        bus.bus_id = d.get("id")

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

    def __init__(self, center: QPointF, I_load_A: float = 0):

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
        self.disconnected = False

        self._label = QGraphicsSimpleTextItem(f"{self.I_load_A:.0f}A", self)
        self._label.setBrush(QBrush(QColor("#d0d7de")))
        self._label.setPos(10, -10)

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
        }

    @classmethod
    def from_dict(cls, d):
        p = QPointF(d["x"], d["y"])
        return cls(p, d.get("I_load_A", 0.0))

# =========================================================
# JOIN
# =========================================================

class BusJoinItem(QGraphicsEllipseItem):

    def __init__(self, center: QPointF, spec: BusbarJointSpec | None = None):
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
        return (
            f"{self.joint_type}\n"
            f"{self.bolt_count}x M{self.bolt_dia_mm:.0f}\n"
            f"{self.torque_Nm:.0f} Nm"
        )

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
            self.setToolTip(format_thermal_tooltip(self.thermal_result))
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
            "nut_factor": self.nut_factor,
            "e_streamline": self.e_streamline,
            "csa_factor": self.csa_factor,
            "h_contact": self.h_contact,
        }

    @classmethod
    def from_dict(cls, d):

        p = QPointF(d["x"], d["y"])

        spec = BusbarJointSpec(
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