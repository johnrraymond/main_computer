# NanoJev HybridAgent v3 — typed numerical thoughts and two-pass self-conditioning

## Purpose

Replace the invalid arithmetic-as-count-feature teacher of v2 with a typed numerical thought vocabulary, establish teacher utility **before** latent generation, and enforce separate optimization boundaries for teacher bootstrap, residual HybridLink activation, and joint self-conditioned learning. No old trainer or checkpoint is replaced.

The experiment uses the existing **oracle-blind six numerical prompt bindings + tier** from the source checkpoint, all matrix-held-out splits and the original Gaussian density output family. Depth 0 calls the original reference model bitwise. The reference remains frozen for every stage. Depth > 0 uses a distinct, trainable Gaussian-location readout of the accumulated causal context; it does **not** add a correction to the reference's hidden answer.

## Stage 0: teacher bootstrap (new)

Train a causal-attention reasoning core, a typed thought encoder and an independent numerical readout on TRAIN.

The typed teacher roles are PRODUCT_HH, PRODUCT_HD, PRODUCT_DH, PRODUCT_DD, SUM_X, SUM_Y, SIGN_X, SIGN_Y. Each teacher token has its own role embedding and scalar-value embedding. Neither count/payoff feature slots nor hidden oracle labels are repurposed as input tokens. Training uses the correct original numerical labels only as **targets**, never as an inference input.

Both product-only (4-token) and all-eight-token teacher trajectories receive numerical supervision. **The gate tests only the first four tokens**: the partial products are enough in principle to compute X and Y without ever showing the final sums or signs. On DEV, correct product tokens must lower combined normalized X/Y mean absolute error more than both (a) the original frozen Gaussian and (b) tokens shuffled across examples. The default minimum relative improvement is strictly greater than zero (`--min-teacher-gain 0`). Fail closed otherwise. Strict per-tier 5% accuracy is also recorded but is not used as a bootstrap gate because it is discontinuous, especially for small fixtures. A `teacher_gate.json` includes all measured values.

Failure here means the model has not learned to **use correct intermediate thoughts**. Do not proceed to link activation or reinterpret a small alignment loss as useful reasoning. Run another experiment with more `--bootstrap-steps` or revised representation. Do not override the gate merely to obtain later stages.

## Stage 1: frozen-core residual HybridLink activation

Freeze the reasoning core, typed teacher embeddings and numerical answer readout. Train **only HybridLink** against the typed next-token embeddings from a teacher-conditioned prefix. HybridLink uses `h + MLP(h)`; the causal core sees the complete preceding context. Checkpoint the exact original bootstrap optimizer, link optimizer and joint optimizer. Require held-out alignment loss to decline, otherwise fail closed.

This differs from v2, which changed its target representation during link activation.

## Stage 2: two-pass HybridSFT-style numerical learning

1. Pass A: With correctly typed teacher prefix, generate the model's *predicted* latent tokens at each step. Predictive alignment to each typed target is supervised.
2. Pass B: Detach those generated predictions and use them—not the true teacher embeddings—as the input thought sequence. Supervise the final X/Y numerical density.
3. Independently run the same causal reasoning core with **fully self-generated autoregressive tokens**, and supervise its numerical output. This prevents a teacher-conditioned two-pass model from escaping the distribution it encounters at inference. Both core and HybridLink and the numerical readout train jointly; teacher embeddings remain fixed to avoid a moving target.

Use a fixed-depth curriculum (1/2/4/8). Learned THINK/ANSWER stopping is *not* included. No teacher token is supplied during normal `forward` inference. There is a debug-only `supplied_tokens` interface for separating teacher ability from autonomous generation; the standard evaluator never passes it.

## Commands

Install by dry-run then applying the zip from the repository's patch installer:

```text
python new_patch.py nanojev_gaussian_hybrid_agent_v3_typed_selfsft_patch.zip --dry-run
python new_patch.py nanojev_gaussian_hybrid_agent_v3_typed_selfsft_patch.zip
```

Train with the same trusted reference checkpoint as v1/v2, but use a **new v3 output directory**:

```text
python -m tools.nanojev_hawk_dove_gaussian_hybrid_agent_v3 --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v3" --device cuda --bootstrap-steps 4000 --warmup-steps 2000 --steps 12000 --probe-every 500 --batch-size 64
```

After a pause using `--bootstrap-stop-after`, `--warmup-stop-after`, or `--stop-after`, resume with **the same total step budgets and other settings**, using `--resume` and omitting the stop-after flag. The step count is the cumulative planned number; neither training stage nor optimizer state silently restarts. Altering training budgets requires a new output directory.

## Outputs and verification

- `config.json` + `matrix_split_manifest.json`: canonical identity and matrix partition.
- `teacher_gate.json`: correct/shuffled/zero teacher controls, continuous errors and strict hits, gate status.
- `activation.json`: frozen-core link activation evidence.
- `latest.pt`: all optimizer/model, CPU/CUDA and sampler RNG states, all stage counters and history.
- `best.pt`: best *trained* depth/step selected on DEV, never by repeatedly inspecting TEST.
- `report.json`: final DEV/TEST and disabled/shuffled/zero/repeated-first ablations, depth trajectories. TEST is exploratory because earlier work has already examined it.

Built-in CPU fixtures verify zero-step parity, all eight typed roles, no teacher entry into ordinary inference, frozen parameter boundaries, dual-pass numerical gradients, gate refusal, exact restarts from all three stages, and source SHA integrity.

## Scientific limitations

This remains a numerical transformer **analogue**, not the exact Qwen HybridAgent implementation. The teacher uses deterministic visible arithmetic derived from the six prompt-sourced numeric bindings. The original reference encoder remains frozen as the input embedding initializer; the newly introduced reasoning core and output head are trained from scratch. The explicit teacher gate can fail on a real 2,000-bank checkpoint; the development tests do not establish that it passes or that v3 improves held-out accuracy. True blind certification requires new, never-consulted payoff-matrix splits.
