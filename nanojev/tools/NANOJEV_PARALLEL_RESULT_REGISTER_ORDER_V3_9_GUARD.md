# NanoJev v3.9.2 — Learn alternating X/Y prompt order

## Correction of the experimental gate

**The user’s hypothesis is whether balanced original/reversed registration training can LEARN label-aligned X/Y extraction. High initial reversed-order numerical error is expected.** The previous v3.9.1 guard incorrectly required reversed-order competence before allowing learning. This replacement relaxes ONLY the accuracy-based gates. It retains paired-order diagnostics and numerical integrity checks.

Reuses the v3.9.1 launcher name and cutover schema, so the existing `parallel_register_order_v3_9_guarded` fork and its real 1,024-row preflight can be resumed directly. On `--resume`, the saved preflight is re-evaluated under `v3.9.2-train-from-high-finite-errors`, and its `safety_gate` is updated in place; **no second GPU preflight and no second fork required**. The source v3.7 lineage remains untouched.

## What remains unchanged

* Original prompts: `XXXXXX = A` then `YYYYYY = B`. Reversed prompts: `YYYYYY = B` then `XXXXXX = A`. Values and X/Y targets are never swapped.
* Only INTERMEDIATE X/Y updates receive a deterministic 50/50 original/reversed replay-feature recapture.
* Shared replay, RESULT, and FINAL updates still use original-order captures. The underlying regression losses, optimizers, 16:3:1 (80/15/5) phase schedule, frozen backbone and checkpoint mechanics remain unchanged.
* Every 100 steps by default, the exact same benchmark pairs are evaluated in original and reversed registration order, with label- and position-aligned metrics and deterministic post-model averaging. Checkpoint selection continues on original-order validation.
* High original-order regression, no reversed-order accuracy, and 10-billion-scale but finite relative MSE are recorded as diagnostics; they do not prevent optimizer updates or stop training.

## Remaining numerical safeguards

* The stored preflight must contain valid **finite** original and reversed X/Y losses, within-5% fractions and operation-probe accuracies.
* The existing v3.7 `safe_clip_gradients()` computes a float64 norm, clips trainable head gradients to norm 1, and raises on non-finite gradients. Its signed-asinh loss also checks for non-finite numerical terms.
* Non-finite original/reversed validation MSE still aborts with `ALTREG_NONFINITE_VALIDATION`.
* This patch does **not** guarantee stable or successful learning. Reversed activation magnitudes can produce large losses and poor original-order performance. The aim is to measure their training trajectory without defining low initial accuracy as a fatal condition.

The report fields `x_mse` and `y_mse` are **relative squared numerical errors**, not signed-asinh MSE. The original v3.7 field names `x_asinh_mse` and `y_asinh_mse` are misleading due to overwritten metrics; this patch leaves the inherited file untouched.

## How to resume the actual checked fork

Extract the ZIP into `C:\Users\subsi\main_computer` using the normal `main_computer_test/` patch workflow. In PowerShell, from `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_parallel_result_register_order_v3_9.py --self-test

# The v3.9.1 CUDA preflight already exists and will be REUSED.
& $py .\tools\nanojev_parallel_result_register_order_v3_9.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_register_order_v3_9_guarded" `
  --device cuda `
  --dtype float16 `
  --batch-size 16 `
  --replay-updates 2 `
  --resume `
  --additional-steps 5000 `
  --eval-every 100 `
  --report-every 50
```

The output should contain `altreg_learning_preflight_accepted`, listing the large reversed preflight error as a **warning**, then `asinh_resumed` at **step 23,800** and subsequent training progress. With `--additional-steps 5000`, the target is step **28,800**.

To inspect the upgraded preflight:

```powershell
(Get-Content "$env:USERPROFILE\NanoJev\runs\parallel_register_order_v3_9_guarded\paired-order-preflight.json" -Raw | ConvertFrom-Json).safety_gate
```

The output should say `eligible: True` with warnings; this says only that the measured data are finite and structurally valid, **not** that reversed predictions are accurate.

## Verification

37 local v3.9 focused tests passed. Combined with the unchanged v3.7 parent-chain regression test, 46 tests passed after applying an **in-test-only** fix to a pre-existing duplicate `--additional-steps` parser option. That unrelated test-harness edit is **not** part of the deliverable. Mock/CPU validation is not a real CUDA training result.
