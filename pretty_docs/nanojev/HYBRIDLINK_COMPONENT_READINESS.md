# HybridLink component readiness — numerical radius × rung depth

This extends the existing `tools.nanojev_hybridlink_latent_diagnostics` with two
additional stages, `readiness-preview` and `readiness`. The old `preview`, `t00`,
and `all` stages are unchanged. It does not train, repair, or promote checkpoints.

## 1. Coverage first (fast, no Qwen model instantiated)

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage readiness-preview --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --max-rung 8 --radius 8 --max-cases-per-rung 24 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_readiness_preview_v1"
```

For each T00–T08, the report identifies recorded-chain depth available, signed
residuals within ±8, operand bands (0–8, 9–64, 65–1024, 1025–32768,
32769–1M, >1M), and whether a test is a *complete* original chain or only
its suffix. A missing signed residual × operand-band cell is `missing_from_deck`,
not `passed` or `failed`. Under-sampled cells are distinct from missing ones.

## 2. Measure frozen components and generated latent pathway (slower)

Use the best available **partial** checkpoint to measure the current limits,
not just the older accepted `latest.pt`. The source checkpoint is verified via
its training schema, hash and frozen-detector lineage. It is never overwritten.

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage readiness --device cuda --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_active_frontier_resume_v2\pending.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --max-rung 8 --radius 8 --max-cases-per-rung 8 `
  --max-tokens 192 --min-gold-cosine 0.60 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_readiness_dev_v1"
```

Replace `pending.pt` with that run's `latest.pt` if the partially repaired
checkpoint no longer exists. The diagnostic accepts the original accepted T01,
frontier `latest.pt` or frontier `pending.pt`, with exact lineage checks.

Run the `ood` split (rather than reusing development cases) in a separate output
directory for a more independent magnitude check. Increase
`--max-cases-per-rung` after the first diagnostic to widen measured coverage.
The maximum-per-rung flag limits the sample size; it does **not** synthesize
missing zero-line cases. Generating new targeted signed-zero examples belongs in
a separate curriculum patch.

Optional independent text-only Qwen *prompt-control* (costly; default disabled):

```
--qwen-text-probe-per-rung 1 --qwen-text-max-new-tokens 48
```

This asks Qwen to perform one leftmost addition using a declared few-shot
instruction protocol; the output is scored exactly against the recorded next
text state. It does not use the numeric readout, Answer Detector or HybridLink.
However, this prompt was not used to train the Stage1 vector checkpoint, so
**a text-generation failure cannot by itself prove the frozen Qwen latent
representation is inadequate or that Qwen must be fine-tuned**. Successful
text reductions are useful positive evidence; absent/negative results require
protocol calibration and further isolating tests.

## Measurements, intervention logic, and boundaries

1. **Oracle**: independently reconstructs each leftmost addition from the
   signed leaves, verifies every intermediate text state and final exact sum.
   This establishes data integrity, not model ability.
2. **Frozen Qwen + numerical sidecar**: applies the separately trained
   numerical head to the real frozen Stage1 Qwen hidden vector for **each
   recorded one-step source**, and measures direct/complex decoded integer
   exactness (absolute error <0.5). A failure cannot distinguish the numeric
   head from the frozen backbone; test or retrain them separately.
3. **Qwen + trained Stage1 transition probe**: classifies PROBLEM vs ANSWER and
   retrieves the next teacher target from a fixed, known target-vector pool.
   It is **not open-ended arithmetic generation**. Candidates are present
   deliberately; do not mistake retrieval success for arithmetic solving.
4. **Answer Detector**: tests detector output on *independently encoded gold*
   intermediate and answer states. If it fails here, repair/calibrate detector
   before attributing generated stopping mistakes to HybridLink.
5. **HybridLink**: runs live generated embeddings with **no gold intermediate
   states supplied to the rollout**. Reports correct stopping depth and
   individual stepwise gold-state cosine relative to the configured threshold.
   This distinguishes stopping failures from transition-alignment failures.
6. **Readiness / next experiment**: reports measured success per rung,
   magnitude, and signed residual; scores missing/under-sampled cells separately;
   recommends dataset work, Qwen/probe isolation, numeric head diagnosis,
   detector retraining, or generator repair as warranted. No automatic Qwen
   fine-tuning claims, no promotion.

Outputs: `readiness.md` (rung summary and recommendations), `readiness.json`
(full per-case evidence and historical checkpoint metadata), `coverage.csv`
(every expected signed-residual × operand-band × rung cell), and
`selected_cases.json` (exact audited chains and full-chain/suffix provenance).

Note: T08 corresponds to **nine actual recorded leftmost-addition transitions**;
there are depth-12 records from which T08 suffixes can be drawn. This audit
still does not prove nine transitions from the **original** 12-step root.

The resulting report answers *where the current evidence breaks down*. A claim
that Qwen itself **needs** fine-tuning requires a controlled Qwen-only reduction
baseline in its supported protocol, not just gold-vector proximity or the
failure of another head.

## Focused zero-saddle stage diagnosis (T00–T08, fixed bounds)

Run the **new** bounded diagnostic with the latest preserved/pending checkpoint:

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage readiness --device cuda --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_active_frontier_resume_v2\pending.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --max-rung 8 --radius 8 --operand-limit 1024 `
  --max-cases-per-rung 24 --min-per-cell 1 `
  --min-gold-cosine 0.60 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_readiness_bounded_v1"
```

This constrains all original-chain **intermediate integer literals** to ±1024
and the exact final answer to ±8. The earlier example that listed more than a
million as an operand band is no longer part of this bounded experiment.
With three bands (0–8, 9–64, 65–1024) and 17 possible signed residuals, there
are **51 target cells per rung**. This does not mean the recorded deck contains
51 eligible examples: `readiness-preview` will identify missing cells without
inventing training data.

New outputs, in addition to the earlier reports:

* `training_matrix.csv`: one row per rung and training component, even if
  untested. Contains `SAMPLED_PASS`, `OBSERVED_FAILURE`, `UNMEASURED`, or a
  carefully labeled **proxy**. Contains cases measured and coverage counts.
* `failure_events.csv`: per-case and per-transition observed failure type with
  expected vs observed results. Distinguishes retrieval, gold numeric heads,
  gold detector, generated-state stop, early/late/stalled/budget, latent cosine,
  and generated-state numeric readouts.
* `readiness.json`: includes `component_matrix`, `next_training_stages`, and
  aggregate `failure_counts` for machine-readable planning.
* `readiness.md`: compact table of which frozen/trained components need
  diagnosis at every rung, alongside the existing numerical coverage report.

**Important attribution limits:** failure of a Qwen+numeric head does not prove
backbone failure. Stage1 retrieval is a known-candidates proxy, not autonomous
Qwen reduction. Error on generated detector input may result from poor latent
generation, detector generalization, or both. The final-state numeric readout
is observational, **not an emitted decimal answer**: the emitted-output stage
is explicitly `UNMEASURED` until that protocol is exercised separately. This
is diagnosis only, not permission to unfreeze a model or certify a radius.

## Deterministic full-chain zero-saddle diagnostic deck (new)

The recorded dev deck was sparse, particularly for exact zero at T01–T08.
Instead of mistaking missing cells for learned competence, use the **read-only,
synthetic bounded evaluation deck** by adding `--generate-zero-line`.
This does not train Qwen, HybridLink, or any other component.

The generated deck covers the complete **T00–T08 × result −8…+8 × operand
magnitude band** matrix. For the fixed ±1024 operand ceiling, the three
bands are `0-8`, `9-64`, and `65-1024`: 9 × 17 × 3 = **459 cells**.
Each case has a complete, oracle-verified canonical leftmost-reduction chain,
including every intermediate PROBLEM and final ANSWER. The initial pair
cancels a band-sized number toward a small signed residual, and subsequent
partial reductions travel around zero. Every literal and running partial sum
is within the configured operand ceiling. Each rung has three mandatory exact
zero cells (one per band), plus every nonzero signed residual through ±8.

### Fast deck preview / export (no loaded Qwen weights)

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage readiness-preview --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --generate-zero-line --generated-per-cell 1 `
  --max-rung 8 --radius 8 --operand-limit 1024 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_zero_saddle_preview_v1"
```

The output now includes `zero_line_deck.jsonl` in addition to
`selected_cases.json`, `coverage.csv`, `readiness.json`, and `readiness.md`.
In generated mode `coverage.csv` differentiates `available_generated` from
`available_recorded`; every filled cell is `generated_sampled`, **not**
`certified` or `previously trained`. `deck_sha256` identifies the exact deck.
The new `--generated-per-cell` accepts 1–8 **distinct** cases per cell; two
produces 918 cases. `--max-cases-per-rung` intentionally does **not** truncate
generated cases, because that would recreate the silent coverage holes.

### Measure components on generated cases (CUDA, slower)

First isolate T00 (51 full chains), then increase `--max-rung` to 2, 4, 8
as desired. Each new run must use a **new output directory**:

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage readiness --device cuda --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_active_frontier_resume_v2\pending.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --generate-zero-line --generated-per-cell 1 `
  --max-rung 0 --radius 8 --operand-limit 1024 `
  --min-gold-cosine 0.60 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_zero_saddle_t00_v1"
```

The generated deck still passes through existing per-transition component
attribution and failure-event reporting. Frozen Qwen one-step text generation
remains an **optional control**, not automatically certified by a gold-vector
similarity or retrieval match. Generated problems are *not certified absent*
from all earlier model training; training-overlap verification is explicitly
false. An exact pass proves only the sampled instances and their stated
metrics; independently correct numeric output and actual text emission
still require their respective tests. The full 459-case CUDA run may be
computationally expensive; preview is cheap.

## LM-head full-packet continuation control (read-only)

The `lm-packet` diagnostic is a separate control requested after the T00
zero-line matrix showed that both frozen numeric sidecars were unreliable.
The normal HybridLink runtime is **headless**; this test explicitly invokes
Qwen's original vocabulary/LM head, which was **not** trained by Stage1.
It does not retrain or promote the checkpoint, and never feeds a gold answer
or teacher embedding into the generated sequence.

Four control conditions (selected with `--lm-packet-modes`):

- `qwen_free`: Frozen Stage1 Qwen continues `PROBLEM: ...\n` and must itself
  emit a complete `ANSWER: signed_integer` response line.
- `qwen_prefilled`: Same Qwen continues `PROBLEM: ...\nANSWER: ` and must
  emit the signed decimal integer. The *label*, not its value, is prefilled.
- `hybrid_free`: The generated HybridLink latent loop runs to its actual
  detector exit, then Qwen's LM head is allowed to emit the packet text
  directly from that terminal hidden state.
- `hybrid_prefilled`: Same generated latent prefix, but an explicit
  `\nANSWER: ` marker is inserted before LM-head decoding. This is a
  counterfactual marker-conditioned readout, **not** normal system behavior.

Each response is saved literally in `lm_packet_cases.csv` and
`lm_packet_report.json`, along with parsed number, format validity, exact
integer result, and HybridLink's actual stop depth. Even a wrong integer is
observable; malformed output and token-budget exhaustion are separate
outcomes. A correct *text* answer does not establish that the current headless
production architecture would produce it unaided.

First run (all 51 bounded T00 cells, Qwen direct output only):

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage lm-packet --device cuda --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --generate-zero-line --generated-per-cell 1 `
  --max-rung 0 --radius 8 --operand-limit 1024 `
  --lm-packet-modes qwen_free,qwen_prefilled `
  --lm-packet-max-new-tokens 24 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_lm_packet_t00_qwen_v1"
```

To include the HybridLink terminal state, add `--detector-checkpoint`,
`--checkpoint`, and `--cache-dir` as in normal readiness runs, and use
`--lm-packet-modes qwen_free,qwen_prefilled,hybrid_free,hybrid_prefilled`.
Use `--lm-packet-cases-per-rung 3` for a fast smoke test before the full run.
Full-prefix greedy decoder execution is deliberately conservative and can be
slow; token sampling is deterministic.
