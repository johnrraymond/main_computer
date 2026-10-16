# NanoJev MC3 — first verified game-transformation training experiment

This is a **source-only** clean snapshot patch, rooted at `main_computer_test/`. It includes the MC3 mathematical game library, 229-game catalog and 73-term dictionary, and the source-generated game's supplementary 39-game/19-transformation catalog. The latter is labelled *catalog/unverified teacher* because its original Python implementation imports unavailable Captain modules and uses private synthetic utility coefficients. The MC3 mathematical models are the source of executable, independently checked gold values.

## Invariant

**The SQL oracle determines and verifies a transformation's mathematical answer, but its answer is never presented to NanoJev at inference. A derived trainable decoder must produce the answer on held-out instances and games before that capability is claimed.**

## What is genuinely trained

- Existing frozen Qwen3-0.6B backbone and frozen precision parent remain unchanged.
- The **trained V4 attention X/Y decoder** is copied from the supplied checkpoint; the copy is optimized on *before / after* transformed payoff or scalar-model values.
- The original 14-class arithmetic `OperationHead` remains frozen. The game's transformation type is **not** smuggled through the decoder's out-of-band arithmetic operation embedding. The game-operation classifier is a new trainable head using the frozen contextual tokens.
- Gold X/Y is provided only to the objective, not the prompt. The input describes the source game, operator, parameters, and requested payoff cell (or scalar mathematical rule).
- Game cases are supervised in forward and reordered presentations, each with the same gold values. A fixed quota includes legacy numerical-retention examples.
- This is **scalar-projection training**, not a structured tensor/equilibrium training head. Full tensors and strategic certificates remain in the SQL teacher, with their proposed future objectives separately labelled.
- A new AdamW optimizer is used on the *copied decoder weights and new game classifier*. The active V4 optimizer checkpoint is not overwritten.

## Installation and smoke — Windows PowerShell

From `C:\Users\subsi\main_computer`, after saving the ZIP there:

```powershell
python new_patch.py .\nanojev_game_transform_mc3_integrated_v1.zip --dry-run
python new_patch.py .\nanojev_game_transform_mc3_integrated_v1.zip
```

Run the offline mathematical tests without needing CUDA, Qwen downloads, or a saved V4 checkpoint:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_game_transformation_library tests.test_nanojev_game_transform_experiment -v
& $py -m tools.nanojev_game_transform_experiment build `
  --database .\runtime\game-transform-experiment\games.sqlite3 `
  --instances-per-game 16
& $py -m tools.nanojev_game_transform_experiment smoke `
  --database .\runtime\game-transform-experiment\games.sqlite3
```

Both commands generate/verify records locally. The builder **refuses to overwrite** a database unless explicitly passed `--replace`. `smoke` tests both true and deliberately corrupted oracle results. No model training is performed by those commands.

## Freeze a source checkpoint

**Do not stop the active V4 trainer by editing its weights or config.** Copy a V4 checkpoint to a separate immutable input path, ideally during a saved-checkpoint boundary. A continually changing `latest.pt` is a poor lineage identifier; the experimental run seals the SHA-256 of the exact input.

```powershell
New-Item -ItemType Directory -Force .\runtime\game-transform-experiment | Out-Null
Copy-Item "$HOME\NanoJev\runs\independent_altreg_math_qwen_curriculum_v4_exact_mix\latest.pt" `
  .\runtime\game-transform-experiment\source-v4.pt
```

The V4 checkpoint's frozen parent path/config must still be accessible and match the saved identity. Your installed V3 fixed-breakdown trainer file must remain available as `tools/nanojev_altreg_attention_curriculum_mix_v4.py`.

## A genuine read-only checkpoint baseline

```powershell
$common = @(
 '--database', '.\runtime\game-transform-experiment\games.sqlite3',
 '--checkpoint', '.\runtime\game-transform-experiment\source-v4.pt',
 '--output', "$HOME\NanoJev\runs\game_transform_experiment_v1",
 '--device', 'cuda', '--dtype', 'float16', '--eval-limit', '32'
)
& $py -m tools.nanojev_game_transform_experiment baseline @common
```

The baseline runs the actual frozen Qwen model, precision parent, saved numerical decoder, and untrained game-classification head. It predicts 5%/10% X/Y accuracy and game-operator accuracy across **dev**, **test**, and **whole-game transfer** slices. It writes results and experiment identity into the *new output directory only*. It never writes to the source checkpoint or main training run.

## Actual experiment training

```powershell
$mix = 'positive_affine=2,permute_row=1,permute_column=1,compose_transformations=1,scalar_parameter_shift=2,retention=1'
& $py -m tools.nanojev_game_transform_experiment train @common `
  --training-mix $mix --steps 20 --save-every 5 --lr 0.00001
```

The default fixed mix contains 8 logical training positions, accumulated as batch-one frozen Qwen captures for small VRAM. Each game position supervises both presentations, and the single retention position uses native numerical tasks. **No training-step/cycle milestones automatically change the mix.**

The training checkpoints (`latest.pt`) and final `comparison.json` appear only under `game_transform_experiment_v1`. Training a few dozen steps is a *pilot*, not a guarantee of generalization. You can run the same command with a larger `--steps` for more updates; it reloads optimizer, decoder, and game-head state from the sealed experiment checkpoint. For a new mix, use a **new output directory** so the original experiment is reproducible.

Run evaluation without training:

```powershell
& $py -m tools.nanojev_game_transform_experiment evaluate @common --training-mix $mix --lr 0.00001
```

The `--training-mix` and `--lr` must match the sealed experiment identity; changing them requires a new output directory.

## Evaluations and honest promotion criteria

- `baseline.json`: source V4 numerical head on unseen transformed problems at 5% and 10%, paired forward/reversed, independent new operator classifier, plus original numerical retention.
- `comparison.json`: before/after on **the same held-out cases**, including per-game and per-operator results and legacy retention. No positive result is asserted in advance.
- `game_transfer`: entire named games are absent from training; explicitly reserved: Chicken, Captain Ship Defense, Bayesian posterior, Routing Delay, Pursuit Gap.
- `test`: previously unseen parameter instances and cases from games that are otherwise trainable. All transformed cases and paired orientations of one original instance share its split.
- Composition is row permutation followed by mutation of the first payoff entry, and **does not commute** with the reverse order. It is not incorrectly marked equilibrium-preserving.
- 229 catalog names are not 229 executable oracles; missing mathematical teachers are not trainable labels.
- Adding new game families should require documenting what operator or transfer capability they introduce and a held-out test demonstrating its impact.

Success requires real improvements on held-out transformations and games without unacceptable numeric-retention degradation, not the SQL smoke passing and not a low training loss. If evaluation does not improve, the experiment accurately reports that failure.

## Implementation boundary

The independent `tools/nanojev_game_transform_experiment.py` imports the *installed* V4 trainer and frozen Qwen model lazily for baseline/train/evaluate, so offline database smoke runs without any model dependency. The active numerical training mix and checkpoint are never edited. A later patch can add structured game-tensor prediction or real Captain-rubric learning; neither is silently claimed by this one.
