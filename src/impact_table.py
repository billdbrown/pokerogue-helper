"""Impact Score browser — Pokémon, Moves, and Abilities tabs."""

import json
import os
import statistics

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QWidget, QCheckBox, QSlider,
    QTabWidget, QComboBox, QFrame,
)
from PyQt6.QtCore import Qt, QUrl, pyqtSlot, QMetaObject, Q_ARG
from PyQt6.QtGui import QColor, QDesktopServices

import impact_db
import move_info_db
import ability_info_db
from scoring import OFFENSIVE_CHART

# Final-evolution starters across all generations (including Hisuian forms)
_STARTER_POKEMON: frozenset[str] = frozenset({
    # Gen 1
    "venusaur", "charizard", "blastoise",
    # Gen 2
    "meganium", "typhlosion", "feraligatr",
    # Gen 3
    "sceptile", "blaziken", "swampert",
    # Gen 4
    "torterra", "infernape", "empoleon",
    # Gen 5
    "serperior", "emboar", "samurott",
    # Gen 6
    "chesnaught", "delphox", "greninja",
    # Gen 7
    "decidueye", "incineroar", "primarina",
    # Gen 8
    "rillaboom", "cinderace", "inteleon",
    # Gen 9
    "meowscarada", "skeledirge", "quaquaval",
    # Hisuian forms
    "typhlosion-hisui", "samurott-hisui", "decidueye-hisui",
})

TYPE_COLORS = {
    "normal":   ("#A8A878", "#000"), "fire":     ("#F08030", "#fff"),
    "water":    ("#6890F0", "#fff"), "electric": ("#F8D030", "#000"),
    "grass":    ("#78C850", "#000"), "ice":      ("#98D8D8", "#000"),
    "fighting": ("#C03028", "#fff"), "poison":   ("#A040A0", "#fff"),
    "ground":   ("#E0C068", "#000"), "flying":   ("#A890F0", "#000"),
    "psychic":  ("#F85888", "#fff"), "bug":      ("#A8B820", "#000"),
    "rock":     ("#B8A038", "#fff"), "ghost":    ("#705898", "#fff"),
    "dragon":   ("#7038F8", "#fff"), "dark":     ("#705848", "#fff"),
    "steel":    ("#B8B8D0", "#000"), "fairy":    ("#EE99AC", "#000"),
}

_STYLE = """
    QDialog   { background: #1e1e2e; color: #cdd6f4; }
    QTabWidget::pane { border: none; background: #1e1e2e; }
    QTabBar::tab {
        background: #181825; color: #6c7086; padding: 6px 20px;
        border: none; border-bottom: 2px solid transparent;
        font-size: 12px; margin-right: 2px; min-width: 80px;
    }
    QTabBar::tab:selected { color: #cdd6f4; border-bottom-color: #89b4fa; background: #1e1e2e; }
    QTabBar::tab:hover    { color: #bac2de; }
    QComboBox {
        background: #313244; color: #cdd6f4; border: 1px solid #45475a;
        border-radius: 4px; padding: 3px 8px; font-size: 12px; min-width: 110px;
    }
    QComboBox::drop-down  { border: none; width: 20px; }
    QComboBox QAbstractItemView {
        background: #313244; color: #cdd6f4; border: 1px solid #45475a;
        selection-background-color: #45475a; selection-color: #cdd6f4;
    }
    QLineEdit {
        background: #313244; color: #cdd6f4; border: 1px solid #45475a;
        border-radius: 4px; padding: 4px 8px; font-size: 12px;
    }
    QLineEdit:focus { border-color: #89b4fa; }
    QTableWidget {
        background: #181825; color: #cdd6f4; gridline-color: #2a2a3d;
        border: none; font-size: 12px; outline: none;
    }
    QTableWidget::item          { padding: 2px 6px; }
    QTableWidget::item:selected { background: #313244; color: #cdd6f4; }
    QHeaderView::section {
        background: #1e1e2e; color: #6c7086; border: none;
        border-bottom: 1px solid #45475a; border-right: 1px solid #2a2a3d;
        padding: 4px 8px; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;
    }
    QHeaderView::section:hover { color: #cdd6f4; }
    QScrollBar:vertical { background: #181825; width: 5px; border-radius: 2px; margin: 0; }
    QScrollBar::handle:vertical { background: #45475a; border-radius: 2px; min-height: 20px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QCheckBox { color: #cdd6f4; font-size: 12px; spacing: 6px; }
    QCheckBox::indicator {
        width: 13px; height: 13px; border: 1px solid #45475a;
        border-radius: 3px; background: #313244;
    }
    QCheckBox::indicator:checked { background: #89b4fa; border-color: #89b4fa; }
"""

_BULBAPEDIA_OVERRIDES: dict[str, str] = {
    "mr-mime":   "Mr._Mime",
    "mr-rime":   "Mr._Rime",
    "mime-jr":   "Mime_Jr.",
    "ho-oh":     "Ho-Oh",
    "porygon-z": "Porygon-Z",
    "type-null": "Type:_Null",
    "jangmo-o":  "Jangmo-o",
    "hakamo-o":  "Hakamo-o",
    "kommo-o":   "Kommo-o",
    "nidoran-f": "Nidoran♀",
    "nidoran-m": "Nidoran♂",
    "flabebe":   "Flabébé",
    "farfetchd": "Farfetch%27d",
}


def _bulbapedia_url(name: str, display: str) -> str:
    for prefix, slug in _BULBAPEDIA_OVERRIDES.items():
        if name == prefix or name.startswith(prefix + "-"):
            return f"https://bulbapedia.bulbagarden.net/wiki/{slug}_(Pok%C3%A9mon)"
    slug = display.replace(" ", "_")
    return f"https://bulbapedia.bulbagarden.net/wiki/{slug}_(Pok%C3%A9mon)"


# col index → (row key, ascending-by-default)
_SORTABLE = {
    0: ("rank",       True),
    3: ("display",    True),
    4: ("combined",   False),
    5: ("off_impact", False),
    6: ("def_impact", False),
    7: ("spd_impact", False),
}

_ADVERSE_COLORS = {
    "None":          "#a6e3a1",
    "Self-damaging": "#f38ba8",
    "Self-reducing": "#f9e2af",
}

_FEASIBILITY_COLORS = {
    "Easy":    "#a6e3a1",
    "Medium":  "#f9e2af",
    "Hard":    "#fab387",
    "Complex": "#f38ba8",
    "—":       "#6c7086",
}


def _badges(types: list[str], small: bool = False) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    hl = QHBoxLayout(w)
    hl.setContentsMargins(4, 1, 4, 1)
    hl.setSpacing(3)
    for t in types:
        bg, fg = TYPE_COLORS.get(t, ("#888", "#fff"))
        lbl = QLabel(t[:4].upper())
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if small:
            lbl.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:2px;"
                f"padding:0 2px; font-size:9px; font-weight:bold;"
            )
            lbl.setFixedHeight(13)
        else:
            lbl.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:3px;"
                f"padding:0 4px; font-size:10px; font-weight:bold;"
            )
            lbl.setFixedHeight(17)
        hl.addWidget(lbl)
    hl.addStretch()
    return w


class ImpactTableDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Impact Score Browser")
        self.setModal(False)
        self.resize(1150, 700)
        self.setStyleSheet(_STYLE)

        # Pokémon tab state
        self._all_rows: list[dict] = []
        self._visible_rows: list[dict] = []
        self._sort_col = 4
        self._sort_asc = False
        self._clean_mode = False

        # Moves tab state
        self._moves_all_rows: list[dict] = []
        self._moves_sort_col = 3   # Power, descending
        self._moves_sort_asc = False
        self._moves_loaded = False
        self._moves_init_started = False
        self._moves_adoptions_all:   dict[str, int] = {}
        self._moves_adoptions_clean: dict[str, int] = {}

        # Abilities tab state
        self._abilities_all_rows: list[dict] = []
        self._abilities_sort_col = 1   # # Pokémon, descending
        self._abilities_sort_asc = False
        self._abilities_loaded = False
        self._abilities_init_started = False

        self._build_ui()

        if impact_db.is_ready():
            self._load_data()
        else:
            self._status.setText("Building caches — this takes a few minutes on first run…")

    @pyqtSlot()
    def refresh(self):
        """Called when impact_db becomes ready after the dialog was already open."""
        if self._all_rows:
            return
        self._load_data()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)

        self._tabs = QTabWidget()
        outer.addWidget(self._tabs)

        poke_w = QWidget()
        self._build_pokemon_tab(poke_w)
        self._tabs.addTab(poke_w, "Pokémon")

        moves_w = QWidget()
        self._build_moves_tab(moves_w)
        self._tabs.addTab(moves_w, "Moves")

        abil_w = QWidget()
        self._build_abilities_tab(abil_w)
        self._tabs.addTab(abil_w, "Abilities")

        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _build_pokemon_tab(self, container: QWidget):
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name…")
        self._search.textChanged.connect(self._apply_filter)
        top.addWidget(self._search, 1)

        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setFixedHeight(18)
            f.setStyleSheet("color: #45475a;")
            return f

        def _group_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#6c7086; font-size:10px; font-weight:bold; letter-spacing:0.5px;")
            return lbl

        top.addSpacing(10)
        top.addWidget(_sep())
        top.addSpacing(6)
        top.addWidget(_group_label("FILTER"))
        top.addSpacing(6)
        self._hide_leg = QCheckBox("Legendaries")
        self._hide_leg.toggled.connect(self._apply_filter)
        top.addWidget(self._hide_leg)
        self._hide_paradox = QCheckBox("Paradox")
        self._hide_paradox.toggled.connect(self._apply_filter)
        top.addWidget(self._hide_paradox)
        self._only_starters = QCheckBox("Starters only")
        self._only_starters.toggled.connect(self._apply_filter)
        top.addWidget(self._only_starters)

        top.addSpacing(10)
        top.addWidget(_sep())
        top.addSpacing(6)
        top.addWidget(_group_label("SCORING"))
        top.addSpacing(6)
        self._clean_cb = QCheckBox("No recoil")
        self._clean_cb.toggled.connect(self._on_clean_toggled)
        top.addWidget(self._clean_cb)
        self._egg_cb = QCheckBox("Egg mvs")
        self._egg_cb.toggled.connect(self._on_egg_toggled)
        top.addWidget(self._egg_cb)

        self._egg_cap_slider = QSlider(Qt.Orientation.Horizontal)
        self._egg_cap_slider.setRange(1, 4)
        self._egg_cap_slider.setValue(4)
        self._egg_cap_slider.setFixedWidth(56)
        self._egg_cap_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._egg_cap_slider.setTickInterval(1)
        self._egg_cap_slider.setVisible(False)
        self._egg_cap_slider.valueChanged.connect(self._on_egg_cap_changed)
        top.addWidget(self._egg_cap_slider)

        self._egg_cap_lbl = QLabel("4🥚")
        self._egg_cap_lbl.setStyleSheet("color:#cdd6f4; font-size:11px; min-width:24px;")
        self._egg_cap_lbl.setVisible(False)
        top.addWidget(self._egg_cap_lbl)

        layout.addLayout(top)

        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels(
            ["Rank", "Filter", "Types", "Name", "Impact", "Offense", "Defense", "Speed", "Moveset"]
        )
        self._table.setSortingEnabled(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(26)
        self._table.setWordWrap(False)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(4, Qt.SortOrder.DescendingOrder)
        hdr.sectionClicked.connect(self._on_header_click)
        self._table.cellClicked.connect(self._on_cell_clicked)

        self._table.setColumnWidth(0, 52)
        self._table.setColumnWidth(1, 52)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(3, 200)
        self._table.setColumnWidth(4, 66)
        self._table.setColumnWidth(5, 70)
        self._table.setColumnWidth(6, 70)
        self._table.setColumnWidth(7, 66)

        layout.addWidget(self._table)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#6c7086; font-size:11px;")
        layout.addWidget(self._status)

    def _build_moves_tab(self, container: QWidget):
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)
        self._moves_search = QLineEdit()
        self._moves_search.setPlaceholderText("Search moves…")
        self._moves_search.textChanged.connect(self._apply_moves_filter)
        top.addWidget(self._moves_search, 1)
        top.addSpacing(8)

        self._moves_adverse_cb = QComboBox()
        self._moves_adverse_cb.addItems(["All adverse effects", "Safe only", "Self-damaging", "Self-reducing"])
        self._moves_adverse_cb.currentIndexChanged.connect(self._apply_moves_filter)
        top.addWidget(self._moves_adverse_cb)

        self._moves_cat_cb = QComboBox()
        self._moves_cat_cb.addItems(["All categories", "Physical", "Special", "Status"])
        self._moves_cat_cb.currentIndexChanged.connect(self._apply_moves_filter)
        top.addWidget(self._moves_cat_cb)

        layout.addLayout(top)

        self._moves_table = QTableWidget()
        self._moves_table.setColumnCount(11)
        self._moves_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Category", "Power", "Hits", "Accuracy", "PP", "Adverse",
             "# Sets", "Sim Note", "Description"]
        )
        self._moves_table.setSortingEnabled(False)
        self._moves_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._moves_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._moves_table.verticalHeader().setVisible(False)
        self._moves_table.verticalHeader().setDefaultSectionSize(24)
        self._moves_table.setWordWrap(False)

        hdr = self._moves_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(9, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(3, Qt.SortOrder.DescendingOrder)
        hdr.sectionClicked.connect(self._on_moves_header_click)

        self._moves_table.setColumnWidth(0, 170)
        self._moves_table.setColumnWidth(1, 85)
        self._moves_table.setColumnWidth(2, 80)
        self._moves_table.setColumnWidth(3, 55)
        self._moves_table.setColumnWidth(4, 50)
        self._moves_table.setColumnWidth(5, 65)
        self._moves_table.setColumnWidth(6, 42)
        self._moves_table.setColumnWidth(7, 100)
        self._moves_table.setColumnWidth(8, 65)
        self._moves_table.setColumnWidth(9, 190)

        layout.addWidget(self._moves_table)

        self._moves_status = QLabel("Moves load on first visit to this tab.")
        self._moves_status.setStyleSheet("color:#6c7086; font-size:11px;")
        layout.addWidget(self._moves_status)

    def _build_abilities_tab(self, container: QWidget):
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)
        self._abilities_search = QLineEdit()
        self._abilities_search.setPlaceholderText("Search abilities…")
        self._abilities_search.textChanged.connect(self._apply_abilities_filter)
        top.addWidget(self._abilities_search, 1)
        top.addSpacing(8)

        self._abilities_feas_cb = QComboBox()
        self._abilities_feas_cb.addItems(["All feasibility", "Easy", "Medium", "Hard", "Complex"])
        self._abilities_feas_cb.currentIndexChanged.connect(self._apply_abilities_filter)
        top.addWidget(self._abilities_feas_cb)

        layout.addLayout(top)

        self._abilities_table = QTableWidget()
        self._abilities_table.setColumnCount(4)
        self._abilities_table.setHorizontalHeaderLabels(
            ["Ability", "# Pokémon", "Feasibility", "Description"]
        )
        self._abilities_table.setSortingEnabled(False)
        self._abilities_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._abilities_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._abilities_table.verticalHeader().setVisible(False)
        self._abilities_table.verticalHeader().setDefaultSectionSize(24)
        self._abilities_table.setWordWrap(False)

        hdr = self._abilities_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(1, Qt.SortOrder.DescendingOrder)
        hdr.sectionClicked.connect(self._on_abilities_header_click)

        self._abilities_table.setColumnWidth(0, 175)
        self._abilities_table.setColumnWidth(1, 85)
        self._abilities_table.setColumnWidth(2, 90)

        layout.addWidget(self._abilities_table)

        self._abilities_status = QLabel("Abilities load on first visit to this tab.")
        self._abilities_status.setStyleSheet("color:#6c7086; font-size:11px;")
        layout.addWidget(self._abilities_status)

    # ── Tab switching ─────────────────────────────────────────────────────────

    @pyqtSlot(int)
    def _on_tab_changed(self, index: int):
        if index == 1 and not self._moves_loaded:
            if move_info_db.is_ready():
                self._moves_loaded = True
                self._apply_moves_filter()
            elif not self._moves_init_started:
                self._moves_init_started = True
                self._init_moves_tab()
        elif index == 2 and not self._abilities_loaded:
            if ability_info_db.is_ready():
                self._abilities_loaded = True
                self._apply_abilities_filter()
            elif not self._abilities_init_started:
                self._abilities_init_started = True
                self._init_abilities_tab()

    # ── Pokémon tab ───────────────────────────────────────────────────────────

    def _on_clean_toggled(self, checked: bool):
        self._clean_mode = checked
        if impact_db.is_ready():
            self._load_data()

    def _on_egg_cap_changed(self, val: int):
        self._egg_cap_lbl.setText(f"{val}🥚")
        impact_db.set_egg_cap(val)
        self._load_data()

    def _on_egg_toggled(self, checked: bool):
        self._egg_cap_slider.setVisible(checked)
        self._egg_cap_lbl.setVisible(checked)
        if not checked:
            impact_db.set_include_egg(False)
            self._load_data()
            return
        if impact_db.is_egg_ready():
            impact_db.set_include_egg(True)
            self._load_data()
            return
        impact_db.init_egg(
            on_progress=lambda msg: QMetaObject.invokeMethod(
                self._status, "setText",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg),
            ),
            on_ready=lambda: QMetaObject.invokeMethod(
                self, "_on_egg_ready",
                Qt.ConnectionType.QueuedConnection,
            ),
        )
        self._status.setText("Building egg-moves cache…")

    @pyqtSlot()
    def _on_egg_ready(self):
        impact_db.set_include_egg(True)
        self._load_data()

    def _load_data(self):
        # Reset adoptions so Moves tab re-fetches them on next filter call
        self._moves_adoptions_all   = {}
        self._moves_adoptions_clean = {}

        entries  = impact_db.all_entries()
        clean    = self._clean_mode
        sort_key = (lambda x: x[1].get("impact_clean", x[1]["impact"])) if clean \
                   else (lambda x: x[1]["impact"])
        sorted_entries = sorted(entries.items(), key=sort_key, reverse=True)
        self._all_rows = []
        cap = impact_db.get_egg_cap()
        for rank, (name, data) in enumerate(sorted_entries, 1):
            types = data.get("types", [])
            stab_cov: set[str] = set()
            for t in types:
                stab_cov |= OFFENSIVE_CHART.get(t, frozenset())

            if clean:
                raw_moves = data.get("moves_clean", data.get("moves", []))
                active_impact = data.get("impact_clean", data["impact"])
            else:
                raw_moves = impact_db.get_capped_moves(name, cap)
                active_impact = data["impact"]

            self._all_rows.append({
                "rank":       rank,
                "name":       name,
                "display":    name.replace("-", " ").title(),
                "impact":     active_impact,
                "coverage":   data.get("coverage", data["impact"]),
                "speed":      data.get("speed", 0),
                "pct":        data["percentile"],
                "def_score":  data.get("bulk", 0.0),
                "types":      types,
                "legendary":  data.get("legendary", False),
                "paradox":    data.get("paradox", False),
                "starter":    name in _STARTER_POKEMON,
                "stab_cov":   sorted(stab_cov),
                "moves":      raw_moves,
                "outcomes":   data.get("outcomes", {}),
                "move_usage": data.get("move_usage", {}),
            })

        nonleg    = [r for r in self._all_rows if not r["legendary"] and not r["paradox"]]
        impact_vals = [r["impact"]    for r in nonleg if r["impact"]    > 0]
        cov_vals    = [r["coverage"]  for r in nonleg if r["coverage"]  > 0]
        spd_vals    = [r["speed"]     for r in nonleg if r["speed"]     > 0]
        bulk_vals   = [r["def_score"] for r in nonleg if r["def_score"] > 0]
        med_impact = statistics.median(impact_vals) if impact_vals else 1.0
        med_cov    = statistics.median(cov_vals)    if cov_vals    else 1.0
        med_spd    = statistics.median(spd_vals)    if spd_vals    else 1.0
        med_bulk   = statistics.median(bulk_vals)   if bulk_vals   else 1.0
        for row in self._all_rows:
            row["combined"]   = max(row["impact"]    / med_impact, 1e-9)
            row["off_impact"] = max(row["coverage"]  / med_cov,    1e-9)
            row["spd_impact"] = max(row["speed"]     / med_spd,    1e-9)
            row["def_impact"] = max(row["def_score"] / med_bulk,   1e-9)

        self._apply_filter()
        if self._moves_loaded:
            self._apply_moves_filter()

    def _apply_filter(self):
        q = self._search.text().strip().lower()
        hide_leg      = self._hide_leg.isChecked()
        hide_paradox  = self._hide_paradox.isChecked()
        only_starters = self._only_starters.isChecked()
        is_filtered   = bool(q or hide_leg or hide_paradox or only_starters)
        self._table.setColumnHidden(1, not is_filtered)
        rows = [
            r for r in self._all_rows
            if (not q or q in r["name"])
            and (not hide_leg or not r["legendary"])
            and (not hide_paradox or not r["paradox"])
            and (not only_starters or r["starter"])
        ]
        if self._sort_col in _SORTABLE:
            field, asc_default = _SORTABLE[self._sort_col]
            rows.sort(key=lambda r: r[field], reverse=not self._sort_asc)
        self._populate(rows)

    @pyqtSlot(int, int)
    def _on_cell_clicked(self, row: int, col: int):
        if col != 3 or row >= len(self._visible_rows):
            return
        r = self._visible_rows[row]
        QDesktopServices.openUrl(QUrl(_bulbapedia_url(r["name"], r["display"])))

    def _on_header_click(self, col: int):
        if col not in _SORTABLE:
            return
        _, asc_default = _SORTABLE[col]
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = asc_default
        order = (Qt.SortOrder.AscendingOrder if self._sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._table.horizontalHeader().setSortIndicator(col, order)
        self._apply_filter()

    def _populate(self, rows: list[dict]):
        self._visible_rows = list(rows)
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))

        for i, row in enumerate(rows):
            rank_item = QTableWidgetItem(str(row["rank"]))
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            rank_item.setForeground(QColor("#6c7086"))
            self._table.setItem(i, 0, rank_item)

            filt_item = QTableWidgetItem(str(i + 1))
            filt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            filt_item.setForeground(QColor("#cdd6f4"))
            self._table.setItem(i, 1, filt_item)

            self._table.setCellWidget(i, 2, _badges(row["types"]))

            prefix = "★ " if row["legendary"] else ""
            name_item = QTableWidgetItem(prefix + row["display"])
            name_item.setForeground(
                QColor("#cba6f7") if row["legendary"] else QColor("#89b4fa")
            )
            name_item.setToolTip("Click to open Bulbapedia")
            self._table.setItem(i, 3, name_item)

            combined_item = QTableWidgetItem(f"{row['combined']*100:.0f}")
            combined_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            combined_item.setForeground(QColor("#cba6f7"))
            oc = row.get("outcomes", {})
            if oc:
                outcome_line = (
                    f"{oc.get('zdw',0)} ZDW  "
                    f"{oc.get('dw',0)} DW  "
                    f"{oc.get('dl',0)} DL  "
                    f"{oc.get('zdl',0)} ZDL\n"
                )
            else:
                outcome_line = ""
            mode_note = (
                "Clean mode: recoil and self-reducing moves excluded."
                if self._clean_mode else
                "All moves included (recoil and self-reducing moves allowed)."
            )
            combined_item.setToolTip(
                outcome_line +
                "Battle score: Σ (A_hp − B_hp + 1) / 2 across all non-legendary matchups\n"
                f"Normalized to median non-legendary. 100 = median, 200 = twice median.\n"
                f"{mode_note}"
            )
            self._table.setItem(i, 4, combined_item)

            off_item = QTableWidgetItem(f"{row['off_impact']*100:.0f}")
            off_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            off_item.setForeground(QColor("#f9e2af"))
            off_item.setToolTip("coverage / median coverage  (excl. legendaries & paradox)")
            self._table.setItem(i, 5, off_item)

            def_item = QTableWidgetItem(f"{row['def_impact']*100:.0f}")
            def_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            def_item.setForeground(QColor("#a6e3a1"))
            def_item.setToolTip("bulk / median bulk  (excl. legendaries & paradox)\nbulk = Σ sqrt(hits-to-KO) across 18 types")
            self._table.setItem(i, 6, def_item)

            spd_item = QTableWidgetItem(f"{row['spd_impact']*100:.0f}")
            spd_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            spd_item.setForeground(QColor("#89b4fa"))
            spd_item.setToolTip("base speed / median base speed  (excl. legendaries & paradox)")
            self._table.setItem(i, 7, spd_item)

            usage = row.get("move_usage", {})
            move_names = " · ".join(
                m["name"].replace("-", " ").title()
                for m in row["moves"]
            )
            move_item = QTableWidgetItem(move_names)
            move_item.setForeground(QColor("#a6adc8"))
            move_item.setToolTip("\n".join(
                "{name}  [{t}]  {bp} BP{u}".format(
                    name=m["name"].replace("-", " ").title(),
                    t=m["type"],
                    bp=m["power"],
                    u=f"  ·  best in {usage[m['name']]} matchups" if m["name"] in usage else "",
                )
                for m in row["moves"]
            ))
            self._table.setItem(i, 8, move_item)

        self._status.setText(f"{len(rows):,} Pokémon")

    # ── Moves tab ─────────────────────────────────────────────────────────────

    def _init_moves_tab(self):
        def on_prog(msg: str):
            QMetaObject.invokeMethod(
                self._moves_status, "setText",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg),
            )
            if move_info_db.is_ready():
                QMetaObject.invokeMethod(
                    self, "_finish_moves_tab",
                    Qt.ConnectionType.QueuedConnection,
                )
        self._moves_status.setText("Fetching moves…")
        move_info_db.init(on_progress=on_prog)

    @pyqtSlot()
    def _finish_moves_tab(self):
        if self._moves_loaded:
            return
        self._moves_loaded = True
        self._apply_moves_filter()

    def _apply_moves_filter(self):
        if not move_info_db.is_ready():
            return
        if not self._moves_all_rows:
            self._moves_all_rows = list(move_info_db.all_entries().values())
        if impact_db.is_ready() and not self._moves_adoptions_all:
            self._moves_adoptions_all, self._moves_adoptions_clean = impact_db.get_move_adoptions()

        q           = self._moves_search.text().strip().lower()
        adv_choice  = self._moves_adverse_cb.currentText()
        cat_choice  = self._moves_cat_cb.currentText()

        rows = [
            r for r in self._moves_all_rows
            if (not q or q in r["name"] or q in r["display"].lower())
            and (adv_choice == "All adverse effects"
                 or (adv_choice == "Safe only" and r["adverse"] == "None")
                 or r["adverse"] == adv_choice)
            and (cat_choice == "All categories"
                 or r["category"].lower() == cat_choice.lower())
        ]

        adopt_all   = self._moves_adoptions_all
        adopt_clean = self._moves_adoptions_clean
        # col → (field, asc_default); None = special-cased below
        _moves_sort_keys = {
            0: ("display",  True),
            1: ("type",     True),
            2: ("category", True),
            3: ("power",    False),
            4: ("min_hits", False),
            5: ("accuracy", False),
            6: ("pp",       False),
            7: ("adverse",  True),
            8: None,   # # Sets — sorted by adoption count
            9: ("excluded", True),
        }
        if self._moves_sort_col == 8:
            rows.sort(
                key=lambda r: adopt_all.get(r["name"], 0),
                reverse=not self._moves_sort_asc,
            )
        else:
            entry = _moves_sort_keys.get(self._moves_sort_col)
            key_field = entry[0] if entry else "display"
            rows.sort(
                key=lambda r: r.get(key_field, 0) if isinstance(r.get(key_field, 0), int)
                              else (r.get(key_field) or "").lower(),
                reverse=not self._moves_sort_asc,
            )
        self._populate_moves(rows, adopt_all, adopt_clean)

    def _populate_moves(self, rows: list[dict],
                        adopt_all: dict[str, int],
                        adopt_clean: dict[str, int]):
        t = self._moves_table
        t.setRowCount(0)
        t.setRowCount(len(rows))

        _cat_colors = {"Physical": "#fab387", "Special": "#89b4fa", "Status": "#a6adc8"}

        for i, row in enumerate(rows):
            name_item = QTableWidgetItem(row["display"])
            name_item.setForeground(QColor("#cdd6f4"))
            t.setItem(i, 0, name_item)

            t.setCellWidget(i, 1, _badges([row["type"]], small=True))

            cat = row["category"].title()
            cat_item = QTableWidgetItem(cat)
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            cat_item.setForeground(QColor(_cat_colors.get(cat, "#cdd6f4")))
            t.setItem(i, 2, cat_item)

            pwr = row["power"]
            pwr_item = QTableWidgetItem(str(pwr) if pwr else "—")
            pwr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            pwr_item.setForeground(QColor("#cba6f7"))
            t.setItem(i, 3, pwr_item)

            min_h = row.get("min_hits") or 0
            max_h = row.get("max_hits") or 0
            if min_h and max_h:
                hits_text = f"{min_h}×" if min_h == max_h else f"{min_h}-{max_h}×"
                avg = (min_h + max_h) / 2.0
                hits_tip = f"Hits {min_h}-{max_h} times; avg ×{avg:.1f} effective power in sim"
                hits_color = "#f9e2af"
            else:
                hits_text = "—"
                hits_tip  = "Single hit"
                hits_color = "#6c7086"
            hits_item = QTableWidgetItem(hits_text)
            hits_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            hits_item.setForeground(QColor(hits_color))
            hits_item.setToolTip(hits_tip)
            t.setItem(i, 4, hits_item)

            acc = row["accuracy"]
            acc_item = QTableWidgetItem(f"{acc}%" if acc else "—")
            acc_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            acc_item.setForeground(QColor("#cdd6f4"))
            t.setItem(i, 5, acc_item)

            pp_item = QTableWidgetItem(str(row["pp"]) if row["pp"] else "—")
            pp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            pp_item.setForeground(QColor("#6c7086"))
            t.setItem(i, 6, pp_item)

            adv = row["adverse"]
            adv_item = QTableWidgetItem(adv)
            adv_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            adv_item.setForeground(QColor(_ADVERSE_COLORS.get(adv, "#cdd6f4")))
            t.setItem(i, 7, adv_item)

            mn = row["name"]
            n_all   = adopt_all.get(mn, 0)
            n_clean = adopt_clean.get(mn, 0)
            sets_text = str(n_all) if n_all else "—"
            sets_item = QTableWidgetItem(sets_text)
            sets_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sets_item.setForeground(QColor("#a6e3a1" if n_all else "#6c7086"))
            sets_item.setToolTip(
                f"All mode (incl. recoil/stat-drop): {n_all} forms\n"
                f"Clean mode (no recoil/stat-drop):  {n_clean} forms"
            )
            t.setItem(i, 8, sets_item)

            excl = row.get("excluded", "")
            excl_item = QTableWidgetItem(excl if excl else "—")
            excl_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            excl_item.setForeground(
                QColor("#f38ba8") if excl else QColor("#a6e3a1")
            )
            t.setItem(i, 9, excl_item)

            eff_item = QTableWidgetItem(row["effect"])
            eff_item.setForeground(QColor("#a6adc8"))
            t.setItem(i, 10, eff_item)

        self._moves_status.setText(f"{len(rows):,} moves")

    def _on_moves_header_click(self, col: int):
        if col not in range(10):  # cols 0-9 are sortable; 10 (Description) is not
            return
        if self._moves_sort_col == col:
            self._moves_sort_asc = not self._moves_sort_asc
        else:
            self._moves_sort_col = col
            self._moves_sort_asc = col in {0, 1, 2, 7, 9}  # strings default ascending
        order = (Qt.SortOrder.AscendingOrder if self._moves_sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._moves_table.horizontalHeader().setSortIndicator(col, order)
        self._apply_moves_filter()

    # ── Abilities tab ─────────────────────────────────────────────────────────

    def _init_abilities_tab(self):
        def on_prog(msg: str):
            QMetaObject.invokeMethod(
                self._abilities_status, "setText",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg),
            )
            if ability_info_db.is_ready():
                QMetaObject.invokeMethod(
                    self, "_finish_abilities_tab",
                    Qt.ConnectionType.QueuedConnection,
                )
        self._abilities_status.setText("Fetching abilities…")
        ability_info_db.init(on_progress=on_prog)

    @pyqtSlot()
    def _finish_abilities_tab(self):
        if self._abilities_loaded:
            return
        self._abilities_loaded = True
        self._apply_abilities_filter()

    def _apply_abilities_filter(self):
        if not ability_info_db.is_ready():
            return
        if not self._abilities_all_rows:
            self._abilities_all_rows = list(ability_info_db.all_entries().values())

        q           = self._abilities_search.text().strip().lower()
        feas_choice = self._abilities_feas_cb.currentText()

        rows = [
            r for r in self._abilities_all_rows
            if (not q or q in r["name"] or q in r["display"].lower()
                or q in r["effect"].lower())
            and (feas_choice == "All feasibility" or r["feasibility"] == feas_choice)
        ]

        _abil_sort_keys = {
            0: ("display",     True),
            1: ("total",       False),
            2: ("feasibility", True),
        }
        key_field, _ = _abil_sort_keys.get(self._abilities_sort_col, ("total", False))
        rows.sort(
            key=lambda r: r[key_field] if isinstance(r[key_field], int)
                          else (r[key_field] or "").lower(),
            reverse=not self._abilities_sort_asc,
        )
        self._populate_abilities(rows)

    def _populate_abilities(self, rows: list[dict]):
        t = self._abilities_table
        t.setRowCount(0)
        t.setRowCount(len(rows))

        for i, row in enumerate(rows):
            name_item = QTableWidgetItem(row["display"])
            name_item.setForeground(QColor("#89b4fa"))
            t.setItem(i, 0, name_item)

            count_item = QTableWidgetItem(str(row["total"]))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            count_item.setForeground(QColor("#cdd6f4"))
            count_item.setToolTip(
                f"Primary (slot 1):        {row['primary']}\n"
                f"Secondary (slot 2):      {row['secondary']}\n"
                f"Hidden ability (slot 3): {row['hidden']}"
            )
            t.setItem(i, 1, count_item)

            feas = row["feasibility"]
            feas_item = QTableWidgetItem(feas)
            feas_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            feas_item.setForeground(QColor(_FEASIBILITY_COLORS.get(feas, "#cdd6f4")))
            feas_item.setToolTip(
                "Easy    — always-on passive multiplier or type immunity\n"
                "Medium  — triggers on a single condition (entry, weather, HP threshold)\n"
                "Hard    — accumulates over turns or has multi-step trigger\n"
                "Complex — copies, transforms, or suppresses other abilities/types\n"
                "(heuristic — may be wrong for edge cases)"
            )
            t.setItem(i, 2, feas_item)

            eff_item = QTableWidgetItem(row["effect"])
            eff_item.setForeground(QColor("#a6adc8"))
            t.setItem(i, 3, eff_item)

        self._abilities_status.setText(f"{len(rows):,} abilities")

    def _on_abilities_header_click(self, col: int):
        if col not in {0, 1, 2}:
            return
        if self._abilities_sort_col == col:
            self._abilities_sort_asc = not self._abilities_sort_asc
        else:
            self._abilities_sort_col = col
            self._abilities_sort_asc = col in {0, 2}  # strings default ascending
        order = (Qt.SortOrder.AscendingOrder if self._abilities_sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._abilities_table.horizontalHeader().setSortIndicator(col, order)
        self._apply_abilities_filter()
