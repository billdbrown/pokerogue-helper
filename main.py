import sys
import os
import signal

if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
else:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --ignore-gpu-blocklist --enable-webgl",
)

from PyQt6.QtWidgets import QApplication


def _run_full():
    from embedded_window import EmbeddedMainWindow
    app = QApplication(sys.argv)
    win = EmbeddedMainWindow()
    win.show()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sys.exit(app.exec())


def _run_browser():
    from PyQt6.QtCore import Qt, QMetaObject
    from impact_table import ImpactTableDialog
    import impact_db

    app = QApplication(sys.argv)
    dlg = ImpactTableDialog()
    dlg.setWindowFlag(Qt.WindowType.Window)
    dlg.finished.connect(app.quit)
    dlg.show()

    if not impact_db.is_ready():
        impact_db.init(
            on_ready=lambda: QMetaObject.invokeMethod(
                dlg, "refresh", Qt.ConnectionType.QueuedConnection
            )
        )

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sys.exit(app.exec())


def main():
    if "--browser" in sys.argv:
        _run_browser()
    else:
        _run_full()


if __name__ == "__main__":
    main()
