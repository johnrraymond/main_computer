# Frozen semantic integrity verification: G1-0115 and G2-0116

## Research decision

Conditionally accept a component only to the extent that downstream evidence warrants it. Reopen the relevant component when a falsifying observation appears. This verification is not new training, model selection, certification, or a stage-gate change. The seven early 0116 cases recovered 196/196 binding edges collectively with reconstructed endpoints; the new-reader integration attained 98.21% edge F1. These control cases alone do not establish semantic generalization. The 0115 policy is tested alongside the 0114 policy with the exact 0113 checkpoint inventory and the 0116 reader checkpoints and fixed decoders.

## Execution from nanojev

```powershell
& $py -u -m tools.nanojev_gamer_v1_semantic_integrity_probe audit
& $py -u -m tools.nanojev_gamer_v1_semantic_integrity_probe selftest
& $py -u -m tools.nanojev_gamer_v1_semantic_integrity_probe run --device cuda
```

Do not run two full CUDA model processes concurrently unless memory allows it. The original 0115 and 0116 runs can be stopped and resumed independently; no probe command edits their artifacts. The default probe snapshots up to twelve completed, distinct 0116 bases (one completed prompt per base) and adds eight hand-authored binding fragments in four matched semantic pairs. Actual cached count is reported. New prompts are screened against known prior prompt hashes, not asserted to be structurally disjoint from all possible training data. The four fragments with implicit ownership are explicitly identified as exploratory due to possible ontology ambiguities. They are not full decision problems. The other fragments use explicit name references and numeric associations. All gold binding annotations are hand-authored in code, not inferred by the tested model. Same object offsets/types and differing gold edges within each pair are checked by selftest.

For a faster cache-only run, use `run --device cuda --cache-only` (no Qwen loading); this excludes the new contrasts. `--cached 4` reduces the initial cached base count and creates a separate sealed output directory. Repeating exactly the same command resumes its sealed case set. The full run is in `runtime/reports/gamer-v1/semantic-integrity-0115-0116/`; cache-only and nondefault count runs have suffixed directories.

## Interventions

* Normal: exact replay of 0116 endpoints and the three named new-reader policies on cached cases. A replay discrepancy is flagged as a blocker to interpretation, not averaged away. 0115 source shard hashes and answer/prompt commitments are verified; numerical intervention uses the same 0115 policy on 0116 states, rather than rerunning the full 0115 population.
* Zero all: zero all captured representations; literal prompt, tokenizer boundaries and frozen decoder are retained. G2 fixed-endpoint controls retain inferred endpoint kinds/locations. Residual performance measures the frozen system's behavior under removal of representations, not the maximum competence of a retrained shortcut-only model.
* Shuffle tokens: cyclic nonidentity permutation of eligible token states, identically across layers and S/original sources; ineligible positions are untouched. This tests dependence on representation alignment. It does not remove global semantic information encoded in remaining states.
* Embedding only: preserve actual original input embeddings; zero contextual representations. This is an ablation of the existing architecture, not retraining an embedding-only head.
* S replaced: feed original states to the S-conditioned readers at corresponding layers. Tests dependence on the S-conditioned states, not a controlled causal estimate of the learned prefix versus an equally sized random/zero prefix.
* G2 no geometry, no kind, no lexical, no caps, and no structure: respectively zero explicit positional features, zero learned endpoint-kind embedding output, zero explicit lexical equality, disable trained cardinality caps, and combine these interventions. Endpoints and legal pair exclusivity remain. Implicit ordering, token text, contextual representations and endpoint definitions can still encode structure. G1 decoder/token coordinates remain unchanged; the zero/shuffle conditions probe residual G1 shortcuts without claiming to isolate all such features individually.
* Fixed G2 endpoints: always use the normal inferred endpoint view so the G2 intervention is not confounded with G1 destruction. End-to-end interventions use the ablated G1 endpoints and similarly ablated G2 inputs. Normal fixed-endpoint results are also the operational end-to-end baseline.
* Agreement: report correct/incorrect edge support among the 15 new readers, distinct from the earlier 23-reader inventory. Report per-condition object/edge F1, exact graphs, recoverability, reachable and upstream misses, and pairwise directional relationship changes.

No new fit; no optimizer. All reader weights and policy thresholds are frozen, except the specified inference-time cap-removal intervention. The existing 0103 reranker is not a comparator in this suite. No claim of superiority over that exact architecture is made. No production or G3 unlock.

## Integrity and interpretation

The prepare/predict/score subprocesses are distinct. Prediction reads public prompts and immutable checkpoint/cache lineage, not fresh answer files. A Python audit hook denies opens of secret/answer-named files during prediction; this is a runtime safeguard, not an OS sandbox or proof against adversarial native code. All new predictions are hashed and locked before scoring. The public manifest contains no graphs. Existing source checkpoint identities and prior 0115/0116 seals are preserved. Reports and raw predictions are written exclusively to a new probe directory. Novel hidden states are saved there for optional post-lock gold-endpoint diagnostics.

When needed, after `run` completes, use `& $py -u -m tools.nanojev_gamer_v1_semantic_integrity_probe oracle --device cuda` with the same probe options. This supplies exact gold endpoints only after the prediction lock, reports unalignable cases, and writes `oracle-only.json` independently of `result.json`. Never interpret the oracle as operational performance.

A collapse under representation perturbation is evidence that the frozen architecture depends on the representations; it is not alone proof of generalized semantic understanding. Out-of-distribution intervention effects may reflect distribution shift. Strong normal performance on the contrast pairs together with correct directional changes, reliable support, low shortcut-only performance and reproduced baseline provides a stronger case. If normal behavior fails, diagnose G1 endpoint access versus G2 reachable relationship recovery. If meaningful representations are unnecessary, investigate structural and lexical shortcuts. If only implicit-ownership fragments fail, review annotation compatibility before attributing a semantic defect. Do not use this exposed probe to tune a policy and then call the same cases blind.

The small sample does not certify either stage. Conditional acceptance remains a research decision, subject to falsification by downstream evidence. Local CPU checks exercised the real 0116 reader forward and decoder interfaces with synthetic states, author-contrast invariants, the intervention tensors and the answer-open guard. Actual runtime checkpoint replay, G1 composite inference and CUDA execution must be verified on the user's machine.
