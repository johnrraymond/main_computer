"""V35 falsifiability and read-only integration tests (not CUDA certification)."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_v35_semantic_grounding as v35
from tools import nanojev_gamer_v1_v33_game_learning as v33
from tools import nanojev_gamer_v1_v34_grounded_game_learning as v34


def fit_deck():
    return v35.fit_partition(v35.make_deck('fit',35))


def test_minimal_pairs_same_numbers_different_semantics_and_predictions():
    fit,dev=fit_deck()
    assert len(fit)==480 and len(dev)==120
    assert {r['pair'] for r in fit}.isdisjoint({r['pair'] for r in dev})
    assert {r['intervention'] for r in fit}=={'belief','role'}
    reversed_count=0
    for a,b in zip((fit+dev)[::2],(fit+dev)[1::2]):
        assert a['pair']==b['pair']
        ia,qa=v35.transcribe(a['prompt'])
        ib,qb=v35.transcribe(b['prompt'])
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
    a,p=v35.transcribe(prompt)
    b,q=v35.transcribe(changed)
    assert [i['value'] for i in a]==[i['value'] for i in b]
    assert p['value']==q['value']
    # Inference must be sensitive to a learned q-side prediction.
    same_role=(0,1,2,3)
    x1=v35.numeric_input(prompt,same_role,0)
    x2=v35.numeric_input(changed,same_role,1)
    np.testing.assert_array_equal(x1[:4],x2[:4])
    assert x1[4]==-x2[4]
    # And similarly to a *learned* swap of the CC/DC roles.
    x3=v35.numeric_input(prompt,(2,1,0,3),0)
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
    with pytest.raises(ValueError, match='GAMER_V35_'):
        v35.transcribe(bad)


def test_numeric_transcriber_never_calls_role_parser_or_label_oracle(monkeypatch):
    fit,_=fit_deck()
    prompt=fit[0]['prompt']
    def explode(*_args,**_kw):
        raise AssertionError('forbidden in semantic inference')
    monkeypatch.setattr(v33,'exact_teacher',explode)
    monkeypatch.setattr(v34,'ground',explode)
    ints,q=v35.transcribe(prompt)
    assert len(ints)==4
    np.testing.assert_array_equal(v35.numeric_input(prompt,(0,1,2,3),0).shape,(5,))
    assert 0<q['value']<1
    fake=v34.head(torch)
    guesses=v35.run_decisions(fake,[{'prompt':prompt}],[(0,1,2,3)],[0])
    assert len(guesses)==1 and guesses[0] in (0,1)


def test_assignment_is_structural_not_oracle():
    # A parser that assigns roles by numerical order will fail this test.
    logits=np.array([[[0,0,8,0],[0,8,0,0],[8,0,0,0],[0,0,0,8]]],dtype=np.float32)
    assert v35.assign_roles(logits)==[(2,1,0,3)]
    same=np.array([[[8,0,0,0],[0,8,0,0],[0,0,8,0],[0,0,0,8]]],dtype=np.float32)
    assert v35.assign_roles(same)==[(0,1,2,3)]
    with pytest.raises(ValueError,match='ROLE_LOGIT_SHAPE'):
        v35.assign_roles(np.zeros((1,3,4),np.float32))


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
    x,q=v35.capture(FakeModel(),FakeTokenizer(),torch,prompts,device='cpu',batch_size=2)
    assert x.shape==(2,4,32) and q.shape==(2,32)
    assert np.isfinite(x).all() and np.isfinite(q).all()
    # This fake model ignores semantics; its identical embeddings are not evidence of learning.
    assert np.array_equal(x[0],x[1])


def test_left_and_right_padding_last_token():
    right=torch.tensor([[1,1,0,0],[1,1,1,0]])
    left=torch.tensor([[0,0,1,1],[0,1,1,1]])
    assert v35._last_valid_token(right).tolist()==[1,2]
    assert v35._last_valid_token(left).tolist()==[3,3]


def test_dev_does_not_consume_sealed_test_and_preserves_authority(tmp_path,monkeypatch):
    fit,dev=fit_deck()
    width=8
    def mock_capture(_model,_tok,_torch,prompts,**_):
        return np.ones((len(prompts),4,width),dtype=np.float32),np.ones((len(prompts),width),dtype=np.float32)
    def mock_fit(_x,_q,_fit,_dx,_dq,_dev,_numeric,**_):
        model=v35.head(torch,width)
        roles=[(0,1,2,3)]*len(dev)
        sides=[0]*len(dev)
        decisions=[0]*len(dev)
        return model.state_dict(),[],(0,0,0),(roles,sides,decisions)
    lineage={'model_id':'mock-qwen','source_files':{},'base_activation':'frozen-base'}
    monkeypatch.setattr(v33,'activation',lambda root:lineage)
    monkeypatch.setattr(v33,'verify_activation',lambda root,l:lineage)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device:(torch,object(),FakeModel()))
    monkeypatch.setattr(v35,'_load_v34',lambda root,project:({},v34.head(torch)))
    monkeypatch.setattr(v35,'capture',mock_capture)
    monkeypatch.setattr(v35,'fit_head',mock_fit)
    modelrun=tmp_path/'accepted-v34'
    modelrun.mkdir()
    (modelrun/'grounded_head.pt').write_bytes(b'fixed-numerical-model')
    (modelrun/'train_manifest.json').write_text('{}')
    out=tmp_path/'v35'
    result=v35.train(out,project=tmp_path,v34_root=modelrun,device='cpu')
    assert result['status']=='FAIL_DEV_HOLDOUT_STILL_SEALED'
    assert not (out/'holdout_consumed.json').exists()
    assert not (out/'holdout_acceptance.json').exists()
    manifest=v35.read(out/'train_manifest.json')
    assert manifest['frozen_qwen_used'] is True
    assert manifest['oracle_prediction'] is False
    assert manifest['numerical_semantic_parser_used'] is False
    assert result['production_modified'] is False
    # Loading the frozen head must not regenerate teacher-labelled decks.
    monkeypatch.setattr(v33,'exact_teacher',lambda *_: (_ for _ in ()).throw(AssertionError('oracle in loader')))
    restored,net=v35.load_head(out,tmp_path)
    assert restored['dev_pass'] is False
    assert isinstance(net,torch.nn.Module)
    with pytest.raises(ValueError,match='DEV_GATE_FAILED_HOLDOUT_SEALED'):
        v35.audit(out,project=tmp_path,device='cpu')
    assert not (out/'holdout_consumed.json').exists()


def test_saved_training_provenance_fingerprint_rejects_mutation(tmp_path,monkeypatch):
    # Fail-closed fingerprint check is independent of a user's claimed PASS.
    width=8
    monkeypatch.setattr(v33,'activation',lambda root:{'model_id':'fake','lineage':'fixed'})
    monkeypatch.setattr(v33,'verify_activation',lambda root,l:l)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device:(torch,object(),FakeModel()))
    monkeypatch.setattr(v35,'capture',lambda _model,_tok,_torch,prompts,**_: (
        np.ones((len(prompts),4,width),dtype=np.float32),
        np.ones((len(prompts),width),dtype=np.float32)))
    monkeypatch.setattr(v35,'_load_v34',lambda root,project:({},v34.head(torch)))
    monkeypatch.setattr(v35,'fit_head',lambda *args,**kw:(v35.head(torch,width).state_dict(),[],(0,0,0),
          ([(0,1,2,3)]*120,[0]*120,[0]*120)))
    baseline=tmp_path/'v34';baseline.mkdir()
    (baseline/'grounded_head.pt').write_bytes(b'baseline-frozen')
    (baseline/'train_manifest.json').write_text('{}')
    output=tmp_path/'v35'
    v35.train(output,project=tmp_path,v34_root=baseline,device='cpu')
    v35.load_head(output,tmp_path)
    (output/'training_data_provenance.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='TRAIN_PROVENANCE_CHANGED'):
        v35.load_head(output,tmp_path)
