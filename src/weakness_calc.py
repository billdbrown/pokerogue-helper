from pokemon_api import fetch_type_relations

ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice",
    "fighting", "poison", "ground", "flying", "psychic", "bug",
    "rock", "ghost", "dragon", "dark", "steel", "fairy",
]


def _effectiveness(attack_type: str, defend_types: list[str]) -> float:
    """Damage multiplier of attack_type against a Pokemon with defend_types."""
    eff = 1.0
    for dt in defend_types:
        rels = fetch_type_relations(dt)
        if any(e["name"] == attack_type for e in rels.get("no_damage_from", [])):
            return 0.0
        if any(e["name"] == attack_type for e in rels.get("double_damage_from", [])):
            eff *= 2.0
        elif any(e["name"] == attack_type for e in rels.get("half_damage_from", [])):
            eff *= 0.5
    return eff


def detailed_coverage(move_types: list[str]) -> tuple[set, dict, set]:
    """
    Returns (full, partial, gaps).

    full    — defending types where the team always has SE coverage regardless of
              what secondary type the opponent carries.
    partial — {def_type: [secondary types that make ALL team SE moves immune (0×)]}.
              The team has SE coverage against the pure type, but specific dual-type
              combinations completely negate it (e.g. Electric vs Water/Ground).
    gaps    — defending types with no SE coverage at all.
    """
    unique_attacks = {mt for mt in move_types if mt in ALL_TYPES}
    full: set[str] = set()
    partial: dict[str, list[str]] = {}
    gaps: set[str] = set()

    for def_type in ALL_TYPES:
        se_attacks = {mt for mt in unique_attacks if _effectiveness(mt, [def_type]) >= 2.0}
        if not se_attacks:
            gaps.add(def_type)
            continue

        # Secondaries that make every SE attack do 0× against [def_type + secondary]
        immune_cancelers = [
            sec for sec in ALL_TYPES
            if sec != def_type
            and all(_effectiveness(mt, [def_type, sec]) == 0.0 for mt in se_attacks)
        ]

        if immune_cancelers:
            partial[def_type] = immune_cancelers
        else:
            full.add(def_type)

    return full, partial, gaps


def slot_coverage(slot_move_types: list[str], other_move_types: list[str]) -> tuple[int, int]:
    """Returns (breadth, unique) for one slot's moves vs the rest of the team."""
    my_covered = {dt for dt in ALL_TYPES if any(_effectiveness(mt, [dt]) >= 2.0 for mt in slot_move_types)}
    others_covered = {dt for dt in ALL_TYPES if any(_effectiveness(mt, [dt]) >= 2.0 for mt in other_move_types)}
    return len(my_covered), len(my_covered - others_covered)


def redundancy_suggestions(move_types: list[str], n: int = 1) -> list[tuple[str, int]]:
    """Types that reinforce the most defending types currently covered by only one attack."""
    unique_attacks = {mt for mt in move_types if mt in ALL_TYPES}
    fragile = {
        dt for dt in ALL_TYPES
        if sum(1 for mt in unique_attacks if _effectiveness(mt, [dt]) >= 2.0) == 1
    }
    scored = [
        (c, sum(1 for dt in fragile if _effectiveness(c, [dt]) >= 2.0))
        for c in ALL_TYPES
    ]
    scored = [(c, count) for c, count in scored if count > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:n]


def covered_gaps(candidate_type: str, gaps: set) -> list[str]:
    """Returns the gap types that candidate_type covers with SE damage, sorted."""
    return sorted(gap for gap in gaps if _effectiveness(candidate_type, [gap]) >= 2.0)


def coverage_suggestions_from_gaps(gaps: set, n: int = 2) -> list[tuple[str, int]]:
    """Top n types to add that cover the most gap types — takes a pre-computed gaps set."""
    scored = [
        (c, sum(1 for g in gaps if _effectiveness(c, [g]) >= 2.0))
        for c in ALL_TYPES
    ]
    scored = [(c, count) for c, count in scored if count > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:n]


def coverage_suggestions(move_types: list[str], n: int = 2) -> list[tuple[str, int]]:
    """Top n types to add that cover the most current gap types with SE damage."""
    _, _, gaps = detailed_coverage(move_types)
    return coverage_suggestions_from_gaps(gaps, n)


def dangerous_combos(
    move_types: list[str],
    team_weaknesses: list[dict | None],
    n: int = 3,
) -> list[tuple[str, str, int]]:
    """Top n dual-type combos the team has no SE coverage against, scored by members weak to it."""
    unique_attacks = {mt for mt in move_types if mt in ALL_TYPES}
    results = []
    for i, t1 in enumerate(ALL_TYPES):
        for t2 in ALL_TYPES[i + 1:]:
            covered = any(
                _effectiveness(mt, [t1, t2]) >= 2.0 for mt in unique_attacks
            )
            if covered:
                continue
            weak_count = sum(
                1 for w in team_weaknesses
                if w and w.get(t1, 1.0) * w.get(t2, 1.0) >= 2.0
            )
            results.append((t1, t2, weak_count))
    results.sort(key=lambda x: x[2], reverse=True)
    return results[:n]


def calculate_weaknesses(defending_types: list[str]) -> dict[str, float]:
    """
    Returns a dict mapping each attacking type to its final damage multiplier
    against a Pokemon with the given defending types.
    Only returns non-neutral multipliers (i.e., != 1.0).
    """
    multipliers: dict[str, float] = {t: 1.0 for t in ALL_TYPES}

    for defending_type in defending_types:
        relations = fetch_type_relations(defending_type)

        for entry in relations.get("double_damage_from", []):
            multipliers[entry["name"]] *= 2.0

        for entry in relations.get("half_damage_from", []):
            multipliers[entry["name"]] *= 0.5

        for entry in relations.get("no_damage_from", []):
            multipliers[entry["name"]] *= 0.0

    return {t: m for t, m in multipliers.items() if m != 1.0}
