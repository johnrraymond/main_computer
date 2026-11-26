# G1/G2-0123 — Elementary Semantic Binding Mechanism Laboratory

## Motivation and governing hypothesis

0122 raised ordinary graph G1 F1 to 97.39% in arm A and fresh G2 F1 to 33.08% in arm C, but all six arms resolved zero of 24 fresh matched semantic reversals. On that population the bare native likelihood interface resolved nine reversals. Contrast training loss remained near its zero-logit baseline (1.5 ln 2 = 1.03972). This does not establish whether the information is absent, the reader cannot address it, or optimization is failing. It does not justify another full architectural bakeoff. The new laboratory deliberately isolates the `owns` relation and leaves G1 and the entire backbone unchanged.

## Exact mechanism

The laboratory reuses the 0122 E_attention reader (192-wide, layers 14/21/28), its original initialization seed, span construction, pair-conditioned attention, and contrast loss. No S, R, PEFT, LoRA, weight delta, G1 loss, or ordinary graph loss is used. Qwen's model ID, revision, tokenizer and chat template are verified through the existing 0121 bare-model loader against the 0120 receipt. All model parameters are frozen, and unauthorized gradients are rejected. Hidden states are captured once for each training prompt; only the reader is optimized. Public candidates contain the four known endpoints and the relation, not their correct assignments. This is conditional binding, not end-to-end extraction.

Training uses a full balanced batch of complete reversal pairs, one optimizer step after all opposing examples contribute. The original loss is mean softplus(negative - positive) plus half binary cross entropy. The default learning rate is 0.001, at most 200 updates per training gate; check at update 1, every 10 updates and the final budget. Training stops as soon as all cases are correct and the minimum positive-versus-negative margin is at least 0.1. The checkpoint on a failed fit is retained only for diagnosis. The eight-pair stage starts from the one-pair checkpoint but resets the optimizer. No heldout label enters optimization. These changes (removing graph loss, balanced batching, small population and changed LR) jointly test trainability; a positive result does not uniquely identify the cause of 0122's failure.

## Ordered gates

| Stage | Population | Minimum exact cases | Minimum complete reversals |
|---|---|---:|---:|
| one | One pair, deliberately exposed training | 2/2 | 1/1 |
| eight | Eight pairs, including the first pair, exposed training | 16/16 | 8/8 |
| names | Twenty new actor-name/action-ID pairs, identical grammar | 38/40 | 19/20 |
| paraphrase | Same twenty new pairs, passive assignment and reversed declaration order | 36/40 | 18/20 |
| structure | Same twenty new pairs, ten negation/unique-owner and ten aligned-column constructions | 32/40 | 14/20 |
| no cue | Each reached stage, identical inventory stripped of its binding clause | Chance | 0 complete reversals |

Each prompt has four candidate edges with two positives. Direct and reversed records have identical inventory/anchor spans; they differ only in the binding clause. Names and action numbers in the heldout population are disjoint from the training population; the grammar and the word task are deliberately shared in the first holdout. The wording and structure stages reuse the same heldout lexical population, so their results are not independent replications. They test controlled transfer. All records are authored before training, shuffled deterministically and sealed. There are 138 stage records (136 distinct full prompts because the one-pair gate is included in the eight-pair gate). Exact prompt-hash collision checks against available historical prompt manifests are performed; hash exclusion is not a proof of semantic novelty.

The fixed eight-pair checkpoint is used for all three holdout gates. No holdout retuning is permitted in this run. A failed gate stops further stages, including subsequent holdout access. The no-cue control is evaluated alongside every reached stage rather than waiting until the end: identical public inventories reuse exactly the same control predictions; complete reversal accuracy must be zero and pairwise accuracy exactly chance. This is a negative-control integrity check, not independent evidence of semantic competence. A successful cue-present holdout combined with this control would demonstrate dependence on the removed portion, but not rule out every possible linguistic shortcut.

## Instrumentation

For both training gates the report includes initial and final scores and losses, assignment interactions (z00 + z11 - z01 - z10), minimum positive-versus-negative margins, update-by-update sampled loss and gradient norms, per-example gradient norms and cosine similarity for the first complete reversal pair, paired gradient norm, per-parameter gradient norms, and layerwise activation differences. Activation diagnostics compare mean endpoint vectors, final states, and position-aligned post-anchor token states where token counts match. Endpoint equality is expected for causal representations preceding the binding clause. Gradient cancellation is measured, not assumed to be the cause. Individual gradients can oppose one another even when representations are informative.

Each prediction record also contains the native bare Qwen A/B likelihood comparator in both option orders, using the existing 0120 interface. Symmetrized likelihood is not a claim of ordinary generation ability. The native comparator is never a training target. Each reached stage records full scores, public text, gold labels (only after prediction lock), case-level exactness, row/column pairwise accuracy, and complete reversal counts.

## Interpretation

- Failure on one pair with differing activations and nonzero gradients: investigate loss, addressing, optimization, or the specific feature pathway, rather than scale training. A zero gradient or identical available features provides a more specific mechanistic lead. A failed fit is not proof that Qwen lacks the information.
- One and eight pass, names fail: memorization without demonstrated lexical transfer.
- Names pass, paraphrase or structure fails: narrow grammar dependence.
- All gates pass: evidence of elementary conditional ownership transfer only. Other relations, ordinary graph reconstruction, preservation beyond frozen weights, and production readiness are outside scope.

## Run

From the nanojev directory with the existing environment and completed 0122 result:

```powershell
& $py -u -m tools.nanojev_gamer_v1_elementary_binding_0123 audit
& $py -u -m tools.nanojev_gamer_v1_elementary_binding_0123 selftest
& $py -u -m tools.nanojev_gamer_v1_elementary_binding_0123 run --device cuda
```

The run verifies the 0122 parent identity and operational report hash, uses the pinned locally cached Qwen checkpoint, and automatically stops after the first failure. Completed training gates and locked predictions are reused; incomplete training restarts, while completed prediction shards are reused by identity and digest. Holdout predictions run in a separate process with the 0120 Python answer-access guard; this is not an OS security sandbox. The author module is only imported for preparation and self-test. Reports are written under `runtime/reports/gamer-v1/g1-g2-elementary-binding-0123/`: `result.json`, stage `result.json`, training `fit.json`, `reader.safetensors`, seals and prediction shards. Do not change the parameters in the same result directory; a new investigation needs a separate `--output` directory and must be treated as a new protocol. No production changes or certification.

Local verification: the actual 0122 reader and contrast loss run against a tiny causal decoder; a deliberately separable synthetic hidden-state fixture demonstrates successful optimization and the actual first-stage training/prediction lifecycle. Stop, resume, lock tamper and guarded answer-access fixtures pass. Actual Qwen checkpoint/CUDA execution is not performed in the build environment. Neither fixture establishes semantic competence.
