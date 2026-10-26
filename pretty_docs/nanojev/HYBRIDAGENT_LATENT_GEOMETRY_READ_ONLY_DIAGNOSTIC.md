# HybridAgent latent geometry: read-only diagnosis

## Purpose

Following the corrected tied-marker round trip, the one-step gate produced **6/6 text**, **5/6 zero-latent**, and **1/6 intact-latent** correct answers. This probe measures whether HybridLink's *actual, already-trained* outputs resemble the embeddings Qwen consumes and whether changing only their magnitude improves one-step inference. It does **not** update weights or certify reasoning.

## Install and execute

Install the ZIP with `python new_patch.py <zip> --dry-run` followed by `python new_patch.py <zip>`.

Run the default matched diagnosis (6 held-out cases, 48 new tokens per condition):

```text
python -m tools.nanojev_qwen_hybrid_latent_geometry --device cuda
```

A quick **geometry-only** pass (no token generation, separate destination) is:

```text
python -m tools.nanojev_qwen_hybrid_latent_geometry --device cuda --skip-decode --output C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_latent_geometry_only_v1
```

The tool emits `HYBRID_GEOMETRY_MEASURE`, `HYBRID_GEOMETRY_CASE`, `HYBRID_GEOMETRY_RESULT`, and `HYBRID_GEOMETRY_REPORT`. Full precision measurements and completions appear at:

`C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_latent_geometry_v1\result.json`

Choose a new `--output` directory for a second run; existing outputs are never overwritten.

## Measurements

- Actual Qwen vocabulary input-embedding norm distribution (a deterministic random sample), including the entry/exit input embeddings separately.
- Teacher-forced source hidden-state norm, head **before** and **after** final LayerNorm norm, reference next-token embedding norm, cosine similarity, mean-squared error, and head/reference norm ratios. Exit positions and non-exit positions are reported separately.
- One constant magnitude calibration computed only from **development** teacher pairs. Report geometric alignment separately on **held-out test** teacher pairs. Both types of teacher reference are **measurement-only** and are **never provided to free-running inference**.
- Seven matched inference conditions on each independent test prompt:
  - `text`: ordinary Qwen without marker boundaries.
  - `marker_only`: forced entry and exit markers, no latent vector.
  - `intact`: one original HybridLink vector.
  - `zero`: one all-zero vector.
  - `scale_0p1`: the intact vector multiplied by 0.1.
  - `norm_match`: the intact vector multiplied by a **dev-only** global norm ratio, without changing its direction.
  - `native_argmax`: one actual Qwen token embedding chosen from Qwen's own argmax after the entry marker (no oracle labels).

Each condition preserves the same prompt, model, greedy decoding, and output budget. Results report exact `Answer:` accuracy **separately** from parseability and generation length. These controls do not train the model; no gold/teacher latent is fed into generation.

## Interpretation

| Pattern | Supports | Does not establish |
| --- | --- | --- |
| Head norms differ strongly from raw Qwen token norms; `norm_match` restores performance | Scale or distribution mismatch | Correct latent semantic information |
| Head/reference cosine near zero, even after norm matching | Poor learned direction/alignment | That scaling alone fixes the issue |
| `marker_only` substantially worse than text-only | An entry/exit-boundary problem | A head-specific defect |
| `native_argmax` better than `intact` | Qwen can consume at least one valid embedding more effectively than the current head | Accurate latent reasoning |
| `zero` better than `intact` and `norm_match` | Current learned vector interferes with decoding | Whether the fault is solely scale, direction, or training objective |

The report **always** sets `deeper_training_permitted=false`. It is a diagnostic, not the existing acceptance gate.

## Integrity and restrictions

- Loads the already-trained `format/best.pt` and `align/best.pt` using existing checkpoint restoration and model-provenance rules.
- Requires the **corrected** tied-marker checkpoint-roundtrip gate result and checks its recorded hashes against both saved checkpoint files before and after the run.
- Uses `torch.inference_mode()` for all model forwards, no optimizer, no backward, and freezes all model parameters.
- Writes only to a fresh output directory, never the original repository, checkpoints, or previous gate report.
- If a recorded checkpoint or required control result is missing or mismatched, refuses to run.

The training curriculum and model's mode selection are unchanged. `marker_only`, `native_argmax`, and scale interventions isolate causes; none constitutes an autonomous hybrid-reasoning result.
