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

from pokemon_api import fetch_pokemon, fetch_move, fetch_ability, nature_mod_str, MoveData, PokemonData
from weakness_calc import (
    calculate_weaknesses, detailed_coverage,
    coverage_suggestions, coverage_suggestions_from_gaps,
    redundancy_suggestions, dangerous_combos, slot_coverage, ALL_TYPES,
    covered_gaps,
)
from overlay import _resolve_name, _FORM_VARIANTS, _SLUG_TO_BASE
import stats_db
import moves_db
import impact_db

TEAM_SIZE  = 6
MOVE_SLOTS = 4
_TOTAL_PAIRINGS = 171


def _moves_pairing_vector(pokemon, moves: list) -> dict:
    """Per-pairing best SE damage using the player's actual equipped moves."""
    if not impact_db.is_ready():
        return {}
    atk    = pokemon.stats.get("attack", 0)
    sp_atk = pokemon.stats.get("special-attack", 0)
    types  = pokemon.types
    result: dict = {}
    for move in moves:
        if move is None or not move.power or move.category == "status":
            continue
        stat = atk if move.category == "physical" else sp_atk
        stab = 1.5 if move.type in types else 1.0
        acc  = (move.accuracy or 100) / 100.0
        base = stat * move.power * acc * stab
        for pairing in impact_db.ALL_PAIRINGS:
            se = impact_db._EFF.get((move.type, pairing), 0.0)
            if se > 1.0:
                val = base * se
                if val > result.get(pairing, 0.0):
                    result[pairing] = val
    return result

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
    result_ready          = pyqtSignal(object)  # (slot, PokemonData, weaknesses)
    move_ready            = pyqtSignal(object)  # (slot, mi, MoveData)
    analysis_ready        = pyqtSignal(object)  # (full, partial, gaps, suggestions, danger, slot_stats, weakest_slot, replace_sugg, pref_type)
    team_changed          = pyqtSignal()        # fires whenever the team file is rewritten — listeners can refresh derived displays
    error                 = pyqtSignal(object)
    ability_tooltip_ready = pyqtSignal(int, str)


class TeamPanel(QWidget):
    def __init__(self, embedded: bool = False, strip: bool = False):
        super().__init__()
        self._strip           = strip
        self._embedded        = embedded or strip  # strip implies embedded
        self._team_data       = [None] * TEAM_SIZE
        self._team_weaknesses = [None] * TEAM_SIZE
        self._team_moves      = [[None] * MOVE_SLOTS for _ in range(TEAM_SIZE)]
        self._drag_pos        = None

        self._name_lbls      = [None] * TEAM_SIZE
        self._matchup_lbls   = [None] * TEAM_SIZE
        self._type_rows      = [None] * TEAM_SIZE
        self._stats_lbls     = [None] * TEAM_SIZE
        self._ability_lbls   = [None] * TEAM_SIZE
        self._level_lbls     = [None] * TEAM_SIZE
        self._level_vals: list[int | None] = [None] * TEAM_SIZE  # parsed level per slot, for turn-order calc
        self._hp_bars        = [None] * TEAM_SIZE
        self._move_widgets   = [[None] * MOVE_SLOTS for _ in range(TEAM_SIZE)]
        self._slot_frames    = [None] * TEAM_SIZE
        self._last_abilities: list[tuple] = [(None, None)] * TEAM_SIZE
        self._analysis_panel = None
        self._active_slot_idx: int | None  = None
        self._active_slot_idx2: int | None = None
        self._pending_active  = ""           # last name from active OCR, for matching after auto-add
        self._pending_lookups: set = set()        # slots currently being fetched
        self._pending_lookup_names: dict = {}     # slot → name being fetched
        self._player_sampled_types   = [None, None]
        self._player_sampled_types2  = [None, None]
        self._player_form_pending    = False
        self._player_form_pending2   = False
        # Per-slot form-override state — used by set_party for all 6 party members
        # (not just the active ones). Tracks the JS in-battle types we expect for
        # each slot so _on_result can apply the override after auto-fetch.
        self._party_target_types: list[list] = [[] for _ in range(TEAM_SIZE)]
        self._form_pending_slots: set = set()
        # Move-OCR debounce keyed by move_idx only (not team slot) — in 2v2 the
        # same on-screen move boxes can belong to either active Pokémon, so the
        # owning slot is decided at commit time, not at debounce time.
        self._move_ocr_buf: list[tuple[str, int]] = [("", 0)] * MOVE_SLOTS
        # Which active position (0 or 1) was most recently filled — used as the
        # tiebreaker when 2v2 move attribution is ambiguous (both candidate slots
        # score equally for an OCR'd move).
        self._last_active_position: int = 0
        self._avg_lbl = None

        self._signals = _Signals()
        self._signals.result_ready.connect(self._on_result)
        self._signals.move_ready.connect(self._on_move_ready)
        self._signals.analysis_ready.connect(self._on_analysis_ready)
        self._signals.error.connect(lambda p: (
            self._pending_lookups.discard(p[0]),
            self._pending_lookup_names.pop(p[0], None),
        ))
        self._signals.ability_tooltip_ready.connect(self._on_ability_tooltip)

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

        app = QApplication.instance()
        app.setStyleSheet(
            "QToolTip { background:#313244; color:#cdd6f4; border:1px solid #45475a;"
            " font-size:12px; padding:4px; border-radius:4px; }"
        )
        try:
            app.setAttribute(Qt.ApplicationAttribute.AA_AlwaysShowToolTips)
        except AttributeError:
            pass  # removed in Qt6

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

        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr = QLabel("My Team")
        hdr.setStyleSheet("color:#cdd6f4; font-size:13px; font-weight:bold;")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        self._avg_lbl = QLabel("")
        self._avg_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._avg_lbl.setStyleSheet("font-size:11px;")
        hdr_row.addWidget(self._avg_lbl)
        root.addLayout(hdr_row)

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
        target_base = _SLUG_TO_BASE.get(name, name) if name else ""

        def _find_slot() -> int | None:
            # 1) exact name match
            for slot in range(TEAM_SIZE):
                pokemon = self._team_data[slot]
                if pokemon and name and pokemon.name.lower() == name:
                    return slot
            # 2) base-species fallback (handles regional/form mismatch:
            # JS may report "ninetales" while team_data has "ninetales-alola")
            if name:
                for slot in range(TEAM_SIZE):
                    pokemon = self._team_data[slot]
                    if not pokemon:
                        continue
                    pkm_norm = pokemon.name.lower()
                    pkm_base = _SLUG_TO_BASE.get(pkm_norm, pkm_norm)
                    if pkm_base == target_base:
                        return slot
            return None

        if position == 0:
            self._pending_active = name
            self._active_slot_idx = _find_slot()
            self._last_hp_cur = None
            self._last_hp_max = None
            self._player_sampled_types = [None, None]
            self._player_form_pending  = False
            already_pending = name in self._pending_lookup_names.values()
            if name and self._active_slot_idx is None and not already_pending:
                for slot in range(TEAM_SIZE):
                    if self._team_data[slot] is None and slot not in self._pending_lookups:
                        self._pending_lookups.add(slot)
                        self._pending_lookup_names[slot] = name
                        threading.Thread(
                            target=self._lookup, args=(slot, name), daemon=True
                        ).start()
                        break
            if name:
                self._last_active_position = 0
        else:
            self._active_slot_idx2 = _find_slot()
            self._player_sampled_types2 = [None, None]
            self._player_form_pending2  = False
            if name:
                self._last_active_position = 1
            else:
                # Position 1 cleared (e.g. exiting 2v2) — fall back to position 0
                # as the default tiebreaker for ambiguous move attribution.
                self._last_active_position = 0
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
        """Called by JS-state dispatcher with the active player Pokémon level as digit text."""
        digits = "".join(c for c in text if c.isdigit())
        slot = self._active_slot_idx if position == 0 else self._active_slot_idx2
        if not digits or slot is None:
            return
        lbl = self._level_lbls[slot]
        if lbl is not None:
            lbl.setText(f"L{digits}")
            lbl.setVisible(True)
        try:
            lv = int(digits)
        except ValueError:
            return
        if self._level_vals[slot] != lv:
            self._level_vals[slot] = lv

    def receive_player_abilities(self, position: int, ability: str | None, passive: str | None,
                                  ability_index: int | None = None, nature=None) -> None:
        """Called by JS-state dispatcher with the active player Pokémon's ability + passive."""
        slot = self._active_slot_idx if position == 0 else self._active_slot_idx2
        if slot is None:
            return
        self._set_slot_abilities(slot, ability, passive, ability_index, nature)

    def _set_slot_abilities(self, slot: int, ability: str | None, passive: str | None,
                             ability_index: int | None = None, nature=None) -> None:
        lbl = self._ability_lbls[slot]
        if lbl is None:
            return
        ab_parts = []
        if ability:
            hidden = ability_index == 2
            h_mark = " <span style='color:#f9e2af;font-size:9px;'>[H]</span>" if hidden else ""
            ab_parts.append(f"<span style='color:#cba6f7;'>⚡ {ability}{h_mark}</span>")
        if passive:
            ab_parts.append(f"<span style='color:#89b4fa;'>✦ {passive}</span>")
        nat = nature_mod_str(nature)
        if ab_parts or nat:
            left = "  ".join(ab_parts)
            right = (f"<span style='color:#a6adc8;font-size:9px;'>{nat}</span>"
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

    def set_party(self, party: list) -> None:
        """Pre-populate all 6 team slots from JS-state party data. Each entry:
        {name, level, hp, maxHp, fainted, types, ability, passive, moves}.

        Slots are 1:1 with Pokerogue's party order. For any slot whose team_data
        is empty or a different base species, triggers an async PokeAPI fetch.
        For slots whose stored types disagree with JS in-battle types (form
        mismatch — e.g. Alolan Ninetales), triggers a form-override fetch."""
        def _norm(s: str) -> str:
            return s.lower().replace(" ", "-").replace(".", "").replace("'", "")

        for slot in range(TEAM_SIZE):
            if slot >= len(party):
                self._party_target_types[slot] = []
                if self._team_data[slot] is not None:
                    self._clear_slot(slot)
                elif self._hp_bars[slot] is not None:
                    self._hp_bars[slot].setVisible(False)
                continue
            member = party[slot] or {}
            name = _norm(member.get('name') or '')
            if not name:
                self._party_target_types[slot] = []
                if self._team_data[slot] is not None:
                    self._clear_slot(slot)
                elif self._hp_bars[slot] is not None:
                    self._hp_bars[slot].setVisible(False)
                continue

            # HP bar — always render, even before fetch completes
            self._render_hp_bar(slot, member.get('hp', 0), member.get('maxHp', 0),
                                bool(member.get('fainted')))

            # Level — push immediately
            lv = member.get('level')
            if lv is not None:
                self._level_vals[slot] = lv
                lbl = self._level_lbls[slot]
                if lbl is not None:
                    lbl.setText(f"L{lv}")
                    lbl.setVisible(True)

            # Abilities
            self._set_slot_abilities(slot, member.get('ability'), member.get('passive'),
                                     member.get('abilityIndex'), member.get('nature'))

            # Track JS types for form-override on this slot
            types = list(member.get('types') or [])
            self._party_target_types[slot] = types

            current = self._team_data[slot]
            new_base = _SLUG_TO_BASE.get(name, name)
            need_fetch = False
            if current is None:
                need_fetch = True
            else:
                cur_norm = _norm(current.name)
                cur_base = _SLUG_TO_BASE.get(cur_norm, cur_norm)
                if cur_base != new_base:
                    need_fetch = True

            if need_fetch:
                if slot not in self._pending_lookups:
                    self._pending_lookups.add(slot)
                    self._pending_lookup_names[slot] = name
                    threading.Thread(target=self._lookup, args=(slot, name), daemon=True).start()
            elif types and set(types) != set(current.types):
                # Same base species, different form — fetch the matching variant
                self._try_form_override_for_slot(slot, types)

            # Moves — push each by slot directly (no OCR attribution needed)
            for mi, move in enumerate(member.get('moves') or []):
                if mi >= MOVE_SLOTS or not move:
                    continue
                existing = self._team_moves[slot][mi]
                if existing and existing.name.lower() == move.lower():
                    continue
                threading.Thread(
                    target=self._do_lookup_move, args=(slot, mi, move.lower()), daemon=True
                ).start()

    def _try_form_override_for_slot(self, slot: int, sampled: list) -> None:
        """Slot-keyed form override: fetch the form variant whose PokeAPI types
        match the JS in-battle types and re-emit result_ready for this slot."""
        pokemon = self._team_data[slot]
        if pokemon is None:
            return
        pkm_norm = pokemon.name.lower()
        base = _SLUG_TO_BASE.get(pkm_norm, pkm_norm)
        variants = _FORM_VARIANTS.get(base)
        if not variants or slot in self._form_pending_slots:
            return
        self._form_pending_slots.add(slot)

        def _check():
            try:
                for _label, slug in variants:
                    if slug == pkm_norm:
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
                self._form_pending_slots.discard(slot)

        threading.Thread(target=_check, daemon=True).start()

    def _render_hp_bar(self, slot: int, hp: int, max_hp: int, fainted: bool) -> None:
        bar = self._hp_bars[slot]
        if bar is None or max_hp <= 0:
            return
        pct = max(0, min(100, int(round(hp / max_hp * 100))))
        if fainted or hp <= 0:
            color = "#45475a"
            text = "FAINTED"
        elif pct <= 20:
            color = "#f38ba8"
            text = f"{hp}/{max_hp}"
        elif pct <= 50:
            color = "#f9e2af"
            text = f"{hp}/{max_hp}"
        else:
            color = "#a6e3a1"
            text = f"{hp}/{max_hp}"
        bar.setStyleSheet(_HP_BAR_STYLE.format(color=color))
        bar.setValue(pct if not fainted else 100)
        bar.setFormat(text)
        bar.setVisible(True)

    def receive_move_ocr(self, move_idx: int, text: str) -> None:
        """Called by JS-state dispatcher with a move name. In 2v2 either active
        Pokémon may own a given move slot, so we debounce on the move text alone
        and decide the owning team slot at commit time by scoring each candidate
        against what we already know."""
        candidates = self._active_team_slots()
        if not candidates:
            return
        cleaned = " ".join(text.strip().split())
        cleaned = "".join(c for c in cleaned if c.isalpha() or c == " ").strip()
        if len(cleaned) < 3 or len(cleaned) > 22:
            return
        # Debounce: only commit after 2 consecutive identical readings at this move_idx.
        last_text, count = self._move_ocr_buf[move_idx]
        if cleaned.lower() == last_text.lower():
            count += 1
            self._move_ocr_buf[move_idx] = (cleaned, count)
            if count != 2:
                return
        else:
            self._move_ocr_buf[move_idx] = (cleaned, 1)
            return
        target_slot = self._choose_slot_for_move(candidates, cleaned.lower())
        if target_slot is None:
            return
        threading.Thread(
            target=self._do_lookup_move, args=(target_slot, move_idx, cleaned.lower()), daemon=True
        ).start()

    def _active_team_slots(self) -> list[int]:
        """Currently-active team slot indices (1 entry in 1v1, up to 2 in 2v2)."""
        result: list[int] = []
        if self._active_slot_idx is not None:
            result.append(self._active_slot_idx)
        if self._active_slot_idx2 is not None and self._active_slot_idx2 not in result:
            result.append(self._active_slot_idx2)
        return result

    def _last_active_slot(self) -> int | None:
        """Whichever active slot was most recently filled — tiebreaker for ambiguous moves."""
        if self._active_slot_idx2 is None:
            return self._active_slot_idx
        if self._active_slot_idx is None:
            return self._active_slot_idx2
        return self._active_slot_idx if self._last_active_position == 0 else self._active_slot_idx2

    def _score_move_for_slot(self, slot: int, move_name: str, all_candidates: list[int]) -> int:
        """Score how well `move_name` fits being attributed to `slot`. Higher = better fit.
        +10  this slot's known moveset already contains the move (confirms existing)
         -10  another active slot has the move but this one doesn't (almost certainly theirs)
         +5  bootstrap: no slot has it, but this slot still has empty move rows
          0  default
        """
        slot_known = {m.name.lower() for m in self._team_moves[slot] if m is not None}
        if move_name in slot_known:
            return 10
        others_have = any(
            move_name in {m.name.lower() for m in self._team_moves[o] if m is not None}
            for o in all_candidates if o != slot
        )
        if others_have:
            return -10
        has_empty = any(m is None for m in self._team_moves[slot])
        if has_empty:
            return 5
        return 0

    def _choose_slot_for_move(self, candidates: list[int], move_name: str) -> int | None:
        """Pick the best-scoring candidate slot. Tie-break by most-recently-active.
        Lenient fallback: if no candidate scores positive (true ambiguity, no signal),
        attribute to the last-active slot rather than dropping the read."""
        if len(candidates) == 1:
            return candidates[0]
        scores = {s: self._score_move_for_slot(s, move_name, candidates) for s in candidates}
        best = max(scores.values())
        if best <= 0:
            # Pure ambiguity (bootstrap with both fully-known, or other edge cases) —
            # fall through to last-active so unknown moves still attach somewhere.
            last = self._last_active_slot()
            return last if last in candidates else candidates[0]
        winners = [s for s, sc in scores.items() if sc == best]
        if len(winners) == 1:
            return winners[0]
        last = self._last_active_slot()
        return last if last in winners else winners[0]

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

        name_lbl = QLabel("pokemon…")
        name_lbl.setMinimumWidth(0)
        name_lbl.setStyleSheet("color:#45475a; font-size:16px;")
        self._name_lbls[slot] = name_lbl

        matchup_lbl = QLabel("")
        matchup_lbl.setTextFormat(Qt.TextFormat.RichText)
        matchup_lbl.setStyleSheet("font-size:11px;")
        matchup_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        matchup_lbl.setVisible(False)
        self._matchup_lbls[slot] = matchup_lbl

        clear_btn = QPushButton("✕")
        clear_btn.setFixedSize(13, 13)
        clear_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#45475a;border:none;font-size:9px;}"
            "QPushButton:hover{color:#f38ba8;}"
        )
        clear_btn.clicked.connect(lambda _, s=slot: self._clear_slot(s))

        header.addWidget(level_lbl)
        header.addWidget(type_container)
        header.addWidget(name_lbl, 1)
        header.addWidget(matchup_lbl)
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

        ability_lbl = QLabel("")
        ability_lbl.setStyleSheet(
            "color:#cba6f7; font-size:9px;" if self._strip
            else "color:#cba6f7; font-size:12px;"
        )
        ability_lbl.setVisible(False)
        self._ability_lbls[slot] = ability_lbl
        layout.addWidget(ability_lbl)

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
        lbl = self._name_lbls[slot]
        if lbl is not None:
            lbl.setText("pokemon…")
            lbl.setStyleSheet("color:#45475a; font-size:16px;")
        if self._matchup_lbls[slot] is not None:
            self._matchup_lbls[slot].setVisible(False)
        self._clear_type_badges(slot)
        for mi in range(MOVE_SLOTS):
            self._reset_move_row(slot, mi)
        if self._stats_lbls[slot] is not None:
            self._stats_lbls[slot].setVisible(False)
        if self._hp_bars[slot] is not None:
            self._hp_bars[slot].setValue(0)
            self._hp_bars[slot].setVisible(False)
        if self._ability_lbls[slot] is not None:
            self._ability_lbls[slot].setVisible(False)
            self._ability_lbls[slot].setToolTip("")
        if self._level_lbls[slot] is not None:
            self._level_lbls[slot].setVisible(False)
        self._last_abilities[slot] = (None, None)

    def _clear_slot(self, slot: int):
        self._clear_slot_data(slot)
        self._level_vals[slot] = None
        if not self._embedded:
            self._rebuild_weakness_grid()
        self._update_avg_lbl()
        self._trigger_analysis_rebuild()
        self._save_team()

    def clear_all_slots(self):
        """Wipe every team slot. Used by New Run."""
        for slot in range(TEAM_SIZE):
            self._clear_slot_data(slot)
            self._level_vals[slot] = None
        self._pending_lookups.clear()
        self._pending_lookup_names.clear()
        self._move_ocr_buf = [("", 0)] * MOVE_SLOTS
        if not self._embedded:
            self._rebuild_weakness_grid()
        self._update_avg_lbl()
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
        self._pending_lookup_names.pop(slot, None)
        self._team_data[slot]       = pokemon
        self._team_weaknesses[slot] = weaknesses
        lbl = self._name_lbls[slot]
        if lbl is not None:
            lbl.setText(pokemon.name.capitalize())
            lbl.setStyleSheet("color:#cdd6f4; font-size:16px; font-weight:bold;")
        self._clear_type_badges(slot)
        for t in pokemon.types:
            self._type_rows[slot].addWidget(self._make_type_badge(t))
        if not self._embedded:
            self._rebuild_weakness_grid()
        # If the newly-added pokemon matches what JS currently has active, activate
        # immediately without waiting for the next snapshot.
        pkm_norm = pokemon.name.lower()
        pkm_base = _SLUG_TO_BASE.get(pkm_norm, pkm_norm)
        if self._pending_active:
            pa_base = _SLUG_TO_BASE.get(self._pending_active, self._pending_active)
            if pkm_norm == self._pending_active or pkm_base == pa_base:
                self._active_slot_idx = slot
                self._refresh_slot_highlights()
        # Form override: if JS tracked target types for this slot disagree with the
        # PokeAPI types we just fetched, kick off a form-variant fetch.
        target = self._party_target_types[slot]
        if target and set(target) != set(pokemon.types):
            self._try_form_override_for_slot(slot, target)
        self._update_avg_lbl()
        self._trigger_analysis_rebuild()
        self._save_team()

    # ── Player type override (from JS-state dispatcher) ──────────────────────

    def set_player_types(self, types: list, position: int = 0) -> None:
        """Atomic two-type push from the JS-state dispatcher. Avoids a race
        where pushing types one-by-one fires the form-override on incomplete
        sampled data, marking _player_form_pending and silently dropping the
        second push."""
        sampled = self._player_sampled_types if position == 0 else self._player_sampled_types2
        changed = False
        for i in range(2):
            new_val = types[i] if i < len(types) else None
            if sampled[i] != new_val:
                sampled[i] = new_val
                changed = True
        if changed:
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
        team_moves = [list(self._team_moves[s]) for s in range(TEAM_SIZE)]
        if not any(d is not None for d in self._team_data):
            empty = [None] * TEAM_SIZE
            self._signals.analysis_ready.emit(
                (None, None, None, None, None, empty, None, None, None,
                 list(self._team_weaknesses), [None] * TEAM_SIZE)
            )
            return
        threading.Thread(
            target=self._compute_analysis,
            args=(move_types_by_slot, team_moves, list(self._team_data), list(self._team_weaknesses)),
            daemon=True,
        ).start()

    def _compute_analysis(self, move_types_by_slot: list, team_moves: list, team_data: list, team_weaknesses: list):
        try:
            all_move_types = [t for slot in move_types_by_slot for t in slot]

            if all_move_types:
                full, partial, gaps = detailed_coverage(all_move_types)
                suggestions = coverage_suggestions_from_gaps(gaps, n=2)
                danger      = dangerous_combos(all_move_types, team_weaknesses, n=3)
                if gaps:
                    s = coverage_suggestions_from_gaps(gaps, n=4)
                    pref_type = [(t, c, "gap") for t, c in s]
                else:
                    s = redundancy_suggestions(all_move_types, n=4)
                    pref_type = [(t, c, "redundancy") for t, c in s]
            else:
                full = partial = gaps = suggestions = danger = None
                pref_type = []

            # Matchup share: for each of the 171 type pairings, which slot's
            # actual equipped moves deal the most SE damage?
            slot_pvs = [
                _moves_pairing_vector(pokemon, team_moves[s]) if pokemon else {}
                for s, pokemon in enumerate(team_data)
            ]
            matchup_counts = [0] * TEAM_SIZE
            unique_counts  = [0] * TEAM_SIZE
            if impact_db.is_ready():
                for pairing in impact_db.ALL_PAIRINGS:
                    covered = [(s, pv[pairing]) for s, pv in enumerate(slot_pvs) if pairing in pv]
                    if not covered:
                        continue
                    if len(covered) == 1:
                        unique_counts[covered[0][0]] += 1
                    winner = max(covered, key=lambda x: x[1])[0]
                    matchup_counts[winner] += 1

            slot_stats = []
            for s, pokemon in enumerate(team_data):
                if pokemon is None:
                    slot_stats.append(None)
                    continue
                bst     = sum(pokemon.stats.values())
                bst_pct = stats_db.bst_percentile(bst)
                slot_stats.append((bst, bst_pct, matchup_counts[s], unique_counts[s]))

            filled = [(s, st) for s, st in enumerate(slot_stats) if st is not None]
            weakest_slot = replace_sugg = None
            if filled:
                weakest_slot, _ = min(filled, key=lambda x: (x[1][2], x[1][3], x[1][1]))
                other_types = [t for s, st in enumerate(move_types_by_slot) if s != weakest_slot for t in st]
                if other_types:
                    _, _, weakest_gaps = detailed_coverage(other_types)
                    suggs = coverage_suggestions_from_gaps(weakest_gaps, n=1)
                    if suggs:
                        type_name, count = suggs[0]
                        specific = covered_gaps(type_name, weakest_gaps)
                        replace_sugg = (type_name, count, specific)
                    else:
                        replace_sugg = None
                else:
                    replace_sugg = None

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
                covered = covered_gaps(type_name, gaps) if gaps else []
                row = QHBoxLayout()
                row.setSpacing(4)
                add_lbl = QLabel("Add")
                add_lbl.setStyleSheet(f"color:#89b4fa; font-size:{fs};")
                row.addWidget(add_lbl)
                row.addWidget(self._make_type_badge(type_name))
                count_lbl = QLabel(f"({count})")
                count_lbl.setStyleSheet(f"color:#a6adc8; font-size:{fs};")
                row.addWidget(count_lbl)
                for gap_type in covered:
                    row.addWidget(self._make_type_badge(gap_type))
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
            mu_lbl = self._matchup_lbls[s]
            stats_lbl = self._stats_lbls[s]
            if stat is None:
                if mu_lbl is not None:
                    mu_lbl.setVisible(False)
                if stats_lbl is not None:
                    stats_lbl.setVisible(False)
                continue
            bst, bst_pct, matchup, unique = stat
            mu_pct = round(matchup / _TOTAL_PAIRINGS * 100)
            mu_color = "#a6e3a1" if mu_pct >= 20 else "#f9e2af" if mu_pct >= 10 else "#f38ba8"
            if mu_lbl is not None:
                mu_lbl.setText(
                    f"<span style='color:{mu_color}'>{matchup}/{_TOTAL_PAIRINGS}</span>"
                )
                mu_lbl.setVisible(True)
            if stats_lbl is not None:
                pct_color = "#a6e3a1" if bst_pct >= 66 else "#f9e2af" if bst_pct >= 33 else "#f38ba8"
                stats_lbl.setText(
                    f'<span style="color:#6c7086">BST </span>'
                    f'<span style="color:#a6adc8">{bst}</span>'
                    f'<span style="color:{pct_color}"> {bst_pct}th%</span>'
                )
                stats_lbl.setVisible(True)

    def _rebuild_weakest_link(self, slot_stats: list, weakest_slot, replace_sugg):
        self._clear_layout(self._weak_link_area)
        filled = [(s, st) for s, st in enumerate(slot_stats) if st is not None]
        if not filled:
            self._weak_link_area.addWidget(self._placeholder("Add Pokemon to see weakest link"))
            return

        ranked = sorted(filled, key=lambda x: (x[1][2], x[1][3], x[1][1]))
        for s, (bst, bst_pct, matchup, unique) in ranked:
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

            mu_pct = round(matchup / _TOTAL_PAIRINGS * 100)
            mu_color = "#a6e3a1" if mu_pct >= 20 else "#f9e2af" if mu_pct >= 10 else "#f38ba8"
            cov_lbl = QLabel(f"{matchup}/{_TOTAL_PAIRINGS}")
            cov_lbl.setStyleSheet(f"color:{mu_color}; font-size:12px;")

            row.addWidget(warn)
            row.addWidget(name_lbl)
            row.addWidget(stat_lbl)
            row.addStretch()
            row.addWidget(cov_lbl)
            self._weak_link_area.addLayout(row)

        if weakest_slot is not None:
            row = QHBoxLayout()
            row.setSpacing(4)
            if replace_sugg:
                type_name, gain, specific_gaps = replace_sugg
                add_lbl = QLabel("Add")
                add_lbl.setStyleSheet("color:#89b4fa; font-size:12px;")
                row.addWidget(add_lbl)
                row.addWidget(self._make_type_badge(type_name))
                count_lbl = QLabel(f"({gain})")
                count_lbl.setStyleSheet("color:#a6adc8; font-size:12px;")
                row.addWidget(count_lbl)
                for gap_type in specific_gaps:
                    row.addWidget(self._make_type_badge(gap_type))
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

    # ── team average ──────────────────────────────────────────────────────────

    def _update_avg_lbl(self):
        if self._avg_lbl is None:
            return
        names = [p.name.lower() for p in self._team_data if p is not None]
        if not names or not impact_db.is_ready():
            self._avg_lbl.setText("")
            return
        score = impact_db.team_score(names)
        if score <= 0:
            self._avg_lbl.setText("")
            return
        if score >= 1_000_000:
            score_str = f"{score / 1_000_000:.1f}M"
        elif score >= 1_000:
            score_str = f"{score / 1_000:.0f}k"
        else:
            score_str = f"{score:.0f}"
        self._avg_lbl.setText(
            f"<span style='color:#6c7086'>impact </span>"
            f"<span style='color:#a6e3a1'>{score_str}</span>"
        )

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
        self._signals.team_changed.emit()

    @property
    def team_changed(self):
        return self._signals.team_changed

    def load_saved_team(self):
        state = window_state.load()
        for i, entry in enumerate(state.get("team", [])[:TEAM_SIZE]):
            if not (entry and isinstance(entry, dict)):
                continue
            if entry.get("name"):
                self._pending_lookups.add(i)
                self._pending_lookup_names[i] = entry["name"].lower()
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
