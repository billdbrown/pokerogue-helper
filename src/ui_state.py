from dataclasses import dataclass, field
from typing import Literal

FightMode = Literal['1v1', '2v2']


@dataclass
class UIState:
    fight_mode: FightMode = '1v1'
    boss_mask: set[int] = field(default_factory=set)

    def slot_preset(self, slot: int) -> str | None:
        """Preset key for a slot's boxes, or None if the slot doesn't exist."""
        if slot == 1 and self.fight_mode == '1v1':
            return None
        boss = slot in self.boss_mask
        return f"{self.fight_mode}_{'boss' if boss else 'standard'}"

    def is_2v2(self) -> bool:
        return self.fight_mode == '2v2'

    def reset(self):
        self.fight_mode = '1v1'
        self.boss_mask.clear()
