# heatcalc/boot.py
import sys, os, time
from pathlib import Path

# use your existing resource helper semantics without changing your codebase
base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
png = base / "heatcalc" / "data" / "title.png"

from PyQt5.QtWidgets import QApplication, QLabel
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap

def main():
    app = QApplication(sys.argv)
    lab = QLabel()
    lab.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    lab.setAttribute(Qt.WA_TranslucentBackground, True)
    lab.setPixmap(QPixmap(str(png)))
    lab.show()

    # import your heavy app AFTER showing splash
    def _start():
        from heatcalc.main import main as real_main  # your existing entry
        lab.close()
        real_main()

    QTimer.singleShot(50, _start)
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
