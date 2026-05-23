"""Impact Score browser — searchable, sortable table dialog."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QWidget, QCheckBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

import impact_db
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

# col index → (row key, ascending-by-default)
_SORTABLE = {
    0: ("rank",    True),
    1: ("display", True),
    2: ("impact",   False),
    3: ("pct",      False),
    4: ("coverage", False),
    5: ("speed",   False),
}


def _pct_color(pct: int) -> str:
    if pct >= 75: return "#a6e3a1"
    if pct >= 50: return "#f9e2af"
    if pct >= 25: return "#fab387"
    return "#f38ba8"


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
        self.resize(1150, 680)
        self.setStyleSheet(_STYLE)

        self._all_rows: list[dict] = []
        self._sort_col = 2      # score column
        self._sort_asc = False  # descending

        self._build_ui()

        if impact_db.is_ready():
            self._load_data()
        else:
            self._status.setText("Impact cache loading…")

    def refresh(self):
        """Called when impact_db becomes ready after the dialog was already open."""
        if self._all_rows:
            return
        self._load_data()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name…")
        self._search.textChanged.connect(self._apply_filter)
        top.addWidget(self._search, 1)
        self._hide_leg = QCheckBox("Hide legendaries")
        self._hide_leg.toggled.connect(self._apply_filter)
        top.addWidget(self._hide_leg)
        self._hide_paradox = QCheckBox("Hide paradox")
        self._hide_paradox.toggled.connect(self._apply_filter)
        top.addWidget(self._hide_paradox)
        self._only_starters = QCheckBox("Starters only")
        self._only_starters.toggled.connect(self._apply_filter)
        top.addWidget(self._only_starters)
        layout.addLayout(top)

        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels(
            ["Rank", "Name", "Impact", "%ile", "Coverage", "Spd", "Types", "Moveset"]
        )
        self._table.setSortingEnabled(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(26)
        self._table.setWordWrap(False)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(2, Qt.SortOrder.DescendingOrder)
        hdr.sectionClicked.connect(self._on_header_click)

        self._table.setColumnWidth(0, 52)
        self._table.setColumnWidth(1, 200)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 52)
        self._table.setColumnWidth(4, 100)
        self._table.setColumnWidth(5, 48)
        self._table.setColumnWidth(6, 110)

        layout.addWidget(self._table)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#6c7086; font-size:11px;")
        layout.addWidget(self._status)

    # ── Data ──────────────────────────────────────────────────────────────────

    def _load_data(self):
        entries = impact_db.all_entries()
        sorted_entries = sorted(entries.items(), key=lambda x: x[1]["impact"], reverse=True)
        self._all_rows = []
        for rank, (name, data) in enumerate(sorted_entries, 1):
            types = data.get("types", [])
            stab_cov: set[str] = set()
            for t in types:
                stab_cov |= OFFENSIVE_CHART.get(t, frozenset())
            raw_moves = data.get("moves", [])
            self._all_rows.append({
                "rank":      rank,
                "name":      name,
                "display":   name.replace("-", " ").title(),
                "impact":    data["impact"],
                "coverage":  data.get("coverage", data["impact"]),
                "speed":     data.get("speed", 0),
                "pct":       data["percentile"],
                "types":     types,
                "legendary": data.get("legendary", False),
                "paradox":   data.get("paradox", False),
                "starter":   name in _STARTER_POKEMON,
                "stab_cov":  sorted(stab_cov),
                "moves":     raw_moves,
            })
        self._apply_filter()

    def _apply_filter(self):
        q = self._search.text().strip().lower()
        hide_leg     = self._hide_leg.isChecked()
        hide_paradox = self._hide_paradox.isChecked()
        only_starters = self._only_starters.isChecked()
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

    # ── Table population ──────────────────────────────────────────────────────

    def _populate(self, rows: list[dict]):
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))

        for i, row in enumerate(rows):
            # Rank
            rank_item = QTableWidgetItem(str(row["rank"]))
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            rank_item.setForeground(QColor("#6c7086"))
            self._table.setItem(i, 0, rank_item)

            # Name
            prefix = "★ " if row["legendary"] else ""
            name_item = QTableWidgetItem(prefix + row["display"])
            if row["legendary"]:
                name_item.setForeground(QColor("#cba6f7"))
            self._table.setItem(i, 1, name_item)

            # Score
            score_item = QTableWidgetItem(f"{row['impact']:,.0f}")
            score_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(i, 2, score_item)

            # %ile
            pct = row["pct"]
            pct_item = QTableWidgetItem(f"p{pct}")
            pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            pct_item.setForeground(QColor(_pct_color(pct)))
            self._table.setItem(i, 3, pct_item)

            # Impact (raw, before speed weighting)
            impact_item = QTableWidgetItem(f"{row['coverage']:,.0f}")
            impact_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            impact_item.setForeground(QColor("#6c7086"))
            impact_item.setToolTip("Raw type coverage score before speed weighting")
            self._table.setItem(i, 4, impact_item)

            # Speed
            spd_item = QTableWidgetItem(str(row["speed"]))
            spd_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            spd_item.setForeground(QColor("#89b4fa"))
            self._table.setItem(i, 5, spd_item)

            # Types
            self._table.setCellWidget(i, 6, _badges(row["types"]))

            # Moveset (stretches to fill remaining width)
            move_names = " · ".join(
                m["name"].replace("-", " ").title()
                for m in row["moves"]
            )
            move_item = QTableWidgetItem(move_names)
            move_item.setForeground(QColor("#a6adc8"))
            tooltip = "\n".join(
                f"{m['name'].replace('-', ' ').title()}  [{m['type']}]  {m['power']} BP"
                for m in row["moves"]
            )
            move_item.setToolTip(tooltip)
            self._table.setItem(i, 7, move_item)

        self._status.setText(f"{len(rows):,} Pokémon")
