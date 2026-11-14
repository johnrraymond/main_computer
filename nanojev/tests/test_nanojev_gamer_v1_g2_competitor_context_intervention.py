"""G2-0018: frozen, label-blind, paired-context intervention tests."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import math
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_competitor_context_intervention as m
from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as ceiling17
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def test_cli_frozen_new_command_and_no_train_controls():
    proc = subprocess.run([sys.executable, '-m', 'tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-competitor-context-intervention', '--help'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    for option in ('--ceiling-run', '--general-run', '--output', '--device', '--max-length'):
        assert option in proc.stdout
    assert '--steps' not in proc.stdout


def synthetic_inventory(count=27):
    torch.manual_seed(101)
    width = 8
    return {'features': torch.randn(count, 4*width + source13.EXTRAS),
            'base': torch.arange(count, 0, -1).float(),
            'groups': [0]*count,
            'edges': [((i*9, i*9+1, 'action'), (i*11+1, i*11+2, 'action'), 'owns')
                      for i in range(count)]}


def test_proxy_pool_exact_label_blind_reproduction_of_held_target_forward():
    inv = synthetic_inventory()
    net = source13.SourceConditionedScorer(8).eval().requires_grad_(False)
    assert len(m.proxy_pool(inv, 0, 27, seed=59)) < 27
    with torch.inference_mode():
        logits, sizes = m.proxy_logits(net, inv, seed=59)
        chosen = m.proxy_pool(inv, 0, 27, seed=59)
        assert sizes == [len(chosen)]
        # The original forward is the oracle: one target plus label-blind pool.
        for target in range(27):
            indexes = sorted(set(chosen) | {target})
            original = net(inv['features'][indexes], inv['base'][indexes],
                           [0]*len(indexes), with_competition=True)
            assert torch.allclose(logits[target], original[indexes.index(target)],
                                  atol=1e-5, rtol=1e-5)
        assert torch.equal(m.proxy_logits(net, inv, seed=59)[0], logits)
        assert net.training is False
        assert all(not p.requires_grad for p in net.parameters())


def test_proxy_pool_independent_of_gold_and_no_inference_gold_injection():
    anchors = [(i*8, i*8+3, 'action') for i in range(28)]
    rank = torch.zeros(28, 28, len(BIND_CLASSES))
    for i in range(28):
        for j in range(28):
            rank[i,j,1] = 28-j
    allowed = {('owns','action','action')}
    row = {'anchors':anchors, 'vectors':torch.randn(28,8)}
    g1 = {**row, 'gold': {(anchors[0],anchors[1],'owns')}}
    g2 = {**row, 'gold': {(anchors[0],anchors[27],'owns')}}
    inv1 = source13.make_inventory(g1, rank, allowed)
    inv2 = source13.make_inventory(g2, rank, allowed)
    assert torch.equal(inv1['features'], inv2['features'])
    assert inv1['edges'] == inv2['edges']
    net = source13.SourceConditionedScorer(8).eval().requires_grad_(False)
    score1, sizes1 = m.proxy_logits(net, inv1, seed=123)
    score2, sizes2 = m.proxy_logits(net, inv2, seed=123)
    assert torch.equal(score1, score2)
    assert sizes1 == sizes2
    assert len(inv1['edges']) == 28*24
    assert (anchors[0],anchors[27],'owns') not in inv1['edges']


def test_score_drift_attribution_counts_gold_and_nongold_flips():
    edges = [('x','y','owns'),('x','z','owns'),('x','w','owns'),('x','t','owns')]
    before = torch.tensor([1.2, 1.3, 0., 0.])
    after = torch.tensor([0., 1.5, 2., 0.])
    attribution = m.paired_scores(edges, before, after, {edges[0], edges[2]})
    assert attribution['candidates'] == 4
    assert attribution['gold_candidates'] == 2
    assert attribution['gold_positive_to_negative'] == 1
    assert attribution['gold_negative_to_positive'] == 1
    assert attribution.get('nongold_positive_to_negative', 0) == 0
    assert attribution['absolute_score_drift_max'] == pytest.approx(2.)
    with pytest.raises(ValueError, match='BAD_PAIRED_SCORES'):
        m.paired_scores(edges, torch.tensor([1.]), after, set())


def test_case_replay_paired_24_to_48_and_fail_closed(monkeypatch):
    torch.manual_seed(18)
    anchors = [(i*8, i*8+3, 'action') for i in range(29)]
    rank = torch.zeros(29,29,len(BIND_CLASSES))
    for i in range(29):
        for j in range(29):
            rank[i,j,1] = 30-j
    gold = {(anchors[0], anchors[1], 'owns'),
            (anchors[0], anchors[28], 'owns')}
    allowed = {('owns','action','action')}
    row = {'prompt':'synthetic immutable context', 'family':'encounter',
           'structural_topology':'4x4'}
    case = {'prompt_sha256':base._sha(row['prompt']), 'anchors':anchors,
            'family':'encounter', 'vectors':torch.randn(29,8), 'gold':gold}
    scorer = source13.SourceConditionedScorer(8).eval().requires_grad_(False)
    inv = source13.make_inventory(case, rank, allowed)
    with torch.inference_mode():
        logits = scorer(inv['features'], inv['base'], inv['groups'], with_competition=True)
    score = sel.score_set(source13.decode_scores(inv,logits,m.THRESHOLD),gold)
    ceiling = ceiling17.candidate_ceiling(anchors,rank,gold,allowed)
    parent = {'prompt_sha256':case['prompt_sha256'], 'family':'encounter',
              'density_bucket':'medium_25_48','structural_topology':'4x4',
              'ceiling':ceiling, 'competition':{'candidate_sum':len(inv['edges'])},
              'configurations':{'general_75':score}}
    monkeypatch.setattr(m.rel,'full_logits',lambda head, vec:rank)
    result = m.intervention_case(case,row,parent,None,scorer,allowed,
                                 device='cpu',group=m.SPLITS[1],index=1)
    assert result['configurations']['baseline_top24'] == score
    assert result['candidate_inventory']['additional_reachable_gold'] == 1
    assert result['candidate_inventory']['additional_candidates'] > 0
    assert result['paired']['expanded_context']['candidates'] == len(inv['edges'])
    assert result['paired']['train_geometry_proxy']['candidates'] == len(inv['edges'])
    assert set(result['configurations']) == set(m.ARMS)
    result2 = m.aggregate([result])
    assert result2['metrics']['baseline_top24']['cases'] == 1
    assert result2['candidate_inventory']['additional_reachable_gold'] == 1
    assert m.THRESHOLD == .75
    damaged = deepcopy(parent)
    damaged['configurations']['general_75']['correct'] += 1
    with pytest.raises(ValueError, match='BASELINE_REPLAY_DRIFT'):
        m.intervention_case(case,row,damaged,None,scorer,allowed,
                            device='cpu',group=m.SPLITS[1],index=1)
    with pytest.raises(ValueError, match='PROMPT_DRIFT'):
        m.intervention_case({**case,'prompt_sha256':'bad'},row,parent,None,scorer,
                            allowed,device='cpu',group=m.SPLITS[1],index=1)


def test_parent_0017_requires_real_certified_provenance_and_immutable_output(tmp_path):
    (tmp_path / ceiling17.REPORT).write_text(json.dumps({'schema':'wrong'}))
    with pytest.raises(ValueError, match='PARENT_0017_CONTRACT'):
        m.verify_parent_0017(tmp_path,{m.SPLITS[0]:[],m.SPLITS[1]:[]},{})
    # Ensure immutable-output refusal is before any expensive model load.
    with pytest.raises(FileExistsError, match='IMMUTABLE_OUTPUT'):
        m.run(root=tmp_path,g1_validation_report='unused',g2_run='unused',
              selection_run='unused',discrimination_run='unused',relational_run='unused',
              density_run='unused',consistency_run='unused',context_run='unused',
              learned_run='unused',recall_run='unused',crowded_run='unused',
              robustness_run='unused',diagnosis_run='unused',source_run='unused',
              specialization_run='unused',routing_robustness_run='unused',
              general_run='unused',ceiling_run='unused',output=tmp_path)
