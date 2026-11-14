"""Regression and mocked replay checks for G1-0022 (not CUDA certification)."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import random

import pytest
import torch

from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
from tools.nanojev_gamer_v1_graph_contract import execute_graph,validate_graph
from tools.nanojev_gamer_v1_graph_head import SPAN_KINDS
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g1_structural_ood as exp


@pytest.mark.parametrize('factory', [encounter,allocation])
@pytest.mark.parametrize('layout',exp.LAYOUTS)
@pytest.mark.parametrize('index',[0,1,2,3])
def test_ood_layout_preserves_every_labeled_mention_and_graph(factory,layout,index):
    original=factory(random.Random(17+index),'dev',index)
    altered=exp.structural_ood_row(original,layout=layout)
    assert original['prompt']!=altered['prompt']
    assert original['graph']['edges']==altered['graph']['edges']
    assert original['graph']['objective']==altered['graph']['objective']
    assert altered['split']=='ood_dev'
    assert altered['surface_shift']==layout
    assert altered['source_prompt_sha256']==hashlib.sha256(original['prompt'].encode()).hexdigest()
    assert altered['expected_decisions']==original['expected_decisions']
    assert execute_graph(altered['graph'])['decisions']==original['expected_decisions']
    validate_graph(altered['graph'],complete=True)
    for before,after in zip(original['graph']['nodes'],altered['graph']['nodes']):
        assert before['kind']==after['kind']
        assert before.get('value')==after.get('value')
        if 'span' in before:
            x,y=before['span'];a,b=after['span']
            assert original['prompt'][x:y]==altered['prompt'][a:b]
    assert len(altered['object_spans'])==len(original['object_spans'])
    assert 'DATA LEDGER' in altered['prompt'] or 'EVIDENCE DOSSIER' in altered['prompt']
    assert altered['prompt'].index('benefit')<altered['prompt'].index('compares') if (
        factory is allocation and index==0) else True


def test_ood_rejects_wrong_split_layout_and_malformed_source():
    row=allocation(random.Random(3),'dev',1)
    for bad in ('original','not-a-layout'):
        with pytest.raises(ValueError,match='INVALID_SOURCE_OR_LAYOUT'):
            exp.structural_ood_row(row,layout=bad)
    row['split']='test'
    with pytest.raises(ValueError,match='INVALID_SOURCE_OR_LAYOUT'):
        exp.structural_ood_row(row,layout=exp.LAYOUTS[0])
    row['split']='dev';row['prompt']='A simplified invalid prompt.'
    with pytest.raises(ValueError,match='SOURCE_SENTENCES_UNEXPECTED'):
        exp.structural_ood_row(row,layout=exp.LAYOUTS[0])


def prior_samples(count,prefix):
    return [{'split':'dev','family':exp.FAMILIES[i%2],
             'prompt':f'{prefix}-{i}'} for i in range(count)]


def test_ood_generation_is_fixed_disjoint_and_balanced(tmp_path,monkeypatch):
    monkeypatch.setattr(exp,'_original_prompts',lambda _:{'already in train'})
    parents=[prior_samples(80,'0011'),prior_samples(320,'0014'),
             prior_samples(320,'0018'),prior_samples(320,'0021')]
    rows=exp.ood_rows('ignored',*parents)
    rows2=exp.ood_rows('ignored',*parents)
    assert exp._jsonl_bytes(rows)==exp._jsonl_bytes(rows2)
    assert len(rows)==320
    counts=Counter((r['family'],r['surface_shift']) for r in rows)
    assert sorted(counts.values())==[80]*4
    old={r['prompt'] for group in parents for r in group}
    assert all(r['prompt'] not in old and r['split']=='ood_dev' for r in rows)
    with pytest.raises(ValueError,match='GENERATION_PARAMETERS_LOCKED'):
        exp.ood_rows('ignored',*parents,seed=221)
    parents[0][0]['prompt']='already in train'
    with pytest.raises(ValueError,match='PRIOR_OVERLAP_OR_STRUCTURE'):
        exp.ood_rows('ignored',*parents)


def _prior_fixture(tmp_path):
    old=prior_samples(320,'width')
    rows=prior_samples(320,'guard')
    path=tmp_path/'0021';path.mkdir()
    report={
        'schema':exp.PRIOR_SCHEMA,
        'status':'FRESH_WITHIN_GENERATOR_DEV_VALIDATION_NOT_CERTIFICATION',
        'seed':exp.PRIOR_SEED,'cases':320,
        'families':{'encounter':160,'allocation':160},
        'candidate_frozen_for_validation_only':exp.GUARD,
        'source_selective_report_sha256':'selective-sha',
        'source_adapter_sha256':'adapter-sha',
        'checkpoint_file_sha256':{'head_file_sha256':'head','manifest_sha256':'manifest'},
        'excluded_0011_dataset_sha256':'fresh-sha',
        'excluded_0014_dataset_sha256':'validation-sha',
        'new_dev_dataset_sha256':hashlib.sha256(exp._jsonl_bytes(rows)).hexdigest(),
        'configurations_predeclared':[
            {'key':exp.CONTROL,'checkpoint':'null_focused',
             'null_logit_shift':4.0,'action_width_power':1.0,'action_per_span_penalty':0.0},
            {'key':exp.GUARD,'checkpoint':'null_focused',
             'null_logit_shift':4.0,'action_width_power_for_proposal':1.25,
             'min_score_retention':0.85,'boundary_guard':True}],
        'passes_predeclared_four_metric_guard':True,
        'fresh_advantage_checks':{
             'higher_object_f1':True,'more_exact_prompts':True,
             'no_more_false_positives':True,'no_more_missing_objects':True},
        'metrics':{exp.CONTROL:dict(exp.PRIOR_CONTROL),exp.GUARD:dict(exp.PRIOR_GUARD)},
        'paired_comparison':{'a_only_exact':0,'b_only_exact':8,'exact_discordant_pairs':8,
            'b_newly_recovered_gold':16,'b_previously_correct_gold_lost':2,
            'b_old_false_positives_removed':16,'b_new_false_positives':2},
        **{name:True for name in ('heads_frozen','candidate_eligibility_unchanged',
                    'one_feature_capture_per_prompt','no_training',
                    'production_decoder_unchanged')},
        **{name:False for name in ('challenge_or_test_split_used',
              'certification_authorized','production_promotion','g2_unlocked')},
    }
    # Synthetic per-case totals add up to the independently recorded G1-0021
    # control and challenger counts; no neural network inference is claimed.
    cases=[]
    for i,row in enumerate(rows):
        gold=28 if i<319 else 49
        miss_a=int(i>=117)+int(117<=i<206)
        miss_b=int(i>=125)+int(125<=i<208)
        fp_a=int(i>=117)+int(117<=i<145)
        fp_b=int(i>=125)+int(125<=i<147)
        row['object_spans']=[{'start':0,'end':1,'kind':'action'}]*gold
        cases.append({'index':i+1,'family':row['family'],
            'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
            'gold_count':gold,'configurations':{
                exp.CONTROL:{'correct':gold-miss_a,'missing':miss_a,
                             'false_positives':fp_a,'exact':i<117},
                exp.GUARD:{'correct':gold-miss_b,'missing':miss_b,
                           'false_positives':fp_b,'exact':i<125}}})
    report['new_dev_dataset_sha256']=hashlib.sha256(exp._jsonl_bytes(rows)).hexdigest()
    def save():
        (path/exp.PRIOR_FILES[0]).write_text(json.dumps(report),encoding='utf-8')
    save()
    (path/exp.PRIOR_FILES[1]).write_bytes(exp._jsonl_bytes(rows))
    (path/exp.PRIOR_FILES[2]).write_bytes(exp._jsonl_bytes(cases))
    (path/exp.PRIOR_FILES[3]).write_bytes(exp._jsonl_bytes([{'index':1}]))
    info={'adapter_sha':'adapter-sha',
         'checkpoint_files':{'null_focused':{'head_file_sha256':'head','manifest_sha256':'manifest'}},
         'fresh_dataset_sha':'fresh-sha',
         'validation_report':{'validation_dataset_sha256':'validation-sha'}}
    def load():
        return exp._load_0021_reference(path,info,
          {exp.SELECTIVE_FILES[0]:'selective-sha'},old)
    return path,report,rows,cases,save,load


def test_0021_reference_is_frozen(tmp_path):
    path,report,rows,cases,save,load=_prior_fixture(tmp_path)
    parsed,found,paths,hashes=load()
    assert len(found)==320 and len(paths)==4
    for name in ('b_only_exact','b_previously_correct_gold_lost'):
        original=report['paired_comparison'][name]
        report['paired_comparison'][name]=original+1;save()
        with pytest.raises(ValueError,match='INVALID_0021_REFERENCE:paired'):
            load()
        report['paired_comparison'][name]=original;save()
    report['g2_unlocked']=True;save()
    with pytest.raises(ValueError,match='INVALID_0021_REFERENCE:g2_unlocked'):
        load()
    report['g2_unlocked']=False;save()
    cases[7]['prompt_sha256']='altered'
    (path/exp.PRIOR_FILES[2]).write_bytes(exp._jsonl_bytes(cases))
    with pytest.raises(ValueError,match='INVALID_0021_REFERENCE:case_8'):
        load()


def test_physical_reference_hash_still_rejects_crlf_drift(tmp_path):
    path,report,rows,cases,save,load=_prior_fixture(tmp_path)
    _,_,files,hashes=load()
    p=files[exp.PRIOR_FILES[1]]
    p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
    with pytest.raises(ValueError,match='GUARD_INPUT_CHANGED_DURING_RUN'):
        exp._check_files_unchanged(files,hashes,'GUARD')


def test_mocked_320_case_ood_replay(tmp_path,monkeypatch):
    cp=tmp_path/'cp';cp.mkdir()
    (cp/'manifest.json').write_bytes(b'manifest')
    (cp/cur.HEAD_FILE).write_bytes(b'head')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    adapter=tmp_path/'adapter';adapter.write_bytes(b'freeze')
    info={
        'entries':{'null_focused':{'checkpoint':cp,'manifest':{
            'source_adapter':str(adapter),'model_id':'mock','head_sha256':'head-sha'}}},
        'adapter_sha':'adapter-sha',
        'checkpoint_files':{'null_focused':{'manifest_sha256':sha(cp/'manifest.json'),
                                           'head_file_sha256':sha(cp/cur.HEAD_FILE)}},
        'dataset':tmp_path/'original','fresh_rows':[],'validation_rows':[],
        'validation_report':{'validation_dataset_sha256':'validation-sha'},
        'fresh_dataset_sha':'fresh-sha'}
    rows=[{'prompt':'ab','family':exp.FAMILIES[i%2],
          'split':'ood_dev','surface_shift':exp.LAYOUTS[(i//2)%2],
          'object_spans':[{'start':0,'end':2,'kind':'action'}]}
          for i in range(320)]
    monkeypatch.setattr(exp,'_load_prior_inputs',lambda *args:info)
    monkeypatch.setattr(exp,'_load_action_reference',lambda *args:({}, {}, {}))
    monkeypatch.setattr(exp,'_load_ranking_reference',lambda *args:({}, {}, {}))
    monkeypatch.setattr(exp,'prior_width_rows',lambda *args:rows)
    monkeypatch.setattr(exp,'_load_width_reference',lambda *args:(
        {'validation_dataset_sha256':'width-sha'},[],{},
        {exp.WIDTH_FILES[0]:'width-report-sha'}))
    monkeypatch.setattr(exp,'_read_conflict_reference',lambda *args:({},[],{},
        {exp.CONFLICT_FILES[0]:'conflict-report-sha'}))
    monkeypatch.setattr(exp,'_load_selective_reference',lambda *args:({}, {},
        {exp.SELECTIVE_FILES[0]:'selective-sha'}))
    monkeypatch.setattr(exp,'_load_0021_reference',lambda *args:(
        {'new_dev_dataset_sha256':'guard-sha'},[],{},
        {exp.PRIOR_FILES[0]:'guard-report-sha'}))
    monkeypatch.setattr(exp,'ood_rows',lambda *args:rows)
    monkeypatch.setattr(exp,'_verify_no_drift',lambda *args:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:'adapter-sha')
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),object()))
    action=SPAN_KINDS.index('action')+1
    class Head:
        def eval(self):return self
        def __call__(self,hidden,**kwargs):
            logits=torch.full((2,2,len(SPAN_KINDS)+1),-40.,dtype=torch.float32)
            logits[:,:,0]=4.0
            logits[0,0,action]=4.0
            logits[0,1,action]=1.8
            valid=torch.zeros(2,2,dtype=torch.bool)
            valid[0,0]=True;valid[0,1]=True;valid[1,0]=True
            return {'span_logits':logits,'span_valid':valid}
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *args,**kwargs:(Head(),{
        'stage':'G1','head_sha256':'head-sha'}))
    monkeypatch.setattr(cur,'capture',lambda *args:{
        'prepared':{'eligible':torch.ones(2),'offsets':[(0,1),(1,2)],
                    'prompt_start':0,'prompt':'ab'},
        'hidden':torch.zeros(2,4)})
    output=tmp_path/'out'
    report=exp.run(root='r',transfer_report='t',fresh_run='f',null_run='n',
        validation_run='v',action_run='a',ranking_run='k',width_run='w',
        conflict_run='c',selective_run='s',guard_run='g',output=output,device='cpu')
    assert report['frozen_development_leader']==exp.GUARD
    assert report['metrics'][exp.GUARD]['exact_prompts']==320
    assert report['metrics'][exp.CONTROL]['exact_prompts']==0
    assert report['cases']==320 and not report['g2_unlocked']
    assert not report['certification_authorized'] and not report['sealed_test_used']
    assert all(v==80 for v in report['shift_distribution'].values())
    assert len(exp._read_jsonl(output/'structural_ood_per_case.jsonl'))==320
    frozen=json.loads((output/'frozen_development_leader.json').read_text())
    assert frozen['checkpoint']=='null_focused'
    assert frozen['source_0021_report_sha256']=='guard-report-sha'
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        exp.run(root='r',transfer_report='t',fresh_run='f',null_run='n',
        validation_run='v',action_run='a',ranking_run='k',width_run='w',
        conflict_run='c',selective_run='s',guard_run='g',output=output,device='cpu')
