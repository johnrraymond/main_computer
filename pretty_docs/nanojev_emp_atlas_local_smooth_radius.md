# NanoJev Adaptive Failure Atlas — Local Smoothness Radius Mapper

## Purpose

Map *sampled* arithmetic-error regions surrounding independently discovered
Atlas failures, then compare their geometry with neural activation changes.
This tool is a **read-only second pass** over a completed Atlas. It neither
restarts the 100,000-case census nor trains or changes the four regular EMP
heads, Hard EMP, detector, or any checkpoint.

The mapper measures three different concepts:

- **Failure radius:** largest **fully probed integer Chebyshev ball** around a
  wrong answer where every tested coordinate is also wrong.
- **Signed-error radius:** largest fully probed ball where every coordinate has
  the *same signed numerical error* as the selected failure.
- **Activation smoothness:** per-layer standardized distance from the selected
  failure and the largest fully captured local integer ball below a chosen
  threshold. The tool also compares normalized activation gradients on
  measured unit-step correctness boundaries with unit-step same-label edges.

Local radius refers to changes in the two-dimensional `m, delta` sampling
coordinates on the **same signed and ordered operand branch**. These are
integer grid coordinates, not interpolated Euclidean or neural-manifold
radius. The underlying card is `[a, b, a+b, 0, max(abs(a),abs(b))]`.

## Install

```powershell
python .\new_patch.py "C:\Users\subsi\Downloads\nanojev_emp_atlas_local_smooth_radius_patch.zip"
```

The patch creates this document, `tools/nanojev_emp_atlas_smooth_radius.py`,
and `tests/test_nanojev_emp_atlas_smooth_radius.py`. No existing file is
modified or replaced. Prerequisite: the Adaptive Failure Atlas patch already
installed and its 100,000-case run marked complete.

## Run existing 36 error centers — no new census

```powershell
& $py -m tools.nanojev_emp_atlas_smooth_radius `
    --atlas "C:\Users\subsi\NanoJev\runs\nanojev_adaptive_failure_atlas_v1" `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_atlas_smooth_radius_v1" `
    --device cuda `
    --max-m-radius 512 `
    --max-delta-radius 32 `
    --inner-radius 4 `
    --budget 1200 `
    --refine-rounds 4 `
    --activation-budget 96 `
    --controls 8 `
    --threshold 2.0
```

No `--run` or `--checkpoint-source` is required: the program reads the
original Atlas's immutable `config.json` and loads the exact checkpoint the
Atlas used. It verifies its SHA-256 and reuses the original model loader and
source provenance. An event-time Atlas stays event-time; a latest-checkpoint
Atlas stays final-checkpoint. Nothing is silently substituted.

### Extend to fresh census failures

After the original 36 centers, run **into a new output directory** with:

```powershell
    --extra-discovery 80 `
    --extra-validation 40 `
    --max-centers 200
```

Additional centers are selected from the **existing census**, using its
original deterministic, hypothesis-neutral selection policy and split
membership. Independent held-out log2 magnitude bands are preserved. This
allows a much larger neighborhood study without generating another hundred
thousand operands. Increasing the center count does consume additional GPU
probes and activation captures.

## How it works

1. Load each selected incorrect case and recover its exact sign and operand
   order. Check that the fixed checkpoint still reproduces the stored wrong
   answer; refuse on drift.
2. Probe a **complete** integer square with `--inner-radius 4` (up to 81
   coordinates), then progressively expand independent magnitude and residual
   offsets at powers of two, up to the radius limits.
3. Find intervals between observed neighboring grid samples that disagree in
   correctness or signed error. Add midpoint and nearby unit-step samples for
   up to `--refine-rounds`, subject to `--budget` probes *per center*.
4. Record a **certified discrete radius** only for fully sampled integer
   shells. Beyond this core, report **nearest observed** directional changes,
   right-censoring, and the sampled connected error component. There is no
   assumption that error boundaries are monotonic or geometrically smooth.
5. Capture full intermediate vectors for the selected failure, nearby correct
   controls, a local lattice core, and sampled unit-step error boundaries,
   within `--activation-budget` *per center*. Correct controls must occupy the
   center's original log2 magnitude band and original discovery/validation
   partition. If none exist, activation attribution is **unavailable**, not
   a zero-valued heat map.
   **Capture parity:** The arithmetic census uses unhooked batched inference.
   Full TransformerEncoderLayer hooks may disable PyTorch's fused encoder path
   and, near decision ties, change the predicted number. The mapper does NOT
   relabel arithmetic outcomes or fabricate an activation boundary. It records
   both predictions, retries mismatches with a `norm@0`, `norm@1`-only hook
   (which does not hook the encoder layers), and uses only parity-matched
   activation vectors. When norm-only fallback succeeds, the measurement has
   final-norm channels only; it does NOT claim intermediate backbone layers.
   Remaining mismatches are excluded from activation gradients and ablations,
   while their original scored arithmetic observations remain in the maps.
6. For each backbone layer and norm invocation, compute its standardized
   activation-distance profile and compare the size of unit-step activation
   changes across error boundaries to unit-step changes within the same label.
   Return edge counts and ratios. A gradient ratio without enough matched
   edges is not a mechanism finding.
7. Optionally use `--ablate-top 4` to test the top anomaly-ranked channels
   against the center and its successful controls. This is an independent
   probe and does not modify the saved model.

## Outputs

`OUTPUT/config.json` locks the source Atlas, its config SHA, source checkpoint
SHA, selected center identities, and diagnostic parameters. A rerun with the
**same** command verifies and reuses completed center artifacts; changing
parameters requires a **new output directory**. Interrupted centers are
recomputed, while fully committed centers are SHA-verified.

Each `OUTPUT/centers/{discovery|validation}_NNNN/` contains:

| File | Content |
|---|---|
| `report.json` | Center, certified and observed radii, same-error count, component size and censoring, per-layer activation radius and boundary gradient statistics, candidate channels, optional ablations, `capture_consistency` parity diagnostics |
| `samples.jsonl.gz` | All local exact and tolerant predictions, signed error, actual operands, offsets, and cross-band indicators |
| `samples.meta.json` | SHA-256 hash and sample count for the full local archive |
| `arithmetic_map.svg` | Correct/incorrect observed samples in signed-log offset display |
| `error_signature_map.svg` | Cases with identical signed error versus other errors or successes |
| `activation_distance_map.svg` | Mean normalized layer distance for captured points; uncaptured points explicitly gray |
| `selected_activations.pt` | Raw hooked vectors, parity-verified usable vectors (sometimes norm-only), and separate captured/scanned predictions |

`OUTPUT/result.json` compares discovery and validation groups separately,
reports per-layer gradient ratios with event support, and summarizes recurring
candidate channels. Terminal events are labeled `EMP_ATLAS_RADIUS_CENTER`;
final status is `EMP_ATLAS_RADIUS_RESULT`.

**Important interpretation:** `--threshold 2.0` defines a *measurement*
criterion for standardized activation-distance, not a calibrated physical
boundary. Signed-log plot coordinates are display-only; radius values use
untransformed integer offsets. A `nearest_sampled_success` is **not** a claim
that every intervening coordinate was evaluated. For a strict local guarantee,
use `certified.failure.certified_radius` and its recorded complete-coverage
limit. No layer/channel relationship establishes causation without controlled
interventions and replication on independent failures.

## Recovery after an interrupted original radius run

Install the capture-parity fix and **rerun the exact same command with the same
`--output` path**. The mapper's `config.json` is compatible; committed centers
are SHA-verified and reused. The center that raised
`ATLAS_RADIUS_CAPTURE_PREDICTION_DRIFT` had not committed `report.json`, so it
will be recomputed. No 100,000-case census, retraining or checkpoint changes
are necessary. Do not remove the output directory.

## Troubleshooting

- `ATLAS_RADIUS_REQUIRES_COMPLETED_ATLAS`: supply your completed Adaptive
  Failure Atlas directory, not the earlier seven-ring run.
- `ATLAS_RADIUS_CHECKPOINT_SHA_MISMATCH`: underlying checkpoint missing or
  changed. Restore the original model; do not reinterpret a new checkpoint as
  the old one.
- `ATLAS_RADIUS_FIXED_MODEL_DRIFT`: the **unhooked** arithmetic scan no longer
  reproduces the Atlas center; the mapping refuses. This differs from a
  full-hook capture disagreement, which is now reported under
  `report.json -> capture_consistency` and does not abort the other centers.
- `capture_consistency.full_hook_disagreements`: instrumenting the model changed
  some predictions. Check `norm_only_recoveries`, `uncertified_cases`, and
  `center_capture_mode`. If the center is `uncertified`, arithmetic radius
  mapping remains valid but attribution/intervention findings are unavailable
  for that center. Those are diagnostics, not silently repaired predictions.
- `ATLAS_RADIUS_RESUME_CONFIG_DRIFT`: use a new `--output` directory when
  changing radius, budgets, threshold, or number of selected centers.

Run regression tests from the repository root:

```powershell
& $py -m pytest -q tests/test_nanojev_emp_atlas_smooth_radius.py
```
