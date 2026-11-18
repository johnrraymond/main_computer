"""G2-0059 label-blind binding pilot, same-pair comparison and fail-closed tests."""
from __future__ import annotations

import json
import torch
import pytest

from tools import nanojev_gamer_v1_g2_scaling_0059 as g
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def test_all_first_sixteen_signatures_supported_by_original_train():
    # The new clause does not introduce an unseen edge signature.  That rules
    # out a type-inventory fix as the cause of the 41 missing gold edges.
    train=[corpus.allocation(__import__('random').Random(i),'train',i)
           for i in range(4)]+[corpus.encounter(__import__('random').Random(i),'train',i)
           for i in range(4)]
    allowed=sel.compatibility_from_train(train)
    for index in range(16):
        pair=g.make_pair('train',index)
        for row in pair['rows']:
            by_id={n['id']:n for n in row['graph']['nodes']}
            assert all((edge['type'],by_id[edge['src']]['kind'],
                        by_id[edge['dst']]['kind']) in allowed
                       for edge in row['graph']['edges']
                       if edge['type'] in ('owns','refers_to','measures','applies_to'))


def test_spatial_constructor_uses_frozen_g1_only_and_preserves_admitted(monkeypatch):
    prompt='Alpha Beta Gamma Delta'
    offsets=[(0,5),(6,10),(11,16),(17,22)]
    prepared={'prompt':prompt,'prompt_start':0,'offsets':offsets}
    logits=torch.zeros(4,1,len(SPAN_KINDS)+1)
    # G1 is much more confident about early examples than about the suffix.
    for t in range(4):
        logits[t,0,SPAN_KINDS.index('action')+1]=5-t
        logits[t,0,SPAN_KINDS.index('quantity')+1]=5-t
    valid=torch.ones(4,1,dtype=torch.bool)
    original=[(0,5,'action'),(6,10,'action')]
    allowed=frozenset({('refers_to','action','action'),('measures','action','quantity')})
    parent={'expanded_anchors':original,'recovered_typed':g.p52.inventory(original,allowed)}
    monkeypatch.setattr(g.p52,'MAX_ANCHORS',5)
    raw={'span_logits':logits,'span_valid':valid,'prepared':prepared,'allowed':allowed}
    spatial=g._construct_spatial(raw,parent)
    assert set(original)<=set(spatial['anchors'])
    assert any(a[0]>=17 for a in spatial['anchors'] if a not in original)
    assert len(spatial['anchors'])<=5
    assert set(spatial['new_endpoint_provenance'])==set(spatial['new_anchors'])
    assert parent['recovered_typed']<=spatial['typed_edges']
    assert all(x['source']=='frozen_g1_raw_spatial'
               for x in spatial['new_endpoint_provenance'].values())


def test_readonly_cause_diagnosis_distinguishes_missing_lattice_and_score():
    pair=g.make_pair('train',0)
    row=pair['rows'][0]
    row['_g2_0059_distractor_count']=pair['distractor_count']
    gold=g.p50.g40.base.gold_binding_edges(row)
    # Deliberately omit one of the author-added quantity endpoints.
    appended=row['graph']['nodes'][-1]
    missing=(appended['span'][0],appended['span'][1],'quantity')
    assert any(missing in (src,dst) for src,dst,_ in gold)
    anchors={src for src,dst,_ in gold}|{dst for src,dst,_ in gold}
    anchors.remove(missing)
    allowed={(rel,src[2],dst[2]) for src,dst,rel in gold}
    edges=g.p52.inventory(sorted(anchors),allowed)
    logits=torch.zeros(1,1,len(SPAN_KINDS)+1)
    logits[0,0,SPAN_KINDS.index('quantity')+1]=-2
    expanded={'anchors':sorted(anchors),'typed_edges':edges,
              'raw_lattice':{missing[:2]:[(0,0)]},'logits':logits}
    report=g._diagnose_missing_edges(row,{'allowed':allowed},expanded)
    assert report['missing_edges']>0
    assert report['by_cause']=={'missing_endpoint':report['missing_edges']}
    assert report['by_origin']=={'added_distractor':report['missing_edges']}
    assert any(n['reason']=='raw_g1_favors_null'
               for case in report['cases'] for n in case['missing_nodes'])
    expanded['raw_lattice']={}
    report=g._diagnose_missing_edges(row,{'allowed':allowed},expanded)
    assert any(n['reason']=='no_exact_or_punctuation_trimmed_token_boundary'
               for case in report['cases'] for n in case['missing_nodes'])


def test_pilot_requires_first_shard_and_never_rebases_committed_capture(tmp_path):
    with pytest.raises(ValueError,match='PILOT_COMPARISON_REQUIRES_ONE_ORIGINAL_SHARD'):
        g.capture(tmp_path/'plan',tmp_path/'new',root=tmp_path,
                  candidate_policy='spatial',compare_capture=tmp_path/'old',
                  max_shards=2)


def test_same_sixteen_pairs_replayed_into_isolated_pilot(monkeypatch,tmp_path):
    from test_nanojev_gamer_v1_g2_scaling_0059 import _fake_capture
    root=_fake_capture(monkeypatch,tmp_path)
    original_construct=g.p52.construct
    def enriched(raw,proposal):
        result={**proposal,'typed_edges':set(proposal['edges']),
            'raw_lattice':{},'logits':torch.zeros(1,1,len(SPAN_KINDS)+1)}
        return result
    monkeypatch.setattr(g.p52,'construct',enriched)
    monkeypatch.setattr(g,'_construct_spatial',enriched)
    # The mocked capture generates a known-complete candidate inventory;
    # this test verifies the exact-pair comparison, not neural recall.
    original_run=g.p40.run
    def fake_run(*,diagnostic_callback,output,**kwargs):
        def patched_callback(**hooks):
            real_cap=hooks['capture_candidate_repair_rows']
            def cap(rows,tag,include_raw_g1):
                records=real_cap(rows,tag,include_raw_g1)
                for record in records:
                    record['g1_raw_interface']['allowed']={
                        (edge[2],edge[0][2],edge[1][2])
                        for edge in record['candidate_repair']['edges']}
                return records
            return diagnostic_callback(capture_candidate_repair_rows=cap,
                                       assert_all_frozen=hooks['assert_all_frozen'])
        return original_run(diagnostic_callback=patched_callback,output=output,**kwargs)
    monkeypatch.setattr(g.p40,'run',fake_run)
    plan=tmp_path/'plan';baseline=tmp_path/'original';pilot=tmp_path/'pilot'
    g.create_plan(plan,train_graphs=32,validation_graphs=8,
                  test_graphs=8,challenge_graphs=8)
    g.capture(plan,baseline,root=root,device='cpu',max_shards=1)
    original_sha=g.file_sha(g._shard_paths(baseline,'train',0)[0])
    result=g.capture(plan,pilot,root=root,device='cpu',max_shards=1,
                     candidate_policy='spatial',compare_capture=baseline)
    comparison=result['pilot_comparison']
    assert comparison['pairs']==16
    assert comparison['baseline_missing_edges']==0
    assert comparison['candidate_missing_edges']==0
    assert comparison['baseline_reachable_pairs']==16
    assert comparison['candidate_reachable_pairs']==16
    assert not comparison['production_promotion']
    assert g.file_sha(g._shard_paths(baseline,'train',0)[0])==original_sha
    assert json.loads((pilot/'capture.json').read_text())['candidate_policy']=='spatial'
    assert (pilot/'pilot-comparison.json').is_file()
    assert all('binding_diagnosis_before' in e and 'binding_diagnosis' in e
       for e in g.verify_shard(pilot,'train',0)['entries'])
    plain_pair,_=g.FeatureCorpus(plan,pilot,'train',require_complete=False).pair(
        0,with_labels=False)
    assert all('binding_diagnosis' not in row and 'binding_diagnosis_before' not in row
               for row in plain_pair)
    with pytest.raises(ValueError,match='CAPTURE_CONFIG_DRIFT'):
        g.capture(plan,baseline,root=root,device='cpu',max_shards=1,
                  candidate_policy='spatial')


def test_spatial_recovers_a_tail_binding_global_g1_rank_budget_misses(monkeypatch):
    from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
    prompt='Alpha Beta Gamma Delta'
    anchors=[(0,5,'action'),(6,10,'action')]
    allowed=frozenset({('measures','action','quantity')})
    parent={'expanded_anchors':anchors,
            'recovered_typed':g.p52.inventory(anchors,allowed)}
    offsets=[(0,5),(6,10),(11,16),(17,22)]
    logits=torch.zeros(4,1,len(SPAN_KINDS)+1)
    # Earlier mentions dominate the frozen ranking; Gamma and Delta are
    # individually valid but lose scarce global-extra-anchor slots.
    for t in range(4):
        logits[t,0,SPAN_KINDS.index('action')+1]=10-t
        logits[t,0,SPAN_KINDS.index('quantity')+1]=10-t
    raw={'span_logits':logits,'span_valid':torch.ones(4,1,dtype=torch.bool),
        'prepared':{'prompt':prompt,'prompt_start':0,'offsets':offsets},
        'allowed':allowed}
    monkeypatch.setattr(g.p52,'MAX_ANCHORS',6)
    before=g.p52.construct(raw,parent)
    after=g._construct_spatial(raw,parent)
    gold=((17,22,'action'),(11,16,'quantity'),'measures')
    assert gold not in before['typed_edges']
    assert gold in after['typed_edges']
    assert set(parent['expanded_anchors']) <= set(after['anchors'])


def test_spatial_generic_punctuation_normalization_for_numeric_marker(monkeypatch):
    prompt='Alpha 79.'
    offsets=[(0,5),(6,9)]  # Frozen tokenizer includes the period.
    logits=torch.zeros(2,1,len(SPAN_KINDS)+1)
    logits[1,0,SPAN_KINDS.index('quantity')+1]=9
    valid=torch.ones(2,1,dtype=torch.bool)
    allowed={('measures','action','quantity')}
    parent={'expanded_anchors':[(0,5,'action')],
            'recovered_typed':set()}
    monkeypatch.setattr(g.p52,'MAX_ANCHORS',8)
    raw={'span_logits':logits,'span_valid':valid,'allowed':allowed,
         'prepared':{'prompt':prompt,'prompt_start':0,'offsets':offsets}}
    out=g._construct_spatial(raw,parent)
    assert (6,8,'quantity') in out['anchors']
    assert ((0,5,'action'),(6,8,'quantity'),'measures') in out['typed_edges']
