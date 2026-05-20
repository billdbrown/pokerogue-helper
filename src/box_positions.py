import json

_FILE = "box_positions.json"

# Valid preset keys per slot
SLOT_PRESETS = ('1v1_standard', '1v1_boss', '2v2_standard', '2v2_boss')

_GLOBAL_KEYS = (
    'wave', 'active', 'player_lv',
    'move1', 'move2', 'move3', 'move4',
    'accuracy', 'player_type1', 'player_type2',
)


def load() -> dict:
    try:
        with open(_FILE) as f:
            data = json.load(f)
        if 'global' not in data and 'slot0' not in data:
            data = _migrate_v1(data)
        return data
    except Exception:
        return {}


def save(data: dict):
    try:
        with open(_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[box_positions] save failed: {e}")


def _migrate_v1(old: dict) -> dict:
    """Convert old flat format to nested preset format, preserving all data."""
    new: dict = {'global': {}, 'slot0': {}, 'slot1': {}}
    for key in _GLOBAL_KEYS:
        if key in old:
            new['global'][key] = old[key]
    slot0: dict = {}
    if 'box1' in old:
        slot0['name'] = old['box1']
    if 'opp_level' in old:
        slot0['level'] = old['opp_level']
    if 'enemy_type1' in old:
        slot0['type1'] = old['enemy_type1']
    if 'enemy_type2' in old:
        slot0['type2'] = old['enemy_type2']
    if slot0:
        new['slot0']['1v1_standard'] = slot0
    return new
