"""EmbeddedMainWindow — single-window layout:

  ┌──────────────┬────────────────────────────────────┐
  │ Title bar    │  Navbar (incl. wave tracker)       │
  ├──────────────┼────────────────────────────────────┤
  │ Opponents    │                                    │
  │              │        pokerogue.net               │
  ├──────────────┤        (Phaser canvas)             │
  │ Analysis     │                                    │
  │ (fills down) ├────────────────────────────────────┤
  │              │  Team (6 slots)                    │
  └──────────────┴────────────────────────────────────┘

All battle state comes from JSStateService, which polls the live Phaser scene
via runJavaScript every 300ms. The Function.prototype.bind hook in
_inject_phaser_capture catches the Phaser.Game instance at boot and stashes it
on window.__pokerogue_game__ for the extractor to read.
"""

from __future__ import annotations

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
    QMainWindow, QSizePolicy, QStackedWidget,
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
from team_builder_panel import TeamBuilderPanel
from impact_table import ImpactTableDialog
import impact_db
import biome_db
from wave_panel import WavePanel
from analysis_panel import AnalysisPanel
from turn_order import make_fighter
from turn_order_panel import TurnOrderPanel
from notification_panel import NotificationPanel
from js_state import JSStateService

# Waves at which rival battles occur in Classic mode.
_RIVAL_WAVES = [8, 25, 55, 95, 145, 195]


_LEFT_WIDTH = 390
_NAVBAR_H   = 34
_TEAM_H     = 178
_FIXED_W    = 1600
_FIXED_H    = 900

_JS_LEVELS    = {0: "Info", 1: "Warning", 2: "Error"}
from app_dirs import data_path
_LOG_PATH    = data_path("embedded_console.log")
_PROFILE_DIR = data_path("browser_data")


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
        self._wave          = WavePanel(embedded=True)
        self._enemy         = OverlayPanel([], embedded=True)
        self._team_builder  = TeamBuilderPanel()
        self._analysis      = AnalysisPanel()
        self._team          = TeamPanel(strip=True)
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

        self._battle_badge = QLabel()
        self._battle_badge.setVisible(False)
        title_hl.addWidget(self._battle_badge)

        title_hl.addStretch()

        # ── Left panel: title + stacked (enemy | team builder) + analysis ───
        self._left_stack = QStackedWidget()
        self._left_stack.addWidget(self._enemy)         # index 0 — battle view
        self._left_stack.addWidget(self._team_builder)  # index 1 — starter select

        left_panel = QWidget()
        left_panel.setFixedWidth(_LEFT_WIDTH)
        left_panel.setStyleSheet("QWidget { background: #181825; }")
        left_vl = QVBoxLayout(left_panel)
        left_vl.setContentsMargins(0, 0, 0, 0)
        left_vl.setSpacing(0)
        left_vl.addWidget(title_bar)
        left_vl.addWidget(self._left_stack, 1)
        left_vl.addWidget(self._analysis)

        # ── Right panel: navbar + browser + team ─────────────────────────
        right_panel = QWidget()
        right_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_vl = QVBoxLayout(right_panel)
        right_vl.setContentsMargins(0, 0, 0, 0)
        right_vl.setSpacing(0)
        right_vl.addWidget(self._build_navbar())
        right_vl.addWidget(self._web, 1)
        self._team.setFixedHeight(_TEAM_H)
        right_vl.addWidget(self._team)

        # ── Central widget: left panel + right panel ──────────────────────
        central = QWidget()
        ch = QHBoxLayout(central)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(0)
        ch.addWidget(left_panel)
        ch.addWidget(right_panel, 1)
        self.setCentralWidget(central)

        # ── Impact score browser ─────────────────────────────────────────
        self._impact_dlg: ImpactTableDialog | None = None
        impact_db.init(on_ready=self._on_impact_ready)
        biome_db.init()

        # ── Settings dialog (single checkbox: opponent weaknesses) ───────
        self._settings_dlg = SettingsDialog(self, self._enemy)

        # ── Turn-order slide-out (anchors to web-view top-left) ──────────
        self._turn_order_panel = TurnOrderPanel(self._web)

        # ── Rival warning banner (slides down from web-view top) ──────────
        self._notif_panel = NotificationPanel(self._web)
        self._prev_wave: int | None = None

        # Focus the web view on launch so keyboard input goes straight to the game.
        QTimer.singleShot(800, self._web.setFocus)

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

        impact_btn = QPushButton("Impact Score Browser")
        impact_btn.setToolTip("Browse Impact Scores")
        impact_btn.clicked.connect(self._open_impact_table)
        row.addWidget(impact_btn)

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

    def _open_impact_table(self):
        if self._impact_dlg is None:
            self._impact_dlg = ImpactTableDialog(self)
        if self._impact_dlg.isVisible():
            self._impact_dlg.hide()
        else:
            self._impact_dlg.show()
            self._impact_dlg.raise_()

    def _on_impact_ready(self):
        if self._impact_dlg is not None:
            self._impact_dlg.refresh()

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
            self._left_stack.setCurrentIndex(0)
            self._team.set_active_slot("", position=0)
            self._team.set_active_slot("", position=1)
            self._enemy.set_active_pokemon("")
            self._enemy._signals.slot_cleared.emit(0)
            self._enemy._signals.slot_cleared.emit(1)
            self._team.set_party([])
            self._turn_order_panel.update_fighters([])
            self._wave.clear_biome()
            self._battle_badge.setVisible(False)
            if self._in_fight:
                self._in_fight = False
                self._push_debug_state()
            return

        # ── Starter select screen ─────────────────────────────────────────
        if snap.get('is_starter_select'):
            self._left_stack.setCurrentIndex(1)
            self._team_builder.update_starter_state(snap)
            return

        self._left_stack.setCurrentIndex(0)

        # Wave + biome
        wave = snap.get('wave')
        biome_id = snap.get('biome')
        if wave is not None:
            self._wave.receive_wave_text(str(wave))
            self._check_rival_warning(wave)
            self._prev_wave = wave
        self._wave.set_biome(biome_id)

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

        # Battle type badge
        enemies = snap.get('enemies') or []
        self._update_battle_badge(snap.get('battleType', 0), len(enemies) > 0)

        # Fight mode + 2v2 ↔ 1v1
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
                # Biome rarity — wild encounters only; trainer mons aren't drawn
                # from the biome's wild pool.
                if snap.get('battleType', 0) == 0:
                    tier, is_boss = biome_db.rarity_for(
                        biome_id, e.get('speciesId'), e.get('name'))
                    self._enemy.receive_opponent_biome_rarity(
                        slot, biome_db.biome_display(biome_id), tier, is_boss)
                else:
                    self._enemy.receive_opponent_biome_rarity(slot, None, None, False)
                ebs = e.get('battleStats') or {}
                _ebs_vals = [ebs.get(k) for k in ('atk', 'def', 'spa', 'spd')]
                _enemy_total = (
                    sum(v for v in _ebs_vals if v) +
                    (e.get('speed') or 0) + (e.get('maxHp') or 0)
                ) or None
                self._enemy.receive_enemy_battle_stats(
                    slot, ebs.get('atk') or 0, ebs.get('spa') or 0,
                    e.get('level') or 1, e.get('types') or [], e.get('status'),
                    stat_total=_enemy_total,
                )
                perm = e.get('permanentStats') or {}
                self._enemy.receive_enemy_perm_stats(slot, perm)
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

    # ── Battle badge ──────────────────────────────────────────────────────────

    def _update_battle_badge(self, battle_type: int, has_enemies: bool):
        if not has_enemies:
            self._battle_badge.setVisible(False)
            return
        if battle_type == 0:
            self._battle_badge.setText("  Wild  ")
            self._battle_badge.setStyleSheet(
                "QLabel { background:#a6e3a1; color:#1e1e2e; padding:2px 6px;"
                " margin:5px 0; border-radius:8px; font-size:11px; font-weight:bold; }"
            )
        else:
            self._battle_badge.setText("  Trainer  ")
            self._battle_badge.setStyleSheet(
                "QLabel { background:#fab387; color:#1e1e2e; padding:2px 6px;"
                " margin:5px 0; border-radius:8px; font-size:11px; font-weight:bold; }"
            )
        self._battle_badge.setVisible(True)

    # ── Rival warning ─────────────────────────────────────────────────────────

    def _check_rival_warning(self, wave: int):
        """Fire a notification on the rising edge of the two waves before a rival."""
        if wave == self._prev_wave:
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
        self._turn_order_panel.close()
        self._notif_panel.close()
        super().closeEvent(event)
