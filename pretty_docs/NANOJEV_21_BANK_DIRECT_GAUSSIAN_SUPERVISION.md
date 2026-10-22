# NanoJev: direct Gaussian-bank supervision, before numerical decoding

This is a **new, isolated research runner**. It never trains the numerical decoder, never optimizes overall answer accuracy, and never uses held-out development/test examples for gradients. It imports the previous verified 21-bank checkpoint only as an initializer. No changes to its source checkpoints occur.

## Goal

Each of 21 independent fixed-radius, zero-centered banks is trained to output the *oracle* `[left overflow, 129-bin Gaussian interior, right overflow]` probability distribution. Targets strictly outside the radius have exactly 100% probability at the corresponding overflow bucket. Inside, their target is a Gaussian centered at the answer. Centers and radii remain immutable.

Every bank receives its **own region-balanced KL gradient** (`KL(oracle || predicted)`), directly backpropagated through bank readout weights. This is not the old `numerical_first_backward` rule and there is **no cap on Gaussian gradients relative to decoder gradients**. The decoder is frozen and not invoked in this training objective. Each bank is gradient-clipped independently, so updates to one bank do not suppress another bank's learning.

Raw cross-entropy has an irreducible entropy floor for a true Gaussian. We therefore measure **both cross-entropy and KL**, and KL should approach zero if the bank learns the oracle distribution. This is not a way to suppress or remove errors: KL explicitly subtracts the *fixed target's entropy*, not any error. Raw cross-entropy is retained unchanged in all probes.

**Coverage limitation:** the focused 1–1,000 curriculum cannot certify regions it never exercises. The probes show separate left/interior/right example counts for each bank and explicitly mark unsupported regions. A bank that sees only RHS examples can learn RHS routing, but does **not** count as having learned a Gaussian interior. No unseen numeric values are fabricated to make results look better. To learn absent LHS regions or Gaussian interiors in the finest banks, later training will require oracle-valid examples exercising those regions.

## Run

From `C:\Users\subsi\main_computer`, with `$py` pointing at your NanoJev Python environment:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_bank_21_supervision `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt" `
    --mixture-train-per-tier 900 `
    --mixture-new-dev-per-tier 180 `
    --learnability-train-per-tier 0 `
    --retention-fraction 0 `
    --lr 0.0001 `
    --bank-clip-norm 1 `
    --views 32 `
    --additional-steps 1000 `
    --max-steps 30000000 `
    --chunk-steps 100
```

Read-only monitor:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_bank_21_monitor `
    --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_bank_21_direct_v1"
```

Files in the new run directory: immutable source snapshots, `bank_initial.pt`, `config.json`, `focused_curriculum.json`, `latest.pt`, `probe-step-*.json`, and a **research-only** best-held-out-KL checkpoint. Run the **same command** to resume additional optimizer steps, subject to maximum step budget.

## What to read in the monitor

- `train_bank_kl` and `held_out_bank_kl` should fall. The latter measures whether structure generalizes beyond memorized examples.
- Every bank gets its **own** KL, raw cross-entropy, entropy floor, weight-change norm, and supported-region counts.
- `held_out_conditional_gaussian_shape_kl` measures actual shape error after normalizing only the predicted interior probability. `held_out_interior_mass` distinguishes the correct shape with too little total interior probability from a genuinely correct full distribution.
- `held_out_interior_center_error_bins` tells us whether the **Gaussian centers** align with their oracle centers. This is measured in bin spacings, since 21 banks span enormous differences in numerical scale.
- `held_out_center_order_violation_fraction` tests whether distinct oracle centers sort in the same direction as predicted centers **within the same tier**, excluding unresolvably close centers. Null means no sufficiently resolvable pairs.
- `held_out_kl_reduction` and `held_out_region_kl_reduction` report actual improvements, not a constructed percentage of final numerical answers.
- `banks_with_measurable_weight_updates` and per-chunk clip counts show actual optimization. Missing support is a warning, never a pass.

**Important:** The source has not been run on your full CUDA datasets in this environment; tests use synthetic CPU models and mocked loading, plus oracle and legacy regression suites. Do not promote these checkpoints as a finished numerical solver.
