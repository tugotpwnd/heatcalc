import math
from typing import Optional

from PyQt5.QtGui import QPen, QColor, QPainter, QBrush
from PyQt5.QtWidgets import (
    QGraphicsView,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsEllipseItem,
    QGraphicsLineItem, QGraphicsItem
)
from PyQt5.QtCore import Qt, QRectF, QPointF
from .bus_items import BusLineItem, BusSpecUI, BusLoadItem, BusJoinItem, BusSourceItem
from heatcalc.ui.temperature_legend import TemperatureLegend
from .tier_item import TierItem
from .geometry import GRID, snap

class DesignerView(QGraphicsView):
    """Scene with grid, zoom, and panning helpers."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(QRectF(-5000, -5000, 10000, 10000))
        self.setScene(self._scene)

        self.setBackgroundBrush(QColor("#1e1f22"))
        self.setRenderHints(self.renderHints() |
                            QPainter.Antialiasing |
                            QPainter.TextAntialiasing)

        self._solver_overlay_items = []

        self._load_mode = False
        self._join_mode = False
        self._bus_draw_mode = False
        self._source_mode = False

        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)

        # panning
        self._panning = False
        self._pan_start = QPointF()

        # ---Tier layer support ----
        self._active_tier = None

        # ---- bus drawing tool ----
        self._draw_bus_mode = False
        self._bus_start = None          # QPointF
        self._bus_temp = None           # QGraphicsLineItem
        self._bus_spec = BusSpecUI()    # default spec (UI will overwrite this later)

        self.legend = TemperatureLegend(self)
        self.legend.move(20, 20)
        self.legend.show()

        # --- snap preview marker ---
        self._snap_tol_px = 15

        self._snap_marker = QGraphicsEllipseItem(-5, -5, 10, 10)
        self._snap_marker.setBrush(QBrush(QColor("#58a6ff")))
        self._snap_marker.setPen(QPen(Qt.NoPen))
        self._snap_marker.setZValue(10_000)
        self._snap_marker.setVisible(False)
        self._scene.addItem(self._snap_marker)

    def scene(self) -> QGraphicsScene:
        return self._scene

    # ---- Layer support --------------------------------------------------------------

    def set_active_tier(self, tier: TierItem):
        self._active_tier = tier
        self.update_tier_visuals()

    def update_tier_visuals(self):
        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]
        for t in tiers:
            t.set_active(t is self._active_tier)

    def refresh_tier_stack_visuals(self):
        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]
        self._apply_layer_visuals(tiers)

    def set_tier_layer(self, tier: TierItem, layer: int):

        tier.layer_index = layer

        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]

        self._apply_layer_visuals(tiers)

    def _apply_layer_visuals(self, tiers):

        for t in tiers:

            # enforce depth ordering
            t.setZValue(t.layer_index * 1000)
            t.setFlag(QGraphicsItem.ItemStacksBehindParent, t.layer_index == 0)

            # keep overlay above tier
            if hasattr(t, "overlay_item") and t.overlay_item is not None:
                t.overlay_item.setZValue(t.zValue() + 1)

            t.update()

    # ---- Draw modes --------------------------------------------------------------

    def set_load_mode(self, enabled: bool):

        self._load_mode = enabled

        if enabled:
            self._bus_draw_mode = False
            self._join_mode = False
            self._source_mode = False
        else:
            self._set_snap_marker(None)

    def set_join_mode(self, enabled: bool):

        self._join_mode = enabled

        if enabled:
            self._bus_draw_mode = False
            self._load_mode = False
            self._source_mode = False
        else:
            self._set_snap_marker(None)

    def set_source_mode(self, enabled: bool):

        self._source_mode = enabled

        if enabled:
            self._load_mode = False
            self._join_mode = False
            self._bus_draw_mode = False
        else:
            self._set_snap_marker(None)

    # ---- Grid --------------------------------------------------------------
    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)

        left = int(rect.left()) - (int(rect.left()) % GRID)
        top = int(rect.top()) - (int(rect.top()) % GRID)

        thin = QPen(QColor(60, 60, 60))
        bold = QPen(QColor(80, 80, 80), 2)

        # verticals
        x = left
        while x < rect.right():
            painter.setPen(bold if (int(x) % (GRID * 10) == 0) else thin)
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            x += GRID

        # horizontals
        y = top
        while y < rect.bottom():
            painter.setPen(bold if (int(y) % (GRID * 10) == 0) else thin)
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            y += GRID

    # ---- Zoom & pan --------------------------------------------------------
    def resizeEvent(self, event):

        super().resizeEvent(event)

        margin = 20

        x = self.width() - self.legend.width() - margin
        y = self.height() - self.legend.height() - margin

        self.legend.move(x, y)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event):

        if event.button() == Qt.LeftButton:

            pos = self.mapToScene(event.pos())

            tier = self.tier_at_point(pos)

            if tier:
                self.set_active_tier(tier)

                self.update_tier_visuals()

        if self._source_mode and event.button() == Qt.LeftButton:

            pos = self.mapToScene(event.pos())
            node = self.find_nearest_bus_endpoint(pos, tol_px=self._snap_tol_px)

            if node is None:
                return  # reject

            if self._attachment_exists(node):
                return

            from .bus_items import BusSourceItem
            bus = self.find_bus_for_point(node)

            if bus:
                src = BusSourceItem(node)
                bus.attach_child(src, node)
            return

        from PyQt5.QtWidgets import QInputDialog

        if self._load_mode and event.button() == Qt.LeftButton:

            pos = self.mapToScene(event.pos())
            node = self.find_nearest_bus_endpoint(pos, tol_px=self._snap_tol_px)

            if node is None:
                return  # reject

            if self._attachment_exists(node):
                return

            from PyQt5.QtWidgets import QInputDialog
            I, ok = QInputDialog.getDouble(self, "Load Current", "Enter load current (A):", 100.0, 0, 10000, 1)
            if not ok:
                return

            from .bus_items import BusLoadItem
            bus = self.find_bus_for_point(node)

            if bus:
                load = BusLoadItem(node, I)
                bus.attach_child(load, node)
            return

        # bus draw tool (no modifiers)
        if self._join_mode and event.button() == Qt.LeftButton:

            pos = self.mapToScene(event.pos())
            end = self.find_nearest_bus_endpoint(pos, tol_px=self._snap_tol_px)

            if end is not None:
                node = end
            else:
                node = self.find_nearest_bus_segment(pos, tol_px=self._snap_tol_px)

            if node is None:
                return  # reject

            if self._attachment_exists(node):
                return

            from .bus_items import BusJoinItem
            bus = self.find_bus_for_point(node)

            if bus:
                join = BusJoinItem(node)
                bus.attach_child(join, node)
            return

        if self._draw_bus_mode and event.button() == Qt.LeftButton and not (event.modifiers() & Qt.ControlModifier):

            pos = self.mapToScene(event.pos())
            pos.setX(snap(pos.x()))
            pos.setY(snap(pos.y()))

            tier = self._active_tier or self.tier_at_point(pos)

            if tier is None:
                return

            # ensure inside tier bounds
            local = tier.mapFromScene(pos)

            if not tier.boundingRect().contains(local):
                return

            self._bus_start = pos

            self._bus_temp = QGraphicsLineItem(pos.x(), pos.y(), pos.x(), pos.y())
            self._bus_temp.setPen(QPen(QColor("#aaaaaa"), 2, Qt.DashLine))

            self.scene().addItem(self._bus_temp)

            return

        # panning (existing behaviour)
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier)):
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # update temp bus line

        # Snap preview (only when placing source/load/join)
        pos = self.mapToScene(event.pos())
        snap_p = self._snap_target_for_mode(pos)

        if snap_p is not None:
            self._set_snap_marker(snap_p)
        else:
            self._set_snap_marker(None)

        if self._bus_start is not None and self._bus_temp is not None:

            pos = self.mapToScene(event.pos())

            dx = pos.x() - self._bus_start.x()
            dy = pos.y() - self._bus_start.y()

            # axis lock
            if abs(dx) > abs(dy):
                pos.setY(self._bus_start.y())
            else:
                pos.setX(self._bus_start.x())

            pos.setX(snap(pos.x()))
            pos.setY(snap(pos.y()))

            tier = self._active_tier

            if tier:
                p_local = tier.mapFromScene(pos)
                rect = tier._rect

                # clamp cursor to tier bounds
                x = min(max(p_local.x(), rect.left()), rect.right())
                y = min(max(p_local.y(), rect.top()), rect.bottom())

                p_local = QPointF(x, y)
                pos = tier.mapToScene(p_local)

            self._bus_temp.setLine(
                self._bus_start.x(),
                self._bus_start.y(),
                pos.x(),
                pos.y()
            )

            return

        # panning (existing behaviour)
        if self._panning:
            delta = self.mapToScene(self._pan_start) - self.mapToScene(event.pos())
            self._pan_start = event.pos()
            self.setTransformationAnchor(QGraphicsView.NoAnchor)
            self.translate(delta.x(), delta.y())
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):

        # finish bus draw FIRST
        if self._bus_start is not None and self._bus_temp is not None and event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.pos())

            dx = pos.x() - self._bus_start.x()
            dy = pos.y() - self._bus_start.y()

            # keep same axis lock behaviour as preview
            if abs(dx) > abs(dy):
                pos.setY(self._bus_start.y())
            else:
                pos.setX(self._bus_start.x())

            pos.setX(snap(pos.x()))
            pos.setY(snap(pos.y()))

            tier = self._active_tier or self.tier_at_point(self._bus_start)

            if tier:
                p_local = tier.mapFromScene(pos)
                rect = tier._rect

                # final clamp after snap
                p_local.setX(min(max(p_local.x(), rect.left()), rect.right()))
                p_local.setY(min(max(p_local.y(), rect.top()), rect.bottom()))

                pos = tier.mapToScene(p_local)

            tier = self._active_tier or self.tier_at_point(self._bus_start)
            commit_bus = False
            p0 = None
            p1 = None

            if tier:
                p_local = tier.mapFromScene(pos)
                rect = tier._rect
                # clamp final point to tier bounds
                x = min(max(p_local.x(), rect.left()), rect.right())
                y = min(max(p_local.y(), rect.top()), rect.bottom())
                p0 = tier.mapFromScene(self._bus_start)
                p1 = tier.mapFromScene(pos)

                rect = tier._rect
                p1.setX(min(max(p1.x(), rect.left()), rect.right()))
                p1.setY(min(max(p1.y(), rect.top()), rect.bottom()))
                # only commit non-zero length buses
                if abs(p1.x() - p0.x()) > 1e-6 or abs(p1.y() - p0.y()) > 1e-6:
                    commit_bus = True

            if commit_bus:
                line = BusLineItem(p0, p1, spec=self._bus_spec)
                tier.add_bus_item(line)

            # ALWAYS clean up preview state
            if self._bus_temp.scene() is not None:
                self.scene().removeItem(self._bus_temp)

            self._bus_temp = None
            self._bus_start = None

            self.update_bus_connections()
            self.refresh_tier_stack_visuals()
            return

        # panning end
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
        else:
            super().mouseReleaseEvent(event)

        # NOW geometry is final
        self.update_bus_connections()
        self.refresh_tier_stack_visuals()


    # ---- tier helpers --------------------------------------------------------

    def tier_at_point(self, p):

        from .tier_item import TierItem

        for item in self.scene().items(p):

            if isinstance(item, TierItem):
                return item

        return None

    # ---- Bus --------------------------------------------------------

    def set_bus_draw_mode(self, enabled: bool) -> None:
        self._draw_bus_mode = bool(enabled)

    def _cancel_bus_draw(self):
        if self._bus_temp is not None:
            if self._bus_temp.scene() is not None:
                self.scene().removeItem(self._bus_temp)
        self._bus_temp = None
        self._bus_start = None

    def set_default_bus_spec(self, spec: BusSpecUI) -> None:
        # called from SwitchboardTab when UI fields change
        self._bus_spec = spec

    def show_edge_currents(self, graph, current_result):

        # clear previous overlay objects
        for item in self._solver_overlay_items:
            if item.scene():
                self.scene().removeItem(item)

        self._solver_overlay_items.clear()

        for edge in graph.edges.values():

            line_item = edge.ui_item
            I = current_result.edge_I_A.get(edge.id, 0.0)

            if I < 200:
                color = QColor("#2ea043")
            elif I < 600:
                color = QColor("#d29922")
            else:
                color = QColor("#f85149")

            line_item.setPen(QPen(color, 5))

            line = line_item.line()
            p0 = line.p1()
            p1 = line.p2()

            mid = QPointF(
                (p0.x() + p1.x()) * 0.5,
                (p0.y() + p1.y()) * 0.5,
            )

            label = QGraphicsSimpleTextItem(f"{I:.1f} A")
            label.setBrush(QBrush(QColor("#ffffff")))
            label.setPos(mid)
            label._is_current_label = True

            self.scene().addItem(label)
            self._solver_overlay_items.append(label)

    def show_tees(self, graph):

        degree = {n: 0 for n in graph.nodes}

        for e in graph.edges.values():
            degree[e.u] += 1
            degree[e.v] += 1

        for nid, node in graph.nodes.items():

            if degree[nid] >= 3:
                marker = QGraphicsEllipseItem(-6, -6, 12, 12)
                marker.setBrush(QColor("#58a6ff"))

                marker.setPos(node.p)

                marker._is_tee_marker = True

                self.scene().addItem(marker)
                self._solver_overlay_items.append(marker)

    def show_bus_temperature_field(self, solve_result):

        segments_by_line = {}

        node_meta = solve_result.node_meta
        T_vec = solve_result.T_vec_C

        seg_count = {}

        for nd in node_meta:
            seg_count.setdefault(nd.bus_name, 0)
            seg_count[nd.bus_name] += 1

        Tmax = -1
        Tmax_node = None

        for i, nd in enumerate(node_meta):

            T = T_vec[i]

            if T > Tmax:
                Tmax = T
                Tmax_node = nd

            color = temperature_to_color(T)

            line = nd.ui_item

            if line is None:
                continue

            n = seg_count[nd.bus_name]

            s0 = nd.seg_index / n
            s1 = (nd.seg_index + 1) / n

            segments_by_line.setdefault(line, []).append(
                (s0, s1, color)
            )

        for line, segs in segments_by_line.items():
            line.set_temperature_segments(segs)

            # hottest segment colour drives glow
            hottest = max(segs, key=lambda x: x[2].red())
            line.update_glow(hottest[2])

        # ---------- draw hotspot label ----------
        if Tmax_node:

            line = self.bus_lookup.get(Tmax_node.bus_name)

            if line:
                s = (Tmax_node.seg_index + 0.5) / seg_count[Tmax_node.bus_name]

                p0 = line.p0()
                p1 = line.p1()

                pos = p0 + (p1 - p0) * s

                label = QGraphicsSimpleTextItem(f"{Tmax:.1f}°C")
                label.setBrush(QBrush(QColor("#ffffff")))
                label.setZValue(200)

                label.setPos(pos + QPointF(10, -10))

                self.scene().addItem(label)

    def find_nearest_bus_endpoint(self, p, tol_px=15):

        best = None
        best_dist = tol_px

        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]

        for obj in tiers:

            if isinstance(obj, TierItem):

                for item in obj.bus_items():
                    line = item.line()

                    p1 = item.mapToScene(line.p1())
                    p2 = item.mapToScene(line.p2())

                    for pt in (p1, p2):

                        d = math.hypot(p.x() - pt.x(), p.y() - pt.y())

                        if d < best_dist:
                            best = pt
                            best_dist = d

        return best

    def find_bus_for_point(self, p: QPointF, tol_px=15):

        best_bus = None
        best_dist = tol_px

        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]

        for obj in tiers:

            if isinstance(obj, TierItem):

                for item in obj.bus_items():
                    line = item.line()

                    a = item.mapToScene(line.p1())
                    b = item.mapToScene(line.p2())

                    s, proj = project_point_to_segment(p, a, b)

                    d = math.hypot(p.x() - proj.x(), p.y() - proj.y())

                    if d < best_dist:
                        best_bus = item
                        best_dist = d

        return best_bus

    def find_nearest_bus_segment(self, p, tol_px=15):

        best_point = None
        best_dist = tol_px

        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]

        for obj in tiers:

            if isinstance(obj, TierItem):

                for item in obj.bus_items():
                    line = item.line()

                    a = item.mapToScene(line.p1())
                    b = item.mapToScene(line.p2())

                    ax, ay = a.x(), a.y()
                    bx, by = b.x(), b.y()
                    px, py = p.x(), p.y()

                    vx = bx - ax
                    vy = by - ay

                    vv = vx * vx + vy * vy
                    if vv < 1e-9:
                        continue

                    wx = px - ax
                    wy = py - ay

                    s = (wx * vx + wy * vy) / vv
                    s = max(0.0, min(1.0, s))

                    proj = QPointF(ax + s * vx, ay + s * vy)

                    d = math.hypot(px - proj.x(), py - proj.y())

                    if d < best_dist:
                        best_point = proj
                        best_dist = d

        return best_point

    def update_bus_connections(self):

        self.clear_connection_markers()

        buses = []

        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]

        for obj in tiers:

            if isinstance(obj, TierItem):
                buses.extend(obj.bus_items())

        tol = 5

        for a in buses:

            line_a = a.line()

            p0 = a.mapToScene(line_a.p1())
            p1 = a.mapToScene(line_a.p2())

            for p in (p0, p1):

                for b in buses:

                    if a is b:
                        continue

                    line_b = b.line()

                    q0 = b.mapToScene(line_b.p1())
                    q1 = b.mapToScene(line_b.p2())

                    # ---------- endpoint → endpoint ----------
                    for q in (q0, q1):

                        if math.hypot(p.x() - q.x(), p.y() - q.y()) < tol:
                            self._draw_connection_marker(p)
                            break

                    # ---------- endpoint → segment ----------
                    s, proj = project_point_to_segment(p, q0, q1)

                    if math.hypot(p.x() - proj.x(), p.y() - proj.y()) < tol:

                        if s <= 1e-6 or s >= 1 - 1e-6:
                            continue

                        self._draw_connection_marker(p)

    def _draw_connection_marker(self, p):

        marker = QGraphicsEllipseItem(-6, -6, 12, 12)

        marker.setBrush(QColor("#ffd33d"))
        marker.setPen(QPen(Qt.NoPen))

        marker.setZValue(500)

        marker.setPos(p)

        marker._is_connection_marker = True

        self.scene().addItem(marker)

    def clear_solver_overlay(self):

        for item in self.scene().items():

            if isinstance(item, BusLineItem):
                item.clear_temperature_overlay()
            if getattr(item, "_is_current_label", False) or getattr(item, "_is_tee_marker", False):
                self.scene().removeItem(item)

        self._solver_overlay_items.clear()

    def clear_connection_markers(self):

        for item in self.scene().items():

            if getattr(item, "_is_connection_marker", False):
                self.scene().removeItem(item)

    def _set_snap_marker(self, p: Optional[QPointF]):
        if p is None:
            self._snap_marker.setVisible(False)
            return
        self._snap_marker.setPos(p)
        self._snap_marker.setVisible(True)

    def _snap_target_for_mode(self, p: QPointF) -> Optional[QPointF]:

        if self._join_mode:

            # prefer endpoint snap
            end = self.find_nearest_bus_endpoint(p, tol_px=self._snap_tol_px)
            if end is not None:
                return end

            # otherwise snap to segment
            return self.find_nearest_bus_segment(p, tol_px=self._snap_tol_px)

        if self._load_mode or self._source_mode:
            return self.find_nearest_bus_endpoint(p, tol_px=self._snap_tol_px)

        return None

    from .bus_items import BusLoadItem, BusJoinItem, BusSourceItem

    def _attachment_exists(self, p: QPointF, tol_px=8):

        for item in self.scene().items():

            if isinstance(item, (BusLoadItem, BusSourceItem, BusJoinItem)):

                c = item.center()

                if math.hypot(c.x() - p.x(), c.y() - p.y()) < tol_px:
                    return True

        return False


def temperature_to_color(T):

    Tmin = 40
    Tmax = 140

    T = max(Tmin, min(Tmax, T))

    x = (T - Tmin) / (Tmax - Tmin)

    r = int(255 * x)
    g = int(255 * (1 - x))
    b = 0

    return QColor(r, g, b)



def project_point_to_segment(p: QPointF, a: QPointF, b: QPointF):
    """
    Project point p onto segment a→b.

    Returns
    -------
    s : float
        Parametric position along segment (0=start, 1=end)
    proj : QPointF
        Projected point on segment
    """

    ax, ay = a.x(), a.y()
    bx, by = b.x(), b.y()
    px, py = p.x(), p.y()

    vx = bx - ax
    vy = by - ay

    wx = px - ax
    wy = py - ay

    vv = vx * vx + vy * vy

    if vv <= 1e-12:
        return 0.0, QPointF(ax, ay)

    s = (wx * vx + wy * vy) / vv
    s_clamped = max(0.0, min(1.0, s))

    proj = QPointF(ax + s_clamped * vx, ay + s_clamped * vy)

    return s_clamped, proj