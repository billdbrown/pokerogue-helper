from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt

from weakness_calc import ALL_TYPES

TEAM_SIZE = 6


def _fmt_opp_list(names: list, per_line: int = 6, cap: int = 90) -> str:
    """Pretty, wrapped 'Charizard, Blastoise, …' opponent list for a tooltip."""
    pretty = [n.replace("-", " ").title() for n in names]
    overflow = ""
    if cap and len(pretty) > cap:
        overflow = f"\n…and {len(pretty) - cap} more"
        pretty = pretty[:cap]
    lines = [", ".join(pretty[i:i + per_line]) for i in range(0, len(pretty), per_line)]
    return "\n".join(lines) + overflow

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

_TAB_STYLE = """
    QTabWidget::pane {
        background: #1e1e2e;
        border: none;
        border-top: 1px solid #313244;
    }
    QTabBar {
        background: #181825;
    }
    QTabBar::tab {
        background: #181825;
        color: #6c7086;
        padding: 4px 8px;
        font-size: 16px;
        font-weight: bold;
        letter-spacing: 0.5px;
        border: none;
        border-right: 1px solid #313244;
        min-width: 28px;
    }
    QTabBar::tab:selected {
        background: #1e1e2e;
        color: #cdd6f4;
        border-bottom: 2px solid #89b4fa;
    }
    QTabBar::tab:hover:!selected {
        color: #a6adc8;
        background: #1e1e2e;
    }
"""


class AnalysisPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(0)
        self.setStyleSheet("background: #1e1e2e;")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._build_ui()

    # ── Construction ──────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QLabel("Analysis")
        hdr.setStyleSheet(
            "color:#cdd6f4; font-size:13px; font-weight:bold;"
            " padding: 6px 8px 4px 8px;"
            " border-top: 1px solid #313244;"
        )
        root.addWidget(hdr)

        # Team coverage summary — moved here from the team strip so the Pokémon
        # slots get that vertical real estate back. Values pushed by the team panel.
        cov_row = QHBoxLayout()
        cov_row.setContentsMargins(8, 0, 8, 2)
        cov_row.setSpacing(4)
        chk_hdr = QLabel("Potential")
        chk_hdr.setStyleSheet("color:#6c7086; font-size:13px; background:transparent;")
        self._checks_lbl = QLabel("—")
        self._checks_lbl.setStyleSheet("color:#cba6f7; font-size:24px; font-weight:bold; background:transparent;")
        cnt_hdr = QLabel("Current")
        cnt_hdr.setStyleSheet("color:#6c7086; font-size:13px; background:transparent;")
        self._counters_lbl = QLabel("—")
        self._counters_lbl.setStyleSheet("color:#a6e3a1; font-size:24px; font-weight:bold; background:transparent;")
        cov_row.addWidget(chk_hdr)
        cov_row.addSpacing(3)
        cov_row.addWidget(self._checks_lbl)
        cov_row.addStretch()
        cov_row.addWidget(cnt_hdr)
        cov_row.addSpacing(3)
        cov_row.addWidget(self._counters_lbl)
        root.addLayout(cov_row)

        self._uncovered_lbl = QLabel("")
        self._uncovered_lbl.setStyleSheet("color:#6c7086; font-size:11px; padding: 0 8px 2px 8px; background:transparent;")
        self._uncovered_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._uncovered_lbl)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(_TAB_STYLE)
        self._tabs.setDocumentMode(True)
        self._tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        self._weak_link_area = self._add_tab("COVERAGE")
        self._danger_area    = self._add_tab("DANGER")

        root.addWidget(self._tabs)

        self._init_empty()

    def _add_tab(self, label: str) -> QVBoxLayout:
        page = QWidget()
        page.setStyleSheet("background: #1e1e2e;")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)
        content = QVBoxLayout()
        content.setSpacing(3)
        outer.addLayout(content)
        outer.addStretch()
        self._tabs.addTab(page, label)
        return content

    def _init_empty(self):
        self._rebuild_weakest_link([None] * TEAM_SIZE, None, None, [None] * TEAM_SIZE)
        self._rebuild_danger(None)

    # ── Public API ────────────────────────────────────────────────────────────

    def update_coverage(self, checks, counters, uncovered, pool_size=None):
        """Set the team checks/counters coverage (computed and pushed by the team panel).

        checks/counters are absolute opponent counts shown as raw counts; pool_size
        is the total opponent pool, surfaced in the tooltip for context.
        Pass uncovered=None (with falsy checks/counters) to clear for an empty team.
        """
        suffix = f" of {pool_size}" if pool_size else ""
        self._checks_lbl.setText(str(int(checks)) if checks else "—")
        self._checks_lbl.setToolTip(f"Team beats {int(checks)}{suffix} opponents "
                                    "with ideal movesets" if checks else "")
        self._counters_lbl.setText(str(int(counters)) if counters else "—")
        self._counters_lbl.setToolTip(f"Team beats {int(counters)}{suffix} opponents "
                                      "with currently equipped moves" if counters else "")

        if not checks and not counters and not uncovered:
            self._uncovered_lbl.setText("")
            self._uncovered_lbl.setToolTip("")
            return

        n = len(uncovered) if uncovered else 0
        if n == 0:
            self._uncovered_lbl.setText("All opponents checked")
            self._uncovered_lbl.setStyleSheet(
                "color:#a6e3a1; font-size:11px; padding: 0 8px 2px 8px; background:transparent;")
            self._uncovered_lbl.setToolTip("")
        else:
            self._uncovered_lbl.setText(f"Uncovered: {n}")
            self._uncovered_lbl.setStyleSheet(
                "color:#f38ba8; font-size:11px; padding: 0 8px 2px 8px; background:transparent;")
            preview = uncovered[:60]
            more = f"\n… and {n - len(preview)} more" if n > len(preview) else ""
            self._uncovered_lbl.setToolTip(
                "\n".join(name.replace("-", " ").title() for name in preview) + more)

    def set_current(self, current, pool_size):
        """Update only the 'Current' counters label (async result from the team panel)."""
        if not current:
            self._counters_lbl.setText("—")
            self._counters_lbl.setToolTip("")
        else:
            self._counters_lbl.setText(str(int(current)))
            suffix = f" of {pool_size}" if pool_size else ""
            self._counters_lbl.setToolTip(
                f"Team beats {int(current)}{suffix} opponents with currently equipped moves")

    def on_analysis_ready(self, payload):
        (full, partial, gaps, suggestions, danger,
         slot_stats, weakest_slot, replace_sugg, pref_type,
         team_weaknesses, team_names) = payload
        self._rebuild_weakest_link(slot_stats, weakest_slot, replace_sugg, team_names)
        self._rebuild_danger(danger)

    # ── Tab content builders ──────────────────────────────────────────────────

    def _rebuild_weakest_link(self, slot_stats, weakest_slot, replace_sugg, team_names):
        self._clear_layout(self._weak_link_area)
        filled = [(s, st) for s, st in enumerate(slot_stats) if st is not None]
        if not filled:
            self._weak_link_area.addWidget(self._placeholder("Add Pokémon to see coverage distribution"))
            return

        # Legend so the two counts are self-explanatory.
        legend = QLabel("✓ wins · ⚔ exclusive (best on team)")
        legend.setStyleSheet("color:#6c7086; font-size:11px; background:transparent;")
        self._weak_link_area.addWidget(legend)

        # Rank by exclusive coverage, then total wins.
        total_excl = sum(st[3] for _, st in filled) or 1
        ranked = sorted(filled, key=lambda x: (x[1][3], x[1][2]), reverse=True)
        for s, (bst, bst_pct, wins, owned, owned_names) in ranked:
            name = (team_names[s] or f"Slot {s + 1}").capitalize()

            # Colour by share of the team's total exclusive coverage:
            # blue (carries the team) > green > yellow > red (weak link).
            excl_pct = round(owned / total_excl * 100)
            cov_color = ("#38bdf8" if excl_pct >= 30 else
                         "#a6e3a1" if excl_pct >= 15 else
                         "#f9e2af" if excl_pct >= 10 else
                         "#f38ba8")

            row = QHBoxLayout()
            row.setSpacing(6)

            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(f"color:{cov_color}; font-size:18px; font-weight:bold;")

            wins_lbl = QLabel(f"✓{wins}")
            wins_lbl.setStyleSheet("color:#a6adc8; font-size:18px;")
            wins_lbl.setToolTip(f"Wins vs {wins} opponents in simulation (this member alone).")

            cov_lbl = QLabel(f"⚔{owned}")
            cov_lbl.setStyleSheet(f"color:{cov_color}; font-size:18px; font-weight:bold;")
            if owned_names:
                tip = (f"Best on the team vs {owned} opponent(s) — {excl_pct}% of the "
                       f"team's exclusive coverage:\n" + _fmt_opp_list(owned_names))
                name_lbl.setToolTip(tip)
                cov_lbl.setToolTip(tip)

            row.addWidget(name_lbl)
            row.addStretch()
            row.addWidget(wins_lbl)
            row.addWidget(cov_lbl)
            self._weak_link_area.addLayout(row)

    def _rebuild_danger(self, danger):
        self._clear_layout(self._danger_area)
        if danger is None:
            self._danger_area.addWidget(self._placeholder("Enter moves to see danger combos"))
            return
        if danger:
            for t1, t2, weak_count in danger:
                row = QHBoxLayout()
                row.setSpacing(3)
                warn = QLabel("⚠")
                warn.setStyleSheet("color:#f38ba8; font-size:18px;")
                row.addWidget(warn)
                row.addWidget(self._make_type_badge(t1))
                sep = QLabel("/")
                sep.setStyleSheet("color:#6c7086; font-size:18px;")
                row.addWidget(sep)
                row.addWidget(self._make_type_badge(t2))
                count_color = (
                    "#f38ba8" if weak_count >= 2 else
                    "#fab387" if weak_count == 1 else
                    "#6c7086"
                )
                count_lbl = QLabel(f"{weak_count}×weak")
                count_lbl.setStyleSheet(f"color:{count_color}; font-size:18px;")
                row.addWidget(count_lbl)
                row.addStretch()
                self._danger_area.addLayout(row)
        else:
            self._danger_area.addWidget(self._placeholder("No uncovered dual-type combos"))

    def _rebuild_weakness_grid(self, team_weaknesses):
        self._clear_layout(self._weak_area)

        if not any(w is not None for w in team_weaknesses):
            self._weak_area.addWidget(self._placeholder("Add Pokémon to see weaknesses"))
            return

        type_counts = {}
        for t in ALL_TYPES:
            w2 = sum(1 for w in team_weaknesses if w is not None and w.get(t, 1.0) >= 2.0)
            w4 = sum(1 for w in team_weaknesses if w is not None and w.get(t, 1.0) >= 4.0)
            if w2 > 0:
                type_counts[t] = (w2, w4)

        if not type_counts:
            self._weak_area.addWidget(self._placeholder("No shared weaknesses"))
            return

        entries = sorted(type_counts.items(), key=lambda x: (-x[1][0], -x[1][1]))
        for i in range(0, len(entries), 3):
            row = QHBoxLayout()
            row.setSpacing(2)
            for type_name, (w2, w4) in entries[i:i + 3]:
                color = (
                    "#f38ba8" if w2 >= 4 else
                    "#fab387" if w2 >= 3 else
                    "#f9e2af" if w2 == 2 else
                    "#6c7086"
                )
                count_str = f"{w2}" + (" 4×" if w4 else "")
                count_lbl = QLabel(count_str)
                count_lbl.setStyleSheet(f"color:{color}; font-size:19px; font-weight:bold;")
                row.addWidget(self._make_type_badge(type_name))
                row.addWidget(count_lbl)
                row.addSpacing(6)
            row.addStretch()
            self._weak_area.addLayout(row)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_type_badge(self, type_name: str) -> QLabel:
        bg, fg = TYPE_COLORS.get(type_name, ("#888", "#fff"))
        lbl = QLabel(type_name[:4].upper())
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:3px;"
            f"padding:0px 3px; font-size:19px; font-weight:bold;"
        )
        lbl.setFixedHeight(28)
        return lbl

    def _placeholder(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#45475a; font-size:19px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        return lbl

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
