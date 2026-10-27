# Qwen Backbone Zero-Line: Train Style Leakage Repair (v2)

## Diagnosed defect

At residual radius 1, the old trainer used `delta_options[step % 3]` and
`style = step % 3`. On 3 out of every 4 training steps, the style perfectly
identified the answer irrespective of operands. Low single-card cross-entropy
could then coexist with ~33% DEV accuracy and no intact-vs-control advantage.

## Repair

Train with the same certified frozen Head 1/Head 2, EMP cards, LoRA attention
parameters, and signed-residual cell gates. Within each residual stage, use
`style=(stage_step // (2*radius+1)) % 5`. Every consecutive balanced residual
block has a common style and five blocks cross all class-style combinations.
Difficulty-weighted overrides still provide hard examples on each fourth step.
The `preview` now audits the deterministic class/style cross-product.

The trainer schema and output lineage are v2, separate from the failed v1
checkpoints; there is deliberately no v1 LoRA resume or migration.

## Run

```text
python -m tools.nanojev_hybrid_emp_backbone_zero_line --stage preview --device cpu
python -m tools.nanojev_hybrid_emp_backbone_zero_line --device cuda
```

## Scope and interpretation

This patch fixes one verified training-data shortcut. It does **not** imply
that the backbone will pass per-cell zero-line certification. Successful
training still requires intact numerical edits to outperform masked and
mismatched controls, and the trainer refuses sealed TEST on DEV failure.
A low value of single-card training loss is not a substitute for held-out
arithmetic accuracy. Retain the historical v1 checkpoint as a failed baseline.
