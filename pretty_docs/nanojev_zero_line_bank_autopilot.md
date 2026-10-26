# NanoJev Zero-Line Bank Autopilot — Adaptive Curriculum and Expert Audit (v2)

## Purpose

Grow an immutable zero-line backbone plus frozen, learned-routed absolute-magnitude
specialists toward operand magnitude 2,000,000. The bootstrap is the accepted
128–159 bank/router and repaired 160–191 bank/gate. New banks and gates learn
from the backbone's activations; **inference receives no true sum, residual,
Python magnitude band, correct-expert label, or wrong-answer fallback**.

The 30-stage doubling schedule is a **plan**, not evidence that learning scales.
Each interval must pass acceptance; failures are never silently promoted.

## Updated methodology

1. **Measure separate experts on identical examples.** Report incumbent-only
   accuracy, new-bank accuracy, both-correct, incumbent-only, specialist-only,
   both-wrong, and an offline answer-aware *OR-of-experts upper bound*. This
   upper bound is for diagnosis only; using it at inference would be cheating.
2. **Adapt specialist training on DEV, never OOD.** Train an initial block
   (`--bank-steps`, default 1,500). If DEV accuracy is below the desired bank
   target, extend by `--bank-extension-steps` (default 1,500), stopping on
   attainment, a DEV improvement plateau, or `--bank-max-steps` (default 9,000).
   A plateau means fewer than `--bank-min-dev-gain` (default .002 absolute
   accuracy) improvement in best DEV for `--bank-patience` consecutive blocks.
   Fixed intermediate probes store the optimizer and sampling RNG for resume.
3. **Refuse impossible routing attempts early.** If even the offline oracle
   combination of two experts is below `--min-accuracy` on DEV, skip gate
   training. No live routing uses the oracle.
4. **Train the neural gate against the incumbent** with historical replay,
   whole-answer supervision, and preservation penalties. The gate can stop on
   its own DEV plateau using `--gate-patience` and `--gate-min-dev-gain`.
5. **Gate by arithmetic competence, not use rate.** The previous 90% new-bank
   selection requirement has been REMOVED. A candidate must make a *strict net
   DEV arithmetic improvement*, not lose net OOD arithmetic, satisfy the
   integrated DEV/OOD ≥90% (configurable), exact zero and near-zero gates,
   and cause zero new errors on required historical witnesses. Report new-bank
   usage but do not require it.
6. **Diagnose before splitting.** An insufficient expert ceiling can justify
   splitting the interval. A failing neural gate or historical regression
   instead stops the campaign for routing repair; splitting is not a universal
   substitute for a router fix.

OOD is assessed **after** DEV selection and is used for final stage acceptance,
not gradient updates or checkpoint selection. Repeated acceptance decisions
against the same OOD population mean it is no longer an untouched final test.
For scientific confirmation, establish new, previously unseen population splits.

## Recommended controlled 192–223 continuation

The 6,000-step experiment already saved an excellent diagnostic candidate; do
not discard it. The `--bank-warmstart-stage` argument loads **only its
`bank_best.pt` weights** after checking the old stage band, base checkpoint
fingerprint, hidden width, and SHA-256. The optimizer is new; the added-step
counter starts at zero, not 6,000. The prior output directory is read-only.

From Main Computer's repository root, with your Python environment activated:

```text
python -m tools.nanojev_zero_line_bank_autopilot --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" --prior-bank-output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_v1" --prior-router-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_learned_router_128_159_v1" --repair-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_cumulative_160_191_repair_v1" --bank-warmstart-stage "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_autopilot_6000_v1\stages\192_223" --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_autopilot_adaptive_v2" --device cuda --target 2000000 --max-stages 1 --failure-policy stop --bank-steps 1500 --bank-max-steps 9000 --bank-extension-steps 1500 --bank-patience 2 --bank-min-dev-gain .002 --bank-target-accuracy .90 --gate-steps 1200 --batch-size 128
```

This first experiment handles only **one successfully promoted new stage**. It
may halt on refusal. No implicit Python routing or label-corrected inference is
allowed.

To start without old bank weights, omit `--bank-warmstart-stage` and use a
**different new output directory**. Do not reuse an output directory from the
v1 autopilot: configuration and stage semantics changed.

### Interpretation of stage results

- `ZERO_AUTO_BANK_ADAPT`: completed bank-training block, best DEV, gain,
  and stopping reason (`target_reached`, `dev_plateau`, or `max_steps`).
- `ZERO_AUTO_EXPERT_DIAGNOSTIC`: paired incumbent vs bank accuracy and offline
  complementarity on DEV. The final `result.json` also records independent OOD.
- `ZERO_AUTO_GATE_SKIPPED`: the offline DEV upper bound was too low; no learned
  gate could have passed the DEV accuracy requirement at those weights.
- `ZERO_AUTO_GATE`: learned-route accuracy, retained-answer harm, new-bank usage.
  A low usage percentage *by itself* does not constitute a failed router.
- `ZERO_AUTO_STAGE_REFUSED`: stage unpromoted; read `refusal_reason` in
  `stages/<low>_<high>/result.json` before deciding whether to split or repair.
- `ZERO_AUTO_STAGE_ACCEPTED`: bank+gate checkpoints frozen, SHA-256 pinned in
  the manifest, previous accepted chain unmodified.

## Certification limits

The original 65,025-card base deck is checked exhaustively at 100%, and by
default both bootstrap magnitude regions are checked over all ordered pairs.
For larger historical regions, fixed sampled witnesses protect retained
answers. This is explicitly **not exhaustive** retention proof through two
million. New-band DEV/OOD groups test exact zero, ±1..2, ±3..15, ±16..32,
±33..64 and larger sums with both signed orientations. Routing must not use
these target-derived labels at inference.

## Persistence and operations

`config.json` and `manifest.json` are atomic and source-pinned. Each stage
records `bank_training.pt`, `bank_best.pt`, `adaptive_progress.json`,
`gate_training.pt`, `gate_best.pt` (if routing was attempted), and `result.json`.
A run can be resumed with the **same configuration**. A changed source, training
policy or warm-start checksum fails closed. Every prior bank/router is frozen.
If the campaign stops because learned routing fails, inspect the paired expert
comparison before expanding or splitting; splitting is only automatic when an
insufficient expert ceiling justifies it and `--failure-policy split` is set.

Prediction is unchanged from v1:

```text
python -m tools.nanojev_zero_line_bank_autopilot --phase predict --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_autopilot_adaptive_v2" --device cuda --a 175 --b -159
```

Inspect schedule without touching sources:

```text
python -m tools.nanojev_zero_line_bank_autopilot --phase plan --target 2000000
```

Run CPU regression tests:

```text
python -m pytest -q tests/test_nanojev_zero_line_bank_autopilot.py
```
