"""V40 actor-ownership falsification; never opens sealed test variants."""
from __future__ import annotations

import inspect
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_v40_actor_grounding as v40
from tools import nanojev_gamer_v1_v38_interacting_role_grounding as v38
from tools import nanojev_gamer_v1_v33_game_learning as v33
from tools import nanojev_gamer_v1_v34_grounded_game_learning as v34


def deck():
    return v40.fit_partition(v40.make_deck('fit', 40))


def test_train_dev_dataset_balanced_across_all_four_causal_interventions():
    fit, dev = deck()
    assert len(fit) == 480 and len(dev) == 120
    assert not {r['pair'] for r in fit} & {r['pair'] for r in dev}
    assert Counter(r['intervention'] for r in fit) == {k: 120 for k in ('belief','ours','theirs','actor_exchange')}
    assert Counter(r['intervention'] for r in dev) == {k: 30 for k in ('belief','ours','theirs','actor_exchange')}
    assert {r['variant'] for r in fit} == set(range(15))
    assert {r['variant'] for r in dev} == set(range(15))
    assert len({r['prompt'] for r in fit + dev}) == 600
    # Original task sizes and gates remain identical to V38.
    for field in ('DEV_STRUCT_MIN','DEV_DECISION_MIN','DEV_PAIR_MIN','TEST_STRUCT_MIN',
                  'TEST_DECISION_MIN','TEST_PAIR_MIN','PER_VARIANT_MIN'):
        assert getattr(v40,field) == getattr(v38,field)
    assert v40.capture.__code__.co_code == v38.capture.__code__.co_code


def test_counterfactual_pairs_have_identical_numbers_but_different_meaning():
    fit, dev = deck()
    different_actions = 0
    for a,b in zip((fit + dev)[::2],(fit + dev)[1::2]):
        assert a['pair'] == b['pair']
        ints_a, p_a = v40.transcribe(a['prompt'])
        ints_b, p_b = v40.transcribe(b['prompt'])
        assert [x['value'] for x in ints_a] == [x['value'] for x in ints_b]
        assert p_a['value'] == p_b['value']
        if a['intervention'] == 'belief':
            assert a['roles'] == b['roles'] and a['q_side'] != b['q_side']
        else:
            assert a['q_side'] == b['q_side']
            for old, new in zip(a['roles'], b['roles']):
                expected = v40.ROLE_CHOICES.index(v40.transform_role(v40.ROLE_CHOICES[old], a['intervention']))
                assert new == expected
        if a['label'] != b['label']:
            different_actions += 1
        if a['family'] == 'forced_reversal':
            assert a['label'] != b['label']
    assert different_actions >= 100
    # Any model that sees only the digits can get at most this many right.
    assert 600 - different_actions < 500


@pytest.mark.parametrize(('kind','mapping'), [
    ('ours', {'CC':'DC','CD':'DD','DC':'CC','DD':'CD'}),
    ('theirs', {'CC':'CD','CD':'CC','DC':'DD','DD':'DC'}),
    ('actor_exchange', {'CC':'CC','CD':'DC','DC':'CD','DD':'DD'}),
])
def test_actor_transform_is_a_bijection_with_correct_participant_ownership(kind,mapping):
    assert {r:v40.transform_role(r,kind) for r in v40.ROLE_CHOICES} == mapping
    assert len(set(mapping.values())) == 4


def test_ambiguous_action_aliases_are_defined_in_prompt_itself():
    fit,dev=deck()
    assert all('Here cooperation, sharing, deescalation and Yield all denote C;' in row['prompt']
               and 'defection, claiming, challenge and Contest all denote D.' in row['prompt']
               for row in fit + dev)
    assert any('When we Contest and the rival Yield' in r['prompt']
               and 'C (share)' in r['prompt'] for r in fit + dev)
    assert all(len(v40.transcribe(r['prompt'])[0])==4 for r in fit + dev)


def test_actor_role_logits_mean_own_then_rival_not_the_reverse():
    ours=torch.tensor([[[9.,0.], [0.,8.], [4.,0.], [0.,6.]]])
    other=torch.tensor([[[0.,5.], [7.,0.], [4.,0.], [0.,6.]]])
    role=v40.actor_role_logits(torch,ours,other)
    assert role.shape==(1,4,4)
    assert torch.argmax(role,dim=-1).tolist()==[[1,2,0,3]]
    assert v40.assign_roles(role.detach().numpy())==[(1,2,0,3)]
    torch.testing.assert_close(role[...,1]-role[...,2],
                               ours[...,0]-ours[...,1] + other[...,1]-other[...,0])
    with pytest.raises(ValueError,match='BAD_ACTOR_LOGITS'):
        v40.actor_role_logits(torch,torch.zeros((1,4,3)),other)


def test_head_is_equivariant_and_actor_outputs_change_with_clause_input():
    torch.manual_seed(401)
    m=v40.head(torch,32).eval()
    x=torch.randn(2,4,32,requires_grad=True)
    q=torch.randn(2,32)
    ids=torch.tensor([2,0,3,1])
    role,side,ours,other=m(x,q,with_actor=True)
    rp,sp,op,tp=m(x[:,ids],q,with_actor=True)
    torch.testing.assert_close(rp,role[:,ids],atol=1e-5,rtol=1e-5)
    torch.testing.assert_close(op,ours[:,ids],atol=1e-5,rtol=1e-5)
    torch.testing.assert_close(tp,other[:,ids],atol=1e-5,rtol=1e-5)
    torch.testing.assert_close(side,sp,atol=1e-6,rtol=1e-6)
    grad=torch.autograd.grad(ours[0,0,0],x,retain_graph=True)[0]
    assert grad[0,1:].abs().sum()>1.e-8
    grad2=torch.autograd.grad(other[0,0,0],x)[0]
    assert grad2[0,1:].abs().sum()>1.e-8


def test_own_and_rival_heads_receive_distinct_gradient_targets():
    torch.manual_seed(400)
    m=v40.head(torch,32)
    x=torch.randn((6,4,32));q=torch.randn((6,32))
    logits,belief,ours,theirs=m(x,q,with_actor=True)
    truth=torch.tensor([[0,1,2,3]]*6)
    loss=(torch.nn.functional.cross_entropy(ours.flatten(0,1),truth.flatten()//2)
          +torch.nn.functional.cross_entropy(theirs.flatten(0,1),truth.flatten()%2))
    loss.backward()
    assert m.ours[-1].weight.grad.abs().sum()>0
    assert m.rival[-1].weight.grad.abs().sum()>0
    assert not torch.equal(m.ours[-1].weight.grad,m.rival[-1].weight.grad)


def test_predictor_does_not_read_semantic_oracle_or_teacher(monkeypatch):
    fit,_=deck()
    def disallowed(*args,**kwargs):
        raise AssertionError('label or surface oracle entered prediction')
    monkeypatch.setattr(v33,'exact_teacher',disallowed)
    monkeypatch.setattr(v34,'ground',disallowed)
    monkeypatch.setattr(v40,'_surface',disallowed)
    monkeypatch.setattr(v40,'_belief',disallowed)
    monkeypatch.setattr(v40,'make_deck',disallowed)
    monkeypatch.setattr(v40,'transform_role',disallowed)
    m=v40.head(torch,32).eval()
    x=np.random.default_rng(2).normal(size=(1,4,32)).astype('float32')
    q=np.random.default_rng(3).normal(size=(1,32)).astype('float32')
    roles,sides=v40.predict_slots(m,x,q)
    assert sorted(roles[0])==[0,1,2,3]
    y=v40.numeric_input(fit[0]['prompt'],roles[0],sides[0])
    assert y.shape==(5,)
    assert np.isfinite(y).all()


def test_checkpoint_gate_failure_never_reads_holdout(tmp_path,monkeypatch):
    lineage={'model_id':'fake-qwen','source_files':{},'base_activation':'frozen-base'}
    monkeypatch.setattr(v33,'activation',lambda project:lineage)
    monkeypatch.setattr(v33,'verify_activation',lambda p,l:l)
    monkeypatch.setattr(v33,'model_context',lambda model,device:(torch,object(),object()))
    monkeypatch.setattr(v40,'_load_v34',lambda root,project:({},v34.head(torch)))
    monkeypatch.setattr(v40,'capture',lambda m,t,th,prompts,**kw:(
        np.ones((len(prompts),4,8),dtype=np.float32),
        np.ones((len(prompts),8),dtype=np.float32)))
    monkeypatch.setattr(v40,'fit_head',lambda *a,**kw:(v40.head(torch,8).state_dict(),[],(0,0,0),
          ([(0,1,2,3)]*120,[0]*120,[0]*120)))
    baseline=tmp_path/'v34';baseline.mkdir()
    (baseline/'grounded_head.pt').write_bytes(b'unchanged')
    (baseline/'train_manifest.json').write_text('{}')
    out=tmp_path/'v40'
    result=v40.train(out,project=tmp_path,v34_root=baseline,device='cpu')
    assert result['status']=='FAIL_DEV_HOLDOUT_STILL_SEALED'
    assert result['holdout_evaluated'] is False
    assert result['production_modified'] is False
    assert not (out/'holdout_consumed.json').exists()
    assert not (out/'holdout_deck.json').exists()
    assert not (out/'holdout_acceptance.json').exists()
    manifest,_=v40.load_head(out,tmp_path)
    assert manifest['role_training_rule']=='equivariant-cross-clause-actor-bits-plus-permutation-ce'
    assert manifest['oracle_prediction'] is False
    with pytest.raises(ValueError,match='DEV_GATE_FAILED_HOLDOUT_SEALED'):
        v40.audit(out,project=tmp_path,device='cpu')
    (out/'dev_failure_diagnostics.json').write_text('{}')
    with pytest.raises(ValueError,match='DEV_DIAGNOSTICS_CHANGED'):
        v40.load_head(out,tmp_path)


def test_trainable_optimizer_uses_real_actor_supervision_and_checkpoint():
    # No Qwen required in this test. It checks gradient flow through the true
    # production optimizer, not generalization or any sealed result.
    fit,dev=deck()
    rng=np.random.default_rng(40)
    x=rng.standard_normal((480,4,8)).astype('float32')
    q=rng.standard_normal((480,8)).astype('float32')
    dx=rng.standard_normal((120,4,8)).astype('float32')
    dq=rng.standard_normal((120,8)).astype('float32')
    torch.set_num_threads(1)
    state,history,best,pred=v40.fit_head(x,q,fit,dx,dq,dev,v34.head(torch),epochs=2,seed=40)
    assert len(history)==2
    assert all(torch.isfinite(w).all() for w in state.values())
    assert len(pred[0])==120


def test_no_lexical_role_dictionary_in_prediction_functions():
    for func in (v40.transcribe,v40.capture,v40.head,v40.predict_slots,
                 v40.numeric_input,v40.run_decisions,v40.infer):
        source=inspect.getsource(func)
        assert 'exact_teacher(' not in source
        assert 'transform_role(' not in source
        assert '_surface(' not in source
        assert 'make_deck(' not in source
    assert 'forced_reversal' in inspect.getsource(v40.make_deck)


def test_live_inference_does_not_use_label_or_game_role_parser(monkeypatch,tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('teacher accessed live prediction')
    monkeypatch.setattr(v40,'make_deck',forbidden)
    monkeypatch.setattr(v40,'_surface',forbidden)
    monkeypatch.setattr(v40,'_belief',forbidden)
    monkeypatch.setattr(v40,'transform_role',forbidden)
    monkeypatch.setattr(v33,'exact_teacher',forbidden)
    monkeypatch.setattr(v34,'ground',forbidden)
    monkeypatch.setattr(v40,'load_head',lambda root,project,audit_required: (
        {'model_id':'fake-qwen','lineage':'test-lineage','v34_root':'fake'},
        v40.head(torch,8).eval()))
    monkeypatch.setattr(v40,'_load_v34',lambda root,project:({},v34.head(torch)))
    monkeypatch.setattr(v33,'model_context',lambda model_id,device:(torch,object(),object()))
    monkeypatch.setattr(v33,'verify_activation',lambda project,lineage:lineage)
    monkeypatch.setattr(v40,'capture',lambda model,tok,t,prompts,**kwargs:(
        np.zeros((len(prompts),4,8),np.float32),np.zeros((len(prompts),8),np.float32)))
    p=('Two actors choose C or D. CC=1. CD=3. DC=-4. DD=2. '
       'The rival chooses D with probability 3/4.')
    result=v40.infer(tmp_path,p,project=tmp_path,device='cpu')
    assert result['action'] in v34.CHOICES
    assert sorted(result['role_assignment'])==[0,1,2,3]
    assert result['production_modified'] is False


def test_one_shot_audit_marker_precedes_any_holdout_generation(monkeypatch,tmp_path):
    run=tmp_path/'v40';run.mkdir()
    monkeypatch.setattr(v40,'load_head',lambda root,project:({'dev_pass':True,'seed':40},object()))
    def inspect_holdout(split,seed):
        assert split=='holdout' and seed==40
        assert (run/'holdout_consumed.json').is_file()
        raise RuntimeError('halt immediately after proven seal opening')
    monkeypatch.setattr(v40,'make_deck',inspect_holdout)
    (run/'train_manifest.json').write_text('{}')
    (run/'semantic_head.pt').write_bytes(b'unchanged')
    with pytest.raises(RuntimeError,match='proven seal opening'):
        v40.audit(run,project=tmp_path,device='cpu')
    with pytest.raises(ValueError,match='HOLDOUT_ALREADY_OPENED'):
        v40.audit(run,project=tmp_path,device='cpu')
