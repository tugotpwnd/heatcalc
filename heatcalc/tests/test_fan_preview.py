import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
import io
from contextlib import redirect_stdout, contextmanager
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from dataclasses import replace
from uuid import uuid4

from PyQt5.QtWidgets import QApplication, QDialog
from PyQt5.QtCore import QTimer, QPointF, QEventLoop
from PyQt5.QtGui import QFontDatabase, QFont

from heatcalc.core.models import Project
from heatcalc.core.fan_cooling import temperature_rise_for_airflow
from heatcalc.core.iec60890_calc import calc_tier_iec60890
from heatcalc.core.compliance_61439 import evaluate_derating
from heatcalc.ui.tier_item import TierItem, ComponentEntry
from heatcalc.ui.switchboard_tab import SwitchboardTab, GRID
from heatcalc.ui.collapsible_group_box import CollapsibleGroupBox
from heatcalc.ui.bus_items import BusLineItem, BusSourceItem, BusLoadItem, BusSpecUI


@contextmanager
def report_test_directory():
    # Normal inherited permissions also work under restricted Windows tokens.
    directory = Path(__file__).resolve().parent / f"report-qa-{uuid4().hex}"
    directory.mkdir()
    try:
        yield directory
    finally:
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()


class FanPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.path.exists("C:/Windows/Fonts/segoeui.ttf"):
            font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                cls.app.setFont(QFont(families[0], 9))

    def setUp(self):
        self.tier = TierItem("Test tier", w=GRID * 24, h=GRID * 72, depth_mm=500)
        self.tier.component_entries = [ComponentEntry(
            key="Drive", category="Drive", part_number="D100", description="Drive",
            heat_each_w=500, qty=2, max_temp_C=70, rated_current_A=100,
            derating_temp_start_C=40, derating_function="1 - (x - 40) * 0.01",
        )]

    def calculate(self, flow=0, **overrides):
        args = dict(tier=self.tier, tiers=[self.tier], wall_mounted=False,
                    inlet_area_cm2=0, ambient_C=40, altitude_m=0, ip_rating_n=2,
                    selected_airflow_m3h=flow)
        args.update(overrides)
        return calc_tier_iec60890(**args)

    def test_minimum_airflow_reaches_limit_and_oversizing_cools(self):
        natural = self.calculate()
        self.assertGreater(natural["airflow_m3h"], 0)
        at_min = self.calculate(natural["airflow_m3h"])
        larger = self.calculate(natural["airflow_m3h"] * 2)
        self.assertAlmostEqual(at_min["T_top"], 70, places=6)
        self.assertLess(larger["T_top"], at_min["T_top"])
        self.assertGreater(larger["T_top"], 40)
        self.assertLessEqual(larger["T_mid"], larger["T_top"])
        self.assertGreater(evaluate_derating(self.tier.component_entries[0], larger["T_top"]),
                           evaluate_derating(self.tier.component_entries[0], at_min["T_top"]))

    def test_altitude_solar_zero_heat_and_impossible_limit(self):
        self.assertGreater(self.calculate(200, altitude_m=2000)["T_top"],
                           self.calculate(200)["T_top"])
        self.assertAlmostEqual(self.calculate(200, solar_delta_K=10)["T_top"],
                               self.calculate(200)["T_top"] + 10)
        self.assertAlmostEqual(self.calculate(200, P_override_W=0)["T_top"], 40)
        blocked = self.calculate(10000, ambient_C=70)
        self.assertFalse(blocked["cooling_possible"])
        self.assertFalse(blocked["compliant_top"])
        self.assertGreater(blocked["T_top"], 70)

    def test_energy_balance_and_invalid_flow(self):
        rise = temperature_rise_for_airflow(power_W=1000, airflow_m3h=200,
                                            k=.2, c=1.4, x=.804)
        removed = (rise / (.2 * 1.4)) ** (1 / .804) + 1160 * 200 / 3600 * rise
        self.assertAlmostEqual(removed, 1000, places=6)
        for flow in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                self.calculate(flow)

    def test_fan_persistence_and_old_project_defaults(self):
        self.tier.selected_airflow_m3h = 350.5
        self.tier.selected_fan_name = "Filter fan"
        restored = TierItem.from_dict(self.tier.to_dict())
        self.assertEqual(restored.selected_airflow_m3h, 350.5)
        self.assertEqual(restored.selected_fan_name, "Filter fan")
        state = self.tier.to_dict()
        del state["selected_airflow_m3h"]
        del state["selected_fan_name"]
        self.assertEqual(TierItem.from_dict(state).selected_airflow_m3h, 0)

    def make_window(self):
        window = SwitchboardTab(Project())
        window.scene.addItem(self.tier)
        self.tier.setSelected(True)
        window.resize(1700, 950)
        window.show()
        window.right_tabs.setCurrentIndex(1)
        window.splitter.setSizes([500, 650, 550])
        self.app.processEvents()
        self.addCleanup(window.close)
        return window

    def test_preview_no_bus_solve_staleness_and_scrolling(self):
        window = self.make_window()
        result = window.solve_all_thermal(apply_to_ui=True)
        self.assertTrue(result["solver_converged"])
        panel = window.preview_panel
        self.assertEqual(panel.table.rowCount(), 1)
        expected = evaluate_derating(self.tier.component_entries[0], result["tiers"][self.tier]["T_top"])
        self.assertEqual(panel.table.item(0, 3).text(), f"{expected:.1f}")
        self.assertEqual(window.left_scroll.maximumWidth(), 1000)
        self.assertEqual(window.right_tabs.maximumWidth(), 700)
        self.assertGreater(window.left_scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(window.tbl.height(), 260)
        for group in window.left_scroll.findChildren(CollapsibleGroupBox):
            group.setChecked(False)
        self.app.processEvents()
        for group in window.left_scroll.findChildren(CollapsibleGroupBox):
            group.setChecked(True)
        self.app.processEvents()
        self.assertEqual(window.tbl.height(), 260)
        self.tier.component_entries[0].qty = 3
        panel.refresh()
        self.assertEqual(panel.table.rowCount(), 0)
        self.assertIn("Inputs changed", panel.status.text())
        self.assertFalse(panel.curve_button.isEnabled())

    def test_saved_fan_refresh_and_curve(self):
        window = self.make_window()
        panel = window.preview_panel
        panel.flow.setValue(300)
        panel.fan_name.setText("Test fan")
        panel.apply_fan()
        self.assertEqual(self.tier.selected_airflow_m3h, 300)
        result = panel.result["tiers"][self.tier]
        self.assertLess(result["T_top"], result["uncooled_T_top"])
        self.assertGreater(panel.table.rowCount(), 0)
        self.app.processEvents()
        # Open the actual chart, render it, then close the modal dialog.
        def close_chart():
            for dialog in panel.findChildren(QDialog):
                dialog.accept()
        QTimer.singleShot(500, close_chart)
        panel.show_curve()

    def test_report_derates_at_solved_temperature(self):
        from heatcalc.reports.simple_report import component_derating_table
        component = self.tier.component_entries[0]
        thermal = SimpleNamespace(T_top=60.0, max_C=70.0)
        component_derating_table(SimpleNamespace(components=[component]), thermal)
        self.assertEqual(component.derated_current_A, evaluate_derating(component, 60))
        self.assertNotEqual(component.derated_current_A, evaluate_derating(component, 70))

    def test_report_adapter_uses_saved_fan_result(self):
        from heatcalc.reports.export_api import export_project_report
        window = self.make_window()
        self.tier.selected_airflow_m3h = 300
        self.tier.selected_fan_name = "Report test fan"
        solved = window.solve_all_thermal(apply_to_ui=True)
        with patch("heatcalc.reports.export_api.export_simple_report") as export:
            export_project_report(window.project, window, None, Path("unused.pdf"), ambient_C=40)
        thermal = export.call_args.kwargs["tier_thermals"][0]
        self.assertEqual(thermal.selected_airflow_m3h, 300)
        self.assertEqual(thermal.selected_fan_name, "Report test fan")
        self.assertEqual(thermal.T_top, solved["tiers"][self.tier]["T_top"])

    def test_failed_solve_clears_previous_preview(self):
        window = self.make_window()
        self.assertIsNotNone(window.solve_all_thermal())
        bus = BusLineItem(QPointF(50, 100), QPointF(50, 500))
        bus.setParentItem(self.tier)  # incomplete network: no source or load
        with patch("PyQt5.QtWidgets.QMessageBox.warning"):
            self.assertIsNone(window.solve_all_thermal())
        self.assertIsNone(window.last_solve_result)
        self.assertIsNone(window.preview_panel.result)
        self.assertEqual(window.preview_panel.table.rowCount(), 0)

    def test_fan_recouples_busbar_loss_and_repeat_solve_is_stable(self):
        window = self.make_window()
        start, end = QPointF(50, 100), QPointF(50, 500)
        bus = BusLineItem(start, end, BusSpecUI(width_mm=50, thickness_mm=5))
        bus.setParentItem(self.tier)
        source, load = BusSourceItem(start), BusLoadItem(end, 800)
        source.setParentItem(bus)
        source.setPos(start)
        load.setParentItem(bus)
        load.setPos(end)
        with patch("PyQt5.QtWidgets.QMessageBox.warning") as warning, redirect_stdout(io.StringIO()):
            natural = window.solve_all_thermal(apply_to_ui=True)
            self.assertFalse(warning.called)
            self.assertTrue(natural["solver_converged"])
            trial = window.solve_all_thermal(airflow_overrides={self.tier: 300}, preview_only=True)
            self.assertIs(window.last_solve_result, natural)
            self.assertEqual(self.tier.selected_airflow_m3h, 0)
            self.assertLess(trial["global"].total_loss_W, natural["global"].total_loss_W)
            self.tier.selected_airflow_m3h = 300
            cooled = window.solve_all_thermal(apply_to_ui=True)
            repeated = window.solve_all_thermal(apply_to_ui=True)
        self.assertTrue(cooled["solver_converged"])
        self.assertAlmostEqual(cooled["global"].total_loss_W, trial["global"].total_loss_W, places=5)
        self.assertLess(cooled["global"].max_T_C, natural["global"].max_T_C)
        self.assertLess(cooled["global"].total_loss_W, natural["global"].total_loss_W)
        self.assertAlmostEqual(cooled["tiers"][self.tier]["T_top"],
                               repeated["tiers"][self.tier]["T_top"], places=5)
        self.assertAlmostEqual(cooled["tiers"][self.tier]["coupling"]["P_base_W"], 1000)
        panel = window.preview_panel
        bus.spec.gap_to_wall_mm += 10
        panel.refresh()
        self.assertIn("Inputs changed", panel.status.text())

    def make_fan_dialog(self):
        from heatcalc.ui.fan_sizing_dialog import FanSizingDialog
        window = self.make_window()
        window.solve_all_thermal(apply_to_ui=True)
        dialog = FanSizingDialog(window.preview_panel, self.tier)
        self.addCleanup(dialog.close)
        return window, dialog

    def test_plot_filters_missing_blank_and_invalid_curves(self):
        from heatcalc.core.fan_sizing import build_fan_sizing_data
        base = self.tier.component_entries[0]
        self.tier.component_entries.extend([
            replace(base, description="Missing", derating_function=None),
            replace(base, description="Blank", derating_function=" "),
            replace(base, description="Invalid", derating_function="not a formula"),
            replace(base, description="Not finite", derating_function="math.inf"),
            replace(base, description="No rating", rated_current_A=None),
        ])
        window = self.make_window()
        result = window.solve_all_thermal()
        data = build_fan_sizing_data(window, self.tier, result["tiers"][self.tier], 300)
        self.assertEqual(len(data.devices), 1)
        self.assertEqual(data.excluded_count, 5)

    def test_trial_operating_point_updates_without_saving(self):
        window, dialog = self.make_fan_dialog()
        original_result = window.last_solve_result
        original_graph = window._graph
        original_state = self.tier.to_dict()
        original_live = self.tier.live_thermal
        dialog.airflow.setValue(200)
        self.assertFalse(dialog.pdf_button.isEnabled())
        dialog.regenerate()
        self.assertEqual(dialog.data.airflow_m3h, 200)
        self.assertLess(dialog.data.result["T_top"], original_result["tiers"][self.tier]["T_top"])
        self.assertEqual(self.tier.to_dict(), original_state)
        self.assertIs(self.tier.live_thermal, original_live)
        self.assertIs(window.last_solve_result, original_result)
        self.assertIs(window._graph, original_graph)
        index = dialog.data.flows_m3h.index(200)
        self.assertAlmostEqual(dialog.data.tops_C[index], dialog.data.result["T_top"], places=6)
        annotations = [text.get_text() for ax in dialog.figure.axes for text in ax.texts]
        self.assertTrue(any("200.0 m³/h" in text and "A @" in text for text in annotations))
        self.assertTrue(any(f"Top {dialog.data.result['T_top']:.1f}" in text for text in annotations))
        dialog.use_fan()
        self.assertEqual(self.tier.selected_airflow_m3h, 200)
        self.assertEqual(window.preview_panel.flow.value(), 200)

    def test_slider_plot_click_and_plot_range(self):
        _, dialog = self.make_fan_dialog()
        dialog.maximum.setValue(400)
        dialog.slider.setValue(500)
        self.assertEqual(dialog.airflow.value(), 200)
        dialog._plot_clicked(SimpleNamespace(inaxes=dialog.figure.axes[0], xdata=250, button=1))
        self.assertEqual(dialog.airflow.value(), 250)
        dialog.maximum.setValue(100)
        self.assertGreaterEqual(dialog.maximum.value(), dialog.airflow.value())
        dialog.regenerate()
        self.assertEqual(dialog.data.airflow_m3h, 250)
        dialog.airflow.setValue(0)
        dialog.regenerate()
        self.assertEqual(dialog.data.result["T_top"], dialog.data.result["uncooled_T_top"])

    def test_failed_trial_blocks_export_and_preserves_saved_result(self):
        window, dialog = self.make_fan_dialog()
        saved = window.last_solve_result
        dialog.airflow.setValue(250)
        with patch.object(window, "solve_all_thermal", return_value=None):
            dialog.regenerate()
        self.assertIsNone(dialog.data)
        self.assertFalse(dialog.pdf_button.isEnabled())
        self.assertIs(window.last_solve_result, saved)

    def test_debounced_flow_edit_recalculates_live(self):
        _, dialog = self.make_fan_dialog()
        dialog.airflow.setValue(100)
        dialog.airflow.setValue(150)
        event_loop = QEventLoop()
        QTimer.singleShot(700, event_loop.quit)
        event_loop.exec_()
        self.assertEqual(dialog.data.airflow_m3h, 150)
        self.assertTrue(dialog.pdf_button.isEnabled())

    def test_one_page_pdf_contains_operating_values_and_all_devices(self):
        from heatcalc.reports.fan_sizing_report import export_fan_sizing_report
        from PyPDF2 import PdfReader
        base = self.tier.component_entries[0]
        self.tier.component_entries = [replace(base, description=f"Device {i:02d}", heat_each_w=20,
                                               qty=1) for i in range(18)]
        window, dialog = self.make_fan_dialog()
        dialog.airflow.setValue(150)
        dialog.regenerate()
        with report_test_directory() as tmp:
            self.assertTrue(Path(tmp).resolve().is_relative_to(Path(__file__).resolve().parent))
            path = export_fan_sizing_report(Path(tmp) / "fan.pdf", dialog.data,
                                           window.project.meta, "Selected filter fan")
            reader = PdfReader(path)
            self.assertEqual(len(reader.pages), 1)
            text = reader.pages[0].extract_text()
            self.assertIn("Selected filter fan", text)
            self.assertIn("150.0", text)
            self.assertIn("Device 00", text)
            self.assertIn("Device 17", text)
            self.assertIn("Page 1 of 1", text)

    def test_pdf_and_image_export_buttons(self):
        from PyPDF2 import PdfReader
        _, dialog = self.make_fan_dialog()
        dialog.airflow.setValue(150)
        dialog.regenerate()
        with report_test_directory() as tmp:
            self.assertTrue(Path(tmp).resolve().is_relative_to(Path(__file__).resolve().parent))
            path = Path(tmp) / "report"
            with patch("PyQt5.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(path), "")), \
                    patch("PyQt5.QtWidgets.QMessageBox.warning") as warning:
                dialog.export_pdf()
                dialog.export_image()
            self.assertFalse(warning.called, warning.call_args)
            self.assertEqual(len(PdfReader(path.with_suffix(".pdf")).pages), 1)
            self.assertTrue(path.with_suffix(".png").exists())


if __name__ == "__main__":
    unittest.main()
