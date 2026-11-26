# Gamer v1 — backbone research: current direction and operating guide

Record date: 2026-11-25 (Pacific). Completed research through G1/G2-0122; 0123 protocol delivered, operator result pending. Research only: no canonical promotion, no G3 unlock, no general semantic certification.

Read this first. The [historical evidence ledger (0103–0114)](GAMER_V1_BACKBONE_RESEARCH_EVIDENCE_0103_0114.md) remains historical. The [new evidence ledger (0114–0123)](GAMER_V1_BACKBONE_RESEARCH_EVIDENCE_0114_0123.md) records measured results; the [retained assets register](GAMER_V1_BACKBONE_RESEARCH_RETAINED_ASSETS.md) identifies the saved research state. These documents supplement the stage contracts and release gates, not replace them.

## Objective

Recover faithful typed semantic graphs from natural language: G1 objects and G2 relationships. Extend numerical access to semantic computation without damaging the original backbone. Operational graph extraction, conditional binding with supplied candidates, native likelihood, ordinary generation and exact graph recovery are separate capabilities. None may stand in for another.

## What has been established and retained

| Area | Established finding | Retain |
|---|---|---|
| Backbone | Qwen3-0.6B bare calibration 16/16, historical LoRA 12/16; bare has genuine but limited order-symmetrized binding discrimination. | Pinned bare checkpoint; old adapter only as historical comparator. |
| G1 | 0122 A fresh graph F1 97.39%, 1,368/1,368 gold spans correct top kind before admission, 1,325 admitted. | 0122 A checkpoint and earlier 0114/0115 ensembles as distinct reference baselines. |
| Multilayer readout | Earlier adapted-lineage experiments showed complementary observations and high generator-family scores; not proof of semantic transfer. | Original multilayer interfaces, frozen reader and corpus evidence. |
| R | 0122 learned R achieved 33.08% operational G2 F1 and 38.69% with gold endpoints, exceeding fixed R in this bakeoff. | 0122 C checkpoint as a diagnostic comparator, not a production solution. |
| S | Can alter behavior even with frozen weights; 0122 S/R loses five previously correct heldout native decisions. | S experiments and checkpoint provenance; no default S deployment. |
| G2 | All six 0122 arms resolved zero of 24 fresh reversals; native bare likelihood resolved nine. | Matched reversals, no-cue controls, scores and checkpoints, including failed arms. |

## Decisions now in force

1. Freeze the original backbone; exclude historical LoRA, new weight updates, and S/R training from the immediate elementary experiment. Do not erase the historical lineage or claim that all LoRA is harmful.
2. Preserve G1 rather than rebuild it. The immediate admission gap is 43 gold objects in the 0122 A fresh graph cohort. This is a scoped finding; exposed 0117 still has 46 missing objects and no complete end-to-end graphs. Any later admission change needs its own validation and new holdout.
3. Stop broad architecture bakeoffs until elementary relationship trainability is established. Do not extrapolate the 0116 high generator-family F1 to unfamiliar semantic structures.
4. Separate graph and contrast training in the next experiment. Diagnose activation availability, loss, gradient cancellation, margin and optimizer trajectory before expanding scope.
5. Retain evidence and negative results, including selection and prediction locks. Do not tune on already scored holdouts or overwrite existing evidence with a new protocol.

## Next execution: 0123 elementary ownership binding

Use the installed patch `G1-G2-0123-Elementary-Semantic-Binding-Mechanism-Laboratory.zip`. From the `nanojev` working directory with the established `$py` interpreter:

```powershell
& $py -u -m tools.nanojev_gamer_v1_elementary_binding_0123 audit
& $py -u -m tools.nanojev_gamer_v1_elementary_binding_0123 selftest
& $py -u -m tools.nanojev_gamer_v1_elementary_binding_0123 run --device cuda
```

The 0122 E attention architecture is newly initialized; it is not a continuation of the 0122 E trained weights. No graph training or backbone updates. One pair then eight pairs, at most 200 updates per training gate, LR 0.001, same contrast loss, paired gradients and minimum margin 0.1. The eight-pair checkpoint is fixed for later holdouts. Full instructions: [0123 protocol](G1_G2_0123_ELEMENTARY_SEMANTIC_BINDING.md).

| Gate | Correct full cases | Correct complete reversals |
|---|---:|---:|
| One pair (fit) | 2/2 | 1/1 |
| Eight pairs (fit) | 16/16 | 8/8 |
| Unseen names | 38/40 | 19/20 |
| Paraphrases | 36/40 | 18/20 |
| Structural transfer | 32/40 | 14/20 |

No-cue control must return identical scores across matched inventory-only pairs, chance pairwise accuracy and zero complete reversals at every reached stage. A failed gate stops progression. Fit success alone is memorization, not generalization; success cannot uniquely attribute 0122 failure to graph/contrast gradient interference because batching, learning rate and training population also change. The later holdouts reuse the same lexical bases, not independent replications. No further relationship expansion is justified before examining the 0123 result.

Result expected at `runtime/reports/gamer-v1/g1-g2-elementary-binding-0123/result.json`, with per-stage fits, predictions and locks. Actual Qwen/CUDA result not supplied as of this record. Do not report fixture success as live success.
