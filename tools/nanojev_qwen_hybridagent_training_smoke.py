#!/usr/bin/env python3
"""Fail-closed, non-training HybridAgent readiness smoke for Qwen3-0.6B.

No dataset is generated and no model is trained or checkpoint overwritten.
Checks are independent assertions aggregated into a machine-readable report.
Learning-dependent questions are *not* silently marked PASS.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Callable

import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid


class Smoke:
    def __init__(self):
        self.checks = []

    def check(self, identifier: str, action: Callable[[], str | None]):
        try:
            detail = action()
        except Exception as exc:
            # Keep every diagnostic even when an earlier invariant fails.
            self.checks.append({'id': identifier, 'status': 'FAIL',
                                'detail': str(exc) or type(exc).__name__})
        else:
            self.checks.append({'id': identifier, 'status': 'PASS',
                                'detail': detail or 'assertions satisfied'})

    def unavailable(self, identifier: str, why: str):
        self.checks.append({'id': identifier, 'status': 'NOT_TESTED', 'detail': why})

    def report(self):
        failures = [c['id'] for c in self.checks if c['status'] == 'FAIL']
        return {'schema': 'nanojev.hybridagent-training-readiness-smoke.v1',
                'status': 'FAIL' if failures else 'PASS_WITH_LIMITATIONS',
                'failed': failures, 'checks': self.checks,
                'checkpoint_mutations': 0, 'optimizer_steps': 0}


def assert_paper_head(head: nn.Module, hidden: int) -> str:
    """Assert exact released module names, ordered operation and finite output."""
    for attr, cls in (('pre_ln', nn.LayerNorm), ('proj1', nn.Linear),
                      ('act', nn.GELU), ('proj2', nn.Linear),
                      ('post_ln', nn.LayerNorm)):
        assert isinstance(getattr(head, attr, None), cls), (
            f'PAPER_HEAD_MISSING_{attr.upper()}: expected released {cls.__name__} module')
    for attr in ('pre_ln', 'post_ln'):
        layer = getattr(head, attr)
        assert tuple(layer.normalized_shape) == (hidden,), 'PAPER_HEAD_NORM_SHAPE_DRIFT:' + attr
    events = []
    modules = ('pre_ln', 'proj1', 'act', 'proj2', 'post_ln')
    hooks = [getattr(head, name).register_forward_hook(
        lambda _module, _inputs, _output, key=name: events.append(key)) for name in modules]
    try:
        with torch.no_grad():
            ref = next(head.parameters())
            test_input = torch.randn(2, hidden, device=ref.device, dtype=ref.dtype)
            observed = head(test_input)
            expected = head.post_ln(test_input + head.proj2(
                head.act(head.proj1(head.pre_ln(test_input)))))
    finally:
        for hook in hooks:
            hook.remove()
    assert events[:len(modules)] == list(modules), (
        'PAPER_HEAD_FORWARD_ORDER_DRIFT: expected ' + ' -> '.join(modules) +
        ', got ' + str(events[:len(modules)]))
    assert observed.shape == test_input.shape and torch.isfinite(observed).all(), (
        'PAPER_HEAD_OUTPUT_NONFINITE_OR_WRONG_SHAPE')
    assert torch.allclose(observed, expected, atol=1e-5, rtol=1e-5), (
        'PAPER_HEAD_RESIDUAL_FORMULA_DRIFT')
    return 'exact released layer names, order and residual formula verified'


def assert_base_provenance(cfg: dict, allow_instruct: bool = False) -> str:
    source = str(cfg.get('base_model') or '')
    assert source, 'BACKBONE_ID_MISSING'
    if allow_instruct:
        return 'explicit instruct-backbone deviation accepted: ' + source
    assert source.endswith('-Base') or Path(source).is_dir() and cfg.get('verified_base_pretraining') is True, (
        'PAPER_BASE_CHECKPOINT_DRIFT: source is ' + source + '; paper trains on Qwen3-Base. '
        'To deliberately retain this baseline, rerun with --allow-instruct-backbone')
    return 'paper-style base checkpoint identified: ' + source


def assert_token_boundary(model, tokenizer, cfg: dict) -> str:
    start, end = model.start_id, model.end_id
    assert 0 <= start < end or 0 <= end < start, 'LATENT_MARKER_ID_COLLISION_OR_NEGATIVE'
    n = len(tokenizer)
    assert start < n and end < n, 'MARKER_ID_OUT_OF_TOKENIZER_RANGE'
    assert cfg['tokenizer_size'] == n, 'CHECKPOINT_TOKENIZER_SIZE_DRIFT'
    assert int(cfg['start_id']) == start and int(cfg['end_id']) == end, 'MARKER_CHECKPOINT_ID_DRIFT'
    for token, id_ in ((hybrid.START, start), (hybrid.END, end)):
        assert tokenizer.convert_tokens_to_ids(token) == id_, 'MARKER_LOOKUP_MISMATCH:' + token
        assert tokenizer(token, add_special_tokens=False)['input_ids'] == [id_], (
            'MARKER_NOT_ONE_TOKEN:' + token)
    assert tokenizer.eos_token_id not in (start, end), 'MARKER_COLLIDES_WITH_EOS'
    assert tokenizer.pad_token_id not in (start, end), 'MARKER_COLLIDES_WITH_PAD'
    table = model.backbone.get_input_embeddings().weight
    assert table.size(0) >= n and table.size(1) == int(cfg['hidden_size']), 'EMBEDDING_TABLE_SHAPE_DRIFT'
    assert torch.isfinite(table[[start, end]]).all(), 'MARKER_EMBEDDING_NONFINITE'
    return f'separate, atomic latent markers; vocab={n}'


def assert_checkpoint_exact(model, output: Path, cfg: dict) -> str:
    """load_adapter already checks provenance and restores rows; assert exact stored tensors."""
    saved = torch.load(output / 'adapter.pt', map_location='cpu', weights_only=True)
    assert saved['schema'] == hybrid.SCHEMA, 'ADAPTER_SCHEMA_DRIFT'
    for k, saved_tensor in saved['head'].items():
        live = model.head.state_dict().get(k)
        assert live is not None and torch.equal(live.detach().cpu(), saved_tensor), (
            'CHECKPOINT_HEAD_ROW_MISMATCH:' + k)
    ids = [model.start_id, model.end_id]
    input_rows = model.backbone.get_input_embeddings().weight.detach()[ids].cpu()
    assert torch.equal(input_rows, saved['tokens']['input_embeddings']), 'CHECKPOINT_INPUT_MARKERS_DRIFT'
    out = model.backbone.get_output_embeddings()
    inp = model.backbone.get_input_embeddings()
    if out is not None and out.weight.data_ptr() != inp.weight.data_ptr():
        assert 'output_embeddings' in saved['tokens'], 'CHECKPOINT_OUTPUT_MARKERS_MISSING'
        assert torch.equal(out.weight.detach()[ids].cpu(), saved['tokens']['output_embeddings']), (
            'CHECKPOINT_OUTPUT_MARKERS_DRIFT')
    assert cfg['schema'] == hybrid.SCHEMA, 'CHECKPOINT_CONFIG_SCHEMA_DRIFT'
    return 'saved head weights and both marker tables reproduced bit-for-bit'


def assert_recurrence_and_gradient(model, tokenizer) -> str:
    result = hybrid.preflight(model, tokenizer, check_gradient=True)
    assert result['cached_embedding_feedback'] == 'PASS', 'LATENT_FEEDBACK_FAILED'
    assert result['text_zero_step_parity'] == 'PASS', 'TEXT_PARITY_BROKEN'
    assert result['link_backward'] == 'PASS', 'HEAD_GRADIENT_DISCONNECTED'
    assert result['latent_steps'] >= 2, 'RECURRENT_LATENT_STEPS_MISSING'
    return 'cached 2-step recurrence, zero-step parity, finite head gradient verified'


def assert_causal_latent_effect(model, tokenizer) -> str:
    """A perturbed HybridLink must change Qwen's computed hidden state."""
    device = next(model.backbone.parameters()).device
    ids = tokenizer('A + (-A + 1) = ', return_tensors='pt')['input_ids'].to(device)
    assert hasattr(model.head, 'proj2') and model.head.proj2.bias is not None, (
        'CAUSAL_PROBE_REQUIRES_PROJ2_BIAS')
    # Run without gradients or optimizer steps; put original weights back even on failure.
    with torch.no_grad():
        h0 = model.rollout(ids, 2)[0].float().detach().clone()
        bias = model.head.proj2.bias
        original = bias.detach().clone()
        try:
            bias.add_(torch.linspace(-0.5, 0.5, bias.numel(), device=bias.device, dtype=bias.dtype))
            h1 = model.rollout(ids, 2)[0].float().detach().clone()
        finally:
            bias.copy_(original)
        delta = (h1 - h0).abs().max().item()
    assert torch.isfinite(h1).all() and torch.isfinite(h0).all(), 'CAUSAL_PROBE_NONFINITE'
    assert delta > 1e-4, (
        f'CAUSAL_LATENT_DISCONNECTED: changing HybridLink did not change latent Qwen output; max_delta={delta:g}')
    return f'changed head alters recurrent backbone state (max_abs_delta={delta:.6g})'


def assert_untrained_switch_guard(cfg: dict) -> str:
    # Not a claim that adaptive switching works. Prevent it being certified by an untrained adapter.
    status = cfg.get('training_status')
    assert status == 'untrained', (
        'TRAINING_STATUS_UNEXPECTED: this pretraining smoke is scoped to untrained checkpoint; '
        f'found {status!r}')
    return 'adapter declared untrained; adaptive switching cannot be certified here'


def run(*, device: str = 'cuda', output: Path = hybrid.DEFAULT_OUTPUT,
        model_id: str = hybrid.MODEL_ID, revision: str | None = None,
        allow_instruct_backbone: bool = False, allow_download: bool = False,
        load_fn=None) -> dict:
    checks = Smoke()
    model = tokenizer = cfg = None
    # Independent static gates run even if CUDA/checkpoint loading fails.
    checks.check('head_architecture', lambda: assert_paper_head(hybrid.HybridLink(1024), 1024))
    try:
        cfg = hybrid.read_config(output)
    except Exception as exc:
        checks.check('checkpoint_config', lambda: (_ for _ in ()).throw(
            AssertionError('CHECKPOINT_CONFIG_UNAVAILABLE: ' + str(exc))))
    else:
        checks.check('checkpoint_config', lambda: 'adapter config and schema readable')
        checks.check('paper_base_provenance', lambda: assert_base_provenance(cfg, allow_instruct_backbone))
        checks.check('untrained_status_guard', lambda: assert_untrained_switch_guard(cfg))
    try:
        loader = load_fn or hybrid.load_qwen
        model, tokenizer, details = loader(model_id, revision, device=device,
                                               allow_download=allow_download)
        # This also verifies the precise adapter/tokenizer checkpoint contract.
        hybrid.load_adapter(output, model, details)
    except Exception as exc:
        checks.check('live_model_and_adapter', lambda: (_ for _ in ()).throw(
            AssertionError('LIVE_QWEN_OR_ADAPTER_FAILED: ' + str(exc))))
    else:
        checks.check('live_model_and_adapter', lambda: 'Qwen loaded, adapter strictly restored')
        checks.check('latent_token_boundary', lambda: assert_token_boundary(model, tokenizer, cfg))
        checks.check('adapter_exact_restoration', lambda: assert_checkpoint_exact(model, output, cfg))
        checks.check('recurrent_gradient', lambda: assert_recurrence_and_gradient(model, tokenizer))
        checks.check('latent_causal_influence', lambda: assert_causal_latent_effect(model, tokenizer))
    # These are not experimentally decidable from a one-batch smoke. Do not produce fake PASS.
    checks.unavailable('numerical_accuracy', 'requires real held-out X/Y labels; no training run or accuracy benchmark is performed')
    checks.unavailable('oracle_blindness', 'requires trainer data pipeline and an unseen-matrix evaluation fixture')
    checks.unavailable('adaptive_switching_accuracy', 'requires trained switching policy and held-out behavioral trials')
    checks.unavailable('single_gpu_training_capacity', 'inference/backward smoke does not establish optimizer, batch, or activation memory viability')
    return checks.report()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='cuda')
    p.add_argument('--output', type=Path, default=hybrid.DEFAULT_OUTPUT)
    p.add_argument('--model', default=hybrid.MODEL_ID)
    p.add_argument('--revision', default=None)
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--allow-instruct-backbone', action='store_true',
                   help='Explicitly accept deviation from paper Base checkpoint')
    args = p.parse_args(argv)
    report = run(device=args.device, output=args.output, model_id=args.model,
                 revision=args.revision, allow_instruct_backbone=args.allow_instruct_backbone,
                 allow_download=args.allow_model_download)
    print('HYBRID_TRAINING_SMOKE=' + json.dumps(report, sort_keys=True), flush=True)
    return 1 if report['status'] == 'FAIL' else 0


if __name__ == '__main__':
    sys.exit(main())
