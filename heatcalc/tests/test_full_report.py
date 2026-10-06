"""Regression coverage for conditional full-report content and PDF navigation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from PyPDF2 import PdfReader
from reportlab.platypus import Paragraph, Table

from heatcalc.reports.simple_report import (
    BusRow, ComponentRow, ProjectMeta, SectionCounter, TierRow, TierThermal,
    build_tier_summary_page, component_derating_table, export_simple_report,
    render_working_temperature_page, tier_cooling_summary,
)


def flow_text(items):
    if isinstance(items, Paragraph):
        return items.getPlainText()
    if isinstance(items, Table):
        return flow_text(items._cellvalues)
    if isinstance(items, (list, tuple)):
        return "\n".join(flow_text(item) for item in items)
    return ""


def sample_thermal(tag="Tier 1", **changes):
    thermal = TierThermal(
        tag=tag, Ae=4.5, P_W=300, k=0.15, c=1.4, x=0.804, f=1, g=None,
        vent=False, curve=1, ambient_C=40, dt_mid=10, dt_top=15,
        T_mid=50, T_top=55, max_C=70, compliant_mid=True, compliant_top=True,
        P_material_W=180, P_cooling_W=120, P_890=180,
        surfaces=[dict(name="Front", w=0.6, h=1.8, A0=1.08, b=0.9, Ae=0.972)],
    )
    return replace(thermal, **changes)


def sample_tier(tag="Tier 1", **changes):
    return replace(TierRow(tag, 600, 1800, 500, [], [], []), **changes)


def sample_component(description="Drive", **changes):
    return replace(ComponentRow(description, "D100", 1, 100, 100,
                                rated_current_A=100, derating_temp_start_C=40,
                                derating_function="1 - (x - 40) * 0.01"), **changes)


class FullReportTests(unittest.TestCase):
    def test_empty_and_unrated_components_omit_both_assessments(self):
        for components in ([], [sample_component(rated_current_A=None)],
                           [sample_component(rated_current_A=0)]):
            tier = sample_tier(components=components)
            flow = []
            render_working_temperature_page(flow, SectionCounter(), tier, sample_thermal())
            self.assertEqual(flow, [])
            self.assertIsNone(component_derating_table(tier, sample_thermal()))

    def test_component_only_reports_capacity_without_table_6(self):
        tier = sample_tier(components=[sample_component(derating_function=None)])
        flow = []
        render_working_temperature_page(flow, SectionCounter(), tier, sample_thermal())
        text = flow_text(flow)
        self.assertNotIn("Table 6", text)
        self.assertIn("Component derated current capacity", text)
        self.assertIn("80.0", text)
        self.assertNotIn("Drive*", text)

    def test_bus_load_and_source_presence_include_table_6(self):
        bus = BusRow("Bus 1", 50, 10, 1, 1, 400, 20, 60, 55)
        for tier in (sample_tier(buses=[bus]), sample_tier(loads=[object()]),
                     sample_tier(has_bus_elements=True)):
            flow = []
            render_working_temperature_page(flow, SectionCounter(), tier, sample_thermal())
            self.assertIn("Table 6", flow_text(flow))
            self.assertNotIn("Component derated current capacity", flow_text(flow))

    def test_manufacturer_marks_only_applicable_components_and_uses_selected_fan(self):
        tier = sample_tier(components=[sample_component(),
            sample_component("Fallback", derating_function=None),
            sample_component("Invalid", derating_function="bad expression"),
            sample_component("Unrated", rated_current_A=None)])
        thermal = sample_thermal(use_manufacturer_derating=True,
                                 selected_fan_name="3245800", selected_airflow_m3h=960)
        flow = []
        render_working_temperature_page(flow, SectionCounter(), tier, thermal)
        text = flow_text(flow)
        self.assertIn("Drive*", text)
        self.assertIn("85.0", text)
        self.assertIn("Fallback\n100.0\n55.0\n80.0", text)
        self.assertNotIn("Fallback*", text)
        self.assertNotIn("Invalid*", text)
        self.assertNotIn("Unrated", text)
        self.assertIn("* Component(s) temperature deratings were solved directly using manufacturer data. "
                      "Selected fan: 3245800; delivers 960 m³/h of airflow.", text)
        disabled = flow_text(component_derating_table(tier, replace(thermal, use_manufacturer_derating=False)))
        self.assertNotIn("Drive*", disabled)
        self.assertEqual(tier.components[0].derated_current_A, 80)
        no_fan = []
        render_working_temperature_page(no_fan, SectionCounter(), tier,
                                        replace(thermal, selected_airflow_m3h=0))
        self.assertIn("manufacturer data", flow_text(no_fan))
        self.assertNotIn("Selected fan", flow_text(no_fan))

    def test_cooling_summary_reports_actual_arrangement_and_compliance(self):
        natural = sample_thermal()
        fan = replace(natural, selected_airflow_m3h=960, selected_fan_name="3245800",
                      airflow_m3h=200)
        self.assertEqual(tier_cooling_summary(natural), ("Natural convection", "-", False))
        self.assertEqual(tier_cooling_summary(fan), ("Forced ventilation", "960", False))
        for failed in (replace(fan, compliant_top=False), replace(fan, compliant_mid=False)):
            self.assertEqual(tier_cooling_summary(failed), ("Forced ventilation", "960", True))
        required = replace(natural, compliant_top=False, airflow_m3h=200)
        self.assertEqual(tier_cooling_summary(required), ("Forced ventilation required", "200.0", True))
        text = flow_text(build_tier_summary_page([natural, fan]))
        self.assertIn("Important application of results", text)
        self.assertIn("conditional on providing", text)
        self.assertIn("Selected fan: 3245800", text)

    def test_adapter_detects_standalone_load_and_source(self):
        from PyQt5.QtCore import QPointF
        from PyQt5.QtWidgets import QApplication
        from heatcalc.ui.tier_item import TierItem
        from heatcalc.ui.bus_items import BusLoadItem, BusSourceItem
        from heatcalc.reports.export_api import _map_tier_item
        app = QApplication.instance() or QApplication([])
        for factory in (BusLoadItem, BusSourceItem):
            tier = TierItem("Bus element tier")
            self.assertFalse(_map_tier_item(tier, None).has_bus_elements)
            element = factory(QPointF(0, 0))
            tier.add_bus_item(element)
            self.assertTrue(_map_tier_item(tier, None).has_bus_elements)

    def test_full_pdf_has_links_and_no_empty_sections_or_slices(self):
        root = Path(__file__).resolve().parents[1] / "tmp" / "pdfs"
        root.mkdir(parents=True, exist_ok=True)
        directory = (root / uuid4().hex).resolve()
        self.assertTrue(directory.is_relative_to(root.resolve()))
        directory.mkdir()
        self.addCleanup(shutil.rmtree, directory)
        tiers = [sample_tier("Control", components=[sample_component("Power supply", rated_current_A=None)]),
                 sample_tier("Fan tier", components=[sample_component("Drive with a long descriptive equipment name")]),
                 sample_tier("Empty")]
        thermals = [sample_thermal("Control"),
                    sample_thermal("Fan tier", use_manufacturer_derating=True,
                                   selected_airflow_m3h=960, selected_fan_name="3245800"),
                    sample_thermal("Empty", T_mid=40, T_top=40, dt_mid=0, dt_top=0, P_W=0)]
        meta = ProjectMeta("TEST", "Full report regression", "Maxwell", "Test", "A", "2026-09-11", "IP54")
        output = export_simple_report(directory / "full_report.pdf", meta, "Maxwell", tiers,
                                      tier_thermals=thermals, iec60890_checklist=[])
        reader = PdfReader(output)
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertNotIn("Table 6", text)
        self.assertNotIn("AS/NZS 61439 Working Temperature Assessment", text)
        self.assertNotIn("No component derating data", text)
        self.assertNotIn("Temperature Slice", text)
        self.assertIn("Component Current Capacity Assessment - Fan tier", text)
        self.assertIn("manufacturer data", text)
        page_ids = {page.indirect_reference.idnum for page in reader.pages}
        links = [annotation.get_object() for page in reader.pages for annotation in page.get("/Annots", [])
                 if annotation.get_object().get("/Subtype") == "/Link"]
        self.assertGreater(len(links), 10)
        destinations = set()
        for link in links:
            destination = link["/Dest"]
            self.assertIn(destination[0].idnum, page_ids)
            destinations.add(destination[0].idnum)
        self.assertGreater(len(destinations), 4)
        self.assertFalse(list((directory / ".assets").glob("*slice*")))
        # No page should consist solely of headers, footers, or trailing application text.
        for page in reader.pages:
            self.assertGreater(len(page.extract_text().strip()), 150)


if __name__ == "__main__":
    unittest.main()
