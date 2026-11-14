# Qwen Worded Scale Transfer: Answer-Prefix Protocol Diagnostic

## Why

The first 112-case transfer smoke scored 9/112 for both frozen Qwen and refined glass,
but in many displayed failures the generated text began with a new `PROBLEM:` line.
That score mixes output-packet induction with numerical competence. A trained
completer can continue the dataset with another `PROBLEM:` rather than answer it.

## What changes

This is a **new, read-only sidecar**. It does not overwrite the original 112-case
smoke, any baseline checkpoint, either glass, or the live radius ladder.

The input is the original source text plus an invariant `\nANSWER:` suffix.
The text after `ANSWER:` must be generated greedily by the frozen Qwen model;
no expected answer, gold token, label or per-case reference enters inference.
The refined glass (when requested) remains a frozen LM-head input hook.

The prefix decoder must not use `ladder._greedy` unmodified: that helper always
appends `\n` to each source. Appending a newline *after* `ANSWER:` would undo the
answer-prefix conditioning.

## Command (same `$glassArgs`)

```powershell
& $py -m tools.nanojev_qwen_scale_transfer_answer_prefix_probe `
    --stage run @glassArgs --glass-mode refined
```

Outputs are written to a separate `qwen_worded_order_transfer_answer_prefix_probe_v1`
directory under `~/NanoJev/runs/`. This compares fresh baseline and refined-glass
answer-prefilled runs, optionally reporting the difference from the original raw
smoke when its deck and baseline hashes match. The unprefixed test is not changed.

Use `--prefix-space` if you want to try conditioning with `ANSWER: ` instead of
`ANSWER:`. For that alternative, use another `--probe-output` directory.

The report includes strict packet correctness, normalized numerical correctness,
corrected/regressed IDs, and missing-space-only formatting errors. No training
occurs; no claims about worded-number competence are justified until the real
prefilled outputs are measured.
