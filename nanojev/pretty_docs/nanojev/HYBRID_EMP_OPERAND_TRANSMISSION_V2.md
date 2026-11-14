# HybridLink Head 2: isolated operand transmission (v2)

## Objective

Repair the first EMP-to-HybridLink training join. Stop trying to train addition
while the input numerical representations are still incorrect. Train and seal
an **operand-only** channel, preserving the existing signed-radius-16 EMP and
zero-line training split, and keeping Head 1 v3 unchanged.

## Why the preceding run failed

The v1 join trained operand identity and delta computation jointly, encoded
small integers into a 24-bit signed-magnitude target, and used a swapped-operand
control which is not a valid counterfactual for commutative addition. Its
bitwise loss could fall by predicting high-order zeros while exact numerical
outputs remained wrong. V1 checkpoints remain untouched.

## Interface and information boundaries

1. The certified Head 1 identifies two signed source UTF-8 literals and their
   Qwen input token locations. Alignment is checked against known input cards;
   oracle positions are never substituted.
2. The original literal bytes, **not Qwen's weak numerical embedding**, supply
   a lossless canonical 6-bit sign/magnitude encoding for signed radius ±16.
   (One sign bit, five magnitude bits, a unique all-zero representation.)
3. A small learned writer maps the bitfield to 64 learned correction values.
   They **replace** Head-1-selected numerical token coordinates. Other input
   embedding coordinates remain unchanged. The reader receives only those
   64 corrected values, not any other coordinates, source bytes, or question.
4. The learner reconstructs each input operand's exact bits. **No delta target
   is generated, no arithmetic computation runs, and no arithmetic resolver is
   trained in this stage.**
5. A fast batch transmission path is equivalent to reading the sparse replaced
   embedding coordinates. The patch verifies that equivalence on an actual
   embedding edit before training and on held-out edits during certification.
   Qwen transformer layers are **not** invoked or fine-tuned in this stage.

## Training and certification

- Existing EMP/local zero-line pair split, separated into TRAIN / DEV / TEST
  with preserved fingerprints. No TEST data used for checkpoint selection.
- Small full-domain integer representation, with signed bits supervised using
  bit BCE plus a per-bit classification margin. Neither head receives the
  numerical sum, and the learner cannot reach the original Qwen coordinates.
- Select the best checkpoint by **whole-pair exact A+B operand reconstruction**;
  bit accuracy only breaks ties. Stop training early only after **two
  consecutive DEV passes** at >=98% whole-pair exactness, with intact beating
  zeroed and genuinely mismatched-value controls by >=10 percentage points.
- Check DEV again before touching sealed TEST. If DEV has not attained 98%,
  refuse certification and leave TEST unread. TEST is evaluated once and
  never reused. TEST requires >=98% exact operand and pair recovery and the
  same control gaps. Only then `resolver_unlocked=true`.
- `resolver_unlocked` is a permission flag for a **future** arithmetic training
  experiment, not a claim of arithmetic correctness, generalization outside
  ±16, or Qwen consumption of the corrections.
- The wrong-input control increments each operand's value, with wraparound
  from +16 to −16; it is **not** operand exchange (which preserves addition).

### Commands (Windows, repo root)

```
python new_patch.py nanojev_hybrid_emp_operand_transmission_v2_patch.zip
python -m tools.nanojev_hybrid_emp_operand_stage --stage preview --device cpu
python -m tools.nanojev_hybrid_emp_operand_stage --device cuda
```

The default output is `C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_emp_operand_v2`.
It is a distinct lineage; existing EMP, zero-line, Head 1 and failed joined
checkpoints are never overwritten. To intentionally continue a **paused** run
with identical arguments, use `--resume`; completed or sealed runs refuse reuse.

Progress markers:
`HYBRID_OPERAND_STEP`, `HYBRID_OPERAND_TRAIN_RESULT`, `HYBRID_OPERAND_GATE`.

## Limits

The bounded TRAIN, DEV and TEST sets have disjoint **operand pairs** but reuse
some numerical values. This experiment proves reliable channel transmission
on bounded values and unseen pair/context compositions, not generalization to
unseen integer magnitudes or long binary strings. The selected 64 coordinates
are proven sufficient as a separate learned channel; they are not proven to
already represent numbers to Qwen. A later gated resolver must use the same
certified encoder weights and train δ from the EMP/zero-line curriculum.

## Sparse BF16 certification audit repair

Training uses the writer and reader directly in FP32 to conserve resources. In
contrast, the post-training gate reloads the checkpoint and writes the learned
codes into Qwen's frozen input embeddings (BF16 on a compatible CUDA GPU).
The previous sparse audit compared those rounded BF16 values against the
unrounded FP32 writer output and could raise
`OPERAND_SPARSE_READOUT_MISMATCH` even after perfect development recovery.

The repair makes an exact **sparse replacement** at Head 1's predicted token
positions and selected dimensions in the actual destination dtype. It never
changes unrelated coordinates or sees a target delta. Both the reloaded DEV
preflight and the one-shot TEST gate now decode the actual edited embeddings
and require 98% exact pair and operand recovery. The original FP32 direct-path
statistics remain separate as additional controls. The audit reports
`dev_edited` and, only after DEV passes, `test_edited`.

If an already trained v2 run crashed during this post-training audit and did
not produce `gate_result.json`, do not retrain. After installing the repair:

```sh
python -m tools.nanojev_hybrid_emp_operand_stage --stage gate --device cuda
```

Keep the existing v2 `best.pt`, `config.json`, and `training_result.json` intact.
This correction changes the gate's numerical semantics, not the trained
weights. A pass certifies transmission into and recovery from edited input
embeddings; it does **not** certify the arithmetic resolver or consumption by
Qwen transformer layers.
