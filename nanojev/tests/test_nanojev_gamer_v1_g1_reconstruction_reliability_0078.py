"""G1-0078 CPU-only diagnostics, lineage and learning safeguards."""
import copy
import hashlib
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g1_sealed_generalization_0077 as sealed
from tools import nanojev_gamer_v1_g1_reconstruction_reliability_0078 as sut
from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as previous
from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as structural
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def _fake_report(dataset):
    params=previous._export_model(previous.Discriminator())
    return {'schema':previous.SCHEMA,'status':'EXPLORATORY_TRAINABLE_NOT_CERTIFIED',
        'selected':{'cutover':'staged_joint_64','legacy':'staged_joint_64'},
        'g1_head_weights_modified':False,'production_promotion':False,
        'g2_unlocked':False,'sealed_test_used':False,'certification_authorized':False,
        'interface_training_performed':True,
        'learned_interface_parameters':{'cutover':{'staged_joint':params},
                                        'legacy':{'staged_joint':params}},
        'lineage':{'checkpoint_sha256':{'cutover':'a'*64,'legacy':'b'*64},
            'dataset_sha256':sealed.sha256(dataset),'coverage_reference_sha256':'1'*64,
            'learned_reference_sha256':'2'*64,'structural_reference_sha256':'3'*64,
            'source_adapter_sha256':'4'*64,
            'model_specific_0071_source':{'cutover':'runnerup_joint_gap0p50_bonus0p50',
                                          'legacy':'actor_prior_0p25'}}}


def _scored_fixture(tmp_path):
    dataset=tmp_path/'original.jsonl'
    corpus.generate(dataset,train=2,dev=2,challenge=2,test=2,seed=1)
    ref=tmp_path/'0076';ref.mkdir()
    (ref/'report.json').write_bytes(sealed._json_bytes(_fake_report(dataset)))
    seal=tmp_path/'sealed';blind=tmp_path/'blind';score=tmp_path/'score'
    commit=sealed.prepare(dataset=dataset,discrimination_reference=ref,
                          output=seal,seed=3308,cases=80)
    prompts=sealed._rows(seal/'prompts.jsonl')
    answers=sealed._rows(seal/'answers.SECRET.jsonl')
    gold={a['case_id']:a['gold'] for a in answers}
    blind.mkdir()
    rec=[]
    for model in sealed.REQUIRED_MODEL_NAMES:
        for i,p in enumerate(prompts):
            chosen=gold[p['case_id']] if not (model=='cutover' and i<2) else []
            rec.append({'case_id':p['case_id'],'model':model,'family':p['family'],
                        'prompt_sha256':p['prompt_sha256'],'control':[],
                        'selected':chosen})
    sealed._write_rows(blind/'predictions.jsonl',rec)
    (blind/'manifest.json').write_bytes(sealed._json_bytes({
        'schema':sealed.SCHEMA,'phase':'BLIND_PREDICTIONS_LOCKED',
        'prompts_sha256':commit['prompts_sha256'],
        'frozen_report_sha256':commit['frozen_report_sha256'],
        'checkpoints_sha256':commit['checkpoint_sha256'],
        'prediction_sha256':sealed.sha256(blind/'predictions.jsonl'),
        'answers_opened_during_prediction':False,
        'gold_used_for_candidate_selection_or_inference':False,
        'training_performed':False,'promotion':False,'certification_authorized':False,
        'selected_by_model':commit['selected']}))
    sealed.score(seal=seal,predictions=blind,output=score)
    return dataset,seal,blind,score


def test_diagnosis_is_exactly_scored_read_only(tmp_path):
    ds,seal,blind,score=_scored_fixture(tmp_path)
    unsealed_before=(seal/'UNSEALED.json').read_bytes()
    report=sut.diagnose(seal=seal,predictions=blind,scored=score,output=tmp_path/'atlas')
    assert report['status']=='READ_ONLY_SEALED_FAILURE_ATLAS'
    assert report['observed_failure_atlas']['cutover']['inexact_cases']==2
    assert report['observed_failure_atlas']['legacy']['inexact_cases']==0
    assert len(sut._rows(tmp_path/'atlas'/'failures.jsonl'))==2
    assert (seal/'UNSEALED.json').read_bytes()==unsealed_before
    with pytest.raises(FileExistsError):
        sut.diagnose(seal=seal,predictions=blind,scored=score,output=tmp_path/'atlas')


def test_lineage_tampering_fails_closed(tmp_path):
    _,seal,blind,score=_scored_fixture(tmp_path)
    (blind/'predictions.jsonl').write_text('tampered\n',encoding='utf8')
    with pytest.raises(ValueError,match='SEALED_LINEAGE_DRIFT'):
        sut.diagnose(seal=seal,predictions=blind,scored=score,output=tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_observed_missed_span_categories_and_no_oracle_inference():
    predictions={(3,6,'action'),(9,12,'quantity'),(13,15,'actor')}
    assert sut._classify_missing((3,6,'actor'),predictions)=='wrong_type_same_bounds'
    assert sut._classify_missing((4,7,'action'),predictions)=='overlapping_same_type_boundary'
    assert sut._classify_missing((10,11,'actor'),predictions)=='overlap_other_type_competition'
    assert sut._classify_missing((23,27,'action'),predictions)=='no_overlapping_selected_candidate'


def test_new_corpus_excludes_original_and_previously_sealed(tmp_path):
    ds,seal,_,_=_scored_fixture(tmp_path)
    cohorts,hashes=sut._generate_new(original_dataset=ds,old_seal=seal,
                                      seed=780078,train=4,selection=2,audit=2)
    assert [len(cohorts[k]) for k in sut.PARTITIONS]==[4,2,2]
    assert len(set(hashes))==8
    excluded={p['prompt_sha256'] for p in sealed._rows(seal/'prompts.jsonl')}
    assert not set(hashes)&excluded
    assert all(r['split']=='test' for batch in cohorts.values() for r in batch)
    assert all('object_spans' in r for batch in cohorts.values() for r in batch)
    with pytest.raises(ValueError,match='INVALID_NEW_COHORT'):
        sut._generate_new(original_dataset=ds,old_seal=seal,seed=780078,
                          train=3,selection=2,audit=2)


def test_candidate_oracle_attribution_separates_missing_levels():
    # Mocks candidate keys only; this diagnostic cannot change training scores.
    from types import SimpleNamespace
    a=(0,3,'actor');b=(3,5,'action');c=(5,7,'quantity')
    r={'case_id':'sealed-00000','selected':[]}
    class Candidate:
        def __init__(self,key):self.char_key=key
    class Lattice:
        def __init__(self,keys):self.added=SimpleNamespace(case=SimpleNamespace(
            options=[Candidate(k) for k in keys]))
    full=Lattice([a,b,c]);full.raw_span_gold_types={a,b,c}
    old_budget=sut._budget
    try:
        sut._budget=lambda d,budget: Lattice([a] if budget==64 else [a,b])
        stat,records=sut._candidate_failure_atlas([r],{'sealed-00000':{
            'gold':[a,b,c]}},[full])
        assert stat['available_in_64_but_not_selected']==1
        assert stat['excluded_from_64_budget']==1
        assert stat['excluded_from_256_budget']==1
        assert len(records)==1
    finally:
        sut._budget=old_budget


def test_training_variants_are_distinct_and_deterministically_initialized():
    def option(a,b,k,score,old):
        return learned.Option(a,b,k,score,learned._feature(
            margin=1.5,p_cond=.8,width=b-a,n=5,first=a,kind=k,
            original=old,base=score,top=True),
            (a,b,SPAN_KINDS[k-1]),old)
    act=SPAN_KINDS.index('actor')+1
    action=SPAN_KINDS.index('action')+1
    org=[option(0,1,act,1.,True),option(1,2,action,.9,True)]
    extra=[option(1,4,action,.3,False),option(2,4,act,.2,False)]
    gold={(0,1,'actor'),(1,4,'action')}
    case=learned.Case('cutover','train','encounter','sample',5,org,set(gold),set(gold),[])
    full=learned.Case('cutover','train','encounter','sample',5,
        sorted(org+extra,key=lambda x:(x.end,x.start,x.kind)),set(gold),set(gold),[])
    base=structural._encode(case);added=structural._encode(full)
    # Preserve original encoded feature vectors on the same candidates.
    fmap={(o.start,o.end,o.kind):v for o,v in zip(org,base.features)}
    for idx,o in enumerate(full.options):
        if (o.start,o.end,o.kind) in fmap:added.features[idx]=fmap[(o.start,o.end,o.kind)]
    weights=((0.,)*coverage.SET_DIM,)
    scores=tuple(structural._scores(added,coverage.CONTROL,weights))
    data=coverage.Expanded(base,added,tuple(coverage._proposal_features(o,5) for o in full.options),
        tuple(not o.anchored for o in full.options),scores,2,0,8,frozenset(gold))
    # The real sealed G1-0077 loader returns a frozen inference model.
    # Earlier tests used a fresh trainable model and missed this crash.
    initial=sealed._load_coefficients({'learned_interface_parameters':{
        'cutover':{'staged_joint':previous._export_model(previous.Discriminator())}}},'cutover')
    assert not any(p.requires_grad for p in initial.parameters())
    before={k:v.detach().clone() for k,v in initial.state_dict().items()}
    fitted,logs=sut._fit_variants([data],initial,hard_steps=2,structured_epochs=1)
    assert not any(p.requires_grad for p in initial.parameters())
    assert all(torch.equal(initial.state_dict()[key],value) for key,value in before.items())
    assert all(model is not initial and all(p.requires_grad for p in model.parameters())
               for model in fitted.values())
    assert all(next(model.parameters()).data_ptr()!=next(initial.parameters()).data_ptr()
               for model in fitted.values())
    assert logs['continued_structured_64']['violations']>0  # Backprop actually ran.
    assert set(fitted)==set(sut.ARMS[1:])
    assert all(model.type_offset.numel()==len(SPAN_KINDS) for model in fitted.values())
    assert all('structured' in logs[name] if name.startswith(('fresh','mined')) else True
               for name in fitted)
    assert set(sut._metrics([data],weights,initial,fitted))==set(sut.ARMS)


def test_predeclared_selection_preserves_control_on_regressions():
    # A model must not win merely by recovering actors while losing actions.
    g={(0,2,'actor'),(2,4,'action')}
    control=lambda p: {'metrics':sut._summarize([(p,g)])}
    scores={sut.CONTROL:control(g)}
    for arm in sut.ARMS[1:]:scores[arm]=control({(0,2,'actor')})
    assert sut._choose(scores)==sut.CONTROL


def test_full_0078_bakeoff_runs_toy_with_disjoint_new_fit(tmp_path,monkeypatch):
    """Orchestration smoke: the 0077 old prompts never reach the fitting capture."""
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    original,seal,blind,score=_scored_fixture(tmp_path)
    atlas=tmp_path/'atlas'
    sut.diagnose(seal=seal,predictions=blind,scored=score,output=atlas)
    fit_report=_fake_report(original)
    ref=tmp_path/'discrimination';ref.mkdir()
    (ref/'report.json').write_bytes(sealed._json_bytes(fit_report))
    old_hashes={p['prompt_sha256'] for p in sealed._rows(seal/'prompts.jsonl')}
    old_hashes.update(hashlib.sha256(r['prompt'].encode('utf8')).hexdigest()
                      for r in cur.load_rows(original))
    def verify(**kwargs):
        return ({name:{'path':tmp_path/name,'manifest':{
                   'model_id':'fake','source_adapter':'fake'}} for name in sealed.REQUIRED_MODEL_NAMES},
                {name:((0.,)*coverage.SET_DIM,) for name in sealed.REQUIRED_MODEL_NAMES},
                {name:'prior' for name in sealed.REQUIRED_MODEL_NAMES})
    for name in sealed.REQUIRED_MODEL_NAMES:
        (tmp_path/name).mkdir()
        (tmp_path/name/cur.HEAD_FILE).write_bytes(('mock-'+name).encode('ascii'))
    real_sha=sut.sha256
    def guarded_sha(path):
        path=Path(path)
        if path.name==cur.HEAD_FILE and path.parent.name in sealed.REQUIRED_MODEL_NAMES:
            return sealed._read_json(seal/'manifest.json')['checkpoint_sha256'][path.parent.name]
        return real_sha(path)
    monkeypatch.setattr(sut,'sha256',guarded_sha)
    monkeypatch.setattr(sut.sealed,'_verify_frozen_lineage',verify)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:
       sealed._read_json(seal/'manifest.json')['source_adapter_sha256'])
    mock_head=SimpleNamespace(eval=lambda:mock_head)
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *a,**kw:(mock_head,None))
    monkeypatch.setattr(backbone,'model_components',lambda *a,**kw:(object(),object()))
    calls=[]
    actor_id=SPAN_KINDS.index('actor')+1
    def capture(rows,heads,qwen,tok,**kwargs):
        calls.append((kwargs['split'],[hashlib.sha256(r['prompt'].encode('utf8')).hexdigest()
                                        for r in rows]))
        output={name:[] for name in heads}
        for model in heads:
            for row in rows:
                opt=learned.Option(0,1,actor_id,0.8,learned._feature(
                    margin=2.,p_cond=.9,width=1,n=3,first=0,kind=actor_id,
                    original=True,base=0.8,top=True),(0,1,'actor'),True)
                gold={(x['start'],x['end'],x['kind']) for x in row['object_spans']}
                ph=hashlib.sha256(row['prompt'].encode('utf8')).hexdigest()
                case=learned.Case(model,kwargs['split'],row['family'],ph,3,
                    [opt],set(),set(gold),[])
                before=structural._encode(case)
                after=structural._encode(copy.deepcopy(case))
                output[model].append(coverage.Expanded(before,after,
                    (coverage._proposal_features(opt,3),),(False,),
                    (0.8,),0,0,3,frozenset({(0,1,'actor')})))
        return output
    monkeypatch.setattr(sut.coverage,'_capture',capture)
    def fake_fit(train,initial,**kwargs):
        return {arm:copy.deepcopy(initial) for arm in sut.ARMS[1:]},{'toy':True}
    monkeypatch.setattr(sut,'_fit_variants',fake_fit)
    result=sut.bakeoff(diagnostic_reference=atlas,seal=seal,
        predictions=blind,scored=score,dataset=original,
        legacy=tmp_path/'legacy',cutover=tmp_path/'cutover',
        structural_reference=tmp_path/'structure',learned_reference=tmp_path/'learned',
        coverage_reference=tmp_path/'coverage',discrimination_reference=ref,
        output=tmp_path/'bakeoff',device='cpu',seed=78382,train=4,selection=2,audit=2,
        hard_steps=2,structured_epochs=1)
    assert result['status']=='RESEARCH_COMPARISON_NOT_CERTIFIED'
    assert result['sealed_0077_failure_cases_inspected_only_after_scoring']==2
    assert set(result['selected'])=={'cutover','legacy'}
    assert [x[0] for x in calls]==['sealed_diagnosis_read_only',
                                  'research_train','research_selection','research_audit']
    assert all(not set(hashes)&old_hashes for phase,hashes in calls
               if phase.startswith('research_'))
    assert result['sealed_0077_cases_used_in_fitting_or_selection'] is False
    assert not result['g1_certified'] and not result['g2_unlocked']
    assert len(sut._rows(tmp_path/'bakeoff'/'sealed_failure_candidates.jsonl'))==2
