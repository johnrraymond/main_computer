# Cumulative 160–191 bank: learned-gate repair

Purpose: repair the **five previously correct 128–159 predictions** lost by the third neural gate, without changing or retraining the certified backbone, the promoted 128–159 specialist and router, or the trained 160–191 specialist.

This is **not** a retrain of the 160–191 bank. It warm-starts from the `gate_candidate.pt` and `bank_candidate.pt` written by `nanojev_zero_line_cumulative_bank` and trains only the new final learned gate.

## Run (from repository root)

```powershell
& $py -m tools.nanojev_zero_line_cumulative_gate_repair `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --prior-bank-output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_v1" `
  --prior-router-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_learned_router_128_159_v1" `
  --cumulative-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_cumulative_160_191_v1" `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_cumulative_160_191_repair_v1" `
  --device cuda `
  --steps 1200 `
  --probe-every 100 `
  --batch-size 128
```

Every 100 training steps, it performs an **exhaustive historical check** of the 65,025 original ordered pairs and all 36,736 ordered pairs from 128–159. A candidate cannot be promoted if it changes even one formerly correct historical answer. The old-band holdout is also checked at final certification.

### Specific adjustments from the failed third-gate training

1. **Hard prior-region replay:** train using old-region cards where the frozen incumbent predicts correctly and the 160–191 candidate bank predicts incorrectly. This hard-negative sampling uses labels **during training**; gold sums and expert correctness are never supplied to the gate's inference input.
2. **Conservative new gate:** initialize with a negative output-bias shift, then apply a margin penalty against unnecessary switching on historical TRAIN examples while retaining end-to-end final-answer mixture likelihood for new-band inputs.
3. **Exhaustive retention for checkpoint selection:** check the complete historical 128–159 shell and entire certified base at each probe, not just sampled DEV. The sampled **new-band OOD** is evaluated only after selection and does not influence training or checkpoint choice. The exhaustive old-band check necessarily includes old-band held-out magnitudes, so those old-band examples are **not** an independent routing-generalization test. They are a known historical retention regression suite, not evidence of fresh transfer.

The unchanged sources are verified by SHA-256 before and after training. The new output directory must be empty and disjoint from all source directories.

### Acceptance

- No newly wrong predictions compared with the frozen incumbent over **65,025 original cards** and **36,736 prior-bank cards**.
- No newly wrong examples in the prior-bank held-out sample.
- New 160–191 DEV and OOD exact accuracy at least 90%, neural new-bank selection at least 90%, and within two percentage points of the input-magnitude *evaluation reference*.
- Source specialist OOD at least 90%.
- Inference uses only the two learned hard gates and frozen models, never the Python magnitude selector or a correctness-derived fallback.

If no gate satisfies all requirements, `eligible=false` and **only candidate checkpoints** are written. `bank_promoted.pt` and `gate_promoted.pt` appear only if the complete gate passes.

### Outputs

`progress.json`, `latest.pt`, `result.json`, `bank_candidate.pt`, `gate_candidate.pt`, and (only on acceptance) `bank_promoted.pt`, `gate_promoted.pt`. The original cumulative run and the three incumbent artifacts are not modified. A separate `--phase predict` loads only the accepted promoted pair by default; `--allow-candidate` explicitly opts into diagnosing a rejected pair.

### Research boundary

This proves or refutes **preserving earlier predictions while adding a learned third choice** within this numerical environment. It does not certify arbitrary-magnitude arithmetic, nor is supervised hard-negative mining an inference-time oracle.
