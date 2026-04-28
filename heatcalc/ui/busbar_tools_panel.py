from types import SimpleNamespace
from typing import Literal

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout,
    QPushButton, QSpinBox, QButtonGroup,
    QDoubleSpinBox, QComboBox, QGroupBox,
    QToolButton, QTableWidget, QLabel
)
from .collapsible_group_box import CollapsibleGroupBox
from .bus_items import BusSpecUI, BusLineItem, BusJoinItem
from ..core.models import BusbarJointSpec
from ..utils.resources import get_resource_path
from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
from PyQt5.QtCore import Qt


class HoverImageLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setScaledContents(True)
        self.setStyleSheet("border: 2px solid #555; background-color: white;")
        self.hide()

    def show_at_cursor(self, pixmap, scale_factor=1):
        if pixmap.isNull():
            return
        
        # Scale the pixmap
        new_size = pixmap.size() * scale_factor
        scaled_pixmap = pixmap.scaled(new_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled_pixmap)
        self.adjustSize()

        # Position near cursor, but to the left to satisfy user request
        # Subtract the label's width and a small offset to appear to the left
        cursor_pos = QtGui.QCursor.pos()
        self.move(cursor_pos.x() - self.width() - 20, cursor_pos.y() + 20)
        self.show()


class HoverImageButton(QToolButton):
    def __init__(self, icon_path, parent=None):
        super().__init__(parent)
        self.icon_path = icon_path
        self._hover_label = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)  # 0.5 sec delay
        self._timer.timeout.connect(self._show_hover)

    def _show_hover(self):
        if not self.icon_path:
            return
        if not self._hover_label:
            self._hover_label = HoverImageLabel()
        
        pixmap = QtGui.QPixmap(self.icon_path)
        self._hover_label.show_at_cursor(pixmap)

    def enterEvent(self, event):
        if self.icon_path:
            self._timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._timer.stop()
        if self._hover_label:
            self._hover_label.hide()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        # Update position if mouse moves within the button (keep it to the left)
        if self._hover_label and self._hover_label.isVisible():
            cursor_pos = QtGui.QCursor.pos()
            self._hover_label.move(cursor_pos.x() - self._hover_label.width() - 20, cursor_pos.y() + 20)
        super().mouseMoveEvent(event)

    def hideEvent(self, event):
        # Ensure label is hidden if the button itself is hidden (e.g. tab switch)
        if self._hover_label:
            self._hover_label.hide()
        super().hideEvent(event)


class BusbarToolsPanel(QWidget):

    def __init__(self, designer_view, parent=None):
        super().__init__(parent)

        self._draw_bus_mode = False
        self._load_mode = False
        self._join_mode = False

        self.view = designer_view
        self.swb = parent

        self.setMinimumWidth(300) # approximately 1.5x larger, assuming original was around 200

        layout = QVBoxLayout(self)

        # -------------------------------------------------
        # BUS DEFAULTS
        # -------------------------------------------------

        gb_bus = CollapsibleGroupBox("Bus Bar Geometry")
        form_bus = QFormLayout()

        self.width = QSpinBox()
        self.width.setRange(10, 500)
        self.width.setValue(100)

        self.thickness = QSpinBox()
        self.thickness.setRange(2, 50)
        self.thickness.setValue(10)

        self.bars = QSpinBox()
        self.bars.setRange(1, 10)
        self.bars.setValue(1)

        self.gap_to_wall = QDoubleSpinBox()
        self.gap_to_wall.setRange(1, 1000)
        self.gap_to_wall.setValue(50)
        self.gap_to_wall.setSuffix(" mm")

        form_bus.addRow("Width (mm)", self.width)
        form_bus.addRow("Thickness (mm)", self.thickness)
        form_bus.addRow("Bars", self.bars)
        form_bus.addRow("Gap to wall", self.gap_to_wall)

        # -------------------------------------------------
        # ORIENTATION TO WALL
        # -------------------------------------------------

        orient_box = QGroupBox("Face to wall")
        orient_layout = QtWidgets.QHBoxLayout(orient_box)

        self.orient_group = QButtonGroup(self)
        self.orient_group.setExclusive(True)

        def _make_orient_btn(group, idx: int, label: str, icon_name: str):
            icon_path = str(get_resource_path(f"heatcalc/assets/{icon_name}"))
            b = HoverImageButton(icon_path)
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)

            icon = QtGui.QIcon(icon_path)

            if icon.isNull():
                pm = QtGui.QPixmap(64, 40)
                pm.fill(QtGui.QColor("#ddd"))
                painter = QtGui.QPainter(pm)
                painter.drawText(pm.rect(), Qt.AlignCenter, label)
                painter.end()
                icon = QtGui.QIcon(pm)

            b.setIcon(icon)
            b.setIconSize(QtCore.QSize(75, 75))
            b.setText(label)

            group.addButton(b, idx)
            return b

        self.btn_face_width = _make_orient_btn(self.orient_group, 0, "Broad", "Broad_face_to_wall.png")
        self.btn_face_thickness = _make_orient_btn(self.orient_group, 1, "Edge", "Narrow_face_to_wall.png")

        orient_layout.addWidget(self.btn_face_width)
        orient_layout.addWidget(self.btn_face_thickness)

        self.btn_face_width.setChecked(True)
        form_bus.addRow(orient_box)

        # -------------------------------------------------
        # FACE TO FACE (Parallel Bars)
        # -------------------------------------------------

        face_box = QGroupBox("Parallel Bar Arrangement")
        face_layout = QtWidgets.QHBoxLayout(face_box)

        self.face_to_face_group = QButtonGroup(self)
        self.face_to_face_group.setExclusive(True)

        self.btn_face_type1 = _make_orient_btn(self.face_to_face_group, 0, "Type 1", "Type_1_Parallel.png")
        self.btn_face_type2 = _make_orient_btn(self.face_to_face_group, 1, "Type 2", "Type_2_Parallel.png")

        face_layout.addWidget(self.btn_face_type1)
        face_layout.addWidget(self.btn_face_type2)

        self.btn_face_type1.setChecked(True)
        form_bus.addRow(face_box)

        gb_bus.setLayout(form_bus)
        layout.addWidget(gb_bus)


        # -------------------------------------------------
        # JOINT DEFAULTS
        # -------------------------------------------------

        gb_joint = CollapsibleGroupBox("Joint")
        form_joint = QFormLayout()

        # Installation type: 3 image tiles (mutually exclusive)
        inst_box = QGroupBox("Installation type")
        inst_lay = QtWidgets.QHBoxLayout(inst_box)
        self.inst_group = QButtonGroup(self)
        self.inst_group.setExclusive(True)

        def _make_inst_btn(idx: int, label: str, icon_name: str):
            icon_path = str(get_resource_path(f"heatcalc/assets/{icon_name}"))
            b = HoverImageButton(icon_path)
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            icon = QtGui.QIcon(icon_path)
            if icon.isNull():
                pm = QtGui.QPixmap(64, 40)
                pm.fill(QtGui.QColor("#ddd"))
                painter = QtGui.QPainter(pm)
                painter.drawText(pm.rect(), Qt.AlignCenter, str(idx))
                painter.end()
                icon = QtGui.QIcon(pm)
            b.setIcon(icon)
            b.setIconSize(QtCore.QSize(72, 48))
            b.setText(label)
            self.inst_group.addButton(b, idx)
            inst_lay.addWidget(b)
            return b

        self.btn_inst1 = _make_inst_btn(0, "Bolted Overlap", "Joint_Type_1_BoltedOverlap.png")
        self.btn_inst2 = _make_inst_btn(1, "Clamped Edge", "Joint_Type_2_Clamped.png")
        self.btn_inst3 = _make_inst_btn(2, "Sandwich", "Joint_Type_3_Sandwich.png")
        self.btn_inst1.setChecked(True)

        self.joint_type = QComboBox()
        self.joint_type.addItems(["bolted_overlap", "clamped_edge", "sandwich_joint"])
        # Hide the actual joint_type combo box as we now use installation_type
        self.joint_type.setVisible(False)

        self.overlap_m = QDoubleSpinBox()
        self.overlap_m.setRange(0.001, 1.0)
        self.overlap_m.setDecimals(3)
        self.overlap_m.setValue(0.05)
        self.overlap_m.setSuffix(" m")

        self.bolt_count = QSpinBox()
        self.bolt_count.setRange(1, 20)
        self.bolt_count.setValue(4)

        self.bolt_dia = QDoubleSpinBox()
        self.bolt_dia.setRange(2, 40)
        self.bolt_dia.setValue(10)
        self.bolt_dia.setSuffix(" mm")

        self.torque = QDoubleSpinBox()
        self.torque.setRange(1, 500)
        self.torque.setDecimals(1)
        self.torque.setValue(45.0)
        self.torque.setSuffix(" Nm")

        self.nut_factor = QDoubleSpinBox()
        self.nut_factor.setRange(0.05, 1.0)
        self.nut_factor.setDecimals(3)
        self.nut_factor.setValue(0.20)

        self.h_contact = QDoubleSpinBox()
        self.h_contact.setRange(10, 100000)
        self.h_contact.setValue(5000)
        self.h_contact.setSuffix(" W/m²K")

        form_joint.addRow(inst_box)
        form_joint.addRow("Overlap", self.overlap_m)
        form_joint.addRow("Bolt count", self.bolt_count)
        form_joint.addRow("Bolt dia", self.bolt_dia)
        form_joint.addRow("Torque", self.torque)
        form_joint.addRow("Nut factor", self.nut_factor)
        form_joint.addRow("h contact", self.h_contact)
        gb_joint.setLayout(form_joint)
        layout.addWidget(gb_joint)

        # -------------------------------------------------
        # DRAWING TOOLS
        # -------------------------------------------------

        gb_draw = CollapsibleGroupBox("Drawing tools")
        draw_layout = QVBoxLayout()

        self.btn_draw = QPushButton("Draw bus")
        self.btn_draw.setCheckable(True)

        self.btn_source = QPushButton("Add source")
        self.btn_source.setCheckable(True)
        self.btn_source.toggled.connect(self.view.set_source_mode)

        self.btn_load = QPushButton("Add load")
        self.btn_load.setCheckable(True)
        self.btn_load.toggled.connect(self.view.set_load_mode)

        self.btn_join = QPushButton("Add joint")
        self.btn_join.setCheckable(True)
        self.btn_join.toggled.connect(self.view.set_join_mode)

        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setCheckable(True)
        self.btn_delete.toggled.connect(self.view.set_delete_mode)

        self.group = QButtonGroup(self)
        self.group.setExclusive(False)

        self.group.addButton(self.btn_draw)
        self.group.addButton(self.btn_source)
        self.group.addButton(self.btn_load)
        self.group.addButton(self.btn_join)
        self.group.addButton(self.btn_delete)

        self.btn_draw.clicked.connect(lambda: self._exclusive_toggle(self.btn_draw))
        self.btn_source.clicked.connect(lambda: self._exclusive_toggle(self.btn_source))
        self.btn_load.clicked.connect(lambda: self._exclusive_toggle(self.btn_load))
        self.btn_join.clicked.connect(lambda: self._exclusive_toggle(self.btn_join))
        self.btn_delete.clicked.connect(lambda: self._exclusive_toggle(self.btn_delete))

        draw_layout.addWidget(self.btn_draw)
        draw_layout.addWidget(self.btn_source)
        draw_layout.addWidget(self.btn_load)
        draw_layout.addWidget(self.btn_join)
        draw_layout.addWidget(self.btn_delete)
        gb_draw.setLayout(draw_layout)
        layout.addWidget(gb_draw)

        layout.addStretch()

        # -------------------------------------------------
        # SOLVE BUTTON
        # -------------------------------------------------

        self.btn_solve = QPushButton("Solve network")
        self.btn_solve.clicked.connect(self.solve_network)
        layout.addWidget(self.btn_solve)

        self.btn_draw.toggled.connect(self.view.set_bus_draw_mode)

        # bus spec bindings
        self.width.valueChanged.connect(self.update_spec)
        self.thickness.valueChanged.connect(self.update_spec)
        self.bars.valueChanged.connect(self.update_spec)
        self.gap_to_wall.valueChanged.connect(self.update_spec)
        self.orient_group.buttonClicked.connect(self.update_spec)
        self.face_to_face_group.buttonClicked.connect(self.update_spec)

        # joint spec bindings
        self.inst_group.buttonClicked.connect(self._on_installation_type_changed)
        self.joint_type.currentTextChanged.connect(self.update_joint_spec)
        self.overlap_m.valueChanged.connect(self.update_joint_spec)
        self.bolt_count.valueChanged.connect(self.update_joint_spec)
        self.bolt_dia.valueChanged.connect(self.update_joint_spec)
        self.torque.valueChanged.connect(self.update_joint_spec)
        self.nut_factor.valueChanged.connect(self.update_joint_spec)
        self.h_contact.valueChanged.connect(self.update_joint_spec)

        self.update_spec()
        self._on_installation_type_changed()  # sets initial visibility and updates spec
        self._init_compliance_table()

    # -------------------------------------------------

    def _exclusive_toggle(self, btn):
        if btn.isChecked():
            for b in self.group.buttons():
                if b != btn:
                    b.setChecked(False)

    # -------------------------------------------------

    from typing import Literal

    def update_spec(self):

        orientation: Literal["width", "thickness"] = (
            "width" if self.orient_group.checkedId() == 0 else "thickness"
        )
        face_to_face: Literal["width", "thickness"] = (
            "width" if self.face_to_face_group.checkedId() == 0 else "thickness"
        )

        spec = BusSpecUI(
            width_mm=self.width.value(),
            thickness_mm=self.thickness.value(),
            bars_in_parallel=self.bars.value(),
            gap_to_wall_mm=self.gap_to_wall.value(),
            orientation_to_wall=orientation,
            face_to_face_dim=face_to_face,
        )

        self.view.set_default_bus_spec(spec)

    # -------------------------------------------------

    def _on_installation_type_changed(self):
        idx = self.inst_group.checkedId()
        if idx < 0:
            idx = 0

        # Update the hidden joint_type combo box for backward compatibility
        if idx == 0:  # Type 1
            self.joint_type.setCurrentText("bolted_overlap")
        elif idx == 1:  # Type 2
            self.joint_type.setCurrentText("clamped_edge")
        elif idx == 2:  # Type 3
            self.joint_type.setCurrentText("sandwich_joint")
        else:
            pass

        self._update_joint_fields_visibility(idx)
        self.update_joint_spec()

    def _update_joint_fields_visibility(self, type_idx):
        """Enable/disable fields based on the selected installation type."""

        is_type1 = (type_idx == 0)
        is_type2 = (type_idx == 1)
        is_type3 = (type_idx == 2)

        # Type 1 (Bolted Overlap): All options available.
        # Selecting Type 1, should be effectively bolted overlap, and all of the options should be available.
        # Type 2 (Clamped Edge): Only required inputs are Torque or pertinent values.
        # Type 3 (Sandwich): Same inputs as Type 2 (bolt count, dia, torque, nut factor, h contact). No overlap.

        self.overlap_m.setEnabled(is_type1)
        self.bolt_count.setEnabled(is_type1 or is_type2 or is_type3)
        self.bolt_dia.setEnabled(is_type1 or is_type2 or is_type3)
        self.torque.setEnabled(is_type1 or is_type2 or is_type3)
        self.nut_factor.setEnabled(is_type1 or is_type2 or is_type3)
        self.h_contact.setEnabled(is_type1 or is_type2 or is_type3)

        # Grey out/disable labels as well for clarity if needed,
        # but QFormLayout's row labels are harder to access individually.
        # Setting the widget's enabled state usually greys out the associated labels in most QStyles.

    # -------------------------------------------------

    def update_joint_spec(self):

        spec = BusbarJointSpec(
            overlap_m=self.overlap_m.value(),
            bolt_count=self.bolt_count.value(),
            bolt_dia_mm=self.bolt_dia.value(),
            torque_Nm=self.torque.value(),
            joint_type=self.joint_type.currentText(),
            nut_factor=self.nut_factor.value(),
            h_contact=self.h_contact.value(),
            other_bar_width_mm=None,
            other_bar_thickness_mm=None,
            other_bar_count=None,
        )

        self.view.set_default_joint_spec(spec)

    # -------------------------------------------------
    def solve_network(self):
        result = self.swb.solve_all_thermal()
        if not result:
            return

        # -----------------------------------------
        # STORE RESULT (AUTHORITATIVE SOURCE)
        # -----------------------------------------
        self.last_result = result
        self.swb.last_solve_result = result  # <-- important for report layer

        # -----------------------------------------
        # EXISTING BEHAVIOUR
        # -----------------------------------------
        self._apply_results(result)
        self.update_compliance_table(result)


    def _apply_results(self, result: dict):
        """
        Apply solved thermal results to:
          - bus / joint graphics in DesignerView
          - thermal legend
          - tier overlay summaries (t.live_thermal)
        """
        if not result:
            return

        global_sol = result.get("global")
        graph = result.get("graph")
        tier_results = result.get("tiers", {})
        tier_edges = result.get("tier_edges", {})
        air_by_edge = result.get("air_by_edge", {})
        node_T = result.get("node_temps", {})

        if global_sol is None or graph is None or not tier_results:
            return

        # NEW: Clear previous thermal results from all bus items first
        for item in self.view.scene().items():
            if isinstance(item, (BusLineItem, BusJoinItem)):
                if isinstance(item, BusLineItem):
                    item.thermal_results = []
                else:
                    item.thermal_result = None

        edge_result_by_id = {e.edge_id: e for e in global_sol.edge_results}

        from collections import defaultdict

        bus_edge_results = defaultdict(list)

        ambient = float(getattr(self.swb.project.meta, "ambient_C", 40.0))


        for er in global_sol.edge_results:
            edge = graph.edges[er.edge_id]

            if edge.ui_item:
                er.ambient_C = float(air_by_edge.get(er.edge_id, ambient))
                bus_edge_results[edge.ui_item].append(er)

        # assign FULL results to each bus UI item
        for ui_item, results in bus_edge_results.items():
            ui_item.thermal_results = results

        # assign explicit joint results
        for er in global_sol.edge_results:
            edge = graph.edges[er.edge_id]
            if edge.is_joint and edge.ui_join_item:
                edge.ui_join_item.thermal_result = er

        for er in global_sol.edge_results:
            edge = graph.edges[er.edge_id]

            if edge.ui_item:
                edge.ui_item.thermal_edge = edge

        scene = self.view.scene()

        scene.thermal_graph = graph
        scene.node_temperatures = node_T

        # -------------------------------------------------
        # Clear previous disconnected highlighting
        # -------------------------------------------------
        scene = self.view.scene()
        for item in scene.items():
            if hasattr(item, "set_disconnected"):
                item.set_disconnected(False)

        # -------------------------------------------------
        # Global temperature range
        # -------------------------------------------------
        all_bus_temps = [
            float(e.T_C)
            for e in global_sol.edge_results
            if not e.is_joint
        ]

        if all_bus_temps:
            global_Tmin = min(all_bus_temps)
            global_Tmax = max(all_bus_temps)
        else:
            global_Tmin = ambient
            global_Tmax = ambient + 100.0

        if hasattr(self.view, "legend"):
            self.view.legend.set_temperature_range(global_Tmin, global_Tmax)

        # -------------------------------------------------
        # Push results tier-by-tier into DesignerView + overlays
        # -------------------------------------------------

        for t, res in tier_results.items():
            owned_edge_list = tier_edges.get(t, [])
            owned_edges = {e.id: e for e in owned_edge_list}

            g_serial = {
                "name": f"Tier-{id(t)}",
                "use_air_temp": "top",
                "T_air_C": float(res.get("T_top", ambient)),
                "edges": [],
            }

            for e in owned_edge_list:
                er = edge_result_by_id.get(e.id)
                if not er:
                    continue

                g_serial["edges"].append({
                    "edge_id": er.edge_id,
                    "T_C": float(er.T_C),
                    "I_A": float(er.I_A),
                    "length_m": float(er.length_m),
                    "is_joint": bool(er.is_joint),
                    "kind": "joint" if er.is_joint else "bus",
                    "P_gen_W": float(er.P_gen_W),
                    "P_conv_W": float(er.P_conv_W),
                    "P_rad_W": float(er.P_rad_W),
                    "P_cond_W": float(er.P_cond_W),
                    "residual_W": float(er.residual_W),
                    "ambient_C": float(air_by_edge.get(er.edge_id, ambient)),
                })

            class TierGraph:
                pass

            tg = TierGraph()
            tg.name = g_serial["name"]
            tg.edges = owned_edges
            tg.nodes = graph.nodes
            tg.loads = graph.loads
            tg.joins = graph.joins
            tg.use_air_temp = "top"

            self.view.apply_thermal_results(
                tg,
                g_serial,
                Tmin=global_Tmin,
                Tmax=global_Tmax,
            )

            coupling = res.get("coupling", {}) or {}

            P_base_W = float(coupling.get("P_base_W", getattr(t, "total_heat_w", 0.0)))
            P_bus_W = float(coupling.get("P_bus_W", 0.0))
            P_total_W = P_base_W + P_bus_W

            edge_temps = [
                float(edge_result_by_id[e.id].T_C)
                for e in owned_edge_list
                if e.id in edge_result_by_id
            ]

            if edge_temps:
                max_graph_T = max(edge_temps)
                min_graph_T = min(edge_temps)
            else:
                max_graph_T = ambient
                min_graph_T = ambient

            lt = dict(res)
            lt["P_base_W"] = P_base_W
            lt["P_bus_W"] = P_bus_W
            lt["P_total_W"] = P_total_W
            lt["max_graph_T_C"] = max_graph_T
            lt["min_graph_T_C"] = min_graph_T
            lt["solver_converged"] = bool(global_sol.converged)
            lt["solver_iterations"] = int(global_sol.iterations)
            lt["limit_C"] = float(getattr(t, "effective_max_temp_C", lambda: 70.0)())

            t.live_thermal = lt

            try:
                t.update()
            except Exception:
                pass

    def _init_compliance_table(self):
        table = QTableWidget()
        table.setColumnCount(5)

        table.setHorizontalHeaderLabels([
            "Tier",
            "Built-in",
            "Terminals",
            "Enclosure\n(Top / Hotspot)",
            "Busbars"
        ])

        self.compliance_table = table
        self.layout().addWidget(table)

    def update_compliance_table(self, result: dict):
        from heatcalc.core.compliance_61439 import evaluate_tier_compliance

        if not result:
            return

        global_sol = result.get("global")
        tier_results = result.get("tiers", {})
        ambient = float(self.swb.project.meta.ambient_C)

        table = self.compliance_table
        table.setRowCount(len(tier_results))

        def set_cell(row, col, ok, text, tooltip=None):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)

            if ok:
                item.setBackground(Qt.green)
            else:
                item.setBackground(Qt.red)

            if tooltip:
                item.setToolTip(tooltip)

            table.setItem(row, col, item)

        for row, (t, res) in enumerate(tier_results.items()):
            comp = evaluate_tier_compliance(
                t,
                global_sol,
                res,
                ambient,
                result["tier_edges"],
                result["graph"]
            )
            # Tier label
            table.setItem(row, 0, QTableWidgetItem(comp.tier_id))

            # Built-in
            set_cell(
                row, 1,
                comp.built_in_ok,
                f"{comp.built_in_max_T:.1f}°C",
                "\n".join(comp.notes)
            )

            # Terminals
            term_text = f"{comp.terminals_max_T:.1f}°C" if comp.terminals_max_T else "-"
            set_cell(
                row, 2,
                comp.terminals_ok,
                term_text
            )

            # Enclosure (top + hotspot)
            enc_text = (
                f"T:{comp.enclosure_surface_T_top_side:.1f}°C\n"
                f"H:{comp.enclosure_surface_T_hotspot:.1f}°C"
            )

            set_cell(
                row, 3,
                comp.enclosure_ok,
                enc_text
            )

            # Busbars
            set_cell(
                row, 4,
                comp.busbar_ok,
                f"{comp.busbar_max_T:.1f}°C"
            )

        table.resizeColumnsToContents()