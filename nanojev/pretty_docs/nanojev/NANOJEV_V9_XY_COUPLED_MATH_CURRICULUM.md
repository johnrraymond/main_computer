# NanoJev V9 — XY-first mathematical curriculum + coupled-Z diagnostic

## Purpose and provenance

V9 is an **isolated fork of the current Qwen numerical-head training exercise**, not a replacement for NanoJev's V2.1 lineage and not a trained model. It consumes the source-regenerated **V8 deterministic native-game teacher** and the **T03 V2.1 parent decoder**, uses supervised X/Y regression, and reports an independent diagnostic Z computed by running the *teacher's known operation* on the *model's predicted X/Y*. Z is **not** included in the training loss.

The verified native teacher admits these T00 operations: `ADD`, `SUBTRACT`, `MULTIPLY`, `DIVIDE`, `MAX`, `ARGMAX`. V9 does not pretend the older nine-class game-operator classifier has learned these six mathematical operations. OP identification is a separate future objective; all coupled-Z scores are **teacher-OP-conditioned**.

## Execution contract

- `build` reads native V8 `native_v6_cases`/`native_v6_tiers` from a read-only source database. For every selected source question and variant, it executes V8 seeded level-local checkpoints from T08 through T00, then invokes the independent native oracle **after** T00 to verify X/Y/OP/Z. It saves all nine post-checkpoint prompts, split, source identity, and exact fraction-encoded mathematical answers to a *new* SQLite file. It will not overwrite an existing teacher database.
- `probe` computes model X/Y at T00–T08 and T03 V2.1 sublayers. For each native tier, it reports X, Y, joint X/Y within 5%, plus **Z from OP(predicted X,predicted Y)** and operation-family breakdown. Diagnostic Z is not fed back into the model. Legacy T03 subtiers use the existing `SUBTRACT` operation and separately generated oracle cases. T03i uses held-out `game_transfer` evidence in the **original** game-transformation database; missing evidence blocks advancement.
- `train` forks the V2.1 decoder into `game_math_xy_v9`, leaves the V2.1 parent file and its source checkpoint untouched, and trains on teacher prompts plus earlier-tier/legacy-subtier retention. The previously trained game-operation classifier is frozen unchanged (it cannot classify new six-OP labels). This **only** updates the X/Y attention decoder. Saved checkpoint, optimizer, and PRNG state are tied to source, teacher, legacy database, parent, and generator hashes; resume rejects drift.

## Advancement rule

For X and Y independently, the error used is `abs(pred-truth) / max(1, abs(truth))`. A value of zero therefore has a 0.05 absolute tolerance. Each reported held-out tier requires:

- X success fraction **at least 0.80** within 0.05 relative error;
- Y success fraction **at least 0.80** within 0.05 relative error;
- **joint X-and-Y** fraction at least 0.80, not merely two unrelated 0.80 marginals;
- at least 16 held-out examples, all scorable, and **two consecutive passing probes** before training focus moves upward.

This makes the interpretation of the user's proposed 80%-within-5% target operational. `coupled_Z` is diagnostic only; it is **not** a promotion gate. Missing lower tiers and missing T03i transfer cases block promotion. All levels can be probed independently; new T08.5 mutations remain withheld and are not exported into this supervised exercise.

## Run on Windows PowerShell (from main_computer)

First install V8 and V9, and regenerate V8's game library if it doesn't already exist:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m tools.nanojev_game_math_smoke --generate --verify-all --report
```

Now build the teacher entirely from source and a fixed seed:

```powershell
& $py -m tools.nanojev_game_math_curriculum_v9 build `
    --native-db runtime/game-math-source-smoke-v8/game_transformations.sqlite3 `
    --teacher-db runtime/game-math-curriculum-v9/teacher.sqlite3 `
    --seed 20261017 --variants 1
```

Locate the *actual latest* V2.1 stage checkpoint, not a guessed stage-00 checkpoint:

```powershell
$stages = Get-ChildItem "$HOME\NanoJev\runs\game_transform_t03_tiered_v21" -Directory -Filter 'stage-*' |
    Where-Object { Test-Path (Join-Path $_.FullName 'latest.pt') } |
    Sort-Object Name -Descending
$parent = Join-Path $stages[0].FullName 'latest.pt'
```

Probe the current head without changing it:

```powershell
& $py -m tools.nanojev_game_math_curriculum_v9 probe `
    --teacher-db runtime/game-math-curriculum-v9/teacher.sqlite3 `
    --legacy-db runtime/game-transformation-experiment/game_transformations.sqlite3 `
    --source-checkpoint runtime/game-transform-experiment/source-v4.pt `
    --parent-checkpoint $parent --probe-limit 48 --device cuda
```

Create a **new V9** training run, selecting a target `--steps` **greater than** the step stored in `$parent`:

```powershell
& $py -m tools.nanojev_game_math_curriculum_v9 train `
    --teacher-db runtime/game-math-curriculum-v9/teacher.sqlite3 `
    --legacy-db runtime/game-transformation-experiment/game_transformations.sqlite3 `
    --source-checkpoint runtime/game-transform-experiment/source-v4.pt `
    --parent-checkpoint $parent `
    --output "$HOME\NanoJev\runs\game_math_xy_v9" `
    --steps 222000 --chunk-steps 250 --probe-limit 48 --device cuda
```

Probe a saved V9 checkpoint using `--experimental-checkpoint "$HOME\NanoJev\runs\game_math_xy_v9\latest.pt"` in addition to the same source/teacher/legacy/parent/seed/training-config arguments used to create it.

## Verification and limitations

Source-only patch: one Python runtime, one CPU-only test module, and this documentation. The native training-source database is **regenerated**, never included as a binary. Tests check fraction encoding, coupled-Z pred-vs-gold separation, lossless replay, gate threshold, source split preservation, no overwrites, and byte-identical teacher rebuilds.

No GPU/model checkpoint or live user's training artifacts are available in the build environment. Consequently the full training run and its real numerical accuracies **remain unmeasured**; V9 asserts neither 80% attainment nor neural generalization. For completed native V8 proofs, teacher generation has run on a full-size 1,136-case fixture (**10,224 prompts**) and two cold builds were byte-identical. These are **teacher** proof results, not model results.
