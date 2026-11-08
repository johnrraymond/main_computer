"""Bounded acceptance tests: S-only closure + actual forward hook contract."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace
import json

import numpy as np
import pytest

from tools import nanojev_gamer_v1_s_payoff_operational_acceptance as lab
from tools import nanojev_gamer_v1_exact_belief_lab as exact
from tools import nanojev_gamer_v1_shared_digit_repair_lab as shared
from tools import nanojev_gamer_v1_token_localization_lab as token


def _digit_model():
    rng=np.random.default_rng(223)
    centers=rng.normal(size=(10,lab.HIDDEN)).astype(np.float32)
    bank={'centroids':centers}
    return shared._candidate(bank,'exact_centroid')


def _groups(strings,model):
    return [[model['centroids'][int(d)] for d in value] for value in strings]


def test_unseen_s_109_and_other_values_from_frozen_digit_embeddings():
    model=_digit_model()
    vals=['109','107','136','140','570','809','999']
    assert lab._decode_component_vectors(_groups(vals,model),model)==[int(v) for v in vals]


def test_corrupt_evaluation_labels_cannot_change_recovered_s():
    model=_digit_model()
    val=lab._decode_component_vectors(_groups(['109'],model),model)
    assert val==[109]
    # Only feature vectors and the previously trained model are accepted.
    assert 'labels' not in lab._decode_component_vectors.__code__.co_varnames


def test_unknown_digit_embedding_fails_closed():
    model=_digit_model()
    bad=_groups(['109','136'],model)
    bad[0][1]=bad[0][1]+np.ones(1024,dtype=np.float32)*9
    assert lab._decode_component_vectors(bad,model)==[None,136]


def test_noncanonical_or_unsupported_s_fails_closed():
    model=_digit_model()
    assert lab._decode_component_vectors(_groups(['010','1000'],model),model)==[None,None]
    with pytest.raises(ValueError,match='COMPONENT_LENGTH'):
        lab._decode_component_vectors([[np.zeros(1024)]*7],model)


class DigitTokenizer:
    padding_side='right'
    def _encode(self,text):
        # One character token each; digits map to a ten-entry frozen embedding.
        ids=[50];offsets=[(0,0)]
        for i,c in enumerate(text):
            ids.append(1+int(c) if c.isdigit() else 30)
            offsets.append((i,i+1))
        return ids,offsets
    def encode(self,text,add_special_tokens=True):
        return self._encode(text)[0]
    def __call__(self,text,**kwargs):
        if isinstance(text,list):
            import torch
            encoded=[self._encode(x)[0] for x in text]
            n=max(len(x) for x in encoded)
            return {'input_ids':torch.tensor([x+[0]*(n-len(x)) for x in encoded]),
                    'attention_mask':torch.tensor([[1]*len(x)+[0]*(n-len(x)) for x in encoded])}
        ids,offsets=self._encode(text)
        return {'input_ids':ids,'offset_mapping':offsets}


def test_s_alignment_exact_offsets_both_prompt_surfaces():
    tok=DigitTokenizer()
    deck,_=lab._operational_deck()
    for case in deck[:8]:
        ids,places=lab._s_span(tok,case)
        lo,hi=token._spans(case.prompt)['payoffs'][1]
        assert [int(ids[i])-1 for i in places]==[int(d) for d in case.prompt[lo:hi]]
        assert len(places)==3


def test_s_alignment_rejects_multi_digit_token_offsets():
    class Broken(DigitTokenizer):
        def __call__(self,text,**kwargs):
            result=super().__call__(text,**kwargs)
            a,b=token._spans(text)['payoffs'][1]
            # Broken tokenizer groups two S digits into one token.
            k=next(i for i,span in enumerate(result['offset_mapping']) if span==(a,a+1))
            result['offset_mapping'][k]=(a,a+2)
            del result['offset_mapping'][k+1]
            del result['input_ids'][k+1]
            return result
    with pytest.raises(ValueError,match='UNSUPPORTED_S_DIGIT_TOKENIZATION'):
        lab._s_span(Broken(),lab._operational_deck()[0][0])


def test_operational_deck_is_fresh_and_contains_strict_neighborhoods():
    deck,tags=lab._operational_deck()
    assert len(deck)==112
    assert len({x.id for x in deck})==112
    zero=[i for i,t in enumerate(tags) if t['panel']=='zero_anchor']
    assert len(zero)==48
    assert sum(t['edit']=='zero' for t in tags)==16
    assert sum(t['panel']=='s_only' for t in tags)==64
    assert {int(exact._parse_payoffs(deck[i].prompt)[1]) for i in range(len(deck))}=={107,109,136,140}
    assert all(exact._exact_q(deck)[i]==Fraction(lab.FRESH_DENOMINATORS[j]//2,lab.FRESH_DENOMINATORS[j])
               for j in range(8) for i,t in enumerate(tags) if
               t['profile']==f'freshs.{j:02d}' and t['edit']=='zero')


def _oracle_operational():
    deck,tags=lab._operational_deck()
    q=exact._exact_q(deck)
    pay={name:np.array([int(dict(zip(lab.payoff.PAYOFFS,exact._parse_payoffs(c.prompt)))[name])
        for c in deck],dtype=np.int64) for name in ('R','T','P')}
    ss=[int(exact._parse_payoffs(c.prompt)[1]) for c in deck]
    return deck,tags,q,ss,pay


def test_full_operational_acceptance_requires_real_recovered_operand_equality():
    deck,tags,q,ss,pay=_oracle_operational()
    result=lab._score_live(deck,tags,q,ss,pay)
    assert result['passed']
    assert result['exact_margin_cases']==112
    assert result['strict_zero_triples']['strict_exact_full_triples']==16
    assert result['s_only_counterfactuals']=={'cases':64,'exact':64}


def test_wrong_s_in_nonzero_neighbor_fails_strict_zero_gate():
    deck,tags,q,ss,pay=_oracle_operational()
    corrupted=list(ss)
    i=next(i for i,t in enumerate(tags) if t['panel']=='zero_anchor' and t['edit']=='negative')
    corrupted[i]+=1
    result=lab._score_live(deck,tags,q,corrupted,pay)
    assert result['passed'] is False
    assert result['strict_zero_triples']['strict_exact_full_triples']==15
    assert result['exact_margin_cases']<112


def test_wrong_s_in_s_only_counterfactual_fails_gate():
    deck,tags,q,ss,pay=_oracle_operational()
    changed=list(ss)
    i=next(i for i,t in enumerate(tags) if t['panel']=='s_only')
    changed[i]+=1
    result=lab._score_live(deck,tags,q,changed,pay)
    assert result['passed'] is False
    assert result['s_only_counterfactuals']['exact']==63


def test_wrong_q_zero_center_fails_full_triple():
    deck,tags,q,ss,pay=_oracle_operational()
    changed=list(q)
    i=next(i for i,t in enumerate(tags) if t['panel']=='zero_anchor' and t['edit']=='zero')
    changed[i]+=Fraction(1,3000)
    result=lab._score_live(deck,tags,changed,ss,pay)
    assert not result['passed']
    assert result['strict_zero_triples']['strict_exact_full_triples']==15


def test_bad_frozen_r_checkpoint_output_fails_full_triple():
    deck,tags,q,ss,pay=_oracle_operational()
    broken={k:v.copy() for k,v in pay.items()}
    broken['R'][0]+=1
    result=lab._score_live(deck,tags,q,ss,broken)
    assert not result['passed']
    assert result['strict_zero_triples']['strict_exact_full_triples']==15


def test_fresh_all_values_have_valid_one_digit_token_alignment():
    deck,_=lab._operational_deck()
    tok=DigitTokenizer()
    spans=lab.compose._aligned_components(tok,deck)
    assert all(1 <= len(s['indices'][0]) <= lab.SLOTS and
               1 <= len(s['indices'][1]) <= lab.SLOTS for s in spans)


def test_previous_s_109_was_outside_training_support():
    from tools import nanojev_gamer_v1_deconfounded_operand_audit as d
    from tools import nanojev_gamer_v1_operand_sufficiency_audit as op
    cases,_,panels,_,_=d._make_deck()
    labels=op._targets(cases)['S']
    supported={int(labels[i]) for i in panels['train_independent']}
    unseen=[int(i) for i in panels['heldout_independent'] if int(labels[i]) not in supported]
    assert len(unseen)==2
    assert [int(labels[i]) for i in unseen]==[109,109]


def test_live_group_reconstruction_from_embedding_features():
    model=_digit_model()
    q,s=lab._predict_live_groups({'numerator':_groups(['19','3581'],model),
                                 'denominator':_groups(['41','5700'],model),
                                 'S':_groups(['109','136'],model)},model)
    assert q==[Fraction(19,41),Fraction(3581,5700)]
    assert s==[109,136]


def test_missing_q_denominator_cannot_be_oracle_substituted():
    model=_digit_model()
    broken=_groups(['19'],model)
    broken[0][1]=broken[0][1]+123
    q,s=lab._predict_live_groups({'numerator':_groups(['9'],model),
        'denominator':broken,'S':_groups(['109'],model)},model)
    assert q==[None] and s==[109]


def test_output_rejects_unsupported_capture_batch():
    with pytest.raises(ValueError,match='CAPTURE_BATCH'):
        lab.run(SimpleNamespace(capture_batch=0))


def test_real_torch_forward_hooks_and_s_digit_capture(monkeypatch):
    import torch
    from torch import nn
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    rng=np.random.default_rng(223)
    centers=rng.normal(size=(10,lab.HIDDEN)).astype(np.float32)
    digit_model=shared._candidate({'centroids':centers},'exact_centroid')

    class Core(nn.Module):
        def __init__(self):
            super().__init__()
            self.embed_tokens=nn.Embedding(64,lab.HIDDEN)
            self.layers=nn.ModuleList(nn.Identity() for _ in range(10))
            self.norm=nn.Identity()
            with torch.no_grad():
                for k in range(10):
                    self.embed_tokens.weight[k+1].copy_(torch.from_numpy(centers[k]))
    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.model=Core()
        def forward(self,input_ids,attention_mask=None,**kw):
            h=self.model.embed_tokens(input_ids)
            for layer in self.model.layers:
                h=layer(h)
            return {'logits':h[:,-1:]}
    m=Model();tok=DigitTokenizer()
    deck,_=lab._operational_deck()
    # Both prompt surfaces and zero vs nonzero cases exercise actual hooks.
    selected=[deck[0],deck[1],deck[2],deck[16]]
    s_emb=lab._embed_s(m,tok,selected,digit_model,'cpu')
    assert s_emb==[int(exact._parse_payoffs(c.prompt)[1]) for c in selected]
    groups,views=lab._live_capture(m,tok,selected,
                    {'curvature':4.,'max_length':1024},'cpu',2)
    q,s=lab._predict_live_groups(groups,digit_model)
    assert q==exact._exact_q(selected)
    assert s==s_emb
    assert all(arr.shape[0]==4 and arr.dtype==np.float32 for arr in views.values())


def test_s_repair_closes_both_109_cases_without_indexing_later_q_training(monkeypatch):
    # Synthetic orchestration fixture, deliberately includes q-only TRAIN
    # indices after the 696 archived payoff cases.
    deck=[SimpleNamespace(id=f'case.{i}') for i in range(1216)]
    tags=[{} for _ in deck]
    panels={'train':np.array([0,1,696,697]),
            'legacy_dev':np.array([2,3]),
            'heldout_independent':np.array([558,559]),
            'heldout_zero_anchor':np.array([550,551]),
            'challenge_independent':np.array([600,601]),
            'challenge_zero_anchor':np.array([650,651])}
    qs=[Fraction(2,3)]*1216
    monkeypatch.setattr(lab.positional,'_deck',lambda source:(deck,tags,panels,qs))
    monkeypatch.setattr(lab.positional,'_load_features',lambda source,count:{})
    monkeypatch.setattr(lab.shared,'_aligned_trace',lambda source,deck,panels:[])
    monkeypatch.setattr(lab.shared,'_infer',lambda *args:({'numerator':[2]*1216,
             'denominator':[3]*1216},{}))
    monkeypatch.setattr(lab.shared,'_score_exact',lambda *args: (
       {key:{'exact_fraction_margin':1.,'all_payoffs_exact':1.}
            for key in ('heldout_independent','heldout_zero_anchor',
                        'challenge_independent','challenge_zero_anchor')},
       {key:{'strict_exact_full_triples':8} for key in ('heldout','challenge')}))
    # In a real run labels come from exact oracle for scoring only.
    monkeypatch.setattr(lab.operand,'_targets',lambda deck:{'S':np.array([109]*696)})
    monkeypatch.setattr(lab.payoff,'_integer_targets',lambda labels:labels)
    fixed=[109]*696
    baseline={k:np.array(fixed) for k in lab.payoff.PAYOFFS}
    baseline['S'][558]=129;baseline['S'][559]=129
    res,q=lab._repair_score({'underlying':{'composition':{}},'digit_model':{}},
                            fixed,baseline)
    assert res['passed']
    assert res['prior_s_failure_indices']==[558,559]
    assert res['repaired_s_failure_indices']==[558,559]
    assert res['s_component_metrics']['train']['cases']==2
    assert len(q)==1216
    fixed[559]=None
    res,_=lab._repair_score({'underlying':{'composition':{}},'digit_model':{}},
                            fixed,baseline)
    assert not res['passed']


def test_s_diagnostic_checkpoint_is_byte_bound_to_original_shared_head(tmp_path):
    import hashlib
    model=_digit_model()
    src={'digit_model':model,'head_sha256':'9'*64,'report_sha256':'8'*64}
    path=tmp_path/'s.npz'
    info=lab._save_s_checkpoint(path,src)
    assert info['replay_params_identical'] is True
    assert info['trained_on_s_heldout'] is False
    assert info['source_q_head_sha256']=='9'*64
    with np.load(path,allow_pickle=False) as ck:
        meta=json.loads(str(ck['metadata'].item()))
        assert meta['component']=='S'
        assert meta['source_shared_sha256']=='8'*64
        assert hashlib.sha256(ck['centroids'].tobytes()).hexdigest()==meta['centroids_sha256']
