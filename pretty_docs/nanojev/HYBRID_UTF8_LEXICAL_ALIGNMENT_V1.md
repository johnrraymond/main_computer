# HybridLink Head 1 — UTF-8 lexical/embedding localization

This stage learns where signed integer literals in original UTF-8 bytes align with Qwen3-0.6B input tokens, and selects a sparse subset of its 1,024 embedding dimensions as candidate edit channels. It does **not** learn arithmetic, A+B, delta, or numerical corrections. Existing Qwen weights and older checkpoints are never modified.

## Components

- **Byte locator**: trainable UTF-8 byte CNN with supervised byte-span labels. The regular-expression scanner is an oracle **only for training and testing**; at inference the edit map uses the learned byte predictions alone.
- **Byte-to-token correspondence**: fast-tokenizer character offsets converted to UTF-8 byte offsets. Token spans that contain two distinct numerical literals refuse rather than claim impossible sub-token localization.
- **Sparse edit gate**: dimension selection learned from per-literal signed-bit decoding using *frozen original Qwen embeddings*, with a sparsity budget. These are candidate edit coordinates, **not** a proven intrinsic numerical subspace of Qwen. Probing individual bits is lexical identity supervision, never arithmetic.
- **Edit interface**: `apply_edit(original_embeddings, gate, supplied_corrections)` is differentiable and non-mutating; stage 1 never supplies corrections or edits the backbone.

Signed literal radius is 16. Grammar and context difficulty vary independently (parentheses, arithmetic-looking punctuation, negative signs, Unicode/UTF-8 contexts). Expressions containing `+` are **not evaluated**. The held-out forms of the test split are only read once by `--stage gate`.

## Commands

From `main_computer`, with the pinned Qwen model in the local Hugging Face cache:

```sh
python -m tools.nanojev_hybrid_lexical_alignment --stage all --device cuda
```

Inspect predicted alignment/edit positions *without oracle annotations* after training:

```sh
python -m tools.nanojev_hybrid_lexical_alignment --stage inspect --device cuda --text "Compute 8 + (-8)"
```

Resume an intentionally stopped run:

```sh
python -m tools.nanojev_hybrid_lexical_alignment --stage train --device cuda --stop-after 100
python -m tools.nanojev_hybrid_lexical_alignment --stage train --device cuda --resume
```

Use `--output PATH` for another independent run lineage. Training prints `LEX_ALIGN_STEP` and `LEX_ALIGN_TRAIN_RESULT`. The one-shot gate prints `LEX_ALIGN_GATE`, with thresholds **byte accuracy >= 98%**, **token IoU >= 95%**, **exact signed-bit identity of individual literals >= 80%**. These are provisional alignment criteria, **not** evidence of arithmetic or permission for HybridAgent recurrent training.

The diagnostic lexical-bit readout uses oracle token spans to isolate **what information is present in the original embeddings**; inferred edit positions are separately assessed using byte/token localization. A full learned bitfield encoder and numerical correction source belong to the next stage.

This code was tested on CPU against a toy fast tokenizer/embedding; actual Qwen CUDA training must be run and inspected on the user's GPU.
