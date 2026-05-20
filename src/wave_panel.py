import re
import window_state
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QLineEdit,
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal


class _ClickableLabel(QLabel):
    """QLabel that emits `clicked` on left mouse press."""
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _EditLineEdit(QLineEdit):
    """QLineEdit that emits `cancelled` on Escape so the wave panel can revert."""
    cancelled = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

# ── Classic mode milestone data ───────────────────────────────────────────────

_SPECIFIC = {
    8:   ("Rival",           "rival"),
    25:  ("Rival",           "rival"),
    35:  ("Evil Team Grunt", "grunt"),
    55:  ("Rival",           "rival"),
    62:  ("Evil Team Grunt", "grunt"),
    64:  ("Evil Team Grunt", "grunt"),
    66:  ("Evil Team Grunt", "grunt"),
    95:  ("Rival",           "rival"),
    112: ("Evil Team Grunt", "grunt"),
    114: ("Evil Team Grunt", "grunt"),
    115: ("Evil Team Admin", "admin"),
    145: ("Rival",           "rival"),
    164: ("Evil Team Admin", "admin"),
    165: ("Evil Team Boss",  "evil_boss"),
    182: ("Elite Four #1",   "elite"),
    184: ("Elite Four #2",   "elite"),
    186: ("Elite Four #3",   "elite"),
    188: ("Elite Four #4",   "elite"),
    190: ("Champion",        "champion"),
    195: ("Rival (Final!)",  "rival"),
    200: ("Eternatus",       "final"),
}

_SEVERITY_COLOR = {
    "final":     ("#f38ba8", True),
    "rival":     ("#f38ba8", True),
    "evil_boss": ("#f38ba8", True),
    "champion":  ("#cba6f7", True),
    "elite":     ("#cba6f7", False),
    "admin":     ("#fab387", False),
    "grunt":     ("#f9e2af", False),
    "boss":      ("#a6adc8", False),
}


def _next_events(current_wave: int, count: int = 5) -> list:
    results = []
    for w in range(max(current_wave + 1, 1), 201):
        if w in _SPECIFIC:
            label, severity = _SPECIFIC[w]
            results.append((w, label, severity))
        elif w % 10 == 0:
            results.append((w, "Gym Boss", "boss"))
        if len(results) >= count:
            break
    return results


# ── Signals ───────────────────────────────────────────────────────────────────

class _Signals(QObject):
    wave_ready    = pyqtSignal(int)
    wave_changed  = pyqtSignal(int)   # fires only when wave number increments
    error         = pyqtSignal(str)


# ── Panel ─────────────────────────────────────────────────────────────────────

_WAVE_AGREE_THRESHOLD = 3   # consecutive matching reads required to commit (~1.5s @ 500ms)
# Wave is monotonic-forward: OCR reads strictly lower than the current committed
# wave are silently dropped. The wave only goes down via an explicit New Run reset
# or a manual entry from the user. This sidesteps the entire class of "OCR mistook
# a sprite digit for the wave" failures without needing escalating thresholds.


class WavePanel(QWidget):
    def __init__(self, wave_box=None, embedded: bool = False):
        super().__init__()
        self._drag_pos     = None
        self._embedded     = embedded
        self._current_wave = None
        # Corroboration state: hold a candidate wave value until it's seen
        # N times consecutively before committing. Hallucinated single reads
        # never reach the threshold and never trigger downstream state changes.
        self._pending_wave:  int | None = None
        self._pending_count: int        = 0

        self._signals = _Signals()
        self._signals.wave_ready.connect(self._on_wave)
        self._signals.error.connect(lambda msg: None)  # suppress unhandled-signal warnings

        if not embedded:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedWidth(220)

        self._build_ui()

        # Restore the last committed wave so manual entries (and the OCR floor)
        # survive app restarts. Updating the display directly avoids firing
        # wave_changed — restoration isn't a game-state transition.
        saved = window_state.load().get("current_wave")
        if isinstance(saved, int) and 1 <= saved <= 200:
            self._current_wave = saved
            self._render_wave(saved)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        if self._embedded:
            self._build_embedded_ui()
        else:
            self._build_standalone_ui()

    def _build_standalone_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._card = QFrame()
        self._card.setObjectName("card")
        self._card.setStyleSheet("""
            QFrame#card {
                background: #1e1e2e;
                border-radius: 10px;
                border: 1px solid #44475a;
            }
        """)
        root.addWidget(self._card)

        inner = QVBoxLayout(self._card)
        inner.setContentsMargins(12, 10, 12, 10)
        inner.setSpacing(6)

        title_row = QHBoxLayout()
        title_lbl = QLabel("Wave Tracker")
        title_lbl.setStyleSheet("color:#cdd6f4; font-size:13px; font-weight:bold;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#6c7086;border:none;font-size:12px;}"
            "QPushButton:hover{color:#f38ba8;}"
        )
        close_btn.clicked.connect(self.hide)
        title_row.addWidget(close_btn)
        inner.addLayout(title_row)

        inner.addWidget(self._hline())
        self._wave_lbl = QLabel("WAVE —")
        self._wave_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wave_lbl.setStyleSheet(
            "color:#cdd6f4; font-size:22px; font-weight:bold; padding:4px 0;"
        )
        inner.addWidget(self._wave_lbl)
        inner.addWidget(self._hline())

        hdr = QLabel("UPCOMING")
        hdr.setStyleSheet("color:#6c7086; font-size:10px; font-weight:bold; letter-spacing:1px;")
        inner.addWidget(hdr)

        self._event_rows = []
        for _ in range(5):
            row = QHBoxLayout()
            row.setSpacing(6)
            wave_lbl = QLabel("")
            wave_lbl.setFixedWidth(28)
            wave_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            wave_lbl.setStyleSheet("color:#6c7086; font-size:11px;")
            name_lbl = QLabel("")
            name_lbl.setStyleSheet("color:#6c7086; font-size:11px;")
            row.addWidget(wave_lbl)
            row.addWidget(name_lbl)
            row.addStretch()
            inner.addLayout(row)
            self._event_rows.append((wave_lbl, name_lbl))

        self.adjustSize()

    def _build_embedded_ui(self):
        self.setStyleSheet("background: #1e1e2e;")
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 0, 8, 0)
        root.setSpacing(6)

        _wave_style = "color:#cdd6f4; font-size:11px; font-weight:bold;"
        self._wave_lbl = _ClickableLabel("W —")
        self._wave_lbl.setStyleSheet(_wave_style)
        self._wave_lbl.setFixedWidth(50)
        self._wave_lbl.setCursor(Qt.CursorShape.IBeamCursor)
        self._wave_lbl.setToolTip("Click to set wave manually")
        self._wave_lbl.clicked.connect(self._enter_edit)
        root.addWidget(self._wave_lbl)

        self._wave_input = _EditLineEdit()
        self._wave_input.setStyleSheet(
            "QLineEdit { background:#313244; color:#cdd6f4; border:1px solid #45475a;"
            " border-radius:3px; padding:0 4px; font-size:11px; font-weight:bold; }"
        )
        self._wave_input.setFixedWidth(50)
        self._wave_input.setMaxLength(3)
        self._wave_input.hide()
        self._wave_input.returnPressed.connect(self._commit_manual)
        self._wave_input.cancelled.connect(self._cancel_edit)
        self._wave_input.editingFinished.connect(self._cancel_edit)  # blur = cancel
        root.addWidget(self._wave_input)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet("background:#44475a;")
        root.addWidget(sep)

        self._event_rows = []
        for _ in range(5):
            w_lbl = QLabel("")
            w_lbl.setStyleSheet("color:#6c7086; font-size:10px; font-weight:bold;")
            n_lbl = QLabel("")
            n_lbl.setStyleSheet("color:#6c7086; font-size:10px;")
            w_lbl.setFixedWidth(22)
            n_lbl.setFixedWidth(90)
            root.addWidget(w_lbl)
            root.addWidget(n_lbl)
            self._event_rows.append((w_lbl, n_lbl))

        root.addStretch()

    def _hline(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#313244;")
        return line

    # ── OCR result receiver (called by OCRService on main thread) ───────────────

    def receive_wave_text(self, text: str) -> None:
        digits = re.sub(r'[^0-9]', '', text)[:3]
        if not digits:
            # Blank read — no progress, but keep pending so a transient miss doesn't reset.
            return
        val = int(digits)
        if not (1 <= val <= 200):
            return
        # Floor: wave is monotonic-forward. Anything strictly less than the current
        # committed wave is dropped silently — only New Run / manual entry can lower it.
        if self._current_wave is not None and val < self._current_wave:
            return
        # Corroboration: same value N consecutive times before commit.
        if val == self._pending_wave:
            self._pending_count += 1
        else:
            self._pending_wave  = val
            self._pending_count = 1
        print(f"[wave] OCR read {val} (current={self._current_wave}, pending {self._pending_count}/{_WAVE_AGREE_THRESHOLD})")
        if self._pending_count >= _WAVE_AGREE_THRESHOLD:
            self._pending_count = 0
            self._signals.wave_ready.emit(val)

    # ── Manual entry (embedded only) ──────────────────────────────────────────

    def _enter_edit(self):
        if not hasattr(self, '_wave_input'):
            return
        self._wave_input.setText(str(self._current_wave) if self._current_wave is not None else "")
        self._wave_lbl.hide()
        self._wave_input.show()
        self._wave_input.setFocus()
        self._wave_input.selectAll()

    def _commit_manual(self):
        # editingFinished may fire right after returnPressed (when we hide the input
        # and focus is lost) — block the recursive cancel by checking visibility.
        if not self._wave_input.isVisible():
            return
        text = self._wave_input.text().strip()
        self._wave_input.hide()
        self._wave_lbl.show()
        if text.isdigit():
            val = int(text)
            if 1 <= val <= 200:
                self.set_wave_manually(val)

    def _cancel_edit(self):
        if not self._wave_input.isVisible():
            return
        self._wave_input.hide()
        self._wave_lbl.show()

    def set_wave_manually(self, val: int) -> None:
        """Bypass corroboration and commit a user-entered wave value. The new value
        becomes the OCR floor — subsequent OCR reads strictly lower than it are
        ignored until the user clicks New Run."""
        if not (1 <= val <= 200):
            return
        print(f"[wave] manual set: {self._current_wave} → {val}")
        self._pending_wave  = None
        self._pending_count = 0
        self._signals.wave_ready.emit(val)

    def clear_wave(self) -> None:
        """Reset wave state. Called by New Run."""
        print(f"[wave] cleared (was {self._current_wave})")
        self._current_wave  = None
        self._pending_wave  = None
        self._pending_count = 0
        # Persist the cleared state — otherwise the next launch would restore the
        # pre-reset wave from disk and re-establish the old OCR floor.
        window_state.save_key("current_wave", None)
        if hasattr(self, '_wave_lbl'):
            self._wave_lbl.setText("W —" if self._embedded else "WAVE  —")
        for w_lbl, n_lbl in getattr(self, '_event_rows', []):
            w_lbl.setText("")
            n_lbl.setText("")

    # ── Display ───────────────────────────────────────────────────────────────

    def _on_wave(self, wave: int):
        if wave != self._current_wave:
            print(f"[wave] wave_changed: {self._current_wave} → {wave}")
            self._signals.wave_changed.emit(wave)
        self._current_wave = wave
        # Persist so manual entries and the OCR floor survive across restarts.
        window_state.save_key("current_wave", wave)
        self._render_wave(wave)

    def _render_wave(self, wave: int):
        """Update the wave label and upcoming-events rows. No persistence, no signals."""
        self._wave_lbl.setText(f"W {wave}" if self._embedded else f"WAVE  {wave}")
        events = _next_events(wave)
        for i, (w_lbl, n_lbl) in enumerate(self._event_rows):
            if i < len(events):
                ev_wave, ev_label, ev_severity = events[i]
                color, bold = _SEVERITY_COLOR.get(ev_severity, ("#a6adc8", False))
                weight = "bold" if bold else "normal"
                w_lbl.setText(str(ev_wave))
                w_lbl.setStyleSheet(f"color:{color}; font-size:11px; font-weight:{weight};")
                n_lbl.setText(ev_label)
                n_lbl.setStyleSheet(f"color:{color}; font-size:11px; font-weight:{weight};")
            else:
                w_lbl.setText("")
                n_lbl.setText("")
        if not self._embedded:
            self.adjustSize()

    @property
    def wave_changed(self):
        return self._signals.wave_changed

    # ── Drag to move (standalone only) ────────────────────────────────────────

    def mousePressEvent(self, event):
        if self._embedded:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._embedded:
            return
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _event):
        if self._embedded:
            return
        self._drag_pos = None
        p = self.pos()
        window_state.save_key("wave_panel", {"x": p.x(), "y": p.y()})
