from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout,
    QPushButton, QSpinBox, QLabel, QButtonGroup
)

from .bus_items import BusSpecUI
from ..core.bus_current_resolver_ss import resolve_edge_currents
from ..core.bus_current_solver import filter_graph_to_source_component
from ..core.bus_graph_extractor_ss import extract_bus_graph_from_scene


class BusbarToolsPanel(QWidget):

    def __init__(self, designer_view, parent=None):
        super().__init__(parent)
        self._draw_bus_mode = False
        self._load_mode = False
        self._join_mode = False
        self.view = designer_view


        layout = QVBoxLayout(self)

        title = QLabel("Busbar tools")
        title.setStyleSheet("font-weight: bold")
        layout.addWidget(title)

        form = QFormLayout()

        self.width = QSpinBox()
        self.width.setRange(10, 500)
        self.width.setValue(100)

        self.thickness = QSpinBox()
        self.thickness.setRange(2, 50)
        self.thickness.setValue(10)

        self.bars = QSpinBox()
        self.bars.setRange(1, 10)
        self.bars.setValue(1)

        form.addRow("Width (mm)", self.width)
        form.addRow("Thickness (mm)", self.thickness)
        form.addRow("Bars", self.bars)

        layout.addLayout(form)

        self.btn_draw = QPushButton("Draw bus")
        self.btn_draw.setCheckable(True)

        self.btn_source = QPushButton("Add source")
        self.btn_source.setCheckable(True)
        self.btn_source.toggled.connect(self.view.set_source_mode)

        self.btn_solve = QPushButton("Solve network")
        self.btn_solve.clicked.connect(self.solve_network)

        self.btn_load = QPushButton("Add load")
        self.btn_load.setCheckable(True)
        self.btn_load.toggled.connect(self.view.set_load_mode)

        self.btn_join = QPushButton("Add joint")
        self.btn_join.setCheckable(True)
        self.btn_join.toggled.connect(self.view.set_join_mode)

        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self.delete_selected)

        self.group = QButtonGroup(self)
        self.group.setExclusive(False)

        self.group.addButton(self.btn_draw)
        self.group.addButton(self.btn_source)
        self.group.addButton(self.btn_load)
        self.group.addButton(self.btn_join)

        self.btn_draw.clicked.connect(lambda: self._exclusive_toggle(self.btn_draw))
        self.btn_source.clicked.connect(lambda: self._exclusive_toggle(self.btn_source))
        self.btn_load.clicked.connect(lambda: self._exclusive_toggle(self.btn_load))
        self.btn_join.clicked.connect(lambda: self._exclusive_toggle(self.btn_join))

        layout.addWidget(self.btn_draw)
        layout.addWidget(self.btn_source)
        layout.addWidget(self.btn_load)
        layout.addWidget(self.btn_join)
        layout.addWidget(self.btn_delete)
        layout.addWidget(self.btn_solve)

        layout.addStretch()

        self.btn_draw.toggled.connect(self.view.set_bus_draw_mode)

        self.width.valueChanged.connect(self.update_spec)
        self.thickness.valueChanged.connect(self.update_spec)
        self.bars.valueChanged.connect(self.update_spec)

        self.update_spec()

    def _exclusive_toggle(self, btn):

        if btn.isChecked():

            for b in self.group.buttons():
                if b != btn:
                    b.setChecked(False)

    def update_spec(self):

        spec = BusSpecUI(
            width_mm=self.width.value(),
            thickness_mm=self.thickness.value(),
            bars_in_parallel=self.bars.value(),
        )

        self.view.set_default_bus_spec(spec)

    def delete_selected(self):
        scene = self.view.scene()
        for item in scene.selectedItems():
            scene.removeItem(item)

    def set_load_mode(self, enabled: bool):
        self._load_mode = enabled
        if enabled:
            self._draw_bus_mode = False
            self._join_mode = False

    def set_join_mode(self, enabled: bool):
        self._join_mode = enabled
        if enabled:
            self._draw_bus_mode = False
            self._load_mode = False

    def solve_network(self):

        from heatcalc.core.bus_graph import extract_graph
        from heatcalc.core.bus_current_solver import solve_currents
        from heatcalc.core.bus_thermal_solver import solve_thermal

        scene = self.view.scene()

        print("\n==============================")
        print("BUSBAR NETWORK SOLVE")
        print("==============================")

        # -------------------------------------------------
        # Extract graph from scene
        # -------------------------------------------------

        graph = extract_graph(scene)
        print("\nGraph edges before/after filter:")
        for e in graph.edges.values():
            print(f"  edge {e.id}: u={e.u}, v={e.v}, L={e.length_m:.3f} m")

        print("\nGraph loads before/after filter:")
        for ld in graph.loads:
            print(f"  load at node {ld.node}: {ld.I_A:.2f} A")

        print(f"\nSource node: {graph.source_node}")
        print(f"\nNodes: {len(graph.nodes)}")
        print(f"Edges: {len(graph.edges)}")
        print(f"Loads: {len(graph.loads)}")

        # -------------------------------------------------
        # Electrical solve
        # -------------------------------------------------

        filter_graph_to_source_component(graph)
        solve_currents(graph)

        print("\nGraph edges before/after filter:")
        for e in graph.edges.values():
            print(f"  edge {e.id}: u={e.u}, v={e.v}, L={e.length_m:.3f} m")

        print("\nGraph loads before/after filter:")
        for ld in graph.loads:
            print(f"  load at node {ld.node}: {ld.I_A:.2f} A")

        print(f"\nSource node: {graph.source_node}")

        print("\nEdge currents:")

        for e in graph.edges.values():
            print(f"  Edge {e.id:3d}  I = {e.I_A:.2f} A")

        # -------------------------------------------------
        # Thermal solve
        # -------------------------------------------------

        air_temp = 35.0  # you can later use IEC60890 result

        T = solve_thermal(graph, air_temp_C=air_temp, debug=True)

        print("\nThermal results:")
        print("--------------------------")

        # -------------------------------------------------
        # Group temperatures by tier
        # -------------------------------------------------

        tier_results = {}

        for idx, edge in enumerate(graph.edges.values()):

            if idx >= len(T):
                print(f"Warning: no thermal result for edge {edge.id}")
                continue

            tier = edge.tier

            if tier not in tier_results:
                tier_results[tier] = []

            tier_results[tier].append((edge.id, T[idx]))
        # -------------------------------------------------
        # Print per tier
        # -------------------------------------------------

        for tier, results in tier_results.items():

            print(f"\nTier {id(tier)}")

            maxT = max(t for _, t in results)

            print(f"  Max temperature: {maxT:.2f} °C")

            for eid, temp in results:
                print(f"    Edge {eid:3d} : {temp:.2f} °C")