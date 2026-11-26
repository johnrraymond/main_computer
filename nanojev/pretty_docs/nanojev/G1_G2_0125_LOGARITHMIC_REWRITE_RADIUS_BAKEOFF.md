# G1/G2-0125 — Logarithmic rewrite-radius bakeoff

## Research rationale and current evidence

0124 intermediate operator log (2026-11-25 Pacific) reported 5/10 symmetrized choice and 0/10 exact free-generation math answers for original Qwen; unrestricted B obtained 7/10 and 2/10 with 11 preservation decisions lost and mean blind KL 11.808733; joint C obtained 9/10 and 5/10 with six preservation decisions lost and mean blind KL 3.989877. Both failed the preservation gate. All seven 0124 binding arms failed elementary acquisition. These are bounded, interim observations, not proof of mathematical generalization or final 0124 rankings. The former normalized L2 update radius was 0.25, independent of the gradient's magnitude. The radius hypothesis is that smaller steps will retain useful acquisition while reducing collateral damage; it has not yet been tested.

0125 is an additive research module. It leaves all 0124 sources, checkpoints, report identities and original model weights unchanged. It requires the installed 0124 source and its completed reference lock; it does not require 0124's remaining mutable arms to finish. Avoid simultaneous 0124 and 0125 CUDA runs because of memory pressure. The 0124 identity is reconstructed from its saved configuration and checked exactly, including upstream source and checkpoint provenance. The reference lock is verified. The actual device must match the 0124 reference configuration. Nothing is promoted, and G3 remains locked.

## Phase I — logarithmic calibration

On the same bare pinned FP32 Qwen, calculate one full-dimensional mathematical acquisition gradient (stage-2 original 0124 fit population, same balanced A/B plus teacher-forced answer-token objective). Normalize that gradient once. For every probe, restore the **exact same initial weights** and verify equality, then apply the specified radius. Probe radii:

`0, 0.0001, 0.001, 0.0025, 0.01, 0.025, 0.1, 0.25`.

The zero-radius measurement is the negative control. No training checkpoint is saved for a single-step probe. Report the requested radius, measured FP32 weight L2 displacement, nonzero changed-coordinate count, eligible dimensions, fit and development acquisition losses and correct counts, both fit and development preservation KL, maximum per-presentation KL, original-correct preservation decisions lost, exact generation gains and losses on development, and the frozen 0122 G1 regression review. KL is measured on the 0124 teacher distributions, not merely choice labels. The zero control must satisfy preservation or execution stops; all nonzero probes are compared to it. The model is exactly restored at the end. A zero or nonfinite acquisition gradient is refused.

A safe local radius must have mean preservation KL <=0.1 nats (configurable downward only) on **both fit and development**, no original-correct choices lost in either preservation set, no original-correct generated development mathematical answer lost, and no original-correct G1 object lost or aggregate false-object increase on the three exposed 0122 controls. Report KL maxima as diagnostics, not hidden acceptance gates. Safety is scoped to these measured controls. There may be no safe nonzero radius; in that case stop before training. A safe but initially flat radius can still be continued, but it is not evidence of acquisition.

Select at most two radii by default: the highest-ranked safe radius with an acquisition signal (free generation, choice accuracy, then lower development loss and KL), plus the largest safe radius if distinct; fill remaining slots with other highest-ranked safe radii. If no safe radius has a positive signal, rank the safe radii by the same criteria. These rules are fixed in code before any run. Selection never uses fresh confirmation labels or the exposed 0124 blind results.

## Phase II — matched cumulative rewrite bakeoff

For each chosen radius, restart from W0 for every arm, use twelve proposed acquisition updates (same budget as 0124), and compare:

* B_fixed: normalized acquisition gradient, fixed radius, no repair.
* C_joint: normalized acquisition plus original-function KL gradient, fixed radius, no separate repair.
* F_sequential_fuzz: same B acquisition budget, followed by the inherited 0124 matrix-free finite-difference repair. Sampled weight directions have full parameter support, not a complete Jacobian. Actual repair accepted by the parent nonlinear line search. Record before/after acquisition and preservation, including whether the candidate survives the stricter radius-laboratory safety gate.
* C_adaptive: normalized joint gradient. At each update try scale 1, 0.5, 0.25, 0.1 of the initial radius from exactly restored current weights. Accept the first trial that reduces fit acquisition loss and has no original-correct fit preservation loss and mean fit KL <= threshold; otherwise restore exactly and count a rejected update. Development is not used in the update decision. The final development gate is independent of this local fit constraint.

All mutable parameter dimensions remain eligible; no LoRA, S or R. The frozen reader is retained only for its diagnostic interface. Fixed arms are allowed to incur measurable cumulative damage so the adaptive method can be compared. All arms record full fit update trajectories. Final development generation and G1 checks are performed after each matched budget, and before/after the separate repair. The exact same math acquisition objective is used across all methods. Matched proposed updates are not matched wall clock cost: adaptive retries and repair involve additional forward/backward passes; record elapsed time and per-update accepted scales.

Only candidates with no measured fit/development preservation failure and a positive dev acquisition signal (choice or free gains, or lower acquisition loss by >1e-6) get experimental full-weight checkpoints. Loss-only signal is not a capability gain; it merely permits fresh confirmation. The winner is ranked by development free exact count, symmetrized choice count, lower dev acquisition loss, lower dev KL, and smaller actual net movement. Lock every trial receipt and the selected checkpoint before any fresh confirmation inference. Failed trials' numerical evidence is saved even though their large weight files are not. No model written to the HF cache.

## Phase III — sealed confirmation

231 original 0124 math/preservation cards remain unchanged. Reuse original stage-2 fit/development and original reference. The previous 0124 blind split has already been exposed by the earlier experiment and is explicitly retrospective regression only. Add 36 genuinely new authored prompt records before calibration: 20 elementary math (ten signed arithmetic, ten zero-line/cancellation), eight mathematical preservation (fractions and comparison), eight language preservation (coreference and grammar). Their number ranges and wording differ from the original small population; exact prompt collisions against all original 0124 card/native decks are refused. This is not a guarantee of pretraining novelty or semantic-family independence. Use a separate prediction subprocess with the prior answer-file audit guard. It reads public prompts, predicts original W0 and the selected candidate, and locks outputs before scoring. A/B candidate ordering is symmetrized, actual greedy free generation capped at 20 tokens is independently evaluated using exact integer/rational equivalence. No fresh labels enter the calibration, training, repair or selection. The Python audit hook is not an OS security boundary.

Preservation scoring includes all native and language distributions and initially correct mathematics (choice and/or free answer). G1 uses the fixed original 0122 reader and exposed graph regression prompts. Report fresh math and old 0124 math separately, including gains/losses, and preservation overall and on the fresh subset. Require no lost baseline-correct choice or free-generation answers, mean preservation KL <= threshold overall and on fresh controls, G1 preservation pass, and fresh math >=80% choice, >=60% exact generation, at least one genuinely gained fresh math decision. A final gate pass remains research only; finite prompts cannot establish functional identity outside measured populations. If no safe improving candidate exists, finish without reading confirmation answers; preserve all measured failures.

## Operator commands

From `nanojev`, with the existing `$py` interpreter:

```powershell
& $py -u -m tools.nanojev_gamer_v1_rewrite_radius_0125 audit
& $py -u -m tools.nanojev_gamer_v1_rewrite_radius_0125 selftest
& $py -u -m tools.nanojev_gamer_v1_rewrite_radius_0125 run --device cuda
```

To run only the single-step calibration (with default settings, so the subsequent run can reuse it):

```powershell
& $py -u -m tools.nanojev_gamer_v1_rewrite_radius_0125 prepare --device cuda
& $py -u -m tools.nanojev_gamer_v1_rewrite_radius_0125 calibrate --device cuda
```

`run` reuses completed sealed calibration and fit receipts; interrupted partial fits restart from W0, not from an intermediate state. `--steps`, `--max-radii`, `--max-kl`, and device are bound in the identity. Use a different output directory named with prefix `g1-g2-rewrite-radius-0125` if changing configuration. Default outputs: `runtime/reports/gamer-v1/g1-g2-rewrite-radius-0125/`, with `calibration.json`, `trials/<radius>/<method>/fit.json`, conditionally `checkpoint.pt`, `selection.json`, guarded `predictions.json` and `prediction-lock.json`, `confirmation-result.json` if applicable, final `result.json`. Do not delete the parent 0124 evidence tree. No separate JSON input or download.

Storage upper bound at default two radii and four arms is eight FP32 full-backbone checkpoints (roughly 20 GB for a 0.6B-parameter model), generally fewer because unsafe/no-gain candidates are not saved. Reference and failed-trial JSON are much smaller. CPU and GPU memory may be substantial: full FP32 weights and gradients, CPU exact restoration snapshot and gradient directions; F can hold several CPU weight-size buffers. Run arms sequentially. Hardware cost not measured locally.

## Verification limits

Local verification: Python syntax compilation, deterministic author population, radius eligibility and ranking, actual tensor displacement and exact rollback, adaptive backtracking and full rejection, selection/checkpoint digest validation and tamper refusal. These tests were run against an interface fixture implementing the inherited 0124 tensor/storage primitives, because the actual generated 0124 patch source was not present in the current build environment. The new code uses the prior delivered 0124 interfaces and the actual installed parent identity is checked in `audit`. The installed-parent self-test, full integration, actual Qwen/CUDA, and scientific results remain unverified here. The operator run is the authority for the live outcomes. No production changes authorized.
