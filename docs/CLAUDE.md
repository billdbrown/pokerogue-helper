# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Running the Application

```powershell
pip install -r requirements.txt
python main.py
```

No build step, test suite, or linter. Run directly to verify changes.

## Architecture

**`main.py`** → **`embedded_window.py`** — a 1600×900 window with a left sidebar
(wave / opponent / team / analysis panels) and a QtWebEngine view loading
pokerogue.net on the right.

### State reading — no OCR

The project reads live game state by injecting JavaScript into the QtWebEngine
page. A `Function.prototype.bind` hook in `embedded_window._inject_phaser_capture`
captures the Phaser game reference and stores it as `window.__pokerogue_game__`.
`js_state.py` then polls `window.__pokerogue_game__` via `runJavaScript` every
~500ms, extracting battle state (active Pokémon, party, moves, HP, types, stat
stages) and emitting Python signals when anything changes.

All OCR code has been removed. Tesseract is no longer used for state reading
(it may still be bundled in the distributable for legacy reasons).

### Core data flow

```
embedded_window._inject_phaser_capture()   # bind-hook on page load
    └── js_state.JSStateReader (QTimer)    # polls every 500ms
            ├── overlay.OverlayPanel       # opponent name → PokéAPI → analysis
            ├── team_panel.TeamPanel       # party sync → moves → analysis
            └── wave_panel                # wave number display
```

### Module responsibilities

| File | Purpose |
|------|---------|
| `embedded_window.py` | Main window; Phaser hook injection; navbar buttons |
| `js_state.py` | JS extractor; emits signals on state change |
| `overlay.py` | Opponent analysis: PokéAPI lookup, Impact Score display, recommendations |
| `team_panel.py` | 6-slot party tracker; move editor; matchup share calculation |
| `analysis_panel.py` | Tabbed analytics panel (embedded mode) |
| `notification_panel.py` | Great-catch / swap recommendation cards |
| `impact_db.py` | Impact Score: cache build, runtime API (`get`, `team_score`, `best_swap`, `pairing_vector`) |
| `impact_table.py` | Impact Score browser dialog (searchable, sortable, filterable) |
| `pokemon_api.py` | PokéAPI REST client; in-memory caching; name normalization |
| `weakness_calc.py` | Type effectiveness math; coverage/gap analysis |
| `stats_db.py` | Background base-stat cache (`stats_cache.json`) |
| `moves_db.py` | Move name/data lookup |
| `tier_db.py` | Smogon tier data; optional, graceful failure |
| `app_dirs.py` | Cross-platform user data directory (`%LOCALAPPDATA%/PokerogueHelper`) |
| `scoring.py` | Offensive type coverage chart |
| `damage_calc.py` | Damage calculation helpers |
| `turn_order.py` / `turn_order_panel.py` | Turn order prediction |
| `window_state.py` | JSON persistence for geometry, team, wave |
| `ui_state.py` | 1v1/2v2 + boss state model |

## Impact Score System

See `docs/impact_score.md` for full methodology. Key points:

- **Coverage**: raw SE damage sum across all 171 type pairings from optimal 4-move selection
- **Impact**: coverage × speed factor (penalty below p50 speed, minimum ×0.6)
- **Learnset**: level-up moves only, sourced from Pokerogue's GitHub repo
  (`src/data/balance/pokemon-level-moves.ts`). Recoil moves and self-damaging
  moves (Mind Blown, Explosion, etc.) are excluded.
- **Cache**: `impact_cache.json` in the user data dir. `CACHE_VERSION` in
  `impact_db.py` must be bumped whenever the schema or scoring methodology changes.
- **Rebuild**: run `python src/impact_db.py` standalone (~3 min, fetches from
  PokéAPI and Pokerogue repo).

## Threading Model

Background work uses `threading.Thread(daemon=True)`. Results are passed to the
Qt main thread via `QObject` subclasses (`_Signals`) emitting `pyqtSignal`. Never
update Qt widgets directly from worker threads.

## Caching Strategy

| Cache | Location | Built by |
|-------|----------|---------|
| `stats_cache.json` | user data dir | `stats_db` on first run (~30s) |
| `impact_cache.json` | user data dir | `impact_db` on first run (~3 min) |
| API responses | in-memory dicts | `pokemon_api` per session |

Both on-disk caches include a `_version` field. A version mismatch triggers an
automatic background rebuild.

## Key Invariants

- `impact_db._EFF` must be warmed (via `_build_effectiveness_table()`) on both
  the cache-load path and the cache-build path — it's used at runtime by
  `pairing_vector()` and `team_score()`.
- `team_panel._moves_pairing_vector()` uses the player's actual equipped moves
  (not the impact cache's theoretical optimal set) for matchup share calculation.
- Mega evolutions, GMax forms, and Totem forms are excluded from the impact cache.
  Paradox Pokémon are included but flagged (`paradox: true`).
