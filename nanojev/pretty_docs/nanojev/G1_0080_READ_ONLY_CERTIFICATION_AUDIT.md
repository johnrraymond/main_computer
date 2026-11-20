# G1-0080 — Read-only certification evidence audit

## Purpose and boundary

G1-0079 selected a quantity-pinned structured interface and scored a prospective,
sealed 128-example audit. Console results showed Oracle-Fabric cutover 107/128 exact
and legacy 76/128 exact. **G1-0080 verifies those claims from the underlying
precommitted answers and blind predictions. It does not issue a G1 developmental
certificate, train a model, authorize G2, or modify any prior artifact.**

G1-0079's audit uses new prompts drawn from the same known author-annotated graph
generator. A prospective result is more informative than one selected on the same
cases, but it is not a new distribution and is not interchangeable with the
curriculum's declared development and challenge splits.

## Implementation

`tools.nanojev_gamer_v1_g1_certification_audit_0080` is a standalone standard-library
scorer; it does not import the G1-0079 scoring function or PyTorch. It reads files
only and writes **one** new output directory after all checks complete.

It verifies:

1. SHA-256 commitments for the 0079 preparatory seal, answers, prompts, frozen
   source and generator, earlier 0077 and 0078 references, and blind predictions;
   the unseal-once marker must match the scored report and prediction digest.
2. Head weights and model lineage are unchanged: both G1 head checkpoint bytes,
   the original dataset, earlier 0076 discriminator report and reference, and the
   original blocked G1 developmental certificate.
3. Train/selection/audit prompt hashes are mutually disjoint; 0079 prompts are
   also disjoint from the original corpus and the 0077 and 0078 datasets. The
   recorded prompt digest and all row counts must agree.
4. Selected decoders match the predeclared 0079 choices and replay the recorded
   selection criterion. Because raw per-arm selection predictions are unavailable,
   this replay checks *reported selection metrics* for consistency; it does not
   independently regenerate selection inference.
5. Every saved fitted discriminator has a valid parameter hash, finite bounded
   values, canonical seven semantic-kind offsets, and correct feature dimensions.
6. Each audit prediction matches one bound prompt, with valid span coordinates,
   legal semantic types and no duplicate objects or case IDs. Gold spans are
   similarly validated. Quantity sets must be exactly identical between frozen
   and selected predictions, case by case.
7. Independently recomputed exact-set counts, micro precision, recall, F1,
   per-kind recall, macro F1, per-family performance, false objects, paired
   gains/losses and exact paired sign-test p-values. The original scored totals
   and each recorded case delta must match.
8. G1 gate thresholds are read directly from
   `tools.nanojev_gamer_v1_graph_curriculum.GATE_REQUIREMENTS['G1']`, via a
   static source parse. The observed audit metrics are reported separately from
   **required but absent current-interface dev/challenge evaluations and causal
   ablations**.

Hash commitments detect changes relative to trusted local file manifests; they
are not signed timestamps or a cryptographic proof that answers were never read
outside the intended program. The `no gold at inference` statements are checked
as recorded assertions and protected by the prior code/source lineage, not by an
independent process trace. No existing data is independently re-sampled here.

## Run

Apply the patch ZIP at the `main_computer` repository root, then from
`main_computer\nanojev`, with `$py` already set:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_certification_audit_0080 `
  --seal ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-seal `
  --predictions ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-blind `
  --scored ..\runtime\reports\gamer-v1\oracle-fabric-g1-quantity-preserving-0079-scored `
  --seal-0077 ..\runtime\reports\gamer-v1\oracle-fabric-g1-sealed-0077 `
  --reliability-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-reconstruction-reliability-bakeoff-0001 `
  --discrimination-reference ..\runtime\reports\gamer-v1\oracle-fabric-g1-discrimination-trainability-bakeoff-0002 `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 `
  --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --cutover-certificate ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\certificates\G1\certification.json `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-certification-audit-0080
```

No PowerShell variables are used other than the already configured `$py`.

## Expected outputs and semantics

`report.json` contains complete independently derived metrics, provenance
fingerprints, case-level paired gain/loss ledger, quantity-retention checks,
predeclared-threshold observations and explicit formal certification blockers.
`integrity.json` contains the pass/fail provenance fingerprint ledger.

On a changed seal, false reported score, unknown span, lost quantity, incomplete
prediction, checkpoint drift or forbidden output location, the auditor raises an
error **before creating the output directory**. Existing output is immutable.

Successful completion prints `G1_0080_AUDIT` with a scope such as
`READ_ONLY_EVIDENCE_VERIFIED_NOT_CERTIFIED`. It never emits
`DEVELOPMENT_GATE_PASSED` and never flips `g2_unlocked` or production flags.

## Original G1 acceptance requirements and what is missing

The deployed G1 developmental gate in `nanojev_gamer_v1_graph_curriculum.py`
requires both *dev exact rate* >= 80% and *challenge exact rate* >= 70%, G1
object macro F1 >= 90%, actor/action/quantity recall >= 82%, family diversity,
minimum cohort sizes, zero-hidden and stage-component neural gain >= 8 percentage
points, valid checkpoint / dataset lineage, and non-smoke evaluation scope.
These conditions must be evaluated on the **current decoder/interface** and
linked to a coherent G1 checkpoint. The G1-0079 prospective audit does not
provide the corresponding declared dev/challenge/causal-ablation reports.

Consequently a favorable 107/128 score is evidence of capability, **not** an
authorization to unlock G2. A future advancement decision requires integrating
the learned interface into the official G1 evaluation/certification path and
producing the missing current-interface evidence, rather than relabeling the
prospective cohort or relaxing its thresholds.

## Verification scope

CPU-only test fixture exercises a complete committed-seal → blind-prediction →
scored-report → independent-audit pipeline. Adversarial tests include tampered
predictions, changed gold, altered fitted coefficients, forged F1, duplicate
identities, quantity loss, and protected-path output. G1-0079 and earlier G1
regression suites are exercised with the same repository source. Actual local
G1-0079 evidence was not available inside the patch-building environment; run
the command above against the user's original immutable local artifacts.
