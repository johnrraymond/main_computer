# Hawk–Dove crossed-scenario ladder smoke (v5)

## Experimental correction

The previous version conflated **captain jacket** with **scenario/question**: each captain received a different task. This patch crosses the variables correctly:

```
scenario s01: c01, c02, c03, c04, c05, c06, c07, c08, c09, c10
scenario s02: c01, c02, c03, c04, c05, c06, c07, c08, c09, c10
scenario s03: c01, c02, c03, c04, c05, c06, c07, c08, c09, c10
```

Within one scenario:

- The **entire situation**, eight **categorical state values**, **opponent intelligence**, **question**, and **operation** are identical for all ten captains.
- Only the captain's **complete jacket** changes in the final raw story. Its separate synthetic X teacher gives the corresponding X target.
- The opponent model uses **one shared Y teacher**. Changing the captain does not change Y.
- The computed margin is `Z = X - Y`, with the previously established bounded logistic mapping used only for display.
- Every captain receives the same **eleven-step, raw-first condensation ladder**, L10 → L00.

The scenario registry now lives in `tools/nanojev_hawk_dove_scenarios_v1.py`. The captain registry contains **jackets and X rubrics only**; it no longer contains an asset, situation, question, or opponent-scoring teacher. The existing `tools/nanojev_hawk_dove_ladder_smoke.py` CLI was updated in place.

The scenarios currently share an abstract Hawk–Dove decision algebra; scenario-level categorical state is the only scored change between scenarios. This is intentional for an initial controlled smoke. Before claiming competence on genuinely new strategic tasks, we would need rubrics whose relevant decision variables differ and independent validation of the rubric semantics.

## Commands

From `C:\Users\subsi\main_computer`:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --self-test
python -m tools.nanojev_hawk_dove_ladder_smoke --list-captains
python -m tools.nanojev_hawk_dove_ladder_smoke --list-scenarios
```

**One scenario, ten captains, raw stories first:**

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --scenario s01 --all-captains --level 10
```

**Next scenario, same ten captains:**

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --scenario s02 --all-captains --level 10
```

**All scenarios, grouped by scenario (ten captains in each group):**

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --all-scenarios --all-captains --level 10
```

**One captain's full raw-first ladder within a shared scenario:**

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --scenario s02 --captain c07 --show-rubric
```

**Change one or more state variables for all ten captains simultaneously:**

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke `
  --scenario s01 --all-captains --level 10 `
  --crew-danger high --mission-dependency on `
  --opponent-pressure medium --opponent-escalating off
```

Each scenario has its own default categorical state. Explicit overrides take priority; all captains within a scenario receive the **same** resulting state.

**Machine-readable crossed data:**

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke `
  --all-scenarios --all-captains --format json
```

For `--all-scenarios`, JSON is an array of scenario groups, each with a `scenario_id`, `state`, and ten `captains` (each with jacket, eleven rungs, X/Y/OP/Z targets, and rubric if `--show-rubric`).

## Training boundary

All X and Y labels remain synthetic teacher outputs, not observed captain behavior or proven optimal decisions. The script generates data only; it **does not modify the active trainer, checkpoint, or SQLite task bank**. Whole-jacket and whole-scenario holdouts still need to be designed before using this as evidence of generalization.

## Tests

```powershell
python -m unittest discover -s tests -p 'test_nanojev_hawk_dove*.py' -v
```

The tests verify the exact shared non-jacket text, common Y, X variations, raw-first ladder, scenario-first CLI grouping, controlled state overrides, and the 1,296 × ten exhaustive rubric grid.
