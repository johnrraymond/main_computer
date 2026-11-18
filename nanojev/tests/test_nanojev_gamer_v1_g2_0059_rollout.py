"""G2-0059 real capture-shard rollout gates, resumption and test isolation."""
from __future__ import annotations
import json
from pathlib import Path
from collections import Counter

import pytest
import torch
from tools import nanojev_gamer_v1_g2_scaling_0059 as g
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from test_nanojev_gamer_v1_g2_scaling_0059 import _fake_capture


def _setup(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    # Lower sizes ONLY in the tests; production uses the explicit 128 / 512
    # disjoint unseen-pair cohorts.
    monkeypatch.setattr(g,'ROLLOUT_CANARY_NEW_PAIRS',16)
    monkeypatch.setattr(g,'ROLLOUT_QUALIFICATION_NEW_PAIRS',16)
    monkeypatch.setattr(g,'ROLLOUT_MIN_CELL_PAIRS',2)
    monkeypatch.setattr(g,'ROLLOUT_MIN_TOPOLOGIES',4)
    def spatial(raw,proposal):
        anchors=proposal['anchors'];edges=proposal['edges']
        # The spatial baseline demonstrably fails on a real graph edge.
        return {**proposal,'anchors':anchors,'edges':edges[1:],
                'typed_edges':set(edges[1:]),'raw_lattice':{},
                'logits':torch.zeros(1,1,len(SPAN_KINDS)+1)}
    def evidence(raw,proposal):
        return {**proposal,'typed_edges':set(proposal['edges']),
                'raw_lattice':{},'logits':torch.zeros(1,1,len(SPAN_KINDS)+1)}
    monkeypatch.setattr(g,'_construct_spatial',spatial)
    monkeypatch.setattr(g,'_construct_evidence',evidence)
    monkeypatch.setattr(g.p52,'construct',evidence)
    # The frozen capture mock in the established CPU test has no G1 logits.
    # Keep actual shard/label/manifest paths, but replace only the posthoc
    # diagnostic (not the candidate policy or coverage calculations).
    def diag(row,raw,expanded,*,baseline=None):
        gold=set(g.p50.g40.base.gold_binding_edges(row))
        missing=gold-set(expanded['typed_edges'])
        cases=[{'type':r,'src_span':[s[0],s[1]],'dst_span':[d[0],d[1]],
                'src_kind':s[2],'dst_kind':d[2],
                'origin':'original_graph','reason':'inventory_or_alignment_defect',
                'missing_nodes':[]} for s,d,r in sorted(missing)]
        return {'missing_edges':len(missing),'cases':cases,
                'by_cause':{'inventory_or_alignment_defect':len(missing)} if missing else {},
                'by_origin':{'original_graph':len(missing)} if missing else {},
                'by_relation':dict(Counter(r for s,d,r in missing)),
                'baseline_missing_edges':None}
    monkeypatch.setattr(g,'_diagnose_missing_edges',diag)
    plan=tmp_path/'plan';spatial_dir=tmp_path/'spatial';pilot=tmp_path/'evidence'
    g.create_plan(plan,train_graphs=96,validation_graphs=16,
                  test_graphs=8,challenge_graphs=8)
    g.capture(plan,spatial_dir,root=root,device='cpu',max_shards=1,candidate_policy='spatial')
    pilot_result=g.capture(plan,pilot,root=root,device='cpu',max_shards=1,
                  candidate_policy='evidence',compare_capture=spatial_dir)
    assert pilot_result['pilot_comparison']['candidate_reachable_pairs']==16
    assert pilot_result['pilot_comparison']['baseline_missing_edges']>0
    assert pilot_result['pilot_comparison']['regressed_edges']==0
    return root,plan,pilot


def test_rollout_canary_qualification_full_resumes_without_sealed_capture(monkeypatch,tmp_path):
    root,plan,pilot=_setup(monkeypatch,tmp_path)
    target=tmp_path/'rollout'
    pilot_sha=g.file_sha(g._shard_paths(pilot,'train',0)[0])
    first=g.rollout(plan,target,pilot,root=root,device='cpu',stage='canary',max_shards=1)
    assert first['status']=='CAPTURE_IN_PROGRESS'
    assert first['canary']['pairs_captured']==0  # The pilot replay isn't evidence
    assert first['capture']['captured_now']==1
    assert not (target/'rollout-canary-gate.json').exists()
    assert not list(target.glob('test*')) and not list(target.glob('challenge*'))
    with pytest.raises(ValueError,match='ROLLOUT_CANARY_GATE_NOT_PASSED'):
        g.rollout(plan,target,pilot,root=root,device='cpu',stage='qualification')
    second=g.rollout(plan,target,pilot,root=root,device='cpu',stage='canary')
    assert second['status']=='GATE_PASSED', second['canary']
    assert second['canary']['pairs_captured']==16
    assert second['canary']['missing_candidate_gold_edges']==0
    assert second['canary']['distinct_topologies']>=4
    assert second['next_stage']=='qualification'
    qual=g.rollout(plan,target,pilot,root=root,device='cpu',stage='qualification')
    assert qual['status']=='GATE_PASSED'
    assert qual['qualification_train']['pairs_captured']==16
    assert qual['qualification_validation']['pairs_captured']==8
    assert qual['next_stage']=='full'
    assert not list(target.glob('test*')) and not list(target.glob('challenge*'))
    full=g.rollout(plan,target,pilot,root=root,device='cpu',stage='full')
    assert full['status']=='GATE_PASSED'
    assert g.coverage_audit(plan,target)['training_coverage_gate_passed']
    assert g.file_sha(g._shard_paths(pilot,'train',0)[0])==pilot_sha
    assert json.loads((target/'capture.json').read_text())['candidate_policy']=='evidence'
    assert g._rollout_cohort(g.FeatureCorpus(plan,target,'train'),16,16)['gate_passed']


def test_tampered_pilot_and_existing_wrong_shard_block_rollout(monkeypatch,tmp_path):
    root,plan,pilot=_setup(monkeypatch,tmp_path)
    target=tmp_path/'rollout'
    proof=pilot/'pilot-comparison.json'
    report=json.loads(proof.read_text())
    report['regressed_edges']=1
    proof.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='ROLLOUT_PILOT_COMPARISON_REJECTED'):
        g.rollout(plan,target,pilot,root=root,device='cpu')
    assert not target.exists()


def test_unseen_coverage_failure_blocks_qualification(monkeypatch,tmp_path):
    root,plan,pilot=_setup(monkeypatch,tmp_path)
    target=tmp_path/'rollout'
    # Only the new rollout is changed. This patch is intentionally label-blind;
    # here the mock policy loses an edge on a few unseen graphs.
    original=g._construct_evidence
    seen={'n':0}
    def incomplete(raw,proposal):
        seen['n']+=1
        result=original(raw,proposal)
        if seen['n']>32 and result['edges']:
            edges=result['edges'][1:]
            return {**result,'edges':edges,'typed_edges':set(edges)}
        return result
    monkeypatch.setattr(g,'_construct_evidence',incomplete)
    out=g.rollout(plan,target,pilot,root=root,device='cpu',stage='canary')
    assert out['status']=='GATE_FAILED'
    assert not out['canary']['gate_passed']
    assert out['canary']['missing_candidate_gold_edges']>0
    with pytest.raises(ValueError,match='ROLLOUT_CANARY_GATE_NOT_PASSED'):
        g.rollout(plan,target,pilot,root=root,device='cpu',stage='qualification')
    assert not list(target.glob('test*'))
