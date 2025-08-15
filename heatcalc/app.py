import sys
from PyQt5.QtWidgets import QApplication
from .services.settings import SettingsManager
from .services.traceback_dialog import install_excepthook
from .ui.main_window import MainWindow


def run():
    install_excepthook()
    app = QApplication(sys.argv)
    settings = SettingsManager()
    win = MainWindow(settings)
    win.show()
    sys.exit(app.exec_())
