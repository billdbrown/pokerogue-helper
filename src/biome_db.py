"""
Biome encounter data: which Pokemon are common/uncommon/rare in each biome, and
which biome each one links to next.

Source: Pokerogue repo. One file per biome under src/data/balance/biomes/*.ts,
plus the BiomeId and SpeciesId enums. Parsed into biome_cache.json keyed by the
numeric BiomeId (matching battle.arena.biomeType read live in js_state).

Cache shape (JSON keys are strings; loaded back as ints):
  { "15": { "key": "ICE_CAVE", "display": "Ice Cave",
            "links": [{"id": 31, "display": "Snowy Forest", "weight": null}],
            "wild": { "<speciesId>": "COMMON", ... },
            "boss": [<speciesId>, ...] },
    "_version": 1, "_species": {"BULBASAUR": 1, ...} }
"""

from __future__ import annotations

import json
import os
import re
import threading

import requests

from app_dirs import data_path

CACHE_VERSION = 1
CACHE_FILE = data_path("biome_cache.json")

_REPO = "https://raw.githubusercontent.com/pagefaultgames/pokerogue/main"
_BIOME_ID_URL  = f"{_REPO}/src/enums/biome-id.ts"
_SPECIES_ID_URL = f"{_REPO}/src/enums/species-id.ts"
_BIOMES_DIR_URL = f"{_REPO}/src/data/balance/biomes"
_BIOMES_API = (
    "https://api.github.com/repos/pagefaultgames/pokerogue/contents/"
    "src/data/balance/biomes"
)

# Wild tiers, most-common first. A species listed in several wild tiers keeps the
# most common one. BOSS* tiers are tracked separately.
_WILD_TIERS = ["COMMON", "UNCOMMON", "RARE", "SUPER_RARE", "ULTRA_RARE"]

# Fallback biome file list if the GitHub contents API is unavailable.
_FALLBACK_FILES = [
    "abyss.ts", "badlands.ts", "beach.ts", "cave.ts", "construction-site.ts",
    "desert.ts", "dojo.ts", "end.ts", "factory.ts", "fairy-cave.ts", "forest.ts",
    "grass.ts", "graveyard.ts", "ice-cave.ts", "island.ts", "jungle.ts",
    "laboratory.ts", "lake.ts", "meadow.ts", "metropolis.ts", "mountain.ts",
    "plains.ts", "power-plant.ts", "ruins.ts", "sea.ts", "seabed.ts", "slum.ts",
    "snowy-forest.ts", "space.ts", "swamp.ts", "tall-grass.ts", "temple.ts",
    "town.ts", "volcano.ts", "wasteland.ts",
]

_db: dict[int, dict] = {}
_norm_name_to_id: dict[str, int] = {}
_ready = False
_lock = threading.Lock()


def is_ready() -> bool:
    return _ready


def _display_from_key(key: str) -> str:
    return key.replace("_", " ").title()


def _norm(name: str) -> str:
    """Normalize a species name/enum to a comparable key (alphanumerics only)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# ── Enum parsers ──────────────────────────────────────────────────────────────

def _parse_biome_ids(text: str) -> dict[str, int]:
    """biome-id.ts is `export const BiomeId = { TOWN: 0, ... } as const;`."""
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^\s*([A-Z][A-Z0-9_]*)\s*:\s*(\d+)\s*,", text, re.M)
    }


def _parse_species_ids(text: str) -> dict[str, int]:
    """species-id.ts is `export enum SpeciesId { BULBASAUR = 1, IVYSAUR, ... }`.

    Values auto-increment; an explicit `= N` resets the running counter.
    JSDoc/comment lines don't match the member pattern and are skipped.
    """
    ids: dict[str, int] = {}
    counter = 0
    for line in text.splitlines():
        m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*,?\s*(?://.*)?$", line)
        if m:
            counter = int(m.group(2))
            ids[m.group(1)] = counter
            continue
        m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*,\s*(?://.*)?$", line)
        if m:
            counter += 1
            ids[m.group(1)] = counter
    return ids


# ── Biome file parser ─────────────────────────────────────────────────────────

def _parse_biome_file(text: str, species_ids: dict[str, int],
                      biome_ids: dict[str, int]) -> tuple[int, dict] | None:
    bid_m = re.search(r"biomeId:\s*BiomeId\.(\w+)", text)
    if not bid_m:
        return None
    key = bid_m.group(1)
    biome_id = biome_ids.get(key)
    if biome_id is None:
        return None

    wild: dict[int, str] = {}
    boss: set[int] = set()

    pool_m = re.search(r"const pokemonPool[^=]*=\s*\{(.*?)\n\};", text, re.S)
    if pool_m:
        block = pool_m.group(1)
        headers = [(m.start(), m.group(1))
                   for m in re.finditer(r"\[BiomePoolTier\.(\w+)\]", block)]
        for i, (start, tier) in enumerate(headers):
            end = headers[i + 1][0] if i + 1 < len(headers) else len(block)
            seg = block[start:end]
            sids = [species_ids[s] for s in re.findall(r"SpeciesId\.(\w+)", seg)
                    if s in species_ids]
            if tier.startswith("BOSS"):
                boss.update(sids)
            elif tier in _WILD_TIERS:
                rank = _WILD_TIERS.index(tier)
                for sid in sids:
                    prev = wild.get(sid)
                    if prev is None or rank < _WILD_TIERS.index(prev):
                        wild[sid] = tier

    links: list[dict] = []
    link_m = re.search(r"const biomeLinks[^=]*=\s*(\[.*?\]);", text, re.S)
    if link_m:
        seen: set[int] = set()
        for m in re.finditer(r"BiomeId\.(\w+)\s*(?:,\s*(\d+))?", link_m.group(1)):
            lid = biome_ids.get(m.group(1))
            if lid is None or lid in seen:
                continue
            seen.add(lid)
            links.append({
                "id": lid,
                "display": _display_from_key(m.group(1)),
                "weight": int(m.group(2)) if m.group(2) else None,
            })
        if any(e["weight"] for e in links):
            links.sort(key=lambda e: e["weight"] or 0, reverse=True)

    entry = {
        "key": key,
        "display": _display_from_key(key),
        "links": links,
        "wild": {str(k): v for k, v in wild.items()},
        "boss": sorted(boss),
    }
    return biome_id, entry


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _fetch_biome_files() -> list[str]:
    """Return the list of biome .ts filenames (GitHub API, fallback hardcoded)."""
    try:
        r = requests.get(_BIOMES_API, timeout=20)
        r.raise_for_status()
        files = [it["name"] for it in r.json()
                 if it.get("name", "").endswith(".ts")]
        if files:
            return files
    except Exception:
        pass
    return list(_FALLBACK_FILES)


def _build() -> dict:
    biome_text = requests.get(_BIOME_ID_URL, timeout=30)
    biome_text.raise_for_status()
    biome_ids = _parse_biome_ids(biome_text.text)

    species_text = requests.get(_SPECIES_ID_URL, timeout=30)
    species_text.raise_for_status()
    species_ids = _parse_species_ids(species_text.text)

    out: dict = {}
    for fname in _fetch_biome_files():
        try:
            r = requests.get(f"{_BIOMES_DIR_URL}/{fname}", timeout=30)
            r.raise_for_status()
            parsed = _parse_biome_file(r.text, species_ids, biome_ids)
            if parsed:
                bid, entry = parsed
                out[str(bid)] = entry
        except Exception:
            continue

    out["_version"] = CACHE_VERSION
    out["_species"] = species_ids
    return out


# ── Load / accessors ──────────────────────────────────────────────────────────

def _install(cached: dict) -> None:
    global _db, _norm_name_to_id
    species = cached.get("_species") or {}
    db: dict[int, dict] = {}
    for k, v in cached.items():
        if k.startswith("_"):
            continue
        db[int(k)] = {
            **v,
            "wild": {int(sid): tier for sid, tier in (v.get("wild") or {}).items()},
            "boss": set(v.get("boss") or []),
        }
    norm = {_norm(name): sid for name, sid in species.items()}
    with _lock:
        _db = db
        _norm_name_to_id = norm


def _build_or_load(on_progress=None, on_ready=None) -> None:
    global _ready

    def _prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("_version") == CACHE_VERSION:
                _install(cached)
                _ready = True
                _prog(f"Biomes loaded from cache ({len(_db)}).")
                if on_ready:
                    on_ready()
                return
        except Exception:
            pass

    _prog("Fetching biome data…")
    try:
        cached = _build()
    except Exception as e:
        _prog(f"Warning: could not build biome data ({e}).")
        _ready = True
        if on_ready:
            on_ready()
        return

    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cached, f)
    except Exception:
        pass

    _install(cached)
    _ready = True
    _prog(f"Biomes ready: {len(_db)} entries.")
    if on_ready:
        on_ready()


def init(on_progress=None, on_ready=None) -> None:
    threading.Thread(
        target=_build_or_load, args=(on_progress, on_ready), daemon=True
    ).start()


def biome_display(biome_id) -> str | None:
    if biome_id is None:
        return None
    with _lock:
        entry = _db.get(int(biome_id))
    return entry["display"] if entry else None


def next_biomes(biome_id) -> list[dict]:
    """Ordered list of {id, display, weight} the biome can lead into."""
    if biome_id is None:
        return []
    with _lock:
        entry = _db.get(int(biome_id))
    return list(entry["links"]) if entry else []


def rarity_for(biome_id, species_id, species_name: str | None = None
               ) -> tuple[str | None, bool]:
    """(wild_tier, is_boss_pool) for a species in a biome.

    wild_tier is one of _WILD_TIERS, or None if not a wild encounter there.
    Matches on species_id, falling back to a normalized-name lookup.
    """
    if biome_id is None:
        return (None, False)
    with _lock:
        entry = _db.get(int(biome_id))
        if not entry:
            return (None, False)
        sid = None
        if species_id is not None:
            try:
                sid = int(species_id)
            except (TypeError, ValueError):
                sid = None
        if sid is None and species_name:
            sid = _norm_name_to_id.get(_norm(species_name))
        if sid is None:
            return (None, False)
        return (entry["wild"].get(sid), sid in entry["boss"])
