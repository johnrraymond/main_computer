# G2-0057 — Matched counterfactual consistency bake-off

## Purpose

G2-0056 demonstrated that training more diverse examples improved edge transfer (70.943% recall on its first independently generated 32-graph final test), but no complete final graph was correct. This experiment tests whether **paired semantic correspondence**, rather than additional examples or another architecture, improves complete graph reconstruction.

## The four interventions

All arms begin with G2-0056's **validation-selected 80-graph set-predictor checkpoint** (best step 720) and receive 960 additional AdamW steps over the **same 80 training graphs (40 authored counterfactual pairs)**, in the **same pair minibatch order**. The standard G2-0055 Hungarian-matched supervised set-prediction loss is identical across arms.

| Arm | Additional training-only paired objective |
|---|---|
| A_control | None |
| B_renaming | Canonical-ID aligned relation/source/destination consistency for renaming pairs |
| C_role_swap | The same aligned consistency for role-swap pairs |
| D_combined | Both pair types |

The extra term is an averaged symmetric KL divergence over relationship-label probabilities and source/destination distributions projected into an **authored canonical node-ID space**. The two graph predictions are matched on *training gold triples*, not by their arbitrary query slots or surface token offsets. Only training graphs receive canonical alignment annotations. A `role_swap` changes the observed actor/action text; the author-verified graph retains its node IDs, edge direction, and semantic truth. Reversing all edges would be **incorrect supervision**.

The paired loss has weight `0.30`, divided by total pairs in the minibatch. No manually specified task-specific inference rules, graph-label inputs during inference, or gold-based candidate filtering are introduced. Frozen G1, the G2-0052 endpoint interface, and the G2-0048 encoder remain unchanged.

## Cohorts and evaluation policy

The prior G2-0056 cohorts are regenerated and checked against the 0056 saved SHA-256 prompt identities:

- **Training:** 40 counterfactual pairs / 80 graphs, balanced by allocation/encounter and renaming/role-swap.
- **Checkpoint selection:** 8 separate training-domain pairs / 16 graphs. Select by exact graphs, then total edge errors, then missing edges, then exact pairs and earliest step. Tie between arms favors less intervention. No test cohort influences selection.
- **Historical diagnostic probes:** existing 32-graph counterfactual probe, existing 32-graph fresh probe, and the previously scored G2-0056 final 32 graphs. Evaluated **only for the validation-selected model**, after selection.
- **New G2-0057 final test:** 16 additional freshly authored counterfactual pairs / 32 graphs, never used for training or checkpoint selection, scored **once** for the validation winner.

The prior G2-0056 final prompts and the original 8/8 memorization cohort must match the stored 0056 identity lists. Candidate coverage and the G2-0050 legacy per-prompt replay are rechecked. The sealed test is never accessed. Results are research-only and prohibit promotion.

## Outputs

Under `runtime/reports/gamer-v1/graph-g2-0057-counterfactual-bakeoff`:

- `g2_0057_counterfactual_bakeoff_report.json`
- `g2_0057_counterfactual_models.safetensors`
- `g2_0057_counterfactual_policy.json`
- `g2_0057_counterfactual_predictions.jsonl.gz`
- `g2_0057_counterfactual_candidate_per_case.jsonl.gz`

Report all four arm checkpoints and training/validation results, including exact graphs and pairs, missing and extra edges, edge precision/recall, cardinality errors, allocation/encounter and renaming/role-swap breakdowns. Report the final cohorts for the *selected* arm only. No sealed evaluation, promotion, or mutable overwrite.

## Run

From the repository's `nanojev` directory, after applying the changed-file patch at the repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0057-counterfactual-bakeoff --device cuda
```

This run requires all immutable parent receipts, including G2-0056's checkpoint, report, prediction ledger, candidate ledger, and policy. G2-0057 refuses a mismatched parent, a stale prompt cohort, or an existing output directory. The CUDA preflight backpropagates **each arm's actual paired objective** before the expensive capture.

A gain in validation or final edge recall without exact graph gains is **not** a G2 certification. If none of the arms improves complete graph reconstruction, inspect the logged error categories before changing architecture or promoting anything.
