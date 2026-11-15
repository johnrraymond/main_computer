# G2-0046 — Frozen-feature multi-prompt relational discrimination

## Objective

G2-0045 established that an additive nonlinear head could exactly fit the selected difficult TRAIN graph while the tested linear heads did not; it **did not establish prompt-disjoint transfer** or linear separability of that difficult graph. G2-0046 tests whether *one scorer* trained on many TRAIN prompts learns shared relationship discrimination using the unchanged 48-dimensional G2-0033 frozen edge representations. Failure remains ambiguous between representation, optimizer, objective, architecture, dataset difficulty and step budget; no mathematical inseparability claim is made.

This experiment is **diagnostic only**. It neither selects a production policy nor trains G1, the G2-0033 parent, the G2-0039 relationship model, count heads, candidate generation or the graph decoder. No DEV labels, sealed TEST labels or DEV capture are used. No model is promoted or certified.

## Lineage and preservation

Before fresh capture, verify complete immutable G2-0040 through G2-0045 parent lineage, parent G2-0045 report schema, hashes, policy-disabled status, loss-arm inventory, direct controls, feature weight shapes and ledger cardinality. Recompute the same G2-0040 TRAIN cohorts and refuse any change to recorded SHA256 values or G2-0033 decoder policy. Compare the frozen parent state before and after every fitted arm.

G2-0045's two cases (**simple**, **hard**) and six unseen TRAIN prompts are pre-reserved for transfer evaluation, never fitted. Add a deterministic SHA-ordered holdout to reach approximately 20% of the original source TRAIN cohort (at most 32 cases), whether each case is reachable or not. Source TRAIN, TRAIN-fit (80) and natural TRAIN-audit (80) must be mutually prompt-disjoint. Audit and TRAIN-fit are measurement-only; neither can influence gradient updates, hyperparameters, checkpoint selection, training eligibility or policy selection.

Only source TRAIN examples whose entire gold relationship set is in the shortlist, whose exact oracle scores reproduce gold through the **actual frozen G2-0033 decoder**, and whose labels include both positive and negative candidate edges are eligible for fitting. Other source TRAIN cases are excluded from fitting, but separately counted. An omitted gold edge cannot be repaired by any downstream scorer; all evaluation reports split candidate-reachable from candidate-unreachable cases and report irreducibly missing gold edges. Decoder oracle-reachability and candidate reachability are distinct conditions.

## Matched architecture and learning comparison

Both models receive the **same cached G2-0033 post-interaction h**, the same prior, same unchanged count outputs and same exact graph decoder. Fitting observes TRAIN labels only. Shared readouts:

- **Linear**: copied 48→1 parent edge readout, trainable.
- **Nonlinear**: copied frozen parent linear readout plus a trainable, initially zero 48→96→1 GELU correction.

Two **predeclared** objectives are tested for both architectures: weighted BCE, and weighted BCE plus 1.1× per-group hardest-negative softplus margin. Each prompt contributes one loss regardless of its candidate-edge count, preventing large graphs from overwhelming small ones. Use exactly the same deterministic shuffled prompt batches for all four runs.

Default budget: **720 Adam steps**, eight prompts per optimizer step, and training-only evaluation at step 1 and every 120 steps. Linear learning rate 0.008, nonlinear 0.004, gradient clipping 5.0. No early stopping based on transfer, TRAIN-fit or audit. No learned head is used in production or promoted as a policy.

## Evaluation and outputs

For each predeclared arm and the frozen parent, calculate exact graph reconstruction, missing/extra edges, group rank inversions, graph count error, unreachable gold edges and results by family and candidate reachability on:

- supervised TRAIN learn cohort (in-sample only);
- transfer-only TRAIN cohort (including all eight reserved G2-0045 cases);
- independent TRAIN-fit (80 cases), measurement only;
- natural TRAIN-audit (80 cases), measurement only.

Interpretations are deliberately restricted to observed prompt-disjoint improvement in exact graph reconstructions or missing+extra counts. Even success does not prove all frozen features are sufficient for arbitrary graph reconstruction; failure cannot independently prove that new representations are necessary.

All result paths are immutable, written with staging and final rename, and contain four artifacts:

- `g2_multiprompt_discrimination_report.json`
- `g2_multiprompt_discrimination_per_case.jsonl.gz`
- `g2_multiprompt_discrimination_development.safetensors` (diagnostic readouts only)
- `g2_multiprompt_discrimination_frozen_policies.json` (explicitly disabled)

## Execution

Apply the ZIP at the **repository root** with its `nanojev/` directory intact. From inside `nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-multiprompt-discrimination --device cuda
```

The default immutable output directory is `runtime/reports/gamer-v1/graph-g2-multiprompt-discrimination-0046/`. To alter the budget, use `--steps`, `--batch-prompts`, or `--eval-every` and a **new** `--output` directory. Valid bounds: steps 1–4000, batch 1–32 prompts, evaluation interval 1–steps. Default parent report directories follow the G2-0045 CLI, plus `--separability45-run` for the new parent verification gate.

## Acceptance

Source lineage, all original G2-0045 direct controls and policies, frozen score reconstruction and exact decoder path, deterministic prompt-disjoint cohorts, fitting-only gradients, immutable products and a disabled output policy must all pass. This patch registers a new experimental CLI entry point but makes **no inference-path or G2 certification change**. The full CUDA experiment must be run in the user's NanoJev runtime to produce scientific results; tests here exercise bounded synthetic CPU learning and original G2-0045 artifact integrity.
