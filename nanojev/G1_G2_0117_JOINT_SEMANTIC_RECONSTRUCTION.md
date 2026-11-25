# G1/G2-0117 — Joint Semantic Reconstruction Laboratory

## Why
The completed 0115/0116 semantic integrity probe reproduced all 12 cached cases. On eight independently authored fragments, G1-0115 selected 41 of 44 gold objects; its collective inventory contained 43. With selected G1 endpoints, the G2 new ensemble's individual reader inventory contained all 29 reachable relationships, but its output contained only 16 of 32 required edges. Only two of 16 expected directional changes were recovered in the paired contrasts. These findings support a reconstruction diagnosis in this small population, not a general proof of sufficiency. No direct answer leakage was demonstrated; ablations establish dependence on representations, not proof of semantic generalization.

## What
Freeze Qwen, S, the 17 G1 readers and 15 new G2 readers, original reconstruction policies and parent reports. Reuse all 48 locked 0116 cases and the eight novel integrity-probe cases as exposed development. Split by base: eight 0116 bases plus quantity-pair for fitting; four 0116 bases plus reference-pair for selection; two ambiguous implicit-ownership bases for diagnostic-only scoring. No Qwen inference for this phase. Recompute G1 and G2 evidence from the saved contextual states and require exact replay of both 0114 and 0115 endpoint views and new-integrated G2 outputs. The older eight G2 experts are not the comparator in this experiment; the exact 0103 raw reranker remains outside scope.

Each object gets reader votes, architecture diversity, source diversity, native support, probabilities, margins, and overlap competition. Each edge gets per-reader support and probabilities, no-binding and alternative-label margins, and a contextual contrast after explicit geometry/type/lexical features are zeroed. G2 reads the full G1 lattice (overlapping hypotheses allowed at this evidence stage), as well as normal 0114/0115 endpoint sets. A joint candidate pool retains admitted relationships from all three surfaces; duplicate numerical evidence is not counted twice. The full-lattice reader context may be out of distribution, so fixed-surface evidence and raw top-one inventory remain separately auditable.

Compare: baseline 0114, baseline 0115, agreement, competitive, contextual, fixed joint, learned joint. Only the learned joint arm fits two small ridge logistic admission discriminators (object and relationship) using exposed fit bases. No backbone or reader updates. Select by 0.55 base-macro relationship F1 + 0.25 base-macro object F1 + 0.20 directional-change F1; if no paired cases, strict graph rate replaces the directional component. Validation is development, not certification.

## How joint selection works
Maximize the sum of node and edge evidence scores using binary mixed-integer optimization (SciPy HiGHS). A selected edge requires both endpoints; overlapping character or token spans are mutually exclusive; each ordered pair has at most one relationship label; at most 128 objects may be selected (existing G1 bound). No answer-derived cardinality constraints, family IDs or generator IDs in the new joint objective. Each lattice object is represented by its earliest-ending valid token mapping. This is an explicit first-pass limitation: alternate mappings may allow more compatible graphs. The optimizer checks incumbent feasibility and reports time-limit/nonoptimal status instead of claiming optimality. The fixed and learned objective scales are predefined experimental choices, not established optimal calibration. A five-second solver limit applies per graph; if there is no feasible incumbent, fail closed.

## Fresh evaluation and integrity
After the development winner is frozen, prepare 28 new prompts, seven independently authored base structures × direct/reverse and their paraphrases. Four explicit binding structures, two implicit ownership structures and one ordered-quantity structure. Implicit binding annotation conventions are exploratory and reported separately. Structural annotation checks verify coordinates, identifiers, edge endpoints, matched geometry and equivalent gold paraphrases. They are not a human linguistic ontology adjudication. Exact prompt hashes are checked against available prior cohorts; no exhaustive structural independence guarantee. Four explicit holdout bases also share the general task of reference resolution with prior work; do not claim broad semantic generalization from them alone.

Predict in a distinct process under the inherited historical-provenance checksum adapter and Python answer-open guard. The author module is imported during preparation, not prediction. The guard is application-level, not an OS sandbox. Predictions, cache receipts and the chosen policy are hashed and locked before scoring. Score all arms on identical fresh prompts, but report the preselected winner separately; do not choose a new winner on held-out results. Evaluate normal vs zero, shuffled, embedding-only, and S-replacement conditions, both end-to-end and with normal G1 evidence fixed. These are frozen-reader sensitivity experiments. After locking, run a separately labeled oracle endpoint diagnostic to distinguish unavailable G1 objects from missing G2 reader evidence; report unalignable objects explicitly. Gold endpoint results are not operational scores.

## Outputs
`runtime/reports/gamer-v1/g1-g2-joint-reconstruction-0117/`: development cache, `development.json`, `selection.json`, sealed `fresh/` population, cache and predictions, prediction lock, `result.json`, `RESEARCH_RECORD.md`. Development evidence shards and fresh prediction shards are resumable, bound to identity and hashes. Original experiments are not modified. No production promotion or G3 unlock. Interpret final results as diagnostic review, not automatic certification.

## Execution
Use the existing NanoJEV Python environment with SciPy >= 1.9. From nanojev:

```
& $py -u -m tools.nanojev_gamer_v1_joint_reconstruction_0117 audit
& $py -u -m tools.nanojev_gamer_v1_joint_reconstruction_0117 selftest
& $py -u -m tools.nanojev_gamer_v1_joint_reconstruction_0117 run --device cuda
```

`run` performs develop, prepare, predict, score in separate subprocesses. It may be resumed with the same command. Local CPU selftest exercised real 0116 reader forward, baseline parity, empty views, graph solver/endpoint rescue, classifier fit, scoring, evidence pipeline with controlled G1 evidence, matched author contrasts, oracle coordinate mapping and answer-open guard. Actual user checkpoints and CUDA have not been tested in the local environment.
