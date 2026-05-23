"""Offensive type coverage and catch recommendation logic."""

from weakness_calc import calculate_weaknesses

_ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice", "fighting",
    "poison", "ground", "flying", "psychic", "bug", "rock", "ghost",
    "dragon", "dark", "steel", "fairy",
]

# For each attacking type: which defending types does it hit super-effectively?
OFFENSIVE_CHART: dict[str, frozenset[str]] = {}
for _atk in _ALL_TYPES:
    _covered: set[str] = set()
    for _def in _ALL_TYPES:
        if calculate_weaknesses([_def]).get(_atk, 1.0) > 1.0:
            _covered.add(_def)
    OFFENSIVE_CHART[_atk] = frozenset(_covered)


def offensive_coverage(move_types: list[str]) -> frozenset[str]:
    """Defending types hit super-effectively by any move of the given types."""
    result: set[str] = set()
    for t in move_types:
        result |= OFFENSIVE_CHART.get(t, frozenset())
    return frozenset(result)
