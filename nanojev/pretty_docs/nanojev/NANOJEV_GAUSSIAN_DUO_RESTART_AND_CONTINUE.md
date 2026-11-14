# NanoJev: restart and continue independently trained TinyStories/Pythia Gaussian heads

## Invariant

Each model must retain **its own** trained Gaussian-head weights, frozen-backbone activation cache, payoff-matrix split, and best DEV checkpoint. Continuing a run must not initialize a new head or substitute a reference/Qwen model.

`train-all` runs TinyStories-33M and Pythia-70M as separate processes, **sequentially** by default. It does not average their predictions.

## Continue the completed October 23 v1 runs

The previous trainer wrote `best.pt` (head weights + best step) but did **not** write optimizer, LR-scheduler or sampling-RNG state. The old run cannot be resumed bit-for-bit. This patch supports one explicit **weights-only migration** that starts a new training phase from those actual learned weights. It preserves the original results under `before_weights_only_resume/`.

Run from `main_computer` using the NanoJev virtual-environment Python used for the original run:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_two_backbone train-all `
    --resume `
    --weights-only-resume `
    --device cuda `
    --banks 2000 `
    --grid-bins 256 `
    --steps 30000 `
    --seed 20261022 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_two_backbone_fresh_v1"
```

`--steps` means **cumulative target step**, not additional steps. In this specific run:

- TinyStories resumes the best head at **15,000** and trains through **30,000**.
- Pythia resumes the best head at **13,500** and trains through **30,000**. Its previous steps 13,501–15,000 were already evaluated but were **not** retained in `best.pt`, so this continuation branches from the better 13,500-step head.
- Both runs keep the same data/binding identity; the only permitted identity change is the cumulative step target.
- The original v1 summaries, history, best weights, and DEV/TEST predictions are preserved separately in `before_weights_only_resume/`. Do not overwrite or delete that directory.

Only use `--weights-only-resume` for this legacy migration. Logs will explicitly show `optimizer=NEW scheduler=NEW sampler_rng=NEW`. These runs **do not** reproduce the hypothetical uninterrupted 30,000-step trajectory.

## Restart an interrupted patched run

After migration, every run writes `latest.pt` **atomically** at each DEV probe and at each optional `--checkpoint-every` interval. It includes:

- latest training-step weights, AdamW moment/state and parameter groups;
- cosine scheduler state, training phase bounds and LR;
- independent batch-sampling generator, global CPU RNG, and available CUDA RNG states;
- historical DEV probe records, best-rank selection and best-step index;
- strict experiment/capture identity and legacy-migration provenance.

To recover from an interruption, rerun the **same command with `--resume`** (and the same `--steps`, e.g. 30,000). The presence of `latest.pt` automatically selects **full-state** restoration; no optimizer reset is performed. A completed run can later be extended to 45,000 total steps by using `--resume --steps 45000`. That extension starts a *new cosine learning-rate phase* while preserving AdamW moments and sampling RNG. The LR schedule is documented as a new phase, not represented as one uninterrupted 45,000-step cosine.

For shorter recovery intervals, add `--checkpoint-every 100` (or another positive number). The default is to checkpoint every `--probe-every` (500) steps; the final step is always saved. A crash may lose steps since the last atomic checkpoint, which are recomputed on restart.

The `best.pt` file **is not** a complete training-state checkpoint. It is retained exclusively for DEV-selected inference and audit. `latest.pt` is the training checkpoint. Do not manually replace either file.

## Run only one backbone

To extend just Pythia after both heads have migrated:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_two_backbone train `
    --backbone pythia `
    --resume `
    --device cuda `
    --banks 2000 `
    --grid-bins 256 `
    --steps 45000 `
    --seed 20261022 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_two_backbone_fresh_v1\pythia"
```

The run refuses a different seed, backbone, Gaussian dimensions, split, prompt hash, learning-rate settings, matrix policy, or cache source. It also refuses changing a phase's `--steps` before that phase has finished. The reference head and Qwen are never loaded.

## Test and evaluation contract

The TRAIN split alone updates weights; DEV selects `best.pt`. On completing an extension, TEST is evaluated **once** using the new best DEV-selected weights. DEV and TEST predictions for the eventual three-model ensemble are regenerated from the same best checkpoint. For legacy weights-only migration the original reports and predictions are copied into the archive before any further optimizer steps.

The CPU regression suite includes simulated interruption/restart parity at the exact weight and sampling-state level, AdamW extension, legacy checkpoint migration, preserved originals, independent backbones, strict identity drift checks, and checkpoint re-audit. Full NVIDIA/CUDA execution still needs validation on the user's workstation.
