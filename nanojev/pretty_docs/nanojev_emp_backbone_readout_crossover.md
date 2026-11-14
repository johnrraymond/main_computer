# NanoJev backbone × readout checkpoint crossover

## Scientific question

The stage-27 Hard EMP checkpoint at radius 464 predicted an extra **−100** on four nearby cancellation cases; after 250 gradient steps at stage 28 (radius 480), all four were correct without errors on the 26 nearby controls. Is the change sufficient when applied **only to the arithmetic readout**, **only to the backbone**, or only when both changes are present?

This is a **fixed, read-only**, four-way component replacement. It does not train a model, use an external arithmetic executor for inference, change the Hard EMP/detector, or alter either checkpoint. The previous `nanojev_emp_defect_cross_transfer` evidence supplies the fixed thirty-case population and its exact before/after predictions.

| Name | Backbone (all other AdditionNet parameters) | Readout (both sign_head and digit_head) |
|------|-------------------------------|------------------|
| `B0R0` | stage 27 | stage 27 |
| `B0R1` | stage 27 | stage 28 |
| `B1R0` | stage 28 | stage 27 |
| `B1R1` | stage 28 | stage 28 |

`regression_head.*` belongs to the backbone group but is **not used by the decoded arithmetic output**. Swaps include both sign and digit heads as a single component: swapping only one could create artificial output incompatibility. Shape, dtype, head presence, state-key equality, and finite weights are verified before any inference. Each combination is evaluated with the exact same deterministic input cards and model topology. `B0R0` and `B1R1` predictions are required to reproduce all thirty archived evidence rows exactly; any mismatch aborts without a misleading summary.

## Command

```powershell
& $py -m tools.nanojev_emp_backbone_readout_crossover `
  --run "C:\Users\subsi\NanoJev\runs\nanojev_isolated_defects_v1" `
  --cross-transfer "C:\Users\subsi\NanoJev\runs\nanojev_defect_cross_transfer_v1" `
  --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --hard-state-dir hard_emp_frontier_v2 `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_backbone_readout_crossover_v1" `
  --device cuda `
  --before-stage 27 `
  --after-stage 28
```

## Outputs

- `result.json`: correct / repaired / regressed counts per combination; per-failure categorization; limitations and SHA-256 snapshot provenance.
- `cases.csv`: the full four-combination prediction table for the four repaired errors **and all 26 previously correct controls**, native gold log scores, and a backbone × readout interaction contrast.
- `head_diagnostics.csv`: sign class margins and all seven digit-class margins against the best competing class, per case and combination.
- `head_logits.jsonl.gz`: raw sign and 7×10 digit logits for every combination, permitting independent verification of exact decoding and digit errors.
- `component_changes.json`: parameter-group L2 norms and before/after relative changes; not causal importance scores.
- `config.json`: immutable input and checkpoint fingerprints, with a config-drift refusal on repeat execution.

`gold_log_score` is **negative native CE**: sign cross-entropy plus the *mean* of seven digit cross-entropies. It is **not** a calibrated probability of an integer answer. The interaction is `gold_score(B1R1) − gold_score(B1R0) − gold_score(B0R1) + gold_score(B0R0)`; interpretation is descriptive for a learned nonlinear model.

## Interpretation

- `B0R1` fixes a case: replacing *only* the arithmetic readout is sufficient for that case in the old backbone.
- `B1R0` fixes a case: replacing *only* the backbone is sufficient for that case under the old readout.
- Both fix a case: more than one component change can produce a correction.
- Neither fixes, but `B1R1` does: a cooperative or compatibility-dependent change is suggested; **this is not proof of a uniquely coordinated neural mechanism**.

Always review `damaged_previous_controls`, not just failure-repair counts. A mix may repair a defect but introduce errors in previously correct neighbors. Thirty cases are only a local controlled panel, not an estimate of generalization. Disagreement between hybrid and natural performance also can reflect incompatible intermediate representations from different checkpoints.
