# NanoJev binary next-lexeme training

This experiment replaces multi-candidate next-token ranking with a simpler binary recognition task while keeping Qwen3-0.6B permanently frozen.

For each sampled source boundary, the generator emits two questions with the exact same code prefix:

```text
actual next lexeme      -> TRUE
wrong corpus lexeme     -> FALSE
```

A lexeme may be a multi-character lexical unit: identifier, keyword, number literal, string literal, regex literal (JavaScript), operator, or delimiter. Ground truth comes from Python/JavaScript lexical analysis rather than Qwen tokenizer tokens.

False candidates come from a persistent train-only `super_suffix.json`. Sampling prefers the same lexical class, so an identifier is normally replaced by another identifier, an operator by another operator, and so on. This prevents the head from solving the task merely by detecting the broad token class.

Each selected boundary therefore creates a balanced matched pair. With the default `--train-pairs-per-cycle 128`, each cycle contains 256 available Boolean questions.

## Important

Do not resume `main_computer_code_frozen_v1`. That head learned the old multi-choice objective. Initialize this as a new experiment so the head starts clean.

## Initialize

```powershell
cd C:\Users\subsi\main_computer
.\tools\init_nanojev_code_training.ps1
```

The default experiment directory is:

```text
C:\Users\subsi\NanoJev\runs\main_computer_code_lexeme_v1
```

Initialization creates deterministic train/dev/test file manifests, a train-derived super-suffix pool, balanced fixed dev/test lexeme probes, and a fresh NanoJev attention head on the pinned Qwen3-0.6B backbone.

## Train or restart

```powershell
.\tools\run_nanojev_code_training.ps1
```

The Qwen backbone remains frozen. Each cycle reports ordinary loss/accuracy plus binary diagnostics:

```text
dev_positive_accuracy
dev_negative_accuracy
dev_balanced_accuracy
dev_auc
dev_mean_p_true_on_true_suffix
dev_mean_p_true_on_false_suffix
dev_probability_separation
```

The most useful early signal is `dev_probability_separation`: it should become positive and grow as the head learns to give higher TRUE probability to the real lexeme than to matched false lexemes.

Frozen-backbone invariants remain mandatory:

```text
body_grad_norm = 0.0
backbone_probe_changed = false
head_probe_changed = true
```

## Lexeme length is not character length

The default `max_lexeme_tokens=48` is only an input-budget guard. It does **not** reduce the task to one-character symbols. Multi-character and multi-Qwen-token lexemes remain valid targets. Extremely large string/template literals are skipped so the complete NanoJev Boolean question remains within the configured 384-token path limit.

## Best-checkpoint selection

The trained head is a discriminator first. Best-checkpoint selection therefore uses this strict ordering:

```text
1. maximize dev_auc
2. maximize dev_probability_separation
3. minimize dev_mean_nll
```

The fixed 0.5 TRUE/FALSE threshold is not used to select the best model because the head may learn useful ranking before it becomes probability-calibrated.

The trainer migrates older lexeme experiments automatically. On restart it reads `baseline_dev.json` and `history.jsonl`, reconstructs the strongest historical discriminator, updates `training_state.json`, and rematerializes `checkpoints/best` from that retained generation when available. Reinitialization is not required.

Rolling checkpoint cleanup always preserves the selected best cycle in addition to the newest `--keep-generations` generations. This prevents a strong discriminator from being deleted merely because a newer cycle exists.
