# NanoJev MC3 — T00–T08 read-only mathematical transformation probe

## Purpose

Compare the **step-104,500 source V4 checkpoint** against the **step-20 isolated MC3 experimental checkpoint** on the **same verified mathematical cases**, without training, resealing, writing checkpoints, or changing the source-generated SQLite database. This is the follow-up to the Hawk–Dove Space Captain B00–B14 bridge probe.

**Invariant:** Database oracles determine gold outputs but never produce predictions. Both checkpoints must independently infer X/Y using the existing Qwen3-0.6B capture and V4 decoder; the saved nine-class game operator head is evaluated separately. No optimizer is instantiated in this probe.

The probe is additive: it requires `tools.nanojev_game_transform_experiment` from the integrated MC3 patch. It does not replace that module, touch V4 trainer files, or regenerate databases.

## Windows PowerShell: install and run

From `C:\Users\subsi\main_computer`, after placing the ZIP in the repository root:

```powershell
python new_patch.py .\nanojev_game_transform_levels_mc3_patch_v1.zip --dry-run
python new_patch.py .\nanojev_game_transform_levels_mc3_patch_v1.zip

$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_game_transform_level_probe -v

# Small read-only diagnostic across dev, test and all five transfer games:
& $py -m tools.nanojev_game_transform_level_probe `
  --database .\runtime\game-transformation-experiment\game_transformations.sqlite3 `
  --source-checkpoint .\runtime\game-transform-experiment\source-v4.pt `
  --experimental-checkpoint "$HOME\NanoJev\runs\game_transform_experiment_v1\latest.pt" `
  --experiment-config "$HOME\NanoJev\runs\game_transform_experiment_v1\experiment-config.json" `
  --device cuda --dtype float16 `
  --limit-per-split 12 `
  --report .\runtime\game-transform-experiment\levels-12.json
```

A larger sample (qualification requires **at least 30 fully scorable distinct cases** at a level, with >=80% paired joint accuracy at 5% and >=80% operator accuracy for the respective qualifications):

```powershell
& $py -m tools.nanojev_game_transform_level_probe `
  --device cuda --dtype float16 `
  --limit-per-split 48 `
  --report .\runtime\game-transform-experiment\levels-48.json
```

The **T07 composition** dev/test holdouts contain fewer than 30 cases in this database; they remain diagnostic until the mathematical corpus is expanded. Some levels on scalar games are inapplicable because the V4 native register renderer accepts only integers: these retain exact noninteger targets at T05–T08 and are reported as `unsupported_noninteger_native` at T00–T04. Nothing is rounded or silently dropped. Increasing `--limit-per-split` does not create additional cases.

The report includes checkpoint paths/steps/SHA256, database SHA256, experiment config identity, case manifests, every prompt hash, token count, target and raw prediction for each orientation, 5% and 10% X/Y/strict-paired accuracy, operator accuracy, changed/unchanged target subsets, per-game/per-operator scores, and before/after differences. `--report` is optional. Without it, the tool writes no files. The tool verifies hashes are unchanged after inference.

## Levels

| Level | Information supplied | Isolated capability |
|---|---|---|
| T00 | Exact `mix_v4.curriculum_prompt` native `XXXXXX`/`YYYYYY`/LogBar (both values explicit) | Control: exact trained format |
| T01 | T00 plus operator name | Operator-label interference |
| T02 | T00 plus source game and transformation definition | Game-context interference |
| T03 | Exact X register, Y hidden, mathematical game and operator provided | Compute transformed Y with X known |
| T04 | Exact Y register, X hidden, mathematical game and operator provided | Reconstruct original X with Y known; **not necessarily a mathematical inverse** |
| T05 | Formal game source and transformation, both target registers hidden | Two-operand extraction and execution |
| T06 | Narrative-wrapper game text, including the JSON/math scaffold from integrated MC3 | Representation transfer beyond native registers (not yet a fully raw story) |
| T07 | Actual two-step `compose_transformations` cases, with separate composition sampling | Order-sensitive composition; sample cohort differs from T00–T06 |
| T08 | T06-style prompts for wholly withheld games | Whole-game transfer across reserved game IDs |

T00–T06 use identical canonical case IDs for a given split; T07 draws its own reserved composition cases so the result is not accidentally `n=0`; T08 reuses the `game_transfer` T06 canonical cases for an explicit, separately named transfer gate. All have forward and reordered presentation variants with **strict** paired success (both must pass). T00–T02 explicitly supply the answers and are *controls*, not learned-transformation results.

The default selection excludes projections where the sampled output happened not to change (`X == Y`). Pass `--include-unchanged` to audit those separately; the report always separates changed vs. unchanged cases. A stratified, deterministic selection balances game and operator coverage. In particular it samples all five reserved game IDs when adequate cases exist, rather than allowing one alphabetically early game to dominate.

## Reading results

- `paired.x`, `paired.y`, and `paired.joint` are distinct; compare T03 Y and T04 X, rather than accepting a misleading aggregate.
- `operator_paired` is the independent nine-class game operator head, *not* the V4 arithmetic OP side-channel. A model may read X/Y while failing to classify the operator.
- `joint_and_operator` combines both requirements.
- `original/reordered` accuracy never substitutes averaged predictions for strict paired success.
- Overlength or otherwise unsupported native-format cases are `unscorable`, not counted as wrong predictions. All prompts are tokenized against the checkpoint's trained `max_prompt_tokens`; no silent truncation.
- A score on T00–T02 is not proof of transformation execution because both gold operands appear in the prompt. T03–T08 supply the mathematical problem, never the target answer.
- `qualification` flags are purely numerical screening thresholds, not proof of cross-family cognition.

## Known boundaries

The current MC3 mathematical teachers are deterministic, but the scalar projection of a transformation is only one output coordinate. A success here does not establish transformation of an entire payoff tensor or a Nash equilibrium set. Some MC3 narrative prompts still contain JSON payoff tables and formulas; they are not unscaffolded story problems. This is intentionally a **read-only diagnostic**, not a training patch. CPU mocks certify pipeline wiring only; your actual checkpoints must be evaluated locally on CUDA.

For a complete proof of input immutability, SHA256 checksums of source/experimental checkpoints and SQLite are taken before inference and verified again before saving the report.
