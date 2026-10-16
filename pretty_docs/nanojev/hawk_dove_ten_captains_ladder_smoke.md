# Hawk–Dove ten-captain ladder smoke (v4)

A **deterministic offline inspection harness** with ten distinct, synthetic Space Captain jackets and ten associated question/rubric pairs. It modifies the existing `tools/nanojev_hawk_dove_ladder_smoke.py` in place and adds `tools/nanojev_hawk_dove_captains_v1.py`. No model weights, checkpoints, trainer configurations, or task databases are touched.

## Captains and questions

| ID | Jacket | Question theme | Driving tradeoff |
|---|---|---|---|
| c01 | Crew-first mission pragmatist | Supply-cache contest | Crew preservation vs mission necessity |
| c02 | Ally-bound guardian | Relief corridor | Allied commitment vs disproportionate sacrifice |
| c03 | Deterrence hardliner | Extortion checkpoint | Credible resistance vs future deterrence capability |
| c04 | Patient strategist | Listening post | Option preservation vs expiring opportunity |
| c05 | Civilian protector | Medical docking berth | De-escalation vs urgent evacuation |
| c06 | Expedition opportunist | Salvage | Opportunity upside vs personnel risk |
| c07 | Treaty keeper | Navigation lane | Treaty compliance vs essential mission |
| c08 | Deception skeptic | Sensor relay | Suspicion of feints vs justified exploitation |
| c09 | Mission absolutist | Coolant depot | Mission completion vs pointless losses |
| c10 | Coalition builder | Refueling station | Cooperative sharing vs exclusionary monopoly |

Every captain has its **own full jacket**, short doctrine, question, integer-valued teaching rubric, possible cross-variable interactions, and semantic names for X and Y. The original c01 jacket, default states, and label values are retained.

## Run it

From `C:\Users\subsi\main_computer`:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --self-test
python -m tools.nanojev_hawk_dove_ladder_smoke --list-captains
```

Inspect a particular captain, raw story **first**, then progressively condensed L09 → L00:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --captain c04
```

Inspect **all ten raw questions** while holding the same qualitative state fixed:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --all-captains --level 10
```

Compare each captain's raw question with one rung:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --all-captains --level 6
```

Inspect a rubric and control the qualitative conditions:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke `
  --captain c08 `
  --resource-priority high `
  --crew-danger medium `
  --mission-dependency on `
  --friendly-support in-range `
  --decision-horizon soon `
  --opponent-pressure high `
  --opponent-escalating on `
  --opponent-support out-of-range `
  --show-rubric
```

Generate machine-readable data (ten records, with every rung and targets):

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --all-captains --format json
```

## Representation sequence

**Text output always starts at L10**, the full raw story with the complete captain jacket, a current encounter, categorical state statements, opponent intelligence and one question. It progresses backward through abbreviated English, X/Y semantic roles, J/K functional notation, typed categorical registers, and numerical scaffolding, ending with the teacher targets. The display renders **complete prompts**, not placeholders for omitted story text.

All eleven rungs in one case share identical rubric-generated X/Y targets, OP=SUB, and Z=X−Y. Choosing `--focus x` instead of `--focus y` changes which operand loses direct numeric help first; it does not change the targets.

Eight state fields remain entirely categorical: `resource-priority`, `crew-danger`, `mission-dependency`, `friendly-support`, `decision-horizon`, `opponent-pressure`, `opponent-escalating`, `opponent-support`. The identical CLI state can be presented to ten different captains. X is computed from **own-state** variables and the jacket-specific teaching rubric, and Y from **opponent-state** variables and that captain's inference rubric. Both range from 0 to 2,000,000. Z is signed; the endpoint-normalized logistic maps Z to [0,1] as a *preference index*, **not a calibrated probability**.

## Experimental boundaries

These are **synthetic rubric-driven targets**, not learned or empirically established judgments. The generator proves stable program → language representations and distinct jacket-conditioned deterministic outputs; it does **not** show that a trained head will generalize to unseen captains or new questions. All ten rubrics intentionally share the simplest subtraction decision algebra, to isolate inference from jacket language rather than add unnecessary OP complexity. The ten captions/questions are not an independently assessed evaluation set; later training will need whole-jacket and whole-question holdouts.

## Tests

```powershell
python -m unittest discover -s tests -p 'test_nanojev_hawk_dove*.py' -v
```

Includes earlier c01 compatibility tests and additional tests covering ten independent jackets/questions, exact raw-first display, absence of numerical targets in raw text, full 1,296-state grid × ten rubrics, rubric separation (our own state affects only X; opponent state affects only Y), and a common state producing both contest and yield among different captains.
