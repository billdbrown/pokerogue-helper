from dataclasses import dataclass, field
from typing import Literal

FightMode = Literal['1v1', '2v2']


@dataclass
class UIState:
    """Tracks the title-bar badges (1V1/2V2, boss-1, boss-2). Populated by the
    JS-state dispatcher each snapshot."""
    fight_mode: FightMode = '1v1'
    boss_mask: set[int] = field(default_factory=set)

    def reset(self):
        self.fight_mode = '1v1'
        self.boss_mask.clear()
