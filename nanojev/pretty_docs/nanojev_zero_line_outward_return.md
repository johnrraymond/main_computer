# NanoJev: outward acquisition, then reverse zero-line conformity

## Purpose

Move a **single recurrent arithmetic backbone** rapidly through increasingly
large **signed residuals**, reaching the full possible sum ±4,000,000 for two
operands each in ±2,000,000, *without* waiting for full inner/history retention
at each doubling. Once the outward exposure campaign is complete, train the
same backbone back toward zero and attempt simultaneous conformity.

No new MLP banks, inference routing, arithmetic executor, hardcoded
magnitude-based inference choice, or ground-truth access at inference.
Exact integer arithmetic is used only to generate and score supervision.

The script is a new campaign, not a mutation of
`nanojev_zero_line_sampled_radius_ladder.py`. Earlier radius-127, zero-only,
±16, and ±32 source checkpoints are read-only. Earlier ±16/±32 failed TEST
results remain unchanged and are not retrospectively certified.

## Phases and advancement

- **Outward (`--phase outward`, default `--outward-policy exposure`):** 17
  stages: 64, 128, 256, …, 2,097,152, 4,000,000. Each stage receives a
  fixed `--stage-steps` budget (default 750) and advances on completion,
  recording true DEV inner exact, outer 5%-relative, separately guaranteed
  frontier 5%-relative, exact-zero, and historical probe results. Old
  capabilities may temporarily regress. The saved **exposure milestone**
  proves the training stage ran, *not* that the new radius was mastered.
  `--outward-policy demonstrated` requires outer/frontier DEV gates and
  improvement before advancing; `--stage-steps` can be raised with `--resume`.
- **Return (`--phase return`):** requires every outward exposure/competence
  milestone through the target. Descends 4,000,000, 2,097,152, …, 64,
  32, 16, 0. Each nonfinal stage executes its full `--return-steps`
  budget (default 1,500), concentrating on its current shell while mixing
  original signed-neighborhood rehearsal, exact-zero examples, broad
  large-residual rehearsal, and original arithmetic. Intermediate reverse
  milestones are not certifications.
- **Full conformity, final return-0 stage:** only here do we gate on
  canonical exact zero (all observed zero cards), ≥99.9% exact inner ±32,
  ≥90% 5%-relative outer accuracy, ≥99% sign accuracy on *both* DEV views,
  **all 65,025 original arithmetic pairs exact with no false-zero**, and
  both prior zero-only historical probes. After qualifying DEV and history,
  a sealed TEST fold is opened **once**. A failed sealed TEST permanently
  refuses this campaign; it cannot be reused to pick another checkpoint.

Outer and return stages use separate TRAIN and DEV magnitude-family folds.
The prior ±16/±32 TEST/quarantine magnitude families are embargoed from
training. Final TEST is not statistically pristine because earlier experiments
have already informed this research; call a pass *sampled research conformity*,
not a proof of integer arithmetic on all possible inputs. The exact
±4,000,000 endpoint can be tested separately; it is deliberately not a
TRAIN example because magnitude 2,000,000 is embargoed. The radius is the
requested coverage bound, not proof that every possible extreme sum is learned.

## Source and provenance

The normal seed is the original ±32 `latest.pt` research checkpoint recorded
as 20,270/20,280 with exact zero and full historical retention. The loader
verifies its origin hashes/status, and does not promote the old failed holdout.

**Optional accelerated warm start:** Stop the currently running *old*
`nanojev_zero_line_sampled_radius_ladder` process first, after a saved probe.
If its ±64 stage has **not opened TEST**, pass:

```
--warmstart-stage "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_sampled_radius_ladder_v1\stages\radius_64"
```

This imports its `latest.pt` **weights** (not its optimizer), validates
its ±32 parent hash and original campaign config, and then trains the new
outward ±64 stage. A running/changing source, source mismatch, or sealed
old ±64 stage must not be reused. If the old stage has opened TEST, omit
`--warmstart-stage` to start cleanly from the ±32 research checkpoint.

The choice of warm start is recorded in the new campaign config with its SHA256
and checked on every resume; do not change or continue training the old
checkpoint after starting a campaign from it.

Each stage records source/parent hashes, DEV baseline, actual probe reports,
trainer+optimizer+RNG snapshot `latest.pt`, and immutable staged acceptance
hashes for advancing. `--resume` is required for an existing campaign.
`--max-stages` / `--stage-steps` / `--return-steps` may be increased without
changing the campaign's statistical/training configuration.

## Commands

Inspect the stage schedule:

```text
python -m tools.nanojev_zero_line_outward_return --phase plan --target 4000000
```

Run outward acquisition (the **same paths and warmstart choice** must be
supplied again on resume and on the return command):

```text
python -m tools.nanojev_zero_line_outward_return --phase outward --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" --zero-only-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_zero_only_v1" --r16-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius16_v1" --r32-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius32_v1" --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_outward_return_v1" --device cuda --target 4000000 --outward-policy exposure --stage-steps 750 --max-stages 17
```

Then run reverse consolidation by changing only the phase and adding `--resume`:

```text
python -m tools.nanojev_zero_line_outward_return --phase return --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" --zero-only-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_zero_only_v1" --r16-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius16_v1" --r32-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius32_v1" --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_outward_return_v1" --device cuda --target 4000000 --outward-policy exposure --stage-steps 750 --return-steps 1500 --max-stages 20 --resume
```

If outward stops at a numerical issue, address the issue before continuing.
In `demonstrated` mode, `budget_exhausted` is a safe stop; extend
`--stage-steps` and run with `--resume`. For the final return-0 DEV failure,
increase `--return-steps` with `--resume` while TEST remains unopened.

## Honest interpretation

- `ZERO_OR_STAGE_ACCEPTED` and `exposure_milestone` do **not** assert old
  arithmetic retention or significant accuracy at that radius.
- `ZERO_OR_PROBE` shows real inner, outer and frontier accuracy and historical
  probe regressions. Earlier abilities may be temporarily lost.
- `ZERO_OR_FINAL_TEST` is emitted **only after** final DEV and exact historical
  checks. Failed TEST is sealed; no automatic retry over the same cases.
- `ZERO_OR_SUMMARY status=outward_exposure_completed` means large-residual
  exposure finished; it does **not** mean ±4M arithmetic is certified.
- Results from both phases are sampled research, not a mathematical theorem;
  run an independently designed fresh certification campaign for broad claims.
