# G2-0061 — Resume learning with contextual candidates, retain coverage debt

## Decision and objective

G2-0060 completed a controlled, frozen-G1 actor-candidate bake-off on 128 known development pairs and **128 separate holdout pairs**. On holdout, the evidence control reached **119/128 (92.97%)**; both contextual actors and hybrid reached **125/128 (97.66%)**, recovered 15 missing gold edges over control without regressions, but left **11 missing `owns` edges** across three pairs. Actor-quota performance worsened to 114/128, with 28 new missed gold edges. The locked hybrid failed the **98% overall** and **95% encounter-renaming** acceptance conditions. The contextual-only arm matched hybrid reachability and avoided quota complexity. We therefore use **`actor_cues`**, not the hybrid, for the *research training path*.

**Decision:** Proceed with actual G2 learning on substantially more examples. Treat 97.66% candidate reachability as adequate to investigate learning, **not** as formal qualification. Preserve the existing 98% acceptance gate unchanged for production and full-corpus certification. The G2-0060 holdout is now examined and cannot serve as fresh independent qualification data. The validation cohort in the precommitted G2-0059 plan remains separate from the G2-0060 training-pair bake-off, but comes from the same synthetic author; it is not independent grammar generalization.

## Technical safeguards

- **No false supervision from missing candidates.** G2-0061 includes a complete counterfactual *pair* in its optimizer loss only if both graphs have every gold relationship represented. If either side has an unavailable endpoint, both sides are excluded, not relabeled as negatives. The report exposes numbers of excluded pairs and their game-family/mode cells. This sacrifices some experience to protect supervision integrity; it is not a solution for learning from censored labels. Because excluded cases may be systematically harder, the trained subset is selection-biased; use per-family/mode exclusion counts and full-validation accuracy when interpreting apparent progress.
- **Independent validation remains honest.** Validation includes *all* captured validation graphs, including those with unrepresentable gold edges, and reports a second reachable-only score. The complete score captures the real pipeline ceiling; the reachable-only score separates potential learning failures from candidate defects. Checkpoint selection uses the full validation set. Parent step-zero results and selected-checkpoint deltas are recorded. The selected checkpoint is also evaluated on the **reachable training graphs**, with its training/validation exact-graph gap reported to expose memorization.
- **Real scale, without a full-corpus prerequisite.** Capture a requested, immutable prefix of the existing 10,000-graph training plan plus an independent validation prefix. First recommended cohort: **1,000 training graphs (500 counterfactual pairs)** plus **400 validation graphs (200 pairs)**. The source author remains limited to the two current game families. Increase to 5,000 and 10,000 graphs by extending the same capture directory and running fresh, independently initialized learning rungs. No sealed test or challenge data are captured in this lane.
- **No model promotion.** Frozen Qwen, G1, G2-0052 and parent G2-0057 weights remain unchanged. G2-0061 saves only experimental feature shards and new G2 model checkpoints. The formal G2-0059 `train`, `rollout` and `evaluate` commands do not acquire a coverage override. The new training command requires an explicit `--research-coverage-exception` argument. That flag cannot authorize deployment.
- **Integrity and recovery.** Reuse G2-0059 plan/shard checksums, source-pinned candidate capture, frozen parent verification, deterministic pair/shard order, checkpoint/optimizer verification, and pause/resume. Capture is pinned to the G2-0060 contextual policy source and G2-0061 learning-lane source. Restart with unchanged flags and artifacts.

## Execute (from `main_computer\nanojev`)

Apply the patch at the repository root. Existing G2-0059 and G2-0060 data remain untouched. The research lane writes to **new G2-0061 directories**, and the G2-0059 immutable plan stays unchanged.

Capture 1,000 training graphs and all 400 existing validation graphs. This is GPU capture with frozen parent weights; the first run may repeat the legacy G2-0040 ancestor diagnostic setup. Rerun the same command after interruption to resume verified shards.

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale learn-capture --device cuda --train-graphs 1000 --validation-graphs 400
```

**Start the actual learning experiment** on the completed prefix (four epochs and two paired examples per batch). The output includes step-zero baseline, training coverage exclusions, full validation performance and reachable-only validation performance. Training never loads Qwen again.

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale learn-train --device cuda --train-graphs 1000 --validation-graphs 400 --epochs 4 --batch-pairs 2 --research-coverage-exception
```

For an immediate optimizer/restart smoke, add `--max-new-steps 1` to the training command; then rerun the original command without that option to resume. Do not change data, epochs, batch-pairs, or source versions mid-run. The first successful one-step call reports `PAUSED_RESTARTABLE`; it does **not** establish learning.

After inspecting actual validation results, extend the same feature capture to 5,000 graphs. Train this rung in a **distinct output directory** so that it starts from the same G2-0057 parent instead of silently continuing the 1k checkpoint:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale learn-capture --device cuda --train-graphs 5000 --validation-graphs 400
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale learn-train --device cuda --train-graphs 5000 --validation-graphs 400 --epochs 4 --batch-pairs 2 --research-coverage-exception --output ..\runtime\reports\gamer-v1\graph-g2-0061-training-5000
```

The first rung's output is `runtime/reports/gamer-v1/graph-g2-0061-training/selection.json`. The experimental capture is `graph-g2-0061-context-capture`. No full 10,000-graph capture is prerequisite for the first learning result. `--max-shards N` on `learn-capture` provides a bounded, resumable CUDA capture invocation; training requires the requested train/validation prefixes to be complete.

## What success and failure mean

A validation improvement over the frozen G2-0057 step-zero baseline, especially with a larger reachable-only exact-graph count, is evidence that increasing *new experience* helps G2. No improvement is evidence against this specific parent/objective/cohort, not proof that the candidate repair is wrong. A high training score with flat validation is continued memorization; report it rather than claim generalization. Compare results within the precommitted validation cohort, and only open fresh test/challenge labels **after** a genuinely selected final model and separate qualification decision.

The continuing risks are: 97.66% holdout candidate reachability is below formal policy; three holdout pairs and 11 `owns` edges are still not representable; contextual cues may exploit templates shared with the synthetic author; and 1k/5k/10k examples might not supply independent semantic variety. None of those concerns should prevent measuring actual G2 learning, but none should be erased from subsequent review.

## Verification status

CPU acceptance covers actual shard writing, restart, feature policy pinning, exclusion of a deliberately unreachable pair, rejection by the original formal gate, a real optimizer step with resume, validation including unreachable examples, and no test/challenge capture. CUDA capture throughput, 1k-scale convergence, and held-out learning improvements are **not yet verified** until the operator executes the above commands.
