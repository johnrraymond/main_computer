# NanoJev V10 — X/Y lattice trainer and paired probe

Source-only additive patch for the already installed **V10 v2 deterministic teacher**.
No regenerated database or checkpoint is shipped. No V8/V9 code is replaced.

## Inputs and contract

- Verified teacher: `runtime/game-math-curriculum-v10/teacher.sqlite3`.
- Frozen representation backbone and source: `runtime/game-transform-experiment/source-v4.pt`.
- Import checkpoint: a **frozen copy** of V9 `latest.pt`, generally at or after step 245500.
- Stage coordinates: T00–T08 × `full`, `logbar_x`, `logbar_y`, `no_logbars`, `x_raw_y_full`, `y_raw_x_full`, `register_x`, `register_y`, `raw`.
- Exactly **81** supported stage coordinates (9 tiers × 9 states).
- X/Y targets come from each stored case's verified V10 `answer_json`. All presentations for that case inherit the same targets.
- The same held-out source case is evaluated across the entire lattice. Full probe: 81 stage scores, 108 intra-tier paired mutation-edge comparisons, operation breakdowns, and example failures.
- X and Y accuracy each ≥ 80%, joint X/Y accuracy ≥ 80% within a 5% relative-to-unit tolerance, at least 16 cases, all scored, and two consecutive passing post-training probes before promotion.
- An unscorable prompt counts as failure, never a correct zero prediction. `coupled_Z` is diagnostic-only, calculated from predicted X/Y with the teacher OP; Z is **not** part of training loss or advancement.
- The V9 backbone and decoder architecture remain unchanged. No independent operator-identification objective is added.

## Install

Extract the patch so that files appear under `main_computer/tools/`, `main_computer/tests/`, and `main_computer/pretty_docs/nanojev/`, **removing the leading `main_computer_test/` directory** when merging into your repository. The paths inside the zip are rooted at `main_computer_test/` by design.

Use the already completed teacher build; **do not** delete or rebuild it solely to apply this patch.

From PowerShell in `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$teacher = 'runtime/game-math-curriculum-v10/teacher.sqlite3'
$source  = 'runtime/game-transform-experiment/source-v4.pt'

& $py -m tools.nanojev_game_math_curriculum_v10_probe --teacher-db $teacher --teacher-info
& $py -m tools.nanojev_game_math_curriculum_v10_train --teacher-db $teacher --preflight-only
& $py -m unittest tests.test_nanojev_game_math_curriculum_v10_runtime tests.test_nanojev_game_math_curriculum_v10 -v
```

## Snapshot the V9 parent (important)

Do not use a moving `latest.pt` as your permanent ancestry reference if V9 training is still running. First stop V9 training or make a stable copy. This does not change the checkpoint contents:

```powershell
$v9 = "$HOME\NanoJev\runs\game_math_xy_v9\latest.pt"
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_parent.pt"
if (-not (Test-Path $v9)) { throw "V9 trained checkpoint missing: $v9" }
if (Test-Path $parent) { throw "Parent snapshot already exists: $parent" }
Copy-Item -LiteralPath $v9 -Destination $parent -ErrorAction Stop
```

The new lineage validates the parent/source hashes and refuses silent drift.

## Baseline probe (T00–T02 first)

```powershell
& $py -m tools.nanojev_game_math_curriculum_v10_probe `
  --teacher-db $teacher `
  --source-checkpoint $source `
  --checkpoint $parent `
  --tiers T00 T01 T02 `
  --split dev --limit 48 `
  --output "$HOME\NanoJev\runs\game_math_xy_v10\pretrain-T00-T02.json" `
  --device cuda
```

That partial report scores 27 states and their available intra-tier edges. `first_failing_in_selection` applies to the selected stages only; no global advancement claim is made.

For a complete pretraining baseline (all 81 states), omit `--tiers` and change `--output` to a different filename. The trainer **also** performs an all-stage baseline automatically before its first optimizer step.

## Train a new V10 lineage

```powershell
& $py -m tools.nanojev_game_math_curriculum_v10_train `
  --teacher-db $teacher `
  --source-checkpoint $source `
  --parent-checkpoint $parent `
  --output "$HOME\NanoJev\runs\game_math_xy_v10" `
  --steps 1000 `
  --chunk-steps 250 `
  --batch-levels 4 `
  --probe-limit 48 `
  --device cuda
```

`--steps` means **additional V10 optimizer steps since parent import**; not the absolute step number recorded by V9. For example, parent step 245500 and `--steps 1000` yield final absolute step 246500. For another 1,000 steps, rerun the same command with `--steps 2000`; this resumes V10 optimizer and RNG state, not V9.

Outputs are saved in the new V10 run folder:

- `baseline-probe.json` — entire 81-state baseline.
- `probe-step-000XXXXXX.json` — all-stage post-chunk gate plus X/Y paired-edge diagnostics.
- `latest.pt` — V10 decoder, frozen gamehead, fresh V10 AdamW optimizer state, RNG states, lineage and gate state.
- `v10-config.json` — immutable configuration, source, parent and teacher SHA-256 hashes.

**Each chunk probes all 81 stages.** That is intentionally more compute-intensive than V9's nine native tiers; use `--probe-limit 24` for an initial engineering pilot (requires at least 16) but retain 48 for the progression gate intended here.

An unscorable stage due to maximum token length remains a failed gate. Do not silently truncate its prompts or exclude it from the denominator.

## Live read-only probe while training

The training process writes each `probe-step-*` file automatically. In another PowerShell terminal, manually probe the last **fully written** `latest.pt`:

```powershell
& $py -m tools.nanojev_game_math_curriculum_v10_probe `
  --teacher-db $teacher `
  --source-checkpoint $source `
  --checkpoint "$HOME\NanoJev\runs\game_math_xy_v10\latest.pt" `
  --tiers T02 `
  --limit 48 --split dev `
  --output "$HOME\NanoJev\runs\game_math_xy_v10\manual-T02-probe.json" `
  --device cuda
```

If training holds the GPU exclusively, prefer inspecting the trainer-generated report instead of running competing inference jobs.

## Verification scope and limitations

Automated CPU tests exercise: teacher schema, 81-stage topology, exact-oracle probe, isolated Y mutation regression, token-overflow failure, advancement/fallback, reproducible sampling, V9-shaped checkpoint import and provenance, and a mocked-gradient V10 training/resume through a saved optimizer checkpoint. The existing V10 deterministic teacher tests and focused V9 tests also run unchanged.

**Not claimed:** a real Qwen GPU training step, actual model accuracy gains, complete historical legacy suite, or exhaustive production-database revalidation from this container (the production SQLite and actual trained weights remain on the user's workstation). The first workstation CUDA baseline is the required neural integration test.

### Quick troubleshooting

`V10_PARENT_CHECKPOINT_MISSING`: snapshot and provide `--checkpoint`/`--parent-checkpoint` as shown above.

`V10_CONFIG_DRIFT_CANNOT_RESUME`: new teacher, different source/parent, or changed hyperparameters; create a different V10 run directory rather than overwrite an existing lineage.

`V10_TEACHER_SCHEMA_MISMATCH`: V9 teacher was supplied instead of the verified V10 teacher.

`V10_NO_TOKEN_ELIGIBLE_TRAINING_CASES`: representation exceeds the frozen head's prompt-token budget; fix the model/context contract explicitly before training that stage.
