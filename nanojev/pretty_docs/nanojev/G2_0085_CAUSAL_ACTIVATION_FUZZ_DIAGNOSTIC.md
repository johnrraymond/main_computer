# G2-0085 — Frozen Native G2 Causal Activation Fuzz Diagnostic

## Purpose and boundary

Inspect **why** G2-0083's native predicted-G1 binding head admits false edges. This
experiment changes **no** trained weights, decoded graph contracts, inference
thresholds, checkpoints, G1-0082 certificates, or stage gates. G3 remains locked.
It is exploratory causal/representation diagnosis, **not** a new prospective
certification or a decoder-parameter search.

## Command

Apply the patch at the `main_computer` root, then from `main_computer/nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_causal_activation_fuzz_0085 --device cuda
```

The default immutable destination is
`../runtime/reports/gamer-v1/oracle-fabric-g2-causal-activation-fuzz-0085`.
To expand the balanced exploratory sample, use a distinct output directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_causal_activation_fuzz_0085 --device cuda --pairs 16 --output ../runtime/reports/gamer-v1/oracle-fabric-g2-causal-activation-fuzz-0085-b
```

`--pairs` must be a multiple of four (one of each perturbation class per
block). Default `--pairs 8`, seed `850085`, and up to three high-margin false
edge targets per prompt (`--max-targets`). No extra PowerShell variables.

## Provenance / refusal behavior

The runner verifies the original G1 native certificate and frozen composite
checkpoint through the existing 0082 verifier; verifies the G2-0083 seal,
trained head and parent lineage, frozen G1/future parameters, and reported
failure status; rejects reused historical/sealed prompt hashes; and rehashes
the trained head and native certificate after the experiment. Missing proof,
changed source, altered checkpoint, or existing output directory is a refusal.

The new probes are derived from annotated author generators, using new test
surface words with recorded seeds, but are **not** an independently adjudicated
out-of-distribution validation set. Author answers are read after inference for
attribution only. Unlike a clean blinded acceptance test, gold labels also
select some causal probe targets and donor pairs. These interventions are **not
an inference policy** and may never be promoted or tuned against sealed scores.

## Controlled input perturbations

1. `renaming`: surfaces change while the author-ID relationship graph stays
   constant.
2. `role_swap`: surface identities exchange actor/action positions while the
   author-ID relationship graph remains unchanged. This tests dependence on
   lexical identity versus the syntax/role of the **mention**, not reversal of
   an original graph edge.
3. `added_distractor`: append an explicitly unrelated external note. Original
   author annotations and edges are unchanged. This tests whether G1 or G2
   adds spurious objects/bindings despite the unchanged underlying task.
4. `action_removal`: in an allocation example, remove one offered alternative
   and its mentions. This is a genuine graph change; cross-prompt ID alignment
   is deliberately not asserted for this mode.

The baseline and fuzz variant each run the same frozen Qwen, composite G1, and
native G2 checkpoint. G1 is inferred from each actual input; **within each G2
causal intervention**, its selected token spans are held fixed. A G1 exactness
and reachable-gold-edge counter prevents interpreting endpoint omissions as a
G2 relational defect.

## Activation and intervention atlas

The recorder captures the actual outputs of `project`, contextual `encoder`,
`G1_G2` interface coupler (when present), `pair_projection`, and `binding`, plus
binding input. It records class-conditioned pair geometry, stage centroids,
sampled true/false edge geometry, gold-vs-null score ranking, and all surface
pair logits. Same-author-ID activations are compared across gold-invariant
prompt variants; action removal is not forcibly aligned.

For selected wrong and correct pairs, interventions zero the source token's
encoder representation, zero its coupled representation, or zero one pair's
projection. Further experiments transplant a known correctly rejected pair or
a known true positive into a target pair. A matched unrelated object-zeroing
probe serves as a non-specific context control. Results record the targeted
logit-margin change, whether its edge decision flipped/corrected, and how many
other pair decisions changed. All hooks are removed after each forward and no
optimizer is constructed.

A change in an edge logit under activation replacement demonstrates causal
**sensitivity** to that replacement; it does **not** prove the transplanted
representation is a valid learned relation detector. Positive/negative
donors and centroid statistics use gold labels post hoc. A high in-sample
centroid separation is not generalization evidence, and zeroing may be an
out-of-distribution intervention. The named negative controls and fresh
untuned validation would be needed before claiming a causal fix.

## Output

- `report.json`: source hashes, experiment contract, overall and mode-specific
  error counts, matched fuzz response shifts, and aggregate intervention
  effects. `g2_certified=false` and `g3_unlocked=false` are hard-coded.
- `cases.jsonl`: one entry per actual input (baseline and variant), showing
  G1 endpoints, reachable gold edges, pair outcomes, stage geometry, and each
  causal intervention outcome.
- `pair-scores.jsonl`: one entry per input with every directed G1-predicted
  surface pair, its type/anchors, full binding-logit vector, null and edge
  probabilities, score margin, gold label, predicted label and error class.

## Interpretation / next decision

- If true and false pairs separate in final binding margins but admission is
  poor, test a **new** calibration/structured-decoding hypothesis on a separate
  calibration cohort and sealed evaluation. No acceptance gate changes.
- If earlier stages exhibit stable positive-vs-negative contrasts that later
  stages lose, use causal intervention controls to locate the suppression.
  Centroids alone are insufficient proof of where information was destroyed.
- If intermediate and final activations alike cannot distinguish compatible
  false pairs, the G2 training objective, negative sampling, or pair
  representation needs investigation, not arbitrary threshold tuning.
- If the same semantic graph changes under renaming/distractors, distinguish
  G1 endpoint drift from G2 binding sensitivity before repairing G2.

The tool produces evidence, **not** a declaration that a particular root cause
has already been experimentally demonstrated.
