"""CPU-only quantity guarding, structured gradients and prospective seal tests."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 as sut
from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as previous
from tools import nanojev_gamer_v1_g1_sealed_generalization_0077 as sealed
from tools import nanojev_gamer_v1_g1_reconstruction_reliability_0078 as reliability
from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as structural
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def _toy():
    actor=SPAN_KINDS.index('actor')+1
    action=SPAN_KINDS.index('action')+1
    qty=SPAN_KINDS.index('quantity')+1
    def option(a,b,k,score,old):
        return learned.Option(a,b,k,float(score),learned._feature(
            margin=2.,p_cond=.86,width=b-a,n=7,first=a,kind=k,
            original=old,base=score,top=True),(a,b,SPAN_KINDS[k-1]),old)
    originals=[option(0,1,actor,1.5,True),option(1,2,action,.9,True),
               option(2,4,action,.9,True),option(4,5,qty,2.,True)]
    extra=[option(1,4,action,.4,False),option(4,5,actor,2.8,False),
           option(5,6,qty,1.8,False),option(1,4,actor,1.1,False)]
    gold={(0,1,'actor'),(1,4,'action'),(4,5,'quantity')}
    old=learned.Case('cutover','train','encounter','t',7,originals,set(gold),set(gold),[])
    combined=sorted(originals+extra,key=lambda x:(x.end,x.start,x.kind))
    new=learned.Case('cutover','train','encounter','t',7,combined,set(gold),set(gold),[])
    old_data=structural._encode(old);new_data=structural._encode(new)
    old_features={(o.start,o.end,o.kind):f for o,f in zip(originals,old_data.features)}
    for i,o in enumerate(new.options):
        if (o.start,o.end,o.kind) in old_features:
            new_data.features[i]=old_features[(o.start,o.end,o.kind)]
    weights=((0.,)*coverage.SET_DIM,)
    scores=tuple(structural._scores(new_data,coverage.CONTROL,weights))
    expanded=coverage.Expanded(old_data,new_data,
        tuple(coverage._proposal_features(o,7) for o in new.options),
        tuple(not o.anchored for o in new.options),scores,len(extra),0,12,
        frozenset(gold))
    return expanded,weights


def test_quantity_guard_is_exact_at_inference_and_gold_blind():
    data,weights=_toy()
    initial=previous.Discriminator().eval().requires_grad_(False)
    rival=copy.deepcopy(initial)
    with torch.no_grad():rival.type_offset[SPAN_KINDS.index('quantity')]+=6.
    control=sut._predict(data,sut.CONTROL,weights,initial,rival)
    guarded=sut._predict(data,sut.PINNED_CONTINUED,weights,initial,rival)
    assert sut._quantity(guarded)==sut._quantity(control)
    control_first=control.copy();guarded_first=guarded.copy()
    data.added.case.gold={(0,1,'other'),(20,40,'action')}
    assert sut._predict(data,sut.CONTROL,weights,initial,rival)==control_first
    assert sut._predict(data,sut.PINNED_CONTINUED,weights,initial,rival)==guarded_first
    assert all(not p.requires_grad for p in initial.parameters())


def test_quantity_pin_is_hard_even_when_new_quantity_has_huge_score():
    data,weights=_toy()
    model=previous.Discriminator().eval()
    prior=sut._baseline(data,weights,model)
    pinned=sut._quantity(prior)
    with torch.no_grad():
        model.type_offset[SPAN_KINDS.index('quantity')]+=7.
        fixed=sut._guarded_scores(data,model,pinned)
    for i,o in enumerate(data.added.case.options):
        if o.kind==SPAN_KINDS.index('quantity')+1 and o.char_key not in pinned:
            assert fixed[i].item()==-1.e8
    assert sut._quantity(sut._predict(data,sut.PINNED_CONTINUED,
                  weights,previous.Discriminator(),model))==pinned


def test_three_objectives_have_real_gradients_and_frozen_source():
    data,weights=_toy()
    seed=previous.Discriminator().eval().requires_grad_(False)
    before={k:v.detach().clone() for k,v in seed.state_dict().items()}
    outputs=[]; logs=[]
    for amount in (1.,2.,4.):
        fit,log=sut._fit_weighted([data],weights,seed,quantity_margin=amount,
                                   epochs=2,lr=.012)
        outputs.append(fit);logs.append(log)
        assert log['quantity_margin']==amount
        assert log['epochs']==2 and log['updates']>0
        assert sut._quantity(sut._predict(data,sut.PINNED_CONTINUED,
            weights,seed,fit))==sut._quantity(sut._baseline(data,weights,seed))
        assert not any(p.requires_grad for p in fit.parameters())
    assert all(torch.equal(seed.state_dict()[k],v) for k,v in before.items())
    assert len({id(x) for x in outputs})==3
    # The losses differ even if model selection retains the unchanged seed.
    assert [x['quantity_anchor_coefficient'] for x in logs]==[0.,.02,.06]
    assert logs[0]['quantity_anchor_samples']==[]
    assert len(logs[1]['quantity_anchor_samples'])>0
    with pytest.raises(ValueError,match='TRAIN_CONFIG'):
        sut._fit_weighted([data],weights,seed,quantity_margin=3.)


def test_selection_disallows_quantity_or_action_regressions():
    true={(0,2,'actor'),(2,4,'action'),(4,5,'quantity')}
    good=sut._summarize([(true,true)])
    lose_qty=sut._summarize([({(0,2,'actor'),(2,4,'action')},true)])
    lose_action=sut._summarize([({(0,2,'actor'),(4,5,'quantity')},true)])
    scores={name:lose_qty for name in sut.ARMS}
    scores[sut.CONTROL]=good
    assert sut._choose(scores)==sut.CONTROL
    scores[sut.PINNED_ONEX]=lose_action
    assert sut._choose(scores)==sut.CONTROL
    assert sut.RAW_CONTINUED not in sut.SELECTABLE


def test_loaded_fitted_models_have_exact_report_hash_and_are_frozen():
    bundle=previous._export_model(previous.Discriminator())
    net=sut._load_model(bundle)
    assert net.type_offset.numel()==len(SPAN_KINDS)
    assert not any(p.requires_grad for p in net.parameters())
    broken=copy.deepcopy(bundle);broken['parameters']['proj'][0]+=1.
    with pytest.raises(ValueError,match='COEFFICIENT_HASH_DRIFT'):
        sut._load_model(broken)


def _fake_prepared(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    ds=tmp_path/'original.jsonl'
    corpus.generate(ds,train=2,dev=2,challenge=2,test=2,seed=4)
    oldseal=tmp_path/'prior_seal';oldseal.mkdir()
    oldcases=[]
    for phase,n in [('train',96),('selection',32),('audit',64)]:
        for model in sut.MODELS:
            for i in range(n):
                oldcases.append({'phase':phase,'model':model,'prompt_sha256':
                    hashlib.sha256(f'{phase}{i}'.encode()).hexdigest()})
    hash_concat=''.join(x['prompt_sha256'] for x in oldcases if x['model']=='cutover')
    report={'schema':reliability.SCHEMA,'status':'RESEARCH_COMPARISON_NOT_CERTIFIED',
        'selected':{'cutover':'fresh_staged_64','legacy':reliability.CONTROL},
        'new_cohorts':{'train':96,'selection':32,'audit':64},
        'new_prompt_digest':hashlib.sha256(hash_concat.encode()).hexdigest(),
        'new_prompt_overlap':0,'g1_certified':False,'g2_unlocked':False,
        'head_weights_modified':False,'production_promotion':False,
        'original_0077_test_reused_for_selection':False,
        'sealed_0077_cases_used_in_fitting_or_selection':False,
        'original_dataset_sha256':sut.sha256(ds),
        'generator_sha256':sut.sha256(corpus.__file__),
        'frozen_discrimination_report_sha256':'a'*64,
        'learned_interface_parameters':{}}
    ref=tmp_path/'prior_0078';ref.mkdir()
    sut._write_json(ref/'report.json',report)
    sut._write_rows(ref/'cases.jsonl',oldcases)
    commit={'generator_sha256':sut.sha256(corpus.__file__),
        'frozen_report_sha256':'a'*64}
    manifest=oldseal/'manifest.json';manifest.write_text('{}',encoding='utf8')
    monkeypatch.setattr(sut.sealed,'_load_seal',lambda path:(oldseal,commit,[
        {'prompt_sha256':hashlib.sha256(b'old-sealed').hexdigest()}]))
    return ds,oldseal,ref


def test_preparation_commits_and_disjoins_audit_before_training(tmp_path,monkeypatch):
    ds,prior_seal,ref=_fake_prepared(tmp_path,monkeypatch)
    dest=tmp_path/'new_seal'
    out=sut.prepare(dataset=ds,seal_0077=prior_seal,reliability_reference=ref,
                    output=dest,seed=790079,train=16,selection=16,audit=64)
    assert out['cohorts']=={'train':16,'selection':16,'audit':64}
    assert (dest/'audit.answers.SECRET.jsonl').exists()
    assert out['committed_audit_answers_sha256']==sut.sha256(dest/'audit.answers.SECRET.jsonl')
    assert all('object_spans' not in p for p in sut._rows(dest/'audit.prompts.jsonl'))
    _,train,select,blind=sut._load_prepared(dest,ds,prior_seal,ref)
    assert len(train)==len(select)==16 and len(blind)==64
    with pytest.raises(FileExistsError):
        sut.prepare(dataset=ds,seal_0077=prior_seal,reliability_reference=ref,output=dest)
    (dest/'audit.prompts.jsonl').write_text('tamper\n',encoding='utf8')
    with pytest.raises(ValueError,match='PREPARED_FILE_CHANGED'):
        sut._load_prepared(dest,ds,prior_seal,ref)


def test_score_once_and_quantity_guard_with_committed_answers(tmp_path):
    seal=tmp_path/'seal';out=tmp_path/'blind';score=tmp_path/'score'
    seal.mkdir();out.mkdir()
    prompts=[{'case_id':'prospective-00000','family':'encounter',
              'prompt':'hello', 'prompt_sha256':sut._hash('hello')}]
    gold=[{'case_id':'prospective-00000','family':'encounter',
           'prompt_sha256':sut._hash('hello'),'gold':[[0,2,'actor'],[2,4,'quantity']]}]
    sut._write_rows(seal/'audit.prompts.jsonl',prompts)
    sut._write_rows(seal/'audit.answers.SECRET.jsonl',gold)
    manifest={'schema':sut.SCHEMA,
        'committed_audit_answers_sha256':sut.sha256(seal/'audit.answers.SECRET.jsonl'),
        'audit_prompts_sha256':sut.sha256(seal/'audit.prompts.jsonl')}
    sut._write_json(seal/'manifest.json',manifest)
    predicted=[{'case_id':'prospective-00000','family':'encounter','model':name,
                'prompt_sha256':sut._hash('hello'),
                'control':[[0,2,'actor'],[2,4,'quantity']],
                'selected':[[0,2,'actor'],[2,4,'quantity']]} for name in sut.MODELS]
    sut._write_rows(out/'audit.predictions.jsonl',predicted)
    sut._write_json(out/'report.json',{'schema':sut.SCHEMA,
        'status':'AUDIT_BLIND_PREDICTIONS_LOCKED_NOT_SCORED',
        'seal_manifest_sha256':sut.sha256(seal/'manifest.json'),
        'audit_answers_opened_during_fit':False,
        'audit_gold_used_for_selection_or_inference':False,
        'committed_audit_answers_sha256':manifest['committed_audit_answers_sha256'],
        'audit_prompts_sha256':manifest['audit_prompts_sha256'],
        'prediction_sha256':sut.sha256(out/'audit.predictions.jsonl'),
        'selected':{'cutover':sut.CONTROL,'legacy':sut.CONTROL}})
    result=sut.score(seal=seal,predictions=out,output=score)
    assert result['metrics']['cutover']['selected']['exact_prompts']==1
    assert (seal/'UNSEALED.json').exists()
    with pytest.raises(FileExistsError):sut.score(seal=seal,predictions=out,output=tmp_path/'retry')


def test_score_rejects_tampered_quantity_prediction(tmp_path):
    seal=tmp_path/'seal';out=tmp_path/'out'
    seal.mkdir();out.mkdir()
    prompt={'case_id':'p','family':'encounter','prompt':'abcde','prompt_sha256':sut._hash('abcde')}
    gold={'case_id':'p','family':'encounter','prompt_sha256':prompt['prompt_sha256'],
          'gold':[[0,2,'quantity']]}
    sut._write_rows(seal/'audit.prompts.jsonl',[prompt]);sut._write_rows(seal/'audit.answers.SECRET.jsonl',[gold])
    sut._write_json(seal/'manifest.json',{'schema':sut.SCHEMA,
        'committed_audit_answers_sha256':sut.sha256(seal/'audit.answers.SECRET.jsonl'),
        'audit_prompts_sha256':sut.sha256(seal/'audit.prompts.jsonl')})
    predictions=[{'case_id':'p','family':'encounter','prompt_sha256':prompt['prompt_sha256'],
                  'model':name,'control':[[0,2,'quantity']],'selected':[]}
                 for name in sut.MODELS]
    sut._write_rows(out/'audit.predictions.jsonl',predictions)
    sut._write_json(out/'report.json',{'schema':sut.SCHEMA,
        'status':'AUDIT_BLIND_PREDICTIONS_LOCKED_NOT_SCORED',
        'seal_manifest_sha256':sut.sha256(seal/'manifest.json'),
        'audit_answers_opened_during_fit':False,
        'audit_gold_used_for_selection_or_inference':False,
        'committed_audit_answers_sha256':sut.sha256(seal/'audit.answers.SECRET.jsonl'),
        'audit_prompts_sha256':sut.sha256(seal/'audit.prompts.jsonl'),
        'prediction_sha256':sut.sha256(out/'audit.predictions.jsonl')})
    with pytest.raises(ValueError,match='AUDIT_QUANTITY_PRESERVATION_FAILED'):
        sut.score(seal=seal,predictions=out,output=tmp_path/'score')
    assert not (seal/'UNSEALED.json').exists()


def test_miniature_full_protocol_never_reads_audit_answers_during_training(tmp_path,monkeypatch):
    """CPU-only harness for the exact prepare/fit/predict/score orchestration."""
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    from tools import nanojev_gamer_v1_backbone_first as backbone
    ds,prior_seal,ref=_fake_prepared(tmp_path,monkeypatch)
    discr=tmp_path/'discrimination';discr.mkdir()
    sut._write_json(discr/'report.json',{'mock':'valid-report'})
    fake_heads={}
    checkpoint_hashes={}
    for name in sut.MODELS:
        path=tmp_path/name;path.mkdir()
        (path/cur.HEAD_FILE).write_bytes(('head-'+name).encode())
        checkpoint_hashes[name]=sut.sha256(path/cur.HEAD_FILE)
        fake_heads[name]={'path':path,'manifest':{'model_id':'mock','source_adapter':'mock'}}
    commit={'generator_sha256':sut.sha256(corpus.__file__),
            'frozen_report_sha256':sut.sha256(discr/'report.json'),
            'checkpoint_sha256':checkpoint_hashes}
    monkeypatch.setattr(sut.sealed,'_load_seal',lambda path:(prior_seal,commit,[
        {'prompt_sha256':hashlib.sha256(b'old-sealed').hexdigest()}]))
    prior_report=sut._json(ref/'report.json')
    prior_report['frozen_discrimination_report_sha256']=commit['frozen_report_sha256']
    sut._write_json(ref/'report.json',prior_report)
    seal=tmp_path/'new_seal'
    sut.prepare(dataset=ds,seal_0077=prior_seal,reliability_reference=ref,
                output=seal,seed=790079,train=16,selection=16,audit=64)
    weights={n:((0.,)*coverage.SET_DIM,) for n in sut.MODELS}
    monkeypatch.setattr(sut.sealed,'_verify_frozen_lineage',lambda **kwargs:(
        fake_heads,weights,{n:'mock' for n in sut.MODELS}))
    mock_head=SimpleNamespace();mock_head.eval=lambda:mock_head
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *args,**kwargs:(mock_head,None))
    monkeypatch.setattr(backbone,'model_components',lambda *args,**kwargs:(object(),object()))
    monkeypatch.setattr(sut,'_models',lambda prior,initial,name:(
        previous.Discriminator().eval().requires_grad_(False),
        previous.Discriminator().eval().requires_grad_(False)))
    phases=[]
    def capture(rows,heads,qwen,tok,**kwargs):
        phases.append((kwargs['split'],len(rows)))
        assembled={n:[] for n in heads}
        for name in heads:
            for row in rows:
                item,w=_toy()
                item.added.case.model=name
                item.added.case.prompt_hash=hashlib.sha256(row['prompt'].encode()).hexdigest()
                item.added.case.family=row['family']
                item.added.case.gold={(s['start'],s['end'],s['kind'])
                                       for s in row['object_spans']}
                assembled[name].append(item)
        return assembled
    monkeypatch.setattr(sut.coverage,'_capture',capture)
    monkeypatch.setattr(sut,'_fit_weighted',lambda cases,weights,initial,**kw:(
        copy.deepcopy(initial),{'quantity_margin':kw['quantity_margin'],
                                'mock':'tested separately on real backprop'}))
    real_reader=Path.read_text
    def no_answer_reads(path,*args,**kwargs):
        if Path(path).name=='audit.answers.SECRET.jsonl':
            raise AssertionError('AUDIT_ANSWERS_OPENED_DURING_TRAINING')
        return real_reader(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',no_answer_reads)
    blind=tmp_path/'blind'
    report=sut.fit_predict(seal=seal,seal_0077=prior_seal,
        reliability_reference=ref,dataset=ds,
        legacy=tmp_path/'legacy',cutover=tmp_path/'cutover',
        structural_reference=tmp_path/'structural',
        learned_reference=tmp_path/'learned',
        coverage_reference=tmp_path/'coverage',
        discrimination_reference=discr,output=blind,device='cpu',epochs=1)
    assert report['audit_answers_opened_during_fit'] is False
    assert len(sut._rows(blind/'audit.predictions.jsonl'))==128
    assert phases==[('prospective_train',16),('prospective_selection',16),
                    ('prospective_audit',64)]
    assert not (seal/'UNSEALED.json').exists()
    monkeypatch.setattr(Path,'read_text',real_reader)
    result=sut.score(seal=seal,predictions=blind,output=tmp_path/'score')
    assert result['audit_cases']==64
    assert result['audit_used_for_fit_or_model_selection'] is False
    assert (seal/'UNSEALED.json').exists()


def test_incorrect_baseline_quantity_does_not_break_training_oracle():
    data,weights=_toy()
    seed=previous.Discriminator().eval().requires_grad_(False)
    protected=sut._quantity(sut._baseline(data,weights,seed))
    assert protected
    # This gold action conflicts with a locked predicted quantity. The
    # optimizer must learn the best reachable compatible set, not abort.
    data.added.case.gold={(0,1,'actor'),(3,5,'action'),(5,6,'quantity')}
    output,log=sut._fit_weighted([data],weights,seed,
                                  quantity_margin=2.,epochs=1)
    assert log['unreachable_under_quantity_pin']>=1
    assert sut._quantity(sut._predict(data,sut.PINNED_CONTINUED,
                           weights,seed,output))==protected
