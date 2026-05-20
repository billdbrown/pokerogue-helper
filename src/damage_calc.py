import math


def damage_range(
    level: int,
    power: int,
    atk: int,
    def_: int,
    type_eff: float,
    is_stab: bool,
    is_burned: bool = False,
    is_physical: bool = True,
) -> tuple[int, int] | None:
    """Returns (min_dmg, max_dmg) using the standard Gen 6+ damage formula.
    Returns None for status / zero-power moves or invalid inputs."""
    if not power or def_ <= 0 or atk <= 0:
        return None
    base = math.floor(math.floor(math.floor(2 * level / 5 + 2) * power * atk / def_) / 50) + 2
    stab   = 1.5 if is_stab else 1.0
    burn   = 0.5 if (is_burned and is_physical) else 1.0
    mod    = stab * type_eff * burn
    lo = math.floor(math.floor(base * 85 / 100) * mod)
    hi = math.floor(base * mod)
    return lo, hi


def pct_color(pct_hi: float) -> str:
    if pct_hi >= 100: return "#f38ba8"
    if pct_hi >= 50:  return "#fab387"
    if pct_hi >= 25:  return "#f9e2af"
    return "#a6adc8"
