# G2-0059 — Scalable, auditable graph learning

## Why this intervention exists

G2-0056 improved validation edge recall from 28.5% (8 training graphs) to 77.6% (80 graphs). G2-0057 then memorized all 80 training graphs, but reconstructed only 2/16 validation graphs and 0/32 unseen final graphs exactly. G2-0058 found substantial source/destination binding headroom but its new loss did not improve validation; both arms selected step zero. Continuing to refine a loss on the same 80 memorized graphs does not test the leading unexamined variable: number and diversity of genuinely new training scenarios.

G2-0059 establishes the reusable training system before another model architecture or loss bake-off. This is **research-only**, does not modify G1, G2-0052, or frozen Qwen/encoder weights, and cannot authorize production promotion.

## System boundaries

1. **Author:** deterministically generate complete counterfactual pairs in four balanced family/mode cells. Vary allocation choices (2–5), encounter matrix dimensions (2×2, 2×3, 3×2, 3×3), numeric outcomes, templates, actor/action words, and optional non-decision reference/quantity occurrences. The challenge split uses previously excluded dimensions (six allocation choices, or 2×4/4×2 encounters). Validate the complete authored graph, the executor's decisions, and paired counterfactual consistency. Gold edges must fit the existing 64-slot set predictor. The author is used only to generate and label research examples, never in inference.
2. **Partition:** create immutable graph-pair manifests with SHA256 digests. Separate full scenarios and lexical inventories across training, validation, lexical out-of-distribution test, and structural-challenge test. A pair is atomic and cannot cross splits. Track actual topology hashes and family/mode distributions. Counts and topology hashes measure coverage; **they do not by themselves demonstrate 10,000 or 100,000 independent semantic skills.** Both currently supported game families remain the real domain boundary.
3. **Frozen capture:** invoke the existing frozen G2-0040/G1 route once for each capture session, with the same G2-0052 candidate constructor and frozen occurrence encoder. Extract exactly the model-facing feature tensors and save bounded 16-pair feature shards (`.safetensors`); place gold labels in separate files. Store source/parent hashes, exact prompt IDs and topology identities. A completed shard is immutable. Interrupted capture resumes from verified completed shards, never silently recycling data from another parent or plan.
4. **Admission audit:** compute candidate reachability per split, shape and family/mode. Require complete training and validation capture, pair-complete train/validation examples, 98% reachable-pair threshold and balanced eligible four-cell coverage before training. Do not invent missing candidate edges or silently train on an incomplete experiment. Check actual data reachability on a small pilot before committing to a major Qwen capture.
5. **Streaming learning:** reuse cached feature tensors, bounded to two active shards by default. Train independent nested cohorts of 1,000 → 5,000 → 10,000 (and 25,000/100,000 once captured). All initialize from the verified G2-0057 selected model and reuse its combined supervision. Full epochs replace the arbitrary 960-step budget: `ceil(reachable_pairs / batch_pairs) × epochs`. Hash-pinned training configuration, checkpoint contents, optimizer states, model source, and deterministic shard shuffle make pause/restart reproducible. Validation selects checkpoints. Qwen is **not** loaded during G2 training.
6. **Final evaluation:** compare independent scale cohorts using one common validation population, select the winner *before* opening test and structural-challenge labels, and evaluate that frozen winner against original G2-0057. Final evaluation refuses overwriting an existing output. No sealed test data and no production promotion.

## Windows PowerShell workflow

Run these commands **from `C:\Users\subsi\main_computer\nanojev`** after applying the repository-root-relative patch to `main_computer`. They use the existing `$py` NanoJev interpreter variable. The default parent/output root is `main_computer\runtime\reports\gamer-v1`.

Create an immutable 10,000-graph training population, plus independent 400-graph validation, 400-graph lexical test, and 400-graph structurally new challenge populations:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale plan --train-graphs 10000 --validation-graphs 400 --test-graphs 400 --challenge-graphs 400
```

Pilot a single frozen capture shard. This is intended to expose feature/candidate problems cheaply, not certify the full run:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale capture --device cuda --max-shards 1
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale audit --allow-incomplete
```

Review its actual pair reachability and latency before deciding on a 10,000-graph frozen capture. Continue the same capture until complete; it verifies saved shards and resumes:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale capture --device cuda
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale audit
```

If the audit fails, **stop and diagnose** frozen endpoint coverage on the new graph shapes instead of reducing the threshold or backfilling gold into candidate generation. An unacceptable audit means a training result would not be interpretable.

Train matched independent nested cohorts, four full epochs each; each can be rerun with the identical command to resume after interruption:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale train --device cuda --epochs 4 --batch-pairs 2 --limit-training-graphs 1000 --output runtime/reports/gamer-v1/graph-g2-0059-train-1000
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale train --device cuda --epochs 4 --batch-pairs 2 --limit-training-graphs 5000 --output runtime/reports/gamer-v1/graph-g2-0059-train-5000
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale train --device cuda --epochs 4 --batch-pairs 2 --limit-training-graphs 10000 --output runtime/reports/gamer-v1/graph-g2-0059-train-10000
```

Each rung is a separate continuation of the **same** parent weights and is selected based on validation, not on test. For 10,000 graphs = 5,000 complete pairs with batch size two pairs, four epochs means 10,000 optimizer steps, not 960. Use the `--max-new-steps N` option for bounded test executions and resumable checkpoints (same command with the flag removed to continue).

Once all rungs have completed, evaluate *once* using the precommitted held-out populations:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale evaluate --device cuda --training runtime/reports/gamer-v1/graph-g2-0059-train-1000 --training runtime/reports/gamer-v1/graph-g2-0059-train-5000 --training runtime/reports/gamer-v1/graph-g2-0059-train-10000
```

The result is at `runtime\reports\gamer-v1\graph-g2-0059-final-evaluation\evaluation.json`. Compare exact graphs/pairs first, then edge errors, and break out allocation/encounter and renaming/role-swap. Scaling curves should not be treated as generalization if distinct source/target scenarios do not increase.

## Subsequent 100,000-graph run

The author permits 100,000 graphs (50,000 pairs) and larger datasets within its engineering limit. To run a **new independent 100,000-graph experiment**, create a **new plan/capture/evaluation destination** using `--output` and the corresponding `--plan`/`--capture` arguments, rather than replacing the existing 10,000-graph plan. Use a distinct `--seed` and preserve its source revision; measure frozen-capture runtime, disk footprint, and shard reachability in pilots. Scales of 25,000 and 100,000 should be attempted only after 1k/5k/10k curves demonstrate continued improvement and the generator is confirmed to provide genuinely new discriminative scenarios.

## Verification status and limitations

Local acceptance exercises real authored/validated graphs, actual parent weight loading and hash verification, a simulated frozen capture callback using the same shard and feature contract, actual CPU G2 optimizer steps, optimizer-state reload with deterministic replay, archive integrity, one-time evaluation, and the existing adjacent G2 regression suite. The 10,000-graph author-only benchmark created 2,000 distinct topology hashes in approximately 12 seconds on the test container; it **did not** run frozen-Qwen/G1 capture or GPU training and is not an estimate of end-to-end runtime. Real CUDA capture, large training and held-out generalization are unmeasured until they run on your machine. The pipeline correctly refuses to certify an unverified model.

This is a substantial capability correction, not evidence yet that a 100,000-graph model solves G2. If higher-scale models saturate, inspect actual ambiguous endpoint performance and authored scenario diversity before changing the neural architecture.

## Capture restart and launcher integration

The G2-0040 frozen ancestor runner requires a **fresh, nonexistent** output directory and the `main_computer` repository as its working directory. G2-0059 now allocates a new temporary ancestor directory for each capture or resumed capture invocation, passes absolute paths for the immutable plan, cache, and parent reports, and restores the previous working directory even if frozen capture raises an exception. It commits completed feature shards separately and verifies their hashes on restart. The frozen-capture source lineage also pins the legacy runner, author, training-label contract, G1 encoder, G2-0052 constructor, G2 feature extractor, and counterfactual-pair alignment code.

Local acceptance includes an interrupted-parent-run recovery test and checks that the frozen runner never receives a previously used output path. These are functional CPU checks; they are not substitutes for a first-shard CUDA pilot on the actual production ancestor stack.

## November 2026 learning transition (G2-0060 → G2-0061)

The original mandatory 98% dataset-wide G2-0059 gate remains appropriate for qualification, but was blocking the *experiment* that originally motivated the scaling pipeline. The G2-0060 contextual actor policy reached 125/128 (97.66%) on independent author-generated holdout pairs, with 11 missing `owns` edges and zero new regressions. This is **not a formal pass** and does not prove transfer beyond the two synthetic game families.

A distinct **G2-0061 experimental learning lane** now captures contextual-policy features from a prefix of the immutable G2-0059 plan, filters gold-incomplete *paired* examples from the training loss (without reinterpreting them as negatives), and trains G2-0057's objective with a separate validation score covering both reachable and unreachable graphs. The operator must explicitly permit research below 98% with `--research-coverage-exception`; nothing changes the formal `train`, `rollout` or `evaluate` gates and no sealed test is accessed. The next major question is whether 1,000 → 5,000 → 10,000 genuinely varied graphs improve validation generalization. See `G2_0061_EXPERIMENTAL_GRAPH_LEARNING.md`.
