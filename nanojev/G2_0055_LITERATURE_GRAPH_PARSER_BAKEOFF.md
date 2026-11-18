# GAMER G2-0055 — Literature-Informed Graph Parser Bake-off

## Purpose

Test whether genuinely structured neural graph-prediction architectures can learn **complete exact graphs** from the frozen G1/G2-0052 repaired endpoint inventory. Earlier G2-0054 improved top-512 edge recall but achieved **zero exact training graphs and zero exact probe graphs**. G2-0055 is a research experiment, not a production upgrade.

The three arms are grounded in research on biaffine semantic dependency parsing (Dozat & Manning, 2018), second-order semantic graph parsing (Wang et al., 2019; Yang & Tu, 2022), and unordered set/triple prediction with bipartite matching (Sui et al., 2020). They are **small experimental adaptations**, not exact reproductions of the papers or claims of equivalent accuracy.

## Architecture

- `biaffine`: distinct source/destination neural projections, relation-conditioned bilinear interaction, geometry, and learned graph cardinality. Evaluates the complete typed-edge inventory rather than truncating it to the first 512 edges.
- `higher_order`: the biaffine model plus predicted top-512 edge neighborhoods passing differentiable incident-edge messages through shared nodes, updating all edge scores. The top-512 neighborhood is **label-blind** and is not the full graph decoder's hard candidate limit.
- `set_prediction`: 64 unordered learned triple slots; cross-attention over frozen node occurrences; Hungarian-matched supervision of relation, source and destination. Relation 0 means `null`, and decoded slots must map onto an admissible typed G2 edge.

All arms receive exactly the same frozen G1/G2-0048 representations and G2-0052 repaired candidate inventory. No oracle labels are inserted into the inventory, features, neural inference, learned-cardinality decisions, or set decoding. Gold edges are used solely for supervised training and post-hoc auditing. G1 and the G2-0048 encoder remain frozen.

## Mandatory pre-generalization gate

Use eight training graphs: one complete counterfactual pair from each allocation/encounter × role-swap/renaming cell. Each arm must reach **8/8 exact** on those same training cases (not merely good edge F1). Per-arm training stops early on success. **Any arm failing its gate is not trained or evaluated on train-fit, existing probes, or new synthetic probes.** If all fail, the experiment records memorization failures and terminates with no probe evaluation.

Passing arms continue to train on 80 examples and are measured on a disjoint 16-example training-fit cohort, the existing 32-example counterfactual probe, and an additional 32-example fresh author-generated synthetic probe (not a sealed independent test). The train-fit cohort is the only development-selection surface. The existing and fresh probes **never** determine a winning architecture. G2-0054 Arm D metrics are included as a historical reference, not an equal-training-budget contender.

The implementation intentionally does not impose a fixed cardinality or fixed 96-edge output: biaffine/higher-order learn graph cardinality from training; set prediction learns a `null` class and slots, capped at 64 triples. The 96-edge maximum is an upper safety cap for the two edge-parsing arms.

## Run

From the `nanojev` directory after applying the repository-root-relative ZIP:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-literature-bakeoff --device cuda
```

The CLI accepts `--memorization-steps` (default 240), `--full-steps` (default 320), `--batch-prompts` (default 2), and the same parent-path overrides as G2-0054 plus `--candidate54-run`.

The CUDA preflight **actually performs backward propagation and inference for each architecture** before the costly G1/G2 capture. The preflight reports `GAMER_G2_0055_DEVICE_PREFLIGHT` with `status: PASS` or throws before capture. The memorization gate prints one `GAMER_G2_0055_MEMORIZATION_GATE` result per arm.

## Outputs and controls

`runtime/reports/gamer-v1/graph-g2-literature-bakeoff-0055/` stores:

- `g2_literature_bakeoff_report.json`
- `g2_literature_bakeoff_per_case.jsonl.gz` (including false-positive and false-negative edge indices for each evaluated case)
- `g2_0055_development_models.safetensors`
- `g2_0055_frozen_policy.json` with `enabled: false`

Outputs are immutable, G2-0054 parent ledger/weights hashes are verified, and the new models use separate prefixed weight namespaces. The current full CUDA experiment is **not verified by CPU tests**; training success and generalization remain empirical questions. G2 remains uncertified, and no production promotion is authorized.
