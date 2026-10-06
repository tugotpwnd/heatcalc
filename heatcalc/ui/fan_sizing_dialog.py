"""Interactive fan selection with a coupled operating point and report export."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QSignalBlocker
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QDoubleSpinBox, QSlider,
    QLabel, QPushButton, QLineEdit, QFileDialog, QMessageBox, QScrollArea,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT

from ..core.fan_sizing import build_fan_sizing_data
from ..reports.fan_sizing_plot import draw_fan_sizing_figure


class FanSizingDialog(QDialog):
    def __init__(self, panel, tier):
        super().__init__(panel)
        self.panel, self.swb, self.tier = panel, panel.swb, tier
        self.data = None
        self._solved_flow = None
        self._trial_result = None
        self._input_signature = panel.solved_signature
        self.setWindowTitle(f"Fan sizing - {tier.name}")
        self.resize(1120, 880)
        layout = QVBoxLayout(self)
        instructions = QLabel("Adjust airflow, move the slider, or click a plot to choose an operating point. "
                              "The network recalculates after each change. Use this fan to save the selection.")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        controls = QHBoxLayout()
        form = QFormLayout()
        self.airflow = QDoubleSpinBox()
        self.airflow.setRange(0, 1000000)
        self.airflow.setDecimals(1)
        self.airflow.setSuffix(" m³/h")
        self.airflow.setSingleStep(25)
        self.airflow.setValue(tier.selected_airflow_m3h)
        self.maximum = QDoubleSpinBox()
        self.maximum.setRange(1, 1000000)
        self.maximum.setDecimals(1)
        self.maximum.setSuffix(" m³/h")
        res = panel.result["tiers"][tier]
        self.maximum.setValue(min(1000000, max(100, res.get("airflow_m3h", 0)*3,
                                                tier.selected_airflow_m3h*2)))
        self.fan_name = QLineEdit(tier.selected_fan_name)
        self.fan_name.setPlaceholderText("Optional model / reference")
        form.addRow("Delivered airflow:", self.airflow)
        form.addRow("Plot upper limit:", self.maximum)
        form.addRow("Fan reference:", self.fan_name)
        controls.addLayout(form, 1)
        actions = QVBoxLayout()
        self.use_button = QPushButton("Use this fan")
        self.pdf_button = QPushButton("Export one-page PDF…")
        self.image_button = QPushButton("Export plot image…")
        actions.addWidget(self.use_button)
        actions.addWidget(self.pdf_button)
        actions.addWidget(self.image_button)
        controls.addLayout(actions)
        layout.addLayout(controls)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setToolTip("Delivered airflow across the displayed plot range")
        layout.addWidget(self.slider)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.figure = Figure(figsize=(10, 6), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        layout.addWidget(self.toolbar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll, 1)
        current_basis = ("Manufacturer derating curves replace the 80% Ith cap. "
                         if self.swb.project.meta.use_manufacturer_derating else
                         "Current is limited to 80% of rated current. ")
        self.note = QLabel("Operating-point markers use the coupled network solve. Curves use that operating "
                           "point's heat load. " + current_basis + "Current is per device; temperature ratings "
                           "must also be satisfied. Zero airflow uses natural cooling.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.regenerate)
        self.airflow.valueChanged.connect(self._airflow_changed)
        self.maximum.valueChanged.connect(self._range_changed)
        self.slider.valueChanged.connect(self._slider_changed)
        self.use_button.clicked.connect(self.use_fan)
        self.pdf_button.clicked.connect(self.export_pdf)
        self.image_button.clicked.connect(self.export_image)
        self.canvas.mpl_connect("button_press_event", self._plot_clicked)
        self._sync_slider()
        # The initial point is already solved by the parent preview.
        self._trial_result = panel.result
        self._solved_flow = tier.selected_airflow_m3h
        self.regenerate()

    def _sync_slider(self):
        with QSignalBlocker(self.slider):
            self.slider.setValue(round(1000 * self.airflow.value() / self.maximum.value()))

    def _airflow_changed(self):
        if self.airflow.value() > self.maximum.value():
            with QSignalBlocker(self.maximum):
                self.maximum.setValue(min(1000000, self.airflow.value()*1.2))
        self._sync_slider()
        self._schedule()

    def _range_changed(self):
        # A range change must not silently change the selected operating point.
        if self.maximum.value() < self.airflow.value():
            with QSignalBlocker(self.maximum):
                self.maximum.setValue(self.airflow.value())
        self._sync_slider()
        self._schedule()

    def _slider_changed(self, value):
        self.airflow.setValue(self.maximum.value() * value / 1000)

    def _plot_clicked(self, event):
        if event.inaxes in self.figure.axes and event.xdata is not None and event.button == 1:
            if not self.toolbar.mode:
                self.airflow.setValue(max(0, min(self.maximum.value(), event.xdata)))

    def _enable_actions(self, enabled):
        for button in (self.use_button, self.pdf_button, self.image_button):
            button.setEnabled(enabled)

    def _schedule(self):
        self._enable_actions(False)
        self.status.setText("Updating network operating point…")
        self.timer.start()

    def regenerate(self):
        self.timer.stop()
        self._enable_actions(False)
        try:
            if self.panel.signature() != self._input_signature:
                raise ValueError("Project inputs changed. Close this dialog and solve the network again.")
            flow = self.airflow.value()
            if flow != self._solved_flow or self._trial_result is None:
                result = self.swb.solve_all_thermal(
                    airflow_overrides={self.tier: flow}, preview_only=True,
                )
                if not result or not result["solver_converged"] or not result["global"].converged:
                    raise ValueError("The trial network solve did not converge. Choose another airflow and try again.")
                self._trial_result, self._solved_flow = result, flow
            self.data = build_fan_sizing_data(
                self.swb, self.tier, self._trial_result["tiers"][self.tier], self.maximum.value(),
            )
            self.canvas.setMinimumHeight(max(460, 290 + len(self.data.devices)*60))
            draw_fan_sizing_figure(self.figure, self.data)
            self.canvas.draw_idle()
            r = self.data.result
            self.status.setText(
                f"{flow:.1f} m³/h  |  Top {r['T_top']:.1f} °C  |  Mid {r['T_mid']:.1f} °C  |  "
                f"{len(self.data.devices)} device curves ({self.data.excluded_count} entries omitted). "
                + ("Within tier limit." if r["compliant_top"] else "Above tier limit.")
            )
            self._enable_actions(True)
        except Exception as exc:
            self.data = None
            self.figure.clear()
            self.figure.text(.5, .5, "Operating point unavailable", ha="center")
            self.canvas.draw_idle()
            self.status.setText(str(exc))

    def use_fan(self):
        if self.data is None or self.timer.isActive():
            return
        self.tier.selected_airflow_m3h = self.data.airflow_m3h
        self.tier.selected_fan_name = self.fan_name.text().strip()
        self.panel.flow.setValue(self.tier.selected_airflow_m3h)
        self.panel.fan_name.setText(self.tier.selected_fan_name)
        self.swb.tierContentsChanged.emit()
        self.panel.solve()
        self._input_signature = self.panel.solved_signature
        self._trial_result = self.panel.result
        self._solved_flow = self.data.airflow_m3h
        self.regenerate()
        if self.data is not None:
            self.status.setText("Fan saved to tier. " + self.status.text())

    def export_pdf(self):
        if self.data is None or self.timer.isActive():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export fan sizing report", "Fan sizing.pdf", "PDF (*.pdf)")
        if not path:
            return
        try:
            from ..reports.fan_sizing_report import export_fan_sizing_report
            path = Path(path)
            if path.suffix.lower() != ".pdf":
                path = path.with_suffix(".pdf")
            export_fan_sizing_report(path, self.data, replace(self.swb.project.meta), self.fan_name.text().strip())
            self.status.setText(f"One-page report exported: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def export_image(self):
        if self.data is None or self.timer.isActive():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export fan sizing plot", "Fan sizing.png", "PNG (*.png)")
        if not path:
            return
        try:
            path = Path(path)
            if path.suffix.lower() != ".png":
                path = path.with_suffix(".png")
            self.figure.savefig(path, dpi=220, facecolor="white")
            self.status.setText(f"Annotated plot exported: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def done(self, result):
        self.timer.stop()
        super().done(result)
