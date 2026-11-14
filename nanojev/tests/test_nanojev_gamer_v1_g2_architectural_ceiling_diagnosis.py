"""G2-0017 frozen graph-architecture attribution and acceptance tests."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as m
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def test_cli_has_canonical_frozen_audit_options():
    result = subprocess.run([sys.executable, '-m', 'tools.nanojev_gamer_v1_graph_curriculum',
                             'frob-g2-architectural-ceiling-diagnosis', '--help'],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    for flag in ('--general-run', '--routing-robustness-run', '--output', '--device'):
        assert flag in result.stdout


def _anchors(n=29):
    return [(j * 8, j * 8 + 4, 'action') for j in range(n)]


def _rank(n):
    rank = torch.zeros(n, n, len(BIND_CLASSES))
    for j in range(1, n):
        rank[0, j, 1] = float(n - j)
    return rank


def test_exact_candidate_ceiling_distinguishes_disjoint_losses():
    anchors = _anchors()
    fake = (999, 1001, 'action')
    gold = {(anchors[0], anchors[1], 'owns'),
            (anchors[0], anchors[-1], 'owns'),
            (anchors[0], anchors[2], 'refers_to'),
            (fake, anchors[1], 'owns'),
            (anchors[0], fake, 'owns')}
    allowed = {('owns', 'action', 'action')}
    result = m.candidate_ceiling(anchors, _rank(len(anchors)), gold, allowed)
    assert result['gold_total'] == 5
    assert result['g1_missing_source'] == 1
    assert result['g1_missing_destination'] == 1
    assert result['g1_endpoint_reachable'] == 3
    assert result['compatibility_or_self_filtered'] == 1
    assert result['compatible_reachable'] == 2
    assert result['top24_excluded_reachable'] == 1
    assert result['topk_reachable']['24'] == 1
    assert result['topk_reachable']['32'] == 2
    assert all(result['topk_reachable'][str(a)] <= result['topk_reachable'][str(b)]
               for a, b in zip(m.TOP_KS, m.TOP_KS[1:]))
    case = {'anchors': anchors, 'vectors': torch.zeros(len(anchors), 8), 'gold': gold}
    inv = source13.make_inventory(case, _rank(len(anchors)), allowed)
    assert sum(edge in gold for edge in inv['edges']) == result['topk_reachable']['24']
    assert sum(edge in gold for edge in source13.make_inventory(
        case, _rank(len(anchors)), allowed, top_k=128)['edges']) == 2


def test_g1_missing_both_null_argmax_and_bad_input():
    anchors = _anchors(3)
    rank = _rank(3)
    rank[0, 1, 0] = 100  # Upstream prefers null despite positive compatible edge.
    gold = {(anchors[0], anchors[1], 'owns'),
            ((500, 501, 'actor'), (700, 701, 'action'), 'owns')}
    report = m.candidate_ceiling(anchors, rank, gold, {('owns', 'action', 'action')})
    assert report['g1_missing_both'] == 1
    assert report['upstream_null_argmax_reachable'] == 1
    assert report['topk_reachable']['24'] == 1
    with pytest.raises(ValueError, match='BAD_CEILING_INPUT'):
        m.candidate_ceiling([anchors[0]] * 3, rank, gold, set())
    with pytest.raises(ValueError, match='BAD_CEILING_INPUT'):
        m.candidate_ceiling(anchors, torch.full_like(rank, float('nan')), gold, set())


def test_competition_stats_are_descriptive_and_monotone():
    torch.manual_seed(41)
    net = source13.SourceConditionedScorer(8)
    inv = {'features': torch.randn(6, 4 * 8 + source13.EXTRAS),
           'base': torch.zeros(6), 'groups': [0, 0, 0, 1, 1, 2]}
    stats = m.competition_diagnostics(net, inv)
    assert stats['groups'] == 3
    assert stats['multi_groups'] == 2
    assert stats['candidate_sum'] == 6
    assert stats['max_group_size'] == 3
    assert stats.get('top_weight_gt_075', 0) <= 2
    assert m.competition_diagnostics(net, {'groups': [], 'features': torch.empty(0)})['groups'] == 0


def _score(tp, fp, fn):
    return {'correct': tp, 'false_positive': fp, 'missing': fn,
            'exact': tp > 0 and fp == 0 and fn == 0}


def _record(gold=5):
    return {'family': 'allocation', 'ceiling': {
        'gold_total': gold, 'g1_missing_source': 1,
        'g1_missing_destination': 0, 'g1_missing_both': 0,
        'g1_endpoint_reachable': gold - 1,
        'compatibility_or_self_filtered': 1,
        'compatible_reachable': gold - 2,
        'top24_excluded_reachable': 1, 'topk_reachable': {str(k):
                max(0, gold - 3) for k in m.TOP_KS}},
        'competition': {'groups': 4, 'multi_groups': 3},
        'configurations': {name: _score(gold - 3, 2, 3) for name in m.CONFIGS}}


def test_ceiling_micro_accounting_and_reject_over_oracle():
    rec = _record()
    result = m.aggregate([rec, deepcopy(rec)])
    assert result['cases'] == 2
    assert result['gold_total'] == 10
    assert result['stage_losses'] == {'g1_missing_endpoint': 2,
        'compatibility_or_self': 2, 'top24_truncation': 2,
        'scorer_or_decoding_on_reachable': 0}
    assert result['fixed_weight_metrics'][m.GENERAL]['correct'] == 4
    assert result['stage_oracle_recall']['top24'] == .4
    assert result['competition']['groups'] == 8
    bad = deepcopy(rec)
    bad['configurations'][m.GENERAL] = _score(4, 0, 1)
    with pytest.raises(ValueError, match='AGGREGATE_CEILING_IDENTITY'):
        m.aggregate([bad])
    with pytest.raises(ValueError, match='EMPTY_AGGREGATE'):
        m.aggregate([])


def test_frozen_general_checkpoint_hash_and_key_validation(tmp_path):
    torch.manual_seed(5)
    net = source13.SourceConditionedScorer(8)
    data = {tag + '.' + key: tensor.detach().clone().contiguous()
            for tag in ('general', 'crowd_only')
            for key, tensor in net.state_dict().items()}
    path = tmp_path / 'model.safetensors'
    save_file(data, str(path))
    loaded = m.load_general_scorer(path, 8, backbone.sha256(path))
    assert all(torch.equal(loaded.state_dict()[k], v)
               for k, v in net.state_dict().items())
    assert not any(p.requires_grad for p in loaded.parameters())
    with pytest.raises(ValueError, match='WEIGHT_HASH_DRIFT'):
        m.load_general_scorer(path, 8, 'different')
    save_file({k: v for k, v in data.items() if not k.startswith('crowd_only.')}, str(path))
    with pytest.raises(ValueError, match='WEIGHT_KEY_DRIFT'):
        m.load_general_scorer(path, 8, backbone.sha256(path))


def test_parent_verify_fail_closed(tmp_path):
    (tmp_path / m.general16.REPORT).write_text(json.dumps({'schema': 'corrupt'}))
    with pytest.raises(ValueError, match='PARENT_CONTRACT'):
        m.verify_frozen_0016(tmp_path, tmp_path, tmp_path, [], [], [], [], [])


def test_diagnose_cpu_inference_matches_exact_parent_replay(monkeypatch):
    torch.manual_seed(18)
    anchors = _anchors(4)
    gold = {(anchors[0], anchors[1], 'owns')}
    allowed = {('owns', 'action', 'action')}
    rank = _rank(4)
    row = {'prompt': 'frozen evaluation text', 'family': 'allocation',
           'structural_topology': 'native'}
    case = {'prompt_sha256': base._sha(row['prompt']), 'index': 1, 'family': 'allocation',
            'anchors': anchors, 'vectors': torch.randn(4, 8), 'gold': gold}
    scorer = source13.SourceConditionedScorer(8).eval()
    inv = source13.make_inventory(case, rank, allowed)
    with torch.inference_mode():
        logits = scorer(inv['features'], inv['base'], inv['groups'], with_competition=True)
        expected = {name: sel.score_set(source13.decode_scores(inv, logits, t), gold)
                    for name, t in [('general_70', .70), (m.GENERAL, .75), ('general_80', .80)]}
    expected[m.BASELINE] = sel.score_set(set(), gold)
    old = {'prompt_sha256': case['prompt_sha256'], 'g1_predicted_nodes': 4,
           'gold_binding_edges': 1, 'structural_topology': 'native',
           'density_bucket': 'small_1_24', 'configurations': expected}
    monkeypatch.setattr(m.rel, 'full_logits', lambda head, vectors: rank)
    monkeypatch.setattr(m, '_parent_0015', lambda *args, **kwargs: set())
    rec = m.diagnose_case(case, row, old, None, scorer, None, None, None, allowed,
                          {}, device='cpu', group=m.SPLITS[0], index=1)
    assert rec['configurations'][m.GENERAL] == expected[m.GENERAL]
    assert rec['ceiling']['topk_reachable']['24'] == 1
    assert rec['configurations']['pair_only_75']['correct'] <= 1
    bad = deepcopy(old)
    bad['configurations'][m.GENERAL]['correct'] = 100
    with pytest.raises(ValueError, match='FROZEN_REPLAY_DRIFT'):
        m.diagnose_case(case, row, bad, None, scorer, None, None, None, allowed,
                        {}, device='cpu', group=m.SPLITS[0], index=1)
    with pytest.raises(ValueError, match='CASE_PROMPT_DRIFT'):
        m.diagnose_case({**case, 'prompt_sha256': 'bad'}, row, old, None, scorer,
                        None, None, None, allowed, {}, device='cpu', group=m.SPLITS[0], index=1)
