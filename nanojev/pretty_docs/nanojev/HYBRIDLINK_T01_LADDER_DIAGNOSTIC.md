# NanoJev — T01 retention-cutover ladder forensics

This is a **read-only diagnostic**, not another trainer and not an automatic
transfer of arithmetic-model weights. It mines the training ladder to find where
our arithmetic-frontier lessons may matter: known hard examples, threatened
correct examples, local counterexample replay, candidate scaling and exact
retention gates. A HybridLink latent transition is *not* the same task as a
signed-arithmetic EMP backbone.

## Source boundary

The repository snapshot `main_computer_test-20261028-000121.zip` includes
`tools.nanojev_hybridlink_t00_curriculum_repair` and the shared latent-loop
runner, but it **does not contain**
`tools.nanojev_hybridlink_t01_retention_cutover`. The T01 runner may exist in
your active checkout separately. The diagnostic checks for the file and, when
present, parses its source **without importing or executing it**, to record the
actual CLI and function names. It does not invent a missing T01 interface.

The T00 source exposes these measurable areas: correct answer-stop depth,
early false stops, generated/gold/zero baselines, generated-to-gold cosine,
source-to-gold gain, intact-minus-zero gain, and T00 retention at every rung.
Qwen, Answer Detector, and numerical observers remain frozen during T00
training. Only the HybridLink generator is trained.

## Run against the actual T01 output

```powershell
& $py -m tools.nanojev_hybridlink_t01_ladder_diagnostic `
    --t00-run "C:\Users\subsi\NanoJev\runs\hybridlink_t00_repair_v1" `
    --t01-run "C:\Users\subsi\NanoJev\runs\hybridlink_t01_retention_v1" `
    --output "C:\Users\subsi\NanoJev\runs\hybridlink_t01_ladder_diagnostic_v1"
```

The T01 directory is an example: use the **real directory of the T01 run**.
If the runner source is elsewhere, supply `--t01-source PATH`. The diagnostic
never modifies either run. If the T01 run does not yet exist, it will refuse
rather than manufacture a ladder.

Outputs:

- `diagnostic.json`: complete source provenance, scalar rung chronology, cases,
  explicit evidence gaps, and evidence-driven opportunities.
- `diagnostic.md`: human-readable rung and improvement report.
- `rung_metrics.csv`: T00/T01 per-rung trend for recorded stage evaluations.
- `hard_examples.csv` and `hard_examples.sqlite3`: observed problems,
  near-failures, early stops and cross-checkpoint instabilities. This is
  diagnostic evidence, **not an answer-serving memory or hidden training input**.

## What matters to inspect

1. **T00 retention throughout T01.** A T01 result without a paired T00
   retention check cannot demonstrate the required retention. Use per-case
   records to locate which T00 transitions were endangered.
2. **T01 stop depth versus T00 final hop.** Count premature `ANSWER` exits,
   missed exits, stalled states, and context exhaustion separately.
3. **Answer anchoring.** Check whether each training window reaches the
   terminal answer or is an earlier transition. Earlier windows are useful,
   but cannot stand in for the terminal answer gate.
4. **Generated feedback causality.** Compare intact versus zero latent on
   the *same* cases; raw detector confidence and cosine are observers, not
   evidence of correct arithmetic or a causal geometry representation.
5. **Trajectory and preservation.** Find rungs/steps where T01 improves
   while T00 decays, or where a formerly correct individual transition fails.
6. **Hard-case ledger.** Rank known failures, early false stops, low-confidence
   cases, low target alignment, and inconsistent outcomes. Recheck prior
   successful transitions whenever changing the generator.
7. **Missing instrumentation.** When run artifacts only contain aggregate
   metrics, the diagnostic explicitly says per-case evidence is missing.
   This cannot be fixed by inventing intermediate vectors or guessing labels.

## Next integration, conditional on evidence

Once the T01 code and its actual output schema are available, a separate
instrumentation change can emit (case ID, rung, source, target, generated
trajectory, stop depth, detector probability, reference alignment,
intact/zero contrast, before/after correctness, hash provenance) for *each*
selected case. That makes counterexample-guided T00 rehearsal, per-rung
hard-case banking, gradient-localized generator microedits, and reversible
update scaling scientifically testable. Do not change acceptance gates or
alter trained weights merely because an aggregate metric looks worse.

Neither DEV holdouts nor arbitrary train/test splits are introduced by the
diagnostic. It reads the evidence already generated; future T01 curriculum
probes can generate deterministic new cases as needed. A passing T00 gate is
not full latent competency, and a failing T01 gate is not equivalent to an
arithmetic EMP error.

## Correction: real T01 schema and terminal gate (v1.1)

The first diagnostic incorrectly labelled an accepted T01 run as
`T01_GATE_NOT_PASSED` if **any earlier checkpoint** failed its gate. It also
missed real T01 metrics stored under `t01`, T00 metrics under `t00`, and
named `retention.lost_sources`. This update fixes those errors.

- The `result.json` `accepted_rung: T01` + checkpoint is authoritative for
  cutover acceptance; intermediate `gate_pass: false` steps remain in the
  chronology as historical retention failures. If `result.json` is absent,
  an explicit passing historical gate can establish a **recorded gate pass**,
  but not checkpoint evidence.
- Actual `retention.lost_sources` strings become source-only, known-bad T00
  entries in `hard_examples.sqlite3` and `hard_examples.csv`. Missing targets,
  predictions, and trajectories are **not invented**.
- Nested T01 two-hop information is extracted per step: correct stop depth,
  first/second-step detector probabilities, first/final gold alignment,
  budget exhaustion, and retention counts. All observed stages appear in
  the added `ladder_timeline.csv`.
- The diagnostic distinguishes `T01_GATE_ACCEPTED`,
  `TRANSIENT_T00_RETENTION_LOSS` and
  `DETAILED_PER_CASE_TRAJECTORIES_MISSING`; a passing cutover does *not*
  mean full latent competence or measured causal geometry.

Rerun the same diagnostic command, using a separate analysis output directory
if you wish to keep the original incorrect report for comparison:

```powershell
& $py -m tools.nanojev_hybridlink_t01_ladder_diagnostic `
  --t00-run "C:\Users\subsi\NanoJev\runs\hybridlink_t00_repair_v1" `
  --t01-run "C:\Users\subsi\NanoJev\runs\hybridlink_t01_retention_v1" `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_t01_ladder_diagnostic_v2"
```

No trainer, checkpoint, cached embedding, or model parameter is edited.
