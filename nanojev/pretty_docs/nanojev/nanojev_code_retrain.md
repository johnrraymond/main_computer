# Frozen-backbone NanoJev code retraining

This experiment keeps the pretrained Qwen backbone immutable for the entire run and trains only the fresh NanoJev decision head.

## Why this is a new experiment

Do not resume `main_computer_code_retrain_v1`. That run already executed full-backbone optimizer steps, so its Qwen weights are no longer the pristine pretrained base.

Initialize a new experiment from the pinned Qwen revision instead.

## 1. Initialize exactly once

```powershell
cd C:\Users\subsi\main_computer
.\tools\init_nanojev_code_training.ps1 `
  -Model "Qwen/Qwen3-0.6B" `
  -Revision "c1899de289a04d12100db370d81485cdf75e47ca"
```

The default experiment directory is now:

```text
C:\Users\subsi\NanoJev\runs\main_computer_code_frozen_v1
```

The initializer creates a clean Qwen3-0.6B backbone, a fresh NanoJev decision head, deterministic train/dev/test manifests, and fixed dev/test probes. It records `backbone_frozen=true` and `phase=frozen_head`. It never loads the game-trained NanoJev checkpoint and it never trains.

## 2. Train/restart

```powershell
cd C:\Users\subsi\main_computer
.\tools\run_nanojev_code_training.ps1
```

Every cycle:

```text
small deterministic code shard
    -> train NanoJev head only for ~75 seconds
    -> evaluate fixed dev probe
    -> print train/dev NLL + top-1 error
    -> save head + optimizer + RNG
    -> atomically advance training_state.json
```

The Qwen backbone is always `requires_grad=False` and is kept in eval mode while the NanoJev head trains. The head is the only optimizer parameter group.

Each step must report:

```text
phase = frozen_head
body_grad_norm = 0.0
head_grad_norm > 0 (normally)
```

Each completed cycle must report:

```text
backbone_probe_changed = false
head_probe_changed = true
```

The trainer aborts if the frozen backbone produces gradients or if the backbone probe changes.

## Restart contract

Cycle generations store only what can change:

```text
checkpoints\generations\cycle-XXXXXX\
    head.safetensors
    optimizer.pt
    rng_state.pt
    config.json
    meta.json
```

On process restart, the trainer reloads the exact pinned pretrained Qwen revision and attaches the saved NanoJev head. This both reduces checkpoint cost and prevents a modified backbone from leaking into a restart.

When a cycle beats the current dev NLL, `checkpoints\best\best.safetensors` is materialized as a normal full NanoJev-compatible model package for later inference.

## Baseline bookkeeping

The initial dev evaluation is seeded as `best_dev_nll` with `best_cycle=0`. A trained cycle is marked `improved_best=true` only if it actually beats the clean initialized model.


## Per-cycle seen/available guard

The trainer stops the training portion of a cycle before starting any batch that would make `train_questions_seen` exceed `train_questions_available`. The wall-clock budget remains an upper bound; the available-question count may end the cycle sooner. This guard does not otherwise change the sampling policy.
