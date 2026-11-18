# G2-0056 — Set prediction learning-scale experiment

## Objective

After G2-0055 memorized 8/8 graphs at step 320 but produced 0/80 exact
predictions on non-memorization cases, test whether greater *diverse training
coverage* improves transfer before changing architecture. This is **research
only** and does not certify G2 or modify production.

## Protocol

1. Verify the original G2-0055 step-320 **best** model with its parent reports,
   policies, prompt identities, exact-training gate and SHA256 hashes.
2. Recapture frozen G1 raw evidence, G2-0052 typed candidate inventory, and
   frozen G2-0048 occurrence features. No G1/G2-0052 or encoder parameters
   are trained. Existing G2-0050/0051 parent replay remains identical.
3. Keep 48 balanced TRAIN author pairs (96 graphs). Reserve the **first two
   pairs of each family/mode cell** for validation (8 pairs = 16 graphs). The
   remaining ten pairs/cell form 80 train graphs, with four **nested** training
   cohorts: 1, 2, 4, 10 pairs per cell = 8, 16, 32, 80 graphs. The 8 original
   exact-memorization graphs are the first training pair in each cell; their
   hashes must match the verified G2-0055 diagnosis.
4. Run **four independent** weight continuations from the identical step-320
   source, with fresh independent AdamW optimizers and **960 steps each**
   (default), batch size four. This is a matched-compute scale comparison, not
   sequential fine-tuning; each arm starts from exactly the same source.
5. Every 120 steps, score *only* the 16 validation graphs. Choose each arm's
   checkpoint by: exact validation graphs, then minimum missing+extra edges,
   then minimum missing, then exact validation pairs, then earliest checkpoint.
   Among stages choose the same validation quality, preferring the smaller
   training cohort on a tie. Training scores are reported but do not select.
6. Once the arm and checkpoint are selected, perform frozen inference on the
   previously inspected 32 counterfactual probes and 32 synthetic fresh probes,
   and on **32 newly generated final-test graphs** (16 pairs) appended after
   all prior probes. These new prompts are not in any earlier G2-0055 cohort.
   The new test cannot affect model selection. Its one-shot result is a
   diagnostic, not permission to tune against the test afterward.

**Important validation qualification:** the 16-graph TRAIN-fit split was used
by earlier G2 research to calibrate other models. It is not a pristine
independent external validation dataset. It remains disjoint from all four
G2-0056 training cohorts; only this experiment's new final-test cohort is
never previously evaluated by the G2-0055 model.

## Measurement

For each training size record best validation checkpoint, full validation
history, exact train and validation graphs/pairs, edge recall and precision,
missing and extra edges, and breakdowns by allocation/encounter and
role-swap/renaming. After selection, record the same metrics for historical
probes and new final holdout plus source/destination/relation nearest-error
categories for missed relationships. Save all four stage-best checkpoints,
source hashes, validation/test prompt hashes, model-selection receipt and
compressed per-case predictions. All inference excludes `labels`, `edges`,
`positive` and `truth_count` model inputs.

## Run

Apply the changed-file ZIP at the **main_computer** repository root. From
`main_computer\\nanojev` on Windows with `$py` set to the NanoJev Python:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0056-learning-scale --device cuda
```

Optional fixed budgets: `--steps 960 --checkpoint-interval 120 --batch-prompts 4`.
Output is immutable at:

`runtime/reports/gamer-v1/graph-g2-0056-learning-scale`

If rerunning with changed experimental budgets, use a new `--output` path;
the runner refuses to overwrite an existing report.

## Decision

No production promotion. If the 80-graph arm improves new-cohort exact graph
reconstruction relative to smaller scales, scale diverse training and perform
independent validation. If all arms remain near zero, investigate transferable
object-slot binding and family-specific generalization rather than adding
more arbitrary decoding thresholds. No sealed G2 DEV/TEST was used.
