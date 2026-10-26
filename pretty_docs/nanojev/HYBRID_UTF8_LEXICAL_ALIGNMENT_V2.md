# HybridLink Head 1: source alignment repair (v2)

This is a corrective revision of the **byte -> token -> input-embedding edit-map** experiment. It does **not** calculate arithmetic, reconstruct exact number identity from UTF-8 at inference, update Qwen embeddings, or certify which of Qwen's hidden dimensions encode numerical values.

## Evidence and defect

The v1 held-out run had 99.49% byte accuracy and 95.29% token IoU but only 13.73% exact signed-bit recovery. A direct `inspect` on `Compute 8 + (-8)` found only the second signed literal, missing byte 8 (the first `8`). The bit probe consumed **frozen Qwen input embeddings** using oracle-provided literal token positions; it did not consume exact UTF-8 signed bitfields. Its accuracy was therefore not evidence that exact numerical information had been acquired. Furthermore, v1 selected the best checkpoint by *token IoU plus bitfield accuracy*, which ranked step 400 above the more accurate step 600 alignment model.

## Scope of v2

- Independent checkpoint lineage: `~/NanoJev/runs/qwen3_06b_hybrid_utf8_alignment_v2`. Do not overwrite or reuse v1 checkpoints.
- More training templates vary spacing, parentheses, and terminal punctuation.
- Source-byte and aligned-token positive-label weighting prevents background prose bytes from dominating BCE.
- Development checkpoint scoring follows token IoU, positive numeric-byte recall, complete-literal recall and whole-example exactness. It excludes the frozen-embedding bitfield diagnostic.
- The one-time held-out gate requires byte accuracy >= 98%, token IoU >= 95%, numeric-byte recall >= 99%, complete-literal recall >= 98%, whole-example exactness >= 98%, plus exact source-byte alignment on seven **known regression canaries**, including `Compute 8 + (-8)`. Those canaries are *not a substitute for a blind test*.
- Inspection now prints oracle expected numeric byte offsets for diagnosis alongside model predictions, and explicitly reports missed bytes, false bytes and complete/incomplete literals. Oracle spans remain excluded from edit-map inference.
- The bitfield probe remains an exploratory diagnostic, not an accepted claim or gate. Dimension indices are candidate edit slots rather than certified intrinsic Qwen numerical coordinates.

## Run

```powershell
python -m tools.nanojev_hybrid_lexical_alignment --device cuda
python -m tools.nanojev_hybrid_lexical_alignment --stage inspect --device cuda --text "Compute 8 + (-8)"
```

After head-1 source alignment is certified, the subsequent lexical encoder can take the *actual signed literal bytes/bitfield* and train an embedding correction. Stage 1 alone does not establish an accurate numerical resolver.
