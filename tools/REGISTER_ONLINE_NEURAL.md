# NanoJev XX registered-number online neural decoder

A standalone experiment. Does not modify Qwen, NanoJev's champion, game, Hub,
mainnet, or existing LASSO paths. Requires the existing `tools/` modules
`nanojev_register_stability_v3_generate.py` and
`nanojev_numerate_spine_cassette_smoke.py`, PyTorch, transformers, numpy,
and the Qwen3-0.6B weights already used by the v3 capture generator.

## Design

- **Frozen input model**: Qwen3-0.6B, same pinned revision and dtype as v3.
- **Registration**: `XXXXXX` / `YYYYYY`, paired decimal + Log-Bar syntax.
- **Problems**: new ordered integer pairs drawn across ±2,000,000; 20 training
  instruction variants from five mathematical families. The four withheld
  operation variants are never included in online training.
- **Leakage safeguard**: reject all 2,688 ordered pairs belonging to the fixed
  v3 benchmark. Individual operand *values* can recur, so the benchmark tests
  unseen **pairs**, not unseen isolated operand values.
- **Readout**: four final *real* prompt tokens of frozen hidden states 16, 28,
  unpooled, flattened in layer-token-coordinate order (8,192 floats).
- **Decoder**: LayerNorm (no learned affine parameters),
  `8192 → 32 → GELU → 32 → GELU → 2`: 263,298 trainable parameters.
  `--width 16` gives 131,394 parameters.
- **Numerical objective**: SmoothL1 on `asinh(n)/asinh(2,000,000)`, two outputs.
  Inference inverts `asinh`, rounds to integer, scores 5% relative error and
  exact zero. The decoder has *no separate routers, sign/zero classifiers,* or
  operation head.
- **Streaming training**: fresh prompts → batched frozen Qwen forward → 2 small
  neural updates (`--replay-updates`) → discard activations; no new activation
  archive or 16,032-row training limit.
- **Benchmark**: fixed v3 validation records during training. Test + full 24-
  operation control are evaluated *only* on explicit `--eval-only`.
- **Isolation**: Qwen gradients disabled; checkpoints written only to the new
  results root. No service integration and no mutation of v3 captures.

The small head's gradient updates should be inexpensive. End-to-end sample
throughput is primarily bounded by frozen Qwen forward passes and tokenization.
Progress events report `frozen_qwen_seconds` and `decoder_update_seconds`
independently; no throughput or accuracy is assumed before GPU execution.

## Installation

Extract the ZIP preserving its `main_computer_test/` paths so that the new
script ends up at `C:\Users\subsi\main_computer\tools\nanojev_register_online_neural.py`.
Do not delete or change the v3 `records/` directory.

## Offline smoke

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_register_online_neural.py --self-test
```

## Pilot: 100 steps, 16 freshly generated prompts per step

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$run = "$env:USERPROFILE\NanoJev\runs\register_online_neural_v1"
$bench = "$env:USERPROFILE\NanoJev\runs\register_stability_v3"
& $py .\tools\nanojev_register_online_neural.py `
  --v3-root $bench `
  --results-root $run `
  --steps 100 `
  --batch-size 16 `
  --width 32 `
  --replay-updates 2 `
  --lr 0.0003 `
  --val-examples 512 `
  --eval-every 25 `
  --checkpoint-every 25 `
  --report-every 10 `
  --device cuda `
  --dtype float16 `
  --threads 2
```

At startup, `neural_capture_alignment` compares live padded-batch Qwen output
against saved unpadded v3 captures. It refuses to train if their relative RMS
error exceeds 0.06. The 100-step pilot provides 1,600 fresh ordered pairs and
200 neural gradient updates. It is not expected to establish final accuracy.

## Continue online training, without restarting

Use exactly the same hyperparameters, result root and benchmark path, but
increase `--steps` and append `--resume`. For example, use the command above
with `--steps 2000 --resume`: this takes the total to 2,000 steps, generating
additional fresh pairs. The `latest.pt` checkpoint restores optimizer and
pseudo-random generator state, including previously drawn pairs.

Run with `--width 16` and a different `--results-root` to compare a 131k-
parameter model. The training configuration and original backbone identity
are validated on resume; mismatches raise an error instead of silently
mixing experiments.

## Unseen-pair and operation-transfer evaluation

After training, repeat the same flags/configuration and add `--eval-only`.
This loads `best.pt` and the saved v3 activations. It **never loads Qwen**.
It scores the full validation, test, and all-24-operation control splits,
with separate per-operation and per-family recovery and zero recall.

Outputs in `--results-root`:

- `run_config.json`: sealed decoder/training/holdout configuration
- `latest.pt`: resumable model, optimizer, and pair-generator state
- `best.pt`: highest validation-joint-recovery checkpoint
- JSONL-like JSON event lines printed to the console: train loss, pair-rate,
  validation recovery, per-operation success, checkpoint provenance

No claims of numerical generalization or writable register vectors are made
by this code; both require separate empirical and causal tests.
