# G2-0083 checkpoint-resume and G1 retention evaluation repair

## Incident and evidence

The original G2-0083 CUDA run completed all 400 training steps and finished G2 development and challenge evaluations (both reporting zero complete binding graphs). It then raised `GAMER_GRAPH_INFERENCE_SPANS_CONFLICT` while evaluating **G1 retention on the G2 checkpoint**, specifically on the zero-hidden branch of `nanojev_gamer_v1_graph_curriculum.evaluate`.

The signed native graph head correctly rejects `inference_spans` for `active_stage='G1'`, because this stage outputs span logits and does not consume input spans. The existing curriculum attempted this illegal call inside its G1 zero-hidden ablation. The G1-0082 sealed certificate already provides the required G1 causal-ablation evidence; G2-0083 only needs to measure whether G1's complete-object skill is retained on the newly trained G2 weights.

## Repair

Only the independent `nanojev_gamer_v1_g2_native_training_0083.py` orchestration module changes. The hash-bound G1-0082 graph curriculum, graph head, native G1 interface, original certification and checkpoints remain unchanged.

* G2 development and challenge evaluation: unchanged, including 32-case zero-hidden and binding-component causal checks.
* G1 retention on G2 checkpoint: compute complete-object accuracy over all development/challenge examples with `ablation_limit=0`; this avoids passing G2-style input spans to the G1-only head. G1-0082 provides the separate, authoritative G1 causal evidence.
* `resume` verifies the original G1-0082 certificate, the committed pretraining seal, the G2 checkpoint and parent hashes, the exact composite binding, frozen G1 parameter retention, and saved evaluation-report lineage. It never calls the optimizer, resamples the corpus, or replaces existing evidence.
* Existing G2 evaluation reports are reused only if their recorded case-level success counts recompute their exact rates and all checkpoint/dataset/source hashes match.
* Complete missing G1 retention evaluations, run the original G2 developmental gate, perform gold-blind prospective G2 inference using the already-committed seal, and independently score trained versus untrained G2 edge metrics.
* Formal status remains **blocked** unless all original G2 and prospective acceptance requirements pass. G3 remains locked even when the G2 research gate passes.

## Run from `main_computer\\nanojev`

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_native_training_0083 resume --device cuda
```

The default existing campaign is `..\\runtime\\reports\\gamer-v1\\oracle-fabric-g2-native-training-0083`.

The reported `0/80` and `0/32` G2 exact-binding results are **not** interpreted as a training or certification pass. The recovered workflow writes edge precision, recall, F1, number of gold edges unreachable from predicted G1 objects, conditional exact binding scores, and paired comparison to the untrained head. Distinguishing poor decoding from an ineffective representation requires those measurements. This repair does not change training losses, decoding thresholds, or certification gates.

## Safety / regression tests

Tests cover no-retrain resume, immutable evidence, report lineage and case-level metric parity, G2-only causal ablations, G1 retention without G1-stage inference spans, G1/G2 hash isolation, and the original native-stage/frozen-parameter tests.
