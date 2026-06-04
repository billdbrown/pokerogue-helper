import re
import biome_db
import window_state
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal


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
    wave_changed = pyqtSignal(int)   # fires only when wave number changes


# ── Panel ─────────────────────────────────────────────────────────────────────

class WavePanel(QWidget):
    def __init__(self, wave_box=None, embedded: bool = False):
        super().__init__()
        self._drag_pos      = None
        self._embedded      = embedded
        self._current_wave  = None
        self._current_biome = None

        self._signals = _Signals()

        if not embedded:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedWidth(220)

        self._build_ui()

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

        self._biome_lbl = QLabel("")
        self._biome_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._biome_lbl.setWordWrap(True)
        self._biome_lbl.setStyleSheet("color:#94e2d5; font-size:12px; font-weight:bold;")
        self._biome_lbl.setVisible(False)
        inner.addWidget(self._biome_lbl)

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

        self._wave_lbl = QLabel("W —")
        self._wave_lbl.setStyleSheet("color:#cdd6f4; font-size:11px; font-weight:bold;")
        self._wave_lbl.setFixedWidth(50)
        root.addWidget(self._wave_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet("background:#44475a;")
        root.addWidget(sep)

        self._biome_lbl = QLabel("")
        self._biome_lbl.setStyleSheet("color:#94e2d5; font-size:10px; font-weight:bold;")
        self._biome_lbl.setMinimumWidth(250)
        self._biome_lbl.setVisible(False)
        root.addWidget(self._biome_lbl)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFixedWidth(1)
        sep2.setStyleSheet("background:#44475a;")
        root.addWidget(sep2)

        self._event_rows = []
        for _ in range(3):
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

    # ── State receivers ───────────────────────────────────────────────────────

    def receive_wave_text(self, text: str) -> None:
        """Called by JSStateService dispatcher with the live wave number as a string."""
        digits = re.sub(r'[^0-9]', '', text)[:3]
        if not digits:
            return
        val = int(digits)
        if not (1 <= val <= 200):
            return
        if val == self._current_wave:
            return
        self._current_wave = val
        self._signals.wave_changed.emit(val)
        self._render_wave(val)

    def set_biome(self, biome_id) -> None:
        """Called by the JSStateService dispatcher with the live biome id."""
        if biome_id == self._current_biome:
            return
        self._current_biome = biome_id
        self._render_biome()

    def clear_wave(self) -> None:
        """Reset wave display. Called by New Run."""
        self._current_wave = None
        if hasattr(self, '_wave_lbl'):
            self._wave_lbl.setText("W —" if self._embedded else "WAVE  —")
        for w_lbl, n_lbl in getattr(self, '_event_rows', []):
            w_lbl.setText("")
            n_lbl.setText("")
        self.clear_biome()

    def clear_biome(self) -> None:
        self._current_biome = None
        if hasattr(self, '_biome_lbl'):
            self._biome_lbl.setText("")
            self._biome_lbl.setVisible(False)

    # ── Display ───────────────────────────────────────────────────────────────

    def _render_wave(self, wave: int):
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
        self._render_biome()
        if not self._embedded:
            self.adjustSize()

    def _render_biome(self):
        lbl = getattr(self, '_biome_lbl', None)
        if lbl is None:
            return
        name = biome_db.biome_display(self._current_biome)
        if not name:
            lbl.setText("")
            lbl.setVisible(False)
            return
        text = name.upper()
        nexts = biome_db.next_biomes(self._current_biome)
        if nexts:
            nxt = " / ".join(n["display"] for n in nexts[:2])
            wave = self._current_wave or 0
            rem = 10 - (wave % 10) if wave else 0
            ctd = f" (in {rem})" if 1 <= rem <= 9 else ""
            text = f"{text} → {nxt}{ctd}"
        lbl.setText(text)
        lbl.setVisible(True)
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
