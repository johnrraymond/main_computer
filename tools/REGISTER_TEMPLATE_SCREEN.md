# NanoJev register-template screening pilot

The script `tools/nanojev_register_template_screen.py` is a **new, additive** experiment. It does not modify or interrupt either two-number analysis. It uses frozen Qwen and the existing full-token capture and fast sparse LASSO hierarchy modules already installed by the two-number patches.

## Purpose

Learn **which register declaration produces the strongest shared two-number readout as the downstream operation changes**. The experiment is not yet a test of arithmetic competence or activation injection. Numeric operands occur once each, as text inside the register declaration, during discovery. Their values are never repeated in operation or answer text. Both position decoders must reconstruct the registered operand, not the arithmetic result.

For each candidate template, the script separately fits two new LASSO decoder hierarchies using **all operation variants pooled into a shared training set**. Every candidate independently learns the selected features, coefficients, intercepts, and statistics. **No previous learned coefficient or selected feature index is used.**

The previous run supplies only the choice of representation (decimal/logbar/paired), alpha ratio (relative to per-head alpha_max), hierarchy stage, and chosen layers. Solver tolerances/iterations can be specified explicitly.

## First pilot and command-line settings

Default pilot: 4 registration templates (`ab,xx,r0,brackets`), 64 ordered pairs, 3 operations (`add,sub,gt`), **768 frozen-model forward passes**, one representation, one alpha, one hierarchy stage.

Template identifiers available: `ab`, `xx`, `r0`, `brackets`, `dollar`, `long`. Operation options: `add`, `sub`, `mul`, `gt`.

Example captured prompt for `xx` / decimal:

```
XXXXXX = 2000000
YYYYYY = -3
operation: XXXXXX + YYYYYY
Answer:
```

For this prompt, only the operator line changes between operation variants. The two registered operand values are held fixed.

### Before a capture: print plan without downloading/loading Qwen

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_template_screen.py `
  --prepare-only `
  --representation decimal `
  --alpha-ratio 0.005 `
  --stage 2 `
  --layers 16,28
```

The `decimal/0.005/stage 2` values are an **illustrative configuration**, not a claim that they are the current run's eventual winner. Use explicit arguments when you know your desired settings.

### Option A: automatically read the best currently reported hyperparameters

Supply the *summary JSON*, not a saved model directory:

```powershell
$summary = "$env:USERPROFILE\NanoJev\runs\two_number_sparse_fast_v1\fast_two_number_summary.json"
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_template_screen.py `
  --prepare-only `
  --inherit-summary $summary
```

`--inherit-summary` chooses the highest observed joint pass rate from valid rows; ties favor fewer active coefficients. If the source two-number experiment is **still running**, this is the **best result so far**, not necessarily the final one. The selected configuration is frozen into the new capture manifest. Override any specific field with `--representation`, `--alpha-ratio`, `--stage`, or `--layers`. Imported learned weights/indices are never read.

### Option B: manually specify the configuration and capture

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_template_screen.py `
  --capture-only --device cuda --threads 2 `
  --pair-count 64 --templates ab,xx,r0,brackets `
  --operations add,sub,gt `
  --representation decimal --alpha-ratio 0.005 `
  --stage 2 --layers 16,28 `
  --readout-scope suffix --tail-tokens 4 `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v1"
```

Resume interrupted capture with `--resume` and the same capture arguments. An existing capture will not be silently overwritten.

### Analyze independently of ongoing two-number runs

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_template_screen.py `
  --analyze-only --threads 2 `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v1"
```

To **change LASSO penalty or hierarchy without Qwen recapture**, re-analyze existing records and provide a distinct results root:

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_template_screen.py `
  --analyze-only --alpha-ratio 0.002 --stage 3 `
  --readout-scope full --threads 2 `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v1" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v1\analysis_full_alpha_002"
```

Changing **representation**, templates, operations, or operand pairs requires a different capture plan and root, because the prompt text changes. Changing penalty, stage, layers or readout scope reuses the same frozen-model captures.

## Observation site

`--readout-scope suffix --tail-tokens 4` (default) uses the final four token positions, after the operation, **without pooling**. This is the important first test of whether numerical information is still readable after processing different operations. It intentionally avoids the trivial result that a causal transformer's register-prefix states are invariant to later operation text. `--readout-scope full` uses every captured token and allows LASSO to select any coordinate, including earlier register tokens; this is a useful alternate baseline but not sufficient on its own to prove downstream stability. The raw `.npz` captures always preserve all tokens and all layers, regardless of scope.

## Output

Files under `<output-root>/analysis/`, or `--results-root`:

- `register_template_scores.csv`: independent template scores, per-slot joint 5% readback, worst operation, per-pair **all operations** success, normalized drift of readouts across operations, active coefficient count, fit seconds, convergence statistics.
- `register_template_summary.json`: configuration, per-operation scores, provenance.
- `register_template_failures.json`: sparse convergence failures by template (never disguised as valid scores).
- `register_predictions_<template>.csv`: pair/operation-level predictions.
- `models/<template>/...`: **newly fitted** sparse coefficients and feature indices for that template only.

The pilot intentionally reports **in-sample** reconstruction and operation variation. It is a ranking/screening step, **not proof of generalization to new operands/operations or proof that injected registers can drive arithmetic**. Follow-up tests can be built after candidates are identified.

## Dependencies and validation

Requires the existing two-number and full-token packages and `numpy`, `scikit-learn`, `threadpoolctl`; CUDA capture requires the original frozen-model environment. Tests:

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' -m unittest `
  discover -s .\tests -p test_nanojev_register_template_screen.py -v
```

Set `--self-test` for a tiny dependency and prompt-format smoke without Qwen. The full CUDA capture and live LASSO pilot were **not** executed in the packaging environment.

## Live analysis logging and concurrent capture safety

The original pilot did not log anything until **both operand hierarchies for an
entire template** had been fitted. The logging fix reports startup, the full
capture preflight, per-template matrix construction, normalization, every
operand position, periodic completed LASSO heads, and a heartbeat every 30
seconds even when a single fit is slow. Scikit-learn convergence warnings remain
consolidated rather than printed per fit.

An analysis started during capture must not fit against an incomplete dataset.
The default mode **fails immediately** with `REGISTER_CAPTURE_INCOMPLETE` and
counts of present and missing unique prompt records. If you want to launch the
analysis before capture ends, use `--wait-for-capture`: it polls the completed
atomic `.npz` records, reports progress, and starts fitting **only when every
record is present**. No activation data is recaptured by the analyzer.

Run alongside an active CUDA capture using a separate results root:

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_template_screen.py `
  --analyze-only `
  --representation paired `
  --alpha-ratio 0.01 `
  --stage 5 `
  --layers 16,28 `
  --readout-scope suffix `
  --tail-tokens 4 `
  --solver-max-iter 10000 `
  --solver-retry-multiplier 3 `
  --kkt-iters 12 `
  --threads 8 `
  --wait-for-capture `
  --capture-poll-seconds 15 `
  --progress-every-heads 10 `
  --heartbeat-seconds 30 `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v2" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v2\analysis_logged_alpha_001"
```

The readout template, numerical representation and samples remain those of the
saved capture manifest; analysis options change only the regression settings.
Use a distinct `--results-root` when comparing configurations or when an older
analyzer might still be writing to the default `analysis` folder.


## Weak-penalty convergence correction: per-head continuation

The registration pilot previously attempted `--alpha-ratio 0.002` as a cold
LASSO fit for every head. In a real 3,072-row, 8,192-feature suffix analysis,
**both** `ab` and `xx` failed in their *first zero-detection head* with
`SPARSE_HEAD_SOLVER_NOT_CONVERGED`; neither template produced an accuracy score.
The two-number fast runner had succeeded at 0.002 after visiting stronger
penalties. This patch gives registration analysis the same per-head strategy.

Each head is trained from scratch with the **new template's own** activations,
number labels, and its own `alpha_max`, using sequential initialization:

```
alpha_ratio = 0.03 -> 0.015 -> 0.005 -> 0.002
```

The final scores and exported model ALWAYS correspond to `0.002`. Intermediate
penalty fits are warm-starts only and are not promoted to the result. This does
NOT transfer learned features or weights from the prior two-number study, nor
from the other template, nor from the other register position. The unaveraged
activation features, hierarchy, objective, convergence tolerance, and KKT checks
remain unchanged. The continuation sequence is configurable by
`--warmup-ratios`; stronger values only, in descending order. Use
`--warmup-ratios none` to reproduce the previous cold-start behavior.

Re-run from the already completed 6,114 capture records (no GPU needed):

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' `
  .\tools\nanojev_register_template_screen.py `
  --analyze-only `
  --representation paired --alpha-ratio 0.002 --stage 5 `
  --layers 16,28 --readout-scope suffix --tail-tokens 4 `
  --warmup-ratios 0.03,0.015,0.005 `
  --solver-max-iter 10000 --solver-retry-multiplier 3 --kkt-iters 12 `
  --threads 8 --heartbeat-seconds 30 `
  --output-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v2" `
  --results-root "$env:USERPROFILE\NanoJev\runs\register_template_screen_v2\analysis_alpha_0002_continuation"
```

`register_analysis_heartbeat` now includes the internal alpha ratio being
solved; `register_analysis_position_failure` reports the numerical diagnostics,
including which step in the continuation failed. The `summary.json` records
`warmup_ratios`, and score rows record warm-up solve counts. Failures are still
fatal **for that template**, and are saved rather than reported as success.

Penalty continuation does not mathematically guarantee convergence: if the
actual data still defeat the optimizer, increasing
`--solver-max-iter`/`--solver-retry-multiplier` may be necessary. Weakening
convergence checks or changing the penalty merely to produce a score is not
part of this patch.
