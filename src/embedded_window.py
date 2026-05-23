"""EmbeddedMainWindow — single-window layout:

  ┌──────────────┬────────────────────────────────────┐
  │ Title bar    │  Navbar (incl. wave tracker)       │
  ├──────────────┼────────────────────────────────────┤
  │ Opponents    │                                    │
  │              │        pokerogue.net               │
  ├──────────────┤        (Phaser canvas)             │
  │ Analysis     │                                    │
  ├──────────────┴────────────────────────────────────┤
  │ Team (6 slots)                                    │
  └───────────────────────────────────────────────────┘

All battle state comes from JSStateService, which polls the live Phaser scene
via runJavaScript every 300ms. The Function.prototype.bind hook in
_inject_phaser_capture catches the Phaser.Game instance at boot and stashes it
on window.__pokerogue_game__ for the extractor to read.
"""

import os
import sys
import window_state
from ui_state import UIState


def _read_version() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "version.txt")) as f:
            return f.read().strip()
    except OSError:
        return "?"


_VERSION = _read_version()


from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QSizePolicy,
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QLabel,
    QDialog, QCheckBox, QDialogButtonBox,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEngineSettings, QWebEnginePage, QWebEngineScript, QWebEngineProfile,
)
from PyQt6.QtCore import Qt, QUrl, QPoint, QTimer

from overlay import OverlayPanel
from team_panel import TeamPanel
from wave_panel import WavePanel
from analysis_panel import AnalysisPanel
from turn_order import make_fighter
from turn_order_panel import TurnOrderPanel
from notification_panel import NotificationPanel
from js_state import JSStateService

# Waves at which rival battles occur in Classic mode.
_RIVAL_WAVES = [25, 55, 95, 145, 182]


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
        if level.value >= 2:
            print(text)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n")


# ── Settings dialog ───────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, parent, enemy_panel):
        super().__init__(parent)
        self._enemy = enemy_panel
        self.setWindowTitle("Settings")
        self.setModal(False)
        self.setMinimumWidth(260)
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
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self._weak_check = QCheckBox("Show opponent weaknesses")
        self._weak_check.setChecked(False)
        self._weak_check.toggled.connect(self._enemy.set_weaknesses_visible)
        layout.addWidget(self._weak_check)

        self._moves_check = QCheckBox("Show enemy moves")
        self._moves_check.setChecked(False)
        self._moves_check.toggled.connect(self._enemy.set_moves_visible)
        layout.addWidget(self._moves_check)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.hide)
        layout.addWidget(btns)


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

        # ── UI state (drives mode + boss badges) ──────────────────────────
        self._ui_state = UIState()
        self._in_fight = False

        # ── Web view ──────────────────────────────────────────────────────
        self._web = QWebEngineView()
        self._configure_web()

        # ── Panels ────────────────────────────────────────────────────────
        self._wave     = WavePanel(embedded=True)
        self._enemy    = OverlayPanel([], embedded=True)
        self._analysis = AnalysisPanel()
        self._team     = TeamPanel(strip=True)
        self._team.set_analysis_panel(self._analysis)
        self._team.load_saved_team()

        # ── JS state service (live Phaser scene reader) ──────────────────
        self._js_state = JSStateService(self._web.page(), interval_ms=300, parent=self)
        self._js_state.snapshot_changed.connect(self._on_js_snapshot)
        self._js_state.start()

        # ── Cross-panel wiring ────────────────────────────────────────────
        # When the team's moves change, re-render the SEND IN matchups so
        # recommendations stay in sync with the team list.
        self._team.team_changed.connect(self._enemy.refresh_matchups)

        # ── Title bar ─────────────────────────────────────────────────────
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

        title_hl.addStretch()

        # ── Left panel: title + enemy/analysis splitter ──────────────────
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

        # ── Settings dialog (single checkbox: opponent weaknesses) ───────
        self._settings_dlg = SettingsDialog(self, self._enemy)

        # ── Turn-order slide-out (anchors to web-view top-left) ──────────
        self._turn_order_panel = TurnOrderPanel(self._web)

        # ── Rival warning banner (slides down from web-view top) ──────────
        self._notif_panel = NotificationPanel(self._web)
        self._prev_wave: int | None = None

        # Focus the web view on launch so keyboard input goes straight to the game.
        QTimer.singleShot(800, self._web.setFocus)

        saved = state.get("embedded_splitter_state")
        if saved:
            from PyQt6.QtCore import QByteArray
            self._left_splitter.restoreState(QByteArray.fromBase64(saved.encode()))

        self._push_debug_state()

    # ── Web view setup ────────────────────────────────────────────────────────

    def _configure_web(self):
        profile = QWebEngineProfile("pokerogue_helper", self._web)
        profile.setPersistentStoragePath(_PROFILE_DIR)
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
        self._inject_phaser_capture()
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

    def _inject_phaser_capture(self):
        """Catch Phaser's game instance by hooking Function.prototype.bind at
        DocumentCreation. Phaser's TimeStep does `this.step.bind(this)` once at
        boot where `this.game` is the Phaser.Game. We stash it on
        window.__pokerogue_game__ for js_state.py to read."""
        script = QWebEngineScript()
        script.setName("phaser_capture")
        script.setSourceCode(r"""
            (function () {
                var origBind = Function.prototype.bind;
                Function.prototype.bind = function (thisArg) {
                    try {
                        if (thisArg && thisArg.game && thisArg.game.scene
                            && typeof thisArg.game.scene.getScene === 'function'
                            && !window.__pokerogue_game__) {
                            window.__pokerogue_game__ = thisArg.game;
                            console.log('[CAPTURE] Phaser game captured');
                            Function.prototype.bind = origBind;
                        }
                    } catch (e) {}
                    return origBind.apply(this, arguments);
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
        settings_btn.setFixedSize(34, 28)
        settings_btn.setToolTip("Settings")
        settings_btn.clicked.connect(self._open_settings)
        row.addWidget(settings_btn)

        return bar

    def _open_settings(self):
        if self._settings_dlg.isVisible():
            self._settings_dlg.hide()
        else:
            p = self.mapToGlobal(QPoint(self.width() - 280, _NAVBAR_H))
            self._settings_dlg.move(p)
            self._settings_dlg.show()
            self._settings_dlg.raise_()

    def _navigate_to_url(self):
        text = self._url_bar.text().strip()
        if not text:
            return
        if not text.startswith(("http://", "https://", "about:")):
            text = "https://" + text
        self._web.load(QUrl(text))

    def _on_load_started(self):
        self._status_lbl.setText("Loading…")

    def _on_load_finished(self, ok: bool):
        self._status_lbl.setText("" if ok else "Failed to load")

    def _on_url_changed(self, url: QUrl):
        self._url_bar.setText(url.toString())

    # ── JS state dispatcher ───────────────────────────────────────────────────

    def _on_js_snapshot(self, snap):
        """Live Phaser-scene snapshot. Drives every downstream panel."""
        if snap is None or snap.get('error'):
            self._team.set_active_slot("", position=0)
            self._team.set_active_slot("", position=1)
            self._enemy.set_active_pokemon("")
            self._enemy._signals.slot_cleared.emit(0)
            self._enemy._signals.slot_cleared.emit(1)
            self._team.set_party([])
            self._turn_order_panel.update_fighters([])
            if self._in_fight:
                self._in_fight = False
                self._push_debug_state()
            return

        # Wave
        wave = snap.get('wave')
        if wave is not None:
            self._wave.receive_wave_text(str(wave))
            self._check_rival_warning(wave)
            self._prev_wave = wave

        # Pre-populate the full team (all 6 slots) — HP, level, types, abilities,
        # moves, plus auto-fetch + form override per slot.
        party = snap.get('party') or []
        self._team.set_party(party)
        self._enemy.set_party_data(party)

        # Active highlights + enemy "send in" matchup target
        players = snap.get('player') or []
        p0_name = players[0]['name'] if len(players) >= 1 else ""
        p1_name = players[1]['name'] if len(players) >= 2 else ""
        self._team.set_active_slot(p0_name, position=0)
        self._team.set_active_slot(p1_name, position=1)
        self._enemy.set_active_pokemon(p0_name)
        p0_types = players[0].get('types') or [] if players else []
        self._enemy.set_battle_context(p0_types, snap.get('battleType', 0))
        if players:
            p0bs = players[0].get('battleStats') or {}
            self._enemy.set_player_battle_stats(
                p0bs.get('def') or 0, p0bs.get('spd') or 0,
                players[0].get('maxHp') or 0,
            )

        # Fight mode + 2v2 ↔ 1v1
        enemies = snap.get('enemies') or []
        want_2v2 = len(enemies) >= 2
        desired_mode = '2v2' if want_2v2 else '1v1'
        if self._ui_state.fight_mode != desired_mode:
            self._ui_state.fight_mode = desired_mode
            if not want_2v2:
                self._enemy.reset_slot1()
            self._push_debug_state()

        # Enemy slots
        for slot in range(2):
            if slot < len(enemies):
                e = enemies[slot]
                self._enemy.receive_ocr_result(slot, e['name'])
                self._enemy.receive_opponent_level(slot, str(e['level']))
                if e.get('hp') is not None and e.get('maxHp') is not None:
                    self._enemy.receive_opponent_hp(slot, f"{e['hp']}/{e['maxHp']}")
                types = e.get('types') or []
                self._enemy.receive_type_sample(slot, 0, types[0] if len(types) >= 1 else None)
                self._enemy.receive_type_sample(slot, 1, types[1] if len(types) >= 2 else None)
                self._enemy.receive_opponent_abilities(slot, e.get('ability'), e.get('passive'),
                                                       e.get('abilityIndex'), e.get('nature'))
                self._enemy.receive_opponent_moves(slot, e.get('moves') or [])
                ebs = e.get('battleStats') or {}
                self._enemy.receive_enemy_battle_stats(
                    slot, ebs.get('atk') or 0, ebs.get('spa') or 0,
                    e.get('level') or 1, e.get('types') or [], e.get('status'),
                )
                was_boss = slot in self._ui_state.boss_mask
                is_boss = bool(e.get('isBoss'))
                if is_boss != was_boss:
                    if is_boss:
                        self._ui_state.boss_mask.add(slot)
                    else:
                        self._ui_state.boss_mask.discard(slot)
                    self._push_debug_state()
            else:
                self._enemy._signals.slot_cleared.emit(slot)

        # Turn order — build fighters straight from JS data (exact speed + stages)
        fighters = []
        for pos, p in enumerate(players):
            if p.get('speed'):
                fighters.append(make_fighter(
                    p['name'], "player", pos,
                    speed=p['speed'], level=p['level'],
                    speed_stage=p.get('speedStage', 0),
                ))
        for pos, e in enumerate(enemies):
            if e.get('speed'):
                fighters.append(make_fighter(
                    e['name'], "opponent", pos,
                    speed=e['speed'], level=e['level'],
                    speed_stage=e.get('speedStage', 0),
                ))
        self._turn_order_panel.update_fighters(fighters, trick_room=bool(snap.get('trickRoom')))

        in_fight = len(enemies) > 0
        if in_fight != self._in_fight:
            self._in_fight = in_fight
            self._push_debug_state()

    # ── Rival warning ─────────────────────────────────────────────────────────

    def _check_rival_warning(self, wave: int):
        """Fire a notification on the rising edge of the two waves before a rival."""
        if self._prev_wave is None or wave == self._prev_wave:
            return
        for rival_wave in _RIVAL_WAVES:
            if wave == rival_wave - 2:
                self._notif_panel.show_message(
                    "⚔", "Rival in 2 levels", "#f9e2af"
                )
                break
            elif wave == rival_wave - 1:
                self._notif_panel.show_message(
                    "⚔", "Rival next level!", "#fab387"
                )
                break

    # ── Title-bar badge updates ──────────────────────────────────────────────

    def _push_debug_state(self):
        pass  # badges removed; kept as call-site hook

    # ── Window movement ──────────────────────────────────────────────────────

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, '_turn_order_panel'):
            self._turn_order_panel.reposition()
        if hasattr(self, '_notif_panel'):
            self._notif_panel.reposition()

    # ── Persistence ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._js_state.stop()
        g = self.geometry()
        window_state.save_key("embedded_window", {"x": g.x(), "y": g.y()})
        window_state.save_key(
            "embedded_splitter_state",
            self._left_splitter.saveState().toBase64().data().decode(),
        )
        self._turn_order_panel.close()
        self._notif_panel.close()
        super().closeEvent(event)
