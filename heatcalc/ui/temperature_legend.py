from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtCore import QRect


class TemperatureLegend(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setFixedSize(120, 200)

        self.temps = [140, 120, 100, 80, 60, 40]

    def paintEvent(self, event):

        painter = QPainter(self)

        h = 25

        for i, T in enumerate(self.temps):

            y = i * h

            color = self.temp_to_color(T)

            painter.fillRect(QRect(60, y + 5, 40, 15), color)

            painter.drawText(5, y + 18, f"{T}°C")

        painter.drawText(5, 190, "Temp")

    def temp_to_color(self, T):

        Tmin = 40
        Tmax = 140

        T = max(Tmin, min(Tmax, T))

        x = (T - Tmin) / (Tmax - Tmin)

        r = int(255 * x)
        g = int(255 * (1 - x))

        return QColor(r, g, 0)