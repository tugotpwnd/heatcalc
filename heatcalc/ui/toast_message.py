# heatcalc/ui/toast_message.py
from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QRect
from PyQt5.QtGui import QFont, QColor

class ToastMessage(QWidget):
    def __init__(self, text: str, parent=None, timeout=3000):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.ToolTip | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # label as the toast body
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("""
            QLabel {
                background-color: rgba(40, 40, 40, 220);
                color: white;
                padding: 14px 28px;
                border-radius: 12px;
                font-size: 12pt;
            }
        """)
        label.setFont(QFont("Segoe UI", 11))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)

        self.adjustSize()

        # Auto close
        QTimer.singleShot(timeout, self._fade_out)

        # Start hidden for fade-in
        self.setWindowOpacity(0.0)
        self._fade_in()

    def show_centered(self, parent_window):
        """Place toast in the center of parent_window"""
        geo: QRect = parent_window.geometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)
        self.show()

    def _fade_in(self):
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start()
        self._anim = anim

    def _fade_out(self):
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(500)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self.close)
        anim.start()
        self._anim = anim
