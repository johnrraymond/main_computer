# NanoJev — two EMPs × two independently trainable full backbones

## Research hypothesis

A pair of independently fine-tuned numerical backbones per EMP may expose different error distributions and complementary solutions on the same held-out arithmetic cards.  These are **four complete AdditionNet models**, not four output readouts attached to a shared body.  The experiment does not modify the existing outward ladder, its checkpoints, or its sealed test.

```
source population checkpoint(s)
    left EMP shared body + left EMP sign/digit readout
        -> EMP left / bank left   (fully independent)
        -> EMP left / bank right  (fully independent)
    right EMP shared body + right EMP sign/digit readout
        -> EMP right / bank left  (fully independent)
        -> EMP right / bank right (fully independent)
```

At prefill time, the original population only has ONE shared backbone. The two EMPs' different readouts are preserved, and their respective source body weights are copied. After prefill every body, input encoder, and readout has distinct parameter storage and a distinct optimizer. A check refuses any shared parameter identities. Both banks of an EMP initially have identical weights; asymmetric training controls make their trajectories diverge.

### Important historical-source limit

The previous `nanojev_emp_population` saves `latest.pt` and `history.json`, not an independently retrievable checkpoint for every historical DEV best per EMP. Therefore the default prefill is **the latest available saved weights**, not an invented historical optimum. If you have preserved earlier `.pt` snapshots, select them independently with `--left-checkpoint` and `--right-checkpoint`. The runner pins byte-exact copies as `source_left.pt`, `source_right.pt`; future mutations of the old population will not modify the new run. If a historical peak exists only in `history.json` and was not checkpointed, its weights cannot be reconstructed from that log.

## Answer-only training and left-from-right update

Every bank predicts the arithmetic result independently and receives supervised sign + seven decimal-digit cross entropy **against the actual sum on TRAIN cards**. The right bank's decoded prediction is *never* an imitation target or a gradient source for the left bank.

The within-EMP right-to-left relation is implemented as **TRAIN-card prioritization**: when the right bank's numeric answer is closer to the true sum than the left's, multiply that card's left loss weight by `right_answer_boost`. Both are compared only against the known **TRAIN** answer. If both are wrong, each still trains independently against the gold sum. No shared-parameter gradients, direct distillation, privileged intermediate calculation, or inference-time oracle. There is no required right-from-left coupling; right updates directly from its own answers. There is no teacher-based historical `legacy_loss` in this research branch.

To make the four trajectories independently tunable, each bank exposes:
- `backbone_lr` and `readout_lr`: optimizer rates for body/input and sign/digit output readouts.
- `train_every`: update frequency (e.g. 2 means every second global batch; useful for asymmetry without just rescaling Adam's loss).
- `frontier_boost`, `zero_boost`, `repeat_boost`: TRAIN-only card weights.
- `right_answer_boost`: additional left-bank weight where the paired right bank produces a better answer; must remain 1 for right banks.

`repeat_boost` refers to **adjacent repeated decimal digits in integer operands**, not actual lexical-language repetition. A lexical EMP requires a token-capable input and dedicated lexical supervision.

## Launch (separate branch from your EMP population step 4000)

Apply the ZIP contents at the repository root (ZIP itself is rooted at `main_computer_test/`, as with previous patches). From `C:\Users\subsi\main_computer`:

```powershell
& $py -m tools.nanojev_emp_dual_bank `
    --source-population "C:\Users\subsi\NanoJev\runs\nanojev_emp_population_512_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --radius 512 `
    --device cuda `
    --steps 2000 `
    --probe-every 250
```

Initial conditions: left EMP banks emphasize frontier and optionally learn from their right bank's *answer advantage*; right EMP banks emphasize broad arithmetic with less frontier preference. All four receive real arithmetic targets.

### Tune during running or after stopping

```powershell
& $py -m tools.nanojev_emp_dual_bank_tune `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --bank left.left `
    --right-answer-boost 10 `
    --frontier-boost 12 `
    --backbone-lr 0.00001
```

Each tune atomically increments the control revision. The trainer reads it at the next probe boundary, saving the effective step. To resume for 1000 more steps, repeat the launch with `--steps 3000 --resume` (all other args the same). `--steps` is **total** training steps, not additional. CUDA running memory is approximately four times a single full backbone plus optimizer/activations. Test initially with `--batch-size 32` if GPU memory is limited; new run configs are immutable, so choose batch size at creation. Running four full backbones simultaneously is expected to be slower than the old shared-backbone population.

## Reports and safeguards

The runner writes `history.json` (four per-bank distributions, per-EMP paired correct/wrong counts and tipping cases), `histogram.svg` (four comparative signed prediction/target histograms), `controls.json`, copied source snapshots/config, and `latest.pt` (all four models and optimizer/RNG states). The DEV cards are deterministic and equal across banks, and do not supply training gradients.

The diagnostic `qualified_80_50` checks the **left bank of the left EMP** at 80% and the **left bank of the right EMP** at 50%, on frontier and broader outer arithmetic in both DEV views. This diagnostic does **not** confer arithmetic certification. The other two banks' performance and their within-EMP disagreement remain separately recorded; no inference router is installed. All final model assertions require a separate unseen TEST evaluation.

The run refuses unexpected source schemas, source experiment radius mismatches, control schema drift, missing source checkpoints, modified pinned source snapshots, checkpoint schema drift, and source-code configuration changes on resume. CPU smoke tests validate output independence, feedback directionality, per-bank optimizers, source snapshot portability, live tuning, and resume.
