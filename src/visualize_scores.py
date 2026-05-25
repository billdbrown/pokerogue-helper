"""Quick visualizations of impact score data from the local cache.

Usage (run from src/ or repo root):
    python visualize_scores.py              # three-distribution overview
    python visualize_scores.py --impact     # original impact-score histogram only
    python visualize_scores.py --egg        # use egg-moves cache
"""

import argparse
import json
import math
import os
import sys

# ensure src/ is importable regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib.pyplot as plt
import numpy as np


# ── cache loading ─────────────────────────────────────────────────────────────

def _load_impact_cache(egg: bool = False) -> dict:
    from app_dirs import data_path
    fname = "impact_cache_egg.json" if egg else "impact_cache.json"
    path  = data_path(fname)
    if not os.path.exists(path):
        sys.exit(f"Cache not found: {path}\nRun the app first to build the impact cache.")
    with open(path) as f:
        data = json.load(f)
    data.pop("_version", None)
    data.pop("_starters", None)
    return data


def _load_stats_cache() -> dict:
    from app_dirs import data_path
    path = data_path("stats_cache.json")
    if not os.path.exists(path):
        sys.exit(f"Stats cache not found: {path}\nRun the app first.")
    with open(path) as f:
        data = json.load(f)
    data.pop("_version", None)
    return data


# ── defensive score ───────────────────────────────────────────────────────────

def _build_defensive_scores(impact_data: dict, stats_data: dict) -> dict[str, float]:
    """Sum of log(1 + hits_to_ko) across all 18 attacking types.

    hits_to_ko = hp * effective_def / (100 * type_effectiveness)
    effective_def = (defense + sp_defense) / 2   [simplified — not yet category-weighted]
    Immune types (0x) are capped at 50 hits.
    """
    from weakness_calc import _effectiveness, ALL_TYPES

    IMMUNE_CAP = 50

    results: dict[str, float] = {}
    missing = 0
    for name, entry in impact_data.items():
        stat_entry = stats_data.get(name)
        if stat_entry is None:
            # alternate forms (e.g. deoxys-attack) have IDs >10000 and aren't in
            # stats_cache; fall back to the base form
            base = name.split("-")[0]
            stat_entry = stats_data.get(base)
        if stat_entry is None:
            missing += 1
            continue

        types      = entry["types"]
        s          = stat_entry["stats"]
        hp         = s.get("hp", 1)
        eff_def    = (s.get("defense", 1) + s.get("special-defense", 1)) / 2

        score = 0.0
        for atk_type in ALL_TYPES:
            eff = _effectiveness(atk_type, types)
            if eff == 0:
                hits = IMMUNE_CAP
            else:
                hits = hp * eff_def / (100.0 * eff)
            score += math.log(1 + hits)

        results[name] = score

    if missing:
        print(f"  [defensive] {missing} forms had no stat entry — skipped")
    return results


# ── plots ─────────────────────────────────────────────────────────────────────

def _hist_ax(ax, values: np.ndarray, color: str, title: str, xlabel: str,
             xfmt=None, n_bins: int = 40) -> None:
    med = float(np.median(values))
    avg = float(np.mean(values))
    ax.hist(values, bins=n_bins, color=color, alpha=0.85, edgecolor="white", linewidth=0.4)
    ax.axvline(med, color="black", linestyle="--", linewidth=1.4,
               label=f"Median: {xfmt(med) if xfmt else f'{med:.1f}'}")
    ax.axvline(avg, color="black", linestyle=":",  linewidth=1.4,
               label=f"Mean:   {xfmt(avg) if xfmt else f'{avg:.1f}'}")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel("Count", fontsize=10)
    ax.legend(fontsize=9)
    ax.yaxis.grid(True, linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    if xfmt:
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: xfmt(x)))


def plot_distributions(impact_data: dict, stats_data: dict, egg: bool = False) -> None:
    """Three side-by-side histograms: offensive, defensive, speed — non-legendary only."""
    nonleg = {name: v for name, v in impact_data.items() if not v.get("legendary")}
    print(f"Non-legendary forms: {len(nonleg)}")

    # Offensive
    off_scores = np.array([v["impact"] for v in nonleg.values()])

    # Defensive
    print("Computing defensive scores…")
    def_map = _build_defensive_scores(nonleg, stats_data)
    def_names   = sorted(def_map)
    def_scores  = np.array([def_map[n] for n in def_names])

    # Speed
    spd_scores = np.array([v["speed"] for v in nonleg.values()])

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    suffix = " (egg)" if egg else ""

    _hist_ax(axes[0], off_scores, "#4e79a7",
             f"Offensive Impact{suffix}",
             "Raw impact score",
             xfmt=lambda x: f"{x/1e6:.1f}M")

    _hist_ax(axes[1], def_scores, "#59a14f",
             f"Defensive Coverage{suffix}",
             "Σ log(1 + hits-to-KO) across 18 types\n"
             "[effective_def = avg(def, sp_def), simplified]")

    _hist_ax(axes[2], spd_scores, "#f28e2b",
             "Speed",
             "Base Speed stat")

    fig.suptitle("Non-legendary Pokémon — Component Score Distributions",
                 fontsize=14, fontweight="bold", y=1.01)
    fig.tight_layout()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "distributions.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved: {os.path.abspath(out)}")
    plt.show()


def plot_impact_histogram(data: dict, egg: bool = False) -> None:
    """Histogram of raw impact scores for all Pokémon forms (legendary + non-leg)."""
    nonleg = [(name, v) for name, v in data.items() if not v.get("legendary")]
    leg    = [(name, v) for name, v in data.items() if v.get("legendary")]

    scores_nonleg = np.array([v["impact"] for _, v in nonleg])
    scores_leg    = np.array([v["impact"] for _, v in leg])
    all_scores    = np.concatenate([scores_nonleg, scores_leg])
    bin_edges     = np.linspace(0, all_scores.max(), 51)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.hist(scores_nonleg, bins=bin_edges, color="#4e79a7", alpha=0.85,
            label=f"Non-legendary ({len(scores_nonleg)})", edgecolor="white", linewidth=0.4)
    if len(scores_leg):
        ax.hist(scores_leg, bins=bin_edges, color="#e15759", alpha=0.75,
                label=f"Legendary / Mythical ({len(scores_leg)})", edgecolor="white", linewidth=0.4)

    med = float(np.median(scores_nonleg))
    avg = float(np.mean(scores_nonleg))
    ax.axvline(med, color="#4e79a7", linestyle="--", linewidth=1.5,
               label=f"Non-leg median: {med/1e6:.2f}M")
    ax.axvline(avg, color="#4e79a7", linestyle=":",  linewidth=1.5,
               label=f"Non-leg mean:   {avg/1e6:.2f}M")

    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M"))
    ax.set_xlabel("Impact Score (raw)", fontsize=13)
    ax.set_ylabel("Number of Pokémon forms", fontsize=13)
    title = "Impact Score Distribution — All Pokémon Forms"
    if egg:
        title += " (egg moves included)"
    ax.set_title(title, fontsize=15, fontweight="bold")
    ax.legend(fontsize=10)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    ax.set_axisbelow(True)

    fig.tight_layout()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "impact_score_histogram.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {os.path.abspath(out)}")
    plt.show()


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Visualize Pokerogue Helper score data.")
    parser.add_argument("--egg",    action="store_true", help="Use the egg-moves cache")
    parser.add_argument("--impact", action="store_true", help="Show original impact-only histogram")
    args = parser.parse_args()

    impact_data = _load_impact_cache(egg=args.egg)

    if args.impact:
        plot_impact_histogram(impact_data, egg=args.egg)
    else:
        stats_data = _load_stats_cache()
        plot_distributions(impact_data, stats_data, egg=args.egg)


if __name__ == "__main__":
    main()
