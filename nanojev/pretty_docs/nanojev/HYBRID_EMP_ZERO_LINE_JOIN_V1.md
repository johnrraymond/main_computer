# HybridLink Head 2 ← EMP / zero-line shared curriculum (v1)

## Scope

The existing EMP and zero-line experimental models are *not* replaced, copied, or modified. This is a **training integration**: `tools.nanojev_hybrid_emp_curriculum` provides a shared card schema and calls the existing signed-radius and outward samplers; `tools.nanojev_hybrid_emp_join` trains a **new HybridLink numerical writer/reader/resolver** on those cards. Both systems are supervised against the exact same mathematical invariant (`a+b=delta`). The new model receives `a` and `b` as source literal bits **only**, never its target `delta` or an externally computed answer at inference.

The initial experiment is deliberately confined to the **certified Head 1 UTF-8 lexical radius ±16**. Full operand pairs in `[-16,16]²` include residuals from -32 through +32. The numerical output encoding therefore reserves more than 5 magnitude bits. The default width 24 uses one sign bit and 23 magnitude bits, sufficient for eventual residuals through ±4,000,000; widening requires an explicitly new lineage, not silent checkpoint reinterpretation.

The provider imports and reuses **original** modules:

- `nanojev_zero_line_signed_radius16.pair_for_max`, `band_of` — exact signed near-cancellation pairs.
- `nanojev_zero_line_outward_return.generate_batch` — actual outward curriculum cards.
- `nanojev_zero_line_sampled_radius_ladder.evaluation_cards` — fixed out-of-sample magnitude-family probes.
- EMP dual-bank control principle: bounded TRAIN-only error weighting. The student **does not** copy or distill an EMP model; the gold arithmetic target is the only supervision.

`outward_cards` is an interface for later radius stages. **A Head 1 checkpoint certified only at ±16 cannot be used for larger literals.** The joined trainer refuses such input until that lexical boundary is explicitly expanded and tested. The outward training stage is not silently claimed as implemented or certified.

## Flow and trust boundary

1. EMP provider creates a frozen TRAIN/DEV/TEST split by hashing each ordered operand pair; the TRAIN-only curriculum balances signed residuals and prioritizes demonstrated TRAIN mistakes. DEV and TEST labels cannot affect training sampling. The full local deck has 1,089 ordered pairs; no pair crosses splits.
2. The card is rendered as an arithmetic **question** with lexical variations. The pretrained, `ALIGNMENT_PASS` Head 1 reads raw UTF-8 and predicts two signed spans. The fast tokenizer's existing offsets map those spans to exact Qwen token positions; no oracle span locations are injected. Any missing, mismatched, or overlapping span fails closed.
3. Exact binary fields for A and B are assembled from the **predicted** raw literal spans. This operation only encodes the two operands. Head 2's learned writer inserts their representation solely at Head 1's selected numerical token/dimension mask (`apply_edit`), without modifying frozen Qwen embeddings.
4. The learned reader reconstructs operand bitfields **from edited embeddings**, and the learned resolver predicts the signed bitfield of delta. The label is used only for supervised loss/evaluation.
5. A and B exact **signed-bitfield** reconstruction and delta exact **signed-bitfield** prediction (including zero sign) are independently scored; integer correctness is reported separately. Zeroed/shuffled edits are scored as ablations; the training loss discourages answers that ignore the latent correction.
6. Checkpoints are selected only on DEV. A single sealed TEST is run after all training, records its result once, and refuses repeat. It cannot promote radius expansion after a failure.

This is a neural learned resolver, **not** a fixed binary adder. Good performance at radius ±16 would not demonstrate arbitrary-length carry generalization.

## Default GPU workflow (RTX 3060, Qwen3-0.6B)

The existing Head 1 v3 `~/NanoJev/runs/qwen3_06b_hybrid_utf8_alignment_v3` must have a genuine `ALIGNMENT_PASS`, `best.pt`, and matching identity. It is not retrained.

```powershell
python -m tools.nanojev_hybrid_emp_join --stage preview --device cpu
python -m tools.nanojev_hybrid_emp_join --device cuda
```

Default output: `~/NanoJev/runs/qwen3_06b_hybrid_emp_join_head2_v1`. The default `all` operation trains 1,200 steps and then runs the one-shot TEST. For a shorter **partial run without exposing TEST**, e.g. to check VRAM:

```powershell
python -m tools.nanojev_hybrid_emp_join --stage train --device cuda --stop-after 10
python -m tools.nanojev_hybrid_emp_join --stage train --device cuda --resume
```

Use another `--output` for independent trials. Never rerun completed TEST or overwrite an existing run to tune against its scores.

CLI exposes `--radius 16`, `--bit-width 24`, `--steps 1200`, `--eval-every 100`, `--lr 0.001`, `--head1-output`, `--resume`, and `--stop-after`. Initial EXP cannot exceed radius 16: `HYBRID_LEXICAL_RADIUS_NOT_CERTIFIED` / `HYBRID_LOCAL_RADIUS_UNCERTIFIED`.

## Reports and gates

- `HYBRID_JOIN_PROVIDER`: provider schema, split counts, question rendering.
- `HYBRID_JOIN_STEP`: TRAIN losses, DEV operand-bit exactness, delta exactness, zero/shuffled ablations, sign/residual/magnitude cells, 5% score, signed-error population standard deviation and CUDA peak allocation.
- `HYBRID_JOIN_TRAIN_RESULT`: trained but uncertified; no final TEST has been read.
- `HYBRID_JOIN_GATE`: one-shot separate TEST, source hashes, strict result. The local gate requires >=98% alignment, >=98% joint operand identity, >=80% exact delta, and >=10 percentage points intact advantage over **both** zero and shuffled corrections. Any failure means **no outward promotion**.

Checkpoints retain optimizer state, Python and Torch RNG state, training cursor, TRAIN-only difficulty weights and provenance. Head 1's `gate_result`, config and checkpoint SHA-256 fingerprints are bound to the run; source sampler/provider file hashes are also bound. Original EMP/HDB checkpoints and backbone parameters remain unchanged.

## Verification boundary

The CPU harness uses a tiny tokenizer, an embedding table, and an oracle-free *structural stand-in locator* to test training integration, locality, gradients, splits, pause/resume, and sealed TEST behavior. That stand-in is not a substitute for the real Head 1 model: the CLI always uses the actual certified neural locator and Qwen's frozen input embeddings. Passing CPU tests establishes functional integration, **not** final CUDA arithmetic accuracy. Do not infer arithmetic certification from decreasing bit loss.
