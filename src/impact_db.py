"""
Impact Score: pre-computed offensive threat score for every final-evolution
Pokemon form. See docs/impact_score.md for full methodology.

Score = sum over all 171 type pairings of the best SE damage the optimal
4-move selection can deal to that pairing, where:
  damage = stat * base_power * (accuracy/100) * STAB * SE_multiplier

Two caches: impact_cache.json (level-up moves only) and impact_cache_egg.json
(level-up + egg moves). Toggle with set_include_egg().
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations

import requests

from app_dirs import data_path
from weakness_calc import ALL_TYPES, _effectiveness

BASE_URL = "https://pokeapi.co/api/v2"
CACHE_FILE     = data_path("impact_cache.json")
CACHE_FILE_EGG = data_path("impact_cache_egg.json")
CACHE_VERSION  = 45

# Forms omitted from all scoring (duplicates or Pokerogue-unavailable mechanics).
_EXCLUDED_FORMS: frozenset[str] = frozenset({
    "greninja-ash",         # Battle Bond — mechanic not present in Pokerogue
    "greninja-battle-bond", # alternate PokéAPI slug for the same form
    "magearna-original-mega",  # keep only magearna-mega
    "ditto",                # only Transform — no scoreable moves
    "smeargle",             # only Sketch — no scoreable moves
    "wobbuffet",            # Counter/Mirror Coat have null power in PokéAPI
    "pyukumuku",            # only Bide/Purify — no fixed-power offensive moves
})


def _is_excluded_form(form: str) -> bool:
    """True for forms that should be omitted from all analysis and scoring."""
    if form in _EXCLUDED_FORMS:
        return True
    if "-totem" in form:      # totem variants (incl. raticate-totem-alola)
        return True
    if "-gmax" in form:       # Gigantamax: cosmetic-only, same moveset as base form
        return True
    if form == "unown" or form.startswith("unown-"):  # 28 forms, all only know Hidden Power (null power)
        return True
    # Alternate ride/battle builds — same base stats, just mechanical variants.
    # Keep base koraidon / miraidon; drop everything else.
    if form.startswith("koraidon-") or form.startswith("miraidon-"):
        return True
    return False


# Paradox Pokémon (Gen 9 Scarlet/Violet + DLC) — not flagged in PokéAPI
_PARADOX_POKEMON: frozenset[str] = frozenset({
    # Past paradox (Scarlet)
    "great-tusk", "scream-tail", "brute-bonnet", "flutter-mane",
    "slither-wing", "sandy-shocks", "roaring-moon",
    "walking-wake", "gouging-fire", "raging-bolt",
    # Future paradox (Violet)
    "iron-treads", "iron-bundle", "iron-hands", "iron-jugulis",
    "iron-moth", "iron-thorns", "iron-valiant",
    "iron-leaves", "iron-boulder", "iron-crown",
})

# Moves excluded from scoring: self-damaging (fixed HP loss or faint) or too conditional
# to reliably contribute (e.g. requires a full charge turn with no incoming hit).
_EXCLUDED_MOVES: frozenset[str] = frozenset({
    "mind-blown", "steel-beam", "chloroblast",  # lose 50% max HP
    "explosion", "self-destruct", "final-gambit",  # user faints
    "focus-punch",  # charge turn; negated by any hit
    "overheat",     # -2 sp.atk after use; effectively one-shot
    "dream-eater",  # requires target to be asleep
    "sky-drop",     # complex two-turn grounding mechanic
})

_POKEROGUE_LEARNSET_URL = (
    "https://raw.githubusercontent.com/pagefaultgames/pokerogue/main"
    "/src/data/balance/pokemon-level-moves.ts"
)
_STARTERS_URL = (
    "https://raw.githubusercontent.com/pagefaultgames/pokerogue/main"
    "/src/data/balance/starters.ts"
)
_POKEROGUE_EGG_MOVES_URL = (
    "https://raw.githubusercontent.com/pagefaultgames/pokerogue/main"
    "/src/data/balance/moves/egg-moves.ts"
)
_POKEROGUE_SPECIES_URL = (
    "https://raw.githubusercontent.com/pagefaultgames/pokerogue/main"
    "/src/data/balance/pokemon-species.ts"
)

# SpeciesFormKey enum → URL slug used in PokéAPI / our cache keys
_FORM_KEY_MAP: dict[str, str] = {
    "MEGA":   "mega",
    "MEGA_X": "mega-x",
    "MEGA_Y": "mega-y",
    "MEGA_Z": "mega-z",
}

# Evolutions that don't work in Pokerogue despite PokéAPI saying they should.
# Maps Pokerogue starter slug → cache key of the actual final form to use.
_POKEROGUE_EVO_OVERRIDES: dict[str, str] = {
    "froakie": "greninja",  # Ash-Greninja not obtainable through normal evolution
}

# Starters to omit from team builder suggestions entirely.
# Use for Pokemon whose evolution situation in Pokerogue is too broken to model cleanly.
_POKEROGUE_STARTER_EXCLUSIONS: frozenset[str] = frozenset({
    "farfetchd",   # can't evolve into Sirfetch'd in Pokerogue; Kantonian form unscored
})

# All 171 unordered type pairings: 18 single + C(18,2)=153 dual
ALL_PAIRINGS: list[tuple[str, ...]] = []
for _i, _t1 in enumerate(ALL_TYPES):
    ALL_PAIRINGS.append((_t1,))
    for _t2 in ALL_TYPES[_i + 1:]:
        ALL_PAIRINGS.append((_t1, _t2))

# Pre-computed effectiveness table: (atk_type, pairing) -> multiplier
# Populated once during _build_effectiveness_table(), before any score math.
_EFF: dict[tuple, float] = {}

_db_noegg: dict[str, dict] = {}
_db_egg:   dict[str, dict] = {}
_use_egg:  bool = False

_nonleg_median: float | None = None  # memoized non-legendary impact median (cap/egg-aware)

# Owned-egg-aware scoring (team builder). Independent of the egg cache / egg slider.
_EGG_ORDER_FILE    = data_path("egg_moves_ordered.json")   # species_slug -> ordered move slugs
_RUNTIME_MOVES_FILE = data_path("runtime_egg_moves.json")  # move_slug   -> battle move dict
_egg_order:          dict[str, list[str]] | None = None
_runtime_move_cache: dict[str, dict | None] | None = None
_battle_targets_cache: list[dict] | None = None
_noegg_median:       float | None = None   # median noegg battle score (leg+paradox excluded)
_egg_aware_memo:     dict[tuple, tuple] = {}
_eff_memo_runtime:   dict = {}

_OHKO_K: float = 22.0 / 50.0  # level-50 game damage formula constant
_IMMUNE_CAP: int = 50          # hits-to-KO cap for immune matchups in bulk scoring

_move_adoptions_all:   dict[str, int] = {}  # move_name → # forms that selected it (all mode)
_move_adoptions_clean: dict[str, int] = {}  # same, clean mode (no recoil/self-reducing)

_target_order_list: list[str] = []  # opponent pool order — each entry's outcomes_vec aligns to this


def _is_self_reducing(effect: str) -> bool:
    """True if the move's effect text describes a user-side stat drop."""
    import re as _re
    e = effect.lower()
    for pat in (r"lowers?\s+the\s+user", r"lower\s+the\s+user",
                r"harshly\s+lower", r"the\s+user'?s\s+\w[\w\s-]+\s+(?:drop|lower|decreas|fall)"):
        if _re.search(pat, e):
            return True
    return False


# ─── Ability effects ──────────────────────────────────────────────────────────
# Each entry may have:
#   "off"   : list of (type_filter, category_filter, multiplier)  — attacker bonus
#   "remap" : (from_type, to_type, multiplier)  — -ate abilities (Normal→X)
#   "def"   : list of (type_filter, category_filter, multiplier)  — damage reduction
#   "immune": set of incoming move types that deal 0 damage
# None in a filter position means "any".
_IRON_FIST_MOVES: frozenset[str] = frozenset({
    "bullet-punch", "comet-punch", "dizzy-punch", "drain-punch", "dynamic-punch",
    "fire-punch", "focus-punch", "hammer-arm", "ice-hammer", "ice-punch",
    "mach-punch", "mega-punch", "meteor-mash", "plasma-fists", "power-up-punch",
    "shadow-punch", "sky-uppercut", "thunder-punch",
})

_STRONG_JAW_MOVES: frozenset[str] = frozenset({
    "bite", "bug-bite", "crunch", "fire-fang", "fishious-rend",
    "hyper-fang", "ice-fang", "jaw-lock", "poison-fang",
    "psychic-fangs", "thunder-fang",
})

_RECKLESS_MOVES: frozenset[str] = frozenset({
    "brave-bird", "double-edge", "flare-blitz", "head-charge", "head-smash",
    "submission", "take-down", "volt-tackle", "wild-charge", "wood-hammer",
})

_SOUNDPROOF_MOVES: frozenset[str] = frozenset({
    "alluring-voice", "boomburst", "bug-buzz", "chatter", "clanging-scales",
    "clangorous-soul", "clangorous-soulblaze", "confide", "disarming-voice",
    "echoed-voice", "eerie-spell", "grass-whistle", "growl", "heal-bell",
    "howl", "hyper-voice", "metal-sound", "noble-roar", "overdrive",
    "parting-shot", "perish-song", "psychic-noise", "relic-song", "roar",
    "round", "screech", "sing", "snarl", "snore", "sparkling-aria",
    "supersonic", "torch-song", "uproar",
})

_BULLETPROOF_MOVES: frozenset[str] = frozenset({
    "acid-spray", "aura-sphere", "barrage", "bullet-seed", "egg-bomb",
    "electro-ball", "energy-ball", "focus-blast", "gyro-ball", "ice-ball",
    "magnet-bomb", "mist-ball", "mud-bomb", "octazooka", "pollen-puff",
    "pyro-ball", "rock-blast", "rock-wrecker", "seed-bomb", "shadow-ball",
    "sludge-bomb", "weather-ball", "zap-cannon",
})

_SHARPNESS_MOVES: frozenset[str] = frozenset({
    "aerial-ace", "air-slash", "aqua-cutter", "behemoth-blade", "bitter-blade",
    "ceaseless-edge", "cross-poison", "cut", "fury-cutter", "kowtow-cleave",
    "leaf-blade", "night-slash", "population-bomb", "psycho-cut", "razor-leaf",
    "razor-shell", "razor-wind", "sacred-sword", "secret-sword", "slash",
    "solar-blade", "spacial-rend", "stone-axe", "x-scissor",
})

_MEGA_LAUNCHER_MOVES: frozenset[str] = frozenset({
    "aura-sphere", "dark-pulse", "dragon-pulse", "origin-pulse",
    "terrain-pulse", "water-pulse", "oblivion-wing",
})

_WIND_MOVES: frozenset[str] = frozenset({
    "bleakwind-storm", "fairy-wind", "gust", "hurricane", "icy-wind",
    "petal-blizzard", "tailwind", "twister", "whirlwind",
})

ABILITY_EFFECTS: dict[str, dict] = {
    # ── Offensive ──────────────────────────────────────────────────────────────
    "transistor":     {"off": [("electric", None,       1.5)]},
    "dragons-maw":    {"off": [("dragon",   None,       1.5)]},
    "rocky-payload":  {"off": [("rock",     None,       1.5)]},
    "water-bubble":   {"off": [("water",    None,       2.0)],
                       "def": [("fire",     None,       0.5)]},
    "sheer-force":    {"off": [(None,       None,       1.3)]},
    "hustle":         {"off": [(None,       "physical", 1.2)]},
    "huge-power":     {"off": [(None,       "physical", 2.0)]},
    "pure-power":     {"off": [(None,       "physical", 2.0)]},
    "technician":     {"power_cap":  [(60, None, None, 1.5)]},
    "iron-fist":      {"move_set":   [(_IRON_FIST_MOVES,      None, None, 1.2)]},
    "strong-jaw":     {"move_set":   [(_STRONG_JAW_MOVES,     None, None, 1.5)]},
    "reckless":       {"move_set":   [(_RECKLESS_MOVES,       None, None, 1.2)]},
    "sharpness":      {"move_set":   [(_SHARPNESS_MOVES,      None, None, 1.5)]},
    "mega-launcher":  {"move_set":   [(_MEGA_LAUNCHER_MOVES,  None, None, 1.5)]},
    "punk-rock":      {"move_set":   [(_SOUNDPROOF_MOVES,     None, None, 1.3)],
                       "def_move_set": [(_SOUNDPROOF_MOVES,   None, None, 0.5)]},
    "truant":         {"off": [(None, None, 0.5)]},
    "slow-start":     {"off": [(None, "physical", 0.5)]},
    "defeatist":      {"off": [(None, None, 0.75)]},
    "fairy-aura":     {"off": [("fairy", None, 4/3)]},
    "dark-aura":      {"off": [("dark",  None, 4/3)]},
    "parental-bond":  {"off": [(None, None, 1.25)]},
    "intrepid-sword": {"off": [(None, "physical", 1.5)]},
    "steelworker":    {"off": [("steel", None, 1.5)]},
    "neuroforce":     {"off_se": 1.25},
    "stall":          {"always_last": True},
    # ── Offensive (remap all move types) ──────────────────────────────────────
    "normalize":      {"remap_all": ("normal", 1.2)},
    # ── Offensive (charge when hit — Electric ×2 when moving second) ──────────
    "electromorphosis": {"charge_electric": 2.0},
    # ── Offensive (ruin — reduces opponent's defense stats) ────────────────────
    "beads-of-ruin":  {"off": [(None, "special",  4/3)]},
    "sword-of-ruin":  {"off": [(None, "physical", 4/3)]},
    # ── Defensive (ruin — reduces opponent's attack stats) ─────────────────────
    "vessel-of-ruin": {"def": [(None, "special",  0.75)]},
    "tablets-of-ruin":{"def": [(None, "physical", 0.75)]},
    # ── Defensive (only SE moves deal damage) ──────────────────────────────────
    "wonder-guard":   {"wonder_guard": True},
    "compound-eyes":  {"acc_mult": 1.3},
    "victory-star":   {"acc_mult": 1.1},
    # ── Offensive (STAB boost) ─────────────────────────────────────────────────
    "adaptability":   {"stab": 2.0},
    # ── Offensive (recoil negation — affects clean-mode move eligibility) ──────
    "rock-head":      {"no_recoil": True},
    # ── Offensive (pierce immunity / Ghost-type) ──────────────────────────────
    "scrappy":        {"scrappy": True},
    "minds-eye":      {"scrappy": True},
    # ── Offensive (NVE doubling) ───────────────────────────────────────────────
    "tinted-lens":    {"tinted": True},
    # -ate: Normal moves become new type + ×1.3 power; STAB re-evaluated on new type
    "aerilate":       {"remap": ("normal", "flying",    1.3)},
    "pixilate":       {"remap": ("normal", "fairy",     1.3)},
    "refrigerate":    {"remap": ("normal", "ice",       1.3)},
    # ── Defensive ──────────────────────────────────────────────────────────────
    "thick-fat":      {"def": [("fire",     None,       0.5),
                                ("ice",      None,       0.5)]},
    "fur-coat":       {"def": [(None,       "physical", 0.5)]},
    "ice-scales":     {"def": [(None,       "special",  0.5)]},
    "heatproof":      {"def": [("fire",     None,       0.5)]},
    "multiscale":     {"def": [(None,       None,       0.5)]},
    "shadow-shield":  {"def": [(None,       None,       0.5)]},
    "purifying-salt": {"def": [("ghost",    None,       0.5)]},
    "intimidate":     {"def": [(None,       "physical", 2/3)]},
    "dauntless-shield": {"def": [(None,    "physical", 2/3)]},
    "fluffy":         {"def": [(None,       "physical", 0.5),
                                ("fire",     None,       2.0)]},
    # ── Immunity ───────────────────────────────────────────────────────────────
    "well-baked-body": {"immune": {"fire"}},
    "levitate":        {"immune": {"ground"}},
    "lightning-rod":   {"immune": {"electric"}},
    "water-absorb":    {"immune": {"water"}},
    "volt-absorb":     {"immune": {"electric"}},
    "motor-drive":     {"immune": {"electric"}},
    "earth-eater":     {"immune": {"ground"}},
    "flash-fire":      {"immune": {"fire"}},
    "sap-sipper":      {"immune": {"grass"}},
    "dry-skin":        {"immune": {"water"}, "def": [("fire", None, 1.25)]},
    # ── Defensive (move-set immunity) ─────────────────────────────────────────
    "soundproof":      {"immune_move_set": _SOUNDPROOF_MOVES},
    "bulletproof":     {"immune_move_set": _BULLETPROOF_MOVES},
    "wind-rider":      {"immune_move_set": _WIND_MOVES},
    # ── Offensive (recoil negation) ────────────────────────────────────────────
    "magic-guard":     {"no_recoil": True},
    # ── Offensive (accuracy override) ─────────────────────────────────────────
    "no-guard":        {"no_guard": True},
    # ── Offensive (STAB on every move) ────────────────────────────────────────
    "protean":         {"protean": True},
    # ── Offensive (speed-conditional boost) ───────────────────────────────────
    "analytic":        {"analytic": True},
    # ── Offensive (max multi-hit) ──────────────────────────────────────────────
    "skill-link":      {"skill_link": True},
    # ── Offensive (contact moves) ──────────────────────────────────────────────
    "tough-claws":     {"off": [(None, "physical", 1.3)]},
    # ── Offensive (STAB on every move — same flag as Protean) ─────────────────
    "libero":          {"protean": True},
    # ── Offensive (per-matchup stat boost) ────────────────────────────────────
    "download":        {"download": True},
    # ── Offensive (sound → Water remap) ───────────────────────────────────────
    "liquid-voice":    {"sound_remap": "water"},
    # ── Offensive (type remap, same pattern as -ate) ───────────────────────────
    "galvanize":       {"remap": ("normal", "electric", 1.2)},
    # ── Defensive (SE damage reduction) ───────────────────────────────────────
    "filter":          {"filter_se": 0.75},
    "solid-rock":      {"filter_se": 0.75},
    "prism-armor":     {"filter_se": 0.75},
    # ── Weather setters (both sides feel the weather) ─────────────────────────
    "drizzle":        {"off": [("water", None, 1.5), ("fire",  None, 0.5)],
                       "def": [("water", None, 1.5), ("fire",  None, 0.5)],
                       "move_acc_override": {"hurricane": 100, "thunder": 100}},
    "primordial-sea": {"off": [("water", None, 1.5), ("fire",  None, 0.0)],
                       "def": [("water", None, 1.5), ("fire",  None, 0.0)],
                       "move_acc_override": {"hurricane": 100, "thunder": 100}},
    "drought":        {"off": [("fire",  None, 1.5), ("water", None, 0.5)],
                       "def": [("fire",  None, 1.5), ("water", None, 0.5)]},
    "desolate-land":  {"off": [("fire",  None, 1.5), ("water", None, 0.0)],
                       "def": [("fire",  None, 1.5), ("water", None, 0.0)]},
    # ── Terrain setters ────────────────────────────────────────────────────────
    "grassy-surge":   {"off": [("grass",    None, 1.3)],
                       "def": [("grass",    None, 1.3), ("ground", None, 0.5)]},
    "electric-surge": {"off": [("electric", None, 1.3)],
                       "def": [("electric", None, 1.3)]},
    "psychic-surge":  {"off": [("psychic",  None, 1.3)],
                       "def": [("psychic",  None, 1.3)]},
    "misty-surge":    {"def": [("dragon",   None, 0.5)]},
    # ── Immunity (water redirect) ──────────────────────────────────────────────
    "storm-drain":     {"immune": {"water"}},
}

# All abilities explicitly categorized — modeled, deferred, or intentional no-ops.
# Used to identify Pokémon whose impact scores are fully accounted for.
KNOWN_ABILITIES: frozenset[str] = frozenset(ABILITY_EFFECTS) | frozenset({
    # Intentional no-ops — no battle-math effect in this sim
    "keen-eye", "frisk", "pressure", "run-away", "gluttony", "pickup",
    "unnerve", "telepathy", "mold-breaker", "regenerator", "infiltrator",
    "weak-armor", "rattled", "damp", "prankster",
    "clear-body", "natural-cure", "synchronize", "rivalry", "anticipation",
    "unburden", "early-bird", "vital-spirit", "steadfast", "pickpocket",
    "defiant", "serene-grace", "cute-charm", "unaware", "cursed-body",
    "big-pecks", "competitive", "speed-boost", "limber", "shields-down", "water-veil",
    "shield-dust", "hyper-cutter", "klutz", "healer", "aftermath", "contrary",
    "magic-bounce", "sweet-veil", "plus", "sticky-hold", "friend-guard",
    "heavy-metal", "justified", "magnet-pull", "harvest", "stakeout",
    "illuminate", "minus", "aroma-veil",
    "trace", "light-metal", "cheek-pouch", "gooey", "tangling-hair",
    "shadow-tag", "rough-skin", "iron-barbs", "forewarn", "magician",
    "suction-cups", "white-smoke", "simple", "honey-gather", "wonder-skin",
    "flower-veil", "symbiosis", "ripen", "unseen-fist",
    "illusion", "stalwart", "steam-engine", "cud-chew", "long-reach",
    "gulp-missile", "neutralizing-gas", "commander",
    "liquid-ooze", "gale-wings", "stamina", "berserk",
    "turboblaze", "teravolt", "aura-break", "water-compaction", "merciless",
    "soul-heart", "power-of-alchemy", "cotton-down", "propeller-tail",
    "screen-cleaner", "guard-dog", "toxic-debris", "mycelium-might",
    "supersweet-syrup", "hospitality", "gorilla-tactics",
    "triage", "queenly-majesty", "dazzling", "battery", "receiver",
    "full-metal-body", "ball-fetch", "power-spot", "steely-spirit",
    "color-change", "perish-body", "curious-medicine",
    "chilling-neigh", "grim-neigh", "as-one", "good-as-gold", "costar",
    # Deferred — multi-battle scaling (1v1 sim cannot capture)
    "moxie", "beast-boost", "moody", "speed-boost", "innards-out",
    "supreme-overlord",
    # Deferred — HP-conditional (needs HP tracking inside sim)
    "overgrow", "blaze", "torrent", "swarm",
    # Deferred — needs sim architecture change
    "sturdy",
    # Deferred — needs weather mechanic
    "swift-swim", "chlorophyll", "sand-veil",
    "overcoat", "hydration", "leaf-guard", "ice-body",
    "sand-force", "snow-cloak", "rain-dish",
    "solar-power", "sand-rush", "snow-warning", "protosynthesis", "quark-drive",
    "cloud-nine", "slush-rush", "sand-stream", "orichalcum-pulse",
    "hadron-engine", "forecast", "sand-spit",
    "grass-pelt", "wind-power",
    "air-lock", "delta-stream",
    "surge-surfer", "mimicry",
    # Deferred — needs status mechanic
    "static", "own-tempo",
    "oblivious", "guts", "insomnia", "flame-body", "shed-skin", "poison-point",
    "effect-spore", "quick-feet", "poison-touch", "tangled-feet", "corrosion",
    "immunity", "magma-armor", "marvel-scale", "arena-trap", "poison-heal",
    "toxic-chain", "thermal-exchange",
    "flare-boost", "pastel-veil",
    # Deferred — needs flinch mechanic
    "inner-focus", "stench",
    # Deferred — HP-conditional / form-change
    "zen-mode", "disguise", "dancer", "power-construct",
    "mummy", "stance-change", "schooling", "battle-bond",
    "ice-face", "wandering-spirit", "mirror-armor",
    "hunger-switch", "zero-to-hero",
    "multitype", "flower-gift", "imposter",
    "wimp-out", "emergency-exit", "rks-system",
    "comatose", "bad-dreams", "toxic-boost", "flare-boost",
    "lingering-aroma", "seed-sower", "anger-shell", "opportunist",
    "armor-tail", "quick-draw", "tera-shift", "tera-shell",
    "teraform-zero", "poison-puppeteer",
    # Deferred — needs critical-hit mechanic
    "shell-armor", "sniper", "anger-point", "battle-armor", "super-luck",
})


def _off_effect(abilities: list[str], move_type: str, category: str,
                move_name: str = "", base_power: int = 0) -> tuple[str, float]:
    """Return (effective_type, power_multiplier) after applying all active abilities."""
    etype = move_type
    mult = 1.0
    for ability in abilities:
        fx = ABILITY_EFFECTS.get(ability, {})
        if "remap" in fx:
            from_t, to_t, rmult = fx["remap"]
            if etype == from_t:
                etype = to_t
                mult *= rmult
        if "remap_all" in fx:
            new_t, rmult = fx["remap_all"]
            etype = new_t
            mult *= rmult
        if "sound_remap" in fx and move_name in _SOUNDPROOF_MOVES:
            etype = fx["sound_remap"]
        for t, cat, m in fx.get("off", []):
            if (t is None or t == move_type) and (cat is None or cat == category):
                mult *= m
        for ms, t, cat, m in fx.get("move_set", []):
            if move_name and move_name in ms and (t is None or t == move_type) and (cat is None or cat == category):
                mult *= m
        for max_bp, t, cat, m in fx.get("power_cap", []):
            if 0 < base_power <= max_bp and (t is None or t == move_type) and (cat is None or cat == category):
                mult *= m
    return etype, mult


def _stab_mult(abilities: list[str]) -> float:
    """Return STAB multiplier (2.0 for Adaptability, 1.5 otherwise)."""
    for ab in abilities:
        s = ABILITY_EFFECTS.get(ab, {}).get("stab")
        if s:
            return s
    return 1.5


def _acc_mult(abilities: list[str]) -> float:
    """Return accuracy multiplier (1.3 for Compound Eyes, 1.0 otherwise)."""
    for ab in abilities:
        m = ABILITY_EFFECTS.get(ab, {}).get("acc_mult")
        if m:
            return m
    return 1.0


def _move_acc_overrides(abilities: list[str]) -> dict[str, int]:
    """Return move slug → forced accuracy from weather/ability effects (e.g. Hurricane→100 in rain)."""
    result: dict[str, int] = {}
    for ab in abilities:
        for slug, acc in ABILITY_EFFECTS.get(ab, {}).get("move_acc_override", {}).items():
            result[slug] = acc
    return result


def _def_mult(abilities: list[str], move_type: str, category: str,
              move_name: str = "") -> float:
    """Return combined incoming damage multiplier (0.0 = immune) across all abilities."""
    mult = 1.0
    for ability in abilities:
        fx = ABILITY_EFFECTS.get(ability, {})
        if move_type in fx.get("immune", set()):
            return 0.0
        if move_name and move_name in fx.get("immune_move_set", frozenset()):
            return 0.0
        for t, cat, m in fx.get("def", []):
            if (t is None or t == move_type) and (cat is None or cat == category):
                mult *= m
        for ms, t, cat, m in fx.get("def_move_set", []):
            if move_name and move_name in ms and (t is None or t == move_type) and (cat is None or cat == category):
                mult *= m
    return mult


def _pick_ability(api_abilities: list[str]) -> str | None:
    """Return the first modeled regular ability in slot order (slot1 → slot2 → hidden)."""
    for ab in api_abilities:
        if ab in ABILITY_EFFECTS:
            return ab
    return None


def _pick_known_ability(api_abilities: list[str]) -> str | None:
    """Return the first non-modeled but categorized ability in slot order (display only)."""
    for ab in api_abilities:
        if ab in KNOWN_ABILITIES and ab not in ABILITY_EFFECTS:
            return ab
    return None


_PASSIVE_URL = (
    "https://raw.githubusercontent.com/pagefaultgames/pokerogue/main"
    "/src/data/balance/passives.ts"
)
_KNOWN_REGIONS = {"alola", "galar", "hisui", "paldea"}


def _fetch_pokerogue_passives() -> dict[str, str]:
    """Return {form_slug: passive_ability_slug} parsed from Pokerogue's passives.ts.

    Regional forms like 'typhlosion-hisui' are resolved via HISUI_TYPHLOSION entries.
    Forms without an explicit entry fall back to the base species form-0 passive.
    """
    r = requests.get(_PASSIVE_URL, timeout=30)
    r.raise_for_status()

    # Parse each [SpeciesId.NAME]: { formIdx: AbilityId.NAME, ... } block
    raw: dict[str, dict[int, str]] = {}
    for species_key, forms_str in re.findall(
        r"\[SpeciesId\.(\w+)\]:\s*\{([^}]+)\}", r.text
    ):
        form_map: dict[int, str] = {}
        for idx_str, ab_key in re.findall(r"(\d+):\s*AbilityId\.(\w+)", forms_str):
            form_map[int(idx_str)] = ab_key.replace("_", "-").lower()
        if form_map:
            raw[species_key] = form_map  # e.g. "HISUI_TYPHLOSION": {0: "drought"}

    result: dict[str, str] = {}
    for species_key, form_map in raw.items():
        passive = form_map.get(0)
        if not passive:
            continue
        # Derive PokéAPI slug from SpeciesId key
        # HISUI_TYPHLOSION → "typhlosion-hisui"  |  VENUSAUR → "venusaur"
        parts = species_key.lower().split("_")
        if parts[0] in _KNOWN_REGIONS:
            slug = "-".join(parts[1:]) + "-" + parts[0]
        else:
            slug = "-".join(parts)
        result[slug] = passive
    return result


# PokeAPI does not reliably set recharge_turn / min_turns — detect from effect text.
_TWO_TURN_OVERRIDES: frozenset[str] = frozenset({
    "meteor-beam",   # PokeAPI short_effect is generic; it IS a charge-turn move
    "electro-shot",  # Gen 9; PokeAPI has no effect text yet
})
_TWO_TURN_PATTERNS = (
    "hits next turn",          # Fly, Dig, Dive, Bounce, Phantom Force
    "requires a turn to charge",  # Solar Beam, Solar Blade, Razor Wind, Ice Burn, Freeze Shock
    "charges for one turn",    # Sky Attack, Skull Bash
    "takes one turn to charge",   # (Geomancy; status so filtered anyway)
)


def _is_recharge(effect: str) -> bool:
    """True for moves that force skipping the next turn (Hyper Beam class)."""
    return "foregoes its next turn to recharge" in effect.lower()


def _is_two_turn(move_name: str, effect: str, is_recharge: bool) -> bool:
    """True for charge-turn moves (Solar Beam, Fly, Dig, etc.) that take 2 turns."""
    if is_recharge:
        return False
    if move_name in _TWO_TURN_OVERRIDES:
        return True
    e = effect.lower()
    return any(pat in e for pat in _TWO_TURN_PATTERNS)


def _is_always_skip(effect: str) -> bool:
    """True for delayed-hit moves (Future Sight, Doom Desire) — can't score reliably."""
    return "hits the target two turns later" in effect.lower()

_starters: dict[int, dict] = {}  # {national_dex_id: {name, cost, final_evo}}
_ready:     bool = False          # noegg cache ready
_ready_egg: bool = False          # egg cache ready
_egg_cap:   int  = 4              # max egg moves in optimal set (1–4)
_lock = threading.Lock()

# Nature ID → (atk_multiplier, spa_multiplier). Ordered by Pokémon nature enum (Hardy=0..Quirky=24).
# Neutral natures: Hardy(0), Docile(6), Serious(12), Bashful(18), Quirky(24)
_NATURE_MODS: tuple[tuple[float, float], ...] = (
    (1.0, 1.0),  # 0  Hardy
    (1.1, 1.0),  # 1  Lonely  (+Atk/-Def)
    (1.1, 1.0),  # 2  Brave   (+Atk/-Spe)
    (1.1, 0.9),  # 3  Adamant (+Atk/-SpA)
    (1.1, 1.0),  # 4  Naughty (+Atk/-SpD)
    (0.9, 1.0),  # 5  Bold    (+Def/-Atk)
    (1.0, 1.0),  # 6  Docile
    (1.0, 1.0),  # 7  Relaxed (+Def/-Spe)
    (1.0, 0.9),  # 8  Impish  (+Def/-SpA)
    (1.0, 1.0),  # 9  Lax     (+Def/-SpD)
    (0.9, 1.0),  # 10 Timid   (+Spe/-Atk)
    (1.0, 1.0),  # 11 Hasty   (+Spe/-Def)
    (1.0, 1.0),  # 12 Serious
    (1.0, 0.9),  # 13 Jolly   (+Spe/-SpA)
    (1.0, 1.0),  # 14 Naive   (+Spe/-SpD)
    (0.9, 1.1),  # 15 Modest  (+SpA/-Atk)
    (1.0, 1.1),  # 16 Mild    (+SpA/-Def)
    (1.0, 1.1),  # 17 Quiet   (+SpA/-Spe)
    (1.0, 1.0),  # 18 Bashful
    (1.0, 1.1),  # 19 Rash    (+SpA/-SpD)
    (0.9, 1.0),  # 20 Calm    (+SpD/-Atk)
    (1.0, 1.0),  # 21 Gentle  (+SpD/-Def)
    (1.0, 1.0),  # 22 Sassy   (+SpD/-Spe)
    (1.0, 0.9),  # 23 Careful (+SpD/-SpA)
    (1.0, 1.0),  # 24 Quirky
)


def _nature_mods(nature_id) -> tuple[float, float]:
    if not isinstance(nature_id, int) or not (0 <= nature_id < len(_NATURE_MODS)):
        return 1.0, 1.0
    return _NATURE_MODS[nature_id]


def _active_db() -> dict[str, dict]:
    return _db_egg if _use_egg else _db_noegg


def _patch_nonleg_percentiles(db: dict) -> None:
    """Recompute percentile (and percentile_caps) relative to the non-legendary pool.

    Applied in-memory after loading or building the cache. Non-legendaries no
    longer have their scores suppressed by legendaries, and legendaries that
    outperform all non-legendaries can exceed AI100 (e.g. AI110 = 10% above best
    non-leg). Runs in < 1 second — no network calls needed.
    """
    nonleg_scores = sorted(v["impact"] for v in db.values() if not v.get("legendary"))
    n_nonleg    = len(nonleg_scores)
    best_nonleg = nonleg_scores[-1] if nonleg_scores else 1.0

    for entry in db.values():
        s     = entry["impact"]
        below = sum(1 for x in nonleg_scores if x < s)
        if below >= n_nonleg:
            entry["percentile"] = round(100 + (s - best_nonleg) / best_nonleg * 100)
        else:
            entry["percentile"] = round(below / n_nonleg * 100)

    has_caps = any(v.get("impact_caps") for v in db.values())
    if not has_caps:
        return
    for cap_idx in range(4):
        cap_nonleg = sorted(
            v["impact_caps"][cap_idx]
            for v in db.values()
            if not v.get("legendary") and v.get("impact_caps")
        )
        n_cap    = len(cap_nonleg)
        best_cap = cap_nonleg[-1] if cap_nonleg else 1.0
        for entry in db.values():
            if not entry.get("impact_caps"):
                continue
            s     = entry["impact_caps"][cap_idx]
            below = sum(1 for x in cap_nonleg if x < s)
            if below >= n_cap:
                pct = round(100 + (s - best_cap) / best_cap * 100)
            else:
                pct = round(below / n_cap * 100)
            entry.setdefault("percentile_caps", [0, 0, 0, 0])
            entry["percentile_caps"][cap_idx] = pct


# ── public API ────────────────────────────────────────────────────────────────

def init(on_progress=None, on_ready=None):
    threading.Thread(
        target=_build_or_load, args=(on_progress, on_ready, False), daemon=True
    ).start()


def init_egg(on_progress=None, on_ready=None):
    """Build/load the egg-moves cache. Safe to call multiple times."""
    if _ready_egg:
        if on_ready:
            on_ready()
        return
    threading.Thread(
        target=_build_or_load, args=(on_progress, on_ready, True), daemon=True
    ).start()


def is_ready() -> bool:
    return _ready


def is_egg_ready() -> bool:
    return _ready_egg


def set_include_egg(value: bool) -> None:
    """Switch the active database. Egg db must be ready before enabling."""
    global _use_egg, _nonleg_median
    _use_egg = value and _ready_egg
    _nonleg_median = None  # active pool changed — recompute median lazily


def set_egg_cap(cap: int) -> None:
    """Set max egg moves allowed in the optimal 4-move set (1–4)."""
    global _egg_cap, _nonleg_median
    _egg_cap = max(1, min(4, cap))
    _nonleg_median = None  # capped impacts changed — recompute median lazily


def get_egg_cap() -> int:
    return _egg_cap


def _apply_cap(entry: dict) -> dict:
    """Return a copy of entry with impact/percentile adjusted for the current egg cap."""
    caps  = entry.get("impact_caps")
    pcaps = entry.get("percentile_caps")
    if not caps:
        return entry
    entry = dict(entry)
    entry["impact"] = caps[_egg_cap - 1]
    if pcaps:
        entry["percentile"] = pcaps[_egg_cap - 1]
    return entry


def get_capped_moves(name: str, cap: int) -> list[dict]:
    """Return the brute-force optimal move list for a given egg cap (1–4).

    Used by the impact table to show which moves are in the optimal set
    when the egg cap slider is below 4. Returns the uncapped 'moves' list
    when cap==4 or egg cache is not active.
    """
    if not _use_egg or cap >= 4:
        entry = _active_db().get(name.lower())
        return entry.get("moves", []) if entry else []
    egg_entry = _db_egg.get(name.lower())
    if not egg_entry:
        return []
    noegg_entry   = _db_noegg.get(name.lower(), {})
    levelup_moves = noegg_entry.get("moves", [])
    egg_moves     = egg_entry.get("egg_move_data", [])
    types  = egg_entry["types"]
    atk    = egg_entry["atk"]
    sp_atk = egg_entry["sp_atk"]

    # Build per-vector for each candidate move
    def _pv(m: dict) -> dict | None:
        if not (m.get("power") and m["power"] > 0
                and m.get("category") != "status"
                and (m.get("drain") or 0) >= 0
                and m.get("name") not in _EXCLUDED_MOVES):
            return None
        mtype = m["type"]
        stat  = atk if m["category"] == "physical" else sp_atk
        stab  = 1.5 if mtype in types else 1.0
        acc   = (m["accuracy"] or 100) / 100.0
        base  = stat * m["power"] * acc * stab
        pv: dict = {}
        for p in ALL_PAIRINGS:
            se = _EFF.get((mtype, p), 0.0)
            if se > 1.0:
                pv[p] = base * se
        return pv if pv else None

    lu_items = [(m, pv) for m in levelup_moves if (pv := _pv(m))]
    eg_items = [(m, pv) for m in egg_moves     if (pv := _pv(m))]
    n_lu     = len(lu_items)
    all_items = lu_items + eg_items
    if not all_items:
        return []

    k = min(4, len(all_items))
    best_score = -1.0
    best_combo: tuple = ()
    for combo in combinations(range(len(all_items)), k):
        if sum(1 for idx in combo if idx >= n_lu) > cap:
            continue
        curr: dict = {}
        for idx in combo:
            for p, v in all_items[idx][1].items():
                if v > curr.get(p, 0.0):
                    curr[p] = v
        score = sum(curr.values())
        if score > best_score:
            best_score = score
            best_combo = combo
    return [all_items[idx][0] for idx in best_combo]


def get_move_adoptions() -> tuple[dict[str, int], dict[str, int]]:
    """Returns (adoptions_all, adoptions_clean): move_name → # forms that selected it."""
    with _lock:
        return dict(_move_adoptions_all), dict(_move_adoptions_clean)


def get(name: str) -> dict | None:
    """Returns {score, percentile, types} or None."""
    with _lock:
        entry = _active_db().get(name.lower())
    if entry is None or not _use_egg or _egg_cap >= 4:
        return entry
    return _apply_cap(entry)


def top_n(n: int = 20, exclude_legendary: bool = False) -> list[tuple[str, float]]:
    with _lock:
        db = _active_db()
        use_cap = _use_egg and _egg_cap < 4
        items = [
            (name, (v["impact_caps"][_egg_cap - 1] if use_cap and v.get("impact_caps") else v["impact"]))
            for name, v in db.items()
            if not (exclude_legendary and v.get("legendary"))
        ]
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:n]


def _nonleg_median_impact() -> float:
    """Median impact across the non-legendary pool in the active db (cap/egg-aware).

    Memoized; invalidated by set_include_egg / set_egg_cap. This is the same
    denominator the Impact-Score browser uses to normalize its 'Impact' column.
    """
    global _nonleg_median
    if _nonleg_median is None:
        use_cap = _use_egg and _egg_cap < 4
        with _lock:
            vals = []
            for v in _active_db().values():
                if v.get("legendary") or v.get("paradox"):
                    continue
                imp = (v["impact_caps"][_egg_cap - 1]
                       if use_cap and v.get("impact_caps") else v.get("impact", 0))
                if imp > 0:
                    vals.append(imp)
        _nonleg_median = statistics.median(vals) if vals else 1.0
    return _nonleg_median


def impact_score(name: str) -> int:
    """Median-normalized battle score — the number shown in the browser 'Impact' column.

    impact / non-legendary-median * 100, so 100 = a typical non-legendary and
    e.g. Delphox Mega reads ~174. Egg-cap aware via get() and _nonleg_median_impact().
    """
    entry = get(name)
    if not entry or not entry.get("impact"):
        return 0
    med = _nonleg_median_impact()
    return round(entry["impact"] / med * 100) if med > 0 else 0


# ── Growth-rate early-game weighting ───────────────────────────────────────────
# A faster-levelling Pokémon comes online sooner on the early floors, so it's worth
# more there than its raw Impact implies; a slow leveller (Beldum → Metagross is the
# canonical case) is worth less. Factors below up-weight Impact by leveling speed,
# with Medium-slow as the 0% baseline. Keys are PokéAPI growth_rate names; unknown
# or missing → neutral 1.0.
_GROWTH_FACTORS = {
    "fast":                1.15,  # +15%
    "medium":              1.10,  # Medium-fast  +10%
    "fast-then-very-slow": 1.10,  # Fluctuating  +10%
    "slow-then-very-fast": 1.05,  # Erratic       +5%
    "medium-slow":         1.00,  # baseline       0%
    "slow":                0.90,  # -10%
}

# Display names for tooltips, keyed by PokéAPI growth_rate name.
_GROWTH_NAMES = {
    "fast":                "Fast",
    "medium":              "Medium-fast",
    "fast-then-very-slow": "Fluctuating",
    "slow-then-very-fast": "Erratic",
    "medium-slow":         "Medium-slow",
    "slow":                "Slow",
}


def growth_factor(growth_rate: str | None) -> float:
    """Early-game Impact multiplier for a PokéAPI growth_rate name (1.0 if unknown)."""
    return _GROWTH_FACTORS.get(growth_rate or "", 1.0)


def growth_badge(growth_rate: str | None):
    """(emoji, label, color) for a known growth rate, else None.

    The emoji is '' for near-baseline rates — the card shows no glyph for them but
    the tooltip still reports the leveling speed. Only the clear movers get ⚡/🐢.
    """
    gf = _GROWTH_FACTORS.get(growth_rate or "")
    if gf is None:
        return None
    label = f"{_GROWTH_NAMES.get(growth_rate, growth_rate)} leveling"
    if gf >= 1.10:
        return ("⚡", label, "#a6e3a1")
    if gf <= 0.95:
        return ("🐢", label, "#f38ba8")
    return ("", label, "#a6adc8")


# ── Owned-egg-aware scoring ────────────────────────────────────────────────────
# Computes a true Impact battle score for a starter given the *specific* egg moves
# the player owns (a subset, per the game's eggMoves bitmask) — not zero, not all.
# Sourced lazily at runtime so no cache rebuild is needed.

def _egg_move_order() -> dict[str, list[str]]:
    """species_slug -> ordered egg-move slugs (index = bitmask bit). Memoized + side-cached."""
    global _egg_order
    if _egg_order is not None:
        return _egg_order
    if os.path.exists(_EGG_ORDER_FILE):
        try:
            with open(_EGG_ORDER_FILE) as f:
                _egg_order = json.load(f)
                return _egg_order
        except Exception:
            pass
    order: dict[str, list[str]] = {}
    try:
        r = requests.get(_POKEROGUE_EGG_MOVES_URL, timeout=30)
        r.raise_for_status()
        for line in r.text.splitlines():
            sm = re.search(r'\[SpeciesId\.(\w+)\]', line)
            if not sm:
                continue
            species = sm.group(1).lower().replace("_", "-")
            moves = [mm.group(1).lower().replace("_", "-")
                     for mm in re.finditer(r'MoveId\.(\w+)', line)]
            moves = [m for m in moves if m != "none"]
            if moves:
                order[species] = moves
        with open(_EGG_ORDER_FILE, "w") as f:
            json.dump(order, f)
    except Exception as e:
        print(f"[impact_db] egg-move order fetch failed: {e}")
    _egg_order = order
    return _egg_order


def _owned_egg_move_names(species_slug: str, bitmask: int) -> list[str]:
    """Resolve an eggMoves bitmask to the owned move slugs for a species."""
    order = _egg_move_order().get((species_slug or "").lower(), [])
    return [order[i] for i in range(len(order)) if bitmask & (1 << i)]


def _runtime_move(name: str) -> dict | None:
    """Battle-ready move dict for an egg move (PokéAPI), matching _to_move_dict format.

    Memoized in-memory and persisted to a side file so repeat lookups are free.
    """
    global _runtime_move_cache
    if _runtime_move_cache is None:
        _runtime_move_cache = {}
        if os.path.exists(_RUNTIME_MOVES_FILE):
            try:
                with open(_RUNTIME_MOVES_FILE) as f:
                    _runtime_move_cache = json.load(f)
            except Exception:
                _runtime_move_cache = {}
    if name in _runtime_move_cache:
        return _runtime_move_cache[name]

    md: dict | None = None
    try:
        r = requests.get(f"{BASE_URL}/move/{name}", timeout=10)
        if r.status_code != 404:
            r.raise_for_status()
            d = r.json()
            meta   = d.get("meta") or {}
            effect = next((e["short_effect"] for e in d.get("effect_entries", [])
                           if e["language"]["name"] == "en"), "")
            recharge = _is_recharge(effect)
            two_turn = _is_two_turn(name, effect, recharge)
            raw      = d.get("power") or 0
            cat      = d.get("damage_class", {}).get("name")
            if (raw and cat != "status" and name not in _EXCLUDED_MOVES
                    and not _is_always_skip(effect)):
                pwr = float(raw)
                if recharge or two_turn:
                    pwr /= 2.0
                min_h, max_h = meta.get("min_hits") or 0, meta.get("max_hits") or 0
                if min_h and max_h:
                    pwr_max = float(raw) * max_h
                    pwr *= (min_h + max_h) / 2.0
                else:
                    pwr_max = pwr
                md = {"name": name, "type": d["type"]["name"], "power": pwr,
                      "power_max": pwr_max, "base_power": raw,
                      "accuracy": d["accuracy"], "category": cat}
    except Exception:
        md = None

    _runtime_move_cache[name] = md
    try:
        with open(_RUNTIME_MOVES_FILE, "w") as f:
            json.dump(_runtime_move_cache, f)
    except Exception:
        pass
    return md


def _reset_runtime_egg_caches() -> None:
    """Invalidate caches derived from the noegg db when it (re)loads."""
    global _battle_targets_cache, _noegg_median
    _battle_targets_cache = None
    _noegg_median = None
    _egg_aware_memo.clear()


def _battle_targets() -> list[dict]:
    """The opponent pool _compute_battle_score scores against — reconstructed from the
    noegg cache (non-legendary, non-paradox, has moves), matching the cache-build pool."""
    global _battle_targets_cache
    if _battle_targets_cache is None:
        with _lock:
            t = []
            for name, v in _db_noegg.items():
                if v.get("legendary") or v.get("paradox") or not v.get("moves"):
                    continue
                t.append({
                    "name": name, "types": v["types"], "hp": float(v["hp"]),
                    "def": float(v["defense"]), "sp_def": float(v["sp_def"]),
                    "atk": float(v["atk"]), "sp_atk": float(v["sp_atk"]),
                    "speed": v["speed"], "moves": v["moves"],
                })
        _battle_targets_cache = t
    return _battle_targets_cache


def _noegg_battle_median() -> float:
    """Median noegg battle score (leg+paradox excluded) — the denominator for the
    Impact display number. Toggle-independent so team-builder numbers stay comparable."""
    global _noegg_median
    if _noegg_median is None:
        with _lock:
            vals = [v["impact"] for v in _db_noegg.values()
                    if not v.get("legendary") and not v.get("paradox")
                    and v.get("impact", 0) > 0]
        _noegg_median = statistics.median(vals) if vals else 1.0
    return _noegg_median


def _select_egg_aware_moves(entry: dict, candidates: list[dict]) -> list[dict]:
    """Pick the best ≤4 moves (by SE coverage) from candidates — same greedy/brute-force
    objective the cache build uses to choose optimal movesets. candidates ≤ 8 → C(8,4)."""
    types, atk, sp_atk = entry["types"], entry["atk"], entry["sp_atk"]

    def _pv(m: dict) -> dict | None:
        if not (m and (m.get("power") or 0) > 0 and m.get("category") != "status"
                and (m.get("drain") or 0) >= 0 and m.get("name") not in _EXCLUDED_MOVES):
            return None
        mtype = m["type"]
        stat  = atk if m["category"] == "physical" else sp_atk
        stab  = 1.5 if mtype in types else 1.0
        acc   = (m["accuracy"] or 100) / 100.0
        base  = stat * m["power"] * acc * stab
        pv: dict = {}
        for p in ALL_PAIRINGS:
            se = _EFF.get((mtype, p), 0.0)
            if se > 1.0:
                pv[p] = base * se
        return pv or None

    items = [(m, pv) for m in candidates if (pv := _pv(m))]
    if not items:
        return candidates[:4]
    k = min(4, len(items))
    best_score, best_combo = -1.0, ()
    for combo in combinations(range(len(items)), k):
        curr: dict = {}
        for idx in combo:
            for p, v in items[idx][1].items():
                if v > curr.get(p, 0.0):
                    curr[p] = v
        s = sum(curr.values())
        if s > best_score:
            best_score, best_combo = s, combo
    return [items[idx][0] for idx in best_combo]


def egg_aware_score(final_name: str, species_slug: str, egg_bitmask: int) -> tuple[int, float, bool]:
    """Impact for `final_name` using the egg moves owned for `species_slug` (per bitmask).

    Returns (display_score, raw_battle_score, boosted). When no egg moves are owned the
    base noegg impact is returned (boosted=False). Memoized per (form, owned-set).
    """
    with _lock:
        entry = _db_noegg.get((final_name or "").lower())
    if not entry or not entry.get("moves"):
        return (0, 0.0, False)

    owned = _owned_egg_move_names(species_slug, egg_bitmask) if egg_bitmask else []
    med = _noegg_battle_median()
    if not owned:
        raw = entry.get("impact", 0.0)
        return (round(raw / med * 100) if med > 0 else 0, raw, False)

    key = ((final_name or "").lower(), frozenset(owned))
    if key in _egg_aware_memo:
        return _egg_aware_memo[key]

    move_dicts = [md for n in owned if (md := _runtime_move(n))]
    candidates = list(entry["moves"]) + move_dicts
    selected   = _select_egg_aware_moves(entry, candidates)
    raw = _compute_battle_score(entry, _battle_targets(), _eff_memo_runtime,
                                moves_override=selected)[0]
    result = (round(raw / med * 100) if med > 0 else 0, raw, True)
    _egg_aware_memo[key] = result
    return result


def impact_percentile(score: float, exclude_legendary: bool = False) -> int:
    with _lock:
        values = [
            v["impact"] for v in _active_db().values()
            if not (exclude_legendary and v.get("legendary"))
        ]
    if not values:
        return 0
    return round(sum(1 for v in values if v < score) / len(values) * 100)


def all_entries() -> dict[str, dict]:
    with _lock:
        raw = dict(_active_db())
    if not _use_egg or _egg_cap >= 4:
        return raw
    return {name: _apply_cap(entry) for name, entry in raw.items()}


def _resolve_cache_key(name: str) -> str | None:
    """Return the actual _db_noegg key that matches name, trying normalisation fallbacks.

    Handles cases where PokéAPI evo-chain slugs differ from the form key stored
    in the cache (e.g. apostrophe-stripped names like farfetch-d → farfetchd).
    Returns None when no match is found so callers can skip the entry cleanly.
    """
    n = name.lower()
    with _lock:
        if n in _db_noegg:
            return n
        stripped = n.replace("-", "")
        if stripped in _db_noegg:
            return stripped
    return None


def starters_index() -> dict[int, dict]:
    """Returns {national_dex_id: {name, cost, final_evo}} for all starter-eligible Pokemon.

    Applies _POKEROGUE_STARTER_EXCLUSIONS and _POKEROGUE_EVO_OVERRIDES at runtime —
    no cache rebuild needed, just edit the dicts and restart.
    Also normalises final_evo values so they always match actual cache keys.
    """
    with _lock:
        base = dict(_starters)
    result = {}
    for sid, info in base.items():
        if info["name"] in _POKEROGUE_STARTER_EXCLUSIONS:
            continue
        # Skip starters that are themselves Hisuian forms
        if info["name"].endswith("-hisui"):
            continue
        override = _POKEROGUE_EVO_OVERRIDES.get(info["name"])
        if override:
            info = dict(info)
            info["final_evo"] = override
        # If the cached final evo is a Hisuian form, prefer the standard evolution.
        # (Pokerogue starters evolve to their standard forms, not Hisuian variants.)
        if info["final_evo"].endswith("-hisui"):
            base_name = info["final_evo"][:-6]  # strip "-hisui"
            resolved_base = _resolve_cache_key(base_name)
            if resolved_base:
                info = dict(info)
                info["final_evo"] = resolved_base
        resolved = _resolve_cache_key(info["final_evo"])
        if resolved is None:
            continue  # no matching form in cache — skip rather than silently break
        if resolved != info["final_evo"]:
            info = dict(info)
            info["final_evo"] = resolved
        result[sid] = info
    return result


def pairing_vector(name: str) -> dict[tuple, float]:
    """Best SE damage this Pokémon can deal to each of the 171 type pairings, speed-adjusted."""
    with _lock:
        entry = _active_db().get(name.lower())
    if not entry:
        return {}
    types   = entry.get("types", [])
    atk     = entry.get("atk", 0)
    sp_atk  = entry.get("sp_atk", 0)
    sp      = entry.get("speed_pct", 50)
    factor  = 1.0 if sp >= 70 else 0.4 + 0.6 * (sp / 70)
    abs_         = [ab for ab in (entry.get("ability_used"), entry.get("passive_ability")) if ab]
    stab_base    = _stab_mult(abs_)
    acc_m        = _acc_mult(abs_)
    acc_ovr      = _move_acc_overrides(abs_)
    has_no_guard = any(ABILITY_EFFECTS.get(ab, {}).get("no_guard")   for ab in abs_)
    has_protean  = any(ABILITY_EFFECTS.get(ab, {}).get("protean")    for ab in abs_)
    has_skill_lk = any(ABILITY_EFFECTS.get(ab, {}).get("skill_link") for ab in abs_)
    result: dict[tuple, float] = {}
    for m in entry.get("moves", []):
        mtype    = m.get("type", "")
        raw_acc  = acc_ovr.get(m.get("name", ""), m.get("accuracy") or 0)
        category = m.get("category", "special")
        power    = m.get("power_max", m.get("power") or 0) if has_skill_lk else (m.get("power") or 0)
        if not power:
            continue
        etype, ab_mult = _off_effect(abs_, mtype, category, m.get("name", ""), m.get("base_power", 0))
        stat = atk if category == "physical" else sp_atk
        stab = stab_base if (has_protean or etype in types) else 1.0
        acc  = 1.0 if has_no_guard else (min(raw_acc * acc_m, 100) if raw_acc else 100) / 100.0
        base = stat * power * ab_mult * acc * stab * factor
        for pairing in ALL_PAIRINGS:
            se = _EFF.get((etype, pairing), 0.0)
            if se > 1.0:
                val = base * se
                if val > result.get(pairing, 0.0):
                    result[pairing] = val
    return result


def team_score(names: list[str]) -> float:
    """Combined offensive score for a team: sum of best SE damage per type pairing."""
    best: dict[tuple, float] = {}
    for name in names:
        for pairing, val in pairing_vector(name).items():
            if val > best.get(pairing, 0.0):
                best[pairing] = val
    return sum(best.values())


_final_evo_memo: dict[str, str] = {}


def _final_evo_for(name: str) -> str:
    """Return the cache key for this pokemon's final evolution.

    Resolves at any evolution stage:
      1. Already a final evo (in the cache) → return it.
      2. A starter base form → use the starters index final_evo.
      3. An intermediate stage (e.g. Fletchinder) → resolve via the evolution
         chain and pick the final evo present in the cache.
    Falls back to the raw name so callers receive an empty pairing_vector rather
    than crashing. Results are memoized (chain lookups can hit the network once).
    """
    # Normalize the live game's display name (e.g. "Iron Treads") to the cache's
    # slug form ("iron-treads"); otherwise multi-word mons miss the cache and get
    # scored as zero coverage/impact.
    n = name.lower().strip()
    for ch in (".", "'", "’", ":"):
        n = n.replace(ch, "")
    n = n.replace(" ", "-")
    with _lock:
        if n in _db_noegg:
            return n
    if n in _final_evo_memo:
        return _final_evo_memo[n]

    for info in starters_index().values():
        if info["name"] == n:
            _final_evo_memo[n] = info["final_evo"]
            return info["final_evo"]

    # Intermediate stage — walk the evolution chain (cached after first lookup).
    resolved = n
    try:
        import pokemon_api
        finals = [f.lower() for f in pokemon_api.fetch_final_evolutions(n)]
        with _lock:
            in_cache = [f for f in finals if f in _db_noegg]
            if in_cache:
                resolved = max(in_cache, key=lambda f: _db_noegg[f].get("bst", 0))
        if resolved == n and finals:
            resolved = finals[0]
    except Exception:
        pass
    _final_evo_memo[n] = resolved
    return resolved


def potential_team_score(names: list[str]) -> float:
    """Potential score: each member resolved to its best final evo with cached optimal moves."""
    best: dict[tuple, float] = {}
    for name in names:
        for pairing, val in pairing_vector(_final_evo_for(name)).items():
            if val > best.get(pairing, 0.0):
                best[pairing] = val
    return sum(best.values())


def best_swap(team_names: list[str], wild_name: str) -> tuple[str, float, float] | None:
    """Find which team member to replace with wild_name to maximise team score.

    Returns (member_name, current_team_score, new_team_score) or None if no
    beneficial swap exists.
    """
    current = team_score(team_names)
    best_gain    = 0.0
    best_member  = None
    best_new     = current
    for i, name in enumerate(team_names):
        proposed     = [n for j, n in enumerate(team_names) if j != i] + [wild_name]
        new          = team_score(proposed)
        gain         = new - current
        if gain > best_gain:
            best_gain   = gain
            best_member = name
            best_new    = new
    if best_member is None:
        return None
    return best_member, current, best_new


def _pi_pairing_vector(final_name: str, nature_id) -> dict:
    """Per-pairing PI contribution for one team member.

    Uses final-evo BST with nature-adjusted atk/sp_atk, cached optimal
    learnset moves, and speed percentile weighting.
    """
    entry = _db_noegg.get(final_name)
    if not entry:
        return {}
    atk_mod, spa_mod = _nature_mods(nature_id)
    types   = entry["types"]
    atk     = entry["atk"] * atk_mod
    sp_atk  = entry["sp_atk"] * spa_mod
    sp      = entry["speed_pct"]
    factor  = 1.0 if sp >= 70 else 0.4 + 0.6 * (sp / 70)
    abs_         = [ab for ab in (entry.get("ability_used"), entry.get("passive_ability")) if ab]
    stab_base    = _stab_mult(abs_)
    acc_m        = _acc_mult(abs_)
    has_no_guard = any(ABILITY_EFFECTS.get(ab, {}).get("no_guard")   for ab in abs_)
    has_protean  = any(ABILITY_EFFECTS.get(ab, {}).get("protean")    for ab in abs_)
    has_skill_lk = any(ABILITY_EFFECTS.get(ab, {}).get("skill_link") for ab in abs_)
    result: dict = {}
    for m in entry.get("moves", []):
        mtype    = m.get("type", "")
        raw_acc  = m.get("accuracy") or 0
        category = m.get("category", "special")
        power    = m.get("power_max", m.get("power") or 0) if has_skill_lk else (m.get("power") or 0)
        if not power:
            continue
        etype, ab_mult = _off_effect(abs_, mtype, category, m.get("name", ""), m.get("base_power", 0))
        stat = atk if category == "physical" else sp_atk
        stab = stab_base if (has_protean or etype in types) else 1.0
        acc  = 1.0 if has_no_guard else (min(raw_acc * acc_m, 100) if raw_acc else 100) / 100.0
        base = stat * power * ab_mult * acc * stab * factor
        for pairing in ALL_PAIRINGS:
            se = _EFF.get((etype, pairing), 0.0)
            if se > 1.0:
                val = base * se
                if val > result.get(pairing, 0.0):
                    result[pairing] = val
    return result


def best_swap_pi(
    party_snapshot: list[dict],
    candidate_name: str,
    require_positive: bool = True,
) -> dict | None:
    """Find best team slot to replace with candidate, optimising for PI delta.

    party_snapshot entries: {name, nature, stats, fainted, moves (equipped)}
    candidate_name: final-evo slug (already resolved by caller)
    require_positive: if False, returns the best swap even when PI would decrease
      (used to show delta numbers on the consider card).

    Returns dict with swap details or None if no swap can be found:
      {slot, replaced_name, replaced_final, replaced_ai, candidate_ai, pi_delta}
    """
    if not _ready or not _EFF:
        return None

    cand_entry = _db_noegg.get(candidate_name.lower())
    if not cand_entry:
        return None

    # Candidate PI vector: no nature known → use neutral BST (identical to pairing_vector)
    cand_pv = pairing_vector(candidate_name.lower())

    # Per-slot PI vectors for current party
    team_pv: list[dict] = []
    for member in party_snapshot:
        if member.get("fainted"):
            team_pv.append({})
            continue
        name       = (member.get("name") or "").lower()
        final_name = _final_evo_for(name)
        team_pv.append(_pi_pairing_vector(final_name, member.get("nature")))

    # Current team PI
    current_best: dict = {}
    for pv in team_pv:
        for p, v in pv.items():
            if v > current_best.get(p, 0.0):
                current_best[p] = v
    current_pi = sum(current_best.values())

    # Try each swap (skip fainted slots only if live members exist)
    any_alive = any(not m.get("fainted") for m in party_snapshot)
    best_pi_delta = 0.0 if require_positive else float('-inf')
    best_slot: int | None = None
    for i, member in enumerate(party_snapshot):
        if any_alive and member.get("fainted"):
            continue
        new_best: dict = {}
        for j, pv in enumerate(team_pv):
            src = cand_pv if j == i else pv
            for p, v in src.items():
                if v > new_best.get(p, 0.0):
                    new_best[p] = v
        new_pi    = sum(new_best.values())
        pi_delta  = new_pi - current_pi
        if pi_delta > best_pi_delta:
            best_pi_delta = pi_delta
            best_slot     = i

    if best_slot is None:
        return None

    replaced       = party_snapshot[best_slot]
    replaced_name  = (replaced.get("name") or "").lower()
    replaced_final = _final_evo_for(replaced_name)
    replaced_entry = _db_noegg.get(replaced_final)
    replaced_ai    = replaced_entry["percentile"] if replaced_entry else 0

    return {
        "slot":          best_slot,
        "replaced_name": replaced_name,
        "replaced_final": replaced_final,
        "replaced_ai":   replaced_ai,
        "candidate_ai":  cand_entry["percentile"],
        "pi_delta":      best_pi_delta,
    }


# ── Coverage (checks / counters) ──────────────────────────────────────────────
#
# Outcome chars in the per-pokemon outcomes_vec string (length = pool size):
#   'Z' = ZDW (counter — won without taking damage)
#   'W' = DW  (check — won but took damage)
#   'L' = DL  (loss — dealt damage but couldn't KO first)
#   'l' = ZDL (loss — never dealt damage)
#   '-' = draw / unscoreable
#
# A team checks an opponent if at least one member's char is Z or W.
# A team counters an opponent if at least one member's char is Z.

_CHECK_CHARS = frozenset({'Z', 'W'})
_COUNTER_CHARS = frozenset({'Z'})

# Per-matchup "win quality" q = (a_final − b_final + 1) / 2 ∈ [0, 1] — exactly the
# term summed into the battle score. q > 0.5 ⟺ win, and its magnitude says how
# dominant the win is, so it ranks which member is the *strongest* answer to a given
# opponent. Stored as one printable char per target (94 levels) in quality_vec,
# aligned to _target_order_list just like outcomes_vec.
_QUALITY_LEVELS = 93  # chr(33)='!' .. chr(126)='~'


def _quality_char(q: float) -> str:
    lvl = round(max(0.0, min(1.0, q)) * _QUALITY_LEVELS)
    return chr(33 + lvl)


def _quality_level(c: str) -> int:
    """Decode a quality_vec char back to its 0.._QUALITY_LEVELS integer level."""
    return ord(c) - 33


def _team_outcomes_vectors(team_names: list[str]) -> tuple[list[str], list[str]]:
    """Resolve each team member to its projected (final-evo) outcomes_vec.

    Returns (projected_names, vectors) — same length as team_names. Missing or
    empty entries map to "" vectors so position indexing matches the team.
    """
    projected: list[str] = []
    vectors:   list[str] = []
    for name in team_names:
        n = (name or "").lower()
        if not n:
            projected.append("")
            vectors.append("")
            continue
        final = _final_evo_for(n)
        with _lock:
            entry = _db_noegg.get(final)
        projected.append(final)
        vectors.append(entry.get("outcomes_vec", "") if entry else "")
    return projected, vectors


def team_coverage(team_names: list[str]) -> dict:
    """Team checks/counters coverage against the impact opponent pool.

    Each member is resolved to its projected (final-evo) form before lookup.
    The opponent pool is the same non-legendary, non-paradox set used by
    `_compute_battle_score` during the cache build.

    Returns:
      checks:     int — opponents the team has at least one check (W or Z) against
      counters:   int — opponents the team has at least one counter (Z) against
      uncovered:  list[str] — opponent form names with no team check
      pool_size:  int
      per_member: list[dict] parallel to team_names; each entry:
                  {name, projected_name, checks, counters,
                   unique_checks, unique_counters}
                  unique_* = opponents only this member checks/counters
                  (i.e. removing the member loses that coverage tier)
    """
    if not _ready or not _target_order_list:
        return {"checks": 0, "counters": 0, "uncovered": [],
                "pool_size": 0, "per_member": []}

    projected, vectors = _team_outcomes_vectors(team_names)
    n = len(_target_order_list)

    checks = counters = 0
    uncovered: list[str] = []
    per_member = [
        {"name": team_names[i] or "", "projected_name": projected[i],
         "checks": 0, "counters": 0,
         "unique_checks": 0, "unique_counters": 0}
        for i in range(len(team_names))
    ]

    for i in range(n):
        check_members:   list[int] = []
        counter_members: list[int] = []
        for m_idx, v in enumerate(vectors):
            if i >= len(v):
                continue
            c = v[i]
            if c == 'Z':
                check_members.append(m_idx)
                counter_members.append(m_idx)
                per_member[m_idx]["checks"]   += 1
                per_member[m_idx]["counters"] += 1
            elif c == 'W':
                check_members.append(m_idx)
                per_member[m_idx]["checks"] += 1

        if check_members:
            checks += 1
        else:
            uncovered.append(_target_order_list[i])
        if counter_members:
            counters += 1
        if len(check_members) == 1:
            per_member[check_members[0]]["unique_checks"] += 1
        if len(counter_members) == 1:
            per_member[counter_members[0]]["unique_counters"] += 1

    return {
        "checks":     checks,
        "counters":   counters,
        "uncovered":  uncovered,
        "pool_size":  n,
        "per_member": per_member,
    }


def _assign_from_vectors(team_names, projected, vectors, qvectors, impacts, opp_names) -> dict:
    """Shared partition: assign each opponent to its single strongest team member.

    vectors/qvectors/opp_names must all align to the same opponent ordering.
    """
    per_member = [
        {"name": team_names[i] or "", "projected_name": projected[i],
         "wins": 0, "owned": 0, "owned_names": []}
        for i in range(len(team_names))
    ]
    no_one_wins = 0
    gaps: list[str] = []
    n = len(opp_names)

    for i in range(n):
        best_key = None
        best_m   = -1
        for m_idx, v in enumerate(vectors):
            if i >= len(v):
                continue
            c = v[i]
            is_win = c in _CHECK_CHARS
            if is_win:
                per_member[m_idx]["wins"] += 1
            qv = qvectors[m_idx]
            qlvl = _quality_level(qv[i]) if i < len(qv) else 0
            # Sort key: any winner outranks any non-winner; among winners rank by
            # quality, then prefer a clean counter (Z), then higher overall Impact.
            key = (1 if is_win else 0, qlvl, 1 if c == 'Z' else 0, impacts[m_idx])
            if best_key is None or key > best_key:
                best_key = key
                best_m   = m_idx

        opp = opp_names[i]
        if best_m >= 0 and best_key[0] == 1:   # the strongest member actually wins
            per_member[best_m]["owned"] += 1
            per_member[best_m]["owned_names"].append(opp)
        else:
            no_one_wins += 1
            gaps.append(opp)

    return {
        "pool_size":   n,
        "no_one_wins": no_one_wins,
        "gaps":        gaps,
        "per_member":  per_member,
    }


def team_counter_assignment(team_names: list[str],
                            team_moves: list[list[dict]] | None = None) -> dict:
    """Partition the opponent pool by which team member is the *strongest* answer.

    For every opponent each member is scored by win quality (quality_vec); the
    opponent is assigned to the single member whose win is strongest. Opponents no
    member beats (no Z/W from anyone) fall into the 'no one wins' bucket. The
    per-member owned counts plus no_one_wins sum to pool_size — a clean partition,
    unlike team_coverage's overlapping checks/counters tallies.

    By default each member is scored with its cached *optimal* moveset. Pass
    `team_moves` (parallel to team_names; each a list of current move dicts with
    keys name/type/category/power/accuracy) to score members by their *currently
    equipped* moves instead — a member with no damaging current moves falls back to
    its optimal moveset so it isn't dropped to zero before moves have loaded.

    Degrades gracefully before a cache rebuild: if quality_vec is absent the ranking
    falls back to outcome category (Z>W) then member Impact.

    Returns:
      pool_size:    int
      no_one_wins:  int — opponents beaten by no member
      gaps:         list[str] — those opponent form names
      per_member:   list[dict] parallel to team_names; each:
                    {name, projected_name, wins, owned, owned_names}
                    wins         = opponents this member beats on its own (Z or W)
                    owned/_names = opponents this member is the strongest answer to
                                   (its exclusive, best-on-team coverage)
    """
    if not _ready or not _target_order_list:
        return {"pool_size": 0, "no_one_wins": 0, "gaps": [], "per_member": []}

    if team_moves is None:
        projected, vectors = _team_outcomes_vectors(team_names)
        qvectors: list[str] = []
        impacts:  list[float] = []
        for final in projected:
            with _lock:
                entry = _db_noegg.get(final) if final else None
            qvectors.append(entry.get("quality_vec", "") if entry else "")
            impacts.append(entry.get("impact", 0.0) if entry else 0.0)
        return _assign_from_vectors(team_names, projected, vectors, qvectors,
                                    impacts, _target_order_list)

    # Current-moveset path: recompute each member's outcome/quality vectors against
    # the runtime opponent pool using its equipped moves.
    targets = _get_runtime_targets()
    eff_memo: dict = {}
    projected: list[str] = []
    vectors:   list[str] = []
    qvectors:  list[str] = []
    impacts:   list[float] = []
    for i, name in enumerate(team_names):
        nm = (name or "").lower()
        if not nm:
            projected.append(""); vectors.append(""); qvectors.append(""); impacts.append(0.0)
            continue
        final = _final_evo_for(nm)
        with _lock:
            attacker = _db_noegg.get(final)
        projected.append(final)
        if not attacker:
            vectors.append(""); qvectors.append(""); impacts.append(0.0)
            continue
        impacts.append(attacker.get("impact", 0.0))
        cur = team_moves[i] if i < len(team_moves) else None
        if cur:
            atk_moves = [
                {**mv, "power": p, "accuracy": mv.get("accuracy") or 0}
                for mv in cur if mv and (p := (mv.get("power") or 0)) > 0
            ]
        else:
            atk_moves = attacker.get("moves", [])  # no current data → optimal fallback
        res = _compute_battle_score(attacker, targets, eff_memo, moves_override=atk_moves)
        vectors.append(res[6])
        qvectors.append(res[7])

    opp_names = [t["name"] for t in targets]
    return _assign_from_vectors(team_names, projected, vectors, qvectors, impacts, opp_names)


_runtime_targets: list[dict] | None = None


def _get_runtime_targets() -> list[dict]:
    """Opponent pool as target dicts for runtime battle sims, aligned to
    _target_order_list. Built once from the no-egg cache and memoized."""
    global _runtime_targets
    if _runtime_targets is not None:
        return _runtime_targets
    targets: list[dict] = []
    with _lock:
        for name in _target_order_list:
            e = _db_noegg.get(name)
            if not e:
                continue
            targets.append({
                "name":   name,
                "types":  e["types"],
                "atk":    e["atk"],
                "sp_atk": e["sp_atk"],
                "speed":  e["speed"],
                "hp":     e["hp"],
                "def":    e["defense"],
                "sp_def": e["sp_def"],
                "moves":  e.get("moves", []),
            })
    _runtime_targets = targets
    return targets


def team_current_counters(team_moves: list[tuple[str, list[dict]]]) -> int:
    """Opponents the team counters (clean win, Z) using each member's *current*
    equipped moves rather than its cached optimal moveset.

    Each member is resolved to its projected (final-evo) stats/types/abilities —
    the only difference from `team_coverage`'s counters is the moveset. An
    opponent counts if at least one member cleanly wins (Z) against it.

    team_moves: list of (member_name, current_move_dicts). A move dict needs
    keys: name, type, category, power, accuracy (base_power/power_max optional).
    """
    if not _ready or not _target_order_list:
        return 0
    targets = _get_runtime_targets()
    if not targets:
        return 0
    countered = [False] * len(targets)
    eff_memo: dict = {}
    for name, moves in team_moves:
        n = (name or "").lower()
        if not n or not moves:
            continue
        final = _final_evo_for(n)
        with _lock:
            attacker = _db_noegg.get(final)
        if not attacker:
            continue
        # Keep only damaging moves and coerce numeric fields — status moves carry
        # power/accuracy = None, which the damage math can't multiply.
        atk_moves = [
            {**mv, "power": p, "accuracy": mv.get("accuracy") or 0}
            for mv in moves if mv and (p := (mv.get("power") or 0)) > 0
        ]
        if not atk_moves:
            continue
        vec = _compute_battle_score(attacker, targets, eff_memo, moves_override=atk_moves)[6]
        for j, c in enumerate(vec):
            if c == 'Z':
                countered[j] = True
    return sum(countered)


def swap_coverage_delta(
    team_names: list[str], slot_to_replace: int, candidate_name: str
) -> dict:
    """Coverage change if candidate_name replaces team_names[slot_to_replace].

    Returns:
      delta_checks, delta_counters:        signed ints
      new_total_checks, new_total_counters: post-swap absolute totals
      newly_covered:    list[str] — opponents now checked that weren't before
      newly_lost:       list[str] — opponents no longer checked
      newly_countered:  list[str] — opponents now countered (Z) that weren't before
      lost_counters:    list[str] — opponents no longer countered (may still be checked)
    """
    empty = {"delta_checks": 0, "delta_counters": 0,
             "new_total_checks": 0, "new_total_counters": 0,
             "newly_covered": [], "newly_lost": [],
             "newly_countered": [], "lost_counters": []}
    if not _ready or not _target_order_list:
        return empty
    if slot_to_replace < 0 or slot_to_replace >= len(team_names):
        return empty

    new_team = list(team_names)
    new_team[slot_to_replace] = candidate_name

    _, old_vecs = _team_outcomes_vectors(team_names)
    _, new_vecs = _team_outcomes_vectors(new_team)

    def best(vectors: list[str], i: int) -> str:
        b = ''
        for v in vectors:
            if i >= len(v):
                continue
            c = v[i]
            if c == 'Z':
                return 'Z'
            if c == 'W':
                b = 'W'
            elif not b and c in ('L', 'l', '-'):
                b = c
        return b

    new_total_checks = new_total_counters = 0
    old_total_checks = old_total_counters = 0
    newly_covered:   list[str] = []
    newly_lost:      list[str] = []
    newly_countered: list[str] = []
    lost_counters:   list[str] = []

    for i in range(len(_target_order_list)):
        o = best(old_vecs, i)
        n = best(new_vecs, i)
        if o in _CHECK_CHARS:
            old_total_checks += 1
        if o in _COUNTER_CHARS:
            old_total_counters += 1
        if n in _CHECK_CHARS:
            new_total_checks += 1
        if n in _COUNTER_CHARS:
            new_total_counters += 1
        if n in _CHECK_CHARS and o not in _CHECK_CHARS:
            newly_covered.append(_target_order_list[i])
        elif o in _CHECK_CHARS and n not in _CHECK_CHARS:
            newly_lost.append(_target_order_list[i])
        if n in _COUNTER_CHARS and o not in _COUNTER_CHARS:
            newly_countered.append(_target_order_list[i])
        elif o in _COUNTER_CHARS and n not in _COUNTER_CHARS:
            lost_counters.append(_target_order_list[i])

    return {
        "delta_checks":      new_total_checks - old_total_checks,
        "delta_counters":    new_total_counters - old_total_counters,
        "new_total_checks":  new_total_checks,
        "new_total_counters": new_total_counters,
        "newly_covered":     newly_covered,
        "newly_lost":        newly_lost,
        "newly_countered":   newly_countered,
        "lost_counters":     lost_counters,
    }


def best_coverage_swap(
    team_names: list[str], candidate_name: str, require_positive: bool = True,
    locked: set[str] | None = None, max_replaced_score: float | None = None,
) -> dict | None:
    """Find the team slot to replace with candidate_name that maximises Δchecks.

    Tiebreak by Δcounters. Returns a dict with the swap_coverage_delta payload
    plus 'slot' (the index to replace) and 'replaced_name', or None if no
    candidate slot can be evaluated (or, when require_positive, no positive
    Δchecks swap exists).

    `locked` is a set of lowercased species names the player has marked as keepers;
    those slots are never offered as the one to drop.

    `max_replaced_score`, when given, skips any slot whose final-evo Impact score
    exceeds it — i.e. never suggest dropping a Pokémon stronger than the candidate.
    Folding this guard into the search (rather than only checking the single top
    swap) keeps the suggestion stable: removing a too-strong slot from contention
    no longer flips a different, valid swap into view.
    """
    if not _ready or not _target_order_list:
        return None
    if not team_names:
        return None

    best: dict | None = None
    best_key = (0, 0) if require_positive else (-(10 ** 9), -(10 ** 9))
    for i, name in enumerate(team_names):
        if not name:
            continue
        if locked and name.lower() in locked:
            continue
        if max_replaced_score is not None and \
                impact_score(_final_evo_for(name.lower())) > max_replaced_score:
            continue
        delta = swap_coverage_delta(team_names, i, candidate_name)
        key = (delta["delta_checks"], delta["delta_counters"])
        if key > best_key:
            best_key = key
            best = {**delta, "slot": i, "replaced_name": name}
    return best


def matchup_details(name: str) -> list[dict]:
    """Per-matchup battle detail for one Pokémon vs every non-legendary pool target.

    Returns a list of dicts — one per opponent — with keys:
      opponent, opponent_types, move_used, move_against,
      outcome (ZDW/DW/DL/ZDL/Draw), pohko_a, pohko_b, a_final, b_final.
    Replicates _compute_battle_score logic exactly, including all modeled abilities.
    """
    with _lock:
        entry = _active_db().get(name.lower())
        if not entry:
            return []
        pool = {
            k: v for k, v in _active_db().items()
            if not v.get("legendary") and not v.get("paradox") and v.get("moves")
        }

    a_types  = entry["types"]
    a_atk    = entry["atk"]
    a_sp_atk = entry["sp_atk"]
    a_speed  = entry["speed"]
    a_hp     = float(entry["hp"])
    a_def    = float(entry["defense"])
    a_sp_def = float(entry["sp_def"])
    a_moves  = entry.get("moves", [])
    a_abs: list[str] = [ab for ab in (entry.get("ability_used"), entry.get("passive_ability")) if ab]

    _scrappy  = any(ABILITY_EFFECTS.get(ab, {}).get("scrappy")    for ab in a_abs)
    _tinted   = any(ABILITY_EFFECTS.get(ab, {}).get("tinted")     for ab in a_abs)
    _no_guard = any(ABILITY_EFFECTS.get(ab, {}).get("no_guard")   for ab in a_abs)
    _protean  = any(ABILITY_EFFECTS.get(ab, {}).get("protean")    for ab in a_abs)
    _analytic = any(ABILITY_EFFECTS.get(ab, {}).get("analytic")   for ab in a_abs)
    _skill_lk = any(ABILITY_EFFECTS.get(ab, {}).get("skill_link") for ab in a_abs)
    _download      = any(ABILITY_EFFECTS.get(ab, {}).get("download")      for ab in a_abs)
    _off_se        = max((ABILITY_EFFECTS.get(ab, {}).get("off_se", 1.0)   for ab in a_abs), default=1.0)
    _always_last   = any(ABILITY_EFFECTS.get(ab, {}).get("always_last")    for ab in a_abs)
    _charge_elec   = max((ABILITY_EFFECTS.get(ab, {}).get("charge_electric", 1.0) for ab in a_abs), default=1.0)
    _wonder_guard  = any(ABILITY_EFFECTS.get(ab, {}).get("wonder_guard")   for ab in a_abs)
    _acc_m         = _acc_mult(a_abs)
    _acc_ovr       = _move_acc_overrides(a_abs)

    eff_memo: dict = {}
    _EPS = 1e-9
    results: list[dict] = []

    for tgt_name, tgt_e in pool.items():
        t_types  = tgt_e["types"]
        t_hp     = float(tgt_e["hp"])
        t_def    = float(tgt_e["defense"])
        t_sp_def = float(tgt_e["sp_def"])
        t_atk    = float(tgt_e["atk"])
        t_sp_atk = float(tgt_e["sp_atk"])
        t_speed  = tgt_e["speed"]
        t_moves  = tgt_e.get("moves", [])

        analytic_mult = 1.3 if _analytic and a_speed < t_speed else 1.0
        dl_phys_mult  = 1.5 if _download and t_def < t_sp_def else 1.0
        dl_spec_mult  = 1.5 if _download and t_sp_def <= t_def else 1.0

        # A's best P(OHKO) vs B
        pohko_a = 0.0
        best_move_a:       str | None = None
        best_move_a_etype: str | None = None
        for m in a_moves:
            mtype = m["type"]
            cat   = m["category"]
            etype, ab_mult = _off_effect(a_abs, mtype, cat, m.get("name", ""), m.get("base_power", 0))
            k = (etype, tuple(t_types))
            eff = eff_memo.get(k)
            if eff is None:
                eff = _effectiveness(etype, t_types)
                eff_memo[k] = eff
            if _scrappy and eff == 0.0 and etype in {"normal", "fighting"} and "ghost" in t_types:
                eff = 1.0
            if _tinted and 0 < eff < 1.0:
                eff *= 2.0
            if eff > 0:
                stat = a_atk if cat == "physical" else a_sp_atk
                stab = _stab_mult(a_abs) if (_protean or etype in a_types) else 1.0
                raw_acc = _acc_ovr.get(m.get("name", ""), m["accuracy"] or 0)
                acc = 1.0 if _no_guard else (min(raw_acc * _acc_m, 100) if raw_acc else 100) / 100.0
                pwr     = m.get("power_max", m["power"]) if _skill_lk else m["power"]
                dl_mult = dl_phys_mult if cat == "physical" else dl_spec_mult
                charge  = _charge_elec if etype == "electric" and a_speed < t_speed else 1.0
                base    = stat * pwr * ab_mult * analytic_mult * dl_mult * charge * acc * stab
                if eff > 1.0:
                    base *= _off_se
                avg_def = t_def if cat == "physical" else t_sp_def
                pohko = min(base * eff * _OHKO_K / (t_hp * avg_def), 1.0)
                if pohko > pohko_a:
                    pohko_a    = pohko
                    best_move_a      = m["name"]
                    best_move_a_etype = etype

        # B's best P(OHKO) vs A
        pohko_b = 0.0
        best_move_b: str | None = None
        best_move_b_mtype: str | None = None
        for m in t_moves:
            mtype = m["type"]
            cat   = m["category"]
            dmg_mult = _def_mult(a_abs, mtype, cat, m.get("name", ""))
            if dmg_mult == 0.0:
                continue
            k = (mtype, tuple(a_types))
            eff = eff_memo.get(k)
            if eff is None:
                eff = _effectiveness(mtype, a_types)
                eff_memo[k] = eff
            if eff > 0:
                if _wonder_guard and eff <= 1.0:
                    continue  # Wonder Guard: only SE moves deal damage
                if eff > 1.0:
                    for _ab in a_abs:
                        dmg_mult *= ABILITY_EFFECTS.get(_ab, {}).get("filter_se", 1.0)
                stat = t_atk if cat == "physical" else t_sp_atk
                stab = 1.5 if mtype in t_types else 1.0
                acc  = (m["accuracy"] or 100) / 100.0
                base = stat * m["power"] * acc * stab
                avg_def = a_def if cat == "physical" else a_sp_def
                pohko = min(base * dmg_mult * eff * _OHKO_K / (a_hp * avg_def), 1.0)
                if pohko > pohko_b:
                    pohko_b           = pohko
                    best_move_b       = m["name"]
                    best_move_b_mtype = mtype

        a_final, b_final = _simulate_battle(pohko_a, pohko_b, 0 if _always_last else a_speed, t_speed)

        if b_final < _EPS:
            outcome = "ZDW" if a_final >= 1.0 - _EPS else "DW"
        elif a_final < _EPS:
            outcome = "ZDL" if b_final >= 1.0 - _EPS else "DL"
        else:
            outcome = "Draw"

        results.append({
            "opponent":       tgt_name,
            "opponent_types": t_types,
            "a_types":        a_types,
            "a_speed":        a_speed,
            "b_speed":        t_speed,
            "move_used":      best_move_a or "—",
            "move_a_type":    best_move_a_etype or "",
            "move_against":   best_move_b or "—",
            "move_b_type":    best_move_b_mtype or "",
            "outcome":        outcome,
            "pohko_a":        pohko_a,
            "pohko_b":        pohko_b,
            "a_final":        a_final,
            "b_final":        b_final,
        })

    return results


# ── score computation ─────────────────────────────────────────────────────────

def _best_capped_score(
    types: list[str], atk: int, sp_atk: int,
    levelup_moves: list[dict], egg_moves: list[dict], max_egg: int,
) -> float:
    """Brute-force optimal 4-move coverage score with at most max_egg from egg_moves.

    Uses combinations (C(n,4)) — feasible because n ≤ 8 (4 levelup + 4 egg).
    """
    def _pv(m: dict) -> dict | None:
        if not (m.get("power") and m["power"] > 0
                and m.get("category") != "status"
                and (m.get("drain") or 0) >= 0
                and m.get("name") not in _EXCLUDED_MOVES):
            return None
        mtype = m["type"]
        stat  = atk if m["category"] == "physical" else sp_atk
        stab  = 1.5 if mtype in types else 1.0
        acc   = (m["accuracy"] or 100) / 100.0
        base  = stat * m["power"] * acc * stab
        pv: dict = {}
        for p in ALL_PAIRINGS:
            se = _EFF.get((mtype, p), 0.0)
            if se > 1.0:
                pv[p] = base * se
        return pv if pv else None

    lu_pvs = [pv for m in levelup_moves if (pv := _pv(m))]
    eg_pvs = [pv for m in egg_moves     if (pv := _pv(m))]
    n_lu   = len(lu_pvs)
    all_pvs = lu_pvs + eg_pvs
    if not all_pvs:
        return 0.0

    k = min(4, len(all_pvs))
    best = 0.0
    for combo in combinations(range(len(all_pvs)), k):
        if sum(1 for i in combo if i >= n_lu) > max_egg:
            continue
        curr: dict = {}
        for i in combo:
            for p, v in all_pvs[i].items():
                if v > curr.get(p, 0.0):
                    curr[p] = v
        score = sum(curr.values())
        if score > best:
            best = score
    return best


def _build_effectiveness_table():
    """Warm _EFF with all 18 * 171 = 3078 entries. Triggers 18 API calls."""
    for atk in ALL_TYPES:
        for pairing in ALL_PAIRINGS:
            _EFF[(atk, pairing)] = _effectiveness(atk, list(pairing))


def _compute_score(
    pokemon_types: list[str],
    atk: int,
    sp_atk: int,
    moves: list[dict],
    targets: list[dict],
    eff_memo: dict,
    abilities: list[str] | None = None,
) -> tuple[float, list[dict]]:
    """Returns (coverage_score, selected_moves) via pairwise individual-target scoring.

    coverage_score = Σ best P(OHKO) against each SE-vulnerable target in the pool.
    Greedy submodular moveset selection picks up to 4 moves (≥63% of optimal).
    """
    damaging = [
        m for m in moves
        if m.get("power") and m["power"] > 0 and m.get("category") != "status"
    ]
    if not damaging:
        return 0.0, []

    _abs      = abilities or []
    _acc_m    = _acc_mult(_abs)
    _acc_ovr  = _move_acc_overrides(_abs)
    _no_guard = any(ABILITY_EFFECTS.get(ab, {}).get("no_guard")   for ab in _abs)
    _protean  = any(ABILITY_EFFECTS.get(ab, {}).get("protean")    for ab in _abs)
    _skill_lk = any(ABILITY_EFFECTS.get(ab, {}).get("skill_link") for ab in _abs)
    _download = any(ABILITY_EFFECTS.get(ab, {}).get("download")   for ab in _abs)
    _off_se   = max((ABILITY_EFFECTS.get(ab, {}).get("off_se", 1.0) for ab in _abs), default=1.0)
    # (move, se_pv, base_power) — se_pv empty means no SE targets for this move
    move_pv: list[tuple[dict, dict[int, float], float]] = []
    for m in damaging:
        mtype = m["type"]
        cat   = m["category"]
        etype, ab_mult = _off_effect(_abs, mtype, cat, m.get("name", ""), m.get("power") or 0)
        stat  = atk if cat == "physical" else sp_atk
        stab  = _stab_mult(_abs) if (_protean or etype in pokemon_types) else 1.0
        raw_acc = _acc_ovr.get(m.get("name", ""), m["accuracy"] or 0)
        acc     = 1.0 if _no_guard else (min(raw_acc * _acc_m, 100) if raw_acc else 100) / 100.0
        pwr = float(m["power"] or 0)
        if m.get("recharge") or m.get("two_turn"):
            pwr /= 2.0
        min_h, max_h = m.get("min_hits") or 0, m.get("max_hits") or 0
        if min_h and max_h:
            pwr *= max_h if _skill_lk else (min_h + max_h) / 2.0
        base  = stat * pwr * ab_mult * acc * stab

        pv: dict[int, float] = {}
        for t_idx, tgt in enumerate(targets):
            types_key = (etype, tuple(tgt["types"]))
            eff = eff_memo.get(types_key)
            if eff is None:
                eff = _effectiveness(etype, tgt["types"])
                eff_memo[types_key] = eff
            if eff > 1.0:
                dl_mult = 1.5 if _download and (
                    (cat == "physical" and tgt["def"] < tgt["sp_def"]) or
                    (cat != "physical" and tgt["sp_def"] <= tgt["def"])
                ) else 1.0
                avg_def = tgt["def"] if cat == "physical" else tgt["sp_def"]
                pv[t_idx] = min(base * dl_mult * _off_se * eff * _OHKO_K / (tgt["hp"] * avg_def), 1.0)
        move_pv.append((m, pv, base))

    if not move_pv:
        return 0.0, []

    # Greedy moveset: pick up to 4 moves by marginal SE gain
    se_pool = [(m, pv, base) for m, pv, base in move_pv if pv]
    current_best: dict[int, float] = {}
    selected: list[dict] = []
    for _ in range(min(4, len(se_pool))):
        best_gain = 0.0
        best_idx  = -1
        for idx, (_, se_pv, _) in enumerate(se_pool):
            gain = sum(max(0.0, v - current_best.get(t, 0.0)) for t, v in se_pv.items())
            if gain > best_gain:
                best_gain = gain
                best_idx  = idx
        if best_idx == -1:
            break
        m, se_pv, _ = se_pool[best_idx]
        for t, v in se_pv.items():
            if v > current_best.get(t, 0.0):
                current_best[t] = v
        selected.append(m)
        se_pool.pop(best_idx)

    # Fill remaining slots (up to 4 total) with highest-power non-SE moves
    if len(selected) < 4:
        selected_ids = {id(m) for m in selected}
        extras = sorted(
            [(m, base) for m, pv, base in move_pv if not pv and id(m) not in selected_ids] +
            [(m, base) for m, pv, base in se_pool if id(m) not in selected_ids],
            key=lambda x: x[1], reverse=True,
        )
        for m, _ in extras:
            if len(selected) >= 4:
                break
            selected.append(m)

    return sum(current_best.values()), selected


def _compute_bulk_pairwise(
    defender_types: list[str],
    defender_hp: float,
    defender_def: float,
    defender_sp_def: float,
    targets: list[dict],
    eff_memo: dict,
    defender_abilities: list[str] | None = None,
) -> float:
    """Σ sqrt(hits-to-KO) from each non-legendary attacker in the target pool.

    Each attacker uses its cached optimal 4 moves. Immune matchups are capped at
    _IMMUNE_CAP hits. sqrt gives ~4-5x range across the population.
    """
    _dabs = defender_abilities or []
    _wg   = any(ABILITY_EFFECTS.get(ab, {}).get("wonder_guard") for ab in _dabs)
    bulk = 0.0
    for tgt in targets:
        if not tgt.get("moves"):
            continue
        best_pohko = 0.0
        for m in tgt["moves"]:
            mtype = m["type"]
            cat   = m["category"]
            dmg_mult = _def_mult(_dabs, mtype, cat, m.get("name", ""))
            if dmg_mult == 0.0:
                continue
            types_key = (mtype, tuple(defender_types))
            eff = eff_memo.get(types_key)
            if eff is None:
                eff = _effectiveness(mtype, defender_types)
                eff_memo[types_key] = eff
            if eff == 0:
                continue
            if _wg and eff <= 1.0:
                continue  # Wonder Guard: only SE moves count toward bulk
            if eff > 1.0:
                for _ab in _dabs:
                    dmg_mult *= ABILITY_EFFECTS.get(_ab, {}).get("filter_se", 1.0)
            stat = tgt["atk"] if cat == "physical" else tgt["sp_atk"]
            stab = 1.5 if mtype in tgt["types"] else 1.0
            acc  = (m["accuracy"] or 100) / 100.0
            base = stat * m["power"] * acc * stab
            avg_def = defender_def if cat == "physical" else defender_sp_def
            pohko = min(base * dmg_mult * eff * _OHKO_K / (defender_hp * avg_def), 1.0)
            if pohko > best_pohko:
                best_pohko = pohko
        hits_to_ko = (1.0 / best_pohko) if best_pohko > 0 else _IMMUNE_CAP
        bulk += math.sqrt(hits_to_ko)
    return bulk


def _simulate_battle(
    pohko_a: float, pohko_b: float, speed_a: int, speed_b: int
) -> tuple[float, float]:
    """Discrete 1v1 battle sim. Returns (A_final_hp_frac, B_final_hp_frac) in [0, 1].

    Turn order: A goes first if speed_a >= speed_b.
    Each round the attacker-first fires, then (if defender survives) retaliates.
    pohko_x = probability of one-hit-KO from x's best move, capped at 1.0.
    """
    if pohko_a <= 0 and pohko_b <= 0:
        return 0.5, 0.5   # neither can damage the other
    if pohko_a <= 0:
        return 0.0, 1.0   # A can't fight back
    if pohko_b <= 0:
        return 1.0, 0.0   # B can't fight back

    n_a = math.ceil(1.0 / pohko_a)  # hits A needs to KO B
    n_b = math.ceil(1.0 / pohko_b)  # hits B needs to KO A

    if speed_a >= speed_b:          # A goes first
        if n_a <= n_b:              # A wins: fires n_a hits, takes n_a-1 in return
            return max(0.0, 1.0 - (n_a - 1) * pohko_b), 0.0
        else:                       # B wins: A fires n_b times before dying
            return 0.0, max(0.0, 1.0 - n_b * pohko_a)
    else:                           # B goes first
        if n_a < n_b:               # A wins: fires n_a times, takes n_a hits from B
            return max(0.0, 1.0 - n_a * pohko_b), 0.0
        else:                       # B wins: fires n_b times, takes n_b-1 in return
            return 0.0, max(0.0, 1.0 - (n_b - 1) * pohko_a)


def _compute_battle_score(
    attacker: dict,
    targets: list[dict],
    eff_memo: dict,
    moves_override: list[dict] | None = None,
) -> tuple[float, int, int, int, int, dict, str]:
    """Σ (A_final_hp − B_final_hp + 1) / 2 across all target matchups.

    Returns (total_score, zdw, dw, dl, zdl, move_usage, outcome_vec, quality_vec) where:
      ZDW = won without taking any damage
      DW  = won but took some damage en route
      DL  = hit B at least once but couldn't KO before B KO'd A
      ZDL = A never dealt any damage to B before being KO'd
      move_usage = {move_name: count} — how many targets each move was best against
      outcome_vec = string of one char per target (Z=ZDW, W=DW, L=DL, l=ZDL, -=draw)
      quality_vec = string of one char per target encoding win quality q∈[0,1]
                    via _quality_char (q>0.5 ⟺ win) — for ranking strongest member
    attacker fields used: types, atk, sp_atk, speed, hp, defense, sp_def, moves, ability_used.
    target fields used:   types, atk, sp_atk, speed, hp, def, sp_def, moves.
    """
    a_types   = attacker["types"]
    a_atk     = attacker["atk"]
    a_sp_atk  = attacker["sp_atk"]
    a_speed   = attacker["speed"]
    a_hp      = float(attacker["hp"])
    a_def     = float(attacker["defense"])
    a_sp_def  = float(attacker["sp_def"])
    a_moves   = moves_override if moves_override is not None else attacker.get("moves", [])
    # Collect all active abilities (active + passive, non-None only)
    a_abs: list[str] = [ab for ab in (
        attacker.get("ability_used"), attacker.get("passive_ability")
    ) if ab]
    _scrappy  = any(ABILITY_EFFECTS.get(ab, {}).get("scrappy")    for ab in a_abs)
    _tinted   = any(ABILITY_EFFECTS.get(ab, {}).get("tinted")     for ab in a_abs)
    _no_guard = any(ABILITY_EFFECTS.get(ab, {}).get("no_guard")   for ab in a_abs)
    _protean  = any(ABILITY_EFFECTS.get(ab, {}).get("protean")    for ab in a_abs)
    _analytic = any(ABILITY_EFFECTS.get(ab, {}).get("analytic")   for ab in a_abs)
    _skill_lk = any(ABILITY_EFFECTS.get(ab, {}).get("skill_link") for ab in a_abs)
    _acc_m    = _acc_mult(a_abs)

    _download      = any(ABILITY_EFFECTS.get(ab, {}).get("download")      for ab in a_abs)
    _off_se        = max((ABILITY_EFFECTS.get(ab, {}).get("off_se", 1.0)   for ab in a_abs), default=1.0)
    _always_last   = any(ABILITY_EFFECTS.get(ab, {}).get("always_last")    for ab in a_abs)
    _charge_elec   = max((ABILITY_EFFECTS.get(ab, {}).get("charge_electric", 1.0) for ab in a_abs), default=1.0)
    _wonder_guard  = any(ABILITY_EFFECTS.get(ab, {}).get("wonder_guard")   for ab in a_abs)
    _acc_ovr       = _move_acc_overrides(a_abs)

    _EPS = 1e-9
    zdw = dw = dl = zdl = 0
    move_usage: dict[str, int] = {}
    outcome_chars: list[str] = []
    quality_chars: list[str] = []
    total = 0.0
    for tgt in targets:
        analytic_mult = 1.3 if _analytic and a_speed < tgt["speed"] else 1.0
        dl_phys_mult  = 1.5 if _download and tgt["def"] < tgt["sp_def"] else 1.0
        dl_spec_mult  = 1.5 if _download and tgt["sp_def"] <= tgt["def"] else 1.0
        # Best P(OHKO) of A on B — apply A's offensive abilities
        pohko_a = 0.0
        best_move: str | None = None
        for m in a_moves:
            mtype = m["type"]
            cat   = m["category"]
            etype, ab_mult = _off_effect(a_abs, mtype, cat, m.get("name", ""), m.get("base_power", 0))
            k = (etype, tuple(tgt["types"]))
            eff = eff_memo.get(k)
            if eff is None:
                eff = _effectiveness(etype, tgt["types"])
                eff_memo[k] = eff
            if _scrappy and eff == 0.0 and etype in {"normal", "fighting"} and "ghost" in tgt["types"]:
                eff = 1.0
            if _tinted and 0 < eff < 1.0:
                eff *= 2.0
            if eff > 0:
                stat = a_atk if cat == "physical" else a_sp_atk
                stab = _stab_mult(a_abs) if (_protean or etype in a_types) else 1.0
                raw_acc = _acc_ovr.get(m.get("name", ""), m["accuracy"] or 0)
                acc     = 1.0 if _no_guard else (min(raw_acc * _acc_m, 100) if raw_acc else 100) / 100.0
                pwr     = m.get("power_max", m["power"]) if _skill_lk else m["power"]
                dl_mult = dl_phys_mult if cat == "physical" else dl_spec_mult
                charge  = _charge_elec if etype == "electric" and a_speed < tgt["speed"] else 1.0
                base    = stat * pwr * ab_mult * analytic_mult * dl_mult * charge * acc * stab
                if eff > 1.0:
                    base *= _off_se
                avg_def = tgt["def"] if cat == "physical" else tgt["sp_def"]
                pohko = min(base * eff * _OHKO_K / (tgt["hp"] * avg_def), 1.0)
                if pohko > pohko_a:
                    pohko_a = pohko
                    best_move = m["name"]
        if best_move:
            move_usage[best_move] = move_usage.get(best_move, 0) + 1

        # Best P(OHKO) of B on A — apply A's defensive abilities
        pohko_b = 0.0
        for m in tgt.get("moves", []):
            mtype = m["type"]
            cat   = m["category"]
            dmg_mult = _def_mult(a_abs, mtype, cat, m.get("name", ""))
            if dmg_mult == 0.0:
                continue
            k = (mtype, tuple(a_types))
            eff = eff_memo.get(k)
            if eff is None:
                eff = _effectiveness(mtype, a_types)
                eff_memo[k] = eff
            if eff > 0:
                if _wonder_guard and eff <= 1.0:
                    continue  # Wonder Guard: only SE moves deal damage
                if eff > 1.0:
                    for _ab in a_abs:
                        dmg_mult *= ABILITY_EFFECTS.get(_ab, {}).get("filter_se", 1.0)
                stat = tgt["atk"] if cat == "physical" else tgt["sp_atk"]
                stab = 1.5 if mtype in tgt["types"] else 1.0
                acc  = (m["accuracy"] or 100) / 100.0
                base = stat * m["power"] * acc * stab
                avg_def = a_def if cat == "physical" else a_sp_def
                pohko = min(base * dmg_mult * eff * _OHKO_K / (a_hp * avg_def), 1.0)
                if pohko > pohko_b:
                    pohko_b = pohko

        a_final, b_final = _simulate_battle(pohko_a, pohko_b, 0 if _always_last else a_speed, tgt["speed"])
        q = (a_final - b_final + 1.0) / 2.0
        total += q
        quality_chars.append(_quality_char(q))

        if b_final < _EPS:          # A wins (B KO'd)
            if a_final >= 1.0 - _EPS:
                zdw += 1
                outcome_chars.append('Z')
            else:
                dw += 1
                outcome_chars.append('W')
        elif a_final < _EPS:        # B wins (A KO'd)
            if b_final >= 1.0 - _EPS:
                zdl += 1
                outcome_chars.append('l')
            else:
                dl += 1
                outcome_chars.append('L')
        else:                       # draw (0.5, 0.5) — both had no moves
            outcome_chars.append('-')

    return total, zdw, dw, dl, zdl, move_usage, ''.join(outcome_chars), ''.join(quality_chars)


def _is_legendary(species: str, form: str, legendary_set: set[str]) -> bool:
    """True if species or any prefix of form name matches a legendary species.
    Handles alternate forms like groudon-primal → groudon."""
    if species in legendary_set or form in legendary_set:
        return True
    parts = form.split("-")
    for i in range(1, len(parts)):
        if "-".join(parts[:i]) in legendary_set:
            return True
    return False


# ── Pokerogue learnset ────────────────────────────────────────────────────────

def _fetch_pokerogue_learnset() -> dict[str, set[str]]:
    """Parse Pokerogue's level-up learnset TS file into {species_slug: {move_slug}}.

    Converts SpeciesId.MR_MIME → 'mr-mime' and MoveId.THUNDER_PUNCH → 'thunder-punch'.
    All three level-tag types (numeric level, EVOLVE_MOVE, RELEARN_MOVE) are included —
    the user asked for the full level-up source, not TMs or egg moves.
    """
    r = requests.get(_POKEROGUE_LEARNSET_URL, timeout=30)
    r.raise_for_status()

    learnset: dict[str, set[str]] = {}
    current: str | None = None

    for line in r.text.splitlines():
        sm = re.search(r'\[SpeciesId\.(\w+)\]', line)
        if sm:
            current = sm.group(1).lower().replace("_", "-")
            learnset.setdefault(current, set())
        if current:
            for mm in re.finditer(r'MoveId\.(\w+)', line):
                slug = mm.group(1).lower().replace("_", "-")
                if slug != "none":
                    learnset[current].add(slug)

    return learnset


def _fetch_pokerogue_egg_moves() -> dict[str, set[str]]:
    """Parse Pokerogue's egg-moves.ts into {species_slug: {move_slug}}.

    Format per line: [SpeciesId.BULBASAUR]: [MoveId.SAPPY_SEED, MoveId.EARTH_POWER, ...]
    Exactly 4 moves per species.
    """
    r = requests.get(_POKEROGUE_EGG_MOVES_URL, timeout=30)
    r.raise_for_status()

    egg_moves: dict[str, set[str]] = {}
    for line in r.text.splitlines():
        sm = re.search(r'\[SpeciesId\.(\w+)\]', line)
        if not sm:
            continue
        species = sm.group(1).lower().replace("_", "-")
        moves: set[str] = set()
        for mm in re.finditer(r'MoveId\.(\w+)', line):
            slug = mm.group(1).lower().replace("_", "-")
            if slug != "none":
                moves.add(slug)
        if moves:
            egg_moves[species] = moves
    return egg_moves


_FORM_RE = re.compile(
    r'new PokemonForm\s*\(\s*"([^"]+)",\s*'          # form display name
    r'(?:SpeciesFormKey\.(\w+)|"([^"]*)")\s*,\s*'    # form key (enum or string)
    r'PokemonType\.(\w+)\s*,\s*'                      # type1
    r'(?:PokemonType\.(\w+)|null)\s*,\s*'             # type2 or null
    r'[\d.]+\s*,\s*[\d.]+\s*,\s*'                    # height, weight
    r'AbilityId\.(\w+)\s*,\s*AbilityId\.\w+\s*,\s*AbilityId\.\w+\s*,\s*'  # abilities (capture first)
    r'\d+,\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)'          # bst(skip), hp,atk,def,spa,spd,spe
)


def _fetch_pokerogue_form_stats() -> dict[str, dict]:
    """Parse Pokerogue's balance/pokemon-species.ts and return Mega form stats.

    Returns {form_slug: {types, ability, stats}} for every PokemonForm whose key
    contains 'mega'. Only Mega forms are extracted — other forms are ignored.
    """
    r = requests.get(_POKEROGUE_SPECIES_URL, timeout=60)
    r.raise_for_status()
    # Collapse whitespace so multiline constructor calls become single lines.
    text = re.sub(r'\s+', ' ', r.text)

    results: dict[str, dict] = {}
    for species_m in re.finditer(r'new PokemonSpecies\s*\(\s*SpeciesId\.(\w+)', text):
        species_slug = species_m.group(1).lower().replace("_", "-")
        start = species_m.start()
        next_start = text.find('new PokemonSpecies(', start + 20)
        block = text[start:next_start] if next_start != -1 else text[start:]

        for fm in _FORM_RE.finditer(block):
            key_enum = fm.group(2)
            key_str  = fm.group(3)
            if key_enum:
                suffix = _FORM_KEY_MAP.get(key_enum, key_enum.lower().replace("_", "-"))
            else:
                suffix = (key_str or "").lower()
            if "mega" not in suffix:
                continue

            type2 = fm.group(5)
            results[f"{species_slug}-{suffix}"] = {
                "types":   [fm.group(4).lower()] + ([type2.lower()] if type2 else []),
                "ability": fm.group(6).lower().replace("_", "-"),
                "stats": {
                    "hp":               int(fm.group(7)),
                    "attack":           int(fm.group(8)),
                    "defense":          int(fm.group(9)),
                    "special-attack":   int(fm.group(10)),
                    "special-defense":  int(fm.group(11)),
                    "speed":            int(fm.group(12)),
                },
            }
    return results


def _fetch_starter_costs() -> dict[str, int]:
    """Parse Pokerogue's speciesStarterCosts map → {species_slug: cost}."""
    r = requests.get(_STARTERS_URL, timeout=30)
    r.raise_for_status()
    costs: dict[str, int] = {}
    in_map = False
    for line in r.text.splitlines():
        if "speciesStarterCosts" in line and "{" in line:
            in_map = True
        if in_map:
            m = re.search(r"\[SpeciesId\.(\w+)\]:\s*(\d+)", line)
            if m:
                name = m.group(1).lower().replace("_", "-")
                costs[name] = int(m.group(2))
            if line.strip().startswith("};"):
                break
    return costs


def _evo_finals(node: dict) -> list[str]:
    """Return all leaf species slugs in a PokéAPI evolution chain node."""
    if not node.get("evolves_to"):
        return [node["species"]["name"]]
    result: list[str] = []
    for child in node["evolves_to"]:
        result.extend(_evo_finals(child))
    return result


# ── cache build ───────────────────────────────────────────────────────────────

def _build_or_load(on_progress, on_ready, include_egg: bool = False):
    global _db_noegg, _db_egg, _ready, _ready_egg, _starters, _move_adoptions_all, _move_adoptions_clean, _target_order_list

    cache_file = CACHE_FILE_EGG if include_egg else CACHE_FILE
    label      = "egg" if include_egg else "standard"

    if os.path.exists(cache_file):
        _prog(on_progress, f"Loading {label} impact cache…")
        try:
            with open(cache_file) as f:
                data = json.load(f)
            if data.get("_version") == CACHE_VERSION:
                data.pop("_version")
                starters_raw    = data.pop("_starters", {})
                adopt_all_raw   = data.pop("_adoptions_all",   {})
                adopt_clean_raw = data.pop("_adoptions_clean", {})
                target_order    = data.pop("_target_order", [])
                data.pop("_pairing_def", None)  # compat: ignore old field if present
                with _lock:
                    if include_egg:
                        _db_egg = data
                    else:
                        _db_noegg = data
                        _starters = {int(k): v for k, v in starters_raw.items()}
                        _target_order_list[:] = target_order
                    _move_adoptions_all   = adopt_all_raw
                    _move_adoptions_clean = adopt_clean_raw
                if not _EFF:
                    _build_effectiveness_table()
                _patch_nonleg_percentiles(data)
                if include_egg:
                    _ready_egg = True
                else:
                    _ready = True
                    _reset_runtime_egg_caches()
                if on_ready:
                    on_ready()
                return
            _prog(on_progress, f"{label.title()} impact cache outdated — rebuilding…")
        except Exception:
            pass

    _prog(on_progress, f"Building {label} impact cache — takes ~3 minutes…")

    # Step 1: fully-evolved species from stats cache
    stats_path = data_path("stats_cache.json")
    try:
        with open(stats_path) as f:
            stats_raw = json.load(f)
        fully_evolved = {
            name for name, v in stats_raw.items()
            if name != "_version" and v.get("fully_evolved")
        }
        legendary_set = {
            name for name, v in stats_raw.items()
            if name != "_version" and (v.get("legendary") or v.get("mythical"))
        }
    except Exception as e:
        _prog(on_progress, f"Stats cache missing — run stats_db first ({e})")
        return

    _prog(on_progress, f"{len(fully_evolved)} fully-evolved species found…")

    # Step 2: expand each species to all its forms via /pokemon-species/{name}
    def fetch_varieties(species: str) -> list[str]:
        try:
            r = requests.get(f"{BASE_URL}/pokemon-species/{species}", timeout=10)
            r.raise_for_status()
            return [v["pokemon"]["name"] for v in r.json().get("varieties", [])]
        except Exception:
            return [species]

    all_forms: list[str] = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        for varieties in pool.map(fetch_varieties, sorted(fully_evolved)):
            all_forms.extend(varieties)
    all_forms = sorted(
        f for f in set(all_forms)
        if not _is_excluded_form(f)
    )
    _prog(on_progress, f"{len(all_forms)} total forms to score…")

    # Step 3: fetch pokemon data (types, stats, move list) for each form
    def fetch_poke(form: str) -> tuple[str, dict | None]:
        try:
            r = requests.get(f"{BASE_URL}/pokemon/{form}", timeout=15)
            r.raise_for_status()
            d = r.json()
            all_moves = d.get("moves", [])
            return form, {
                "types": [t["type"]["name"] for t in sorted(d["types"], key=lambda t: t["slot"])],
                "stats": {s["stat"]["name"]: s["base_stat"] for s in d["stats"]},
                "move_names": [m["move"]["name"] for m in all_moves],
                "species": d.get("species", {}).get("name", form),
                "abilities": [a["ability"]["name"] for a in
                               sorted(d.get("abilities", []), key=lambda a: a["slot"])],
            }
        except Exception:
            return form, None

    pokemon_data: dict[str, dict] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        for form, pd in pool.map(fetch_poke, all_forms):
            if pd:
                pokemon_data[form] = pd
            completed += 1
            if completed % 100 == 0:
                _prog(on_progress, f"Fetching pokemon data… {completed}/{len(all_forms)}")

    # Step 3.5: fetch Pokerogue level-up learnset and filter move lists
    _prog(on_progress, "Fetching Pokerogue level-up learnsets…")
    try:
        pokerogue_learnset = _fetch_pokerogue_learnset()
        _prog(on_progress, f"Learnset loaded — {len(pokerogue_learnset)} species entries.")

        # Inject Pokerogue-exclusive Mega forms that PokéAPI doesn't know about.
        # These are forms the learnset references but that never came back from fetch_poke.
        injected_forms: set[str] = set()
        for form in list(pokerogue_learnset):
            if "-mega" not in form or form in pokemon_data or _is_excluded_form(form):
                continue
            base = re.sub(r"-mega.*$", "", form)
            base_pd = pokemon_data.get(base)
            if base_pd:
                pokemon_data[form] = {**base_pd, "move_names": list(base_pd["move_names"]), "species": base}
                injected_forms.add(form)
        if injected_forms:
            _prog(on_progress, f"  Injected {len(injected_forms)} Pokerogue-exclusive Mega forms.")

        # Apply real stats/types/ability from Pokerogue's species data for injected forms.
        if injected_forms:
            try:
                poke_form_stats = _fetch_pokerogue_form_stats()
                applied = 0
                for form in injected_forms:
                    info = poke_form_stats.get(form)
                    if not info:
                        continue
                    pd = pokemon_data[form]
                    pd["stats"]   = info["stats"]
                    pd["types"]   = info["types"]
                    pd["abilities"] = [info["ability"]] + pd.get("abilities", [])[1:]
                    applied += 1
                _prog(on_progress, f"  Applied Pokerogue form stats to {applied}/{len(injected_forms)} injected Megas.")
            except Exception as e:
                _prog(on_progress, f"  Warning: could not fetch Pokerogue form stats ({e}) — using base-form stats.")

        filtered = 0
        for form, pd in pokemon_data.items():
            species = pd.get("species", form)
            allowed = pokerogue_learnset.get(species) or pokerogue_learnset.get(form)
            if allowed is not None:
                before = len(pd["move_names"])
                pd["move_names"] = [m for m in pd["move_names"] if m in allowed]
                filtered += before - len(pd["move_names"])
        _prog(on_progress, f"Filtered {filtered} non-level-up moves from learnsets.")

        # Final fallback: any Mega still with no moves inherits the base form's moveset.
        mega_fallback = 0
        for form, pd in pokemon_data.items():
            if "-mega" in form and not pd["move_names"]:
                base = re.sub(r"-mega.*$", "", form)
                base_pd = pokemon_data.get(base)
                if base_pd and base_pd["move_names"]:
                    pd["move_names"] = list(base_pd["move_names"])
                    mega_fallback += 1
        if mega_fallback:
            _prog(on_progress, f"  {mega_fallback} Mega forms fell back to base-form moveset.")

        if include_egg:
            _prog(on_progress, "Fetching Pokerogue egg moves…")
            try:
                egg_moves_map = _fetch_pokerogue_egg_moves()
                _prog(on_progress, f"Egg move map loaded — {len(egg_moves_map)} species.")
                # Egg moves are indexed by base/starter species, not final evo.
                # Build final_evo → base_species reverse map from the starters index.
                with _lock:
                    final_to_base = {info["final_evo"]: info["name"] for info in _starters.values()}
                egg_added = 0
                for form, pd in pokemon_data.items():
                    species = pd.get("species", form)
                    egg_set = (egg_moves_map.get(species)
                               or egg_moves_map.get(form)
                               or egg_moves_map.get(final_to_base.get(form))
                               or egg_moves_map.get(final_to_base.get(species))
                               or set())
                    pd["_egg_move_set"] = egg_set   # tracked for step 5/6.5
                    if egg_set:
                        before = len(pd["move_names"])
                        pd["move_names"] = list(set(pd["move_names"]) | egg_set)
                        egg_added += len(pd["move_names"]) - before
                _prog(on_progress, f"Added {egg_added} egg move entries to learnsets.")
            except Exception as e:
                _prog(on_progress, f"Warning: could not fetch Pokerogue egg moves ({e}) — skipping.")
    except Exception as e:
        _prog(on_progress, f"Warning: could not fetch Pokerogue learnset ({e}) — using full PokéAPI movesets.")

    # Step 3.7: fetch Pokerogue passive abilities
    _prog(on_progress, "Fetching Pokerogue passive abilities…")
    try:
        passive_map = _fetch_pokerogue_passives()
        _prog(on_progress, f"Passive map loaded — {len(passive_map)} entries.")
    except Exception as e:
        passive_map = {}
        _prog(on_progress, f"Warning: could not fetch Pokerogue passives ({e}) — skipping.")

    # Step 4: collect unique move names and fetch details
    all_move_names: set[str] = set()
    for pd in pokemon_data.values():
        all_move_names.update(pd["move_names"])

    _prog(on_progress, f"Fetching {len(all_move_names)} unique moves…")

    def fetch_move(move_name: str) -> tuple[str, dict | None]:
        try:
            r = requests.get(f"{BASE_URL}/move/{move_name}", timeout=10)
            if r.status_code == 404:
                return move_name, None
            r.raise_for_status()
            d = r.json()
            meta       = d.get("meta") or {}
            drain      = meta.get("drain") or 0
            pp         = d.get("pp") or 0
            min_hits   = meta.get("min_hits") or 0
            max_hits   = meta.get("max_hits") or 0
            effect     = next(
                (e["short_effect"] for e in d.get("effect_entries", [])
                 if e["language"]["name"] == "en"), ""
            )
            recharge    = _is_recharge(effect)
            two_turn    = _is_two_turn(move_name, effect, recharge)
            always_skip = _is_always_skip(effect)
            return move_name, {
                "name":          move_name,
                "type":          d["type"]["name"],
                "power":         d["power"],
                "accuracy":      d["accuracy"],
                "category":      d["damage_class"]["name"],
                "drain":         drain,
                "pp":            pp,
                "recharge":      recharge,
                "two_turn":      two_turn,
                "always_skip":   always_skip,
                "min_hits":      min_hits,
                "max_hits":      max_hits,
                "self_reducing": _is_self_reducing(effect),
            }
        except Exception:
            return move_name, None

    move_cache: dict[str, dict | None] = {}
    completed = 0
    total_moves = len(all_move_names)
    with ThreadPoolExecutor(max_workers=20) as pool:
        for name, md in pool.map(fetch_move, sorted(all_move_names)):
            move_cache[name] = md
            completed += 1
            if completed % 200 == 0:
                _prog(on_progress, f"Fetching moves… {completed}/{total_moves}")

    # Step 5: warm effectiveness table, build pairwise target list
    _prog(on_progress, "Warming type effectiveness table…")
    _build_effectiveness_table()

    _prog(on_progress, "Building pairwise target list…")
    targets_build: list[dict] = []
    form_to_tidx: dict[str, int] = {}
    for form, pd in pokemon_data.items():
        if _is_legendary(pd.get("species", form), form, legendary_set):
            continue
        if form in _PARADOX_POKEMON:
            continue
        s = pd["stats"]
        form_to_tidx[form] = len(targets_build)
        targets_build.append({
            "name":   form,
            "types":  pd["types"],
            "hp":     float(s.get("hp", 1)),
            "def":    float(s.get("defense", 1)),
            "sp_def": float(s.get("special-defense", 1)),
            "atk":    float(s.get("attack", 1)),
            "sp_atk": float(s.get("special-attack", 1)),
            "speed":  s.get("speed", 0),
        })
    _prog(on_progress, f"  {len(targets_build)} pairwise targets.")
    eff_memo: dict = {}  # memoize (mtype, types_tuple) → effectiveness

    # Step 6a: Pass 1 — compute coverage scores and select optimal moves (two modes)
    _prog(on_progress, "Computing coverage scores (pass 1)…")

    def _is_eligible(m: dict) -> bool:
        """Base filter: both modes exclude these moves."""
        return (
            m is not None
            and (m.get("power") or 0) > 0
            and (m.get("pp") or 0) > 1
            and m.get("category") != "status"
            and m["name"] not in _EXCLUDED_MOVES
            and not m.get("always_skip", False)
        )

    def _is_adverse(m: dict) -> bool:
        """True for recoil moves and self-reducing moves, excluded in clean mode."""
        return (m.get("drain") or 0) < 0 or m.get("self_reducing", False)

    def _to_move_dict(m: dict) -> dict:
        """Stored move entry; power adjusted for recharge/two-turn and multi-hit."""
        raw = m["power"] or 0
        pwr = float(raw)
        if m.get("recharge") or m.get("two_turn"):
            pwr /= 2.0
        min_h, max_h = m.get("min_hits") or 0, m.get("max_hits") or 0
        if min_h and max_h:
            pwr_max = float(raw) * max_h   # Skill Link: always max hits
            pwr *= (min_h + max_h) / 2.0
        else:
            pwr_max = pwr
        return {
            "name":       m["name"],
            "type":       m["type"],
            "power":      pwr,
            "power_max":  pwr_max,
            "base_power": raw,
            "accuracy":   m["accuracy"],
            "category":   m["category"],
        }

    db: dict[str, dict] = {}
    for form, pd in pokemon_data.items():
        atk    = pd["stats"].get("attack", 0)
        sp_atk = pd["stats"].get("special-attack", 0)
        speed  = pd["stats"].get("speed", 0)
        ability_used         = _pick_ability(pd.get("abilities", []))
        ability_acknowledged = _pick_known_ability(pd.get("abilities", []))
        passive_raw          = passive_map.get(form) or passive_map.get(pd.get("species", form))
        passive_ability      = passive_raw if passive_raw and passive_raw in ABILITY_EFFECTS else None
        passive_acknowledged = (passive_raw if passive_raw and passive_raw in KNOWN_ABILITIES
                                and passive_raw not in ABILITY_EFFECTS else None)
        abilities = [ab for ab in (ability_used, passive_ability) if ab]
        has_no_recoil = any(ABILITY_EFFECTS.get(ab, {}).get("no_recoil") for ab in abilities)

        all_eligible = [
            move_cache[mn] for mn in pd["move_names"]
            if move_cache.get(mn) and _is_eligible(move_cache[mn])
        ]
        clean_eligible = [
            m for m in all_eligible
            if not ((m.get("drain") or 0) < 0 and not has_no_recoil)
            and not m.get("self_reducing", False)
        ]

        coverage_all,   selected_all   = _compute_score(pd["types"], atk, sp_atk, all_eligible,   targets_build, eff_memo, abilities)
        coverage_clean, selected_clean = _compute_score(pd["types"], atk, sp_atk, clean_eligible, targets_build, eff_memo, abilities)

        entry: dict = {
            "coverage":      coverage_all,
            "atk":           atk,
            "sp_atk":        sp_atk,
            "speed":         speed,
            "hp":            pd["stats"].get("hp", 1),
            "defense":       pd["stats"].get("defense", 1),
            "sp_def":        pd["stats"].get("special-defense", 1),
            "types":         pd["types"],
            "legendary":     _is_legendary(pd.get("species", form), form, legendary_set),
            "paradox":       form in _PARADOX_POKEMON,
            "ability_used":          ability_used,
            "ability_acknowledged":  ability_acknowledged,
            "passive_ability":       passive_ability,
            "passive_acknowledged":  passive_acknowledged,
            "moves":         [_to_move_dict(m) for m in selected_all],
            "moves_clean":   [_to_move_dict(m) for m in selected_clean],
        }
        if include_egg:
            egg_names = pd.get("_egg_move_set", set())
            entry["egg_move_data"] = [
                {"name": m["name"], "type": m["type"], "power": m["power"],
                 "accuracy": m["accuracy"], "category": m["category"]}
                for mn in egg_names
                if (m := move_cache.get(mn)) and m is not None
            ]
        db[form] = entry

    # Populate targets with their optimal moves, then filter to those with moves
    for form, t_idx in form_to_tidx.items():
        targets_build[t_idx]["moves"] = db[form]["moves"]

    no_move_forms = sorted(form for form, entry in db.items() if not entry.get("moves"))
    filtered_targets = [tgt for tgt in targets_build if tgt.get("moves")]
    target_order = [tgt["name"] for tgt in filtered_targets]
    _prog(on_progress,
          f"  {len(filtered_targets)} battle targets "
          f"({len(targets_build) - len(filtered_targets)} no-move forms excluded).")

    # Step 6b: Pass 2 — compute bulk and battle scores against filtered targets
    _prog(on_progress, "Computing bulk and battle scores (pass 2)…")
    for form, entry in db.items():
        def_abs = [ab for ab in (entry.get("ability_used"), entry.get("passive_ability")) if ab]
        entry["bulk"] = _compute_bulk_pairwise(
            entry["types"],
            float(entry["hp"]),
            float(entry["defense"]),
            float(entry["sp_def"]),
            filtered_targets,
            eff_memo,
            defender_abilities=def_abs,
        )
        score, zdw, dw, dl, zdl, move_usage, outcome_vec, quality_vec = _compute_battle_score(
            entry, filtered_targets, eff_memo
        )
        entry["impact"]       = score
        entry["outcomes"]     = {"zdw": zdw, "dw": dw, "dl": dl, "zdl": zdl}
        entry["outcomes_vec"] = outcome_vec
        entry["quality_vec"]  = quality_vec
        entry["move_usage"]   = move_usage

        score_clean, *_ = _compute_battle_score(
            entry, filtered_targets, eff_memo,
            moves_override=entry.get("moves_clean", []),
        )
        entry["impact_clean"] = score_clean

    # Count move adoptions across all forms (for Moves browser tab)
    adoptions_all:   dict[str, int] = {}
    adoptions_clean: dict[str, int] = {}
    for entry in db.values():
        for m in entry.get("moves", []):
            adoptions_all[m["name"]] = adoptions_all.get(m["name"], 0) + 1
        for m in entry.get("moves_clean", []):
            adoptions_clean[m["name"]] = adoptions_clean.get(m["name"], 0) + 1
    with _lock:
        _move_adoptions_all   = adoptions_all
        _move_adoptions_clean = adoptions_clean

    # Step 7: speed percentiles (preserved for pairing_vector compatibility)
    # impact is already set from battle simulation — no speed-factor adjustment.
    all_speeds = [v["speed"] for v in db.values()]
    n = len(all_speeds)
    for entry in db.values():
        sp = entry["speed"]
        entry["speed_pct"] = round(sum(1 for x in all_speeds if x < sp) / n * 100)

    # Percentile relative to non-legendary pool only.
    nonleg_scores_sorted = sorted(
        v["impact"] for v in db.values() if not v.get("legendary")
    )
    n_nonleg    = len(nonleg_scores_sorted)
    best_nonleg = nonleg_scores_sorted[-1] if nonleg_scores_sorted else 1.0

    for entry in db.values():
        s     = entry["impact"]
        below = sum(1 for x in nonleg_scores_sorted if x < s)
        if below >= n_nonleg:
            excess = (s - best_nonleg) / best_nonleg * 100
            entry["percentile"] = round(100 + excess)
        else:
            entry["percentile"] = round(below / n_nonleg * 100)

    # Step 7.5: brute-force cap scores for egg caps 1–4 (egg build only)
    if include_egg:
        _prog(on_progress, "Computing egg-cap scores (1–4 egg moves)…")
        n_db = len(db)
        for form, entry in db.items():
            noegg_entry   = _db_noegg.get(form, {})
            levelup_moves = noegg_entry.get("moves", [])
            egg_moves     = entry.get("egg_move_data", [])
            types  = entry["types"]
            atk    = entry["atk"]
            sp_atk = entry["sp_atk"]
            sp_pct = entry["speed_pct"]
            factor = 1.0 if sp_pct >= 70 else 0.4 + 0.6 * (sp_pct / 70)
            entry["impact_caps"] = [
                _best_capped_score(types, atk, sp_atk, levelup_moves, egg_moves, cap) * factor
                for cap in range(1, 5)
            ]
        for cap_idx in range(4):
            cap_nonleg_sorted = sorted(
                v["impact_caps"][cap_idx] for v in db.values() if not v.get("legendary")
            )
            n_cap_nonleg    = len(cap_nonleg_sorted)
            best_cap_nonleg = cap_nonleg_sorted[-1] if cap_nonleg_sorted else 1.0
            for entry in db.values():
                s     = entry["impact_caps"][cap_idx]
                below = sum(1 for x in cap_nonleg_sorted if x < s)
                if below >= n_cap_nonleg:
                    excess = (s - best_cap_nonleg) / best_cap_nonleg * 100
                    pct = round(100 + excess)
                else:
                    pct = round(below / n_cap_nonleg * 100)
                entry.setdefault("percentile_caps", [0, 0, 0, 0])
                entry["percentile_caps"][cap_idx] = pct

    # Step 8: Build starters index (only needed for the primary noegg cache)
    _si: dict[int, dict] = {}
    if not include_egg:
        _prog(on_progress, "Building starters index…")
        try:
            starter_costs = _fetch_starter_costs()
            _prog(on_progress, f"Starter costs: {len(starter_costs)} entries. Fetching evo chains…")

            def _fetch_starter_info(species_name: str) -> tuple[str, int | None, list[str], str | None]:
                try:
                    r = requests.get(f"{BASE_URL}/pokemon-species/{species_name}", timeout=10)
                    if r.status_code == 404:
                        return species_name, None, [species_name], None
                    r.raise_for_status()
                    d = r.json()
                    sid = d["id"]
                    growth = (d.get("growth_rate") or {}).get("name")  # free — already fetched
                    chain_url = d["evolution_chain"]["url"]
                    r2 = requests.get(chain_url, timeout=10)
                    r2.raise_for_status()
                    finals = _evo_finals(r2.json()["chain"])
                    return species_name, sid, finals, growth
                except Exception:
                    return species_name, None, [species_name], None

            with ThreadPoolExecutor(max_workers=10) as pool:
                for sname, sid, finals, growth in pool.map(_fetch_starter_info, sorted(starter_costs.keys())):
                    if sid is None:
                        continue
                    cost = starter_costs[sname]
                    best_final = sname
                    best_score = -1.0
                    for final in finals:
                        for form_name, entry in db.items():
                            base = form_name.split("-")[0]
                            if base == final or form_name == final:
                                s = entry.get("impact", 0.0)
                                if s > best_score:
                                    best_score = s
                                    best_final = form_name
                    _si[sid] = {"name": sname, "cost": cost, "final_evo": best_final,
                                "growth": growth}

            _starters = _si
            _prog(on_progress, f"Starters index: {len(_si)} entries.")
        except Exception as e:
            _prog(on_progress, f"Warning: starters index failed ({e})")
    else:
        # Reuse the already-built starters index from the noegg cache file
        with _lock:
            _si = dict(_starters)

    _prog(on_progress, f"Saving {label} impact cache…")
    with open(cache_file, "w") as f:
        json.dump({
            "_version":         CACHE_VERSION,
            "_starters":        _si,
            "_adoptions_all":   adoptions_all,
            "_adoptions_clean": adoptions_clean,
            "_target_order":    target_order,
            **db,
        }, f)

    with _lock:
        if include_egg:
            _db_egg = db
        else:
            _db_noegg = db
            _target_order_list[:] = target_order
    if include_egg:
        _ready_egg = True
    else:
        _ready = True
        _reset_runtime_egg_caches()
    if on_ready:
        on_ready()
    _prog(on_progress, f"Done — {len(db)} forms scored ({label}).")
    if no_move_forms:
        _prog(on_progress,
              f"Forms excluded (no scorable moves, {len(no_move_forms)} total):")
        for f in no_move_forms:
            _prog(on_progress, f"  {f}")


def _prog(cb, msg: str):
    if cb:
        cb(msg)
    else:
        print(f"[impact_db] {msg}")


# ── standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    done = threading.Event()

    def _on_ready():
        done.set()

    _build_or_load(lambda msg: print(f"  {msg}"), _on_ready, False)
    done.wait()

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(f"\nTop {n} by Impact Score (all forms):")
    print(f"{'Rank':<5} {'Name':<30} {'Score':>10}  {'%ile':>5}  Types")
    print("-" * 65)
    for rank, (name, score) in enumerate(top_n(n), 1):
        entry = _db_noegg[name]
        pct = entry["percentile"]
        types = "/".join(entry["types"])
        leg = " [L]" if entry.get("legendary") else ""
        print(f"{rank:<5} {name:<30} {score:>10.0f}  p{pct:<4}  {types}{leg}")

    print(f"\nTop {n} excluding legendaries/mythicals:")
    print(f"{'Rank':<5} {'Name':<30} {'Score':>10}  {'%ile':>5}  Types")
    print("-" * 65)
    for rank, (name, score) in enumerate(top_n(n, exclude_legendary=True), 1):
        entry = _db_noegg[name]
        pct = entry["percentile"]
        types = "/".join(entry["types"])
        print(f"{rank:<5} {name:<30} {score:>10.0f}  p{pct:<4}  {types}")
