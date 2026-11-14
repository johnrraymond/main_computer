# HybridLink Head 2 → Qwen operand-consumption gate (v1)

## Why this stage exists

The Head 2 operand-transmission gate proved that a **separate learned reader**
can reconstruct signed A/B from its selected coordinates in corrected BF16 Qwen
input embeddings. It did not prove that the **Qwen transformer** understands
these coordinates. The Qwen signed-zero-line v2 LoRA training reached
`STAGE_DEV_FAIL` (last DEV exact 0.1944) with intact performance below its
zeroed and mismatched controls. Do not extend the arithmetic training budget
on that lineage without establishing backbone consumption first.

This stage changes *the student*, not the previously certified Head 1, Head 2,
EMP samplers, or signed zero-line objectives.

## Data and model contract

1. The signed radius-16 operand grid is drawn from the existing EMP join's
   pair-disjoint, magnitude-aware TRAIN / DEV / sealed TEST split.
2. Frozen Head 1 extracts actual source numerical spans and token positions.
   Frozen Head 2 writes its six-bit signed code into its certified sparse
   embedding coordinates, using destination BF16 precision when applicable.
3. **Every other coordinate of an original numeric token is zeroed.** Qwen
   receives the edited input through `inputs_embeds` and its own transformer.
4. The native Qwen language-model head, conditioned separately on `First
   operand code:` and `Second operand code:`, predicts one of 33 *one-token
   numerical class codes* (−16 to +16). This is a temporary readout for
   consumption certification, not user-facing textual arithmetic.
5. Only Qwen attention LoRA receives gradients. The frozen heads, embedding
   table, and LM head are not updated. Arithmetic targets, sums, residuals,
   and a separate arithmetic-expert network are **never** supplied.
6. DEV evaluates intact, zeroed and **genuinely mismatched** Head 2 codes
   under identical masking and prompt conditions. No swapped-operand control.
7. A representative DEV probe contains every signed value at least twice;
   two consecutive passing probes select an intermediate checkpoint. The
   separate complete DEV gate, then the sealed TEST gate, cover all 33 values
   with multiple lexical renderings.

### Promotion criteria

- ≥98% exact reconstruction of both operands in a pair.
- ≥98% exact individual-operand reconstruction.
- ≥98% exact recovery **for every signed value** from −16 to +16, with at
  least two samples per value.
- Intact pair accuracy at least 10 percentage points above *each* zeroed and
  mismatched control.
- The full DEV must pass before reading TEST. TEST is read at most once.
- No arithmetic training is unlocked on a failed or missing certificate.

## Install / operate

The parent repo's `new_patch.py` installs the patch ZIP. Use the same Python
interpreter used for Qwen/HybridLink training, and preserve the certified Head
1 and Head 2 v2 output directories.

Preview without loading Qwen:

```text
python -m tools.nanojev_hybrid_emp_backbone_operand_consumption --stage preview --device cpu
```

Train **only the consumption stage** (default cap: 1,800 updates):

```text
python -m tools.nanojev_hybrid_emp_backbone_operand_consumption --device cuda
```

If pausing deliberately, use `--stage train --stop-after 200`; then resume
with `--stage train --resume`. There is no need to retrain Head 1 or Head 2.

The fresh output lineage is:

```text
~/NanoJev/runs/qwen3_06b_hybrid_emp_qwen_operand_consumption_v1
```

Look for `HYBRID_CONSUME_STEP`, `HYBRID_CONSUME_TRAIN_RESULT`, and the final
`HYBRID_CONSUME_GATE`. Success is exactly:

```text
"status": "BACKBONE_OPERAND_CONSUMPTION_PASS"
"gate_pass": true
```

If DEV never passes, expect `CONSUMPTION_DEV_FAIL` and **no test read**. If a
complete DEV check fails at gate time, expect `CONSUMPTION_FULL_DEV_FAIL`, no
TEST read, and no arithmetic promotion.

## Arithmetic continuation (only AFTER consumption PASS)

The existing arithmetic CLI is changed to schema v3 and fails closed unless
it sees a matching, successful operand-consumption certificate. On pass, the
arithmetic harness loads **the certified Qwen operand-decoding LoRA weights**
prior to zero-line acquisition. It does not start from the failed v1/v2 LoRA.

```text
python -m tools.nanojev_hybrid_emp_backbone_zero_line --stage preview --device cpu
python -m tools.nanojev_hybrid_emp_backbone_zero_line --device cuda
```

The v3 arithmetic checkpoint lineage is separate:

```text
~/NanoJev/runs/qwen3_06b_hybrid_emp_zero_line_lora_v3
```

Arithmetic remains subject to the existing exact residual/magnitude-cell,
zeroed/mismatched control, signed-error and consecutive-DEV gates. No outward
promotion can bypass the operand-consumption certificate. Nothing in this
patch itself demonstrates that Qwen already computes signed cancellation.

## Interpretation and limitations

This is a **Qwen backbone consumption** experiment, not a Qwen/CUDA training
result. CPU tests verify control isolation, backpropagation to LoRA,
checkpoint reload, one-shot DEV/TEST gating, and the fail-closed arithmetic
warm-start contract. The code still needs the live CUDA result. Native LM-head
class accuracy is not equivalent to free-form natural-language arithmetic.

Use an independent new held-out population for any claim of unseen-domain
arithmetic generalization beyond the previously inspected radius-16 deck.
