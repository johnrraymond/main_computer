"""Strict offline controls for the frozen NanoJev composition architecture bakeoff."""
from __future__ import annotations

from hashlib import sha256
import json

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_composition_bakeoff as lab
from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_ternary_bakeoff as ternary
from tests.test_nanojev_sealed_overlap_ternary_bakeoff import fake_encoder


def setup_cases(train_examples=720):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [x["id"] for x in space["dimensions"]]
    pools = lab.load_phrases(lab.FRESH_PHRASES,space)
    rows = lab.make_cases(axes,pools,train_examples=train_examples,groups=24,seed=1337)
    return space,axes,pools,rows


def test_fresh_final_was_authored_independently_and_training_unchanged():
    space,axes,pools,rows=setup_cases()
    assert len(axes)==8
    assert len(rows['train'])==720
    assert len(rows['selection'])==72 and len(rows['final'])==72
    assert all(r['prompt'].startswith(lab.FINAL_NEW_WRAPPER) for r in rows['final'])
    old=ternary.read_phrases(ternary.DEFAULT_PROBES,space)
    assert pools['train']==old['train'] and pools['selection']==old['selection']
    fresh={p for pair in pools['final'].values() for values in pair.values() for p in values}
    earlier={p for phase in old.values() for pair in phase.values() for values in pair.values() for p in values}
    assert fresh.isdisjoint(earlier) and len(fresh)==32
    states=[tuple(r['states']) for phase in ternary.PHASES for r in rows[phase]]
    assert len(states)==len(set(states))
    text=[r['prompt'] for phase in ternary.PHASES for r in rows[phase]]
    assert len(text)==len(set(text))


def test_phrase_reuse_or_train_override_rejected(tmp_path):
    space=smoke.load_space(smoke.SPACE_DEFAULT)
    obj=json.loads(lab.FRESH_PHRASES.read_text())
    obj['phases']['final']['authority']['negative'][0]=obj['phases']['train']['authority']['negative'][0]
    p=tmp_path/'bad.json';p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match='REUSE|LEAKAGE'):
        lab.load_phrases(p,space)
    obj=json.loads(lab.FRESH_PHRASES.read_text())
    obj['phases']['train']['authority']['positive'][0]='Changed training phrase without authorization'
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match='TRAIN_SELECTION_POOLS'):
        lab.load_phrases(p,space)


def test_splitter_is_syntactic_lossless_and_does_not_know_semantic_states():
    _,axes,pools,rows=setup_cases()
    for phase in ternary.PHASES:
        for row in rows[phase]:
            clauses=lab.split_clauses(row['prompt'])
            assert len(clauses)==sum(s!=0 for s in row['states'])
            assert lab.WRAPPERS[phase]+'. '.join(clauses)+'.'==row['prompt']
    with pytest.raises(ValueError,match='WRAPPER'):
        lab.split_clauses('unknown opening: The supplier must perform.')
    with pytest.raises(ValueError,match='SPLIT'):
        lab.split_clauses(lab.FINAL_NEW_WRAPPER+'A sentence. A sentence.')
    with pytest.raises(ValueError,match='PUNCTUATION'):
        lab.split_clauses(lab.FINAL_NEW_WRAPPER+'A sentence')
    # The splitter does not depend on a target label supplied by the fixture.
    prompt=lab.FINAL_NEW_WRAPPER+'The widget exists. The valve does not exist.'
    assert lab.split_clauses(prompt)==['The widget exists','The valve does not exist']


def test_training_atomic_supervision_is_only_train_and_cover_full_48():
    _,axes,pools,rows=setup_cases()
    train_texts,indices=lab.catalog(rows['train'])
    labels=lab.training_annotations(pools,axes,train_texts,require_complete=True)
    assert len(train_texts)==48 and labels.shape==(48,2)
    assert len(set(map(tuple,labels)))==16
    assert set(np.unique(labels[:,1]))=={-1,1}
    final_texts,_=lab.catalog(rows['final'])
    assert len(final_texts)==32 and set(final_texts).isdisjoint(train_texts)
    with pytest.raises(ValueError,match='UNKNOWN_TRAINING_CLAUSE'):
        lab.training_annotations(pools,axes,final_texts,require_complete=False)
    assert sum(map(len,indices))>720


def test_structured_assembly_preserves_denial_omission_and_conflict():
    atoms=np.array([[0,1],[0,-1],[3,1],[3,-1],[7,-1]],dtype=np.int16)
    output=lab.compose_atoms(atoms,[[0,2],[1,4],[],[0,1],[3,2]])
    assert output.shape==(5,8)
    assert output[0,0]==1 and output[0,3]==1 and output[0,1]==0
    assert output[1,0]==-1 and output[1,7]==-1
    assert np.all(output[2]==0)
    assert output[3,0]==2 and output[3,1]==0
    assert output[4,3]==2
    assert np.array_equal(lab.compose_atoms(atoms,[[2,0]]),lab.compose_atoms(atoms,[[0,2]]))
    with pytest.raises(ValueError,match='ATOMIC_PREDICTIONS'):
        lab.compose_atoms(np.array([[0,0]]),[[0]])
    with pytest.raises(ValueError,match='ATOMIC_INDEX'):
        lab.compose_atoms(atoms,[[999]])


def test_permutation_and_conflict_challenge_never_attach_label_to_inference():
    _,axes,pools,rows=setup_cases()
    txt,target=lab.contradiction_prompts(rows['final'],seed=2026)
    assert len(txt)==len(target)==24
    for i,(text,axis) in enumerate(zip(txt,target)):
        original=[lab.split_clauses(r['prompt']) for r in rows['final'][3*i:3*i+3]]
        both=lab.split_clauses(text)
        assert len(both)==len(original[0])+1
        assert set(both)==set(original[0])|set(original[1])
        assert axis==rows['final'][3*i]['target_axis']
    shuffled=lab.shuffle_rows(rows['final'],seed=11)
    assert len(shuffled)==72
    assert all(set(lab.split_clauses(x['prompt']))==set(lab.split_clauses(y['prompt'])) for x,y in zip(shuffled,rows['final']))
    assert any(x['prompt']!=y['prompt'] for x,y in zip(shuffled,rows['final']))


def test_atom_rbf_training_and_prototype_are_train_only():
    rng=np.random.default_rng(41)
    feats=np.eye(16,dtype=float)+0.02*rng.normal(size=(16,16))
    x=np.vstack([feats,feats])
    labels=np.tile(np.arange(16),2)
    model=lab.fit_rbf_classifier(x,labels,16,ridge=.01)
    assert lab.score_rbf_classifier(model,x).shape==(32,16)
    assert (np.argmax(lab.score_rbf_classifier(model,x),axis=1)==labels).mean()>=.75
    with pytest.raises(ValueError,match='CLASS_TRAINING'):
        lab.fit_rbf_classifier(x,np.zeros(32),16,ridge=.01)
    with pytest.raises(ValueError,match='CLASS_TRAINING'):
        lab.fit_rbf_classifier(x,labels,16,ridge=0)


def test_score_counts_predicted_conflicts_as_wrong_even_if_truth_is_omission():
    axes=list('abcdefgh')
    rows=[{'states':[1,0,0,0,0,0,0,0],'variant':'affirmed','target_axis':'a','group_id':'g0'},
          {'states':[-1,0,0,0,0,0,0,0],'variant':'denied','target_axis':'a','group_id':'g0'},
          {'states':[0,0,0,0,0,0,0,0],'variant':'unspecified','target_axis':'a','group_id':'g0'}]
    truth=ternary.truth_matrix(rows)
    oracle=lab.score_prediction(rows,truth,axes)
    assert oracle['exact_semantic_pattern_accuracy']==1
    wrong=truth.copy(); wrong[2,1]=2
    metric=lab.score_prediction(rows,wrong,axes)
    assert metric['exact_semantic_pattern_accuracy']==2/3
    assert metric['predicted_conflict_axis_fraction'] == pytest.approx(1/24)
    assert metric['triads']['target_denied_vs_unmentioned_both_correct']==1.
    with pytest.raises(ValueError,match='INVALID_PREDICTIONS'):
        lab.score_prediction(rows,np.full((3,8),9),axes)


def test_null_is_not_mistaken_for_complete_semantics():
    _,axes,pools,rows=setup_cases()
    truth=ternary.truth_matrix(rows['final'])
    pred=np.zeros_like(truth)
    score=lab.score_prediction(rows['final'],pred,axes)
    assert score['exact_semantic_pattern_accuracy']==0
    assert score['mean_axis_accuracy']>.3 # Omitted-state bias is explicitly exposed.
    assert score['stated_axis_accuracy']==0
    assert score['triads']['target_denied_vs_unmentioned_both_correct']==0


def test_require_provenance_and_do_not_reuse_stale_confirmation(tmp_path):
    space=smoke.load_space(smoke.SPACE_DEFAULT)
    c={'schema':'nanojev.sealed-overlap.polarity-confirmation.v1',
       'status':'COMPLETE_LOCKED_POLARITY_CONFIRMATION_DIAGNOSTIC_NO_SEAL_NO_PROMOTION',
       'acceptance':{'never_updates_model_weights':True,'candidate_not_promoted':True},
       'design':{'candidate_locked_before_new_holdout':'half:last',
                 'reference_locked_before_new_holdout':'final:mean','selection_on_new_holdout':False},
       'sources':{'space_sha256':smoke.seal(space)},'results':{v:{'projection_numeric_stability':{'fp32':{'comparison_flip_rate':0.0}}} for v in lab.VIEWS}}
    path=tmp_path/'confirm.json';path.write_text(json.dumps(c))
    assert lab.validate_lineage(path,space=space)['schema']==c['schema']
    c['design']['candidate_locked_before_new_holdout']='quarter:mean'
    path.write_text(json.dumps(c))
    with pytest.raises(ValueError,match='PROVENANCE_INVALID'):
        lab.validate_lineage(path,space=space)


def test_full_pipeline_mock_reuses_same_test_rows_and_cannot_overwrite(tmp_path):
    # The tiny train size exercises end-to-end orchestration without relying
    # on Qwen weights or GPU; the actual run uses the 720-case locked budget.
    space,axes,pools,rows=setup_cases(train_examples=120)
    conf={'schema':'nanojev.sealed-overlap.polarity-confirmation.v1',
          'status':'COMPLETE_LOCKED_POLARITY_CONFIRMATION_DIAGNOSTIC_NO_SEAL_NO_PROMOTION',
          'acceptance':{'never_updates_model_weights':True,'candidate_not_promoted':True},
          'design':{'candidate_locked_before_new_holdout':'half:last',
                    'reference_locked_before_new_holdout':'final:mean','selection_on_new_holdout':False},
          'sources':{'space_sha256':smoke.seal(space)},'results':{v:{'projection_numeric_stability':{'fp32':{'comparison_flip_rate':0.0}}} for v in lab.VIEWS}}
    path=tmp_path/'conf.json';path.write_text(json.dumps(conf))
    args=lab.parser().parse_args(['--device','cpu','--train-examples','120','--groups','24',
        '--bootstrap-trials','10','--confirmation-report',str(path),'--output',str(tmp_path/'report.json')])
    calls=[]
    base=fake_encoder(space,pools,calls)
    def fake_layer(texts):
        x=base(texts)
        # Both locked arms are different representational geometries; no
        # inference-time labels are passed to this function.
        return {'final:mean':x,'half:last':np.column_stack([x[:,:16]*2,x[:,16:]])}
    result=lab.run(args,encoder=fake_layer)
    assert result['status']=='COMPLETE_COMPOSITION_BAKEOFF_DIAGNOSTIC_NO_SEAL_NO_PROMOTION'
    assert len(result['final'])==2
    assert all(set(v)==set(lab.ARMS) for v in result['final'].values())
    assert len(result['selection']['final:mean'])==5
    assert len(result['heldout_clause_axis_and_polarity_audit']['half:last'])==3
    assert result['design']['train_prompts']==120
    assert result['design']['final_prompts']==72
    assert result['design']['fresh_final_wording'] is True
    assert result['acceptance']['no_final_based_model_selection'] is True
    assert result['all_omitted_null_control']['exact_semantic_pattern_accuracy']==0
    assert set(result['contradiction_challenge']['half:last'])==set(lab.ARMS[2:])
    assert len(result['paired_vs_flat_within_extraction']['half:last'])==4
    assert all(len(v['per_axis'])==8 for v in result['final']['half:last'].values())
    for view in lab.VIEWS:
        for arm in lab.ARMS[1:]:
            assert result['permutation_challenge'][view][arm]['axis_prediction_flip_rate']==0
    assert len(calls)==8  # train and clause, selection and clause, final and clause, conflict reuse, shuffled whole
    assert len(calls[0])==120 and len(calls[2])==72 and len(calls[4])==72
    assert json.loads(args.output.read_text())['schema']==lab.SCHEMA
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        lab.run(args,encoder=fake_layer)
