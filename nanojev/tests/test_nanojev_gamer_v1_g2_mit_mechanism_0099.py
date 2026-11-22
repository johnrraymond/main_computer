"""0099 causal attribution and no-mutation evidence, including full dry lifecycle."""
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_mit_mechanism_0099 as m
from tools import nanojev_gamer_v1_graph_curriculum as cur


SPEC = {'method': 'joint', 'threshold': 0.0,
        'out_caps': {'quantity|action|applies_to': 1},
        'in_caps': {'action|quantity|applies_to': 1}}


def sample_surface(*, good=1.0, bad=2.0):
    objs = [[0, 1, 'action'], [2, 3, 'action'], [4, 5, 'quantity']]
    pairs = []
    for i in range(len(objs)):
        for j in range(len(objs)):
            if i != j:
                v = [-10., -10., -10., -10.]
                if (i, j) == (2, 0): v[3] = good
                if (i, j) == (2, 1): v[3] = bad
                pairs.append([i, j, v])
    return {'g1_objects': objs, 'pair_margins': pairs, 'binding_edges': []}


def edges():
    good = ('quantity', (4, 5), 'action', (0, 1), 'applies_to')
    bad = ('quantity', (4, 5), 'action', (2, 3), 'applies_to')
    return good, bad


def weights():
    w = torch.zeros(5, 192)
    w[4, 0] = 1.0
    w[4, 1] = 0.2
    w[0, 0] = 0.4
    return w


def training_record(family):
    good, bad = edges()
    f = torch.zeros(3, 3, 192)
    f[2, 1, 0] = 3.0
    f[2, 0, 0] = 0.0
    f[2, 0, 1] = 3.0
    return {'family': family, 'prompt_sha256': 'ignored',
            'surface': sample_surface(), 'features': f,
            'baseline': [m.source_reader.json_edge(bad)],
            'gold': [m.source_reader.json_edge(good)]}


def test_failure_attribution_distinguishes_neural_from_decoder_and_g1():
    good, bad = edges()
    base = sample_surface()
    cap = m.missing_attribution(base, {bad}, {good}, SPEC)
    assert cap['decoder_capacity_or_global_competition'] == 1
    scored = sample_surface(good=-0.1, bad=-1.)
    threshold = m.missing_attribution(scored, set(), {good}, SPEC)
    assert threshold['below_admission_threshold'] == 1
    label_wrong = sample_surface()
    for pair in label_wrong['pair_margins']:
        if pair[:2] == [2, 0]:
            pair[2][2] = 3.0
    ranked = m.missing_attribution(label_wrong, {bad}, {good}, SPEC)
    assert ranked['wrong_label_top_scored'] == 1
    missing = ('quantity', (40, 41), 'action', (0, 1), 'applies_to')
    absent = m.missing_attribution(base, set(), {missing}, SPEC)
    assert absent['g1_endpoint_unavailable'] == 1


def test_counterfactual_exact_readout_path_and_full_activation():
    record = training_record('allocation')
    surface, feat, weight = record['surface'], record['features'], weights()
    unchanged = surface['pair_margins'][0][2][:]
    original_state = weight.clone()
    path = m.ablate_readout_path(surface, feat, weight, dimension=0, mode='path')
    full = m.ablate_readout_path(surface, feat, weight, dimension=0, mode='full')
    pair = next(x for x in path['pair_margins'] if x[:2] == [2, 1])
    pair_full = next(x for x in full['pair_margins'] if x[:2] == [2, 1])
    assert pair[2][3] == pytest.approx(-1.)
    assert pair_full[2][3] == pytest.approx(0.2)
    assert path['pair_margins'][0][2] == unchanged
    assert surface['pair_margins'][0][2] == unchanged
    assert torch.equal(weight, original_state)
    assert m.bakeoff.decode(path, SPEC) == {edges()[0]}
    assert m.bakeoff.decode(full, SPEC) == {edges()[0]}


def test_discovery_only_selection_and_negative_controls():
    records = [training_record('allocation' if k%2 == 0 else 'encounter')
               for k in range(60)]
    plan = m.select_dimensions(records, weights())
    assert 0 in plan['suspected_features']
    assert 1 in plan['negative_controls']
    assert plan['n_competitions'] == 20
    # An intervention selection must never see other partitions.
    for k in range(60):
        if m.group_of(k) != 'discovery':
            records[k]['features'][2, 1, 0] = -900.0
    assert m.select_dimensions(records, weights()) == plan
    assert plan['prior_0098_overlap'] == []


def test_partitions_are_family_balanced_and_0098_training_excluded(monkeypatch):
    training = [dict(family=f, split='train', prompt=f'{f} training {i}',
                     graph={'nodes': [], 'edges': []})
                for i in range(200) for f in ('allocation', 'encounter')]
    monkeypatch.setattr(m.cur, 'load_rows', lambda p: training)
    chosen = m.training_rows({'dataset': 'unused'}, 120)
    old = m.earlier._train_selection({'dataset': 'unused'}, 80)
    assert not {r['prompt'] for r in chosen} & {r['prompt'] for r in old}
    for group in m.GROUPS:
        assert len([r for i, r in enumerate(chosen) if m.group_of(i) == group]) == 40
        for family in ('allocation', 'encounter'):
            assert sum(r['family'] == family
                       for i, r in enumerate(chosen) if m.group_of(i) == group) == 20


def test_full_lifecycle_writes_only_runtime_diagnostics(tmp_path, monkeypatch):
    root = tmp_path / 'reports'; root.mkdir()
    campaign = root / 'oracle-fabric-g2-extended-learning-0096'
    source = campaign / 'warm_supervised' / 'milestone-01600'
    source.mkdir(parents=True)
    states = {'binding.weight': weights().clone(),
              'binding.bias': torch.zeros(5),
              'unrelated.weight': torch.ones(4, 4)}
    save_file(states, str(source / cur.HEAD_FILE))
    m.write(source / cur.MANIFEST, {'schema': 'nanojev.gamer.graph.g2.extended-fuzz-learning-0096.v1',
                                   'head_sha256': m.sha(source / cur.HEAD_FILE),
                                   'g3_unlocked': False})
    old_digest = m.sha(source / cur.HEAD_FILE)
    dataset = root / 'train_data.jsonl'
    dataset.write_text('unmodified source train set')
    training = [dict(family=f, split='train', prompt=f'{f} training {i}',
                     graph={'nodes': [], 'edges': []})
                for i in range(150) for f in ('allocation', 'encounter')]
    monkeypatch.setattr(m.cur, 'load_rows', lambda p: training)
    monkeypatch.setattr(m.structural, 'paths_for', lambda root: {'dataset': dataset})
    monkeypatch.setattr(m.structural, 'preflight', lambda paths: ({}, SPEC))

    class Model:
        def eval(self): return self
        def requires_grad_(self, value): return self
        binding = type('Binding', (), {'weight': weights()})()

    monkeypatch.setattr(m.structural, '_load_runtime',
                        lambda *args: (None, None, None, Model(), None))
    monkeypatch.setattr(m.source_reader, '_load_scientific_head',
                        lambda *args, **kwargs: Model())
    def fake_capture(row, *args):
        result = training_record(row['family'])
        result['prompt_sha256'] = m.digest(row['prompt'])
        return result
    monkeypatch.setattr(m, '_capture_one', fake_capture)
    result = m.run(root=root, cases=60, device='cpu')
    assert result['training_cases'] == 60
    assert result['source_unchanged'] is True
    assert result['weight_edits'] == result['gradient_steps'] == 0
    assert result['production_promotion'] is False
    assert result['no_new_checkpoint'] is True
    assert result['baseline']['discovery']['overall']['cases'] == 20
    assert result['missing_gold_by_cause']['replication']['decoder_capacity_or_global_competition'] == 20
    assert 0 in result['discovery_feature_selection']['suspected_features']
    assert result['counterfactuals']['0']['path']['collateral']['protection']['correct_edges_rescued'] == 20
    assert m.sha(source / cur.HEAD_FILE) == old_digest
    assert len(list(root.rglob('gamer_graph_head.safetensors'))) == 1
    assert m.run(root=root, device='cpu')['source_unchanged'] is True
    record = m.location(root,None)/'capture'/'shards'/'0000.pt'
    with record.open('ab') as f: f.write(b'corrupted')
    with pytest.raises(ValueError, match='CAPTURE_SHARD_HASH_DRIFT'):
        m.run(root=root, device='cpu')


def test_dataset_and_parent_checkpoint_changes_fail_closed(tmp_path, monkeypatch):
    root = tmp_path / 'reports'; root.mkdir()
    campaign = root / 'oracle-fabric-g2-extended-learning-0096'
    source = campaign / 'warm_supervised' / 'milestone-01600'
    source.mkdir(parents=True)
    save_file({'binding.weight': weights(), 'binding.bias': torch.zeros(5)},
              str(source / cur.HEAD_FILE))
    m.write(source / cur.MANIFEST, {'head_sha256': m.sha(source / cur.HEAD_FILE)})
    dataset = root / 'train.jsonl';dataset.write_text('frozen data')
    training = [dict(family=f, split='train', prompt=f'{f}-{i}', graph={'nodes':[],'edges':[]})
                for f in ('allocation', 'encounter') for i in range(150)]
    monkeypatch.setattr(m.cur, 'load_rows', lambda p: training)
    monkeypatch.setattr(m.structural, 'paths_for', lambda root: {'dataset':dataset})
    monkeypatch.setattr(m.structural, 'preflight',lambda paths:({},SPEC))
    m.prepare(root=root, cases=60)
    dataset.write_text('changed')
    with pytest.raises(ValueError, match='TRAIN_DATASET_DRIFT'):
        m.verify(root, m.location(root,None))
    dataset.write_text('frozen data')
    manifest=m.read(source/cur.MANIFEST)
    manifest['head_sha256']='0'*64
    m.write(source/cur.MANIFEST,manifest)
    with pytest.raises(ValueError, match='SOURCE_CHECKPOINT_HASH_DRIFT'):
        m.verify(root,m.location(root,None))


def test_prior_0098_replay_is_score_only_and_parent_gated():
    rec=training_record('encounter')
    rec['features'][2,1,6]=3.0  # First actual 0098 edit coordinate.
    original=rec['surface']
    before=[(i,j,list(margins)) for i,j,margins in original['pair_margins']]
    changed=m.replay_0098_score_edit(original,rec['features'])
    target=next(x for x in changed['pair_margins'] if x[:2]==[2,1])
    old=next(x for x in original['pair_margins'] if x[:2]==[2,1])
    expected=3.0 * (m.PRIOR_0098_DELTAS[0])
    assert target[2][3]-old[2][3]==pytest.approx(expected)
    assert target[2][:3]==old[2][:3]
    assert original['pair_margins']==[[i,j,v] for i,j,v in before]
    assert m.PRIOR_0098_PARENT_SHA=='eb99869d7c851e13eb79859f545d35426072b2a55cdb004435a0539e5a8919a7'
