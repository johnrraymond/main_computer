#!/usr/bin/env python3
"""Isolated Intel DirectML backend selection for read-only NanoJev HDB rescoring.

Nothing in this module initializes CUDA, uses a CUDA device, or changes checkpoints.
Any unsupported DirectML operation fails visibly rather than routing to CUDA.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

# Scoped to read-only Intel rescoring. Never change CUDA or normal CPU loading.
_historical_bf16_cpu_source = ContextVar('nanojev_intel_bf16_cpu_source', default=False)


@contextmanager
def historical_bf16_cpu_source():
    """Load frozen BF16 tensors on CPU so checkpoint provenance stays byte-exact.

    It is not permission to accept a source mismatch. The source SHA and
    frozen-tensor SHA guards remain mandatory and fail closed.
    """
    token = _historical_bf16_cpu_source.set(True)
    try:
        yield
    finally:
        _historical_bf16_cpu_source.reset(token)


def provenance_cpu_precision(device):
    """Only CPU loads inside the Intel rescore scope use historical BF16."""
    return 'bfloat16' if str(device) == 'cpu' and _historical_bf16_cpu_source.get() else None


def intel_device(adapter_index: int = -1):
    try:
        import torch
        import torch_directml
    except ImportError as exc:
        raise RuntimeError('INTEL_DML_ENVIRONMENT_MISSING: run with the isolated '
                           'DirectML Python and install torch-directml') from exc
    names=[torch_directml.device_name(i) for i in range(torch_directml.device_count())]
    intel=[i for i,name in enumerate(names) if 'intel' in name.lower() and
           ('iris' in name.lower() or 'xe' in name.lower())]
    if adapter_index == -1:
        if len(intel)!=1:
            raise RuntimeError(f'INTEL_DML_ADAPTER_SELECTION_AMBIGUOUS: {names!r}')
        adapter_index=intel[0]
    if adapter_index not in intel:
        raise RuntimeError(f'INTEL_DML_REFUSES_NON_INTEL_ADAPTER: '
                           f'index={adapter_index}; names={names!r}')
    device=torch_directml.device(adapter_index)
    if device.type != 'privateuseone':
        raise RuntimeError('INTEL_DML_UNEXPECTED_DEVICE_TYPE: '+str(device))
    # An actual DirectML operation, not merely the reported adapter name.
    probe=torch.tensor([2.0,3.0],device=device)
    outcome=(probe*probe).cpu().tolist()
    if outcome != [4.,9.]:
        raise RuntimeError('INTEL_DML_DEVICE_PROBE_FAILED')
    return device, names[adapter_index]


def prepare_model_for_intel(backbone, device):
    """Use eager attention, FP32 and a frozen model; no silent CPU model fallback."""
    import torch
    if device.type != 'privateuseone':
        raise RuntimeError('INTEL_DML_BAD_TARGET_DEVICE')
    # Qwen was loaded and provenance-checked on CPU.  An SDPA kernel may rely
    # on unsupported PyTorch paths on DirectML, so use the explicit eager path.
    if hasattr(backbone,'set_attn_implementation'):
        backbone.set_attn_implementation('eager')
    elif hasattr(backbone,'config'):
        backbone.config._attn_implementation='eager'
    backbone.requires_grad_(False)
    backbone.eval()
    try:
        backbone.to(device=device,dtype=torch.float32)
    except Exception as exc:
        raise RuntimeError('INTEL_DML_QWEN_TRANSFER_FAILED: '+str(exc)) from exc
    for param in backbone.parameters():
        if param.device != device:
            raise RuntimeError('INTEL_DML_QWEN_PARTIALLY_ON_WRONG_DEVICE: '+str(param.device))
    return backbone
