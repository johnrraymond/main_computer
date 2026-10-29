# Qwen LM-head reduction ladder: committed updates and 95% retention

**Independent Qwen-only acquisition experiment.** No HybridLink, stop detector, numerical sidecars, latent-cosine certification, or final-answer executor participates.

## The contract

At rung `T00`, the source packet is `PROBLEM: A+B`, and the **unassisted Qwen LM vocabulary head** must emit the *exact single* packet `ANSWER: signed_integer` (optionally terminated by a newline).

At rungs `T01` through `T08`, Qwen must emit **exactly one canonical next `PROBLEM:` reduction**, `T_ii -> T_(ii-1)`. The final numerical answer of a deeper original problem is never used to grade its rung. The independent Python oracle constructs supervised labels and **grades emitted text**; it never runs at inference or fixes Qwen's output. We do **not** prefill `ANSWER:` in model generation.

The frozen pretrained Qwen weights and LM vocabulary head do not change; only existing Qwen q/v LoRA parameters train. The upstream Stage 1 vector-retrieval probe is loaded for provenance but **not trained or graded**. Stage 1 LoRA is the initialization. Generated continuation is the only acceptance gate. The stopping detector and HybridLink are out of scope.

## Two-dimensional frontier

- Starting signed-answer radius sequence: `--radii 0,1,2,4,8`.
- Starting operand and partial-sum ceiling: `--operand-limit 1024`.
- Rung sequence: `T00..T08`; **exact certification** requires every case in the declared deck, while **sampled provisional advancement** accepts two 95%-accurate diagnostic samples. Historical decks must meet the configured 95% retention policy at the scope actually measured (sampled during routine runs; fully enumerated at periodic/exact audits).
- Radius is the **sum of all leaves / signed final residual**, not the size of any individual operand. The latter is capped separately.
- `T00 --t00-mode exhaustive` is an **exhaustive set of ordered additions**: every integer pair `(a,b)` with `|a|,|b| <= ceiling` and `|a+b|<=R`. For ±8 and ±1024 this means exactly **34,761** oracle-checked cases (including **2,049** exact-zero pairs).
- `T01..T08` are **finite deterministic full-chain diagnostic/acquisition decks**, by default `--per-cell 8`, spanning each feasible signed residual × magnitude band. At ±8 and 1024, this gives 51 cells × 8 = 408 cases per rung. Passing them is a **finite-deck certificate, not exhaustive mastery** of combinatorially many long expressions.
- Original and intermediate leaf values and partial sums are bounded by the operand limit. The oracle validates each next reduction with the same canonical left-deep `PROBLEM:` formatter used by Stage 1.
- Radius expansion beyond ±8 is supported; cells requiring more independent examples than mathematically exist are explicitly marked under-sampled. A rung **cannot** certify with under-sampled cells. Use a new campaign with a smaller `--per-cell` or a more expressive deck if that becomes binding.

## Sampled acquisition progression (opt-in)

The model may acquire a usable approximation before it reaches exact full-deck
accuracy. Use **sampled advancement** to treat this as a *provisional milestone*
so acquisition proceeds to the next reduction rung without paying for a full
6,145-case T00/R1 or larger audit every time a 128-case probe succeeds.

Append these flags to an existing `--stage run` command, preserving the same
`--output` directory and existing model/deck configuration:

```powershell
--advance-mode sampled `
--advance-accuracy 0.95 `
--advance-probes 2 `
--probe-cases 128 `
--retention-sample-cases 128 `
--retention-known-failure-cases 32 `
--retention-full-every 10 `
--gate-full-every 10 `
--audit-progress-every 128
```

### Error tripwires and projected deck errors

At 95% accuracy, a gate of 128 cases can tolerate six incorrect answers:
`ceil(128 * 0.95) = 122` must pass. **On the seventh error**, the
remaining sample cases cannot save that gate. The ladder immediately ends that
sample, records `QWEN_LADDER_GATE_TRIPWIRE`, and continues according to the
usual committed-training/local-repair policy; it does not waste model
generations finishing a gate that has already failed. A 24-case sample permits
one error and trips on its second error. The previously existing
`--fail-limit` remains only a cap on stored error examples: it is NOT the
acceptance threshold.

The event reports `errors`, `allowed_errors`, `tested`, `sample_total`,
`audit_complete:false`, and `error_projection`. The projection reports the
number of errors observed **exactly**, then estimates errors across the
original whole deck from the randomly ordered observed prefix. It uses a
Jeffreys-smoothed exchangeable Bernoulli error rate and accounts for the
already observed failures before extrapolating to unobserved cases. It also
logs an unregularized rate extrapolation for comparison. Both projections are
**model-dependent estimates, not verified error totals or confidence
bounds**. They may be inaccurate if faults are concentrated in a structure
poorly represented by the sample, or if a sample stops very early. Each
rotating stratified sample is shuffled before a tripwire can terminate it;
otherwise the first magnitude/sign band could bias the projection.

An early-stopped audit keeps its *planned* denominator (`total`), separately
records `tested`, and leaves the full-deck `accuracy` null. It cannot be
mistaken for a completed check. Acquisition still requires each of the two
sample gates to pass its own 95% floor; false successes cannot arise from a
truncated test. Known-failure replay remains outside the stratified sample's
statistical denominator, and failed examples still seed local-radius repair.

**Periodic complete audits are deliberately exempt from early stops.**
`--gate-full-every 10` (default) performs a full **current-rung** audit on
every tenth sampled *readiness* gate, counting successive training attempts,
not just successful advancements. It will evaluate the whole deck even if it
already exceeds the error limit. It also blocks provisional advancement at
that gate if the full-deck accuracy falls below the advancement floor.
`--retention-full-every 10` independently performs a full **historical
retention** audit before every tenth provisional milestone; this census also
continues through the entire deck even if it fails. Both intervals accept any
positive integer; setting either to `0` disables only that periodic check.
Neither periodic audit fabricates an exact certificate. The readiness-gate
counter survives a resume, and the intervals can be changed without forking
or resetting model/optimizer state. Exact certification still requires its
full declared current deck.

Each readiness decision evaluates **two freshly rotating, signed-result and
operand-magnitude-stratified samples** of up to 128 cases each. They are
disjoint when the deck is sufficiently large. **Each sample must individually
score at least 95% exact emitted packets** (at least 122 of 128), rather than
passing on a pooled average. Samples are regenerated deterministically on
subsequent attempts; there is no DEV holdout or claim of statistical
certification. Small decks may be completely covered, but all sampled-mode
milestones remain explicitly provisional.

When the samples qualify, the ladder applies the **same rotating,
proportional stratified retention check to both previous exact certificates and
previous provisional milestones**. Each historical deck contributes at most
`--retention-sample-cases` randomly selected examples (default 128), sampled
without replacement. Samples rotate on later attempts and cover signed
residuals, the first operand's sign, and magnitude bands. The 95% threshold is
checked **separately for each sampled historical deck**; short decks are
measured in full.

Previously observed failures are remembered by exact case ID in
`progress.json` / `working.pt` under `retention_known_failures`. Up to
`--retention-known-failure-cases` (default 32 **per historical deck**) are
retested in addition to the random sample when not already selected. These
purposefully difficult regression cases **do not count toward the random
sample's accuracy denominator**, which would bias the 95% estimate. Any
observed failure, whether random or regression, remains available to the local
radius repair routine. The bank is bounded and persisted alongside the normal
working weights; it does not change immutable certified snapshots. Newly
observed historical faults are added as the campaign proceeds; older run logs
are not retroactively reconstructed.

On every `--retention-full-every N`-th provisional milestone (default N=10),
the ladder performs an **uninterrupted full 95%-floor historical retention audit** for all
prior exact and provisional decks before advancing. `--retention-full-every 0`
disables periodic full audits (not exact-mode certification). Failed audits do
not consume a milestone or silently downgrade the certification standard.
Full tests remain mandatory when running `--advance-mode exact`. A provisional
milestone is **never** an exact certificate, even if its sampled retention was
perfect or its periodic historical census passed.

Approximate evaluation work per routine retention pass: T00/R0 and T00/R1
combined have 8,194 cases; 128 samples from each require 256 generations,
plus up to 64 remembered regression cases, versus 8,194 in a full census.
That is about **32x fewer randomly evaluated cases** without remembered
cases, and at least ~26x fewer with both 32-case regression banks full.
These ratios apply to retention *case counts*, not necessarily wall-clock
runtime or the current-rung probes. Small histories and periodic audits are
less accelerated.

In sampled mode the scheduler uses **radius-major progression**: finish T00,
T01, ..., T08 at radius zero, then T00, T01, ..., T08 at radius one, etc.,
skipping exact certificates and provisional milestones already acquired. In
particular, after T00/R1 reaches adequate sampled accuracy, the next target
is T01/R1 rather than immediately trying to perfect all T00 radii. Historical
weights, optimizer state, spent-step ledger, and prior exact certificates are
preserved when enabling the mode on an existing campaign.

The `QWEN_LADDER_PROVISIONAL_ADVANCE` event records the sample measurement and
`exact_certificate:false`. `progress.json` stores provisional milestones
separately from `certificates`; completed sampled schedules report
`PROVISIONAL_ALL`, not `CERTIFIED_ALL`. Switching back to `--advance-mode exact`
revisits the remaining uncertified combinations for full-deck exact grading.
This policy switch is explicitly recorded in `advancement_policy_history`.

**Caution:** A 95% sample score can conceal rare errors. Provisional progress
supports training-schedule decisions only, not claims of universal arithmetic
accuracy or exact certificate-level retention. The original exhaustive mode
remains available with `--advance-mode exact` (the default).

## Model-free preview

From the `main_computer` repository:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage preview --radii 0,1,2,4,8 `
    --operand-limit 1024 --max-rung 8 --per-cell 8 `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_radius_preview_v1"
```

Review `deck_preview.json`, including `deck_sha256`, `covered_cells`, `unreachable_cells`, and `undersampled_cells`. No Qwen model is loaded.

## Train / repair / certify

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --radii 0,1,2,4,8 --operand-limit 1024 --max-rung 8 `
    --t00-mode exhaustive --per-cell 8 `
    --steps-per-rung 10000 --repair-steps 25 `
    --retention-floor 0.95 --local-radius 4 --local-repair-steps 10 `
    --batch-size 4 --replay-per-batch 1 --eval-batch 8 --fail-limit 64 `
    --probe-cases 128 --audit-progress-every 32 `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_exact_radius_v1"
```

Requires installed torch, Transformers, Stage 1 and text-parent checkpoints plus the repository's existing Qwen loader. The machine executing the run needs the same compatible local backbone sources used by those checkpoints. We did **not** run a real pretrained Qwen optimization cycle in the offline patch tests; run a bounded CUDA smoke campaign before committing large compute.

### Algorithm (commit → evaluate → local repair)

1. Load the verified Qwen Stage 1 checkpoint and frozen text parent. Train only Qwen q/v LoRA, not HybridLink, the detector, or the LM vocabulary head.
2. Construct the canonical current `T`/`R` deck and grade greedy, single-line LM-head packets. Current-rung success remains **exact on the full deck**. The sample probe never issues a certificate.
3. Train the next `--repair-steps` acquisition steps from observed current-rung errors and old-rung rehearsal. **Always commit** the resulting LoRA + optimizer to `working.pt` before examining retention. Never restore pre-update weights because a retention check failed, a previously correct probe case regressed, or the candidate sampled accuracy dropped. New-rung learning and historical retention are independent measured quantities.
4. Audit each earlier certificate with `--retention-floor 0.95`, enforced **per certificate** (not a global average). Scores still count **exactly matching emitted packets**; 95% is the *share of previously certified cases retained*, not numeric-error tolerance. For T00/R0 and operand limit 1024, the minimum is **1,947/2,049** (up to 102 misses). For a 24-card previous rung, the minimum is 23/24.
5. If the retention audit finds errors, derive *local operand neighborhoods* around those actual counterexamples: shift adjacent leaves by `(+d,-d)`, for all integer `d` from `-local-radius` to `+local-radius` while keeping the residual and all operand/partial-sum bounds. Include the original failure and its reachable neighbors, train up to `--local-repair-steps` with interleaved current-rung and older-rung replay, and **commit** these repair updates too. Neither phase rolls weights back. Local repairs consume the **same existing rung budget**; there is no implicit extra allocation.
6. Re-evaluate the current probe independently of retention before and after local repair. Re-audit historical retention afterward. Even if recovery still falls below 95%, keep the latest committed weights and continue learning until the rung budget is exhausted. Do not advance unless the new rung has *fully exact* certification **and** each previous certificate meets 95% retention.
7. A 95% audit reads the complete old deck if the threshold is still reachable. If enough failures make 95% mathematically impossible, routine checks stop early, emit the tripwire with a model-based estimate of total errors, and report an **upper bound** on accuracy, not a false full-deck percentage. Periodic full checks remain exhaustive even when they fail. The audit mixes the order of old signed/operand cases so early-stop failures do not come only from one extreme of an ordered T00 deck. Report separately the number actually tested and the original deck total.
8. The append-only, fsynced `budget_ledger.jsonl` charges steps *before* training. The saved `working.pt` contains the latest committed LoRA, optimizer, and spend. An OS crash inside an unpersisted optimizer batch can still lose the latest transient weight changes, but never refunds the charged budget. An ordinary retention failure never causes such a rollback.
9. At budget exhaustion, retain all provisional weights, log the observed new-rung and old-rung scores, and halt with `BUDGET_EXHAUSTED`. Do not skip a rung or automatically extend its budget.

**Logging:** `QWEN_LADDER_COMMIT` reports new-rung accuracy and retention after each committed acquisition chunk. `QWEN_LADDER_LOCAL_REPAIR` reports the number of localized cases, repair steps, before/after retention and current-rung scores. `QWEN_LADDER_RETENTION` reports `passed`, `tested`, `total`, `required`, `complete`, and `accuracy_upper_bound`. `QWEN_LADDER_REPAIR` persists the combined transaction result. The immutable historical `certified_T00_R0000.pt` remains untouched; a historical certificate is not a promise that subsequent `working.pt` stays 100% correct on that deck.

The new runtime flags are `--retention-floor` (default `0.95`), `--local-radius` (default `4`), and `--local-repair-steps` (default `10`). They are stored in `progress.json` as `learning_policy`, outside the original deck/model config hash. Once stored, resuming with different values refuses `LADDER_LEARNING_POLICY_DRIFT` rather than silently changing the evaluation rules. Policy activation is recorded with the already-spent step counts.

### Report and resume

- `progress.json`: current rung, radius, certificates and persistent per-rung budget.
- `budget_ledger.jsonl`: durable, monotonic spend records for each rung.
- `working.pt`: latest restartable LoRA, optimizer and exact progress metadata; **not** a certification of its current radius.
- `certified_Txx_Rxxxx.pt`: immutable exact-at-acquisition LoRA snapshot and exact case-deck fingerprint; the working LoRA subsequently obeys the recorded retention floor.

Resume by rerunning the command against the **same output directory**, keeping the model/deck/training settings and recorded learning-policy flags fixed. You may change `--radii` freely; radius schedule changes are recorded in `radius_schedule_history`. If the budget is exhausted, no more optimizer steps occur. To explicitly allocate **an additional 2,000 optimizer steps only for the stopped rung**, rerun with `--extend-budget 2000` (no other configuration changes). The extension is recorded and cannot be invoked unless status was `BUDGET_EXHAUSTED`.

Get current status without loading Qwen:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder --stage status `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_exact_radius_v1"
```

### Change radius schedule in place (no fork)

The `--radii` sequence is **mutable campaign scheduling**, not the frozen
identity of the model. You may replace `--radii 0` with `--radii 0,1,4`, or
remove previously requested radii, in the **same `--output` directory**.
Stop the process between updates and rerun the command with the modified
`--radii` argument. The existing `working.pt` LoRA weights, AdamW optimizer
state, durable spent-step ledger, per-rung budgets, exact historical certificates,
and the policy's 95% retention threshold are preserved. A
`QWEN_LADDER_RADIUS_SCHEDULE_CHANGED` event reports the old/new lists and the
current target. The change is recorded durably in `progress.json` and
`working.pt`, without changing certified snapshot files.

**Active-rung continuity:** If the currently incomplete (rung, radius) is
still in the new list, it continues first. For example, an ongoing `T01/R0`
run with 725 steps already spent stays at T01/R0 after changing the radius
schedule from `0` to `0,1,4`. Once that target certifies, the scheduler finds
the first newly requested, not-yet-certified combination (in this example
`T00/R1`, then `T00/R4`, etc.). Unfinished targets removed from the schedule
stop being selected; their provisional learning is still retained in the
working weights and optimizer. Certificates for removed radii remain protected
by retention audits. A previously completed `CERTIFIED_ALL` campaign reopens
if the modified schedule requires additional certificates.

**Safety invariants:** A schedule edit cannot mint new certificates or grant
new optimizer steps. Budgets remain **per rung**, not per radius: if a rung has
exhausted its previously allocated steps, changing radii does not reset it.
Use `--extend-budget N` explicitly at `BUDGET_EXHAUSTED` when appropriate.
Changing `--operand-limit`, `--per-cell`, checkpoints, seed, model configuration,
training hyperparameters, or other non-radius acquisition parameters remains
prohibited on an existing output, and still causes `LADDER_RESUME_CONFIG_DRIFT`.
`--radii` must still begin with zero, be strictly increasing, and fit the
original operand limit.

**Example:** On your current `qwen_lm_zero_line_fork_v1` output, keep every
other argument unchanged and change only:

```powershell
--radii 0,1,4 `
--output "C:\Users\subsi\NanoJev\runs\qwen_lm_zero_line_fork_v1"
```

For an entirely separate research lineage (not required for ordinary radius
schedule changes), `--warm-start-ladder` and `--fork-certified-zero-from` still
support explicit new-output experiments.

### Progress logging

`--audit-progress-every N` retains its normal value for acquisition probes and
full current-rung audits, but **retention progress** is now printed only after
approximately `5*N` tested cases. With `--audit-progress-every 8` and a
2,049-case passing T00/R0 retention deck, this reduces the periodic progress
lines from about 257 to about 52 (80% fewer) without skipping any cases.
`QWEN_LADDER_RETENTION_BEGIN` and `QWEN_LADDER_RETENTION` remain visible so
that audits can still be distinguished and their results read. Set N to zero
to suppress all periodic progress lines, or use a larger N to reduce them
further.

**Practical warning:** autoregressive generation for all 34,761 T00 pairs is expensive in exact mode. In sampled mode, a sampled readiness pass does not trigger a full current-deck audit; exhaustive certification is a separate subsequent stage. Historical exact certificates are sampled during *routine* advancement and checked in full only at periodic milestones or deliberate exact audits. When a full audit is required, progress remains visible. GPU evaluation work is not counted as optimizer steps. The two new flags are runtime diagnostic options and **do not change the original campaign config hash**, so they can be used when resuming an existing `working.pt` with the identical acquisition settings. To switch from an already-running process, stop it cleanly after a `QWEN_LADDER_REPAIR` line, apply this patch, and rerun the original command with the same output path.

## Zero-line-only fork from an already certified T00/R0

Use this path when a multi-radius campaign has **certified T00/R0** and
subsequently begun an uncertified wider radius. A fork is useful when you want an independent
lineage from the *original certified T00/R0 snapshot* rather than continuing
with provisional weights. Ordinary radius changes do not require a fork. Instead, start a sibling campaign from the
**immutable `certified_T00_R0000.pt` checkpoint**. The fork reads the parent's
`working.pt` to check lineage and certificate history, but never imports its
possibly-uncertified working LoRA weights.

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --radii 0 --operand-limit 1024 --max-rung 8 `
    --t00-mode exhaustive --per-cell 8 `
    --steps-per-rung 10000 --repair-steps 25 `
    --batch-size 4 --replay-per-batch 1 `
    --eval-batch 8 --fail-limit 64 `
    --probe-cases 128 --audit-progress-every 8 `
    --fork-certified-zero-from "C:\Users\subsi\NanoJev\runs\qwen_lm_exact_radius_v1" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_zero_line_fork_v1"
```

The fork imports precisely the T00/R0 certificate, its original deck
fingerprint, and the **50 original optimizer steps spent to acquire it** (when
that value is what the certificate records). The first attempted rung is
**T01/R0**, followed by T02/R0 through T08/R0. T00/R0 must retain at least 95% exact LM-head packet accuracy
throughout certification checks, even if later optimizers change LoRA weights. No R1-or-higher
certificates or later uncertified weights are imported. The fork copies the
certified T00/R0 snapshot to its output directory and records the exact source
snapshot SHA-256 in `progress.json`.

Rerun the same command to resume the zero-line fork. Existing forks created by the previous rollback-on-regression policy resume with their current working LoRA and durable spend; they record a one-way policy migration, without changing the immutable parent snapshot or resetting T01 progress. The source snapshot is
rechecked for changes. The fork output directory remains unchanged; the `--radii` sequence
may now be edited in place. Alternatively, on an established fork the `--fork-certified-zero-from`
flag may be omitted; the self-contained fork checkpoint and certificate
metadata are already persisted. Neither a fork nor an ordinary restart changes
original-campaign artifacts.

Budget accounting treats inherited T00 steps as already spent. New T01..T08
steps begin at zero, each with its own 10,000-step cap. The local write-ahead
budget ledger starts where inherited costs end rather than reissuing historical
charges. At the first exhausted uncertified rung, the run halts with
`BUDGET_EXHAUSTED`; no automatically authorized extension occurs. (The exact
first failed rung is an experimental result, not a prediction.)

Retention checks print `QWEN_LADDER_RETENTION_BEGIN` and periodic
`QWEN_LADDER_AUDIT_PROGRESS` lines marked `"phase": "retention"`.
`QWEN_LADDER_RETENTION_SCOPE` reports whether a provisional milestone is using
`rotating_stratified_sample` or `periodic_full`.
`sample_only:true` identifies a sampled historical deck; only `sample_only:false`
and `complete:true` indicate a full-deck measurement. A below-floor audit may
stop early when its remaining cases cannot reach 95%; such a result is marked
incomplete with an upper bound. A slow CUDA run is not necessarily stalled;
retention progress defaults to five times the user-selected audit-progress
interval. All new sampling flags are operational and do not change the
model-training config hash.

Inspect progress without loading the model:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder --stage status `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_zero_line_fork_v1"
```

### Resume an existing T01/R0 fork with committed local repairs

Once this patch is applied, resume the **same fork output directory** and original checkpoint paths. Add the policy flags (new runtime settings only) and optionally reduce the progress-print frequency; these do not change the original deck config hash:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --radii 0 --operand-limit 1024 --max-rung 8 `
    --t00-mode exhaustive --per-cell 8 `
    --steps-per-rung 10000 --repair-steps 25 `
    --retention-floor 0.95 --local-radius 4 --local-repair-steps 10 `
    --batch-size 4 --replay-per-batch 1 `
    --eval-batch 8 --fail-limit 64 --probe-cases 128 `
    --audit-progress-every 128 `
    --fork-certified-zero-from "C:\Users\subsi\NanoJev\runs\qwen_lm_exact_radius_v1" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_zero_line_fork_v1"
```

If that fork was already `BUDGET_EXHAUSTED`, it will remain stopped until an explicit `--extend-budget N` is provided. No new output folder is required for an active matching fork. Training policy and sampling/periodic-audit flags are runtime controls and
can be adjusted when resuming with the same underlying deck and checkpoints.
Changing the radius schedule remains supported without a fork.

**Verification:** CPU tests cover threshold rounding, full-deck packet grading, bounded local neighborhoods, policy migration/drift, preserved budgets, and an integration run in which retention fails yet both acquisition and local updates stay committed. Actual Qwen CUDA convergence and wall-clock cost have not been measured by these unit tests.

## Resume an existing sampled campaign with faster historical retention

Keep the existing original checkpoint paths, `--radii 0,1,2,3,4`, and the same
`--output` directory. Add the following flags to the command currently used
for `qwen_lm_zero_line_fork_v1`:

```powershell
--advance-mode sampled `
--advance-accuracy 0.95 `
--advance-probes 2 `
--probe-cases 128 `
--retention-floor 0.95 `
--retention-sample-cases 128 `
--retention-known-failure-cases 32 `
--retention-full-every 10 `
--gate-full-every 10 `
--audit-progress-every 128
```

No restart of optimizer progress, re-forking, reallocation of spent steps, or
re-issuance of historical certificates is required. Exact historical decks
retain their complete original identity and remain available for full audit.
The new persistent failure bank begins empty on pre-patch checkpoints and
collects newly observed failures as you train. Stop the currently running
process before applying the patch; only one process should write `working.pt`
for any campaign at a time. Back up the run directory before changing training
code if you need a recovery point.

## Adaptive acquisition and local repair controller (experimental, opt-in)

`--adaptive-repair` enables an adaptive feedback controller **on the existing
campaign**. It does not alter the Qwen architecture, the original numerical
rung decks, the LoRA checkpoint identity, the existing locally generated
(+d,-d) arithmetic neighborhoods, or any earned exact certificates. It does
not reset the optimizer, regenerate spent training budget, or create a fork.
Without the flag, the fixed acquisition/repair timing remains a control group.

Example additions to the existing PowerShell training command (keep the
original parent checkpoints, `--output`, radii, and sampling settings):

```powershell
--advance-mode sampled `
--advance-accuracy 0.95 `
--adaptive-repair `
--adaptive-check-min 5 `
--adaptive-check-max 100 `
--adaptive-repair-min 2 `
--adaptive-repair-max 20 `
--adaptive-harvest-cases 128 `
--adaptive-failure-target 8 `
--adaptive-full-steps 1000 `
--gate-full-every 10 `
--retention-full-every 10
```

### Adaptive decision sequence

1. **Acquisition:** Continue committing all training updates. The controller
   uses the original `--repair-steps` as its initial number of acquisition
   steps between checks, clipped to `--adaptive-check-min` and
   `--adaptive-check-max`. With stable historical retention and low current-rung
   readiness, the interval grows by about 40%, capped at the upper limit.
   When historical retention falls below its floor, the interval halves;
   near the 95% current-rung readiness threshold, it also decreases to avoid
   wasting steps after the model is already ready to advance.
2. **Diagnosis:** Keep the normal 95% gate and early error tripwire. If a
   historical deck's gate trips, **do not use the truncated gate denominator as
   a measured full accuracy**. Instead, harvest additional *distinct failed
   cases* specifically to improve the local repair map. A historical deck of
   32 cases or fewer is inspected to completion, independent of the remaining
   adaptive harvest budget. Larger decks use bounded rotating stratified
   random samples, stopping after enough useful new centers or repeated empty
   discovery batches. Harvesting does not change the original gate outcome.
3. **Local repair:** Reuse the existing signed-preserving adjacent-leaf
   neighborhood generator. In adaptive mode, first guarantee direct attention
   to discovered failed examples (when the repair step budget permits), then
   take nearby neighbors ordered by increasing local offset. Continue mixing
   the current rung and older arithmetic replay. All completed optimizer
   updates remain committed. The targeted-repair step budget increases by
   about 50% when history remains below its floor, decreases after recovered
   retention, and is bounded by the adaptive min/max and the unchanged rung
   budget ledger. There is no rollback of the LoRA weights.
4. **Measure and adapt:** Recheck acquisition and historical retention after
   repair. Log current-rung accuracy, change in readiness, whether retention
   recovered, next evaluation interval, next repair budget, harvest yield,
   and measured elapsed wall time in `QWEN_LADDER_ADAPTIVE_DECISION` and
   `QWEN_LADDER_ADAPTIVE_HARVEST`. Values persist under `adaptive_controller`
   in `working.pt` and `progress.json`. These are bounded heuristics, **not
   proven optimal decisions**. Evaluate empirical GPU time and successful
   retained rungs against the fixed controller before claiming acceleration.
5. **Validation:** The existing default full gate every 10 readiness checks
   and full historical retention audit every 10 provisional advancements remain
   intact. As a fallback for campaigns stalled without advancing, adaptive
   mode also performs a **complete** historical retention audit every
   `--adaptive-full-steps` charged optimizer steps (default 1,000; `0` disables
   just this fallback). A full audit never stops at the error tripwire and
   cannot retroactively convert a provisional milestone to an exact certificate.

Adaptive policy changes and the per-target controller state are independent of
`config_hash`, so the adaptive settings may be changed on resume **without a
fork**. The controller records policy changes in its history; switching the
flag off returns to the fixed strategy without deleting recorded decisions.
Changing numerical deck identity or model checkpoints is still guarded.

### Experimental measurement

Compare two copies of the same checkpoint (fixed and adaptive) with equal GPU
resources and the same rung/radius objectives. Track **GPU seconds and charged
optimizer steps per newly advanced-and-retained rung**, frequency of 95%
retention violations, how often local repair recovers retention, number of
new failure centers harvested, and independent periodic full-check results.
If adaptive sampling lets deeper rungs advance with poor actual retention,
that is *not* a speedup in the scientific objective. Both modes must preserve
exact certification as a separate exhaustive standard.

### Repair-focused gate reuse and balanced local neighborhoods

When a sampled current rung already meets readiness, but historical retention
fails, the acquisition loop performs **zero** new acquisition optimizer steps.
It now reuses the current readiness and retention results for that unchanged
model, rather than immediately repeating both evaluations under a second
"committed" label. The next operation is adaptive failure harvesting and
local repair. Post-repair evaluation still runs normally. This reduces redundant
inference in deep-rung repair-dominated campaigns without suppressing any
independent check of updated weights.

In adaptive local repair, discovered failed original problems are still trained
first. Subsequent local-neighborhood draws are interleaved **across all
feasible failure centers**, with each center's offsets ordered small-to-large.
This prevents the lexical order of case IDs from concentrating the bounded
repair budget on the first failure center. Rung replay, signed residual
preservation, optimizer-step charges, commit-first behavior, sample gates, and
periodic full audits are unchanged.

A diagnostic signal of repair-dominated operation is an adaptive decision with
`acquisition_accuracy_before == acquisition_accuracy_after == 1`,
`acquisition_steps == 0` in the commit line, and `retention_ok == false`.
The controller must still repair older rungs before advancing. Neither this
optimization nor the adaptive controller guarantees a speedup until measured
CUDA elapsed time per successfully retained milestone improves.

## Width-first signed zero-line curriculum (rung-major)

The depth-first experiment reached `T06/R0` while some earlier depths had
only a zero-residual acquisition. That measures reduction depth but is an
incomplete test of small **signed** residual preservation. The optional
**rung-major curriculum** first acquires *every requested radius* at one
reduction depth before advancing to the next depth:

```text
T00/R0 -> T00/R1 -> T00/R2 -> T00/R3 -> T00/R4
       -> T01/R0 -> T01/R1 -> T01/R2 -> T01/R3 -> T01/R4
       -> T02/R0 -> ... -> T08/R4
```

`R4` means the **cumulative interval** `[-4,+4]`, not just the ±4
boundary. The `--radii` schedule is a set of increasing maximum residuals.
The `--local-radius 4` repair setting is different: it changes adjacent
operands in opposite directions, preserving the **exact signed residual**.
The broader acquisition curriculum supplies actual +1/-1/+2/-2/... errors
for the repair system to detect, not just variations that still sum to zero.

**Resume an existing campaign in place**, after stopping its training
process, by appending one flag to the unchanged command (keep the identical
`--output`, checkpoints, radii, training settings, and adaptive flags):

```powershell
--radii 0,1,2,3,4 `
--curriculum rung-major `
--advance-mode sampled `
--adaptive-repair `
--local-radius 4
```

The `--curriculum` flag does **not** alter the immutable acquisition/deck
configuration. `rung-major` persists in the saved campaign even when the
flag is omitted on later resumes. `radius-major` is available to reproduce
the legacy sampled-mode depth-first trajectory. Without an explicit new
selection, legacy campaigns keep the active unfinished target as before.

The scheduler reads the existing exact certificates and provisional
milestones, then chooses the **earliest missing rung-major cell**. A campaign
with a T00/R0 exact certificate, T00/R1 provisional milestone, and T01/R0
through T05/R0 provisional milestones will next work on **T00/R2**; after
T00/R4 it returns to **T01/R1**. A T06/R0 readiness result that never passed
historical retention is **not** promoted into a milestone. The model's LoRA
weights and optimizer momentum are preserved, but previously observed T06
competence is **not guaranteed to survive later training**: the immutable
pre-transition checkpoint provides a recoverable reference for experiments.

Before a nonempty campaign changes curriculum, the ladder copies the
original `working.pt` to `curriculum_snapshots/before_rung-major_Txx_Rxxxx.pt`
(or corresponding target mode). The file contains the prior weights,
optimizer, and campaign state. It is never overwritten on later resumes.
Events `QWEN_LADDER_CURRICULUM_SNAPSHOT` and `QWEN_LADDER_CURRICULUM`
record the path and schedule decision. The normal `working.pt` remains the
mutable continuation. Never run two trainers against the same output.

With `--adaptive-repair`, **actual failed historical examples** receive
direct training first. They are now ordered round-robin across
`(rung, radius, exact signed residual)` before their local operand-preserving
neighbors are considered. This keeps a single large zero-residual deck from
monopolizing a short repair budget when failures occur at other signed
residuals. It retains the existing local-radius generator, the acquisition
replay mix, the 95% per-historical-deck provisional floor, the tripwire,
rotating samples, and both configurable full-audit cadences.

All updates continue to be committed. Completed historical milestones are
*retention-guarded*, but a newly expanded radius may initially cause more
interference and require repair. Rung-major is a scientific curriculum
hypothesis, **not a guaranteed throughput improvement**. Compare GPU time,
repair steps, width completion, and retained accuracy against the
`radius-major` control on a comparable starting checkpoint. Exact
certification remains independent of sampled advancement.
