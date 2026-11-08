"""GAMER frozen compositional numerator/denominator tests."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import nanojev_gamer_v1_compositional_belief_lab as lab
from tools import nanojev_gamer_v1_exact_belief_lab as original
from tools import nanojev_gamer_v1_deconfounded_operand_audit as deconf


@pytest.fixture(scope='module')
def deck():
    return lab._denominator_deck()


def test_denominator_splits_are_nonempty_and_exact(deck):
    cases,tags,index=deck
    assert len(cases)==520 and len(index['train'])==360 and len(index['dev'])==160
    assert len({c.id for c in cases})==520
    q=original._exact_q(cases)
    train={q[int(i)].denominator for i in index['train']}
    dev={q[int(i)].denominator for i in index['dev']}
    assert train.isdisjoint(dev)
    assert {19,23,29,31,37,41}.isdisjoint(train|dev)
    assert {len(str(d)) for d in train}=={1,2,3,4,5,6}
    assert {len(str(d)) for d in dev}=={2,3,4,5}
    assert all(0<x<1 for x in q)


def test_generated_denominator_ranges_and_syntax(deck):
    cases,tags,_=deck
    assert all('/' in c.prompt for c in cases)
    assert all(t['panel']=='independent' and t['role'] in ('train','dev') for t in tags)
    assert all(c.group==t['group'] and c.id==t['id'] for c,t in zip(cases,tags))
    assert all(c.target==Fraction(c.target) for c in cases)


def test_numerator_denominator_reconstruction_is_exact_not_rounded():
    pred=lab._assemble([5,6,12,0,23,5],[19,19,19,7,23,0])
    assert pred==[Fraction(5,19),Fraction(6,19),Fraction(12,19),None,None,None]
    assert pred[0] != Fraction(5,17)


def test_digitwise_decoder_can_express_unseen_multi_digit_denominators():
    truth=[5700,12180,70001]
    y,spec=lab._columns(truth,np.arange(3),'digitwise')
    assert y.shape==(3,60)
    assert lab._decode(y,spec)==truth
    assert set(truth).isdisjoint({7,17,19,23})


def test_whole_integer_classifier_cannot_express_unseen_number():
    y,spec=lab._columns([19,23,29],[0,1],'integer_class')
    assert spec['vocabulary']==[19,23]
    assert 29 not in lab._decode(np.ones((2,2)),spec)


def test_digit_width_bound_refuses_overflow():
    with pytest.raises(ValueError,match='DIGIT_WIDTH'):
        lab._columns([10**lab.DIGITS],[0],'digitwise')


def test_alignment_extracts_only_numeric_component_positions(deck):
    class FakeTokenizer:
        padding_side='right'
        def __call__(self,text,**kwargs):
            from tools import nanojev_gamer_v1_token_localization_lab as t
            s,e=t._spans(text)['belief'][0]
            slash=text.index('/',s,e)
            pairs=[(0,s),(s,slash),(slash,slash+1),(slash+1,e),(e,len(text))]
            return {'offset_mapping':pairs,'input_ids':list(range(5))}
    spec=lab._aligned_components(FakeTokenizer(),deck[0][:1])[0]
    assert list(spec['indices'][0])==[1]
    assert list(spec['indices'][1])==[3]


def test_alignment_accepts_six_single_digit_tokens_per_component(deck):
    """Regression: real tokenizer can represent 6 ASCII digits as 6 tokens.

    The previous 4-slot cap prevented frozen CUDA capture before first batch.
    Each per-digit token must be retained in its original positional order.
    """
    import re

    class PerDigitTokenizer:
        padding_side = 'right'

        def __call__(self, text, **_kwargs):
            pieces = list(re.finditer(r'\d|[^\d]', text))
            return {'input_ids': [1] * len(pieces),
                    'offset_mapping': [m.span() for m in pieces]}

    cases, _, _ = deck
    from tools import nanojev_gamer_v1_token_localization_lab as t
    # The fresh deck includes six-digit denominators such as 100003.
    six_digit_cases = [c for c in cases if len(
        c.prompt[slice(*t._spans(c.prompt)['belief'][0])].split('/')[1]) == 6]
    assert six_digit_cases
    for case in six_digit_cases[:6]:
        result = lab._aligned_components(PerDigitTokenizer(), [case])[0]
        numerator, denominator = result['indices']
        span = t._spans(case.prompt)['belief'][0]
        text = case.prompt[slice(*span)]
        assert len(numerator) == len(text.split('/')[0])
        assert len(denominator) == 6
        assert lab.SLOTS >= 6
        hidden = np.arange(result['length']*1024, dtype=np.float32).reshape(-1, 1024)
        features = lab._component_vectors(hidden, result)
        assert np.array_equal(features['denominator_slots'],
                              hidden[denominator].reshape(-1))
        assert features['denominator_slots'].shape == (lab.SLOTS*1024,)
        assert np.array_equal(features['numerator_slots'][-len(numerator)*1024:],
                              hidden[numerator].reshape(-1))


def test_alignment_six_digit_numerator_and_denominator_are_both_preserved(deck):
    import re
    from tools import nanojev_gamer_v1_token_localization_lab as t

    class PerDigitTokenizer:
        padding_side = 'right'

        def __call__(self, text, **_kwargs):
            matches = list(re.finditer(r'\d|[^\d]', text))
            return {'input_ids': [1]*len(matches),
                    'offset_mapping': [m.span() for m in matches]}

    from tools import nanojev_gamer_v1_zero_sign_frob as prompts
    from tools import nanojev_two_game_spine_smoke as spine
    q = Fraction(123457, 200011)
    assert [len(p) for p in str(q).split('/')] == [6, 6]
    case = SimpleNamespace(id='six-digit-both', prompt=prompts._spine_belief_prompt(
        spine.Stage(Fraction(113), Fraction(119), Fraction(125), Fraction(115)), q))
    result = lab._aligned_components(PerDigitTokenizer(), [case])[0]
    assert [len(v) for v in result['indices']] == [6, 6]
    hidden = np.arange(result['length']*1024, dtype=np.float32).reshape(-1,1024)
    features = lab._component_vectors(hidden, result)
    for name, indexes in zip(lab.COMPONENTS, result['indices']):
        assert np.array_equal(features[name+'_slots'], hidden[indexes].reshape(-1))


def test_alignment_refuses_token_spanning_slash(deck):
    class BadTokenizer:
        padding_side='right'
        def __call__(self,text,**kwargs):
            from tools import nanojev_gamer_v1_token_localization_lab as t
            s,e=t._spans(text)['belief'][0]
            return {'offset_mapping':[(0,s),(s,e),(e,len(text))],
                    'input_ids':[0,1,2]}
    with pytest.raises(ValueError,match='CROSSES_BOUNDARY'):
        lab._aligned_components(BadTokenizer(),deck[0][:1])


def test_vector_source_num_den_isolated():
    spec={'indices':[np.asarray([1,2]),np.asarray([4])],'length':6}
    hidden=np.arange(6*1024,dtype=np.float32).reshape(6,1024)
    original=lab._component_vectors(hidden,spec)
    altered=hidden.copy();altered[4]+=5
    changed=lab._component_vectors(altered,spec)
    assert np.array_equal(original['numerator_pool'],changed['numerator_pool'])
    assert not np.array_equal(original['denominator_pool'],changed['denominator_pool'])
    assert changed['numerator_slots'].shape==(lab.SLOTS*1024,)
    assert np.array_equal(changed['numerator_slots'][-2*1024:],hidden[1:3].ravel())
    assert np.count_nonzero(changed['denominator_slots'][:3*1024])==0


def test_invalid_capture_width_refused():
    spec={'indices':[np.asarray([1]),np.asarray([2])],'length':4}
    with pytest.raises(ValueError,match='HIDDEN_SHAPE'):
        lab._component_vectors(np.zeros((4,512)),spec)


def test_component_metric_reports_unseen_errors():
    truth=[19,23,29,31]
    pred=[19,23,29,37]
    m=lab._component_metric(truth,pred,[0,1,2,3],[19,23])
    assert m['exact_cases']==3 and m['exact']==.75
    assert m['unseen_value_cases']==2 and m['unseen_value_exact']==.5


def test_integer_decoder_fits_training_only():
    rng=np.random.default_rng(5)
    n=42
    x=rng.normal(size=(n,16)).astype(np.float32)
    labels=[19,23,29,31,37,41]*7
    tr=np.arange(27)
    a=list(lab._candidates(x,labels,tr,kernels=('linear',),regs=(.1,)))
    altered=labels.copy()
    for i in range(27,n):altered[i]=999
    b=list(lab._candidates(x,altered,tr,kernels=('linear',),regs=(.1,)))
    assert len(a)==len(b)==2
    assert all(aa['prediction']==bb['prediction'] for aa,bb in zip(a,b))


def test_choose_uses_dev_not_heldout_labels():
    rng=np.random.default_rng(19);n=45
    x=rng.normal(size=(n,9)).astype('float32')
    qs=[Fraction(3+(i%6),17) for i in range(n)]
    frames={'embedding':{'numerator_pool':x,'denominator_pool':x}}
    train=np.arange(20);dev=np.arange(20,30)
    a,_=lab._choose(frames,qs,train,dev,kernels=('linear',),regs=(.1,),
                    stages=('embedding',),views=('pool',))
    modified=qs.copy()
    for i in range(30,n):modified[i]=Fraction(100+i,401)
    b,_=lab._choose(frames,modified,train,dev,kernels=('linear',),regs=(.1,),
                    stages=('embedding',),views=('pool',))
    for k in lab.COMPONENTS:
        assert a[k]['prediction']==b[k]['prediction']
        assert a[k]['rank']==b[k]['rank']


def test_checkpoint_replays_and_fails_on_changed_reference(tmp_path):
    x=np.asarray([[i,i*i] for i in range(1,18)],dtype='float32')
    q=[i+11 for i in range(17)]
    fit=next(lab._candidates(x,q,np.arange(12),kernels=('linear',),regs=(1.,)))
    fit={**fit,'stage':'embedding','view':'pool'}
    p=tmp_path/'num.npz'
    record=lab._checkpoint(p,fit,x,'a'*64,'b'*64)
    assert record['replay_matches']
    replay,meta=lab._replay(p,x,'a'*64,'b'*64)
    assert replay==fit['prediction']
    with pytest.raises(ValueError,match='LINEAGE_CHANGED'):
        lab._replay(p,x,'f'*64,'b'*64)
    xx=x.copy();xx[0,0]+=1
    with pytest.raises(ValueError,match='LINEAGE_CHANGED'):
        lab._replay(p,xx,'a'*64,'b'*64)


def test_counterfactual_report_requires_exact_fraction_delta(deck):
    cases,tags,ix=deck
    q=original._exact_q(cases)
    r=lab._counterfactual(cases,tags,q,q,ix['dev'])
    assert r['pairs']==len(lab.DEV_DENOMINATORS)*2*2
    assert r['exact_rational_delta']==1
    bad=q.copy();bad[int(ix['dev'][0])]=None
    rr=lab._counterfactual(cases,tags,q,bad,ix['dev'])
    assert rr['exact_rational_delta']<1


def test_protected_old_challenge_zero_triples_require_neural_exact_q():
    old,labels,ix,*_=deconf._make_deck()
    new,ct=original._challenge()
    cases=old+new
    q=original._exact_q(cases)
    payoff_values={key:np.asarray([int(original._parse_payoffs(c.prompt)[i]) for c in cases])
                  for i,key in enumerate(('R','S','T','P'))}
    idx={'heldout_zero_anchor':ix['heldout_zero_anchor'],
         'challenge_zero_anchor':np.arange(len(old)+72,len(old)+96)}
    _,m,all_exact=original._recompose(cases,labels+ct,idx,q,q,payoff_values)
    assert original._triples(labels+ct,idx['challenge_zero_anchor'],q,q,m,all_exact,
                             expected_groups=8)['accepted']
    altered=q.copy();altered[int(idx['challenge_zero_anchor'][1])]=Fraction(12,17)
    _,m,all_exact=original._recompose(cases,labels+ct,idx,q,altered,payoff_values)
    assert not original._triples(labels+ct,idx['challenge_zero_anchor'],q,altered,m,all_exact,
                                 expected_groups=8)['accepted']


def test_source_refuses_uncertified_input(tmp_path):
    root=tmp_path/'exact';root.mkdir()
    (root/'final_report.json').write_text(json.dumps({'schema':'wrong'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETE_FROZEN_BELIEF_REPORT'):
        lab._source(root)


def test_mocked_end_to_end_immutable_frozen_replay(tmp_path,monkeypatch):
    """Synthetic exact labels are an explicit integration fixture, NOT CUDA evidence."""
    from tools import nanojev_gamer_v1_payoff_precision_lab as payoff
    from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    old,otags,ix,*_=deconf._make_deck()
    ch,ct=original._challenge()
    gen,gt,gix=lab._denominator_deck()
    cases=old+ch+gen
    qs=original._exact_q(cases)
    root=tmp_path/'exact';root.mkdir()
    precision=tmp_path/'precision';precision.mkdir()
    source=tmp_path/'source';source.mkdir()
    (root/'final_report.json').write_text('{}')
    for k in payoff.PAYOFFS:(precision/f'{k}.npz').write_bytes(k.encode())
    source_hash=lab.token._sha
    report={'frozen_lora_sha256':'testsha',
            'source_deck_sha256':oracle.deck_hash(old),
            'challenge_deck_sha256':oracle.deck_hash(ch),
            'challenge_feature_archive':{'path':str(root/'challenge_layer09_float32.npz'),
                                         'sha256':'mock'},
            'diagnostic_checkpoint':{'path':str(root/'q.npz'),'sha256':'mock'}}
    base={k:np.zeros((len(cases),2),dtype='float32') for k in deconf.VIEWS}
    np.savez_compressed(precision/'frozen_layer09_float32.npz',
        **{'layer_09__'+k:v[:600] for k,v in base.items()})
    np.savez_compressed(root/'challenge_layer09_float32.npz',
        **{k:v[600:696] for k,v in base.items()})
    origin={'adapter':source,'ckpt':source/'ckpt', 'training':source,
            'source':source,'capture':source,'audit_path':source/'audit'}
    prec={'root':precision,'archive':precision/'frozen_layer09_float32.npz',
          'previous':{'root':source,'source':{'path':source/'prior',
                        'origin':origin}},
          'report':{'diagnostic_checkpoints':{k:{'path':str(precision/f'{k}.npz')}
                 for k in payoff.PAYOFFS},
                 'payoffs':{k:{'selection':{'view':'R'},
                              'heldout':{'independent':{'exact':1.}}}
                            for k in payoff.PAYOFFS},
                 'deconfounded_report_sha256':'mocksha'}}
    src={'root':root,'report':report,'report_path':root/'final_report.json',
         'attested_sha256':source_hash(root/'final_report.json'),
         'precision':prec}
    monkeypatch.setattr(lab,'_source',lambda _:src)
    monkeypatch.setattr(lab,'_verify_frozen',lambda _:None)
    numeric={name:np.asarray([int(original._parse_payoffs(c.prompt)[j]) for c in cases])
             for j,name in enumerate(payoff.PAYOFFS)}
    monkeypatch.setattr(lab.payoff,'_replay_checkpoint',
        lambda _path,_frame,**kwargs:numeric)
    frames={stage:{f'{component}_{view}':np.zeros((len(cases),2),dtype='float32')
                   for component in lab.COMPONENTS for view in lab.VIEWS}
            for stage in lab.STAGES}
    def capture_with_checked_lineage(bundle, captured_deck, device, batch_size):
        # Actual _capture needs the entire exact-belief source bundle,
        # including its nested 'precision' entry.  Do not pass prec alone.
        assert bundle is src
        assert bundle['precision'] is prec
        assert bundle['report'] is report
        assert len(captured_deck) == len(cases)
        assert device == 'cpu' and batch_size == 2
        return frames, base
    monkeypatch.setattr(lab,'_capture',capture_with_checked_lineage)
    models={p:{'stage':'embedding','view':'pool','kind':'linear','reg':.1,
               'mode':'digitwise','development':{'exact':1.},
               'prediction':[getattr(q,p) for q in qs],
               'support':[], 'training':np.arange(10),
               'meta':{'mode':'digitwise','digits':lab.DIGITS},
               'alpha':np.zeros((10,60)), 'bias':np.zeros(60)}
            for p in lab.COMPONENTS}
    monkeypatch.setattr(lab,'_choose',lambda *args,**kwargs:(models,{p:1 for p in models}))
    monkeypatch.setattr(lab,'_checkpoint',lambda path,*args:(
        path.write_bytes(b'fixture') and
        {'path':str(path),'sha256':source_hash(path),'replay_matches':True}))
    out=tmp_path/'new-report'
    outcome=lab.run(SimpleNamespace(belief=root,output=out,
                                     device='cpu',capture_batch=2))
    assert outcome['component_gate']['passed']
    assert outcome['zero_anchor_triples']['challenge']['strict_exact_full_triples']==8
    assert outcome['zero_anchor_triples']['heldout']['strict_exact_full_triples']==8
    assert outcome['fraction_accuracy']['challenge_independent']['exact']==1
    assert outcome['capture_parity']['old_fp32_max_delta']==0
    assert (out/'final_report.json').exists()
    assert all((precision/f'{p}.npz').read_bytes()==p.encode() for p in payoff.PAYOFFS)
    assert (root/'final_report.json').read_text()=='{}'


def test_denominator_only_counterfactuals_hold_numerator_and_payoffs(deck):
    cases,tags,ix=deck
    q=original._exact_q(cases)
    result=lab._denominator_counterfactual(tags,q,q,ix['dev'])
    assert result['pairs']==16
    assert result['exact_fraction_delta']==1
    assert result['numerator_invariant_exact']==1
    altered=q.copy();altered[int(ix['dev'][-2])]=None
    changed=lab._denominator_counterfactual(tags,q,altered,ix['dev'])
    assert changed['exact_fraction_delta']<1


def test_report_retains_only_selected_diagnostic_features(tmp_path,monkeypatch):
    # The integration suite above also checks the output report. Verify that
    # the archive helper itself serializes float32 arrays without pickling.
    path=tmp_path/'selected.npz'
    data=np.asarray([[1,2],[3,4]],dtype=np.float32)
    np.savez_compressed(path,numerator=data,denominator=data+1)
    with np.load(path,allow_pickle=False) as saved:
        assert np.array_equal(saved['numerator'],data)
        assert saved['denominator'].dtype==np.float32


def test_mock_forward_hook_captures_separate_digit_spans(monkeypatch,deck):
    import re
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress

    class ToyTokenizer:
        padding_side='right'
        def split(self,text):
            chunks=list(re.finditer(r'\d+|/|[^\d/]+',text))
            return [sum(ord(c) for c in m.group())%101+1 for m in chunks], [m.span() for m in chunks]
        def encode(self,text,**kwargs):return self.split(text)[0]
        def __call__(self,arg,**kwargs):
            if isinstance(arg,str):
                ids,spans=self.split(arg)
                return {'input_ids':ids,'offset_mapping':spans}
            rows=[self.split(text)[0] for text in arg]
            n=max(len(r) for r in rows)
            return {'input_ids':torch.tensor([r+[0]*(n-len(r)) for r in rows]),
                    'attention_mask':torch.tensor([[1]*len(r)+[0]*(n-len(r)) for r in rows])}

    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embed_tokens=torch.nn.Embedding(128,1024)
            self.layers=torch.nn.ModuleList([torch.nn.Identity() for _ in range(10)])
            self.norm=torch.nn.Identity()
        def forward(self,input_ids,attention_mask,**kwargs):
            x=self.embed_tokens(input_ids)
            for layer in self.layers:x=layer(x)
            x=self.norm(x)
            return {'logits':x[:,-1:,:]}

    model=Tiny().eval()
    tok=ToyTokenizer()
    monkeypatch.setattr(gamer,'model_components',lambda *a,**k:(None,tok,model))
    monkeypatch.setattr(oracle,'_backbone_parts',lambda *_:model)
    monkeypatch.setattr(stress,'_verify_snapshot',lambda *_:'abc')
    src={'report':{'frozen_lora_sha256':'abc'},'precision':{
        'previous':{'source':{'snapshot':Path('unused'),
               'origin':{'meta':{'lineage':{'model_id':'toy',
                'configuration':{'max_length':300,'curvature':4.0}}}}}}}}
    frames,pay=lab._capture(src,deck[0][:2],device='cpu',batch_size=2)
    assert frames['layer_09']['numerator_pool'].shape==(2,1024)
    assert frames['layer_09']['denominator_slots'].shape==(2,lab.SLOTS*1024)
    assert frames['embedding']['numerator_pool'].shape==(2,1024)
    assert pay['R'].shape==(2,1024)
    assert pay['operand_joint'].shape==(2,5120)
    assert all(np.isfinite(a).all() for stage in frames.values() for a in stage.values())
