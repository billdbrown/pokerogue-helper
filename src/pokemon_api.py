from __future__ import annotations
from dataclasses import dataclass, field
import requests

BASE_URL = "https://pokeapi.co/api/v2"

# Nature index → "+Boost/-Drop" string. Empty string = neutral.
_NATURE_MODS = [
    "",               # 0  Hardy
    "+Atk/-Def",     # 1  Lonely
    "+Atk/-Spd",     # 2  Brave
    "+Atk/-SpAtk",   # 3  Adamant
    "+Atk/-SpDef",   # 4  Naughty
    "+Def/-Atk",     # 5  Bold
    "",               # 6  Docile
    "+Def/-Spd",     # 7  Relaxed
    "+Def/-SpAtk",   # 8  Impish
    "+Def/-SpDef",   # 9  Lax
    "+Spd/-Atk",     # 10 Timid
    "+Spd/-Def",     # 11 Hasty
    "",               # 12 Serious
    "+Spd/-SpAtk",   # 13 Jolly
    "+Spd/-SpDef",   # 14 Naive
    "+SpAtk/-Atk",   # 15 Modest
    "+SpAtk/-Def",   # 16 Mild
    "+SpAtk/-Spd",   # 17 Quiet
    "",               # 18 Bashful
    "+SpAtk/-SpDef", # 19 Rash
    "+SpDef/-Atk",   # 20 Calm
    "+SpDef/-Def",   # 21 Gentle
    "+SpDef/-Spd",   # 22 Sassy
    "+SpDef/-SpAtk", # 23 Careful
    "",               # 24 Quirky
]


def nature_mod_str(nature_idx) -> str:
    """'+Boost/-Drop' string for the given nature index. Empty for neutral natures."""
    if nature_idx is None:
        return ""
    try:
        return _NATURE_MODS[int(nature_idx)]
    except (IndexError, TypeError, ValueError):
        return ""

_pokemon_cache:    dict = {}
_type_cache:       dict = {}
_evo_cache:        dict = {}
_move_cache:       dict = {}
_move_fail_cache:  set  = set()   # keys that 404'd so we don't retry
_ability_cache:    dict = {}
_ability_fail_cache: set = set()

_REGION_MAP = {
    "galarian": "galar",  "galar":  "galar",
    "alolan":   "alola",  "alola":  "alola",
    "hisuian":  "hisui",  "hisui":  "hisui",
    "paldean":  "paldea", "paldea": "paldea",
}


def _normalize_pokemon_name(name: str) -> str:
    parts = name.lower().strip().split()
    if len(parts) >= 2:
        if parts[0] in _REGION_MAP:
            return "-".join(parts[1:]) + "-" + _REGION_MAP[parts[0]]
        if parts[-1] in _REGION_MAP:
            return "-".join(parts[:-1]) + "-" + _REGION_MAP[parts[-1]]
    return "-".join(parts)


@dataclass
class PokemonData:
    name: str
    types: list[str]
    stats: dict[str, int]
    move_names: list[str] = field(default_factory=list)  # all learnable move names


@dataclass
class MoveData:
    name: str
    type: str
    power: int | None       # None for status moves
    accuracy: int | None    # None for moves that always hit
    category: str           # "physical" | "special" | "status"
    description: str


def fetch_pokemon(name: str) -> PokemonData:
    key = _normalize_pokemon_name(name)
    if key in _pokemon_cache:
        return _pokemon_cache[key]

    resp = requests.get(f"{BASE_URL}/pokemon/{key}", timeout=10)
    resp.raise_for_status()
    data = resp.json()

    types = [t["type"]["name"] for t in sorted(data["types"], key=lambda t: t["slot"])]
    stats = {s["stat"]["name"]: s["base_stat"] for s in data["stats"]}
    move_names = [m["move"]["name"] for m in data.get("moves", [])]

    result = PokemonData(name=data["name"], types=types, stats=stats, move_names=move_names)
    _pokemon_cache[key] = result
    return result


def fetch_final_evolutions(name: str) -> list[str]:
    key = name.lower().strip()
    if key in _evo_cache:
        return _evo_cache[key]

    species = requests.get(f"{BASE_URL}/pokemon-species/{key}", timeout=10)
    species.raise_for_status()
    chain_url = species.json()["evolution_chain"]["url"]

    chain = requests.get(chain_url, timeout=10)
    chain.raise_for_status()

    finals = list(_leaf_names(chain.json()["chain"]))
    _evo_cache[key] = finals
    return finals


def _leaf_names(node: dict) -> set[str]:
    if not node["evolves_to"]:
        return {node["species"]["name"]}
    result = set()
    for child in node["evolves_to"]:
        result.update(_leaf_names(child))
    return result


def fetch_learnable_coverage(pokemon_name: str) -> frozenset[str]:
    """Return the set of move types in a Pokemon's full learnable moveset.

    Runs through every move name stored on PokemonData, resolving types via the
    cached fetch_move. Cache misses result in network calls; already-seen moves
    are instant. Intended for background-thread use only.
    """
    try:
        pdata = fetch_pokemon(pokemon_name)
    except Exception:
        return frozenset()
    types: set[str] = set()
    for move_name in pdata.move_names:
        try:
            mdata = fetch_move(move_name)
            if mdata.type and mdata.category != "status" and (mdata.power or 0) > 0:
                types.add(mdata.type)
        except Exception:
            pass
    return frozenset(types)


def fetch_move(name: str) -> MoveData:
    key = name.lower().strip().replace(" ", "-")
    if key in _move_cache:
        return _move_cache[key]
    if key in _move_fail_cache:
        raise ValueError(f"move not found: {key}")

    resp = requests.get(f"{BASE_URL}/move/{key}", timeout=10)
    if resp.status_code == 404:
        _move_fail_cache.add(key)
    resp.raise_for_status()
    d = resp.json()

    desc = next(
        (e["flavor_text"] for e in d.get("flavor_text_entries", [])
         if e["language"]["name"] == "en"),
        "",
    ).replace("\n", " ").replace("\f", " ")

    result = MoveData(
        name=name,
        type=d["type"]["name"],
        power=d["power"],
        accuracy=d["accuracy"],
        category=d["damage_class"]["name"],
        description=desc,
    )
    _move_cache[key] = result
    return result


def fetch_ability(name: str) -> str:
    """Returns the English flavor text for an ability, or '' on failure/miss."""
    key = name.lower().strip().replace(" ", "-")
    if key in _ability_cache:
        return _ability_cache[key]
    if key in _ability_fail_cache:
        return ""
    try:
        resp = requests.get(f"{BASE_URL}/ability/{key}", timeout=10)
        if resp.status_code == 404:
            _ability_fail_cache.add(key)
            return ""
        resp.raise_for_status()
        d = resp.json()
        desc = next(
            (e["flavor_text"] for e in d.get("flavor_text_entries", [])
             if e["language"]["name"] == "en"),
            "",
        ).replace("\n", " ").replace("\f", " ")
        _ability_cache[key] = desc
        return desc
    except Exception:
        return ""


def fetch_type_relations(type_name: str) -> dict:
    key = type_name.lower()
    if key in _type_cache:
        return _type_cache[key]

    resp = requests.get(f"{BASE_URL}/type/{key}", timeout=10)
    resp.raise_for_status()
    data = resp.json()

    _type_cache[key] = data["damage_relations"]
    return data["damage_relations"]
