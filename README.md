# Pokerogue Helper

A Windows desktop overlay for [pokerogue.net](https://pokerogue.net). Embeds the
game in a native window, OCRs game state (wave, opponent, your team) from the
screen in real time, and shows type weaknesses, base stats, and team analysis
pulled live from PokéAPI.

Single-developer hobby tool. Built with PyQt6, QtWebEngine, mss, and Tesseract.

## Features

- **Embedded browser** — pokerogue.net runs in a 1600×900 native window so the
  helper panels can sit alongside the game without Alt-Tabbing.
- **Opponent analysis** — reads the opposing Pokémon's name and level via OCR,
  fetches type/stats/moves from PokéAPI, computes effectiveness against your
  team and shows dangerous dual-type combos.
- **Team management** — track your 6-Pokémon party, including known moves;
  surface coverage gaps and weak links.
- **Wave tracker** — OCRs the current wave number and shows upcoming boss /
  rival / Elite Four milestones. Robust against the red boss-wave font color
  and pixel-font confusables; supports manual override and an explicit "New
  Run" reset.
- **2v2 and boss detection** — automatically reshapes OCR window positions when
  a second opponent appears or a boss plaque is detected by color.
- **Calibration UI** — a debug mode lets you drag OCR capture windows to
  fine-tune positions for your monitor and DPI scale.

## Requirements

- **OS:** Windows 10/11
- **Python:** 3.10 (other 3.x may work but the OCR wheel is pinned)
- **Tesseract OCR:** install separately from
  [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki) — the
  app auto-discovers it via the registry or common paths.

## Install

```powershell
# 1. Clone and enter the project
git clone https://github.com/<your-username>/pokerogue-helper.git
cd pokerogue-helper

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. (Optional, faster OCR) Install the prebuilt tesserocr wheel for Python 3.10:
pip install https://github.com/simonflueckiger/tesserocr-windows_build/releases/download/tesserocr-v2.10.0-tesseract-5.5.2/tesserocr-2.10.0-cp310-cp310-win_amd64.whl
# pytesseract is used as a fallback if tesserocr isn't available.
```

## Run

```powershell
python main.py
```

The first launch builds the base-stats cache in the background (~60s); after
that it's instant.

## Building a distributable bundle

```powershell
.\build.ps1            # incremental build
.\build.ps1 -Clean     # clean build
```

Output: `dist\PokerogueHelper_v<version>\PokerogueHelper_v<version>.exe`. The
script auto-bundles Tesseract from your local install so the end user doesn't
need a separate install.

## Project layout

```
.
├── main.py                  # entry point
├── src/                     # all source modules
│   ├── embedded_window.py   # 1600x900 main window, OCR wiring, UI state
│   ├── overlay.py           # opponent analysis (name OCR → PokéAPI)
│   ├── team_panel.py        # 6-slot team roster
│   ├── wave_panel.py        # wave tracker + manual entry
│   ├── ocr_service.py       # centralized Tesseract OCR worker
│   ├── type_color_service.py# pixel-color sampling for type / boss detection
│   ├── ui_state.py          # 1v1/2v2 + boss state model
│   ├── box_positions.py     # OCR window position persistence
│   ├── pokemon_api.py       # PokéAPI client + name normalization
│   ├── weakness_calc.py     # type effectiveness math
│   ├── stats_db.py          # background base-stat cache
│   ├── tier_db.py           # Smogon tier data (graceful failure)
│   ├── moves_db.py          # move data lookup
│   ├── active_tracker.py    # active Pokémon detection per slot
│   ├── analysis_panel.py    # team analytics tabs
│   ├── capture_box.py       # draggable OCR capture overlays
│   ├── ocr_debug_window.py  # debug view of OCR pipeline
│   ├── tesseract_path.py    # auto-detect Tesseract install
│   └── window_state.py      # JSON persistence for geometry / wave
├── docs/
│   └── CLAUDE.md            # architecture notes
├── resources/               # icons and other bundled assets
├── requirements.txt
├── version.txt
├── pokerogue_helper.spec    # PyInstaller config
├── build.ps1                # build orchestrator (also bundles Tesseract)
└── LICENSE                  # MIT
```

## License

MIT — see [LICENSE](LICENSE).
