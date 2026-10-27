# QW5 zero-balance → Hawk–Dove ladder experiment

## Purpose

This is a **new, opt-in QW5 research harness**. It does not alter the source
four ordinary EMP heads, Hard EMP specialist, original QW5 Stage 1 checkpoint,
Qwen weights, HybridLink, or LM output pathway. QW5 remains the numerical
readout inside the harness; this does **not** authorize Stage 2 or Stage 3.

The experiment asks: **How far can the same trained five-headed, Qwen-conditioned
system climb while preserving zero-line cancellation and near-equilibrium
balance?** The harness advances on DEV gates and makes a separate one-shot
independent TEST check after a candidate passes. It stops at the first failure.

## Corpus rungs

| Rung | Inputs presented to the neural system | Target |
|---|---|---|
| Z0 | `A`, `−A+δ` up to 512 | Signed `δ` |
| Z1 | Same structure up to 65,536 | Signed `δ` |
| Z2 | Same structure up to 2,000,000 | Signed `δ` |
| R0a | Signed numerical inputs through ±2,000,000; residuals through ±4,096 | First outward residual expansion |
| R0b | Same signed input; residuals through ±100,000 | Intermediate residual expansion; R0a retained |
| R0c | Same signed input; residuals through ±1,000,000 | Large residual expansion; R0a/b retained |
| R1 | Same signed numeric inputs `(A,-B)` paired with alternate addition/subtraction/difference/advantage wordings | Same signed result under equivalent notation |
| R2 | **Identical, unnegated numeric input `(A,B)` for forward and reverse questions** | Qwen-conditioned signed direction; opposite answer when question reverses |
| B0 | Explicit Hawk and Dove totals `(X,-Y)` | Signed `X−Y` |
| T00 | Original `(ARGMAX X Y)` expression, `(X,-Y)` | Signed `X−Y` |
| T01 | X visible, Dove payoff expression, raw `(a,b)` to EMP | Signed `X−Y` |
| T02 | Hawk expression, Y visible, raw `(a,b)` to EMP | Signed `X−Y` |
| T03 | Both payoff expressions; raw `(a,b)` | Signed `X−Y` |
| T04–T06 | Symbolically equivalent reordered operands and operators | Signed `X−Y` |
| T07 | English scenario + symbolic expression | Signed `X−Y` |
| T08 | English-only scenario with explicit payoff descriptions | Signed `X−Y` |

`T00`–`T08` are rendered using the repository's
`nanojev_hawk_dove_million` tier grammars and validated against its exact
Decimal oracle. **This is not a replay of the fixed-payoff historical teacher**:
the harness constructs multiple, explicitly specified integer payoff matrices.
It supplies payoff bindings to Qwen, since their values must not be treated as
hidden global constants. At T08 they appear in natural-language sentences.

The current QW5 architecture has **one signed, seven-digit readout**, so this
experiment scores the signed payoff difference `X−Y` (and, by its sign, the
strategy boundary), not independently certified absolute X and Y readouts.
Training labels are never inserted as extra numerical EMP inputs. T00 may see
both totals; T01 and T02 receive only the one total legitimately visible in
the rendered prompt; at T01+ EMP's two numerical inputs are the **raw opponent
encounter counts**, never teacher-computed payoff totals.

## Balance invariance and splits

Each game family has an exact tie at

`a = 1,000,000 * p / (p+q)` and `X−Y = p*b − q*a`.

The selected divisors `p+q` divide one million, so exact ties are present.
Each family includes positive, negative, exact-zero, near-zero and more distant
cases. A paired counterfactual adds `+1` to **all four payoff entries**. That
adds one million to both totals, while leaving `X−Y` identical. Both
counterfactual presentations remain together in a single split and are
scored for **paired invariant accuracy**. In the zero-line rungs the paired
invariant instead reverses the operand orientation while preserving the sum.

TRAIN/DEV/TEST assignments are by entire magnitude family for zero-line and
by the entire `(p,q)` **decision-boundary family** for
games, not by individual sentence or example. Exactly balanced examples occur
in all three splits, with *different games* in each split. Split mixing or an
incomplete counterfactual pair causes a hard refusal.

Gates (configurable): DEV overall exact >= 0.80; DEV exact for zero, near +,
near -, far +, far - each >= 0.80; DEV paired invariant accuracy >= 0.80;
DEV retention of every earlier rung >= 0.80 **in every sign/zero band and paired invariant condition**, with any earlier conditioning-gain gates also retained. From T01 onwards DEV accuracy
with **intact Qwen** must beat both zeroed and mismatched Qwen representations
by at least 0.02. Explicit-total rungs do not require Qwen gain because the
visible inputs already determine the answer. All advancement requires a
one-shot independent TEST gate. A failed independent TEST locks that campaign
rather than reusing its labels to tune a retry.

## Staged residual expansion: R0a → R0b → R0c (schema v3)

**The new order** is `Z0 → Z1 → Z2 → R0a → R0b → R0c → R1 → R2 → B0 → T00…T08`.
The earlier one-shot `R0` has been retired in new campaigns; do not modify or resume an existing v2 output with a v3 config.

- **R0a**: exact signed residuals through **±4,096**, including zero and small ± residual controls.
- **R0b**: exact signed residuals through **±100,000**, preserving R0a competence.
- **R0c**: exact signed residuals through **±1,000,000**, preserving R0a/b competence.

Every R0a/b/c rung includes negative and positive residuals in every eligible
magnitude band, exact zero and small positive/negative controls. Counterfactual
pairs swap the two signed operands without changing the sum, and are never
split between datasets. Numerical operands remain within ±2,000,000; Qwen
receives a matching unambiguous prompt. There is no arithmetic-target leakage.

### Held-out numerical targets, not only held-out anchors

For **each rung independently**, the generator draws 12 distinct magnitude
values **per magnitude band per split**. The sets of far residual magnitudes
(`abs(answer)>16`) used by TRAIN, DEV and TEST are disjoint, even before
accounting for separately held-out numerical anchor families. The near/zero
controls deliberately recur in all splits because they test retention.
Training includes each new rung's exact signed ceiling **on both signs**.
DEV and TEST contain different unseen numerical residual values, not merely
new large starting numbers with familiar outputs.

The v3 sampler and metrics use the same named residual-scale categories and
retain the original **80% per-group, exact-total, invariant-pair, retention,
and independent-TEST** acceptance gates. Earlier passed rungs are
re-evaluated on DEV whenever a later rung is tested. No TEST label is used
for optimizer selection or subsequent retry.

### More varied examples per stage

R0a/b/c each have **512 TRAIN cards, 128 DEV cards, and 128 TEST cards** by
default (`--r0-train-cards`, `--r0-dev-cards`, `--r0-test-cards`). The original
rung defaults remain **128 / 64 / 64**. These R0 cards are paired, so the
512 training cards represent 256 full commutativity pairs, selected from a
much larger pool of distinct targets and operand anchors.

### Resume from the current HybridBeast's accepted Z2

Your cutover lineage `qw5_hybridbeast_cutover_v2` and its certified ladder
`qw5_hybridbeast_ladder_v2` passed Z0/Z1/Z2 but **failed the older R0 on
DEV**. Begin a NEW v3 ladder; use `--seed-accepted-from` to import only the
previous `accepted.pt` at Z2, after verifying source hashes, order, seed,
trainable names and shapes, and absence of an independent-TEST failure.
The old failed R0 model state, rung certification, and TEST results are not
imported. Old files are never modified.

```powershell
& $py -m tools.nanojev_qw5_balance_ladder preview
& $py -m tools.nanojev_qw5_balance_ladder examples --examples-per-rung 12
& $py -m tools.nanojev_qw5_balance_ladder run `
  --qw5-run "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_cutover_v2" `
  --seed-accepted-from "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_ladder_v2" `
  --output "C:\Users\subsi\NanoJev\runs\qw5_hybridbeast_ladder_v3" `
  --through R0a `
  --device cuda `
  --steps-per-rung 3000 `
  --probe-every 100
```

After R0a acceptance, repeat using the **identical config/output/warm-start**
with `--through R0b`, then `--through R0c`, then `--through T08`.
Changing the established `--steps-per-rung`, seed, or sample sizes between
continuations is refused because the campaign records those as immutable
settings. You can instead start with `--through T08` to stop automatically
at the first failed rung. The existing `v2` directory must remain unchanged.

**R1 and R2 have not changed.** R1 tests alternative mathematical/linguistic
notation with pre-signed numeric inputs; R2 uses identical unnegated inputs
for opposite-direction questions, so only Qwen's representations can supply
direction. R2 still demands directional contrast accuracy and a measured
Qwen conditioning advantage before it can advance to B0.

See `pretty_docs/NANOJEV_QW5_R0_STAGED_EXAMPLES.md` for genuine selected
TRAIN examples and exact tensor inputs/target residuals.

## Z0 balance-sampler correction

The original ladder sampler grouped examples by their source displacement
(`Card.near`, e.g., `delta=8`) while the assessment gate grouped examples by
the exact signed target residual (`1..16` near, `>16` far). That discrepancy
caused the original Z0 TEST selection to contain **zero far-positive examples**,
which failed its minimum-group-count gate despite perfect DEV accuracy.

The corrected sampler, gate, and preview use **one target-residual band
classification**. `preview` now reports `scored_bands` for TRAIN, DEV and TEST;
each must contain at least two examples from zero, near-positive,
near-negative, far-positive, and far-negative groups. Invariant pairs remain
indivisible, with entire magnitude/game-boundary families still split apart.

An original campaign that reached `QW5_LADDER_TEST_REFUSED` remains locked by
its `independent_test_failed` ledger. **Do not reset or overwrite that run.**
Start the corrected corpus in a new `--output` directory, treating the old
TEST outcome as a harness coverage defect, not an accuracy measurement.

## Commands (Windows PowerShell)

From `C:\Users\subsi\main_computer`, with the Python interpreter you use for
NanoJev in `$py`:

```
& $py -m tools.nanojev_qw5_balance_ladder preview
```

`preview` constructs, checks, and summarizes every rung **without downloading
Qwen, touching source models, or starting training**.

Start with only the first rung:

```
& $py -m tools.nanojev_qw5_balance_ladder run `
  --qw5-run "C:\Users\subsi\NanoJev\runs\qw5_phase1_v1" `
  --output "C:\Users\subsi\NanoJev\runs\qw5_zero_balance_ladder_v1" `
  --through Z0 `
  --device cuda `
  --steps-per-rung 1000 `
  --probe-every 100
```

If accepted, use the **same command** with `--through T08` to continue through
all remaining rungs until one fails (do not rerun with `--through Z0` if it has
already passed). The source model is always loaded from the pre-existing QW5
Stage 1 run, then trainable parameters are restored from this ladder's own
last accepted checkpoint. Reports and model checkpoints are written **only to
the new output directory**.

For a shorter exploratory pass, start a *new output directory* with fewer
steps, a smaller set of balanced examples, or only `--through Z1`. All choices
except `--through` and `--device` are recorded in `config.json` and held
constant for the run. Default: 128 training, 64 DEV, 64 TEST cards per rung.
Counts must be even and at least 20 to preserve invariant pairs.

```
& $py -m tools.nanojev_qw5_balance_ladder status `
  --output "C:\Users\subsi\NanoJev\runs\qw5_zero_balance_ladder_v1"
```

Read `ledger.json` for the list of passed rungs, all candidate probes,
independent test results, retention, and the first failed condition.

### Failure policies and limitations

- The original QW5 Stage 1 run and the original EMP checkpoint lineage must
  still pass the existing source hash checks when the ladder starts or resumes.
  Do not mutate or clean up those checkpoint files during a live ladder run.
- The first rung that fails its DEV gate ends the current climb. Re-running the
  same rung starts from the most recently *accepted* parameters, not from a
  partially trained failure. After an independent TEST failure, the run is
  locked to prevent selection on TEST labels.
- The harness does not yet train HybridLink or authorize QW5 as application
  output. It evaluates the QW5 component that would later support those roles.
- Gate PASS is an experimental corpus result, **not** certification that QW5
  has fully learned game theory, interprets arbitrary stories, or computes X
  and Y separately.
- The original QW5 checkpoint used a fixed 512-radius task. Do not assume it
  will pass the 65,536 or 2,000,000 radius tests. Stopping there is a valid,
  informative result.
