# NanoJev: third bank (absolute magnitude 160–191) with cumulative learned routing

The experiment adds an **independent MLP** for operand-magnitude band `160:191` to the **already accepted** three-artifact lineage:

1. Certified radius-127 zero-line backbone, including its frozen original sign/digit heads.
2. Promoted `128:159` absolute-magnitude MLP bank, still frozen.
3. Promoted learned base-versus-128:159 neural router, still frozen. Its accepted checkpoint was selected at step 600 in the earlier run.

A new bank and a new **neural gate** are trained. The new gate chooses between the *hard output of the frozen original neural router* (base or 128:159) and the new bank (160:191). Its decision uses only the frozen backbone's features and input-token embeddings. **No operand-magnitude rule, oracle expert label, correct sum, correctness check, fallback, or Python band routing is used in inference.** The correct sum is used only for supervised losses and evaluation, not bank selection.

## Run

From the repository root with the CUDA-enabled Python environment:

```text
python -m tools.nanojev_zero_line_cumulative_bank --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" --prior-bank-output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_v1" --prior-router-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_learned_router_128_159_v1" --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_cumulative_160_191_v1" --new-band 160:191 --device cuda --steps-per-bank 1500 --probe-every 250 --router-steps 2000 --router-probe-every 200 --batch-size 128
```

This runs specialist training and then router training in **one invocation**. It refuses to proceed unless the prior bank and prior neural router are promoted, match the original certified checkpoint by SHA256, and pass the saved eligibility check. It refuses to overwrite or nest an input artifact directory.

To test a **promoted** resulting system after the acceptance gates pass:

```text
python -m tools.nanojev_zero_line_cumulative_bank --phase predict --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" --prior-bank-output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_v1" --prior-router-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_learned_router_128_159_v1" --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_cumulative_160_191_v1" --device cuda --a 175 --b -159
```

`--phase predict` **refuses** an unpromoted new bank/router by default. `--allow-candidate` permits explicit diagnostic inference with rejected candidates (not production certification).

## What is trained / what is preserved

- The certified backbone and both original heads never change.
- The accepted first bank never changes.
- The accepted two-choice neural router never changes.
- The new magnitude 160–191 bank learns full signed sums, with balanced zero-line and non-zero residual examples, from frozen backbone activations.
- The new neural gate learns from the final signed-answer likelihood of the incumbent versus the new specialist. **It is never given the answer-derived right-bank ID.**
- The new gate uses **hard expert selection** at inference. An incorrect neural decision stays incorrect.
- Only candidate weights and reports are saved if the new stage fails.

## Separate reports and acceptance criteria

`result.json` includes isolated new-bank DEV/OOD accuracy, final learned-route DEV/OOD accuracy, selection rates, baseline-relative newly correct/newly wrong answers, near-zero errors, and a reference Python input-magnitude result that is **evaluation-only**. It also evaluates the next `192:223` magnitude region without training on it; this is an exploratory extrapolation diagnostic, not part of checkpoint selection.

**Promote only when all hold:**

1. The original 65,025-card center retains all correct answers. Its new-bank selection rate is at most 1%.
2. **Exhaustive 36,736-pair check** of previously accepted 128–159 region introduces **zero new errors**, and the new-bank selection rate is at most 1% there; the previous bank DEV and OOD cohorts also show zero new errors.
3. Isolated new bank DEV and magnitude-held-out OOD accuracies each reach at least 90%.
4. New learned-route 160–191 DEV and held-out OOD accuracies each reach at least 90%, with at least 90% neural selection of the new bank.
5. No near-zero prediction previously correct under the frozen incumbent becomes wrong on new DEV/OOD samples.
6. Learned routing is within two percentage points of an **evaluation-only** Python magnitude router on new DEV/OOD.
7. All three previous input checkpoint files are unchanged (SHA256); no external test split is consumed.

The old 128–159 DEV and OOD sample families are used for gate training/evaluation with the same predefined seeds and partitioning; the exhaustive prior-band check gives broader retention evidence. NEW OOD held-out maximum magnitudes are excluded from TRAIN and DEV. The separate 192–223 experiment is an extrapolation probe only. Repeated model decisions made using any reported OOD would require a new sealed final test set.

## Files

- `tools/nanojev_zero_line_cumulative_bank.py`: runnable experiment and hard-inference path
- `tests/test_nanojev_zero_line_cumulative_bank.py`: CPU regression and synthetic end-to-end tests
- `pretty_docs/nanojev_zero_line_cumulative_bank.md`: this guide

No changes to existing tools are required. CUDA training of the actual supplied checkpoints must be performed locally.
