# NanoJev signed zero-line radius 16 — backbone training campaign

## Goal

Expand the previously promoted **zero-only** backbone into the exact signed
residual interval `-16,-15,...,-1,0,1,...,15,16` (33 integer targets), for
valid operands in `[-2,000,000,+2,000,000]`.

The backbone must compute `A + (-A + delta) = delta` exactly for every sampled
signed delta in every magnitude band. **It is not sufficient to recognize
nonzero or merely hit the endpoints ±16.** There are 13 magnitude bands,
therefore the certification matrix has **33 × 13 = 429 separate cells**, all
required to be populated and 100% exact to pass. Swapped operand order is
included. Canonical output sign and all seven decimal digits must match.

This is a standalone backbone finetune starting from `zero_only_v1/promoted.pt`
not the radius-127 checkpoint. It freezes the input digit encoder and output
heads and trains recurrent backbone/norm/query/position. No extra specialist,
inference router, arithmetic executor, target-dependent feature or Python
selection is added. The old zero-only checkpoint and previously certified
bank chain are not modified.

## Zero retention / historical retention

Training uses **explicit exact signed residual supervision** (unlike the
previous phase's zero-versus-nonzero training), with a configurable additional
weight for exact zero. It also rehearses the original radius-127 arithmetic
using supervised labels plus distillation against the frozen certified original.
The promoted zero-only checkpoint is the starting model and frozen reference
for audits; its prior DEV and TEST magnitude probes are used as *historical*
zero/nonzero retention checks in this new phase, not as new blind tests.

During evaluation:

- All 429 signed-residual × magnitude DEV cells must be **exactly** correct in
  each of `--stability-probes` consecutive probes.
- The historical 65,025 ordered arithmetic cards for [-127,127]^2 must be
  exactly retained. Sampled legacy rehearsal checks run on every probe; the
  exhaustive audit runs only after an apparently passing DEV streak.
- The original zero-only model's prior DEV and TEST probe populations must
  continue to have **100% canonical zero** and **100% nonzero rejection**.
- Only after all those conditions pass is the new phase's *sealed TEST*
  examined **once**. Every one of its 429 cells must be populated and 100%
  exact. Any failure or interrupted sealed TEST permanently disallows resume
  into that campaign; `promoted.pt` is never written on failure.

DEV and TEST magnitudes for this **phase** are partitioned by the true maximum
absolute input operand magnitude. All sign and operand-swap variations of an
absolute magnitude stay in the same partition. The endpoint 2,000,000 is in
TEST. The small original radius-127 interval has been seen by the prior model
and is strictly **historical retention**, never claimed to be novel generalization.
The source model was also previously exposed to some larger magnitude families:
phase TEST is held out from *this stage of training*, not from all earlier
research. Perfect accuracy here is sampled evidence, not a mathematical proof.

## Curriculum

The default batch size is 132 (four complete 33-residual cycles). Every step
contains **exactly four examples of each signed residual**. A deterministic
band/residual lattice cycles through all 13 bands, plus a portion of each
batch spent on difficult bands as determined **exclusively by TRAIN** probes.
All training families belong to this phase's TRAIN partition. The curriculum
never mines DEV or sealed TEST errors.

To prevent the model from making zero at the expense of every other residual,
all signed residuals are exact targets every batch, and any incorrect sign or
digit is an error. Extra loss weight on true zero does not remove supervision
from the other 32 integers.

## Commands

Plan without touching checkpoint data:

```powershell
& $py -m tools.nanojev_zero_line_signed_radius16 --phase plan --device cpu
```

Train the signed interval using the **promoted zero-only backbone**:

```powershell
& $py -m tools.nanojev_zero_line_signed_radius16 `
    --phase train `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --zero-only-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_zero_only_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius16_v1" `
    --device cuda `
    --max-steps 30000 `
    --probe-every 250 `
    --batch-size 132 `
    --legacy-batch-size 128 `
    --legacy-weight 2 `
    --zero-weight 3 `
    --lr 0.00005
```

On interruption or an exhausted training budget, repeat **the same command**
with `--resume`. It is permissible to raise `--max-steps` when resuming. All
algorithmic settings, source checksums, code checksums and optimizer/RNG state
must remain the same. Resume is forbidden after sealed TEST has opened, after
promotion, or when the source checkpoint has changed.

## Outputs

- `config.json`: all algorithmic settings, code SHA-256 and both certified
  checkpoint origins.
- `baseline.json`: signed-residual accuracy of the promoted zero-only starting
  model plus its prior zero-line retention audits.
- `latest.pt`: training model, optimizer, RNG states, train-only curriculum
  weights, residual-cycle cursor, step, and DEV streak.
- `best_dev.pt`: best worst-cell DEV checkpoint (**not certified**).
- `result.json`: current status, the entire 429-cell matrix by magnitude band
  and signed residual, historical audits and zero-line preservation scores.
- `test_opened.json`: hard seal created *before* the one-time final test.
- `promoted.pt`: created **only after** all promotion requirements pass. Never
  overwrite any source checkpoint.

Logs are `ZERO_R16_BASELINE`, `ZERO_R16_PROBE`, `ZERO_R16_RETENTION_REFUSED`,
`ZERO_R16_CERTIFICATION`, and `ZERO_R16_SUMMARY`.

## Limits and scientific interpretation

This run is deliberately zero-centered and exact through ±16; it does **not**
optimize 5%-relative-error arithmetic at larger residuals. Successful sampled
certification is evidence of stable local cancellation *on this population*,
not evidence of perfect addition for all 4 trillion operand combinations.

The 100% exact-pair policy is intentionally strict. Failure to promote does not
prove the invariant is unlearnable. The runner is intended to refuse misleading
success and preserve the currently accepted system.
