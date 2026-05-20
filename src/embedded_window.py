"""
EmbeddedMainWindow — fixed single-window layout:

  ┌──────────────┬────────────────────────────────────┐
  │ Wave (1)     │                                    │
  ├──────────────┤        pokerogue.net               │
  │ Opponents (3)│                                    │
  │              │                                    │
  ├──────────────┤                                    │
  │              │                                    │
  │ My Team (6)  │                                    │
  │              │                                    │
  └──────────────┴────────────────────────────────────┘

Left column is 340 px wide with a 1:3:6 height ratio between panels.
CaptureBoxes remain floating Tool windows so they can overlay the GPU web view.
"""

import os
import re
import sys
import window_state
import box_positions
from ui_state import UIState

def _read_version() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        # In source layout this file lives at src/embedded_window.py; version.txt
        # is one level up at the project root (kept there so build.ps1 and the
        # PyInstaller spec can read it without path gymnastics).
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "version.txt")) as f:
            return f.read().strip()
    except OSError:
        return "?"

_VERSION = _read_version()
from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QScrollArea, QFrame, QSizePolicy,
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QLabel,
    QDialog, QCheckBox, QDialogButtonBox, QComboBox, QMessageBox,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEngineSettings, QWebEnginePage, QWebEngineScript, QWebEngineProfile,
)
from PyQt6.QtCore import Qt, QUrl, QPoint, QTimer, QByteArray

from capture_box import CaptureBox, BOX_W, BOX_H
from overlay import OverlayPanel
from team_panel import TeamPanel
from wave_panel import WavePanel
from analysis_panel import AnalysisPanel
from active_tracker import ActiveTracker
from ocr_service import OCRService, OCRBox, PreprocessMode
from ocr_debug_window import OCRDebugWindow
from type_color_service import TypeColorService, ColorBox

_LEFT_WIDTH = 390
_NAVBAR_H   = 34
_TEAM_H     = 178
_FIXED_W    = 1600
_FIXED_H    = 900

_JS_LEVELS    = {0: "Info", 1: "Warning", 2: "Error"}
_LOG_PATH     = "embedded_console.log"
_PROFILE_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_data")


# ── JS console capture ────────────────────────────────────────────────────────

class _LoggingPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, parent=None):
        super().__init__(profile, parent)

    def javaScriptConsoleMessage(self, level, message, line, source):
        tag = _JS_LEVELS.get(level.value, f"L{level.value}")
        text = f"[JS {tag}] {source}:{line}  {message}"
        if level.value >= 2:  # only print Errors to console; Info/Warning go to log only
            print(text)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n")


# ── Opponent layout grouping ─────────────────────────────────────────────────

class _OpponentLayout:
    """Groups the CaptureBoxes that belong to one opponent slot."""
    def __init__(self, name_box, level_box, type1_box, type2_box, boss_box=None):
        self.name_box  = name_box
        self.level_box = level_box
        self.type1_box = type1_box
        self.type2_box = type2_box
        self.boss_box  = boss_box

    def boxes(self):
        result = [self.name_box, self.level_box, self.type1_box, self.type2_box]
        if self.boss_box is not None:
            result.append(self.boss_box)
        return result

    def apply_positions(self, origin, saved: dict, defaults: dict):
        items = [('name', self.name_box), ('level', self.level_box),
                 ('type1', self.type1_box), ('type2', self.type2_box)]
        if self.boss_box is not None:
            items.append(('boss', self.boss_box))
        for key, box in items:
            s = saved.get(key, {})
            if 'dx' in s and 'dy' in s:
                box.move(origin.x() + s['dx'], origin.y() + s['dy'])
                if box._resizable and 'w' in s and 'h' in s:
                    box.resize(s['w'], s['h'])
            elif key in defaults:
                d = defaults[key]
                box.move(origin.x() + d[0], origin.y() + d[1])
                if box._resizable and len(d) >= 4:
                    box.resize(d[2], d[3])


# Default positions per slot per preset: (dx, dy) or (dx, dy, w, h) for resizable.
# All values are web-view offsets. 2v2 and boss presets share 1v1_standard defaults
# until calibrated via the Configure OCR window positions UI.
_SLOT_DEFAULTS: dict = {
    0: {
        '1v1_standard': {
            'name':  (62,   98),
            'level': (361,  93, 102, 67),
            'type1': (481, 107),
            'type2': (481, 173),
            'boss':  (640, 131),
        },
        '1v1_boss': {
            'name':  ( 62,  98),
            'level': (535,  91, 102, 67),
            'type1': (660, 105),
            'type2': (659, 174),
            'boss':  (640, 131),
        },
        '2v2_standard': {
            'name':  (62,   98),
            'level': (361,  93, 102, 67),
            'type1': (482, 107),
            'type2': (481, 173),
            'boss':  (640, 131),
        },
        '2v2_boss': {
            'name':  (62,   98),
            'level': (361,  93, 102, 67),
            'type1': (482, 107),
            'type2': (481, 173),
            'boss':  (640, 131),
        },
    },
    1: {
        '2v2_standard': {
            'name':  (25,  197),
            'level': (328, 194,  98, 61),
            'type1': (444, 208),
            'type2': (443, 276),
            'boss':  (520, 208),
        },
        '2v2_boss': {
            'name':  (25,  197),
            'level': (328, 194,  98, 61),
            'type1': (444, 208),
            'type2': (443, 276),
            'boss':  (520, 208),
        },
    },
}

# Fixed boxes — position never changes between 1v1 and 2v2.
_GLOBAL_FIXED_DEFAULTS: dict = {
    'wave':     (QPoint(1138, -10),  65,  60),
    'wave2':    (QPoint(1141,  45),  61,  61),
    'move1':    (QPoint(  66, 518), 322,  73),
    'move2':    (QPoint( 491, 519), 345,  75),
    'move3':    (QPoint(  65, 580), 322,  76),
    'move4':    (QPoint( 491, 580), 343,  78),
    'accuracy': (QPoint( 936, 609), 125,  54),
}

# Player card boxes — positions differ between 1v1 and 2v2 because the layout shifts.
_PLAYER_KEYS_1V1 = ('active', 'player_lv', 'player_type1', 'player_type2')
_PLAYER_KEYS_2V2 = ('active', 'player_lv', 'player_type1', 'player_type2',
                    'active2', 'player2_lv', 'player2_type1', 'player2_type2')

_GLOBAL_PLAYER_DEFAULTS: dict = {
    '1v1': {
        'active':       (QPoint( 728, 340), BOX_W, BOX_H),
        'player_lv':    (QPoint(1034, 331), 102,   67),
        'player_type1': (QPoint( 705, 373),  11,   11),
        'player_type2': (QPoint( 705, 442),  11,   11),
    },
    '2v2': {
        'active':        (QPoint( 731, 290), BOX_W, BOX_H),
        'player_lv':     (QPoint(1035, 281), 102,   67),
        'player_type1':  (QPoint( 720, 300),  11,   11),
        'player_type2':  (QPoint( 718, 367),  11,   11),
        'active2':       (QPoint( 769, 391), BOX_W, BOX_H),
        'player2_lv':    (QPoint(1073, 383),  99,   64),
        'player2_type1': (QPoint( 758, 400),  11,   11),
        'player2_type2': (QPoint( 755, 465),  11,   11),
    },
}


# ── Settings dialog ───────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, parent, boxes: list, enemy_panel, ocr_service, ocr_debug_win):
        super().__init__(parent)
        self._boxes       = boxes
        self._main_win    = parent
        self._enemy       = enemy_panel
        self._ocr_service = ocr_service
        self._ocr_debug   = ocr_debug_win
        # Dirty tracking for configure mode: snapshot is the box layout captured
        # the last time we applied or saved a preset. If positions diverge before
        # the user switches preset, we prompt rather than silently discarding.
        self._configure_snapshot:   dict | None  = None
        self._previous_dropdowns:   tuple | None = None
        self.setWindowTitle("Settings")
        self.setModal(False)
        self.setMinimumWidth(300)
        self.setStyleSheet("""
            QDialog      { background: #1e1e2e; }
            QLabel       { color: #cdd6f4; font-size: 13px; }
            QCheckBox    { color: #cdd6f4; font-size: 12px; spacing: 8px; }
            QCheckBox::indicator { width: 14px; height: 14px;
                                   border: 1px solid #45475a; border-radius: 3px;
                                   background: #313244; }
            QCheckBox::indicator:checked { background: #89b4fa; border-color: #89b4fa; }
            QPushButton  { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                           border-radius: 4px; padding: 4px 12px; font-size: 12px; }
            QPushButton:hover { background: #45475a; }
            QComboBox    { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                           border-radius: 4px; padding: 2px 6px; font-size: 12px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #313244; color: #cdd6f4;
                                          selection-background-color: #45475a; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        hdr = QLabel("Debug")
        hdr.setStyleSheet("color:#6c7086; font-size:10px; font-weight:bold; letter-spacing:1px;")
        layout.addWidget(hdr)

        self._ocr_check = QCheckBox("Show OCR windows")
        self._ocr_check.setChecked(False)
        self._ocr_check.toggled.connect(self._toggle_ocr)
        layout.addWidget(self._ocr_check)

        self._configure_check = QCheckBox("Configure OCR window positions")
        self._configure_check.setChecked(False)
        self._configure_check.toggled.connect(self._toggle_configure)
        layout.addWidget(self._configure_check)

        # Configure panel — hidden until configure mode is active
        self._configure_panel = QWidget()
        cfg = QVBoxLayout(self._configure_panel)
        cfg.setContentsMargins(12, 0, 0, 0)
        cfg.setSpacing(6)

        fight_row = QHBoxLayout()
        fight_row.addWidget(QLabel("Fight:"))
        self._fight_combo = QComboBox()
        self._fight_combo.addItems(["1v1", "2v2"])
        self._fight_combo.currentIndexChanged.connect(self._on_preset_changed)
        fight_row.addWidget(self._fight_combo)
        cfg.addLayout(fight_row)

        op1_row = QHBoxLayout()
        op1_row.addWidget(QLabel("Op1:"))
        self._op1_combo = QComboBox()
        self._op1_combo.addItems(["Standard", "Boss"])
        self._op1_combo.currentIndexChanged.connect(self._on_preset_changed)
        op1_row.addWidget(self._op1_combo)
        cfg.addLayout(op1_row)

        op2_row = QHBoxLayout()
        self._op2_label = QLabel("Op2:")
        op2_row.addWidget(self._op2_label)
        self._op2_combo = QComboBox()
        self._op2_combo.addItems(["Standard", "Boss"])
        self._op2_combo.currentIndexChanged.connect(self._on_preset_changed)
        op2_row.addWidget(self._op2_combo)
        cfg.addLayout(op2_row)

        self._save_btn = QPushButton("Save positions for this layout")
        self._save_btn.clicked.connect(self._save_positions)
        cfg.addWidget(self._save_btn)

        self._save_label = QLabel("")
        self._save_label.setStyleSheet("color:#6c7086; font-size:10px;")
        self._save_label.setWordWrap(True)
        cfg.addWidget(self._save_label)

        self._configure_panel.hide()
        layout.addWidget(self._configure_panel)

        self._weak_check = QCheckBox("Show opponent weaknesses")
        self._weak_check.setChecked(False)
        self._weak_check.toggled.connect(self._enemy.set_weaknesses_visible)
        layout.addWidget(self._weak_check)

        self._ocr_debug_check = QCheckBox("Show OCR debug view")
        self._ocr_debug_check.setChecked(False)
        self._ocr_debug_check.toggled.connect(self._toggle_ocr_debug)
        layout.addWidget(self._ocr_debug_check)

        self._wave_untrigger_check = QCheckBox("Wave change resets to 1v1 (needs reliable wave OCR)")
        self._wave_untrigger_check.setChecked(self._main_win._wave_untrigger_enabled)
        self._wave_untrigger_check.toggled.connect(
            lambda v: setattr(self._main_win, '_wave_untrigger_enabled', v)
        )
        layout.addWidget(self._wave_untrigger_check)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.hide)
        layout.addWidget(btns)

    def _toggle_ocr(self, checked: bool):
        if not self._configure_check.isChecked():
            for box in self._boxes:
                box.setVisible(checked)

    def _toggle_configure(self, checked: bool):
        self._configure_panel.setVisible(checked)
        self._main_win._configure_mode = checked
        if checked:
            # Force boxes visible and interactive
            self._ocr_check.setChecked(True)
            for box in self._boxes:
                box.setVisible(True)
                box.set_interactive(True)
            # Reset dirty tracking; the _on_preset_changed call below will snapshot fresh.
            self._configure_snapshot = None
            self._previous_dropdowns = None
            self._on_preset_changed()
        else:
            # Restore non-interactive, snap back to live UIState positions
            for box in self._boxes:
                box.set_interactive(False)
                box.setVisible(self._ocr_check.isChecked())
            # Reload global boxes from saved json — configure mode allows accidental drags
            mw = self._main_win
            _data = box_positions.load()
            mw._position_global_boxes(mw._web.mapToGlobal(QPoint(0, 0)), _data.get('global', {}))
            mw._apply_ui_state()
            # Drop dirty tracking so the next configure session starts clean.
            self._configure_snapshot = None
            self._previous_dropdowns = None
            self._save_label.setText("")
        self.adjustSize()

    def _on_preset_changed(self):
        if not self._configure_check.isChecked():
            return
        # Dirty check: if positions have diverged from the last snapshot, prompt
        # before discarding the in-progress drag.
        if self._configure_snapshot is not None:
            current = self._main_win._capture_visible_positions()
            if current != self._configure_snapshot:
                reply = QMessageBox.question(
                    self,
                    "Unsaved position changes",
                    "Unsaved drags to the current preset will be lost. Discard?",
                    QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Discard:
                    # Revert dropdowns without re-firing this handler
                    if self._previous_dropdowns is not None:
                        f, o1, o2 = self._previous_dropdowns
                        for combo, idx in ((self._fight_combo, f),
                                           (self._op1_combo, o1),
                                           (self._op2_combo, o2)):
                            combo.blockSignals(True)
                            combo.setCurrentIndex(idx)
                            combo.blockSignals(False)
                    return
        fight_mode = '1v1' if self._fight_combo.currentIndex() == 0 else '2v2'
        op1_boss = self._op1_combo.currentIndex() == 1
        op2_boss = self._op2_combo.currentIndex() == 1
        # Disable Op2 selector in 1v1 (can't exist)
        is_2v2 = fight_mode == '2v2'
        self._op2_combo.setEnabled(is_2v2)
        self._op2_label.setEnabled(is_2v2)
        self._main_win._apply_configure_preset(fight_mode, op1_boss, op2_boss)
        # Re-snapshot now that boxes are in their canonical preset positions.
        self._configure_snapshot = self._main_win._capture_visible_positions()
        self._previous_dropdowns = (
            self._fight_combo.currentIndex(),
            self._op1_combo.currentIndex(),
            self._op2_combo.currentIndex(),
        )
        self._update_save_label(fight_mode, op1_boss, op2_boss)

    def _save_positions(self):
        fight_mode = '1v1' if self._fight_combo.currentIndex() == 0 else '2v2'
        op1_boss = self._op1_combo.currentIndex() == 1
        op2_boss = self._op2_combo.currentIndex() == 1
        self._main_win._save_configure_preset(fight_mode, op1_boss, op2_boss)
        # Refresh snapshot — current positions are now the persisted truth.
        self._configure_snapshot = self._main_win._capture_visible_positions()

    def _update_save_label(self, fight_mode: str, op1_boss: bool, op2_boss: bool):
        op1 = 'boss' if op1_boss else 'standard'
        parts = [f"slot0={fight_mode}_{op1}"]
        if fight_mode == '2v2':
            op2 = 'boss' if op2_boss else 'standard'
            parts.append(f"slot1=2v2_{op2}")
        parts.append(f"player={fight_mode}")
        self._save_label.setText("Will write: " + ", ".join(parts))

    def _toggle_ocr_debug(self, checked: bool):
        self._ocr_service.set_debug_enabled(checked)
        self._main_win._type_color_service.set_debug_enabled(checked)
        if checked:
            self._ocr_debug.show()
        else:
            self._ocr_debug.hide()


# ── Navbar style ──────────────────────────────────────────────────────────────

_NAV_STYLE = """
    QWidget#navbar  { background: #181825; border-bottom: 1px solid #313244; }
    QLineEdit       { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                      border-radius: 4px; padding: 2px 8px; font-size: 12px; }
    QLineEdit:focus { border-color: #89b4fa; }
    QPushButton     { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                      border-radius: 4px; padding: 3px 10px; font-size: 12px; }
    QPushButton:hover   { background: #45475a; }
    QPushButton:pressed { background: #585b70; }
    QLabel#status   { color: #6c7086; font-size: 10px; padding: 0 6px; }
"""


# ── Main window ───────────────────────────────────────────────────────────────

class EmbeddedMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pokerogue Helper")
        self.setStyleSheet("QMainWindow { background: #181825; }")

        state = window_state.load()
        ws = state.get("embedded_window", {})
        self.move(ws.get("x", 80), ws.get("y", 40))
        self.setFixedSize(_FIXED_W, _FIXED_H)

        # ── Floating capture boxes (overlay GPU web view) ─────────────────
        # No state_key — positions are managed as window offsets in closeEvent
        self._box1            = CaptureBox(label="1",   parent=self)
        self._box2            = CaptureBox(label="2",   parent=self)
        self._wave_box        = CaptureBox(label="W1",  width=95, height=115, resizable=True, parent=self)
        self._wave_box2       = CaptureBox(label="W2",  width=95, height=115, resizable=True, parent=self)
        self._active_box      = CaptureBox(label="A",   parent=self)
        self._opp_level_box   = CaptureBox(label="OL",  resizable=True, parent=self)
        self._opp2_level_box  = CaptureBox(label="OL2", resizable=True, parent=self)
        self._player_lv_box   = CaptureBox(label="PL",  resizable=True, parent=self)
        self._move_boxes      = [CaptureBox(label=f"M{i+1}", resizable=True, parent=self) for i in range(4)]
        self._accuracy_box    = CaptureBox(label="AC",  resizable=True, parent=self)
        self._enemy_type1_box  = CaptureBox(label="", width=11, height=11, parent=self)
        self._enemy_type2_box  = CaptureBox(label="", width=11, height=11, parent=self)
        self._enemy2_type1_box = CaptureBox(label="", width=11, height=11, parent=self)
        self._enemy2_type2_box = CaptureBox(label="", width=11, height=11, parent=self)
        self._player_type1_box  = CaptureBox(label="", width=11, height=11, parent=self)
        self._player_type2_box  = CaptureBox(label="", width=11, height=11, parent=self)
        self._active2_box       = CaptureBox(label="A2", parent=self)
        self._player2_lv_box    = CaptureBox(label="PL2", resizable=True, parent=self)
        self._player2_type1_box = CaptureBox(label="", width=11, height=11, parent=self)
        self._player2_type2_box = CaptureBox(label="", width=11, height=11, parent=self)
        self._enemy_boss_box    = CaptureBox(label="", width=11, height=11, parent=self)
        self._enemy2_boss_box   = CaptureBox(label="", width=11, height=11, parent=self)
        self._ocr_boxes = [
            self._box1, self._box2, self._wave_box, self._wave_box2,
            self._active_box, self._active2_box,
            self._opp_level_box, self._opp2_level_box,
            self._player_lv_box, self._player2_lv_box,
            *self._move_boxes,
            self._accuracy_box,
            self._enemy_type1_box,   self._enemy_type2_box,
            self._enemy2_type1_box,  self._enemy2_type2_box,
            self._player_type1_box,  self._player_type2_box,
            self._player2_type1_box, self._player2_type2_box,
            self._enemy_boss_box,    self._enemy2_boss_box,
        ]

        # ── UI state + layout groupings ───────────────────────────────────
        self._ui_state = UIState()
        self._configure_mode = False
        self._in_fight = False
        self._w1_last_read: str | None = None  # raw W1 text from current cycle; W2 callback combines them
        # Default un-trigger for 2v2→1v1 is the box2 name-miss path (more reliable
        # while wave OCR is fragile). Wave-change un-trigger is opt-in via Settings.
        self._wave_untrigger_enabled = False
        self._slot_layouts = [
            _OpponentLayout(self._box1, self._opp_level_box,
                            self._enemy_type1_box, self._enemy_type2_box,
                            self._enemy_boss_box),
            _OpponentLayout(self._box2, self._opp2_level_box,
                            self._enemy2_type1_box, self._enemy2_type2_box,
                            self._enemy2_boss_box),
        ]

        # ── Web view ──────────────────────────────────────────────────────
        self._web = QWebEngineView()
        self._configure_web()

        # ── Panels ────────────────────────────────────────────────────────
        self._wave     = WavePanel(self._wave_box, embedded=True)
        self._enemy    = OverlayPanel([self._box1, self._box2], embedded=True)
        self._analysis = AnalysisPanel()
        self._team     = TeamPanel(strip=True)
        self._team.set_analysis_panel(self._analysis)
        self._team.load_saved_team()

        self._active_tracker  = ActiveTracker()
        self._active_tracker2 = ActiveTracker()
        self._active_tracker.active_changed.connect(self._team.set_active_slot)
        self._active_tracker.active_changed.connect(self._enemy.set_active_pokemon)
        self._active_tracker2.active_changed.connect(
            lambda name: self._team.set_active_slot(name, position=1)
        )

        _ocr_labels = ["box1", "box2", "active", "active2", "wave", "wave2",
                        "opp_level", "opp2_level",
                        "player_level", "player2_level",
                        "move1", "move2", "move3", "move4",
                        "enemy_type1", "enemy_type2",
                        "enemy2_type1", "enemy2_type2",
                        "player_type1", "player_type2",
                        "player2_type1", "player2_type2"]
        self._ocr_debug_win = OCRDebugWindow(_ocr_labels)
        self._ocr_service = OCRService(interval_ms=500, parent=self)
        self._ocr_service._signals.debug.connect(self._ocr_debug_win.update_box)
        self._ocr_service.register(OCRBox(self._box1,            PreprocessMode.NAME,  lambda t: self._enemy.receive_ocr_result(0, t), "box1",          gated=False))
        self._ocr_service.register(OCRBox(self._box2,            PreprocessMode.NAME,  lambda t: self._enemy.receive_ocr_result(1, t), "box2",          gated=False))
        self._ocr_service.register(OCRBox(self._active_box,      PreprocessMode.NAME,  lambda t: self._active_tracker.receive_ocr_result(t),  "active"))
        self._ocr_service.register(OCRBox(self._active2_box,     PreprocessMode.NAME,  lambda t: self._active_tracker2.receive_ocr_result(t), "active2",  enabled=False))
        # Wave is read by two boxes (W1 and W2) covering different on-screen positions
        # the game may place the wave text. A single box may misread when it overlaps
        # a Pokémon icon or other UI element. W2 always fires after W1 in each cycle
        # (ungated boxes dispatch in registration order), so _on_wave2 is where we
        # combine the pair:
        #   - both read AND agree → feed value to corroborator
        #   - both read AND disagree → drop the cycle (no corroboration progress)
        #   - exactly one read → trust it
        #   - neither read → blank cycle, no progress
        # This blocks the failure mode where W1 OCRs a stray digit from a sprite
        # and suppresses W2's correct reading.
        def _digits(t: str) -> str:
            return re.sub(r'[^0-9]', '', t or '')[:3]
        def _on_wave1(t):
            self._w1_last_read = t
        def _on_wave2(t):
            w1 = _digits(self._w1_last_read)
            w2 = _digits(t)
            self._w1_last_read = None
            if w1 and w2:
                if w1 == w2:
                    self._wave.receive_wave_text(w1)
                else:
                    print(f"[wave] W1='{w1}' W2='{w2}' disagree, dropping cycle")
            elif w1:
                self._wave.receive_wave_text(w1)
            elif w2:
                self._wave.receive_wave_text(w2)
        self._ocr_service.register(OCRBox(self._wave_box,  PreprocessMode.WAVE, _on_wave1, "wave",  gated=False))
        self._ocr_service.register(OCRBox(self._wave_box2, PreprocessMode.WAVE, _on_wave2, "wave2", gated=False))
        self._ocr_service.register(OCRBox(self._opp_level_box,   PreprocessMode.DIGIT, lambda t: self._enemy.receive_opponent_level(0, t), "opp_level",  gated=False))
        self._ocr_service.register(OCRBox(self._opp2_level_box,  PreprocessMode.DIGIT, lambda t: self._enemy.receive_opponent_level(1, t), "opp2_level", gated=False, enabled=False))
        self._ocr_service.register(OCRBox(self._player_lv_box,   PreprocessMode.DIGIT, lambda t: self._team.receive_player_level(t, position=0), "player_level"))
        self._ocr_service.register(OCRBox(self._player2_lv_box,  PreprocessMode.DIGIT, lambda t: self._team.receive_player_level(t, position=1), "player2_level", enabled=False))
        for i, mb in enumerate(self._move_boxes):
            self._ocr_service.register(OCRBox(mb, PreprocessMode.MOVE_NAME, lambda t, idx=i: self._team.receive_move_ocr(idx, t), f"move{i+1}"))
        self._ocr_service.register(OCRBox(self._accuracy_box, PreprocessMode.MOVE_NAME, lambda t: None, "accuracy"))
        self._ocr_service.set_gate("accuracy")
        self._ocr_service.start()

        self._type_color_service = TypeColorService(interval_ms=500, parent=self)
        self._type_color_service._signals.debug.connect(self._ocr_debug_win.update_box)
        self._type_color_service.register(ColorBox(
            self._enemy_type1_box,
            lambda t: self._on_type_sample(0, 0, t),
            "enemy_type1",
        ))
        self._type_color_service.register(ColorBox(
            self._enemy_type2_box,
            lambda t: self._on_type_sample(0, 1, t),
            "enemy_type2",
        ))
        self._type_color_service.register(ColorBox(
            self._enemy2_type1_box,
            lambda t: self._on_type_sample(1, 0, t),
            "enemy2_type1",
            enabled=False,
        ))
        self._type_color_service.register(ColorBox(
            self._enemy2_type2_box,
            lambda t: self._on_type_sample(1, 1, t),
            "enemy2_type2",
            enabled=False,
        ))
        self._type_color_service.register(ColorBox(
            self._enemy_boss_box,
            lambda t: self._on_boss_sample(0, t),
            "enemy_boss",
        ))
        self._type_color_service.register(ColorBox(
            self._enemy2_boss_box,
            lambda t: self._on_boss_sample(1, t),
            "enemy2_boss",
            enabled=False,
        ))
        self._type_color_service.register(ColorBox(
            self._player_type1_box,
            lambda t: (self._team.receive_player_type_sample(0, t, position=0), self._active_tracker.notify_type(t is not None)),
            "player_type1",
        ))
        self._type_color_service.register(ColorBox(
            self._player_type2_box,
            lambda t: self._team.receive_player_type_sample(1, t, position=0),
            "player_type2",
        ))
        self._type_color_service.register(ColorBox(
            self._player2_type1_box,
            lambda t: (self._team.receive_player_type_sample(0, t, position=1), self._active_tracker2.notify_type(t is not None)),
            "player2_type1",
            enabled=False,
        ))
        self._type_color_service.register(ColorBox(
            self._player2_type2_box,
            lambda t: self._team.receive_player_type_sample(1, t, position=1),
            "player2_type2",
            enabled=False,
        ))
        self._type_color_service.start()

        # ── UIRE trigger connections ──────────────────────────────────────
        self._enemy.slot1_visible.connect(self._on_slot1_visible)
        self._wave.wave_changed.connect(self._on_wave_changed)
        self._ocr_service.gate_changed.connect(self._on_gate_changed)

        # ── Left panel: PokeHelper title + splitter ──────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(_NAVBAR_H)
        title_bar.setStyleSheet(
            "QWidget { background: #181825; border-bottom: 1px solid #313244; }"
        )
        title_hl = QHBoxLayout(title_bar)
        title_hl.setContentsMargins(8, 0, 8, 0)
        title_hl.setSpacing(5)
        title_lbl = QLabel("PokeHelper")
        title_lbl.setStyleSheet("color:#cdd6f4; font-size:13px; font-weight:bold;")
        title_lbl.setToolTip(f"Pokerogue Helper v{_VERSION}")
        title_hl.addWidget(title_lbl)

        _BADGE_OFF = ("font-size:10px; font-weight:bold; padding:2px 10px;"
                      " border-radius:3px; margin:5px 0; background:#313244; color:#6c7086;")
        def _badge(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(_BADGE_OFF)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return lbl

        self._badge_mode    = _badge("1V1")
        self._badge_fight   = _badge("Moves")
        self._badge_op1boss = _badge("Boss 1")
        self._badge_op2boss = _badge("Boss 2")
        self._badge_off_style = _BADGE_OFF
        for b in (self._badge_mode, self._badge_fight, self._badge_op1boss, self._badge_op2boss):
            title_hl.addWidget(b)
        self._new_run_btn = QPushButton("New Run")
        self._new_run_btn.setStyleSheet(
            "QPushButton { background:#313244; color:#cdd6f4; border:1px solid #45475a;"
            " border-radius:3px; padding:2px 10px; font-size:10px; font-weight:bold;"
            " margin:5px 4px; }"
            "QPushButton:hover { background:#45475a; }"
        )
        self._new_run_btn.setToolTip("Clear team, wave, and fight state for a fresh run")
        self._new_run_btn.clicked.connect(self._on_new_run)
        title_hl.addWidget(self._new_run_btn)
        title_hl.addStretch()

        splitter_h = _FIXED_H - _NAVBAR_H - _TEAM_H
        left_split = QSplitter(Qt.Orientation.Vertical)
        left_split.setStyleSheet("QSplitter::handle { background: #313244; height: 2px; }")
        left_split.addWidget(self._enemy)
        left_split.addWidget(self._analysis)
        left_split.setStretchFactor(0, 2)
        left_split.setStretchFactor(1, 3)
        left_split.setSizes([int(splitter_h * 0.4), int(splitter_h * 0.6)])
        self._left_splitter = left_split

        left_panel = QWidget()
        left_panel.setFixedWidth(_LEFT_WIDTH)
        left_panel.setStyleSheet("QWidget { background: #181825; }")
        left_vl = QVBoxLayout(left_panel)
        left_vl.setContentsMargins(0, 0, 0, 0)
        left_vl.setSpacing(0)
        left_vl.addWidget(title_bar)
        left_vl.addWidget(left_split, 1)

        # ── Right panel: navbar + browser ────────────────────────────────
        right_panel = QWidget()
        right_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_vl = QVBoxLayout(right_panel)
        right_vl.setContentsMargins(0, 0, 0, 0)
        right_vl.setSpacing(0)
        right_vl.addWidget(self._build_navbar())
        right_vl.addWidget(self._web, 1)

        # ── Top section: left panel + right panel ─────────────────────────
        top = QWidget()
        top.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        top_hl = QHBoxLayout(top)
        top_hl.setContentsMargins(0, 0, 0, 0)
        top_hl.setSpacing(0)
        top_hl.addWidget(left_panel)
        top_hl.addWidget(right_panel, 1)

        # ── Team strip (full width at bottom) ────────────────────────────
        self._team.setFixedHeight(_TEAM_H)

        # ── Central widget ────────────────────────────────────────────────
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(top, 1)
        cv.addWidget(self._team)
        self.setCentralWidget(central)

        # ── Settings dialog ───────────────────────────────────────────────
        self._settings_dlg = SettingsDialog(
            self, self._ocr_boxes, self._enemy,
            self._ocr_service, self._ocr_debug_win,
        )

        # ── Position capture boxes (hidden by default) ───────────────────
        for box in self._ocr_boxes:
            box.hide()

        # Restore after show() has settled so self.pos() is the true screen pos.
        # Offset-based storage means positions survive window moves between sessions.
        QTimer.singleShot(300, lambda: (self._position_boxes(), self._push_debug_state()))

        # Enable move-tracking only after startup positioning is complete
        QTimer.singleShot(600, lambda: setattr(self, '_boxes_ready', True))

        # Focus the web view on launch so keyboard input goes straight to the game
        # without the user needing to click first. Delayed to let the page mount.
        QTimer.singleShot(800, self._web.setFocus)

        saved = state.get("embedded_splitter_state")
        if saved:
            left_split.restoreState(QByteArray.fromBase64(saved.encode()))

    # ── Web view setup ────────────────────────────────────────────────────────

    def _configure_web(self):
        profile = QWebEngineProfile("pokerogue_helper", self._web)
        profile.setPersistentStoragePath(_PROFILE_DIR)
        profile.setCachePath(os.path.join(_PROFILE_DIR, "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self._web.setPage(_LoggingPage(profile, self._web))

        cfg = self._web.settings()
        cfg.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled,      True)
        cfg.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled,   True)
        cfg.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        cfg.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled,        True)
        try:
            cfg.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        except AttributeError:
            pass

        self._inject_video_patch()
        self._web.load(QUrl("https://pokerogue.net"))
        self._web.loadStarted.connect(self._on_load_started)
        self._web.loadFinished.connect(self._on_load_finished)
        self._web.urlChanged.connect(self._on_url_changed)

    def _inject_video_patch(self):
        script = QWebEngineScript()
        script.setName("video_canplay_patch")
        script.setSourceCode("""
            (function () {
                var _orig = HTMLVideoElement.prototype.canPlayType;
                HTMLVideoElement.prototype.canPlayType = function (type) {
                    var r = _orig.call(this, type);
                    if (!r && type &&
                        (type.indexOf('webm') !== -1 ||
                         type.indexOf('mp4')  !== -1 ||
                         type.indexOf('ogg')  !== -1)) {
                        return 'maybe';
                    }
                    return r;
                };
            })();
        """)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        self._web.page().scripts().insert(script)

    # ── Navbar ────────────────────────────────────────────────────────────────

    def _build_navbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("navbar")
        bar.setFixedHeight(34)
        bar.setStyleSheet(_NAV_STYLE)

        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(4)

        back_btn = QPushButton("◀")
        back_btn.setFixedWidth(30)
        back_btn.clicked.connect(self._web.back)
        row.addWidget(back_btn)

        fwd_btn = QPushButton("▶")
        fwd_btn.setFixedWidth(30)
        fwd_btn.clicked.connect(self._web.forward)
        row.addWidget(fwd_btn)

        reload_btn = QPushButton("↺")
        reload_btn.setFixedWidth(30)
        reload_btn.clicked.connect(self._web.reload)
        row.addWidget(reload_btn)

        self._url_bar = QLineEdit()
        self._url_bar.setPlaceholderText("https://")
        self._url_bar.returnPressed.connect(self._navigate_to_url)
        row.addWidget(self._url_bar, 1)

        self._wave.setFixedWidth(630)
        row.addWidget(self._wave)

        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("status")
        row.addWidget(self._status_lbl)

        settings_btn = QPushButton("⚙")
        settings_btn.setFixedWidth(30)
        settings_btn.setToolTip("Settings")
        settings_btn.clicked.connect(self._open_settings)
        row.addWidget(settings_btn)

        return bar

    def _open_settings(self):
        if self._settings_dlg.isVisible():
            self._settings_dlg.hide()
        else:
            self._settings_dlg.show()
            self._settings_dlg.raise_()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _navigate_to_url(self):
        text = self._url_bar.text().strip()
        if text and "://" not in text:
            text = "https://" + text
        self._web.load(QUrl(text))

    def _on_load_started(self):
        self._status_lbl.setText("Loading…")

    def _on_load_finished(self, ok: bool):
        self._status_lbl.setText("" if ok else "Failed to load")

    def _on_url_changed(self, url: QUrl):
        self._url_bar.setText(url.toString())

    # ── Capture box positioning ───────────────────────────────────────────────

    def _position_boxes(self):
        origin = self._web.mapToGlobal(QPoint(0, 0))
        data = box_positions.load()
        self._position_fixed_boxes(origin, data.get('global', {}))
        mode = self._ui_state.fight_mode
        self._position_player_boxes(origin, mode, data.get(f'global_{mode}', {}))
        for slot in range(2):
            self._apply_slot_layout(slot, origin, data)
        if not self._ui_state.is_2v2():
            self._park_slot1_layout(origin, data)

    def _position_fixed_boxes(self, origin: QPoint, saved: dict):
        _MAP = {
            'wave':     self._wave_box,
            'wave2':    self._wave_box2,
            'move1':    self._move_boxes[0],
            'move2':    self._move_boxes[1],
            'move3':    self._move_boxes[2],
            'move4':    self._move_boxes[3],
            'accuracy': self._accuracy_box,
        }
        for key, box in _MAP.items():
            s = saved.get(key, {})
            if 'dx' in s and 'dy' in s:
                box.move(origin.x() + s['dx'], origin.y() + s['dy'])
                if box._resizable and 'w' in s and 'h' in s:
                    box.resize(s['w'], s['h'])
            else:
                pt, w, h = _GLOBAL_FIXED_DEFAULTS[key]
                box.move(origin + pt)
                if box._resizable:
                    box.resize(w, h)

    def _position_player_boxes(self, origin: QPoint, mode: str, saved: dict):
        _ALL = {
            'active':        self._active_box,
            'player_lv':     self._player_lv_box,
            'player_type1':  self._player_type1_box,
            'player_type2':  self._player_type2_box,
            'active2':       self._active2_box,
            'player2_lv':    self._player2_lv_box,
            'player2_type1': self._player2_type1_box,
            'player2_type2': self._player2_type2_box,
        }
        keys = _PLAYER_KEYS_2V2 if mode == '2v2' else _PLAYER_KEYS_1V1
        defaults = _GLOBAL_PLAYER_DEFAULTS[mode]
        for key in keys:
            box = _ALL[key]
            s = saved.get(key, {})
            if 'dx' in s and 'dy' in s:
                box.move(origin.x() + s['dx'], origin.y() + s['dy'])
                if box._resizable and 'w' in s and 'h' in s:
                    box.resize(s['w'], s['h'])
            else:
                pt, w, h = defaults[key]
                box.move(origin + pt)
                if box._resizable:
                    box.resize(w, h)

    # Keep old name as alias so _toggle_configure can still call it for fixed-box restore
    def _position_global_boxes(self, origin: QPoint, saved: dict):
        self._position_fixed_boxes(origin, saved)

    def _apply_slot_layout(self, slot: int, origin: QPoint = None, data: dict = None):
        if origin is None:
            origin = self._web.mapToGlobal(QPoint(0, 0))
        if data is None:
            data = box_positions.load()
        preset = self._ui_state.slot_preset(slot)
        if preset is None:
            return  # slot 1 in 1v1 — no positions to apply
        saved_slot = data.get(f'slot{slot}', {}).get(preset, {})
        defaults = _SLOT_DEFAULTS[slot].get(preset, {})
        self._slot_layouts[slot].apply_positions(origin, saved_slot, defaults)

    def _apply_ui_state(self):
        """Reposition all mode-dependent boxes to match the current UIState."""
        origin = self._web.mapToGlobal(QPoint(0, 0))
        data = box_positions.load()
        mode = self._ui_state.fight_mode
        self._position_player_boxes(origin, mode, data.get(f'global_{mode}', {}))
        for slot in range(2):
            self._apply_slot_layout(slot, origin, data)
        is_2v2 = self._ui_state.is_2v2()
        if not is_2v2:
            self._park_slot1_layout(origin, data)
            self._enemy.reset_slot1()
        for label in ("opp2_level", "active2", "player2_level"):
            self._ocr_service.set_enabled(label, is_2v2)
        for label in ("enemy2_type1", "enemy2_type2", "player2_type1", "player2_type2", "enemy2_boss"):
            self._type_color_service.set_enabled(label, is_2v2)

    def _park_slot1_layout(self, origin: QPoint, data: dict):
        """In 1v1 mode, cluster all slot-1 boxes at the 2v2_standard preset positions.

        Box2 (name OCR) needs to be there so it can detect opponent 2 appearing and
        trigger the 2v2 transition. The other slot-1 boxes (level, type1, type2, boss)
        are OCR-disabled in 1v1 but kept clustered so debug-visible boxes don't appear
        as orphans elsewhere on screen.
        """
        saved = data.get('slot1', {}).get('2v2_standard', {})
        defaults = _SLOT_DEFAULTS[1].get('2v2_standard', {})
        self._slot_layouts[1].apply_positions(origin, saved, defaults)

    def _on_type_sample(self, slot: int, type_index: int, type_name: str | None):
        self._enemy.receive_type_sample(slot, type_index, type_name)

    def _on_boss_sample(self, slot: int, type_name: str | None):
        # Strict: exact "_boss" match → boss; anything else (including None) → not boss.
        # No tolerance, no waiting for a positive non-boss type. The boss color box
        # is sampled at a position that should always render the plaque background
        # when a boss is present, and render something else (or nothing) when not.
        if self._configure_mode:
            return
        if slot == 1 and not self._ui_state.is_2v2():
            return
        was_boss = slot in self._ui_state.boss_mask
        is_boss = type_name == "_boss"
        if is_boss == was_boss:
            return
        if is_boss:
            self._ui_state.boss_mask.add(slot)
        else:
            self._ui_state.boss_mask.discard(slot)
        self._apply_slot_layout(slot)
        self._push_debug_state()

    def _on_gate_changed(self, open_: bool):
        self._in_fight = open_
        self._push_debug_state()

    def _on_new_run(self):
        """Hard reset: wipe team, wave, and UIState. Used between runs."""
        print("[new_run] clearing team, wave, and fight state")
        self._team.clear_all_slots()
        self._wave.clear_wave()
        self._ui_state.reset()
        self._apply_ui_state()
        self._push_debug_state()

    def _on_slot1_visible(self, visible: bool):
        """Fires when overlay detects/clears slot 1 opponent name. Drives 1v1↔2v2."""
        if self._configure_mode:
            return
        if visible and self._ui_state.fight_mode == '1v1':
            self._ui_state.fight_mode = '2v2'
            self._apply_ui_state()
            self._push_debug_state()
        elif not visible and self._ui_state.fight_mode == '2v2':
            self._ui_state.fight_mode = '1v1'
            self._apply_ui_state()
            self._push_debug_state()

    def _on_wave_changed(self, _wave: int):
        """Wave increment marks the start of a new opponent.

        Boss mask always clears (boss color will re-detect on the new opponent if
        applicable). Fight mode reverts to 1v1 only if the user has opted into
        the wave-change un-trigger — otherwise we rely on box2's name-miss path,
        which is safer while wave OCR can hallucinate.
        """
        print(f"[wave_changed] wave={_wave} fight_mode={self._ui_state.fight_mode} boss_mask={self._ui_state.boss_mask} configure={self._configure_mode} wave_untrigger={self._wave_untrigger_enabled}")
        if self._configure_mode:
            return
        changed = False
        if self._ui_state.boss_mask:
            self._ui_state.boss_mask.clear()
            changed = True
        if self._wave_untrigger_enabled and self._ui_state.fight_mode == '2v2':
            self._ui_state.fight_mode = '1v1'
            changed = True
        if changed:
            print(f"[wave_changed] → state now fight_mode={self._ui_state.fight_mode} boss_mask={self._ui_state.boss_mask}")
            self._apply_ui_state()
            self._push_debug_state()

    def _push_debug_state(self):
        fight_mode = self._ui_state.fight_mode
        in_fight   = self._in_fight
        op1_boss   = 0 in self._ui_state.boss_mask
        op2_boss   = 1 in self._ui_state.boss_mask
        is_2v2     = fight_mode == '2v2'

        self._ocr_debug_win.set_state(fight_mode, in_fight, op1_boss, op2_boss)

        _ON  = "font-size:10px; font-weight:bold; padding:2px 10px; border-radius:3px; margin:5px 0; color:#1e1e2e; background:{bg};"
        _OFF = self._badge_off_style
        self._badge_mode.setText(fight_mode.upper())
        self._badge_mode.setStyleSheet(_ON.format(bg="#cba6f7" if is_2v2 else "#89b4fa"))
        self._badge_fight.setStyleSheet(_ON.format(bg="#a6e3a1") if in_fight else _OFF)
        self._badge_op1boss.setStyleSheet(_ON.format(bg="#fab387") if op1_boss else _OFF)
        self._badge_op2boss.setStyleSheet(_ON.format(bg="#fab387") if (is_2v2 and op2_boss) else _OFF)

    def _apply_configure_preset(self, fight_mode: str, op1_boss: bool, op2_boss: bool):
        """Reposition boxes to show a specific preset (called from SettingsDialog)."""
        origin = self._web.mapToGlobal(QPoint(0, 0))
        data = box_positions.load()
        self._position_player_boxes(origin, fight_mode, data.get(f'global_{fight_mode}', {}))
        preset0 = f"{fight_mode}_{'boss' if op1_boss else 'standard'}"
        saved0 = data.get('slot0', {}).get(preset0, {})
        self._slot_layouts[0].apply_positions(origin, saved0, _SLOT_DEFAULTS[0].get(preset0, {}))
        if fight_mode == '2v2':
            preset1 = f"2v2_{'boss' if op2_boss else 'standard'}"
            saved1 = data.get('slot1', {}).get(preset1, {})
            self._slot_layouts[1].apply_positions(origin, saved1, _SLOT_DEFAULTS[1].get(preset1, {}))

    def _save_configure_preset(self, fight_mode: str, op1_boss: bool, op2_boss: bool):
        """Save current box positions to the specified preset (called from SettingsDialog)."""
        origin = self._web.mapToGlobal(QPoint(0, 0))
        data = box_positions.load()
        # Save player card boxes for this fight mode
        keys = _PLAYER_KEYS_2V2 if fight_mode == '2v2' else _PLAYER_KEYS_1V1
        _ALL = {
            'active':        self._active_box,
            'player_lv':     self._player_lv_box,
            'player_type1':  self._player_type1_box,
            'player_type2':  self._player_type2_box,
            'active2':       self._active2_box,
            'player2_lv':    self._player2_lv_box,
            'player2_type1': self._player2_type1_box,
            'player2_type2': self._player2_type2_box,
        }
        player_data = data.setdefault(f'global_{fight_mode}', {})
        for key in keys:
            box = _ALL[key]
            bp = box.pos()
            entry = {'dx': bp.x() - origin.x(), 'dy': bp.y() - origin.y()}
            if box._resizable:
                entry['w'] = box.width()
                entry['h'] = box.height()
            player_data[key] = entry
        # Save enemy slot boxes for this preset
        preset0 = f"{fight_mode}_{'boss' if op1_boss else 'standard'}"
        data.setdefault('slot0', {})[preset0] = self._capture_slot_positions(0, origin)
        if fight_mode == '2v2':
            preset1 = f"2v2_{'boss' if op2_boss else 'standard'}"
            data.setdefault('slot1', {})[preset1] = self._capture_slot_positions(1, origin)
        box_positions.save(data)

    def _capture_visible_positions(self) -> dict:
        """Snapshot every OCR box's position as window-relative (dx, dy, w, h).
        Used by configure mode to detect unsaved drags before switching preset."""
        origin = self._web.mapToGlobal(QPoint(0, 0))
        snapshot = {}
        for box in self._ocr_boxes:
            bp = box.pos()
            snapshot[id(box)] = (bp.x() - origin.x(), bp.y() - origin.y(),
                                 box.width(), box.height())
        return snapshot

    def _capture_slot_positions(self, slot: int, origin: QPoint) -> dict:
        layout = self._slot_layouts[slot]
        result = {}
        items = [('name', layout.name_box), ('level', layout.level_box),
                 ('type1', layout.type1_box), ('type2', layout.type2_box)]
        if layout.boss_box is not None:
            items.append(('boss', layout.boss_box))
        for key, box in items:
            bp = box.pos()
            entry = {'dx': bp.x() - origin.x(), 'dy': bp.y() - origin.y()}
            if box._resizable:
                entry['w'] = box.width()
                entry['h'] = box.height()
            result[key] = entry
        return result

    # ── Window movement — keep capture boxes in sync ──────────────────────────

    def moveEvent(self, event):
        super().moveEvent(event)
        if not getattr(self, '_boxes_ready', False):
            return
        delta = event.pos() - event.oldPos()
        if delta.x() or delta.y():
            for box in self._ocr_boxes:
                box.move(box.pos() + delta)

    # ── Persistence ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._type_color_service.stop()
        self._ocr_service.stop()
        g = self.geometry()
        window_state.save_key("embedded_window", {"x": g.x(), "y": g.y()})
        window_state.save_key(
            "embedded_splitter_state",
            self._left_splitter.saveState().toBase64().data().decode(),
        )
        # Save positions as offsets from web-view origin so they survive window moves.
        origin = self._web.mapToGlobal(QPoint(0, 0))
        data = box_positions.load()

        # Fixed boxes (wave, moves, accuracy) — never change between modes
        fixed_data = data.setdefault('global', {})
        _FIXED_MAP = {
            'wave':     self._wave_box,
            'wave2':    self._wave_box2,
            **{f'move{i+1}': mb for i, mb in enumerate(self._move_boxes)},
            'accuracy': self._accuracy_box,
        }
        for key, box in _FIXED_MAP.items():
            bp = box.pos()
            entry = {'dx': bp.x() - origin.x(), 'dy': bp.y() - origin.y()}
            if box._resizable:
                entry['w'] = box.width()
                entry['h'] = box.height()
            fixed_data[key] = entry

        # Preset-keyed boxes (player + slot) only auto-save when NOT in configure mode.
        # In configure mode the visible boxes can be at any preset the user is tuning;
        # trusting live UIState here would clobber whichever real preset matches it.
        # Explicit Save button is the only path to persist preset positions.
        if not self._configure_mode:
            # Player card boxes — save under the current fight mode
            mode = self._ui_state.fight_mode
            player_data = data.setdefault(f'global_{mode}', {})
            keys = _PLAYER_KEYS_2V2 if mode == '2v2' else _PLAYER_KEYS_1V1
            _ALL_PLAYER = {
                'active':        self._active_box,
                'player_lv':     self._player_lv_box,
                'player_type1':  self._player_type1_box,
                'player_type2':  self._player_type2_box,
                'active2':       self._active2_box,
                'player2_lv':    self._player2_lv_box,
                'player2_type1': self._player2_type1_box,
                'player2_type2': self._player2_type2_box,
            }
            for key in keys:
                box = _ALL_PLAYER[key]
                bp = box.pos()
                entry = {'dx': bp.x() - origin.x(), 'dy': bp.y() - origin.y()}
                if box._resizable:
                    entry['w'] = box.width()
                    entry['h'] = box.height()
                player_data[key] = entry

            # Slot boxes — save under the current active preset for each slot
            for slot in range(2):
                preset = self._ui_state.slot_preset(slot)
                if preset is None:
                    continue
                slot_data = data.setdefault(f'slot{slot}', {})
                slot_data[preset] = self._capture_slot_positions(slot, origin)

        box_positions.save(data)
        for box in self._ocr_boxes:
            box.close()
        self._ocr_debug_win.close()
        super().closeEvent(event)
