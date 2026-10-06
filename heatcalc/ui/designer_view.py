import math
from typing import Optional

from PyQt5.QtGui import QPen, QColor, QPainter, QBrush
from PyQt5.QtWidgets import (
    QGraphicsView,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsEllipseItem,
    QGraphicsLineItem, QGraphicsItem,
    QMenu
)
from PyQt5.QtCore import Qt, QRectF, QPointF
from .bus_items import (
    BusLineItem, BusSpecUI, BusLoadItem, BusJoinItem, BusSourceItem,
    BUS_LINE_TYPE, BUS_LOAD_TYPE, BUS_JOIN_TYPE, BUS_SOURCE_TYPE
)
from heatcalc.ui.temperature_legend import TemperatureLegend
from .color_utils import temperature_to_color
from .tier_item import TierItem
from .geometry import GRID, snap
from copy import deepcopy
from ..core.models import BusbarJointSpec

class DesignerView(QGraphicsView):
    """Scene with grid, zoom, and panning helpers."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(QRectF(-5000, -5000, 10000, 10000))
        self.setScene(self._scene)

        self._joint_spec = BusbarJointSpec()

        self.setBackgroundBrush(QColor("#1e1f22"))
        self.setRenderHints(self.renderHints() |
                            QPainter.Antialiasing |
                            QPainter.TextAntialiasing)

        self._solver_overlay_items = []

        self._load_mode = False
        self._join_mode = False
        self._bus_draw_mode = False
        self._source_mode = False
        self._delete_mode = False

        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)

        # panning
        self._panning = False
        self._pan_start = QPointF()

        # ---Tier layer support ----
        self._active_tier = None

        # ---- bus drawing tool ----
        self._bus_z_counter = 0
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

        # --- Tier swapping (Tab key & Button) ---
        self._tiers_swapped = False
        self._create_layer_toggle_button()

    def _create_layer_toggle_button(self):
        from PyQt5.QtWidgets import QPushButton
        from PyQt5.QtGui import QIcon, QColor
        
        self.btn_toggle_layers = QPushButton("Toggle Layers", self)
        self.btn_toggle_layers.setCheckable(True)
        self.btn_toggle_layers.setStyleSheet("""
            QPushButton {
                background-color: rgba(50, 50, 50, 180);
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: rgba(70, 70, 70, 220);
                border: 1px solid #777;
            }
            QPushButton:checked {
                background-color: #cc8800;
                color: black;
                font-weight: bold;
            }
        """)
        self.btn_toggle_layers.clicked.connect(self._on_toggle_layers_clicked)
        self.btn_toggle_layers.setToolTip("Toggle Front & Rear Layer Priority (Tab)")
        self.btn_toggle_layers.move(10, 10)
        self.btn_toggle_layers.show()

    def _on_toggle_layers_clicked(self, checked: bool):
        self._tiers_swapped = checked
        self.refresh_tier_stack_visuals()
        
        # Show toast message
        from .toast_message import show_toast
        msg = "Rear Tiers at Front (Edit Rear)" if self._tiers_swapped else "Normal View (Edit Front/Mid)"
        color = "#cc8800" if self._tiers_swapped else "#0088cc"
        show_toast(self, msg, duration=1500, color=color)

    def scene(self) -> QGraphicsScene:
        return self._scene

    # ---- Layer support --------------------------------------------------------------

    def set_active_tier(self, tier: Optional[TierItem]):
        self._active_tier = tier
        self.update_tier_visuals()
        if tier:
            self.refresh_tier_stack_visuals()

    def update_tier_visuals(self):
        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]
        for t in tiers:
            t.set_active(t is self._active_tier)

        # Force all bus items to repaint so they pick up the tier activation state
        for item in self.scene().items():
            if item.type() in (BUS_LINE_TYPE, BUS_LOAD_TYPE, BUS_JOIN_TYPE, BUS_SOURCE_TYPE):
                item.update()

    def refresh_tier_stack_visuals(self):
        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]
        self._apply_layer_visuals(tiers)

    def set_tier_layer(self, tier: TierItem, layer: int):
        tier.layer_index = layer
        tiers = [i for i in self.scene().items() if isinstance(i, TierItem)]
        self._apply_layer_visuals(tiers)

        # Notify SwitchboardTab so it can recompute curves (layer affects curve)
        w = self.parent()
        while w:
            if w.__class__.__name__ == "SwitchboardTab":
                if hasattr(w, "_recompute_all_curves"):
                    w._recompute_all_curves()
                break
            w = w.parent()

    def _apply_layer_visuals(self, tiers):

        for t in tiers:
            # Swap logic: 
            # Normal: Rear=0, Mid=1000, Front=2000
            # Swapped: Rear=2000, Mid=1000, Front=0
            
            z_value = t.layer_index * 1000
            if self._tiers_swapped:
                if t.layer_index == 0:  # Rear
                    z_value = 2000
                elif t.layer_index == 2:  # Front
                    z_value = 0
                # Mid (layer 1) stays at 1000

            # Boost active tier slightly within its layer
            if t is self._active_tier:
                z_value += 10

            # enforce depth ordering
            t.setZValue(z_value)
            t.setFlag(QGraphicsItem.ItemStacksBehindParent, z_value == 0)

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
            self._delete_mode = False
        else:
            self._set_snap_marker(None)

    def set_join_mode(self, enabled: bool):

        self._join_mode = enabled

        if enabled:
            self._bus_draw_mode = False
            self._load_mode = False
            self._source_mode = False
            self._delete_mode = False
        else:
            self._set_snap_marker(None)

    def set_source_mode(self, enabled: bool):

        self._source_mode = enabled

        if enabled:
            self._load_mode = False
            self._join_mode = False
            self._bus_draw_mode = False
            self._delete_mode = False
        else:
            self._set_snap_marker(None)

    def set_delete_mode(self, enabled: bool):

        self._delete_mode = enabled

        if enabled:
            self._load_mode = False
            self._join_mode = False
            self._bus_draw_mode = False
            self._source_mode = False
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

        # 1. Update Legend position (bottom right)
        margin = 20
        lx = self.width() - self.legend.width() - margin
        ly = self.height() - self.legend.height() - margin
        self.legend.move(lx, ly)

        # 2. Maintain Toggle Button position (top left)
        if hasattr(self, "btn_toggle_layers"):
            self.btn_toggle_layers.move(10, 10)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Tab:
            # Sync button state
            self.btn_toggle_layers.setChecked(not self.btn_toggle_layers.isChecked())
            self._on_toggle_layers_clicked(self.btn_toggle_layers.isChecked())
            
            event.accept()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        # 0. Check for bus items at this position first
        pos = self.mapToScene(event.pos())
        all_items = self.scene().items(pos)
        
        target_item = None
        for top_item in all_items:
            item = top_item
            while item and not isinstance(item, (BusLineItem, BusLoadItem, BusSourceItem, BusJoinItem, TierItem)):
                item = item.parentItem()
            
            if isinstance(item, (BusLineItem, BusLoadItem, BusSourceItem, BusJoinItem)):
                target_item = item
                break
        
        # If we found a bus item, let it handle its own context menu
        if target_item:
            # Let the scene/view dispatch the event to the graphics item correctly
            # Note: We must map the event to the scene coordinates if we were to manual call it, 
            # but super().contextMenuEvent(event) in QGraphicsView handles mapping and dispatching to items.
            super().contextMenuEvent(event)
            return

        # 1. Update active tier based on right-click location if no bus item found
        tier = self.tier_at_point(pos)
        if tier:
            self.set_active_tier(tier)
            self.update_tier_visuals()

        # Walk up parent chain to find SwitchboardTab
        switchboard = None
        w = self
        while w is not None:
            if w.__class__.__name__ == "SwitchboardTab":
                switchboard = w
                break
            w = w.parent()

        if not switchboard:
            super().contextMenuEvent(event)
            return

        menu = QMenu(self)

        # 1. Layer actions
        front_action = menu.addAction("Set Front")
        mid_action = menu.addAction("Set Mid")
        rear_action = menu.addAction("Set Rear")
        
        menu.addSeparator()

        # 2. Content actions
        act_copy_contents = menu.addAction("Copy tier contents")
        act_paste_contents = menu.addAction("Paste tier contents")

        menu.addSeparator()

        # 3. Tier actions
        act_copy_tier = menu.addAction("Copy tier")
        act_paste_tier = menu.addAction("Paste tier")

        menu.addSeparator()

        # 4. Delete action
        act_delete = menu.addAction("Delete tier")

        # Enable/disable based on whether a tier was clicked
        has_tier = (tier is not None)
        front_action.setEnabled(has_tier)
        mid_action.setEnabled(has_tier)
        rear_action.setEnabled(has_tier)
        act_copy_contents.setEnabled(has_tier)
        act_copy_tier.setEnabled(has_tier)
        act_delete.setEnabled(has_tier)

        # Content paste: enabled if we have tier AND something in contents clipboard
        has_contents_cb = bool(getattr(switchboard, "_tier_clipboard", None))
        act_paste_contents.setEnabled(has_tier and has_contents_cb)

        # Full Tier paste: ONLY enabled if NO tier clicked AND something in full clipboard
        has_full_cb = hasattr(switchboard, "_tier_full_clipboard") and switchboard._tier_full_clipboard
        act_paste_tier.setEnabled(not has_tier and bool(has_full_cb))

        chosen = menu.exec_(event.globalPos())
        if not chosen:
            return

        if chosen == front_action:
            self.set_tier_layer(tier, 2)
        elif chosen == mid_action:
            self.set_tier_layer(tier, 1)
        elif chosen == rear_action:
            self.set_tier_layer(tier, 0)
        elif chosen == act_copy_contents:
            switchboard.copy_tier_contents(tier)
        elif chosen == act_paste_contents:
            switchboard.paste_tier_contents(tier)
        elif chosen == act_copy_tier:
            switchboard.copy_tier(tier)
        elif chosen == act_paste_tier:
            switchboard.paste_tier(pos)
        elif chosen == act_delete:
            tier.requestDelete.emit(tier)

    def mousePressEvent(self, event):

        if event.button() == Qt.LeftButton:

            pos = self.mapToScene(event.pos())

            tier = self.tier_at_point(pos)

            if tier:
                self.set_active_tier(tier)

                self.update_tier_visuals()

        if self._delete_mode and event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.pos())
            # Use scene().items(pos) to get all items at the click point, sorted by ZValue (topmost first)
            all_items = self.scene().items(pos)

            # Find the first bus item (or its child that resolves to a bus item)
            # We want to ignore TierItem and TierOverlayItem if a bus item is also present.
            target_item = None
            for top_item in all_items:
                item = top_item
                # Climb hierarchy for EACH item to see if it belongs to a bus component
                while item and not isinstance(item, (BusLineItem, BusLoadItem, BusSourceItem, BusJoinItem, TierItem)):
                    item = item.parentItem()

                if isinstance(item, (BusLineItem, BusLoadItem, BusSourceItem, BusJoinItem)):
                    target_item = item
                    break # Found our bus item, stop looking

            if target_item:
                item = target_item
                # Special handling for BusLineItem to delete its children
                if isinstance(item, BusLineItem):
                    # Delete all attached children
                    children = item.endpoint_a_items + item.endpoint_b_items + item.segment_items
                    for child in list(children): # iterate over copy to be safe
                        self.scene().removeItem(child)
                    self.scene().removeItem(item)
                elif isinstance(item, (BusLoadItem, BusSourceItem, BusJoinItem)):
                    # These are typically children of a BusLineItem in our implementation,
                    # but let's check if we should remove them from their parent's lists too
                    parent = item.parentItem()
                    if isinstance(parent, BusLineItem):
                        parent.delete_child(item)
                    self.scene().removeItem(item)
            return

        if self._source_mode and event.button() == Qt.LeftButton:

            pos = self.mapToScene(event.pos())
            node = self.find_nearest_bus_endpoint(pos, tol_px=self._snap_tol_px)

            if node is None:
                return  # reject

            # --- Edge constraint: must be >25mm (GRID) from TierItem edges ---
            bus = self.find_bus_for_point(node)
            if bus:
                bus_layer = bus.parentItem()
                if bus_layer:
                    tier = bus_layer.parentItem()
                    if isinstance(tier, TierItem):
                        tr = tier.boundingRect()
                        margin = GRID
                        node_tier = tier.mapFromScene(node)
                        if (node_tier.x() < tr.left() + margin - 0.1 or 
                            node_tier.x() > tr.right() - margin + 0.1 or
                            node_tier.y() < tr.top() + margin - 0.1 or 
                            node_tier.y() > tr.bottom() - margin + 0.1):
                            from .toast_message import show_toast
                            show_toast(self, "Cannot place on edges (>25mm from edge)", duration=2000, color="#f85149")
                            return

            if self._attachment_exists(node):
                return

            # Only one source allowed globally
            existing = self.find_existing_source()
            if existing:
                parent = existing.parentItem()
                if isinstance(parent, BusLineItem):
                    parent.delete_child(existing)
                self.scene().removeItem(existing)

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

            # --- Edge constraint: must be >25mm (GRID) from TierItem edges ---
            bus = self.find_bus_for_point(node)
            if bus:
                bus_layer = bus.parentItem()
                if bus_layer:
                    tier = bus_layer.parentItem()
                    if isinstance(tier, TierItem):
                        tr = tier.boundingRect()
                        margin = GRID
                        node_tier = tier.mapFromScene(node)
                        if (node_tier.x() < tr.left() + margin - 0.1 or 
                            node_tier.x() > tr.right() - margin + 0.1 or
                            node_tier.y() < tr.top() + margin - 0.1 or 
                            node_tier.y() > tr.bottom() - margin + 0.1):
                            from .toast_message import show_toast
                            show_toast(self, "Cannot place on edges (>25mm from edge)", duration=2000, color="#f85149")
                            return

            if self._attachment_exists(node):
                return

            from PyQt5.QtWidgets import QInputDialog, QDialog, QVBoxLayout, QFormLayout, QDoubleSpinBox, QDialogButtonBox
            dialog = QDialog(self)
            dialog.setWindowTitle("Add Load")
            layout = QVBoxLayout(dialog)
            form = QFormLayout()

            spin_i = QDoubleSpinBox()
            spin_i.setRange(0, 10000)
            spin_i.setDecimals(2)
            spin_i.setValue(100.0)
            spin_i.setSuffix(" A")
            form.addRow("Load current:", spin_i)

            spin_t = QDoubleSpinBox()
            spin_t.setRange(0, 500)
            spin_t.setDecimals(1)
            spin_t.setValue(105.0)
            spin_t.setSuffix(" °C")
            form.addRow("Max terminal temp:", spin_t)

            layout.addLayout(form)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)

            if dialog.exec_() != QDialog.Accepted:
                return

            I = spin_i.value()
            T_max = spin_t.value()

            bus = self.find_bus_for_point(node)

            if bus:
                load = BusLoadItem(node, I, max_terminal_temp_c=T_max)
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

            # --- Edge constraint: must be >25mm (GRID) from TierItem edges ---
            bus = self.find_bus_for_point(node)
            if bus:
                bus_layer = bus.parentItem()
                if bus_layer:
                    tier = bus_layer.parentItem()
                    if isinstance(tier, TierItem):
                        tr = tier.boundingRect()
                        margin = GRID
                        node_tier = tier.mapFromScene(node)
                        if (node_tier.x() < tr.left() + margin - 0.1 or 
                            node_tier.x() > tr.right() - margin + 0.1 or
                            node_tier.y() < tr.top() + margin - 0.1 or 
                            node_tier.y() > tr.bottom() - margin + 0.1):
                            from .toast_message import show_toast
                            show_toast(self, "Cannot place on edges (>25mm from edge)", duration=2000, color="#f85149")
                            return

            if self._attachment_exists(node):
                return

            bus = self.find_bus_for_point(node)

            if bus:
                join = BusJoinItem(node, spec=deepcopy(self._joint_spec))
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
                from copy import deepcopy
                line = BusLineItem(p0, p1, spec=deepcopy(self._bus_spec))
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
        if enabled:
            self._load_mode = False
            self._join_mode = False
            self._source_mode = False
            self._delete_mode = False
        else:
            self._set_snap_marker(None)

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


    def apply_thermal_results(self, graph_obj, graph_result, Tmin=None, Tmax=None):

        edge_rows = list(graph_result.get("edges", []))

        temps = [
            float(e.get("T_C", 0.0))
            for e in edge_rows
        ]

        local_Tmin = min(temps) if temps else 40.0
        local_Tmax = max(temps) if temps else 140.0

        if Tmin is None:
            Tmin = local_Tmin
        if Tmax is None:
            Tmax = local_Tmax

        if hasattr(self, "legend"):
            self.legend.set_temperature_range(Tmin, Tmax)

        # clear previous segment results / UI caches
        for e in graph_obj.edges.values():
            if e.ui_item and hasattr(e.ui_item, "thermal_results"):
                e.ui_item.thermal_results.clear()
            if e.ui_item is not None:
                e.ui_item._thermal_segments = []

        air_payload = graph_result.get("T_air_C", 40.0)
        if isinstance(air_payload, dict):
            try:
                ambient_C = float(sum(float(v) for v in air_payload.values()) / max(len(air_payload), 1))
            except Exception:
                ambient_C = 40.0
        else:
            ambient_C = float(air_payload)

        line_samples = {}
        joint_numbers_by_edge = {}
        for idx, join in enumerate(getattr(graph_obj, "joins", []) or [], start=1):
            joint_numbers_by_edge[int(join.edge_id)] = idx
            ui_join = getattr(join, "ui_item", None)
            if ui_join is not None and hasattr(ui_join, "set_joint_number"):
                ui_join.set_joint_number(idx)

        for e in edge_rows:

            edge_id = e.get("edge_id")
            edge_obj = graph_obj.edges.get(edge_id)

            if edge_obj is None:
                continue

            # --- inject missing physics + geometry ---
            e["ambient_C"] = ambient_C
            e["gap_to_wall_mm"] = float(
                getattr(edge_obj, "gap_to_wall_mm", 50.0)
            )
            e["orientation_to_wall"] = str(
                getattr(edge_obj, "orientation_to_wall", "width")
            )
            e["P_gen_W"] = float(e.get("P_gen_W", 0.0))

            T = float(e.get("T_C", 0.0))
            color = temperature_to_color(T, Tmin, Tmax)

            if edge_obj.ui_item is not None:
                line = edge_obj.ui_item

                if not hasattr(line, "thermal_results"):
                    line.thermal_results = []

                line.thermal_results.append(e)
                e["ui_item"] = line

                line_samples.setdefault(line, []).append((edge_obj, T))

            if edge_obj.ui_join_item is not None:
                join = edge_obj.ui_join_item
                if edge_id in joint_numbers_by_edge:
                    e["joint_number"] = joint_numbers_by_edge[edge_id]
                join.temperature_C = T
                join.thermal_result = e

                join.setBrush(QBrush(color))
                join.setPen(QPen(color.darker(150), 2))
                join.update()

        # ---------- APPLY CONTINUOUS FIELDS ----------
        for line, entries in line_samples.items():

            line_geom = line.line()
            p0 = line.mapToScene(line_geom.p1())
            p1 = line.mapToScene(line_geom.p2())

            samples_by_key = {}

            for edge_obj, T in entries:
                u_node = graph_obj.nodes[edge_obj.u].p
                v_node = graph_obj.nodes[edge_obj.v].p

                su, _ = project_point_to_segment(u_node, p0, p1)
                sv, _ = project_point_to_segment(v_node, p0, p1)

                for s in (su, sv):
                    s = max(0.0, min(1.0, float(s)))
                    key = round(s, 6)
                    samples_by_key.setdefault(key, []).append(float(T))

            segs = sorted(
                (float(k), sum(vals) / len(vals))
                for k, vals in samples_by_key.items()
            )

            if len(segs) == 1:
                # Force both ends so hover interpolation remains intuitive.
                only_T = segs[0][1]
                segs = [(0.0, only_T), (1.0, only_T)]

            # store full segment field for UI usage
            line._thermal_segments = segs

            hottest_T = max(T for _, T in segs) if segs else 0.0
            hottest_color = temperature_to_color(hottest_T, Tmin, Tmax)

            line._Tmin = Tmin
            line._Tmax = Tmax

            line.set_temperature_segments(segs)

            line.temperature_C = hottest_T
            line.update_glow(hottest_color)
            line.update()




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
                item.temperature_C = None
                item.update_glow(Qt.transparent)
                item.update()

            elif isinstance(item, BusJoinItem):
                item.temperature_C = None
                item.setBrush(QBrush(QColor("#f85149")))
                item.setPen(QPen(QColor("#a40e26"), 2))
                item.update()

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

    def find_existing_source(self) -> Optional[BusSourceItem]:
        for item in self.scene().items():
            if isinstance(item, BusSourceItem):
                return item
        return None

    def set_default_joint_spec(self, spec: BusbarJointSpec) -> None:
        self._joint_spec = deepcopy(spec)

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
