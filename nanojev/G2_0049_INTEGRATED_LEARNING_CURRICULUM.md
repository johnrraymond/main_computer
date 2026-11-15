# G2-0049 — Integrated learning-method curriculum experiment

## Purpose

Test whether continuing **the existing G2-0048 occurrence-evidence scorer** with a
structured *learning curriculum* yields transfer benefits that exceed merely
training that same scorer longer.

G2-0048's reported baseline is 110/111 exact on the TRAIN learning set and
9/32 exact on the previously fixed TRAIN-partition transfer cohort (9/25
candidate-reachable). That cohort has been repeatedly examined in earlier
experiments. Its evaluation is **diagnostic**, not independent certification.

G2-0049 does **not** replace the production model, promote a checkpoint,
unseal DEV/TEST, alter G1/Qwen, alter candidate mining, or tune the decoder's
policy. It writes an immutable experimental result next to earlier G2 runs.

## Design: four matched arms from one saved parent

Every arm initializes its binding weights from the verified
`g2_occurrence_evidence_repair_development.safetensors` artifact. All arms
receive the exact same TRAIN-only prompt schedule, the same extra fixed
TRAIN-only replay anchor prompts, the same AdamW optimizer, learning rate,
number of gradient steps, and otherwise unchanged G2-0048 edge evidence.

| Arm | Foundation phase | Ranking phase | Integration phase |
| --- | --- | --- | --- |
| `replay_bce` | BCE + replay | BCE + replay | BCE + replay |
| `replay_rank` | BCE + replay | BCE + ranking + replay | BCE + ranking + replay |
| `replay_count` | BCE + replay | BCE + replay | BCE + group/graph counts + replay |
| `integrated` | BCE + replay | BCE + ranking + replay | BCE + ranking + group/graph counts + replay |

Stages are fixed before observing transfer. Foundation occupies the first
one-sixth of steps, ranking the next third, integration the final half.

- **BCE**: G2-0048's weighted edge classification objective.
- **Ranking**: within-source/relation-group softplus contrastive margin,
  using candidate positives and competing negatives from existing TRAIN gold
  labels. No invented counterfactual pairs.
- **Cardinality**: a small learnable correction to the *frozen* decoder's
  per-group count log-probabilities and global total. Predictions are based on
  inferred edge scores and label-blind frozen count priors; TRAIN count targets
  are derived from the same gold edge sets as BCE. The original dynamic-
  programming graph decoder and policy remain unchanged.
- **Retention**: an ID-determined one-in-five subset of learning prompts is
  replayed in approximately 25% of every minibatch, identically across arms.
  `train_fit`, `natural_train_audit`, and `transfer` are never trained upon.

For G2-0049, defaults are **360 steps per arm**, eight prompts per step, one
immutable report. The saved G2-0048 weights are loaded, not retrained.
The source Qwen/G1/G2-0040 capture is regenerated under the existing read-only
harness and checked for lineage and cohort stability; this is not an offline
run using only the saved 0048 ledger.

## Command

From `C:\Users\subsi\main_computer\nanojev`, after applying the changed-file
ZIP to the `main_computer` repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-integrated-curriculum --device cuda
```

Optional `--steps`, `--batch-prompts`, `--eval-every`, `--root`, and explicit
parent run paths are available; nondefault budgets are separately identified
by their report protocol, not directly comparable to the canonical defaults.

By default the immutable result is created at:

`runtime/reports/gamer-v1/graph-g2-integrated-curriculum-0049/`

Files:
- `g2_integrated_curriculum_report.json` — complete metrics, training history,
  paired fixed/regressed outcomes, method gates, parent hashes, all cohort IDs
- `g2_integrated_curriculum_per_case.jsonl.gz` — each graph's detailed errors
- `g2_integrated_curriculum_development.safetensors` — four experimental arms
- `g2_integrated_curriculum_frozen_policies.json` — disabled production policy

The console prints a compact summary after training rather than the thousands
of cohort hashes contained in the full report.

## Interpretation and decision

Evaluate all arms on the same 32-prompt transfer cohort and the independent
TRAIN-only fit/audit cohorts after training. Compare the new models both to
(1) the original G2-0048 checkpoint and (2) **the matched BCE continuation**.
The matched comparison distinguishes loss/curriculum effects from extra steps.

The engineering signal is at least **four additional exact candidate-reachable
transfer graphs relative to 0048**, no increase in missing+extra reachable
edges, and retention of at least the G2-0048 learning exactness minus five
whole graphs. A curriculum-specific gain additionally requires at least four
exact graphs beyond the matched `replay_bce` continuation. These thresholds
are *practical screening rules*, not statistical proof. Report allocation
and encounter transfer separately: improving only allocation does not resolve
the encounter failure.

No DEV or sealed TEST examples are used for optimizing, calibration, or
promotion. Repeated use of the 32 TRAIN-partition transfer prompts means this
experiment is not an unbiased estimate of final generalization. A new,
structurally disjoint development cohort will be needed before a certification
claim, regardless of these exploratory results.

## Verification

CPU acceptance / backward compatibility:

```powershell
python -m pytest -q nanojev/tests/test_nanojev_gamer_v1_g2_integrated_curriculum.py nanojev/tests/test_nanojev_gamer_v1_g2_occurrence_evidence_repair.py nanojev/tests/test_nanojev_gamer_v1_g2_curriculum_pretest.py
```

No separate rollback archive, manifest, or production installation is included.
