# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app (1600x900 single window with built-in browser)
python main.py
```

There is no build step, test suite, or linter. This is a single-developer desktop tool; run it directly to verify changes.

## Architecture

**`main.py`** → **`embedded_window.py`** — unified 1600×900 window with a left sidebar (wave/opponent/team panels) and a QtWebEngine view loading pokerogue.net on the right.

### Core data flow (opponent analysis)

1. `CaptureBox` (`capture_box.py`) — frameless drag-to-position overlay; defines the screen region to OCR.
2. `OverlayPanel` (`overlay.py`) — polls every ~500ms; captures that region with `mss`, **inverts the image** (white-on-dark → dark-on-white for Tesseract), runs `pytesseract` with `--psm 8 --oem 3`, fuzzy-matches the result against `stats_db.all_names()`.
3. `pokemon_api.py` — fetches types, stats, moves from PokéAPI; caches in-memory dicts.
4. `weakness_calc.py` — computes type effectiveness multipliers; provides full/partial coverage, gap analysis, dangerous dual-type combos.
5. Results are rendered back into the overlay/team/analysis panels.

### Module responsibilities

| File | Purpose |
|---|---|
| `pokemon_api.py` | PokéAPI REST client; name normalization (e.g. "Alolan Raichu" → "raichu-alola"); caching |
| `weakness_calc.py` | Type matchup math; `detailed_coverage()`, `dangerous_combos()`, `coverage_suggestions()` |
| `stats_db.py` | Background-thread cache of all Pokémon base stats (`stats_cache.json`); percentile helpers |
| `tier_db.py` | Smogon tier data from formats-data.ts → `tier_cache.json`; optional/graceful failure |
| `team_panel.py` | 6-slot team roster; per-Pokémon move editor; team-wide weakness grid |
| `wave_panel.py` | Wave counter OCR; upcoming boss schedule display |
| `analysis_panel.py` | Tabbed analytics for embedded mode (WEAKEST / TIPS / TYPING / DANGER / WEAKNESS) |
| `window_state.py` | JSON persistence for window geometry keyed by `box1`, `box2`, `panel`, etc. |

## Platform-Specific Requirements

### Tesseract path (hardcoded)

`overlay.py` and `wave_panel.py` both set:
```python
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```
If Tesseract is installed elsewhere, update both files. Tesseract must be installed separately.

### DPI scaling

`mss` requires physical pixels; Qt reports logical pixels. All capture coordinates must be multiplied by `QApplication.primaryScreen().devicePixelRatio()` before passing to mss.

### OCR inversion

Pokerogue uses white text on dark backgrounds. All captured images must be inverted before OCR:
```python
inverted = ImageOps.invert(image.convert('L'))
```

### QtWebEngine sandbox (embedded mode)

`embedded_app.py` disables the Chromium sandbox for Windows compatibility via environment variables set before Qt initializes.

## Threading Model

Background work uses `threading.Thread(daemon=True)`. Results are passed back to the Qt main thread via custom `QObject` subclasses (`_Signals`) emitting `pyqtSignal`. Never update Qt widgets directly from worker threads.

## Caching Strategy

- **In-memory:** API responses cached in module-level dicts (`_pokemon_cache`, `_type_cache`, etc.) — cleared on restart.
- **On-disk:** `stats_cache.json` (all Pokémon stats, built in background on first run ~60s), `tier_cache.json` (Smogon tiers). Both include a version field; corrupted caches are rebuilt automatically.
