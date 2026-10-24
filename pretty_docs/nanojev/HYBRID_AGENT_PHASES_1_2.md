# NanoJev Gaussian Hybrid Agent — Phases 1 and 2

## Experimental claim

A **single frozen** oracle-blind Gaussian numerical model can potentially improve
its X/Y predictions by consuming multiple learned hidden-to-input feedback
vectors before emitting its Gaussian-bank prediction. This is a numerical
**analogue** of HybridAgent, not the original transformer implementation: no LM
head, token generation, learned mode switch, attention KV history, or Qwen
training is included.

The invariant is: **depth=0 calls the original reference model verbatim**.
The pretrained model's weights and original `best.pt` never change.

## Installation

Apply the zip from the root of `main_computer` using the repository's patch
installer. Only three new files are included in the patch.

## Run

Using the Python executable in the diagram environment:

```text
python -m tools.nanojev_hawk_dove_gaussian_hybrid_agent \
  --reference-checkpoint 'C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt' \
  --output 'C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v1' \
  --device cuda --warmup-steps 2000 --steps 12000 \
  --probe-every 500 --batch-size 64
```

The backslash line continuations above illustrate argument grouping; on
Windows use a **single line** (or your existing Python-launcher convention).

Resume a paused/interrupted run with exactly the **same configuration**, adding
`--resume`. Training steps are a *planned total*; changing `--steps` would
change the 1/2/4/8 curriculum, so changing it is intentionally refused.

To perform a checkpointed pause for an experimental smoke test, start with
`--stop-after 1000` (Phase 2) or `--warmup-stop-after 500` (Phase 1). Resume
without the pause flag and with `--resume`; optimizer, samplers, CPU/CUDA RNG,
training history and model weights are restored.

## Phase 0: exact baseline

The pretrained `BlindDensityNetwork` is loaded and frozen. The original
14-element oracle-blind numerical input is recovered by the existing
`parse_blind` pipeline, which validates that every numeric feature was actually
visible in the prompt. Matrix split SHA identities and numerical domain must
match the pretrained checkpoint.

At **zero latent steps**, inference executes the pretrained forward method
without modifications. A bitwise preflight check guards this invariant.

## Phase 1: learned feedback activation

Let `E(x)` be the original encoder's input embedding, `H` its hidden state,
and `L(H)` the newly trained `HybridLink` (128D at standard configuration).

The four arithmetic teacher features are the *visible* terms

- `a_fraction * hawk_hawk_payoff_scaled`
- `b_fraction * hawk_dove_payoff_scaled`
- `a_fraction * dove_hawk_payoff_scaled`
- `b_fraction * dove_dove_payoff_scaled`

They do not include oracle X/Y targets. A fixed, seeded projection maps these
four values into the 128D input-embedding space. HybridLink is optimized by
MSE plus cosine alignment on TRAIN. DEV alignment loss is checked against the
initial zero-link loss; failure stops the run before Phase 2.

The teacher vector is never provided at inference or as the recurrent input in
Phase 2. This is numerical intermediate-feature alignment, **not** token
embedding alignment or an imported HybridAgent teacher.

## Phase 2: self-conditioned recurrent learning

For each recurrence, the model generates `L(H_t)` and constructs

```
next_input_embedding = original_input_embedding + L(H_t)
H_(t+1) = SiLU(original_shared_hidden_layer(next_input_embedding))
```

Every recurrence calls the same frozen hidden-layer weights and the same
trainable HybridLink. The initial input facts remain available. No oracle
teacher vectors are fed during this loop. The final hidden state reaches the
original 2,000-bank Gaussian decoder, and X/Y are decoded from the density.

The curriculum increases *forced* recursion depth from 1 to 2, 4 and 8 over
the planned Phase 2 training steps. The training objective includes the original
Gaussian density loss plus strict-relative numeric loss and a weak arithmetic
alignment regularizer. Gradients flow through **all unrolled recursive steps**
into HybridLink; the pretrained encoder/decoder remains frozen.

There is no adaptive THINK/ANSWER decision in Phases 1 and 2: inference emits
X/Y after the specified fixed depth. Adaptive stopping belongs to Phase 3.

## Evaluation, negative controls, and reports

- `HYBRID_ZERO_STEP_PARITY=PASS`: exact source-model forward equivalence.
- `HYBRID_ACTIVATION_GATE=PASS`: DEV teacher-target alignment improved.
- `HYBRID_SELF_CONDITIONING=ACTIVE`: actual generated vectors recur.
- Each probe logs DEV min-tier all-masks joint X/Y within 5% for depths 1/2/4/8.
- `best.pt` stores only **trained** HybridLink candidates, selected on DEV.
- `latest.pt` stores full-state resumability, including optimizer states and RNG.
- `activation.pt` stores the completed Phase 1 link.
- Final report evaluates TEST only **after** DEV selects best depth and step.
- Causal controls disable feedback or shuffle it between batch examples. A
  depth improvement without an intervention effect is *not* sufficient to
  claim useful latent thought.

This experiment reuses payoff-matrix splits that were previously examined,
therefore TEST is **exploratory**, not a new final blind certification. A
successful result must later be retested on entirely unseen payoff matrices.
The 80%-per-tier, 5%-relative joint-X/Y target remains unchanged.

## Tests

```text
python -m pytest -q tests/test_nanojev_hawk_dove_gaussian_hybrid_agent.py
```

The tests include source-file immutability; original output exactness;
teacher computation from visible fields only; multi-step backpropagation;
zero/shuffled feedback interventions; smoke training; and exact equivalence
between uninterrupted and paused/resumed runs in both phases.
