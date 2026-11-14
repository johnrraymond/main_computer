# Qwen relative-deformation glass — repeated refinement and preservation audits

## Motivation and exact continuity

The initial two-pass smoke completed 20,149 cases, leaving 70 teacher-forced
wrong tokens in the frozen Qwen baseline and 56 after the first 70-update glass
pass (68 modes, 69,632 correction parameters). Its 162 greedy examples had
zero corrected answers. That **does not demonstrate** reusable corrective
memorization. This patch tests that hypothesis **without rerunning Qwen's first
pass or replacing baseline.pt/glass.pt**.

The new script consumes the *unchanged* checkpoint trio:
`source_snapshot.pt`, `baseline.pt`, and `glass.pt`. It writes only to
`<output>/glass_refinement/`, and verifies frozen LoRA before/after audit.
The live ladder `working.pt` may continue changing without affecting this run.

## Apply

```powershell
python new_patch.py "C:\Users\subsi\Downloads\nanojev_qwen_relative_deformation_glass_refinement_audit_patch.zip"
```

## Execute with existing `$glassArgs`

Use the **exact** `$glassArgs` array from the first experiment. It supplies
checkpoint paths, model, output directory, corpus dimensions and CUDA device.

```powershell
# Step 1 — scan the SAME entire corpus with frozen Qwen, cache matched hidden
# activations once, and refine the 68 existing glass modes for 8 epochs.
& $py -m tools.nanojev_qwen_lm_deformation_refine `
    --stage refine @glassArgs `
    --refine-epochs 8 `
    --refine-lr 0.001 `
    --protect-per-mode 8 `
    --protect-ratio 2

# Step 2 — paired baseline / original-glass / refined-glass teacher-forced
# audit of the COMPLETE corpus, plus greedy decoding of every baseline error
# case and stratified controls from every rung/signed-delta cell.
& $py -m tools.nanojev_qwen_lm_deformation_refine `
    --stage audit @glassArgs `
    --greedy-audit targeted

# Step 3 — definitive preservation test: independently decode ALL 20,149
# cases, comparing frozen Qwen against the refined glass. Restartable audit.
& $py -m tools.nanojev_qwen_lm_deformation_refine `
    --stage audit @glassArgs `
    --greedy-audit full `
    --eval-batch 8

# All previously written reports, no model load:
& $py -m tools.nanojev_qwen_lm_deformation_refine `
    --stage status `
    --output 'C:\Users\subsi\NanoJev\runs\qwen_deformation_glass_smoke_v1'
```

**The full greedy audit is computationally substantial.** It runs actual
autoregressive Qwen generation with and without the glass for each selected
case. It checkpoints every 20 batches to `greedy_full_partial.json`, and
resumes on re-invocation. Only after it finishes does
`greedy_full.json` record `full_corpus_tested=true`. The initial `targeted`
audit is cheaper and exercises all cases that show token errors in any of the
three teacher-forced configurations, plus original greedy failures and at
least two stratified controls per rung/delta cell.

## Mechanism

The frozen Qwen is passed once over every selected arithmetic example to
collect final hidden states at supervised target-token positions. Every wrong
next-token occurrence **inside** a recognized activation mode is kept. Nearby
correct token activations are reservoir-sampled and retained for preservation
training, limited to at most 2 per wrong token in each mode (and an absolute
per-mode budget of 8 by default). This prevents overwhelmingly many already
correct cases from forcing every learned deformation back to zero.

All later refinement epochs replay the cached hidden vectors and correct
next-token targets against the **unchanged frozen LM head**. Qwen is not run
again during refinement, because its vectors cannot move. Its LoRA, embeddings,
LM head, and glass's projection and mode keys are all frozen. **Only the existing
correction-vector bank changes.** The original `glass.pt` remains untouched;
new weights live in `refined_glass.pt`. On interruptions, both optimizer state
and epoch/batch offset are persisted in `refinement_partial.pt`.

Wrong-token positions train cross-entropy. Correctly predicted positions in
the *same correction neighborhoods* receive an output-deformation penalty
that encourages the glass to stay zero. The correction remains identically
zero at inputs not matching a known mode. This is **not** a guarantee of no
interference inside a known mode: the full audit is needed to prove that.

## Results and falsification

Files in `<output>/glass_refinement/`:

- `activation_cache.pt` — immutable corpus-wide frozen activations **only where
  their projected signatures match a mode**, with matched/unmatched-error counts.
- `refined_glass.pt` — checkpoint with unchanged mode keys and projection;
  `refinement_partial.pt` only exists for an interrupted pass.
- `refine_training.json` — correction-only optimizer steps, cached error and
  protected-correct examples, parameter count and elapsed time.
- `teacher_forced_audit.json` — full-corpus baseline/original/refined loss and
  wrong-token counts, every rung/delta cell, each mode's fixes/regressions, and
  **distinct problem counts** for learned-mode reuse.
- `greedy_targeted.json` — all identified token-failure cases and stratified
  controls: actual emitted answers, corrections and new failures.
- `greedy_full.json` — full-corpus actual generated-answer comparison, with
  full-corpus proof only if all cases were evaluated.

A mode that fixes two wrong tokens in one problem **does not** count as
cross-problem generalization. The by-mode report includes
`distinct_fixed_cases` and `modes_correcting_multiple_distinct_cases`.
Positive compression evidence requires several distinct cases fixed by the
same mode without regressions. If the glass lowers CE but does not fix emitted
answers, the architectural goal remains **unproven**.

The corpus is deliberately the same memorization corpus as the first smoke.
This measures retention and mode reuse, **not out-of-distribution arithmetic
generalization**. A later experiment should use additional operand magnitudes
and unseen complete reduction chains without changing the correctness oracle.

## Practical notes

- Keep the existing `source_snapshot.pt`, `baseline.pt` and `glass.pt` files;
  no first-pass retraining is necessary.
- The `refined_glass.pt` is immutable for a given training policy. To compare
  different epoch counts, seeds or preservation weights, use a separately
  copied source run under a different `--output` path; never overwrite baseline.
- If the full greedy audit is interrupted, rerun the same command with the same
  options. Previously completed batches are not regenerated.
- For a quick integration test only, use the original smoke's `--case-cap`
  setup in a **different** source-run directory. Do not use a cap when making
  full-corpus claims.
