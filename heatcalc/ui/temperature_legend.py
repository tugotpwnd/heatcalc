from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QPainter, QColor, QLinearGradient, QPen
from PyQt5.QtCore import QRect


class TemperatureLegend(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setFixedSize(140, 220)

        self.Tmin = 40.0
        self.Tmax = 140.0

    # ---------------------------------------------------------

    def set_temperature_range(self, Tmin, Tmax):

        if Tmax <= Tmin:
            Tmax = Tmin + 1.0

        self.Tmin = float(Tmin)
        self.Tmax = float(Tmax)

        self.update()

    # ---------------------------------------------------------

    def paintEvent(self, event):

        painter = QPainter(self)

        width = self.width()
        height = self.height()

        bar_x = 70
        bar_y = 20
        bar_w = 30
        bar_h = 150

        # -------- continuous gradient --------

        grad = QLinearGradient(
            bar_x,
            bar_y + bar_h,
            bar_x,
            bar_y
        )

        grad.setColorAt(0.0, QColor(0, 255, 0))   # green
        grad.setColorAt(0.5, QColor(255, 255, 0)) # yellow
        grad.setColorAt(1.0, QColor(255, 0, 0))   # red

        painter.fillRect(QRect(bar_x, bar_y, bar_w, bar_h), grad)

        painter.setPen(QPen(QColor("#888")))
        painter.drawRect(QRect(bar_x, bar_y, bar_w, bar_h))

        # -------- tick labels --------

        steps = 5

        for i in range(steps):

            frac = i / (steps - 1)

            T = self.Tmax - frac * (self.Tmax - self.Tmin)

            y = bar_y + frac * bar_h

            painter.drawLine(bar_x - 5, int(y), bar_x, int(y))

            painter.drawText(
                5,
                int(y + 5),
                f"{T:.1f}°C"
            )

        # -------- title --------

        painter.drawText(5, 12, "Temperature")

        # -------- delta T --------

        deltaT = self.Tmax - self.Tmin

        painter.drawText(
            5,
            bar_y + bar_h + 25,
            f"ΔT = {deltaT:.1f} °C"
        )