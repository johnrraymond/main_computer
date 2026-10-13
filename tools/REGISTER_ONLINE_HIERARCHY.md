# NanoJev: standalone online stage-5 numerical head (38 outputs)

This is an **additive experiment**. It does not modify Qwen, the game, NanoJev's champion, Hub, or any service. It does not change the existing 23,552 prompt captures or the earlier two-output neural run.

## Prerequisites

Your repository must already contain:

- `tools/nanojev_register_online_neural.py` **with the CUDA RNG resume fix**;
- `tools/nanojev_register_stability_v3_generate.py`;
- `tools/nanojev_numerate_spine_cassette_smoke.py`;
- completed `NanoJev/runs/register_stability_v3` captures (layers 16 and 28), plus the Python environment used by the prior online neural trainer.

All these were used in the user's previous run. This package adds one new script and new unit tests; it intentionally **does not replace** those working dependencies.

## Architecture

- Frozen Qwen3-0.6B supplies **all 8,192 coordinates**: 2 layers × 4 last-token positions × 1,024 dimensions.
- Per-example, non-trainable LayerNorm followed by a **single independent linear layer** with 38 outputs. Trainable parameters: **311,334**.
- For each register separately: zero classifier (1), sign classifier (1), five magnitude-threshold classifiers (cuts 20/200/2,000/20,000/200,000), and twelve signed magnitude experts (six magnitude bands × two signs).
- Each classifier has independent BCE supervision. Each regression expert has a local [-1, 1] target and receives gradients **only** from examples in its ground-truth signed magnitude band. Per-active-head loss averaging prevents common classes from dominating rare experts.
- All operation prompts follow the original paired decimal + Log-Bar `XXXXXX/YYYYYY` syntax.
- The balanced stream cycles through exact zero and all 12 signed numerical regimes. Training pairs are newly generated, do not duplicate any benchmark **ordered pairs**, and exclude the four reserved operation variants. Individual operands can recur across benchmark and training; test therefore establishes unseen *pairs*, not guaranteed unseen *individual numbers*.
- Inference predicts zero, sign, magnitude regime, then the matching expert. **No true labels are used for inference routing.** An oracle-routed diagnostic is reported separately to isolate expert errors from routing errors.
- Existing v3 validation, test, and full-operation control captures remain fixed and untouched.

## Install

Extract the ZIP rooted at `main_computer_test/` to your repository root, so the new file lands at `C:\Users\subsi\main_computer\tools\nanojev_register_online_hierarchy.py`.

## Offline self-test

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_register_online_hierarchy.py --self-test
```

## CUDA pilot: 500 steps

```powershell
& $py .\tools\nanojev_register_online_hierarchy.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_v1" `
  --steps 500 `
  --batch-size 16 `
  --replay-updates 2 `
  --lr 0.0003 `
  --eval-every 100 `
  --checkpoint-every 100 `
  --report-every 50 `
  --device cuda `
  --dtype float16 `
  --threads 2
```

Use a new, empty `results-root` — do not point at `register_online_neural_v1`.

## Resume to 5,000 steps

Use **identical architecture, sampling, and optimizer arguments** and add `--resume`:

```powershell
& $py .\tools\nanojev_register_online_hierarchy.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_v1" `
  --steps 5000 `
  --batch-size 16 `
  --replay-updates 2 `
  --lr 0.0003 `
  --eval-every 100 `
  --checkpoint-every 100 `
  --report-every 50 `
  --device cuda `
  --dtype float16 `
  --threads 2 `
  --resume
```

`--steps` is a **total** step count. This example continues from step 500, not another 5,000 steps. The checkpoint contains the model, optimizer, CPU/CUDA RNG state, and fresh-pair generator state. Only checkpoints from this 38-head architecture can resume this experiment; the previous two-output checkpoint cannot be used.

## Evaluate the selected checkpoint, without Qwen inference

```powershell
& $py .\tools\nanojev_register_online_hierarchy.py `
  --v3-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_online_hierarchy_v1" `
  --batch-size 16 `
  --eval-only `
  --device cuda `
  --dtype float16 `
  --threads 2
```

If you change other config-sealed training args, replicate them in this command. It loads `best.pt` and scores validation, unseen-pair test, and all 24 control-operation variants. The train loop uses validation only; test and control are not touched until `--eval-only`.

## Events and interpretation

- `hierarchy_decoder_ready`: parameter count, held-out operations, frozen-backbone status.
- `hierarchy_train_progress`: mean per-head training loss, active head coverage, generated pairs/second, frozen Qwen seconds, head-update seconds.
- `hierarchy_validation`: **joint exact-zero / 5%-relative-error recovery**, per-register recovery, worst/family/operation results, zero precision/recall, sign and regime accuracy, **38 per-head scores**, oracle-routed joint recovery, and a continuous head validation loss.
- `hierarchy_best_checkpoint`: selects higher joint 5%-pass rate; ties break on lower head validation loss, preventing best checkpoint from freezing at step 1 when joint accuracy is initially zero.
- `hierarchy_checkpoint`: atomic `latest.pt` for resume. `best.pt` is separate.
- `hierarchy_final_evaluation`: validation, test, and all 24 control operations, using only already captured activations.

The oracle-routing metric uses true zero/sign/regime **only for diagnosis**. The actual accuracy metric never does. If oracle-routing recovery rises while regular recovery stays low, improve classification routing; if both remain low, improve magnitude experts or the captured readout.

## Verification and scope

The package's 14 offline tests passed using CPU PyTorch. Tests include 38-output shape, exact stage-5 boundaries, forward/inverse magnitude normalization, expert-specific gradient masks, benchmark exclusion, deterministic fresh-stream resume, checkpoint restore, per-head metrics, synthetic loss reduction, and a simulated train → resume → evaluation run. This is **not** a claim of actual numerical recovery on your Qwen captures or of successful full CUDA training; both require the user's run.
