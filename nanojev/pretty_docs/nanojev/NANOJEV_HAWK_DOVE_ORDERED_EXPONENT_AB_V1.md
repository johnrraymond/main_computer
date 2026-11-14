# NanoJev Hawk–Dove: ordered-exponent and combined-magnitude A/B experiment

## Objective

Determine whether supervising the **numerical distance** of the exponent and the **combined base-10 logarithmic magnitude** of each output X and Y corrects the one-decade failures observed on T02. The existing V3 sign, exponent-class, and fractional-log-mantissa outputs and losses are retained unchanged. Z/ARGMAX remains diagnostic only. The scientific-notation teacher and T00–T08 mutation lattice are unchanged.

The factorized head already uses `sign`, `exponent` (classification logits), and `fraction` (sigmoid prediction). For nonzero target `t`, this experiment adds:

- `e_gold = floor(log10(abs(t)))`; `e_expected = sum_k softmax(exponent_logits)[k] * k`.
- `log_gold = log10(abs(t))`; `log_expected = e_expected + fraction`.
- `0.25 * smooth_l1(e_expected, e_gold, beta=0.5)`.
- `0.50 * smooth_l1(log_expected, log_gold, beta=0.5)`.

Loss terms are computed per X and per Y and averaged across available nonzero coordinates; the two weights can be changed **only at the initial fork**. Zero or `abs(t)<1` is excluded from log losses and still receives the original sign/classification loss. **Inference remains the existing hard `argmax` decoder.** This experiment adds no parameters; existing AdamW moments load without modification. Expected exponent is a differentiable softmax mean, not a new continuous output head. There is no change to `tools/nanojev_altreg_math_v3_decoder_experiment.py` or the checkpoint-bound scientific trainer.

## Source and isolation

The original scientific run must be stopped **after a complete checkpoint** before forking. Check `latest.pt` and keep a backup. The fork procedure copies exact bytes of that checkpoint into two new directories, including `config.json`, `tier-gates.json`, and any `mask-weights.json`. Both copies initially have identical model weights, optimizer state, RNG state, training step, focus, streak and persisted gates. It refuses existing directories or changes in donor hash during copying.

The old `hawk_dove_million_scientific_v2` run and teacher SQLite database are never written by the fork or subsequent training. The branches are:

- `.../hawk_dove_million_ordered_exponent_ab_v1/control`: original objective, original cached trainer.
- `.../hawk_dove_million_ordered_exponent_ab_v1/ordered`: original objective **plus** ordered exponent/magnitude losses.

Every branch has `ab-experiment.json` binding the starting checkpoint hash, donor step, source/teacher SHA, trainer SHA, supervision code SHA, branch and exact weights. After the first optimizer checkpoint, each checkpoint also embeds the branch-specific `ordered_exponent_ab` objective stamp. Resume refuses unstamped checkpoints unless the bytes are still **identical** to the original donor, and refuses mismatched stamps. Existing checkpoint schema and trainer SHA remain unchanged.

## Windows PowerShell: install

From `C:\Users\subsi\main_computer` place the ZIP in the repo root:

```powershell
Expand-Archive .\nanojev_hawk_dove_ordered_exponent_ab_v1_patch.zip .\_ordered_exponent_patch -Force
Copy-Item .\_ordered_exponent_patch\main_computer_test\* . -Recurse -Force
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_hawk_dove_ordered_exponent -v
```

## One complete run (12,000 optimizer updates on each branch)

```powershell
& .\tools\run-nanojev-hawk-dove-ordered-exponent.ps1 `
  -Stage All `
  -Python $py `
  -AdditionalSteps 12000 `
  -ChunkSteps 300 `
  -Tier T02 `
  -CacheMemoryMB 1024
```

`-AdditionalSteps` is additional, not absolute. It is computed separately from each branch's saved optimizer step. Both start from the donor step; control is trained first, then treatment, using the same budget and the same existing tier gates.

## Individual stages (safer, recommended)

```powershell
# Step 1: fork; one time only. Existing branch directories are refused.
& .\tools\run-nanojev-hawk-dove-ordered-exponent.ps1 -Stage Fork -Python $py

# Step 2: before any training, verify both branches have identical T02 baselines.
& .\tools\run-nanojev-hawk-dove-ordered-exponent.ps1 -Stage Baseline -Python $py

# Step 3: same budget from the same source checkpoint.
& .\tools\run-nanojev-hawk-dove-ordered-exponent.ps1 -Stage TrainControl -Python $py -AdditionalSteps 12000
& .\tools\run-nanojev-hawk-dove-ordered-exponent.ps1 -Stage TrainOrdered -Python $py -AdditionalSteps 12000

# Step 4: evaluate all 23 dev instances under each of T02's 8 masks.
& .\tools\run-nanojev-hawk-dove-ordered-exponent.ps1 -Stage ProbeBoth -Python $py -Tier T02
& .\tools\run-nanojev-hawk-dove-ordered-exponent.ps1 -Stage Compare -Python $py -Tier T02

# Step 5: inspect the training/dev gap using an even 20-instance sample per mask.
& .\tools\run-nanojev-hawk-dove-ordered-exponent.ps1 -Stage TrainProbe -Python $py -Tier T02 -TrainPerMask 20
```

Once forked, **do not run `-Stage Fork` again**. Both `TrainControl` and `TrainOrdered` can be called repeatedly using additional steps. Do not train both simultaneously on the RTX 3060. Avoid running another trainer against the same checkpoint while these commands execute.

## Probe / criteria

The paired read-only probe records, separately for X and Y:

- raw joint and per-axis numerical accuracy within 5%; per-mask breakdown;
- exponent top-1 class accuracy, true-class probability, signedness, exponent-distance error and one-decade errors;
- whether substituting **only the correct exponent**, **only the correct sign**, or **only the correct fractional mantissa** during scoring rescues each failure;
- all-component oracle (validation check, not an operational inference mode).

The probe writes per-case reports to each branch. `compare` refuses mismatched source donor hashes, case IDs, steps, tiers, or splits. It does not inspect the test split.

**Interpretation:** Improvements in treatment exponent accuracy accompanied by T02 X accuracy improvements are evidence for the new ordered/magnitude supervision. Improvements only on training cases suggest memorization/generalization limits. If exponent accuracy improves but numerical accuracy does not, inspect sign and mantissa oracle rescues. Hard argmax inference may remain unreliable even if the differentiable expected exponent loss decreases.

## Provenance and limits

- No database regeneration or historical-parent reloading.
- Frozen Qwen capture cache reused within each run; cache starts cold for each fork and warms by prompt. Existing batch/feature keys are unmodified.
- CUDA/FP16, same Qwen source, fixed teacher split and stored optimizer/RNG.
- Original loss and exact gate mechanics remain intact. Changing losses can change adaptive tier selection after divergence; these are identical-budget A/B branches, not guaranteed identical minibatch trajectories.
- Stop at an ordinary saved checkpoint if interrupted. Resume through this same forked experiment runner so objective stamping and checks remain active.
- CPU unit tests do not demonstrate CUDA training improvement. The outcome is determined from workstation probes.
