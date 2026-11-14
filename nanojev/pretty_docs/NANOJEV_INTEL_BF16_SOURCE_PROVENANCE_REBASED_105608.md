# Intel HDB BF16 checkpoint provenance — exact rebase

Baseline: `main_computer_test-20261101-105608(1).zip`.

This changes only the Intel-only read-only HDB rescore load procedure. The
original CUDA process reconstructs Qwen in BF16. The generic CPU loader
otherwise used FP32, which changes the frozen-backbone hash (dtype and bytes).
Within the Intel-only rescore scope, Qwen is reconstructed on CPU in the
historical BF16 precision while all source/checkpoint hash guards run normally.
Only after verification does the existing path transfer the model to Intel
DirectML in FP32. Unsupported operators fail instead of routing to CUDA.

The 10:56:08 snapshot adds a mandatory embedded robust-metrics persistence
contract to HDB rescores. Its implementations and call sites remain untouched.
This package only inserts a scoped BF16 load around `hdb._model()` and preserves
all newer balance reporting and fail-closed metrics validation.

Apply (repository root):

    python new_patch.py "C:\Users\subsi\Downloads\nanojev_intel_bf16_provenance_rebased_105608.zip" --dry-run
    python new_patch.py "C:\Users\subsi\Downloads\nanojev_intel_bf16_provenance_rebased_105608.zip"
    .\tools\run_nanojev_qwen_hdb_intel_smoke.ps1

The installer must report `verification: exact`; never force `--allowfuzz`
against a different source. No checkpoint is changed. The training optimizer
is not invoked. `MICRO_SOURCE_HASH_DRIFT` remains a stop condition, now with
source/frozen digest diagnostics. `INTEL_DML_QWEN_READY` then confirms
checkpoint load and model transfer, not yet successful end-to-end Qwen inference.
