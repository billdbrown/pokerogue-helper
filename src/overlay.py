from __future__ import annotations
import threading
import difflib
import window_state
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import QGraphicsOpacityEffect

from pokemon_api import fetch_pokemon, fetch_final_evolutions, fetch_ability, fetch_move, fetch_learnable_coverage, nature_mod_str, MoveData, PokemonData
from damage_calc import damage_range, pct_color as dmg_pct_color
from weakness_calc import calculate_weaknesses, slot_coverage
from scoring import offensive_coverage
import stats_db
import tier_db
import moves_db
import impact_db

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


def _impact_tooltip(entry: dict, evo_line: str = "") -> str:
    lines = []
    if evo_line:
        lines.append(evo_line)
    moves = entry.get("moves", [])
    if moves:
        if lines:
            lines.append("")
        lines.append("Optimal moveset:")
        for m in moves:
            name = m["name"].replace("-", " ").title()
            lines.append(f"  {name}  [{m['type']}]  {m['power']} BP")
    return "\n".join(lines)


def _move_cell(m: dict, contributing: set | None = None) -> str:
    bg, fg     = TYPE_COLORS.get(m["type"], ("#888", "#fff"))
    name       = m["name"].replace("-", " ").title()
    name_color = "#a6e3a1" if (contributing and m["name"] in contributing) else "#cdd6f4"
    return (
        f"<span style='background:{bg}; color:{fg}; border-radius:3px;"
        f" padding:1px 6px; font-size:11px; font-weight:bold;'>"
        f"&nbsp;{m['type'][:4].upper()}&nbsp;</span>"
        f"<span style='font-size:13px; color:{name_color};'> {name}</span>"
    )


def _moves_html(moves: list, contributing: set | None = None) -> str:
    """Render up to 4 moves in a 2×2 grid."""
    if not moves:
        return ""
    cells = [_move_cell(m, contributing) for m in moves[:4]]
    row1 = "&nbsp;&nbsp;".join(cells[:2])
    if len(cells) > 2:
        row2 = "&nbsp;&nbsp;".join(cells[2:])
        return f"{row1}<br>{row2}"
    return row1


def _fmt_delta(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{int(val):,}"


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
    result_ready          = pyqtSignal(object)
    evo_ready             = pyqtSignal(object)
    error                 = pyqtSignal(object)
    status                = pyqtSignal(str)
    db_status             = pyqtSignal(str)
    slot1_visible         = pyqtSignal(bool)
    slot_cleared          = pyqtSignal(int)
    ability_tooltip_ready = pyqtSignal(int, str)
    moves_ready           = pyqtSignal(object)  # (slot, list[MoveData | None])
    rec_ready             = pyqtSignal(object)  # (slot, dict | None)


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
        self._pct_lbls         = [None] * NUM_SLOTS
        self._tier_badges      = [None] * NUM_SLOTS
        self._leg_lbls         = [None] * NUM_SLOTS
        self._level_lbls       = [None] * NUM_SLOTS
        self._level_vals: list[int | None] = [None] * NUM_SLOTS  # parsed opponent level per slot, for turn-order calc
        self._hp_lbls          = [None] * NUM_SLOTS
        self._ability_lbls     = [None] * NUM_SLOTS
        self._moves_containers = [None] * NUM_SLOTS
        self._moves_rows       = [None] * NUM_SLOTS   # QGridLayout holding move cells (2×2)
        self._moves_cells      = [[] for _ in range(NUM_SLOTS)]   # list[QFrame] per slot
        self._enemy_moves      = [[] for _ in range(NUM_SLOTS)]   # list[MoveData] per slot
        self._swap_lbls        = [None] * NUM_SLOTS
        self._last_move_names  = [[] for _ in range(NUM_SLOTS)]
        self._player_active_types: list = []
        self._battle_type: int = 0   # 0=wild, 1=trainer
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

        self._last_abilities = [(None, None)] * NUM_SLOTS

        # Catch recommendation
        self._rec_lbls: list[QLabel | None]          = [None] * NUM_SLOTS
        self._rec_effects: list                      = [None] * NUM_SLOTS
        self._rec_anims: list                        = [None] * NUM_SLOTS
        self._slot_bst_pcts: list[float | None]      = [None] * NUM_SLOTS
        self._party_names: list[str]                 = []
        self._party_current_moves: list[list[str]]   = []  # currently equipped move names
        self._party_stat_totals: list[int | None]    = []  # sum of all 6 live stats per member
        self._party_snapshot: list[dict]             = []  # full party snapshot (live stats + nature)
        self._enemy_stat_totals: list[int | None]    = [None] * NUM_SLOTS
        self._enemy_perm_stats: list[dict]           = [{} for _ in range(NUM_SLOTS)]
        self._team_type_groups: list[list[str]]      = []

        # Damage calculation state
        self._enemy_battle_stats: list[dict] = [{} for _ in range(NUM_SLOTS)]
        self._player_battle_stats: dict = {}   # {def, spd, max_hp}
        self._moves_dmg_lbls: list[list] = [[] for _ in range(NUM_SLOTS)]

        self._signals = _Signals()
        self._signals.result_ready.connect(self._on_result)
        self._signals.evo_ready.connect(self._on_evo)
        self._signals.error.connect(self._on_error)
        self._signals.status.connect(self._set_status)
        self._signals.db_status.connect(self._set_db_status)
        self._signals.slot1_visible.connect(self._set_slot1_visible)
        self._signals.slot_cleared.connect(self._clear_slot_display)
        self._signals.ability_tooltip_ready.connect(self._on_ability_tooltip)
        self._signals.moves_ready.connect(self._on_moves_ready)
        self._signals.rec_ready.connect(self._on_rec_ready)

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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        # Row 1: name | BST | %ile | types
        name_row = QHBoxLayout()
        name_row.setSpacing(6)

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

        bst_lbl = QLabel("")
        bst_lbl.setStyleSheet("color:#a6adc8; font-size:14px;")
        bst_lbl.setVisible(False)
        name_row.addWidget(bst_lbl)
        self._bst_lbls[slot] = bst_lbl

        pct_lbl = QLabel("")
        pct_lbl.setStyleSheet("color:#a6adc8; font-size:14px;")
        pct_lbl.setVisible(False)
        name_row.addWidget(pct_lbl)
        self._pct_lbls[slot] = pct_lbl

        type_row = QHBoxLayout()
        type_row.setSpacing(3)
        name_row.addLayout(type_row)
        self._type_rows[slot] = type_row

        self._tier_badges[slot] = None  # removed from layout

        leg_lbl = QLabel("")
        leg_lbl.setVisible(False)
        name_row.addWidget(leg_lbl)
        self._leg_lbls[slot] = leg_lbl

        inner.addLayout(name_row)

        # Row 2: level (left) | HP (right)
        lv_hp_row = QHBoxLayout()
        lv_hp_row.setSpacing(4)
        lv_hp_row.setContentsMargins(0, 0, 0, 0)

        level_lbl = QLabel("")
        level_lbl.setStyleSheet("color:#89b4fa; font-size:13px;")
        level_lbl.setVisible(False)
        lv_hp_row.addWidget(level_lbl)
        self._level_lbls[slot] = level_lbl

        lv_hp_row.addStretch()

        hp_lbl = QLabel("")
        hp_lbl.setStyleSheet("color:#a6e3a1; font-size:13px;")
        hp_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hp_lbl.setVisible(False)
        lv_hp_row.addWidget(hp_lbl)
        self._hp_lbls[slot] = hp_lbl

        inner.addLayout(lv_hp_row)

        ability_lbl = QLabel("")
        ability_lbl.setStyleSheet("color:#cba6f7; font-size:15px;")
        ability_lbl.setWordWrap(True)
        ability_lbl.setVisible(False)
        inner.addWidget(ability_lbl)
        self._ability_lbls[slot] = ability_lbl

        rec_lbl = QLabel("")
        rec_lbl.setWordWrap(True)
        rec_lbl.setTextFormat(Qt.TextFormat.RichText)
        rec_lbl.setVisible(False)
        inner.addWidget(rec_lbl)
        self._rec_lbls[slot] = rec_lbl

        effect = QGraphicsOpacityEffect(rec_lbl)
        effect.setOpacity(1.0)
        rec_lbl.setGraphicsEffect(effect)
        self._rec_effects[slot] = effect

        pulse = QPropertyAnimation(effect, b"opacity", rec_lbl)
        pulse.setDuration(2800)
        pulse.setKeyValueAt(0.0, 1.0)
        pulse.setKeyValueAt(0.5, 0.45)
        pulse.setKeyValueAt(1.0, 1.0)
        pulse.setEasingCurve(QEasingCurve.Type.SineCurve)
        pulse.setLoopCount(-1)
        self._rec_anims[slot] = pulse

        moves_container = QWidget()
        moves_container.setStyleSheet("background: transparent;")
        moves_vbox = QVBoxLayout(moves_container)
        moves_vbox.setContentsMargins(0, 2, 0, 0)
        moves_vbox.setSpacing(2)
        moves_vbox.addWidget(self._section_hdr("MOVES"))
        moves_row = QGridLayout()
        moves_row.setSpacing(3)
        moves_row.setContentsMargins(0, 0, 0, 0)
        moves_row.setColumnStretch(0, 1)
        moves_row.setColumnStretch(1, 1)
        moves_vbox.addLayout(moves_row)
        swap_lbl = QLabel("")
        swap_lbl.setStyleSheet("color:#6c7086; font-size:13px;")
        swap_lbl.setVisible(False)
        moves_vbox.addWidget(swap_lbl)
        moves_container.setVisible(False)
        inner.addWidget(moves_container)
        self._moves_containers[slot] = moves_container
        self._moves_rows[slot] = moves_row
        self._swap_lbls[slot] = swap_lbl

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

    def refresh_matchups(self):
        """Re-render the SEND IN list for every active opponent slot. Called when
        the team's moves change so the recommendations don't go stale until the
        next opponent swap."""
        for slot in range(NUM_SLOTS):
            data = self._slot_data[slot]
            if data is None:
                continue
            _, weaknesses = data
            self._display_matchup(slot, weaknesses)

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
        self._level_vals[slot] = None
        self._last_abilities[slot] = (None, None)
        self._last_move_names[slot] = []
        self._enemy_moves[slot] = []
        self._moves_cells[slot] = []
        self._moves_dmg_lbls[slot] = []
        self._enemy_battle_stats[slot] = {}
        self._enemy_stat_totals[slot]  = None
        if self._swap_lbls[slot] is not None:
            self._swap_lbls[slot].setVisible(False)
        if self._moves_containers[slot] is not None:
            self._moves_containers[slot].setVisible(False)
        if self._moves_rows[slot] is not None:
            row = self._moves_rows[slot]
            while row.count():
                item = row.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        self._name_inputs[slot].clear()

        tr = self._type_rows[slot]
        for i in reversed(range(tr.count())):
            w = tr.itemAt(i).widget()
            if w:
                w.deleteLater()

        self._bst_lbls[slot].setVisible(False)
        if self._pct_lbls[slot] is not None:
            self._pct_lbls[slot].setVisible(False)
        if self._leg_lbls[slot] is not None:
            self._leg_lbls[slot].setVisible(False)
        if self._rec_anims[slot] is not None:
            self._rec_anims[slot].stop()
        if self._rec_effects[slot] is not None:
            self._rec_effects[slot].setOpacity(1.0)
        if self._rec_lbls[slot] is not None:
            self._rec_lbls[slot].setVisible(False)
        self._slot_bst_pcts[slot] = None
        if self._tier_badges[slot] is not None:
            self._tier_badges[slot].setVisible(False)
        self._level_lbls[slot].setVisible(False)
        self._hp_lbls[slot].setVisible(False)
        if self._ability_lbls[slot] is not None:
            self._ability_lbls[slot].setVisible(False)

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

    # ── Opponent name receiver (called by JS-state dispatcher) ────────────────

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
        """Called by JS-state dispatcher with opponent level as digit text."""
        digits = "".join(c for c in text if c.isdigit())
        lbl = self._level_lbls[slot]
        if lbl is None:
            return
        if digits:
            lbl.setText(f"Lv.{digits}")
            lbl.setVisible(True)
            try:
                lv = int(digits)
            except ValueError:
                return
            if self._level_vals[slot] != lv:
                self._level_vals[slot] = lv

    def receive_opponent_abilities(self, slot: int, ability: str | None, passive: str | None,
                                   ability_index: int | None = None, nature=None) -> None:
        """Called by JS-state dispatcher with the opponent's current ability + passive."""
        lbl = self._ability_lbls[slot]
        if lbl is None:
            return
        ab_parts = []
        if ability:
            hidden = ability_index == 2
            h_mark = " <span style='color:#f9e2af;font-size:11px;'>[H]</span>" if hidden else ""
            ab_parts.append(f"<span style='color:#cba6f7;'>⚡ {ability}{h_mark}</span>")
        if passive:
            ab_parts.append(f"<span style='color:#89b4fa;'>✦ {passive}</span>")
        nat = nature_mod_str(nature)
        if ab_parts or nat:
            left = "  ".join(ab_parts)
            right = (f"<span style='color:#a6adc8;font-size:13px;'>{nat}</span>"
                     if nat else "")
            html = (
                f"<table width='100%' cellpadding='0' cellspacing='0'><tr>"
                f"<td>{left}</td>"
                f"<td align='right'>{right}</td>"
                f"</tr></table>"
            )
            lbl.setText(html)
            lbl.setVisible(True)
            if (ability, passive) != self._last_abilities[slot]:
                self._last_abilities[slot] = (ability, passive)
                threading.Thread(
                    target=self._fetch_ability_tooltips,
                    args=(slot, ability, passive),
                    daemon=True,
                ).start()
        else:
            lbl.setVisible(False)

    def _fetch_ability_tooltips(self, slot: int, ability: str | None, passive: str | None):
        lines = []
        if ability:
            desc = fetch_ability(ability)
            lines.append(f"⚡ {ability}: {desc}" if desc else f"⚡ {ability}")
        if passive:
            desc = fetch_ability(passive)
            lines.append(f"✦ {passive}: {desc}" if desc else f"✦ {passive}")
        self._signals.ability_tooltip_ready.emit(slot, "\n\n".join(lines))

    def _on_ability_tooltip(self, slot: int, tooltip: str):
        lbl = self._ability_lbls[slot]
        if lbl is not None:
            lbl.setToolTip(tooltip)

    def receive_opponent_moves(self, slot: int, move_names: list) -> None:
        """Called by JS-state dispatcher with the opponent's moveset (list of names)."""
        names = [n for n in (move_names or []) if n]
        if not names:
            return
        if names == self._last_move_names[slot]:
            return
        self._last_move_names[slot] = list(names)
        threading.Thread(
            target=self._fetch_moves,
            args=(slot, names),
            daemon=True,
        ).start()

    def _fetch_moves(self, slot: int, names: list):
        results = []
        for name in names:
            try:
                results.append(fetch_move(name))
            except Exception:
                results.append(None)
        self._signals.moves_ready.emit((slot, results))

    def _on_moves_ready(self, payload):
        slot, moves = payload
        row = self._moves_rows[slot]
        container = self._moves_containers[slot]
        if row is None or container is None:
            return
        while row.count():
            item = row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        valid = [m for m in moves if m is not None]
        cells, dmg_lbls = [], []
        for i, move in enumerate(valid):
            cell, dmg_lbl = self._make_move_cell(move)
            row.addWidget(cell, i // 2, i % 2)
            cells.append(cell)
            dmg_lbls.append(dmg_lbl)
        self._enemy_moves[slot] = valid
        self._moves_cells[slot] = cells
        self._moves_dmg_lbls[slot] = dmg_lbls
        container.setVisible(bool(valid) and self._battle_type != 0)
        self._update_move_damage_labels(slot)
        self._run_prediction(slot)
        # Trigger recommendation now that moves are available (handles turn-1 case
        # where set_party_data ran before enemy moves were populated).
        threading.Thread(
            target=self._compute_recommendation, args=(slot,), daemon=True
        ).start()

    def set_player_battle_stats(self, def_: int, spd: int, max_hp: int) -> None:
        """Called each snapshot with the active player Pokémon's defensive stats."""
        self._player_battle_stats = {"def": def_, "spd": spd, "max_hp": max_hp}
        for slot in range(NUM_SLOTS):
            self._update_move_damage_labels(slot)

    def receive_enemy_battle_stats(self, slot: int, atk: int, spa: int,
                                    level: int, types: list, status,
                                    stat_total: int | None = None) -> None:
        """Called each snapshot with the enemy's offensive stats."""
        new = {"atk": atk, "spa": spa, "level": level, "types": types,
               "burned": status == 6}
        if new != self._enemy_battle_stats[slot]:
            self._enemy_battle_stats[slot] = new
            self._update_move_damage_labels(slot)
        self._enemy_stat_totals[slot] = stat_total

    def _update_move_damage_labels(self, slot: int) -> None:
        moves    = self._enemy_moves[slot]
        dmg_lbls = self._moves_dmg_lbls[slot]
        if not moves or not dmg_lbls:
            return
        es = self._enemy_battle_stats[slot]
        ps = self._player_battle_stats
        if not es or not ps:
            return
        atk     = es.get("atk") or 0
        spa     = es.get("spa") or 0
        level   = es.get("level") or 1
        etypes  = es.get("types") or []
        burned  = es.get("burned", False)
        def_    = ps.get("def") or 0
        spd     = ps.get("spd") or 0
        max_hp  = ps.get("max_hp") or 0
        if not (atk and level and def_ and max_hp):
            return
        ptypes = self._player_active_types
        pw = calculate_weaknesses(ptypes) if ptypes else {}
        for move, lbl in zip(moves, dmg_lbls):
            if move.category == "status" or not move.power:
                lbl.setText("—")
                lbl.setStyleSheet("color:#6c7086; font-size:10px; background:transparent;")
                continue
            is_phys = move.category == "physical"
            off = atk if is_phys else (spa or atk)
            def_stat = def_ if is_phys else (spd or def_)
            is_stab = move.type in etypes
            eff = pw.get(move.type, 1.0)
            result = damage_range(level, move.power, off, def_stat, eff, is_stab, burned, is_phys)
            if result is None:
                lbl.setText("")
                continue
            lo, hi = result
            if max_hp:
                plo = round(lo / max_hp * 100)
                phi = round(hi / max_hp * 100)
                pct_str = f"{plo}%" if plo == phi else f"{plo}-{phi}%"
                text = f"{lo}-{hi} ({pct_str})"
            else:
                text = f"{lo}-{hi}"
            color = dmg_pct_color(hi / max_hp * 100 if max_hp else 0)
            lbl.setText(text)
            lbl.setStyleSheet(f"color:{color}; font-size:10px; background:transparent;")

    def receive_enemy_perm_stats(self, slot: int, stats: dict) -> None:
        """Permanent (getStat) atk/spa for the enemy — used in TI delta calculation."""
        self._enemy_perm_stats[slot] = stats

    def set_party_data(self, party: list[dict]) -> None:
        """Called each snapshot with the live party. Refreshes catch recommendations."""
        self._party_names         = [m.get('name') or '' for m in party]
        self._party_current_moves = [m.get('moves') or [] for m in party]
        self._team_type_groups    = [m.get('types') or [] for m in party if m.get('types')]
        self._party_snapshot      = list(party)
        self._party_stat_totals   = [
            sum(s for s in (m.get('stats') or {}).values() if s is not None) or None
            for m in party
        ]
        for slot in range(NUM_SLOTS):
            if self._enemy_moves[slot]:
                threading.Thread(
                    target=self._compute_recommendation, args=(slot,), daemon=True
                ).start()

    def _build_team_coverage(self, exclude_slot: int | None) -> dict:
        """Per-pairing best SE damage for the current party, optionally skipping one slot."""
        best: dict = {}
        if not impact_db._EFF:
            return best
        for i, member in enumerate(self._party_snapshot):
            if exclude_slot is not None and i == exclude_slot:
                continue
            if member.get("fainted"):
                continue
            live = member.get("stats") or {}
            atk  = live.get("atk") or 0
            spa  = live.get("spa") or 0
            name = (member.get("name") or "").lower()
            entry     = impact_db.get(name)
            speed_pct = entry.get("speed_pct", 50) if entry else 50
            factor    = 1.0 if speed_pct >= 70 else 0.4 + 0.6 * (speed_pct / 70)
            types     = member.get("types") or []
            moves_raw = (self._party_current_moves[i]
                         if i < len(self._party_current_moves) else [])
            for mn in moves_raw:
                try:
                    md = fetch_move(mn.lower().replace(" ", "-"))
                    if not md or not md.power or md.category == "status":
                        continue
                    if md.name.lower() in impact_db._EXCLUDED_MOVES:
                        continue
                    stat = atk if md.category == "physical" else spa
                    stab = 1.5 if md.type in types else 1.0
                    acc  = (md.accuracy or 100) / 100.0
                    base = stat * md.power * acc * stab * factor
                    for pairing in impact_db.ALL_PAIRINGS:
                        se = impact_db._EFF.get((md.type, pairing), 0.0)
                        if se > 1.0:
                            val = base * se
                            if val > best.get(pairing, 0.0):
                                best[pairing] = val
                except Exception:
                    pass
        return best

    def _ti_delta_for_swap(self, enemy_slot: int, party_slot: int,
                            wild_entry: dict | None) -> float:
        """TI change if party_slot is replaced by the enemy at enemy_slot."""
        if not self._party_snapshot or not impact_db._EFF:
            return 0.0
        without = self._build_team_coverage(exclude_slot=party_slot)
        current = self._build_team_coverage(exclude_slot=None)
        current_ti = sum(current.values())

        perm      = self._enemy_perm_stats[enemy_slot]
        cand_atk  = perm.get("atk") or 0
        cand_spa  = perm.get("spa") or 0
        speed_pct = wild_entry.get("speed_pct", 50) if wild_entry else 50
        factor    = 1.0 if speed_pct >= 70 else 0.4 + 0.6 * (speed_pct / 70)
        slot_data = self._slot_data[enemy_slot]
        cand_types = slot_data[0].types if slot_data else []

        new_best = dict(without)
        for md in self._enemy_moves[enemy_slot]:
            if not md or not md.power or md.category == "status":
                continue
            if md.name.lower() in impact_db._EXCLUDED_MOVES:
                continue
            stat = cand_atk if md.category == "physical" else cand_spa
            stab = 1.5 if md.type in cand_types else 1.0
            acc  = (md.accuracy or 100) / 100.0
            base = stat * md.power * acc * stab * factor
            for pairing in impact_db.ALL_PAIRINGS:
                se = impact_db._EFF.get((md.type, pairing), 0.0)
                if se > 1.0:
                    val = base * se
                    if val > new_best.get(pairing, 0.0):
                        new_best[pairing] = val
        return sum(new_best.values()) - current_ti

    def _ti_contributing_moves(self, enemy_slot: int,
                                exclude_party_slot: int | None,
                                wild_entry: dict | None) -> set[str]:
        """Move names from wild_entry that add SE coverage the current team lacks."""
        if not wild_entry or not impact_db._EFF:
            return set()
        team_pv = self._build_team_coverage(exclude_slot=exclude_party_slot)
        covered = {p for p, v in team_pv.items() if v > 0}
        result: set[str] = set()
        for m in wild_entry.get("moves", []):
            mtype = m.get("type", "")
            for pairing in impact_db.ALL_PAIRINGS:
                se = impact_db._EFF.get((mtype, pairing), 0.0)
                if se > 1.0 and pairing not in covered:
                    result.add(m["name"])
                    break
        return result

    def _compute_recommendation(self, slot: int) -> None:
        """Background thread: analyse enemy vs party, emit rec_ready."""
        if self._battle_type != 0:
            self._signals.rec_ready.emit((slot, None))
            return

        slot_data = self._slot_data[slot]
        if slot_data is None:
            self._signals.rec_ready.emit((slot, None))
            return
        pokemon, _ = slot_data

        enemy_bst_pct = self._slot_bst_pcts[slot]
        if enemy_bst_pct is None:
            self._signals.rec_ready.emit((slot, None))
            return

        enemy_name = pokemon.name.lower()
        enemy_bst  = sum(pokemon.stats.values())

        # ── Resolve final evolution ───────────────────────────────────────
        evo_name = enemy_name
        evo_bst  = enemy_bst
        if stats_db.is_ready() and not stats_db.is_fully_evolved(enemy_name):
            try:
                for fname in fetch_final_evolutions(enemy_name):
                    if fname == enemy_name:
                        continue
                    try:
                        fbst = sum(fetch_pokemon(fname).stats.values())
                        if fbst > evo_bst:
                            evo_bst = fbst
                            evo_name = fname
                    except Exception:
                        pass
            except Exception:
                pass

        # ── Impact DB lookup ──────────────────────────────────────────────
        wild_entry = impact_db.get(evo_name) or impact_db.get(enemy_name)
        wild_ai    = wild_entry["percentile"] if wild_entry else None
        wild_moves = wild_entry.get("moves", []) if wild_entry else []
        cand_name  = evo_name if (wild_entry and impact_db.get(evo_name)) else enemy_name

        party_size = len([n for n in self._party_names if n])
        team_full  = party_size >= 6
        team_names = [n for n in self._party_names if n]

        # ── Party not full ────────────────────────────────────────────────
        if not team_full:
            if wild_ai is not None and wild_ai >= 75:
                contributing = self._ti_contributing_moves(slot, None, wild_entry)
                # Coverage gain from adding this candidate (no replacement).
                cov_before = impact_db.team_coverage(team_names)
                cov_after  = impact_db.team_coverage(team_names + [cand_name])
                delta_checks   = cov_after["checks"]   - cov_before["checks"]
                delta_counters = cov_after["counters"] - cov_before["counters"]
                self._signals.rec_ready.emit((slot, {
                    'kind':            'catch',
                    'name':            cand_name.replace('-', ' ').title(),
                    'candidate_ai':    wild_ai,
                    'moves':           wild_moves,
                    'ti_contributing': contributing,
                    'delta_checks':    delta_checks,
                    'delta_counters':  delta_counters,
                }))
            else:
                self._signals.rec_ready.emit((slot, None))
            return

        # ── Full team: best swap by coverage delta ─────────────────────────
        if not impact_db.is_ready() or not team_names:
            self._signals.rec_ready.emit((slot, None))
            return

        swap = impact_db.best_coverage_swap(team_names, cand_name)

        if swap is not None:
            contributing = self._ti_contributing_moves(slot, swap['slot'], wild_entry)
            replaced_name = swap['replaced_name']
            replaced_entry = impact_db.get(impact_db._final_evo_for(replaced_name.lower()))
            replaced_ai = replaced_entry['percentile'] if replaced_entry else 0
            self._signals.rec_ready.emit((slot, {
                'kind':             'replace',
                'name':             replaced_name.replace('-', ' ').title(),
                'candidate_ai':     wild_ai or 0,
                'replaced_ai':      replaced_ai,
                'delta_checks':     swap['delta_checks'],
                'delta_counters':   swap['delta_counters'],
                'newly_covered':    swap['newly_covered'][:5],
                'newly_lost':       swap['newly_lost'][:5],
                'newly_countered':  swap['newly_countered'][:5],
                'lost_counters':    swap['lost_counters'][:5],
                'moves':            wild_moves,
                'ti_contributing':  contributing,
            }))
            return

        # ── No positive coverage swap — context for the player ─────────────
        if wild_ai is None:
            self._signals.rec_ready.emit((slot, None))
            return

        # Best swap allowing negative deltas — show the player what would happen.
        consider_swap = impact_db.best_coverage_swap(
            team_names, cand_name, require_positive=False
        )
        consider_delta_checks   = consider_swap['delta_checks']   if consider_swap else 0
        consider_delta_counters = consider_swap['delta_counters'] if consider_swap else 0

        team_ais: list[tuple[str, int]] = []
        for n in self._party_names:
            if not n:
                continue
            e = impact_db.get(n) or impact_db.get(impact_db._final_evo_for(n))
            if e:
                team_ais.append((n.replace('-', ' ').title(), e['percentile']))

        weakest = min(team_ais, key=lambda x: x[1]) if team_ais else None

        if weakest and wild_ai > weakest[1]:
            self._signals.rec_ready.emit((slot, {
                'kind':            'consider',
                'name':            cand_name.replace('-', ' ').title(),
                'candidate_ai':    wild_ai,
                'weakest_name':    weakest[0],
                'weakest_ai':      weakest[1],
                'moves':           wild_moves,
                'delta_checks':    consider_delta_checks,
                'delta_counters':  consider_delta_counters,
            }))
        elif wild_ai > 0:
            self._signals.rec_ready.emit((slot, {
                'kind':         'skip',
                'name':         cand_name.replace('-', ' ').title(),
                'candidate_ai': wild_ai,
            }))
        else:
            self._signals.rec_ready.emit((slot, None))

    def _on_rec_ready(self, payload) -> None:
        slot, rec = payload
        lbl    = self._rec_lbls[slot]
        effect = self._rec_effects[slot]
        pulse  = self._rec_anims[slot]
        if lbl is None:
            return

        if rec is None:
            if pulse: pulse.stop()
            if effect: effect.setOpacity(1.0)
            lbl.setStyleSheet("")
            lbl.setVisible(False)
            return

        kind = rec['kind']
        contributing = rec.get('ti_contributing') or set()

        if kind == 'catch':
            ai        = rec['candidate_ai']
            d_checks   = rec.get('delta_checks', 0)
            d_counters = rec.get('delta_counters', 0)
            move_line = _moves_html(rec.get('moves', []), contributing)
            cov_line = (
                f"<span style='font-size:14px; color:#cba6f7'>+{d_checks} checks</span>"
                f"&nbsp;&nbsp;<span style='font-size:14px; color:#a6e3a1'>+{d_counters} counters</span><br>"
            ) if (d_checks or d_counters) else ""
            html = (
                f"<span style='font-size:22px; font-weight:bold; color:#89dceb'>"
                f"Catch {rec['name']}!</span>"
                f"<span style='font-size:14px; color:#89dceb'>&nbsp;ai{ai}</span><br>"
                f"{cov_line}"
                f"{move_line}"
            )
            lbl.setStyleSheet(
                "QLabel { background:#071825; border-left:4px solid #89dceb;"
                " border-radius:4px; padding:8px 10px; margin-top:2px; }"
            )
            lbl.setText(html)
            lbl.setVisible(True)
            if pulse: pulse.start()

        elif kind == 'replace':
            old_ai      = rec.get('replaced_ai', 0)
            new_ai      = rec.get('candidate_ai', 0)
            d_checks    = rec.get('delta_checks', 0)
            d_counters  = rec.get('delta_counters', 0)
            newly_cov   = rec.get('newly_covered', []) or []
            newly_lost  = rec.get('newly_lost', []) or []
            positive    = d_checks > 0 or (d_checks == 0 and d_counters > 0)
            border_col  = "#52f07a" if positive else "#a6e3a1"
            bg_col      = "#0c1c10" if positive else "#0a1810"
            move_line   = _moves_html(rec.get('moves', []), contributing)
            sign_ch = "+" if d_checks   >= 0 else "-"
            sign_co = "+" if d_counters >= 0 else "-"
            color_ch = "#a6e3a1" if d_checks   >= 0 else "#f38ba8"
            color_co = "#a6e3a1" if d_counters >= 0 else "#f38ba8"
            fills = ", ".join(n.replace('-', ' ').title() for n in newly_cov[:3])
            loses = ", ".join(n.replace('-', ' ').title() for n in newly_lost[:3])
            fills_line = (
                f"<span style='font-size:12px; color:#a6adc8'>Fills: {fills}"
                f"{'…' if len(newly_cov) > 3 else ''}</span><br>"
            ) if fills else ""
            loses_line = (
                f"<span style='font-size:12px; color:#f38ba8'>Loses: {loses}"
                f"{'…' if len(newly_lost) > 3 else ''}</span><br>"
            ) if loses else ""
            html = (
                f"<span style='font-size:20px; font-weight:bold; color:#a6e3a1'>"
                f"Replace {rec['name']}</span><br>"
                f"<span style='font-size:18px; color:{color_ch}'>{sign_ch}{abs(d_checks)} checks</span>"
                f"&nbsp;&nbsp;<span style='font-size:15px; color:{color_co}'>"
                f"{sign_co}{abs(d_counters)} counters</span><br>"
                f"{fills_line}{loses_line}"
                f"<span style='font-size:13px; color:#6c7086'>"
                f"ai{old_ai} → ai{new_ai}</span><br>"
                f"<br>{move_line}"
            )
            lbl.setStyleSheet(
                f"QLabel {{ background:{bg_col}; border-left:4px solid {border_col};"
                f" border-radius:4px; padding:8px 10px; margin-top:2px; }}"
            )
            lbl.setText(html)
            lbl.setVisible(True)
            if pulse: pulse.start()

        elif kind == 'consider':
            cand_ai     = rec['candidate_ai']
            weak_name   = rec['weakest_name']
            weak_ai     = rec['weakest_ai']
            d_checks    = rec.get('delta_checks', 0)
            d_counters  = rec.get('delta_counters', 0)
            sign_ch = "+" if d_checks   >= 0 else "-"
            sign_co = "+" if d_counters >= 0 else "-"
            color_ch = "#a6e3a1" if d_checks   >= 0 else "#f38ba8"
            color_co = "#a6e3a1" if d_counters >= 0 else "#f38ba8"
            move_line = _moves_html(rec.get('moves', []))
            html = (
                f"<span style='font-size:20px; font-weight:bold; color:#f9e2af'>"
                f"Consider {rec['name']}</span>"
                f"<span style='font-size:13px; color:#f9e2af'>&nbsp;ai{cand_ai}</span><br>"
                f"<span style='font-size:13px; color:#a09070'>"
                f"Beats {weak_name} (ai{weak_ai})</span><br>"
                f"<span style='font-size:15px; color:{color_ch}'>{sign_ch}{abs(d_checks)} checks</span>"
                f"&nbsp;&nbsp;<span style='font-size:15px; color:{color_co}'>"
                f"{sign_co}{abs(d_counters)} counters</span><br>"
                f"<br>{move_line}"
            )
            lbl.setStyleSheet(
                "QLabel { background:#1a1808; border-left:3px solid #f9e2af;"
                " border-radius:4px; padding:8px 10px; margin-top:2px; }"
            )
            lbl.setText(html)
            lbl.setVisible(True)
            if pulse: pulse.stop()

        else:  # skip
            cand_ai = rec.get('candidate_ai', 0)
            html = (
                f"<span style='font-size:16px; color:#45475a'>"
                f"{rec['name']} — ai{cand_ai} — team already stronger</span>"
            )
            lbl.setStyleSheet(
                "QLabel { background:#13131f; border-left:2px solid #313244;"
                " border-radius:4px; padding:6px 10px; margin-top:2px; }"
            )
            lbl.setText(html)
            lbl.setVisible(True)
            if pulse: pulse.stop()

    def set_battle_context(self, player_types: list, battle_type: int) -> None:
        """Called each snapshot tick with the primary player's live types and battle type."""
        new_types = list(player_types) if player_types else []
        prev_battle_type = self._battle_type
        changed = new_types != self._player_active_types or battle_type != self._battle_type
        self._player_active_types = new_types
        self._battle_type = battle_type or 0
        if changed:
            for slot in range(NUM_SLOTS):
                self._run_prediction(slot)
        # When battle type changes, flip move containers and rec labels together
        if self._battle_type != prev_battle_type:
            is_trainer = self._battle_type != 0
            for slot in range(NUM_SLOTS):
                c = self._moves_containers[slot]
                if c is not None:
                    c.setVisible(is_trainer and bool(self._enemy_moves[slot]))
                lbl = self._rec_lbls[slot]
                anim = self._rec_anims[slot]
                if is_trainer and lbl is not None:
                    if anim: anim.stop()
                    if self._rec_effects[slot]: self._rec_effects[slot].setOpacity(1.0)
                    lbl.setVisible(False)

    def _run_prediction(self, slot: int):
        moves = self._enemy_moves[slot]
        cells = self._moves_cells[slot]
        swap_lbl = self._swap_lbls[slot]
        if not moves or not cells:
            if swap_lbl:
                swap_lbl.setVisible(False)
            return

        player_types = self._player_active_types

        # Score each move: type_effectiveness × power.
        # Status moves score 0 so a damaging move always wins when available.
        if player_types:
            player_weaknesses = calculate_weaknesses(player_types)
            scores = [
                (player_weaknesses.get(m.type, 1.0) * (m.power or 0))
                if m.category != "status" else 0.0
                for m in moves
            ]
            # If the enemy only has status moves, score by raw type effectiveness instead.
            if max(scores) == 0:
                scores = [player_weaknesses.get(m.type, 1.0) for m in moves]
        else:
            scores = [(m.power or 0) if m.category != "status" else 0 for m in moves]

        best_idx = scores.index(max(scores))

        # Highlight cells — use #movecell so the border only targets the outer frame,
        # not any child QFrames inside the cell.
        for i, cell in enumerate(cells):
            if i == best_idx:
                cell.setStyleSheet(
                    "QFrame#movecell { background: #2a1515; border-radius: 3px;"
                    " border: 2px solid #f38ba8; }"
                )
            else:
                cell.setStyleSheet("QFrame#movecell { background: #11111b; border-radius: 3px; }")

        # Swap likelihood — only meaningful in trainer battles (battleType == 1)
        if swap_lbl is None:
            return
        if self._battle_type != 1:
            swap_lbl.setVisible(False)
            return
        slot_data = self._slot_data[slot]
        if slot_data is None:
            swap_lbl.setVisible(False)
            return

        _, enemy_weaknesses = slot_data
        if player_types:
            # How hard does the player's typing hit the enemy?
            player_threat = max(
                (enemy_weaknesses.get(pt, 1.0) for pt in player_types),
                default=1.0,
            )
        else:
            player_threat = 1.0

        if player_threat >= 4.0:
            pct, color = 65, "#f38ba8"
        elif player_threat >= 2.0:
            pct, color = 40, "#fab387"
        elif player_threat <= 0.5:
            pct, color = 5,  "#a6e3a1"
        else:
            pct, color = 15, "#6c7086"

        swap_lbl.setText(f"⟲ Swap: ~{pct}%")
        swap_lbl.setStyleSheet(f"color:{color}; font-size:13px;")
        swap_lbl.setVisible(True)

    def _make_move_cell(self, move: MoveData) -> tuple:
        cell = QFrame()
        cell.setObjectName("movecell")
        cell.setStyleSheet("QFrame#movecell { background: #11111b; border-radius: 3px; }")
        vbox = QVBoxLayout(cell)
        vbox.setContentsMargins(3, 3, 3, 3)
        vbox.setSpacing(2)

        # Line 1: type badge
        bg, fg = TYPE_COLORS.get(move.type, ("#888", "#fff"))
        badge = QLabel(move.type[:4].upper())
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedHeight(15)
        badge.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:2px;"
            f"font-size:10px; font-weight:bold;"
        )
        vbox.addWidget(badge)

        # Line 2: move name (left) + damage (right)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(2)

        name_lbl = QLabel(move.name.replace("-", " ").title())
        name_lbl.setStyleSheet("color:#cdd6f4; font-size:11px; background:transparent;")
        if move.description:
            name_lbl.setToolTip(move.description)
        bottom.addWidget(name_lbl, 1)

        dmg_lbl = QLabel("")
        dmg_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        dmg_lbl.setStyleSheet("color:#a6adc8; font-size:10px; background:transparent;")
        bottom.addWidget(dmg_lbl)

        vbox.addLayout(bottom)
        return cell, dmg_lbl

    def receive_opponent_hp(self, slot: int, text: str) -> None:
        """Called by JS-state dispatcher with opponent HP text, e.g. '324/369'."""
        cleaned = "".join(c for c in text if c.isdigit() or c == "/")
        lbl = self._hp_lbls[slot]
        if lbl is None:
            return
        if "/" in cleaned:
            lbl.setText(cleaned)
            lbl.setVisible(True)

    # ── lookup ────────────────────────────────────────────────────────────────

    def _set_user_active(self, slot: int):
        self._user_input_active[slot] = True

    def _lookup_from_input(self, slot: int):
        self._user_input_active[slot] = False
        name = self._name_inputs[slot].text().strip().lower()
        if name:
            self._set_status("")
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
                self._signals.status.emit("")
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
        threading.Thread(target=self._compute_recommendation, args=(slot,), daemon=True).start()
        if any(t is not None for t in self._sampled_types[slot]):
            self._apply_type_override(slot)

    def _on_evo(self, payload):
        slot, evos = payload
        self._slot_evos[slot] = evos
        self._display_evos(slot, evos)
        if evos:
            evo_line = "Evolves into " + " / ".join(
                n.replace("-", " ").title() for n, _, _ in evos
            )
            for evo_name, _, _ in sorted(evos, key=lambda x: x[1], reverse=True):
                impact_entry = impact_db.get(evo_name)
                if impact_entry:
                    pct = impact_entry["percentile"]
                    color = _pct_color(pct)
                    self._pct_lbls[slot].setText(f"ai{pct}")
                    self._pct_lbls[slot].setStyleSheet(f"color:{color}; font-size:14px;")
                    self._pct_lbls[slot].setToolTip(_impact_tooltip(impact_entry, evo_line))
                    self._pct_lbls[slot].setVisible(True)
                    self._slot_bst_pcts[slot] = pct
                    threading.Thread(
                        target=self._compute_recommendation, args=(slot,), daemon=True
                    ).start()
                    break

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

        leg_lbl = self._leg_lbls[slot]
        if leg_lbl is not None and stats_db.is_ready():
            name_lower = pokemon.name.lower()
            bst_sum    = sum(pokemon.stats.values())
            if stats_db.is_legendary(name_lower):
                leg_lbl.setText("LEGENDARY")
                leg_lbl.setStyleSheet(
                    "background:#2a1540; color:#cba6f7; border-radius:3px;"
                    " padding:1px 5px; font-size:10px; font-weight:bold;"
                )
                leg_lbl.setVisible(True)
            elif stats_db.is_fully_evolved(name_lower) and bst_sum >= 580:
                leg_lbl.setText("PSEUDO")
                leg_lbl.setStyleSheet(
                    "background:#201510; color:#f9e2af; border-radius:3px;"
                    " padding:1px 5px; font-size:10px; font-weight:bold;"
                )
                leg_lbl.setVisible(True)
            else:
                leg_lbl.setVisible(False)

        self._bst_lbls[slot].setVisible(False)
        impact_entry = impact_db.get(pokemon.name.lower())
        if impact_entry:
            pct = impact_entry["percentile"]
            color = _pct_color(pct)
            self._pct_lbls[slot].setText(f"ai{pct}")
            self._pct_lbls[slot].setStyleSheet(f"color:{color}; font-size:14px;")
            self._pct_lbls[slot].setToolTip(_impact_tooltip(impact_entry))
            self._pct_lbls[slot].setVisible(True)
            self._slot_bst_pcts[slot] = pct
        else:
            # Not in impact_db yet (pre-evo) — wait for _on_evo() to resolve it.
            self._pct_lbls[slot].setVisible(False)
            self._pct_lbls[slot].setToolTip("")
            self._slot_bst_pcts[slot] = None

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

        slot_data = self._slot_data[slot]
        opponent_types = slot_data[0].types if slot_data else []
        name_to_types = {
            s["name"].lower(): s.get("types", [])
            for s in team if s and isinstance(s, dict) and s.get("name")
        }

        for i, (pname, move_name, move_type, eff) in enumerate(picks):
            is_active = bool(self._active_pokemon and pname.lower() == self._active_pokemon)

            friend_types = name_to_types.get(pname.lower(), [])
            takes_se = False
            if friend_types and opponent_types:
                friend_weaknesses = calculate_weaknesses(friend_types)
                takes_se = any(friend_weaknesses.get(t, 1.0) >= 2.0 for t in opponent_types)

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
            name_lbl.setMinimumWidth(0)
            row.addWidget(name_lbl)

            if takes_se:
                warn_lbl = QLabel("*")
                warn_lbl.setStyleSheet("color:#f38ba8; font-size:14px; background: transparent;")
                warn_lbl.setToolTip("Takes SE damage from opponent's type(s)")
                row.addWidget(warn_lbl)

            bg, fg = TYPE_COLORS.get(move_type, ("#888", "#fff"))
            badge = QLabel(move_type[:4].upper())
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(44, 20)
            badge.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:3px; font-size:15px; font-weight:bold;"
            )
            row.addWidget(badge)

            move_lbl = QLabel(move_name)
            move_lbl.setStyleSheet("color:#6c7086; font-size:15px; background: transparent;")
            move_lbl.setMinimumWidth(0)
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

    # ── Type override (from JS-state dispatcher) ─────────────────────────────

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
