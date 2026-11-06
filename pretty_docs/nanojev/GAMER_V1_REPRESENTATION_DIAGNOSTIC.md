# GAMER V1 — Representation failure localization

## Purpose

Find the **earliest measured decline in register recoverability** between an
original certified `prompt_08` and the flat numerical register readout. This is
an opt-in, read-only **diagnostic**, not a new training stage. It does not
replace the flat-register system, introduce a pointer decoder, update the LoRA,
modify C0 Wide, or promote any checkpoint.

## Full certified run

```powershell
& $py -m tools.nanojev_gamer_v1_staircase representation-diagnostic `
  --run runtime\reports\gamer-v1\transition-005 `
  --device cuda
```

By default all 146 certified games are retained, including 1,752 training
cases and 438 development cases (subject to the run's verified corpus). The
command evaluates the **same** captured cases for base Qwen and its frozen
step-1800 LoRA. It performs two fresh, expensive inference passes across the
original inputs to collect hidden representations. Model downloads, if needed,
use the source manifest's exact model identifier. Hidden states are captured using the **same padded batch size, source
train/dev batch boundaries, tokenizer model inputs, and forward arguments** as
the original C0 feature cache, then reduced immediately on CPU without saving
giant sequence tensors. Span offsets are collected separately from a fast
tokenizer; offset-map arguments never alter the model forward pass.

For a **bounded pipeline smoke** before the full run, use:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase representation-diagnostic `
  --run runtime\reports\gamer-v1\transition-005 `
  --device cuda `
  --repr-max-games 16 `
  --repr-train-per-game 12 `
  --repr-dev-per-game 3
```

The sampling is deterministic, based on game IDs, **not development errors**.
`--repr-max-games 0`, `--repr-train-per-game 0` and
`--repr-dev-per-game 0` each mean no cap. `--repr-perturbations 0` disables
one-register prompt mutation controls; `--repr-skip-base` omits base Qwen if
resource constraints prohibit the matched comparison. `--repr-output DIR`
selects a new folder **outside** the original source run.

The full run is intended to be extensive. Run the bounded smoke first if
hardware memory or elapsed processing time is a concern.

## What is measured

1. **Tokenizer and literal integrity:** exact character offsets, numeric
   token spans/IDs, optional tokenizer decode round-trip, symbolic selectors,
   unresolved repeated-value ambiguity, and missing literal witnesses. Every
   model input is the ORIGINAL prompt, not oracle metadata, math completions,
   factors, table labels or answers.
2. **Frozen base vs frozen LoRA:** embeddings, quarter-, half-, three-quarter-
   and final-layer hidden states from each model on the same input cases.
3. **Feature selection:** original final-token vector, mean of the complete
   token sequence, pooled numeric-token neighborhoods, four position-ordered
   numeric-token buckets, and an **ordered bank of 32 individual numerical
   spans** (the largest certified-generator sample checked had 18 numerical
   candidates). The 32-span feature is captured at the final layer only to
   control RAM usage. No oracle role chooses a token.
4. **Train-only diagnostic probes:** fixed-regularization dual linear ridge
   and RBF-kernel ridge readouts for each `(game, register)` label, trained
   on the identical TRAIN cases and evaluated on identical held-out DEV rows.
   A training-mean baseline, train error, dev error, signed-log error, and
   separate answer-zone/register-zone assessments are reported.
5. **Perturbations:** change one uniquely visible numeric literal in a DEV
   prompt and measure feature changes, probe response for the corresponding
   register, and probe response in *other* registers of that game. Original
   and perturbed numbers are never added to probe training examples.

The original frozen LoRA final-token vector is compared against the certified
cached head features using the **original C0 capture contract**. The experiment
first performs a small **LoRA cache-parity preflight** using source-aligned
first/last batches from the certified train/dev splits, *before* spending time
on the full base and LoRA captures. For sampled game subsets, it reconstructs
each original batch using the omitted partner prompts (never their oracle labels)
so that the comparison is meaningful. The experiment **refuses** to proceed
if the maximum per-case absolute difference exceeds the original 0.1 threshold.
It does not silently relax the threshold or overwrite cached features. If it
still fails, `capture_parity_report.json` ranks the worst mismatches and
`capture_parity.csv` lists all compared cases; the failure is an unresolved
source-capture parity issue, not evidence of lost register information.

The source dataset, original LoRA checkpoint, pinned adapter files and saved
head checkpoint are fingerprinted before and after the experiment. Input
files stay read-only; all outputs are written to a fresh sibling directory.

## Report files

- `final_report.json`: completion status, evaluated cases, final-layer feature
  and decoder comparison, first measured probe degradations, caveats
- `provenance.json`: source fingerprints, frozen adapter, source model, split
  and capture policy
- `events.jsonl`: per-case durable capture events for long runs
- `capture_parity_preflight.json` and `.csv`: early source-cache identity check
  when preflight succeeds
- `capture_parity_report.json` and `capture_parity.csv`: complete frozen-LoRA
  versus certified source-cache comparisons (or the failed preflight evidence)
- `token_fidelity.csv`: numeric literal source offsets, token spans and IDs
- `register_alignment.csv`: lexically verified vs duplicate/ambiguous spans
- `layer_recoverability.csv`: matched probes for every captured layer
- `feature_comparison.csv`: matched feature-selection comparison
- `decoder_comparison.csv`: matched linear/RBF readout comparison
- `register_probes.csv`: per `(game,register,model,layer,feature,decoder)` scores
- `register_predictions.csv`: case-level prediction errors, answer and
  register sign/zero zones
- `zone_recoverability.csv`: separate zero/positive/negative breakdowns
- `first_degradations.csv`: **earliest measured probe degradation**, not a
  proven irreversible information loss
- `register_perturbations.csv`: intervention-linked feature and readout changes
- `failure_report.json`: written if the diagnostic fails, including stage and
  source-fingerprint verification

## Interpretation guardrails

All register readouts are **oracle-conditioned for evaluation**: knowing which
register is being probed is not equivalent to learning to identify that
register in general language. They do **not** constitute a deployable learned
selector. Worse recoverability from one frozen layer or one decoder does not
prove the representation has destroyed information. Better token features
than final-token features would favor an input/capture explanation. Better RBF
than linear recovery on matched features would favor insufficient readout
capacity. Base and LoRA probes can show adapter-related changes but do not
by themselves justify further LoRA training. Small per-game dev cohorts and
perturbed out-of-distribution prompts limit causal conclusions.

**Decision gate:** do not update Qwen, LoRA or the numerical head based solely
on probe MAE. Diagnose the earliest loss *measurable under matched methods*,
then verify a targeted mechanism with an independent controlled experiment.
