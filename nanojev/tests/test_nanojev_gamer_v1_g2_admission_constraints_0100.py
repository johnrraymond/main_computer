"""0100: threshold/capacity factorial and strict gold-blind read-only lifecycle."""
from pathlib import Path
import json

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_admission_constraints_0100 as m
from tools import nanojev_gamer_v1_g2_mit_mechanism_0099 as previous
from tools import nanojev_gamer_v1_graph_curriculum as cur


def surface(*, a=0.6, b=0.8):
    objs = [[0,1,'quantity'],[2,3,'action'],[4,5,'action']]
    pairs = []
    for i in range(3):
        for j in range(3):
            if i == j:continue
            margins = [-10.,-10.,-10.,-10.]
            if (i,j) == (0,1):margins[3] = a
            if (i,j) == (0,2):margins[3] = b
            pairs.append([i,j,margins])
    return {'g1_objects':objs,'pair_margins':pairs,'binding_edges':[]}


def edges():
    return (('quantity',(0,1),'action',(2,3),'applies_to'),
            ('quantity',(0,1),'action',(4,5),'applies_to'))


SPEC = {'method':'joint','threshold':0.5,
        'out_caps':{'quantity|action|applies_to':1},
        'in_caps':{'action|quantity|applies_to':2}}


def test_two_factor_changes_are_separable_and_base_is_identical():
    original = json.dumps(SPEC,sort_keys=True)
    s = surface(a=.6,b=.8)
    base = m.decode_variant(s,SPEC)
    assert base == {edges()[1]}
    assert m.decode_variant(s,SPEC,offset=-.5) == base
    assert m.decode_variant(s,SPEC,capacity=1) == set(edges())
    assert m.decode_variant(s,SPEC,offset=-.5,capacity=1)==set(edges())
    s2=surface(a=.3,b=.8)
    assert m.decode_variant(s2,SPEC,capacity=1) == {edges()[1]}
    assert m.decode_variant(s2,SPEC,offset=-.3,capacity=1) == set(edges())
    assert json.dumps(SPEC,sort_keys=True)==original
    assert m.variant_spec(SPEC,capacity='unlimited')['method']=='threshold'
    assert m.decode_variant(s,SPEC,capacity='unlimited')==set(edges())


def test_oracle_ceilings_are_nested_and_read_only():
    s=surface(a=.3,b=.8)
    gold=set(edges())
    old=json.dumps(s,sort_keys=True)
    ceilings,categories=m._oracle(s,gold,SPEC)
    assert ceilings['admitted_oracle']=={edges()[1]}
    assert ceilings['threshold_released_oracle']==gold
    assert ceilings['g1_reachable_oracle']==gold
    assert categories['below_admission_threshold']==1
    assert categories['admitted_but_may_be_capacity_rejected']==1
    assert json.dumps(s,sort_keys=True)==old
    # Wrong-top-label edges do NOT count as threshold-only recoveries.
    s2=surface(a=.3,b=.8)
    for i,j,v in s2['pair_margins']:
        if (i,j)==(0,1):v[2]=2.0
    ceilings,categories=m._oracle(s2,gold,SPEC)
    assert ceilings['threshold_released_oracle']=={edges()[1]}
    assert categories['wrong_neural_top_label']==1
    ceilings,categories=m._oracle(s, {('quantity',(10,11),'action',(2,3),'applies_to')},SPEC)
    assert not any(ceilings.values())
    assert categories['g1_endpoint_unavailable']==1


def test_axis_selection_is_discovery_only(monkeypatch):
    records=[{'surface':surface(a=.3,b=.8),'family':'allocation' if i%2==0 else 'encounter',
              'gold':[m.source.json_edge(e) for e in edges()],
              'baseline':[m.source.json_edge(edges()[1])]}
             for i in range(120)]
    baseline=m._partition_scores(records,SPEC)
    threshold=m._axis_selection(records,SPEC,'admission',baseline)
    capacity=m._axis_selection(records,SPEC,'capacity',baseline)
    assert threshold['selected'] in m.ADMISSION_OFFSETS
    assert capacity['selected'] in m.CAP_INCREMENTS
    old_threshold=threshold['selected']
    old_capacity=capacity['selected']
    for i in range(120):
        if previous.group_of(i) != 'discovery':
            records[i]['surface']=surface(a=-6,b=-7)
            records[i]['baseline']=[]
    baseline2=m._partition_scores(records,SPEC)
    assert m._axis_selection(records,SPEC,'admission',baseline2)['selected']==old_threshold
    assert m._axis_selection(records,SPEC,'capacity',baseline2)['selected']==old_capacity


def _stage(tmp_path, monkeypatch):
    root=tmp_path/'reports';root.mkdir()
    source_dir=root/'oracle-fabric-g2-extended-learning-0096'/'warm_supervised'/'milestone-01600'
    source_dir.mkdir(parents=True)
    save_file({'binding.weight':torch.zeros(5,192),'binding.bias':torch.zeros(5)},
              str(source_dir/cur.HEAD_FILE))
    m.write(source_dir/cur.MANIFEST,{'head_sha256':m.sha(source_dir/cur.HEAD_FILE),
                                     'g3_unlocked':False})
    identity=m.source._candidate_info(source_dir)
    prior=root/m.TRAIN_CAMPAIGN
    (prior/'capture').mkdir(parents=True)
    m.write(prior/'commit.json',{'preexisting':'0099 evidence'})
    m.write(prior/'capture'/'lock.json',{'locked':'0099'})
    frozen=m.sha(source_dir/cur.HEAD_FILE)
    training=[]
    for i in range(120):
        family='allocation' if i%2==0 else 'encounter'
        training.append({'family':family,'surface':surface(a=.3,b=.8),
                         'gold':[m.source.json_edge(e) for e in edges()],
                         'baseline':[m.source.json_edge(edges()[1])]})
    previous_commit={'source':identity,'decoder_spec':SPEC}
    monkeypatch.setattr(m,'_selected_source',lambda root:(previous_commit,training,prior))
    dataset=root/'train.jsonl';dataset.write_text('frozen training set')
    monkeypatch.setattr(m.structural,'paths_for',lambda root:{'dataset':dataset})
    def challenge(root,dataset,count):
        public=[{'case_id':f'0100-{i}', 'family': 'allocation' if i%2==0 else 'encounter',
                 'prompt':f'unseen-{i}', 'prompt_sha256':m.digest(f'unseen-{i}')}
                for i in range(count)]
        secret=[{'case_id':r['case_id'],'family':r['family'],
                 'prompt_sha256':r['prompt_sha256'],'graph':{}} for r in public]
        return public,secret
    monkeypatch.setattr(m,'_fresh_cohort',challenge)
    class FakeComposite:
        def predict(self,*args,**kwargs):return {'token_spans':[]}
    class Head:
        def eval(self):return self
        def requires_grad_(self,flag):return self
    monkeypatch.setattr(m.structural,'_load_runtime',lambda *args:(
        None,None,FakeComposite(),Head(),{}))
    monkeypatch.setattr(m.source,'_load_scientific_head',lambda *args,**kwargs:Head())
    monkeypatch.setattr(m.bakeoff.previous,'_capture_inference',lambda *args:
        ({'eligible':torch.ones(3)}, torch.ones(3)))
    monkeypatch.setattr(m.bakeoff,'_infer',lambda *args,**kwargs:surface(a=.3,b=.8))
    monkeypatch.setattr(m.bakeoff,'truth',lambda graph:set(edges()))
    return root,source_dir,frozen


def test_complete_lifecycle_gold_blind_and_unchanged_weights(tmp_path,monkeypatch):
    root, source_dir, frozen = _stage(tmp_path,monkeypatch)
    c=m.prepare(root=root,cases=40)
    dest=m.location(root)
    assert c['source']['head_sha256']==frozen
    assert set(c['factors'])==set(m.ARMS)
    assert m.read(dest/'train_evidence.json')['baseline']['protection']['overall']['cases']==40
    secret=dest/'seal'/'answers.SECRET.jsonl'
    hidden=secret.with_suffix('.hidden')
    secret.rename(hidden)
    m.predict(root=root,device='cpu')
    hidden.rename(secret)
    report=m.score(root=root)
    assert report['source_unchanged'] is True
    assert report['weight_edits']==0 and report['gradient_steps']==0
    assert report['production_promotion'] is False and report['g3_unlocked'] is False
    assert report['metrics']['overall']['baseline']['cases']==40
    assert report['gold_missing_causes']['overall']['below_admission_threshold']==40
    assert report['gold_informed_ideal_precision_ceilings']['overall']['threshold_released_oracle']['exact']==40
    assert m.sha(source_dir/cur.HEAD_FILE)==frozen
    assert list(root.rglob('gamer_graph_head.safetensors'))==[source_dir/cur.HEAD_FILE]
    assert m.run(root=root,device='cpu')['source_unchanged']


def test_tampered_blind_surface_is_rejected(tmp_path,monkeypatch):
    root, source_dir, frozen = _stage(tmp_path,monkeypatch)
    m.prepare(root=root,cases=40)
    dest=m.location(root)
    m.predict(root=root,device='cpu')
    shard=dest/'blind'/'shards'/'0100-0.json.gz'
    tampered=m._gzip_read(shard)
    tampered['surface']['pair_margins'][0][2][3] = 900
    m._gzip_write(shard,tampered)
    with pytest.raises(ValueError,match='BLIND_SHARD_CHECKSUM_DRIFT'):
        m.score(root=root)
    assert m.sha(source_dir/cur.HEAD_FILE)==frozen


def test_source_change_fails_closed(tmp_path,monkeypatch):
    root,source_dir,frozen = _stage(tmp_path,monkeypatch)
    m.prepare(root=root,cases=40)
    dest=m.location(root)
    c=m.read(dest/'seal'/'commit.json')
    c['source']['head_sha256']='0'*64
    m.write(dest/'seal'/'commit.json',c)
    with pytest.raises(ValueError,match='TRAIN_EVIDENCE_OR_SOURCE_DRIFT'):
        m.verify(root,dest)


def test_factor_tampering_fails_even_before_blind_inference(tmp_path,monkeypatch):
    root,_,_=_stage(tmp_path,monkeypatch)
    m.prepare(root=root,cases=40)
    dest=m.location(root)
    c=m.read(dest/'seal'/'commit.json')
    c['factors']['admission_only']['offset']=0.0
    m.write(dest/'seal'/'commit.json',c)
    with pytest.raises(ValueError,match='FROZEN_FACTORS_DRIFT'):
        m.predict(root=root,device='cpu')


def test_blind_resume_uses_saved_shards_and_no_gold(tmp_path,monkeypatch):
    root,_,_=_stage(tmp_path,monkeypatch)
    m.prepare(root=root,cases=40)
    dest=m.location(root)
    secret=dest/'seal'/'answers.SECRET.jsonl'
    hidden=secret.with_suffix('.hidden')
    secret.rename(hidden)
    partial=dest/'blind'/'shards'
    partial.mkdir(parents=True)
    first={'case_id':'0100-0','family':'allocation','prompt_sha256':m.digest('unseen-0'),
           'surface':surface(a=.3,b=.8)}
    c=m.read(dest/'seal'/'commit.json')
    first['predictions']={name:[m.source.json_edge(e) for e in sorted(
        m.decode_variant(first['surface'],c['decoder_spec'],**c['factors'][name]))]
        for name in m.ARMS}
    m._gzip_write(partial/'0100-0.json.gz',first)
    original_digest=m.sha(partial/'0100-0.json.gz')
    m.predict(root=root,device='cpu')
    assert m.sha(partial/'0100-0.json.gz')==original_digest
    hidden.rename(secret)
    assert m.score(root=root)['metrics']['overall']['baseline']['cases']==40
