"""CPU-only contract tests: real embedding recurrence, gradients, checkpoint safety."""
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid


class ToyBackbone(nn.Module):
    def __init__(self, d=12, vocab=40):
        super().__init__()
        self.tokens = nn.Embedding(vocab, d)
        self.core = nn.Linear(d, d)
        self.readout = nn.Linear(d, vocab, bias=False)

    def get_input_embeddings(self):
        return self.tokens

    def get_output_embeddings(self):
        return self.readout

    def forward(self, *, input_ids=None, inputs_embeds=None, attention_mask=None,
                past_key_values=None, output_hidden_states=True, use_cache=True,
                return_dict=True):
        emb = self.tokens(input_ids) if input_ids is not None else inputs_embeds
        used = 0 if past_key_values is None else past_key_values[0]
        if attention_mask is not None:
            assert attention_mask.size(1) == used + emb.size(1)
        hidden = torch.tanh(self.core(emb))
        return SimpleNamespace(hidden_states=(hidden,), logits=self.readout(hidden),
                               past_key_values=(used + emb.size(1),))


class ToyTokenizer:
    eos_token_id = 1
    def __call__(self, text, *, return_tensors=None, **ignored):
        return {'input_ids': torch.tensor([[2, 3, 4, 5]], dtype=torch.long)}


def fixture():
    torch.manual_seed(77)
    backbone = ToyBackbone()
    backbone.requires_grad_(False)
    model = hybrid.HybridQwen(backbone, hybrid.HybridLink(12), 38, 39)
    info = {'schema': hybrid.SCHEMA, 'base_model': 'fake-local-qwen', 'revision': None,
            'model_type': 'qwen3', 'hidden_size': 12, 'original_vocab_size': 38,
            'tokenizer_size': 40, 'start_id': 38, 'end_id': 39,
            'device': 'cpu', 'dtype': 'float32', 'training_status': 'untrained',
            'adapter_type': 'ln_res_adapter'}
    return model, info


def test_real_latent_passes_share_backbone_and_are_differentiable():
    model, _ = fixture()
    tokens = torch.tensor([[2, 3, 4]], dtype=torch.long)
    h0, logits0, cache0, mask0, trace0 = model.rollout(tokens, 0)
    h2, logits2, cache2, mask2, trace2 = model.rollout(tokens, 2, return_trace=True)
    assert cache0 == (3,) and cache2 == (6,)
    x = torch.randn(2, 12)
    assert torch.allclose(model.head(x), model.head.post_ln(x + model.head.proj2(
        model.head.act(model.head.proj1(model.head.pre_ln(x))))), rtol=1e-5, atol=1e-5)
    assert mask2.shape == (1, 6) and len(trace2) == 2
    assert not torch.equal(h2, h0)
    assert not torch.equal(logits2, logits0)
    h2.sum().backward()
    assert model.head.proj2.weight.grad is not None
    assert torch.count_nonzero(model.head.proj2.weight.grad)
    assert all(p.grad is None for p in model.backbone.parameters())


def test_feedback_is_causal_and_not_static():
    model, _ = fixture()
    tokens = torch.tensor([[2, 3, 4]], dtype=torch.long)
    base = model.rollout(tokens, 2)[0].detach()
    with torch.no_grad():
        model.head.proj2.weight[0, :] = torch.linspace(-0.3, 0.5, 12)
    changed = model.rollout(tokens, 2)[0].detach()
    assert not torch.allclose(base, changed)


def test_smoke_parity_and_backward():
    model, _ = fixture()
    result = hybrid.preflight(model, ToyTokenizer())
    assert result['text_zero_step_parity'] == 'PASS'
    assert result['cached_embedding_feedback'] == 'PASS'
    assert result['link_backward'] == 'PASS'
    assert result['arithmetic_accuracy'] == 'NOT_TESTED'


def test_save_load_restores_both_markers_and_adapter(tmp_path):
    model, info = fixture()
    destination = tmp_path / 'new_head'
    hybrid.save_adapter(destination, model, info)
    checkpoint = torch.load(destination / 'adapter.pt', weights_only=True)
    assert checkpoint['schema'] == hybrid.SCHEMA
    original = model.head.proj1.weight.detach().clone()
    input_rows = model.backbone.tokens.weight.detach()[[38, 39]].clone()
    output_rows = model.backbone.readout.weight.detach()[[38, 39]].clone()
    with torch.no_grad():
        model.head.proj1.weight.add_(10)
        model.backbone.tokens.weight[[38, 39]].fill_(999)
        model.backbone.readout.weight[[38, 39]].fill_(999)
    hybrid.load_adapter(destination, model, info)
    assert torch.equal(model.head.proj1.weight, original)
    assert torch.equal(model.backbone.tokens.weight[[38,39]], input_rows)
    assert torch.equal(model.backbone.readout.weight[[38,39]], output_rows)
    with pytest.raises(FileExistsError, match='REFUSES_EXISTING_OUTPUT'):
        hybrid.save_adapter(destination, model, info)
    with pytest.raises(RuntimeError, match='TOKENIZER_DRIFT'):
        hybrid.load_adapter(destination, model, {**info, 'start_id': 33})


def test_exit_detects_nearest_input_token_and_guards_shapes():
    model, _ = fixture()
    token = model.backbone.tokens.weight[model.end_id].detach().clone()[None]
    assert model.nearest_embedding_is_end(token)
    with pytest.raises(ValueError, match='BATCH_NOT_ONE'):
        model.nearest_embedding_is_end(token.expand(2, -1))
    with pytest.raises(ValueError, match='OUT_OF_RANGE'):
        model.rollout(torch.tensor([[2, 3]]), -1)
    with pytest.raises(ValueError, match='NONEMPTY_PROMPT'):
        model.rollout(torch.empty((1, 0), dtype=torch.long), 1)


def test_text_reentry_consumes_marker_after_latent_cache():
    model, _ = fixture()
    ids = torch.tensor([[2, 3, 4]])
    _, _, cache, mask, _ = model.rollout(ids, 2)
    generated = model.text_after_latents(cache, mask, max_tokens=3, eos_token_id=None)
    assert len(generated) == 3
    assert all(0 <= t < 40 for t in generated)


class _FakeAddedToken:
    def __init__(self, content, *, normalized, special):
        self.content = content
        self.normalized = normalized
        self.special = special

    def __str__(self):
        return self.content


class _FakeTokenizerWithExistingSpecialTokens:
    def __init__(self):
        self._ids = {'<old>': 40}
        self.calls = []

    def add_special_tokens(self, tokens):
        self.calls.append(tokens)
        key, values = next(iter(tokens.items()))
        assert key == self.key
        assert str(values[0]) == '<old>'
        for value in values:
            value = str(value)
            if value not in self._ids:
                self._ids[value] = len(self._ids) + 40
        setattr(self, key, values)

    def convert_tokens_to_ids(self, token):
        return self._ids[token]


class _FakeTokenizerV4(_FakeTokenizerWithExistingSpecialTokens):
    key = 'additional_special_tokens'

    def __init__(self):
        super().__init__()
        self.additional_special_tokens = [_FakeAddedToken('<old>', normalized=False, special=True)]


class _FakeTokenizerV5(_FakeTokenizerWithExistingSpecialTokens):
    key = 'extra_special_tokens'

    def __init__(self):
        super().__init__()
        self.extra_special_tokens = [_FakeAddedToken('<old>', normalized=False, special=True)]
        # Deliberately no ``additional_special_tokens`` attribute. This is the
        # actual traceback from Qwen2Tokenizer under Transformers 5.


def _assert_special_token_compatibility(tokenizer):
    start_id, end_id = hybrid._register_latent_markers(tokenizer, _FakeAddedToken)
    assert start_id != end_id
    added = tokenizer.calls[-1][tokenizer.key]
    assert [str(token) for token in added] == ['<old>', hybrid.START, hybrid.END]
    assert all(token.special and not token.normalized for token in added)
    # Running registration twice must neither duplicate nor reorder tokens.
    start2, end2 = hybrid._register_latent_markers(tokenizer, _FakeAddedToken)
    assert (start2, end2) == (start_id, end_id)
    assert len(tokenizer.calls[-1][tokenizer.key]) == 3


def test_transformers_4_additional_special_tokens_are_preserved():
    _assert_special_token_compatibility(_FakeTokenizerV4())


def test_transformers_5_extra_special_tokens_without_legacy_attribute():
    _assert_special_token_compatibility(_FakeTokenizerV5())


def test_released_link_checkpoint_has_exact_upstream_parameter_keys():
    expected = {'proj1.weight', 'proj1.bias', 'proj2.weight', 'proj2.bias',
                'pre_ln.weight', 'pre_ln.bias', 'post_ln.weight', 'post_ln.bias'}
    assert set(hybrid.HybridLink(12).state_dict()) == expected
    assert hybrid.MODEL_ID == 'Qwen/Qwen3-0.6B-Base'
    assert 'ln-res-link.v2' in hybrid.SCHEMA


def test_legacy_v1_checkpoint_refused_without_mutation(tmp_path):
    model, info = fixture()
    target = tmp_path / 'legacy'
    hybrid.save_adapter(target, model, info)
    saved = (target / 'adapter_config.json').read_bytes()
    cfg = __import__('json').loads(saved)
    cfg['schema'] = 'nanojev.qwen3-0.6b.hybridagent-residual-link.v1'
    (target / 'adapter_config.json').write_text(__import__('json').dumps(cfg))
    with pytest.raises(RuntimeError, match='legacy v1 and normalized v2'):
        hybrid.load_adapter(target, model, info)
    assert __import__('json').loads((target / 'adapter_config.json').read_text())['schema'] == cfg['schema']


def test_fp32_hybrid_link_into_bfloat16_decoder_keeps_gradients():
    """Real heterogeneous precision: Head fp32, frozen decoder bf16."""
    model, _ = fixture()
    model.backbone.to(dtype=torch.bfloat16)
    model.head.to(dtype=torch.float32)
    assert model.backbone.get_input_embeddings().weight.dtype == torch.bfloat16
    assert next(model.head.parameters()).dtype == torch.float32
    ids = torch.tensor([[2, 3, 4]])
    h, logits, cache, mask, _ = model.rollout(ids, 2)
    assert h.dtype == torch.bfloat16 and logits.dtype == torch.bfloat16
    assert cache == (6,) and mask.shape == (1, 6)
    h.float().sum().backward()
    grads = [p.grad for p in model.head.parameters()]
    assert any(g is not None and bool(torch.isfinite(g).all()) and bool((g != 0).any()) for g in grads)
    assert all(p.grad is None for p in model.backbone.parameters())
