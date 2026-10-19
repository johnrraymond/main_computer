# Hawk–Dove million-counts: T00 mask diagnostics and optional focused sampling

This patch builds on the **independent tier-gates** version of the trainer and applies over `main_computer_test/`. It does **not** change the game database, LogBar encodings, the X/Y decoder loss, tier-specific advancement gates, or any saved optimizer state.

## 1. Diagnose the four actual T00 masks (read-only)

From `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_operand_audited\latest.pt"

& $py -m tools.nanojev_hawk_dove_million_train diagnose `
    --parent $parent `
    --device cuda `
    --diagnose-tier T00
```

The diagnostic uses the **entire development split for the chosen tier**, once per mask and numerical instance. It deliberately does **not** use the test split or the 48-presentation advancement-probe subsample. Its JSON report is saved alongside the current checkpoint, e.g.:

`$HOME\NanoJev\runs\hawk_dove_million_v1\diagnose-T00-step-032700.json`

T00 masks:

| Mask | X | Y |
|--|--|--|
| `0` | raw | raw |
| `1` | raw + LogBar | raw |
| `2` | raw | raw + LogBar |
| `3` | raw + LogBar | raw + LogBar |

For each mask the report includes: eligible count, X-only/Y-only/joint accuracy within 5%, X/Y failure counts, mean and median relative error for each output, and three large-error prompt examples with true and predicted X/Y. Every evaluated case is also in the JSON `cases` list, enabling pairwise comparison of all four masks on the **same** underlying development instances. `weakest_masks` ranks masks by joint accuracy, breaking ties by the larger mean relative error. Inspect the counts and examples rather than treating a close ordering as decisive.

The training logs previously supplied contain only aggregate joint accuracy, so they do **not** establish which mask is weakest. Run `diagnose` to determine that from the actual current checkpoint. The diagnostic does not take an optimizer step or modify the checkpoint or policies.

## 2. Optionally emphasize the weak mask in training

After seeing the report, use `--mask-weight T00:MASK=WEIGHT`. For example, **only if mask 3 is weak**:

```powershell
& $py -m tools.nanojev_hawk_dove_million_train train `
    --parent $parent `
    --device cuda `
    --steps 5000000 `
    --chunk-steps 300 `
    --mask-weight T00:3=4
```

The existing independent tier-gate policy is reused from `tier-gates.json`. You do not need to repeat nine `--tier-gate` flags unless changing them. With mask weights `1,1,1,4`, mask 3 receives approximately `4/7 = 57.1%` of **T00-focused training presentations**, while the other masks each receive about `1/7`. Other tiers' samples are unchanged. The option is an explicit intervention, never an automatic reaction to a noisy probe.

Weights are persisted separately in `mask-weights.json`, bound to the original checkpoint identity. They can be changed independently, e.g. `--mask-weight T00:2=3 --mask-weight T00:3=4`. To turn all weighting off without deleting the policy file, use `--reset-mask-weights` on the next `train` invocation, which resets every mask to 1. For a single mask, `--mask-weight T00:3=1` restores its default weight.

Defaults are all weights 1. If no weights are set, the original training row selection is byte-for-byte / RNG-equivalent for identical seeds and steps.

## 3. Checkpoint safety

This patch recognizes both the original million-count trainer SHA and the existing independent-tier-gates trainer SHA as valid ancestors, while preserving `config.json`, `latest.pt`, checkpoint's embedded immutable config, optimizer tensors, global step, random state and tier-gates sidecar. The new mask sidecar is written only in `train` mode when weights are deliberately configured (or an existing mask policy must be updated). The read-only `diagnose` mode writes a diagnosis JSON but does not write the model, policy or data files.

The source/parent checkpoint paths and teacher database must still match the existing lineage; drift is rejected, not silently reset.

## 4. Tests

```powershell
& $py -m unittest tests.test_nanojev_hawk_dove_million -v
```

The additions test diagnosis per-mask with a known failing mask, persistence/validation, deterministic default sampling, strongly weighted sampling, prior-tier-gate checkpoint compatibility, and standard regression checks. CPU mock tests do not demonstrate real CUDA accuracy; the actual weak variant is unknown until you run the read-only diagnostic.
