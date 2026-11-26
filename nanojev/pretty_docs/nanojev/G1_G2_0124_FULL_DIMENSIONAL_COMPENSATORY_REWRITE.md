# 0124 — Full-Dimensional Mathematical Excitation and Compensatory Rewrite Laboratory

## Research record and premise

Status: implemented pilot; CPU mechanism and synthetic causal-model lifecycle fixtures pass. Real Qwen/CUDA execution and all scientific outcomes are pending. No production edit or certification is authorized.

0123 stopped at its first gate. The conditional ownership reader recovered 0/2 assignments and 0/1 complete reversals; native Qwen scored 2/2 and 1/1 through symmetrized A/B likelihood. After 200 updates the reader approached loss 1.03972077 (indifferent logits). The retained checkpoint is the step-80 diagnostic, not a successful reader. The later supplied fit receipt reported initial/selected total-gradient cosines 0.9988254/0.9948837, invariant pre-cue endpoint states and post-anchor mean RMS deltas 0.159106, 0.529130, 0.198637 at layers 14,21,28. Attention key/query gradients were small relative to projection gradients. These facts motivate the rewrite; they do not establish which weight coordinates encode mathematical reasoning. The original Qwen and 0123/0122 results remain immutable.

## Execution

From `nanojev` in the repository after extracting the patch at repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_compensatory_rewrite_0124 audit
& $py -u -m tools.nanojev_gamer_v1_compensatory_rewrite_0124 selftest
& $py -u -m tools.nanojev_gamer_v1_compensatory_rewrite_0124 run --device cuda
```

Default `--steps 12`, `--radius 0.25` (global L2 norm of each normalized full-weight acquisition update, NOT a per-weight learning rate), `--reader-lr 0.001`, `--repair-steps 1`, `--max-kl 0.1`. Configuration and source hashes are sealed; use a new `--output` directory when changing them. Requires prior completed 0123 `STOPPED_AT_ONE`, 0122 Arm A selected checkpoint and fresh graph evidence, 0120 model/calibration receipt, and repository mathematical generators. Requires locally cached original Qwen commit recorded in 0120; no network model fetch. The new matched reference uses FP32 loaded from the pinned checkpoint; its numerical scores need not exactly match the earlier FP16 results. No adapter is loaded. Actual shared/tied parameter objects are counted once by `model.parameters()`.

FP32 full-model weights and gradients consume roughly 2.4 GB each for a 0.6B-parameter model, before activations and temporary buffers. The directional repair methods also keep several full-weight CPU directions and a restoration snapshot. This is a materially heavier experiment than the frozen-reader work. Save approximately 30 GB of free disk space for the first two gates (six mutable arms), potentially 60 GB or more if all four gates complete, excluding existing cache and other reports. These are estimates, not measured requirements. Run one arm/stage at a time (the orchestrator does this). If hardware cannot support it, do not silently substitute a LoRA arm or lower precision: the experiment would change. Interrupted incomplete fits restart; complete fit and prediction locks are reused. No separate experiment JSON download is needed.

## Seven matched arms

* A: reference backbone never thawed; reader-only training at gate 1; immutable mathematical baseline at other gates.
* B: normalized full-parameter acquisition-gradient updates, no compensation.
* C: joint acquisition gradient plus gradient of original-function KL, equal nominal loss weighting before normalization.
* D: acquire, then explicit KL-gradient repair with actual nonlinear backtracking.
* E: project away a conflicting component along the aggregate preservation proxy gradient (teacher argmax NLL plus KL); this is a one-constraint local projection, not an exact full preservation nullspace.
* F: acquire, then measure centered finite differences of the Fisher-weighted full-vocabulary log-probability residual along up to three directions (repair gradient, acquisition gradient, dense random direction); solve a regularized small coefficient system; test three correction scales with exact snapshot restoration between probes.
* G: the same directional mechanism interleaved at three scheduled points of acquisition, rather than just afterward.

Every mutable arm starts at the same bare FP32 checkpoint on each independent entry branch. All backbone parameters are eligible; eligibility is not evidence every parameter was changed or received gradients. No LoRA, S or R. Same initial step budget and global update norm, different repair and forward/backward costs. The existing 0123 failed attention reader is used for binding, trainable equally in each arm, frozen for mathematical gates. It is not used to calculate native mathematical answers. No optimizer is carried across gates. SGD updates are globally normalized; reader uses AdamW. These are pilot methods, not claims of optimal hyperparameters.

## Batteries and gates

| Stage | Fit | Dev | Blind | Scope |
| --- | ---: | ---: | ---: | --- |
| 1 | 2 | same 2 | same 2 | 0123 elementary binding, exposed fitting diagnostic only |
| 2 | 10 | 10 | 10 | signed arithmetic and exact zero-line cancellation |
| 3 | 9 | 9 | 9 | composition, rational values, expectation |
| 4 | 45 | 45 | 45 | algebra, geometry, discounting, T00–T08, HDB00–HDB08 |
| Preservation | 13 | 13 | 13 | independent cancellation, fraction, comparison, coreference, syntax, logic, instruction, sequence, classification, facts |

231 authored cards total (77 per split), plus the two exposed 0123 binding cases, sixteen exposed 0120 calibration cases split by complete reversal groups, and three exposed 0122 fresh-graph cases for G1 regression. Reference activation experiments additionally use three matched pairs: the 0123 pair, a signed cancellation remainder change, and an inside/outside language change. Existing `nanojev_game_math_registry.execute`, `nanojev_qwen_zero_line_backbone.make_cases`, `nanojev_qwen_lm_radius_ladder._case` and `nanojev_qwen_hdb_frontier._row` are actually invoked. The T cases ask for the final integer rather than reproducing the original one-step reduction contract. HDB cases use the original exact payoff oracle, explicit payoffs and distinct payoff matrices. This is a bounded sample, not the entire historical V10 database or complete curriculum. Exact prompt disjointness is checked within new cards; this does not prove absence from model pretraining or all historical datasets.

**Independent entry gates:** gates 1 and 2 both run for all seven arms, even if the elementary reader fails. They start independently from W0 so failure in G2 cannot prevent the mathematical experiment. Gates 3 and 4 inherit the preceding successful mathematical checkpoint only. Prior mathematical fit examples are replayed cumulatively and previously correct mathematical holdout decisions are tested for retention. Previously tested blind splits become exposed retention controls, never used for training. Future acquisition holdouts are not consulted in candidate fitting or gating. Immutable reference inference on all public prompt decks is completed before rewrites, and mathematical baseline aggregates are published at finish even if later gates never run. This design change is intentional: mathematical acquisition is the central research objective, not a consequence of the current reader's trainability.

Mathematical acquisition combines order-balanced A/B loss with teacher-forced exact answer token loss (including EOS when available). Blind scoring records both answer-order symmetrized discrimination and actual unconstrained greedy answer generation (20-token cap; exact rational equivalence accepted). No oracle enters inference. Original-output preservation KL is on full next-token distributions at the A/B question positions, not all sequence positions. Native binding and language distributions and initially correct mathematical cases form preservation controls. Unconstrained mathematical output preservation is independently gated. G1 diagnostic uses the saved frozen 0122 Arm A reader on three exposed prompts, with its selected object threshold; correct objects lost and false object counts are compared with the FP32 original reference. G1 is a regression gate, not a differentiable term in the repair solve.

Gate 1 requires both bindings and minimum positive-vs-negative margin 0.1. Mathematical gates require at least 80% order-balanced exact choice and 60% free exact answer on both dev and current blind cohorts, with no loss of previously correct native decisions, no loss of previously correct generated mathematical answers on reached blind stages, no loss of prior mathematical acquisitions on retained blind cohorts, and mean preservation KL <=0.1 nats on dev and blind. The G1 regression gate requires no baseline-correct objects lost, no increase in aggregate false-object count, no object overflow. The fixed end-of-budget checkpoint is scored after locking predictions; no blind checkpoint selection. A passed gate is a local research criterion, not operational competence certification. A passed baseline with no gains is not acquisition.

## Sensitivity and correction evidence

Reference activation probes record per-coordinate last-state deltas and aligned-token per-coordinate RMS deltas across all available hidden-state layers, and one full-width randomized last-token perturbation per actual transformer layer, followed by removal and output restoration check. These are response measurements, not identified math-only circuitry. F/G use full-weight sampled directions, no low-rank parameter restriction, no explicit full Jacobian. The finite-difference Jacobian has only the sampled direction columns. The repair residual is the original-teacher-probability-weighted log-probability difference, including the full vocabulary. The linear predicted residual and actual nonlinear residual are separately reported. Corrections are accepted only if measured preservation improves lexicographically (lost decisions then mean KL), fitted acquisition count is not reduced, binding margin is retained if already above gate, and fitted loss increases by no more than 0.03. Otherwise restore the original candidate. A failed correction remains negative evidence. The proxy for E is not a proof that other capabilities are unaffected. Activation probes are diagnostic, not automatically converted into weight edits.

## Artifacts

Under `runtime/reports/gamer-v1/g1-g2-compensatory-rewrite-0124/`: sealed public prompts and separated answer files; immutable reference full-distribution tensors and free outputs; `reference/activation.json`; G1 baseline; each `arms/<arm>/gate-<n>/fit.json`, full experimental `checkpoint.pt` (reader only for A), `predictions.json`, `prediction-lock.json`, `result.json`; consolidated `result.json` with reference mathematical scores, gate progress, acquisition gains/losses, G1 review, repair effects, checkpoint identities and update counts. `predict` uses the prior Python audit-hook answer-access guard in a separate subprocess; not an OS security sandbox. Completed locks detect changed bytes; not a guarantee against a privileged adversary rewriting all metadata. Retain the negative as well as positive checkpoints.

## Local verification and limitations

Actual existing mathematical author dependencies were exercised against the supplied historical source archive. CPU fixtures exercised every arm's update mechanism, math and binding gradients, directional finite difference/regularized solve, exact restoration, dense random directions, seal tamper refusal, checkpoint roundtrip and separate-process answer guard. A small causal-model fixture exercised real reference/fit/predict/score/lock/resume for gates 1 and 2 of the sequential repair arm; generation, G1 and activation capture were simulated in that lifecycle fixture, not live Qwen. CUDA, all real gate outcomes, full parameter hardware requirements and global functional equivalence are unverified. Finite batteries cannot establish that arbitrary rewriting leaves every other model function untouched. Do not promote any checkpoint from this experiment into production.
