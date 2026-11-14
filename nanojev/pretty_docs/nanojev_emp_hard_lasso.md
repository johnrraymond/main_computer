# Progressive readout-channel LASSO: non-hybrid Hard EMP

## Question and experimental distinction

Does gradually concentrating the computation in the trained Hard EMP's arithmetic
readouts preserve or improve *unseen hard-case* and high-magnitude signed
zero-line computation, compared with an equally trained unrestricted clone?

This is an **offline, independent experiment**. It **does not modify** the four
ordinary EMPs, reverse/outward acceptance, the detector's weights, the original
Hard EMP, or any previously saved checkpoint. It does not introduce a router,
external arithmetic calculator, or extra computation head.

Both arms are cloned from the **existing hard.left specialist** in
`hard_emp_frontier_v2/latest.pt`, including its AdamW optimizer moments:

- **Dense control:** hard-case cross entropy, no pruning and no sparsity penalty.
- **LASSO arm:** exactly the same sampled hard cases and optimizer steps;
  gradually increasing structured group penalty on each common sign/digit
  readout input channel, plus staged permanent masking of the least-weighted
  readout channels.

The mathematical readouts in `AdditionNet` are `sign_head` and `digit_head`,
which are **linear projections** of the final normalized backbone state. We
therefore prune **shared channels of this state as received by these two
readouts**, *not* arbitrary interior Transformer neurons. The frozen detector
is unchanged. The primary Hard EMP and its cloned control/sparse experiments
remain fully trainable, including their backbones.

The group penalty is `lambda * mean_j sqrt(sum(sign_weight[:,j]^2) +
sum(digit_weight[:,j]^2))`, summed over the **currently active** channels.
Masking a feature sets its input to zero for both readouts. Its index is
recorded and never regrown. Increasing group pressure alone does not guarantee
an exact zero, so discrete masking makes the sparsity test falsifiable.

## Train, test and attribution

The harness reads prior TRAIN-family `training_pairs` and held-out
`validation_pairs` from existing accuracy-probe evidence. No held-out answers
are optimized upon. It rejects cross-split operand collisions; reports the
hash of each input evidence file; and uses a fixed independent signed/zero
held-out magnitude deck using the project's DEV-family magnitude partition.
The latter is a generalization probe, **not** a claim that no numerical
magnitude was ever encountered by an earlier large-scale curriculum.

At each sparsity stage both arms receive the same batches, each for exactly
`--steps-per-stage` optimizer updates. The fixed detector supplies a paired
hard/easy offline combined-accuracy comparison. Arithmetic-only accuracy is
reported on held-out hard examples and the signed near-zero magnitude probe.
Because the former is balanced, positive combined accuracy does not establish
improvement on a naturally distributed, rare-hard sample.

After each stage, the experiment disables selected **retained** channels one
at a time, measures the change in held-out hard-case accuracy, and restores
them. These are causal **readout-channel interventions** for the trained
checkpoint, not proofs of the functions of individual backbone neurons.

Default mask schedule: 0%, 2%, 5%, 10%, 20%, 35%. Each level adds a little
more sparsity; if results worsen, the prior stage checkpoint remains in the
experiment history, but the training experiment continues to measure the full
response curve. No stage is silently declared a success based on TRAIN loss.

## Command (run AFTER the completed reverse curriculum)

```powershell
& $py -m tools.nanojev_emp_hard_lasso `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_hard_lasso_v1" `
    --device cuda `
    --sparsity 0,0.02,0.05,0.10,0.20,0.35 `
    --steps-per-stage 250 `
    --batch 64 `
    --lambda-max 0.001 `
    --max-train 8192 `
    --max-dev 1024 `
    --ood-min 65536 --ood-max 131072 `
    --detector-gate 0.55
```

The isolated experiment writes `config.json`, `latest.pt`, `history.json`,
and `result.json` under `--output`. Interrupt at a completed
`EMP_HARD_LASSO_STAGE=` line and rerun the same command to resume. Source and
evidence hashes/configurations are checked; changes fail closed. No command
switches the original reversed campaign back to outward training.

## Interpretation

Compare equal-step sparse/control held-out arithmetic scores and paired
combined accuracy, not merely training loss. The zero-penalty initial stage
is an experimental parity check. A sparse arm beating the control on
independent magnitude/residual pairs indicates possible generalization gains
from concentration; it does **not** alone establish sparse causal circuits.
Ablation scores quantify causal contributions of the remaining readout input
channels, and should be repeated at multiple seeds if they appear stable.
