# NanoJev — Register Stability v3 dataset generator and capture

This is an **additive** script. It does not modify the running two-number or register-screen experiments. Install under `C:\Users\subsi\main_computer\tools\`.

## Objective

Test how reliably the numbers registered as `XXXXXX` and `YYYYYY` can later be extracted with a **newly fitted, shared sparse decoder** under mixed mathematical instructions. This stage **generates and captures** the input data. It does **not yet implement the v3 LASSO analyzer or prove arithmetic execution**. The old `nanojev_register_template_screen.py --analyze-only` does not recognize the new v3 manifest and must not be directed at it.

The decimal/Log-Bar registration stays identical across operations. No coefficients or selected `x_i` from a prior regression are imported. The `analysis_defaults.json` file records the *procedure* we intend to reuse: paired, `alpha_ratio=0.002`, stage 5, layers 16/28, four-token suffix, 10,000 solver iterations with a ×3 retry and 12 KKT rounds.

## Fixed budget

| Dataset | Ordered numerical pairs | Operations per pair | Captured prompts |
|---|---:|---:|---:|
| Main mixed-operation | 2,560 | 8 selected from 20 eligible variants | 20,480 |
| Held-out full-operation controls | 128 | All 24 | 3,072 |
| **Total** | **2,688** | | **23,552** |

Absolute hard budget: 24,000 prompts, and the generator refuses to exceed it. There are exactly 23,552 distinct prompt digests with the default seed; no estimates based on deduplication. Operations span 6 symbolic, 6 written, 4 algebraic, 4 comparison/conditional, and 4 multistep variants. Each main pair gets at least one operation from every family and 8 total, balanced over families.

Four complete operation *variants* are withheld from the main set: `written_average`, `algebra_balance`, `conditional_abs`, and `multistep_square`. The 128 control pairs see all 24 and **never appear in training**. The main pool also includes `train`, `val`, and `test` pair-group splits. Reversed pairs stay in the same split. This is pair-grouped, **not a guarantee of unseen individual operand values**. The hold-out design is useful only if the future analyzer actually respects the splits.

## Preview (no files written)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_stability_v3_generate.py `
  --prepare-only
```

## Generate JSONL manifests (no Qwen inference)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_stability_v3_generate.py `
  --generate-only `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3"
```

Writes `manifest.json`, `pairs.jsonl`, `prompts.jsonl`, `operations.json`, and `analysis_defaults.json`. All are reproducible with the seed; the script refuses to overwrite differing data.

## Capture Qwen activations (CUDA)

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_stability_v3_generate.py `
  --capture-only `
  --device cuda `
  --dtype float16 `
  --store-layers 16,28 `
  --max-prompt-tokens 256 `
  --threads 2 `
  --report-every 100 `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3"
```

Capture requires the same existing NanoJev cassette/activation modules as the previous experiment: `nanojev_full_activation_sparse_hierarchy.py` and its imported dependencies. This script calls its frozen-Qwen loader and `capture_full` function. All token positions of selected layers are saved without averaging; default `16,28` dramatically reduces storage versus retaining all hidden levels. Change `--store-layers all` **before starting capture** only if later exploring additional layers is essential; this uses substantially more disk. Changing the list of stored layers after capture requires a new output directory and recapture.

Capture records have the same `metadata` and `hidden_states` NumPy archive fields as the earlier runner, and include a `stored_layers` mapping. For the default mode, arrays have shape `[2, number_of_prompt_tokens, hidden_width]`, where rows represent original Qwen hidden-state levels 16 and 28. A future v3 analyzer must honor this mapping rather than index the two-row array by 16 or 28.

To smoke the capture path on 50 records, add `--max-new-captures 50`. Then resume the full run:

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_stability_v3_generate.py `
  --capture-only --resume --device cuda `
  --store-layers 16,28 `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3"
```

Resume validates completed records against their prompts and captured model identity. Writes use a `.partial` file and atomic rename, so interruptions do not create false complete records.

## Check capture status without loading the model

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_stability_v3_generate.py `
  --check-capture `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_stability_v3"
```

## Experimental limitations

The budget caps the **number of prompts**, not exact GPU runtime or disk use; written and multistep instructions are longer than symbolic expressions. Capture will execute 23,552 frozen-model forward passes. This generator performs **no** LASSO fitting, no operation result grading, and no activation injection. It prepares provenance and splits for those subsequent experiments. Captured tensors are enough to reanalyze different suffix widths on the stored layers without running Qwen again.
