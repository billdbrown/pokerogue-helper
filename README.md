# Pokerogue Helper

<img width="1598" height="900" alt="image" src="https://github.com/user-attachments/assets/f842aab5-58df-4ae4-8b48-1c0d995fb538" />


A Windows desktop overlay for [pokerogue.net](https://pokerogue.net). Embeds the
game in a native window and reads live battle state directly from the running
Phaser scene via JavaScript injection — no OCR required.

Single-developer hobby tool. Built with PyQt6 and QtWebEngine.

## Features

- **Embedded browser** — pokerogue.net runs inside a native window so the helper
  panels sit alongside the game without Alt-Tabbing.
- **Live state reading** — hooks into the Phaser scene via `runJavaScript` to
  read the active Pokémon, party, moves, HP, types, and stat stages in real time.
- **Opponent analysis** — shows type effectiveness, base stats, and an Impact
  Score percentile for the wild/trainer Pokémon. Flags great catches and
  recommends beneficial team swaps.
- **Team management** — tracks your 6-Pokémon party with moves, HP bars, levels,
  abilities, and natures. Surfaces coverage gaps, dangerous dual-type combos, and
  matchup share per slot.
- **Impact Score** — pre-computed offensive threat score for every final-evolution
  Pokémon form, derived from their level-up learnset (sourced from the Pokerogue
  repo), weighted by speed. Browseable via the Impact tab with filters for
  legendaries, paradox Pokémon, and starters.
- **Team score** — tracks your team's combined offensive coverage across all 171
  type pairings; recommends which member to swap for any encountered wild Pokémon.
- **Wave tracker** — displays current wave and upcoming boss / rival / Elite Four
  milestones.
- **Analysis tabs** — WEAKEST (matchup share ranking), TIPS (coverage gaps),
  TYPING (preferred next type), DANGER (shared weaknesses), WEAKNESS (team
  defensive holes).

## Requirements

- **OS:** Windows 10/11
- **Python:** 3.11+

## Install

```powershell
# 1. Clone and enter the project
git clone https://github.com/billdbrown/pokerogue-helper.git
cd pokerogue-helper

# 2. Install Python dependencies
pip install -r requirements.txt
```

## Run

```powershell
python main.py            # full app
python main.py --browser  # impact score browser only (no web engine required)
```

First launch builds two background caches:
- **Stats cache** (~30s) — base stats for all Pokémon from PokéAPI
- **Impact cache** (~3 min) — offensive threat scores for all final-evolution forms,
  fetching level-up learnsets from the Pokerogue GitHub repo and move data from
  PokéAPI. Cached to disk; subsequent launches load instantly.

## Building a distributable bundle

```powershell
.\build.ps1            # incremental build
.\build.ps1 -Clean     # clean build
```

Output: `dist\PokerogueHelper_v<version>\PokerogueHelper_v<version>.exe`.

## Project layout

```
.
├── main.py                   # entry point
├── src/
│   ├── embedded_window.py    # main window; Phaser hook injection; navbar
│   ├── js_state.py           # live battle state reader (JS → Python)
│   ├── overlay.py            # opponent analysis panel
│   ├── team_panel.py         # 6-slot team roster + move editor
│   ├── analysis_panel.py     # tabbed analytics (WEAKEST/TIPS/TYPING/DANGER/WEAKNESS)
│   ├── notification_panel.py # great-catch / swap recommendation cards
│   ├── impact_db.py          # Impact Score cache build + runtime API
│   ├── impact_table.py       # Impact Score browser dialog (sortable; includes Bulk score)
│   ├── team_builder_panel.py # Team builder — optimise composition via impact scores
│   ├── debug_score.py        # Dev tool: per-move score breakdown for one Pokémon
│   ├── visualize_scores.py   # Score distribution plots (matplotlib)
│   ├── wave_panel.py         # wave tracker
│   ├── turn_order_panel.py   # turn order display
│   ├── damage_calc.py        # damage calculation helpers
│   ├── scoring.py            # type coverage scoring
│   ├── pokemon_api.py        # PokéAPI client + caching
│   ├── weakness_calc.py      # type effectiveness math
│   ├── stats_db.py           # base-stat cache (stats_cache.json)
│   ├── moves_db.py           # move data lookup
│   ├── tier_db.py            # Smogon tier data (optional)
│   ├── app_dirs.py           # cross-platform user data directory
│   ├── window_state.py       # JSON persistence for geometry / team
│   ├── ui_state.py           # 1v1/2v2 + boss state model
│   └── turn_order.py         # turn order calculation
├── docs/
│   ├── CLAUDE.md             # architecture notes for Claude Code
│   └── impact_score.md       # Impact Score methodology writeup
├── resources/                # icons and bundled assets
├── requirements.txt
├── version.txt
├── pokerogue_helper.spec     # PyInstaller config
└── build.ps1                 # build + Tesseract bundling script
```

## License

MIT — see [LICENSE](LICENSE).
