import difflib
import math
import threading
import window_state
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QFrame, QScrollArea, QSizePolicy,
    QProgressBar, QStyleFactory,
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPointF
from PyQt6.QtGui import QPixmap, QPainter, QBrush, QColor, QPen, QPolygonF

from pokemon_api import fetch_pokemon, fetch_move, MoveData, PokemonData
from weakness_calc import (
    calculate_weaknesses, detailed_coverage,
    coverage_suggestions, redundancy_suggestions, dangerous_combos, slot_coverage, ALL_TYPES,
)
from overlay import _resolve_name, _FORM_VARIANTS, _SLUG_TO_BASE
import stats_db
import moves_db

TEAM_SIZE  = 6
MOVE_SLOTS = 4

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

_HP_BAR_STYLE = (
    "QProgressBar{{border:1px solid #45475a;border-radius:3px;"
    "background:#313244;color:#cdd6f4;font-size:8px;}}"
    "QProgressBar::chunk{{background:{color};border-radius:2px;}}"
)

_CAT_ICON_CACHE: dict = {}

def _cat_icon(category: str, size: int = 16) -> QPixmap:
    key = (category, size)
    if key in _CAT_ICON_CACHE:
        return _CAT_ICON_CACHE[key]
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = size / 2
    if category == "physical":
        p.setBrush(QBrush(QColor("#C62828")))
        p.setPen(Qt.PenStyle.NoPen)
        r_out, r_in = c * 0.92, c * 0.38
        pts = [
            QPointF(
                c + (r_out if i % 2 == 0 else r_in) * math.cos(i * math.pi / 8),
                c + (r_out if i % 2 == 0 else r_in) * math.sin(i * math.pi / 8),
            )
            for i in range(16)
        ]
        p.drawPolygon(QPolygonF(pts))
    elif category == "special":
        p.setPen(Qt.PenStyle.NoPen)
        for radius, color in [
            (c * 0.90, "#1565C0"),
            (c * 0.58, "#90CAF9"),
            (c * 0.26, "#1565C0"),
        ]:
            p.setBrush(QBrush(QColor(color)))
            p.drawEllipse(QPointF(c, c), radius, radius)
    else:
        pen = QPen(QColor("#9E9E9E"), max(1.5, size * 0.14))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.GlobalColor.transparent)
        margin = size * 0.18
        p.drawEllipse(QPointF(c, c), c - margin, c - margin)
    p.end()
    _CAT_ICON_CACHE[key] = pm
    return pm


class _Signals(QObject):
    result_ready   = pyqtSignal(object)  # (slot, PokemonData, weaknesses)
    move_ready     = pyqtSignal(object)  # (slot, mi, MoveData)
    analysis_ready = pyqtSignal(object)  # (full, partial, gaps, suggestions, danger, slot_stats, weakest_slot, replace_sugg, pref_type)
    error          = pyqtSignal(object)


class TeamPanel(QWidget):
    def __init__(self, embedded: bool = False, strip: bool = False):
        super().__init__()
        self._strip           = strip
        self._embedded        = embedded or strip  # strip implies embedded
        self._team_data       = [None] * TEAM_SIZE
        self._team_weaknesses = [None] * TEAM_SIZE
        self._team_moves      = [[None] * MOVE_SLOTS for _ in range(TEAM_SIZE)]
        self._drag_pos        = None

        self._name_inputs    = [None] * TEAM_SIZE
        self._type_rows      = [None] * TEAM_SIZE
        self._stats_lbls     = [None] * TEAM_SIZE
        self._level_lbls     = [None] * TEAM_SIZE
        self._hp_bars        = [None] * TEAM_SIZE
        self._move_widgets   = [[None] * MOVE_SLOTS for _ in range(TEAM_SIZE)]
        self._slot_frames    = [None] * TEAM_SIZE
        self._analysis_panel = None
        self._active_slot_idx: int | None  = None
        self._active_slot_idx2: int | None = None
        self._pending_active  = ""           # last name from active OCR, for matching after auto-add
        self._pending_lookups: set = set()   # slots currently being auto-fetched
        self._last_hp_cur: int | None = None
        self._last_hp_max: int | None = None
        self._player_sampled_types   = [None, None]
        self._player_sampled_types2  = [None, None]
        self._player_form_pending    = False
        self._player_form_pending2   = False
        # Per-slot, per-move debounce: (last_text, consecutive_count)
        self._move_ocr_buf   = [[("", 0)] * MOVE_SLOTS for _ in range(TEAM_SIZE)]

        self._signals = _Signals()
        self._signals.result_ready.connect(self._on_result)
        self._signals.move_ready.connect(self._on_move_ready)
        self._signals.analysis_ready.connect(self._on_analysis_ready)
        self._signals.error.connect(lambda p: self._pending_lookups.discard(p[0]))

        if not self._embedded:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedSize(490, 760)
        else:
            self.setMinimumWidth(0)

        QApplication.instance().setStyleSheet(
            "QToolTip { background:#313244; color:#cdd6f4; border:1px solid #45475a;"
            " font-size:12px; padding:4px; border-radius:4px; }"
        )

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        if self._strip:
            self._build_strip_ui()
            return

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

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
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)

        title_row = QHBoxLayout()
        title_lbl = QLabel("My Team")
        title_lbl.setStyleSheet("color:#cdd6f4; font-size:15px; font-weight:bold;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        if not self._embedded:
            close_btn = QPushButton("✕")
            close_btn.setFixedSize(20, 20)
            close_btn.setStyleSheet(
                "QPushButton{background:transparent;color:#6c7086;border:none;font-size:12px;}"
                "QPushButton:hover{color:#f38ba8;}"
            )
            close_btn.clicked.connect(self.hide)
            title_row.addWidget(close_btn)
        inner.addLayout(title_row)
        inner.addWidget(self._divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
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

        scroll_widget = QWidget()
        scroll_widget.setMinimumWidth(0)
        scroll_widget.setStyleSheet("background: transparent;")
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 2, 0)
        scroll_layout.setSpacing(6)

        slots_col = QVBoxLayout()
        slots_col.setSpacing(6)
        for slot in range(TEAM_SIZE):
            slots_col.addWidget(self._build_slot_frame(slot))
        scroll_layout.addLayout(slots_col)

        if not self._embedded:
            # Analysis sections inline (standalone window only)
            scroll_layout.addWidget(self._divider())

            self._weak_link_area, wl_frame   = self._make_section_cell("WEAKEST LINK")
            self._tips_area,      tips_frame = self._make_section_cell("COVERAGE TIPS")
            self._pref_type_area, pt_frame   = self._make_section_cell("PREFERRED TYPING")
            self._danger_area,    dan_frame  = self._make_section_cell("DANGER COMBOS")

            analysis_grid = QGridLayout()
            analysis_grid.setSpacing(6)
            analysis_grid.setColumnStretch(0, 1)
            analysis_grid.setColumnStretch(1, 1)
            analysis_grid.addWidget(wl_frame,   0, 0)
            analysis_grid.addWidget(tips_frame, 0, 1)
            analysis_grid.addWidget(pt_frame,   1, 0)
            analysis_grid.addWidget(dan_frame,  1, 1)
            scroll_layout.addLayout(analysis_grid)

            scroll_layout.addWidget(self._divider())
            scroll_layout.addWidget(self._section_hdr("TEAM WEAKNESSES"))
            self._weak_area = QVBoxLayout()
            self._weak_area.setSpacing(3)
            scroll_layout.addLayout(self._weak_area)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        inner.addWidget(scroll, 1)

        if not self._embedded:
            self._rebuild_weakness_grid()
            self._rebuild_analysis_display(None, None, None, None, None)
            self._rebuild_weakest_link([None] * TEAM_SIZE, None, None)
            self._rebuild_preferred_typing(None)

    def _build_strip_ui(self):
        self.setStyleSheet("background: #181825;")
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(3)

        hdr = QLabel("My Team")
        hdr.setStyleSheet("color:#cdd6f4; font-size:13px; font-weight:bold;")
        root.addWidget(hdr)

        slots_row = QHBoxLayout()
        slots_row.setContentsMargins(0, 0, 0, 0)
        slots_row.setSpacing(4)
        for slot in range(TEAM_SIZE):
            frame = self._build_slot_frame(slot)
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            slots_row.addWidget(frame, 1)
        root.addLayout(slots_row)

    def set_active_slot(self, name: str, position: int = 0) -> None:
        name = name.strip().lower()
        if position == 0:
            self._pending_active = name
            self._active_slot_idx = None
            self._last_hp_cur = None
            self._last_hp_max = None
            self._player_sampled_types = [None, None]
            self._player_form_pending  = False
            for slot in range(TEAM_SIZE):
                pokemon = self._team_data[slot]
                if pokemon and name and pokemon.name.lower() == name:
                    self._active_slot_idx = slot
            if name and self._active_slot_idx is None:
                for slot in range(TEAM_SIZE):
                    if self._team_data[slot] is None and slot not in self._pending_lookups:
                        self._pending_lookups.add(slot)
                        threading.Thread(
                            target=self._lookup, args=(slot, name), daemon=True
                        ).start()
                        break
        else:
            self._active_slot_idx2 = None
            self._player_sampled_types2 = [None, None]
            self._player_form_pending2  = False
            for slot in range(TEAM_SIZE):
                pokemon = self._team_data[slot]
                if pokemon and name and pokemon.name.lower() == name:
                    self._active_slot_idx2 = slot
        self._refresh_slot_highlights()

    def _refresh_slot_highlights(self):
        for slot in range(TEAM_SIZE):
            frame = self._slot_frames[slot]
            if frame is None:
                continue
            active = slot == self._active_slot_idx or slot == self._active_slot_idx2
            frame.setStyleSheet(
                f"QFrame#slot_{slot} {{ background: #181825; border-radius: 6px; border: 2px solid #a6e3a1; }}"
                if active else
                f"QFrame#slot_{slot} {{ background: #181825; border-radius: 6px; }}"
            )

    def receive_player_level(self, text: str, position: int = 0) -> None:
        """Called by OCRService on main thread with digit text for the active player Pokémon level."""
        digits = "".join(c for c in text if c.isdigit())
        slot = self._active_slot_idx if position == 0 else self._active_slot_idx2
        if not digits or slot is None:
            return
        lbl = self._level_lbls[slot]
        if lbl is not None:
            lbl.setText(f"L{digits}")
            lbl.setVisible(True)

    def receive_player_hp_cur(self, text: str) -> None:
        digits = "".join(c for c in text if c.isdigit())
        if not digits:
            return
        try:
            self._last_hp_cur = int(digits)
        except ValueError:
            return
        self._update_hp_bar()

    def receive_player_hp_max(self, text: str) -> None:
        digits = "".join(c for c in text if c.isdigit())
        if not digits:
            return
        try:
            self._last_hp_max = int(digits)
        except ValueError:
            return
        self._update_hp_bar()

    def _update_hp_bar(self) -> None:
        pass  # HP tracking disabled

    def receive_move_ocr(self, move_idx: int, text: str) -> None:
        """Called by OCRService on main thread with a detected move name."""
        if self._active_slot_idx is None:
            return
        cleaned = " ".join(text.strip().split())
        cleaned = "".join(c for c in cleaned if c.isalpha() or c == " ").strip()
        if len(cleaned) < 3 or len(cleaned) > 22:
            return
        # Debounce: only commit after 2 consecutive identical readings
        slot = self._active_slot_idx
        last_text, count = self._move_ocr_buf[slot][move_idx]
        if cleaned.lower() == last_text.lower():
            count += 1
            self._move_ocr_buf[slot][move_idx] = (cleaned, count)
            if count != 2:
                return
        else:
            self._move_ocr_buf[slot][move_idx] = (cleaned, 1)
            return
        threading.Thread(
            target=self._do_lookup_move, args=(slot, move_idx, cleaned.lower()), daemon=True
        ).start()

    def _build_slot_frame(self, slot: int) -> QFrame:
        frame = QFrame()
        frame.setObjectName(f"slot_{slot}")
        self._slot_frames[slot] = frame
        frame.setStyleSheet(f"QFrame#slot_{slot} {{ background: #181825; border-radius: 6px; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(3)

        num_lbl = QLabel(f"{slot + 1}:")
        num_lbl.setFixedWidth(14)
        num_lbl.setStyleSheet("color:#6c7086; font-size:12px; font-weight:bold;")

        level_lbl = QLabel("")
        level_lbl.setStyleSheet("color:#89b4fa; font-size:14px;")
        level_lbl.setFixedWidth(44)
        level_lbl.setVisible(False)
        self._level_lbls[slot] = level_lbl

        # Type badges live inline — hidden until a pokemon is loaded
        type_container = QWidget()
        type_container.setStyleSheet("background: transparent;")
        type_container.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        type_layout = QHBoxLayout(type_container)
        type_layout.setContentsMargins(0, 0, 0, 0)
        type_layout.setSpacing(3)
        self._type_rows[slot] = type_layout

        inp = QLineEdit()
        inp.setPlaceholderText("pokemon…")
        inp.setMinimumWidth(0)
        inp.setStyleSheet(
            "QLineEdit{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
            "border-radius:4px;padding:1px 5px;font-size:16px;}"
            "QLineEdit:focus{border:1px solid #89b4fa;}"
        )
        inp.returnPressed.connect(lambda s=slot: self._lookup_from_input(s))
        self._name_inputs[slot] = inp

        clear_btn = QPushButton("✕")
        clear_btn.setFixedSize(13, 13)
        clear_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#45475a;border:none;font-size:9px;}"
            "QPushButton:hover{color:#f38ba8;}"
        )
        clear_btn.clicked.connect(lambda _, s=slot: self._clear_slot(s))

        header.addWidget(num_lbl)
        header.addWidget(level_lbl)
        header.addWidget(type_container)
        header.addWidget(inp, 1)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        hp_bar = QProgressBar()
        hp_bar.setStyle(QStyleFactory.create("Fusion"))
        hp_bar.setRange(0, 100)
        hp_bar.setValue(0)
        hp_bar.setFormat("HP")
        hp_bar.setFixedHeight(12)
        hp_bar.setTextVisible(True)
        hp_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hp_bar.setStyleSheet(_HP_BAR_STYLE.format(color="#a6e3a1"))
        hp_bar.setVisible(False)
        self._hp_bars[slot] = hp_bar
        layout.addWidget(hp_bar)

        if not self._strip:
            layout.addWidget(self._divider())

        move_grid = QGridLayout()
        move_grid.setSpacing(3)
        move_grid.setContentsMargins(0, 0, 0, 0)
        for mi in range(MOVE_SLOTS):
            r, c = divmod(mi, 2)
            cell, widgets = self._build_move_cell(slot, mi)
            self._move_widgets[slot][mi] = widgets
            move_grid.addWidget(cell, r, c)
        layout.addLayout(move_grid)

        if not self._strip:
            stats_lbl = QLabel("")
            stats_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stats_lbl.setStyleSheet("font-size:12px;")
            stats_lbl.setWordWrap(True)
            stats_lbl.setVisible(False)
            self._stats_lbls[slot] = stats_lbl
            layout.addWidget(stats_lbl)

        return frame

    def _build_move_cell(self, slot: int, mi: int):
        cell = QFrame()
        cell.setStyleSheet("QFrame{background:#11111b; border-radius:3px;}")
        outer = QHBoxLayout(cell)
        outer.setContentsMargins(3, 3, 3, 3)
        outer.setSpacing(4)

        # Left column: type badge stacked over category icon
        left = QVBoxLayout()
        left.setSpacing(2)
        left.setContentsMargins(0, 0, 0, 0)

        badge = QLabel("····")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(32, 16)
        badge.setStyleSheet(
            "background:#313244; color:#45475a; border-radius:2px;"
            "font-size:9px; font-weight:bold;"
        )

        cat_icon_lbl = QLabel()
        cat_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cat_icon_lbl.setFixedSize(32, 22)
        cat_icon_lbl.setStyleSheet("background:#1e1e2e; border-radius:2px;")

        left.addWidget(badge)
        left.addWidget(cat_icon_lbl)

        # Right column: move name over (power, accuracy)
        right = QVBoxLayout()
        right.setSpacing(1)
        right.setContentsMargins(0, 0, 0, 0)

        name_inp = QLineEdit()
        name_inp.setPlaceholderText(f"move {mi + 1}…")
        name_inp.setMinimumWidth(0)
        name_inp.setStyleSheet(
            "QLineEdit{background:transparent;color:#cdd6f4;border:none;"
            "font-size:13px;padding:0;}"
            "QLineEdit:focus{border-bottom:1px solid #89b4fa;}"
        )
        name_inp.returnPressed.connect(lambda s=slot, m=mi: self._lookup_move_from_input(s, m))

        stats = QHBoxLayout()
        stats.setSpacing(6)
        stats.setContentsMargins(0, 0, 0, 0)

        power_lbl = QLabel("—")
        power_lbl.setStyleSheet("color:#45475a; font-size:12px;")

        acc_lbl = QLabel("—")
        acc_lbl.setStyleSheet("color:#45475a; font-size:12px;")

        stats.addWidget(power_lbl)
        stats.addWidget(acc_lbl)
        stats.addStretch()

        right.addWidget(name_inp)
        right.addLayout(stats)

        outer.addLayout(left)
        outer.addLayout(right, 1)

        return cell, (badge, name_inp, power_lbl, acc_lbl, cat_icon_lbl)

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#313244;")
        return line

    def _section_hdr(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#6c7086; font-size:12px; font-weight:bold; letter-spacing:1px;")
        return lbl

    def _make_section_cell(self, title: str) -> tuple:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: #181825; border-radius: 6px; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        layout.addWidget(self._section_hdr(title))
        content = QVBoxLayout()
        content.setSpacing(2)
        layout.addLayout(content)
        layout.addStretch()
        return content, frame

    # ── Pokemon lookup ────────────────────────────────────────────────────────

    def _lookup_from_input(self, slot: int):
        name = self._name_inputs[slot].text().strip().lower()
        if name:
            threading.Thread(target=self._lookup, args=(slot, name), daemon=True).start()

    def _lookup(self, slot: int, name: str):
        try:
            pokemon = None
            try:
                pokemon = fetch_pokemon(name)
            except Exception as exc:
                if "404" not in str(exc):
                    self._signals.error.emit((slot, str(exc)))
                    return

            if pokemon is None:
                names = stats_db.all_names()
                if names:
                    matched = _resolve_name(name, names)
                    if matched:
                        pokemon = fetch_pokemon(matched)

            if pokemon is None:
                self._signals.error.emit((slot, f"Not found: {name}"))
                return

            try:
                weaknesses = calculate_weaknesses(pokemon.types)
            except Exception:
                weaknesses = {}
            self._signals.result_ready.emit((slot, pokemon, weaknesses))
        except Exception as exc:
            self._signals.error.emit((slot, str(exc)))

    # ── move lookup ───────────────────────────────────────────────────────────

    def _lookup_move_from_input(self, slot: int, mi: int):
        name = self._move_widgets[slot][mi][1].text().strip().lower()
        if name:
            threading.Thread(
                target=self._do_lookup_move, args=(slot, mi, name), daemon=True
            ).start()

    def _do_lookup_move(self, slot: int, mi: int, name: str):
        candidates = [name]
        # If OCR omitted spaces (e.g. "stealthrock"), try all single-split positions.
        # fetch_move caches 404s so repeated misses are cheap.
        flat = name.replace(" ", "").replace("-", "")
        if flat == name and len(name) >= 6:
            for i in range(3, len(name) - 2):
                candidates.append(name[:i] + " " + name[i:])
        for candidate in candidates:
            try:
                move = fetch_move(candidate)
                self._signals.move_ready.emit((slot, mi, move))
                return
            except Exception:
                continue

        # Fuzzy fallback: compare normalized OCR text against all known move names.
        # Normalization strips hyphens so "tronhead" matches "ironhead" → "iron-head".
        if moves_db.is_ready():
            known = moves_db.all_names()
            norm_to_api = {n.replace("-", ""): n for n in known}
            matches = difflib.get_close_matches(flat, norm_to_api.keys(), n=1, cutoff=0.75)
            if matches:
                try:
                    move = fetch_move(norm_to_api[matches[0]])
                    self._signals.move_ready.emit((slot, mi, move))
                except Exception:
                    pass

    # ── slot management ───────────────────────────────────────────────────────

    def _clear_slot_data(self, slot: int):
        """Per-slot reset without triggering rebuild/save — use clear_all_slots or
        _clear_slot to batch those side effects."""
        self._team_data[slot]       = None
        self._team_weaknesses[slot] = None
        self._team_moves[slot]      = [None] * MOVE_SLOTS
        self._move_ocr_buf[slot]    = [("", 0)] * MOVE_SLOTS
        self._name_inputs[slot].clear()
        self._clear_type_badges(slot)
        for mi in range(MOVE_SLOTS):
            self._reset_move_row(slot, mi)
        if self._stats_lbls[slot] is not None:
            self._stats_lbls[slot].setVisible(False)
        if self._hp_bars[slot] is not None:
            self._hp_bars[slot].setValue(0)
            self._hp_bars[slot].setVisible(False)

    def _clear_slot(self, slot: int):
        self._clear_slot_data(slot)
        if not self._embedded:
            self._rebuild_weakness_grid()
        self._trigger_analysis_rebuild()
        self._save_team()

    def clear_all_slots(self):
        """Wipe every team slot. Used by New Run."""
        for slot in range(TEAM_SIZE):
            self._clear_slot_data(slot)
        if not self._embedded:
            self._rebuild_weakness_grid()
        self._trigger_analysis_rebuild()
        self._save_team()

    def _clear_type_badges(self, slot: int):
        layout = self._type_rows[slot]
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _reset_move_row(self, slot: int, mi: int):
        badge, name_inp, power_lbl, acc_lbl, cat_icon_lbl = self._move_widgets[slot][mi]
        badge.setText("····")
        badge.setStyleSheet(
            "background:#313244; color:#45475a; border-radius:2px;"
            "font-size:9px; font-weight:bold;"
        )
        badge.setToolTip("")
        name_inp.clear()
        power_lbl.setText("—")
        power_lbl.setStyleSheet("color:#45475a; font-size:12px;")
        acc_lbl.setText("—")
        acc_lbl.setStyleSheet("color:#45475a; font-size:12px;")
        cat_icon_lbl.setPixmap(QPixmap())
        cat_icon_lbl.setStyleSheet("background:#1e1e2e; border-radius:2px;")

    # ── signal handlers ───────────────────────────────────────────────────────

    def _on_result(self, payload):
        slot, pokemon, weaknesses = payload
        self._pending_lookups.discard(slot)
        self._team_data[slot]       = pokemon
        self._team_weaknesses[slot] = weaknesses
        self._name_inputs[slot].setText(pokemon.name)
        self._clear_type_badges(slot)
        for t in pokemon.types:
            self._type_rows[slot].addWidget(self._make_type_badge(t))
        if not self._embedded:
            self._rebuild_weakness_grid()
        # If the newly-added pokemon matches what OCR currently sees, activate immediately
        # without waiting for the next OCR cycle.
        if self._pending_active and pokemon.name.lower() == self._pending_active:
            self._active_slot_idx = slot
            self._refresh_slot_highlights()
        # If color samples are already in for an active slot, check for form mismatch now.
        if slot == self._active_slot_idx and any(t is not None for t in self._player_sampled_types):
            self._apply_player_type_override(0)
        if slot == self._active_slot_idx2 and any(t is not None for t in self._player_sampled_types2):
            self._apply_player_type_override(1)
        self._trigger_analysis_rebuild()
        self._save_team()

    # ── Player type color override (from TypeColorService) ───────────────────

    def receive_player_type_sample(self, type_index: int, type_name: str | None, position: int = 0) -> None:
        if position == 0:
            if self._player_sampled_types[type_index] == type_name:
                return
            self._player_sampled_types[type_index] = type_name
        else:
            if self._player_sampled_types2[type_index] == type_name:
                return
            self._player_sampled_types2[type_index] = type_name
        self._apply_player_type_override(position)

    def _apply_player_type_override(self, position: int = 0):
        if position == 0:
            slot = self._active_slot_idx
            sampled_types = self._player_sampled_types
        else:
            slot = self._active_slot_idx2
            sampled_types = self._player_sampled_types2
        if slot is None:
            return
        pokemon = self._team_data[slot]
        if pokemon is None:
            return
        sampled = list(dict.fromkeys(t for t in sampled_types if t is not None))
        if not sampled:
            return
        if set(sampled) != set(pokemon.types):
            self._try_player_form_from_types(slot, pokemon.name, sampled, position)

    def _try_player_form_from_types(self, slot: int, pokemon_name: str, sampled: list, position: int = 0):
        base = _SLUG_TO_BASE.get(pokemon_name, pokemon_name)
        variants = _FORM_VARIANTS.get(base)
        pending_attr = '_player_form_pending' if position == 0 else '_player_form_pending2'
        if not variants or getattr(self, pending_attr):
            return
        setattr(self, pending_attr, True)

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
                setattr(self, pending_attr, False)

        threading.Thread(target=_check, daemon=True).start()

    def _on_move_ready(self, payload):
        slot, mi, move = payload
        self._team_moves[slot][mi] = move
        self._apply_move_to_row(slot, mi, move)
        self._trigger_analysis_rebuild()
        self._save_team()

    def _apply_move_to_row(self, slot: int, mi: int, move: MoveData):
        badge, name_inp, power_lbl, acc_lbl, cat_icon_lbl = self._move_widgets[slot][mi]
        bg, fg = TYPE_COLORS.get(move.type, ("#888", "#fff"))
        badge.setText(move.type[:4].upper())
        badge.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:2px;"
            f"font-size:9px; font-weight:bold;"
        )
        badge.setToolTip(move.description or "")
        name_inp.setText(move.name)
        if move.power is not None:
            power_lbl.setText(str(move.power))
            power_lbl.setStyleSheet("color:#cdd6f4; font-size:12px;")
        else:
            power_lbl.setText("—")
            power_lbl.setStyleSheet("color:#45475a; font-size:12px;")
        if move.accuracy is not None:
            acc_lbl.setText(f"{move.accuracy}%")
            acc_lbl.setStyleSheet("color:#a6adc8; font-size:12px;")
        else:
            acc_lbl.setText("—%")
            acc_lbl.setStyleSheet("color:#6c7086; font-size:12px;")
        cat_icon_lbl.setPixmap(_cat_icon(move.category, 22))
        cat_icon_lbl.setStyleSheet("background:#1e1e2e; border-radius:2px;")

    def set_analysis_panel(self, panel):
        self._analysis_panel = panel

    def _on_analysis_ready(self, payload):
        (full, partial, gaps, suggestions, danger,
         slot_stats, weakest_slot, replace_sugg, pref_type,
         team_weaknesses, team_names) = payload
        self._update_slot_stats(slot_stats)
        if self._analysis_panel is not None:
            self._analysis_panel.on_analysis_ready(payload)
        else:
            self._rebuild_analysis_display(full, partial, gaps, suggestions, danger)
            self._rebuild_weakest_link(slot_stats, weakest_slot, replace_sugg)
            self._rebuild_preferred_typing(pref_type)
            self._rebuild_weakness_grid()

    # ── analysis ──────────────────────────────────────────────────────────────

    def _trigger_analysis_rebuild(self):
        move_types_by_slot = [
            [m.type for m in self._team_moves[s] if m is not None and m.category != "status"]
            for s in range(TEAM_SIZE)
        ]
        if not any(d is not None for d in self._team_data):
            empty = [None] * TEAM_SIZE
            self._signals.analysis_ready.emit(
                (None, None, None, None, None, empty, None, None, None,
                 list(self._team_weaknesses), [None] * TEAM_SIZE)
            )
            return
        threading.Thread(
            target=self._compute_analysis,
            args=(move_types_by_slot, list(self._team_data), list(self._team_weaknesses)),
            daemon=True,
        ).start()

    def _compute_analysis(self, move_types_by_slot: list, team_data: list, team_weaknesses: list):
        try:
            all_move_types = [t for slot in move_types_by_slot for t in slot]

            if all_move_types:
                full, partial, gaps = detailed_coverage(all_move_types)
                suggestions = coverage_suggestions(all_move_types, n=2)
                danger      = dangerous_combos(all_move_types, team_weaknesses, n=3)
                if gaps:
                    s = coverage_suggestions(all_move_types, n=4)
                    pref_type = [(t, c, "gap") for t, c in s]
                else:
                    s = redundancy_suggestions(all_move_types, n=4)
                    pref_type = [(t, c, "redundancy") for t, c in s]
            else:
                full = partial = gaps = suggestions = danger = None
                pref_type = []

            slot_stats = []
            for s, pokemon in enumerate(team_data):
                if pokemon is None:
                    slot_stats.append(None)
                    continue
                bst     = sum(pokemon.stats.values())
                bst_pct = stats_db.bst_percentile(bst)
                my_types    = move_types_by_slot[s]
                other_types = [t for i, st in enumerate(move_types_by_slot) if i != s for t in st]
                breadth, unique = slot_coverage(my_types, other_types)
                slot_stats.append((bst, bst_pct, breadth, unique))

            filled = [(s, st) for s, st in enumerate(slot_stats) if st is not None]
            weakest_slot = replace_sugg = None
            if filled:
                weakest_slot, _ = min(filled, key=lambda x: (x[1][3], x[1][2], x[1][1]))
                other_types = [t for s, st in enumerate(move_types_by_slot) if s != weakest_slot for t in st]
                suggs = coverage_suggestions(other_types, n=1) if other_types else []
                replace_sugg = suggs[0] if suggs else None

            team_names = [p.name if p else None for p in team_data]
            self._signals.analysis_ready.emit(
                (full, partial, gaps, suggestions, danger,
                 slot_stats, weakest_slot, replace_sugg, pref_type,
                 list(team_weaknesses), team_names)
            )
        except Exception:
            pass

    def _rebuild_analysis_display(self, full, partial, gaps, suggestions, danger):
        self._clear_layout(self._tips_area)
        self._clear_layout(self._danger_area)

        if full is None:
            self._tips_area.addWidget(self._placeholder("Enter moves to see coverage tips"))
            self._danger_area.addWidget(self._placeholder("Enter moves to see danger combos"))
            return

        fs = "10px" if self._embedded else "12px"

        if suggestions:
            for type_name, count in suggestions:
                row = QHBoxLayout()
                row.setSpacing(4)
                arr = QLabel("→")
                arr.setFixedWidth(10)
                arr.setStyleSheet(f"color:#89b4fa; font-size:{fs};")
                row.addWidget(arr)
                row.addWidget(self._make_type_badge(type_name))
                desc = QLabel(f"covers {count} gap{'s' if count != 1 else ''}")
                desc.setStyleSheet(f"color:#a6adc8; font-size:{fs};")
                row.addWidget(desc)
                row.addStretch()
                self._tips_area.addLayout(row)
        else:
            self._tips_area.addWidget(self._placeholder("Full coverage — no gaps!"))

        if danger:
            for t1, t2, weak_count in danger:
                row = QHBoxLayout()
                row.setSpacing(3)
                warn = QLabel("⚠")
                warn.setStyleSheet(f"color:#f38ba8; font-size:{fs};")
                row.addWidget(warn)
                row.addWidget(self._make_type_badge(t1))
                sep = QLabel("/")
                sep.setStyleSheet(f"color:#6c7086; font-size:{fs};")
                row.addWidget(sep)
                row.addWidget(self._make_type_badge(t2))
                count_color = (
                    "#f38ba8" if weak_count >= 2 else
                    "#fab387" if weak_count == 1 else
                    "#6c7086"
                )
                count_lbl = QLabel(f"{weak_count}×weak")
                count_lbl.setStyleSheet(f"color:{count_color}; font-size:{fs};")
                row.addWidget(count_lbl)
                row.addStretch()
                self._danger_area.addLayout(row)
        else:
            self._danger_area.addWidget(self._placeholder("No uncovered dual-type combos"))

    def _update_slot_stats(self, slot_stats: list):
        for s, stat in enumerate(slot_stats):
            lbl = self._stats_lbls[s]
            if lbl is None:
                continue
            if stat is None:
                lbl.setVisible(False)
                continue
            bst, bst_pct, breadth, unique = stat
            pct_color  = "#a6e3a1" if bst_pct >= 66 else "#f9e2af" if bst_pct >= 33 else "#f38ba8"
            uniq_color = "#f38ba8" if unique == 0 and breadth > 0 else "#6c7086"
            lbl.setText(
                f'<span style="color:#6c7086">BST </span>'
                f'<span style="color:#a6adc8">{bst}</span>'
                f'<span style="color:{pct_color}"> {bst_pct}th%</span>'
                f'<span style="color:#313244"> · </span>'
                f'<span style="color:#6c7086">{breadth} types · </span>'
                f'<span style="color:{uniq_color}">{unique} unique</span>'
            )
            lbl.setVisible(True)

    def _rebuild_weakest_link(self, slot_stats: list, weakest_slot, replace_sugg):
        self._clear_layout(self._weak_link_area)
        filled = [(s, st) for s, st in enumerate(slot_stats) if st is not None]
        if not filled:
            self._weak_link_area.addWidget(self._placeholder("Add Pokemon to see weakest link"))
            return

        ranked = sorted(filled, key=lambda x: (x[1][3], x[1][2], x[1][1]))
        for s, (bst, bst_pct, breadth, unique) in ranked:
            pokemon    = self._team_data[s]
            name       = pokemon.name.capitalize() if pokemon else f"Slot {s + 1}"
            is_weakest = s == weakest_slot

            row = QHBoxLayout()
            row.setSpacing(5)

            warn = QLabel("⚠" if is_weakest else "")
            warn.setFixedWidth(12)
            warn.setStyleSheet("color:#f38ba8; font-size:12px;")

            name_lbl = QLabel(name)
            name_color = "#f38ba8" if is_weakest else "#cdd6f4"
            name_lbl.setStyleSheet(f"color:{name_color}; font-size:12px;")

            pct_color = "#a6e3a1" if bst_pct >= 66 else "#f9e2af" if bst_pct >= 33 else "#f38ba8"
            stat_lbl  = QLabel(f"{bst} {bst_pct}th%")
            stat_lbl.setStyleSheet(f"color:{pct_color}; font-size:12px;")

            uniq_color = "#f38ba8" if unique == 0 and breadth > 0 else "#6c7086"
            cov_lbl    = QLabel(f"{breadth}t/{unique}u")
            cov_lbl.setStyleSheet(f"color:{uniq_color}; font-size:12px;")

            row.addWidget(warn)
            row.addWidget(name_lbl)
            row.addWidget(stat_lbl)
            row.addStretch()
            row.addWidget(cov_lbl)
            self._weak_link_area.addLayout(row)

        if weakest_slot is not None:
            row = QHBoxLayout()
            row.setSpacing(6)
            arr = QLabel("→")
            arr.setFixedWidth(12)
            arr.setStyleSheet("color:#89b4fa; font-size:12px;")
            row.addWidget(arr)
            if replace_sugg:
                type_name, gain = replace_sugg
                row.addWidget(self._make_type_badge(type_name))
                desc = QLabel(f"gains {gain} type{'s' if gain != 1 else ''}")
                desc.setStyleSheet("color:#a6adc8; font-size:12px;")
                row.addWidget(desc)
            else:
                desc = QLabel("Coverage unchanged without them")
                desc.setStyleSheet("color:#6c7086; font-size:12px;")
                row.addWidget(desc)
            row.addStretch()
            self._weak_link_area.addLayout(row)

    def _rebuild_preferred_typing(self, pref_type):
        self._clear_layout(self._pref_type_area)
        if not pref_type:
            self._pref_type_area.addWidget(self._placeholder("Enter moves to see"))
            return
        fs = "10px" if self._embedded else "11px"
        for type_name, count, mode in pref_type:
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(self._make_type_badge(type_name))
            if mode == "gap":
                desc = QLabel(f"fills {count} gap{'s' if count != 1 else ''}")
                desc.setStyleSheet(f"color:#f38ba8; font-size:{fs};")
            else:
                desc = QLabel(f"backs up {count} type{'s' if count != 1 else ''}")
                desc.setStyleSheet(f"color:#a6e3a1; font-size:{fs};")
            row.addWidget(desc)
            row.addStretch()
            self._pref_type_area.addLayout(row)

    # ── weakness grid ─────────────────────────────────────────────────────────

    def _rebuild_weakness_grid(self):
        self._clear_layout(self._weak_area)

        if not any(d is not None for d in self._team_data):
            self._weak_area.addWidget(self._placeholder("Add Pokemon to see weaknesses"))
            return

        type_counts = {}
        for t in ALL_TYPES:
            w2 = sum(1 for w in self._team_weaknesses if w is not None and w.get(t, 1.0) >= 2.0)
            w4 = sum(1 for w in self._team_weaknesses if w is not None and w.get(t, 1.0) >= 4.0)
            if w2 > 0:
                type_counts[t] = (w2, w4)

        if not type_counts:
            self._weak_area.addWidget(self._placeholder("No shared weaknesses"))
            return

        entries = sorted(type_counts.items(), key=lambda x: (-x[1][0], -x[1][1]))
        for i in range(0, len(entries), 4):
            row = QHBoxLayout()
            row.setSpacing(2)
            for type_name, (w2, w4) in entries[i:i + 4]:
                color = (
                    "#f38ba8" if w2 >= 4 else
                    "#fab387" if w2 >= 3 else
                    "#f9e2af" if w2 == 2 else
                    "#6c7086"
                )
                count_str = f"{w2}" + (" 4×" if w4 else "")
                count_lbl = QLabel(count_str)
                count_lbl.setStyleSheet(f"color:{color}; font-size:12px; font-weight:bold;")
                row.addWidget(self._make_type_badge(type_name))
                row.addWidget(count_lbl)
                row.addSpacing(8)
            row.addStretch()
            self._weak_area.addLayout(row)

    # ── type badge ────────────────────────────────────────────────────────────

    def _make_type_badge(self, type_name: str, tooltip: str = "") -> QLabel:
        bg, fg = TYPE_COLORS.get(type_name, ("#888", "#fff"))
        lbl = QLabel(type_name[:4].upper())
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:3px;"
            f"padding:0px 3px; font-size:12px; font-weight:bold;"
        )
        lbl.setFixedHeight(17)
        if tooltip:
            lbl.setToolTip(tooltip)
        return lbl

    # ── helpers ───────────────────────────────────────────────────────────────

    def _placeholder(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#45475a; font-size:12px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    # ── persistence ───────────────────────────────────────────────────────────

    def _save_team(self):
        team = []
        for i in range(TEAM_SIZE):
            if self._team_data[i] is not None:
                moves = []
                for m in self._team_moves[i]:
                    if m is not None:
                        moves.append({
                            "name":        m.name,
                            "type":        m.type,
                            "power":       m.power,
                            "accuracy":    m.accuracy,
                            "category":    m.category,
                            "description": m.description,
                        })
                    else:
                        moves.append(None)
                lbl = self._level_lbls[i]
                level = lbl.text()[1:] if (lbl is not None and lbl.isVisible() and lbl.text().startswith("L")) else None
                team.append({"name": self._team_data[i].name, "moves": moves, "level": level})
            else:
                team.append(None)
        window_state.save_key("team", team)

    def load_saved_team(self):
        state = window_state.load()
        for i, entry in enumerate(state.get("team", [])[:TEAM_SIZE]):
            if not (entry and isinstance(entry, dict)):
                continue
            if entry.get("name"):
                threading.Thread(
                    target=self._lookup, args=(i, entry["name"]), daemon=True
                ).start()
            level = entry.get("level")
            if level:
                lbl = self._level_lbls[i]
                if lbl is not None:
                    lbl.setText(f"L{level}")
                    lbl.setVisible(True)
            for mi, move in enumerate(entry.get("moves", [])[:MOVE_SLOTS]):
                if not (move and isinstance(move, dict) and move.get("name") and move.get("type")):
                    continue
                if "category" in move:
                    self._apply_saved_move(i, mi, move)
                else:
                    # Old save format without full stats — re-fetch
                    threading.Thread(
                        target=self._do_lookup_move,
                        args=(i, mi, move["name"]),
                        daemon=True,
                    ).start()
        self._trigger_analysis_rebuild()

    def _apply_saved_move(self, slot: int, mi: int, move_dict: dict):
        move = MoveData(
            name=move_dict["name"],
            type=move_dict["type"],
            power=move_dict.get("power"),
            accuracy=move_dict.get("accuracy"),
            category=move_dict.get("category", "status"),
            description=move_dict.get("description", ""),
        )
        self._team_moves[slot][mi] = move
        self._apply_move_to_row(slot, mi, move)

    # ── drag to move ──────────────────────────────────────────────────────────

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
        window_state.save_key("team_panel", {"x": p.x(), "y": p.y()})
