# G2-0092 — Mixed-Family Structural Trainability and Live Causal Fuzz Monitoring

## Decision and scope

G2-0090 memorized eight allocation graphs, while G2-0091 showed worse
encounter performance and no exact fresh graphs. The next rational test is a
*bounded, mixed-family feasibility experiment*. Its purpose is to expose
failure mechanisms while they are occurring, **not** to claim that this
architecture or objective will generalize.

Run from `main_computer/nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_mixed_family_fuzz_trainability_0092 run --device cuda
```

This runs two **independent** initializations, each with the same selected
training graphs and diagnostics. It creates
`../runtime/reports/gamer-v1/oracle-fabric-g2-mixed-family-fuzz-0092/`.
Do not run a second experiment into the same destination: experiments are
write-once and refuse existing output paths. `--output` accepts an alternate
output directory. `--steps 650,1300` is the default pair of stage limits.

## Experimental arms and gates

- **A_from_0087:** initialize from the original G2-0087 learned head.
- **B_from_0090:** initialize from G2-0090's hash-verified stage-8 head.

Both arms use G1's certified predicted spans, the same frozen G2-0088 joint
decoder, and the same graph-set objective. Only native G2-authorized weights
may update. No G1, G3, or original G2 checkpoint is modified.

Train first on **two allocation + two encounter** training graphs. Proceed to
**four + four** only if all four first-stage graphs are reconstructed exactly
from actual neural inference and joint decoding. Training starts at a frozen
parent in each arm. Stage two retains the stage-one best head **and optimizer**.
The first stage runs for up to 650 updates, the second up to 1,300; a stage
stops early after two consecutive complete-correct evaluations. Checkpoint
selection uses the balanced training objective (minimum family exact rate,
then exact count, then graph errors and loss), **never evaluation cases**.

Training examples must be original training-set examples with *all* gold edges
reachable under predicted G1 and oracle-decodable by the fixed joint decoder.
This selection biases the feasibility experiment toward recoverable cases;
therefore success is not a population accuracy claim.

## Real-time diagnostics and failure attribution

At every 25 updates, the monitor reports and journals, **by family**:

- Exact complete graphs (training and excluded dev/challenge diagnostics).
- False-positive and false-negative relationships and edge F1.
- Which graph cases gained/lost exact reconstruction.
- Whether family-specific heldout F1 fell more than three points beneath
  the arm's initial baseline.
- G1 reachability on the monitored historical cases, with *all* gold edges
  counted even if G1 omitted an endpoint.

At every 50 updates, the monitor also runs an excluded, author-annotated
**counterfactual fuzz battery**: renaming, role swapping, added distractors,
and action removal. It checks *gold-defined transformation deltas*, not
naive equality of surface forms. It counts whether both resulting graphs are
correct as well as whether the **change** in predicted relationships is
correct. For role swaps, stable author IDs can preserve graph topology even
when surface names trade roles. For action removal, dependent edges must be
removed. Each run records G1 exactness to separate upstream errors.

Training uses separate **train-name** renaming and role-swap pairs for
source-ID-aligned consistency supervision at every fourth update. Those are
not the excluded fuzz evaluation examples. If eligible, G1-exact training
counterfactuals cannot be found for both families, preflight fails rather
than falsely claiming consistency training took place. Distractor and
removal cases remain **diagnostics**, not an unimplemented training promise.

On regression and at 100-update diagnostic intervals, capture:

- The allocation versus encounter gradient cosine, including **binding**,
  **pair projection**, and **G1→G2 coupler** trainable weight groups.
- Reversible, normalized, per-example gradient interventions at 100-update
  intervals, restoring every affected weight afterward.
- Full G2 activation traces on the highest-error diagnostic case from each
  family: projection, encoder, coupler, pair projection, and binding input,
  with gold-conditioned edge-margin AUC and class centroid separation.
- Explicit, evidence-linked flags for false-edge admission, missing-edge
  recall, cross-family regression, G1 extraction instability, and incorrect
  counterfactual transformations.

**Interpretation limits:** Negative gradient cosine suggests first-order
interference; it does not prove that a particular update caused a regression.
An activation class-separation statistic cannot uniquely identify a faulty
layer. Finite weight perturbations are causal interventions but only on the
specific disposable model state tested. The reports label hypotheses, not
unwarranted neural localization claims.

## Durable artifacts

For each arm and completed stage:

- `stage-4/monitor.jsonl` or `stage-8/monitor.jsonl` is appended during
  training so an interrupted experiment retains its measured intervals.
- `diagnostic-STEP.json` records triggered gradient, counterfactual, and
  activation evidence. Each has a committed SHA-256 in the stage report.
- `history.json` contains the full monitor snapshots, including fuzz records.
- `checkpoint/gamer_graph_head.safetensors` is a **diagnostic-only** best head.
- `report.json` includes structured failure attribution and the baseline and
  final per-family heldout measurements.
- `lock.json` binds checkpoints, reports, histories, and diagnostics.

The experiment-level `provenance.json` records the frozen parent hashes,
training selection, heldout selection, two disjoint fuzz pools, seed and step
limits. `report.json` reports the two outcomes and never unlocks G3.

## What a pass or failure authorizes

A 4/4 + 8/8 memorization pass proves mixed-family **training capacity on
these selected examples**, not independent generalization. After any pass,
we still need fresh sealed mixed-family testing, native G2 causal ablations,
original G1 retention, and the original development/challenge acceptance
gates. Original G2 thresholds remain unchanged:

- Development exact binding-set rate: **80%**.
- Challenge exact binding-set rate: **70%**.
- Required causal gains: **at least 8 percentage points**.

If the experiment fails, read `report.json` and the stage's
`failure_attribution` and `diagnostic-*.json` before proposing any new
training or architecture. In particular, distinguish a deficit in graph
recall from false-positive overadmission, G1 endpoint omissions, family
interference, and transformation instability. **G3 remains locked in all
outcomes.**
