# Impact Score — Methodology

## What It Measures

Impact Score is a single number representing a Pokémon's **offensive threat potential** across all possible opposing type combinations. It answers: *if this Pokémon ran its perfect offensive moveset, how much super-effective damage could it deal in aggregate?*

Unlike raw coverage (count of types hit SE) or raw power (attack stat alone), Impact Score denominates both in the same unit — effective damage — so they trade off naturally. A specialist with a massive Attack stat and one move type can outscore a generalist with four weak moves across many types.

---

## Formula

```
Impact Score = Σ over all 171 type pairings P of:
    max_SE_damage(P, optimal_moveset)

where max_SE_damage(P, moveset) =
    max over moves M in moveset of:
        stat(M) × base_power(M) × (accuracy(M) / 100) × STAB(M) × SE(M, P)
    or 0 if no move in the moveset hits P super-effectively
```

---

## Parameters

**Type pairings (171 total)**  
All unique unordered combinations of the 18 types a defending Pokémon can have:
- 18 single-type pairings `(Fire,)`, `(Water,)`, …
- 153 dual-type pairings `(Fire, Water)`, `(Fire, Grass)`, …
- Ordered duplicates like `(Water, Fire)` are collapsed; each real-world match-up is counted once.

**stat(M)**  
The Pokémon's Attack for Physical moves, Special Attack for Special moves. Base stats of the final evolution / alternate form used directly — no EVs, IVs, or natures applied (all Pokémon evaluated on equal footing).

**accuracy(M)**  
The move's listed accuracy as a fraction (85 accuracy → 0.85). Moves with no accuracy value (e.g. Swift) are treated as 100%.

**STAB**  
1.5 if the move's type matches one of the Pokémon's own types, 1.0 otherwise.

**SE(M, P)**  
The stacked type-effectiveness multiplier of move M against pairing P. Computed from the standard type chart:
- Neutral (1×): does not contribute (term is 0)
- Super-effective (2×): counted at 2×
- Double super-effective (4× for two compounding weaknesses): counted at 4×
- Any 0× immunity: not super-effective, term is 0

---

## Optimal Moveset Selection

A Pokémon's learnable movepool can contain dozens of damaging moves. We pick the **4 moves that maximise the Impact Score sum** — i.e., the set that covers the most type pairings at the highest damage values.

Because two moves that share type coverage have diminishing returns (only the stronger one counts per pairing), the selection is solved with a **greedy marginal-gain algorithm**:

1. For each candidate move, pre-compute its damage against every type pairing.
2. Pick the move with the highest total marginal gain over the current selected set.
3. Commit it; update the per-pairing best-damage values.
4. Repeat up to 4 times.

This is a standard greedy approach on a submodular coverage function, guaranteed to be within (1 − 1/e) ≈ 63% of optimal in the worst case and near-optimal in practice for Pokémon movepools (where true worst-case overlap is rare).

Only **damaging moves** (base_power > 0, category ≠ status) are eligible.

---

## Scope

- **Final evolutions only** — non-final forms (Charmeleon, Metapod, etc.) are excluded.
- **Alternate forms treated separately** — Rotom-Wash and Rotom-Heat are distinct rows with their own stats and movepools, and can score differently.
- **Legendaries and mythicals included** — they are scored but can be filtered out for team-building percentile pools.
- **No abilities, items, or field effects** — pure stat × move math only.

---

## Speed Weighting

Raw Impact Score captures offensive power but ignores turn order. A faster Pokémon is more likely to land its hit before being KO'd, making its damage more reliable in practice.

Speed is folded in as a soft floor penalty. Pokémon above the 30th speed percentile are unaffected; those below it receive a graduated penalty:

```
speed_factor = 1.0                              if speed_pct >= 50
speed_factor = 0.6 + 0.4 × (speed_pct / 50)    if speed_pct < 50

Combined Score = Impact Score × speed_factor
```

- Speed percentile is computed among all scored forms (0–100)
- Pokémon at or above p50 speed suffer **no penalty** — score equals raw impact
- Pokémon below p50 speed receive a penalty ranging from ×0.6 (slowest) to ×1.0 (p50)
- The maximum penalty is 40%, meaning even the slowest Pokémon retains 60% of its impact score

Rationale: in practice you want to avoid Pokémon so slow they're KO'd before acting, but beyond a reasonable speed baseline extra speed matters far less than raw power. The score is always ≤ the raw impact — speed cannot inflate a Pokémon's ranking above what its offensive coverage earns.

Both `impact` (raw) and `score` (speed-adjusted) are stored in the cache. The displayed percentile rank is based on the combined score.

---

## Percentile Ranks

After computing all scores, each Pokémon is assigned a percentile rank within the full scored population. Separate percentile pools (e.g. excluding legendaries) can be derived by filtering before computing rank.

---

## Limitations

- The greedy moveset is near-optimal but not guaranteed globally optimal.
- Accuracy is treated as an expected-value multiplier; variability is not modelled.
- STAB is the only damage bonus modelled (no abilities like Adaptability, Tough Claws, etc.).
- Move availability doesn't account for version exclusivity or breeding chains; any learnable move is considered fair game.
