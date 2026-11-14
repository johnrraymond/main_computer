# EMP / zero-line curriculum → Qwen backbone: first numerical-consumption experiment

## Purpose

Head 1 (UTF-8 numerical span locator) passed its bounded lexical gate. Head 2
(`nanojev_hybrid_emp_operand_stage`) recovered exactly both operand identities
from the selected Qwen input embedding coordinates under BF16. Neither result
established **consumption by Qwen's transformer layers**.

This harness makes that missing experiment explicit. It **does not** install
another numeric resolver or train Head 1 or 2 again. Its student is **Qwen's
attention LoRA**; its output head is **Qwen's native language-model head**.

## Training flow and information firewall

1. Refuse execution without `OPERAND_TRANSMISSION_PASS`, the same Head 1 source
   identity, compatible Qwen tokenizer/embedding shape and a valid `best.pt`.
2. Generate the exact bounded ordered-operand cards from the existing EMP/
   signed-zero-line `LocalCurriculum(16)`. Repartition those cards *deterministically*
   into pair-disjoint TRAIN/DEV/TEST by **(signed residual, magnitude band)**.
   The original hashed split leaves several near-zero held-out cells empty;
   here, every one of the nine ±1/zero × magnitude cells has two distinct
   DEV and two distinct TEST operand pairs. The new split fingerprints are
   recorded. This new task does not claim a pristine test continuation from
   the earlier Head 2 experiment.
3. Head 1 identifies the real signed source UTF-8 literals; the extractor
   refuses missed or overlapping numeric spans. A/B from those spans are
   encoded using the **certified six-bit Head 2 operand format**, not the old
   seven-bit arithmetic-result format.
4. The frozen Head 2 writer creates the numerical edit in Qwen's selected
   coordinates. **All other coordinates at numerical token positions are set
   to zero** for every arm, including intact. Ordinary Qwen numeral embeddings
   cannot act as an accidental fallback. Non-numerical context is left intact.
5. Qwen receives these vectors via `backbone(inputs_embeds=...)`. Only the
   attention `q_proj` / `v_proj` LoRA matrices are trainable; Qwen's original
   weights, embedding table and LM head, Head 1 and Head 2 are frozen.
6. The supervisor retains gold `delta = A+B` entirely outside the forward
   inputs. Native Qwen LM logits over **single-token residual class labels**
   are compared with the correct class by cross-entropy. This is a bounded
   *class-coded exact integer* experiment (−1, 0, +1 at entry); it does not
   certify free-form natural-language numerical answers.
7. Evaluate exactly the **same text and positions** under intact, zeroed and
   **changed-value** numerical corrections. The wrong pair is not A/B swapped:
   for commutative addition that would not change the answer. Original numeral
   embeddings remain neutral in all arms.

## Zero-line / EMP methodology

- First stage: balanced `delta ∈ {-1, 0, 1}`, including ordinary
  cancellation and small positive/negative remainders, with A/B bounded ±16.
- Optional preconfigured stage ladder: signed residual radii `1 → 2 → 4 → 8 → 16`
  using the **same trainable Qwen LoRA**; operand radius remains ±16.
- The EMP provider controls the TRAIN cards and maintains smoothed difficulty
  weights using **TRAIN errors only**; no DEV/TEST-driven sample selection.
- At each stage, require **≥98% exactness for every observed signed-residual ×
  operand-magnitude cell**, plus ≥98% total accuracy and ≥10 percentage-point
  improvement over **each** zeroed and mismatched control. Every residual class
  must be represented, and every expected held-out cell must be checked.
- Require **two consecutive DEV passes** before expansion. Failure to pass
  by the stage's training budget stops with `STAGE_DEV_FAIL` and **never reads TEST**.
- Once the configured stages pass, `--stage gate` reopens DEV with the final
  LoRA and, only if DEV still passes, reads the sealed TEST once. The resulting
  `gate_result.json` prevents test reuse.
- Log exact accuracy, signed-error mean and **signed-error standard deviation**;
  the detailed gate report also gives each signed-residual/magnitude cell.

## Commands (Windows, from main_computer repo)

Install:

```powershell
python new_patch.py nanojev_hybrid_emp_qwen_backbone_zero_line_v1_patch.zip
```

Inspect available cards **without touching Qwen or checkpoints**:

```powershell
& $py -m tools.nanojev_hybrid_emp_backbone_zero_line --stage preview --device cpu
```

First bounded Qwen/CUDA experiment (default stage ±1 only, up to 500 optimizer
steps, with DEV evaluation every 100 steps):

```powershell
& $py -m tools.nanojev_hybrid_emp_backbone_zero_line --device cuda
```

For the **complete residual ladder on one weight lineage**, choose the
maximum radius **at creation time**, with a distinct output directory:

```powershell
& $py -m tools.nanojev_hybrid_emp_backbone_zero_line --device cuda --max-residual 16 --output "C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_emp_zero_line_lora_full_v1"
```

Resume a paused run with precisely matching arguments and sources by appending
`--resume`. A `STAGE_DEV_FAIL` result does **not** permit radius promotion; use
its checkpoint as a failed baseline, not as certified weights.

If a trained run did not invoke certification, run the gate alone:

```powershell
& $py -m tools.nanojev_hybrid_emp_backbone_zero_line --stage gate --device cuda
```

Default output:

`C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_emp_zero_line_lora_v1`

Markers: `HYBRID_BACKBONE_PREVIEW`, `HYBRID_BACKBONE_STEP`,
`HYBRID_BACKBONE_STAGE_PASS`, `HYBRID_BACKBONE_TRAIN_RESULT`,
`HYBRID_BACKBONE_GATE`, `HYBRID_BACKBONE_REFUSED`.

## Scientific boundaries

- Actual Qwen/CUDA success is **not yet established** by the CPU toy regression
  tests; the tests cover the implementation, optimizer connection, masked
  numeral controls, split stratification, failure to leak TEST, checkpoint
  recovery and one-shot gate.
- The head edits are used by Qwen proper, but zeroing the remaining original
  numerical-token coordinates makes the experiment deliberately different
  from unconstrained natural-language inference. A later separate probe must
  check whether the learned LoRA remains effective with ordinary numeric token
  embeddings present, and whether the improvement persists on other text.
- Class-coded residuals are not free-form decimal outputs and do not certify
  arithmetic outside ±16 operands or residuals larger than the configured
  radius. To expand the operand magnitudes, Head 1 and the six-bit Head 2 must
  themselves be extended and re-certified.
- The test set is a new pair-disjoint stratified split of previously generated
  cards. It is sealed within **this run** but not statistically independent of
  previous experiment design decisions.
