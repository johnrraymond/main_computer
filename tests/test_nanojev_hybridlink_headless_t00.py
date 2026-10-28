"""CPU-only verification of the headless T00 source/evaluation boundary."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_hybridlink_headless_t00 as t00
from tools import nanojev_hybridlink_micro_latent_transfer as transfer
from tools import nanojev_hybridlink_micro_reduction as micro
from tools import nanojev_hybridlink_rung_reduction as base


def test_deck_has_distinct_inputs_and_balanced_gates_and_magnitudes():
    d=t00.make_deck(train_count=112,dev_count=32,ood_count=32,test_count=32)
    assert d==t00.make_deck(train_count=112,dev_count=32,ood_count=32,test_count=32)
    assert len(set(x['prompt'] for split in d.values() for x in split))==208
    assert {r['action'] for r in d['train']}==set(t00.ACTIONS)
    assert any(r['answer']==0 for r in d['train'])
    assert any(r['answer']<0 for r in d['train'] if r['answer'] is not None)
    assert any(r['answer']>0 for r in d['train'] if r['answer'] is not None)
    assert {r['scale'] for r in d['ood']} <= set(t00.OOD_SCALES)
    assert {r['scale'] for r in d['train']} <= set(t00.SCALES)
    for r in d['train']:
        assert r['prompt'].startswith('PROBLEM: ')
        assert (r['answer'] is not None)==(r['action']=='ANSWER')
        if r['action']=='ANSWER':assert r['answer']==r['a']+r['b']


@pytest.mark.parametrize('n',[-4000000,-500000,-12,-1,0,1,12,4000000])
def test_numeric_readout_exact_signed_numbers(n):
    sg,digits=t00.digit_labels(n)
    s=torch.full((1,3),-10.)
    s[0,sg]=10.
    ds=torch.full((1,t00.DIGITS,10),-10.)
    for p,d in enumerate(digits):ds[0,p,d]=10.
    assert t00.number_from_logits(s,ds)==[n]


def test_digit_codec_rejects_overflow():
    with pytest.raises(ValueError,match='OUT_OF_RANGE'):
        t00.digit_labels(100000000)


class ToyTokenizer:
    pad_token_id=0
    def __call__(self,s,add_special_tokens=False):
        return {'input_ids':[1+ord(c)%1023 for c in s]}

class ToyDecoder(nn.Module):
    def __init__(self,emb):
        super().__init__();self.emb=emb
    def forward(self,*,input_ids,attention_mask,use_cache,return_dict):
        assert not use_cache
        return SimpleNamespace(last_hidden_state=self.emb(input_ids))

class ToyBackbone(nn.Module):
    def __init__(self):
        super().__init__();self.emb=nn.Embedding(1025,24);self.decoder=ToyDecoder(self.emb)
        self.config=SimpleNamespace(hidden_size=24)
        self.lm_head=nn.Linear(24,1025,bias=False)
    def get_decoder(self):return self.decoder
    def forward(self,*args,**kwargs):
        raise AssertionError('Vocabulary LM forward forbidden')

class ToyModel(nn.Module):
    def __init__(self):
        super().__init__();self.backbone=ToyBackbone();self.head=nn.Linear(24,24)


def test_qwen_hidden_encoder_does_not_call_lm_head():
    model=ToyBackbone();model.requires_grad_(False)
    model.lm_head.forward=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('LM head was invoked'))
    rows=[{'prompt':'PROBLEM: 6+6'},{'prompt':'PROBLEM: (4+7)+3'},
          {'prompt':'PROBLEM: -1000000+1000001'}]
    hidden=t00._encoded_features(model,ToyTokenizer(),rows,'cpu',2)
    assert hidden.shape==(3,24)
    assert torch.isfinite(hidden).all()
    assert not hidden.requires_grad
    assert torch.allclose(hidden[0],model.emb(torch.tensor([1+ord('6')%1023])).squeeze(0))


def test_model_training_updates_link_and_numerical_readout_not_encoder():
    torch.manual_seed(8)
    model=ToyModel();model.backbone.requires_grad_(False)
    numeric=t00._numeric_module(24,'cpu')
    data=t00.make_deck(train_count=32,dev_count=16,ood_count=16,test_count=16)['train']
    x=t00._encoded_features(model.backbone,ToyTokenizer(),data,'cpu',8)
    act,sign,digits=t00._targets(data)
    old={k:v.detach().clone() for k,v in model.backbone.state_dict().items()}
    prior={k:v.detach().clone() for k,v in model.head.state_dict().items()}
    optim=torch.optim.AdamW([*model.head.parameters(),*numeric.parameters()],lr=.01)
    loss,_=t00.compute_loss(model.head,numeric,x,act,sign,digits)
    optim.zero_grad(set_to_none=True);loss.backward();optim.step()
    assert torch.isfinite(loss)
    assert any(not torch.equal(prior[k],v) for k,v in model.head.state_dict().items())
    assert all(torch.equal(old[k],v) for k,v in model.backbone.state_dict().items())
    with torch.no_grad():
        m,f=t00.measure(model.head,numeric,x,data,'cpu')
        z,_=t00.measure(model.head,numeric,x,data,'cpu',ablation=True)
    assert m['answers']>0 and m['problems']>0
    assert 0<=m['action_accuracy']<=1 and 0<=z['action_accuracy']<=1


def test_loss_never_supervises_numeric_output_on_problem_examples():
    link=nn.Identity();numeric=t00._numeric_module(8,'cpu')
    x=torch.randn(4,8)
    kinds=torch.tensor([0,0,0,0])
    a,b=t00.compute_loss(link,numeric,x,kinds,torch.tensor([0,0,0,0]),torch.zeros(4,8,dtype=torch.long))
    c,_=t00.compute_loss(link,numeric,x,kinds,torch.tensor([2,2,2,2]),torch.ones(4,8,dtype=torch.long))
    assert torch.equal(a,c)
    assert b['sign']==0 and b['digits']==0


def test_parents_check_without_model_loading(tmp_path):
    root=tmp_path/'text';root.mkdir()
    (root/'best.pt').write_bytes(b'text')
    (root/'run_config.json').write_text(json.dumps({'schema':micro.SCHEMA,'stage':'train-text'}))
    link=tmp_path/'link';link.mkdir()
    (link/'best.pt').write_bytes(b'link')
    (link/'run_config.json').write_text(json.dumps({'schema':transfer.SCHEMA}))
    args=SimpleNamespace(text_checkpoint=str(root/'best.pt'),link_checkpoint=str(link/'best.pt'),
                         seed=9127,train_count=32,dev_count=16,ood_count=16,test_count=16)
    t00.preview(args,t00.parents(args))
    (link/'run_config.json').write_text(json.dumps({'schema':'something_else'}))
    with pytest.raises(RuntimeError,match='WRONG_FAMILY'):t00.parents(args)


def test_preview_does_not_load_qwen(tmp_path,monkeypatch,capsys):
    args=SimpleNamespace(seed=9127,train_count=32,dev_count=16,ood_count=16,test_count=16)
    monkeypatch.setattr(t00,'_load_backbone',lambda *a,**k:(_ for _ in ()).throw(AssertionError('no load')))
    t00.preview(args,{})
    payload=capsys.readouterr().out
    assert '"model_loaded": false' in payload
    assert '"recursion_implemented": false' in payload


def test_mock_full_train_probe_roundtrip_with_parent_hashes(tmp_path,monkeypatch,capsys):
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    text=tmp_path/'text'/'best.pt';text.parent.mkdir();text.write_bytes(b'text')
    link=tmp_path/'link'/'best.pt';link.parent.mkdir();link.write_bytes(b'link')
    cfg={'schema':micro.SCHEMA,'stage':'train-text','sources':{'mock':'hash'},'frozen_backbone':'mock-frozen'}
    (text.parent/'run_config.json').write_text(json.dumps(cfg))
    (link.parent/'run_config.json').write_text(json.dumps({'schema':transfer.SCHEMA}))
    monkeypatch.setattr(micro,'_load',lambda *a,**kw:(ToyModel(),ToyTokenizer(),cfg['sources'],cfg['frozen_backbone'],()))
    monkeypatch.setattr(transfer,'_load_link_into',lambda *a,**kw: None)
    monkeypatch.setattr(zero,'lora_state',lambda backbone:{})
    monkeypatch.setattr(zero,'frozen_backbone_sha256',lambda backbone:cfg['frozen_backbone'])
    run=tmp_path/'output';probe=tmp_path/'probe'
    args=SimpleNamespace(device='cpu',text_checkpoint=str(text),link_checkpoint=str(link),
        seed=9127,train_count=32,dev_count=16,ood_count=16,test_count=16,
        output=str(run),steps=2,eval_every=1,batch_size=8,encoder_batch=8,lr=.001)
    t00.train(args,cfg)
    assert (run/'best.pt').is_file()
    check=torch.load(run/'best.pt',weights_only=True)
    assert check['status']=='T00_TRAINED_UNCERTIFIED' and 'numeric' in check and 'head' in check
    assert 'lora' not in check
    args.stage='probe';args.checkpoint=str(run/'best.pt');args.output=str(probe)
    args.split='dev';args.case_limit=8
    t00.probe(args,cfg)
    result=json.loads((probe/'result.json').read_text())
    assert result['lm_head_used'] is False
    assert result['recursion_implemented'] is False
    assert result['intact']['answers']+result['intact']['problems']==8
    assert len(result['checkpoint_sha256'])==64
    args.problem='6+6'
    response=t00.predict(args,cfg)
    assert response['input']=='PROBLEM: 6+6' and response['control'] in t00.ACTIONS
    assert response['lm_head_used'] is False and response['language_generated'] is False
    args.problem='(6+6)+3'
    response=t00.predict(args,cfg)
    assert response['input']=='PROBLEM: (6+6)+3'
    args.problem='not an arithmetic expression'
    with pytest.raises(ValueError,match='T00_PREDICT_SUPPORTS'):
        t00.predict(args,cfg)
    with pytest.raises(RuntimeError,match='REFUSES_NONEMPTY_OUTPUT'):
        t00.probe(args,cfg)
