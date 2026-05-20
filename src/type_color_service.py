import threading
from dataclasses import dataclass
from typing import Callable
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QRect
from PyQt6.QtWidgets import QApplication
import mss
from PIL import Image


# Exact type badge colors as they appear in the game (RGB).
_TYPE_RGB: dict[tuple[int, int, int], str] = {
    (173, 165, 148): "normal",
    (247,  82,  49): "fire",
    ( 57, 156, 255): "water",
    (255, 198,  33): "electric",
    (123, 206,  82): "grass",
    ( 90, 206, 231): "ice",
    (165,  82,  57): "fighting",
    (145,  65, 203): "poison",
    (174, 122,  59): "ground",
    (156, 173, 247): "flying",
    (239,  65, 121): "psychic",
    (173, 189,  33): "bug",
    (189, 165,  90): "rock",
    ( 99,  99, 181): "ghost",
    (123,  99, 231): "dragon",
    (115,  90,  74): "dark",
    (129, 166, 190): "steel",
    (239, 112, 239): "fairy",
}

# Boss name-plaque background color — signals a boss encounter for the gate logic.
_BOSS_RGB = (54, 45, 62)


def _match_type(r: int, g: int, b: int) -> str | None:
    if (r, g, b) == _BOSS_RGB:
        return "_boss"
    return _TYPE_RGB.get((r, g, b))


@dataclass
class ColorBox:
    capture_box: object                          # CaptureBox
    callback: Callable[[str | None], None]       # called on Qt main thread
    label: str
    enabled: bool = True


class _Signals(QObject):
    result = pyqtSignal(str, object)             # (label, type_name | None)
    debug  = pyqtSignal(object)                  # {"label", "text", "raw", "prep"}


class TypeColorService(QObject):
    def __init__(self, interval_ms: int = 500, parent=None):
        super().__init__(parent)
        self._boxes: list[ColorBox] = []
        self._scanning = False
        self._debug_enabled = False
        self._signals = _Signals()
        self._signals.result.connect(self._dispatch)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_timer)

    def set_debug_enabled(self, enabled: bool):
        self._debug_enabled = enabled

    def register(self, box: ColorBox) -> None:
        self._boxes.append(box)

    def set_enabled(self, label: str, enabled: bool) -> None:
        for b in self._boxes:
            if b.label == label:
                b.enabled = enabled

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _on_timer(self):
        if self._scanning:
            return
        self._scanning = True
        # Snapshot rects on main thread — QWidget geometry must not be read from worker threads
        active = [(b, b.capture_box.get_capture_rect()) for b in self._boxes if b.enabled]
        if not active:
            self._scanning = False
            return
        threading.Thread(target=self._worker, args=(active,), daemon=True).start()

    def _worker(self, active: list):
        try:
            dpr = QApplication.primaryScreen().devicePixelRatio()
            rects: list[QRect] = [r for _, r in active]
            union: QRect = rects[0]
            for r in rects[1:]:
                union = union.united(r)
            monitor = {
                "left":   int(union.left()   * dpr),
                "top":    int(union.top()     * dpr),
                "width":  int(union.width()   * dpr),
                "height": int(union.height()  * dpr),
            }
            with mss.mss() as sct:
                shot = sct.grab(monitor)
            full_img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

            for box, rect in active:
                cx = int((rect.left() + rect.width()  / 2 - union.left()) * dpr)
                cy = int((rect.top()  + rect.height() / 2 - union.top())  * dpr)
                x0, y0 = max(0, cx - 2), max(0, cy - 2)
                x1, y1 = min(full_img.width, cx + 3), min(full_img.height, cy + 3)
                if x1 <= x0 or y1 <= y0:
                    self._signals.result.emit(box.label, None)
                    continue
                pixels = list(full_img.crop((x0, y0, x1, y1)).getdata())
                avg_r = sum(p[0] for p in pixels) // len(pixels)
                avg_g = sum(p[1] for p in pixels) // len(pixels)
                avg_b = sum(p[2] for p in pixels) // len(pixels)
                type_name = _match_type(avg_r, avg_g, avg_b)
                self._signals.result.emit(box.label, type_name)
                if self._debug_enabled:
                    swatch = Image.new("RGB", (60, 24), (avg_r, avg_g, avg_b))
                    hex_str = f"#{avg_r:02X}{avg_g:02X}{avg_b:02X}"
                    label_text = f"{hex_str}  →  {type_name or '(no match)'}"
                    self._signals.debug.emit({"label": box.label, "text": label_text, "raw": swatch, "prep": None})
        except Exception as e:
            print(f"[TypeColorService] error: {e}")
        finally:
            self._scanning = False

    def _dispatch(self, label: str, type_name):
        for box in self._boxes:
            if box.label == label:
                box.callback(type_name)
                break
