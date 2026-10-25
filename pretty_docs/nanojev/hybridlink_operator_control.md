# HybridLink learned-operator control (not a format-phase repair)

The original teacher-format and precision experiments pass in their ordinary regimes, but their continuous learned regression cannot meet strict 5% relative X/Y error near zero. A perfect exact-zero classifier does not fix the near-zero problem. This control asks whether an explicit, trainable bilinear operator structure and integer execution eliminate that obstacle in the same oracle-blind problem.

## Scientific boundary

This is an *ablation*, **not** a pass for HybridLink format, alignment, joint training, or autonomous computation. It does not evaluate learned causal-transformer state, teacher-token understanding, or independent numerical reasoning. The bilinear primitive is engineered from the known task grammar. The routing coefficients that select/add products are **learned from TRAIN X/Y** by a full-rank least-squares solution; they are not written as target-specific constants in source. A TRAIN-derived integer symbolic hypothesis is accepted only if every coefficient is sufficiently close to an integer and its TRAIN reconstruction has exactly zero error. Under that hypothesis, inference executes multiplication/addition with integer values reconstructed from visible count and payoff fields, with no oracle X/Y supplied.

The control reports **both** its unrestricted float64 fitted readout and quantized integer readout on unseen DEV payoff matrices. A difference on near-zero and exact-zero examples isolates representational precision and cancellation as distinct from learning the operator routing. TEST is never evaluated. An input-payoff intervention verifies that predictions change when only the visible payoff inputs are changed.

Neither the base Gaussian model, format checkpoint, precision checkpoint, nor existing acceptance gates are modified. The experiment requires a fresh, isolated output folder and refuses overwrite; reference SHA is verified before/after. Do not promote the experimental control's score to a HybridLink certification.

## Run from Main Computer repository

```powershell
& $py -m tools.nanojev_hawk_dove_hybridlink_operator_control `
    --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_operator_control_v1"
```

JSON report: `output/result.json`. Output labels are `PASS_CONTROL_ONLY` or `FAIL`, never an official format pass. The engineering decision after the run should be driven by whether the **learned float64** representation succeeds and whether quantization is necessary to remove exact-zero / near-zero failures. If quantization proves necessary, a continuous regression head is the wrong numerical authority for the original strict standard; a staged learnable operator/program head must be considered without silently hardcoding correct X/Y formulas.
