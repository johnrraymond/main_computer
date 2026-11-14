# NanoJev v3.4 — Bottom-heavy hierarchical regression

## Objective and scope

This cutover **reverses the gradient emphasis** of FINAL v3.3. It is additive:
only the new `nanojev_parallel_result_bottom_heavy_v3_4.py` script, this runbook,
and its test file are installed. It never writes to the benchmark, immutable
buffered-v1 source checkpoint, or v1–v3.3 experiment directories.

The Qwen3-0.6B backbone stays frozen and provides one feature capture per fresh
training batch. The v3.3 error estimators and seven RESULT heads are inherited
from a specific parent checkpoint, **then become trainable in the new lineage**.
The historical v3, v3.1 and v3.2 arbitration controls remain weight-frozen.
The pretrained 38-output buffered operand decoder is cloned and trained in the
v3.4 run, while its original source checkpoint and frozen in-memory control stay
unaltered. The operation probe stays frozen because previous held-out operator
recognition was already very high; evaluate it again in v3.4.

The training phase cycle is exactly:

* 16 of 20 phases (80%): fit the cloned 38-output X/Y hierarchy plus an independent
  auxiliary X/Y representation against known operand targets.
* 3 of 20 phases (15%): fit the inherited seven structured RESULT heads and
  learned median using current **updated X/Y predictions** and exact RESULT targets.
* 1 of 20 phases (5%): fit eleven inherited FINAL error estimators against the
  **current changing RESULT vector**. Candidate truth is used only as supervision,
  never as an inference feature.

`--replay-updates 2` runs two replay minibatches of the designated phase per
fresh batch, preserving 80/15/5 **phase/minibatch allocation**. Each phase may
call multiple individual optimizers (one for each separately trainable head);
thus 80/15/5 is **not** the proportion of low-level `.step()` invocations or
of raw scalar gradient magnitude. Phase totals are saved and reported.

### Detailed intermediate supervision

The cloned 38-head operand decoder receives explicit labels for both registers:
zero, sign, ordinal band thresholds, and selected signed-magnitude local expert.
Its existing constrained continuity and shared-parameter losses are retained.
A second `OperandAux` network is trained independently with labels for both
operands' signed normalized-asinh coordinates, sign, zero, 6-way magnitude
regime, and seven decimal-place digits. It is measured as an alternative
operand decoder and executed RESULT source. **The auxiliary pathway is a probe
and a separate executed-RESULT baseline in v3.4; it is not yet a twelfth
member of FINAL's legacy 11-register candidate vector.** The refined
38-head hierarchy, however, feeds R0 and all seven RESULT readouts, the
learned median, and FINAL.

A hard operand decode separates the lower-level decoder from the RESULT heads.
Consequently, RESULT losses do **not** backpropagate through the hard decode into
its weights. The 80% base updates supply **direct known-intermediary supervision**
for this reason; claiming differentiable end-to-end operand training would be
incorrect. No true operand, operation or RESULT is fed into inference.

### Evaluation and checkpoint rules

The v3.3 parent manifest, source identity, and inherited model configuration
are checked before any v3.4 output directory is created. Training starts from
its latest checkpoint by default. Add `--fork-parent-best` to import the
validation-selected parent `error_calibrated/best.pt` instead.

The v3.4 checkpoint contains the live 38-head decoder, auxiliary operand
network, their optimizers, updated RESULT heads/median/FINAL estimators, frozen
controls, replay, random states, accumulated 20-phase position/counters, and
provenance. `latest.pt` is resumable; `intermediate_primary/best.pt` is selected
on the held-out validation partition by X/Y **joint 5%-accuracy first**,
then combined X/Y signed-asinh error, then R0 numerical accuracy. FINAL accuracy
is reported, but does not control the primary checkpoint ranking.

Validation indexes 0,2,... fit the per-candidate FINAL calibration offsets and
retention thresholds. Indexes 1,3,... are disjoint and select the hierarchy
checkpoint. Test and control receive the fixed validation-derived parameters
without retuning. The JSON report contains operand X/Y accuracy and signed-asinh
errors for `original_frozen`, `hierarchy_refined`, and `aux_continuous`, plus
executed RESULT accuracy for each, all eleven updated candidates, oracle union,
FINAL accuracy, numerical regret, rescues/damage, and historical weight-frozen
FINAL comparisons. Historical control heads consume updated candidates, so they
are **not** frozen candidate-distribution controls; use `original_frozen` as the
immutable executed-source reference.

## Run commands (Windows PowerShell)

Extract the additive ZIP through the usual `main_computer_test/`-rooted cutover
into `C:\Users\subsi\main_computer`. All parents v1–v3.3 must already be installed.

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py .\tools\nanojev_parallel_result_bottom_heavy_v3_4.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_bottom_heavy_v3_4.py
```

### Recommended fresh fork: from v3.3 validation best (step 4,600)

```powershell
& $py .\tools\nanojev_parallel_result_bottom_heavy_v3_4.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-3-root "$env:USERPROFILE\NanoJev\runs\parallel_result_final_errors_v3_3" `
  --fork-parent-best `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_bottom_heavy_v3_4" `
  --device cuda --dtype float16 --batch-size 16 `
  --replay-updates 2 --additional-steps 500
```

This uses v3.3's observed step-4,600 best and targets **step 5,100**.
If you instead want the last state at step 4,800, omit `--fork-parent-best`;
that run targets **step 5,300** in a fresh, different output directory.
Do not fork twice into an occupied results directory.

### Resume another 500 phases

```powershell
& $py .\tools\nanojev_parallel_result_bottom_heavy_v3_4.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_bottom_heavy_v3_4" `
  --device cuda --dtype float16 --batch-size 16 `
  --replay-updates 2 --additional-steps 500 --resume
```

The phase position and individual optimizers are restored. Do not repeat the
`--fork-v3-3-root` or `--fork-parent-best` flags on resume.

### Independent evaluation, validation-best hierarchy checkpoint

```powershell
& $py .\tools\nanojev_parallel_result_bottom_heavy_v3_4.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_result_bottom_heavy_v3_4" `
  --device cuda --dtype float16 --batch-size 16 `
  --replay-updates 2 --eval-only --eval-best
```

Writes `evaluation-best-validation.json`, `evaluation-best-test.json`, and
`evaluation-best-control.json`. Use `--eval-only` without `--eval-best` to
inspect the latest step instead.

## Decision criteria

Our first diagnostic is the change in both X and Y's 5% accuracy and **joint**
5% accuracy against `original_frozen`. Then check signed-asinh operand error and
executed R0 RESULT accuracy, rather than mistaking FINAL arbitration changes
for operand progress. Only after intermediate accuracy increases and remains
stable should we decide whether RESULT or FINAL require different objectives.

CPU self-tests and mocked integration do not demonstrate GPU convergence or a
numerical-accuracy gain; only the actual CUDA run and held-out evaluations can.
