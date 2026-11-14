"""Fast CPU regression tests for a *real* teacher-embedding activation update.

Requires the v2 HybridAgent module already installed in the working repository.
Never downloads Qwen weights or uses CUDA.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_activation as activation


class TinyTokenizer:
    def __call__(self, text, *, add_special_tokens=False, **_):
        if text == hybrid.START:
            return {'input_ids': [250]}
        if text == hybrid.END:
            return {'input_ids': [251]}
        return {'input_ids': [ord(c) for c in text]}


class TinyBackbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.embed = nn.Embedding(256, 24)
        self.linear = nn.Linear(24, 24)
        self.linear2 = nn.Linear(24, 24)

    def get_input_embeddings(self):
        return self.embed

    def forward(self, *, input_ids, attention_mask, use_cache, output_hidden_states, return_dict):
        assert input_ids.shape == attention_mask.shape
        hidden = torch.tanh(self.linear2(torch.tanh(self.linear(self.embed(input_ids)))))
        return SimpleNamespace(hidden_states=(hidden, hidden), past_key_values=None)


class TinyHybrid(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone = TinyBackbone()
        self.head = hybrid.HybridLink(24)


def loader(**kwargs):
    model = TinyHybrid()
    details = {'schema': hybrid.SCHEMA,
               'base_model': 'Qwen/Qwen3-0.6B-Base',
               'revision': 'test_revision', 'hidden_size': 24,
               'tokenizer_size': 256, 'start_id': 250, 'end_id': 251}
    return model, TinyTokenizer(), details


@pytest.fixture
def pretend_baseline(tmp_path, monkeypatch):
    base = tmp_path / 'baseline'
    base.mkdir()
    (base / 'adapter.pt').write_bytes(b'tiny baseline hash - not a Qwen checkpoint')
    (base / 'adapter_config.json').write_text('{"schema":"test"}')
    monkeypatch.setattr(activation.hybrid, 'load_adapter', lambda baseline, model, details: {
        'schema': hybrid.SCHEMA, 'base_model': 'Qwen/Qwen3-0.6B-Base',
        'training_status': 'untrained'})
    return base


def test_trajectory_aligns_entry_payload_to_exit():
    ids, sources, exits = activation.make_trajectory(TinyTokenizer(), activation.examples()[0][0])
    assert len(ids) <= 256
    assert ids[sources[0]] == 250
    assert ids[sources[0] + 1] == ord('C')  # first token of 'Cancel ...'
    assert ids[sources[-1] + 1] == 251
    assert sum(exits) == 1 and exits[-1] == 1
    assert all(n == 0 for n in exits[:-1])


def test_loss_matches_released_cos_mse_exit_formula():
    head = nn.Identity()
    x = torch.tensor([[1., 0., 0.], [0., 1., 0.], [1., 1., 0.]])
    y = torch.tensor([[0., 1., 0.], [0., 1., 0.], [0., 0., 1.]])
    exit_ = torch.tensor([False, False, True])
    out = activation.loss_components(head, x, y, exit_)
    cos = 1 - torch.nn.functional.cosine_similarity(x, y, dim=-1)
    mse = (x-y).square().mean(dim=-1)
    expected = cos.mean() + .2 * mse.mean() + cos[-1] + .2 * mse[-1]
    assert torch.allclose(out['loss'], expected)
    assert out['exit_count'] == 1
    with pytest.raises(AssertionError, match='INPUT_MISMATCH'):
        activation.loss_components(head, x, y[:-1], exit_)


def test_teacher_pairs_are_nonzero_finite_and_exit_supervised():
    model, tokenizer, _ = loader()
    model.backbone.requires_grad_(False)
    train, dev = activation.examples()
    xs, ys, exits = activation.capture_pairs(model, tokenizer, train[:2], 'cpu')
    assert xs.ndim == 2 and xs.shape == ys.shape and xs.shape[1] == 24
    assert exits.sum() == 2
    assert bool(torch.isfinite(xs).all() and torch.isfinite(ys).all())
    assert set((x['a'],x['b']) for x in train).isdisjoint(
        set((x['a'],x['b']) for x in dev))


def test_optimizer_strict_scope_refuses_backbone():
    model, _, _ = loader()
    model.backbone.requires_grad_(False)
    good = torch.optim.AdamW(model.head.parameters(), lr=2e-4)
    assert activation.verify_optimizer_scope(model, good) > 0
    bad = torch.optim.AdamW(list(model.head.parameters()) + list(model.backbone.parameters()), lr=2e-4)
    with pytest.raises(AssertionError, match='OPTIMIZER_SCOPE_DRIFT'):
        activation.verify_optimizer_scope(model, bad)


def test_training_update_pause_resume_checkpoint_exact(tmp_path, pretend_baseline):
    complete = tmp_path / 'complete'
    partial = tmp_path / 'partial'
    arguments = dict(device='cpu', baseline=pretend_baseline, steps=12,
                     batch_size=24, eval_every=4, lr=5e-4,
                     model_loader=loader)
    uninterrupted = activation.activation_run(output=complete, **arguments)
    assert uninterrupted['status'] == 'PASS_MECHANICS_ONLY'
    assert uninterrupted['final_train']['loss'] < uninterrupted['initial_train']['loss']
    assert uninterrupted['backbone_hash_preserved']
    assert uninterrupted['baseline_mutations'] == 0
    assert uninterrupted['head_changed_from_call_start']
    assert (complete / 'last.pt').exists()
    assert (complete / 'best_head.pt').exists()
    assert (complete / 'result.json').exists()
    assert (complete / 'run_config.json').exists()
    paused = activation.activation_run(output=partial, stop_after=4, **arguments)
    assert paused['status'] == 'PAUSED_RESUMABLE'
    assert paused['step'] == 4
    with pytest.raises(FileExistsError, match='REFUSES_EXISTING_OUTPUT'):
        activation.activation_run(output=partial, **arguments)
    resumed = activation.activation_run(output=partial, resume=True, **arguments)
    assert resumed['status'] == 'PASS_MECHANICS_ONLY'
    assert resumed['start_step'] == 4 and resumed['step'] == 12
    assert resumed['final_train']['loss'] == pytest.approx(uninterrupted['final_train']['loss'], abs=1e-6)
    last_full = torch.load(complete / 'last.pt', weights_only=True)
    last_resumed = torch.load(partial / 'last.pt', weights_only=True)
    for key in last_full['head']:
        assert torch.equal(last_full['head'][key], last_resumed['head'][key])
    result = json.loads((partial / 'result.json').read_text())
    assert result['status'] == 'PASS_MECHANICS_ONLY'
    assert result['optimizer_steps_this_invocation'] == 8


def test_resume_refuses_base_change(tmp_path, pretend_baseline):
    location = tmp_path / 'probe'
    args = dict(device='cpu', baseline=pretend_baseline, output=location,
                steps=6, stop_after=2, batch_size=8, model_loader=loader)
    activation.activation_run(**args)
    (pretend_baseline / 'adapter.pt').write_bytes(b'malicious baseline change')
    with pytest.raises(AssertionError, match='RESUME_CONFIG_OR_BASELINE_DRIFT'):
        activation.activation_run(**{**args, 'resume':True, 'stop_after':None})


def test_refuses_writing_into_base(tmp_path, pretend_baseline):
    with pytest.raises(ValueError, match='OUTPUT_COLLIDES_WITH_BASELINE'):
        activation.activation_run(device='cpu', baseline=pretend_baseline,
                                  output=pretend_baseline, model_loader=loader)
