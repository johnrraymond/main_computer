"""G2-0059 evidence pilot: label-blind spans, exact-pair replay and safety."""
from __future__ import annotations

import json
import re
from pathlib import Path
import pytest
import torch

from tools import nanojev_gamer_v1_g2_scaling_0059 as g
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def _mock_raw(prompt,base,allowed):
    # Synthetic frozen G1 lattice. It intentionally scores the last marker
    # quantity below null and gives a frequent decoy a higher margin.
    offsets=[m.span() for m in re.finditer(r'\S+',prompt)]
    prepared={'prompt':prompt,'prompt_start':0,'offsets':offsets,
              'eligible':torch.ones(len(offsets),dtype=torch.bool)}
    logits=torch.zeros(len(offsets),1,len(SPAN_KINDS)+1)
    for t,span in enumerate(offsets):
        logits[t,0,SPAN_KINDS.index('action')+1]=10-t
        logits[t,0,SPAN_KINDS.index('quantity')+1]=(-9 if t==len(offsets)-1 else 10-t)
    return {'span_logits':logits,'span_valid':torch.ones(len(offsets),1,dtype=torch.bool),
            'prepared':prepared,'allowed':allowed,'hidden':torch.zeros(len(offsets),8)},


def test_evidence_extracts_number_with_trailing_period_and_negative_margin():
    prompt='Alpha holds 7. Alpha measures -4. Alpha holds 79.'
    anchor=(0,5,'action')
    allowed={('measures','action','quantity')}
    raw=_mock_raw(prompt,[anchor],allowed)[0]
    parent={'expanded_anchors':[anchor],
            'recovered_typed':g.p52.inventory([anchor],allowed)}
    out=g._construct_evidence(raw,parent)
    for literal in ('7','-4','79'):
        found=[anchor for anchor in out['anchors'] if anchor[2]=='quantity'
               and prompt[anchor[0]:anchor[1]]==literal]
        assert found, (literal,out['anchors'])
    assert set(parent['recovered_typed'])<=out['typed_edges']
    assert all(item['source'] in ('inference_text_numeric',
                                'recurrent_frozen_g1_surface','frozen_g1_raw_spatial')
               for item in out['new_endpoint_provenance'].values())
    assert out['evidence_summary']['selected']<=g.p52.MAX_ANCHORS-len(parent['expanded_anchors'])


def test_evidence_recovers_repeated_action_from_frozen_span_surface():
    prompt='Amber met Silver; Silver met Amber. Amber met Silver.'
    allowed={('refers_to','action','action')}
    raw=_mock_raw(prompt,[],allowed)[0]
    parent={'expanded_anchors':[(0,5,'action')],
            'recovered_typed':g.p52.inventory([(0,5,'action')],allowed)}
    out=g._construct_evidence(raw,parent)
    assert (36,41,'action') in out['anchors']  # the second Amber
    assert (0,5,'action') in out['anchors']
    assert all(isinstance(a,tuple) for a in out['anchors'])


def test_all_first_16_authored_quantities_have_generic_lexical_span():
    # This tests source text morphology only. It does not assume the frozen
    # logits give these tokens positive scores or that neural capture succeeds.
    for index in range(16):
        for row in g.make_pair('train',index)['rows']:
            found={(m.start(),m.end()) for m in re.finditer(
                r'(?<![\w.])[-+]?\d+(?:\.\d+)?(?!\w)',row['prompt'])}
            for node in row['graph']['nodes']:
                if node['kind']=='quantity' and 'span' in node:
                    assert tuple(node['span']) in found, (index,node)


def test_evidence_replay_compares_spatial_parent_only(monkeypatch,tmp_path):
    from test_nanojev_gamer_v1_g2_scaling_0059 import _fake_capture
    root=_fake_capture(monkeypatch,tmp_path)
    def enriched(raw,proposal):
        return {**proposal,'typed_edges':set(proposal['edges']),
                'raw_lattice':{},'logits':torch.zeros(1,1,len(SPAN_KINDS)+1)}
    monkeypatch.setattr(g.p52,'construct',enriched)
    monkeypatch.setattr(g,'_construct_spatial',enriched)
    monkeypatch.setattr(g,'_construct_evidence',enriched)
    original_run=g.p40.run
    def fake_run(*,diagnostic_callback,output,**kwargs):
        def callback(**hooks):
            original_capture=hooks['capture_candidate_repair_rows']
            def cap(rows,tag,include_raw_g1):
                records=original_capture(rows,tag,include_raw_g1)
                for rec in records:
                    rec['g1_raw_interface']['allowed']={
                        (edge[2],edge[0][2],edge[1][2]) for edge in rec['candidate_repair']['edges']}
                return records
            return diagnostic_callback(capture_candidate_repair_rows=cap,
                    assert_all_frozen=hooks['assert_all_frozen'])
        return original_run(diagnostic_callback=callback,output=output,**kwargs)
    monkeypatch.setattr(g.p40,'run',fake_run)
    plan=tmp_path/'plan';baseline=tmp_path/'spatial';pilot=tmp_path/'evidence'
    with pytest.raises(ValueError,match='EVIDENCE_PILOT_REQUIRES_SPATIAL_PARENT'):
        g.capture(plan,pilot,root=root,device='cpu',max_shards=1,candidate_policy='evidence')
    g.create_plan(plan,train_graphs=32,validation_graphs=8,
                  test_graphs=8,challenge_graphs=8)
    g.capture(plan,baseline,root=root,device='cpu',max_shards=1,candidate_policy='spatial')
    old_sha=g.file_sha(g._shard_paths(baseline,'train',0)[0])
    inspect=g.inspect_missing(plan,baseline)
    assert inspect['missing_edges']==0
    assert inspect['capture_policy']=='spatial'
    result=g.capture(plan,pilot,root=root,device='cpu',max_shards=1,
                     candidate_policy='evidence',compare_capture=baseline)
    assert result['pilot_comparison']['baseline_missing_edges']==0
    assert result['pilot_comparison']['candidate_missing_edges']==0
    assert result['pilot_comparison']['candidate_reachable_pairs']==16
    assert result['pilot_comparison']['regressed_edges']==0
    assert g.file_sha(g._shard_paths(baseline,'train',0)[0])==old_sha
    assert json.loads((pilot/'capture.json').read_text())['candidate_policy']=='evidence'
    assert all('binding_diagnosis' in entry for entry in
               g.verify_shard(pilot,'train',0)['entries'])
    with pytest.raises(ValueError,match='PILOT_COMPARISON_PARENT_POLICY_MISMATCH'):
        g.capture(plan,tmp_path/'bad',root=root,device='cpu',max_shards=1,
                  candidate_policy='evidence',compare_capture=pilot)


def test_inspect_binding_rejects_non_pilot(tmp_path):
    with pytest.raises((FileNotFoundError,ValueError)):
        g.inspect_missing(tmp_path/'plan',tmp_path/'capture')
