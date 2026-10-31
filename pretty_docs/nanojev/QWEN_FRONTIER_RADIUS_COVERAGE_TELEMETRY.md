# Qwen Mathematical Frontier — radius and magnitude visibility

## Critical distinction

The legacy numeric-terminal ladder's **R** is the bound on the *signed
result/residual*: R=0 permits operands as large as the configured operand
limit if their sum is exactly zero, e.g. 1024+(-1024)=0. The old checkpoint
stores `radius_index`, an index into its `config.radii` array, **not** a radius
number. Reading the frozen `seed_snapshot.pt` resolves its actual R.

**Neither new frontier phase advances a mutable R.** The current independent
mathematical frontier has a fixed `--operand-boundary` (default 1024) and a
fixed `--levels` count (default 2). Its generator uses the numerical anchor
`operand_boundary * (2 ** level)` for **signed_arithmetic** and
**arithmetic_operators**, so their Level 0 input values are sampled around
1024 and Level 1 around 2048. These are *input anchors*, not hard upper
bounds, certified intervals, or signed-result radii. The generator's near
band allows deviations; cancellation can yield a small result from large
operands. The other seven task families have *their own* numerical difficulty
schemes; the 1024/2048 anchor is not a measure of their difficulty.

Level 1 is reached only after completing every Level 0 task. The samples
phase systematically randomizes **the same configured task/level cells**;
it does **not** dynamically grow the magnitude anchor beyond Level 1 or
increase the legacy radius. Adding outward frontier expansion would require
a separate training-policy/generator change; this telemetry patch deliberately
does not make that change to a running campaign.

## Inspect without loading Qwen or changing checkpoints

While Phase 1 is training, run in another PowerShell terminal:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase systematic --stage radius `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

Once Phase 2 has started:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder_frontier `
    --phase samples --stage radius `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_v1"
```

Both are read-only. They emit `QWEN_FRONTIER_RADIUS` or `QWEN_MIXED_RADIUS`,
respectively, using immutable seed and committed working snapshots. They
show legacy seed signed-result R (or null if unresolvable), current task
and level (Phase 1), configured level input anchors, and that no live
radius-growth algorithm exists. `--stage status` also emits this concise
coverage record and retains the existing full saved state output.

While training, Phase 1 prints a radius record at startup and every task
transition; each train and reference-check line includes its current
`level_operand_anchor` and `anchor_applies_to_task` flag. Phase 2 prints
coverage at start, includes sampled task-level cells and input anchors in
training records, and reports the configured anchors with whole-frontier
checks. These are logs only: **no optimizer, sample sequencing, policy,
checkpoint schema, schedule, or stopping condition changes**.

`--phase glasses --stage radius` intentionally refuses; there is no
operational glass training phase/radius to report yet.
