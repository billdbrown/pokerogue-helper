"""
Ability detail database for the Abilities browser tab.
Sources: Pokerogue repo (ability list) + PokéAPI (details, Pokémon counts).
"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from app_dirs import data_path

CACHE_VERSION = 1
CACHE_FILE = data_path("ability_info_cache.json")

_ROGUE_ABILITY_URL = (
    "https://raw.githubusercontent.com/pagefaultgames/pokerogue/main"
    "/src/data/abilities/init-abilities.ts"
)
_POKEAPI = "https://pokeapi.co/api/v2"

_db: dict[str, dict] = {}
_ready = False
_lock = threading.Lock()


def is_ready() -> bool:
    return _ready


def all_entries() -> dict[str, dict]:
    with _lock:
        return dict(_db)


def _to_slug(ability_id: str) -> str:
    return ability_id.replace("_", "-").lower()


def _fetch_rogue_ability_slugs() -> list[str]:
    r = requests.get(_ROGUE_ABILITY_URL, timeout=30)
    r.raise_for_status()
    ids = re.findall(r"AbilityId\.(\w+)", r.text)
    seen: set[str] = set()
    result: list[str] = []
    for i in ids:
        if i == "NONE":
            continue
        slug = _to_slug(i)
        if slug not in seen:
            seen.add(slug)
            result.append(slug)
    return result


def _classify_feasibility(effect: str) -> str:
    """Editorial gauge of how tractable an ability is to model in the battle sim."""
    e = effect.lower()
    complex_kws = ("copies", "traces", "transforms", "changes type", "suppresses",
                   "negates all", "role play", "imposter", "neutraliz", "skill link")
    hard_kws    = ("each turn", "end of each turn", "at the end of", "per turn",
                   "every turn", "after each turn", "speed raises", "wonder guard",
                   "regenerat", "speed boost")
    medium_kws  = ("when switched in", "upon entry", "on entry", "when the holder",
                   "if the holder", "when hit", "after taking", "when damaged",
                   "when the user", "if the user", "in harsh sunlight", "in rain",
                   "in sandstorm", "in hail", "in snow", "during",
                   "when the pokemon", "if this pokemon", "after being hit",
                   "on contact", "upon using")
    easy_kws    = ("doubles the bearer", "doubles the user", "halves", "immune",
                   "immunity to", "multiplied by", "power of", "type moves",
                   "stat is doubled", "damage from", "reduces damage",
                   "this pokemon's", "the bearer's")
    for kw in complex_kws:
        if kw in e:
            return "Complex"
    for kw in hard_kws:
        if kw in e:
            return "Hard"
    for kw in medium_kws:
        if kw in e:
            return "Medium"
    for kw in easy_kws:
        if kw in e:
            return "Easy"
    return "—"


def _fetch_one(slug: str) -> tuple[str, dict | None]:
    try:
        r = requests.get(f"{_POKEAPI}/ability/{slug}", timeout=12)
        if r.status_code == 404:
            return slug, None
        r.raise_for_status()
        d = r.json()
        effect = next(
            (e["short_effect"] for e in d.get("effect_entries", [])
             if e["language"]["name"] == "en"),
            "",
        )
        pokemon_list = d.get("pokemon", [])
        primary   = sum(1 for p in pokemon_list if p.get("slot") == 1 and not p.get("is_hidden"))
        secondary = sum(1 for p in pokemon_list if p.get("slot") == 2 and not p.get("is_hidden"))
        hidden    = sum(1 for p in pokemon_list if p.get("is_hidden"))
        total     = primary + secondary + hidden
        return slug, {
            "name":        slug,
            "display":     slug.replace("-", " ").title(),
            "effect":      effect,
            "total":       total,
            "primary":     primary,
            "secondary":   secondary,
            "hidden":      hidden,
            "feasibility": _classify_feasibility(effect),
        }
    except Exception:
        return slug, None


def _build_or_load(on_progress=None) -> None:
    global _db, _ready

    def _prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cached = json.load(f)
            if cached.get("_version") == CACHE_VERSION:
                data = {k: v for k, v in cached.items() if not k.startswith("_")}
                with _lock:
                    _db = data
                _ready = True
                _prog(f"Abilities loaded from cache ({len(data):,}).")
                return
        except Exception:
            pass

    _prog("Fetching Pokerogue ability list…")
    try:
        slugs = _fetch_rogue_ability_slugs()
    except Exception as e:
        _prog(f"Warning: could not fetch Pokerogue ability list ({e}).")
        _ready = True
        return

    _prog(f"Fetching {len(slugs):,} abilities from PokéAPI…")
    db: dict[str, dict] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        for slug, data in pool.map(_fetch_one, slugs):
            if data:
                db[slug] = data
            completed += 1
            if completed % 50 == 0:
                _prog(f"Fetching abilities… {completed}/{len(slugs)}")

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"_version": CACHE_VERSION, **db}, f)
    except Exception:
        pass

    with _lock:
        _db = db
    _ready = True
    _prog(f"Abilities ready: {len(db):,} entries.")


def init(on_progress=None) -> None:
    threading.Thread(target=_build_or_load, args=(on_progress,), daemon=True).start()
