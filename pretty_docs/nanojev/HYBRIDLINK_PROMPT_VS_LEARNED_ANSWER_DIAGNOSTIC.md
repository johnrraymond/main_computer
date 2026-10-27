# HybridLink prompt/marker/latent ablation — v3

## Purpose

Read-only diagnostic of the **existing Qwen3-0.6B HybridAgent HybridLink** and its saved `numerical_pre_format` and `format_activated` configurations; optionally `previous_joint`. The still-disabled **QW5 Stage 2** is not trained, used, or certified. Sources are fingerprint-checked before/after and output cannot overlap a source. This experiment evaluates prompt/operation understanding, latent-marker disruption, blank latent-context disruption, learned feedback contribution, answer extraction, and end-of-answer formatting.

## Matched, causal controls

All inference arms receive the *same input question* (direct/scaffold) ending in the **trained `Reasoning: ` prefix**. Neither prompt supplies the oracle answer. Each output is scored against the same expected signed integer.

| `control` | `depth` | Execution path | Interpretation |
| --- | ---: | --- | --- |
| `text_only` | 0 | Normal Qwen text continuation; **no** latent markers | Ordinary answer baseline |
| `marker_only` | 0 | Append atomic `<latent></latent>` token IDs, **without any latent embeddings or HybridLink invocation**, then continue text | Isolate entry/exit marker effect |
| `zero_latent` | 1, 2, 4 | `<latent>` + *N* zeroed link vectors (identical latent positions) + `</latent>`; preserve Qwen KV history | Test added latent positions with no learned signal |
| `intact` | 1, 2, 4 | `<latent>` + *N* trained link vectors + `</latent>`; same model/Qwen KV path | Test learned HybridLink signal |

**Interpretation:** `marker_only - text_only` measures marker cost. `zero_N - marker_only` measures the combined effect of adding *N* latent positions and their zero embeddings; it is **not** a pure time/sequence-length effect, because the zero embeddings still run through Qwen. `intact_N - zero_N` is the important paired feedback intervention at the **same markers, depth, and prompt**. This diagnostic cannot prove that a successful intact model uses the correct numerical semantics; it is a first causal-control screen.

The diagnostic uses the existing `paired.decode_once` inference implementation and keeps the same KV-cache/correct zero-head intervention. Marker-only is implemented by passing the exact two atomic marker IDs to `paired.decode_once(..., depth=0)`. That explicitly avoids the usual `model.rollout(..., depth=N)` call which creates latent input positions.

## Answers and termination scored independently

Every record saves `first_completed_answer`, explicit `strict_answer`, `first_answer_kind`, `single_answer_terminated`, `generated_new_question`, `multiple_answer_labels`, `extra_output_after_answer`, `completion_window_limit_reached`, `generation_token_budget_reached`, plus `marker_tokens_present` and `latent_positions`.

A correct `Answer: -4\nQuestion: ...` is a **correct first numerical answer but an incorrect termination**. A second generated question cannot contribute answer credit. The 300-character observation window remains an explicit scoring limitation. A generation that exhausts `--max-new-tokens` without an EOS is also **not credited with clean termination**, even when its first numerical answer is correct. Summaries include separate `first_completed_accuracy`, `strict_accuracy`, and `terminated_accuracy` per model, rung/sign, prompt style, control and depth.

Paired comparisons are restricted to identical `(configuration, problem id, style)` for mode contrasts; direct/scaffold uses identical `(configuration, problem id, control, depth)`. Every comparator reports strict/first/terminated wins, losses and ties. The recorded comparisons cover text-vs-markers, zero-vs-markers and text, intact-vs-zero and text, at each requested depth. These are descriptive comparisons (not significance or certification claims).

## How to apply and run (PowerShell)

Apply `hybridlink_marker_latent_depth_ablation_patch.zip` using `python .\new_patch.py '<downloaded zip>'`. It contains just this diagnostic, its regression tests and this guide, with no manifest and **no `.pt` model files**.

Preview prompts without loading weights:

```powershell
& $py -m tools.nanojev_hybridlink_prompt_learning_probe `
  --stage preview --device cpu `
  --rungs Z0,R2,T08 --band 0-4 --scales 8
```

Focused first test, to minimize repeated prompts and console output (Z0 direct only, two model configurations, five questions × eight inference conditions = 80 records):

```powershell
& $py -m tools.nanojev_hybridlink_prompt_learning_probe `
  --device cuda `
  --rungs Z0 `
  --band 0-4 `
  --scales 8 `
  --styles direct `
  --depths 0,1,2,4 `
  --max-new-tokens 48 `
  --quiet-cases `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_marker_depth_Z0_v3"
```

Then the full semantic comparison (Z0, R2, T08; direct and scaffold):

```powershell
& $py -m tools.nanojev_hybridlink_prompt_learning_probe `
  --device cuda `
  --rungs Z0,R2,T08 `
  --band 0-4 `
  --scales 8 `
  --styles direct,scaffold `
  --depths 0,1,2,4 `
  --max-new-tokens 48 `
  --quiet-cases `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_marker_depth_full_v3"
```

**Use new output directories**. Both runs print `HYBRID_PROMPT_RESULT=` and write full records and prompts to `result.json`. Remove `--quiet-cases` to print every `HYBRID_PROMPT_CASE=`. Preview does not load the model or run inference.

## Reading failure signatures

1. Text correct, marker-only wrong: the marker *round trip itself* is problematic. Do not blame HybridLink weights yet.
2. Marker-only correct, zeroed latent fails: occupying recurrent positions or the transition between them disrupts readout.
3. Zeroed correct, intact wrong: learned HybridLink signal is harmful at that depth; investigate head alignment/training.
4. Intact beats zero at matched depths: evidence the learned feedback contributes something. Check numerical correctness **and** termination, and replicate with other seeds/scales.
5. One or two steps help but four hurt: suspect recurrent accumulation or inappropriate fixed-depth exit; consider an adaptive stopping test.
6. Direct correct, scaffold wrong with the same `Reasoning:` suffix: additional explicit instruction may interfere, rather than reveal arithmetic ignorance.
7. A first correct signed answer followed by `Question:` is **not** a clean answer. Treat termination as separate training target.

The results do not establish or relax QW5's numeric certification, do not activate Stage 2, and do not change any saved checkpoint.
