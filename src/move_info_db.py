"""
Full move detail database for the Moves browser tab.
Sources: Pokerogue repo (move list) + PokéAPI (details, effect text).
"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from app_dirs import data_path

CACHE_VERSION = 3
CACHE_FILE = data_path("move_info_cache.json")

_ROGUE_MOVE_URL = (
    "https://raw.githubusercontent.com/pagefaultgames/pokerogue/main"
    "/src/data/moves/move.ts"
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


def _to_slug(move_id: str) -> str:
    return move_id.replace("_", "-").lower()


def _fetch_rogue_move_slugs() -> list[str]:
    r = requests.get(_ROGUE_MOVE_URL, timeout=30)
    r.raise_for_status()
    ids = re.findall(r"new\s+(?:Attack|Status|SelfStatus)Move\s*\(\s*MoveId\.(\w+)", r.text)
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


_TWO_TURN_OVERRIDES: frozenset[str] = frozenset({
    "meteor-beam",
    "electro-shot",
})
_TWO_TURN_PATTERNS = (
    "hits next turn",
    "requires a turn to charge",
    "charges for one turn",
    "takes one turn to charge",
)


def _is_recharge(effect: str) -> bool:
    return "foregoes its next turn to recharge" in effect.lower()


def _is_two_turn(move_name: str, effect: str, is_recharge: bool) -> bool:
    if is_recharge:
        return False
    if move_name in _TWO_TURN_OVERRIDES:
        return True
    e = effect.lower()
    return any(pat in e for pat in _TWO_TURN_PATTERNS)


def _is_always_skip(effect: str) -> bool:
    return "hits the target two turns later" in effect.lower()


_ALWAYS_EXCLUDED: frozenset[str] = frozenset({
    "mind-blown", "steel-beam", "chloroblast",
    "explosion", "self-destruct", "final-gambit",
    "focus-punch", "overheat",
    "dream-eater", "sky-drop",
})


def _classify_adverse(drain: int, effect: str) -> str:
    """None / Self-damaging (recoil or HP cost) / Self-reducing (lowers own stats)."""
    if drain < 0:
        return "Self-damaging"
    e = effect.lower()
    for kw in ("recoil", "loses hp", "lose hp", "costs hp",
                "half of its maximum hp", "half its max hp",
                "half its maximum hp", "loses half", "1/2 its"):
        if kw in e:
            return "Self-damaging"
    for pat in (r"lowers?\s+the\s+user", r"lower\s+the\s+user",
                r"harshly\s+lower", r"the\s+user'?s\s+\w[\w\s-]+\s+(?:drop|lower|decreas|fall)"):
        if re.search(pat, e):
            return "Self-reducing"
    return "None"


def _excluded_reason(slug: str, pp: int, power: int, cat: str,
                     recharge: bool, two_turn: bool, always_skip: bool,
                     adverse: str) -> str:
    """Return exclusion reason string, or '' if the move is included in sim."""
    if always_skip:
        return "Always excluded (delayed hit)"
    if slug in _ALWAYS_EXCLUDED:
        return "Always excluded"
    if cat == "status" or power <= 0:
        return ""
    if recharge:
        return "Power ×½ (recharge turn)"
    if two_turn:
        return "Power ×½ (charge turn)"
    if adverse == "Self-damaging":
        return "Adverse: clean mode (recoil)"
    if adverse == "Self-reducing":
        return "Adverse: clean mode (stat drop)"
    return ""


def _fetch_one(slug: str) -> tuple[str, dict | None]:
    try:
        r = requests.get(f"{_POKEAPI}/move/{slug}", timeout=12)
        if r.status_code == 404:
            return slug, None
        r.raise_for_status()
        d = r.json()
        meta       = d.get("meta") or {}
        drain      = meta.get("drain") or 0
        pp         = d.get("pp") or 0
        min_hits   = meta.get("min_hits") or 0
        max_hits   = meta.get("max_hits") or 0
        effect = next(
            (e["short_effect"] for e in d.get("effect_entries", [])
             if e["language"]["name"] == "en"),
            "",
        )
        cat        = d.get("damage_class", {}).get("name", "status")
        power      = d.get("power") or 0
        recharge   = _is_recharge(effect)
        two_turn   = _is_two_turn(slug, effect, recharge)
        always_skip = _is_always_skip(effect)
        adverse    = _classify_adverse(drain, effect) if cat != "status" else "None"
        excluded   = _excluded_reason(slug, pp, power, cat, recharge, two_turn, always_skip, adverse)
        return slug, {
            "name":        slug,
            "display":     slug.replace("-", " ").title(),
            "type":        d["type"]["name"],
            "power":       power,
            "accuracy":    d.get("accuracy") or 0,
            "pp":          pp,
            "category":    cat,
            "drain":       drain,
            "recharge":    recharge,
            "two_turn":    two_turn,
            "always_skip": always_skip,
            "min_hits":    min_hits,
            "max_hits":    max_hits,
            "effect":      effect,
            "adverse":     adverse,
            "excluded":    excluded,
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
                _prog(f"Moves loaded from cache ({len(data):,}).")
                return
        except Exception:
            pass

    _prog("Fetching Pokerogue move list…")
    try:
        slugs = _fetch_rogue_move_slugs()
    except Exception as e:
        _prog(f"Warning: could not fetch Pokerogue move list ({e}).")
        _ready = True
        return

    _prog(f"Fetching {len(slugs):,} moves from PokéAPI…")
    db: dict[str, dict] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        for slug, data in pool.map(_fetch_one, slugs):
            if data and (data.get("pp") or 0) > 1:  # drop 1PP moves entirely
                db[slug] = data
            completed += 1
            if completed % 100 == 0:
                _prog(f"Fetching moves… {completed}/{len(slugs)}")

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"_version": CACHE_VERSION, **db}, f)
    except Exception:
        pass

    with _lock:
        _db = db
    _ready = True
    _prog(f"Moves ready: {len(db):,} entries.")


def init(on_progress=None) -> None:
    threading.Thread(target=_build_or_load, args=(on_progress,), daemon=True).start()
