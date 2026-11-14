"""Frozen source-literal signs versus mathematically certified output signs."""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_upstream_path as up
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_staircase as staircase
from tools import nanojev_gamer_v1_radius_activations as radius


def test_trained_versus_heldout_group_directions():
    a={'game'+str(i):[np.asarray([i+1.,0.]) if i%3 else np.asarray([2.,0.])]
       for i in range(7)}
    b={'other'+str(i):[np.asarray([1.+i,0.])] for i in range(5)}
    m=up._contrast_score(a,b,permutations=59,seed=2)
    assert m['status']=='COMPLETE'
    assert m['train_groups']==7 and m['test_groups']==5
    assert m['group_equal_weight_sign_ordering']==1.0
    assert 0<m['null_p_one_sided']<=1
    m=up._contrast_score(a,{'a':[np.zeros(2)]},seed=2)
    assert m['status']=='INSUFFICIENT_GROUPED_PAIRS'


def test_input_sign_pairs_come_from_real_signed_integer_values():
    r=np.arange(-8,9)
    x=np.zeros((4,3,len(r),3))
    for gi in range(4):
        for form in range(3):
            for ix,val in enumerate(r):x[gi,form,ix,0]=val*4
    a,b=up._pair_dirs(x,r,['ga','gb','gc','gd'],'words',(2,3,4),(5,6,7,8),[0,1],[2,3])
    assert set(a)=={'ga','gb'} and set(b)=={'gc','gd'}
    assert all(np.allclose(v[0],[16.,0.,0.]) for v in a.values())
    assert all(np.allclose(v[0],[40.,0.,0.]) for v in b.values())
    assert up._contrast_score(a,b,permutations=19)['group_equal_weight_sign_ordering']==1.0


def _fake_exact_deck():
    return oracle.make_deck(2,2,2,include_ood=False)


def test_exact_oracle_target_signs_and_frozen_head():
    deck=_fake_exact_deck()
    rows=[x.public() for x in deck]
    n=len(rows)
    ar=np.zeros((n,102400),dtype=np.float16)
    pred=np.zeros(n,dtype=np.float32)
    for i,r in enumerate(rows):
        sign=np.sign(float(oracle.Fraction(r['target'])))
        # Predicted mathematical answer not literal source operand.
        ar[i,0]=float(sign)*3
        pred[i]=float(sign)*0.2
    meta={'schema':oracle.SCHEMA,'dataset':rows,'dataset_sha256':oracle.deck_hash(deck),
          'deck':oracle.validate_deck(deck),'ablation':['lora']}
    arrays={'lora__final_flat':ar,'production_head_predictions':pred}
    result=up.math_oracle_path(meta,arrays,{'localized':[0,1,2,3]},permutations=19)
    report=result['computed_answer_sign_contrasts']['lora']['localized']['signed_offset']
    assert report['group_equal_weight_sign_ordering']==1.0
    head=result['frozen_production_c0_test_triples']['signed_offset']
    assert head['triples']==4
    assert head['whole_triple_correct']==1.0
    pred[0]=8
    arrays['production_head_predictions']=pred
    with pytest.raises(ValueError,match='ORACLE_DATASET_CHANGED'):
        broken=dict(meta,dataset_sha256='bad')
        up.math_oracle_path(broken,arrays,{'localized':[0,1,2,3]})


def test_oracle_label_not_literal_sign():
    d=_fake_exact_deck()
    # The ship and center values in this deck are BOTH positive, even for
    # the NEGATIVE correct answer. It is not a lexical-negative test.
    negatives=[r for r in d if r.target<0]
    assert negatives and all(r.offset<0 and 'minus ' not in r.prompt.lower() for r in negatives)
    assert all(oracle.validate_deck(d)['classes'][key]>0 for key in ('negative','zero','positive'))


def test_head_metrics_refuse_incomplete_group():
    d=_fake_exact_deck();rows=[x.public() for x in d]
    meta={'dataset':[r for r in rows if r['id']!='test.offset.0000.zero.source_native']}
    arr={'production_head_predictions':np.zeros(len(meta['dataset']))}
    with pytest.raises(ValueError,match='MISSING_HEAD_ORACLE_TRIPLE'):
        up._head_triple_metrics(meta,arr)


def test_cli_read_only_flags():
    args=staircase.parser().parse_args(['head-100-upstream-path','--run','a','--device','cpu'])
    assert args.upstream_train_groups==24 and not args.upstream_input_only
    with pytest.raises(ValueError,match='READ_ONLY_NO_STEPS'):
        staircase.main(['head-100-upstream-path','--run','a','--steps','3'])
    with pytest.raises(ValueError,match='FLAGS_ONLY'):
        staircase.main(['head-100-sign-only','--run','a','--upstream-train-groups','5'])


def test_input_capture_output_is_read_only(tmp_path):
    capture=tmp_path/'capture';capture.mkdir()
    games=[f'g{i}' for i in range(8)]
    values=np.arange(-8,9)
    # Deliberately 102400-wide, as real Qwen has, but sparse tiny content.
    x=np.zeros((8,3,len(values),102400),dtype=np.float32)
    for i,game in enumerate(games):
        for f in range(3):
            for j,value in enumerate(values):
                x[i,f,j,65*1024]=value
                x[i,f,j,64*1024]=-value
                x[i,f,j,8*1024]=value*.5
    act=capture/'activations.npz'
    np.savez_compressed(act,values=values,token_counts=np.ones((8,3,17),dtype=np.int32),
                        qwen_final_100=x)
    (capture/'interventions.jsonl').write_text('immutable')
    (capture/'final_report.json').write_text(json.dumps({'contexts':[{'game_id':g} for g in games]}))
    prior={p.name:radius._canonical_hash(p) for p in capture.iterdir()}
    result=up.input_sign_grounding(capture,{'localized':[65,66,71,72],
          'neighbor_control':[64,67,70,73],'random_control':[8,49,74,77],'full100':None},
          permutations=9)
    assert result['capture_games']==8
    assert result['arms']['localized']['signed']['game_equal_weight_accuracy']==1.
    assert result['arms']['neighbor_control']['signed']['game_equal_weight_accuracy']==1.
    # Reverse signed direction in both train/test still encodes the source sign;
    # this experiment is explicitly NOT a feature-uniqueness claim.
    assert prior=={p.name:radius._canonical_hash(p) for p in capture.iterdir()}


def test_invalid_destination_refuses_source_overlap(tmp_path):
    root=tmp_path/'source';root.mkdir()
    with pytest.raises(ValueError,match='OVERLAPS'):
        up._destination(root,root/'oops')
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        up._destination(root,root)


def test_zero_input_midpoint_is_descriptive_not_answer_certification():
    values=np.arange(-8,9)
    x=np.zeros((2,3,17,4));n=np.ones((2,3,17),dtype=int)
    for game in range(2):
        for form in range(3):
            for j,value in enumerate(values):x[game,form,j,0]=value*3
    original=up._zero_midpoint_geometry(x,values,n,'signed')
    assert original['triples']==8 and original['token_count_matched_triples']==8
    assert original['median_zero_midpoint_offset_over_signed_span']==0
    x[:,:,8,0]=5.0
    curvature=up._zero_midpoint_geometry(x,values,n,'signed')
    assert curvature['median_zero_midpoint_offset_over_signed_span']>0
    n[:,1,8]=2
    mismatched=up._zero_midpoint_geometry(x,values,n,'signed')
    assert mismatched['token_count_matched_triples']==0
    assert mismatched['token_count_matched_median'] is None


def test_token_mismatch_is_not_silent_grounding():
    r=np.arange(-8,9)
    x=np.zeros((4,3,17,3));n=np.full((4,3,17),2,dtype=int)
    for i in range(4):
        for f in range(3):
            for j,val in enumerate(r):x[i,f,j,0]=val
    n[:,2,:r.tolist().index(0)]=3  # every negative word has different token count
    train,test=up._pair_dirs(x,r,['a','b','c','d'],'words',(2,3,4),(5,6,7,8),[0,1],[2,3],
             counts=n,match_token_counts=True)
    assert len(train)==2 and len(test)==0
    untrain,untest=up._pair_dirs(x,r,['a','b','c','d'],'words',(2,3,4),(5,6,7,8),[0,1],[2,3])
    assert len(untest)==2


def test_full_oracle_pipeline_with_mocked_capture_preserves_sources(tmp_path,monkeypatch):
    sign_root=tmp_path/'sign-only';sign_root.mkdir()
    source=tmp_path/'source';source.mkdir()
    expanded=tmp_path/'expanded';expanded.mkdir()
    capture=tmp_path/'capture';capture.mkdir()
    (expanded/'provenance.json').write_text(json.dumps({'model':'Qwen/Qwen3-0.6B',
                           'configuration':{'max_length':1024}}))
    src_file=source/'certified_pairs.jsonl';src_file.write_text('CERTIFIED_AND_FROZEN')
    source_digest=up.suff._digest(src_file)
    rec={'schema':up.sign.SCHEMA,'status':'COMPLETE_DISPOSABLE_SIGN_ONLY_NOT_PROMOTED',
        'source_fingerprints':{str(src_file):source_digest},'source_expanded':str(expanded),
        'results':{'localized':{'all':{'auc':.689},'reserved_untrained_magnitudes':{'auc':.571}}},
        'coverage':{'dev_nonzero_cases':307}}
    (sign_root/'final_report.json').write_text(json.dumps(rec))
    arms={'localized':[65,66,71,72],'neighbor_control':[64,67,70,73],
          'random_control':[8,49,74,77],'full100':None}
    monkeypatch.setattr(up,'_source',lambda *_:(rec,arms,expanded,source,capture,[src_file]))
    monkeypatch.setattr(up,'input_sign_grounding',lambda *args,**kwargs:
       {'status':'COMPLETE_READ_ONLY','arms':{arm:{form:{'game_equal_weight_accuracy':.8}
         for form in ('signed','digits','words')} for arm in up.DEFAULT_ARMS}})
    created=[]
    def fake_capture(ns):
        ns.output.mkdir()
        (ns.output/'frozen_features.npz').write_bytes(b'MOCK_FROZEN_FEATURES')
        created.append(('capture',ns.skip_base_ablation,ns.train_groups,ns.test_groups))
    def fake_analyze(ns):
        (ns.input/'analysis.json').write_text('{}')
        created.append(('analyze',ns.offline))
        return {'conclusion':'INCONCLUSIVE_REPRESENTATION_OR_READOUT',
                'development_selected_readouts':{'lora':{
                   'feature':'final_flat','selection':{'dev_balanced_sign_accuracy':.9},
                   'test':{'balanced_sign_accuracy':.7,'triple_accuracy':.4}}}}
    monkeypatch.setattr(up.oracle,'capture',fake_capture)
    monkeypatch.setattr(up.oracle,'analyze',fake_analyze)
    monkeypatch.setattr(up.oracle,'_verify_archive',lambda *a,**k:({'schema':up.oracle.SCHEMA},{}))
    monkeypatch.setattr(up,'math_oracle_path',lambda *a,**kw:{'status':'COMPLETE_EXACT_ORACLE_TRIPLES',
                       'frozen_production_c0_test_triples':{'signed_offset':{
                       'positive_output_greater_than_negative':.7,'whole_triple_correct':.4}}})
    result=up.run_upstream_path(sign_root,device='cpu',permutations=9,train_groups=2,dev_groups=2,test_groups=2)
    assert result['status']=='COMPLETE_READ_ONLY_WITH_EXACT_ORACLE'
    assert result['oracle_development_selected_readouts']['lora']['test_triple_accuracy']==.4
    assert created==[('capture',False,2,2),('analyze',False)]
    dest=Path(result['oracle_capture_dir']).parent
    assert (dest/'final_report.json').exists() and (dest/'input_sign_grounding.json').exists()
    assert src_file.read_text()=='CERTIFIED_AND_FROZEN' and up.suff._digest(src_file)==source_digest
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        up.run_upstream_path(sign_root,output=dest,device='cpu',permutations=9,
             train_groups=2,dev_groups=2,test_groups=2)


def test_input_only_never_calls_model(tmp_path,monkeypatch):
    root=tmp_path/'sign';root.mkdir()
    source=tmp_path/'source';source.mkdir()
    exp=tmp_path/'exp';exp.mkdir()
    capture=tmp_path/'capture';capture.mkdir()
    (exp/'provenance.json').write_text(json.dumps({'model':'Qwen/Qwen3-0.6B',
         'configuration':{'max_length':1024}}))
    rec={'schema':up.sign.SCHEMA,'status':'COMPLETE_DISPOSABLE_SIGN_ONLY_NOT_PROMOTED',
         'source_fingerprints':{},'source_expanded':str(exp),'coverage':{'dev_nonzero_cases':307},
         'results':{'localized':{'all':{'auc':.6},'reserved_untrained_magnitudes':{'auc':.5}}}}
    (root/'final_report.json').write_text(json.dumps(rec))
    arms={'localized':[65,66,71,72],'neighbor_control':[64,67,70,73],
          'random_control':[8,49,74,77],'full100':None}
    monkeypatch.setattr(up,'_source',lambda *_:(rec,arms,exp,source,capture,[]))
    monkeypatch.setattr(up,'input_sign_grounding',lambda *a,**k: {'arms':{
       arm:{form:{'game_equal_weight_accuracy':.5} for form in ('signed','digits','words')}
       for arm in up.DEFAULT_ARMS}})
    monkeypatch.setattr(up.oracle,'capture',lambda *a,**k:pytest.fail('MUST NOT RUN QWEN'))
    result=up.run_upstream_path(root,device='cpu',input_only=True)
    assert result['status']=='INPUT_SIGN_ONLY_ORACLE_NOT_TESTED'


def _completed_sign_lineage(tmp_path,monkeypatch):
    """Model the actual sign -> separability -> calibration -> balanced chain."""
    folders={name:tmp_path/name for name in
        ('sign','separability','calibration','balanced','generalization',
         'expanded','source','capture')}
    for path in folders.values():path.mkdir()
    positions=[65,66,71,72]
    arms=up.suff._arms(positions,seed=47)
    capture_report=folders['capture']/'final_report.json'
    capture_report.write_text(json.dumps({'schema':up.distance.SCHEMA,
                                         'status':'COMPLETE_READ_ONLY'}))
    origin_file=folders['source']/'certified_pairs.jsonl'
    origin_file.write_text('SOURCE_NEVER_MODIFIED')
    balanced_report=folders['balanced']/'final_report.json'
    balanced_report.write_text(json.dumps({
        'schema':up.sign.repair.SCHEMA,
        'status':'COMPLETE_DISPOSABLE_NOT_PROMOTED',
        'source_generalization_or_sufficiency':str(folders['generalization']),
        'source_expanded':str(folders['expanded']),
        'selected_positions':positions,'arms':arms,
        'source_fingerprints':{str(origin_file):up.suff._digest(origin_file)}}))
    calibration_report=folders['calibration']/'final_report.json'
    calibration_report.write_text(json.dumps({
        'schema':up.sign.cal.SCHEMA,
        'status':'COMPLETE_READ_ONLY_NOT_PROMOTED',
        'source_balanced_repair':str(folders['balanced']),
        'source_fingerprints':{str(balanced_report):up.suff._digest(balanced_report)}}))
    sep_report=folders['separability']/'final_report.json'
    sep_report.write_text(json.dumps({
        'schema':up.sign.sep.SCHEMA,
        'status':'COMPLETE_READ_ONLY_NOT_PROMOTED',
        'source_calibration':str(folders['calibration']),
        'source_fingerprints':{str(calibration_report):up.suff._digest(calibration_report)}}))
    def fake_repair_lineage(path):
        assert Path(path)==folders['generalization']
        return (folders['generalization'],{},None,None,positions,
                folders['expanded'],folders['source'],folders['capture'],[origin_file])
    monkeypatch.setattr(up.sign.repair,'_lineage',fake_repair_lineage)
    sign_record={
        'schema':up.sign.SCHEMA,
        'status':'COMPLETE_DISPOSABLE_SIGN_ONLY_NOT_PROMOTED',
        'source_run':str(folders['separability']),
        'source_expanded':str(folders['expanded']),
        'arms':arms,
        'source_fingerprints':{str(p):up.suff._digest(p) for p in (
            sep_report,calibration_report,balanced_report,origin_file)}}
    sign_report=folders['sign']/'final_report.json'
    sign_report.write_text(json.dumps(sign_record))
    return folders,sign_record,sign_report


def test_upstream_resolves_sign_parent_through_all_verified_ancestors(tmp_path,monkeypatch):
    folders,record,sign_report=_completed_sign_lineage(tmp_path,monkeypatch)
    actual,arms,expanded,source,capture,protected=up._source(folders['sign'])
    assert actual==record and arms==record['arms']
    assert expanded==folders['expanded'] and source==folders['source']
    assert capture==folders['capture']
    assert sign_report in protected
    assert folders['separability']/'final_report.json' in protected
    assert folders['calibration']/'final_report.json' in protected
    assert folders['balanced']/'final_report.json' in protected


def test_upstream_rejects_untracked_or_self_referential_sign_parent(tmp_path,monkeypatch):
    folders,record,sign_report=_completed_sign_lineage(tmp_path,monkeypatch)
    record['source_fingerprints'].pop(str(folders['separability']/'final_report.json'))
    sign_report.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='SIGN_PARENT_NOT_FINGERPRINTED'):
        up._source(folders['sign'])
    record['source_run']=str(folders['sign'])
    sign_report.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='SIGN_PARENT_SELF_REFERENCE'):
        up._source(folders['sign'])


def test_upstream_rejects_sign_arms_tampering_and_parent_drift(tmp_path,monkeypatch):
    folders,record,sign_report=_completed_sign_lineage(tmp_path,monkeypatch)
    record['arms']['localized']=[1,2,3,4]
    sign_report.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='SIGN_CONTROL_ARMS_MISMATCH'):
        up._source(folders['sign'])
    record['arms']=up.suff._arms([65,66,71,72],seed=47)
    sign_report.write_text(json.dumps(record))
    (folders['separability']/'final_report.json').write_text('MUTATED')
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        up._source(folders['sign'])
