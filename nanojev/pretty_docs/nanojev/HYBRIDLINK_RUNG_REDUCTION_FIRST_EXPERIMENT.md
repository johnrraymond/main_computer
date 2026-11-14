# HybridLink rung reduction: first supervised experiment

Status: **experimental**; not a certified QW5 Stage 2 deployment.

## Hypothesis

The link is more useful if trained to map a difficult question to the exact next
lower question, not to hallucinate an answer directly. The initial inspectable
prototype is:

`T08 question → B0b question → B0a question → T0 arithmetic question → ANSWER`

**B0b and B0a are experimental intermediate labels**, not a claim that they
already exist in the historical certified ladder. This first experiment tests
that reduction concept, not all historical T00–T08 transitions.

For a case with opponent Hawk count A and opponent Dove count B, the constructed
payoffs obey HH-DH = +1, HD-DD = -1, hence Hawk-minus-Dove = A-B. Each forward
or reversed prompt preserves this sign and amount through all four steps.
All transition targets are generated once by a deterministic teacher; the
teacher's lower questions or answer **are never substituted into an autonomous
rollout**. If the model fails a reduction, that chain stops. No external
arithmetic executor is used at inference. The Python generator uses arithmetic
solely to supply supervised training targets and evaluation oracles.

## Program and source dependencies

`tools.nanojev_hybridlink_rung_reduction` uses the installed Qwen3-0.6B Base,
its verified baseline adapter, zero-line LoRA, HybridLink activation head, and
format-activated LoRA through the already installed
`tools.nanojev_hybridlink_prompt_learning_probe` source/provenance loader.

Two **new**, separate model lineages:

1. `train-text`: freeze Qwen base and current HybridLink head. Train Qwen's LoRA
   against the *next-prompt text* target, never against a direct answer except
   for the terminal T0→ANSWER hop. This establishes the **output contract**.
2. `train-link`: initialize from `train-text/best.pt`; freeze the trained LoRA
   and Qwen. Train **one actual HybridLink latent step** using the same one-hop
   next-prompt cross-entropy after the existing `<latent> ... </latent>` protocol.
   This specifically tests whether trainable feedback can help produce the
   next prompt instead of spoiling generation.

Neither stage touches the already accepted QW5 or HybridLink source files.
Both write their own `run_config.json`, `best.pt`, `result.json`, and SHA witness.
Only DEV teacher-forced loss selects a checkpoint; the TEST split is untouched
until explicitly requested, and is one-shot guarded per checkpoint directory.

## Apply

Run in the main_computer repository using the same `new_patch.py` ZIP installer
as prior NanoJev patches.

## Preview with no model load

```powershell
& $py -m tools.nanojev_hybridlink_rung_reduction `
  --stage preview --device cpu `
  --band 0-4 --scales 8,16,32,64,128,256
```

The preview prints all four exact teaching pairs of the first TRAIN case, split
sizes, and a preview witness. It does not load model weights or run training.

## Stage 1: learn the output language and one-hop question reduction

```powershell
& $py -m tools.nanojev_hybridlink_rung_reduction `
  --stage train-text --device cuda `
  --band 0-4 --scales 8,16,32,64,128,256 `
  --steps 600 --eval-every 50 --lr 0.00001 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_v1'
```

Start with this limited 0–4 band before scaling to 4M. The full curriculum's
outer loop can later double input scales, keeping all prior conditions as
retention probes, once this short chain demonstrates competence.

## Stage 1 DEV one-hop and free-chain probe (no TEST)

```powershell
& $py -m tools.nanojev_hybridlink_rung_reduction `
  --stage probe --device cuda --split dev `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_v1\best.pt' `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_dev_v1'
```

## Stage 2: one learned HybridLink latent step

```powershell
& $py -m tools.nanojev_hybridlink_rung_reduction `
  --stage train-link --device cuda `
  --band 0-4 --scales 8,16,32,64,128,256 `
  --text-checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_v1\best.pt' `
  --steps 300 --eval-every 50 --lr 0.0001 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_link_v1'
```

## Stage 2 DEV one-hop and free-chain probe

```powershell
& $py -m tools.nanojev_hybridlink_rung_reduction `
  --stage probe --device cuda --split dev `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_link_v1\best.pt' `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_link_dev_v1'
```

### Interpret

Reports contain `one_hop_by_rung` for each condition and `full_chain` for the
free-running chain. Each evaluation probes `text_only`, `zero_latent(1)` and
`intact(1)`. Match the same DEV examples and source model. A useful link must
increase **intact versus zero-latent** accuracy on the next-prompt output,
without losing text-only ability on already solved problems. A passing hop is
strict: the expected bracketed target-rung tag, exact canonical simplified
question, and `END_RUNG`. A correct second attempt after a wrong first hop
is never credited; extra text is logged separately. This initial exact-output
scorer intentionally marks free-form paraphrases as failures.

Caution: a high teacher-forced DEV likelihood is **not** proof of free-running
reduction or useful feedback. `TRAINED_UNCERTIFIED` means that actual DEV
free-chain and held-out TEST results have not yet established the hypothesis.

### Independent TEST (after checkpoint selection)

Run `--stage probe --split test --checkpoint ... --output ...` with a new empty
output path. Do not tune on these results. The test call writes
`test_consumed.json` in the checkpoint's run directory to prevent accidental
repeated testing of that lineage.

### Known research boundaries

- This first experiment trains only the short four-hop chain, not all existing
  semantic rungs, and does not update QW5 Stage 2.
- The first stage is **text** supervised learning; the second is genuine
  single-step latent-to-text supervision with frozen Qwen. A successful text
  model alone does not establish HybridLink utility.
- DEV/TEST scenarios use distinct count/payoff/direction tuples, but bounded
  signed deltas repeat across splits; this is *not* blind interpolation to unseen
  residual values outside 0–4.
- No full-sized CUDA training was run in the patch-authoring environment.

## Probe responsiveness repair (October 27, 2026)

The original probe prints nothing after Qwen loading until the entire DEV
comparison finishes. For 12 DEV cases, three controls, and four supervised
hops + up to four autonomous hops, this can require 180–288 **serial** text
generations. Each of those could generate up to 128 tokens because `END_RUNG`
was not a decoder stop condition. Silence after `Loading weights` did **not**
prove a deadlock, but it was poor diagnostics and could waste substantial GPU
compute.

The repaired probe offers:

- `RUNG_PROBE_BEGIN`, `RUNG_PROBE_CALL`, `RUNG_PROBE_PROGRESS` flushed to stdout.
  Default `--progress-every 1` prints the start and completion of every decode
  so a stalled call can be identified. Use `--progress-every 4` for less logging.
- `--case-limit N`: **DEV-only**, selects the first N cases from the exact
  original saved deck. Its result is `subset_probe=true` and MUST NOT be
  reported as full DEV accuracy. The TEST split cannot be partially consumed.
- `--stop-at-end-rung`: optional greedy-decoding acceleration that stops when
  a complete `END_RUNG` line is generated. The model, checkpoint, latent path,
  answer label, and training objective are unchanged. Unlike unrestricted
  decoding, this intentionally does not measure post-terminator extra text.
  Leave this option off for strict historical comparisons of trailing output.
- No checkpoint overwrites. Every probe needs a new empty result directory.

**Quick end-to-end DEV smoke** (one case, one control, 4–8 inference calls):

```powershell
& $py -m tools.nanojev_hybridlink_rung_reduction `
  --stage probe --device cuda --split dev `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_v1\best.pt' `
  --controls text_only --case-limit 1 `
  --stop-at-end-rung --progress-every 1 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_smoke_v2'
```

**Full DEV diagnostic with feedback controls:**

```powershell
& $py -m tools.nanojev_hybridlink_rung_reduction `
  --stage probe --device cuda --split dev `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_v1\best.pt' `
  --controls text_only,zero_latent,intact `
  --stop-at-end-rung --progress-every 1 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_dev_v2'
```

`RUNG_PROBE_RESULT` supplies separate `one_hop_by_rung`, `full_chain`,
`selected_cases`, `available_cases`, `generation_calls`, and `subset_probe`.
Stopping an old in-progress probe with Ctrl+C does not alter `best.pt`; the
original implementation only writes result files after the full probe completes.

Important: the original exact-text evaluation remains intentionally strict.
A semantically equivalent but differently worded lower question is not credited.
