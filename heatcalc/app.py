import sys
from PyQt5.QtWidgets import QApplication
from heatcalc.services.settings import SettingsManager
from heatcalc.services.traceback import install_excepthook
from heatcalc.ui.main_window import MainWindow


def run():
    install_excepthook()
    app = QApplication(sys.argv)
    settings = SettingsManager()
    win = MainWindow(settings)
    win.show()
    sys.exit(app.exec_())
