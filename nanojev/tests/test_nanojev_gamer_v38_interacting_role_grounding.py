"""V38 falsifiability and read-only integration tests (not CUDA certification)."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_v38_interacting_role_grounding as v38
from tools import nanojev_gamer_v1_v33_game_learning as v33
from tools import nanojev_gamer_v1_v34_grounded_game_learning as v34


def fit_deck():
    return v38.fit_partition(v38.make_deck('fit',35))


def test_minimal_pairs_same_numbers_different_semantics_and_predictions():
    fit,dev=fit_deck()
    assert len(fit)==480 and len(dev)==120
    assert {r['pair'] for r in fit}.isdisjoint({r['pair'] for r in dev})
    assert {r['intervention'] for r in fit}=={'belief','role'}
    reversed_count=0
    for a,b in zip((fit+dev)[::2],(fit+dev)[1::2]):
        assert a['pair']==b['pair']
        ia,qa=v38.transcribe(a['prompt'])
        ib,qb=v38.transcribe(b['prompt'])
        assert [i['value'] for i in ia]==[i['value'] for i in ib]
        assert qa['value']==qb['value']
        if a['intervention']=='belief':
            assert a['roles']==b['roles'] and a['q_side']!=b['q_side']
        else:
            assert a['roles']!=b['roles'] and a['q_side']==b['q_side']
        if a['label']!=b['label']:
            reversed_count+=1
    assert reversed_count>=150  # one or both semantic interventions must matter
    assert len({r['prompt'] for r in fit+dev})==600


def test_numeric_only_oracle_free_transcription_and_false_semantic_controls():
    prompt=('A team chooses C or D. CC=0. CD=0. DC=10. DD=-10. '
            'The opponent chooses C with probability 3/4.')
    changed=prompt.replace('chooses C with probability','chooses D with probability')
    a,p=v38.transcribe(prompt)
    b,q=v38.transcribe(changed)
    assert [i['value'] for i in a]==[i['value'] for i in b]
    assert p['value']==q['value']
    # Inference must be sensitive to a learned q-side prediction.
    same_role=(0,1,2,3)
    x1=v38.numeric_input(prompt,same_role,0)
    x2=v38.numeric_input(changed,same_role,1)
    np.testing.assert_array_equal(x1[:4],x2[:4])
    assert x1[4]==-x2[4]
    # And similarly to a *learned* swap of the CC/DC roles.
    x3=v38.numeric_input(prompt,(2,1,0,3),0)
    assert x1[0]==x3[2] and x1[2]==x3[0]
    assert not np.array_equal(x1,x3)


@pytest.mark.parametrize('bad', [
    'No numbers here.',
    'CC=1; CD=2; DC=3; DD=4; chance 3/4; stray payoff 9.',
    'CC=1; CD=2; DC=3; DD=4; chance 0/0.',
    'CC=1; CD=2; DC=3; DD=4; chance 7/4.',
    'CC=1; CD=2; DC=3; DD=4; no rational probability.',
    'CC=1; CD=2; DC=3; DD=4; chance 3/4; chance 1/4.',
])
def test_transcriber_fails_closed(bad):
    with pytest.raises(ValueError, match='GAMER_V38_'):
        v38.transcribe(bad)


def test_numeric_transcriber_never_calls_role_parser_or_label_oracle(monkeypatch):
    fit,_=fit_deck()
    prompt=fit[0]['prompt']
    def explode(*_args,**_kw):
        raise AssertionError('forbidden in semantic inference')
    monkeypatch.setattr(v33,'exact_teacher',explode)
    monkeypatch.setattr(v34,'ground',explode)
    ints,q=v38.transcribe(prompt)
    assert len(ints)==4
    np.testing.assert_array_equal(v38.numeric_input(prompt,(0,1,2,3),0).shape,(5,))
    assert 0<q['value']<1
    fake=v34.head(torch)
    guesses=v38.run_decisions(fake,[{'prompt':prompt}],[(0,1,2,3)],[0])
    assert len(guesses)==1 and guesses[0] in (0,1)


def test_assignment_is_structural_not_oracle():
    # A parser that assigns roles by numerical order will fail this test.
    logits=np.array([[[0,0,8,0],[0,8,0,0],[8,0,0,0],[0,0,0,8]]],dtype=np.float32)
    assert v38.assign_roles(logits)==[(2,1,0,3)]
    same=np.array([[[8,0,0,0],[0,8,0,0],[0,0,8,0],[0,0,0,8]]],dtype=np.float32)
    assert v38.assign_roles(same)==[(0,1,2,3)]
    with pytest.raises(ValueError,match='ROLE_LOGIT_SHAPE'):
        v38.assign_roles(np.zeros((1,3,4),np.float32))


class FakeTokenizer:
    def __call__(self, prompts, **_):
        # Character-offset tokenizer, deliberately without any semantic role.
        all_words=[list(re.finditer(r'\S+',p)) for p in prompts]
        maxn=max(len(ws) for ws in all_words)
        ids=np.zeros((len(prompts),maxn),dtype=np.int64)
        mask=np.zeros_like(ids)
        off=np.zeros((len(prompts),maxn,2),dtype=np.int64)
        for b,words in enumerate(all_words):
            for t,w in enumerate(words):
                ids[b,t]=(t*17+len(w.group())*13)%101 + 1
                mask[b,t]=1
                off[b,t]=w.span()
        return {'input_ids':torch.tensor(ids),'attention_mask':torch.tensor(mask),
                'offset_mapping':torch.tensor(off)}


class FakeModel:
    def eval(self):
        return self
    def __call__(self,input_ids, attention_mask, **_):
        batch,seq=input_ids.shape
        ids=input_ids.float()
        pos=torch.arange(seq)[None,:].expand(batch,-1).float()
        z=torch.stack((ids,pos,ids+pos,ids-pos,ids*2,1+pos,ids/101,pos/100),dim=-1)
        return SimpleNamespace(hidden_states=[z+(i/100.) for i in range(21)])


def test_contextual_capture_offsets_support_variable_padding_and_num_spans(monkeypatch):
    fit,_=fit_deck()
    prompts=[fit[0]['prompt'],fit[1]['prompt']]
    monkeypatch.setattr(v33,'exact_teacher',lambda *_: (_ for _ in ()).throw(AssertionError('oracle used')))
    x,q=v38.capture(FakeModel(),FakeTokenizer(),torch,prompts,device='cpu',batch_size=2)
    assert x.shape==(2,4,32) and q.shape==(2,32)
    assert np.isfinite(x).all() and np.isfinite(q).all()
    # This fake model ignores semantics; its identical embeddings are not evidence of learning.
    assert np.array_equal(x[0],x[1])


def test_left_and_right_padding_last_token():
    right=torch.tensor([[1,1,0,0],[1,1,1,0]])
    left=torch.tensor([[0,0,1,1],[0,1,1,1]])
    assert v38._last_valid_token(right).tolist()==[1,2]
    assert v38._last_valid_token(left).tolist()==[3,3]


def test_dev_does_not_consume_sealed_test_and_preserves_authority(tmp_path,monkeypatch):
    fit,dev=fit_deck()
    width=8
    def mock_capture(_model,_tok,_torch,prompts,**_):
        return np.ones((len(prompts),4,width),dtype=np.float32),np.ones((len(prompts),width),dtype=np.float32)
    def mock_fit(_x,_q,_fit,_dx,_dq,_dev,_numeric,**_):
        model=v38.head(torch,width)
        roles=[(0,1,2,3)]*len(dev)
        sides=[0]*len(dev)
        decisions=[0]*len(dev)
        return model.state_dict(),[],(0,0,0),(roles,sides,decisions)
    lineage={'model_id':'mock-qwen','source_files':{},'base_activation':'frozen-base'}
    monkeypatch.setattr(v33,'activation',lambda root:lineage)
    monkeypatch.setattr(v33,'verify_activation',lambda root,l:lineage)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device:(torch,object(),FakeModel()))
    monkeypatch.setattr(v38,'_load_v34',lambda root,project:({},v34.head(torch)))
    monkeypatch.setattr(v38,'capture',mock_capture)
    monkeypatch.setattr(v38,'fit_head',mock_fit)
    modelrun=tmp_path/'accepted-v34'
    modelrun.mkdir()
    (modelrun/'grounded_head.pt').write_bytes(b'fixed-numerical-model')
    (modelrun/'train_manifest.json').write_text('{}')
    out=tmp_path/'v38'
    result=v38.train(out,project=tmp_path,v34_root=modelrun,device='cpu')
    assert result['status']=='FAIL_DEV_HOLDOUT_STILL_SEALED'
    assert not (out/'holdout_consumed.json').exists()
    assert not (out/'holdout_acceptance.json').exists()
    manifest=v38.read(out/'train_manifest.json')
    assert manifest['frozen_qwen_used'] is True
    assert manifest['oracle_prediction'] is False
    assert manifest['numerical_semantic_parser_used'] is False
    assert result['production_modified'] is False
    # Loading the frozen head must not regenerate teacher-labelled decks.
    monkeypatch.setattr(v33,'exact_teacher',lambda *_: (_ for _ in ()).throw(AssertionError('oracle in loader')))
    restored,net=v38.load_head(out,tmp_path)
    assert restored['dev_pass'] is False
    assert isinstance(net,torch.nn.Module)
    with pytest.raises(ValueError,match='DEV_GATE_FAILED_HOLDOUT_SEALED'):
        v38.audit(out,project=tmp_path,device='cpu')
    assert not (out/'holdout_consumed.json').exists()


def test_saved_training_provenance_fingerprint_rejects_mutation(tmp_path,monkeypatch):
    # Fail-closed fingerprint check is independent of a user's claimed PASS.
    width=8
    monkeypatch.setattr(v33,'activation',lambda root:{'model_id':'fake','lineage':'fixed'})
    monkeypatch.setattr(v33,'verify_activation',lambda root,l:l)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device:(torch,object(),FakeModel()))
    monkeypatch.setattr(v38,'capture',lambda _model,_tok,_torch,prompts,**_: (
        np.ones((len(prompts),4,width),dtype=np.float32),
        np.ones((len(prompts),width),dtype=np.float32)))
    monkeypatch.setattr(v38,'_load_v34',lambda root,project:({},v34.head(torch)))
    monkeypatch.setattr(v38,'fit_head',lambda *args,**kw:(v38.head(torch,width).state_dict(),[],(0,0,0),
          ([(0,1,2,3)]*120,[0]*120,[0]*120)))
    baseline=tmp_path/'v34';baseline.mkdir()
    (baseline/'grounded_head.pt').write_bytes(b'baseline-frozen')
    (baseline/'train_manifest.json').write_text('{}')
    output=tmp_path/'v38'
    v38.train(output,project=tmp_path,v34_root=baseline,device='cpu')
    v38.load_head(output,tmp_path)
    (output/'training_data_provenance.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='TRAIN_PROVENANCE_CHANGED'):
        v38.load_head(output,tmp_path)


def test_clause_endpoint_is_punctuation_only_and_never_reads_next_payoff():
    prompt = ('Our points are 5 when our move is D and their move is C. '
              'Our points are 8 when our move is C and their move is D. '
              'Our points are -1 when our move is C and their move is C. '
              'Our points are 10 when our move is D and their move is D. '
              'The rival defects with probability 3/4; otherwise cooperates.')
    ints, q = v38.transcribe(prompt)
    boundaries=[v38.complete_clause_end(prompt, c['span'][1]) for c in ints]
    assert all(prompt[end-1]=='.' for end in boundaries)
    assert all(end < ints[i+1]['span'][0] for i,end in enumerate(boundaries[:-1]))
    assert v38.complete_clause_end(prompt,q['span'][1]) < len(prompt)
    tok=FakeTokenizer()
    offsets=tok([prompt])['offset_mapping'][0].tolist()
    mask=tok([prompt])['attention_mask'][0].tolist()
    for idx,span in enumerate(ints):
        numeric_ids=[j for j,(a,b) in enumerate(offsets)
                     if b>a and a<span['span'][1] and b>span['span'][0]]
        selected=v38.clause_token_index(prompt,span,offsets,mask,numeric_ids)
        assert offsets[selected][1] <= boundaries[idx]
        if idx < len(ints)-1:
            assert offsets[selected][1] < ints[idx+1]['span'][0]


def test_clause_endpoint_excludes_semantic_shortcut_and_fails_closed():
    a='Our payoff 7 when we cooperate and they defect. CC=1.'
    b='Our payoff 7 when we defect and they cooperate. CC=1.'
    assert v38.complete_clause_end(a,a.index('7')+1)==v38.complete_clause_end(b,b.index('7')+1)
    # The boundary is unchanged when only a role word changes. Qwen, not
    # punctuation logic, must resolve the new role meaning.
    with pytest.raises(ValueError,match='UNTERMINATED_NUMERIC_CLAUSE'):
        v38.complete_clause_end('5 role unknown',1)


def test_dev_failure_report_contains_only_dev_and_rejects_tampering(tmp_path, monkeypatch):
    width=8
    monkeypatch.setattr(v33,'activation',lambda root:{'model_id':'fake','lineage':'fixed'})
    monkeypatch.setattr(v33,'verify_activation',lambda root,l:l)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device:(torch,object(),FakeModel()))
    monkeypatch.setattr(v38,'capture',lambda _model,_tok,_torch,prompts,**_: (
        np.ones((len(prompts),4,width),dtype=np.float32),
        np.ones((len(prompts),width),dtype=np.float32)))
    monkeypatch.setattr(v38,'_load_v34',lambda root,project:({},v34.head(torch)))
    monkeypatch.setattr(v38,'fit_head',lambda *args,**kw:(v38.head(torch,width).state_dict(),[],(0,0,0),
          ([(0,1,2,3)]*120,[0]*120,[0]*120)))
    baseline=tmp_path/'v34';baseline.mkdir()
    (baseline/'grounded_head.pt').write_bytes(b'baseline-frozen')
    (baseline/'train_manifest.json').write_text('{}')
    output=tmp_path/'v38'
    v38.train(output,project=tmp_path,v34_root=baseline,device='cpu')
    detail=v38.read(output/'dev_failure_diagnostics.json')
    assert detail['scope']=='DEV_ONLY_NO_TEST_NO_PROMOTION'
    assert detail['v34_with_gold_dev_semantics_total']==120
    assert detail['failure_count']>0
    assert all(x['id'].startswith('fit.') for x in detail['failures'])
    v38.load_head(output,tmp_path)
    (output/'dev_failure_diagnostics.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='DEV_DIAGNOSTICS_CHANGED'):
        v38.load_head(output,tmp_path)


def test_joint_permutation_logits_equal_inference_criterion():
    # The training loss must optimize the exact structural assignment that is
    # selected by the production predictor, not an unrelated per-slot proxy.
    rng=np.random.default_rng(1234)
    logits=rng.normal(size=(31,4,4)).astype(np.float32)
    logits[0,:,:]=0.0
    actual=v38.permutation_logits(torch.from_numpy(logits)).numpy()
    assert actual.shape==(31,24)
    assert np.allclose(actual[:,0], logits[:,0,0]+logits[:,1,1]+logits[:,2,2]+logits[:,3,3])
    best=[v38._PERMUTATIONS[i] for i in actual.argmax(1)]
    assert best==v38.assign_roles(logits)


def test_joint_permutation_loss_has_no_oracle_answer_or_prompt_features():
    # Every gold permutation is a supervision target only. Score computation
    # accepts logits and has no access to prompts, roles, labels, or oracle.
    import torch.nn.functional as F
    truth=(2,0,3,1)
    score=torch.nn.Parameter(torch.zeros((1,4,4),dtype=torch.float32))
    optimizer=torch.optim.SGD([score],lr=1.5)
    idx=v38._PERMUTATION_INDEX[truth]
    start=F.cross_entropy(v38.permutation_logits(score),torch.tensor([idx])).item()
    for _ in range(12):
        optimizer.zero_grad()
        loss=F.cross_entropy(v38.permutation_logits(score),torch.tensor([idx]))
        loss.backward()
        optimizer.step()
    end=F.cross_entropy(v38.permutation_logits(score),torch.tensor([idx])).item()
    assert end < start/10
    assert v38.assign_roles(score.detach().numpy())==[truth]


def test_v38_dataset_and_context_capture_are_identical_to_v36():
    # Only the supervision objective changes; no remapped role meanings,
    # synthetic target changes, or dev/holdout redefinition.
    from tools import nanojev_gamer_v1_v36_clause_grounding as v36
    # Never construct the sealed holdout in a unit test.
    assert v38.digest(v38.make_deck('fit',35))==v36.digest(v36.make_deck('fit',35))
    assert v38.capture.__code__.co_code==v36.capture.__code__.co_code
    assert v38.complete_clause_end.__code__.co_code==v36.complete_clause_end.__code__.co_code
    assert v38.TRAIN_VARIANTS==v36.TRAIN_VARIANTS
    assert v38.TEST_VARIANTS==v36.TEST_VARIANTS
    assert v38.DEV_STRUCT_MIN==v36.DEV_STRUCT_MIN
    assert v38.DEV_PAIR_MIN==v36.DEV_PAIR_MIN
    assert v38.TEST_STRUCT_MIN==v36.TEST_STRUCT_MIN


def test_v38_rejects_altered_role_training_rule_in_manifest(tmp_path,monkeypatch):
    # Same mocked fail-closed training as before but make sure an artifact
    # cannot claim a different loss/objective and still load.
    width=8
    monkeypatch.setattr(v33,'activation',lambda root:{'model_id':'fake','lineage':'fixed'})
    monkeypatch.setattr(v33,'verify_activation',lambda root,l:l)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device:(torch,object(),FakeModel()))
    monkeypatch.setattr(v38,'capture',lambda _model,_tok,_torch,prompts,**_: (
        np.ones((len(prompts),4,width),dtype=np.float32),
        np.ones((len(prompts),width),dtype=np.float32)))
    monkeypatch.setattr(v38,'_load_v34',lambda root,project:({},v34.head(torch)))
    monkeypatch.setattr(v38,'fit_head',lambda *args,**kw:(v38.head(torch,width).state_dict(),[],(0,0,0),
          ([(0,1,2,3)]*120,[0]*120,[0]*120)))
    baseline=tmp_path/'v34';baseline.mkdir()
    (baseline/'grounded_head.pt').write_bytes(b'baseline-frozen')
    (baseline/'train_manifest.json').write_text('{}')
    out=tmp_path/'v38'
    v38.train(out,project=tmp_path,v34_root=baseline,device='cpu')
    manifest=v38.read(out/'train_manifest.json')
    assert manifest['role_training_rule']=='equivariant-cross-slot-attention-joint-24-permutation-ce'
    assert v38.load_head(out,tmp_path)[0]['dev_pass'] is False
    manifest['role_training_rule']='manual-lexical-role-map'
    (out/'train_manifest.json').write_text(v38.canonical(manifest))
    with pytest.raises(ValueError,match='DEV_DIAGNOSTICS_CHANGED'):
        v38.load_head(out,tmp_path)


def test_interacting_head_is_permutation_equivariant_without_slot_shortcut():
    torch.manual_seed(38)
    model = v38.head(torch, 32).eval()
    x=torch.randn((3,4,32))
    q=torch.randn((3,32))
    order=torch.tensor([2,0,3,1])
    with torch.inference_mode():
        direct, belief=model(x,q)
        reordered, same_belief=model(x[:,order],q)
    torch.testing.assert_close(reordered,direct[:,order],atol=1e-5,rtol=1e-5)
    torch.testing.assert_close(same_belief,belief,atol=1e-6,rtol=1e-6)


def test_one_clause_prediction_has_cross_clause_causal_gradient():
    # V37's independent role head has identically zero cross-slot derivatives.
    # V38 must have a *learnable causal path* between different clauses.
    torch.manual_seed(3801)
    model = v38.head(torch, 32).eval()
    x = torch.randn((1,4,32),requires_grad=True)
    q = torch.randn((1,32))
    logits, _ = model(x,q)
    grad = torch.autograd.grad(logits[0,0,0],x)[0]
    assert grad[0,0].abs().sum() > 0
    assert grad[0,1:].abs().sum() > 1.e-8


def test_v37_head_has_no_cross_clause_causal_gradient_control():
    # The exact defect demonstrated rather than asserted.
    from tools import nanojev_gamer_v1_v37_joint_role_grounding as old
    torch.manual_seed(3801)
    model = old.head(torch,32).eval()
    x = torch.randn((1,4,32),requires_grad=True)
    q = torch.randn((1,32))
    logits,_=model(x,q)
    grad=torch.autograd.grad(logits[0,0,0],x)[0]
    assert grad[0,0].abs().sum()>0
    assert grad[0,1:].abs().sum()==0


def test_trainable_cross_slot_path_updates_with_permutation_ce():
    import torch.nn.functional as F
    torch.manual_seed(3802)
    model=v38.head(torch,32)
    x=torch.randn((8,4,32))
    q=torch.randn((8,32))
    target=torch.tensor([v38._PERMUTATION_INDEX[(1,3,0,2)]]*8)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.002)
    starting=model.cross.in_proj_weight.detach().clone()
    for _ in range(3):
        optimizer.zero_grad()
        role,belief=model(x,q)
        loss=F.cross_entropy(v38.permutation_logits(role),target)
        loss.backward()
        assert model.cross.in_proj_weight.grad is not None
        optimizer.step()
    assert not torch.equal(starting,model.cross.in_proj_weight)


def test_v38_training_input_and_labels_are_v37_identical_without_opening_holdout():
    from tools import nanojev_gamer_v1_v37_joint_role_grounding as old
    assert v38.digest(v38.make_deck('fit',35))==old.digest(old.make_deck('fit',35))
    assert v38.capture.__code__.co_code==old.capture.__code__.co_code
    assert v38.DEV_STRUCT_MIN==old.DEV_STRUCT_MIN
    assert v38.DEV_DECISION_MIN==old.DEV_DECISION_MIN
    assert v38.DEV_PAIR_MIN==old.DEV_PAIR_MIN
    assert v38.TEST_STRUCT_MIN==old.TEST_STRUCT_MIN
    assert v38.TEST_DECISION_MIN==old.TEST_DECISION_MIN
    assert v38.TEST_PAIR_MIN==old.TEST_PAIR_MIN
    assert v38.PER_VARIANT_MIN==old.PER_VARIANT_MIN
