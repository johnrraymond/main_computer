# Gamer v1 — reversible LoRA / `to_numerical` staircase (v16 workflow, v17 pending-evaluation correction)

This is the current, permanent **alternating-training** workflow for an existing verified Gamer v1 run. It supersedes the v15 one-time active snapshot workflow **when you want to return to LoRA training and later refine the same C0 Wide numerical head**. The older v15 snapshot remains supported for independent frozen experiments.

There is **one logical source LoRA run** (`transition-005` in these examples) and a sequence of versioned numerical-head cycles **inside that run's `staircase/` folder**. Source LoRA training is neither finalized nor permanently frozen when the head trains. Pause one trainer with Ctrl+C before starting the other; do not run both on the same GPU concurrently.

**Invariant:** LoRA sees original `prompt_08` and learns to emit the mathematical tables. C0 Wide sees only hidden activations of **the same original `prompt_08`**, with the certified numeric answer as target. No generated table, oracle table, or completion tokens enter `to_numerical`.

## Inspect current step (read-only)

```powershell
& $py -m tools.nanojev_gamer_v1_staircase status `
  --run runtime\reports\gamer-v1\transition-005

& $py -m tools.nanojev_gamer_v1_diagnostic `
  --run runtime\reports\gamer-v1\transition-005
```

The permanent unified diagnostic reports original LoRA probes **and all numerical-head cycles**, including their source LoRA steps and numerical MAE when available. Neither tool loads Qwen or modifies training files.

## Phase A — stop the LoRA, train the head to a cumulative step target

After stopping the original LoRA process with Ctrl+C, you may switch to the head **even when the last LoRA checkpoint has `pending_eval=True`**. This means the optimizer step is fully committed, but the development probe at that step is unfinished. The staircase snapshots the exact current adapter tensors without changing the original LoRA checkpoint or its per-prompt evaluation progress. It labels the head snapshot as experimental and records the source's pending-evaluation status. **Stop the LoRA process before switching**; checkpoint status alone does not establish process liveness.

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head `
  --run runtime\reports\gamer-v1\transition-005 `
  --steps 200 `
  --device cuda
```

The first `head` call freezes the **exact latest LoRA checkpoint tensors** into `transition-005/staircase/head_cycle_0001/`, not the possibly older `best_adapter`. The frozen copy is local to this numerical training segment; it **does not freeze or write to** the source LoRA run. It fingerprints the certified dataset and LoRA weights. It then trains the existing C0 Wide head for steps 1–200, saving optimizer and RNG progress every step by default.

If Ctrl+C interrupts head training, **rerun the same command**. It resumes that cycle from the last committed optimizer step. Feature captures are cached only within that precise frozen LoRA and original-prompt lineage, so a resume need not recapture an unchanged split. If interruption happens before the first head checkpoint, retrying the same command safely repeats initialization.

## Phase B — resume the original LoRA optimizer

```powershell
& $py -m tools.nanojev_gamer_v1_staircase lora `
  --run runtime\reports\gamer-v1\transition-005 `
  --steps 1600 `
  --device cuda
```

`--steps` is the **total cumulative optimizer step** and normally must exceed the current saved LoRA step. If the source checkpoint has `pending_eval=True`, the same step target is also accepted: it completes the outstanding development evaluation without advancing optimizer steps. For example, when the source is paused at step 1600 with an unfinished evaluation, `lora --steps 1600` completes that evaluation; `lora --steps 1800` completes it and then continues optimization. The source checkpoint, optimizer and RNG are reused. The command recovers the source's original dataset, flags (including `--max-new-tokens 2048`), transfer provenance, exact LoRA optimizer, RNG, and training history. It **does not** reset LoRA and does **not** touch the previous numerical-head cycle.

If this stops before its target, the next `lora --steps HIGHER_NUMBER` continues from its saved checkpoint. If it reaches the target, it archives the completed report automatically when later extended. An interrupted evaluation is recovered by the existing LoRA resume mechanism.

## Phase C — return to the same numerical head after LoRA changes

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head `
  --run runtime\reports\gamer-v1\transition-005 `
  --steps 400 `
  --device cuda
```

When source LoRA weights have changed, this opens `head_cycle_0002`, snapshots the **new** LoRA step, **imports the actual latest numerical-head weights from cycle 1**, and starts at step 201. It **re-extracts hidden activations from the updated LoRA** (the previous feature cache is not valid). Because the input feature distribution changed, it initializes a fresh numerical-head optimizer while preserving the learned head parameters. Its new best/dev score starts as a new comparison on the new LoRA distribution. The prior cycle and evaluation records remain intact.

If LoRA has **not changed**, requesting a higher head target extends the same cycle and restores its exact optimizer state instead. Finishing a previously pending LoRA evaluation changes checkpoint metadata, but **does not change the adapter weights**: the staircase compares adapter tensor fingerprints and reuses the existing head cycle rather than recapturing identical features. This is the difference between a same-representation continuation and a cross-representation warm start.

Repeat `lora --steps N` / `head --steps M` for as many staircase segments as desired. Step targets are cumulative within each respective trainer. **Do not reuse the source LoRA `best_adapter` as a proxy for `latest.pt`; they may differ.**

## Storage and gates

- `RUN/lora/latest.pt`: original LoRA optimizer, RNG, exact current weights; untouched by the head cycles.
- `RUN/staircase/head_cycle_0001/lora/best_adapter/`: frozen **latest-step** LoRA tensors used for the first numerical-head segment.
- `RUN/staircase/head_cycle_0001/to_numerical/latest.pt`: resumable exact head weights/optimizer/RNG for that cycle.
- `RUN/staircase/head_cycle_0002/...`: re-frozen newer LoRA, new prompt features and continued head parameters.
- `RUN/staircase/head_cycle_*/to_numerical/feature_cache/*.npz`: identity-checked per-split original-prompt features; never copied between LoRA versions.
- `RUN/staircase/head_cycle_*/to_numerical/wide.pt` and `report.json`: evaluated **best within that cycle**; the next staircase leg intentionally continues from `latest.pt`, not the earlier best-selected checkpoint.

A short LoRA table-generation probe is not numerical-head accuracy. Use the same dev/test cases to compare numerical MAE and exact accuracy across cycles, and separately audit original games, bounded catalog entries and transition games before any production claim. These staircase snapshots are **experimental**, not full-retention certifications. Training must be stopped manually before phase switching; the tool cannot reliably determine Windows process liveness from a checkpoint timestamp alone.

Existing `tools.nanojev_gamer_v1_handoff` stages and v15 one-off snapshots remain available for other tasks; the staircase is a permanent higher-level workflow built on those original implementations, not a replacement C0 model.

## Numerical error atlas (v18)

The unified **Gamer diagnostic** now reports the *geometry of numerical error*, not just exact accuracy or global MAE. It works against the already saved staircase cycle, including **older, pre-v18 head checkpoints**; it reevaluates the stored head weights on the original-prompt feature cache **on CPU**, without loading Qwen or modifying the active LoRA/head run.

```powershell
& $py -m tools.nanojev_gamer_v1_diagnostic `
  --run runtime\reports\gamer-v1\transition-005
```

Default: inspect the latest head cycle, both *latest* and *best selected* head weights, and the complete development and test splits. The original LoRA diagnostic remains intact. Each numerical report has disjoint signed answer bands (negative/positive; 0, 0–0.1, 0.1–1, 1–2, 2–10, 10–100, 100–1000, greater than 1000), an absolute-error distribution, separately measured `zero_line_residual` distances, error tails, per-table and curriculum breakdowns, and representative largest misses.

The **numerical target's distance from zero** and the optional **independently recorded zero-line residual** are separate coordinates; the report never assumes they coincide. Every residual bin states its included and excluded case count. Numerical accuracy is measured from *unseen dev/test examples*, not the loss on selected training microbatches. The sign rate handles exactly zero targets with a documented 1e-6 prediction tolerance; the report also exposes MAE, median, p90, p99, RMSE, signed bias, median absolute deviation, robust variance of signed-log numerical error, absolute tolerances and relative error on nonzero targets. A global MAE cannot conceal large failures in particular regimes: each regime has a count and distribution.

For complete historical and per-case inspection, run this **same permanent diagnostic**, not a temporary one-off script:

```powershell
& $py -m tools.nanojev_gamer_v1_diagnostic `
  --run runtime\reports\gamer-v1\transition-005 `
  --atlas-cycles all `
  --atlas-csv runtime\reports\gamer-v1\staircase-atlas-export
```

The CSV directory must be *outside* the active Gamer run. A CSV is generated for every selected cycle, head checkpoint (`best` and `latest`) and split (`dev` and `test`). Each row records the exact certified case identity, game, table families, provenance, oracle target, model prediction, signed/absolute/normalized errors, answer-distance bin, independent zero-line-residual bin where present, sign correctness and error-severity bin. `--json` exposes all aggregated cells, including answer-band × error-band, residual-band × answer-band, game × distance zone and table × distance zone. `--atlas-split dev`, `--atlas-weights best`, or `--no-error-atlas` narrow or disable the CPU analysis as needed. `--watch 60` also remains supported.

**New v18 head training** persists complete atomic JSON and CSV error atlases under `staircase/head_cycle_NNNN/to_numerical/error_atlas/` at each scheduled development evaluation and for the selected best development and test checkpoints. These files record their source LoRA SHA, dataset SHA, head step, exact case identities and numerical errors. Previous cycles remain valid even if they have no such files: the diagnostic reconstructs the measurements directly from the old feature cache and exact saved weights. These metrics add observability only; they do **not** change the head objective, architecture, sampler, optimizer, LoRA weights, checkpoint format, or best-step selection.

**Evidence caution:** an error band with only a few examples does not demonstrate broad accuracy. Small MAE does not imply exact arithmetic; reported tolerances, signs, tails and near-zero residual behavior must be checked independently.

## Focusing the existing numerical head on the small-magnitude zero-line

This training-corpus sampler is implemented as **options to `head`**, not a new
head architecture or dataset. For a run already at head step 600 with LoRA
step 1800, continue the same head weights, optimizer and frozen adapter:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head `
  --run runtime\reports\gamer-v1\transition-005 `
  --steps 2000 `
  --device cuda `
  --zero-line-focus 0.75 `
  --zero-line-radius 1.0
```

`--steps` remains **cumulative**. With `--zero-line-focus 0.75`, approximately
75% of minibatch draws are from three equal-priority target strata:
**exact zero**, **−1 ≤ answer < 0**, and **0 < answer ≤ 1**. Within each stratum,
first choose an eligible named game uniformly and then a certified training
case uniformly from that game. This avoids a large game's many examples
swamping a smaller game. The remaining 25% are drawn uniformly from the
**complete original training split**, including games without small-target
examples. The fractions describe sampling probabilities, not exact quotas for
each minibatch.

The radius is configurable and the focus can be any finite value from 0 to 1.
`--zero-line-focus 0` restores the original uniform sampler and exact NumPy RNG
draw order. Nonzero focus requires at least one certified training example in
each of the three strata; absence is a fail-closed error. A focus of 1 has no
full-corpus replay, so 0.75 is the recommended starting point for preserving
previously learned ranges. **No new cases, labels, prompts or oracle values are
generated**. If some games have no existing target in the chosen radius,
coverage is explicitly reported rather than falsely claimed.

The active focus and radius, sampling history, class draw counts, per-game draw
counts, and eligible/missing games are saved in the head checkpoint and final
report. The full held-out dev evaluation still runs on the unchanged 438 cases.
The evaluation records separate target-zero, small-negative, small-positive
and outside-radius error and sign measures; independently recorded
`zero_line_residual` values are **not** silently substituted for output targets.

The options can be changed **only at a committed optimizer-step boundary**.
For an existing head checkpoint the new policy is checkpointed before training
the next step, with its `start_step` recorded in `sampling_history`. Interrupting
and rerunning resumes the saved optimizer and NumPy RNG and keeps the policy.
Omitting the flags on a later `head` command **inherits the committed policy**;
specify `--zero-line-focus 0` to return explicitly to uniform sampling. These
flags are refused for `status`, `lora` and non-head handoff stages. The C0 Wide
layers, Qwen feature capture, LoRA weights, loss, held-out splits, and original
aggregate-MAE best-checkpoint criterion are unchanged. This is an experimental
training-focus control, not automatic zero-line certification.
