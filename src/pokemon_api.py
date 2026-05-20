from dataclasses import dataclass
import requests

BASE_URL = "https://pokeapi.co/api/v2"

_pokemon_cache:   dict = {}
_type_cache:      dict = {}
_evo_cache:       dict = {}
_move_cache:      dict = {}
_move_fail_cache: set  = set()   # keys that 404'd so we don't retry

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

    result = PokemonData(name=data["name"], types=types, stats=stats)
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


def fetch_type_relations(type_name: str) -> dict:
    key = type_name.lower()
    if key in _type_cache:
        return _type_cache[key]

    resp = requests.get(f"{BASE_URL}/type/{key}", timeout=10)
    resp.raise_for_status()
    data = resp.json()

    _type_cache[key] = data["damage_relations"]
    return data["damage_relations"]
