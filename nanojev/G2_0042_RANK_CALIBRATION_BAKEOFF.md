# G2-0042 — Relationship ranking versus score calibration

## Governing question

G2-0041 showed that injecting `+24/-24` gold-informed scores made every shortlist-representable expanded TRAIN-fit graph exact, while correcting total/per-group counts generally did not. Because the oracle changed both *ordering* and *score magnitude*, it did not prove which failure mattered. G2-0042 separates those factors and competes targeted TRAIN-only fixes without altering G1, G2-0031/32/33, G2-0038/39, the parent G2-0040 artifacts, or production.

## Reproducible command

Run from `main_computer/nanojev` after applying the patch at repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-rank-calibration-bakeoff --device cuda
```

Default output (new, immutable directory):

`runtime/reports/gamer-v1/graph-g2-rank-calibration-bakeoff-0042/`

The command accepts all parent directory overrides from G2-0041, plus `--oracle41-run`. It verifies the complete parent G2-0040 report/policies/checkpoint/empty DEV ledger and the G2-0041 report/ledger hashes, case lineage and diagnostic outcome before capturing the same frozen TRAIN cases.

## Stage 1 — causal separation (TRAIN-only, not a deployable policy)

For each expanded, shortlist-representable TRAIN case that the routed parent fails:

* **Ordering-only oracle:** reassign the *existing* logits within each relation group so gold candidates rank above false competitors. Each group's sorted score multiset, its top-k energy values, group-count potentials, and global-count potential remain unchanged. Counts cannot improve from oracle magnitude inflation.
* **Calibration-only oracle:** scale the original logits by positive values in `{0.5, 1, 2}` and apply one of fifteen preregistered global offsets, without changing within-group ordering.
* **Ordering plus calibration:** apply the same offsets/scales after the ordering-only oracle, exposing interactions.

The report counts graph-exact counterfactuals separately for TRAIN, enriched TRAIN-fit, and the naturally distributed TRAIN audit. Oracle labels are used exclusively *after* frozen inference; none become deployable features or policies. Diagnostic calibration-grid results must not determine the candidate training grid or model selection.

## Stage 2 — fixed TRAIN-only bake-off

The G2-0033 structured decoder and all its *count* outputs remain frozen. Fine-tune two independent copies of its **edge-readout only** with 240 optimizer steps and 1:1 original/expanded TRAIN sampling:

1. **Pairwise ranking objective:** compare correct and incorrect alternatives within each source/relation group; small binary cross-entropy auxiliary loss.
2. **Hard-negative ranking objective:** emphasize highest-scoring wrong edges and lowest-scoring right edges within each group; stronger binary cross-entropy auxiliary loss.

Each learned ordering is mapped onto the original group's sorted score values before decoding. Hence the *ranking-only* arms change candidate identity and ordering but do not change the group's original score histogram or cardinality potential. The **joint** arms additionally apply fixed affine bias corrections. Calibration-only arms transform original edge logits with positive slopes, preserving original ordering.

Fixed candidates: six calibration-only, two ordering-only, and eight joint ordering-plus-bias combinations (16 total). All cases use the same admission-aware G2-0040 route; unchanged graphs are never rescored or re-decoded by a challenger. All 16 candidates use the same enriched TRAIN-fit cohort. One winner may be chosen on TRAIN-fit only; the natural TRAIN audit may **veto** it, not select another. No further candidate is considered if the chosen one fails audit.

### TRAIN-fit winner gate

* At least one *newly exact* full graph, no formerly exact graph lost.
* No decrease in relationship F1.
* No increase in false edges.
* No increase in pairwise ranking inversions.

Independent natural TRAIN audit must show no exact losses and no F1, false-edge, or ranking degradation. A null winner is a valid, safely terminated result.

### DEV evaluation, strictly after TRAIN selection and audit

Only if the winner passes TRAIN audit do we capture untouched G2-0040 independent DEV and held-out topology DEV, together with replayed G2-0039 DEV cohorts. To pass, the new independent DEV must gain at least one exact graph while losing zero, and each DEV and replay cohort must satisfy F1, false-edge, and ranking non-regression. DEV may **reject**, never select or retrain. Neither production promotion nor sealed testing occurs.

## Artifacts

* `g2_rank_calibration_bakeoff_report.json`: parent hashes, causal separation, 16 policy outcomes, audit, DEV gates (if reached).
* `g2_rank_calibration_bakeoff_per_case.jsonl.gz`: TRAIN oracle-factor evidence and post-acceptance DEV cases only.
* `g2_rank_calibration_bakeoff_development.safetensors`: both edge-readout-trained GraphSetEnergy copies; no parent weights are overwritten.
* `g2_rank_calibration_bakeoff_frozen_policies.json`: single TRAIN-approved policy or explicitly disabled policy.

Production and all source parent weight files remain unchanged, whether or not any candidate wins.
