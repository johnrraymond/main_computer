# G1/G2-0121 — Backbone-preserving semantic acquisition

## Hypothesis and experimental scope

0120 calibration identified a loss of explicit assignment discrimination after the historical transition-005 LoRA was attached. Bare Qwen attained 16/16 order-symmetrized assignment decisions on eight base problems; this was not evidence of order-invariant answering or general competence. The new lineage tests whether a common semantic readout can be acquired without that adapter. It is research only, not a replacement of any production checkpoint or G3 admission.

## Arms

- A: untouched Qwen + fresh external span and binding readers.
- B: same bare Qwen and readers + newly initialized trainable S prefix.
- C: same + newly initialized trainable S and R suffix. R is a graph readout after the complete formatted prompt, not a textual candidate query. S/R vectors have normalized RMS fixed at 5% of embedding RMS. No historical S weights are used.
- D: bare Qwen + readers + full additive direct weight delta to final-layer attention q_proj, initially zero. This is mathematically a direct weight update to that matrix, implemented as an isolated delta; no LoRA factorization, merge or modification of the original checkpoint.
- E: bare Qwen + readers + new rank-four low-rank q_proj delta (alpha 8), initially zero effective change. Tests new low-rank adaptation with preservation; not the original LoRA.
- H: exact historical LoRA + identical newly initialized readers, frozen adapter and base. H is comparator only and excluded from new-lineage selection.

All six arms share reader initialization, full-width layers 14/21/28, reader width, training order, objective, number of graph updates and validation grid. D/E target the same projection and use the same update LR but differ in trainable parameter count and factorization; do not infer a universal LoRA-vs-direct conclusion. The shared G1/G2 architecture is new and smaller than the historical ensembles, not a bit-for-bit rerun of 0113 or 0116. G1 enumerates input-derived token spans up to 16 tokens with exact whitespace-trimmed offsets; G2 learns independent binding labels using gold endpoints and predicted false proposals during fitting. Operational evaluation uses only predicted objects; conditional binding tests receive all four competing anchor pair hypotheses. No answer-derived relationship count caps. A fixed 256-object resource bound follows the graph contract, with saturation reported. Default two epochs, reader LR 3e-4, register LR 3e-4, weight delta LR 1e-5. Nine validation-only admission combinations are evaluated per epoch, selected by strict graph exact then G2 F1 then G1 F1. No retrospective fresh selection.

## Preservation

Pinned bare Qwen checkpoint and tokenizer from 0120 model receipt, verified adapter fingerprint, and replay of 0120 bare calibration scores before training (decision parity plus <=0.05 canonical-score drift). Train preservation KL on 0119 FIT prompts only, both option orders, full next-token output distribution of the bare model. One teacher query per four graph updates on S or weight-changing arms; R itself is graph-readout-only and is absent from the conventional native interface. Calibration is development gate, not distillation data. At each epoch preserve every baseline-correct calibration assignment, mean full-vocabulary KL <=0.1 nats, mean expected A/B answer mass >=90% of bare reference. Those are initial research thresholds, not established statistical equivalence bounds. If an increment fails the gate it is retained as diagnostic evidence but ineligible for selection; subsequent epochs may still be tested. Best admissible epoch is retained; if none passes, the arm is diagnostic only. Bare readers should not change native logits. Heldout native assignment losses and gains are reported separately; order disagreement and answer-token mass remain visible. These tests measure scoped conditional binding preservation, not preservation of every language or mathematical skill. No ordinary generation test in 0121; 0120 is its separate diagnostic.

## Populations

Original 0113 cohort seal: 96 fit, 64 validation, 192 exposed historical test (actual counts read from the installed seal). Exposed 0117 seven-structure suite (28 graph prompts), including provisional implicit annotations, is diagnostic. New graph holdout uses the installed 0110 generator, seed 1214026, twelve new bases x four modes (48 prompts), not structurally independent of that generator; compare to accessible historical prompt hashes. New conditional suite contains 32 prompts, eight relation/construction groups, two new instruction constructions, each direct/reverse/paraphrased. Public anchors and supplied relation type mean conditional discrimination only. Native preservation: prior 0120 calibration (16), prior 0118/0119 (32/48), prior-authored 0120 fresh (16), and new conditional suite. Prior-authored sets are exposed diagnostics for this research lineage, not claimed blind. 0119 fit public questions used in distillation are never scored as fresh. Grouped structure and exact matching metrics matter more than number of individual pair comparisons.

## Running

From nanojev directory using the established $py interpreter:

```
& $py -u -m tools.nanojev_gamer_v1_backbone_preserving_0121 audit
& $py -u -m tools.nanojev_gamer_v1_backbone_preserving_0121 selftest
& $py -u -m tools.nanojev_gamer_v1_backbone_preserving_0121 run --device cuda
```

Requires installed 0110 generator, 0113 cohort, 0117 fresh seal, 0119 fit seal, 0120 prepared cohorts and completed calibration lock, corrected 0120 module, local pinned Qwen model and historical adapter. Does not require completed 0120 unfamiliar-population inference. Results automatically under runtime/reports/gamer-v1/g1-g2-backbone-preserving-0121. Completed arms and prediction shards resume, interrupted arm fitting restarts that arm; no production writes. Configuration changes require a new --output path. All selection and checkpoint hashes are locked before separate prediction subprocesses. Fresh answers are blocked by the existing Python audit guard during inference (not an OS sandbox); score opens them only after all locks validate. Teacher and selected checkpoints contain only external reader/register and declared deltas, not full backbone weights. Automatic RESEARCH_RECORD.md and result.json record results. Historical training logs and artifacts are read-only.

## Verification and limitations

CPU selftest exercises actual shared reader code, direct and low-rank deltas with frozen base, S/R gradients through a tiny causal decoder, zero-delta parity, checkpoint roundtrip, fresh contrast author contract, full six-arm train/selection/predict/score/resume fixture, tamper rejection and separate-process answer guard. The lifecycle fixture substitutes a small model and tokenizer, not real Qwen. Live CUDA memory/runtime, historical generator execution, pinned checkpoint loading and actual PEFT attachment cannot be verified in the packaging environment. No successful training outcome or semantic certification is asserted. The first diagnostic comparison is A versus H; a positive result supports the new bare lineage only for the tested tasks and reader architecture.
