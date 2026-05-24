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
import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations

import requests

from app_dirs import data_path
from weakness_calc import ALL_TYPES, _effectiveness

BASE_URL = "https://pokeapi.co/api/v2"
CACHE_FILE     = data_path("impact_cache.json")
CACHE_FILE_EGG = data_path("impact_cache_egg.json")
CACHE_VERSION  = 23

# Forms omitted from all scoring (duplicates or Pokerogue-unavailable mechanics).
_EXCLUDED_FORMS: frozenset[str] = frozenset({
    "greninja-ash",         # Battle Bond — mechanic not present in Pokerogue
    "greninja-battle-bond", # alternate PokéAPI slug for the same form
})


def _is_excluded_form(form: str) -> bool:
    """True for forms that should be omitted from all analysis and scoring."""
    if form in _EXCLUDED_FORMS:
        return True
    if "-totem" in form:      # totem variants (incl. raticate-totem-alola)
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

_OHKO_K: float = 22.0 / 50.0  # level-50 game damage formula constant
_IMMUNE_CAP: int = 50          # hits-to-KO cap for immune matchups in bulk scoring

_move_adoptions_all:   dict[str, int] = {}  # move_name → # forms that selected it (all mode)
_move_adoptions_clean: dict[str, int] = {}  # same, clean mode (no recoil/self-reducing)


def _is_self_reducing(effect: str) -> bool:
    """True if the move's effect text describes a user-side stat drop."""
    import re as _re
    e = effect.lower()
    for pat in (r"lowers?\s+the\s+user", r"lower\s+the\s+user",
                r"harshly\s+lower", r"the\s+user'?s\s+\w[\w\s-]+\s+(?:drop|lower|decreas|fall)"):
        if _re.search(pat, e):
            return True
    return False


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
    global _use_egg
    _use_egg = value and _ready_egg


def set_egg_cap(cap: int) -> None:
    """Set max egg moves allowed in the optimal 4-move set (1–4)."""
    global _egg_cap
    _egg_cap = max(1, min(4, cap))


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
    result: dict[tuple, float] = {}
    for m in entry.get("moves", []):
        mtype    = m.get("type", "")
        power    = m.get("power") or 0
        accuracy = m.get("accuracy") or 100
        category = m.get("category", "special")
        if not power:
            continue
        stat = atk if category == "physical" else sp_atk
        stab = 1.5 if mtype in types else 1.0
        base = stat * power * (accuracy / 100.0) * stab * factor
        for pairing in ALL_PAIRINGS:
            se = _EFF.get((mtype, pairing), 0.0)
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


def _final_evo_for(name: str) -> str:
    """Return the cache key for this pokemon's final evolution.

    If the name is already in the cache (i.e. it is a final evo), return it
    directly. Otherwise scan the starters index for a starter that resolves to
    this name and use its final_evo. Falls back to the raw name so callers
    receive an empty pairing_vector rather than crashing.
    """
    n = name.lower()
    with _lock:
        if n in _db_noegg:
            return n
    for info in starters_index().values():
        if info["name"] == n:
            return info["final_evo"]
    return n


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
    result: dict = {}
    for m in entry.get("moves", []):
        mtype    = m.get("type", "")
        power    = m.get("power") or 0
        accuracy = m.get("accuracy") or 100
        category = m.get("category", "special")
        if not power:
            continue
        stat = atk if category == "physical" else sp_atk
        stab = 1.5 if mtype in types else 1.0
        base = stat * power * (accuracy / 100.0) * stab * factor
        for pairing in ALL_PAIRINGS:
            se = _EFF.get((mtype, pairing), 0.0)
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

    move_pv: list[tuple[dict, dict[int, float]]] = []
    for m in damaging:
        mtype = m["type"]
        stat  = atk if m["category"] == "physical" else sp_atk
        stab  = 1.5 if mtype in pokemon_types else 1.0
        acc   = (m["accuracy"] or 100) / 100.0
        pwr = float(m["power"] or 0)
        if m.get("recharge") or m.get("two_turn"):
            pwr /= 2.0
        min_h, max_h = m.get("min_hits") or 0, m.get("max_hits") or 0
        if min_h and max_h:
            pwr *= (min_h + max_h) / 2.0
        base  = stat * pwr * acc * stab

        pv: dict[int, float] = {}
        for t_idx, tgt in enumerate(targets):
            types_key = (mtype, tuple(tgt["types"]))
            eff = eff_memo.get(types_key)
            if eff is None:
                eff = _effectiveness(mtype, tgt["types"])
                eff_memo[types_key] = eff
            if eff > 1.0:
                avg_def = tgt["def"] if m["category"] == "physical" else tgt["sp_def"]
                pv[t_idx] = min(base * eff * _OHKO_K / (tgt["hp"] * avg_def), 1.0)
        if pv:
            move_pv.append((m, pv))

    if not move_pv:
        return 0.0, []

    # Greedy moveset: pick up to 4 moves by marginal gain
    current_best: dict[int, float] = {}
    selected: list[dict] = []
    for _ in range(min(4, len(move_pv))):
        best_gain = 0.0
        best_idx  = -1
        for idx, (_, pv) in enumerate(move_pv):
            gain = sum(max(0.0, v - current_best.get(t, 0.0)) for t, v in pv.items())
            if gain > best_gain:
                best_gain = gain
                best_idx  = idx
        if best_idx == -1:
            break
        m, pv = move_pv[best_idx]
        for t, v in pv.items():
            if v > current_best.get(t, 0.0):
                current_best[t] = v
        selected.append(m)
        move_pv.pop(best_idx)

    return sum(current_best.values()), selected


def _compute_bulk_pairwise(
    defender_types: list[str],
    defender_hp: float,
    defender_def: float,
    defender_sp_def: float,
    targets: list[dict],
    eff_memo: dict,
) -> float:
    """Σ sqrt(hits-to-KO) from each non-legendary attacker in the target pool.

    Each attacker uses its cached optimal 4 moves. Immune matchups are capped at
    _IMMUNE_CAP hits. sqrt gives ~4-5x range across the population.
    """
    bulk = 0.0
    for tgt in targets:
        if not tgt.get("moves"):
            continue
        best_pohko = 0.0
        for m in tgt["moves"]:
            mtype = m["type"]
            types_key = (mtype, tuple(defender_types))
            eff = eff_memo.get(types_key)
            if eff is None:
                eff = _effectiveness(mtype, defender_types)
                eff_memo[types_key] = eff
            if eff == 0:
                continue
            stat = tgt["atk"] if m["category"] == "physical" else tgt["sp_atk"]
            stab = 1.5 if mtype in tgt["types"] else 1.0
            acc  = (m["accuracy"] or 100) / 100.0
            base = stat * m["power"] * acc * stab
            avg_def = defender_def if m["category"] == "physical" else defender_sp_def
            pohko = min(base * eff * _OHKO_K / (defender_hp * avg_def), 1.0)
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
) -> tuple[float, int, int, int, int, dict]:
    """Σ (A_final_hp − B_final_hp + 1) / 2 across all target matchups.

    Returns (total_score, zdw, dw, dl, zdl, move_usage) where:
      ZDW = won without taking any damage
      DW  = won but took some damage en route
      DL  = hit B at least once but couldn't KO before B KO'd A
      ZDL = A never dealt any damage to B before being KO'd
      move_usage = {move_name: count} — how many targets each move was best against
    attacker fields used: types, atk, sp_atk, speed, hp, defense, sp_def, moves.
    target fields used:   types, atk, sp_atk, speed, hp, def, sp_def, moves.
    """
    a_types  = attacker["types"]
    a_atk    = attacker["atk"]
    a_sp_atk = attacker["sp_atk"]
    a_speed  = attacker["speed"]
    a_hp     = float(attacker["hp"])
    a_def    = float(attacker["defense"])
    a_sp_def = float(attacker["sp_def"])
    a_moves  = moves_override if moves_override is not None else attacker.get("moves", [])

    _EPS = 1e-9
    zdw = dw = dl = zdl = 0
    move_usage: dict[str, int] = {}
    total = 0.0
    for tgt in targets:
        # Best P(OHKO) of A on B
        pohko_a = 0.0
        best_move: str | None = None
        for m in a_moves:
            mtype = m["type"]
            k = (mtype, tuple(tgt["types"]))
            eff = eff_memo.get(k)
            if eff is None:
                eff = _effectiveness(mtype, tgt["types"])
                eff_memo[k] = eff
            if eff > 0:
                stat = a_atk if m["category"] == "physical" else a_sp_atk
                stab = 1.5 if mtype in a_types else 1.0
                acc  = (m["accuracy"] or 100) / 100.0
                base = stat * m["power"] * acc * stab
                avg_def = tgt["def"] if m["category"] == "physical" else tgt["sp_def"]
                pohko = min(base * eff * _OHKO_K / (tgt["hp"] * avg_def), 1.0)
                if pohko > pohko_a:
                    pohko_a = pohko
                    best_move = m["name"]
        if best_move:
            move_usage[best_move] = move_usage.get(best_move, 0) + 1

        # Best P(OHKO) of B on A
        pohko_b = 0.0
        for m in tgt.get("moves", []):
            mtype = m["type"]
            k = (mtype, tuple(a_types))
            eff = eff_memo.get(k)
            if eff is None:
                eff = _effectiveness(mtype, a_types)
                eff_memo[k] = eff
            if eff > 0:
                stat = tgt["atk"] if m["category"] == "physical" else tgt["sp_atk"]
                stab = 1.5 if mtype in tgt["types"] else 1.0
                acc  = (m["accuracy"] or 100) / 100.0
                base = stat * m["power"] * acc * stab
                avg_def = a_def if m["category"] == "physical" else a_sp_def
                pohko = min(base * eff * _OHKO_K / (a_hp * avg_def), 1.0)
                if pohko > pohko_b:
                    pohko_b = pohko

        a_final, b_final = _simulate_battle(pohko_a, pohko_b, a_speed, tgt["speed"])
        total += (a_final - b_final + 1.0) / 2.0

        if b_final < _EPS:          # A wins (B KO'd)
            if a_final >= 1.0 - _EPS:
                zdw += 1
            else:
                dw += 1
        elif a_final < _EPS:        # B wins (A KO'd)
            if b_final >= 1.0 - _EPS:
                zdl += 1
            else:
                dl += 1
        # else: draw (0.5, 0.5) — both had no moves

    return total, zdw, dw, dl, zdl, move_usage


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
    global _db_noegg, _db_egg, _ready, _ready_egg, _starters, _move_adoptions_all, _move_adoptions_clean

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
                data.pop("_pairing_def", None)  # compat: ignore old field if present
                with _lock:
                    if include_egg:
                        _db_egg = data
                    else:
                        _db_noegg = data
                        _starters = {int(k): v for k, v in starters_raw.items()}
                    _move_adoptions_all   = adopt_all_raw
                    _move_adoptions_clean = adopt_clean_raw
                if not _EFF:
                    _build_effectiveness_table()
                _patch_nonleg_percentiles(data)
                if include_egg:
                    _ready_egg = True
                else:
                    _ready = True
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
        if "-mega" not in f and not _is_excluded_form(f)
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
        filtered = 0
        for form, pd in pokemon_data.items():
            species = pd.get("species", form)
            allowed = pokerogue_learnset.get(species) or pokerogue_learnset.get(form)
            if allowed is not None:
                before = len(pd["move_names"])
                pd["move_names"] = [m for m in pd["move_names"] if m in allowed]
                filtered += before - len(pd["move_names"])
        _prog(on_progress, f"Filtered {filtered} non-level-up moves from learnsets.")

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
        pwr = float(m["power"] or 0)
        if m.get("recharge") or m.get("two_turn"):
            pwr /= 2.0
        min_h, max_h = m.get("min_hits") or 0, m.get("max_hits") or 0
        if min_h and max_h:
            pwr *= (min_h + max_h) / 2.0
        return {
            "name":     m["name"],
            "type":     m["type"],
            "power":    pwr,
            "accuracy": m["accuracy"],
            "category": m["category"],
        }

    db: dict[str, dict] = {}
    for form, pd in pokemon_data.items():
        atk    = pd["stats"].get("attack", 0)
        sp_atk = pd["stats"].get("special-attack", 0)
        speed  = pd["stats"].get("speed", 0)

        all_eligible = [
            move_cache[mn] for mn in pd["move_names"]
            if move_cache.get(mn) and _is_eligible(move_cache[mn])
        ]
        clean_eligible = [m for m in all_eligible if not _is_adverse(m)]

        coverage_all,   selected_all   = _compute_score(pd["types"], atk, sp_atk, all_eligible,   targets_build, eff_memo)
        coverage_clean, selected_clean = _compute_score(pd["types"], atk, sp_atk, clean_eligible, targets_build, eff_memo)

        entry: dict = {
            "coverage":     coverage_all,
            "atk":          atk,
            "sp_atk":       sp_atk,
            "speed":        speed,
            "hp":           pd["stats"].get("hp", 1),
            "defense":      pd["stats"].get("defense", 1),
            "sp_def":       pd["stats"].get("special-defense", 1),
            "types":        pd["types"],
            "legendary":    _is_legendary(pd.get("species", form), form, legendary_set),
            "paradox":      form in _PARADOX_POKEMON,
            "moves":        [_to_move_dict(m) for m in selected_all],
            "moves_clean":  [_to_move_dict(m) for m in selected_clean],
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
    _prog(on_progress,
          f"  {len(filtered_targets)} battle targets "
          f"({len(targets_build) - len(filtered_targets)} no-move forms excluded).")

    # Step 6b: Pass 2 — compute bulk and battle scores against filtered targets
    _prog(on_progress, "Computing bulk and battle scores (pass 2)…")
    for form, entry in db.items():
        entry["bulk"] = _compute_bulk_pairwise(
            entry["types"],
            float(entry["hp"]),
            float(entry["defense"]),
            float(entry["sp_def"]),
            filtered_targets,
            eff_memo,
        )
        score, zdw, dw, dl, zdl, move_usage = _compute_battle_score(entry, filtered_targets, eff_memo)
        entry["impact"]     = score
        entry["outcomes"]   = {"zdw": zdw, "dw": dw, "dl": dl, "zdl": zdl}
        entry["move_usage"] = move_usage

        score_clean, _, _, _, _, _ = _compute_battle_score(
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

            def _fetch_starter_info(species_name: str) -> tuple[str, int | None, list[str]]:
                try:
                    r = requests.get(f"{BASE_URL}/pokemon-species/{species_name}", timeout=10)
                    if r.status_code == 404:
                        return species_name, None, [species_name]
                    r.raise_for_status()
                    d = r.json()
                    sid = d["id"]
                    chain_url = d["evolution_chain"]["url"]
                    r2 = requests.get(chain_url, timeout=10)
                    r2.raise_for_status()
                    finals = _evo_finals(r2.json()["chain"])
                    return species_name, sid, finals
                except Exception:
                    return species_name, None, [species_name]

            with ThreadPoolExecutor(max_workers=10) as pool:
                for sname, sid, finals in pool.map(_fetch_starter_info, sorted(starter_costs.keys())):
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
                    _si[sid] = {"name": sname, "cost": cost, "final_evo": best_final}

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
            "_version":        CACHE_VERSION,
            "_starters":       _si,
            "_adoptions_all":   adoptions_all,
            "_adoptions_clean": adoptions_clean,
            **db,
        }, f)

    with _lock:
        if include_egg:
            _db_egg = db
        else:
            _db_noegg = db
    if include_egg:
        _ready_egg = True
    else:
        _ready = True
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
