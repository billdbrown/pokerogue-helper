"""Turn-order computation using exact speed values from Pokerogue.

`speed` is the in-battle effective stat from `pokemon.getEffectiveStat(SPD)` —
already includes IVs, EVs, nature, items, ability boosts, weather. Stat stages
and Trick Room are applied here on top.
"""

from dataclasses import dataclass


# Pokerogue stat-stage multiplier table (mainline Gen-IX semantics).
_STAGE_MULT = {
    -6: 2/8, -5: 2/7, -4: 2/6, -3: 2/5, -2: 2/4, -1: 2/3,
     0: 1.0,
     1: 3/2,  2: 4/2,  3: 5/2,  4: 6/2,  5: 7/2,  6: 8/2,
}


@dataclass
class Fighter:
    name: str
    side: str         # "player" | "opponent"
    position: int     # 0 or 1 (slot on that side)
    speed: int        # exact effective speed from getEffectiveStat (no stages)
    speed_stage: int  # -6..+6
    level: int = 0    # display only

    @property
    def staged_speed(self) -> int:
        return int(self.speed * _STAGE_MULT.get(self.speed_stage, 1.0))


def make_fighter(name: str, side: str, position: int,
                 speed: int, level: int, speed_stage: int = 0) -> Fighter:
    return Fighter(
        name=name,
        side=side,
        position=position,
        speed=int(speed) if speed else 0,
        speed_stage=int(speed_stage or 0),
        level=int(level or 0),
    )


def order(fighters: list[Fighter], trick_room: bool = False) -> list[Fighter]:
    """Sort by staged_speed descending — or ascending if Trick Room is up."""
    return sorted(fighters, key=lambda f: f.staged_speed, reverse=not trick_room)


# Gap in staged_speed below which a 1v1 verdict is reported as a coin flip
# (covers priority moves we can't model). Definitive data — tighter than before.
COIN_FLIP_THRESHOLD = 1
