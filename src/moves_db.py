"""
Fetches all move names from PokéAPI and caches them to moves_cache.json.
Used for fuzzy-matching OCR move text against known valid names.
"""

import json
import os
import threading

import requests

from app_dirs import data_path
CACHE_FILE = data_path("moves_cache.json")
CACHE_VERSION = 1

_names: list = []
_ready        = False
_lock         = threading.Lock()


def init():
    threading.Thread(target=_build_or_load, daemon=True).start()


def is_ready() -> bool:
    return _ready


def all_names() -> list:
    with _lock:
        return list(_names)


def _build_or_load():
    global _names, _ready

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                data = json.load(f)
            if data.get("_version") == CACHE_VERSION:
                with _lock:
                    _names = data["names"]
                _ready = True
                return
        except Exception:
            pass

    try:
        resp = requests.get(
            "https://pokeapi.co/api/v2/move?limit=2000", timeout=30
        )
        resp.raise_for_status()
        names = [e["name"] for e in resp.json()["results"]]
        with open(CACHE_FILE, "w") as f:
            json.dump({"_version": CACHE_VERSION, "names": names}, f)
        with _lock:
            _names = names
        _ready = True
    except Exception as e:
        print(f"[moves_db] failed to load move names: {e}")
