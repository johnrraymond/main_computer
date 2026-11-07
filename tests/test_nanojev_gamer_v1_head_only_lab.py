"""Bounded, read-only head-only intervention controls."""
from fractions import Fraction
import json
import hashlib
from pathlib import Path
import numpy as np
import pytest

from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe


def _rows_and_features():
    deck=probe.make_deck(6,3,3,False)
    rows=[x.public() for x in deck]
    y=np.asarray([float(x.target) for x in deck])
    # Known sign and exact-zero information is present in the frozen vectors.
    # The source-native and game surfaces are identical by construction.
    X=np.asarray([[np.sign(v),float(v==0),float(v),0.5]
                  for v in y],dtype=np.float32)
    return rows,y,X


def test_oracle_reports_correct_group_contract(capsys):
    assert lab.main([])==0
    answer=json.loads(capsys.readouterr().out)
    assert answer['model_evaluated'] is False
    assert answer['deck']['cases']==612
    assert answer['head_candidate_count']==18


def test_no_leakage_of_triplets_or_prompt_surface():
    rows,_,_= _rows_and_features()
    assert lab._check_archive_groups(rows)==12
    broken=[dict(v) for v in rows]
    broken[1]['split']='test'
    with pytest.raises(ValueError,match='GROUP_LEAKAGE'):
        lab._check_archive_groups(broken)
    with pytest.raises(ValueError,match='INCOMPLETE'):
        lab._check_archive_groups(rows[:-1])


def test_decoder_preserves_declared_sign_and_can_guard_magnitude():
    raw=np.asarray([[1,0,0,-.1],[0,1,0,9],[0,0,1,.1]],dtype=float)
    old=np.asarray([7,2,3],dtype=float)
    cls,val=lab._decode(raw,old,'sign_guard')
    assert list(cls)==[-1,0,1]
    assert list(val)==[-7,0,3]
    cls,val=lab._decode(raw,old,'hurdle')
    assert list(cls)==[-1,0,1]
    assert val[0]<0 and val[1]==0 and val[2]>0
    with pytest.raises(ValueError,match='REQUIRES_PRODUCTION'):
        lab._decode(raw,None,'sign_guard')


def test_test_split_is_never_fitted_or_used_for_selection(monkeypatch):
    rows,y,X=_rows_and_features()
    data={'dataset':rows,'dataset_sha256':'sample'}
    arrays={'production_head_predictions':np.ones(len(rows),dtype=np.float32)*0.08,
            'lora__final_last':X}
    base=lab._evaluate_head(data,arrays,selected_features=['final_last'])
    assert len(base['candidates'])==3
    # Change ALL held-out feature labels, which can destroy the result, but
    # must not change any development-selected hyperparameter or candidate.
    subset=np.asarray([i for i,x in enumerate(rows) if x['split']=='test'])
    altered=arrays.copy()
    changed=X.copy();changed[subset]=-changed[subset]
    altered['lora__final_last']=changed
    rerun=lab._evaluate_head(data,altered,selected_features=['final_last'])
    assert rerun['dev_selected_candidate_id']==base['dev_selected_candidate_id']
    for first,second in zip(base['candidates'],rerun['candidates']):
        assert first['alpha']==second['alpha']
        assert first['development']==second['development']


def test_missing_retention_never_authorizes_promotion():
    rows,y,X=_rows_and_features()
    result=lab._evaluate_head({'dataset':rows},
              {'production_head_predictions':np.full(len(rows),.2),
               'lora__final_last':X},selected_features=['final_last'])
    assert result['source_retention_status']=='UNVERIFIED_NO_ORIGINAL_FEATURES'
    for row in result['candidates']:
        assert not row['gates']['source_retention']
        assert not row['gates']['all_bounded_gates_pass']
        assert not row['gates']['promotion_authorized']
    assert result['overall_status']=='BOUNDED_HEAD_ONLY_EXPERIMENT_NO_PROMOTION'


def test_retention_rejects_losing_original_correctness_or_magnitude():
    y=np.asarray([-12.,0.,4.,.05,-.05,20.])
    rows=[{'group':str(i),'surface':'source_exact','family':'certified','target':str(v)}
          for i,v in enumerate(y)]
    old=y.copy()
    bad=np.full(len(y),0.1)
    result=lab._retention(y,bad,lab._classify_continuous(bad),old,rows)
    assert result['status']=='FAIL'
    assert result['original_correct_kept_fraction']<.95
    ok=lab._retention(y,old,lab._classify_continuous(old),old,rows)
    # Nonzero ±0.05 are marked zero by the threshold, and baseline retention
    # correctly remains conditional on what the baseline itself got right.
    assert ok['status']=='PASS'


def test_source_fp_mismatch_refused_before_model_comparison(tmp_path):
    n=101
    lineage={'checkpoint_sha256':'a','checkpoint_step':500,'adapter_sha256':'b',
             'model_id':'qwen','configuration':{'policy':1}}
    rows=[{'id':str(i),'source_index':i,'kind':'certified_source','target':'0',
           'surface':'source_exact','group':str(i)} for i in range(n)]
    arr={'predictions':np.zeros(n,dtype=np.float32),
         'lora__final_flat':np.zeros((n,102400),dtype=np.float16)}
    meta={'schema':lab.SOURCE_SCHEMA,'lineage':lineage,'dataset':rows,
          'source_count':n,'source_available':n,'source_complete':True,
          'dataset_sha256':probe.sha_text(probe.canonical(rows)),
          'array_sha256':{k:lab._sha_array(v) for k,v in arr.items()}}
    f=tmp_path/lab.SOURCE_ARCHIVE
    with f.open('wb') as h:np.savez_compressed(h,metadata=probe.canonical(meta),**arr)
    with pytest.raises(ValueError,match='LINEAGE_MISMATCH'):
        lab._verified_source_archive(f,{**lineage,'checkpoint_step':700},False)
    _,loaded=lab._verified_source_archive(f,lineage,False)
    assert loaded['lora__final_flat'].shape==(n,102400)
    arr['predictions'][5]=2
    with f.open('wb') as h:np.savez_compressed(h,metadata=probe.canonical(meta),**arr)
    with pytest.raises(ValueError,match='FINGERPRINT'):
        lab._verified_source_archive(f,lineage,False)


def test_output_path_never_mutates_input_or_original(tmp_path):
    original=tmp_path/'source';original.mkdir()
    out=tmp_path/'new'
    assert lab._output(out,[original])==out
    for p in [original,original/'reports',tmp_path]:
        with pytest.raises(ValueError,match='OVERLAPS_SOURCE'):
            lab._output(p,[original])
    out.mkdir()
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        lab._output(out,[original])


def test_controlled_numeric_gate_cannot_be_faked_by_exact_zero_predictions():
    rows,y,X=_rows_and_features()
    ids=np.asarray([i for i,r in enumerate(rows) if r['split']=='test'])
    z=np.zeros(len(ids))
    cls=np.zeros(len(ids),dtype=np.int8)
    m=lab._summary(y[ids],z,cls,rows,ids)
    assert m['exact_zero_within_0_05']==1.
    assert m['triple_accuracy']==0
    assert m['near_nonzero_strict_sign']==0


def test_gates_block_even_with_perfect_synthetic_if_retention_missing():
    rows,y,X=_rows_and_features()
    ids=np.asarray([i for i,r in enumerate(rows) if r['split']=='test'])
    trueclass=np.sign(y[ids]).astype(np.int8)
    m=lab._group_scores(rows,y,y,np.sign(y).astype(np.int8),ids)
    g=lab._gates(m,None,m['all'])
    assert g['strict_sign_and_zero']
    assert g['family_transfer']
    assert g['magnitude_no_regression']
    assert not g['all_bounded_gates_pass']
    assert not g['promotion_authorized']


def test_mock_source_cache_matches_original_index_order(tmp_path,monkeypatch):
    """A sign-balanced source selection must not reorder trained head inference."""
    from argparse import Namespace
    from tools import nanojev_two_game_spine_smoke as spine
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_zero_sign_frob as original
    run=tmp_path/'run';run.mkdir()
    expanded=tmp_path/'expanded';expanded.mkdir()
    adapter=run/'adapter';adapter.mkdir()
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'frozen')
    checkpoint=expanded/'best_model.pt';checkpoint.write_bytes(b'frozen-checkpoint')
    lineage={'checkpoint_path':str(checkpoint),'checkpoint_sha256':spine.file_sha(checkpoint),
             'adapter_dir':str(adapter),'adapter_sha256':spine.adapter_identity(adapter),
             'checkpoint_step':500,'model_id':'mock-qwen','expanded_run':str(expanded),
             'configuration':{'max_length':1024,'curvature':4.0,
                              'batch_size':4,'capture_batch':2}}
    identity={'dataset_sha256':'verified','original_prompt_corpus_sha256':'verified'}
    monkeypatch.setattr(probe,'_verified_lineage',lambda *a: lineage)
    monkeypatch.setattr(spine,'expanded_metadata',lambda *a: identity)
    def verified(runpath,identity0):
        assert identity0['dataset_sha256']=='verified'
        return [dict(answer=0,game_id='game') for _ in range(100)]
    monkeypatch.setattr(original,'_manifest_verified_dev',verified)
    # Deliberately permute source controls versus the original dev order.
    order=list(reversed(range(100)))
    cases=[original.Probe(f'certified.dev.{i:05d}','source_control','certified_source',
            'source_exact',f'game original prompt {i}',Fraction(0),source_index=i)
            for i in order]
    monkeypatch.setattr(original,'select_source_controls',
        lambda dev,n:(cases,{'source_count':len(cases)}))
    reference={p.id:float(p.source_index) for p in cases}
    monkeypatch.setattr(original,'_check_original_evaluation',
        lambda *a:{'status':'CHECKPOINT_MATCHED',
                   'validated_reference_predictions':reference})
    def cached(expanded0,identity0,dev):
        vals=np.zeros((len(dev),102400),dtype=np.float32)
        vals[:,0]=np.arange(len(dev))
        cache=expanded/'feature_cache'/'dev.npz'
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(b'frozen-training-dev-cache')
        return vals,{'path':str(cache),'sha256':spine.file_sha(cache),
                    'feature_policy':'EXACT_ORIGINAL_TRAINING_CACHE'}
    monkeypatch.setattr(lab,'_verified_training_dev_cache',cached)
    captured_orders=[]
    def head(info,all_features,*,device,head_batch):
        captured_orders.append(list(all_features[:,0]))
        return all_features[:,0].astype(np.float64)
    monkeypatch.setattr(lab,'_original_head_predictions',head)
    out=tmp_path/'retention'
    args=Namespace(run=run,expanded_run=expanded,output=out,source_cases=100,
                   model_id='mock-qwen',capture_batch=2,head_batch=4,
                   max_input_tokens=1024,device='cpu',include_intermediate_layers=False)
    lab.source_capture(args)
    metadata,arrays=lab._verified_source_archive(out,lineage,True)
    assert metadata['source_complete']
    assert metadata['checkpoint_matched_reference_max_delta']==0
    assert metadata['capture']['capture_repeated'] is False
    assert captured_orders==[list(range(100))]
    assert list(arrays['predictions'])==list(reversed(range(100)))
    assert list(arrays['lora__final_flat'][:,0])==list(reversed(range(100)))
    assert (out/'source_manifest.json').is_file()
    assert checkpoint.read_bytes()==b'frozen-checkpoint'
    with pytest.raises(FileExistsError,match='OUTPUT_ALREADY_EXISTS'):
        lab.source_capture(args)


def test_parity_refuses_a_real_discrepancy_instead_of_raising_tolerance():
    from tools import nanojev_gamer_v1_zero_sign_frob as original
    cases=[original.Probe('a','source','certified_source','source_exact','p',
             Fraction(0),source_index=0),
           original.Probe('b','source','certified_source','source_exact','q',
             Fraction(1),source_index=1)]
    result=lab._source_reference_parity(cases,[0.0,1.005],{'a':0.0,'b':1.0})
    assert result['max_absolute_delta']<0.01
    with pytest.raises(ValueError,match='HEAD_LAB_SAVED_CHECKPOINT_PREDICTION_PARITY_FAILED') as e:
        lab._source_reference_parity(cases,[0.0,1.07336033649],{'a':0.0,'b':1.0})
    assert '"source_index": 1' in str(e.value)


def test_training_cache_rejects_unmatched_prompt_order_and_provenance(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_expanded_head as expanded_head
    from tools import nanojev_gamer_v1_handoff as gamer
    expanded=tmp_path/'expanded';expanded.mkdir()
    cache=expanded/'feature_cache';cache.mkdir()
    identity={'configuration':{'input_width':102400},'adapter_sha256':'known'}
    (expanded/'provenance.json').write_text(json.dumps(identity))
    rows=[{'prompt_08':'first'},{'prompt_08':'second'}]
    # This invokes the actual original _read_cache identity verification.
    expected={**identity,'split':'dev',
              'prompt_sha256':hashlib.sha256(gamer.canonical(
                 [gamer.original_prompt(r) for r in rows]).encode()).hexdigest()}
    import tools.nanojev_gamer_v1_handoff as gm
    with (cache/'dev.npz').open('wb') as f:
        np.savez_compressed(f,identity=gm.canonical(expected),
                            features=np.zeros((2,102400),dtype=np.float16),
                            summary=gm.canonical({'capture_hook':'final_norm_only'}))
    x,details=lab._verified_training_dev_cache(expanded,identity,rows)
    assert x.shape==(2,102400)
    assert details['feature_policy']=='EXACT_ORIGINAL_TRAINING_CACHE'
    with pytest.raises(ValueError,match='CACHE_LINEAGE_MISMATCH'):
        lab._verified_training_dev_cache(expanded,identity,list(reversed(rows)))
    (expanded/'provenance.json').write_text(json.dumps({'wrong':'identity'}))
    with pytest.raises(ValueError,match='TRAINING_PROVENANCE_MISMATCH'):
        lab._verified_training_dev_cache(expanded,identity,rows)
