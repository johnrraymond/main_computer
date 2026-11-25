# G1-0115 — integrated semantic reconstruction and certification laboratory

Status: 2026-11-24. Operator development and cohort sealing completed; initial CUDA prediction stopped at the G1/G2 endpoint interface. Endpoint-format repair delivered; corrected blind inference and scoring pending. Research-only certification experiment, not a production gate. No canonical writes, G3 unlock or promotion.

## Why

G1-0114 recovered 5,452/5,488 required objects with its validation-selected `architecture_6` policy, but produced 117 extra objects and 35 incomplete cores. The full candidate union contained all gold objects, with a compatible strict oracle of 192/192. The remaining errors are initially a reconstruction/evidence problem, not a reason to train another backbone. Alias/pronoun variants account for 108/117 extras. These may reflect relevance or annotation ambiguity; do not assume all are failures of language comprehension. See [the evidence ledger](GAMER_V1_BACKBONE_RESEARCH_EVIDENCE_0103_0114.md) and [the current guide](GAMER_V1_BACKBONE_RESEARCH_CURRENT.md).

The operator requested that residual diagnosis, invariance, reader reduction, independent evaluation, downstream G2 and documentation be one automated operation. The scientific boundaries remain strict even though execution is unified.

## Pipeline

`tools/nanojev_gamer_v1_g1_integrated_reconstruction_0115.py` runs four separate processes: `develop`, `prepare`, `predict`, `score`.

1. Verify frozen 0113/0114 checkpoint, cache, report and selection hashes; verify the historical G2-0087 checkpoint. Reuse 0114 candidate evidence and validate the exact old reconstruction. No Qwen capture or reader training for development.
2. Record every 0114 residual: candidate absence, admission rejection or compatibility selection; false-positive boundary/type/disjoint categories; candidate support, competing objects and forced-inclusion diagnostic. Record confidence and agreement discrimination conditional on vote count, with base-linked transformation errors. Flag disjoint alias/pronoun extras for review, without automatically changing labels.
3. Partition the exposed 0113 test bases, stratified by family, into 12 fit and 12 selection bases. Old 0113 validation is an additional exposed diagnostic, not fresh evidence. Compare fixed vote/architecture/source/whole-reader/native-confidence policies and weighting. If the best fixed policy misses the proposed targets, compare small regularized logistic admission calibrators using candidate-level features. Only fit data enter their optimization; models consume no case IDs, author mode, family or gold at inference. No retraining Qwen or the 17 readers. If a subset has no examples of one class, skip calibration rather than invent a classifier.
4. Greedy backward elimination considers every possible next reader removal using fit data at each step; retest thresholds, include all singleton readers, and optionally fit subset calibrators at 12, 8 and 4 readers. Report the tested size/accuracy frontier, not a proven global minimum. On selection data, find the best core/strict/F1 tuple; select the smallest inventory within one complete core, one strict exact, 0.001 F1 and four reachable edges of that best candidate. Use reference-source count as a cost tie-breaker. This is an explicit experimental trade-off, not a claim of statistical equivalence. Preserve the selected policy, coefficients, trial and residual hashes before generating fresh prompts.
5. Generate 192 fresh bases with 14 variants (2,688 prompts) through the existing 0110 author, plus 64 fresh bases with five variants (320 prompts) through the existing 0093 controlled author. Total 3,008 prompts from 256 independent bases by default. New 0113-reader evaluation modes include newline, number formatting, renaming, sentence split, spacing and suffix; the 0093 author adds role swaps, distractors and action removal. These are fresh synthetic problems and expanded transformations, not a new external human distribution. Exclude recorded prior prompt hashes and base IDs. Write separate sealed public prompts and secret answers, only after the development policy is frozen.
6. Blind inference: use actual frozen Qwen, original and S references, the 17 reader checkpoints and the selected policy. Compare original frozen G1, full 0114 architecture_6, and 0115 selected on identical prompts. Use the actual G2-0087 trained native binding head, fixed original L28 hidden states, and each arm's predicted token spans; no teacher forcing. Thus the effect of endpoints on this fixed G2 classifier is directly measurable. This is not the later G2 ensemble or full G3 graph reasoning. The full comparator executes all readers; the selected subset's measured reader time is a component comparison, not standalone wall-clock acceleration. Both input paths are run for the comparator.
7. Lock per-case predictions and hashes before scoring. Report precision/recall/F1, complete core, strict exact, endpoint accessibility, union recoverability, base-cluster paired intervals and strata. For G2 report actual binding TP/FP/FN, strict surface graph, endpoint-unreachable vs reachable misses, and all failure cases including those with strictly correct G1. No tuning after seeing this cohort. Write a run-specific Markdown research record with results and decision logic.

## Proposed research targets (not release gates)

| Metric | Target |
|---|---:|
| G1 object precision | 99.5% |
| G1 object recall | 99.5% |
| G1 complete core | 99% |
| G1 strict exact | 95% |
| Surface edge endpoint accessibility | 99.9% |

If the fresh union itself misses gold, diagnose candidate/reader coverage. If the selected subset loses candidates found by the full inventory, diagnose reduction. If admitted candidates fail the solver, diagnose competition and compatibility. If false positives concentrate on irrelevant but valid linguistic spans, review author labels and relevance semantics separately. If G2 misses reachable edges, investigate the relationship reader and classifier. A failed target is recorded, not erased by changing the test. Do not automatically launch arbitrary training epochs or promote a checkpoint.

## Execution

From `main_computer\nanojev` after extracting the patch into the repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_integrated_reconstruction_0115 audit
& $py -u -m tools.nanojev_gamer_v1_g1_integrated_reconstruction_0115 selftest
& $py -u -m tools.nanojev_gamer_v1_g1_integrated_reconstruction_0115 run --device cuda
```

`run` completes all four processes without operator decision points. Reissue the same command to resume. Completed development search stages and reduction iterations, immutable selection and cohort seals, and each completed prediction shard are reused; an interrupted current search iteration may be replayed. Optional `--bases`, `--alternate-bases`, `--output` exist for a deliberately separate experiment; do not change sealed parameters when resuming. Default inputs are the intact 0110–0114 runtime lineage and the historical G2-0087 checkpoint; no standalone JSON downloads.

Outputs under `runtime/reports/gamer-v1/g1-integrated-reconstruction-0115/`:

| File | Meaning |
|---|---|
| `development/residual.json` | Every old error, candidate evidence, forced-inclusion diagnostic, conditional AUC |
| `development/report.json` | All evaluated trial summaries, reduction path, frontier, development decisions |
| `development/selection.json` | Frozen selected policy and coefficients; source hashes |
| `development/search-state.json` | Resumable development search state |
| `certification/seal.json` | Fresh cohort commitments and source identity |
| `certification/predictions/` | Resumable per-case blind results |
| `certification/prediction-lock.json` | Complete prediction hash commitment |
| `certification/result.json` | Fresh measured results and targets, G1/G2 comparisons, paired statistics |
| `certification/failures.json` | G1 failures and union coverage |
| `certification/g2-failures.json` | Actual G2 errors, including correct-G1 cases |
| `RESEARCH_RECORD.md` | Automatically generated measured summary and next-decision logic |

## Verification boundary

CPU selftest exercises actual repository G1/G2 head classes, token mapping, reconstruction, candidate calibration and reduction logic. A separate small synthetic fixture exercised the complete development search, adaptive skip when one class is absent, replay/resumption, blind seal checking with the answer file absent, scoring, documentation generation, and tampered-prediction rejection. The actual 0110 generator source and runtime checkpoint/caches were not present in the local build environment; the 0110 interfaces are reused from 0113 and the 14 modes are evidenced by operator reports. The 0093 controlled generator was executed locally for both families. Live audit, development and cohort generation have passed; corrected full CUDA inference remains necessary. No independent result has yet been measured for 0115.

## Operator run and endpoint-format repair (2026-11-24)

The operator completed the audit, selftest, 290 development trials, and the 3,008-prompt seal. The prior 0114 residual ledger attributes 30/36 misses to admission rejection and 6/36 to compatibility selection; 96/117 extras are disjoint and 21/117 have overlapping same-type boundaries. These are observed failure categories, not proof of annotation errors. The selected policy retains all 17 readers (`architecture`, threshold 0.18, margin weight, confidence 0.7), with 79/96 complete cores, 30/96 strict extractions, 17 FN, 127 FP, and 97.4423% F1 on the exposed selection subset. The greedy trajectory and optional calibration did not displace this choice. The research targets are not met on development; the fresh cohort is still unscored.

Original prediction failed before the first completed shard: the original G1 comparator supplied serialized list objects to a table keyed by tuples; the old selftest used tuples and missed the real serialization boundary. Both token assignment and G2 inference now normalize endpoints, while the original comparator retains tuple keys until serialization. CPU regression calls the actual G2 head with both representations and verifies identical edges and token assignments.

The only permitted legacy development implementation SHA-256 is `2ed0a4c13ebf2957cb704903c10159fccc081589353f97b4411a7103e09beb6f`. All other source/parent identity fields must still match. Existing development selection, report, residual ledger, and sealed prompt/answer files are preserved unchanged; the corrected source hash is recorded in each new prediction shard, prediction lock, and final result and is checked during scoring. Old prediction shards lacking this execution hash are refused, not silently accepted; the reported run stopped before the first shard. Do not delete or regenerate the sealed cohort. Resume with the same `run --device cuda` command. A CPU fixture verified reuse of the old identity, preservation of selection and seal hashes, scoring with corrected execution provenance, and tamper rejection. Actual corrected CUDA prediction remains unverified.


## Operator decision: conditional acceptance and progression to G2

The first 83 fresh predictions covered six independent base problems. The 0114 balanced route achieved 99.2474% object F1 and 99.8486% recall, 0115 achieved 100% recall and 97.9965% F1, and the frozen 0087 classifier remained around 38.3% F1. The 0115 run may be suspended without discarding its already committed development selection, answer seal or prediction shards. Full completion is not a prerequisite for G2-0116; retain 0114 and 0115 as alternative endpoint providers. This is a conditional research decision based on an interim trend, not a full G1 certificate. Reopen the relevant G1 problem if same-cohort G2 comparisons reveal substantial upstream endpoint or relevance obstruction. See [current research guide](GAMER_V1_BACKBONE_RESEARCH_CURRENT.md) and [0116 protocol](G2_0116_MULTILAYER_RELATIONSHIP_RECONSTRUCTION.md).
