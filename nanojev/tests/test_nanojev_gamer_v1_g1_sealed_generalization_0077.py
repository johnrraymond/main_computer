"""G1-0077 sealed cohort: no-fit, immutable, prompt-only, gold commitment."""
import copy
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g1_sealed_generalization_0077 as sut


def _fake_report():
    from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as prior
    params=prior._export_model(prior.Discriminator())
    return {'schema':prior.SCHEMA,'status':'EXPLORATORY_TRAINABLE_NOT_CERTIFIED',
        'selected':{'cutover':'staged_joint_64','legacy':'staged_joint_64'},
        'g1_head_weights_modified':False,'production_promotion':False,
        'g2_unlocked':False,'sealed_test_used':False,'certification_authorized':False,
        'interface_training_performed':True,
        'learned_interface_parameters':{'cutover':{'staged_joint':params},
                                         'legacy':{'staged_joint':params}},
        'lineage':{'checkpoint_sha256':{'cutover':'a'*64,'legacy':'b'*64},
          'dataset_sha256':'0'*64,'coverage_reference_sha256':'1'*64,
          'learned_reference_sha256':'2'*64,'structural_reference_sha256':'3'*64,
          'source_adapter_sha256':'4'*64,
          'model_specific_0071_source':{'cutover':'runnerup_joint_gap0p50_bonus0p50',
                                       'legacy':'actor_prior_0p25'}}}


def _reference(tmp_path):
    ds=tmp_path/'old.jsonl'
    corpus.generate(ds,train=2,dev=2,challenge=2,test=2,seed=1)
    report=_fake_report()
    report['lineage']['dataset_sha256']=sut.sha256(ds)
    folder=tmp_path/'reference';folder.mkdir()
    (folder/'report.json').write_bytes(sut._json_bytes(report))
    return ds,folder


def test_frozen_coefficients_validation():
    report=_fake_report()
    lineage=sut._validate_report(report)
    assert lineage['model_specific_0071_source']['cutover']=='runnerup_joint_gap0p50_bonus0p50'
    assert sut._load_coefficients(report,'cutover').type_offset.numel()==7
    for name in ('cutover','legacy'):
        tampered=copy.deepcopy(report)
        tampered['learned_interface_parameters'][name]['staged_joint']['parameters']['new_offset'][0]+=1
        with pytest.raises(ValueError,match='COEFFICIENT_SHA_DRIFT'):
            sut._validate_report(tampered)


def test_precommit_disjoint_and_one_time_score(tmp_path):
    ds,ref=_reference(tmp_path)
    seal=tmp_path/'seal';pred=tmp_path/'blind';scored=tmp_path/'scored'
    a=sut.prepare(dataset=ds,discrimination_reference=ref,output=seal,seed=95037,cases=80)
    assert a['cases']==80 and a['old_prompt_overlap']==0
    prompts=sut._rows(seal/'prompts.jsonl')
    assert all(set(v)=={'case_id','family','prompt','prompt_sha256'} for v in prompts)
    with pytest.raises(FileExistsError):
        sut.prepare(dataset=ds,discrimination_reference=ref,output=seal,cases=80)
    answers=sut._rows(seal/'answers.SECRET.jsonl')
    gold={a['case_id']:a['gold'] for a in answers}
    pred.mkdir()
    predictions=[{'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],
                  'family':r['family'],'model':name,
                  'control':[], 'selected':gold[r['case_id']]}
                 for name in sut.REQUIRED_MODEL_NAMES for r in prompts]
    sut._write_rows(pred/'predictions.jsonl',predictions)
    p={'schema':sut.SCHEMA,'phase':'BLIND_PREDICTIONS_LOCKED',
       'prompts_sha256':a['prompts_sha256'],
       'frozen_report_sha256':a['frozen_report_sha256'],
       'checkpoints_sha256':a['checkpoint_sha256'],
       'prediction_sha256':sut.sha256(pred/'predictions.jsonl'),
       'answers_opened_during_prediction':False,'training_performed':False,
       'gold_used_for_candidate_selection_or_inference':False,'promotion':False,
       'certification_authorized':False,'selected_by_model':a['selected']}
    (pred/'manifest.json').write_bytes(sut._json_bytes(p))
    result=sut.score(seal=seal,predictions=pred,output=scored)
    assert all(v['selected']['exact_prompts']==80 for v in result['metrics'].values())
    assert not result['stage_g1_certified'] and not result['g2_unlocked']
    assert (seal/'UNSEALED.json').exists()
    with pytest.raises(ValueError,match='ALREADY_UNSEALED'):
        sut.score(seal=seal,predictions=pred,output=tmp_path/'scored_again')


def test_answer_tamper_refuses_before_unseal(tmp_path):
    ds,ref=_reference(tmp_path)
    seal=tmp_path/'seal';pred=tmp_path/'blind'
    a=sut.prepare(dataset=ds,discrimination_reference=ref,output=seal,seed=30011,cases=80)
    prompts=sut._rows(seal/'prompts.jsonl')
    pred.mkdir()
    sut._write_rows(pred/'predictions.jsonl',[
        {'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],
         'family':r['family'],'model':name,'control':[],'selected':[]}
        for name in sut.REQUIRED_MODEL_NAMES for r in prompts])
    (pred/'manifest.json').write_bytes(sut._json_bytes({
         'schema':sut.SCHEMA,'phase':'BLIND_PREDICTIONS_LOCKED',
         'prompts_sha256':a['prompts_sha256'],
         'frozen_report_sha256':a['frozen_report_sha256'],
         'checkpoints_sha256':a['checkpoint_sha256'],
         'prediction_sha256':sut.sha256(pred/'predictions.jsonl'),
         'answers_opened_during_prediction':False,'training_performed':False,
       'gold_used_for_candidate_selection_or_inference':False,'promotion':False,
       'certification_authorized':False,'selected_by_model':a['selected']}))
    with (seal/'answers.SECRET.jsonl').open('ab') as f:f.write(b'\n')
    with pytest.raises(ValueError,match='ANSWER_COMMITMENT_BROKEN'):
        sut.score(seal=seal,predictions=pred,output=tmp_path/'scored')
    assert not (seal/'UNSEALED.json').exists()


def test_duplicate_or_invalid_predictions_fail_closed(tmp_path):
    ds,ref=_reference(tmp_path)
    seal=tmp_path/'seal';pred=tmp_path/'blind'
    a=sut.prepare(dataset=ds,discrimination_reference=ref,output=seal,seed=11503,cases=80)
    p0=sut._rows(seal/'prompts.jsonl')[0]
    pred.mkdir()
    sut._write_rows(pred/'predictions.jsonl',[
        {'case_id':p0['case_id'],'prompt_sha256':p0['prompt_sha256'],
         'family':p0['family'],'model':'cutover','control':[],'selected':[]}])
    (pred/'manifest.json').write_bytes(sut._json_bytes({
        'schema':sut.SCHEMA,'phase':'BLIND_PREDICTIONS_LOCKED',
        'prompts_sha256':a['prompts_sha256'],
        'frozen_report_sha256':a['frozen_report_sha256'],
        'checkpoints_sha256':a['checkpoint_sha256'],
        'prediction_sha256':sut.sha256(pred/'predictions.jsonl'),
        'answers_opened_during_prediction':False,'training_performed':False,
       'gold_used_for_candidate_selection_or_inference':False,'promotion':False,
       'certification_authorized':False,'selected_by_model':a['selected']}))
    with pytest.raises(ValueError,match='PREDICTIONS_INCOMPLETE'):
        sut.score(seal=seal,predictions=pred,output=tmp_path/'scored')


def test_precommitted_code_and_canonical_kind_contract():
    assert len(sut.SPAN_KINDS)==7
    assert sut._wilson(80,80)['lower']<1.0
    assert sut._wilson(0,80)['upper']>0.0
    valid={'schema':sut.SCHEMA,'cases':80}
    with pytest.raises(ValueError,match='CASE_COUNT'):
        sut._validate_prompt_rows([],valid)


def test_predict_reads_only_prompts_and_preserves_frozen_predictions(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    ds,ref=_reference(tmp_path)
    head_dirs={}
    report=sut._read_json(ref/'report.json')
    for model in sut.REQUIRED_MODEL_NAMES:
        root=tmp_path/model;root.mkdir()
        (root/cur.HEAD_FILE).write_bytes(('frozen-'+model).encode('utf8'))
        report['lineage']['checkpoint_sha256'][model]=sut.sha256(root/cur.HEAD_FILE)
        head_dirs[model]=root
    (ref/'report.json').write_bytes(sut._json_bytes(report))
    seal=tmp_path/'seal'
    sut.prepare(dataset=ds,discrimination_reference=ref,output=seal,seed=87231,cases=80)
    def fake_lineage(**kwargs):
        assert kwargs['record']['selected']=={'cutover':sut.SELECTED,'legacy':sut.SELECTED}
        return ({name:{'path':str(head_dirs[name]),
                        'manifest':{'model_id':'fake','source_adapter':'fake'}}
                  for name in sut.REQUIRED_MODEL_NAMES},
                {'cutover':(), 'legacy':()}, {'cutover':'any','legacy':'any'})
    monkeypatch.setattr(sut,'_verify_frozen_lineage',fake_lineage)
    head=SimpleNamespace(eval=lambda: head)
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *a,**kw:(head,None))
    monkeypatch.setattr(backbone,'model_components',lambda *a,**kw:(object(),object()))
    def fake_capture(rows,heads,model,tok,**kwargs):
        assert all(set(r)=={'prompt','family','object_spans'} and not r['object_spans'] for r in rows)
        return {name:[object() for _ in rows] for name in heads}
    monkeypatch.setattr(sut.coverage,'_capture',fake_capture)
    monkeypatch.setattr(sut.previous,'_restrict',lambda data,*a,**kw:data)
    monkeypatch.setattr(sut.previous,'_predict',lambda data,arm,*args,**kwargs:
                        {(0,1,'actor')} if arm==sut.SELECTED else set())
    real_open=Path.open
    def guarded_open(path,*args,**kwargs):
        if Path(path).name=='answers.SECRET.jsonl':
            raise AssertionError('PREDICT_ATTEMPTED_TO_READ_ORACLE_ANSWERS')
        return real_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded_open)
    out=tmp_path/'predicted'
    result=sut.predict(seal=seal,dataset=ds,legacy=head_dirs['legacy'],
         cutover=head_dirs['cutover'],structural_reference=tmp_path/'unused1',
         learned_reference=tmp_path/'unused2',coverage_reference=tmp_path/'unused3',
         discrimination_reference=ref,output=out,device='cpu')
    assert result['phase']=='BLIND_PREDICTIONS_LOCKED'
    assert not result['answers_opened_during_prediction']
    assert len(sut._rows(out/'predictions.jsonl'))==160
    assert all(r['selected']==[[0,1,'actor']] for r in sut._rows(out/'predictions.jsonl'))
