# G1/G2-0120 — Backbone competence and adapter interference

## Why

0117 failed on unfamiliar semantic structures; 0118 isolated the failure even with public candidate endpoints; 0119 compared passive and active readouts, a fixed and trainable R, and historical native A/B and Yes/No next-token scoring. The historical model with its adapter showed weak native binding competence and considerable option-order disagreement. These observations do not establish that the unmodified checkpoint lacks the capability. Nor do they establish that a better reader can recover it. Suspend reader/register training and isolate the model and interface factors first.

## Design

Four matched conditions: `bare`, `bare_S`, `adapted`, `adapted_S`. The historical model ID is resolved locally with the same precision used by the historical loader. Actual bare predictions are captured before the PEFT adapter is attached to that same model object. The exact historical adapter is fingerprinted and loaded without training. The static S is reconstructed from the verified 0111 checkpoint. Adapter disable is an additional logit-parity diagnostic, not a substitute for actual bare predictions. The model receipt records the resolved commit when available, tokenizer and chat-template digests, adapter/S fingerprints, versions and precision. Historical 0119 did not independently pin the original model revision; replay its native legacy scores to identify possible drift.

Each condition runs the same prompt and candidate inventory, with all four possible relationships and both complete-assignment option orders. Methods:

- Legacy single-token A/B and Yes/No logits, identical question construction to 0119; canonical A/B margin averages two option orders after reversing the orientation of the second.
- Wider single-token label variants including recognized whitespace spellings; logsumexp over label token sets and total probability mass assigned to both labels. This is not sequence likelihood or exhaustive verbalizer coverage.
- Ordinary greedy autoregressive generation, with no forced A/B choice: request two name-to-name binding lines. The same KV-cache greedy decoding path is used with and without S, and S is appended to neither generated output nor subsequent tokens. It is prepended once before the prompt. Preserve raw outputs, stop/length information and parser status. Strict complete exact, exact mapping with extra text, incomplete, ambiguous, unparsed and token-limit cases remain distinguishable. Do not infer a mapping from gold labels or infer semantic incapacity from a format failure.
- Thinking-enabled greedy generation on the calibration cohort only, bare and adapted without S, capped by `--thinking-tokens` (default 256, zero disables). Unfinished thinking is reported separately.
- No-cue likelihood control uses the same inventory and strips the binding instruction. This is a distribution-changing intervention, not a natural language task with a unique answer.

No optimizer, gradient training, production checkpoint write, operational G1 extraction, or G3 promotion.

## Populations and execution

| Cohort | Cases | Paired reversals | Role |
|---|---:|---:|---|
| calibration | 16 | 8 | explicit repeated-name statements, two declaration orders |
| exposed0118 | 32 | 16 | direct comparison with previously failed contrasts |
| exposed0119 | 48 | 24 | direct comparison with previous fresh evaluation |
| fresh | 16 | 8 | new negated-partner and ordinal-partner constructions |

Eight paired groups each in calibration and fresh; two underlying instruction constructions per new cohort. The previous cohorts are retrospective. All conditions are evaluated without fitting or selecting a winner. Candidate identities and relation are supplied; this is conditional binding, not unrestricted graph extraction.

From the NanoJEV directory:

```
& $py -u -m tools.nanojev_gamer_v1_backbone_competence_0120 audit
& $py -u -m tools.nanojev_gamer_v1_backbone_competence_0120 selftest
& $py -u -m tools.nanojev_gamer_v1_backbone_competence_0120 calibrate --device cuda
& $py -u -m tools.nanojev_gamer_v1_backbone_competence_0120 run --device cuda
```

`calibrate` and `run` use the same seal, source and token budgets; run reuses locked calibration. Keep flags unchanged for a resumed experiment. `predict --split ...` and `score --split ...` are available for a specific sealed cohort. `finish` summarizes all completed cohorts. Each case/condition shard is committed atomically and verified before scoring. Prediction runs separately from scoring; a Python file-open audit guard blocks secret-answer opens (not an OS sandbox). Historical verified 0119 seals and checkpoint identities are retained. No downloads attempted; base model must already be in the local model cache or local model path. Outputs under `../runtime/reports/gamer-v1/g1-g2-backbone-competence-0120` include model receipt, cohort seals, individual outputs, lock, per-cohort result.json, final result.json and RESEARCH_RECORD.md.

## Interpretation

Use paired exact-correct transitions, not independent marginal accuracies alone. Measure all four within-row/column pair comparisons, exact complete bindings, reversal pairs where both directions succeed, paraphrase pairs (where available), raw option-order disagreement and both-order correctness, label probability mass, absolute yes/no decision accuracy, strict and extra-text generation scores, and relation/structure breakdowns.

- Bare generated success but adapted failure: evidence of adapter interference for those prompts and this output interface.
- Both generated arms succeed, logits fail: evidence that the chosen interrogation method is insufficient; compare order and label-mass diagnostics.
- Bare and adapted explicit calibration failure: investigate template/tokenization, output parsing, decoding regime and model size before attributing failures to fundamental capability.
- Explicit success, implicit failure: localize the gap to the tested binding constructions.
- S effects: paired intervention conditional on model and interface; no assumption of benefit.
- No-cue score collapse supports dependence on the instruction, but is not alone proof of semantic competence. Option-order averaging may conceal inconsistent answers; both-order correctness is separately reported.
- Failure across these arms does not establish absence of information in Qwen or incapacity under other prompts/decoding or models. The sample is small and authored; no certification claim.

## Verification and limitations

CPU fixture verifies label variants, greedy cache sequencing for bare and S, strict mapping parser, paired reversal scores, all four condition persistence/scoring, immutable replay and tamper detection, answer guard. Separately, the actual 0118/0119 author sources and historical 0119 question-construction functions were exercised for all 80 exposed prompts with and without binding cues; question parity passed. Actual installed Qwen weights, PEFT attachment and CUDA execution are not available in the patch build environment. Audit verifies source and historical lineage, not successful model loading. Runtime records the actual resolved model and adapter checks; bare/adapted comparisons are valid only for that runtime checkpoint.

## Freeze-state repair (2026-11-25)

Live calibration completed bare and bare_S (16 cases each). After the adapter-disable parity check, the frozen guard rejected the adapted condition. The adapter-disable context may restore active LoRA parameter requires_grad flags; the exact flags are now logged. The repair refuses any existing non-null gradient, reapplies eval and requires_grad_(False) after leaving the context, checks restored adapted logits, and checks the freeze state before and after every inference case. No scoring, author, token budget, weights, or protocol semantics changed. Existing seals from the exact audited predecessor source may be reused only when all identity fields match; legacy shard hashes and locks remain enforced. The actual executing source is separately recorded in freeze-repair-execution.json and each new prediction shard. New runs use the new source in the protocol seal. CPU tests simulate adapter-context flag restoration, reject non-null gradients and identity drift, and exercise original cache/scoring fixtures. Actual PEFT/CUDA remains a live acceptance test.
