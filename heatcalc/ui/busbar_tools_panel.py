from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout,
    QPushButton, QSpinBox, QLabel, QButtonGroup
)

from .bus_items import BusSpecUI
from ..core.bus_current_solver import filter_graph_to_source_component


class BusbarToolsPanel(QWidget):

    def __init__(self, designer_view, parent=None):
        super().__init__(parent)
        self._draw_bus_mode = False
        self._load_mode = False
        self._join_mode = False
        self.view = designer_view
        self.swb = parent

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
        from heatcalc.core.bus_current_solver import solve_currents, filter_graph_to_source_component
        from heatcalc.core.tier_coupled_solver import calc_tier_iec60890_coupled
        from heatcalc.ui.tier_item import TierItem, tier_effective_inlet_area_cm2

        swb = self.swb
        scene = self.view.scene()
        tiers = swb.get_tiers()

        print("\n==============================")
        print("BUSBAR TIER COUPLED SOLVE")
        print("==============================")

        # -------------------------------------------------
        # Helper: resolve an edge's actual TierItem
        # -------------------------------------------------

        def resolve_edge_tier(edge):
            obj = getattr(edge, "tier", None)

            while obj is not None:
                if isinstance(obj, TierItem):
                    return obj
                if hasattr(obj, "parentItem"):
                    obj = obj.parentItem()
                else:
                    break

            return None

        # -------------------------------------------------
        # Project meta
        # -------------------------------------------------

        ambient = float(getattr(swb.project.meta, "ambient_C", 40.0))
        wall = bool(swb.cb_wall.isChecked())
        altitude_m = float(getattr(swb.project.meta, "altitude_m", 0.0))
        ip_rating_n = int(getattr(swb.project.meta, "ip_rating_n", 0))

        solar_dt = (
            float(getattr(swb.project.meta, "solar_delta_K", 0.0))
            if getattr(swb.project.meta, "solar_enabled", False)
            else 0.0
        )

        louvre_def = swb._get_louvre_definition()

        # -------------------------------------------------
        # Extract graph + electrical solve once
        # -------------------------------------------------

        graph = extract_graph(scene)

        print(f"Graph edges: {len(graph.edges)}")
        print(f"Graph loads: {len(graph.loads)}")
        print(f"Graph joins: {len(graph.joins)}")

        filter_graph_to_source_component(graph)
        solve_currents(graph)

        # -------------------------------------------------
        # Solve each tier
        # -------------------------------------------------

        for t in tiers:

            print(f"\n--- Tier {id(t)} ---")

            try:
                inlet_area_cm2 = 0.0

                if louvre_def:
                    inlet_area_cm2 = tier_effective_inlet_area_cm2(
                        tier=t,
                        louvre_def=louvre_def,
                        ip_rating_n=ip_rating_n,
                    )

                # -------------------------------------------------
                # Extract edges belonging to this tier
                # -------------------------------------------------

                tier_edges = [
                    e for e in graph.edges.values()
                    if resolve_edge_tier(e) is t
                ]

                print(f"Tier edge count: {len(tier_edges)}")

                if not tier_edges:
                    print("No busbars in this tier")
                    continue

                # -------------------------------------------------
                # Build a small graph object for this tier
                # -------------------------------------------------

                class TierGraph:
                    pass

                tg = TierGraph()
                tg.name = f"Tier-{id(t)}"
                tg.edges = {e.id: e for e in tier_edges}
                tg.nodes = graph.nodes
                tg.loads = graph.loads
                tg.joins = graph.joins
                tg.use_air_temp = "top"

                # -------------------------------------------------
                # Coupled solve
                # -------------------------------------------------

                res = calc_tier_iec60890_coupled(
                    tier=t,
                    tiers=tiers,
                    graphs=[tg],
                    wall_mounted=wall,
                    inlet_area_cm2=inlet_area_cm2,
                    ambient_C=ambient,
                    altitude_m=altitude_m,
                    ip_rating_n=ip_rating_n,
                    solar_delta_K=solar_dt,
                    debug=False,
                )

                # -------------------------------------------------
                # Summary
                # -------------------------------------------------

                print(f"Air mid  : {res['T_mid']:.2f} C")
                print(f"Air top  : {res['T_top']:.2f} C")

                coupling = res["coupling"]

                print(f"P_base   : {coupling['P_base_W']:.2f} W")
                print(f"P_busbar : {coupling['P_bus_W']:.2f} W")
                print(f"Conv     : {coupling['converged']}")
                print(f"Iter     : {coupling['iterations']}")

                # -------------------------------------------------
                # Edge results
                # -------------------------------------------------

                print("\nEdge temperatures")
                print("--------------------------------------------")

                g = res["graphs"][0]

                for e in g["edges"]:
                    kind = "JOINT" if e["is_joint"] else "BUS"

                    dT = e["T_C"] - ambient

                    print(
                        f"{e['edge_id']:3d} | "
                        f"{kind:5s} | "
                        f"I={e['I_A']:8.1f} A | "
                        f"T={e['T_C']:6.2f} C | "
                        f"dT={dT:6.2f} K | "
                        f"P={e['P_gen_W']:7.2f} W"
                    )

                # store on tier
                t.live_thermal = res

            except Exception as ex:
                print(f"Tier solve failed: {ex}")
                t.live_thermal = None

            try:
                t.update()
            except Exception:
                pass