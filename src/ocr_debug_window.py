from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QImage, QFont
from PIL import Image

_MAX_IMG_H = 60   # max display height for both image columns
_LABEL_W   = 72   # fixed width for the box-name column

_WIN_STYLE = """
    QWidget  { background: #1e1e2e; color: #cdd6f4; }
    QScrollArea { border: none; }
    QScrollBar:vertical {
        background: #181825; width: 6px; border-radius: 3px; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #45475a; border-radius: 3px; min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

_COL_HDR = "color:#6c7086; font-size:10px; font-weight:bold; padding: 2px 0;"

_CHIP_BASE = (
    "font-size:11px; font-weight:bold; padding:2px 8px;"
    " border-radius:4px; border:none;"
)
_CHIP_ON  = _CHIP_BASE + "background:{bg}; color:#1e1e2e;"
_CHIP_OFF = _CHIP_BASE + "background:#313244; color:#6c7086;"


def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


def _scale(px: QPixmap) -> QPixmap:
    if px.height() > _MAX_IMG_H:
        return px.scaledToHeight(_MAX_IMG_H, Qt.TransformationMode.SmoothTransformation)
    return px


class OCRDebugWindow(QWidget):
    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("OCR Debug")
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(1100, 640)
        self.setStyleSheet(_WIN_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Trigger state bar ─────────────────────────────────────────────
        state_row = QHBoxLayout()
        state_row.setSpacing(8)

        def _chip(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(_CHIP_OFF)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return lbl

        self._chip_mode    = _chip("1v1")
        self._chip_fight   = _chip("IN FIGHT")
        self._chip_op1boss = _chip("OP1 BOSS")
        self._chip_op2boss = _chip("OP2 BOSS")

        for lbl in (self._chip_mode, self._chip_fight, self._chip_op1boss, self._chip_op2boss):
            state_row.addWidget(lbl)
        state_row.addStretch()
        root.addLayout(state_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#313244;")
        root.addWidget(sep)

        # ── Column headers ────────────────────────────────────────────────
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(8, 0, 8, 0)
        hdr_row.setSpacing(10)
        spacer = QLabel()
        spacer.setFixedWidth(_LABEL_W)
        hdr_row.addWidget(spacer)
        for text, stretch in [("Raw capture (1:1 with box)", 2), ("Processed (Tesseract input)", 2), ("OCR output", 3)]:
            lbl = QLabel(text)
            lbl.setStyleSheet(_COL_HDR)
            hdr_row.addWidget(lbl, stretch)
        root.addLayout(hdr_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._col = QVBoxLayout(inner)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(4)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # label → (raw_lbl, prep_lbl, text_lbl)
        self._rows: dict[str, tuple] = {}
        for lbl in labels:
            self._add_row(lbl)
        self._col.addStretch()

    def _add_row(self, label: str):
        row = QFrame()
        row.setStyleSheet("QFrame { background: #181825; border-radius: 5px; }")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(10)

        name = QLabel(label)
        name.setFixedWidth(_LABEL_W)
        name.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        font = QFont()
        font.setBold(True)
        font.setPointSize(9)
        name.setFont(font)
        name.setStyleSheet("color: #89b4fa;")

        raw_lbl = QLabel("—")
        raw_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        raw_lbl.setStyleSheet("background: #11111b; border-radius: 3px; padding: 2px;")

        prep_lbl = QLabel("—")
        prep_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        prep_lbl.setStyleSheet("background: #11111b; border-radius: 3px; padding: 2px;")

        text_lbl = QLabel("—")
        text_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        text_lbl.setStyleSheet(
            "color: #cdd6f4; font-size: 12px; font-family: Consolas, monospace;"
            " background: transparent;"
        )
        text_lbl.setWordWrap(True)

        hl.addWidget(name)
        hl.addWidget(raw_lbl,  2)
        hl.addWidget(prep_lbl, 2)
        hl.addWidget(text_lbl, 3)

        self._col.addWidget(row)
        self._rows[label] = (raw_lbl, prep_lbl, text_lbl)

    def update_box(self, payload: dict):
        label = payload["label"]
        text  = payload.get("text", "")
        raw   = payload.get("raw")
        prep  = payload.get("prep")

        if label not in self._rows:
            self._add_row(label)

        raw_lbl, prep_lbl, text_lbl = self._rows[label]

        if raw is not None:
            try:
                raw_lbl.setPixmap(_scale(_pil_to_pixmap(raw)))
                raw_lbl.setText("")
            except Exception:
                raw_lbl.setText("(err)")

        if prep is not None:
            try:
                prep_lbl.setPixmap(_scale(_pil_to_pixmap(prep)))
                prep_lbl.setText("")
            except Exception:
                prep_lbl.setText("(err)")

        color   = "#f38ba8" if not text or text == "(blank)" else "#a6e3a1"
        display = repr(text) if text and text != "(blank)" else text or "(empty)"
        text_lbl.setText(f'<span style="color:{color}">{display}</span>')

    def set_state(self, fight_mode: str, in_fight: bool, op1_boss: bool, op2_boss: bool):
        is_2v2 = fight_mode == '2v2'
        self._chip_mode.setText(fight_mode.upper())
        self._chip_mode.setStyleSheet(
            _CHIP_ON.format(bg="#cba6f7") if is_2v2 else _CHIP_ON.format(bg="#89b4fa")
        )
        self._chip_fight.setStyleSheet(
            _CHIP_ON.format(bg="#a6e3a1") if in_fight else _CHIP_OFF
        )
        self._chip_op1boss.setStyleSheet(
            _CHIP_ON.format(bg="#fab387") if op1_boss else _CHIP_OFF
        )
        self._chip_op2boss.setStyleSheet(
            (_CHIP_ON.format(bg="#fab387") if op2_boss else _CHIP_OFF) if is_2v2 else _CHIP_OFF
        )
        self._chip_op2boss.setEnabled(is_2v2)
