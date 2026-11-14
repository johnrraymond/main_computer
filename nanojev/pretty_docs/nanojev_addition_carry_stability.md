# NanoJev carry-stability experiment (v1)

## Motivation and interpretation

The six-branch transfer probe from `signed_passed.pt` found:

- Three-digit positive **no carry**: 96.09% held-out DEV, but **carry**: 64.06%.
- Four-digit positive **no carry**: 81.25%, but **carry**: 35.16%.
- Three-digit opposite-sign DEV minimum: 58.59%; four-digit opposite-sign: 15.62%.
- The best three-digit positive branch retained earlier abilities at a minimum of only 39.84%.
- A zero-only four-digit experiment achieved 100% zero results but failed retention, so is **not** proof of transferable subtraction.

These observations suggest both carry composition and catastrophic forgetting. This lab tests that hypothesis without mixing in four-digit length or cancellation.

## What changes and what does not

This is a **new runner**. It imports the existing `nanojev_addition_curriculum` and `nanojev_addition_transfer_probe` modules without editing either. Both arms load the identical, successful `digit_tokens/signed_passed.pt`. Both train on the same fixed three-digit operand-pair partitions. The neural model architecture and learning objective are unchanged; at inference it receives only the two operands and produces predicted sign/digits. Arithmetic in Python is used **only** for supervised TRAIN labels and scoring.

Three mutually exclusive DEV categories: `no_carry`, `single_carry` (exactly one carry event), `chain_carry` (two or more). The dataset classification is not an input feature. Unordered operand pairs are split by the original deterministic fold, so `(a,b)` and `(b,a)` cannot leak between TRAIN and DEV. TEST is never generated or opened.

Two ablation arms differ only in historical TRAIN replay allocation:

| Arm | New three-digit examples | Replay of prior stages |
| --- | ---: | ---: |
| `balanced` | 50% | 50% |
| `retention_heavy` | 30% | 70% |

Replayed data are drawn **every step** from `atomic`, `no_carry`, `carry`, and `signed`, weighted respectively 1:2:3:6 to protect the vulnerable signed stage. This corrects the older four-step rotation in which each older stage appeared in only one quarter of optimization steps. Both use a lower initial learning rate of 0.0003 (previously 0.001). The arms are matched in architecture, training data partitions and optimizer settings, but because the rehearsal allocations differ their exact mini-batches are different.

## Gate and conclusions

At every probe, the runner evaluates three-digit DEV categories and four original historical DEV sets. Passing requires **all**:

- At least 80% strict 5% accuracy in each three-digit category.
- 100% exposed atomic table retention.
- At least 80% **in every category** of historical no-carry, carry, and signed DEV.

Checkpoint ranking maximizes the *worst normalized requirement* rather than DEV accuracy alone. `best.pt` is therefore not necessarily the checkpoint with maximum current-task DEV; it is the most balanced checkpoint for the research objective. `best_pass.pt` is written only when all gates pass. Neither result promotes HybridLink or Hawk–Dove certification.

A failure to pass even with protected rehearsal indicates that the unchanged digit-token backbone needs a more directed carry/generalization intervention. A pass would justify taking this checkpoint into a separate four-digit extension experiment. Neither outcome proves universal addition generalization.

## Run

Unzip the patch with `main_computer_test/` at the root of the patch archive into the current Main Computer repository root. Preserve your existing `tools/nanojev_addition_curriculum.py` and `tools/nanojev_addition_transfer_probe.py`, which this runner imports. Do **not** use the failing six-branch `best.pt` checkpoints as the source.

```text
python -m tools.nanojev_addition_carry_stability --signed-checkpoint "C:\Users\subsi\NanoJev\runs\nanojev_progressive_addition_v2\arms\digit_tokens\signed_passed.pt" --output "C:\Users\subsi\NanoJev\runs\nanojev_addition_carry_stability_v1" --device cuda --steps 2500 --probe-every 250 --batch-size 128
```

The report includes per-arm TRAIN, DEV categories, historical retention per category, and best checkpoints. Run completion prints `ADDITION_CARRY_SUMMARY`. Preserve the full JSON reports for analysis; the summary alone does not identify which historical skill is lost.

`--resume` adds steps while restoring model, optimizer, random states. Example: repeat the same command with `--steps 3500 --resume`. Other settings, the source checkpoint, and the runner source hash must remain identical. The output directory cannot be overwritten without an explicit new path.

## Regression tests

```text
python -m pytest -q tests/test_nanojev_addition_carry_stability.py
```

A GPU-only decoder parity test is skipped on CPU machines. CPU smoke tests use synthetic signed-pass-format checkpoints to test execution, not to claim trained-model performance. Existing checkpoints, HybridLink modules, and TEST are not modified.
