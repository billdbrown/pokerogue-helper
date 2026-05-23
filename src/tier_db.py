"""
Fetches Smogon tier assignments from Pokemon Showdown's formats-data.ts and
caches them to tier_cache.json. Tiers reflect Gen 9 (SV) singles viability.
"""

import json
import os
import re
import threading

import requests

from app_dirs import data_path
CACHE_FILE = data_path("tier_cache.json")
PS_URL = "https://raw.githubusercontent.com/smogon/pokemon-showdown/master/config/formats-data.ts"

# Tiers we surface, in strength order. Anything not in this set is hidden.
KNOWN_TIERS = {"AG", "Uber", "OU", "UUBL", "UU", "RUBL", "RU", "NUBL", "NU", "PUBL", "PU", "ZU", "ZUBL", "NFE", "LC"}

TIER_COLORS = {
    "AG":   ("#cba6f7", "#1e1e2e"),
    "Uber": ("#f38ba8", "#1e1e2e"),
    "OU":   ("#fab387", "#1e1e2e"),
    "UUBL": ("#f9e2af", "#1e1e2e"),
    "UU":   ("#f9e2af", "#1e1e2e"),
    "RUBL": ("#a6e3a1", "#1e1e2e"),
    "RU":   ("#a6e3a1", "#1e1e2e"),
    "NUBL": ("#89dceb", "#1e1e2e"),
    "NU":   ("#89dceb", "#1e1e2e"),
    "PUBL": ("#89b4fa", "#1e1e2e"),
    "PU":   ("#89b4fa", "#1e1e2e"),
    "ZU":   ("#b4befe", "#1e1e2e"),
    "ZUBL": ("#b4befe", "#1e1e2e"),
    "NFE":  ("#585b70", "#cdd6f4"),
    "LC":   ("#585b70", "#cdd6f4"),
}

_tiers: dict = {}   # normalized_name -> tier string
_ready = False
_lock  = threading.Lock()


def init(on_ready=None):
    threading.Thread(target=_load, args=(on_ready,), daemon=True).start()


def is_ready() -> bool:
    return _ready


def get_tier(pokemon_name: str) -> str | None:
    key = _normalize(pokemon_name)
    with _lock:
        return _tiers.get(key)


def tier_color(tier: str) -> tuple:
    """Returns (background, foreground) hex strings."""
    return TIER_COLORS.get(tier, ("#585b70", "#cdd6f4"))


# ── internal ──────────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    return name.lower().replace("-", "").replace(" ", "").replace("'", "")


def _load(on_ready):
    global _tiers, _ready

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                data = json.load(f)
            with _lock:
                _tiers = data
            _ready = True
            if on_ready:
                on_ready()
            return
        except Exception:
            pass

    try:
        resp = requests.get(PS_URL, timeout=15)
        resp.raise_for_status()

        # Each line looks like:
        #   \tVenusaur: {tier: "UU", doublesTier: "DOU", natDexTier: "UU"},
        pattern = re.compile(
            r'^\t(\w+):\s*\{[^\n}]*\btier:\s*"([^"]+)"',
            re.MULTILINE,
        )

        tiers = {}
        for m in pattern.finditer(resp.text):
            ps_name, tier = m.group(1), m.group(2)
            # Strip parenthetical markers like "(OU)" → "OU"
            if tier.startswith("(") and tier.endswith(")"):
                tier = tier[1:-1]
            if tier in KNOWN_TIERS:
                tiers[_normalize(ps_name)] = tier

        with open(CACHE_FILE, "w") as f:
            json.dump(tiers, f)

        with _lock:
            _tiers = tiers

    except Exception:
        pass  # Tier data is optional — don't crash if unavailable

    _ready = True
    if on_ready:
        on_ready()
