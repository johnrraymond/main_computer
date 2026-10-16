# Hawk–Dove ten-captain head-to-head smoke

This patch adds `--matrix` to the existing `tools.nanojev_hawk_dove_ladder_smoke` command. It **does not train an AI** and does not modify any checkpoint, task database, or existing training schedule.

## What the matrix means

All ten predefined jackets face the **same scenario, qualitative state, and shared opponent evidence**. Each of 45 distinct captain pairs is evaluated once, producing a 10×10 directional matrix; 90 off-diagonal cells show both perspectives. Self-play is excluded (`--`).

In an ordinary crossed jacket experiment, the opponent estimate `Y0` stays fixed between all ten captains. For a **head-to-head** matchup, the opponent is now one of those ten captains, so its jacket must influence the estimate. The smoke uses this explicit synthetic teacher:

1. For each captain *i*, compute its willingness-to-contest score `X_i` using its existing deterministic jacket rubric, on the shared categorical state.
2. Compute the shared opponent-evidence baseline `Y0` using the existing synthetic opponent rubric.
3. Convert captain *j*'s relative disposition into a provisional **pilot** contest index: `p_j = bounded_logistic(X_j - Y0)`. This is an assumed proxy, not observed or learned behavior and not an equilibrium solution.
4. From captain *i*'s perspective, estimate rival *j* as `Y_ij = round(2,000,000 × p_j)`.
5. `Z_ij = X_i - Y_ij`: `H` (contest) if positive, `D` (yield) if negative, `I` (indifferent) if zero.
6. Both choices are made simultaneously; report `HH` mutual contest, `HD` row contests/column yields, `DH` row yields/column contests, `DD` mutual yield. An indifferent choice is not silently randomized. No combat is resolved or payoff invented.

A matrix cell is always written **row's action, column's action**. Thus the transpose of `HD` is `DH`. Cells `HH` and `DD` are their own transposes. `--` denotes a captain facing itself, excluded from the experiment.

## Commands

From `C:\Users\subsi\main_computer` after extracting the patch:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --matrix

python -m tools.nanojev_hawk_dove_ladder_smoke `
  --matrix --scenario s02

python -m tools.nanojev_hawk_dove_ladder_smoke `
  --matrix --all-scenarios

python -m tools.nanojev_hawk_dove_ladder_smoke `
  --matrix --scenario s01 --matrix-pair c01:c03

python -m tools.nanojev_hawk_dove_ladder_smoke `
  --matrix --scenario s03 --crew-danger high --resource-priority high

python -m tools.nanojev_hawk_dove_ladder_smoke `
  --matrix --all-scenarios --format json
```

`--matrix-pair` prints the full numerical comparison and a **raw pair-specific story** with both jackets and the same scenario. Both jackets must be present for the synthetic opponent Y to be inferable. `--matrix` deliberately skips the 11-rung output and instead shows the matchup table and per-captain statistics. Without `--matrix`, the raw-first ladder behaves as before.

The JSON mode contains `captains` (including full jacket text), `matches` (45 unordered pair records with both X/Y/Z/decisions), `cells` (10×10 directional display), `summary`, `matchup_counts`, `teacher`, and the categorical state for each scenario.

## Example result, default scenario s01

Ten captains against the *contested supply cache*, with default categorical state:

- 45 unique pairs, 90 off-diagonal directional results.
- 0 mutual contests, 40 one-sided contests, 5 mutual yields.
- `c01` vs `c03` is `DH`: the crew-first pragmatist yields and the deterrence hardliner contests.

For `s02` (emergency docking corridor), the same synthetic teacher yields 28 mutual contests, 17 one-sided contests, and no mutual yields. This is a change in state and stakes, not captain identity.

## Interpretation limits

The matrix is an inspectable deterministic **teacher smoke**, not ten autonomous simulated captains and not evidence of optimal game play. The synthetic function `p_j` assumes access to the opposing captain's jacket-derived disposition. It should eventually be replaced by a proper `K(opponent evidence, encounter history, context)` estimator trained and tested against opponent decisions. That requires labeled observations or simulated policy rollouts. **Do not use the synthetic opponent propensity as if it were observed ground truth.**

These head-to-head results supplement (and do not replace) the existing crossed-control comparison where Y is intentionally fixed across jackets. That control is still useful for isolating jacket effects.

## Verification

Run:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --self-test
python -m unittest tests.test_nanojev_hawk_dove_matrix -v
```

The new matrix tests cover the full pair count, directional symmetry, X/Y/Z consistency, opponent-specific Y, unchanged shared state, state override effects, different scenario interactions, exact tie handling, deterministic JSON, valid CLI pair selection, and the presence of both full jackets in the raw paired prompt. The previously present ten-captain and ladder tests also pass.
