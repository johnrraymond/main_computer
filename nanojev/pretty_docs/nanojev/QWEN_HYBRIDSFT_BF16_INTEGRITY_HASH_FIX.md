# HybridSFT BF16 frozen-weight hashing and pre-step recovery

The self-conditioned Qwen3-0.6B HybridSFT run uses BF16 backbone parameters on CUDA. The previous frozen-backbone SHA-256 function attempted `tensor.cpu().numpy().tobytes()`, which fails because NumPy does not support PyTorch's `torch.bfloat16` scalar type. This refusal occurs before the first optimizer update.

The fix hashes a contiguous CPU tensor through an unmodified `uint8` view. It hashes the **actual stored bytes**; no BF16→FP32 conversion, rounding, altered metadata, or weakened integrity check. The byte stream and digests for previously supported NumPy dtypes (including float32) remain identical. The existing assertion still rejects any trainable non-LoRA backbone parameters and catches changes to any frozen parameter.

Because the original run writes `run_config.json` before checking the hash, a failed run may have left an output directory containing that file alone. The patched trainer automatically recognizes **only** that single-file pre-step state, checks the full configuration and source fingerprints for exact agreement, and restarts without `--resume`. Existing `last.pt`, `best.pt`, unexpected files, or changed configuration are refused. It never overwrites a trained checkpoint.

## From `main_computer`

Install using the repository's `new_patch.py`:

```text
python new_patch.py nanojev_qwen_hybridsft_bf16_integrity_fix_patch.zip --dry-run
python new_patch.py nanojev_qwen_hybridsft_bf16_integrity_fix_patch.zip
```

Then use the **same** command that failed:

```text
python -m tools.nanojev_qwen_hybridagent_self_conditioned --device cuda
```

A valid first-step recovery prints `HYBRID_SELF_CONDITIONED_RECOVERED_PRESTEP=<output>` before optimizer-step records. A completed run reports `HYBRID_SELF_CONDITIONED_RESULT` with mechanical-training results, not a certification of reasoning capability.

## Tests

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_zero_line_backbone.py tests/test_nanojev_qwen_hybridagent_self_conditioned.py
```

The tests cover raw BF16 byte hashing, detecting BF16 changes, preserving legacy FP32 hashes, recovering from a simulated exact pre-step failure, refusing config drift or unexpected artifacts, and the existing optimizer/checkpoint contracts. CUDA live verification is still required on the target machine.
