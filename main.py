import sys
import os
import signal

if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
else:
    # Source layout puts modules under src/; add it to sys.path before any
    # project imports so bare-name imports (e.g. `import overlay`) keep working.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --ignore-gpu-blocklist --enable-webgl",
)

from PyQt6.QtWidgets import QApplication
from embedded_window import EmbeddedMainWindow


def main():
    app = QApplication(sys.argv)
    win = EmbeddedMainWindow()
    win.show()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
