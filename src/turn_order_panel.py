"""Slide-out turn-order panel.

Frameless top-level Tool window because QWebEngineView is GPU-composited and
child widgets can't reliably overlay it. Anchors to the top-left of a host
widget (the embedded web view) and slides horizontally in/out from
off-screen-left.
"""

from PyQt6.QtCore import (
    Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve, QAbstractAnimation,
)
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame

from turn_order import Fighter, order, COIN_FLIP_THRESHOLD


_MIN_PANEL_W  = 160
_ROW_H        = 26
_HEADER_H     = 22
_VERTICAL_FRAC = 4 / 9   # anchor ~2/3 down the host height
_ANIM_MS      = 220


class TurnOrderPanel(QWidget):
    def __init__(self, host: QWidget):
        # Pass host as parent so Windows establishes an owner HWND relationship.
        # This keeps the panel tethered to the app (stays on top of it, minimizes
        # with it, and doesn't drift off-screen on foreign monitor layouts).
        # The Tool flag still prevents a taskbar entry; mapToGlobal positioning
        # is unaffected because we override the geometry manually anyway.
        super().__init__(host.window())
        self._host = host
        self._shown = False
        self._last_fighters: list[Fighter] = []

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._card = QFrame(self)
        self._card.setObjectName("turn_order_card")
        self._card.setStyleSheet("""
            QFrame#turn_order_card {
                background: #1e1e2e;
                border: 1px solid #45475a;
                border-left: none;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

        self._inner = QVBoxLayout(self._card)
        self._inner.setContentsMargins(10, 6, 10, 8)
        self._inner.setSpacing(3)

        self._header = QLabel("TURN ORDER")
        self._header.setStyleSheet(
            "color:#89b4fa; font-size:11px; font-weight:bold; letter-spacing:1px;"
        )
        self._inner.addWidget(self._header)

        self._rows_holder = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_holder)
        self._rows_layout.setContentsMargins(0, 2, 0, 0)
        self._rows_layout.setSpacing(2)
        self._inner.addWidget(self._rows_holder)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.resize(_MIN_PANEL_W, _HEADER_H + _ROW_H + 18)

    # ── public API ────────────────────────────────────────────────────────────

    def update_fighters(self, fighters: list[Fighter], trick_room: bool = False):
        """Rebuild the rows. Pass [] to hide."""
        if not fighters or len(fighters) < 2:
            self._last_fighters = []
            self._last_trick_room = False
            self.hide_panel()
            return

        ranked = order(fighters, trick_room=trick_room)
        sig = tuple((f.name, f.staged_speed, f.side, f.position) for f in ranked)
        last_sig = tuple((f.name, f.staged_speed, f.side, f.position) for f in self._last_fighters)
        if sig == last_sig and trick_room == getattr(self, '_last_trick_room', False) and self._shown:
            return
        self._last_fighters = ranked
        self._last_trick_room = trick_room

        self._header.setText("TURN ORDER  ⚠ TRICK ROOM" if trick_room else "TURN ORDER")
        self._header.setStyleSheet(
            ("color:#cba6f7;" if trick_room else "color:#89b4fa;")
            + " font-size:11px; font-weight:bold; letter-spacing:1px;"
        )

        self._clear_rows()
        if len(ranked) == 2:
            self._build_oneliner(ranked)
        else:
            for i, f in enumerate(ranked):
                self._rows_layout.addWidget(self._build_row(i + 1, f))

        self._card.adjustSize()
        self.adjustSize()
        hint = self.sizeHint()
        self.resize(max(_MIN_PANEL_W, hint.width()), hint.height())

        self.show_panel()

    def reposition(self):
        """Snap to the host's current top-left without animating."""
        if not self.isVisible():
            return
        target = self._target_rect()
        self.setGeometry(target)

    def show_panel(self):
        target = self._target_rect()
        if self._shown:
            # already on-screen — just snap to refreshed geometry (e.g. height changed)
            self.setGeometry(target)
            return
        start = QRect(target.x() - target.width(), target.y(),
                      target.width(), target.height())
        self.setGeometry(start)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.setDirection(QAbstractAnimation.Direction.Forward)
        self._anim.start()
        self._shown = True

    def hide_panel(self):
        if not self._shown:
            self.hide()
            return
        cur = self.geometry()
        end = QRect(cur.x() - cur.width(), cur.y(), cur.width(), cur.height())
        self._anim.stop()
        self._anim.setStartValue(cur)
        self._anim.setEndValue(end)
        self._anim.start()
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass
        self._anim.finished.connect(self._on_hide_finished)
        self._shown = False

    # ── internals ─────────────────────────────────────────────────────────────

    def _on_hide_finished(self):
        if not self._shown:
            self.hide()

    def _target_rect(self) -> QRect:
        origin = self._host.mapToGlobal(QPoint(0, 0))
        y_offset = int(self._host.height() * _VERTICAL_FRAC)
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.screenAt(origin) or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = max(avail.left(), origin.x())
        y = max(avail.top(), min(origin.y() + y_offset, avail.bottom() - self.height()))
        return QRect(x, y, self.width(), self.height())

    def _clear_rows(self):
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_oneliner(self, ranked: list[Fighter]) -> None:
        fast, slow = ranked
        gap = fast.staged_speed - slow.staged_speed
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(6)

        if gap <= COIN_FLIP_THRESHOLD:
            verdict = QLabel("Speed tie")
            verdict.setStyleSheet(
                "color:#f9e2af; font-size:15px; font-weight:bold;"
            )
            detail = QLabel(f"Δ{gap}")
            detail.setStyleSheet("color:#6c7086; font-size:12px;")
        else:
            you_first = fast.side == "player"
            if you_first:
                text = "You first"
            else:
                name = fast.name.replace("-", " ").title()
                text = f"{name} first"
            verdict = QLabel(text)
            verdict.setStyleSheet(
                f"color:{'#a6e3a1' if you_first else '#f38ba8'};"
                " font-size:15px; font-weight:bold;"
            )
            detail = QLabel(f"+{gap}")
            detail.setStyleSheet("color:#a6adc8; font-size:12px;")

        h.addWidget(verdict)
        h.addWidget(detail)
        h.addStretch()
        self._rows_layout.addWidget(row)

    def _build_row(self, rank: int, f: Fighter) -> QWidget:
        row = QWidget()
        row.setFixedHeight(_ROW_H)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        rank_lbl = QLabel(f"{rank}.")
        rank_lbl.setFixedWidth(16)
        rank_lbl.setStyleSheet("color:#6c7086; font-size:14px;")

        is_player = f.side == "player"
        name_color = "#a6e3a1" if is_player else "#f38ba8"
        marker = "▲" if is_player else "▼"
        name_text = f.name.replace("-", " ").title()

        name_lbl = QLabel(f"{marker} {name_text}")
        name_lbl.setStyleSheet(
            f"color:{name_color}; font-size:13px; font-weight:bold;"
        )

        spd_lbl = QLabel(str(f.staged_speed))
        spd_lbl.setStyleSheet("color:#cdd6f4; font-size:13px;")
        spd_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        stage_widget = None
        if f.speed_stage != 0:
            sign = "+" if f.speed_stage > 0 else ""
            stage_widget = QLabel(f"{sign}{f.speed_stage}")
            stage_widget.setFixedWidth(22)
            stage_color = "#a6e3a1" if f.speed_stage > 0 else "#f38ba8"
            stage_widget.setStyleSheet(f"color:{stage_color}; font-size:11px; font-weight:bold;")
            stage_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)

        h.addWidget(rank_lbl)
        h.addWidget(name_lbl)
        h.addStretch()
        if stage_widget is not None:
            h.addWidget(stage_widget)
        h.addWidget(spd_lbl)
        return row
