from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QFont
import window_state

BOX_W = 249
BOX_H = 56
BORDER_COLOR  = QColor(0, 200, 255)
LABEL_COLOR   = QColor(255, 255, 255)
GRIP_SIZE     = 14   # px square in bottom-right corner that triggers resize
MIN_SIZE      = 30
LABEL_INSET   = 20   # px to skip from top so the label isn't captured by OCR


class CaptureBox(QWidget):
    def __init__(self, label: str = "", state_key: str = "",
                 width: int = BOX_W, height: int = BOX_H,
                 resizable: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
        self._label     = label
        self._state_key = state_key
        self._resizable = resizable
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # locked by default
        if resizable:
            self.setMinimumSize(MIN_SIZE, MIN_SIZE)
            self.resize(width, height)
        else:
            self.setFixedSize(width, height)
        self._interactive       = False
        self._drag_pos          = None
        self._resize_mode       = False
        self._resize_origin     = None
        self._resize_start_geom = None

    def set_interactive(self, enabled: bool):
        self._interactive = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
        self.setCursor(Qt.CursorShape.SizeAllCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def get_capture_rect(self) -> QRect:
        r = QRect(self.pos(), self.size())
        if self._label:
            r.setTop(r.top() + LABEL_INSET)
        return r

    def _in_grip(self, pos: QPoint) -> bool:
        return (self._resizable
                and pos.x() >= self.width()  - GRIP_SIZE
                and pos.y() >= self.height() - GRIP_SIZE)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        top = LABEL_INSET if self._label else 1
        pen = QPen(BORDER_COLOR, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(1, top, self.width() - 2, self.height() - top - 1)

        if self._label:
            font = QFont()
            font.setBold(True)
            font.setPointSize(9)
            painter.setFont(font)
            painter.setPen(QPen(LABEL_COLOR))
            # Draw label above the capture border so it doesn't affect OCR area
            painter.drawText(6, LABEL_INSET - 5, self._label)
        if self._resizable and self._interactive:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 0, 180))
            painter.drawRect(self.width() - GRIP_SIZE, self.height() - GRIP_SIZE,
                             GRIP_SIZE, GRIP_SIZE)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._in_grip(event.position().toPoint()):
            self._resize_mode       = True
            self._resize_origin     = event.globalPosition().toPoint()
            self._resize_start_geom = self.geometry()
        else:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self.setCursor(
                Qt.CursorShape.SizeFDiagCursor
                if self._in_grip(event.position().toPoint())
                else Qt.CursorShape.SizeAllCursor
            )
            return
        if self._resize_mode and self._resize_origin is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            g = self._resize_start_geom
            new_w = max(MIN_SIZE, g.width()  + delta.x())
            new_h = max(MIN_SIZE, g.height() + delta.y())
            self.resize(new_w, new_h)
        elif self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _event):
        self._drag_pos      = None
        self._resize_mode   = False
        self._resize_origin = None
        if self._state_key:
            g = self.geometry()
            window_state.save_key(self._state_key, {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()})
