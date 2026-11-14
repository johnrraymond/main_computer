"""G1-0021: fixed fresh validation and immutable lineage tests (CPU only)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_boundary_guard_validation as exp
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

ACTION=SPAN_KINDS.index('action')+1
CLASS_COUNT=len(SPAN_KINDS)+1


def test_fixed_comparison_cli_is_read_only(capsys):
    assert exp.GUARD=='selective_boundary_guard'
    assert exp.MIN_SCORE_RETENTION==.85
    assert exp.GUARD_ENABLED is True
    assert exp.SEED not in (174321,281803)
    assert [a[0] for a in exp.RULES if a[2]] == [exp.GUARD]
    with pytest.raises(SystemExit) as exc:
        cur.main(['frob-g1-boundary-guard-validation','--help'])
    assert exc.value.code==0
    helptext=capsys.readouterr().out
    for flag in ('--selective-run','--conflict-run','--width-run','--output','--device'):
        assert flag in helptext
    for flag in ('--seed','--power','--margin','--train','--promote','--g2'):
        assert flag not in helptext


def test_explicit_nomination_explains_missing_0020_candidate():
    text=Path(exp.__file__).read_text(encoding='utf-8')
    assert 'left its candidate field null' in text
    assert 'prior_candidate_was_null_by_design' in text
    assert "'candidate_frozen_for_validation_only':GUARD" in text
    assert 'g2_unlocked\':False' in text


def test_fresh_rows_exclude_all_prior_sets_and_are_deterministic(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    monkeypatch.setattr(exp,'_original_prompts',lambda _dataset:{'original-prompt'})
    c={'n':0}
    def make(family):
        def generator(rng,split,index):
            c['n']+=1
            return {'prompt':f'{family}-{index}-{c["n"]}',
                    'family':family,'split':split,'object_spans':[]}
        return generator
    monkeypatch.setattr(corpus,'encounter',make('encounter'))
    monkeypatch.setattr(corpus,'allocation',make('allocation'))
    prior1=[{'prompt':f'fresh-{i}','family':'encounter','split':'dev'} for i in range(80)]
    prior2=[{'prompt':f'validation-{i}','family':'encounter','split':'dev'} for i in range(320)]
    prior3=[{'prompt':f'width-{i}','family':'allocation','split':'dev'} for i in range(320)]
    rows=exp.fresh_validation_rows(tmp_path,prior1,prior2,prior3)
    assert len(rows)==320
    assert sum(r['family']=='encounter' for r in rows)==160
    assert sum(r['family']=='allocation' for r in rows)==160
    assert all(r['split']=='dev' for r in rows)
    assert len({r['prompt'] for r in rows})==320
    c['n']=0
    assert rows==exp.fresh_validation_rows(tmp_path,prior1,prior2,prior3)
    with pytest.raises(ValueError,match='GENERATION_SETTINGS_LOCKED'):
        exp.fresh_validation_rows(tmp_path,prior1,prior2,prior3,seed=3)
    with pytest.raises(ValueError,match='PRIOR_PROMPT_OVERLAP:0018'):
        exp.fresh_validation_rows(tmp_path,prior1,prior2,prior2)
    with pytest.raises(ValueError,match='PRIOR_PROMPT_OVERLAP:0011'):
        exp.fresh_validation_rows(tmp_path,prior1[:-1]+[prior1[0]],prior2,prior3)


def _selective_fixture(tmp_path,monkeypatch):
    # Valid synthetic six-arm report, generated separately from CUDA evaluation.
    path=tmp_path/'0020';path.mkdir()
    rows=[{'prompt':f'prompt {i}',
           'family':'encounter' if i%2==0 else 'allocation',
           'split':'dev',
           'object_spans':[{'start':0,'end':1,'kind':'action'}]} for i in range(320)]
    metrics={}
    cases=[]
    for index,row in enumerate(rows,1):
        configurations={}
        for arm in exp.ARMS:
            good=arm==exp.GUARD or index<=160
            configurations[arm]={
                'correct':int(good),'missing':int(not good),
                'false_positives':int(not good),'exact':good}
        cases.append({'index':index,'family':row['family'],
                      'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
                      'configurations':configurations})
    for arm in exp.ARMS:
        guard=arm==exp.GUARD
        metrics[arm]={'exact_prompts':320 if guard else 160,
                      'correct':320 if guard else 160,'expected':320,
                      'missing_objects':0 if guard else 160,
                      'false_positive_objects':0 if guard else 160,
                      'f1':1. if guard else .5}
    monkeypatch.setattr(exp,'OLD_REFERENCE',{name:{k:metrics[name][k] for k in
        ('exact_prompts','correct','expected','missing_objects','false_positive_objects')}
            for name in (exp.CONTROL,exp.GUARD)})
    report={
        'schema':exp.SELECTIVE_SCHEMA,
        'status':'SELECTIVE_CONFLICT_DEV_ONLY_NOT_CERTIFICATION',
        'cases':320,'families':{'encounter':160,'allocation':160},
        'configuration_predeclared':list(exp.ARMS),
        'rules_predeclared':[{'name':n,'min_score_retention':r,'boundary_guard':b}
                             for n,r,b in exp.RULES],
        'candidate_for_new_independent_validation':None,
        'source_width_report_sha256':'width-sha',
        'source_width_dataset_sha256':'dataset-sha',
        'source_conflict_report_sha256':'conflict-sha',
        'source_adapter_sha256':'adapter-sha',
        'checkpoint_file_sha256':{'head_file_sha256':'head','manifest_sha256':'manifest'},
        'diagnostic_signal_summary':{'gold_tagged_separability_after_inference_only':{
            exp.GUARD:dict(exp.OLD_SIGNALS)}},
        'metrics':metrics,
        'paired_vs_control':{exp.GUARD:{
           'a_only_exact':0,'b_only_exact':160,'exact_discordant_pairs':160,
           'b_newly_recovered_gold':160,'b_previously_correct_gold_lost':0,
           'b_new_false_positives':0,'b_old_false_positives_removed':160}},
        **{name:True for name in ('heads_frozen','candidate_eligibility_unchanged',
            'production_decoder_unchanged','one_feature_capture_per_prompt',
            'no_training','gold_used_after_inference_only')},
        **{name:False for name in ('challenge_or_test_split_used',
            'certification_authorized','production_promotion','g2_unlocked')},
    }
    paths={name:path/name for name in exp.FILES}
    paths[exp.FILES[1]].write_bytes(exp._jsonl_bytes(cases))
    paths[exp.FILES[2]].write_bytes(exp._jsonl_bytes([
        {'index':1,'rule':name,'take_challenger':False}
        for name,_,_ in exp.RULES]))
    paths[exp.FILES[3]].write_bytes(exp._jsonl_bytes([{'direction':'lost_gold'}]*34))
    def load():
        return exp._load_selective_reference(path,
           {'adapter_sha':'adapter-sha','checkpoint_files':{'null_focused':{
             'head_file_sha256':'head','manifest_sha256':'manifest'}}},
           {'validation_dataset_sha256':'dataset-sha','metrics':{
             exp.CONTROL:metrics[exp.CONTROL],exp.CHALLENGER:metrics[exp.CHALLENGER]}},
           {exp.WIDTH_FILES[0]:'width-sha'},
           {exp.CONFLICT_FILES[0]:'conflict-sha'},rows)
    def save():
        paths[exp.FILES[0]].write_text(json.dumps(report),encoding='utf-8')
    save()
    return path,report,paths,load,save


def test_selective_reference_accepts_null_as_explicit_no_auto_selection(tmp_path,monkeypatch):
    _,report,paths,load,save=_selective_fixture(tmp_path,monkeypatch)
    loaded,files,hashes=load()
    assert loaded['candidate_for_new_independent_validation'] is None
    assert len(files)==4 and len(hashes)==4
    report['candidate_for_new_independent_validation']=exp.GUARD
    save()
    with pytest.raises(ValueError,match='candidate_for_new_independent_validation'):
        load()


@pytest.mark.parametrize('change,where',[
    ('source_width_report_sha256','source_width_report_sha256'),
    ('source_conflict_report_sha256','source_conflict_report_sha256'),
    ('source_adapter_sha256','source_adapter_sha256'),
    ('g2_unlocked','g2_unlocked'),
    ('production_promotion','production_promotion'),
    ('rules_predeclared','rules_predeclared'),
    ('candidate_metrics','selective_boundary_guard.exact_prompts'),
    ('candidate_signals','candidate_signals'),
])
def test_selective_reference_rejects_mutated_evidence(tmp_path,monkeypatch,change,where):
    _,report,paths,load,save=_selective_fixture(tmp_path,monkeypatch)
    if change=='g2_unlocked' or change=='production_promotion':report[change]=True
    elif change=='rules_predeclared':report[change][-1]['min_score_retention']=.9
    elif change=='candidate_metrics':report['metrics'][exp.GUARD]['exact_prompts']=100
    elif change=='candidate_signals':
        report['diagnostic_signal_summary']['gold_tagged_separability_after_inference_only'][exp.GUARD]['harmful_long_actions_accepted']=1
    else:report[change]='altered'
    save()
    with pytest.raises(ValueError,match=where):load()


def test_selective_reference_detects_case_and_physical_drift(tmp_path,monkeypatch):
    _,report,paths,load,save=_selective_fixture(tmp_path,monkeypatch)
    loaded,files,hashes=load()
    paths[exp.FILES[2]].write_text(paths[exp.FILES[2]].read_text()+'\n',encoding='utf-8')
    with pytest.raises(ValueError,match='SELECTIVE_INPUT_CHANGED_DURING_RUN'):
        exp._check_files_unchanged(files,hashes,'SELECTIVE')
    paths[exp.FILES[2]].write_bytes(exp._jsonl_bytes([
        {'index':1,'rule':'not-a-rule','take_challenger':True}]))
    with pytest.raises(ValueError,match='BAD_DECISION_ROW'):
        load()


def test_mocked_frozen_320_case_replay(tmp_path,monkeypatch):
    cp=tmp_path/'cp';cp.mkdir()
    (cp/'manifest.json').write_bytes(b'manifest')
    (cp/cur.HEAD_FILE).write_bytes(b'head')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    adapter=tmp_path/'adapter';adapter.write_text('adapter')
    info={
        'entries':{'null_focused':{'checkpoint':cp,'manifest':{
            'source_adapter':str(adapter),'model_id':'mock','head_sha256':'head-sha'}}},
        'adapter_sha':'adapter-sha',
        'checkpoint_files':{'null_focused':{
            'manifest_sha256':sha(cp/'manifest.json'),
            'head_file_sha256':sha(cp/cur.HEAD_FILE)}},
        'dataset':tmp_path/'original','fresh_rows':[], 'validation_rows':[],
        'validation_report':{'validation_dataset_sha256':'validation-sha'},
        'fresh_dataset_sha':'fresh-sha',
    }
    rows=[{'prompt':'ab','family':'encounter' if i%2==0 else 'allocation',
           'split':'dev','object_spans':[{'start':0,'end':2,'kind':'action'}]}
          for i in range(320)]
    monkeypatch.setattr(exp,'_load_prior_inputs',lambda *args:info)
    monkeypatch.setattr(exp,'_load_action_reference',lambda *args:({}, {}, {}))
    monkeypatch.setattr(exp,'_load_ranking_reference',lambda *args:({}, {}, {}))
    monkeypatch.setattr(exp,'prior_width_rows',lambda *args:rows)
    monkeypatch.setattr(exp,'_load_width_reference',lambda *args:({
        'validation_dataset_sha256':'width-sha'},[],{},
        {exp.WIDTH_FILES[0]:'width-report-sha'}))
    monkeypatch.setattr(exp,'_read_conflict_reference',lambda *args:({},[],{},
        {exp.CONFLICT_FILES[0]:'conflict-report-sha'}))
    monkeypatch.setattr(exp,'_load_selective_reference',lambda *args:(
        {'metrics':{exp.CONTROL:{'exact_prompts':122},
                    exp.GUARD:{'exact_prompts':128}}},{},
        {exp.FILES[0]:'selective-sha'}))
    monkeypatch.setattr(exp,'fresh_validation_rows',lambda *args:rows)
    monkeypatch.setattr(exp,'_verify_no_drift',lambda *args:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:'adapter-sha')
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),object()))
    class Head:
        def eval(self):return self
        def __call__(self,hidden,**kwargs):
            logits=torch.full((2,2,CLASS_COUNT),-40.,dtype=torch.float32)
            logits[:,:,0]=4.0
            logits[0,0,ACTION]=4.0 # null shifts from 4 to 0; margin=4
            logits[0,1,ACTION]=1.8 # null shifts from 4 to 0; margin=1.8
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
    report=exp.run(root='root',transfer_report='transfer',fresh_run='fresh',
        null_run='null',validation_run='validation',action_run='action',
        ranking_run='ranking',width_run='width',conflict_run='conflict',
        selective_run='selective',output=output,device='cpu')
    assert report['metrics'][exp.GUARD]['exact_prompts']==320
    assert report['metrics'][exp.CONTROL]['exact_prompts']==0
    assert report['passes_predeclared_four_metric_guard']
    assert report['paired_comparison']['b_only_exact']==320
    assert report['candidate_frozen_for_validation_only']==exp.GUARD
    assert not report['g2_unlocked'] and not report['production_promotion']
    assert len(exp._read_jsonl(output/'boundary_guard_validation_per_case.jsonl'))==320
    assert len(exp._read_jsonl(output/'boundary_guard_validation_decisions.jsonl'))==320
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        exp.run(root='root',transfer_report='transfer',fresh_run='fresh',
            null_run='null',validation_run='validation',action_run='action',
            ranking_run='ranking',width_run='width',conflict_run='conflict',
            selective_run='selective',output=output,device='cpu')
