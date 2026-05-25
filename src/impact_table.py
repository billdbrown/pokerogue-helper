"""Impact Score browser — Pokémon, Moves, and Abilities tabs."""

import json
import os
import statistics

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QWidget, QCheckBox, QSlider,
    QTabWidget, QComboBox, QFrame, QCompleter, QPushButton,
)
from PyQt6.QtCore import Qt, QUrl, pyqtSlot, QMetaObject, Q_ARG, QStringListModel
from PyQt6.QtGui import QColor, QDesktopServices

import impact_db
import move_info_db
import ability_info_db
from scoring import OFFENSIVE_CHART

# Final-evolution starters across all generations (including Hisuian forms)
_STARTER_POKEMON: frozenset[str] = frozenset({
    # Gen 1
    "venusaur", "charizard", "blastoise",
    # Gen 2
    "meganium", "typhlosion", "feraligatr",
    # Gen 3
    "sceptile", "blaziken", "swampert",
    # Gen 4
    "torterra", "infernape", "empoleon",
    # Gen 5
    "serperior", "emboar", "samurott",
    # Gen 6
    "chesnaught", "delphox", "greninja",
    # Gen 7
    "decidueye", "incineroar", "primarina",
    # Gen 8
    "rillaboom", "cinderace", "inteleon",
    # Gen 9
    "meowscarada", "skeledirge", "quaquaval",
    # Hisuian forms
    "typhlosion-hisui", "samurott-hisui", "decidueye-hisui",
})

TYPE_COLORS = {
    "normal":   ("#A8A878", "#000"), "fire":     ("#F08030", "#fff"),
    "water":    ("#6890F0", "#fff"), "electric": ("#F8D030", "#000"),
    "grass":    ("#78C850", "#000"), "ice":      ("#98D8D8", "#000"),
    "fighting": ("#C03028", "#fff"), "poison":   ("#A040A0", "#fff"),
    "ground":   ("#E0C068", "#000"), "flying":   ("#A890F0", "#000"),
    "psychic":  ("#F85888", "#fff"), "bug":      ("#A8B820", "#000"),
    "rock":     ("#B8A038", "#fff"), "ghost":    ("#705898", "#fff"),
    "dragon":   ("#7038F8", "#fff"), "dark":     ("#705848", "#fff"),
    "steel":    ("#B8B8D0", "#000"), "fairy":    ("#EE99AC", "#000"),
}

_STYLE = """
    QDialog   { background: #1e1e2e; color: #cdd6f4; }
    QTabWidget::pane { border: none; background: #1e1e2e; }
    QTabBar::tab {
        background: #181825; color: #6c7086; padding: 6px 20px;
        border: none; border-bottom: 2px solid transparent;
        font-size: 12px; margin-right: 2px; min-width: 80px;
    }
    QTabBar::tab:selected { color: #cdd6f4; border-bottom-color: #89b4fa; background: #1e1e2e; }
    QTabBar::tab:hover    { color: #bac2de; }
    QComboBox {
        background: #313244; color: #cdd6f4; border: 1px solid #45475a;
        border-radius: 4px; padding: 3px 8px; font-size: 12px; min-width: 110px;
    }
    QComboBox::drop-down  { border: none; width: 20px; }
    QComboBox QAbstractItemView {
        background: #313244; color: #cdd6f4; border: 1px solid #45475a;
        selection-background-color: #45475a; selection-color: #cdd6f4;
    }
    QLineEdit {
        background: #313244; color: #cdd6f4; border: 1px solid #45475a;
        border-radius: 4px; padding: 4px 8px; font-size: 12px;
    }
    QLineEdit:focus { border-color: #89b4fa; }
    QTableWidget {
        background: #181825; color: #cdd6f4; gridline-color: #2a2a3d;
        border: none; font-size: 12px; outline: none;
    }
    QTableWidget::item          { padding: 2px 6px; }
    QTableWidget::item:selected { background: #313244; color: #cdd6f4; }
    QHeaderView::section {
        background: #1e1e2e; color: #6c7086; border: none;
        border-bottom: 1px solid #45475a; border-right: 1px solid #2a2a3d;
        padding: 4px 8px; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;
    }
    QHeaderView::section:hover { color: #cdd6f4; }
    QScrollBar:vertical { background: #181825; width: 5px; border-radius: 2px; margin: 0; }
    QScrollBar::handle:vertical { background: #45475a; border-radius: 2px; min-height: 20px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QCheckBox { color: #cdd6f4; font-size: 12px; spacing: 6px; }
    QCheckBox::indicator {
        width: 13px; height: 13px; border: 1px solid #45475a;
        border-radius: 3px; background: #313244;
    }
    QCheckBox::indicator:checked { background: #89b4fa; border-color: #89b4fa; }
"""

_BULBAPEDIA_OVERRIDES: dict[str, str] = {
    "mr-mime":   "Mr._Mime",
    "mr-rime":   "Mr._Rime",
    "mime-jr":   "Mime_Jr.",
    "ho-oh":     "Ho-Oh",
    "porygon-z": "Porygon-Z",
    "type-null": "Type:_Null",
    "jangmo-o":  "Jangmo-o",
    "hakamo-o":  "Hakamo-o",
    "kommo-o":   "Kommo-o",
    "nidoran-f": "Nidoran♀",
    "nidoran-m": "Nidoran♂",
    "flabebe":   "Flabébé",
    "farfetchd": "Farfetch%27d",
}


def _bulbapedia_url(name: str, display: str) -> str:
    for prefix, slug in _BULBAPEDIA_OVERRIDES.items():
        if name == prefix or name.startswith(prefix + "-"):
            return f"https://bulbapedia.bulbagarden.net/wiki/{slug}_(Pok%C3%A9mon)"
    slug = display.replace(" ", "_")
    return f"https://bulbapedia.bulbagarden.net/wiki/{slug}_(Pok%C3%A9mon)"


_OUTCOME_RANK: dict[str, int] = {"ZDW": 0, "DW": 1, "DL": 2, "ZDL": 3, "Draw": 4}


def _type_tag_html(t: str) -> str:
    bg, fg = TYPE_COLORS.get(t, ("#888", "#fff"))
    return (f'<span style="background:{bg}; color:{fg}; border-radius:2px; '
            f'padding:1px 3px; font-size:10px; font-weight:bold;">{t[:4].upper()}</span>')


def _types_html(types: list[str]) -> str:
    return " ".join(_type_tag_html(t) for t in types)


def _poke_html(name: str) -> str:
    return f'<span style="color:#89b4fa; font-weight:bold;">{name.replace("-", " ").title()}</span>'


def _move_html(move_name: str, move_type: str) -> str:
    tag  = _type_tag_html(move_type) + " " if move_type else ""
    text = f'<span style="color:#cdd6f4;">{move_name.replace("-", " ").title()}</span>'
    return tag + text


def _matchup_narrative(row: dict, a_slug: str) -> str:
    """Generate a natural-language HTML description of one matchup row."""
    a_types = row.get("a_types", [])
    b_types = row.get("opponent_types", [])
    move_a  = row.get("move_used",    "—")
    move_b  = row.get("move_against", "—")
    mat     = row.get("move_a_type",  "")
    mbt     = row.get("move_b_type",  "")
    a_final = row.get("a_final", 0.0)
    b_final = row.get("b_final", 0.0)
    pohko_a = row.get("pohko_a", 0.0)
    pohko_b = row.get("pohko_b", 0.0)
    a_speed = row.get("a_speed", 0)
    b_speed = row.get("b_speed", 0)
    outcome = row.get("outcome", "")

    atags = _types_html(a_types)
    btags = _types_html(b_types)
    apoke = _poke_html(a_slug)
    bpoke = _poke_html(row["opponent"])
    amove = _move_html(move_a, mat) if move_a != "—" else ""
    bmove = _move_html(move_b, mbt) if move_b != "—" else ""

    if outcome == "ZDW":
        ko = "OHKOs" if pohko_a >= 1.0 else "cleanly KOs"
        if amove:
            return f"{atags} {apoke} uses {amove} to {ko} {btags} {bpoke}."
        return f"{atags} {apoke} {ko} {btags} {bpoke}."

    if outcome == "DW":
        dmg    = round((1 - a_final) * 100)
        timing = "first" if a_speed < b_speed else "in the process"
        base   = f"{atags} {apoke}"
        base  += f" uses {amove} to KO {btags} {bpoke}" if amove else f" KOs {btags} {bpoke}"
        tail   = f", but takes {dmg}% damage from {bpoke}'s {bmove} {timing}." if bmove \
                 else f", but takes {dmg}% damage {timing}."
        return base + tail

    if outcome == "ZDL":
        ko = "OHKO'd" if pohko_b >= 1.0 else "KO'd without dealing damage"
        if bmove:
            return f"{atags} {apoke} is {ko} by {btags} {bpoke}, who uses {bmove}."
        return f"{atags} {apoke} is {ko} by {btags} {bpoke}."

    if outcome == "DL":
        dmg  = round((1 - b_final) * 100)
        base = f"{atags} {apoke} uses {amove} to deal {dmg}% damage to {btags} {bpoke}" if amove \
               else f"{atags} {apoke} deals {dmg}% damage to {btags} {bpoke}"
        tail = f", but is KO'd by {bpoke}'s {bmove}." if bmove \
               else f", but is KO'd by {btags} {bpoke}."
        return base + tail

    return f"{atags} {apoke} vs {btags} {bpoke}: {outcome}"


# col index → (row key, ascending-by-default)
_SORTABLE = {
    0: ("rank",       True),
    3: ("display",    True),
    4: ("combined",   False),
    5: ("off_impact", False),
    6: ("def_impact", False),
    7: ("spd_impact", False),
}

_ADVERSE_COLORS = {
    "None":          "#a6e3a1",
    "Self-damaging": "#f38ba8",
    "Self-reducing": "#f9e2af",
}

_FEASIBILITY_COLORS = {
    "Easy":    "#a6e3a1",
    "Medium":  "#f9e2af",
    "Hard":    "#fab387",
    "Complex": "#f38ba8",
    "—":       "#6c7086",
}

# All abilities explicitly categorized. status: "modeled" | "deferred" | "ignored".
_ABILITY_SIM_INFO: dict[str, dict] = {
    # ── Modeled: Offensive ────────────────────────────────────────────────────
    "transistor":    {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Electric moves deal ×1.5 damage."},
    "dragons-maw":   {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Dragon moves deal ×1.5 damage."},
    "rocky-payload": {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Rock moves deal ×1.5 damage."},
    "water-bubble":  {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Water moves ×2.0 (off); incoming Fire ×0.5 (def)."},
    "sheer-force":   {"status": "modeled", "category": "Offensive",
                      "sim_desc": "All moves ×1.3 (approx — only moves with secondary effects qualify; slight overestimate)."},
    "hustle":        {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Physical moves ×1.2 (net of ×1.5 Atk and ×0.8 accuracy; SpA unaffected)."},
    "huge-power":    {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Physical moves deal ×2.0 damage (effective Attack stat doubled)."},
    "pure-power":    {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Physical moves deal ×2.0 damage (effective Attack stat doubled)."},
    "technician":    {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Moves with base power ≤60 deal ×1.5 damage (checks original power, before multi-hit/charge adjustments)."},
    "iron-fist":     {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Punching moves deal ×1.2 damage (18 moves: Fire/Ice/Thunder Punch, Bullet/Mach/Drain Punch, etc.)."},
    "adaptability":  {"status": "modeled", "category": "Offensive",
                      "sim_desc": "STAB multiplier raised from ×1.5 to ×2.0."},
    "rock-head":     {"status": "modeled", "category": "Defensive",
                      "sim_desc": "Recoil moves included in clean-mode scoring (no HP lost from recoil in sim)."},
    "magic-guard":   {"status": "modeled", "category": "Defensive",
                      "sim_desc": "Recoil moves included in clean-mode scoring (no recoil damage taken)."},
    "truant":         {"status": "modeled", "category": "Offensive",
                       "sim_desc": "All moves ×0.5 (skip every other turn = half effective output)."},
    "slow-start":     {"status": "modeled", "category": "Offensive",
                       "sim_desc": "Physical moves ×0.5 (Atk halved for first 5 turns; Speed penalty not captured)."},
    "defeatist":      {"status": "modeled", "category": "Offensive",
                       "sim_desc": "All moves ×0.75 (Atk and SpAtk halved below 50% HP; expected value under uniform HP distribution)."},
    "fairy-aura":     {"status": "modeled", "category": "Offensive",
                       "sim_desc": "Fairy moves deal ×1.33 damage (×4/3 boost applies to user's own moves)."},
    "dark-aura":      {"status": "modeled", "category": "Offensive",
                       "sim_desc": "Dark moves deal ×1.33 damage (×4/3 boost applies to user's own moves)."},
    "parental-bond":  {"status": "modeled", "category": "Offensive",
                       "sim_desc": "All moves deal ×1.25 damage (second hit at ¼ power: 1.0 + 0.25 = ×1.25 total)."},
    "steelworker":    {"status": "modeled", "category": "Offensive",
                       "sim_desc": "Steel moves deal ×1.5 damage."},
    "neuroforce":       {"status": "modeled", "category": "Offensive",
                         "sim_desc": "Super-effective moves deal an extra ×1.25 damage."},
    "beads-of-ruin":    {"status": "modeled", "category": "Offensive",
                         "sim_desc": "All opponents' SpDef −25% (Chi-Yu); modeled as special moves ×1.33 (÷0.75 defense)."},
    "sword-of-ruin":    {"status": "modeled", "category": "Offensive",
                         "sim_desc": "All opponents' Def −25% (Chien-Pao); modeled as physical moves ×1.33 (÷0.75 defense)."},
    "electromorphosis": {"status": "modeled", "category": "Offensive",
                         "sim_desc": "Electric moves deal ×2 when moving second (Pawmot); speed-checked per matchup."},
    "minds-eye":        {"status": "modeled", "category": "Offensive",
                         "sim_desc": "Normal and Fighting moves hit Ghost types at ×1 (neutral) — identical to Scrappy."},
    "stall":          {"status": "modeled", "category": "Tactical",
                       "sim_desc": "Always goes last in battle sim (speed treated as 0 for turn-order purposes)."},
    "victory-star":   {"status": "modeled", "category": "Offensive",
                       "sim_desc": "Accuracy of sub-100% moves multiplied by ×1.1."},
    "intrepid-sword": {"status": "modeled", "category": "Offensive",
                       "sim_desc": "Physical moves deal ×1.5 damage (Atk +1 on entry)."},
    "normalize":      {"status": "modeled", "category": "Type Remap",
                       "sim_desc": "All moves become Normal-type with ×1.2 power boost; STAB re-evaluated on Normal."},
    "download":      {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Atk or SpA +1 (×1.5) per matchup: Atk if target's Def < SpDef, SpA otherwise."},
    "libero":        {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Every move gets STAB — identical to Protean."},
    "strong-jaw":    {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Biting moves deal ×1.5 damage (Bite, Crunch, Fire/Ice/Thunder/Poison Fang, Bug Bite, Fishious Rend, Hyper Fang, Jaw Lock, Psychic Fangs)."},
    "sharpness":     {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Slicing moves deal ×1.5 damage (Leaf Blade, Night Slash, Razor Shell, Sacred Sword, Air Slash, etc.)."},
    "mega-launcher": {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Aura and pulse moves deal ×1.5 damage (Aura Sphere, Dark Pulse, Dragon Pulse, Origin Pulse, Water Pulse, etc.)."},
    "punk-rock":     {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Sound moves deal ×1.3 damage (off); incoming sound moves deal ×0.5 damage (def)."},
    "reckless":      {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Recoil moves deal ×1.2 damage (Brave Bird, Double-Edge, Flare Blitz, Head Smash, Take Down, Volt Tackle, Wild Charge, Wood Hammer, etc.)."},
    "compound-eyes": {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Accuracy of moves with sub-100% accuracy multiplied by ×1.3 (capped at 100%)."},
    "scrappy":       {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Normal and Fighting moves hit Ghost types at ×1 (neutral) instead of immune."},
    "tinted-lens":   {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Not-very-effective moves deal ×2 damage in battle sim (0.5× → 1×, 0.25× → 0.5×)."},
    "no-guard":      {"status": "modeled", "category": "Offensive",
                      "sim_desc": "All moves hit (accuracy = 100%). Defensive side (opponent also never misses) not yet modeled."},
    "analytic":      {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Moves deal ×1.3 damage when this Pokémon is slower than the target (per-matchup check)."},
    "protean":       {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Every move gets STAB — type changes to match the move before attacking."},
    "skill-link":    {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Multi-hit moves always hit maximum times (e.g. Rock Blast: 5× instead of avg 3.5×)."},
    "tough-claws":   {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Physical moves deal ×1.3 damage (approx — non-contact physical moves like Earthquake included; slight overestimate)."},
    "magic-guard":   {"status": "modeled", "category": "Offensive",
                      "sim_desc": "Recoil moves included in clean-mode scoring (no recoil damage taken)."},
    "bulletproof":   {"status": "modeled", "category": "Immunity",
                      "sim_desc": "Immune to ball and bomb moves (Shadow Ball, Focus Blast, Aura Sphere, Energy Ball, etc.)."},
    "storm-drain":   {"status": "modeled", "category": "Immunity",
                      "sim_desc": "Immune to Water-type moves (SpA boost on activation not modeled)."},
    "motor-drive":   {"status": "modeled", "category": "Immunity",
                      "sim_desc": "Immune to Electric-type moves (Speed boost not modeled)."},
    "earth-eater":   {"status": "modeled", "category": "Immunity",
                      "sim_desc": "Immune to Ground-type moves (HP recovery not modeled)."},
    "wind-rider":    {"status": "modeled", "category": "Immunity",
                      "sim_desc": "Immune to wind-based moves (Gust, Hurricane, Icy Wind, Bleakwind Storm, etc.)."},
    # ── Modeled: Type Remap ──────────────────────────────────────────────────
    "aerilate":      {"status": "modeled", "category": "Type Remap",
                      "sim_desc": "Normal moves → Flying-type ×1.3 power; STAB re-evaluated."},
    "pixilate":      {"status": "modeled", "category": "Type Remap",
                      "sim_desc": "Normal moves → Fairy-type ×1.3 power; STAB re-evaluated."},
    "refrigerate":   {"status": "modeled", "category": "Type Remap",
                      "sim_desc": "Normal moves → Ice-type ×1.3 power; STAB re-evaluated."},
    "galvanize":     {"status": "modeled", "category": "Type Remap",
                      "sim_desc": "Normal moves → Electric-type ×1.2 power; STAB re-evaluated."},
    "liquid-voice":  {"status": "modeled", "category": "Type Remap",
                      "sim_desc": "Sound-based moves become Water-type; Water STAB applies if Pokémon is Water-type."},
    # ── Modeled: Defensive ───────────────────────────────────────────────────
    "thick-fat":      {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming Fire and Ice moves ×0.5."},
    "fur-coat":       {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming physical moves ×0.5."},
    "ice-scales":     {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming special moves ×0.5."},
    "heatproof":      {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming Fire moves ×0.5."},
    "multiscale":     {"status": "modeled", "category": "Defensive",
                       "sim_desc": "All incoming moves ×0.5 (assumes full HP)."},
    "shadow-shield":  {"status": "modeled", "category": "Defensive",
                       "sim_desc": "All incoming moves ×0.5 (assumes full HP)."},
    "purifying-salt": {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming Ghost moves ×0.5."},
    "intimidate":     {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming physical moves ×0.667 (−1 Atk stage on switch-in)."},
    "dauntless-shield": {"status": "modeled", "category": "Defensive",
                         "sim_desc": "Incoming physical moves ×0.667 (Def +1 stage on entry = ×1.5 effective defense)."},
    "fluffy":         {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming physical moves ×0.5; incoming Fire moves ×2.0 (fire+physical naturally cancels to ×1.0; approx: non-contact physical also halved)."},
    "vessel-of-ruin": {"status": "modeled", "category": "Defensive",
                       "sim_desc": "All opponents' SpAtk −25% (Wo-Chien); modeled as incoming special moves ×0.75."},
    "tablets-of-ruin":{"status": "modeled", "category": "Defensive",
                       "sim_desc": "All opponents' Atk −25% (Ting-Lu); modeled as incoming physical moves ×0.75."},
    "wonder-guard":   {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Only super-effective moves deal damage (Shedinja); non-SE moves deal 0 in battle and bulk scoring."},
    "filter":         {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming super-effective moves deal ×0.75 damage."},
    "solid-rock":     {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming super-effective moves deal ×0.75 damage."},
    "prism-armor":    {"status": "modeled", "category": "Defensive",
                       "sim_desc": "Incoming super-effective moves deal ×0.75 damage."},
    "dry-skin":       {"status": "modeled", "category": "Immunity",
                       "sim_desc": "Immune to Water moves; incoming Fire moves deal ×1.25 damage."},
    # ── Modeled: Immunity ────────────────────────────────────────────────────
    "well-baked-body": {"status": "modeled", "category": "Immunity",
                        "sim_desc": "Immune to Fire-type moves."},
    "levitate":        {"status": "modeled", "category": "Immunity",
                        "sim_desc": "Immune to Ground-type moves."},
    "lightning-rod":   {"status": "modeled", "category": "Immunity",
                        "sim_desc": "Immune to Electric-type moves (SpA boost not modeled)."},
    "water-absorb":    {"status": "modeled", "category": "Immunity",
                        "sim_desc": "Immune to Water-type moves (HP recovery not modeled)."},
    "volt-absorb":     {"status": "modeled", "category": "Immunity",
                        "sim_desc": "Immune to Electric-type moves (HP recovery not modeled)."},
    "flash-fire":      {"status": "modeled", "category": "Immunity",
                        "sim_desc": "Immune to Fire-type moves (Fire-boost on activation not modeled)."},
    "sap-sipper":      {"status": "modeled", "category": "Immunity",
                        "sim_desc": "Immune to Grass-type moves (Atk boost on activation not modeled)."},
    "soundproof":      {"status": "modeled", "category": "Immunity",
                        "sim_desc": "Immune to sound-based moves (Boomburst, Hyper Voice, Bug Buzz, Clanging Scales, etc.)."},
    # ── Deferred: HP-conditional ─────────────────────────────────────────────
    "overgrow": {"status": "deferred", "category": "HP Conditional",
                 "sim_desc": "Grass moves ×1.5 when HP < 1/3 — no HP tracking in sim."},
    "blaze":    {"status": "deferred", "category": "HP Conditional",
                 "sim_desc": "Fire moves ×1.5 when HP < 1/3 — no HP tracking in sim."},
    "torrent":  {"status": "deferred", "category": "HP Conditional",
                 "sim_desc": "Water moves ×1.5 when HP < 1/3 — no HP tracking in sim."},
    "swarm":    {"status": "deferred", "category": "HP Conditional",
                 "sim_desc": "Bug moves ×1.5 when HP < 1/3 — no HP tracking in sim."},
    # ── Deferred: Sim architecture ───────────────────────────────────────────
    "sturdy": {"status": "deferred", "category": "Defensive",
               "sim_desc": "Survives any OHKO at full HP — requires multi-hit sim rework."},
    # ── Modeled: Weather / Terrain setters ───────────────────────────────────
    "drizzle":        {"status": "modeled", "category": "Weather",
                       "sim_desc": "Rain: Water ×1.5, Fire ×0.5 for both sides (Pelipper, Politoed, Kyogre)."},
    "primordial-sea": {"status": "modeled", "category": "Weather",
                       "sim_desc": "Extreme Rain: Water ×1.5 both sides; Fire moves fail (Primal Kyogre)."},
    "drought":        {"status": "modeled", "category": "Weather",
                       "sim_desc": "Sun: Fire ×1.5, Water ×0.5 for both sides (Ninetales, Torkoal, Exeggutor-Alola, Groudon)."},
    "desolate-land":  {"status": "modeled", "category": "Weather",
                       "sim_desc": "Extreme Sun: Fire ×1.5 both sides; Water moves fail (Primal Groudon)."},
    "grassy-surge":   {"status": "modeled", "category": "Weather",
                       "sim_desc": "Grassy Terrain: Grass ×1.3 both sides; incoming Ground ×0.5 (Rillaboom, Tapu Bulu)."},
    "electric-surge": {"status": "modeled", "category": "Weather",
                       "sim_desc": "Electric Terrain: Electric ×1.3 both sides (Tapu Koko, Pincurchin)."},
    "psychic-surge":  {"status": "modeled", "category": "Weather",
                       "sim_desc": "Psychic Terrain: Psychic ×1.3 both sides (Tapu Lele)."},
    "misty-surge":    {"status": "modeled", "category": "Weather",
                       "sim_desc": "Misty Terrain: Incoming Dragon moves ×0.5 for all grounded Pokémon (Tapu Fini)."},
    # ── Deferred: Scaling ────────────────────────────────────────────────────
    "supreme-overlord": {"status": "deferred", "category": "Scaling",
                         "sim_desc": "Atk/SpAtk +10% per fainted ally (up to +50%) — 1v1 sim never fires."},
    "moxie":       {"status": "deferred", "category": "Scaling",
                    "sim_desc": "Atk +1 after each KO — 1v1 sim never fires this; multi-battle value not captured."},
    "innards-out": {"status": "deferred", "category": "Scaling",
                    "sim_desc": "Deals remaining HP as damage when KO'd — requires KO tracking."},
    # ── Deferred: Weather ────────────────────────────────────────────────────
    "swift-swim":  {"status": "deferred", "category": "Weather",
                    "sim_desc": "Speed ×2 in rain — no weather mechanic."},
    "chlorophyll": {"status": "deferred", "category": "Weather",
                    "sim_desc": "Speed ×2 in sun — no weather mechanic."},
    "sand-veil":   {"status": "deferred", "category": "Weather",
                    "sim_desc": "Evasion +20% in sandstorm — no weather mechanic."},
    "sand-force":  {"status": "deferred", "category": "Weather",
                    "sim_desc": "Rock/Ground/Steel moves ×1.3 in sandstorm — no weather mechanic."},
    "snow-cloak":  {"status": "deferred", "category": "Weather",
                    "sim_desc": "Evasion +20% in hail/snow — no weather mechanic."},
    "rain-dish":   {"status": "deferred", "category": "Weather",
                    "sim_desc": "Restores HP in rain — no weather mechanic."},
    # ── Deferred: Status ─────────────────────────────────────────────────────
    "static":    {"status": "deferred", "category": "Status",
                  "sim_desc": "30% paralysis on contact — no status mechanic."},
    "own-tempo": {"status": "deferred", "category": "Status",
                  "sim_desc": "Prevents confusion — no status mechanic."},
    # ── Deferred: Flinching ──────────────────────────────────────────────────
    "inner-focus": {"status": "deferred", "category": "Flinching",
                    "sim_desc": "Prevents flinching — no flinch mechanic."},
    # ── Deferred: Critical Hits ──────────────────────────────────────────────
    "shell-armor": {"status": "deferred", "category": "Critical Hit",
                    "sim_desc": "Prevents critical hits — no crit mechanic."},
    "sniper":      {"status": "deferred", "category": "Critical Hit",
                    "sim_desc": "Boosts critical hit damage ×1.5 — no crit mechanic."},
    # ── Ignored: No battle-math effect ───────────────────────────────────────
    "keen-eye":    {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Prevents accuracy reduction — no accuracy-lowering moves in pool."},
    "frisk":       {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Reveals held item — items not modeled."},
    "pressure":    {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Doubles PP usage — PP not tracked in sim."},
    "run-away":    {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Allows fleeing wild Pokémon — irrelevant to scoring."},
    "gluttony":    {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Uses berries at higher HP — items not modeled."},
    "pickup":      {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Picks up used items — items not modeled."},
    "unnerve":     {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Prevents opponent eating berries — items not modeled."},
    "telepathy":   {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Prevents ally move damage — doubles mechanic, irrelevant in 1v1."},
    "mold-breaker":{"status": "ignored", "category": "No Effect",
                    "sim_desc": "Ignores target abilities — too complex; would suppress modeled defensive abilities."},
    "regenerator": {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Restores 1/3 HP on switch-out — no switch mechanic in sim."},
    "infiltrator": {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Bypasses substitutes and screens — not modeled."},
    "weak-armor":  {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Speed +2 / Def −1 when hit physically — volatile mid-battle stat change."},
    "rattled":     {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Speed +1 when hit by Bug/Ghost/Dark — speed boosts not tracked in sim."},
    "damp":        {"status": "ignored", "category": "No Effect",
                    "sim_desc": "Prevents Explosion/Self-Destruct — those moves are already excluded from the pool."},
    "prankster":      {"status": "ignored", "category": "Tactical",
                       "sim_desc": "Gives priority to status moves — status moves not scored."},
    "clear-body":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents stat reduction — no stat modifier tracking."},
    "natural-cure":   {"status": "deferred", "category": "Status",
                       "sim_desc": "Cures status on switch-out — no switch mechanic."},
    "synchronize":    {"status": "deferred", "category": "Status",
                       "sim_desc": "Mirrors status condition to opponent — no status mechanic."},
    "rivalry":        {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Atk/SpA ±25% based on gender match — no gender tracking."},
    "anticipation":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Alerts to super-effective or OHKO moves — no battle math effect."},
    "unburden":       {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Doubles Speed when item is consumed — items not modeled."},
    "early-bird":     {"status": "deferred", "category": "Status",
                       "sim_desc": "Halves sleep duration — no status mechanic."},
    "vital-spirit":   {"status": "deferred", "category": "Status",
                       "sim_desc": "Prevents sleep — no status mechanic."},
    "steadfast":      {"status": "deferred", "category": "Flinching",
                       "sim_desc": "Speed +1 on flinch — no flinch mechanic."},
    "pickpocket":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Steals held item on contact — items not modeled."},
    "defiant":        {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Atk +2 when stats lowered — no stat modifier tracking."},
    "serene-grace":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Doubles secondary effect chance — secondary effects not scored."},
    "cute-charm":     {"status": "deferred", "category": "Status",
                       "sim_desc": "30% infatuation on contact — no status mechanic."},
    "unaware":        {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Ignores target's stat boosts — no stat modifier tracking."},
    "cursed-body":    {"status": "deferred", "category": "Status",
                       "sim_desc": "30% chance to disable attacker's move — no status mechanic."},
    "big-pecks":      {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents Defense reduction — no stat modifier tracking."},
    "competitive":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "SpA +2 when stats lowered — no stat modifier tracking."},
    "speed-boost":    {"status": "deferred", "category": "Scaling",
                       "sim_desc": "Speed +1 each turn — scaling, not captured in 1v1 sim."},
    "limber":         {"status": "deferred", "category": "Status",
                       "sim_desc": "Prevents paralysis — no status mechanic."},
    "shields-down":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Minior form-change mechanic — not modeled."},
    "water-veil":     {"status": "deferred", "category": "Status",
                       "sim_desc": "Prevents burn — no status mechanic."},
    "beast-boost":    {"status": "deferred", "category": "Scaling",
                       "sim_desc": "Highest stat +1 after KO — 1v1 sim never fires this; multi-battle value not captured."},
    "moody":          {"status": "deferred", "category": "Scaling",
                       "sim_desc": "Random stat ×2/−1 each turn — scaling, not captured in 1v1 sim."},
    "effect-spore":   {"status": "deferred", "category": "Status",
                       "sim_desc": "30% chance to inflict sleep/paralysis/poison on contact."},
    "quick-feet":     {"status": "deferred", "category": "Status",
                       "sim_desc": "Speed ×1.5 when statused — no status mechanic."},
    "poison-touch":   {"status": "deferred", "category": "Status",
                       "sim_desc": "30% chance to poison on contact — no status mechanic."},
    "tangled-feet":   {"status": "deferred", "category": "Status",
                       "sim_desc": "Evasion ×2 when confused — no status mechanic."},
    "stench":         {"status": "deferred", "category": "Flinching",
                       "sim_desc": "10% flinch chance on contact — no flinch mechanic."},
    "anger-point":    {"status": "deferred", "category": "Critical Hit",
                       "sim_desc": "Atk maxes out when hit by a crit — no crit mechanic."},
    "battle-armor":   {"status": "deferred", "category": "Critical Hit",
                       "sim_desc": "Prevents critical hits — no crit mechanic."},
    "super-luck":     {"status": "deferred", "category": "Critical Hit",
                       "sim_desc": "Raises critical hit rate — no crit mechanic."},
    "solar-power":    {"status": "deferred", "category": "Weather",
                       "sim_desc": "SpA ×1.5 in harsh sun — no weather mechanic."},
    "sand-rush":      {"status": "deferred", "category": "Weather",
                       "sim_desc": "Speed ×2 in sandstorm — no weather mechanic."},
    "snow-warning":   {"status": "deferred", "category": "Weather",
                       "sim_desc": "Summons hail/snow on entry — no weather mechanic."},
    "protosynthesis": {"status": "deferred", "category": "Weather",
                       "sim_desc": "Boosts highest stat in harsh sun — no weather mechanic."},
    "quark-drive":    {"status": "deferred", "category": "Weather",
                       "sim_desc": "Boosts highest stat on Electric Terrain — no terrain mechanic."},
    "shield-dust":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents secondary effect damage — secondary effects not scored."},
    "hyper-cutter":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents Attack reduction — no stat modifier tracking."},
    "klutz":          {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Can't use held items — items not modeled."},
    "healer":         {"status": "ignored", "category": "No Effect",
                       "sim_desc": "30% chance to cure ally's status — team fights only."},
    "aftermath":      {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Damages attacker when KO'd by contact — complex mechanic."},
    "contrary":       {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Reverses stat changes — no stat tracking; too complex."},
    "magic-bounce":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Reflects status moves — status moves not scored."},
    "sweet-veil":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents ally sleep — team fights only."},
    "plus":           {"status": "ignored", "category": "No Effect",
                       "sim_desc": "SpA ×1.5 with Minus partner — doubles mechanic."},
    "sticky-hold":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Can't lose held item — items not modeled."},
    "friend-guard":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Reduces damage to allies — team fights only."},
    "heavy-metal":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Doubles weight — weight-based moves not relevant."},
    "justified":      {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Atk +1 when hit by Dark — no stat modifier tracking."},
    "magnet-pull":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Traps Steel-type Pokémon — no trapping mechanic."},
    "harvest":        {"status": "ignored", "category": "No Effect",
                       "sim_desc": "May restore consumed berry — items not modeled."},
    "stakeout":       {"status": "ignored", "category": "No Effect",
                       "sim_desc": "×2 damage vs switched-in target — no switch mechanic."},
    "illuminate":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "No battle math effect in Gen 8+."},
    "minus":          {"status": "ignored", "category": "No Effect",
                       "sim_desc": "SpA ×1.5 with Plus partner — doubles mechanic."},
    "aroma-veil":       {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Protects from mental-targeting moves — team fights only."},
    "turboblaze":       {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Ignores target abilities for Fire moves — too complex; would suppress modeled defensive abilities."},
    "teravolt":         {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Ignores target abilities for Electric moves — too complex."},
    "aura-break":       {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Reverses Fairy/Dark Aura — per-matchup ability interaction."},
    "water-compaction": {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Def +2 when hit by Water — stat modifier."},
    "merciless":        {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Moves are critical against poisoned targets — status + crit."},
    "soul-heart":       {"status": "ignored", "category": "No Effect",
                         "sim_desc": "SpAtk +1 when any Pokémon faints — scaling."},
    "power-of-alchemy": {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Copies fallen ally's ability — team fights only."},
    "cotton-down":      {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Lowers attacker's Speed — stat modifier."},
    "propeller-tail":   {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Ignores redirection effects — no redirection mechanic."},
    "screen-cleaner":   {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Removes screens on entry — no screens mechanic."},
    "guard-dog":        {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Atk +1 when Intimidated, or ignores Intimidate — stat modifier."},
    "toxic-debris":     {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Lays Toxic Spikes when hit by physical move — no field mechanic."},
    "mycelium-might":   {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Status moves go last and bypass abilities — status not scored."},
    "supersweet-syrup": {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Lowers opponent's evasion on first entry — stat modifier."},
    "hospitality":      {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Restores ally's HP on entry — team fights only."},
    "color-change":     {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Changes type to the type of the last move that hit it — too complex."},
    "perish-body":      {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Sets Perish Song when hit by contact — too complex."},
    "curious-medicine": {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Resets ally's stat changes — team fights only."},
    "chilling-neigh":   {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Atk +1 after KO — scaling, 1v1 sim never fires."},
    "grim-neigh":       {"status": "ignored", "category": "No Effect",
                         "sim_desc": "SpAtk +1 after KO — scaling, 1v1 sim never fires."},
    "as-one":           {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Chilling/Grim Neigh + Unnerve combined — scaling."},
    "good-as-gold":     {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Immune to status moves — status moves not scored."},
    "costar":           {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Copies ally's stat boosts — team fights only."},
    "gorilla-tactics":  {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Atk ×1.5 but locked to one move — penalty not modeled, not implemented."},
    "triage":           {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Priority on healing moves — healing moves not scored."},
    "queenly-majesty":  {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Prevents priority moves — priority not tracked in sim."},
    "dazzling":         {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Prevents priority moves — priority not tracked in sim."},
    "battery":          {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Raises ally's SpAtk — team fights only."},
    "receiver":         {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Copies fallen ally's ability — team fights only."},
    "full-metal-body":  {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Prevents stat reduction — no stat modifier tracking."},
    "ball-fetch":       {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Retrieves Poké Ball after failed catch — irrelevant."},
    "power-spot":       {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Boosts adjacent ally's moves — team fights only."},
    "steely-spirit":    {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Raises ally's Steel moves — team fights only."},
    "illusion":       {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Disguises as last Pokémon in party — no battle math effect."},
    "stalwart":       {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Ignores redirection effects — no redirection mechanic."},
    "steam-engine":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Speed +6 when hit by Fire or Water — stat modifier."},
    "cud-chew":       {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Repeats berry effect at end of next turn — items not modeled."},
    "long-reach":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Contact moves don't make contact — no contact effects modeled."},
    "gulp-missile":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Cramorant's Surf/Dive triggers a counterattack — niche mechanic."},
    "neutralizing-gas": {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Suppresses all Pokémon's abilities — would require per-matchup ability nullification."},
    "commander":      {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Dondozo/Tatsugiri combo — doubles mechanic only."},
    "liquid-ooze":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Drain moves damage the user instead of healing — niche interaction."},
    "gale-wings":     {"status": "ignored", "category": "Tactical",
                       "sim_desc": "Flying moves gain priority at full HP — no priority mechanic."},
    "stamina":        {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Def +1 when hit — stat modifier."},
    "berserk":        {"status": "deferred", "category": "HP Conditional",
                       "sim_desc": "SpA +1 when HP drops below 50% — HP-conditional stat modifier."},
    "hadron-engine":  {"status": "deferred", "category": "Weather",
                       "sim_desc": "Boosts highest stat on Electric Terrain — no terrain mechanic."},
    "forecast":       {"status": "deferred", "category": "Weather",
                       "sim_desc": "Castform changes type with weather — no weather mechanic."},
    "drizzle":        {"status": "deferred", "category": "Weather",
                       "sim_desc": "Summons rain on entry — no weather mechanic."},
    "sand-spit":      {"status": "deferred", "category": "Weather",
                       "sim_desc": "Summons sandstorm when hit — no weather mechanic."},
    "psychic-surge":  {"status": "deferred", "category": "Weather",
                       "sim_desc": "Sets Psychic Terrain — no terrain mechanic."},
    "immunity":       {"status": "deferred", "category": "Status",
                       "sim_desc": "Prevents poison — no status mechanic."},
    "magma-armor":    {"status": "deferred", "category": "Status",
                       "sim_desc": "Prevents freezing — no status mechanic."},
    "marvel-scale":   {"status": "deferred", "category": "Status",
                       "sim_desc": "Def ×1.5 when statused — no status mechanic."},
    "arena-trap":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents grounded opponents from fleeing — no switching mechanic."},
    "poison-heal":    {"status": "deferred", "category": "Status",
                       "sim_desc": "Restores HP when poisoned — no status mechanic."},
    "toxic-chain":    {"status": "deferred", "category": "Status",
                       "sim_desc": "30% chance to badly poison on move use — no status mechanic."},
    "thermal-exchange": {"status": "ignored", "category": "No Effect",
                         "sim_desc": "Atk +1 when hit by Fire — stat modifier."},
    "zen-mode":         {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "Darmanitan form change at 50% HP — HP-conditional form change."},
    "defeatist":        {"status": "modeled", "category": "Offensive",
                         "sim_desc": "All moves ×0.75 (Atk and SpAtk halved below 50% HP; expected value under uniform HP distribution)."},
    "mummy":            {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Changes physical attacker's ability to Mummy on contact — per-matchup ability swap."},
    "lingering-aroma":  {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Changes attacker's ability to Lingering Aroma on contact — per-matchup ability swap."},
    "seed-sower":       {"status": "deferred", "category": "Weather",
                         "sim_desc": "Sets Grassy Terrain when hit — no terrain mechanic."},
    "anger-shell":      {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "Atk/SpAtk/Speed +1, Def/SpDef −1 when HP drops below 50% — HP-conditional stat change."},
    "opportunist":      {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Copies opponent's positive stat boosts — no stat modifier tracking."},
    "armor-tail":       {"status": "deferred", "category": "Tactical",
                         "sim_desc": "Prevents opponent from using priority moves — no priority mechanic."},
    "quick-draw":       {"status": "deferred", "category": "Tactical",
                         "sim_desc": "30% chance to go first regardless of speed — probability mechanic."},
    "tera-shift":       {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "Ogerpon Tera form on entry — Pokémon-specific."},
    "tera-shell":       {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "At full HP, SE moves deal neutral damage — Terapagos-specific."},
    "teraform-zero":    {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Removes weather and terrain — too complex."},
    "poison-puppeteer": {"status": "deferred", "category": "Status",
                         "sim_desc": "Poisons opponents Pecharunt poisons — no status mechanic."},
    "multitype":        {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Arceus changes type based on held plate — Pokémon-specific + items."},
    "flower-gift":      {"status": "deferred", "category": "Weather",
                         "sim_desc": "Raises Atk and SpDef of allies in harsh sun — weather + team."},
    "imposter":         {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Transforms into target on entry — would require full per-matchup stat/move copy."},
    "wimp-out":         {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "Switches out when HP drops below 50% — no switching mechanic."},
    "emergency-exit":   {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "Switches out when HP drops below 50% — no switching mechanic."},
    "rks-system":       {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Silvally changes type based on memory — Pokémon-specific + items."},
    "stance-change":    {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Aegislash toggles Blade/Shield form on attack vs protect — form-specific."},
    "schooling":        {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "Wishiwashi school/solo form based on HP — HP-conditional form change."},
    "battle-bond":      {"status": "deferred", "category": "Scaling",
                         "sim_desc": "Greninja transforms after KO — also excluded from scoring pool."},
    "ice-face":         {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "Eiscue blocks one physical hit — requires multi-hit sim change."},
    "wandering-spirit": {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Swaps ability with physical attacker on contact — per-matchup."},
    "mirror-armor":     {"status": "deferred", "category": "No Effect",
                         "sim_desc": "Reflects stat drops back at user — no stat modifier tracking."},
    "hunger-switch":    {"status": "deferred", "category": "HP Conditional",
                         "sim_desc": "Morpeko form switch each turn — Pokémon-specific."},
    "zero-to-hero":     {"status": "deferred", "category": "Scaling",
                         "sim_desc": "Palafin transforms into hero form after fainting — Pokémon-specific."},
    "disguise":       {"status": "deferred", "category": "HP Conditional",
                       "sim_desc": "Mimikyu blocks one hit — would require multi-hit sim change."},
    "dancer":         {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Copies dance moves — too complex to model."},
    "power-construct": {"status": "deferred", "category": "HP Conditional",
                        "sim_desc": "Zygarde form change at 50% HP — HP-conditional form change."},
    "trace":          {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Copies target's ability — requires per-matchup ability lookup; too complex."},
    "light-metal":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Halves weight — weight-based moves not relevant."},
    "cheek-pouch":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Restores HP when eating berries — items not modeled."},
    "gooey":          {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Lowers attacker's Speed on contact — stat modifier."},
    "tangling-hair":  {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Lowers attacker's Speed on contact — stat modifier."},
    "shadow-tag":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents opponent fleeing — no switching mechanic."},
    "rough-skin":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Deals 1/8 max HP to attacker on contact — requires per-turn HP tracking."},
    "iron-barbs":     {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Deals 1/8 max HP to attacker on contact — requires per-turn HP tracking."},
    "forewarn":       {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Reveals opponent's strongest move — no battle math effect."},
    "magician":       {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Steals held item — items not modeled."},
    "suction-cups":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents forced switching — no switching mechanic."},
    "white-smoke":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Prevents stat reduction — no stat modifier tracking."},
    "simple":         {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Doubles all stat changes — no stat modifier tracking."},
    "honey-gather":   {"status": "ignored", "category": "No Effect",
                       "sim_desc": "May find Honey after battle — items not modeled."},
    "wonder-skin":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Halves accuracy of status moves — status moves not scored."},
    "flower-veil":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Protects ally Grass types from status — team fights only."},
    "symbiosis":      {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Passes item to ally — team fights / items not modeled."},
    "ripen":          {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Doubles berry effects — items not modeled."},
    "unseen-fist":    {"status": "ignored", "category": "No Effect",
                       "sim_desc": "Contact moves bypass Protect — no protection mechanic."},
    "grass-pelt":       {"status": "deferred", "category": "Weather",
                         "sim_desc": "Def raised on Grassy Terrain — no terrain mechanic."},
    "air-lock":         {"status": "deferred", "category": "Weather",
                         "sim_desc": "Suppresses all weather effects — no weather mechanic."},
    "primordial-sea":   {"status": "deferred", "category": "Weather",
                         "sim_desc": "Summons extremely heavy rain — no weather mechanic."},
    "desolate-land":    {"status": "deferred", "category": "Weather",
                         "sim_desc": "Summons extremely harsh sun — no weather mechanic."},
    "delta-stream":     {"status": "deferred", "category": "Weather",
                         "sim_desc": "Summons strong winds — no weather mechanic."},
    "surge-surfer":     {"status": "deferred", "category": "Weather",
                         "sim_desc": "Speed ×2 on Electric Terrain — no terrain mechanic."},
    "mimicry":          {"status": "deferred", "category": "Weather",
                         "sim_desc": "Changes type based on active terrain — no terrain mechanic."},
    "electric-surge":   {"status": "deferred", "category": "Weather",
                       "sim_desc": "Sets Electric Terrain on entry — no terrain mechanic."},
    "misty-surge":    {"status": "deferred", "category": "Weather",
                       "sim_desc": "Sets Misty Terrain on entry — no terrain mechanic."},
    "wind-power":     {"status": "deferred", "category": "Weather",
                       "sim_desc": "Becomes Charged when hit by wind moves — no terrain/field mechanic."},
    "cloud-nine":     {"status": "deferred", "category": "Weather",
                       "sim_desc": "Suppresses all weather effects — no weather mechanic."},
    "slush-rush":     {"status": "deferred", "category": "Weather",
                       "sim_desc": "Speed ×2 in snow/hail — no weather mechanic."},
    "sand-stream":    {"status": "deferred", "category": "Weather",
                       "sim_desc": "Summons sandstorm on entry — no weather mechanic."},
    "drought":        {"status": "deferred", "category": "Weather",
                       "sim_desc": "Summons harsh sun on entry — no weather mechanic."},
    "grassy-surge":   {"status": "deferred", "category": "Weather",
                       "sim_desc": "Sets Grassy Terrain on entry — no terrain mechanic."},
    "orichalcum-pulse": {"status": "deferred", "category": "Weather",
                         "sim_desc": "Summons harsh sun + Atk boost in sun — no weather mechanic."},
    "corrosion":      {"status": "deferred", "category": "Status",
                       "sim_desc": "Allows poisoning Steel and Poison types — no status mechanic."},
    # ── Deferred: Weather ────────────────────────────────────────────────────
    "overcoat":    {"status": "deferred", "category": "Weather",
                    "sim_desc": "Immunity to powder moves and weather damage — no weather mechanic."},
    "hydration":   {"status": "deferred", "category": "Weather",
                    "sim_desc": "Cures status in rain — no weather mechanic."},
    "leaf-guard":  {"status": "deferred", "category": "Weather",
                    "sim_desc": "Prevents status in harsh sun — no weather mechanic."},
    "ice-body":    {"status": "deferred", "category": "Weather",
                    "sim_desc": "Restores HP in hail/snow — no weather mechanic."},
    # ── Deferred: Status ─────────────────────────────────────────────────────
    "flare-boost":  {"status": "deferred", "category": "Status",
                     "sim_desc": "SpAtk ×1.5 when burned — no status mechanic."},
    "bad-dreams":   {"status": "deferred", "category": "Status",
                     "sim_desc": "Damages sleeping opponents each turn — no status mechanic."},
    "toxic-boost":  {"status": "deferred", "category": "Status",
                     "sim_desc": "Atk ×1.5 when poisoned — no status mechanic."},
    "comatose":     {"status": "deferred", "category": "Status",
                     "sim_desc": "Permanently in sleep-like state but can still attack — status blocker."},
    "pastel-veil":  {"status": "deferred", "category": "Status",
                     "sim_desc": "Prevents poison of self and allies — no status mechanic."},
    "oblivious":    {"status": "deferred", "category": "Status",
                     "sim_desc": "Prevents infatuation and Taunt — no status mechanic."},
    "guts":         {"status": "deferred", "category": "Status",
                     "sim_desc": "Atk ×1.5 when statused — no status mechanic."},
    "insomnia":     {"status": "deferred", "category": "Status",
                     "sim_desc": "Prevents sleep — no status mechanic."},
    "flame-body":   {"status": "deferred", "category": "Status",
                     "sim_desc": "30% burn on contact — no status mechanic."},
    "shed-skin":    {"status": "deferred", "category": "Status",
                     "sim_desc": "30% chance to cure status each turn — no status mechanic."},
    "poison-point": {"status": "deferred", "category": "Status",
                     "sim_desc": "30% poison on contact — no status mechanic."},
}


def _badges(types: list[str], small: bool = False) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    hl = QHBoxLayout(w)
    hl.setContentsMargins(4, 1, 4, 1)
    hl.setSpacing(3)
    for t in types:
        bg, fg = TYPE_COLORS.get(t, ("#888", "#fff"))
        lbl = QLabel(t[:4].upper())
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if small:
            lbl.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:2px;"
                f"padding:0 2px; font-size:9px; font-weight:bold;"
            )
            lbl.setFixedHeight(13)
        else:
            lbl.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:3px;"
                f"padding:0 4px; font-size:10px; font-weight:bold;"
            )
            lbl.setFixedHeight(17)
        hl.addWidget(lbl)
    hl.addStretch()
    return w


class ImpactTableDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Impact Score Browser")
        self.setModal(False)
        self.resize(1150, 700)
        self.setStyleSheet(_STYLE)

        # Pokémon tab state
        self._all_rows: list[dict] = []
        self._visible_rows: list[dict] = []
        self._sort_col = 4
        self._sort_asc = False
        self._clean_mode = False

        # Moves tab state
        self._moves_all_rows: list[dict] = []
        self._moves_sort_col = 0   # # Sets, descending
        self._moves_sort_asc = False
        self._moves_loaded = False
        self._moves_init_started = False
        self._moves_adoptions_all:   dict[str, int] = {}
        self._moves_adoptions_clean: dict[str, int] = {}
        self._moves_debug = False

        # Abilities tab state
        self._abilities_all_rows: list[dict] = []
        self._abilities_sort_col = 1   # # Pokémon, descending
        self._abilities_sort_asc = False
        self._abilities_loaded = False
        self._abilities_init_started = False
        self._abilities_debug = False

        # Matchups tab state
        self._matchup_all_rows: list[dict] = []
        self._matchup_poke_name: str = ""
        self._matchup_sort_col: int = 0   # Outcome, ascending (ZDW first)
        self._matchup_sort_asc: bool = True

        self._build_ui()

        if impact_db.is_ready():
            self._load_data()
        else:
            self._status.setText("Building caches — this takes a few minutes on first run…")

    @pyqtSlot()
    def refresh(self):
        """Called when impact_db becomes ready after the dialog was already open."""
        if self._all_rows:
            return
        self._load_data()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)

        self._tabs = QTabWidget()
        outer.addWidget(self._tabs)

        poke_w = QWidget()
        self._build_pokemon_tab(poke_w)
        self._tabs.addTab(poke_w, "Pokémon")

        moves_w = QWidget()
        self._build_moves_tab(moves_w)
        self._tabs.addTab(moves_w, "Moves")

        abil_w = QWidget()
        self._build_abilities_tab(abil_w)
        self._tabs.addTab(abil_w, "Abilities")

        matchup_w = QWidget()
        self._build_matchups_tab(matchup_w)
        self._tabs.addTab(matchup_w, "Matchups")

        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _build_pokemon_tab(self, container: QWidget):
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name…")
        self._search.textChanged.connect(self._apply_filter)
        top.addWidget(self._search, 1)

        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setFixedHeight(18)
            f.setStyleSheet("color: #45475a;")
            return f

        def _group_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#6c7086; font-size:10px; font-weight:bold; letter-spacing:0.5px;")
            return lbl

        top.addSpacing(10)
        top.addWidget(_sep())
        top.addSpacing(6)
        top.addWidget(_group_label("FILTER"))
        top.addSpacing(6)
        self._hide_leg = QCheckBox("Legendaries")
        self._hide_leg.toggled.connect(self._apply_filter)
        top.addWidget(self._hide_leg)
        self._hide_paradox = QCheckBox("Paradox")
        self._hide_paradox.toggled.connect(self._apply_filter)
        top.addWidget(self._hide_paradox)
        self._only_starters = QCheckBox("Starters only")
        self._only_starters.toggled.connect(self._apply_filter)
        top.addWidget(self._only_starters)

        top.addSpacing(10)
        top.addWidget(_sep())
        top.addSpacing(6)
        top.addWidget(_group_label("SCORING"))
        top.addSpacing(6)
        self._clean_cb = QCheckBox("No recoil")
        self._clean_cb.toggled.connect(self._on_clean_toggled)
        top.addWidget(self._clean_cb)
        self._egg_cb = QCheckBox("Egg mvs")
        self._egg_cb.toggled.connect(self._on_egg_toggled)
        top.addWidget(self._egg_cb)

        self._egg_cap_slider = QSlider(Qt.Orientation.Horizontal)
        self._egg_cap_slider.setRange(1, 4)
        self._egg_cap_slider.setValue(4)
        self._egg_cap_slider.setFixedWidth(56)
        self._egg_cap_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._egg_cap_slider.setTickInterval(1)
        self._egg_cap_slider.setVisible(False)
        self._egg_cap_slider.valueChanged.connect(self._on_egg_cap_changed)
        top.addWidget(self._egg_cap_slider)

        self._egg_cap_lbl = QLabel("4🥚")
        self._egg_cap_lbl.setStyleSheet("color:#cdd6f4; font-size:11px; min-width:24px;")
        self._egg_cap_lbl.setVisible(False)
        top.addWidget(self._egg_cap_lbl)

        layout.addLayout(top)

        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels(
            ["Rank", "Filter", "Types", "Name", "Impact", "Offense", "Defense", "Speed", "Moveset"]
        )
        self._table.setSortingEnabled(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setWordWrap(False)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(4, Qt.SortOrder.DescendingOrder)
        hdr.sectionClicked.connect(self._on_header_click)
        self._table.cellClicked.connect(self._on_cell_clicked)

        self._table.setColumnWidth(0, 52)
        self._table.setColumnWidth(1, 52)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(3, 200)
        self._table.setColumnWidth(4, 66)
        self._table.setColumnWidth(5, 70)
        self._table.setColumnWidth(6, 70)
        self._table.setColumnWidth(7, 66)

        layout.addWidget(self._table)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#6c7086; font-size:11px;")
        layout.addWidget(self._status)

    def _build_moves_tab(self, container: QWidget):
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)
        self._moves_search = QLineEdit()
        self._moves_search.setPlaceholderText("Search moves…")
        self._moves_search.textChanged.connect(self._apply_moves_filter)
        top.addWidget(self._moves_search, 1)
        top.addSpacing(8)

        self._moves_adverse_cb = QComboBox()
        self._moves_adverse_cb.addItems(["All adverse effects", "Safe only", "Self-damaging", "Self-reducing"])
        self._moves_adverse_cb.currentIndexChanged.connect(self._apply_moves_filter)
        top.addWidget(self._moves_adverse_cb)

        self._moves_cat_cb = QComboBox()
        self._moves_cat_cb.addItems(["All categories", "Physical", "Special", "Status"])
        self._moves_cat_cb.currentIndexChanged.connect(self._apply_moves_filter)
        top.addWidget(self._moves_cat_cb)

        self._moves_debug_cb = QCheckBox("Debug")
        self._moves_debug_cb.toggled.connect(self._on_moves_debug_toggled)
        top.addWidget(self._moves_debug_cb)

        layout.addLayout(top)

        self._moves_table = QTableWidget()
        self._moves_table.setColumnCount(11)
        self._moves_table.setHorizontalHeaderLabels(
            ["# Sets", "Name", "Type", "Category", "Power", "Hits", "Accuracy", "PP",
             "Adverse", "Sim Note", "Description"]
        )
        self._moves_table.setSortingEnabled(False)
        self._moves_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._moves_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._moves_table.verticalHeader().setVisible(False)
        self._moves_table.verticalHeader().setDefaultSectionSize(24)
        self._moves_table.setWordWrap(False)

        hdr = self._moves_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(9, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        hdr.sectionClicked.connect(self._on_moves_header_click)

        self._moves_table.setColumnWidth(0, 68)
        self._moves_table.setColumnWidth(1, 170)
        self._moves_table.setColumnWidth(2, 85)
        self._moves_table.setColumnWidth(3, 80)
        self._moves_table.setColumnWidth(4, 55)
        self._moves_table.setColumnWidth(5, 50)
        self._moves_table.setColumnWidth(6, 65)
        self._moves_table.setColumnWidth(7, 42)
        self._moves_table.setColumnWidth(8, 100)
        self._moves_table.setColumnWidth(9, 190)

        # Debug columns hidden by default
        self._moves_table.setColumnHidden(5, True)
        self._moves_table.setColumnHidden(8, True)
        self._moves_table.setColumnHidden(9, True)

        layout.addWidget(self._moves_table)

        self._moves_status = QLabel("Moves load on first visit to this tab.")
        self._moves_status.setStyleSheet("color:#6c7086; font-size:11px;")
        layout.addWidget(self._moves_status)

    def _build_abilities_tab(self, container: QWidget):
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)
        self._abilities_search = QLineEdit()
        self._abilities_search.setPlaceholderText("Search abilities…")
        self._abilities_search.textChanged.connect(self._apply_abilities_filter)
        top.addWidget(self._abilities_search, 1)
        top.addSpacing(8)

        self._abilities_debug_cb = QCheckBox("Debug")
        self._abilities_debug_cb.setChecked(False)
        self._abilities_debug_cb.toggled.connect(self._on_abilities_debug_toggled)
        top.addWidget(self._abilities_debug_cb)

        layout.addLayout(top)

        # Cols: 0 Ability | 1 # Pokémon | 2 Category | 3 In Sim | 4 Sim Desc (debug) | 5 Description
        self._abilities_table = QTableWidget()
        self._abilities_table.setColumnCount(6)
        self._abilities_table.setHorizontalHeaderLabels(
            ["Ability", "# Pokémon", "Category", "In Sim", "Sim Description", "Description"]
        )
        self._abilities_table.setSortingEnabled(False)
        self._abilities_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._abilities_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._abilities_table.verticalHeader().setVisible(False)
        self._abilities_table.verticalHeader().setDefaultSectionSize(24)
        self._abilities_table.setWordWrap(False)

        hdr = self._abilities_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(1, Qt.SortOrder.DescendingOrder)
        hdr.sectionClicked.connect(self._on_abilities_header_click)

        self._abilities_table.setColumnWidth(0, 175)
        self._abilities_table.setColumnWidth(1, 85)
        self._abilities_table.setColumnWidth(2, 105)
        self._abilities_table.setColumnWidth(3, 58)
        self._abilities_table.setColumnWidth(4, 260)
        self._abilities_table.setColumnHidden(4, True)

        layout.addWidget(self._abilities_table)

        self._abilities_status = QLabel("Abilities load on first visit to this tab.")
        self._abilities_status.setStyleSheet("color:#6c7086; font-size:11px;")
        layout.addWidget(self._abilities_status)

    # ── Tab switching ─────────────────────────────────────────────────────────

    @pyqtSlot(int)
    def _on_tab_changed(self, index: int):
        if index == 1 and not self._moves_loaded:
            if move_info_db.is_ready():
                self._moves_loaded = True
                self._apply_moves_filter()
            elif not self._moves_init_started:
                self._moves_init_started = True
                self._init_moves_tab()
        elif index == 2 and not self._abilities_loaded:
            if ability_info_db.is_ready():
                self._abilities_loaded = True
                self._apply_abilities_filter()
            elif not self._abilities_init_started:
                self._abilities_init_started = True
                self._init_abilities_tab()

    # ── Pokémon tab ───────────────────────────────────────────────────────────

    def _on_clean_toggled(self, checked: bool):
        self._clean_mode = checked
        if impact_db.is_ready():
            self._load_data()

    def _on_egg_cap_changed(self, val: int):
        self._egg_cap_lbl.setText(f"{val}🥚")
        impact_db.set_egg_cap(val)
        self._load_data()

    def _on_egg_toggled(self, checked: bool):
        self._egg_cap_slider.setVisible(checked)
        self._egg_cap_lbl.setVisible(checked)
        if not checked:
            impact_db.set_include_egg(False)
            self._load_data()
            return
        if impact_db.is_egg_ready():
            impact_db.set_include_egg(True)
            self._load_data()
            return
        impact_db.init_egg(
            on_progress=lambda msg: QMetaObject.invokeMethod(
                self._status, "setText",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg),
            ),
            on_ready=lambda: QMetaObject.invokeMethod(
                self, "_on_egg_ready",
                Qt.ConnectionType.QueuedConnection,
            ),
        )
        self._status.setText("Building egg-moves cache…")

    @pyqtSlot()
    def _on_egg_ready(self):
        impact_db.set_include_egg(True)
        self._load_data()

    def _load_data(self):
        # Reset adoptions so Moves tab re-fetches them on next filter call
        self._moves_adoptions_all   = {}
        self._moves_adoptions_clean = {}

        entries  = impact_db.all_entries()
        clean    = self._clean_mode
        sort_key = (lambda x: x[1].get("impact_clean", x[1]["impact"])) if clean \
                   else (lambda x: x[1]["impact"])
        sorted_entries = sorted(entries.items(), key=sort_key, reverse=True)
        self._all_rows = []
        cap = impact_db.get_egg_cap()
        for rank, (name, data) in enumerate(sorted_entries, 1):
            types = data.get("types", [])
            stab_cov: set[str] = set()
            for t in types:
                stab_cov |= OFFENSIVE_CHART.get(t, frozenset())

            if clean:
                raw_moves = data.get("moves_clean", data.get("moves", []))
                active_impact = data.get("impact_clean", data["impact"])
            else:
                raw_moves = impact_db.get_capped_moves(name, cap)
                active_impact = data["impact"]

            self._all_rows.append({
                "rank":            rank,
                "name":            name,
                "display":         name.replace("-", " ").title(),
                "impact":          active_impact,
                "coverage":        data.get("coverage", data["impact"]),
                "speed":           data.get("speed", 0),
                "pct":             data["percentile"],
                "def_score":       data.get("bulk", 0.0),
                "types":           types,
                "legendary":       data.get("legendary", False),
                "paradox":         data.get("paradox", False),
                "starter":         name in _STARTER_POKEMON,
                "stab_cov":        sorted(stab_cov),
                "moves":           raw_moves,
                "outcomes":        data.get("outcomes", {}),
                "move_usage":      data.get("move_usage", {}),
                "ability_used":          data.get("ability_used"),
                "ability_acknowledged":  data.get("ability_acknowledged"),
                "passive_ability":       data.get("passive_ability"),
                "passive_acknowledged":  data.get("passive_acknowledged"),
            })

        nonleg    = [r for r in self._all_rows if not r["legendary"] and not r["paradox"]]
        impact_vals = [r["impact"]    for r in nonleg if r["impact"]    > 0]
        cov_vals    = [r["coverage"]  for r in nonleg if r["coverage"]  > 0]
        spd_vals    = [r["speed"]     for r in nonleg if r["speed"]     > 0]
        bulk_vals   = [r["def_score"] for r in nonleg if r["def_score"] > 0]
        med_impact = statistics.median(impact_vals) if impact_vals else 1.0
        med_cov    = statistics.median(cov_vals)    if cov_vals    else 1.0
        med_spd    = statistics.median(spd_vals)    if spd_vals    else 1.0
        med_bulk   = statistics.median(bulk_vals)   if bulk_vals   else 1.0
        for row in self._all_rows:
            row["combined"]   = max(row["impact"]    / med_impact, 1e-9)
            row["off_impact"] = max(row["coverage"]  / med_cov,    1e-9)
            row["spd_impact"] = max(row["speed"]     / med_spd,    1e-9)
            row["def_impact"] = max(row["def_score"] / med_bulk,   1e-9)

        self._apply_filter()
        if self._moves_loaded:
            self._apply_moves_filter()

    def _apply_filter(self):
        q = self._search.text().strip().lower()
        hide_leg      = self._hide_leg.isChecked()
        hide_paradox  = self._hide_paradox.isChecked()
        only_starters = self._only_starters.isChecked()
        is_filtered   = bool(q or hide_leg or hide_paradox or only_starters)
        self._table.setColumnHidden(1, not is_filtered)
        rows = [
            r for r in self._all_rows
            if (not q or q in r["name"])
            and (not hide_leg or not r["legendary"])
            and (not hide_paradox or not r["paradox"])
            and (not only_starters or r["starter"])
        ]
        if self._sort_col in _SORTABLE:
            field, asc_default = _SORTABLE[self._sort_col]
            rows.sort(key=lambda r: r[field], reverse=not self._sort_asc)
        self._populate(rows)

    @pyqtSlot(int, int)
    def _on_cell_clicked(self, row: int, col: int):
        if col != 3 or row >= len(self._visible_rows):
            return
        r = self._visible_rows[row]
        QDesktopServices.openUrl(QUrl(_bulbapedia_url(r["name"], r["display"])))

    def _on_header_click(self, col: int):
        if col not in _SORTABLE:
            return
        _, asc_default = _SORTABLE[col]
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = asc_default
        order = (Qt.SortOrder.AscendingOrder if self._sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._table.horizontalHeader().setSortIndicator(col, order)
        self._apply_filter()

    def _populate(self, rows: list[dict]):
        self._visible_rows = list(rows)
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))

        for i, row in enumerate(rows):
            rank_item = QTableWidgetItem(str(row["rank"]))
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            rank_item.setForeground(QColor("#6c7086"))
            self._table.setItem(i, 0, rank_item)

            filt_item = QTableWidgetItem(str(i + 1))
            filt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            filt_item.setForeground(QColor("#cdd6f4"))
            self._table.setItem(i, 1, filt_item)

            self._table.setCellWidget(i, 2, _badges(row["types"]))

            prefix = "★ " if row["legendary"] else ""
            name_color = "#cba6f7" if row["legendary"] else "#89b4fa"
            ability_used         = row.get("ability_used")
            ability_acknowledged = row.get("ability_acknowledged")
            passive_ability      = row.get("passive_ability")
            passive_acknowledged = row.get("passive_acknowledged")
            # (text, color) — modeled abilities in normal color, known-but-unmodeled dimmed
            ab_parts: list[tuple[str, str]] = []
            if ability_used:
                ab_parts.append((ability_used.replace("-", " ").title(), "#a6adc8"))
            if passive_ability and passive_ability != ability_used:
                ab_parts.append((passive_ability.replace("-", " ").title() + " (p)", "#a6adc8"))
            if ability_acknowledged:
                ab_parts.append((ability_acknowledged.replace("-", " ").title() + " (—)", "#45475a"))
            if passive_acknowledged:
                ab_parts.append((passive_acknowledged.replace("-", " ").title() + " (—p)", "#45475a"))
            if ab_parts:
                ab_html = " · ".join(
                    f'<span style="color:{c};">{t}</span>' for t, c in ab_parts
                )
                name_html = (
                    f'<span style="color:{name_color};">{prefix}{row["display"]}</span>'
                    f'<br><span style="font-size:10px; font-style:italic;">{ab_html}</span>'
                )
            else:
                name_html = f'<span style="color:{name_color};">{prefix}{row["display"]}</span>'
            name_lbl = QLabel(name_html)
            name_lbl.setTextFormat(Qt.TextFormat.RichText)
            name_lbl.setStyleSheet("background: transparent; padding-left: 4px;")
            name_lbl.setToolTip("Click to open Bulbapedia")
            name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._table.setCellWidget(i, 3, name_lbl)

            combined_item = QTableWidgetItem(f"{row['combined']*100:.0f}")
            combined_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            combined_item.setForeground(QColor("#cba6f7"))
            oc = row.get("outcomes", {})
            if oc:
                outcome_line = (
                    f"{oc.get('zdw',0)} ZDW  "
                    f"{oc.get('dw',0)} DW  "
                    f"{oc.get('dl',0)} DL  "
                    f"{oc.get('zdl',0)} ZDL\n"
                )
            else:
                outcome_line = ""
            mode_note = (
                "Clean mode: recoil and self-reducing moves excluded."
                if self._clean_mode else
                "All moves included (recoil and self-reducing moves allowed)."
            )
            combined_item.setToolTip(
                outcome_line +
                "Battle score: Σ (A_hp − B_hp + 1) / 2 across all non-legendary matchups\n"
                f"Normalized to median non-legendary. 100 = median, 200 = twice median.\n"
                f"{mode_note}"
            )
            self._table.setItem(i, 4, combined_item)

            off_item = QTableWidgetItem(f"{row['off_impact']*100:.0f}")
            off_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            off_item.setForeground(QColor("#f9e2af"))
            off_item.setToolTip("coverage / median coverage  (excl. legendaries & paradox)")
            self._table.setItem(i, 5, off_item)

            def_item = QTableWidgetItem(f"{row['def_impact']*100:.0f}")
            def_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            def_item.setForeground(QColor("#a6e3a1"))
            def_item.setToolTip("bulk / median bulk  (excl. legendaries & paradox)\nbulk = Σ sqrt(hits-to-KO) across 18 types")
            self._table.setItem(i, 6, def_item)

            spd_item = QTableWidgetItem(f"{row['spd_impact']*100:.0f}")
            spd_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            spd_item.setForeground(QColor("#89b4fa"))
            spd_item.setToolTip("base speed / median base speed  (excl. legendaries & paradox)")
            self._table.setItem(i, 7, spd_item)

            usage = row.get("move_usage", {})
            move_names = " · ".join(
                m["name"].replace("-", " ").title()
                for m in row["moves"]
            )
            move_item = QTableWidgetItem(move_names)
            move_item.setForeground(QColor("#a6adc8"))
            move_item.setToolTip("\n".join(
                "{name}  [{t}]  {bp} BP{u}".format(
                    name=m["name"].replace("-", " ").title(),
                    t=m["type"],
                    bp=m["power"],
                    u=f"  ·  best in {usage[m['name']]} matchups" if m["name"] in usage else "",
                )
                for m in row["moves"]
            ))
            self._table.setItem(i, 8, move_item)

        self._status.setText(f"{len(rows):,} Pokémon")

    # ── Moves tab ─────────────────────────────────────────────────────────────

    def _on_moves_debug_toggled(self, checked: bool):
        self._moves_debug = checked
        self._moves_table.setColumnHidden(5, not checked)
        self._moves_table.setColumnHidden(8, not checked)
        self._moves_table.setColumnHidden(9, not checked)

    def _on_abilities_debug_toggled(self, checked: bool):
        self._abilities_debug = checked
        self._abilities_table.setColumnHidden(4, not checked)

    def _init_moves_tab(self):
        def on_prog(msg: str):
            QMetaObject.invokeMethod(
                self._moves_status, "setText",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg),
            )
            if move_info_db.is_ready():
                QMetaObject.invokeMethod(
                    self, "_finish_moves_tab",
                    Qt.ConnectionType.QueuedConnection,
                )
        self._moves_status.setText("Fetching moves…")
        move_info_db.init(on_progress=on_prog)

    @pyqtSlot()
    def _finish_moves_tab(self):
        if self._moves_loaded:
            return
        self._moves_loaded = True
        self._apply_moves_filter()

    def _apply_moves_filter(self):
        if not move_info_db.is_ready():
            return
        if not self._moves_all_rows:
            self._moves_all_rows = list(move_info_db.all_entries().values())
        if impact_db.is_ready() and not self._moves_adoptions_all:
            self._moves_adoptions_all, self._moves_adoptions_clean = impact_db.get_move_adoptions()

        q           = self._moves_search.text().strip().lower()
        adv_choice  = self._moves_adverse_cb.currentText()
        cat_choice  = self._moves_cat_cb.currentText()

        rows = [
            r for r in self._moves_all_rows
            if (not q or q in r["name"] or q in r["display"].lower())
            and (adv_choice == "All adverse effects"
                 or (adv_choice == "Safe only" and r["adverse"] == "None")
                 or r["adverse"] == adv_choice)
            and (cat_choice == "All categories"
                 or r["category"].lower() == cat_choice.lower())
        ]

        adopt_all   = self._moves_adoptions_all
        adopt_clean = self._moves_adoptions_clean
        # col → (field, asc_default); None = special-cased
        _moves_sort_keys = {
            0: None,            # # Sets — sorted by adoption count
            1: ("display",  True),
            2: ("type",     True),
            3: ("category", True),
            4: ("power",    False),
            5: ("min_hits", False),
            6: ("accuracy", False),
            7: ("pp",       False),
            8: ("adverse",  True),
            9: ("excluded", True),
        }
        if self._moves_sort_col == 0:
            rows.sort(
                key=lambda r: adopt_all.get(r["name"], 0),
                reverse=not self._moves_sort_asc,
            )
        else:
            entry = _moves_sort_keys.get(self._moves_sort_col)
            key_field = entry[0] if entry else "display"
            rows.sort(
                key=lambda r: r.get(key_field, 0) if isinstance(r.get(key_field, 0), int)
                              else (r.get(key_field) or "").lower(),
                reverse=not self._moves_sort_asc,
            )
        self._populate_moves(rows, adopt_all, adopt_clean)

    def _populate_moves(self, rows: list[dict],
                        adopt_all: dict[str, int],
                        adopt_clean: dict[str, int]):
        t = self._moves_table
        t.setRowCount(0)
        t.setRowCount(len(rows))

        _cat_colors = {"Physical": "#fab387", "Special": "#89b4fa", "Status": "#a6adc8"}

        for i, row in enumerate(rows):
            mn    = row["name"]
            excl  = row.get("excluded", "")
            adv   = row["adverse"]

            # col 0: # Sets
            n_all   = adopt_all.get(mn, 0)
            n_clean = adopt_clean.get(mn, 0)
            sets_item = QTableWidgetItem(str(n_all) if n_all else "—")
            sets_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sets_item.setForeground(QColor("#a6e3a1" if n_all else "#6c7086"))
            sets_item.setToolTip(
                f"All mode (incl. recoil/stat-drop): {n_all} forms\n"
                f"Clean mode (no recoil/stat-drop):  {n_clean} forms"
            )
            t.setItem(i, 0, sets_item)

            # col 1: Name — colored by adverse/exclusion status
            if excl.startswith("Always excluded"):
                name_color = "#6c7086"   # grey — never used in sim
            elif adv == "Self-damaging":
                name_color = "#f38ba8"   # red
            elif adv == "Self-reducing":
                name_color = "#f9e2af"   # yellow
            else:
                name_color = "#cdd6f4"
            name_item = QTableWidgetItem(row["display"])
            name_item.setForeground(QColor(name_color))
            t.setItem(i, 1, name_item)

            # col 2: Type badge
            t.setCellWidget(i, 2, _badges([row["type"]], small=True))

            # col 3: Category
            cat = row["category"].title()
            cat_item = QTableWidgetItem(cat)
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            cat_item.setForeground(QColor(_cat_colors.get(cat, "#cdd6f4")))
            t.setItem(i, 3, cat_item)

            # col 4: Power
            pwr = row["power"]
            pwr_item = QTableWidgetItem(str(pwr) if pwr else "—")
            pwr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            pwr_item.setForeground(QColor("#cba6f7"))
            t.setItem(i, 4, pwr_item)

            # col 5: Hits (debug)
            min_h = row.get("min_hits") or 0
            max_h = row.get("max_hits") or 0
            if min_h and max_h:
                hits_text  = f"{min_h}×" if min_h == max_h else f"{min_h}-{max_h}×"
                hits_tip   = f"Hits {min_h}-{max_h} times; avg ×{(min_h+max_h)/2:.1f} effective power in sim"
                hits_color = "#f9e2af"
            else:
                hits_text  = "—"
                hits_tip   = "Single hit"
                hits_color = "#6c7086"
            hits_item = QTableWidgetItem(hits_text)
            hits_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            hits_item.setForeground(QColor(hits_color))
            hits_item.setToolTip(hits_tip)
            t.setItem(i, 5, hits_item)

            # col 6: Accuracy
            acc = row["accuracy"]
            acc_item = QTableWidgetItem(f"{acc}%" if acc else "—")
            acc_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            acc_item.setForeground(QColor("#cdd6f4"))
            t.setItem(i, 6, acc_item)

            # col 7: PP
            pp_item = QTableWidgetItem(str(row["pp"]) if row["pp"] else "—")
            pp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            pp_item.setForeground(QColor("#6c7086"))
            t.setItem(i, 7, pp_item)

            # col 8: Adverse (debug)
            adv_item = QTableWidgetItem(adv)
            adv_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            adv_item.setForeground(QColor(_ADVERSE_COLORS.get(adv, "#cdd6f4")))
            t.setItem(i, 8, adv_item)

            # col 9: Sim Note (debug)
            excl_item = QTableWidgetItem(excl if excl else "—")
            excl_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            excl_item.setForeground(QColor("#f38ba8") if excl else QColor("#a6e3a1"))
            t.setItem(i, 9, excl_item)

            # col 10: Description
            eff_item = QTableWidgetItem(row["effect"])
            eff_item.setForeground(QColor("#a6adc8"))
            t.setItem(i, 10, eff_item)

        self._moves_status.setText(f"{len(rows):,} moves")

    def _on_moves_header_click(self, col: int):
        if col not in range(10):  # cols 0-9 are sortable; 10 (Description) is not
            return
        if self._moves_sort_col == col:
            self._moves_sort_asc = not self._moves_sort_asc
        else:
            self._moves_sort_col = col
            self._moves_sort_asc = col in {1, 2, 3, 8, 9}  # strings default ascending
        order = (Qt.SortOrder.AscendingOrder if self._moves_sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._moves_table.horizontalHeader().setSortIndicator(col, order)
        self._apply_moves_filter()

    # ── Abilities tab ─────────────────────────────────────────────────────────

    def _init_abilities_tab(self):
        def on_prog(msg: str):
            QMetaObject.invokeMethod(
                self._abilities_status, "setText",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg),
            )
            if ability_info_db.is_ready():
                QMetaObject.invokeMethod(
                    self, "_finish_abilities_tab",
                    Qt.ConnectionType.QueuedConnection,
                )
        self._abilities_status.setText("Fetching abilities…")
        ability_info_db.init(on_progress=on_prog)

    @pyqtSlot()
    def _finish_abilities_tab(self):
        if self._abilities_loaded:
            return
        self._abilities_loaded = True
        self._apply_abilities_filter()

    def _apply_abilities_filter(self):
        if not ability_info_db.is_ready():
            return
        if not self._abilities_all_rows:
            self._abilities_all_rows = list(ability_info_db.all_entries().values())

        q = self._abilities_search.text().strip().lower()

        rows = []
        for r in self._abilities_all_rows:
            if q and q not in r["name"] and q not in r["display"].lower() and q not in r["effect"].lower():
                continue
            sim_info = _ABILITY_SIM_INFO.get(r["name"], {})
            status   = sim_info.get("status", "")
            in_sim   = "✓" if status == "modeled" else "X" if status == "ignored" else "—" if status == "deferred" else ""
            rows.append({**r, "in_sim": in_sim, "category": sim_info.get("category", ""),
                         "sim_desc": sim_info.get("sim_desc", "")})

        _abil_sort_keys = {
            0: ("display",  True),
            1: ("total",    False),
            2: ("category", True),
            3: ("in_sim",   False),
        }
        key_field, _ = _abil_sort_keys.get(self._abilities_sort_col, ("total", False))
        rows.sort(
            key=lambda r: r[key_field] if isinstance(r[key_field], int)
                          else (r[key_field] or "").lower(),
            reverse=not self._abilities_sort_asc,
        )
        self._populate_abilities(rows)

    def _populate_abilities(self, rows: list[dict]):
        t = self._abilities_table
        t.setRowCount(0)
        t.setRowCount(len(rows))

        _IN_SIM_COLORS = {"✓": "#a6e3a1", "X": "#f38ba8", "—": "#f9e2af"}
        _CAT_COLORS = {
            "Offensive":      "#fab387",
            "Defensive":      "#a6e3a1",
            "Immunity":       "#89b4fa",
            "Type Remap":     "#cba6f7",
            "Weather":        "#94e2d5",
            "Tactical":       "#89dceb",
            "Scaling":        "#cba6f7",
            "Status":         "#f9e2af",
            "Flinching":      "#f9e2af",
            "Critical Hit":   "#f9e2af",
            "HP Conditional": "#f9e2af",
            "No Effect":      "#6c7086",
        }

        for i, row in enumerate(rows):
            name_item = QTableWidgetItem(row["display"])
            name_item.setForeground(QColor("#89b4fa"))
            t.setItem(i, 0, name_item)

            count_item = QTableWidgetItem(str(row["total"]))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            count_item.setForeground(QColor("#cdd6f4"))
            count_item.setToolTip(
                f"Primary (slot 1):        {row['primary']}\n"
                f"Secondary (slot 2):      {row['secondary']}\n"
                f"Hidden ability (slot 3): {row['hidden']}"
            )
            t.setItem(i, 1, count_item)

            cat = row.get("category", "")
            cat_item = QTableWidgetItem(cat)
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            cat_item.setForeground(QColor(_CAT_COLORS.get(cat, "#6c7086")))
            t.setItem(i, 2, cat_item)

            in_sim = row.get("in_sim", "")
            sim_item = QTableWidgetItem(in_sim)
            sim_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sim_item.setForeground(QColor(_IN_SIM_COLORS.get(in_sim, "#45475a")))
            t.setItem(i, 3, sim_item)

            sim_desc_item = QTableWidgetItem(row.get("sim_desc", ""))
            sim_desc_item.setForeground(QColor("#a6adc8"))
            t.setItem(i, 4, sim_desc_item)

            eff_item = QTableWidgetItem(row["effect"])
            eff_item.setForeground(QColor("#a6adc8"))
            t.setItem(i, 5, eff_item)

        self._abilities_status.setText(f"{len(rows):,} abilities")

    def _on_abilities_header_click(self, col: int):
        if col not in {0, 1, 2, 3}:  # cols 4 (Sim Desc) and 5 (Description) not sortable
            return
        if self._abilities_sort_col == col:
            self._abilities_sort_asc = not self._abilities_sort_asc
        else:
            self._abilities_sort_col = col
            self._abilities_sort_asc = col in {0, 2}  # strings default ascending; In Sim (3) defaults desc
        order = (Qt.SortOrder.AscendingOrder if self._abilities_sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._abilities_table.horizontalHeader().setSortIndicator(col, order)
        self._apply_abilities_filter()

    # ── Matchups tab ──────────────────────────────────────────────────────────

    def _build_matchups_tab(self, container: QWidget):
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)

        self._matchup_input = QLineEdit()
        self._matchup_input.setPlaceholderText("Type a Pokémon name and press Enter…")
        self._matchup_input.returnPressed.connect(self._load_matchup_details)
        top.addWidget(self._matchup_input, 1)

        load_btn = QPushButton("Load")
        load_btn.setStyleSheet(
            "QPushButton { background:#313244; color:#cdd6f4; border:1px solid #45475a;"
            " border-radius:4px; padding:4px 14px; font-size:12px; }"
            "QPushButton:hover { background:#45475a; }"
        )
        load_btn.clicked.connect(self._load_matchup_details)
        top.addWidget(load_btn)

        self._matchup_outcome_cb = QComboBox()
        self._matchup_outcome_cb.addItems(["All outcomes", "ZDW", "DW", "DL", "ZDL"])
        self._matchup_outcome_cb.currentIndexChanged.connect(self._apply_matchup_filter)
        top.addWidget(self._matchup_outcome_cb)

        layout.addLayout(top)

        # Cols: Outcome | Description (narrative)
        self._matchups_table = QTableWidget()
        self._matchups_table.setColumnCount(2)
        self._matchups_table.setHorizontalHeaderLabels(["Outcome", "Description"])
        self._matchups_table.setSortingEnabled(False)
        self._matchups_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._matchups_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._matchups_table.verticalHeader().setVisible(False)
        self._matchups_table.verticalHeader().setDefaultSectionSize(32)
        self._matchups_table.setWordWrap(False)

        hdr = self._matchups_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        hdr.sectionClicked.connect(self._on_matchups_header_click)

        self._matchups_table.setColumnWidth(0, 70)

        layout.addWidget(self._matchups_table)

        self._matchups_status = QLabel("Type a Pokémon name and press Enter to view its matchups.")
        self._matchups_status.setStyleSheet("color:#6c7086; font-size:11px;")
        layout.addWidget(self._matchups_status)

    def _load_matchup_details(self):
        if not impact_db.is_ready():
            self._matchups_status.setText("Impact DB not ready yet.")
            return
        raw = self._matchup_input.text().strip().lower().replace(" ", "-")
        if not raw:
            return

        # Exact match first, then substring
        db = impact_db.all_entries()
        if raw in db:
            name = raw
        else:
            matches = sorted(k for k in db if raw in k)
            if not matches:
                self._matchups_status.setText(f"No Pokémon found matching '{raw}'.")
                return
            name = matches[0]
            self._matchup_input.setText(name)

        self._matchups_status.setText(f"Computing matchups for {name.replace('-', ' ').title()}…")
        self._matchup_poke_name = name
        self._matchup_all_rows = impact_db.matchup_details(name)

        # Wire autocomplete from DB keys if not done yet
        if not self._matchup_input.completer():
            completer = QCompleter(sorted(db.keys()))
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self._matchup_input.setCompleter(completer)

        self._apply_matchup_filter()

    def _apply_matchup_filter(self):
        if not self._matchup_all_rows:
            return
        outcome_filter = self._matchup_outcome_cb.currentText()
        rows = self._matchup_all_rows
        if outcome_filter != "All outcomes":
            rows = [r for r in rows if r["outcome"] == outcome_filter]
        rows = sorted(
            rows,
            key=lambda r: _OUTCOME_RANK.get(r["outcome"], 9),
            reverse=not self._matchup_sort_asc,
        )
        self._populate_matchups(rows)

    def _populate_matchups(self, rows: list[dict]):
        _OUTCOME_COLORS = {
            "ZDW":  "#a6e3a1",
            "DW":   "#f9e2af",
            "DL":   "#fab387",
            "ZDL":  "#f38ba8",
            "Draw": "#6c7086",
        }
        t = self._matchups_table
        t.setRowCount(0)
        t.setRowCount(len(rows))

        for i, row in enumerate(rows):
            outcome  = row["outcome"]
            out_item = QTableWidgetItem(outcome)
            out_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            out_item.setForeground(QColor(_OUTCOME_COLORS.get(outcome, "#cdd6f4")))
            t.setItem(i, 0, out_item)

            html = _matchup_narrative(row, self._matchup_poke_name)
            lbl  = QLabel(html)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet("background: transparent; padding-left: 6px;")
            t.setCellWidget(i, 1, lbl)

        all_rows = self._matchup_all_rows
        zdw  = sum(1 for r in all_rows if r["outcome"] == "ZDW")
        dw   = sum(1 for r in all_rows if r["outcome"] == "DW")
        dl   = sum(1 for r in all_rows if r["outcome"] == "DL")
        zdl  = sum(1 for r in all_rows if r["outcome"] == "ZDL")
        wins = zdw + dw
        name_display = self._matchup_poke_name.replace("-", " ").title()
        status = (
            f"{name_display} — {wins}W / {len(all_rows) - wins}L  "
            f"({zdw} ZDW  {dw} DW  {dl} DL  {zdl} ZDL)"
        )
        if len(rows) != len(all_rows):
            status += f"  ·  showing {len(rows)} of {len(all_rows)}"
        self._matchups_status.setText(status)

    def _on_matchups_header_click(self, col: int):
        if col != 0:  # only Outcome is sortable
            return
        if self._matchup_sort_col == col:
            self._matchup_sort_asc = not self._matchup_sort_asc
        else:
            self._matchup_sort_col = col
            self._matchup_sort_asc = True  # ZDW first
        order = (Qt.SortOrder.AscendingOrder if self._matchup_sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._matchups_table.horizontalHeader().setSortIndicator(col, order)
        self._apply_matchup_filter()
