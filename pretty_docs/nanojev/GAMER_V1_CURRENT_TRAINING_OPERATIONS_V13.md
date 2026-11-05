# Gamer v1 — current training operations (v13, 2026-11-04)

This is the **current runnable guide** for `tools.nanojev_gamer_v1_handoff` in the 2026-11-04 source snapshot. The earlier v5/v6 design documents and the retired blind-matrix experiment are preserved for provenance, but their commands are **not** the current training procedure. The architecture and promotion requirements remain in `GAMER_V1_TRAINING_CUTOVER.md`, `GAMER_V1_STAGE_CONTRACTS.md`, and `GAMER_V1_RELEASE_GATES.md`.

## What is implemented—and what is not

| Curriculum selection | CLI flag on `prepare` and `lora` | Named entries | Total examples (18 per entry) | Train / dev / test | Table formats |
|---|---|---:|---:|---|---:|
| Source-verified | no expansion flag | 33 | 594 | 396 / 99 / 99 | 3 |
| Named catalog objectives | `--include-catalog-extensions` | 91 (33 + 58) | 1,638 | 1,092 / 273 / 273 | 3 |
| Catalog + state-transition objectives | `--include-transition-games` | **146 (33 + 58 + 55)** | **2,628** | **1,752 / 438 / 438** | **4** |

The four reusable table families are **strategic payoff**, **calculation**, **probabilistic outcomes**, and **state–action–transition–reward**. A target contains populated input factors and blank calculated results; it is **not** a supplied numerical answer. The 58 and 55 additions are **bounded named mathematical objectives**, not full-game solvers, equilibrium proofs, or a demonstration of neural generalization. `prepare`/`validate` check generated records and source-specified bounded oracles; they do not demonstrate LoRA competence. The 55 transition cases are one-step; they do not supply a multi-step planning engine.

The anonymous random-game curriculum was retired. `--blind-matrix-per-game` must remain zero (nonzero raises `GAMER_BLIND_MATRIX_RETIRED`), and the `blind-probe` stage now refuses execution. Ordinary deterministic parameter variation **inside named and oracle-defined problems** remains part of the curriculum.

**Governing two-branch invariant:** both the LoRA translation branch and the later C0 Wide `to_numerical` branch consume the **same original human-readable *08 prompt**. LoRA is supervised to emit a mathematical table; `to_numerical` uses frozen original-prompt hidden activations, **never** the generated or oracle table. Training progress or small probes do not certify the numerical head or production release.

## Prepare and certify the 146-entry corpus

From the Main Computer repository root in PowerShell, with `$py` set to the intended Python interpreter:

```powershell
& $py -m tools.nanojev_gamer_v1_handoff prepare `
  --output runtime\reports\gamer-v1\transition-005 `
  --include-transition-games

& $py -m tools.nanojev_gamer_v1_handoff validate `
  --output runtime\reports\gamer-v1\transition-005 `
  --include-transition-games
```

Expected default distribution: **2,628 certified examples**, with 1,752 train, 438 dev and 438 test. `--include-transition-games` also includes the 58 earlier catalog entries. `prepare` is for a **new** output directory; do not rerun it against a populated training manifest or regenerate an existing dataset in place.

## Transfer trained weights and begin additional steps

The following example reflects the observed `catalog-004` → `transition-005` handoff. The user's source checkpoint contained **step 795**; the target count **3000 is cumulative**, so the new optimizer begins with **step 796**, not step 1. Change the source run name only if your actual verified source is different.

```powershell
& $py -m tools.nanojev_gamer_v1_handoff lora `
  --output runtime\reports\gamer-v1\transition-005 `
  --include-transition-games `
  --init-lora-from runtime\reports\gamer-v1\catalog-004 `
  --init-lora-checkpoint latest `
  --lora-steps 3000 `
  --lora-eval-every 200 `
  --max-new-tokens 2048 `
  --device cuda
```

**Why 2048?** The CLI default is **512 generated tokens**, and the v13 state-transition table targets can exceed that. On the user's first attempt, the model imported source step 795 but the bounded-probe preflight raised `GAMER_PROBE_TARGET_EXCEEDS_MAX_NEW_TOKENS` **before saving an initial checkpoint or taking an optimizer step**. `--max-new-tokens 2048` raises the ceiling; the bounded probe determines each generation's limit from its particular target plus a margin, not a mandatory 2,048 generated tokens per case. If a different corpus ever exceeds the cap, use the reported preflight to select a sufficient value **before** starting training. A genuine generation truncation must not be ignored.

The source `catalog-004/lora/latest.pt` must be present, compatible and **not** awaiting evaluation. `latest` imports the weights of the actual last saved optimizer step, not the possibly older `best_adapter` selection. The prior adapter and source checkpoint are not overwritten. **Changing the dataset starts a fresh optimizer** even though LoRA weights and cumulative step numbers are preserved. This is weight continuation, not optimizer-state continuation across datasets.

An explicit `--init-lora-from` **skips the expensive fresh-base full-dev baseline**, saves the imported adapter as the new run's initial checkpoint and uses bounded development probes (default 17 cases: six fixed anchors plus rotating coverage). Small probes are monitoring only. They are **not** full retention, unseen-game generalization or full test-set certification. `best_adapter` selection is based on the fixed-anchor score; when anchors tie, the earlier best remains selected, even if the latest step scores better on rotating cases. Preserve and compare `best_adapter` and `latest.pt` rather than assuming “best” is the latest model.

## Resume after interruption; extend the same curriculum beyond the target

If `transition-005/lora/latest.pt` exists, repeat the original command with **exactly the same run, dataset, model, transfer flags and frozen hyperparameters** and add `--resume`. In particular, retain `--max-new-tokens 2048` and `--lora-eval-every 200`; do not switch back to the 512-token default:

```powershell
& $py -m tools.nanojev_gamer_v1_handoff lora `
  --output runtime\reports\gamer-v1\transition-005 `
  --include-transition-games `
  --init-lora-from runtime\reports\gamer-v1\catalog-004 `
  --init-lora-checkpoint latest `
  --lora-steps 3000 `
  --lora-eval-every 200 `
  --max-new-tokens 2048 `
  --device cuda `
  --resume
```

After a run **finishes**, raise the cumulative target, for example `--lora-steps 4000`, and keep `--resume`. This retains the **same dataset and optimizer state**, archives the prior completed report, and advances from step 3000 to 4000. If an experiment has already handed its adapter to the numerical head (`handoff.json` or a `to_numerical` report), in-place extension is refused: create a new run to preserve the frozen inference contract.

If the **first** attempt fails at `GAMER_PROBE_TARGET_EXCEEDS_MAX_NEW_TOKENS` and **no** `transition-005/lora/latest.pt` exists, correct `--max-new-tokens` and run **without `--resume`**. If a checkpoint already exists, changing the token ceiling mid-run violates resume lineage; use the original ceiling or start a fresh output directory with explicit weight transfer.

```powershell
Test-Path "runtime\reports\gamer-v1\catalog-004\lora\latest.pt"
Test-Path "runtime\reports\gamer-v1\transition-005\lora\latest.pt"
```

A `True` confirms the file exists, **not** that the state is complete/non-pending or that the model passes acceptance. The trainer checks checkpoint provenance, dataset fingerprint, model identity, and frozen options on import/resume.

## Experimental numerical handoff while LoRA is still active (v15)

A completed LoRA run and final source `lora/report.json` are **not required** for a frozen, explicitly experimental numerical experiment. Use `switch --snapshot-lora-from RUN --snapshot-step N --output NEW_RUN --include-transition-games --allow-experimental` to copy the **currently selected, fully evaluated** best adapter into a separate immutable run with its own certified dataset and lineage. This leaves `transition-005` training uninterrupted and does **not** place `handoff.json` in the active source run. See [GAMER_V1_ACTIVE_LORA_NUMERICAL_HANDOFF_V15.md](GAMER_V1_ACTIVE_LORA_NUMERICAL_HANDOFF_V15.md) for exact commands, safety checks, and the subsequent `to-numerical` / `probe` steps.

The selected `--snapshot-step` must still match the active source's best step; a later best-adapter replacement is **never** silently substituted. The snapshot reports `EXPERIMENTAL_NOT_PROMOTED` and does not turn a bounded development probe into full-game certification.


## Reversible, alternating LoRA / C0 Wide training (v16)

For stopping LoRA, training `to_numerical` for a chosen number of steps, resuming the **same LoRA optimizer**, and then continuing the **same head weights** against the updated LoRA, use the permanent `tools.nanojev_gamer_v1_staircase` commands (`head`, `lora`, and `status`). Its numerical-head cycles are isolated under `RUN/staircase/`; **they do not create `RUN/handoff.json`** or block continuing the source LoRA. A newer LoRA invalidates cached original-prompt activations, so the next head leg recaptures them and uses a fresh optimizer with previously learned head weights. See [GAMER_V1_STAIRCASE_TRAINING_V16.md](GAMER_V1_STAIRCASE_TRAINING_V16.md) for operational commands and gates. The v15 immutable one-off snapshot remains a different, valid workflow.

## Acceptance before promoting the result

Inspect `transition-005/lora/report.json`, its `history`, the durable evaluation files, and checkpoint lineage. Low training loss and a 17-case probe are insufficient to prove cross-game transfer. Evaluate the **same held-out cases** across candidate checkpoints, and check per-game/per-format retention, especially the newly added state-transition tasks. The complete 438-case dev/test splits are not automatically certified by the 17-case bound. The `to_numerical` branch and release gates remain separate work; `handoff_eligible=false` and `EXPERIMENTAL_NOT_PROMOTED` must not be represented as passing release status.

For the exact mathematical contracts and game crosswalks, see `GAMER_V1_CATALOG_58_BOUNDED_CROSSWALK.md` and `GAMER_V1_FOURTH_STATE_TRANSITION_TABLE_V13.md`. This document does not authorize the retired blind-matrix generator or synthetic labels.

## Numerical head error geometry (v18)

The permanent `python -m tools.nanojev_gamer_v1_diagnostic --run PATH` command also analyzes any saved staircase C0 Wide head **without Qwen/GPU**. It evaluates the cached original-prompt features against the head's exact latest and best weights on the entire dev/test decks. In addition to per-game results it reports signed distance from zero, zero-line residual when present, absolute-error tails, numerical scale, sign accuracy, and the cross-regime error map. `--atlas-cycles all` compares saved staircase legs and `--atlas-csv PATH_OUTSIDE_RUN` exports per-case evidence. See `GAMER_V1_STAIRCASE_TRAINING_V16.md` for full usage. New head training automatically records JSON/CSV atlases at each evaluation step and final selection; pre-v18 head cycles need no retraining to be diagnosed.
