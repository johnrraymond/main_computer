"""Phase-1 QW5 contracts: conditioning, separation, data isolation, stage locks."""
import copy
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_qw5 as qw
from tools import nanojev_qw5_join as join
from tools import nanojev_zero_line_sampled_radius_ladder as sampled


class TinyBank(nn.Module):
    def __init__(self, width=16):
        super().__init__()
        self.input_layer = nn.Linear(2, width)
        self.norm = nn.LayerNorm(width)
        self.sign_head = nn.Linear(width, 3)
        self.digit_head = nn.Linear(width, 70)

    def forward(self, x):
        v = self.norm(self.input_layer(x.float()))
        return {'sign': self.sign_head(v), 'digits': self.digit_head(v).reshape(-1, 7, 10)}


def model():
    return qw.QW5({name: TinyBank() for name in qw.HEAD_NAMES}, qwen_width=8,
                  taps=(1, 2, 3, 4), bridge_width=12)


def test_five_independent_nets_qwen_taps_and_fused_answer():
    torch.manual_seed(101)
    beast = model()
    x = torch.tensor([[20, -19], [3, -3], [-27, 26], [19, 10]], dtype=torch.long)
    taps = torch.randn(4, 4, 8)
    out = beast(x, taps)
    assert out['answer']['digits'].shape == (4, 7, 10)
    assert out['answer']['sign'].shape == (4, 3)
    assert out['emp_states'].shape == (4, 5, 16)
    assert out['hybrid_features'].shape == (4, 12)
    assert len(out['head_predictions']) == 5
    assert torch.allclose(out['weights'].sum(-1), torch.ones(4))
    ids = [set(map(id, bank.parameters())) for bank in beast.banks.values()]
    assert all(not ids[i].intersection(ids[j]) for i in range(5) for j in range(i+1, 5))
    assert all(not p.requires_grad for bank in beast.banks.values() for p in bank.parameters())
    assert not torch.allclose(out['answer']['sign'], beast(x, torch.zeros_like(taps))['answer']['sign'])


def test_qw5_conditioning_backprop_without_changing_source_banks():
    torch.manual_seed(222)
    beast = model()
    x = torch.tensor([[17, -15], [50, -50], [-14, 13]], dtype=torch.long)
    taps = torch.randn(3, 4, 8)
    before = {name: value.detach().clone() for name, value in beast.banks.state_dict().items()}
    loss = qw.qw5_loss(beast(x, taps), torch.tensor([2, 0, -1]))
    loss.backward()
    assert all(any(p.grad is not None and p.grad.abs().sum() > 0
                   for p in conditioner.parameters()) for conditioner in beast.conditioners)
    assert all(p.grad is None for bank in beast.banks.values() for p in bank.parameters())
    assert all(torch.equal(value, beast.banks.state_dict()[key]) for key, value in before.items())


def test_zero_cycles_and_later_stage_gates():
    beast = model()
    inputs = torch.tensor([[10, -9], [17, -17]])
    outputs = beast(inputs, torch.randn(2, 4, 8))
    with pytest.raises(RuntimeError, match='QW5_STAGE3_NUMERICAL_OUTPUT_DISABLED'):
        qw.numerical_output(outputs)
    adapter = qw.HybridLinkAugmentation(12, 12)
    orig = torch.randn(2, 12)
    with pytest.raises(RuntimeError, match='QW5_STAGE2_HYBRIDLINK_AUGMENTATION_DISABLED'):
        adapter(orig, outputs['hybrid_features'])
    assert torch.equal(adapter(orig, outputs['hybrid_features'], authorized=True), orig)
    answer = qw.numerical_output(outputs, authorized=True)
    assert answer.shape == (2,)


def test_data_magnitude_isolation_and_no_label_in_prompt():
    train = join.create_split('train', 512, 20261026, 64)
    dev = join.create_split('dev', 512, 20261026, 32)
    assert not {max(abs(a), abs(b)) for a, b, _ in train}.intersection(
        max(abs(a), abs(b)) for a, b, _ in dev)
    assert all(sampled.stable_fold(max(abs(a), abs(b)), 20261026) == 'train'
               for a, b, _ in train)
    assert all(sampled.stable_fold(max(abs(a), abs(b)), 20261026) == 'dev'
               for a, b, _ in dev)
    assert all(a + b == d for a, b, d in train + dev)
    assert 'Numerical answer:' in join.word_problem(10, -7)
    assert join.word_problem(10, -7) != join.word_problem(10, -7, 1)


def test_source_checkpoint_identity_is_checked(monkeypatch, tmp_path):
    root = tmp_path / 'ladder'
    stage = root / 'stages' / 'outward_64'
    side = root / 'hard_emp'
    stage.mkdir(parents=True); side.mkdir(parents=True)
    (root / 'source_left.pt').write_bytes(b'left')
    (root / 'source_right.pt').write_bytes(b'right')
    torch.save({'schema': qw.ladder.SCHEMA + '.checkpoint', 'radius': 64, 'step': 500,
                'banks': {}}, stage / 'latest.pt')
    stamp = qw.sha(stage / 'latest.pt')
    torch.save({'schema': qw.hard.SCHEMA + '.checkpoint', 'selected': ['left.left'],
                'specialists': {'hard__left.' + k: v for k, v in TinyBank().state_dict().items()},
                'processed': {'64-500': {'source_sha256': stamp}}}, side / 'latest.pt')

    class Four:
        def __getitem__(self, key):
            return self.d[key]

        def __init__(self):
            self.d = {name: TinyBank() for name in qw.dual.BANKS}

    monkeypatch.setattr(qw, 'load_certified_model', lambda *_: (None, {}))
    monkeypatch.setattr(qw.dual, 'checked_source', lambda *_: {})
    monkeypatch.setattr(qw.ladder, 'from_source', lambda *a: Four())
    five, provenance = qw.load_five(root, tmp_path)
    assert list(five) == list(qw.HEAD_NAMES)
    assert provenance['sha256']['stage'] == stamp
    (root / 'source_left.pt').write_bytes(b'new')
    with pytest.raises(RuntimeError, match='QW5_HARD_ORDINARY_STAGE_MISMATCH'):
        torch.save({'schema': qw.ladder.SCHEMA + '.checkpoint', 'radius': 64,
                    'step': 501, 'banks': {}}, stage / 'latest.pt')
        qw.load_five(root, tmp_path)


def test_custom_hard_state_dir_selects_matching_hard_checkpoint(tmp_path):
    campaign = tmp_path / 'campaign'
    stage = campaign / 'stages' / 'outward_512'
    stage.mkdir(parents=True)
    (stage / 'latest.pt').write_bytes(b'ordinary-stage')
    for name in ('source_left.pt', 'source_right.pt'):
        (campaign / name).write_bytes(name.encode())
    custom = campaign / 'hard_emp_frontier_v2'
    custom.mkdir()
    (custom / 'latest.pt').write_bytes(b'custom-specialist')
    assert qw.source_paths(campaign, hard_state_dir='hard_emp_frontier_v2')['hard'] == custom / 'latest.pt'
    with pytest.raises(FileNotFoundError, match='QW5_MISSING_EMP_SOURCE:'):
        qw.source_paths(campaign)
    with pytest.raises(ValueError, match='QW5_INVALID_HARD_STATE_DIR'):
        qw.source_paths(campaign, hard_state_dir='../other')


def test_invalid_shape_and_missing_fifth_head_rejected():
    with pytest.raises(ValueError, match='QW5_EXPECTS_FOUR_PLUS_ONE_IN_ORDER'):
        qw.QW5({name: TinyBank() for name in qw.HEAD_NAMES[:4]}, qwen_width=8, taps=(1, 2, 3, 4))
    with pytest.raises(ValueError, match='QW5_QWEN_HIDDEN_SHAPE'):
        model()(torch.tensor([[1, 2]]), torch.randn(1, 2, 8))


def test_one_command_prepare_train_and_resume_without_real_qwen(monkeypatch, tmp_path):
    """Run full CLI orchestration with fake source networks and fake Qwen taps."""
    campaign = tmp_path / 'campaign'
    campaign.mkdir()
    root_files = {}
    for key in ('stage', 'hard', 'left', 'right'):
        path = campaign / (key + '.pt')
        path.write_bytes(key.encode())
        root_files[key] = path

    def five(_campaign, _ladder, *, stage=None, device='cpu', hard_state_dir='hard_emp', source_files=None):
        source = {'campaign': str(campaign), 'ladder_output': str(tmp_path),
                  'hard_state_dir': hard_state_dir, 'stage_radius': 64, 'stage_step': 100, 'specialist_source': 'left.left',
                  'sha256': {k: qw.sha(p) for k, p in root_files.items()}}
        return {name: TinyBank().to(device) for name in qw.HEAD_NAMES}, source

    monkeypatch.setattr(qw, 'load_five', five)
    monkeypatch.setattr(qw, 'source_paths', lambda *_a, **_kw: root_files)

    class MockQwen:
        def eval(self): return self
        def requires_grad_(self, *_a): return self

    monkeypatch.setattr(join.hybrid, 'load_qwen', lambda *_a, **_kw: (MockQwen(), None, {'hidden_size': 1024}))

    def mock_states(_qwen, _tokenizer, rows, _device, *, style_offset=0):
        # Synthetic *input-derived* features, never using the answer column.
        z = torch.randn(4, 1024)
        return torch.stack([(z + float(a) / 1000 + float(b) / 1001) for a, b, _ in rows])

    monkeypatch.setattr(join, 'encoded_states', mock_states)
    run = tmp_path / 'qw5'
    cmd = ['run', '--campaign', str(campaign), '--ladder-output', str(tmp_path),
           '--hard-state-dir', 'hard_emp_frontier_v2',
           '--output', str(run), '--device', 'cpu', '--radius', '128',
           '--train-cards', '16', '--dev-cards', '8', '--steps', '2',
           '--batch-size', '4', '--probe-every', '1']
    assert join.main(cmd) == 0
    cfg = join.json.loads((run / 'config.json').read_text('utf8'))
    assert cfg['source']['hard_state_dir'] == 'hard_emp_frontier_v2'
    assert cfg['stages']['hybridlink_feedback'] == 'DISABLED_UNCERTIFIED'
    assert cfg['stages']['qw5_output_authority'] == 'DISABLED_UNCERTIFIED'
    cp = torch.load(run / 'latest.pt', weights_only=True)
    assert cp['step'] == 2
    assert join.main(cmd) == 0
    assert torch.load(run / 'latest.pt', weights_only=True)['step'] == 4
    root_files['hard'].write_bytes(b'changed-after-preparation')
    # A new Hard EMP must not invalidate a run whose original source was pinned.
    assert join.main(['status', '--output', str(run)]) == 0
    assert (run / 'source_archive' / 'hard.pt').read_bytes() == b'hard'


def test_runtime_zero_cycle_uses_one_qw5_and_stages_are_locked():
    from types import SimpleNamespace
    from tools.nanojev_qwen_hybridagent import HybridQwen, HybridLink
    from tools.nanojev_qw5_runtime import QW5HybridRuntime

    class DummyBackbone(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(64, 8)
            self.block = nn.Linear(8, 8)
            self.hidden = 8
        def get_input_embeddings(self):
            return self.emb
        def forward(self, input_ids=None, inputs_embeds=None, **_kw):
            x = self.emb(input_ids) if input_ids is not None else inputs_embeds
            states = [x]
            for _ in range(28):
                x = torch.tanh(self.block(x))
                states.append(x)
            return SimpleNamespace(hidden_states=tuple(states), past_key_values=object())

    torch.manual_seed(23)
    hybrid = HybridQwen(DummyBackbone(), HybridLink(8), 5, 6)
    beast = model()
    runtime = QW5HybridRuntime(hybrid, beast)
    prompt = torch.tensor([[1, 3, 4]])
    nums = torch.tensor([[18, -17]])
    candidate = runtime.reason(prompt, nums, latent_steps=0)
    assert candidate['answer']['digits'].shape == (1, 7, 10)
    assert runtime.qw5 is beast
    with pytest.raises(RuntimeError, match='QW5_STAGE2_HYBRIDLINK_AUGMENTATION_DISABLED'):
        runtime.reason(prompt, nums, latent_steps=1)
    with pytest.raises(RuntimeError, match='QW5_STAGE3_NUMERICAL_OUTPUT_DISABLED'):
        runtime.answer(candidate)
    # An authorized-parameter smoke is NOT a certificate or an enabled CLI stage.
    runtime.stage2_certified = True
    runtime.stage3_certified = True
    after_one = runtime.reason(prompt, nums, latent_steps=1)
    assert runtime.answer(after_one).shape == (1,)
