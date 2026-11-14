"""CPU tests: genuine continuous feedback, learned exit, no LM-head shortcuts."""
from types import SimpleNamespace
from pathlib import Path
import json
import pytest
import torch
from torch import nn
from tools import nanojev_hybridlink_latent_loop_cutover as cut
from tools import nanojev_hybridlink_frozen_detector_cutover as fc
from tools import nanojev_qwen_numeric_dual_cutover as prev
from test_nanojev_hybridlink_frozen_detector_cutover import make_args
from test_nanojev_qwen_numeric_dual_cutover import DummyBackbone, DummyDecoder


def test_chains_root_to_answer_and_backward_suffix():
    rows=[{'source':'PROBLEM: 1+2','target':'ANSWER: 3','action':'ANSWER'},
          {'source':'PROBLEM: (2+(-1))+5','target':'PROBLEM: 1+5','action':'PROBLEM'},
          {'source':'PROBLEM: 1+5','target':'ANSWER: 6','action':'ANSWER'}]
    cs=cut.chains_from_rows(rows)
    assert sorted(map(len,cs))==[2,3]
    assert cs[-1][0].startswith('PROBLEM:')
    with pytest.raises(ValueError,match='ACTION_MISMATCH'):
        cut.chains_from_rows([dict(rows[0],action='PROBLEM')])
    with pytest.raises(RuntimeError,match='AMBIGUOUS'):
        cut.chains_from_rows([rows[0],dict(rows[0],target='ANSWER: 999')])
    with pytest.raises(RuntimeError,match='CYCLIC'):
        cut.chains_from_rows([{'source':'PROBLEM: 1+2','target':'PROBLEM: 2+1','action':'PROBLEM'},
                              {'source':'PROBLEM: 2+1','target':'PROBLEM: 1+2','action':'PROBLEM'}])


class StubBB(nn.Module):
    def __init__(self):
        super().__init__();self.embed=nn.Embedding(90,8)
        self.lm_head=nn.Linear(8,90)
    def get_input_embeddings(self):return self.embed
    def get_decoder(self):return self
    def forward(self,*,inputs_embeds,attention_mask,return_dict,use_cache):
        assert not use_cache
        pos=torch.cumsum(attention_mask,dim=1).unsqueeze(-1).float()
        hidden=inputs_embeds+inputs_embeds.cumsum(1)/(pos.clamp_min(1))
        return SimpleNamespace(last_hidden_state=hidden)


class StubTokenizer:
    def __call__(self,x,add_special_tokens=False):return {'input_ids':[1+ord(c)%60 for c in x]}


class StubNumeric(nn.Module):
    def __init__(self):super().__init__();self.w=nn.Parameter(torch.ones(1))
    def forward(self,h,scale):
        y=h[:,0].double()*self.w
        return {'direct_number':y,'complex_number':y,'a':y,'b':y*0,'delta_rho':y*0}


class AlwaysDetector(nn.Module):
    def __init__(self,constant):super().__init__();self.constant=nn.Parameter(torch.tensor(float(constant)))
    def forward(self,h):return self.constant.expand(h.shape[0],1)


def test_true_embedding_feedback_has_gradients_and_bypasses_vocabulary(monkeypatch):
    bb=StubBB();link=cut.make_link(8,8)
    x=cut.token_embeddings(bb,StubTokenizer(),'PROBLEM: 5+1','cpu',128)
    old=x.clone();source=torch.randn(1,8)
    seq,h=cut.feedback_step(bb,link,x,source,'cpu')
    assert seq.shape[1]==old.shape[1]+1
    assert torch.equal(seq[:,:-1],old)
    assert h.shape==(1,8)
    h.sum().backward()
    assert any(p.grad is not None and p.grad.abs().sum()>0 for p in link.parameters())
    assert bb.lm_head.weight.grad is None


def test_runtime_stops_on_detector_not_oracle(monkeypatch):
    bb=StubBB();num=StubNumeric();tok=StubTokenizer();link=cut.make_link(8,8)
    r=cut.rollout(bb,tok,link,AlwaysDetector(10),num,'PROBLEM: 5+1','cpu',128,64.,.9,1e-8,2,6)
    assert r['status']=='ANSWER' and r['steps']==1
    assert r['headless'] and not r['uses_gold_intermediate_states']
    with pytest.raises(ValueError,match='UNFINISHED'):
        cut.rollout(bb,tok,link,AlwaysDetector(10),num,'ANSWER: 6','cpu',128,64.,.9,1e-8,2,6)
    fail=cut.rollout(bb,tok,link,AlwaysDetector(-10),num,'PROBLEM: 5+1','cpu',128,64.,.9,0.,2,2)
    assert fail['status']=='BUDGET_EXHAUSTED' and fail['steps']==2


def test_stable_incomplete_not_an_answer():
    z=torch.tensor([1.,2.,3.])
    action,count,obs=cut.stopping_action(z,z,.001,.9,1e-5,0,2)
    assert action=='CONTINUE' and count==1
    action,count,obs=cut.stopping_action(z,z,.001,.9,1e-5,count,2)
    assert action=='STALLED' and count==2
    action,count,obs=cut.stopping_action(z,z,.999,.9,1e-5,count,2)
    assert action=='ANSWER'


def test_suffix_loss_uses_generated_state_and_differentiates():
    bb=StubBB();link=cut.make_link(8,8);det=nn.Linear(8,1)
    ch=['PROBLEM: 1+2','ANSWER: 3']
    inputs={'ANSWER: 3':torch.randn(8)}
    h=torch.randn(1,8)
    loss,meta=cut.train_suffix(bb,StubTokenizer(),link,det,ch,inputs,h,'cpu',128,.5)
    loss.backward()
    assert any(p.grad is not None for p in link.parameters())
    assert any(p.grad is not None for p in det.parameters())
    assert torch.isfinite(loss) and -1<=meta['alignment']<=1


def _allow_latent_embeddings(monkeypatch):
    orig=DummyDecoder.forward
    def forward(self,*,input_ids=None,inputs_embeds=None,attention_mask,use_cache,return_dict):
        if input_ids is not None:return orig(self,input_ids=input_ids,attention_mask=attention_mask,use_cache=use_cache,return_dict=return_dict)
        x=inputs_embeds
        h=(x*attention_mask[...,None]).sum(1)/attention_mask.sum(1)[:,None]
        return SimpleNamespace(last_hidden_state=self.q_proj(x)+self.v_proj(x)+h[:,None,:])
    monkeypatch.setattr(DummyDecoder,'forward',forward)
    monkeypatch.setattr(DummyBackbone,'get_input_embeddings',lambda self:self.decoder.embed,raising=False)


def test_patch3_trains_and_inspects_without_touching_parents(monkeypatch,tmp_path):
    _allow_latent_embeddings(monkeypatch)
    a=make_args(monkeypatch,tmp_path)
    a.steps=2;a.eval_every=1
    fc.train(a)
    a.detector_checkpoint=str(Path(a.output)/'best.pt')
    sha_p=prev.file_hash(a.stage1_checkpoint)
    sha_d=prev.file_hash(a.detector_checkpoint)
    a.output=str(tmp_path/'latent');a.checkpoint=None
    a.steps=2;a.eval_every=1;a.eval_cases=3;a.max_unroll=1;a.max_depth=2
    a.lr=.001;a.detector_lr=.001;a.completion_weight=.2
    a.drift_threshold=1e-8;a.stall_patience=2;a.reset_detector=False
    a.seed=23;a.control='intact'
    result=cut.train(a)
    assert result['status']=='GENERATED_FEEDBACK_TRAINED_NOT_CERTIFIED'
    assert result['qwen_unchanged'] and result['oracle_at_inference'] is False
    assert prev.file_hash(a.stage1_checkpoint)==sha_p
    assert prev.file_hash(a.detector_checkpoint)==sha_d
    ck=torch.load(Path(a.output)/'best.pt',weights_only=True)
    assert 'link' in ck and 'detector' in ck
    assert 'lora' not in ck and 'numeric' not in ck
    a.checkpoint=str(Path(a.output)/'best.pt')
    a.stage='inspect';a.state='PROBLEM: 6+6'
    monkeypatch.setattr(prev,'numeric_label',lambda _:(_ for _ in ()).throw(AssertionError('RUNTIME_ORACLE')))
    report=cut.evaluate(a)
    assert report['headless'] and not report['inference_oracle_used']
    assert report['history'] and not report['uses_gold_intermediate_states']
    a.stage='probe';a.split='dev';a.checkpoint=str(Path(a.output)/'best.pt')
    probe=cut.evaluate(a)
    assert probe['summary']['control']=='intact'
    assert probe['ablation']['control']=='zero_latent'
    assert probe['summary']['actual_generated_feedback'] is True
