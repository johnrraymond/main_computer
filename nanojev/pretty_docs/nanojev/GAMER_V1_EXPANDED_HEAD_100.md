# GAMER V1 — 100-vector normalized-exponential expanded C0 Wide

## Contract

This is an **isolated experiment**. Existing LoRA, numerical checkpoints, cached features, and the certified dataset are read-only. The exact frozen LoRA snapshot from the most recent certified C0 head cycle is loaded, with source fingerprints verified. No LoRA updates occur.

The original C0 Wide architecture (LayerNorm, three Linear/GELU layers, a Linear/GELU bottleneck, corrected + auxiliary scalar heads) is **not replaced**. Only its dimensions change:

| Component | Old | New |
| --- | ---: | ---: |
| Model input | 1 × 1,024 | 100 × 1,024, concatenated to 102,400 |
| First hidden layer | 1,024 | 4,096 |
| Second hidden layer | 512 | 2,048 |
| Third hidden layer | 128 | 512 |
| Bottleneck | 32 | 128 |
| Corrected/auxiliary scalar output | 1 / 1 | 1 / 1 |

The first Linear weight matrix is exactly 102,400 × 4,096 = **419,430,400 weights**. It is fully dense; not approximated by low-rank, shared-weight, attention, pooling or independent encoders.

## Input sampling

At the final Qwen/LoRA transformer layer, sample **100 contextual vectors** with deterministic, normalized exponential positions over the actual nonpadding length of each prompt. Both first and last positions are included. Sampling is coarse at the beginning and dense near the end. If there are 100 or more tokens, every sampled position is distinct. For fewer than 100 tokens, every token appears at least once, and remaining positions repeat existing tokens. No zero-fill or masking. Samples are concatenated in chronological order. This is agnostic to numeric notation: no regex, number-token detection, hardcoded positional register labels or oracle metadata enter the feature input.

For long prompts, the Qwen forward hook retains only its final normalization output and restricts computed logits to the final token. Intermediate transformer hidden-state tensors are **not** requested/stored. Tokenizer padding, last-nonpadding-token selection, and no-truncation checks match the certified C0 capture contract. Capture compares each new final sampled vector with the original certified 1,024-dimensional cache, refusing a deviation greater than 0.1.

## Run

From the Main Computer repository root, with the same Python interpreter used for Qwen/LoRA:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head-100 `
  --run runtime\reports\gamer-v1\transition-005 `
  --steps 2000 `
  --device cuda
```

This creates an isolated sibling such as `transition-005-expanded-head-100-0001`. The adapter is frozen and the expanded head begins as newly initialized weights. It does **not** attempt incompatible transfer of the old 1,024-wide input layer. Existing `head`, `lora`, `flat-registers`, and all prior artifacts are unchanged.

Optional controls:

```powershell
  --expanded-curvature 4.0 `
  --expanded-batch 4 `
  --expanded-capture-batch 2 `
  --expanded-backend offload-cuda `
  --expanded-eval-every 100 `
  --expanded-save-every 100 `
  --expanded-zero-focus 0.5 `
  --expanded-zero-radius 1.0
```

To permit longer prompts than the source's original `max_length` (without silent truncation), pass `--expanded-max-length 32768` if supported by your Qwen installation and hardware. This does not force every prompt to that length.

For a controlled **small smoke test** only, `--expanded-hidden 8,6,4,2 --expanded-backend cpu --device cpu --steps 2` reduces dimensions. **Do not use reduced widths to assess production capacity.**

### Memory and optimizer

The default `offload-cuda` backend uses the full dense C0 Wide on CUDA in FP16, with a full-precision FP32 CPU master and CPU AdamW optimizer state. Each optimizer step copies gradients to CPU, applies the original AdamW rule to all parameters, and refreshes GPU weights. This avoids storing the largest FP32 AdamW moments on the GPU at the expense of **substantial CPU RAM, host-device traffic and speed**. Budget roughly 1.7 GB CPU weights, 1.7 GB CPU gradients while updating, 3.4 GB CPU AdamW moments, plus cached features and overhead; disk checkpoints can be several GB. CUDA still needs room for its ~0.86 GB FP16 model and gradients, plus temporary buffers. Exact memory depends on batch size and runtime.

If you have adequate GPU memory, `--expanded-backend full-cuda` uses ordinary FP32 CUDA AdamW (requires much more VRAM). `--expanded-backend cpu` executes the original FP32 CPU network directly.

No default 6 GB GPU allocation is guaranteed to work on every Windows installation; initial capture and the numerical training model do not occupy GPU memory simultaneously. **An actual CUDA stress test has not been performed in this development environment.**

### Resume

Interrupted training resumes only from an explicitly supplied existing isolated run, with the same source fingerprints and configuration and a higher cumulative target:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase head-100 `
  --run runtime\reports\gamer-v1\transition-005 `
  --expanded-output runtime\reports\gamer-v1\transition-005-expanded-head-100-0001 `
  --expanded-resume `
  --steps 4000 `
  --device cuda
```

`latest.pt` contains model weights, optimizer state, RNG, and exact provenance. `best_model.pt` holds the lowest-dev-MAE checkpoint, `feature_cache/{train,dev,test}.npz` hold captured samples, `capture_report.json` records cache parity, `dev_evaluations/step_*.csv` records each held-out dev example per evaluation, and `dev_best.csv` / `test_best.csv` record per-example predictions from the selected best checkpoint. `final_report.json` compares dev/test MAE, signs, exact zero, zero-line cohorts, and the old C0 head's saved dev MAE when available. No automatic promotion occurs.

Resume from the *last saved step*, not the interrupted partial step. All output paths are required to be outside the source run. Cached features are keyed by prompt corpus, adapter, source checkpoint, sample policy and split. A source change fails closed.

## Tests

```powershell
& $py -m pytest -q tests/test_nanojev_gamer_v1_expanded_head.py
```

The tests cover deterministic sampling from 1 to 32,768 positions, unique coverage, replication, original C0 network geometry, final-norm capture with source-cache parity, numeric training and optimizer restore, isolated run/resume, and source-file immutability. Actual GPU memory feasibility and final development accuracy require a live run.
