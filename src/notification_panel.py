"""Slide-down notification banner.

Frameless top-level Tool window. Slides down from the top edge of a host
widget, displays a message, then auto-dismisses after a configurable delay.
"""

from PyQt6.QtCore import (
    Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve,
    QAbstractAnimation, QTimer,
)
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QApplication


_ANIM_MS = 220


class NotificationPanel(QWidget):
    def __init__(self, host: QWidget):
        super().__init__(host.window())
        self._host = host
        self._shown = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._card = QFrame(self)
        self._card.setObjectName("notif_card")
        self._card.setStyleSheet("""
            QFrame#notif_card {
                background: #1e1e2e;
                border: 1px solid #45475a;
                border-top: none;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

        inner = QVBoxLayout(self._card)
        inner.setContentsMargins(24, 8, 24, 12)
        inner.setSpacing(4)

        self._icon_lbl = QLabel()
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setStyleSheet("font-size: 20px;")
        inner.addWidget(self._icon_lbl)

        self._msg_lbl = QLabel()
        self._msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(self._msg_lbl)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide_panel)

    # ── public API ─────────────────────────────────────────────────────────────

    def show_message(self, icon: str, message: str, color: str,
                     auto_dismiss_ms: int = 5000):
        """Show (or replace) the current notification."""
        self._dismiss_timer.stop()
        self._icon_lbl.setText(icon)
        self._msg_lbl.setText(message)
        self._msg_lbl.setStyleSheet(
            f"color: {color}; font-size: 15px; font-weight: bold;"
        )

        self._card.adjustSize()
        self.adjustSize()
        target = self._target_rect()

        if self._shown:
            self.setGeometry(target)
        else:
            start = QRect(target.x(), target.y() - target.height(),
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

        if auto_dismiss_ms > 0:
            self._dismiss_timer.start(auto_dismiss_ms)

    def hide_panel(self):
        if not self._shown:
            return
        self._dismiss_timer.stop()
        cur = self.geometry()
        end = QRect(cur.x(), cur.y() - cur.height(), cur.width(), cur.height())
        self._anim.stop()
        self._anim.setStartValue(cur)
        self._anim.setEndValue(end)
        self._anim.setDirection(QAbstractAnimation.Direction.Forward)
        self._anim.start()
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass
        self._anim.finished.connect(self._on_hide_done)
        self._shown = False

    def reposition(self):
        if not self.isVisible():
            return
        self.setGeometry(self._target_rect())

    # ── internals ──────────────────────────────────────────────────────────────

    def _on_hide_done(self):
        if not self._shown:
            self.hide()

    def _target_rect(self) -> QRect:
        origin = self._host.mapToGlobal(QPoint(0, 0))
        hint = self.sizeHint()
        w = max(240, hint.width())
        h = hint.height()
        x = origin.x() + (self._host.width() - w) // 2
        y = origin.y()
        screen = QApplication.screenAt(origin) or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = max(avail.left(), min(x, avail.right() - w))
        y = max(avail.top(), y)
        return QRect(x, y, w, h)
