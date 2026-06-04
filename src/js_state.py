"""Live Pokerogue battle state read from the running Phaser scene.

Replaces OCR. Polls window.__pokerogue_game__ (set by the Function.prototype.bind
hook in embedded_window._inject_phaser_capture) every interval_ms and emits a
snapshot dict whenever the state changes.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


# Pokerogue Stat enum (from src/enums/stat.ts):
# HP=0, ATK=1, DEF=2, SPATK=3, SPDEF=4, SPD=5, ACC=6, EVA=7
# Stat stages live on summonData.statStages, indexed as BattleStat
# (ATK=0, DEF=1, SPA=2, SPD=3, SPE=4, ACC=5, EVA=6). Speed stage = index 4.

# PokemonType IDs (verified live: Skarmory=[8,2]=steel/flying, Alakazam=[13]=psychic)
_TYPE_NAMES = [
    "normal", "fighting", "flying", "poison", "ground", "rock",
    "bug", "ghost", "steel", "fire", "water", "grass",
    "electric", "psychic", "ice", "dragon", "dark", "fairy",
    "stellar", "unknown",
]


EXTRACTOR_JS = r"""
(function() {
    const g = window.__pokerogue_game__;
    if (!g) return null;
    const battle = Object.values(g.scene.keys).find(s => typeof s.getPlayerField === 'function');
    if (!battle) return null;

    const TYPES = """ + repr(_TYPE_NAMES) + r""";

    const tryGet = (fn) => { try { return fn(); } catch(e) { return null; } };

    const summarize = (p) => {
        try {
            const types = (typeof p.getTypes === 'function') ? p.getTypes() : [];
            // getEffectiveStat applies stages/items/abilities/weather. Falls back to
            // the permanent stat if the in-battle method isn't available.
            const effSpeed = tryGet(() => typeof p.getEffectiveStat === 'function'
                ? p.getEffectiveStat(5) : p.getStat(5));
            const stages = tryGet(() => p.summonData && p.summonData.statStages) || [];
            return {
                name: p.species && p.species.name && p.species.name.toLowerCase(),
                speciesId: p.species && p.species.speciesId,
                level: p.level,
                hp: p.hp,
                maxHp: tryGet(() => p.getMaxHp()),
                types: types.map(t => TYPES[t] || 'unknown'),
                ability: tryGet(() => p.getAbility() && p.getAbility().name),
                abilityIndex: tryGet(() => p.abilityIndex),
                passive: tryGet(() => p.hasPassive() && p.getPassiveAbility() && p.getPassiveAbility().name) || null,
                // True when catching this exact wild Pokémon would unlock its
                // hidden ability for the account (it currently has the hidden
                // ability AND the starter line hasn't unlocked it yet). Mirrors
                // PokeRogue's setPokemonSpeciesCaught: idx!==1 || ability2 ? 1<<idx : 4.
                newHiddenUnlock: tryGet(() => {
                    const sp = p.species;
                    if (!sp) return false;
                    const starterId = (typeof sp.getRootSpeciesId === 'function')
                        ? sp.getRootSpeciesId(true) : sp.speciesId;
                    const sd = battle.gameData && battle.gameData.starterData
                        && battle.gameData.starterData[starterId];
                    const abilityAttr = sd ? (sd.abilityAttr || 0) : 0;
                    const idx = p.abilityIndex;
                    const toUnlock = (idx !== 1 || sp.ability2) ? (1 << idx) : 4;
                    return toUnlock === 4 && !(abilityAttr & 4);
                }) || false,
                moves: (p.moveset || []).map(m => m && m.getName ? m.getName() : null),
                ivs: p.ivs,
                nature: p.nature,
                status: tryGet(() => p.status && p.status.effect),
                isBoss: tryGet(() => p.isBoss()) || false,
                speed: effSpeed,
                speedStage: stages.length >= 5 ? stages[4] : 0,
                statStages: stages,  // [ATK, DEF, SPA, SPD, SPE, ACC, EVA]
                battleStats: {
                    atk: tryGet(() => p.getEffectiveStat(1)),
                    def: tryGet(() => p.getEffectiveStat(2)),
                    spa: tryGet(() => p.getEffectiveStat(3)),
                    spd: tryGet(() => p.getEffectiveStat(4)),
                },
                permanentStats: {
                    atk: tryGet(() => p.getStat(1)),
                    spa: tryGet(() => p.getStat(3)),
                },
            };
        } catch(e) { return null; }
    };

    const summarizeParty = (p) => {
        try {
            const types = (typeof p.getTypes === 'function') ? p.getTypes() : [];
            return {
                name: p.species && p.species.name && p.species.name.toLowerCase(),
                level: p.level,
                hp: p.hp,
                maxHp: tryGet(() => p.getMaxHp()),
                fainted: tryGet(() => p.isFainted()) || (p.hp <= 0),
                types: types.map(t => TYPES[t] || 'unknown'),
                ability: tryGet(() => p.getAbility() && p.getAbility().name),
                abilityIndex: tryGet(() => p.abilityIndex),
                passive: tryGet(() => p.hasPassive() && p.getPassiveAbility() && p.getPassiveAbility().name) || null,
                nature: p.nature,
                moves: (p.moveset || []).map(m => m && m.getName ? m.getName() : null),
                stats: {
                    hp:  tryGet(() => p.getStat(0)),
                    atk: tryGet(() => p.getStat(1)),
                    def: tryGet(() => p.getStat(2)),
                    spa: tryGet(() => p.getStat(3)),
                    spd: tryGet(() => p.getStat(4)),
                    spe: tryGet(() => p.getStat(5)),
                },
            };
        } catch(e) { return null; }
    };

    // Trick Room is an arena tag; check arena.tags by tagType or class name.
    const trickRoom = tryGet(() => {
        const tags = (battle.arena && battle.arena.tags) || [];
        return tags.some(t => {
            try {
                return (t && t.tagType && String(t.tagType).indexOf('TRICK_ROOM') >= 0)
                    || (t && t.constructor && t.constructor.name === 'TrickRoomTag');
            } catch(e) { return false; }
        });
    }) || false;

    // ── Starter select detection ─────────────────────────────────────────
    // The starter select runs inside the 'battle' scene as a UI mode/phase.
    // Detect via: no active wave + starterData loaded + no live enemies.
    const _active_scenes = tryGet(() =>
        Object.keys(g.scene.keys).filter(k => g.scene.keys[k].sys && g.scene.keys[k].sys.isActive())
    ) || [];

    // Diagnostic: current UI handler property names (helps tune detection in minified builds)
    const _uiHandler = tryGet(() => battle.ui && battle.ui.getHandler && battle.ui.getHandler()) || null;
    const _handler_keys = tryGet(() => _uiHandler ? Object.getOwnPropertyNames(_uiHandler) : []) || [];

    const isStarterSelect = tryGet(() => {
        if (!battle) return false;
        // No wave in progress
        const wave = battle.currentBattle && battle.currentBattle.waveIndex;
        if (wave) return false;
        // starterData must be present and non-empty (means game data is loaded)
        const starterData = battle.gameData && battle.gameData.starterData;
        if (!starterData || Object.keys(starterData).length === 0) return false;
        // No enemies active (distinguishes from wave 0 edge cases)
        const enemies = tryGet(() => battle.getEnemyField(true)) || [];
        if (enemies.length > 0) return false;
        return true;
    }) || false;

    if (isStarterSelect) {
        const starterData = tryGet(() => battle.gameData.starterData) || {};
        const unlockedIds = [];
        const wonIds = [];
        const valueReductions = {};
        const eggMovesOwned = {};
        for (const [id, d] of Object.entries(starterData)) {
            if (d && d.abilityAttr > 0) {
                const sid = parseInt(id);
                unlockedIds.push(sid);
                if (d.classicWinCount > 0) wonIds.push(sid);
                if (d.valueReduction > 0) valueReductions[sid] = d.valueReduction;
                // eggMoves is a bitmask: bit i set = egg move i unlocked (bit 3 = rare).
                if (d.eggMoves) eggMovesOwned[sid] = d.eggMoves;
            }
        }
        const selectedIds = tryGet(() => {
            if (!_uiHandler) return [];
            // Try known property names from handler key inspection
            const arr = _uiHandler.starters || _uiHandler.selectedStarters ||
                        _uiHandler.starterGens || _uiHandler.selectedPokemon;
            if (arr && arr.length !== undefined) {
                return Array.from(arr).filter(Boolean)
                    .map(s => (s.species && s.species.speciesId) || (s.speciesId))
                    .filter(Boolean);
            }
            // Fallback: scan validStarterContainers for a 'selected' or 'chosen' flag
            const containers = _uiHandler.validStarterContainers || _uiHandler.starterContainers || [];
            return Array.from(containers).filter(c => c && (c.selected || c.chosen || c.isSelected))
                .map(c => c.species && c.species.speciesId).filter(Boolean);
        }) || [];
        return {
            is_starter_select: true,
            unlocked_ids: unlockedIds,
            won_ids: wonIds,
            selected_ids: selectedIds,
            value_reductions: valueReductions,
            egg_moves_owned: eggMovesOwned,
            _handler_keys: _handler_keys,
            wave: null, player: [], enemies: [], party: [],
            _active_scenes: _active_scenes,
        };
    }

    try {
        return {
            wave: battle.currentBattle ? battle.currentBattle.waveIndex : null,
            battleType: tryGet(() => battle.currentBattle && battle.currentBattle.battleType) || 0,
            biome: tryGet(() => battle.arena && (battle.arena.biomeId != null ? battle.arena.biomeId : battle.arena.biomeType)),
            isClassic: tryGet(() => battle.gameMode && battle.gameMode.isClassic) || false,
            weather: tryGet(() => battle.arena.weather && battle.arena.weather.weatherType),
            terrain: tryGet(() => battle.arena.terrain && battle.arena.terrain.terrainType),
            trickRoom: trickRoom,
            player: battle.getPlayerField(true).map(summarize).filter(x => x),
            enemies: battle.getEnemyField(true).map(summarize).filter(x => x),
            party: tryGet(() => battle.getPlayerParty().map(summarizeParty).filter(x => x)) || [],
            _active_scenes: _active_scenes,
        };
    } catch(e) {
        return {error: String(e && e.message)};
    }
})()
"""


class _Signals(QObject):
    snapshot_changed = pyqtSignal(object)  # dict | None


class JSStateService:
    def __init__(self, page, interval_ms: int = 300, parent=None):
        self._page = page
        self._signals = _Signals()
        self.snapshot_changed = self._signals.snapshot_changed
        self._last = None
        self._timer = QTimer(parent)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        self._page.runJavaScript(EXTRACTOR_JS, self._on_result)

    def _on_result(self, snapshot):
        if snapshot is None:
            if self._last is not None:
                self._last = None
                self._signals.snapshot_changed.emit(None)
            return
        if snapshot != self._last:
            self._last = snapshot
            self._signals.snapshot_changed.emit(snapshot)
