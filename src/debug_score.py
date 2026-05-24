"""Debug tool: show move scoring for a single Pokémon.

Usage (run from src/):
    python debug_score.py skeledirge
    python debug_score.py skeledirge --egg        # all egg moves available
    python debug_score.py skeledirge --egg 1      # at most 1 egg move in the final 4
"""

import argparse
import requests
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations

import impact_db

BASE_URL = "https://pokeapi.co/api/v2"


def _fetch_poke(form: str) -> dict:
    r = requests.get(f"{BASE_URL}/pokemon/{form}", timeout=15)
    r.raise_for_status()
    d = r.json()
    return {
        "types":      [t["type"]["name"] for t in sorted(d["types"], key=lambda t: t["slot"])],
        "stats":      {s["stat"]["name"]: s["base_stat"] for s in d["stats"]},
        "move_names": [m["move"]["name"] for m in d.get("moves", [])],
        "species":    d.get("species", {}).get("name", form),
    }


def _fetch_evo_base(species: str) -> str:
    """Walk PokéAPI evo chain to find the root base species (e.g. skeledirge → fuecoco)."""
    try:
        r = requests.get(f"{BASE_URL}/pokemon-species/{species}", timeout=10)
        r.raise_for_status()
        chain_url = r.json()["evolution_chain"]["url"]
        r2 = requests.get(chain_url, timeout=10)
        r2.raise_for_status()
        return r2.json()["chain"]["species"]["name"]
    except Exception:
        return species


def _fetch_move(name: str) -> dict | None:
    r = requests.get(f"{BASE_URL}/move/{name}", timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    return {
        "name":     name,
        "type":     d["type"]["name"],
        "power":    d["power"],
        "accuracy": d["accuracy"],
        "category": d["damage_class"]["name"],
        "drain":    (d.get("meta") or {}).get("drain") or 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Debug impact score for a single Pokémon.")
    parser.add_argument("pokemon", help="Pokémon slug (e.g. skeledirge)")
    parser.add_argument(
        "--egg", nargs="?", const=4, type=int, default=None, metavar="N",
        help="Include egg moves; limit to N slots in the final 4 (default: no limit)",
    )
    args = parser.parse_args()

    name        = args.pokemon.lower()
    include_egg = args.egg is not None
    max_egg     = args.egg if args.egg is not None else 0

    # ── 1. Fetch base data ─────────────────────────────────────────────────────
    print(f"\nFetching {name} from PokéAPI…")
    pd      = _fetch_poke(name)
    types   = pd["types"]
    atk     = pd["stats"]["attack"]
    sp_atk  = pd["stats"]["special-attack"]
    speed   = pd["stats"]["speed"]
    species = pd["species"]
    print(f"  Types:  {' / '.join(types)}")
    print(f"  ATK {atk}  SP_ATK {sp_atk}  SPD {speed}")

    # ── 2. Filter to Pokerogue level-up learnset ───────────────────────────────
    print("\nFetching Pokerogue level-up learnset…")
    learnset = impact_db._fetch_pokerogue_learnset()
    allowed  = learnset.get(species) or learnset.get(name)
    if allowed:
        before = len(pd["move_names"])
        pd["move_names"] = [m for m in pd["move_names"] if m in allowed]
        print(f"  Filtered {before} → {len(pd['move_names'])} moves")
    else:
        print(f"  WARNING: no learnset entry for {species!r} or {name!r} — using full PokéAPI list")

    # ── 3. Optionally add egg moves ────────────────────────────────────────────
    egg_set: set[str] = set()
    if include_egg:
        print("\nFetching Pokerogue egg moves…")
        base_species = _fetch_evo_base(species)
        print(f"  Evo base: {base_species}")
        egg_map = impact_db._fetch_pokerogue_egg_moves()
        egg_set = (egg_map.get(species) or egg_map.get(name)
                   or egg_map.get(base_species) or set())
        print(f"  Egg moves: {sorted(egg_set) if egg_set else 'none found'}")
        before = len(pd["move_names"])
        pd["move_names"] = list(set(pd["move_names"]) | egg_set)
        added = len(pd["move_names"]) - before
        limit_note = f"  (cap: {max_egg} slot{'s' if max_egg != 1 else ''} in final 4)" if max_egg < 4 else ""
        print(f"  Added {added} egg moves → {len(pd['move_names'])} total{limit_note}")

    # ── 4. Fetch move details ──────────────────────────────────────────────────
    print(f"\nFetching details for {len(pd['move_names'])} moves…")
    with ThreadPoolExecutor(max_workers=20) as pool:
        move_details = dict(pool.map(lambda m: (m, _fetch_move(m)), pd["move_names"]))

    # ── 5. Warm effectiveness table ────────────────────────────────────────────
    if not impact_db._EFF:
        impact_db._build_effectiveness_table()

    # ── 6. Build damaging candidate list ──────────────────────────────────────
    candidates = [
        md for mn, md in move_details.items()
        if md
        and md.get("power") and md["power"] > 0
        and md.get("category") != "status"
        and (md.get("drain") or 0) >= 0
        and mn not in impact_db._EXCLUDED_MOVES
    ]

    # Compute per-move SE coverage vector and raw total
    move_rows: list[tuple[dict, dict, float]] = []
    for m in candidates:
        mtype = m["type"]
        stat  = atk if m["category"] == "physical" else sp_atk
        stab  = 1.5 if mtype in types else 1.0
        acc   = (m["accuracy"] or 100) / 100.0
        base  = stat * m["power"] * acc * stab
        pv    = {
            p: base * impact_db._EFF[(mtype, p)]
            for p in impact_db.ALL_PAIRINGS
            if impact_db._EFF.get((mtype, p), 0.0) > 1.0
        }
        if pv:
            move_rows.append((m, pv, sum(pv.values())))

    move_rows.sort(key=lambda x: -x[2])

    # ── 7. Print all damaging moves ────────────────────────────────────────────
    print(f"\n{'Move':<28} {'Type':<12} {'Cat':<9} {'BP':>4} {'Acc':>4}  {'SE pairs':>8}  {'Raw total':>12}")
    print("─" * 83)
    for m, pv, total in move_rows:
        tags = ""
        if m["type"] in types:
            tags += " ★"
        if m["name"] in egg_set:
            tags += " 🥚"
        print(
            f"{m['name']:<28} {m['type']:<12} {m['category']:<9}"
            f" {m['power']:>4} {(m['accuracy'] or 100):>4}"
            f"  {len(pv):>8}  {total:>12,.0f}{tags}"
        )

    # ── 8. Selection ───────────────────────────────────────────────────────────
    # With an egg cap the interaction between the egg slot and move choice means
    # greedy is suboptimal (it can't see that spending the egg slot on a type
    # already covered by level-up moves wastes it). Brute-force all C(n,4)
    # combos when the cap is active; greedy suffices otherwise.

    def _score_combo(combo):
        best: dict = {}
        for _, pv, _ in combo:
            for p, v in pv.items():
                if v > best.get(p, 0.0):
                    best[p] = v
        return sum(best.values()), best

    capped = include_egg and max_egg < 4
    cap_label = f"(egg cap: {max_egg}, brute-force optimal)" if capped else "(greedy)"
    print(f"\n{'═' * 60}")
    print(f"SELECTION {cap_label}")
    print(f"{'═' * 60}")

    current_best: dict = {}
    selected: list[dict] = []

    if capped:
        k = min(4, len(move_rows))
        best_score = -1.0
        best_combo: tuple = ()
        for combo in combinations(move_rows, k):
            egg_count = sum(1 for m, _, __ in combo if m["name"] in egg_set)
            if egg_count > max_egg:
                continue
            score, _ = _score_combo(combo)
            if score > best_score:
                best_score = score
                best_combo = combo
        if best_combo:
            _, current_best = _score_combo(best_combo)
            selected = [m for m, _, __ in best_combo]
        print(f"\nOptimal combo under cap:")
        for m in selected:
            egg_tag = " 🥚" if m["name"] in egg_set else ""
            print(f"  {m['name']}{egg_tag}  [{m['type']}]")
    else:
        pool_pv = [(m, pv) for m, pv, _ in move_rows]
        egg_picked = 0
        for step in range(min(4, len(pool_pv))):
            gains = sorted(
                (
                    (sum(max(0.0, v - current_best.get(p, 0.0)) for p, v in pv.items()), i, m, pv)
                    for i, (m, pv) in enumerate(pool_pv)
                    if not (m["name"] in egg_set and egg_picked >= max_egg)
                ),
                reverse=True,
            )
            if not gains:
                print(f"\nStep {step + 1}: egg cap reached — stopping.")
                break
            best_gain, _, best_m, best_pv = gains[0]
            if best_gain <= 0:
                print(f"\nStep {step + 1}: zero marginal gain — stopping.")
                break
            new_pairings = [p for p, v in best_pv.items() if v > current_best.get(p, 0.0)]
            egg_tag = " 🥚" if best_m["name"] in egg_set else ""
            print(f"\nStep {step + 1}: {best_m['name']}{egg_tag}  [{best_m['type']}]  "
                  f"+{best_gain:,.0f}  ({len(new_pairings)} new pairings)")
            alts = [f"{m['name']} +{g:,.0f}" for g, _, m, _ in gains[1:4] if g > 0]
            if alts:
                print(f"         Alternatives: {' | '.join(alts)}")
            for p, v in best_pv.items():
                if v > current_best.get(p, 0.0):
                    current_best[p] = v
            if best_m["name"] in egg_set:
                egg_picked += 1
            selected.append(best_m)
            pool_pv = [(m, pv) for m, pv in pool_pv if m["name"] != best_m["name"]]

    all_candidate_names = sorted(m["name"] for m, _, __ in move_rows)
    print(f"\n{'═' * 60}")
    print(f"FINAL MOVESET:  {', '.join(m['name'] for m in selected)}")
    print(f"TOTAL SCORE:    {sum(current_best.values()):,.0f}")
    print(f"FULL POOL ({len(all_candidate_names)}): {', '.join(all_candidate_names)}")
    if include_egg:
        print(f"EGG MOVES:      {', '.join(sorted(egg_set)) if egg_set else 'none'}")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    main()
