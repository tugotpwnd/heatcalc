from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional
import uuid
from PyQt5.QtCore import QPointF
from PyQt5.QtGui import QColor, QPen, QBrush
from PyQt5.QtWidgets import (
    QGraphicsLineItem,
    QGraphicsEllipseItem,
    QGraphicsSimpleTextItem,
    QGraphicsItem,
    QGraphicsDropShadowEffect,
)

# --------- QGraphicsItem "type" ids ----------
BUS_LINE_TYPE = 10001
BUS_LOAD_TYPE = 10002
BUS_JOIN_TYPE = 10003
BUS_SOURCE_TYPE = 10004


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

        self.setZValue(10)

        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(20)
        glow.setOffset(0)

        self.setGraphicsEffect(glow)
        self._glow = glow

        self.bus_id = uuid.uuid4().hex

        self._temperature_segments = []

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def type(self):
        return BUS_LINE_TYPE

    # ---------------------------------------------------------
    # Attach children
    # ---------------------------------------------------------

    def attach_child(self, item, scene_pos: QPointF):

        # convert scene coordinate into bus local coordinate
        local_pos = self.mapFromScene(scene_pos)

        item.setParentItem(self)
        item.setPos(local_pos)
        item.setZValue(self.zValue() + 1)

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

    # ---------------------------------------------------------
    # Drawing
    # ---------------------------------------------------------

    def paint(self, painter, option, widget=None):

        line = self.line()

        if not self._temperature_segments:
            painter.setPen(self._base_pen)
            painter.drawLine(line)
            return

        from PyQt5.QtGui import QLinearGradient

        grad = QLinearGradient(line.x1(), line.y1(), line.x2(), line.y2())

        for s0, s1, color in self._temperature_segments:
            grad.setColorAt(s0, color)
            grad.setColorAt(s1, color)

        pen = QPen(grad, self._base_pen.width())
        painter.setPen(pen)
        painter.drawLine(line)

    # ---------------------------------------------------------

    def update_glow(self, color):
        glow_color = QColor(color)
        glow_color.setAlpha(150)
        self._glow.setColor(glow_color)

    # ---------------------------------------------------------

    def itemChange(self, change, value):

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
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

        self.I_load_A = float(I_load_A)

        self._label = QGraphicsSimpleTextItem(f"{self.I_load_A:.0f}A", self)
        self._label.setBrush(QBrush(QColor("#d0d7de")))
        self._label.setPos(10, -10)

    def type(self):
        return BUS_LOAD_TYPE

    def center(self):
        return self.mapToScene(QPointF(0, 0))

    def itemChange(self, change, value):

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

            # convert snap back into parent coordinates
            return parent.mapFromScene(snap_scene)

        return super().itemChange(change, value)

# =========================================================
# JOIN
# =========================================================

class BusJoinItem(QGraphicsEllipseItem):

    def __init__(self, center: QPointF, R_contact_20_uohm: float = 4.0):

        r = 6
        super().__init__(-r, -r, 2 * r, 2 * r)

        self.setBrush(QBrush(QColor("#f85149")))
        self.setPen(QPen(QColor("#a40e26"), 2))

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

        self.R_contact_20_uohm = float(R_contact_20_uohm)

        self._label = QGraphicsSimpleTextItem(f"{self.R_contact_20_uohm:.1f}µΩ", self)
        self._label.setBrush(QBrush(QColor("#d0d7de")))
        self._label.setPos(10, -10)

    def type(self):
        return BUS_JOIN_TYPE

    def center(self):
        return self.mapToScene(QPointF(0, 0))

    def itemChange(self, change, value):

        if change == QGraphicsItem.ItemPositionChange:

            parent = self.parentItem()
            if parent is None:
                return value

            line = parent.line()

            # segment endpoints in SCENE coordinates
            a_scene = parent.mapToScene(line.p1())
            b_scene = parent.mapToScene(line.p2())

            # dragged candidate point in SCENE coordinates
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

            # convert back to parent coordinates for actual placement
            return parent.mapFromScene(proj_scene)

        return super().itemChange(change, value)


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
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

        self._label = QGraphicsSimpleTextItem("SRC", self)
        self._label.setBrush(QBrush(QColor("#d0d7de")))
        self._label.setPos(10, -10)

    def type(self):
        return BUS_SOURCE_TYPE

    def center(self):
        return self.mapToScene(QPointF(0, 0))

    def itemChange(self, change, value):

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

            return parent.mapFromScene(snap_scene)

        return super().itemChange(change, value)