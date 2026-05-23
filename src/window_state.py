import json
import os
from app_dirs import data_path

_FILE = data_path("window_state.json")


def load() -> dict:
    try:
        with open(_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_key(key: str, data: dict):
    state = load()
    state[key] = data
    try:
        with open(_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass
