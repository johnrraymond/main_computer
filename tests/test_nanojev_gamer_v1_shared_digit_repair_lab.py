"""Acceptance-oriented tests for shared frozen-embedding digit extraction."""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import nanojev_gamer_v1_shared_digit_repair_lab as lab
from tools import nanojev_gamer_v1_deconfounded_operand_audit as deconf
from tools import nanojev_gamer_v1_exact_belief_lab as exact
from tools import nanojev_gamer_v1_payoff_precision_lab as payoff


def _fixture():
    rng=np.random.default_rng(47)
    embeddings=rng.normal(0, 1,size=(10,1024)).astype(np.float32)
    # Identical trained embedding identity at each character position.
    numbers=['1','2','3','4','5','6','7','8','9','10',
             '5700','8280','401','3001','5377','5423','123457']
    rows=[];frames={part:[] for part in lab.PARTS}
    for number in numbers:
        row={'components':{}}
        for part in lab.PARTS:
            word=number if part=='denominator' else '1' if number=='123457' else number
            if len(word)>6:raise AssertionError
            tokens=[{'text':digit,'slot':6-len(word)+j,
                     'component_char_span':[j,j+1],
                     'token_id':int(digit)+200}
                    for j,digit in enumerate(word)]
            row['components'][part]={'tokens':tokens}
            frame=np.zeros((6,1024),dtype=np.float32)
            for t in tokens:frame[t['slot']]=embeddings[int(t['text'])]
            frames[part].append(frame.reshape(-1))
        rows.append(row)
    return rows,{k:np.stack(v) for k,v in frames.items()},numbers


def test_train_only_shared_bank_covers_all_ten_digits():
    rows,frames,_=_fixture()
    bank=lab._training_bank(rows,frames,np.arange(10))
    assert set(np.where(bank['counts']>0)[0])==set(range(10))
    assert bank['maximum_train_deviation']<1e-4
    assert int(sum(bank['counts']))==22


@pytest.mark.parametrize('mode',['exact_centroid','shared_linear'])
def test_shared_decoder_recovers_unseen_four_and_six_digit_components(mode):
    rows,frames,numbers=_fixture()
    model=lab._candidate(lab._training_bank(rows,frames,np.arange(10)),mode)
    recovered,unsupported=lab._infer(rows,frames,model)
    assert unsupported=={}
    assert recovered['denominator']==[int(v) for v in numbers]
    assert recovered['numerator'][-1]==1
    assert recovered['denominator'][10:]==[5700,8280,401,3001,5377,5423,123457]


def test_evaluation_token_labels_and_token_ids_cannot_change_prediction():
    rows,frames,_=_fixture()
    decoder=lab._candidate(lab._training_bank(rows,frames,np.arange(10)),'exact_centroid')
    before,_=lab._infer(rows,frames,decoder)
    for row in rows[10:]:
        for part in lab.PARTS:
            for t in row['components'][part]['tokens']:
                t['text']='X'  # deliberately false evaluation label
                t['token_id']=-999
    after,_=lab._infer(rows,frames,decoder)
    assert before==after


def test_exact_centroid_refuses_unknown_embedding_instead_of_guessing():
    rows,frames,_=_fixture()
    model=lab._candidate(lab._training_bank(rows,frames,np.arange(10)),'exact_centroid')
    broken={k:v.copy() for k,v in frames.items()}
    broken['denominator'][10].reshape(6,1024)[2]+=10
    got,unsupported=lab._infer(rows,broken,model)
    assert got['denominator'][10] is None
    assert unsupported['denominator']==1
    assert got['denominator'][11]==8280


def test_train_covers_every_digit_or_fails_closed():
    rows,frames,_=_fixture()
    with pytest.raises(ValueError,match='DIGIT_COVERAGE'):
        lab._training_bank(rows,frames,[0,1])


def test_training_identity_varies_by_position_must_fail_closed():
    rows,frames,_=_fixture()
    altered={k:v.copy() for k,v in frames.items()}
    altered['denominator'][0].reshape(6,1024)[5,0]+=1
    with pytest.raises(ValueError,match='POSITION_INVARIANT'):
        lab._training_bank(rows,altered,np.arange(10))


def test_unsupported_multicharacter_token_span_rejected(tmp_path):
    row={'index':0,'case_id':'example','panel':'train','components':{
        name:{'tokens':[{'slot':5,'component_char_span':[0,3],
                          'text':'570','token_id':2}]}
        for name in lab.PARTS}}
    trace=tmp_path/'trace.jsonl';trace.write_text(json.dumps(row)+'\n')
    with pytest.raises(ValueError,match='ONE_DIGIT_PER_TOKEN'):
        lab._aligned_trace({'trace':trace},[SimpleNamespace(id='example')],{'train':[0]})


def test_alignment_checks_panel_and_case_lineage(tmp_path):
    row={'index':0,'case_id':'correct','panel':'train','components':{
        name:{'tokens':[{'slot':5,'component_char_span':[0,1],
                          'text':'7','token_id':2}]}
        for name in lab.PARTS}}
    trace=tmp_path/'trace.jsonl';trace.write_text(json.dumps(row)+'\n')
    with pytest.raises(ValueError,match='CASE_ID_OR_PANEL'):
        lab._aligned_trace({'trace':trace},[SimpleNamespace(id='incorrect')],{'train':[0]})
    aligned=lab._aligned_trace({'trace':trace},[SimpleNamespace(id='correct')],{'train':[0]})
    assert len(aligned)==1


def test_checkpoint_replays_and_is_sha_bound(tmp_path):
    rows,frames,_=_fixture()
    bank=lab._training_bank(rows,frames,np.arange(10))
    model=lab._candidate(bank,'shared_linear')
    parts,_=lab._infer(rows,frames,model)
    src={'report_sha256':'a'*64,'report':{'source_composition_sha256':'b'*64,
           'combined_deck_sha256':'c'*64,'trace_files':{'all':{'sha256':'d'*64}}}}
    ck=tmp_path/'shared-digit.npz'
    info=lab._save_and_verify(ck,{'model':model,'parts':parts},bank,src,rows,frames)
    assert info['replay_matches_selected_predictions']
    assert info['sha256']==lab.token._sha(ck)
    with np.load(ck,allow_pickle=False) as z:
        meta=json.loads(str(z['metadata'].item()))
        assert meta['source_trace_sha256']=='d'*64
        assert meta['source_deck_sha256']=='c'*64


def test_candidate_selection_only_reads_fresh_development_targets(monkeypatch):
    rows,frames,_=_fixture()
    bank=lab._training_bank(rows,frames,np.arange(10))
    targets=[Fraction(int(v),int(v)+1) for v in range(1,len(rows)+1)]
    panels={'fresh_dev':np.arange(10,len(rows)), 'challenge_zero_anchor':np.arange(2)}
    original,_=lab._choose(rows,frames,targets,panels,bank)
    mutated=list(targets)
    for i in panels['challenge_zero_anchor']:mutated[int(i)]=Fraction(17,101)
    selected,_=lab._choose(rows,frames,mutated,panels,bank)
    assert original['model']['mode']==selected['model']['mode']
    assert original['parts']==selected['parts']


def test_fraction_assembly_requires_both_decoded_components():
    p=lab.compose._assemble([3581,None,3,10],[5700,8280,0,7])
    assert p==[Fraction(3581,5700),None,None,None]


def _protected_fixture():
    from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand
    from tools import nanojev_gamer_v1_token_localization_lab as token
    old,tags,ix,_,_=deconf._make_deck()
    challenge,ct=exact._challenge()
    assert len(old)==600 and len(challenge)==96
    full=old+challenge
    q=exact._exact_q(full)
    sub={'heldout_independent':ix['heldout_independent'],
         'heldout_zero_anchor':ix['heldout_zero_anchor'],
         'challenge_independent':np.array([600+i for i,t in enumerate(ct) if t['panel']=='independent']),
         'challenge_zero_anchor':np.array([600+i for i,t in enumerate(ct) if t['panel']=='zero_anchor'])}
    # A FIXTURE only: emulate an all-exact frozen payoff replay. Production
    # runs never obtain values from the oracle for predictions.
    vals={name:np.array([int(x) for x in target]) for name,target in
          payoff._integer_targets(operand._targets(full)).items()}
    return full,tags+ct,q,sub,vals


def test_sixteen_strict_full_zero_triples_proven_by_exact_recomposition_fixture():
    deck,tags,q,panels,payoffs=_protected_fixture()
    comp,triples=lab._score_exact(deck,tags,panels,q,q,payoffs)
    assert triples['heldout']['strict_exact_full_triples']==8
    assert triples['challenge']['strict_exact_full_triples']==8
    assert all(p['exact_fraction_margin']==1 for p in comp.values())
    # The intended acceptance is not merely that center q is exact.
    changed=list(q)
    altered=int(panels['challenge_zero_anchor'][0])
    changed[altered]=Fraction(17,19)
    comp,triples=lab._score_exact(deck,tags,panels,q,changed,payoffs)
    assert triples['challenge']['strict_exact_full_triples']==7
    assert triples['heldout']['strict_exact_full_triples']==8
    assert comp['challenge_zero_anchor']['exact_fraction_margin']<1


def test_zero_line_refuses_incorrect_frozen_payoff_even_if_q_exact():
    deck,tags,q,panels,payoffs=_protected_fixture()
    altered={k:v.copy() for k,v in payoffs.items()}
    altered['R'][int(panels['challenge_zero_anchor'][0])]+=1
    comp,triples=lab._score_exact(deck,tags,panels,q,q,altered)
    assert triples['challenge']['strict_exact_full_triples']==7
    assert comp['challenge_zero_anchor']['all_payoffs_exact']<1


def test_promotion_remains_blocked_in_source_contract():
    assert lab.SCHEMA.endswith('.v1')
    assert 'BLOCKED_DIAGNOSTIC_ONLY' in lab.run.__code__.co_consts
    assert 'positional' in lab.main.__code__.co_names or 'positional' in lab.run.__code__.co_names


def test_real_source_nesting_replays_696_saved_payoff_rows(tmp_path,monkeypatch):
    old=tmp_path/'old.npz';challenge=tmp_path/'challenge.npz'
    np.savez_compressed(old,**{'layer_09__'+k:np.zeros((600,1),dtype=np.float32)
                               for k in deconf.VIEWS})
    np.savez_compressed(challenge,**{k:np.ones((96,1),dtype=np.float32)
                                    for k in deconf.VIEWS})
    frozen={k:tmp_path/(k+'.npz') for k in payoff.PAYOFFS}
    for p in frozen.values():p.write_bytes(b'untouched')
    entries={k:{'path':str(path)} for k,path in frozen.items()}
    source={'composition':{'upstream':{
       'precision':{'archive':old,'report':{
          'fresh_feature_archive':{'sha256':lab.token._sha(old)},
          'diagnostic_checkpoints':entries,
          'deconfounded_report_sha256':'z'*64,
          'payoffs':{k:{'selection':{'view':k}} for k in payoff.PAYOFFS}}},
       'report':{'challenge_feature_archive':{'path':str(challenge),
                                             'sha256':lab.token._sha(challenge)}}}}}
    calls=[]
    def fake_replay(path,frame,*,expected_source_sha):
        assert frame.shape==(696,1)
        assert np.all(frame[:600]==0)
        assert np.all(frame[600:]==1)
        assert expected_source_sha=='z'*64
        name=Path(path).stem
        calls.append(name)
        return {k:np.full(696,i,dtype=int) for i,k in enumerate(payoff.PAYOFFS)}
    monkeypatch.setattr(lab.payoff,'_replay_checkpoint',fake_replay)
    predictions=lab._payoff_replay(source)
    assert set(calls)==set(payoff.PAYOFFS)
    assert all(len(v)==696 for v in predictions.values())


def test_sha_change_in_payoff_archive_refuses_replay(tmp_path):
    old=tmp_path/'old.npz';new=tmp_path/'new.npz'
    old.write_bytes(b'altered');new.write_bytes(b'archive')
    source={'composition':{'upstream':{
       'precision':{'archive':old,'report':{'fresh_feature_archive':{'sha256':'a'*64}}},
       'report':{'challenge_feature_archive':{'path':str(new),'sha256':'b'*64}}}}}
    with pytest.raises(ValueError,match='INPUT_ARCHIVES_CHANGED'):
        lab._payoff_replay(source)


def test_nan_prototype_cannot_be_silently_used_as_digit():
    rows,frames,_=_fixture()
    bank=lab._training_bank(rows,frames,np.arange(10))
    bank['centroids'][0,0]=np.nan
    decoder=lab._candidate(bank,'exact_centroid')
    # Never let NaNs choose a class by argmin accident.
    with pytest.raises(ValueError,match='NONFINITE_DIGIT_FEATURE'):
        lab._infer(rows,frames,decoder)
