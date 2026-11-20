# G2-0086 — Frozen Within-Type Causal Discrimination

## Purpose

The 0085 tracer established substantial over-admission. This experiment asks a
narrower question: for the *same predicted G1 source*, the *same destination
kind*, and the *same gold binding type*, why does G2 frequently rank an
incorrect destination above the correct one?

This is an **exploratory diagnosis**, not training or G2 certification.

## Run

Apply the ZIP at the `main_computer` root. From `main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_within_type_causal_discrimination_0086 --device cuda
```

Reads the prior run in `runtime/reports/gamer-v1`:

- `oracle-fabric-g2-native-training-0083` — sealed failed G2 run and frozen checkpoint.
- `oracle-fabric-g2-causal-activation-fuzz-0085` — report, cases and **raw pair-scores**.
- G1-0082 native certificate and G1-0081 composite checkpoint as referenced by 0083.

Writes a new directory (refuses overwrite):

`runtime/reports/gamer-v1/oracle-fabric-g2-within-type-causal-discrimination-0086/`

Containing `report.json`, `cases.jsonl`, `groups.jsonl`, `matches.jsonl`.
The last contains stage distances, readout margins and all causal intervention
results. With `--max-pairs-per-case 3` (default), exhaustive ranking is measured
for **all** groups but expensive activation interventions are run only on up to
three selected groups per prompt. Adjust only for exploratory follow-up, not
for retroactive certification.

## Locked method

The exact G2-0083 trained checkpoint is frozen; the experiment validates its
hash and 0085 evidence, and replays the 0085 prompts and logits before
producing conclusions. It uses the same native G1 predicted object spans;
those spans are held fixed for every within-prompt G2 intervention.

For each source / destination-kind / binding-label group:

1. Keep all gold-positive destination candidates in the group, excluding any
   other gold-positive destination from the negative set.
2. Compare the **weakest positive** against the **strongest negative** using
   the *same binding type's* `logit[class] - logit[none]` margin. This is a
   deliberately strict ranking test. Separately record whether *any* positive
   beats all negatives. Exact ties do not pass.
3. Compare destination vectors at the native projection, contextual encoder,
   G1→G2 coupler, and pair representation stages, as well as all four channels
   of the actual pair-projection input: source, destination, source×destination,
   and source−destination.
4. Perform reciprocal **same-kind** destination transplants at encoder and
   coupler (donor mean across destination tokens), plus single-channel and
   combined-channel transplants at the pair input. The cached pair-input path uses the exact frozen
   `pair_projection`→GELU→`binding` computation (unit-tested against full
   native forward passes), avoiding redundant encoder recomputation. Run sham and alternative
   wrong-destination controls. Report typed-margin movement, influence on
   other graph decisions, and how far target margins move toward donor margins.
5. Where both sides of an invariant fuzz pair have exact G1 extraction and
   stable author identities, assess ranking changes across renaming and role
   swapping. Exclude action removal and ambiguous alignments.

**Gold labels enter only after model inference** for diagnostic group matching,
ranking and donor selection. They are not inputs to G1/G2, the decoder, any
training loop, a threshold selector, or an acceptance certificate.

The full destination-dependent pair transplant should duplicate the donor
logits (same source), by construction; it is a **positive intervention control**,
not evidence of a deployable fix. Token-mean interventions can alter many pairs;
report collateral graph changes instead of treating them as isolated fixes.

A nonzero difference between two stage vectors is not proof that the network
uses that difference, and a causal effect after a donor transplant does not
establish that earlier information is semantically correct. Localization must
be interpreted using the combination of geometry, bidirectional
interventions, sham controls, negative donors and unchanged frozen weights.

No checkpoint or original evidence is modified. No G2 certification or G3 gate
is issued or unlocked. Training, threshold fitting and production promotion
are explicitly absent.

## Earlier evidence (before 0086 CUDA tracing)

Recomputed from 0085's 16 existing cases and original pair-score records:

- 314 matched groups; 113 strictly ranked all true destinations above false.
- Quantity→outcome: 24/88 with correct ranking.
- Quantity→action: 20/88 strictly ranked; 24/88 put at least one true
  destination above every negative.
- On 89 author-ID-aligned rank groups with exact G1 on both sides of invariant
  renaming / role-swap fuzz pairs: 11 changed rank status and 30 changed the
  identity of the hardest false candidate.

The model-level stage and intervention results are **not yet known** until the
user's CUDA invocation completes. Keep G2 and G3 gates unchanged.
