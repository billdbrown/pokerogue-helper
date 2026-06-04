"""TeamBuilderPanel — shown on the left sidebar when the game's starter-select
screen is active. Suggests optimal starting teams given the player's unlocked
Pokemon and a point budget.

Two modes:
  COVERAGE — maximises team_score (type coverage across 171 pairings). Rewards
             complementary, diverse teams.
  QUALITY  — maximises sum of individual impact scores. Shows the strongest
             Pokemon you can afford, one per team size, ignoring type synergy.
"""

import itertools
import re
import threading

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QScrollArea, QSizePolicy, QFrame, QPushButton, QSlider,
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QObject, QMetaObject, Q_ARG

import impact_db
from overlay import _pct_color, _impact_tooltip

_BUDGET_DEFAULT = 10
_MAX_TEAM      = 6
_SEARCH_TOP_N  = 25
_SHOW_TEAMS    = 5
_MAX_MEGAS     = 1    # only one Pokémon can hold a Mega Stone per battle
_MEGA_TOP_N    = 8    # strongest Megas to seed the coverage search with


def _is_mega(name: str) -> bool:
    return "-mega" in (name or "")

_COST_COLORS = {1: "#a6e3a1", 2: "#a6e3a1", 3: "#f9e2af", 4: "#f9e2af",
                5: "#fab387", 6: "#fab387", 7: "#f38ba8", 8: "#f38ba8",
                9: "#f38ba8", 10: "#f38ba8"}

_STYLE = """
    QWidget#tbpanel  { background: #1e1e2e; }
    QWidget#ctrl_bar { background: #181825; border-bottom: 1px solid #313244; }
    QScrollArea      { background: #1e1e2e; border: none; }
    QScrollBar:vertical { background: #181825; width: 6px; border-radius: 3px; }
    QScrollBar::handle:vertical { background: #45475a; border-radius: 3px; }
    QCheckBox        { color: #a6adc8; font-size: 11px; spacing: 6px; }
    QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #45475a;
                           border-radius: 2px; background: #313244; }
    QCheckBox::indicator:checked { background: #89b4fa; border-color: #89b4fa; }
    QPushButton#mode_btn {
        background: #313244; color: #6c7086; border: 1px solid #45475a;
        border-radius: 3px; padding: 2px 7px; font-size: 10px; font-weight: bold;
    }
    QPushButton#mode_btn:checked {
        background: #89b4fa; color: #1e1e2e; border-color: #89b4fa;
    }
"""

_SIZE_LABELS = ["", "SOLO", "DUO", "TRIO", "QUAD", "QUINT", "FULL"]


# ── Background worker ─────────────────────────────────────────────────────────

class _Worker(QObject):
    # coverage mode: list of (team_score, [info_dict, ...])
    # quality mode:  list of (size, [info_dict, ...])  — one per team size
    done = pyqtSignal(list, str)   # results, mode

    def __init__(self, unlocked_ids, won_ids, exclude_beaten, budget, mode, fixed_infos,
                 incl_legendary=False, value_reductions=None, egg_moves_owned=None):
        super().__init__()
        self._unlocked        = set(unlocked_ids) if unlocked_ids else set()
        self._won             = set(won_ids)
        self._exclude         = exclude_beaten
        self._budget          = budget
        self._mode            = mode
        self._fixed           = fixed_infos   # already-selected starters (locked in)
        self._incl_legendary  = incl_legendary
        self._value_reductions = value_reductions or {}
        self._egg_moves_owned  = egg_moves_owned or {}
        # Megas the player has already pinned count against the one-mega cap.
        self._fixed_mega = sum(1 for f in self._fixed if _is_mega(f.get("final_evo", "")))

    # ── pool ──────────────────────────────────────────────────────────────────

    def _build_pool(self) -> list[dict]:
        idx = impact_db.starters_index()
        fixed_evos = {f["final_evo"] for f in self._fixed}
        fixed_sids = {f.get("sid") for f in self._fixed}
        pool = []
        for sid, info in idx.items():
            if self._unlocked and sid not in self._unlocked:
                continue
            if self._exclude and sid in self._won:
                continue
            if sid in fixed_sids:
                continue   # species already locked in (in any form)
            if info["cost"] > self._budget:
                continue
            final = info["final_evo"]
            if final in fixed_evos:
                continue   # already locked in
            base_cost  = info["cost"]
            reduction  = self._value_reductions.get(sid, 0)
            eff_cost   = max(1, base_cost - reduction)
            bitmask    = self._egg_moves_owned.get(sid, 0)
            # starters_index picks the strongest final evo, which is the Mega when
            # one exists. Offer the non-mega form alongside it so the species can
            # still be fielded once the team's single Mega slot is spoken for.
            forms = [final]
            if _is_mega(final):
                base_form = re.sub(r"-mega.*$", "", final)
                if base_form and base_form != final:
                    forms.append(base_form)
            for form in forms:
                entry = impact_db.get(form)
                if not entry:
                    continue
                if not self._incl_legendary and entry.get("legendary"):
                    continue
                # Egg-aware Impact: factor in the specific egg moves the player owns.
                disp, raw, boosted = impact_db.egg_aware_score(form, info["name"], bitmask)
                pool.append({
                    "sid":        sid,
                    "name":       info["name"],
                    "final_evo":  form,
                    "cost":       eff_cost,
                    "discounted": reduction > 0,
                    "impact":     raw,            # raw battle score (egg-aware) — drives ranking
                    "score":      disp,           # display Impact number
                    "egg_boosted": boosted,
                    "percentile": entry.get("percentile", 0),
                    "is_mega":    _is_mega(form),
                })
        pool.sort(key=lambda x: x["impact"], reverse=True)
        return pool

    # ── coverage mode ─────────────────────────────────────────────────────────

    def run(self):
        # Compute results inside the guard, but emit OUTSIDE it. The done signal is
        # delivered on this thread (DirectConnection), so emit() synchronously runs
        # the panel's card rendering — keeping it in the try would mask any render
        # error as a worker failure and wrongly fall back to an empty result.
        try:
            if not impact_db.is_ready() or not impact_db.starters_index():
                self.done.emit([], self._mode)
                return
            pool = self._build_pool()
            if not pool and not self._fixed:
                self.done.emit([], self._mode)
                return
            results = (self._run_quality(pool) if self._mode == "quality"
                       else self._run_coverage(pool))
        except Exception as e:
            print(f"[team_builder] worker error: {e}")
            import traceback; traceback.print_exc()
            self.done.emit([], self._mode)
            return

        self.done.emit(results, self._mode)

    def _run_coverage(self, pool: list) -> list:
        # Seed the search with the strongest non-mega forms plus a handful of the
        # best Megas. Keeping non-mega forms in the slice guarantees there are
        # legal fills once the one allowed Mega is placed (an all-Mega slice would
        # leave nothing to build a valid >1-member team from).
        bases = [p for p in pool if not p["is_mega"]]
        megas = [p for p in pool if p["is_mega"]]
        candidates = sorted(bases[:_SEARCH_TOP_N] + megas[:_MEGA_TOP_N],
                            key=lambda x: x["impact"], reverse=True)
        fixed_names = [f["final_evo"] for f in self._fixed]
        results = []

        for size in range(1, 4):
            for combo in itertools.combinations(candidates, size):
                if not self._combo_ok(combo):
                    continue
                total_cost = sum(c["cost"] for c in combo)
                if total_cost > self._budget:
                    continue
                names = fixed_names + [c["final_evo"] for c in combo]
                score = impact_db.team_score(names)
                results.append((score, combo))

        self._greedy_coverage(candidates, fixed_names, results)

        results.sort(key=lambda x: x[0], reverse=True)
        seen, unique = set(), []
        for score, combo in results:
            key = tuple(sorted(c["final_evo"] for c in combo))
            if key not in seen:
                seen.add(key)
                unique.append((score, list(combo)))
            if len(unique) >= _SHOW_TEAMS:
                break
        return unique

    def _greedy_coverage(self, candidates, fixed_names, results):
        for seed_size in range(1, 4):
            for seed_combo in itertools.islice(
                itertools.combinations(candidates[:20], seed_size), 30
            ):
                if not self._combo_ok(seed_combo):
                    continue
                seed_cost = sum(c["cost"] for c in seed_combo)
                if seed_cost > self._budget:
                    continue
                available = [c for c in candidates if c not in seed_combo]
                team, _ = self._greedy_fill(list(seed_combo),
                                            self._budget - seed_cost,
                                            available, fixed_names)
                score = impact_db.team_score(fixed_names + [c["final_evo"] for c in team])
                results.append((score, tuple(team)))

    # ── Constraints & greedy fill ──────────────────────────────────────────────

    def _combo_ok(self, members) -> bool:
        """A combo is legal if it adds at most one Mega (counting pinned Megas)
        and never uses the same species (sid) twice."""
        megas = self._fixed_mega + sum(1 for m in members if m["is_mega"])
        if megas > _MAX_MEGAS:
            return False
        sids = [m["sid"] for m in members]
        return len(sids) == len(set(sids))

    def _greedy_fill(self, team, remaining, available, fixed_names):
        """Greedily add the highest team_score-gain candidate until the team is
        full or the budget runs out, respecting the one-Mega and one-form-per-
        species caps. Returns (team, remaining)."""
        while remaining > 0 and len(team) < _MAX_TEAM and available:
            base = fixed_names + [c["final_evo"] for c in team]
            curr = impact_db.team_score(base)
            team_sids = {t["sid"] for t in team}
            mega_full = self._fixed_mega + sum(1 for t in team if t["is_mega"]) >= _MAX_MEGAS
            best_gain, best_pick = 0.0, None
            for c in available:
                if c["cost"] > remaining:
                    continue
                if c["sid"] in team_sids:
                    continue
                if c["is_mega"] and mega_full:
                    continue
                gain = impact_db.team_score(base + [c["final_evo"]]) - curr
                if gain > best_gain:
                    best_gain, best_pick = gain, c
            if best_pick is None:
                break
            team.append(best_pick)
            remaining -= best_pick["cost"]
            available = [c for c in available if c is not best_pick]
        return team, remaining

    # ── quality mode ──────────────────────────────────────────────────────────

    _QUALITY_MIN_PERCENTILE = 80  # p0–p79 excluded from quality suggestions

    def _run_quality(self, pool: list) -> list:
        """Anchor on the best individual pick, then greedily fill by coverage gain.

        Tries the top-N individual picks as anchors and returns up to _SHOW_TEAMS
        results (same format as coverage mode) so the user can compare different
        quality-anchor choices.
        """
        pool = [p for p in pool if p["percentile"] >= self._QUALITY_MIN_PERCENTILE]
        fixed_names = [f["final_evo"] for f in self._fixed]
        results = []

        for anchor in pool[:_SHOW_TEAMS * 2]:  # try enough anchors to fill the display
            if anchor["cost"] > self._budget:
                continue
            if not self._combo_ok([anchor]):
                continue   # e.g. anchor is a Mega but a pinned starter already is
            available = [p for p in pool if p is not anchor]
            team, _ = self._greedy_fill([anchor], self._budget - anchor["cost"],
                                        available, fixed_names)
            score = impact_db.team_score(fixed_names + [c["final_evo"] for c in team])
            results.append((score, tuple(team)))

        if self._fixed:
            # Pins already set the anchor — rank by which fill best complements them.
            results.sort(key=lambda x: x[0], reverse=True)
        else:
            # No pins — rank by anchor quality so #1 is always the strongest individual.
            results.sort(key=lambda x: x[1][0]["impact"] if x[1] else 0, reverse=True)
        seen, unique = set(), []
        for score, combo in results:
            key = tuple(sorted(c["final_evo"] for c in combo))
            if key not in seen:
                seen.add(key)
                unique.append((score, list(combo)))
            if len(unique) >= _SHOW_TEAMS:
                break
        return unique


# ── Panel ─────────────────────────────────────────────────────────────────────

class TeamBuilderPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("tbpanel")
        self.setStyleSheet(_STYLE)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self._unlocked_ids:    list[int] = []
        self._won_ids:         list[int] = []
        self._selected_ids:    list[int] = []
        self._value_reductions: dict[int, int] = {}  # {sid: reduction_amount}
        self._egg_moves_owned:  dict[int, int] = {}  # {sid: eggMoves bitmask}
        self._budget = _BUDGET_DEFAULT
        self._mode   = "quality"

        self._build_ui()

    # ── Construction ──────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QLabel("Team Builder")
        hdr.setStyleSheet(
            "color:#cdd6f4; font-size:13px; font-weight:bold;"
            " padding:6px 8px 4px 8px; border-top:1px solid #313244;"
        )
        root.addWidget(hdr)

        # ── Controls bar ──────────────────────────────────────────────────
        ctrl = QWidget()
        ctrl.setObjectName("ctrl_bar")  # styled via _STYLE — no setStyleSheet here
        ctrl_hl = QHBoxLayout(ctrl)
        ctrl_hl.setContentsMargins(8, 4, 8, 4)
        ctrl_hl.setSpacing(6)

        # Mode toggle — Quality first
        self._qual_btn = QPushButton("QUALITY")
        self._qual_btn.setObjectName("mode_btn")
        self._qual_btn.setCheckable(True)
        self._qual_btn.setChecked(True)
        self._qual_btn.clicked.connect(lambda: self._set_mode("quality"))

        self._cov_btn = QPushButton("COVERAGE")
        self._cov_btn.setObjectName("mode_btn")
        self._cov_btn.setCheckable(True)
        self._cov_btn.clicked.connect(lambda: self._set_mode("coverage"))

        ctrl_hl.addWidget(self._qual_btn)
        ctrl_hl.addWidget(self._cov_btn)
        ctrl_hl.addStretch()

        self._budget_lbl = QLabel(f"{self._budget}pts")
        self._budget_lbl.setStyleSheet("color:#a6adc8; font-size:11px;")
        ctrl_hl.addWidget(self._budget_lbl)

        self._excl_cb = QCheckBox("Excl. won")
        self._excl_cb.toggled.connect(self._on_filter_changed)
        ctrl_hl.addWidget(self._excl_cb)

        self._leg_cb = QCheckBox("Incl. leg.")
        self._leg_cb.toggled.connect(self._on_filter_changed)
        ctrl_hl.addWidget(self._leg_cb)

        self._egg_cb = QCheckBox("Egg mvs")
        self._egg_cb.toggled.connect(self._on_egg_toggled)
        ctrl_hl.addWidget(self._egg_cb)

        self._egg_cap_slider = QSlider(Qt.Orientation.Horizontal)
        self._egg_cap_slider.setRange(1, 4)
        self._egg_cap_slider.setValue(4)
        self._egg_cap_slider.setFixedWidth(48)
        self._egg_cap_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._egg_cap_slider.setTickInterval(1)
        self._egg_cap_slider.setVisible(False)
        self._egg_cap_slider.valueChanged.connect(self._on_egg_cap_changed)
        ctrl_hl.addWidget(self._egg_cap_slider)

        self._egg_cap_lbl = QLabel("4🥚")
        self._egg_cap_lbl.setStyleSheet("color:#a6adc8; font-size:11px; min-width:22px;")
        self._egg_cap_lbl.setVisible(False)
        ctrl_hl.addWidget(self._egg_cap_lbl)

        root.addWidget(ctrl)

        # ── Scroll area ───────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._content = QWidget()
        self._content.setStyleSheet("background:#1e1e2e;")
        self._content_vl = QVBoxLayout(self._content)
        self._content_vl.setContentsMargins(8, 6, 8, 6)
        self._content_vl.setSpacing(6)
        self._content_vl.addStretch()

        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)

        self._show_placeholder("Waiting for starter select screen…")

    # ── Public API ────────────────────────────────────────────────────────────

    def update_starter_state(self, snapshot: dict):
        unlocked   = snapshot.get("unlocked_ids") or []
        won        = snapshot.get("won_ids") or []
        selected   = snapshot.get("selected_ids") or []
        reductions = snapshot.get("value_reductions") or {}
        egg_owned  = snapshot.get("egg_moves_owned") or {}
        # snapshot keys are strings (JSON); convert to int
        reductions = {int(k): v for k, v in reductions.items()}
        egg_owned  = {int(k): v for k, v in egg_owned.items()}

        changed = (unlocked != self._unlocked_ids
                   or won != self._won_ids
                   or selected != self._selected_ids
                   or reductions != self._value_reductions
                   or egg_owned != self._egg_moves_owned)

        self._unlocked_ids     = unlocked
        self._won_ids          = won
        self._selected_ids     = selected
        self._value_reductions = reductions
        self._egg_moves_owned  = egg_owned

        if changed:
            self._recompute()

    # ── Internals ─────────────────────────────────────────────────────────────

    def _set_mode(self, mode: str):
        self._mode = mode
        self._cov_btn.setChecked(mode == "coverage")
        self._qual_btn.setChecked(mode == "quality")
        self._recompute()

    def _on_filter_changed(self):
        self._recompute()

    def _on_egg_cap_changed(self, val: int):
        self._egg_cap_lbl.setText(f"{val}🥚")
        impact_db.set_egg_cap(val)
        self._recompute()

    def _on_egg_toggled(self, checked: bool):
        self._egg_cap_slider.setVisible(checked)
        self._egg_cap_lbl.setVisible(checked)
        if not checked:
            impact_db.set_include_egg(False)
            self._recompute()
            return
        if impact_db.is_egg_ready():
            impact_db.set_include_egg(True)
            self._recompute()
            return
        # Egg cache not built yet — kick off the build
        self._egg_cb.setEnabled(False)
        self._show_placeholder("Building egg-move cache (~3 min on first use)…")
        impact_db.init_egg(
            on_progress=lambda msg: QMetaObject.invokeMethod(
                self, "_on_egg_progress", Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg),
            ),
            on_ready=lambda: QMetaObject.invokeMethod(
                self, "_on_egg_ready", Qt.ConnectionType.QueuedConnection,
            ),
        )

    @pyqtSlot(str)
    def _on_egg_progress(self, msg: str):
        self._show_placeholder(msg)

    @pyqtSlot()
    def _on_egg_ready(self):
        impact_db.set_include_egg(True)
        self._egg_cb.setEnabled(True)
        self._recompute()

    def _recompute(self):
        if not impact_db.is_ready():
            self._show_placeholder("Loading impact data…")
            return
        if not impact_db.starters_index():
            self._show_placeholder("Starters index not ready — rebuild cache")
            return

        idx          = impact_db.starters_index()
        unlocked_set = set(self._unlocked_ids)
        exclude      = self._excl_cb.isChecked()
        won_set      = set(self._won_ids)

        # Resolve locked-in starters (selected in game UI). Apply the same value
        # reduction the pool uses so locked starters show their true reduced cost
        # and the budget isn't over-counted.
        fixed_infos = []
        fixed_cost  = 0
        for sid in self._selected_ids:
            if sid not in unlocked_set and unlocked_set:
                continue
            info = idx.get(sid)
            if info:
                reduction = self._value_reductions.get(sid, 0)
                eff_cost  = max(1, info["cost"] - reduction)
                bitmask   = self._egg_moves_owned.get(sid, 0)
                disp, raw, boosted = impact_db.egg_aware_score(
                    info["final_evo"], info["name"], bitmask)
                info = {**info, "sid": sid, "cost": eff_cost,
                        "discounted": reduction > 0,
                        "impact": raw, "score": disp, "egg_boosted": boosted}
                fixed_infos.append(info)
                fixed_cost += eff_cost

        fill_budget = self._budget - fixed_cost

        # Budget label
        lbl = f"{self._budget}pts"
        if self._unlocked_ids:
            lbl += f"  ·  {len(self._unlocked_ids)} unlocked"
        if fixed_infos:
            lbl += f"  ·  {len(fixed_infos)} locked"
        self._budget_lbl.setText(lbl)

        self._show_placeholder("Searching…")

        worker = _Worker(
            self._unlocked_ids, self._won_ids,
            exclude, fill_budget, self._mode, fixed_infos,
            incl_legendary=self._leg_cb.isChecked(),
            value_reductions=self._value_reductions,
            egg_moves_owned=self._egg_moves_owned,
        )
        worker.done.connect(lambda results, mode: self._on_results(results, mode, fixed_infos))
        t = threading.Thread(target=worker.run, daemon=True)
        t.start()

    def _on_results(self, results: list, mode: str, fixed_infos: list):
        if mode == "quality":
            self._rebuild_quality(results, fixed_infos)
        else:
            self._rebuild_coverage(results, fixed_infos)

    # ── Coverage display ──────────────────────────────────────────────────────

    def _rebuild_coverage(self, teams: list, fixed_infos: list):
        self._clear_content()
        if not teams and not fixed_infos:
            self._show_placeholder("No suggestions — unlock more Pokémon")
            return
        for rank, (score, combo) in enumerate(teams, 1):
            card = self._make_card(rank, score, combo, fixed_infos)
            self._content_vl.insertWidget(self._content_vl.count() - 1, card)

    # ── Quality display ───────────────────────────────────────────────────────

    def _rebuild_quality(self, results: list, fixed_infos: list):
        self._clear_content()
        if not results and not fixed_infos:
            self._show_placeholder("No suggestions — unlock more Pokémon")
            return
        for rank, (score, combo) in enumerate(results, 1):
            card = self._make_card(rank, score, combo, fixed_infos, quality=True)
            self._content_vl.insertWidget(self._content_vl.count() - 1, card)

    def _make_card(self, rank: int, score: float, combo: list, fixed: list,
                   quality: bool = False) -> QWidget:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background:#24273a; border:1px solid #313244; border-radius:4px; }"
        )
        vl = QVBoxLayout(card)
        vl.setContentsMargins(6, 5, 6, 5)
        vl.setSpacing(3)

        all_members = list(fixed) + list(combo)
        total_cost  = sum(m["cost"] for m in all_members)
        cost_color  = "#a6e3a1" if total_cost >= self._budget else "#f9e2af"

        hdr_hl = QHBoxLayout()
        hdr_hl.setSpacing(6)
        rank_lbl = QLabel(f"#{rank}")
        rank_lbl.setStyleSheet("color:#6c7086; font-size:11px; font-weight:bold;")
        hdr_hl.addWidget(rank_lbl)
        cost_lbl = QLabel(f"{total_cost}/{self._budget}pts")
        cost_lbl.setStyleSheet(f"color:{cost_color}; font-size:11px;")
        hdr_hl.addWidget(cost_lbl)
        # Share of the opponent pool this team has a direct counter (Z) against.
        # Kept defensive: a coverage hiccup should drop the badge, not the card.
        try:
            cov  = impact_db.team_coverage([m["final_evo"] for m in all_members])
            pool = cov.get("pool_size", 0)
            ctr_pct = round(cov["counters"] / pool * 100) if pool else None
        except Exception as e:
            print(f"[team_builder] counter%% badge failed: {e}")
            ctr_pct = None
        if ctr_pct is not None:
            score_lbl = QLabel(f"counter {ctr_pct}%")
            score_lbl.setStyleSheet("color:#a6e3a1; font-size:10px;")
            hdr_hl.addWidget(score_lbl)
        hdr_hl.addStretch()
        vl.addLayout(hdr_hl)

        for i, info in enumerate(all_members):
            is_fixed  = info in fixed
            is_anchor = quality and not fixed and not is_fixed and i == 0
            vl.addLayout(self._make_member_row(info, is_fixed=is_fixed, is_anchor=is_anchor,
                                               font_size=12))

        return card

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _make_member_row(self, info: dict, is_fixed: bool, font_size: int,
                         is_anchor: bool = False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)

        icon = "📌" if is_fixed else "★" if is_anchor else "  "
        pin = QLabel(icon)
        pin.setFixedWidth(16)
        pin.setStyleSheet("color:#f9e2af; font-size:10px;" if is_anchor else "font-size:10px;")
        row.addWidget(pin)

        name = info["name"].replace("-", " ").title()
        name_lbl = QLabel(name)
        name_color = "#89b4fa" if is_fixed else "#f9e2af" if is_anchor else "#cdd6f4"
        name_lbl.setStyleSheet(f"color:{name_color}; font-size:{font_size}px;")
        row.addWidget(name_lbl)

        final = info["final_evo"]
        if final != info["name"]:
            arrow = QLabel(f"→ {final.replace('-', ' ').title()}")
            arrow.setStyleSheet("color:#6c7086; font-size:11px;")
            row.addWidget(arrow)

        row.addStretch()

        entry = impact_db.get(final)
        if entry:
            pct   = entry.get("percentile", 0)
            # Egg-aware Impact when the worker computed one; else fall back to base.
            score   = info["score"] if "score" in info else impact_db.impact_score(final)
            boosted = info.get("egg_boosted", False)
            egg_tag = " 🥚" if boosted else ""
            score_lbl = QLabel(f"Impact {score}{egg_tag}")
            score_lbl.setStyleSheet(f"color:{_pct_color(pct)}; font-size:10px;")
            tip = _impact_tooltip(entry)
            if boosted:
                tip += "\n(includes your owned egg moves)"
            score_lbl.setToolTip(tip)
            row.addWidget(score_lbl)

        cost = info["cost"]
        if info.get("discounted"):
            cost_color = "#89b4fa"  # blue = price was reduced
        else:
            cost_color = _COST_COLORS.get(cost, "#a6adc8")
        c_lbl = QLabel(f"{cost}pt")
        c_lbl.setStyleSheet(f"color:{cost_color}; font-size:11px;")
        row.addWidget(c_lbl)

        return row

    def _make_section_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:#6c7086; font-size:10px; font-weight:bold;"
            " padding:2px 0px 0px 0px; letter-spacing:1px;"
        )
        return lbl

    def _show_placeholder(self, text: str):
        self._clear_content()
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#45475a; font-size:12px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        self._content_vl.insertWidget(0, lbl)

    def _clear_content(self):
        while self._content_vl.count() > 1:
            item = self._content_vl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
