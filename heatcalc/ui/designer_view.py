from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QPen, QColor, QPainter
from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene


GRID = 25  # px grid – keep consistent across items


def snap(v: float) -> float:
    return round(v / GRID) * GRID


class DesignerView(QGraphicsView):
    """Scene with grid, zoom, and panning helpers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#1e1f22"))
        self.setRenderHints(self.renderHints() |
                            QPainter.Antialiasing |
                            QPainter.TextAntialiasing)

        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)

        # Big scene rect you can scroll around
        self.scene().setSceneRect(QRectF(-5000, -5000, 10000, 10000))

        # panning
        self._panning = False
        self._pan_start = QPointF()

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
    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier)):
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = self.mapToScene(self._pan_start) - self.mapToScene(event.pos())
            self._pan_start = event.pos()
            self.setTransformationAnchor(QGraphicsView.NoAnchor)
            self.translate(delta.x(), delta.y())
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
        else:
            super().mouseReleaseEvent(event)
