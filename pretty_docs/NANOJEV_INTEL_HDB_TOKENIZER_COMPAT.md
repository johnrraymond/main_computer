# NanoJev Intel HDB rescore: Transformers tokenizer compatibility fix

This is a narrow follow-up to `nanojev_qwen_hdb_intel_directml_rescore_patch.zip`.

## Failure reproduced from Windows traceback

`transformers==4.56.2` rejects `extra_special_tokens` in
`tokenizer.add_special_tokens()` even though a tokenizer may expose an
`extra_special_tokens` attribute. The prior code mistakenly checked for the
attribute instead of checking whether it is an accepted registration key.

## Change

Only `tools/nanojev_qwen_hybridagent.py` is changed. Its `_register_latent_markers`
now selects `additional_special_tokens` when advertised by Transformers 4,
while retaining `extra_special_tokens` support for Transformers 5.
The existing list is preserved and marker IDs must remain stable.
No CUDA or training code, weights, optimizer, stage, or checkpoints are changed.

## Apply

From Main Computer root, after applying the earlier Intel HDB patch:

    python new_patch.py "C:\Users\subsi\Downloads\nanojev_intel_hdb_tokenizer_compat_fix.zip"

Then run:

    .\tools\run_nanojev_qwen_hdb_intel_smoke.ps1

No reinstall of PyTorch or Transformers is needed. The wrapper selects Intel
DirectML index 1. If this reaches a new failure (e.g. model transfer or a
DirectML operator), retain the full traceback; do not silently fall back to CUDA.

## Verification

    python -m pytest -q tests/test_nanojev_qwen_latent_marker_compat.py

Mocked tokenizer contract tests are run in the packaging environment. Real
Transformers 4.56.2 / actual Qwen3 DirectML execution must run on Windows.
