# NanoJev: Gaussian population density experiment

## Invariant

All Gaussian banks emit **nonnegative density mass onto one shared coordinate grid**. Their contributions are **added**, without LHS/RHS class assignments or overflow gates. The total reconstruction is normalized only across the entire bank population. A far-away Gaussian contributes near zero; the implementation drops tails beyond five standard deviations (less than one part per million of continuous Gaussian mass, even less than that for a standard normal beyond five sigma).

This is a separate **shape-only capacity and optimization experiment**. It does not change or use the 21-bank model's old training state, frozen Qwen, readout head, or numerical inference. The target is an explicitly known synthetic density, not a per-prompt Hawk–Dove answer. This establishes whether increasing the parallel Gaussian population improves density reconstruction under a fixed step budget. It does **not** establish forward-training equivalence, generalization to unseen tasks, or inference of X from prompt features.

## Run

From `C:\Users\subsi\main_computer`, with `$py` set to the NanoJev Python interpreter:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_density `
    --device cuda `
    --steps 50 `
    --probe-every 10
```

Default run order is **200 → 2,000 → 20,000 → 200,000**, sequential, not concurrent. Run precisely two custom bank counts using:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_density `
    --only 5000,50000 `
    --device cuda `
    --steps 50 `
    --probe-every 10
```

A fast CPU smoke check (no GPU necessary):

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_density `
    --only 200,2000 `
    --device cpu `
    --steps 5 `
    --probe-every 1 `
    --output "$env:TEMP\nanojev-gaussian-population-smoke"
```

Options: `--only` accepts **any nonempty comma-separated sequence of distinct positive integers** in supplied order; `--shape single|double|triple` selects known target shapes; `--grid-bins` sets the common coordinate resolution (default 256); `--seed` fixes the candidate stream; `--cpu-threads` caps CPU threads (default 4); `--lr` sets the Adam learning rate (default 0.2); `--memory-limit-mb` bounds estimated peak scratch memory (default 1024). Defaults select the triple peak shape. `--rerun` repeats a completed run; otherwise runs with matching identity are reused. Different settings at the same output path fail closed instead of silently mixing results.

## Mechanism

- For each count N, generate N Gaussian candidate centers uniformly over the common [0,1] axis, with standard deviations drawn uniformly from 1.5–3.5 grid-bin widths. For the same seed, the first N candidates are exactly the same as the first N candidates in a larger population (nested experiments).
- Store only each candidate's Gaussian mass in its local 37-bin stencil, not a `N × grid` dense tensor. Normalize **each Gaussian's integral**, so its amplitude coefficient is interpretable as its total mass; this is not a per-bank probability classifier or independent normalization of 131 categorical outputs. Its learned amplitude is controlled by a softmax **across the whole population**, so the total reconstructed mass is one.
- At step 0 every bank has identical amplitude. This yields the **untrained population baseline**. It approximates the uniform candidate prior, **not necessarily the target**. More samples alone reduce sampling noise but cannot eliminate an incorrect population prior.
- At each optimizer step, compare the **sum of density contributions** against the known target distribution on the shared grid (mass MSE), and backpropagate into the N log-amplitudes. Bank centers and widths stay fixed. Run the *same number of Adam steps* for every population. More banks incur more computation; the test holds gradient steps fixed, not FLOPs.
- Compare the untrained baseline and trained aggregate with total variation, KL(target‖reconstruction), mass-bin RMSE and maximum bin residual. Plot their curves at each probe step. Gaussian amplitudes are optimized against an oracle known shape, so this is **representational/optimization** evidence only, not a valid measured DEV accuracy or proof of a numeric predictor.

Outputs go to `~/NanoJev/runs/hawk_dove_gaussian_population_density_v1`: `comparison.json` at the root, and per-count `banks-000000200` etc containing `config.json`, `summary.json`, `curves-step-000000.csv/.svg`, and subsequent probe curves. No TEST split is read and no old checkpoint is mutated. The plot shows target (gray dashed), untrained uniform population (orange dotted), learned aggregate (blue); CSV contains the residual in every bin.

**Acceptance questions:** Does final reconstruction get closer to the target than the untrained population? Does raising bank count improve fixed-step reconstruction? Does the improvement saturate? The experiment records evidence rather than predetermining the answers. If this works, the next experiment is a **feature-conditioned Gaussian amplitude generator** on actual held-out Hawk–Dove examples, with the oracle used only in the loss/scorer and no target leakage in its inputs.
