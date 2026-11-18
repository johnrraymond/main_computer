# G2-0059: gated evidence-candidate rollout

The 16-pair, label-blind evidence pilot reported **16/16 reachable**, **zero missing gold candidate edges**, **eight recovered edges** and **zero regressions**. That is a pilot result, **not** authorization for 10,000-graph training. This patch adds controlled rollout to unseen examples and keeps all existing pilot captures untouched.

## Preconditions

Apply the patch at the `main_computer` repository root. From the `nanojev` directory, use the existing frozen parents, immutable training plan and evidence pilot at:

- `../runtime/reports/gamer-v1/graph-g2-0059-plan`
- `../runtime/reports/gamer-v1/graph-g2-0059-evidence-pilot`

The `rollout` command validates the pilot's committed shard hashes, 16 original pair receipts, comparison report (including no regressions), ancestor identities, and frozen dependency hashes **before** loading Qwen. An incomplete or changed pilot is refused. The code file for G2-0059 itself is allowed to differ from the earlier pilot; other frozen dependencies are not.

The new target is `../runtime/reports/gamer-v1/graph-g2-0059-evidence-rollout`. Do **not** use the `graph-g2-0059-capture`, `graph-g2-0059-spatial-pilot` or `graph-g2-0059-evidence-pilot` directories as rollout outputs.

## Step 1: unseen canary (128 pairs)

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale rollout --stage canary --device cuda
```

The new capture replays the original 16 diagnostic pairs into its own feature store, then captures **128 previously unseen** training pairs (256 new graphs), over four game-family/mode cells. Only the 128 unseen pairs count toward canary acceptance. The command captures nine 16-pair shards, supports interruption/resumption, and writes `rollout-canary-gate.json` when the canary cohort is complete.

Acceptance requires >=98% reachable counterfactual pairs, at least 24 distinct topologies, every family/mode cell represented with >=8 pairs and >=95% reachability, and >=95% reachability in any adequately sampled with/without-distractor stratum. All thresholds must pass. `GATE_FAILED` does not authorize further capture. Inspect `failed_checks` in the returned result.

Optionally append `--max-shards 1` to limit capture per invocation; rerun the same command to resume. Intermediate results are `CAPTURE_IN_PROGRESS` and do not pass the gate.

## Step 2: independent qualification (512 + validation)

Only after the canary passes:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale rollout --stage qualification --device cuda
```

This captures an additional **512 unseen** training pairs (1,024 new graphs), and the **200 validation pairs** (400 graphs) from the immutable, scenario-disjoint split. Each new cohort is evaluated independently with the same coverage, structure and stratification gates. Their reports are `rollout-qualification-train-gate.json` and `rollout-qualification-validation-gate.json`. There is no test/challenge capture in either of the first two stages. Previously accepted shards are never rewritten.

## Step 3: full corpus (only after both gates pass)

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale rollout --stage full --device cuda
```

This finishes the plan across train/validation/test/challenge using the same frozen policy. It cannot report `GATE_PASSED` while any split is incomplete or while complete training and validation coverage is below 98%. It does not train a model, select checkpoints, or promote production. Preserve the captured features and use the existing `audit`, `train` and `evaluate` commands after the complete-data gates pass.

## Interpretation and safety

The tested acceptance target is **candidate representability**, not learned graph accuracy. Graph sizes, score models and frozen G1 logits are unchanged; the evidence policy derives extra span hypotheses from text and frozen G1 output without consulting author labels. Gold is read only **after** candidate construction for the training-data coverage audit.

The patch has CPU tests for real shard persistence, resume, missing-pilot refusal, interrupted canary, failure to meet the unseen canary gate, gate-ordered qualification, and test/challenge isolation. No full CUDA capture or generalization result is claimed by the patch; those require running the above commands on the operator's machine.
