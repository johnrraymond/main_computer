"""CPU-only regressions for experimental text/HybridLink rung reduction."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_hybridlink_rung_reduction as r


def test_chain_order():
    assert r.CHAIN == ('T08','B0b','B0a','T0','ANSWER')


@pytest.mark.parametrize('delta', [-4,-1,0,1,4])
@pytest.mark.parametrize('reverse', [False,True])
def test_semantic_conservation(delta,reverse):
    ch=r.canonical_chain(128,delta,variant=3,reverse=reverse)
    assert ch['x']-ch['y']==delta
    assert ch['answer']==(-delta if reverse else delta)
    assert str(ch['a']) in ch['texts']['T08']
    assert str(ch['b']) in ch['texts']['T08']
    assert str(ch['x']) in ch['texts']['B0a']
    assert str(ch['y']) in ch['texts']['B0a']
    assert ch['direction'] in ch['texts']['T08']
    for label in r.CHAIN[:-1]:
        p=r.hop_prompt(label,ch['texts'][label])
        assert p.endswith('Next: ')
        assert 'END_RUNG' not in p
        result=r.score_hop(r.hop_target(ch,label),ch,label)
        assert result['correct'],(delta,reverse,label,result)
        assert not result.get('extra_after_hop',False)


def test_opposite_direction_same_payoffs_sign_flip():
    a=r.canonical_chain(400,3,2,False)
    b=r.canonical_chain(400,3,2,True)
    assert (a['a'],a['b'],a['x'],a['y'])==(b['a'],b['b'],b['x'],b['y'])
    assert a['answer']==-b['answer']
    assert a['texts']['T0']!=b['texts']['T0']


def test_bad_input_bounds():
    with pytest.raises(ValueError,match='BOUNDS'):
        r.canonical_chain(4_000_000,-4)
    with pytest.raises(ValueError,match='BOUNDS'):
        r.canonical_chain(0,0)


@pytest.mark.parametrize('band',[(0,4),(5,16),(0,0),(17,64)])
def test_generator_disjoint_and_balanced(band):
    scales=(max(8,2*band[1]),max(16,4*band[1]),max(32,8*band[1]))
    deck=r.generate_deck(band=band,scales=scales,seed=71,train_count=36,dev_count=12,test_count=12)
    assert all(len(rows)==n for rows,n in [(deck['train'],36),(deck['dev'],12),(deck['test'],12)])
    groups=[set(map(r.case_key,deck[sp])) for sp in ('train','dev','test')]
    assert all(not groups[i]&groups[j] for i in range(3) for j in range(i+1,3))
    for rows in deck.values():
        assert any(x['answer']==0 for x in rows)
        if band[1]:
            assert any(x['answer']>0 for x in rows)
            assert any(x['answer']<0 for x in rows)
        assert any(x['reverse'] for x in rows)
        assert len(r.flatten_hops(rows))==4*len(rows)
        for row in rows:
            assert abs(row['delta'])<=band[1]
            assert row['delta']==0 or abs(row['delta'])>=band[0]


def test_reproducibility():
    a=r.generate_deck()
    b=r.generate_deck()
    assert r.digest(a)==r.digest(b)
    assert r.digest(a)!=r.digest(r.generate_deck(seed=72))


def test_wrong_next_rung_is_rejected():
    ch=r.canonical_chain(8,0)
    score=r.score_hop('[B0a] something\nEND_RUNG\n',ch,'T08')
    assert not score['correct'] and score['reason']=='wrong_rung'


def test_unfinished_or_wrong_numeric_hop():
    ch=r.canonical_chain(8,1)
    assert r.score_hop('[ANSWER] '+str(ch['answer']),ch,'T0')['correct'] is False
    assert r.score_hop('[ANSWER] 999\nEND_RUNG\n',ch,'T0')['correct'] is False
    assert r.score_hop('[ANSWER] yes\nEND_RUNG\n',ch,'T0')['reason']=='non_integer_answer'


def test_no_credit_for_correct_second_attempt():
    ch=r.canonical_chain(128,4)
    good=r.hop_target(ch,'T08')
    wrong='[B0b] bogus\nEND_RUNG\n'+good
    s=r.score_hop(wrong,ch,'T08')
    assert not s['correct']
    assert s['extra_after_hop']


def test_next_hop_does_not_already_answer():
    ch=r.canonical_chain(512,4)
    assert '[ANSWER]' not in r.hop_target(ch,'T08')
    assert '[ANSWER]' not in r.hop_target(ch,'B0b')
    assert '[ANSWER]' not in r.hop_target(ch,'B0a')
    assert '[ANSWER]' in r.hop_target(ch,'T0')


def test_scorer_completes_chain_without_teacher_inference():
    ch=r.canonical_chain(512,-4,variant=5,reverse=True)
    prompt=ch['texts']['T08']
    for tier in r.CHAIN[:-1]:
        response=r.hop_target(ch,tier)
        score=r.score_hop(response,ch,tier)
        assert score['correct']
        if tier !='T0':prompt=score['question']
    assert prompt==ch['texts']['T0']


def test_preview_cli_no_model(capsys):
    r.main(['--stage','preview','--device','cpu','--band','0-4','--scales','8,16,32',
            '--train-count','12','--dev-count','6','--test-count','6'])
    o=capsys.readouterr().out
    data=json.loads(o.split('=',1)[1])
    assert data['model_loaded'] is False
    assert data['scenario_counts']=={'train':12,'dev':6,'test':6}
    assert data['teacher_hop_counts']['train']==48
    assert not data['split_overlap']


def test_bad_generator_config():
    with pytest.raises(ValueError,match='RUNG_SCALE'):
        r.generate_deck(band=(0,16),scales=(8,))
    with pytest.raises(ValueError,match='MULTIPLE'):
        r.generate_deck(train_count=5)


def test_encoding_and_losses_toy_cpu():
    torch=pytest.importorskip('torch')
    from torch import nn
    from torch.nn import functional as F
    class Tokenizer:
        def __call__(self,text,add_special_tokens=False):
            return {'input_ids':[3+(ord(c)%67) for c in text]}
    class Decoder(nn.Module):
        def __init__(self,width):
            super().__init__();self.linear=nn.Linear(width,width)
        def forward(self,*,inputs_embeds,attention_mask=None,return_dict=True,use_cache=False):
            return SimpleNamespace(last_hidden_state=self.linear(torch.cumsum(inputs_embeds,dim=1)))
    class Backbone(nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding=nn.Embedding(100,12);self.decoder=Decoder(12)
            self.output=nn.Linear(12,100,bias=False)
        def get_input_embeddings(self):return self.embedding
        def get_decoder(self):return self.decoder
        def get_output_embeddings(self):return self.output
    class Model(nn.Module):
        def __init__(self):
            super().__init__();self.backbone=Backbone();self.head=nn.Linear(12,12)
            self.start_id=1;self.end_id=2
    model=Model();tok=Tokenizer()
    ch=r.canonical_chain(8,1)
    row={'prompt':r.hop_prompt('T0',ch['texts']['T0']),'target':r.hop_target(ch,'T0')}
    v=r.text_loss(model,tok,row,'cpu',384)
    assert torch.isfinite(v)
    model.backbone.requires_grad_(False)
    model.head.requires_grad_(True)
    z=r.latent_loss(model,tok,row,'cpu',384)
    assert torch.isfinite(z)
    z.backward()
    assert model.head.weight.grad is not None
    assert float(model.head.weight.grad.abs().sum())>0
    assert all(p.grad is None for p in model.backbone.parameters())

@pytest.mark.parametrize('text,done', [
    ('[T0] Compute 8 + (-7).\nEND_RUNG\n',True),
    ('[ANSWER] 1\nEND_RUNG',True),
    ('[ANSWER] 1\nEND_RUNG\nnew question',True),
    ('[ANSWER] 1\nEND_RUNGED',False),
    ('[T0] Something END_RUNG inline',False),
    ('[T0] Incomplete\nEND_RU',False),
])
def test_rung_stop_marker_is_complete_line(text,done):
    assert r._rung_finished(text) == done


def test_probe_progress_and_subset_cpu(monkeypatch, tmp_path, capsys):
    pytest.importorskip('torch')
    from tools import nanojev_qwen_hybridagent_activation as activation
    import hashlib
    params=dict(band=(0,4),scales=(8,16,32),seed=71,
                train_count=12,dev_count=6,test_count=6)
    deck=r.generate_deck(**params)
    train=tmp_path/'trained';train.mkdir()
    ck=train/'best.pt';ck.write_bytes(b'fake checkpoint')
    saved={'schema':r.SCHEMA,'band':list(params['band']),
           'scales':list(params['scales']),'seed':params['seed'],
           'train_count':12,'dev_count':6,'test_count':6,
           'deck_hash':r.digest(deck),'sources':{'x':'hash'},
           'frozen_backbone':'frozen-hash'}
    (train/'run_config.json').write_text(json.dumps(saved))
    class EvalOnly:
        def eval(self):pass
    model=SimpleNamespace(backbone=EvalOnly(),head=EvalOnly())
    monkeypatch.setattr(r,'_load',lambda args,trained=None:(model,None,{'x':'hash'},'frozen-hash',()))
    # Every generated hop fails immediately; 4 one-hop + 1 free-chain per control.
    calls=[]
    def fake_decode(model,tok,prompt,control,budget,*,stop_at_end_rung=False):
        calls.append((control,stop_at_end_rung));return '[B0a] bad\nEND_RUNG\n'
    monkeypatch.setattr(r,'_decode',fake_decode)
    args=SimpleNamespace(checkpoint=str(ck),output=str(tmp_path/'results'),split='dev',
           controls=('text_only','zero_latent','intact'),max_new_tokens=128,
           case_limit=1,progress_every=1,stop_at_end_rung=True)
    r.probe(args)
    captured=capsys.readouterr().out.splitlines()
    begin=json.loads(next(line.split('=',1)[1] for line in captured if line.startswith('RUNG_PROBE_BEGIN=')))
    assert begin['max_generation_calls']==24
    assert begin['cases_selected']==1 and begin['subset']
    assert len([x for x in captured if x.startswith('RUNG_PROBE_PROGRESS=')])==15
    result=json.loads((tmp_path/'results'/'result.json').read_text())
    assert result['subset_probe'] and result['generation_calls']==15
    assert len(calls)==15 and all(stop for _,stop in calls)
    assert not (train/'test_consumed.json').exists()


def test_probe_rejects_partial_test_before_loading(monkeypatch,tmp_path):
    pytest.importorskip('torch')
    params=dict(band=(0,4),scales=(8,16,32),seed=71,
                train_count=12,dev_count=6,test_count=6)
    deck=r.generate_deck(**params)
    train=tmp_path/'trained';train.mkdir()
    ck=train/'best.pt';ck.write_bytes(b'fake checkpoint')
    (train/'run_config.json').write_text(json.dumps({
       'schema':r.SCHEMA,'band':[0,4],'scales':[8,16,32],'seed':71,
       'train_count':12,'dev_count':6,'test_count':6,'deck_hash':r.digest(deck),
       'sources':{},'frozen_backbone':'frozen'}))
    class EvalOnly:
       def eval(self):pass
    monkeypatch.setattr(r,'_load',lambda args,trained=None:
                        (SimpleNamespace(backbone=EvalOnly(),head=EvalOnly()),None,{},'frozen',()))
    args=SimpleNamespace(checkpoint=str(ck),output=str(tmp_path/'out'),split='test',
           controls=('text_only',),max_new_tokens=128,
           case_limit=1,progress_every=1,stop_at_end_rung=True)
    with pytest.raises(RuntimeError,match='COMPLETE_SPLIT'):
        r.probe(args)
    assert not (train/'test_consumed.json').exists()

@pytest.mark.parametrize('control', ['text_only','zero_latent','intact'])
def test_early_stop_decoder_retains_exact_latent_protocol(control):
    torch=pytest.importorskip('torch')
    from torch import nn
    answer='[ANSWER] 4\nEND_RUNG\nSHOULD_NOT_BE_GENERATED'
    class Tok:
        eos_token_id=1000
        def __call__(self, prompt,add_special_tokens=False):
            return {'input_ids':[500]}
        def decode(self,ids,skip_special_tokens=True):
            return ''.join(chr(i) for i in ids)
    class Fake(nn.Module):
        def __init__(self):
            super().__init__()
            self.head=nn.Linear(3,3)
            self.backbone=nn.Linear(3,3)
            self.end_id=501
            self.generated=0
            self.marker_seen=False
            self.depth=None
        def _logits(self):
            t=torch.zeros((1,1001));t[0,ord(answer[self.generated])]=1
            return t
        def rollout(self,ids,depth,return_trace=False):
            self.depth=depth
            return None,self._logits(),None,torch.ones((1,1)),[]
        def _step(self,*,input_ids=None,cache=None,mask=None):
            if input_ids.item()==self.end_id and not self.marker_seen:
                self.marker_seen=True
            else:
                self.generated+=1
            return None,self._logits(),None
    model=Fake();original_head=model.head
    out=r._decode(model,Tok(),'prompt',control,128,stop_at_end_rung=True)
    assert out=='[ANSWER] 4\nEND_RUNG'
    assert 'SHOULD_NOT' not in out
    assert model.head is original_head
    assert model.depth==(0 if control=='text_only' else 1)
    assert model.marker_seen==(control!='text_only')
