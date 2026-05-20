import threading
import difflib
import window_state
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QPainter, QColor

from pokemon_api import fetch_pokemon, fetch_final_evolutions, PokemonData
from weakness_calc import calculate_weaknesses
import stats_db
import tier_db
import moves_db

# ── Regional / alternate form variants ───────────────────────────────────────
_FORM_VARIANTS: dict[str, list[tuple[str, str]]] = {
    # Alolan
    "rattata":    [("Normal", "rattata"),    ("Alolan", "rattata-alola")],
    "raticate":   [("Normal", "raticate"),   ("Alolan", "raticate-alola")],
    "raichu":     [("Normal", "raichu"),     ("Alolan", "raichu-alola")],
    "sandshrew":  [("Normal", "sandshrew"),  ("Alolan", "sandshrew-alola")],
    "sandslash":  [("Normal", "sandslash"),  ("Alolan", "sandslash-alola")],
    "vulpix":     [("Normal", "vulpix"),     ("Alolan", "vulpix-alola")],
    "ninetales":  [("Normal", "ninetales"),  ("Alolan", "ninetales-alola")],
    "diglett":    [("Normal", "diglett"),    ("Alolan", "diglett-alola")],
    "dugtrio":    [("Normal", "dugtrio"),    ("Alolan", "dugtrio-alola")],
    "meowth":     [("Normal", "meowth"),     ("Alolan", "meowth-alola"),   ("Galarian", "meowth-galar")],
    "persian":    [("Normal", "persian"),    ("Alolan", "persian-alola")],
    "geodude":    [("Normal", "geodude"),    ("Alolan", "geodude-alola")],
    "graveler":   [("Normal", "graveler"),   ("Alolan", "graveler-alola")],
    "golem":      [("Normal", "golem"),      ("Alolan", "golem-alola")],
    "grimer":     [("Normal", "grimer"),     ("Alolan", "grimer-alola")],
    "muk":        [("Normal", "muk"),        ("Alolan", "muk-alola")],
    "exeggutor":  [("Normal", "exeggutor"),  ("Alolan", "exeggutor-alola")],
    "marowak":    [("Normal", "marowak"),    ("Alolan", "marowak-alola")],
    # Galarian
    "ponyta":     [("Normal", "ponyta"),     ("Galarian", "ponyta-galar")],
    "rapidash":   [("Normal", "rapidash"),   ("Galarian", "rapidash-galar")],
    "slowpoke":   [("Normal", "slowpoke"),   ("Galarian", "slowpoke-galar")],
    "slowbro":    [("Normal", "slowbro"),    ("Galarian", "slowbro-galar")],
    "slowking":   [("Normal", "slowking"),   ("Galarian", "slowking-galar")],
    "farfetchd":  [("Normal", "farfetchd"),  ("Galarian", "farfetchd-galar")],
    "weezing":    [("Normal", "weezing"),    ("Galarian", "weezing-galar")],
    "mr-mime":    [("Normal", "mr-mime"),    ("Galarian", "mr-mime-galar")],
    "articuno":   [("Normal", "articuno"),   ("Galarian", "articuno-galar")],
    "zapdos":     [("Normal", "zapdos"),     ("Galarian", "zapdos-galar")],
    "moltres":    [("Normal", "moltres"),    ("Galarian", "moltres-galar")],
    "corsola":    [("Normal", "corsola"),    ("Galarian", "corsola-galar")],
    "zigzagoon":  [("Normal", "zigzagoon"),  ("Galarian", "zigzagoon-galar")],
    "linoone":    [("Normal", "linoone"),    ("Galarian", "linoone-galar")],
    "darumaka":   [("Normal", "darumaka"),   ("Galarian", "darumaka-galar")],
    "darmanitan": [("Normal", "darmanitan"), ("Galarian", "darmanitan-galar")],
    "yamask":     [("Normal", "yamask"),     ("Galarian", "yamask-galar")],
    "stunfisk":   [("Normal", "stunfisk"),   ("Galarian", "stunfisk-galar")],
    # Hisuian
    "growlithe":  [("Normal", "growlithe"),  ("Hisuian", "growlithe-hisui")],
    "arcanine":   [("Normal", "arcanine"),   ("Hisuian", "arcanine-hisui")],
    "voltorb":    [("Normal", "voltorb"),    ("Hisuian", "voltorb-hisui")],
    "electrode":  [("Normal", "electrode"),  ("Hisuian", "electrode-hisui")],
    "typhlosion": [("Normal", "typhlosion"), ("Hisuian", "typhlosion-hisui")],
    "qwilfish":   [("Normal", "qwilfish"),   ("Hisuian", "qwilfish-hisui")],
    "samurott":   [("Normal", "samurott"),   ("Hisuian", "samurott-hisui")],
    "lilligant":  [("Normal", "lilligant"),  ("Hisuian", "lilligant-hisui")],
    "zorua":      [("Normal", "zorua"),      ("Hisuian", "zorua-hisui")],
    "zoroark":    [("Normal", "zoroark"),    ("Hisuian", "zoroark-hisui")],
    "braviary":   [("Normal", "braviary"),   ("Hisuian", "braviary-hisui")],
    "sliggoo":    [("Normal", "sliggoo"),    ("Hisuian", "sliggoo-hisui")],
    "goodra":     [("Normal", "goodra"),     ("Hisuian", "goodra-hisui")],
    "avalugg":    [("Normal", "avalugg"),    ("Hisuian", "avalugg-hisui")],
    "decidueye":  [("Normal", "decidueye"),  ("Hisuian", "decidueye-hisui")],
    # Paldean
    "wooper":     [("Normal", "wooper"),     ("Paldean", "wooper-paldea")],
    "tauros":     [
        ("Normal",         "tauros"),
        ("Paldean Combat", "tauros-paldea-combat"),
        ("Paldean Blaze",  "tauros-paldea-blaze"),
        ("Paldean Aqua",   "tauros-paldea-aqua"),
    ],
}

_SLUG_TO_BASE: dict[str, str] = {
    slug: base
    for base, forms in _FORM_VARIANTS.items()
    for _, slug in forms
}

TYPE_COLORS = {
    "normal":   ("#A8A878", "#000"),
    "fire":     ("#F08030", "#fff"),
    "water":    ("#6890F0", "#fff"),
    "electric": ("#F8D030", "#000"),
    "grass":    ("#78C850", "#000"),
    "ice":      ("#98D8D8", "#000"),
    "fighting": ("#C03028", "#fff"),
    "poison":   ("#A040A0", "#fff"),
    "ground":   ("#E0C068", "#000"),
    "flying":   ("#A890F0", "#000"),
    "psychic":  ("#F85888", "#fff"),
    "bug":      ("#A8B820", "#000"),
    "rock":     ("#B8A038", "#fff"),
    "ghost":    ("#705898", "#fff"),
    "dragon":   ("#7038F8", "#fff"),
    "dark":     ("#705848", "#fff"),
    "steel":    ("#B8B8D0", "#000"),
    "fairy":    ("#EE99AC", "#000"),
}

NUM_SLOTS      = 2
BADGES_PER_ROW = 3


def _resolve_name(query: str, names: list) -> str | None:
    if query in names:
        return query
    prefix = [n for n in names if n.startswith(query + "-")]
    if prefix:
        return min(prefix, key=len)
    matches = difflib.get_close_matches(query, names, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _ordinal(n: int) -> str:
    n = int(n)
    suffix = "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _pct_color(pct: int) -> str:
    if pct >= 75: return "#a6e3a1"
    if pct >= 50: return "#f9e2af"
    if pct >= 25: return "#fab387"
    return "#f38ba8"


def _best_matchup(weaknesses: dict, team: list) -> list[tuple]:
    scored = []
    for slot in team:
        if not slot or not isinstance(slot, dict):
            continue
        pname = slot.get("name", "")
        best_score, best_move, best_type, best_eff = -1, None, None, 1.0
        for move in slot.get("moves", []):
            if not move or not isinstance(move, dict):
                continue
            if move.get("category") == "status":
                continue
            power = move.get("power")
            if not power:
                continue
            mtype = move.get("type", "")
            eff   = weaknesses.get(mtype, 1.0)
            if eff <= 0:
                continue
            score = power * eff
            if score > best_score:
                best_score, best_move, best_type, best_eff = score, move.get("name", ""), mtype, eff
        if best_move:
            scored.append((best_score, best_eff, pname, best_move, best_type))
    scored.sort(reverse=True)
    return [(p, mv, mt, eff) for _, eff, p, mv, mt in scored[:3]]


class _Signals(QObject):
    result_ready  = pyqtSignal(object)
    evo_ready     = pyqtSignal(object)
    error         = pyqtSignal(object)
    status        = pyqtSignal(str)
    db_status     = pyqtSignal(str)
    slot1_visible = pyqtSignal(bool)
    slot_cleared  = pyqtSignal(int)


def _type_badge(type_name: str, small: bool = False) -> QLabel:
    bg, fg = TYPE_COLORS.get(type_name, ("#888", "#fff"))
    lbl = QLabel(type_name.upper())
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if small:
        lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:2px;"
            f"padding:1px 3px; font-size:10px; font-weight:bold;"
        )
        lbl.setFixedHeight(14)
    else:
        lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:4px;"
            f"padding:2px 6px; font-size:20px; font-weight:bold;"
        )
        lbl.setFixedHeight(28)
    return lbl


class _ResizeGrip(QWidget):
    def __init__(self, panel):
        super().__init__(panel)
        self._panel   = panel
        self._drag_y  = None
        self._start_h = None
        self.setFixedHeight(10)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setPen(QColor("#45475a"))
        cx, y = self.width() // 2, self.height() // 2
        for dx in (-8, -4, 0, 4, 8):
            p.drawEllipse(cx + dx - 1, y - 1, 2, 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_y  = event.globalPosition().toPoint().y()
            self._start_h = self._panel.height()

    def mouseMoveEvent(self, event):
        if self._drag_y is not None:
            dy = event.globalPosition().toPoint().y() - self._drag_y
            self._panel.resize(self._panel.width(), max(400, self._start_h + dy))

    def mouseReleaseEvent(self, _event):
        self._drag_y = None
        p = self._panel.pos()
        window_state.save_key("panel", {"x": p.x(), "y": p.y(), "h": self._panel.height()})


class OverlayPanel(QWidget):
    def __init__(self, capture_boxes: list, embedded: bool = False):
        super().__init__()
        self._embedded          = embedded
        self._boxes             = capture_boxes
        self._slot_data         = [None] * NUM_SLOTS
        self._slot_evos         = [None] * NUM_SLOTS
        self._user_input_active = [False] * NUM_SLOTS
        self._drag_pos          = None

        self._name_lbls        = [None] * NUM_SLOTS
        self._type_rows        = [None] * NUM_SLOTS
        self._bst_lbls         = [None] * NUM_SLOTS
        self._tier_badges      = [None] * NUM_SLOTS
        self._level_lbls       = [None] * NUM_SLOTS
        self._hp_lbls          = [None] * NUM_SLOTS
        self._weak_rows        = [None] * NUM_SLOTS
        self._no_weak_lbls     = [None] * NUM_SLOTS
        self._weak_containers  = [None] * NUM_SLOTS
        self._resist_containers = [None] * NUM_SLOTS
        self._name_inputs      = [None] * NUM_SLOTS
        self._matchup_layouts  = [None] * NUM_SLOTS
        self._miss_counts          = [0] * NUM_SLOTS
        self._type_miss_counts     = [0] * NUM_SLOTS
        self._sampled_types        = [[None, None] for _ in range(NUM_SLOTS)]
        self._last_ocr_name        = [""] * NUM_SLOTS
        self._form_locked          = [False] * NUM_SLOTS
        self._form_lookup_pending  = set()
        self._slot1_container = None
        self._vs_div          = None
        self._active_pokemon  = ""

        self._signals = _Signals()
        self._signals.result_ready.connect(self._on_result)
        self._signals.evo_ready.connect(self._on_evo)
        self._signals.error.connect(self._on_error)
        self._signals.status.connect(self._set_status)
        self._signals.db_status.connect(self._set_db_status)
        self._signals.slot1_visible.connect(self._set_slot1_visible)
        self._signals.slot_cleared.connect(self._clear_slot_display)

        if not embedded:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedWidth(340)
            self.setMinimumHeight(400)

        self._build_ui()

        if not embedded:
            saved_h = window_state.load().get("panel", {}).get("h", 760)
            self.resize(340, saved_h)

        stats_db.init(
            on_progress=lambda msg: self._signals.db_status.emit(msg),
            on_ready=lambda: self._signals.db_status.emit(""),
        )
        tier_db.init()
        moves_db.init()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
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

        card_vbox = QVBoxLayout(self._card)
        card_vbox.setContentsMargins(0, 0, 0, 0)
        card_vbox.setSpacing(0)

        title_widget = QWidget()
        title_widget.setStyleSheet("background: transparent;")
        title_vbox = QVBoxLayout(title_widget)
        title_vbox.setContentsMargins(12, 10, 12, 6)
        title_vbox.setSpacing(4)

        title_row = QHBoxLayout()
        title_lbl = QLabel("Opponents")
        title_lbl.setStyleSheet("color:#cdd6f4; font-size:13px; font-weight:bold;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        if not self._embedded:
            close_btn = QPushButton("✕")
            close_btn.setFixedSize(20, 20)
            close_btn.setStyleSheet(
                "QPushButton{background:transparent;color:#6c7086;border:none;font-size:18px;}"
                "QPushButton:hover{color:#f38ba8;}"
            )
            close_btn.clicked.connect(QApplication.quit)
            title_row.addWidget(close_btn)
        title_vbox.addLayout(title_row)

        self._db_status_lbl = QLabel("")
        self._db_status_lbl.setStyleSheet("color:#a6e3a1; font-size:18px;")
        self._db_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._db_status_lbl.setVisible(False)
        title_vbox.addWidget(self._db_status_lbl)

        card_vbox.addWidget(title_widget)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea  { background: transparent; border: none; }
            QScrollBar:vertical {
                background: #181825; width: 5px; border-radius: 2px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #45475a; border-radius: 2px; min-height: 20px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
        """)

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        inner = QVBoxLayout(scroll_content)
        inner.setContentsMargins(12, 4, 8, 8)
        inner.setSpacing(8)

        for slot in range(NUM_SLOTS):
            if slot > 0:
                vs_div = self._vs_divider()
                inner.addWidget(vs_div)
                self._vs_div = vs_div
                container = QWidget()
                container.setStyleSheet("background: transparent;")
                slot_layout = QVBoxLayout(container)
                slot_layout.setContentsMargins(0, 0, 0, 0)
                slot_layout.setSpacing(8)
                self._build_slot_section(slot_layout, slot)
                inner.addWidget(container)
                self._slot1_container = container
            else:
                self._build_slot_section(inner, slot)

        inner.addStretch()
        scroll.setWidget(scroll_content)
        card_vbox.addWidget(scroll, 1)

        ctrl_frame = QWidget()
        ctrl_frame.setStyleSheet("background: transparent;")
        ctrl_vbox = QVBoxLayout(ctrl_frame)
        ctrl_vbox.setContentsMargins(12, 6, 12, 10)
        ctrl_vbox.setSpacing(6)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#6c7086; font-size:18px;")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl_vbox.addWidget(self._status_lbl)

        card_vbox.addWidget(ctrl_frame)
        if not self._embedded:
            card_vbox.addWidget(_ResizeGrip(self))

    def _build_slot_section(self, inner: QVBoxLayout, slot: int):
        name_row = QHBoxLayout()
        name_row.setSpacing(4)

        inp = QLineEdit()
        inp.setPlaceholderText(f"slot {slot + 1}…")
        inp.setMinimumWidth(0)
        inp.setStyleSheet(
            "QLineEdit{background:transparent;color:#cdd6f4;border:none;"
            "border-bottom:1px solid transparent;font-size:22px;font-weight:bold;padding:0 1px;}"
            "QLineEdit:focus{border-bottom:1px solid #89b4fa;}"
        )
        inp.returnPressed.connect(lambda s=slot: self._lookup_from_input(s))
        inp.textEdited.connect(lambda _, s=slot: self._set_user_active(s))
        name_row.addWidget(inp, 1)
        self._name_inputs[slot] = inp
        self._name_lbls[slot]   = None

        type_row = QHBoxLayout()
        type_row.setSpacing(3)
        name_row.addLayout(type_row)
        inner.addLayout(name_row)
        self._type_rows[slot] = type_row

        bst_row = QHBoxLayout()
        bst_row.setSpacing(6)
        bst_lbl = QLabel("")
        bst_lbl.setStyleSheet("color:#a6adc8; font-size:21px;")
        bst_lbl.setVisible(False)
        tier_badge = QLabel("")
        tier_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tier_badge.setFixedHeight(26)
        tier_badge.setVisible(False)
        level_lbl = QLabel("")
        level_lbl.setStyleSheet("color:#89b4fa; font-size:18px;")
        level_lbl.setVisible(False)
        hp_lbl = QLabel("")
        hp_lbl.setStyleSheet("color:#a6e3a1; font-size:18px;")
        hp_lbl.setVisible(False)
        bst_row.addWidget(bst_lbl)
        bst_row.addStretch()
        bst_row.addWidget(hp_lbl)
        bst_row.addWidget(tier_badge)
        bst_row.addWidget(level_lbl)
        inner.addLayout(bst_row)
        self._bst_lbls[slot]    = bst_lbl
        self._tier_badges[slot] = tier_badge
        self._level_lbls[slot]  = level_lbl
        self._hp_lbls[slot]     = hp_lbl

        inner.addWidget(self._divider())

        inner.addWidget(self._section_hdr("SEND IN"))
        matchup_layout = QVBoxLayout()
        matchup_layout.setSpacing(2)
        inner.addLayout(matchup_layout)
        self._matchup_layouts[slot] = matchup_layout

        inner.addWidget(self._divider())

        # Weaknesses — hidden by default, shown via settings toggle
        weak_container = QWidget()
        weak_container.setStyleSheet("background: transparent;")
        weak_vbox = QVBoxLayout(weak_container)
        weak_vbox.setContentsMargins(0, 0, 0, 0)
        weak_vbox.setSpacing(0)

        weak_rows = {}
        weak_vbox.addWidget(self._section_hdr("WEAKNESSES"))
        for key, label, color in [
            ("4x", "4×", "#f38ba8"), ("2x", "2×", "#fab387"),
        ]:
            w, lbl = self._type_row_widget(label, color)
            weak_vbox.addWidget(w)
            weak_rows[key] = (w, lbl)

        no_weak_lbl = QLabel("No notable weaknesses")
        no_weak_lbl.setStyleSheet("color:#6c7086; font-size:21px;")
        no_weak_lbl.setVisible(False)
        weak_vbox.addWidget(no_weak_lbl)
        self._no_weak_lbls[slot] = no_weak_lbl

        weak_vbox.addWidget(self._divider())
        weak_container.setVisible(False)
        inner.addWidget(weak_container)
        self._weak_containers[slot] = weak_container

        # Resistances — always visible once a Pokémon is loaded
        resist_container = QWidget()
        resist_container.setStyleSheet("background: transparent;")
        resist_vbox = QVBoxLayout(resist_container)
        resist_vbox.setContentsMargins(0, 0, 0, 0)
        resist_vbox.setSpacing(0)
        resist_vbox.addWidget(self._section_hdr("RESISTANCES"))
        for key, label, color in [
            ("025x", "¼×", "#89dceb"), ("05x", "½×", "#89b4fa"), ("0x", "0×", "#6c7086"),
        ]:
            w, lbl = self._type_row_widget(label, color, small=True)
            resist_vbox.addWidget(w)
            weak_rows[key] = (w, lbl)

        resist_vbox.addWidget(self._divider())
        self._weak_rows[slot] = weak_rows

        resist_container.setVisible(False)
        inner.addWidget(resist_container)
        self._resist_containers[slot] = resist_container

    @property
    def slot1_visible(self):
        return self._signals.slot1_visible

    def _vs_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("QFrame { color: #44475a; margin-top: 4px; margin-bottom: 4px; }")
        return line

    def _set_slot1_visible(self, visible: bool):
        if self._vs_div:
            self._vs_div.setVisible(visible)
        if self._slot1_container:
            self._slot1_container.setVisible(visible)

    def reset_slot1(self):
        """Immediately clear and hide slot 1 — called when fight mode drops to 1v1."""
        self._miss_counts[1] = 3          # prevent spurious re-shows before next OCR cycle
        self._clear_slot_display(1)
        self._set_slot1_visible(False)

    def _clear_slot_display(self, slot: int):
        if self._slot_data[slot] is None:
            return
        self._slot_data[slot] = None
        self._slot_evos[slot] = None
        self._form_locked[slot] = False
        self._type_miss_counts[slot] = 0
        self._sampled_types[slot] = [None, None]
        self._last_ocr_name[slot] = ""
        self._form_lookup_pending.discard(slot)

        self._name_inputs[slot].clear()

        tr = self._type_rows[slot]
        for i in reversed(range(tr.count())):
            w = tr.itemAt(i).widget()
            if w:
                w.deleteLater()

        self._bst_lbls[slot].setVisible(False)
        self._tier_badges[slot].setVisible(False)
        self._level_lbls[slot].setVisible(False)
        self._hp_lbls[slot].setVisible(False)

        rows = self._weak_rows[slot]
        for key in rows:
            container, label = rows[key]
            label.setVisible(False)
            vbox = container.layout()
            while vbox.count():
                item = vbox.takeAt(0)
                if item.layout():
                    while item.layout().count():
                        w = item.layout().takeAt(0).widget()
                        if w and w is not label:
                            w.deleteLater()
        self._no_weak_lbls[slot].setVisible(False)
        if self._resist_containers[slot] is not None:
            self._resist_containers[slot].setVisible(False)

        layout = self._matchup_layouts[slot]
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout_items(item.layout())

        self._name_inputs[slot].setToolTip("")

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#313244;")
        return line

    def _section_hdr(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#6c7086; font-size:18px; font-weight:bold; letter-spacing:1px;")
        return lbl

    def _type_row_widget(self, multiplier: str, color: str, small: bool = False):
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label = QLabel(multiplier)
        if small:
            label.setStyleSheet(f"color:{color}; font-size:11px; font-weight:bold; min-width:18px;")
        else:
            label.setStyleSheet(f"color:{color}; font-size:21px; font-weight:bold; min-width:36px;")
        label.setVisible(False)
        return widget, label

    def _fill_weak_row(self, container: QWidget, label: QLabel, types_list: list, small: bool = False):
        vbox = container.layout()
        while vbox.count():
            item = vbox.takeAt(0)
            sub = item.layout()
            if sub:
                while sub.count():
                    si = sub.takeAt(0)
                    w = si.widget()
                    if w is not None and w is not label:
                        w.deleteLater()

        label.setVisible(bool(types_list))
        if not types_list:
            return

        spacer_w = 18 if small else 36
        for chunk_start in range(0, len(types_list), BADGES_PER_ROW):
            chunk = types_list[chunk_start:chunk_start + BADGES_PER_ROW]
            row = QHBoxLayout()
            row.setSpacing(2 if small else 4)
            if chunk_start == 0:
                row.addWidget(label)
            else:
                spacer = QLabel()
                spacer.setFixedWidth(spacer_w)
                spacer.setStyleSheet("background: transparent;")
                row.addWidget(spacer)
            for t in chunk:
                row.addWidget(_type_badge(t, small=small))
            row.addStretch()
            vbox.addLayout(row)

    # ── OCR result receiver (called by OCRService on main thread) ────────────────

    def receive_ocr_result(self, slot: int, text: str) -> None:
        if self._user_input_active[slot]:
            return
        cleaned = text.strip().lower().replace("♀", "").replace("♂", "").replace("'", "")
        name = "".join(c for c in cleaned if c.isalpha() or c == "-")

        if len(name) < 3:
            name = ""

        if name and stats_db.is_ready():
            known = stats_db.all_names()
            matched = _resolve_name(name, known)
            if not matched:
                name = ""

        if name:
            if self._type_miss_counts[slot] >= 4:
                return  # type badge not visible — likely a menu, suppress
            self._miss_counts[slot] = 0
            if slot == 1:
                self._signals.slot1_visible.emit(True)
            if name == self._last_ocr_name[slot] and self._slot_data[slot] is not None:
                return  # same read as last tick and we already have data — skip re-lookup
            self._last_ocr_name[slot] = name
            threading.Thread(target=self._lookup, args=(slot, name), daemon=True).start()
        else:
            self._last_ocr_name[slot] = ""
            self._miss_counts[slot] += 1
            if self._miss_counts[slot] >= 3:
                self._signals.slot_cleared.emit(slot)
                if slot == 1:
                    self._signals.slot1_visible.emit(False)

    def receive_opponent_level(self, slot: int, text: str) -> None:
        """Called by OCRService on main thread with raw digit text for opponent level."""
        digits = "".join(c for c in text if c.isdigit())
        lbl = self._level_lbls[slot]
        if lbl is None:
            return
        if digits and self._slot_data[slot] is not None:
            lbl.setText(f"Lv.{digits}")
            lbl.setVisible(True)

    def receive_opponent_hp(self, slot: int, text: str) -> None:
        """Called by OCRService on main thread with raw HP text, e.g. '324/369'."""
        cleaned = "".join(c for c in text if c.isdigit() or c == "/")
        lbl = self._hp_lbls[slot]
        if lbl is None:
            return
        if "/" in cleaned and self._slot_data[slot] is not None:
            lbl.setText(cleaned)
            lbl.setVisible(True)

    # ── lookup ────────────────────────────────────────────────────────────────

    def _set_user_active(self, slot: int):
        self._user_input_active[slot] = True

    def _lookup_from_input(self, slot: int):
        self._user_input_active[slot] = False
        name = self._name_inputs[slot].text().strip().lower()
        if name:
            self._set_status("Looking up…")
            threading.Thread(target=self._lookup, args=(slot, name), daemon=True).start()

    def _lookup(self, slot: int, name: str):
        try:
            pokemon = fetch_pokemon(name)
            weaknesses = calculate_weaknesses(pokemon.types)
            self._signals.result_ready.emit((slot, pokemon, weaknesses))
            return
        except Exception as exc:
            if "404" not in str(exc):
                self._signals.error.emit((slot, str(exc)))
                return

        names = stats_db.all_names()
        if names:
            matched = _resolve_name(name, names)
            if matched:
                self._signals.status.emit(f"'{name}' → '{matched}'")
                try:
                    pokemon = fetch_pokemon(matched)
                    weaknesses = calculate_weaknesses(pokemon.types)
                    self._signals.result_ready.emit((slot, pokemon, weaknesses))
                    return
                except Exception as exc:
                    self._signals.error.emit((slot, str(exc)))
                    return

        self._signals.error.emit((slot, f"Not found: {name}"))

    # ── evolution fetch ───────────────────────────────────────────────────────

    def _fetch_evolutions(self, slot: int, pokemon_name: str):
        try:
            finals = fetch_final_evolutions(pokemon_name)
            targets = [f for f in finals if f != pokemon_name]
            if not targets:
                self._signals.evo_ready.emit((slot, []))
                return
            results = []
            for form_name in targets:
                try:
                    form = fetch_pokemon(form_name)
                    bst = sum(form.stats.values())
                    pct = stats_db.bst_percentile(bst, pool="final") if stats_db.is_ready() else None
                    results.append((form.name, bst, pct))
                except Exception:
                    pass
            self._signals.evo_ready.emit((slot, results))
        except Exception:
            self._signals.evo_ready.emit((slot, []))

    # ── signal handlers ───────────────────────────────────────────────────────

    def _on_result(self, payload):
        slot, pokemon, weaknesses = payload
        self._miss_counts[slot] = 0
        if slot == 1:
            self._set_slot1_visible(True)
        self._slot_data[slot] = (pokemon, weaknesses)
        self._slot_evos[slot] = None
        self._display(slot, pokemon, weaknesses)
        threading.Thread(target=self._fetch_evolutions, args=(slot, pokemon.name), daemon=True).start()
        if any(t is not None for t in self._sampled_types[slot]):
            self._apply_type_override(slot)

    def _on_evo(self, payload):
        slot, evos = payload
        self._slot_evos[slot] = evos
        self._display_evos(slot, evos)

    def _on_error(self, payload):
        slot, msg = payload
        self._show_error(msg)

    # ── display ───────────────────────────────────────────────────────────────

    def _display(self, slot: int, pokemon: PokemonData, weaknesses: dict):
        if self._user_input_active[slot]:
            return

        self._name_inputs[slot].setText(pokemon.name.capitalize())

        type_row = self._type_rows[slot]
        for i in reversed(range(type_row.count())):
            w = type_row.itemAt(i).widget()
            if w:
                w.deleteLater()
        for t in pokemon.types:
            type_row.addWidget(_type_badge(t))

        bst = sum(pokemon.stats.values())
        if stats_db.is_ready():
            pct = stats_db.bst_percentile(bst)
            color = _pct_color(pct)
            self._bst_lbls[slot].setText(
                f'BST {bst}  ·  <span style="color:{color}">{_ordinal(pct)} %ile</span>'
            )
        else:
            self._bst_lbls[slot].setText(f"BST {bst}")
        self._bst_lbls[slot].setVisible(True)

        t = tier_db.get_tier(pokemon.name) if tier_db.is_ready() else None
        if t:
            bg, fg = tier_db.tier_color(t)
            self._tier_badges[slot].setText(t)
            self._tier_badges[slot].setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:3px;"
                f"padding:1px 6px; font-size:18px; font-weight:bold;"
            )
            self._tier_badges[slot].setVisible(True)
        else:
            self._tier_badges[slot].setVisible(False)

        rows = self._weak_rows[slot]
        w4     = sorted(t for t, m in weaknesses.items() if m >= 4.0)
        w2     = sorted(t for t, m in weaknesses.items() if m == 2.0)
        immune = sorted(t for t, m in weaknesses.items() if m == 0.0)
        r025   = sorted(t for t, m in weaknesses.items() if m == 0.25)
        r05    = sorted(t for t, m in weaknesses.items() if m == 0.5)
        self._fill_weak_row(*rows["4x"],   w4)
        self._fill_weak_row(*rows["2x"],   w2)
        self._fill_weak_row(*rows["025x"], r025, small=True)
        self._fill_weak_row(*rows["05x"],  r05,  small=True)
        self._fill_weak_row(*rows["0x"],   immune, small=True)
        self._no_weak_lbls[slot].setVisible(not w4 and not w2)
        self._resist_containers[slot].setVisible(True)

        self._name_inputs[slot].setToolTip("")  # cleared; tooltip set async when evos arrive

        self._display_matchup(slot, weaknesses)

    def _display_matchup(self, slot: int, weaknesses: dict):
        layout = self._matchup_layouts[slot]
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout_items(item.layout())

        team = window_state.load().get("team", [])
        picks = _best_matchup(weaknesses, team)

        if not picks:
            lbl = QLabel("No team data")
            lbl.setStyleSheet("color:#45475a; font-size:18px;")
            layout.addWidget(lbl)
            return

        for i, (pname, move_name, move_type, eff) in enumerate(picks):
            is_active = bool(self._active_pokemon and pname.lower() == self._active_pokemon)

            row_widget = QFrame()
            row_widget.setObjectName("active_row" if is_active else "")
            row_widget.setStyleSheet(
                "QFrame#active_row { background: #1e3a2f; border-radius: 3px; border: 1px solid #40665a; }"
                if is_active else
                "QFrame { background: transparent; }"
            )
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(4, 2, 4, 2)
            row.setSpacing(5)

            rank = QLabel(f"{i + 1}.")
            rank.setFixedWidth(16)
            rank.setStyleSheet("color:#6c7086; font-size:18px; background: transparent;")
            row.addWidget(rank)

            name_color = "#a6e3a1" if is_active else ("#cdd6f4" if i == 0 else "#a6adc8")
            name_lbl = QLabel(pname.capitalize())
            name_lbl.setStyleSheet(
                f"color:{name_color}; font-size:18px; background: transparent;"
                + (" font-weight:bold;" if i == 0 or is_active else "")
            )
            row.addWidget(name_lbl)

            bg, fg = TYPE_COLORS.get(move_type, ("#888", "#fff"))
            badge = QLabel(move_type[:4].upper())
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(52, 22)
            badge.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:3px; font-size:17px; font-weight:bold;"
            )
            row.addWidget(badge)

            move_lbl = QLabel(move_name)
            move_lbl.setStyleSheet("color:#6c7086; font-size:18px; background: transparent;")
            row.addWidget(move_lbl)

            row.addStretch()

            eff_str = "4×" if eff >= 4 else "2×" if eff >= 2 else "1×"
            eff_color = "#f38ba8" if eff >= 4 else "#fab387" if eff >= 2 else "#a6adc8"
            eff_lbl = QLabel(eff_str)
            eff_lbl.setStyleSheet(f"color:{eff_color}; font-size:18px; font-weight:bold; background: transparent;")
            row.addWidget(eff_lbl)

            layout.addWidget(row_widget)

    def _clear_layout_items(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _display_evos(self, slot: int, evos: list):
        if not evos:
            self._name_inputs[slot].setToolTip("")
            return
        lines = ["Evolves into:"]
        for name, bst, pct in evos:
            pct_str = f"  {_ordinal(pct)} %ile" if pct is not None else ""
            lines.append(f"  → {name.capitalize()}    BST {bst}{pct_str}")
        self._name_inputs[slot].setToolTip("\n".join(lines))

    def set_active_pokemon(self, name: str):
        self._active_pokemon = name.strip().lower()
        for slot in range(NUM_SLOTS):
            if self._slot_data[slot] is not None:
                _, weaknesses = self._slot_data[slot]
                self._display_matchup(slot, weaknesses)

    def set_weaknesses_visible(self, visible: bool):
        for c in self._weak_containers:
            if c is not None:
                c.setVisible(visible)

    # ── Type color override (from TypeColorService) ───────────────────────────

    def receive_type_sample(self, slot: int, type_index: int, type_name: str | None) -> None:
        if type_index == 0:  # primary badge drives the gate
            if type_name is not None:  # includes "_boss"
                self._type_miss_counts[slot] = 0
            else:
                self._type_miss_counts[slot] += 1
        if type_name == "_boss":
            return  # boss confirmed — gate reset; not a real type, skip override
        if self._sampled_types[slot][type_index] == type_name:
            return
        self._sampled_types[slot][type_index] = type_name
        self._apply_type_override(slot)

    def _apply_type_override(self, slot: int):
        data = self._slot_data[slot]
        if data is None:
            return
        pokemon, _ = data
        sampled = list(dict.fromkeys(t for t in self._sampled_types[slot] if t is not None))
        types = sampled if sampled else pokemon.types

        if sampled and set(sampled) != set(pokemon.types):
            self._try_form_from_types(slot, pokemon.name, sampled)

        weaknesses = calculate_weaknesses(types)
        self._slot_data[slot] = (pokemon, weaknesses)

        type_row = self._type_rows[slot]
        for i in reversed(range(type_row.count())):
            w = type_row.itemAt(i).widget()
            if w:
                w.deleteLater()
        for t in types:
            type_row.addWidget(_type_badge(t))

        rows = self._weak_rows[slot]
        w4     = sorted(t for t, m in weaknesses.items() if m >= 4.0)
        w2     = sorted(t for t, m in weaknesses.items() if m == 2.0)
        immune = sorted(t for t, m in weaknesses.items() if m == 0.0)
        r025   = sorted(t for t, m in weaknesses.items() if m == 0.25)
        r05    = sorted(t for t, m in weaknesses.items() if m == 0.5)
        self._fill_weak_row(*rows["4x"],   w4)
        self._fill_weak_row(*rows["2x"],   w2)
        self._fill_weak_row(*rows["025x"], r025, small=True)
        self._fill_weak_row(*rows["05x"],  r05,  small=True)
        self._fill_weak_row(*rows["0x"],   immune, small=True)
        self._no_weak_lbls[slot].setVisible(not w4 and not w2)
        self._resist_containers[slot].setVisible(True)
        self._display_matchup(slot, weaknesses)

    def _try_form_from_types(self, slot: int, pokemon_name: str, sampled: list):
        base = _SLUG_TO_BASE.get(pokemon_name, pokemon_name)
        variants = _FORM_VARIANTS.get(base)
        if not variants or slot in self._form_lookup_pending:
            return
        self._form_lookup_pending.add(slot)

        def _check():
            try:
                for _label, slug in variants:
                    if slug == pokemon_name:
                        continue
                    try:
                        form = fetch_pokemon(slug)
                        if set(form.types) == set(sampled):
                            weaknesses = calculate_weaknesses(form.types)
                            self._signals.result_ready.emit((slot, form, weaknesses))
                            return
                    except Exception:
                        pass
            finally:
                self._form_lookup_pending.discard(slot)

        threading.Thread(target=_check, daemon=True).start()

    def _show_error(self, msg: str):
        self._status_lbl.setText(f"Error: {msg}")
        self._status_lbl.setStyleSheet("color:#f38ba8; font-size:18px;")

    def _set_status(self, msg: str):
        self._status_lbl.setText(msg)
        self._status_lbl.setStyleSheet("color:#6c7086; font-size:18px;")

    def _set_db_status(self, msg: str):
        self._db_status_lbl.setText(msg)
        self._db_status_lbl.setVisible(bool(msg))

    # ── drag to move (standalone only) ───────────────────────────────────────

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
        window_state.save_key("panel", {"x": p.x(), "y": p.y(), "h": self.height()})
