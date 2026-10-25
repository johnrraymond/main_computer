"""CPU-only unit tests for assertions used by the live HybridAgent smoke."""

from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_training_smoke as smoke


class NormedHead(nn.Module):
    def __init__(self, d=12):
        super().__init__()
        self.pre_ln = nn.LayerNorm(d)
        self.proj1 = nn.Linear(d, d)
        self.act = nn.GELU()
        self.proj2 = nn.Linear(d, d)
        self.post_ln = nn.LayerNorm(d)

    def forward(self, x):
        return self.post_ln(x + self.proj2(self.act(self.proj1(self.pre_ln(x)))))


class FakeBackbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.tokens = nn.Embedding(40, 12)
        self.core = nn.Linear(12, 12)
        self.readout = nn.Linear(12, 40, bias=False)

    def get_input_embeddings(self):
        return self.tokens

    def get_output_embeddings(self):
        return self.readout

    def forward(self, *, input_ids=None, inputs_embeds=None, attention_mask=None,
                past_key_values=None, output_hidden_states=True, use_cache=True, return_dict=True):
        emb = self.tokens(input_ids) if input_ids is not None else inputs_embeds
        used = 0 if past_key_values is None else past_key_values[0]
        assert attention_mask.size(1) == used + emb.size(1)
        h = torch.tanh(self.core(emb))
        return SimpleNamespace(hidden_states=(h,), logits=self.readout(h),
                               past_key_values=(used + emb.size(1),))


class FakeTokenizer:
    eos_token_id = 1
    pad_token_id = 1

    def __len__(self):
        return 40

    def convert_tokens_to_ids(self, token):
        return {hybrid.START: 38, hybrid.END: 39}[token]

    def __call__(self, text, *, return_tensors=None, **kwargs):
        ids = [38 if text == hybrid.START else 39] if text in (hybrid.START, hybrid.END) else [2, 3, 4, 5]
        return {'input_ids': torch.tensor([ids]) if return_tensors == 'pt' else ids}


def fixture():
    torch.manual_seed(123)
    backbone = FakeBackbone()
    backbone.requires_grad_(False)
    model = hybrid.HybridQwen(backbone, hybrid.HybridLink(12), 38, 39)
    info = {'schema': hybrid.SCHEMA, 'base_model': 'Qwen/Qwen3-0.6B-Base',
            'revision': hybrid.MODEL_REVISION, 'model_type': 'qwen3',
            'hidden_size': 12, 'original_vocab_size': 38, 'tokenizer_size': 40,
            'start_id': 38, 'end_id': 39, 'training_status': 'untrained',
            'adapter_type': 'ln_res_adapter'}
    return model, FakeTokenizer(), info


def test_real_paper_head_conforms_and_legacy_head_fails_closed():
    assert 'exact released' in smoke.assert_paper_head(hybrid.HybridLink(12), 12)
    legacy = nn.Sequential(nn.Linear(12, 12), nn.GELU(), nn.Linear(12, 12))
    with pytest.raises(AssertionError, match='PAPER_HEAD_MISSING_PRE_LN'):
        smoke.assert_paper_head(legacy, 12)


def test_real_two_norm_forward_passes_and_unused_norms_fail():
    assert 'exact released' in smoke.assert_paper_head(NormedHead(), 12)
    head = NormedHead()
    head.forward = lambda x: x + head.proj2(head.proj1(x))
    with pytest.raises(AssertionError, match='PAPER_HEAD_FORWARD_ORDER_DRIFT'):
        smoke.assert_paper_head(head, 12)


def test_non_base_provenance_fails_without_explicit_waiver():
    cfg = {'base_model': 'Qwen/Qwen3-0.6B'}
    with pytest.raises(AssertionError, match='PAPER_BASE_CHECKPOINT_DRIFT'):
        smoke.assert_base_provenance(cfg)
    assert 'deviation accepted' in smoke.assert_base_provenance(cfg, True)
    assert 'base checkpoint' in smoke.assert_base_provenance({'base_model': 'Qwen/Qwen3-0.6B-Base'})


def test_marker_drift_and_checkpoint_drift_are_detected(tmp_path):
    model, tokenizer, info = fixture()
    assert 'atomic latent markers' in smoke.assert_token_boundary(model, tokenizer, info)
    with pytest.raises(AssertionError, match='CHECKPOINT_TOKENIZER_SIZE_DRIFT'):
        smoke.assert_token_boundary(model, tokenizer, {**info, 'tokenizer_size': 41})
    destination = tmp_path / 'adapter'
    hybrid.save_adapter(destination, model, info)
    assert 'bit-for-bit' in smoke.assert_checkpoint_exact(model, destination, info)
    with torch.no_grad():
        model.head.proj1.bias.add_(0.125)
    with pytest.raises(AssertionError, match='CHECKPOINT_HEAD_ROW_MISMATCH'):
        smoke.assert_checkpoint_exact(model, destination, info)


def test_causal_intervention_is_nondestructive():
    model, tokenizer, _ = fixture()
    original = model.head.proj2.bias.detach().clone()
    assert 'max_abs_delta' in smoke.assert_causal_latent_effect(model, tokenizer)
    assert torch.equal(original, model.head.proj2.bias)
    assert 'gradient' in smoke.assert_recurrence_and_gradient(model, tokenizer)


def test_all_failed_assertions_reported_nonzero_not_masked():
    check = smoke.Smoke()
    check.check('a', lambda: smoke.assert_paper_head(nn.Sequential(nn.Linear(12, 12)), 12))
    check.check('b', lambda: smoke.assert_base_provenance({'base_model': 'Qwen/Qwen3-0.6B'}))
    check.check('c', lambda: 'valid')
    check.unavailable('learning', 'no training performed')
    report = check.report()
    assert report['status'] == 'FAIL'
    assert report['failed'] == ['a', 'b']
    assert report['checks'][2]['status'] == 'PASS'
    assert report['checks'][3]['status'] == 'NOT_TESTED'
    assert report['optimizer_steps'] == 0 and report['checkpoint_mutations'] == 0


def test_base_checkpoint_default_asserted_and_waiver_remains_explicit():
    assert 'base checkpoint' in smoke.assert_base_provenance({'base_model': hybrid.MODEL_ID})
    assert hybrid.DEFAULT_OUTPUT.name == 'qwen3_06b_base_hybridagent_ln_v2'
