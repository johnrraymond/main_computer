# NanoJev v3.9 — corrected alternating register-order experiment

## Defect corrected

The v3.8 launcher monkeypatched `FreshPairs.batch` globally. Every new row could arrive in reversed order, and those altered frozen-Qwen activations were written to the SAME shared replay buffer consumed by INTERMEDIATE, RESULT, and FINAL. This contaminated downstream training even though RESULT/FINAL still used fixed-order operation and source components. The v3.8 training validation also never evaluated reversed-order inputs.

**Do not resume the damaged v3.8 run.** The v3.9 launcher forks a clean unmodified v3.7 checkpoint into a brand-new root. The old v3.7 and v3.8 files are never written.

## New intervention

For each iteration, the original v3.7 fresh-stream example and its original frozen-Qwen features are written to replay unchanged. The original v3.7 phase cycle (16 intermediate, 3 result, 1 final per 20 ticks) is unchanged.

Only during an INTERMEDIATE optimizer update, a copy of the sampled replay minibatch is made. At positions 1, 3, 5, ... its features are re-extracted using a reversed-order prompt:

```text
YYYYYY = <original B numeric representation>
XXXXXX = <original A numeric representation>
operation: <unchanged instruction>
Answer:
```

Positions 0, 2, 4, ... retain their original cached features and prompts. **The replay minibatch, its X/Y targets, the original operation, frozen-source values, and the shared replay buffer remain unchanged.** Each INTERMEDIATE update sees the balanced two-order inputs, while RESULT/FINAL only sample original-order features. This is the narrow X/Y order experiment; it does not modify the frozen operation classifier or retrain the frozen backbone.

The procedure adds inference-time frozen-Qwen recaptures on half the INTERMEDIATE minibatch; expect greater compute time than v3.7. The masks are determined by minibatch index, avoiding additional RNG or resume state.

## Paired evaluation

Before any training, the mandatory preflight uses the **same 64 validation problems** with original and reversed prompts on the frozen Qwen and on the forked learned decoder, **without any gradient or optimizer update**. It records:

* Original decoder X and Y relative squared error and within-5% accuracy.
* Reversed-order decoder X and Y errors against the **original** labels.
* Reversed decoder errors against *swapped* targets, to detect positional tracking.
* Deterministic arithmetic averages of label-aligned original and reversed X/Y numerical predictions, evaluated against original labels. Averaging is a diagnostic, never a training objective.
* Operation-probe accuracies for original versus reversed prompts, plus the frozen original-source decoder's reversed-order error.

The baseline is written to `paired-order-preflight.json` and validated before training starts. The first 64 samples are selected deterministically by the inherited fixed validation loader. Preflight checks the Qwen version and existing v3 capture alignment. The run will refuse to start without the baseline.

At every existing `--eval-every` interval, the original fixed-order v3.7 validation and best-checkpoint criteria remain unchanged; the report gains an `intermediaries.paired_order` record for the *same held-out pairs*, and a `altreg_paired_validation` log event. No extra learned head is added.

The inherited v3.7 `x_asinh_mse`/`y_asinh_mse` statistics are actually computed as **relative squared numerical error** by its existing operand-metrics implementation. v3.9 keeps this definition to ensure comparisons remain consistent; the standalone paired metrics are named `x_mse`/`y_mse` and use the identical `(pred - truth)^2 / max(abs(truth),1)^2` transformation.

## Installation

Unzip `nanojev_parallel_result_register_order_v3_9_fix.zip` into the main repo. The zip paths begin `main_computer_test/`. Place the files from `main_computer_test/tools/` and `main_computer_test/tests/` into the corresponding repo folders if using an extraction tool that does not map the staging prefix automatically. The existing v3.7 file must remain installed and unmodified. Python requirements are the same as v3.7.

Set the NanoJev interpreter:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
```

Offline self-test:

```powershell
& $py .\tools\nanojev_parallel_result_register_order_v3_9.py --self-test
& $py -m pytest -q .\tests\test_nanojev_parallel_result_register_order_v3_9.py
```

### Step 1 — fork and perform paired preflight (no optimizer updates)

```powershell
& $py .\tools\nanojev_parallel_result_register_order_v3_9.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --fork-v3-7-root "$env:USERPROFILE\NanoJev\runs\parallel_result_asinh_squared_v3_7" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_register_order_v3_9" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --preflight-only
```

After it completes inspect:

```powershell
Get-Content "$env:USERPROFILE\NanoJev\runs\parallel_register_order_v3_9\paired-order-preflight.json" -Raw
```

If preflight fails after the fork is created, **do not re-fork into the same directory**. Fix the cause and rerun the same command without `--fork-v3-7-root`, keeping `--results-root` and `--preflight-only`.

### Step 2 — short gated training probe

```powershell
& $py .\tools\nanojev_parallel_result_register_order_v3_9.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --source-checkpoint "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_buffered_v1\best.pt" `
  --results-root "$env:USERPROFILE\NanoJev\runs\parallel_register_order_v3_9" `
  --device cuda --dtype float16 --batch-size 16 --replay-updates 2 `
  --resume --additional-steps 500 --eval-every 100 --report-every 50
```

This must produce `asinh_validation`, `altreg_paired_validation`, and immutable v3.7 parent checkpoints. Inspect the paired logs and compare the original fixed-order and reversed-order within-5% accuracy before extending the run. Later continuation: repeat this command, replacing `--additional-steps 500` with the desired additional number.

## Tests already run for the patch

* 32 focused local unit/invariant tests, including all 24 instruction variants, preserved replay, strict labels, fork safety, paired comparison, and gated preflight.
* 9 upstream v3.7 compatibility and full mocked-lineage integration tests including v3.9 fork → preflight → training → resume → paired evaluation. The test harness uses a mock Qwen because live checkpoint files and CUDA are on the user's workstation.
* Real CUDA/Qwen runtime and the user's actual checkpoint have **not** been executed in this environment.

