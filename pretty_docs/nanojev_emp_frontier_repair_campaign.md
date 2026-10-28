# NanoJev progressive arithmetic-memory frontier (2026-10-27)

## The contract

Teach an exact arithmetic memory. **Within an acquired radius, prior correct answers
are not a holdout; they are permanent training achievements.** Expand into harder
operand magnitudes, find the first failures, repair them by localized parameter
optimization, re-audit the cumulative radius, and repeat. The gold target `a+b`
is available because this is mathematics; gold answers are never passed into
the model's operand inference as answers. There are **no TRAIN/DEV/TEST folds**
in the new runner and no permanently withheld validation cards.

The current `emp` model's investigated family is specifically ordered
**opposite-sign/zero addition** with `|a+b| <= max-delta` and
`max(|a|,|b|) <= radius` (and the origin). This is not an exhaustive audit of
all possible additions or all residual magnitudes. A radius can be certified
**exhaustively within that explicitly declared family**. To extend to all
same-sign arithmetic or wider residuals, rerun with an expanded domain generator.

## The experiment

- Start from the defective historical stage-27 snapshot or supply the exported
  self-discovered, donor-free `autodiscovered_repair.pt` with `--start-repair`.
  The latter must contain the same original snapshot SHA256.
- For each radius, create a complete census when its conservative size estimate
  is below `--census-cap`. Otherwise perform reproducible, rotating, explicitly
  labelled **sampled** audits. `--require-exhaustive` defaults to true and stops
  the run before any training if the maximum radius would exceed that cap.
- If the census has zero errors, advance to the next radius. This is intentional
  memorization. No DEV gate or zero-shot-only metric impedes acquisition.
- If errors appear, cluster a few related failing cards. Rank all parameter
  groups by direct gradients, *not* by a preselected input-normalization mask;
  independently train donor-free corrections using those defects and already
  correct examples. Testing a small parameter group first is a search priority,
  not a permanent restriction.
- Compare candidate predictions against the complete observed radius. **Zero
  new regressions and at least one corrected error** are required, as is
  correction of each chosen failure. New sampled audit waves are also checked
  before committing a correction; sampled observations are archived in the
  subsequent radius and checked again. All training targets and audits are exact.
- A safe correction can be saved as `pending.pt` even if the new radius is not
  yet completely correct. This checkpoint is *not* called mastered. On rerun,
  resume its partial correction automatically. Only zero observed errors promote
  `latest.pt`, which is the certified frontier. Every stage is logged with
  initial/final error counts, coverage kind and repair attempts.

**Important:** `certification: sampled_only` means zero observed errors across
these deterministic/PRNG draws, not mathematical proof of every card in the
radius. `certification: exhaustive` means every pair *in the declared family*
was checked. Every possible pair cannot be claimed when sampling only.

## Installation

```powershell
python .\new_patch.py "C:\Users\subsi\Downloads\nanojev_emp_progressive_memory_frontier_patch.zip"
```

## Suggested first CUDA run

```powershell
& $py -m tools.nanojev_emp_frontier_repair_campaign `
    --run "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --start-repair "C:\Users\subsi\NanoJev\runs\nanojev_autodiscovered_repair_v1\autodiscovered_repair.pt" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_progressive_memory_frontier_v1" `
    --device cuda `
    --stage 27 `
    --start-radius 464 `
    --radius-step 16 `
    --max-radius 640 `
    --max-delta 32 `
    --census-cap 250000 `
    --repair-cycles 6 `
    --search-steps 300
```

To start from the original stage-27 snapshot instead, remove `--start-repair`.
The tool always stores its own weights in a separate output directory. Do not
point `--output` at an existing campaign or source run.

To continue the same configuration, rerun the **identical command**: the
campaign resumes from `pending.pt` (partial repair) or `latest.pt` (certified
radius). A stalled run is not a failed model; inspect the latest `result.json`
and the sequence in `history.json`. Increasing budgets requires a *new output
folder*, because the runner prevents silent configuration drift.

For exploration past the complete-enumeration budget, use
`--no-require-exhaustive --audit-size 8192 --recheck-waves 3`. Every sampled
radius is explicitly marked `sampled_only` and previously observed sample
cards remain in the cumulative memory archive. The archive has a hard limit
`--max-memory-cards`, which refuses rather than silently evicting memories.

## Results

`EMP_FRONTIER_AUDIT`: radius, sampled/exhaustive coverage, observed errors.

`EMP_FRONTIER_REPAIR_CHECK`: candidate defect fixes, controls, full-census
regressions; `qualified` applies to that repair checkpoint only.

`EMP_FRONTIER_REPAIR_ACCEPT`: one verified change, parameter groups and count.

`EMP_FRONTIER_ADVANCE`: a radius was mastered under its reported coverage.

`EMP_FRONTIER_RESULT`: `complete` or `stalled`, highest certified radius,
coverage, recovery checkpoint location and pending checkpoint when applicable.

Checkpoint SHA provenance is checked for `--start-repair`. Other source files
and normal training campaigns remain unchanged. This is bounded supervised
arithmetic repair, not a proof of generalization or unsupervised mathematical
reasoning. No donor from a later training checkpoint is used to produce edits.

## Counterexample-guided exact-memory preservation (CEGIS follow-up)

The first radius-464 GPU census found **1 error out of 58,337 problems**, but
initial repairs of that error introduced **50–70 regressions** among formerly
correct answers, so no checkpoint was accepted. That is a legitimate failed
repair, not proof of frontier advancement. The original version rehearsed only
256 sampled good cards, checked the full census, and discarded the failures
without adding them to training.

The revised repair transaction keeps the **same strict zero-regression rule**
and gives the optimizer more useful evidence:

1. Train a candidate from the current working checkpoint against exact defects
   and correct rehearsal cases; score the full census as before.
2. On a rejected candidate, identify *which* previously correct arithmetic
   memories were damaged and add those exact pairs to a high-priority rehearsal
   bank (up to `--replay-hard-limit`, default 512). Previously wrong answers do
   not enter this bank as if they were correct.
3. Repeat hard cases more often in training (`--replay-multiplier`, default 8)
   with preservation targets captured from the **pre-repair** checkpoint; keep
   accumulating hard cases across parameter-budget attempts.
4. For a candidate fixing the target failures, also test smaller **interpolated
   edits** (`--repair-scales .75 .5 .25 .125 .0625`). Every accepted
   interpolation must pass the same complete audited-memory gate.
5. Export a correction only if it improves at least one defect and introduces
   **zero regressions across the full current census**. A rejected update never
   becomes the starting point for the next radius.

For a clean A/B comparison with the stalled original run, write to a **new
output directory**: the campaign's config provenance deliberately disallows
changing strategy or hyperparameters in place. The `EMP_FRONTIER_REPLAY` trace
reports the number of failing memories being actively rehearsed. Failed checks
report `newly_mined` and the current bank size; scale checks are exploratory,
not a safety exemption.

This is still a bounded arithmetic-memory experiment. If no candidate meets
zero-regression conditions, it stops and reports `stalled`. Increasing sampling,
rehearsal, or search budgets does not guarantee success.

## Persistent hard-number and hard-pair memory (next frontier)

The radius-640 repair campaign achieved **81,217/81,217** correct ordered
signed cancellation cases with residuals in `[-32,32]`, under exhaustive
coverage. The radius-464 failure was repaired by a scaled normalization edit;
all subsequent radii to 640 passed without more repair. This does **not** show
that the repair caused all later radii to be correct.

The next campaign adds an SQLite bank at `OUTPUT/hard_cases.sqlite3`.
Its primary key is the **ordered pair `(a,b)`**; `target=a+b` is validated
on ingestion, not taken from a model prediction. The bank tracks:

- Prior incorrect answers (`wrong_count`), including ones since repaired.
- Correct calculations damaged by *attempted* repairs (`regression_count`);
  only an old-correct/new-incorrect comparison can award this event.
- Nearby mathematical pairs, **candidates** for scrutiny rather than invented
  failures (`neighbor_count`).
- Later correct observations, the most recent prediction, operand magnitude,
  residual, and wave/provenance information.

The database is transactional and persists across **identical-command
resumes**. The `source_sha256`, arithmetic family, and residual bound are
checked when opening it. Moving the bank to a different source checkpoint or
family without an explicit migration refuses to run.

Human-readable snapshots `hard_pairs.csv` and `hard_numbers.csv` are exported
on campaign completion. The latter aggregates signed operands appearing in
hard pairs; it is not a separate arithmetic oracle. `EMP_FRONTIER_HARD_BANK`
prints a compact inventory.

### Frontier sampling protocol

For a radius whose **exact** arithmetic family cardinality fits
`--census-cap`, enumerate the full domain. Cardinality is:

`N(r,d) = 2*r+1 + 4*(m*(r+1) - m*(m+1)/2)`, with `m=min(r,d)`.

At `r=1024,d=32`, **N=131,137**, so the default 250,000 cap still permits a
complete census. The older conservative estimate would have switched to Monte
Carlo too early.

When enumeration becomes expensive, each seeded audit mixes:

1. Priority replay from the durable hard-case bank, up to
   `--hard-audit-cards` (default 1024), capped to a third of the audit budget.
2. A boundary-shell draw within approximately one radius step of the frontier,
   up to `--shell-audit-cards` (default 2048), capped to half the remaining budget.
3. The rest is **fresh full-radius Monte Carlo exploration**, so new failure
   families are not hidden by repeatedly testing only known errors.

The fixed seeded wave produces the same sampled audit again; different waves
sample different cases. There are no permanently withheld DEV/TEST partitions.
All arithmetic can become training evidence. A sampled-only frontier is never
labeled mathematically exhaustive.

The most recent fully enumerated radius is retained as a **protected exact
core**. If a repair is attempted in a sampled outer radius, the candidate must
also pass that entire inner census without new errors. If the core exceeds
`--protected-core-cap`, the program refuses to silently weaken this rule.
The exact core is tested on repair acceptance rather than every outer-radius
exploration pass to save inference cost. Beyond the exact core, retained
previously sampled memories are protected to the extent archived and examined;
unobserved inputs cannot be guaranteed.

### Suggested CUDA expansion from the successful radius-640 checkpoint

Use a **new** output directory. The previous 640 checkpoint must be explicitly
supplied using `--start-frontier-checkpoint`; **do not** combine it with
`--start-repair`.

```powershell
& $py -m tools.nanojev_emp_frontier_repair_campaign `
    --run "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --start-frontier-checkpoint "C:\Users\subsi\NanoJev\runs\nanojev_progressive_memory_frontier_replay_v1\latest.pt" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_frontier_hard_bank_v1" `
    --device cuda --stage 27 --start-radius 640 `
    --radius-step 64 --max-radius 2048 --max-delta 32 `
    --census-cap 250000 --no-require-exhaustive `
    --audit-size 8192 --recheck-waves 3 `
    --hard-audit-cards 1024 --shell-audit-cards 2048 `
    --protected-core-cap 250000 --repair-cycles 6 --search-steps 300
```

The runner re-audits radius 640 in the new campaign, then goes to 704, 768,
832, ..., 2048. At radii with more than 250,000 exact family cases it changes
to sampled-only status and uses its stored hard pairs, frontier-shell draws,
and seeded Monte Carlo exploration. The old checkpoint is read-only.

The primary open research problem remains **safe repair without forgetting**
when the protected exact set itself becomes too costly to evaluate exhaustively.
A hard-case bank improves search efficiency but does **not** solve that proof
problem. Track `protected_exact_radius` separately from the largest sampled
frontier, and never call a sampled-only result fully memorized.
