from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout,
    QPushButton, QSpinBox, QLabel, QButtonGroup,
    QDoubleSpinBox, QComboBox
)

from .bus_items import BusSpecUI
from ..core.models import BusbarJointSpec


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

        # -------------------------------------------------
        # BUS DEFAULTS
        # -------------------------------------------------

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

        # -------------------------------------------------
        # JOINT DEFAULTS
        # -------------------------------------------------

        self.joint_type = QComboBox()
        self.joint_type.addItems(["bolted_overlap", "clamped_edge"])

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

        form.addRow("Joint type", self.joint_type)
        form.addRow("Overlap", self.overlap_m)
        form.addRow("Bolt count", self.bolt_count)
        form.addRow("Bolt dia", self.bolt_dia)
        form.addRow("Torque", self.torque)
        form.addRow("Nut factor", self.nut_factor)
        form.addRow("h contact", self.h_contact)

        layout.addLayout(form)

        # -------------------------------------------------
        # BUTTONS
        # -------------------------------------------------

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

        self.btn_solve = QPushButton("Solve network")
        self.btn_solve.clicked.connect(self.solve_network)

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

        layout.addWidget(self.btn_draw)
        layout.addWidget(self.btn_source)
        layout.addWidget(self.btn_load)
        layout.addWidget(self.btn_join)
        layout.addWidget(self.btn_delete)
        layout.addWidget(self.btn_solve)

        layout.addStretch()

        self.btn_draw.toggled.connect(self.view.set_bus_draw_mode)

        # bus spec bindings
        self.width.valueChanged.connect(self.update_spec)
        self.thickness.valueChanged.connect(self.update_spec)
        self.bars.valueChanged.connect(self.update_spec)

        # joint spec bindings
        self.joint_type.currentTextChanged.connect(self.update_joint_spec)
        self.overlap_m.valueChanged.connect(self.update_joint_spec)
        self.bolt_count.valueChanged.connect(self.update_joint_spec)
        self.bolt_dia.valueChanged.connect(self.update_joint_spec)
        self.torque.valueChanged.connect(self.update_joint_spec)
        self.nut_factor.valueChanged.connect(self.update_joint_spec)
        self.h_contact.valueChanged.connect(self.update_joint_spec)

        self.update_spec()
        self.update_joint_spec()

    # -------------------------------------------------

    def _exclusive_toggle(self, btn):
        if btn.isChecked():
            for b in self.group.buttons():
                if b != btn:
                    b.setChecked(False)

    # -------------------------------------------------

    def update_spec(self):

        spec = BusSpecUI(
            width_mm=self.width.value(),
            thickness_mm=self.thickness.value(),
            bars_in_parallel=self.bars.value(),
        )

        self.view.set_default_bus_spec(spec)

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
        )

        self.view.set_default_joint_spec(spec)

    # -------------------------------------------------

    def solve_network(self):

        from collections import defaultdict

        from heatcalc.core.bus_graph import extract_graph
        from heatcalc.core.bus_current_solver import (
            solve_currents,
            filter_graph_to_source_component,
            get_disconnected_items,
        )
        from heatcalc.core.iec60890_calc import calc_tier_iec60890
        from heatcalc.core.bus_thermal_solver import solve_thermal
        from heatcalc.ui.tier_item import TierItem, tier_effective_inlet_area_cm2
        from PyQt5.QtWidgets import QMessageBox

        swb = self.swb
        scene = self.view.scene()
        tiers = swb.get_tiers()

        print("\n==============================")
        print("BUSBAR GLOBAL-THERMAL SOLVE")
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
        self.view.clear_solver_overlay()

        for item in scene.items():
            if hasattr(item, "set_disconnected"):
                item.set_disconnected(False)

        if graph.source_node is None:
            QMessageBox.warning(self, "Solve Failed", "No source node detected in the network.")
            return

        disconnected_items = get_disconnected_items(graph)
        if disconnected_items:
            for item in disconnected_items:
                if hasattr(item, "set_disconnected"):
                    item.set_disconnected(True)

            QMessageBox.warning(
                self,
                "Disconnected Nodes",
                "Floating nodes detected! Every busline, load, and joint must be connected to the source.\n\n"
                "Disconnected items have been highlighted in red."
            )
            return

        print(f"Graph edges: {len(graph.edges)}")

        filter_graph_to_source_component(graph)
        solve_currents(graph)

        # -------------------------------------------------
        # Partition edges by owning tier
        # -------------------------------------------------

        tier_edges = {}
        edge_owner_tier = {}

        for t in tiers:
            owned = [e for e in graph.edges.values() if resolve_edge_tier(e) is t]
            tier_edges[t] = owned
            for e in owned:
                edge_owner_tier[e.id] = t

        # -------------------------------------------------
        # Global outer iteration:
        #   tier IEC60890 air solve
        #   -> one global copper thermal solve
        #   -> updated bus losses per tier
        # -------------------------------------------------

        max_iter = 30
        tol_T = 0.05
        tol_P = 0.5
        relax = 0.5

        P_bus_by_tier = {t: 0.0 for t in tiers}
        prev_T_top_by_tier = {t: float(ambient) for t in tiers}

        last_tier_res = {}
        last_air_by_edge = {}
        last_global_sol = None
        converged = False
        history = []

        for k in range(max_iter):

            # ---------------------------------------------
            # Solve each tier enclosure temperature using
            # current estimate of busbar watts in that tier
            # ---------------------------------------------
            tier_res = {}

            for t in tiers:
                inlet_area_cm2 = 0.0

                if louvre_def:
                    inlet_area_cm2 = tier_effective_inlet_area_cm2(
                        tier=t,
                        louvre_def=louvre_def,
                        ip_rating_n=ip_rating_n,
                    )

                P_base = float(getattr(t, "total_heat_w", 0.0))
                P_bus = float(P_bus_by_tier.get(t, 0.0))

                res = calc_tier_iec60890(
                    tier=t,
                    tiers=tiers,
                    wall_mounted=wall,
                    inlet_area_cm2=inlet_area_cm2,
                    ambient_C=ambient,
                    altitude_m=altitude_m,
                    ip_rating_n=ip_rating_n,
                    solar_delta_K=solar_dt,
                    P_override_W=P_base + P_bus,
                )
                res["ambient_C"] = float(ambient)
                tier_res[t] = res

            # ---------------------------------------------
            # Build per-edge air temperature map
            # ---------------------------------------------
            air_by_edge = {}

            for t in tiers:
                # keep same air reference behaviour you already had
                T_air_for_bus = float(tier_res[t]["T_top"])

                for e in tier_edges.get(t, []):
                    air_by_edge[e.id] = T_air_for_bus

            if not air_by_edge:
                QMessageBox.warning(self, "Solve Failed", "No bus edges found in the network.")
                return

            # ---------------------------------------------
            # Solve copper network globally
            # ---------------------------------------------
            global_sol = solve_thermal(
                graph=graph,
                air_temp_C=air_by_edge,
                debug=False,
            )

            edge_result_by_id = {e.edge_id: e for e in global_sol.edge_results}

            # ---------------------------------------------
            # Re-accumulate bus loss per tier
            # ---------------------------------------------
            P_bus_raw_by_tier = defaultdict(float)

            for edge_id, er in edge_result_by_id.items():
                t = edge_owner_tier.get(edge_id)
                if t is not None:
                    P_bus_raw_by_tier[t] += float(er.P_gen_W)

            P_bus_new_by_tier = {}
            for t in tiers:
                old = float(P_bus_by_tier.get(t, 0.0))
                raw = float(P_bus_raw_by_tier.get(t, 0.0))
                P_bus_new_by_tier[t] = (1.0 - relax) * old + relax * raw

            dT = max(
                abs(float(tier_res[t]["T_top"]) - float(prev_T_top_by_tier.get(t, ambient)))
                for t in tiers
            ) if tiers else 0.0

            dP = max(
                abs(float(P_bus_new_by_tier.get(t, 0.0)) - float(P_bus_by_tier.get(t, 0.0)))
                for t in tiers
            ) if tiers else 0.0

            history.append({
                "iter": k + 1,
                "max_dT_C": float(dT),
                "max_dP_W": float(dP),
                "P_bus_total_W": float(sum(P_bus_new_by_tier.values())),
            })

            last_tier_res = tier_res
            last_air_by_edge = air_by_edge
            last_global_sol = global_sol

            if dT < tol_T and dP < tol_P:
                converged = True
                P_bus_by_tier = P_bus_new_by_tier
                break

            prev_T_top_by_tier = {t: float(tier_res[t]["T_top"]) for t in tiers}
            P_bus_by_tier = P_bus_new_by_tier

        if last_global_sol is None:
            QMessageBox.warning(self, "Solve Failed", "Global thermal solve did not run.")
            return

        # -------------------------------------------------
        # Global temperature scale
        # -------------------------------------------------

        all_bus_temps = [
            float(er.T_C)
            for er in last_global_sol.edge_results
            if not er.is_joint
        ]

        if all_bus_temps:
            global_Tmin = min(all_bus_temps)
            global_Tmax = max(all_bus_temps)
        else:
            global_Tmin = float(ambient)
            global_Tmax = float(ambient + 100.0)

        self.view.legend.set_temperature_range(global_Tmin, global_Tmax)

        # -------------------------------------------------
        # Apply UI results tier-by-tier using GLOBAL edge temps
        # -------------------------------------------------

        edge_result_by_id = {e.edge_id: e for e in last_global_sol.edge_results}

        for t in tiers:

            owned_edges = tier_edges.get(t, [])

            class TierGraph:
                pass

            tg = TierGraph()
            tg.name = f"Tier-{id(t)}"
            tg.edges = {e.id: e for e in owned_edges}
            tg.nodes = graph.nodes
            tg.loads = graph.loads
            tg.joins = graph.joins
            tg.use_air_temp = "top"

            g_serial = {
                "name": tg.name,
                "use_air_temp": "top",
                "T_air_C": float(last_tier_res[t]["T_top"]),
                "P_loss_W": float(P_bus_by_tier.get(t, 0.0)),
                "max_T_C": max((float(edge_result_by_id[e.id].T_C) for e in owned_edges), default=ambient),
                "min_T_C": min((float(edge_result_by_id[e.id].T_C) for e in owned_edges), default=ambient),
                "solver_converged": bool(last_global_sol.converged),
                "solver_iterations": int(last_global_sol.iterations),
                "edges": [],
            }

            for e in owned_edges:
                er = edge_result_by_id[e.id]

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
                    "ambient_C": float(last_air_by_edge.get(er.edge_id, ambient)),
                })

            self.view.apply_thermal_results(
                tg,
                g_serial,
                Tmin=global_Tmin,
                Tmax=global_Tmax,
            )

            res = last_tier_res[t]
            res["graphs"] = [g_serial]
            res["coupling"] = {
                "converged": bool(converged),
                "iterations": len(history),
                "history": list(history),
                "P_base_W": float(getattr(t, "total_heat_w", 0.0)),
                "P_bus_W": float(P_bus_by_tier.get(t, 0.0)),
            }

            t.live_thermal = res

            print(f"\n--- Tier {id(t)} ---")
            print(f"Air mid  : {res['T_mid']:.2f} C")
            print(f"Air top  : {res['T_top']:.2f} C")
            print(f"P_base   : {res['coupling']['P_base_W']:.2f} W")
            print(f"P_busbar : {res['coupling']['P_bus_W']:.2f} W")
            print(f"Conv     : {res['coupling']['converged']}")
            print(f"Iter     : {res['coupling']['iterations']}")

            try:
                t.update()
            except Exception:
                pass