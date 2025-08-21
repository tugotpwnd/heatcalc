# heatcalc/core/excepthook.py
import sys, traceback, datetime
from pathlib import Path

def install_excepthook():
    log_dir = Path.home() / "AppData/Local/HeatCalc/logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    def handler(exc_type, exc, tb):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        (log_dir / f"crash_{ts}.log").write_text(
            "".join(traceback.format_exception(exc_type, exc, tb)),
            encoding="utf-8"
        )
        # TODO: optionally show a QMessageBox here instead of silent fail
        print(f"Crash log written to {log_dir}")

    sys.excepthook = handler
