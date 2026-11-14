# Qwen T00–T08 vs Balanced Hawks and Doves HDB00–HDB08: competency and rate diagnostic

## Purpose

The arithmetic **T00–T08** ladder and **Balanced Hawks and Doves HDB00–HDB08** are distinct curricula. **In the T-only diagnostic mode**, this command **does not train on HDB cases** or change the optimizer, loss, base Qwen, the parent checkpoint, the aggressive-frontier policy, or the A/B repair strategies. It adds a read-only evaluation of the currently trained LoRA and an opt-in periodic audit of the ongoing frontier run.

The diagnostic directly answers: which capabilities are correct now; which gained or lost correctness between fixed checkpoints; how quickly those changes occurred per actual optimizer step, wall-second, and per-rung training-example exposure; and which capabilities have **never been presented** in training.

## Install and run

Install the manifest-free patch using `python new_patch.py <zip>`. Keep your existing `$frontierArgs` unchanged.

Once your frontier checkpoint exists and its training process is **stopped**:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
  --stage competency --frontier @frontierArgs
```

This evaluates `..._aggressive_frontier_v1/working.pt`, restoring the original source LoRA temporarily to establish a matched step-zero baseline, then restoring the evaluated LoRA without an optimizer step. On a later invocation against a newer `working.pt`, the same deck produces within-interval gain/loss and learning velocity. Repeated invocation at the same step is idempotent. It refuses a changed source or diagnostic deck and a backward checkpoint step.

For automatic periodic checks while **resuming** training, stop the existing process before restarting it with:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
  --stage run --frontier --frontier-competency-every 1 @frontierArgs
```

`1` means one diagnostic after each existing `--frontier-check-every` training check, **after** the normal checkpoint is durably saved. `2` checks every second frontier check, reducing measurement cost. Default `0` leaves the training run unaffected and enables on-demand testing only. Diagnostic settings are not part of the frontier optimizer/resume policy; an old `working.pt` can be evaluated without invalidating its lineage.

Configurable diagnostic budgets (omit for defaults): `--competency-per-rung 8`, `--competency-chain-cases 6`, `--competency-hdb-cases 9` (odd, balanced signs plus exact tie), `--competency-max-tokens 512`, `--competency-max-new-tokens 24`. Larger HDB counts increase the cost of each diagnostic.

## Exact competency populations

1. **T00–T08 near zero**: each rung is a canonical single leftmost reduction of a 2–10 operand addition expression. Signed result residuals cycle through `−2, −1, 0, +1, +2`, with operand magnitudes sampled near the 1,024 limit.
2. **T00–T08 operand-edge**: separate tests with absolute operand magnitude near 1,024 and result offsets `−32, −8, −1, 0, +1, +8, +32`.
3. **T00–T08 outer result frontier**: out-of-shell signed residuals above the currently configured aggressive radius (by default above 64, through 256 when operand limit allows). This tests transfer **beyond** the train radius rather than just replay of trained cases.
4. **Complete T03, T05, and T08 chains**: five, seven, and nine autonomous reductions respectively (rung plus one reductions); **Qwen's own generated output** is the only input to the next hop. Reports strict exact-chain success, terminal success, and first failing rung.
5. **HDB00–HDB08 balanced zero-line game decisions**: tier-specific symbolic/English scaffolding from the existing million-count Hawk–Dove templates, relabeled `HDB00` ... `HDB08`, with deterministic symmetric payoff values (`HH=2, HD=1, DH=1, DD=2`) **explicitly supplied to the model**. Encounter counts are `a=500000+k`, `b=500000−k`; the teacher computes exact decimal totals `X=2a+b`, `Y=a+2b`, residual `Δ=X−Y=2k`, and gold `HAWK`, `DOVE`, or `TIE`. Equal counts produce an exact zero-line tie. Tiny differences of 2 in ~1.5-million totals test near-zero balancing.

**HDB qualification:** This is a *self-contained balanced-HDB diagnostic pilot*, not certification of the historic hidden-payoff version. The original Hawk–Dove teacher hid payoff coefficients from its model-facing prompts, which requires separately learned payoff bindings. Here the payoff table is explicit so a model with no Hawk–Dove-specific training can attempt a properly specified task. The tiers preserve the older teacher's mathematical versus English scaffold but are not yet an approved final HDB curriculum. HDB decisions are graded strictly as the **entire** output `HAWK`, `DOVE`, or `TIE`. PROBLEM packets, long prose, and malformed outputs receive zero credit and are distinguishable as formatting defects. In the **T-only** diagnostic mode HDB is **evaluation only**; reported improvement is transfer or baseline-model ability, **never counted as HDB training**. The new opt-in HDB frontier has its own trained HDB measurements (see linked document below).

**Disjointness qualification:** the script excludes exact cases appearing in the complete deterministic aggressive-frontier **training pools and initial evaluation deck**. No separate diagnostic population is passed to the optimizer. The parent model's earlier training history is not completely enumerated, so these are fresh relative to *this aggressive run*, not proven unseen to the foundation/pretraining model.

## Reports and measurement semantics

All files live under the sibling frontier workspace; none overwrite the source run:

- `competency_eval_deck.json`: frozen audited source / expected outputs for every diagnostic case, with tier, group, signs, X/Y/Δ and game presentation.
- `competency_report.json`: latest full measurement and source-lineage fingerprints; includes per-group and per-tier `tested`, `correct`, `accuracy`, `gained`, `lost`, `net`, zero-side subtotals; chain accuracy and failure loci; inference time by population; reconstructed cumulative actual exposure counts by T rung and radius.
- `competency_history.jsonl`: compact time series snapshots with acquisition velocities for plotting or trend analysis; appends only for new checkpoint steps.
- Log line `QWEN_COMPETENCY_CHECK={...}` containing the summarized results.

Acquisition velocities are computed using **paired answers on the identical frozen deck**, not raw loss. Each T rung reports (i) number of optimizer minibatch examples actually containing the rung, (ii) optimizer steps that contained at least one such example, (iii) net newly correct examples per 100 additional *shared* optimizer steps, (iv) net per 100 rung examples, (v) net per training hour and per measured end-to-end hour **including diagnostic inference**. The code reconstructs exact minibatch membership from the deterministic seed and the persisted frontier policy, so it can account for prior steps before the diagnostic patch existed. The shared optimizer/GPU cost is **not summed over rungs**: one mixed training step can touch multiple rungs. These are observational rates, not causal isolation of independent learning channels. For the original **T-only** audit: `hdb_training_exposures=0`, and `trained_in_this_run=false`. In the opt-in HDB stage, per-tier training exposure is nonzero and is reported separately in `hdb_report.json`. If HDB improves, it is evidence of **transfer**, not of direct HDB acquisition.

The step-zero baseline and later results share the same deck, and the source LoRA is restored immediately after baseline inference. No HDB test cases enter the A/B repair corpus. A/B repair is **not** run by this command.

## Interpretation and limits

- The diagnostic does not change mathematical competence; it measures it. It does not test HybridLink or a separate trained numeric head; `--terminal-mode numeric` is the Qwen textual LM `PROBLEM` packet format.
- High one-step accuracy and lower autonomous-chain accuracy are both valid; they measure different competencies.
- A zero HDB score is possible when a Qwen adapter only knows the PROBLEM reduction format; report a format failure rather than infer absence of reasoning.
- Small per-tier decks have high sampling variance. Change diagnostic budgets only by forking the report workspace or deleting its **diagnostic-only** files intentionally; deck drift is otherwise refused.
- The baseline inference takes extra time once. Later checks report both training-only and measured end-to-end acquisition rates. Full HDB language prompting is slower than short T prompts; inference time is reported separately by family.
- CUDA results must be measured on the user's host. CPU/mock tests establish contract safety, deck invariants, and resume behavior but not numerical learning.

## Inference performance and progress (2026-10-31 fix)

A standalone run evaluates the frozen initial LoRA **and** the current stressed LoRA.
The default deck has 72 near-zero T reductions, 72 operand-edge T reductions,
72 outer-result T reductions, 18 autonomous complete chains (each requiring
up to nine successive Qwen generations), and 81 HDB decisions. Thus there
are at least 315 initial problems **per checkpoint**, two checkpoints, and
additional chained generations; model initialization is not the endpoint.
The previous diagnostic did all of this silently and used the original greedy
decoder, which recomputed the entire prompt on each generated token. A run
that appears frozen after loading weights may simply be performing inference.

The patched diagnostic emits `QWEN_COMPETENCY_START`,
`QWEN_COMPETENCY_PREPARE`, `QWEN_COMPETENCY_DECK_READY`,
`QWEN_COMPETENCY_PHASE_START`, frequent `QWEN_COMPETENCY_PROGRESS`,
`QWEN_COMPETENCY_PHASE_DONE`, and finally `QWEN_COMPETENCY_CHECK`.
Progress events identify `phase` (`baseline` or `current`), `family`,
completed batch count / total cases, elapsed seconds, and active chain count.
By default they report every batch, or use `--competency-progress-every 4`
for a less frequent log.

The competency audit now uses a **diagnostic-only KV-cached greedy decoder**
when supported, retaining the original greedy output protocol and batch padding.
This does not alter the training, certification, or A/B repair decoder. If the
Qwen decoder cannot supply `past_key_values`, an explicit
`QWEN_COMPETENCY_DECODER_FALLBACK` event records the reason and the audit
falls back to the existing uncached engine. To force the original, slower
engine, specify `--competency-legacy-decode`. The accuracy of cached versus
uncached greedy inference is tested with a deterministic CPU mock, but
**on-device Qwen/CUDA parity has not been certified**. For formal comparison
across runs, use the same decoder mode and diagnostic configuration.

After the first full baseline evaluation the audit stores
`competency_baseline_cache.json`, keyed by frozen source LoRA SHA, the fixed
evaluation deck fingerprint, and decoder mode. A subsequent interrupted
current-checkpoint evaluation can reuse this baseline without recomputing it.
An interrupted *baseline* evaluation must begin its baseline phase again.
Neither this cache nor the report modifies the LoRA weights or optimizer.

To safely restart after Ctrl+C, ensure the frontier training process is not
writing the same checkpoint and run the exact original competency command:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage competency --frontier @frontierArgs
```

For a smaller **new** audit, use lower `--competency-per-rung`,
`--competency-chain-cases`, and `--competency-hdb-cases`; this changes the
fixed evaluation deck and may require a different `--frontier-output` workspace
if `competency_report.json` already exists. Never compare scores from different
decks as paired acquisitions.

## Opt-in HDB direct training after the arithmetic frontier

**New:** `--stage hdb-frontier --frontier @frontierArgs` trains HDB00–HDB08 directly on exact negative, zero, and positive payoff margins, with varying payoff matrices and optional arithmetic replay. `--stage run --frontier --frontier-hdb @frontierArgs` completes/resumes the T frontier first and then runs the isolated HDB continuation. The parent arithmetic frontier must be in the completed `STRESSED` state. The T checkpoint remains unchanged. The HDB trainer uses a separate, versioned checkpoint and evaluation report. `--stage competency --frontier --frontier-hdb @frontierArgs` reads the HDB report without training; omit `--frontier-hdb` to keep using the old T-only competency audit.

See [Balanced Hawks and Doves HDB zero-line frontier training](QWEN_BALANCED_HAWKS_DOVES_HDB_FRONTIER_TRAINING.md) for the exact `Δ` algebra, tier schedule, CLI options, heldout matrix policy, new output files, and verification limitations.
