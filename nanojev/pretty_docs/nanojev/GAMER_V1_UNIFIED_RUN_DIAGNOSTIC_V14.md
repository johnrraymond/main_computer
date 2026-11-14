# Gamer v1 — unified run diagnostic (v14)

`tools.nanojev_gamer_v1_diagnostic` is the permanent **read-only** diagnostic for the current Gamer LoRA training run, including the 146 named-game, four-table curriculum. It does not generate cases, train, launch Qwen, call CUDA, perform new model evaluations, alter a checkpoint, or require the running process to stop. It runs in a separate shell with the Python environment used by Gamer.

## One command

Run from the Main Computer repository root:

```powershell
& $py -m tools.nanojev_gamer_v1_diagnostic --run runtime\reports\gamer-v1\transition-005
```

To continuously inspect the same run, still without modifying it:

```powershell
& $py -m tools.nanojev_gamer_v1_diagnostic --run runtime\reports\gamer-v1\transition-005 --watch 60
```

Stop the watch with Ctrl+C. For structured machine-readable output use `--json`. If the run's certified dataset was stored elsewhere via `--dataset`, pass that same path to this diagnostic with `--dataset PATH`.

## Checks and interpretation

The unified report includes:

- **Run and lineage:** manifest SHA versus the certified JSONL bytes, named game counts, native/catalog/transition categories, and table-family totals.
- **Latest resumable LoRA checkpoint:** actual cumulative optimizer step; transfer source and source step; pending evaluation; last microbatch loss; checkpoint freshness; best-anchor step; sampling counts; and historical evaluation-loss snapshots retained in the checkpoint.
- **Saved evaluation progress:** every `lora/eval_step_*.json` evaluation, including partial resumable evaluations. It verifies that the saved evaluation-deck hash matches the deck reconstructed from the exact development records and saved evaluation scheduling parameters *before* assigning per-game or per-table scores.
- **Accuracy breakdown:** native 33-game group, bounded catalog 58-game group, bounded transition 55-game group, each encountered table family, and individual mistakes in the latest *measured* deck.
- **Comparable history:** the six fixed anchor cases across evaluations, excluding rotating cases when comparing steps. It flags a decline on those matched cases, low measured exact accuracy, and the distinction between the `best_adapter` selected by the fixed anchor and the `latest.pt` actual optimizer weights.
- **Limits:** no full retention or generalization claim from a 17-case probe; no claim that a checkpoint's timestamp establishes whether the training process is alive. Training loss is **not** generative exact-match accuracy.

Example: a step-800 diagnostic with 2 correct of 17 development cases is **11.8% on that bounded deck**, not 2/438, not a full retention score, and not an automatic conclusion that training will not learn the new objectives. The next evaluation's rotating questions should not be treated as a matched test set; the fixed anchors *are* comparable.

The diagnostic uses only CPU-side checkpoint deserialization with PyTorch `weights_only=True`; it never loads a GPU model. On Windows it opens the checkpoint with read/write/delete sharing so it cannot hold up the trainer's atomic `os.replace`. If a snapshot is temporarily unreadable, it reports the problem and still examines the independent JSON evaluation files. Re-run the command when the checkpoint becomes readable. The diagnostic does **not** write a report into the active run.

The report may be limited when an old run lacks the exact training dataset or stored hyperparameters. In that situation, it refuses to assign the saved booleans to specific game names or table families without verifying the evaluation deck. The original source-defined certified objectives remain distinct from full-game strategic competence.

## Scope of the diagnostics

The diagnostic reports **already measured evidence**, not new tests. Full development and test-set evaluation, original-prompt C0 `to_numerical` verification, retention certification, and deployment/production promotion remain separate explicit gates. Do not promote an experimental LoRA solely because a bounded probe has improved.
