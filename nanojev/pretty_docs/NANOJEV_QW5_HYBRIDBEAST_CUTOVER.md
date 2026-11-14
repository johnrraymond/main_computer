# QW5 HybridBeast: source-lineage cutover after reverse-trained Hard EMP

The non-hybrid Hard EMP reverse pass updated the live
`nanojev_emp_dual_bank_outward_v1/hard_emp_frontier_v2/latest.pt` and changed
its SHA. The old QW5 Stage-1 run recorded another specialist version
(`8ff8965e86d526a64d22fc3f963a65d89ede48f81780e99dbae9589db3e9c03e`).
Its certified Z0/Z1/Z2 experiment is still useful historical evidence, but
**must not be resumed with the new specialist**. There is no supported way to
recover exact missing Hard EMP weights from probe logs alone.

## Objective / invariant

Create **one new QW5 five-head lineage**, using the *current* four ordinary
EMP heads plus the *current* single trained Hard EMP specialist, and pin all
frozen source files immediately by their complete SHA-256 hashes:

- `source_archive/stage.pt`: outward stage checkpoint
- `source_archive/hard.pt`: current Hard EMP sidecar (may be reverse trained)
- `source_archive/left.pt`, `right.pt`: source banks

No previous run is edited. No old acceptance ledger or optimizer is imported.
The Qwen LM head never becomes the numerical answer authority. HybridLink
feedback and final QW5 output authority remain **DISABLED_UNCERTIFIED**.

The source preflight uses existing `qw.load_five(...)` validation; a Hard EMP
checkpoint must have exactly **one trained specialist**, and the ordinary-stage
checkpoint must have a matching entry in `hard.processed`. A missing match is
not bypassed and requires addressing source compatibility, NOT changing SHA
values by hand. Stop live reverse training briefly while creating the snapshot
to prevent a concurrent source change; once snapshots exist, the trainer may
continue without affecting this QW5 cutover.

## 0. Read-only preflight

```powershell
& $py -m tools.nanojev_qw5_cutover inspect `
  --legacy-qw5 "C:\Users\subsi\NanoJev\runs\qw5_phase1_v1" `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2
```

The report compares the new source's four hashes to the old QW5 lineage, but
reads the old run **without requiring the missing old Hard EMP file**.
If it prints `QW5_HARD_ORDINARY_STAGE_MISMATCH`, it means the selected outward
stage is not represented by the current sidecar's recorded source SHA; do not
substitute stage/specialist checkpoints that lack a real parent relationship.

## 1. Prepare a new model lineage (fresh initialization; recommended)

```powershell
& $py -m tools.nanojev_qw5_cutover prepare `
  --legacy-qw5 "C:\Users\subsi\NanoJev\runs\qw5_phase1_v1" `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2 `
  --output "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_cutover_v2"
```

Creates `config.json`, `latest.pt` at step zero, `source_archive/*.pt`, and
`cutover.json`. This last file records the frozen source SHA values, optional
historical witnesses, and confirms that **no certifications were transferred**.
If an output directory already exists, the command refuses to overwrite it.

### Optional experimental transfer of *adapter initialization only*

Add this to the `prepare` command if you prefer to test a warm start:

```powershell
  --warm-start-accepted "C:\Users\subsi\NanoJev\runs\qw5_zero_balance_ladder_v2"
```

This copies **only** the trainable QW5 adapter/readout/fusion tensors from the
historical accepted-Z2 checkpoint into the new Stage-1 step-zero checkpoint.
It does **not** copy frozen EMP weights, the old optimizer, any accepted rungs,
or certification. This should be treated as an experimental arm and compared
against the fresh-init baseline if used. Both must undergo new Stage-1 training
and **all** fresh ladder gates. Do not pass `--seed-accepted-from` to the new
ladder; that option is for the *same source lineage only*.

## 2. Verify pinning and train new Stage 1

```powershell
& $py -m tools.nanojev_qw5_cutover verify `
  --output "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_cutover_v2"

& $py -m tools.nanojev_qw5_join train `
  --output "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_cutover_v2" `
  --device cuda --steps 500 --probe-every 100

& $py -m tools.nanojev_qw5_cutover verify `
  --output "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_cutover_v2"
```

Verify and Stage-1 training load archived sources even if the live Hard EMP
trainer subsequently updates `latest.pt` again.

## 3. Start *fresh* zero-line / representation / Hawk-Dove certification

```powershell
& $py -m tools.nanojev_qw5_balance_ladder run `
  --qw5-run "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_cutover_v2" `
  --output "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_ladder_v2" `
  --through Z0 `
  --device cuda --steps-per-rung 1000 --probe-every 100
```

Only after the new source passes Z0 do you extend the same ladder output to
Z1, Z2, R0, R1, R2, B0, and T00..T08. The 16-rung ladder still enforces
zero/near+/near-/far+/far-, invariant pairs, old-rung retention, independent
TEST validation, and Qwen causal contribution gates where required. Preserve
the original `qw5_zero_balance_ladder_v2` unchanged; it is a different
historical run with a different specialist SHA.

## What is *not* part of the cutover

- No change to the four-head ordinary EMP training or Hard EMP qualification.
- No change to source EMP or HybridLink source code or live model checkpoints.
- No reinterpretation of old Z0/Z1/Z2 certificates as new-source certificates.
- No automatic promotion of the new beast into production/runtime Stage 2/3.
- No bypass of SHA, architecture, or parent-stage relationship checks.

## Verification (container / Windows)

```powershell
& $py -m pytest -q `
  tests/test_nanojev_qw5_cutover.py `
  tests/test_nanojev_qw5.py `
  tests/test_nanojev_qw5_source_archive.py `
  tests/test_nanojev_qw5_balance_ladder.py
```

The mocked tests validate source archiving and mutation survival, rejected
old-certificate transfer, opt-in adapter-only initialization, tamper rejection,
Stage-1 step-zero semantics and output isolation. Real model loading/training
cannot run until the Windows local GPU checkpoints are available.
