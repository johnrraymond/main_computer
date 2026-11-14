"""Bounded frozen-representation constructor: role labels, isolation, exact handoff.

The tests use synthetic vectors to test head machinery. They do NOT assert
Qwen representation sufficiency; the user's CUDA run must measure that.
"""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_construction_head as c
from tools import nanojev_gamer_v1_strategic_execution as solver


def test_corpus_has_disjoint_writing_templates_and_exact_generated_source_roles():
    rows=c.generate(samples_per_template=4,seed=47)
    assert len(rows)==64
    train=[r for r in rows if r['split']=='train']
    challenge=[r for r in rows if r['split']=='challenge']
    assert len(train)==40 and len(challenge)==24
    assert {r['template_id'] for r in train}==set(range(10))
    assert {r['template_id'] for r in challenge}==set(range(6))
    assert set(r['prompt'] for r in train).isdisjoint(r['prompt'] for r in challenge)
    for r in rows:
        assert set(x[2] for x in r['spans'])==set(c.ROLES)
        assert len(c.NUMERIC.findall(r['prompt']))==5
        for start,end,role,value in r['spans']:
            assert r['prompt'][start:end]==value==r['values'][role]


def test_original_prompt_is_part_of_challenge_and_never_labeled_during_query():
    original=("Two competing players may cooperate or defect. Mutual cooperation pays each player 4. "
        "If one defects while the other cooperates, the defector gets 6 and the cooperator gets 0. "
        "Mutual defection pays each 2. Now impose a penalty of 3 on any player who defects. "
        "What is each player's dominant strategy before and after the penalty?")
    r=c.render(c.TEST_TEMPLATES[0],dict(cc=4,dc=6,cd=0,dd=2,penalty=3))
    assert r['prompt']==original
    assert {key:value for _s,_e,key,value in r['spans']}==dict(cc='4',dc='6',cd='0',dd='2',penalty='3')


@pytest.mark.parametrize('penalty,expected', [('1','defect'),('2',None),('3','cooperate'),('30','cooperate')])
def test_constructed_game_is_exact_with_penalty_counterfactuals(penalty,expected):
    values=dict(cc='4',dc='6',cd='0',dd='2',penalty=penalty)
    spec=c.construct_candidate(values)
    decision=solver.execute(c.canonical(spec))
    assert decision['status']=='BOUNDED_EXACT_TWO_PLAYER_DECISION_COMPUTED'
    assert decision['strategic_decision']['before']['player_1']['action']=='defect'
    assert decision['strategic_decision']['after']['player_1']['action']==expected
    assert decision['strategic_decision']['after']['player_2']['action']==expected
    assert decision['original_payoff_table']==[[['4','4'],['0','6']], [['6','0'],['2','2']]]


def test_negative_zero_fractional_operands_are_preserved_not_rounded():
    values=dict(cc='1/3',dc='-2/3',cd='0',dd='-7/3',penalty='0')
    out=solver.execute(c.canonical(c.construct_candidate(values)))
    assert out['original_payoff_table'][0][0][0]=='1/3'
    assert out['original_payoff_table'][1][0][0]=='-2/3'
    assert out['original_payoff_table'][1][1][0]=='-7/3'
    assert out['penalty']['amount']=='0'
    assert all(Fraction(s['left'])-Fraction(s['right'])==Fraction(s['result'])
               for s in out['arithmetic_trace'])


def test_bind_spans_fail_closed_on_missing_extra_ambiguous_labels_and_low_confidence():
    prompt=c.render(c.TEST_TEMPLATES[0],dict(cc=4,dc=6,cd=0,dd=2,penalty=3))['prompt']
    good=[{'role':role,'confidence':0.99} for role in ('cc','dc','cd','dd','penalty')]
    assert c.bind_spans(prompt,good,min_confidence=.8)['values']==dict(cc='4',dc='6',cd='0',dd='2',penalty='3')
    assert c.bind_spans(prompt,good[:-1],min_confidence=.8)['status']=='ABSTAIN_NUMERIC_SLOT_COUNT'
    assert c.bind_spans(prompt.replace('penalty of 3','penalty of 3 plus 5'),good,min_confidence=.8)['status']=='ABSTAIN_NUMERIC_SLOT_COUNT'
    duplicate=[dict(x) for x in good];duplicate[2]['role']='cc'
    assert c.bind_spans(prompt,duplicate,min_confidence=.8)['status']=='ABSTAIN_MISSING_OR_DUPLICATED_ROLE'
    low=[dict(x) for x in good]; low[1]['confidence']=.79
    assert c.bind_spans(prompt,low,min_confidence=.8)['status']=='ABSTAIN_LOW_SLOT_CONFIDENCE'


def test_training_weights_are_fitted_only_from_train_not_challenge():
    # This is a synthetic representation-sufficiency control, not a Qwen pass.
    rng=np.random.default_rng(7)
    features=[];labels=[]
    for repeat in range(30):
        for index,role in enumerate(c.ROLES):
            vec=np.eye(5,dtype=np.float32)[index]+rng.normal(0,.01,5).astype(np.float32)
            features.append(vec);labels.append(role)
    weights=c._fit(features,labels,epochs=12,seed=47)
    blind=c.predict(weights,np.eye(5,dtype=np.float32))
    assert [r['role'] for r in blind]==list(c.ROLES)
    assert all(r['confidence']>.8 for r in blind)


def test_full_train_saves_separate_checkpoint_and_query_uses_it(monkeypatch,tmp_path):
    # Fake precomputed frozen activations test the I/O path only. Any real Qwen
    # competence still requires CUDA feature capture and a genuinely blind run.
    root=tmp_path/'source';root.mkdir()
    dest=tmp_path/'head'
    monkeypatch.setattr(c,'_adapter_identity',lambda _run:(root,'frozen-adapter-sha'))
    def fake_capture(_model,_run,_device,_layer,rows,*,max_length):
        features=[]
        for row in rows:
            spans=row.get('spans')
            assert spans is not None  # only training path exercised here
            for _start,_end,role,value in spans:
                features.append((np.eye(len(c.ROLES),dtype=np.float32)[c.ROLES.index(role)],role,value))
        return features,'frozen-adapter-sha'
    monkeypatch.setattr(c,'_capture',fake_capture)
    result=c.train(run=root,output=dest,device='cpu',samples_per_template=4,epochs=20)
    assert result['no_lora_training'] and result['no_production_authority_change']
    assert result['metrics']['challenge_exact_game_construction']==24
    assert result['metrics']['position_only_baseline_exact_game_rate'] < 0.5
    assert result['metrics']['numeric_value_only_baseline_exact_game_rate'] < 0.5
    assert (dest/'constructor_head.npz').is_file()
    assert (dest/'final_report.json').is_file()
    before=c.sha_file(dest/'constructor_head.npz')
    with pytest.raises(FileExistsError):
        c.train(run=root,output=dest,device='cpu',samples_per_template=4,epochs=20)
    assert before==c.sha_file(dest/'constructor_head.npz')
    with np.load(dest/'constructor_head.npz',allow_pickle=False) as arrays:
        assert json.loads(str(arrays['meta_json']))['adapter_sha256']=='frozen-adapter-sha'
    # Refuse stale/tampered state before loading frozen Qwen.
    (dest/'constructor_head.npz').write_bytes(b'not a checkpoint')
    with pytest.raises(ValueError,match='LINEAGE_MISMATCH'):
        c.query(run=root,output=dest,prompt='dummy',device='cpu')


def test_query_ignores_original_parser_and_never_claims_neural_proof(monkeypatch,tmp_path):
    root=tmp_path/'source';root.mkdir()
    dest=tmp_path/'head';dest.mkdir()
    prompt=c.render(c.TEST_TEMPLATES[0],dict(cc=4,dc=6,cd=0,dd=2,penalty=3))['prompt']
    weights={
        'mean':np.zeros(5,dtype=np.float32),
        'scale':np.ones(5,dtype=np.float32),
        'coef':np.eye(5,dtype=np.float32)*100,
        'intercept':np.zeros(5,dtype=np.float32),
    }
    meta={'schema':c.SCHEMA,'model_id':'test','run':str(root.resolve()),
          'adapter_sha256':'frozen-sha','layer':9,'max_length':256,'corpus_sha256':'x'}
    checkpoint=dest/'constructor_head.npz'
    np.savez_compressed(checkpoint,**weights,meta_json=c.canonical(meta))
    receipt=dict(meta,checkpoint_sha256=c.sha_file(checkpoint))
    (dest/'final_report.json').write_text(json.dumps(receipt))
    def fake_capture(_model,_run,_device,_layer,rows,*,max_length):
        detected=list(c.NUMERIC.finditer(rows[0]['prompt']))
        assert len(detected)==5
        return [(np.eye(5,dtype=np.float32)[i],None,m.group()) for i,m in enumerate(detected)],'frozen-sha'
    monkeypatch.setattr(c,'_capture',fake_capture)
    monkeypatch.setattr(c,'_adapter_identity',lambda _run:(root,'frozen-sha'))
    original_parse=solver.parse_game
    def assert_json_only(game_text):
        assert game_text.lstrip().startswith('{'), 'English grammar used instead of constructed game'
        return original_parse(game_text)
    monkeypatch.setattr(solver,'parse_game',assert_json_only)
    out=c.query(run=root,output=dest,prompt=prompt,device='cpu')
    assert out['status']=='CANDIDATE_LEARNED_GAME_CONSTRUCTED_EXACTLY_SOLVED'
    assert out['model_checkpoint_used_for_construction'] is True
    assert out['original_english_parser_used'] is False
    assert out['strategic_decision']['after']['player_1']['action']=='cooperate'
    assert out['claim_of_certified_strategic_generalization'] is False
    assert out['decision_certification']=='NOT_CERTIFIED_WITHOUT_INDEPENDENT_GAME_ORACLE'


def test_numerical_formats_do_not_encode_payoff_roles():
    rows=c.generate(samples_per_template=24,seed=43)
    per={name:set() for name in c.ROLES}
    for row in rows:
        for _s,_e,name,value in row['spans']:
            if '/' in value: per[name].add('fraction')
            elif '.' in value: per[name].add('decimal')
            elif value.startswith('-'): per[name].add('negative')
            else: per[name].add('integer')
    assert all({'fraction','decimal','integer'} <= seen for seen in per.values())
    assert all('negative' in per[name] for name in c.ROLES if name!='penalty')


def test_frozen_qwen_capture_aligns_numeric_spans_without_reading_gold_roles(monkeypatch,tmp_path):
    import torch
    from types import SimpleNamespace
    class TinyTokenizer:
        is_fast=True
        def __call__(self,text,**kwargs):
            assert kwargs['return_offsets_mapping'] is True
            assert kwargs['truncation'] is False
            # Synthetic fast character tokenizer, not a production Qwen tokenizer.
            return {'input_ids':[ord(char)%41+1 for char in text],
                    'offset_mapping':[(i,i+1) for i in range(len(text))]}
    class TinyFrozenModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weights=torch.nn.Parameter(torch.ones(2),requires_grad=False)
        def forward(self,input_ids,attention_mask,output_hidden_states,use_cache,return_dict):
            assert not torch.is_grad_enabled()
            values=input_ids.float()[0]
            h=torch.stack((values,values+1),dim=1).unsqueeze(0)
            return SimpleNamespace(hidden_states=tuple(h for _ in range(10)))
    from tools import nanojev_gamer_v1_handoff as handoff
    monkeypatch.setattr(c,'_adapter_identity',lambda _run:(tmp_path,'verified-adapter-sha'))
    monkeypatch.setattr(handoff,'model_components',lambda _model,_device,adapter_dir:
                      (torch,TinyTokenizer(),TinyFrozenModel()))
    row=c.render(c.TEST_TEMPLATES[0],dict(cc=4,dc=6,cd=0,dd=2,penalty=3))
    labeled,sha=c._capture('fake',tmp_path,'cpu',9,[row],max_length=1024)
    unlabeled,sha2=c._capture('fake',tmp_path,'cpu',9,[{'prompt':row['prompt']}],max_length=1024)
    assert sha==sha2=='verified-adapter-sha'
    assert [part[1] for part in labeled]==[span[2] for span in row['spans']]
    assert all(np.array_equal(a[0],b[0]) for a,b in zip(labeled,unlabeled))


def test_negative_payoff_misbinding_as_penalty_abstains_without_changing_source_numbers():
    """A high-confidence wrong role on an entirely legal game is not fatal."""
    row=c.render(c.TEST_TEMPLATES[0],dict(cc='-4',dc='6',cd='0',dd='2',penalty='3'))
    # Swap the predicted CC and penalty labels; every role remains unique and
    # confidence is high. Previously this reached construct_candidate() and
    # aborted training with GAMER_CONSTRUCTION_NEGATIVE_PENALTY.
    predictions=[{'role':{'cc':'penalty','penalty':'cc'}.get(role,role),
                  'confidence':0.99} for _start,_end,role,_value in row['spans']]
    for confidence in (0,0.8):
        got=c.bind_spans(row['prompt'],predictions,min_confidence=confidence)
        assert got=={'status':'ABSTAIN_NEGATIVE_PENALTY_ASSIGNMENT','values':None}
    with pytest.raises(ValueError,match='GAMER_CONSTRUCTION_NEGATIVE_PENALTY'):
        c.construct_candidate(dict(cc='3',dc='6',cd='0',dd='2',penalty='-4'))
    original_roles=[s[2] for s in row['spans']]
    assert original_roles==['cc','dc','cd','dd','penalty']
    assert [s[3] for s in row['spans']]==['-4','6','0','2','3']


def test_malformed_numeric_slot_abstains_instead_of_reaching_solver():
    prompt=c.render(c.TEST_TEMPLATES[0],dict(cc='4',dc='6',cd='0',dd='2',penalty='3'))['prompt']
    prompt=prompt.replace('pays each player 4', 'pays each player 1/0')
    predictions=[{'role':role,'confidence':0.99} for role in c.ROLES]
    assert c.bind_spans(prompt,predictions,min_confidence=0.8)=={
        'status':'ABSTAIN_INVALID_PAYOFF_NUMBER','values':None}


def test_training_report_counts_invalid_constructed_penalty_as_error_not_exception(monkeypatch):
    # There is still a correct gold label; the model's wrong interpretation
    # cannot be silently repaired with the gold role or assumed correct.
    train=c.render(c.TRAIN_TEMPLATES[0],dict(cc='4',dc='6',cd='0',dd='2',penalty='3'))
    train['split']='train'
    challenge=c.render(c.TEST_TEMPLATES[0],dict(cc='-4',dc='6',cd='0',dd='2',penalty='3'))
    challenge['split']='challenge'
    wrong=[{'role':{'cc':'penalty','penalty':'cc'}.get(role,role),
            'confidence':0.99} for _start,_end,role,_value in challenge['spans']]
    def forbidden_solver(*_args,**_kwargs):
        raise AssertionError('a semantically invalid construction reached exact execution')
    monkeypatch.setattr(solver,'execute',forbidden_solver)
    report=c.training_report(seed=47,samples_per_template=4,
                             rows=[train,challenge],prediction_results=[wrong],corpus_sha='test')
    assert report['challenge_examples']==1
    assert report['challenge_role_exact']==0
    assert report['challenge_exact_game_construction']==0
    assert report['challenge_confidence_gated_accepts']==0
    assert report['challenge_confidence_gated_error_count']==0
    assert report['challenge_negative_penalty_assignment_rejections']==1
    assert report['challenge_raw_candidate_status_counts']=={
        'ABSTAIN_NEGATIVE_PENALTY_ASSIGNMENT':1}
    assert report['challenge_confidence_gated_status_counts']=={
        'ABSTAIN_NEGATIVE_PENALTY_ASSIGNMENT':1}


def test_blind_query_abstains_on_high_confidence_negative_penalty_assignment(monkeypatch,tmp_path):
    root=tmp_path/'source';root.mkdir()
    dest=tmp_path/'head';dest.mkdir()
    row=c.render(c.TEST_TEMPLATES[0],dict(cc='-4',dc='6',cd='0',dd='2',penalty='3'))
    weights={'mean':np.zeros(5,dtype=np.float32),'scale':np.ones(5,dtype=np.float32),
             'coef':np.eye(5,dtype=np.float32)*100,
             'intercept':np.zeros(5,dtype=np.float32)}
    meta={'schema':c.SCHEMA,'model_id':'test','run':str(root.resolve()),
          'adapter_sha256':'frozen-sha','layer':9,'max_length':256,'corpus_sha256':'x'}
    checkpoint=dest/'constructor_head.npz'
    np.savez_compressed(checkpoint,**weights,meta_json=c.canonical(meta))
    (dest/'final_report.json').write_text(json.dumps(dict(meta,checkpoint_sha256=c.sha_file(checkpoint))))
    # A simulated frozen encoder assigns the negative CC operand the penalty
    # role and the positive penalty operand the CC role, with confidence ~1.
    def wrong_features(_model,_run,_device,_layer,rows,*,max_length):
        spans=list(c.NUMERIC.finditer(rows[0]['prompt']))
        roles=['penalty','dc','cd','dd','cc']
        return [(np.eye(5,dtype=np.float32)[c.ROLES.index(role)],None,m.group())
                for role,m in zip(roles,spans)],'frozen-sha'
    monkeypatch.setattr(c,'_capture',wrong_features)
    monkeypatch.setattr(c,'_adapter_identity',lambda _run:(root,'frozen-sha'))
    monkeypatch.setattr(solver,'execute',lambda *_a,**_k: (_ for _ in ()).throw(
        AssertionError('invalid game must not reach solver')))
    result=c.query(run=root,output=dest,prompt=row['prompt'],device='cpu')
    assert result['status']=='ABSTAIN_NEGATIVE_PENALTY_ASSIGNMENT'
    assert result['strategic_decision'] is None
    assert result['model_checkpoint_used_for_construction'] is True
    assert result['production_authority_changed'] is False
