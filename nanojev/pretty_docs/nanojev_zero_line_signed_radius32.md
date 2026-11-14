# NanoJev signed zero-line radius 32 — phase 3 backbone campaign

## Research target

Extend the already learned near-equality relationship from radius 16 to **all 65 signed integer residuals** `-32,-31,...,-1,0,1,...,31,32` at actual signed operand magnitudes up to **2,000,000**, without adding inference-time arithmetic, magnitude routing, or expert banks.

**Not just ±32.** Every residual is supervised by the exact signed integer answer and must be covered in all **65 × 13 = 845** sampled magnitude/residual cells. Train the same recurrent backbone, freezing the original digit encoder and output heads. Keep legacy arithmetic (65,025 ordered examples), original zero-only detection, and the previously attained ±16 DEV matrix protected.

This is **not** a claim of exhaustive arithmetic certification across ±2M, which would be impossible from finite sampled tests.

## Why the source is `latest.pt`, not `promoted.pt`

The previous ±16 run at `nanojev_zero_line_signed_radius16_v1` finished at step 8,250, passed three consecutive perfect DEV probes and a complete 65,025-card legacy audit, but **did not get formal promotion**: exactly **one** sample out of 6,864 sealed TEST cards failed (`1572864-2000000`, residual `-16`). The source loader **checks** that precise research result, verifies the previous code hash, source/zero-only checkpoint chain, stored step/streak, historical audit, the sealed-test record, and the report. It then loads **`latest.pt` as an explicitly experimental research starting point**, preserving the old test seal and the original source checkpoints. No bogus retroactive `promoted.pt` is written.

All of the previous ±16 TEST **magnitude families** are quarantined from new radius32 TRAIN/DEV/**fresh TEST** cancellation samples. The prior experiment's 2,000,000 boundary is reused only in a **separately reported endpoint stress test**, never counted toward new independent TEST accuracy. Original small-number legacy rehearsal operates on the previously certified range and is not itself a new held-out magnitude population.

New TEST magnitudes are newly withheld for this phase; they are **not guaranteed to have been wholly unseen by the earlier ±16 backbone campaign**. In particular, some could have been in the earlier TRAIN or DEV. This is experimental generalization evidence, not fully independent pretraining-scale proof.

## Balanced numerical curriculum

- For `-32..32`, every residual appears in each 65-example cycle; the default 130-example batch has **two examples for each signed residual**.
- A deterministic 13-band progression covers all residual × magnitude combinations; the second batch half draws difficulty-weighted **TRAIN-only** bands.
- The pretrained ±16 interval receives extra weighting (`--inner-weight 1.5`); exact zero receives higher weighting (`--zero-weight 3`). This does not exclude ±17 through ±32.
- Legacy `[-127,127]^2` arithmetic is rehearsed with the preexisting supervised/distillation loss and fully checked before promotion.
- Backprop updates backbone, layer norm, query, and positional representations. Input encoder and digit/sign heads are frozen. **Neither ground truth nor arithmetic labels are supplied to the model during inference.**

### Run

Use the same Python environment in which the earlier signed-radius16 run succeeded. After applying the ZIP patch (rooted at `main_computer_test/`) to your repository root:

```powershell
& $py -m tools.nanojev_zero_line_signed_radius32 `
    --phase train `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --zero-only-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_zero_only_v1" `
    --r16-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius16_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius32_v1" `
    --device cuda `
    --max-steps 30000 `
    --probe-every 250 `
    --batch-size 130 `
    --legacy-batch-size 128 `
    --legacy-weight 2 `
    --inner-weight 1.5 `
    --zero-weight 3 `
    --lr 0.000025
```

Use `--phase plan` with no source paths to inspect the 845-cell campaign layout. After interruption **before opening TEST**, run the same command with `--resume`; `--max-steps` can be increased on resume without changing the checkpoint lineage. Do not modify other training settings or any source checkpoint after beginning.

### Promotion and error policy

| Requirement | Policy |
|---|---|
| Exact-zero across all magnitude bands | **Zero errors**, including the endpoint stress audit |
| ±1..±32 across all 845 matrix cells | Every cell populated; at most **one error in an individual cell** |
| All nonzero residuals combined in **each DEV view and sealed TEST** | At most **0.1% exact error** (`--max-nonzero-error-rate 0.001`) |
| Stable DEV | **Two independent, disjoint-magnitude DEV views**, both passing for **3 consecutive probes** |
| Previously achieved ±16 | Previously used radius16 DEV matrix **100% exact** and old zero-only DEV/TEST zero/nonzero controls preserved |
| Legacy arithmetic | All **65,025/65,025** cases exactly correct, zero false zeros |
| Fresh sealed TEST | One evaluation only, **20,280 examples** (12 anchors/band), never used to select or train a checkpoint |
| Magnitude +2,000,000 | Separate previously seen boundary stress test; exact-zero must pass; other error rates diagnostic |

The default gate tolerates a tiny **reported** nonzero error rate (in contrast to radius16's absolute perfection gate) but never sacrifices exact zero, historical math, old ±16 DEV competence, or concentrated failures.

### Outputs and safeguards

- `config.json`: exact training settings, code/source SHA-256 hashes and provenance, including the preceding one-error research status.
- `baseline.json`: two radius32 DEV views before training plus old ±16 DEV accuracy.
- `latest.pt`: current model, optimizer, CPU/GPU/Python RNG state, deterministic curriculum cursor and weights.
- `best_dev.pt`: best passing DEV weights **not independently certified**.
- `result.json`: per-band, per-residual accuracy/error counts and example predictions; separate historical, new TEST, and boundary-stress metrics.
- `test_opened.json`: created **before** opening the new sealed TEST; permanently prevents rerunning this campaign after test is opened, successful or not.
- `promoted.pt`: written only if the declared sampled gates all pass, labeled with its parent research lineage.

The runner refuses overwriting existing campaigns, refuses checkpoint/config/code drift on resume, and leaves all source checkpoints unchanged.

### Failure interpretation

One error on a tiny signed-residual sample is not evidence of a fundamental arithmetic failure. One error on **zero**, widespread ±17..±32 failures, reappearance of old ±16 errors, or any historical regression **is** a hard stop. A passing 845-cell sampled test is a research milestone, not a proof for all ~4 trillion ordered operand pairs.
