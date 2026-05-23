"""
Impact Score: pre-computed offensive threat score for every final-evolution
Pokemon form. See docs/impact_score.md for full methodology.

Score = sum over all 171 type pairings of the best SE damage the optimal
4-move selection can deal to that pairing, where:
  damage = stat * base_power * (accuracy/100) * STAB * SE_multiplier

Cached to impact_cache.json in the user data dir.
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from app_dirs import data_path
from weakness_calc import ALL_TYPES, _effectiveness

BASE_URL = "https://pokeapi.co/api/v2"
CACHE_FILE = data_path("impact_cache.json")
CACHE_VERSION = 5

# All 171 unordered type pairings: 18 single + C(18,2)=153 dual
ALL_PAIRINGS: list[tuple[str, ...]] = []
for _i, _t1 in enumerate(ALL_TYPES):
    ALL_PAIRINGS.append((_t1,))
    for _t2 in ALL_TYPES[_i + 1:]:
        ALL_PAIRINGS.append((_t1, _t2))

# Pre-computed effectiveness table: (atk_type, pairing) -> multiplier
# Populated once during _build_effectiveness_table(), before any score math.
_EFF: dict[tuple, float] = {}

_db: dict[str, dict] = {}
_ready = False
_lock = threading.Lock()


# ── public API ────────────────────────────────────────────────────────────────

def init(on_progress=None, on_ready=None):
    threading.Thread(
        target=_build_or_load, args=(on_progress, on_ready), daemon=True
    ).start()


def is_ready() -> bool:
    return _ready


def get(name: str) -> dict | None:
    """Returns {score, percentile, types} or None."""
    with _lock:
        return _db.get(name.lower())


def top_n(n: int = 20, exclude_legendary: bool = False) -> list[tuple[str, float]]:
    with _lock:
        items = [
            (name, v["score"]) for name, v in _db.items()
            if not (exclude_legendary and v.get("legendary"))
        ]
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:n]


def impact_percentile(score: float, exclude_legendary: bool = False) -> int:
    with _lock:
        values = [
            v["score"] for v in _db.values()
            if not (exclude_legendary and v.get("legendary"))
        ]
    if not values:
        return 0
    return round(sum(1 for v in values if v < score) / len(values) * 100)


def all_entries() -> dict[str, dict]:
    with _lock:
        return dict(_db)


def pairing_vector(name: str) -> dict[tuple, float]:
    """Best SE damage this Pokémon can deal to each of the 171 type pairings."""
    with _lock:
        entry = _db.get(name.lower())
    if not entry:
        return {}
    types   = entry.get("types", [])
    atk     = entry.get("atk", 0)
    sp_atk  = entry.get("sp_atk", 0)
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
        base = stat * power * (accuracy / 100.0) * stab
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


# ── score computation ─────────────────────────────────────────────────────────

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
) -> tuple[float, list[dict]]:
    """Returns (score, selected_moves) where selected_moves are the optimal 4."""
    damaging = [
        m for m in moves
        if m.get("power") and m["power"] > 0 and m.get("category") != "status"
    ]
    if not damaging:
        return 0.0, []

    # Pre-compute each move's value against every pairing, keeping move ref
    move_pv: list[tuple[dict, dict[tuple, float]]] = []
    for m in damaging:
        mtype = m["type"]
        stat = atk if m["category"] == "physical" else sp_atk
        stab = 1.5 if mtype in pokemon_types else 1.0
        acc = (m["accuracy"] or 100) / 100.0
        base = stat * m["power"] * acc * stab

        pv: dict[tuple, float] = {}
        for pairing in ALL_PAIRINGS:
            se = _EFF.get((mtype, pairing), 0.0)
            if se > 1.0:
                pv[pairing] = base * se
        if pv:
            move_pv.append((m, pv))

    if not move_pv:
        return 0.0, []

    # Greedy moveset: pick 4 moves by marginal gain
    current_best: dict[tuple, float] = {}
    selected: list[dict] = []
    for _ in range(min(4, len(move_pv))):
        best_gain = 0.0
        best_idx = -1
        for idx, (_, pv) in enumerate(move_pv):
            gain = sum(
                max(0.0, v - current_best.get(p, 0.0))
                for p, v in pv.items()
            )
            if gain > best_gain:
                best_gain = gain
                best_idx = idx
        if best_idx == -1:
            break
        m, pv = move_pv[best_idx]
        for p, v in pv.items():
            if v > current_best.get(p, 0.0):
                current_best[p] = v
        selected.append(m)
        move_pv.pop(best_idx)

    return sum(current_best.values()), selected


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


# ── cache build ───────────────────────────────────────────────────────────────

def _build_or_load(on_progress, on_ready):
    global _db, _ready

    if os.path.exists(CACHE_FILE):
        _prog(on_progress, "Loading impact cache…")
        try:
            with open(CACHE_FILE) as f:
                data = json.load(f)
            if data.get("_version") == CACHE_VERSION:
                data.pop("_version")
                with _lock:
                    _db = data
                _build_effectiveness_table()
                _ready = True
                if on_ready:
                    on_ready()
                return
            _prog(on_progress, "Impact cache outdated — rebuilding…")
        except Exception:
            pass

    _prog(on_progress, "Building impact cache — takes ~3 minutes on first run…")

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
    all_forms = sorted(f for f in set(all_forms) if "-mega" not in f)
    _prog(on_progress, f"{len(all_forms)} total forms to score…")

    # Step 3: fetch pokemon data (types, stats, move list) for each form
    def fetch_poke(form: str) -> tuple[str, dict | None]:
        try:
            r = requests.get(f"{BASE_URL}/pokemon/{form}", timeout=15)
            r.raise_for_status()
            d = r.json()
            return form, {
                "types": [t["type"]["name"] for t in sorted(d["types"], key=lambda t: t["slot"])],
                "stats": {s["stat"]["name"]: s["base_stat"] for s in d["stats"]},
                "move_names": [m["move"]["name"] for m in d.get("moves", [])],
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
            return move_name, {
                "name": move_name,
                "type": d["type"]["name"],
                "power": d["power"],
                "accuracy": d["accuracy"],
                "category": d["damage_class"]["name"],
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

    # Step 5: warm effectiveness table then compute scores
    _prog(on_progress, "Warming type effectiveness table…")
    _build_effectiveness_table()

    _prog(on_progress, "Computing impact scores…")
    db: dict[str, dict] = {}
    for form, pd in pokemon_data.items():
        atk    = pd["stats"].get("attack", 0)
        sp_atk = pd["stats"].get("special-attack", 0)
        speed  = pd["stats"].get("speed", 0)
        moves  = [move_cache[mn] for mn in pd["move_names"] if move_cache.get(mn)]
        impact, selected = _compute_score(pd["types"], atk, sp_atk, moves)
        db[form] = {
            "impact":  impact,
            "atk":     atk,
            "sp_atk":  sp_atk,
            "speed":   speed,
            "types":   pd["types"],
            "legendary": _is_legendary(pd.get("species", form), form, legendary_set),
            "moves": [
                {
                    "name":     m["name"],
                    "type":     m["type"],
                    "power":    m["power"],
                    "accuracy": m["accuracy"],
                    "category": m["category"],
                }
                for m in selected
            ],
        }

    # Step 6: speed percentiles → combined score → combined percentiles
    all_speeds = [v["speed"] for v in db.values()]
    n = len(all_speeds)
    for entry in db.values():
        sp = entry["speed"]
        entry["speed_pct"] = round(sum(1 for x in all_speeds if x < sp) / n * 100)

    _SPEED_THRESHOLD = 30  # percentile below which a penalty applies
    for entry in db.values():
        sp = entry["speed_pct"]
        if sp >= _SPEED_THRESHOLD:
            factor = 1.0
        else:
            factor = 0.75 + 0.25 * (sp / _SPEED_THRESHOLD)
        entry["score"] = entry["impact"] * factor

    all_scores = [v["score"] for v in db.values()]
    for entry in db.values():
        s = entry["score"]
        entry["percentile"] = round(sum(1 for x in all_scores if x < s) / n * 100)

    _prog(on_progress, "Saving impact cache…")
    with open(CACHE_FILE, "w") as f:
        json.dump({"_version": CACHE_VERSION, **db}, f)

    with _lock:
        _db = db
    _ready = True
    if on_ready:
        on_ready()
    _prog(on_progress, f"Done — {len(db)} forms scored.")


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

    _build_or_load(lambda msg: print(f"  {msg}"), _on_ready)
    done.wait()

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(f"\nTop {n} by Impact Score (all forms):")
    print(f"{'Rank':<5} {'Name':<30} {'Score':>10}  {'%ile':>5}  Types")
    print("-" * 65)
    for rank, (name, score) in enumerate(top_n(n), 1):
        entry = _db[name]
        pct = entry["percentile"]
        types = "/".join(entry["types"])
        leg = " [L]" if entry.get("legendary") else ""
        print(f"{rank:<5} {name:<30} {score:>10.0f}  p{pct:<4}  {types}{leg}")

    print(f"\nTop {n} excluding legendaries/mythicals:")
    print(f"{'Rank':<5} {'Name':<30} {'Score':>10}  {'%ile':>5}  Types")
    print("-" * 65)
    for rank, (name, score) in enumerate(top_n(n, exclude_legendary=True), 1):
        entry = _db[name]
        pct = entry["percentile"]
        types = "/".join(entry["types"])
        print(f"{rank:<5} {name:<30} {score:>10.0f}  p{pct:<4}  {types}")
