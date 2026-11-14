# HybridLink Head 1: atomic signed-span alignment (v3)

This patch replaces independent numerical-byte thresholding with a **learned whole-span decision**. Its purpose is to align original UTF-8 signed-integer spans with frozen Qwen input embedding *token positions*. It is **not** an arithmetic model, a bitfield encoder, or an embedding correction producer.

## Decoder and supervision

1. The non-oracle **candidate generator** locates each contiguous ASCII digit run in the original UTF-8 **bytes**, and notes whether a `+` or `-` immediately precedes it. It does not use ground-truth spans. It also proposes digit runs in nonnumeric identifiers such as `tag_8x` as hard negatives. This constrained candidate grammar cannot detect non-ASCII digits, scientific notation, hexadecimal, decimals, or other untrained formats. Such cases are outside the certified domain.
2. A small convolutional encoder reads the raw bytes and a learned span classifier decides **reject / unsigned digit run / sign-plus-digit run**. A leading sign directly following an operand digit or closing bracket is recognized as a binary operator and cannot be attached as a unary sign; the remaining accept/reject and sign choice is learned. These structural constraints preclude missing a digit within an accepted run and preclude editing a detached sign.
3. Training supervision annotates whole signed spans using the radius-16 grammar. Every digit-run candidate receives **one mutually exclusive class label**, trained with categorical cross-entropy. An auxiliary byte/token BCE remains for monitoring. The weak frozen-Qwen bitfield probe is **not** an alignment gate.
4. Predicted whole spans become byte indicators; the known fast-tokenizer offset mapping translates those byte spans into Qwen token positions deterministically. No learned byte-to-token alignment is invented. The sparse embedding-dimension selection is an **uncertified prospective edit interface**; this patch does not apply numerical edits.
5. Development checkpoint ranking places whole-example exact correspondence first; a small development span-CE term resolves equally accurate checkpoints. One-shot test gating remains byte accuracy >=98%, numeric-byte recall >=99%, complete-literal recall >=98%, token IoU >=95%, whole-example exact >=98%, plus exact regression canaries.

## Covered grammar and limits

Train and held-out templates include `0`, `(0)`, `((0+0))`, `8+(-8)`, `-8+(8)`, `Compute 8 + (-8)`, `(+15)`, nested parentheses, punctuation, binary-vs-unary `+` and `-`, UTF-8 words like `Résumé`, and digit-containing **nonnumeric** identifiers such as `tag_8x` and `v12x`. The constrained candidate grammar and regex annotation policy are specifically for **ASCII signed integer literals within [-16,+16]**, not full mathematical expression parsing. Parentheses and operators are not evaluated; no `A+B=delta` label is used.

## Lineage and commands

v3 uses `~/NanoJev/runs/qwen3_06b_hybrid_utf8_alignment_v3`, preserving all v1 and v2 checkpoints. Run:

```powershell
python -m tools.nanojev_hybrid_lexical_alignment --device cuda
python -m tools.nanojev_hybrid_lexical_alignment --stage inspect --device cuda --text "Compute 8 + (-8)"
```

The first command completes train + one-time blind holdout gate. A post-gate inspect can show byte probabilities, predicted whole spans, token byte offsets, and proposed edit token locations. Do **not** treat `selected_dimensions` as certified numerical coordinates or `lexical_bitfield_exact` as a trained bitfield encoder. Neither Qwen's embedding weights nor other checkpoints are modified.

## Verification boundary

Focused regression tests cover atomicity, UTF-8 byte spans, unary/binary signs, negative proposals, no-oracle token mapping, gradient flow, pause/resume, canary inspection, and gate refusal/reuse. An additional full 600-step **toy CPU model** run passed all seven canaries and achieved exact alignment on its held-out synthetic examples. This does not establish actual Qwen/CUDA performance and must not be described as such. On the user's GPU, the independent held-out gate decides acceptance.
