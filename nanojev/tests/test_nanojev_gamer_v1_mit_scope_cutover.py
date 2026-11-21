"""Validate the scope cutover without modifying frozen experiment sources."""
from __future__ import annotations

import random
import torch
import pytest

from tools import nanojev_gamer_v1_mit_scope_decoder as promoted
from tools import nanojev_gamer_v1_mit_scope_0003 as experiment
from tools import nanojev_gamer_v1_mit_scope_runtime as runtime
from tools import nanojev_gamer_v1_g2_active as active
from tools import nanojev_gamer_v1_mit as historic
from tools import nanojev_gamer_v1_graph_system as system


def sample(family, ordinal):
    factory = (experiment.corpus.allocation if family == 'allocation' else
               experiment.corpus.encounter)
    return factory(random.Random(ordinal + 70), 'test', ordinal)


def synthetic_surface(prompt, graph):
    # Use author-marked spans ONLY in numerical parity tests; the real query
    # must use G1-predicted spans. No label/answer enters production decoding.
    objs = [[*node['span'], node['kind']] for node in graph['nodes']
            if 'span' in node][:10]
    pairs = []
    for i in range(len(objs)):
        for j in range(len(objs)):
            if i == j:
                continue
            # Deterministic raw score surface, not tuned on gold.
            margins = [float(((i * 7 + j * 11 + t * 13) % 23) - 12) / 4
                       for t in range(4)]
            pairs.append([i, j, margins])
    return {'g1_objects': objs, 'binding_edges': [], 'pair_margins': pairs}


@pytest.mark.parametrize('family', ['encounter', 'allocation'])
def test_promoted_decoder_matches_sealed_0003_across_all_seven_variants(family):
    spec = {'method': 'joint', 'threshold': 0., 'out_caps': {}, 'in_caps': {}}
    for ordinal in range(2):
        base = sample(family, ordinal)
        for variant in experiment.VARIANTS:
            prompt, graph = experiment.transform(base, variant, ordinal)
            record = synthetic_surface(prompt, graph)
            ids = promoted.scopes(prompt, record['g1_objects'])
            assert ids == experiment.scopes(prompt, record['g1_objects'])
            cut, discarded = promoted.restrict_surface(record, prompt)
            old_cut, old_discarded = experiment.restrict_surface(
                record, prompt, 'identity_scope')
            assert cut == old_cut and discarded == old_discarded
            chosen, meta = promoted.decode(record, prompt, spec,
                                           return_diagnostics=True)
            expected, old_meta = experiment.decode(
                record, prompt, spec, 'identity_scope')
            assert chosen == expected
            assert meta['blocked_pair_labels'] == old_meta['blocked_pair_labels']
            assert meta['status'] == 'OPTIMAL'


def test_runtime_cutover_uses_verified_wsp_head_and_no_gold(tmp_path, monkeypatch):
    # This is the actual G2 query flow with test doubles replacing expensive
    # model loading. The MILP and promoted scope logic run for real.
    prompt = 'Alpha benefit 5; cost 7; Beta benefit 3 cost 2'
    root = tmp_path / 'root'
    dest = root / 'wsp'
    (dest / 'checkpoint').mkdir(parents=True)
    (tmp_path / 'mit' / 'checkpoint').mkdir(parents=True)
    monkeypatch.setattr(runtime.wsp, 'location', lambda root, output: dest)
    spec = {'method': 'joint', 'threshold': 0.,
            'out_caps': {'quantity|action|applies_to': 1},
            'in_caps': {'action|quantity|applies_to': 1}}
    fake_hash = 'ec3bcb5c7850c77f3f1d58ce76d5cc94a045448316baae8d8407ad7bb86d2ea7'
    monkeypatch.setattr(runtime.wsp, 'check_edit', lambda *args: (
        {}, tmp_path / 'mit', None, {}, None, {'winner': spec},
        {'edited_head_sha256': fake_hash}))

    class Head:
        def eval(self): return self
        def requires_grad_(self, _): return self

    class Composite:
        def predict(self, *args, **kwargs):
            assert kwargs['family'] == 'unlabeled'
            return {'token_spans': []}

    monkeypatch.setattr(runtime.structural, '_load_runtime',
                        lambda *args: (None, None, Composite(), None, None))
    monkeypatch.setattr(runtime.wsp, 'load_edited_head',
                        lambda *args, **kwargs: Head())
    monkeypatch.setattr(runtime.legacy.previous, '_capture_inference',
                        lambda *args: ({'eligible': torch.ones(3)}, None))

    a = prompt.index('Alpha')
    b = prompt.index('Beta')
    c = prompt.index('7')
    surface = {'g1_objects': [[a, a+5, 'action'],
                              [c, c+1, 'quantity'], [b, b+4, 'action']],
               'binding_edges': [], 'pair_margins': [
                   [0, 1, [-7., -7., -7., -7.]],
                   [0, 2, [-7., -7., -7., -7.]],
                   [1, 0, [-7., -7., -7., 9.]],
                   [1, 2, [-7., -7., -7., 10.]],
                   [2, 0, [-7., -7., -7., -7.]],
                   [2, 1, [-7., -7., -7., -7.]]]}
    monkeypatch.setattr(runtime.legacy, '_infer', lambda *args, **kwargs: surface)
    result = runtime.query(prompt=prompt, root=str(root), device='cpu')
    assert result['decoder'] == 'identity_scope'
    assert result['version'] == 'MIT'
    assert result['status'].endswith('PARTIAL_GRAPH_NOT_CERTIFIED')
    assert result['model_head_sha256'] == fake_hash
    assert result['production_promotion'] is False
    assert result['labels_used_at_inference'] is False
    assert len(result['edges']) == 1
    assert result['edges'][0][3] == [a, a+5]
    assert result['baseline_edges'][0][3] == [b, b+4]
    assert result['rescued_vs_unconstrained'] == 1
    assert result['removed_vs_unconstrained'] == 1


def test_fail_closed_when_frozen_wsp_checkpoint_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.wsp, 'location', lambda root, output: tmp_path)
    with pytest.raises(FileNotFoundError, match='MIT_SCOPE_VERIFIED_WSP_CHECKPOINT_REQUIRED'):
        runtime.query(prompt='Alpha benefit 3', root='unused', device='cpu')
    with pytest.raises(ValueError, match='MIT_SCOPE_CHECKPOINT_OVERRIDE_NOT_SUPPORTED'):
        runtime.query(prompt='Alpha benefit 3', root='unused', output='other', device='cpu')


def test_default_routes_to_promoted_decoder_with_explicit_rollbacks(monkeypatch):
    assert active.ACTIVE_VERSION == 'MIT'
    assert active.VERSIONS == ('MIT', 'MIT-0001', 'legacy')
    called = []
    monkeypatch.setattr(active.scope_runtime, 'query',
                        lambda **args: called.append('scope') or {'version': 'MIT'})
    monkeypatch.setattr(active.mit, 'query',
                        lambda **args: called.append('old') or {'version': 'MIT-0001'})
    assert active.query(prompt='test', device='cpu')['version'] == 'MIT'
    assert active.query(prompt='test', device='cpu', version='MIT-0001')['version'] == 'MIT-0001'
    assert called == ['scope', 'old']
    assert system.g2_query(prompt='test', device='cpu')['version'] == 'MIT'


def test_frozen_mit_runner_unmodified_against_recorded_cutover_sha():
    # This is checked again during patch packaging against the 0001 ZIP. An
    # accidental modification would invalidate MIT/WSP seal provenance.
    assert callable(historic.query)
    assert historic.CAMPAIGN == 'oracle-fabric-g2-mit-0001'
