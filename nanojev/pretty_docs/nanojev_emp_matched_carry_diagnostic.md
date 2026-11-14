# NanoJev EMP matched carry/borrow diagnostic

## Question

The input-only detector at radius 16,384 found that **same-sign input pairs** with **three or more carry positions** were associated with both winning banks failing. This follow-up asks whether a controlled change in low decimal digits which increases carry count also increases errors **within same-sign additions**, while separately testing borrow chains **within opposite-sign cancellation**.

This is the *next diagnostic*, **not** a specialist head, a router, or training. It uses a frozen checkpoint and a fresh, reproducible scan of permitted TRAIN-family anchors. It never calls `optimizer.step` or modifies the campaign.

## Command

Stop the trainer so its checkpoint is immutable during inspection. Apply the patch and run:

```powershell
& $py -m tools.nanojev_emp_dual_bank_diagnostic `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --device cuda `
    --matched-carry `
    --matched-candidates 16384 `
    --matched-pairs 128 `
    --matched-max-target-drift 0.20
```

Optionally use `--radius 16384` to pin the diagnosed stage explicitly. `--matched-carry` does not require `--mine-hard` or `--detect-hard`. Existing `--mine-hard` and `--detect-hard` remain available independently.

Console output `EMP_DIAG_MATCHED_CARRY` contains all four banks' per-group results. The full diagnostic JSON includes `matched_carry_intervention` and the original DEV results. The script writes its report **outside** the campaign and refuses a changing checkpoint.

## Design

1. Reconstruct the earlier 8,192-case detector pool (controlled by `--hard-candidates`) and exclude **all of those anchors** from this follow-up; this prevents direct re-use of detector fit or evaluation anchors. Sample a fresh, independently seeded pool of `--matched-candidates` examples from the legal outward TRAIN generator; exclude sealed TEST, DEV and specialist-reserved TRAIN anchor families.
2. Select up to `--matched-pairs` unique TRAIN anchors per sign group. Generate exactly two **diagnostic counterfactuals per anchor**, using the same signs, high-order decimal operand prefixes, operand decimal lengths and magnitude shells. Reject candidates when the two correct answers differ in sign, digits or binary magnitude shell, or by more than the requested relative drift.
3. For **same-sign addition**, contrast two small suffixes that avoid carry versus two large suffixes that induce at least 3 carry positions. For **opposite-sign cancellation**, contrast a suffix that requires few borrows with a suffix that induces at least 3 borrow positions. Require the control's full chain count to be at most 1, and treatment's to be at least 3. Two different numeric templates vary the suffixes across anchors. Never interpret a cross-sign gap as a matched causal sign comparison.
4. Run *all four frozen banks* over both inputs. For each bank report matched control/treatment failure rates (5% criterion), difference in failure rates, anchor-paired bootstrap interval, exact McNemar discordance test, raw count of worsened versus improved examples, scale bias, scale SD, and p50/p90/p99 of absolute relative error. Report the same paired failure comparison for the *provisional best two distinct banks*.
5. Record the selection sample sizes, chain counts, mean within-pair target drift, model checkpoint SHA, and all nonmatches. The paired contrast uses each anchor once in each sign group, so results are **paired**, not independent row-level percentages.

### What this test does **not** prove

The intervention changes decimal suffix digits **and** the true arithmetic residual to alter carry/borrow count. Even after matching high-order prefixes, operand sign and magnitude bands, it does not isolate carrying as a single independent causal variable. Its result is evidence about *sensitivity to deliberate carry-inducing versus non-carry-inducing perturbations*, not proof of the unique underlying neural difficulty mechanism. A large error difference, reproducible across templates and later checkpoints, justifies a sharper specialist-training hypothesis. If matched pairs are scarce, report their number instead of implying a precise effect.

Cross-sign same/opposite comparisons are **not** target-magnitude matched and should not be interpreted as causal sign comparisons. DEV and sealed TEST are not opened for this new comparison; the ordinary diagnostic's established DEV summary still runs. The model may have previously seen similar anchors during original training, so these are *unseen to the previous detector*, not proven base-bank OOD data.

No specialist head, gate, curriculum, four-bank optimizer, or runtime routing is changed.
