"""
Background-fetches BST + fully_evolved + legendary flags for all base-form Pokemon,
then caches to stats_cache.json. Legendaries and mythicals are excluded from
percentile pools so scores reflect obtainable Pokemon only.
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

CACHE_FILE = "stats_cache.json"
CACHE_VERSION = 3   # bump to force a rebuild when cache schema changes

_db: dict = {}
_ready = False
_lock = threading.Lock()


def init(on_progress=None, on_ready=None):
    threading.Thread(target=_build_or_load, args=(on_progress, on_ready), daemon=True).start()


def is_ready() -> bool:
    return _ready


def _is_special(v: dict) -> bool:
    return v.get("legendary") or v.get("mythical")


def bst_percentile(bst: int, pool: str = "all") -> int:
    """Returns percentile rank (0-100). Legendaries/mythicals excluded.
    pool: 'all' or 'final' (fully-evolved only)."""
    with _lock:
        values = [
            v["bst"] for v in _db.values()
            if not _is_special(v)
            and (pool == "all" or v.get("fully_evolved"))
        ]
    if not values:
        return 0
    return round(sum(1 for v in values if v < bst) / len(values) * 100)


def stat_percentile(stat_name: str, value: int) -> int:
    """Returns percentile rank for a single stat. Legendaries/mythicals excluded."""
    with _lock:
        values = [
            v["stats"].get(stat_name, 0)
            for v in _db.values()
            if not _is_special(v)
        ]
    if not values:
        return 0
    return round(sum(1 for v in values if v < value) / len(values) * 100)


def top_stat_percentiles(stats: dict, threshold: int = 66) -> dict:
    """Returns {stat_name: percentile} for stats at or above threshold percentile."""
    return {
        name: pct
        for name, value in stats.items()
        if (pct := stat_percentile(name, value)) >= threshold
    }


def is_fully_evolved(name: str) -> bool:
    with _lock:
        return _db.get(name, {}).get("fully_evolved", False)


def all_names() -> list:
    """All Pokemon names excluding mythicals (kept out of fuzzy matching).
    Legendaries are kept since they appear as real encounters."""
    with _lock:
        return [name for name, v in _db.items() if not v.get("mythical")]


# ── cache build ───────────────────────────────────────────────────────────────

def _build_or_load(on_progress, on_ready):
    global _db, _ready

    if os.path.exists(CACHE_FILE):
        _progress(on_progress, "Loading stats cache…")
        try:
            with open(CACHE_FILE) as f:
                data = json.load(f)
            if data.get("_version") == CACHE_VERSION:
                data.pop("_version")
                with _lock:
                    _db = data
                _ready = True
                if on_ready:
                    on_ready()
                return
            _progress(on_progress, "Cache outdated — rebuilding…")
        except Exception:
            pass

    _progress(on_progress, "Building stats cache — first run, takes ~60s…")

    resp = requests.get("https://pokeapi.co/api/v2/pokemon?limit=2000", timeout=30)
    entries = [e for e in resp.json()["results"] if _get_id(e) < 10000]
    total = len(entries)

    db: dict = {}
    completed = 0
    write_lock = threading.Lock()

    def fetch_stats(entry):
        name = entry["name"]
        try:
            r = requests.get(f"https://pokeapi.co/api/v2/pokemon/{name}", timeout=10)
            r.raise_for_status()
            raw_stats = {s["stat"]["name"]: s["base_stat"] for s in r.json()["stats"]}
            return name, {"bst": sum(raw_stats.values()), "stats": raw_stats,
                          "fully_evolved": False, "legendary": False, "mythical": False}
        except Exception:
            return None, None

    with ThreadPoolExecutor(max_workers=20) as pool:
        for future in as_completed(pool.submit(fetch_stats, e) for e in entries):
            name, entry = future.result()
            if name:
                with write_lock:
                    db[name] = entry
            completed += 1
            if completed % 100 == 0:
                _progress(on_progress, f"Fetching stats… {completed}/{total}")

    _progress(on_progress, "Resolving evolution chains and legendary status…")

    def fetch_species(name):
        try:
            r = requests.get(f"https://pokeapi.co/api/v2/pokemon-species/{name}", timeout=10)
            r.raise_for_status()
            data = r.json()
            return (
                data["evolution_chain"]["url"],
                bool(data.get("is_legendary")),
                bool(data.get("is_mythical")),
            )
        except Exception:
            return None, False, False

    chain_urls: set = set()
    with ThreadPoolExecutor(max_workers=20) as pool:
        for name, (url, legendary, mythical) in zip(
            list(db), pool.map(fetch_species, list(db))
        ):
            if url:
                chain_urls.add(url)
            if name in db:
                db[name]["legendary"] = legendary
                db[name]["mythical"]  = mythical

    final_forms: set = set()

    def fetch_chain(url):
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return _leaf_names(r.json()["chain"])
        except Exception:
            return set()

    with ThreadPoolExecutor(max_workers=20) as pool:
        for leaves in pool.map(fetch_chain, chain_urls):
            final_forms.update(leaves)

    for name in db:
        db[name]["fully_evolved"] = name in final_forms

    _progress(on_progress, "Saving cache…")
    to_save = {"_version": CACHE_VERSION, **db}
    with open(CACHE_FILE, "w") as f:
        json.dump(to_save, f)

    with _lock:
        _db = db
    _ready = True
    if on_ready:
        on_ready()


def _get_id(entry: dict) -> int:
    return int(entry["url"].rstrip("/").split("/")[-1])


def _leaf_names(node: dict) -> set:
    if not node["evolves_to"]:
        return {node["species"]["name"]}
    result = set()
    for child in node["evolves_to"]:
        result.update(_leaf_names(child))
    return result


def _progress(cb, msg: str):
    if cb:
        cb(msg)
