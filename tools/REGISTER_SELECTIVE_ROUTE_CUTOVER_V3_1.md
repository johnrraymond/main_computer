# NanoJev selective magnitude-route cutover v3.1

## Purpose

V3 sometimes assigned the correct magnitude class to a misrouted number, but its active soft gate simultaneously damaged correctly routed predictions. V3.1 separates **error detection**, **alternative-class selection**, **route-conditional numerical reconstruction**, **conservative calibration**, and **direct vs. arithmetic-result integration** into independent stages. V1.2, v2, and v3 are unchanged.

Patch files, merged from the `main_computer_test/` ZIP root:

- `tools/nanojev_register_selective_route_cutover.py`
- `tools/REGISTER_SELECTIVE_ROUTE_CUTOVER_V3_1.md`
- `tests/test_nanojev_register_selective_route_cutover.py`

Requires prior v3, v2, v1, v1.2, buffered decoder and frozen-Qwen cached benchmark scripts already installed in the repository. This is **not** a standalone copy of Qwen or those earlier scripts. The Qwen3-0.6B backbone and the trained v1.2 register/operation decoder are frozen; original checkpoints are read-only and checked by SHA-256.

## Architecture

- Source register values from frozen v1.2: `source_x, source_y` (exact original integer inference at step zero).
- Twelve signed experts plus zero from the original 38-head decoder, **not an average of experts**.
- **Detector:** binary BCE, supervised by whether the source route class differs from the true sign/magnitude route. Class imbalance handled by `--detector-pos-weight`, default 16. Only detector parameters update in this stage.
- **Selector:** 13-way categorical classifier, cross-entropy supervised on source-wrong-route operands. Only selector parameters update in this stage. Route classes are zero plus six positive and six negative magnitude bands.
- **Reconstructor:** given one chosen class (oracle class **during this training stage**), select that class's expert asinh candidate, and train a continuous correction against the true number. Only reconstructor parameters update. This provides an upper bound on what better route classification might achieve.
- **Calibration:** detector threshold and alternative-route probability threshold chosen from a *calibration-only* half of the fixed validation examples. A candidate must meet 99% exact retention of correctly routed source operands, demonstrate previously wrong-route numerical recovery, and beat the source numeric-result metric (ties fall back to source). A separate held-out half vetoes thresholds if either numeric-result or joint-X/Y accuracy regresses, or the 99% retention condition fails.
- **Integration A/B:** frozen calibrated detector and selector, with only reconstructor further updated, either on direct X/Y loss (`direct`) or X/Y plus executable result supervision (`result`). Both integration branches fork identical calibrated weights and the same inherited data stream, with independent optimizers/checkpoints.

Inference uses no true labels. Let `d` be detector probability, `s` alternative-route confidence, and `r_alt` the top-1 route. A replacement occurs **only if**:

```
d >= detector_threshold
and s >= selector_threshold
and r_alt != r_source
```

Then `prediction = sinh(asinh(expert[r_alt]) + learned_fine_correction)`; otherwise the output is **exactly the original v1.2 integer prediction**, without blending, soft interpolation, or rounding changes. A failed calibration installs thresholds 1.01/1.01, so the original decoder remains authoritative. Source operation classification remains frozen.

## Run (PowerShell, repository root)

Extract this ZIP's `main_computer_test/tools` and `main_computer_test/tests` into the repository's `tools` and `tests` directories, respectively. Do not create a nested `main_computer_test/` subdirectory inside an existing `main_computer` repository. The patch is additive.

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_register_selective_route_cutover.py --self-test

$common = @(
  '--v3-root', "$env:USERPROFILE\NanoJev\runs\register_stability_v3",
  '--results-root', "$env:USERPROFILE\NanoJev\runs\register_selective_route_cutover_v3_1",
  '--source-checkpoint', "$env:USERPROFILE\NanoJev\runs\register_operation_result_cutover_v1_2\latest.pt",
  '--constraint-stage', 'continuity',
  '--batch-size', '16', '--buffer-capacity', '256',
  '--per-expert-batch', '16', '--replay-updates', '2',
  '--op-width', '96', '--selective-width', '128',
  '--detector-pos-weight', '16',
  '--reconstruct-wrong-weight', '4',
  '--min-correct-route-retention', '0.99',
  '--result-weight', '0.12',
  '--eval-every', '100', '--checkpoint-every', '100',
  '--report-every', '50', '--lr', '0.0003',
  '--device', 'cuda', '--dtype', 'float16', '--threads', '2'
)

# Run strictly in this order; each stage must complete before the next starts.
& $py .\tools\nanojev_register_selective_route_cutover.py @common --stage detector --steps 3000
& $py .\tools\nanojev_register_selective_route_cutover.py @common --stage selector --steps 3000
& $py .\tools\nanojev_register_selective_route_cutover.py @common --stage reconstruct --steps 3000
& $py .\tools\nanojev_register_selective_route_cutover.py @common --stage calibrate --steps 3000

# Inspect the calibration event before launching both matched integrations.
& $py .\tools\nanojev_register_selective_route_cutover.py @common --stage direct --steps 3000
& $py .\tools\nanojev_register_selective_route_cutover.py @common --stage result --steps 3000
```

The three model-learning stages each inherit the **best selected model weights** from the preceding completed stage, while continuing its completed replay/generator state. The detector is checkpoint-selected by ROC AUC; the selector by route accuracy on previously misrouted operands; the reconstructor by its oracle-route recovery and error. The full final-stage stream state is retained even if best weights came from an earlier step.

To resume an interrupted stage, use the identical command with `--resume` (the last complete `latest.pt`); `--steps` can be increased without a configuration mismatch. Do **not** run two phases concurrently into the same results tree. A new stage rejects a parent checkpoint that has not completed. `calibrate` is a single non-training run and should not be resumed.

Offline evaluation after finishing (requires existing cached validation/test/control tensors; no Qwen download):

```powershell
& $py .\tools\nanojev_register_selective_route_cutover.py @common `
  --stage result --steps 3000 --eval-only --eval-checkpoint best
```

Only evaluate the held-out `test` and `control` sets after you have selected the final configuration. The calibration split is **not** an untouched evaluation set; the other validation half serves as a final safety veto and model selection set. Only an untouched test split can support a fresh performance claim.

## Results and acceptance conditions

Results are written separately under:

```
register_selective_route_cutover_v3_1/
    detector/   latest.pt  best.pt  run_config.json
    selector/   latest.pt  best.pt  run_config.json
    reconstruct/latest.pt  best.pt  run_config.json
    calibrate/  latest.pt  run_config.json
    direct/     latest.pt  best.pt  run_config.json
    result/     latest.pt  best.pt  run_config.json
```

Main measurements:

| Field | Question |
| --- | --- |
| `detector_roc_auc`, `detector_precision_at_half`, `detector_recall_at_half` | Can the detector distinguish wrong source routes from correct routes? |
| `selected_route_accuracy_on_source_wrong` | Can the independent classifier select the correct alternative? |
| `oracle_route_candidate_asinh_mae` | Are frozen expert predictions adequate given a known route? |
| `oracle_route_reconstructed_asinh_mae`, `oracle_route_wrong_source_5pct` | Does supervised route-conditioned numerical reconstruction genuinely recover misrouted values? |
| `correct_route_retention`, `incorrect_route_replacement_rate` | Does integration preserve source-correct route predictions? |
| `wrong_route_operand_5pct_recovery`, `severe_operand_5pct_recovery` | Are hard numerical errors actually corrected? |
| `numeric_result_5pct_pass_rate`, `joint_xy_5pct_pass_rate` | Do gains survive the complete arithmetic task? |
| `calibration.heldout_veto_reasons`, `calibration.chosen_is_source_only` | Did calibration permit replacing any frozen outputs? |

Reference v1.2 performance on the original 512-example validation (465 numeric examples): **19.57% numeric results**, **5.86% simultaneous X/Y**. The v3.1 model's held-out-half rates will not necessarily equal those full-set rates. The aim is to *exceed* those rates on identical evaluation rows and, specifically, to recover operands whose source class was incorrect while preserving at least 99% of already-correct source route predictions.

If `chosen_is_source_only: true`, a model has not earned replacement authority. **Do not interpret stable source-level results as a successful route repair.** Review the detector, selected-route, and oracle-route reconstruction metrics first. The `direct`/`result` branches run safely in fail-closed mode, but with no activated replacements they cannot improve downstream accuracy: their training logs show zero `active_operands`.

## Verification

Mocked end-to-end stage lifecycle, source SHA preservation, step-zero exactness, independent gradients, route-select-only numeric inference, no soft averaging, fail-closed calibration, A/B replay match, resumed training, and offline evaluation have been tested with CPU fixtures. The full CUDA experiment has **not** been executed in this environment.
