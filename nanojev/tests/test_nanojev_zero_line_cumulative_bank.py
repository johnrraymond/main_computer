"""No-oracle / cumulative-retention tests for next absolute bank."""
import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_zero_line_cumulative_bank as cb
from tools.nanojev_addition_curriculum import target_digits


def logits(values):
    y=torch.as_tensor(values,dtype=torch.long)
    out=torch.full((len(y),73),-9.,dtype=torch.float)
    out[torch.arange(len(y)),y.sign()+1]=9.
    digits=target_digits(y)
    for j in range(7):
        out[torch.arange(len(y)),3+10*j+digits[:,j]]=9.
    return out


def data(inputs,incumbent,new,features=None):
    xs=torch.tensor(inputs,dtype=torch.long)
    d=xs.float() / 256. if features is None else features
    return {'operands':xs,'target':xs.sum(-1),'features':d,'tokens':d.clone(),
            'incumbent':logits(incumbent),'prior_route':torch.tensor([False]*len(xs)),
            'new_logits':logits(new)}


class FixedGate(nn.Module):
    def __init__(self,values):
        super().__init__();self.register_buffer('values',torch.tensor(values,dtype=torch.float))
    def forward(self,feats,tokens):return self.values[:len(feats)]


def test_neural_selection_does_not_use_oracle_fallback():
    old=logits([0,1]);bank=logits([7,9])
    selected,route=cb.new_neural_selection(torch.tensor([20.,-20.]),old,bank)
    assert route.tolist()==[True,False]
    assert torch.equal(selected[0],bank[0])
    assert torch.equal(selected[1],old[1])


def test_wrong_switch_is_scored_not_repaired():
    d=data([[0,0],[173,-157]],[0,999],[999,16])
    first=cb.evaluate(FixedGate([20.,-20.]),d)
    assert first['learned_accuracy']==0.
    assert first['newly_wrong_vs_incumbent']==1
    assert first['incorrect_with_correct_alternative']==2
    second=cb.evaluate(FixedGate([-20.,20.]),d)
    assert second['learned_accuracy']==1.
    assert second['newly_wrong_vs_incumbent']==0
    assert second['input_magnitude_reference_accuracy']==1.


def test_gold_mutation_does_not_change_routing():
    net=cb.NeuralRouter(2,2,hidden=8)
    d=data([[2,-2],[175,-159]],[0,0],[888,16])
    before=net(d['features'],d['tokens']).detach().clone()
    d['target']+=5000
    assert torch.equal(before,net(d['features'],d['tokens']))


def test_three_choice_stays_neural():
    prior_logits=logits([1,4]); oldbank=logits([6,7]);newbank=logits([8,9])
    incumbent,pr=cb.hard_select(torch.tensor([-3.,3.]),prior_logits,oldbank)
    output,nw=cb.new_neural_selection(torch.tensor([-1.,1.]),incumbent,newbank)
    assert pr.tolist()==[False,True]
    assert nw.tolist()==[False,True]
    assert torch.equal(output[0],prior_logits[0])
    assert torch.equal(output[1],newbank[1])


def test_exhaustive_shell_cardinality():
    d=cb._whole_shell(128,159)
    assert d['x'].shape==(36736,2)
    assert torch.all(d['y']==d['x'].sum(-1))
    assert int(d['x'].abs().amax(-1).min())==128
    assert int(d['x'].abs().amax(-1).max())==159


def test_paths_fail_closed(tmp_path):
    paths=[tmp_path/x for x in ('base','bank','router')]
    for p in paths:p.mkdir()
    for root in paths:
        with pytest.raises(RuntimeError,match='OVERLAP'):
            cb.require_paths(root/'child',paths)
    target=tmp_path/'output';target.mkdir();(target/'keep').write_text('safe')
    with pytest.raises(FileExistsError):cb.require_paths(target,paths)
    assert (target/'keep').read_text()=='safe'


def test_prior_requires_real_promoted_artifacts(tmp_path, monkeypatch):
    base=nn.Linear(2,2)
    saved=tmp_path/'old';saved.mkdir()
    monkeypatch.setattr(cb,'validate_bank',lambda *a:(base,tmp_path/'bank.pt',{},))
    with pytest.raises(RuntimeError,match='NOT_PROMOTED'):
        cb.load_prior({'checkpoint_sha256':'xx'},base,tmp_path,saved,'cpu')


def test_gate_training_uses_final_sum_and_can_preserve_old(tmp_path):
    old=data([[1,-1],[5,-5],[8,-6],[20,-17]]*12,[0,0,2,3]*12,[10]*48)
    previous=data([[128,-112],[139,-130],[145,-143],[159,-140]]*12,
                  [16,9,2,19]*12,[55]*48)
    new=data([[160,-144],[175,-153],[182,-180],[191,-175]]*12,
             [999]*48,[16,22,2,16]*12)
    gate=cb.NeuralRouter(2,2,hidden=16)
    history,step=cb.train_gate(gate,old,previous,new,previous,new,
           steps=4,probe_every=2,batch_size=12,learning_rate=.001,seed=7,output=tmp_path)
    assert step in (2,4)
    assert len(history)==2
    assert (tmp_path/'latest.pt').exists()
    assert (tmp_path/'progress.json').exists()


def test_real_additionnet_feature_path_cpu():
    from tools.nanojev_addition_curriculum import AdditionNet
    torch.manual_seed(3)
    base=AdditionNet('digit_tokens',width=16,heads=2,layers=1,rounds=1,seed=3)
    base.eval().requires_grad_(False)
    pairs=torch.tensor([[0,0],[175,-159]],dtype=torch.long)
    cache=cb.cache_with_tokens(base,{'x':pairs,'y':pairs.sum(-1)},'cpu',2,True)
    assert cache['features'].shape==(2,16)
    assert cache['tokens'].shape==(2,14*16)
    gate=cb.NeuralRouter(16,14*16,hidden=12)
    before=gate(cache['features'],cache['tokens']).detach()
    assert torch.equal(before,gate(cache['features'],cache['tokens']))

class _FakeInput(nn.Module):
    def forward(self,x):return x.float().unsqueeze(-1)/256.

class _FakeBase(nn.Module):
    def __init__(self):
        super().__init__()
        self.query=nn.Parameter(torch.zeros(2))
        self.norm=nn.Identity()
        self.input=_FakeInput()
    def forward(self,x):
        _=self.norm(x.float()/256.)
        value=x.sum(-1)
        val=torch.where(x.abs().amax(-1)<=127,value,value+100)
        outputs=logits(val.tolist()).to(x.device)
        return {'sign':outputs[:,:3],'digits':outputs[:,3:].reshape(-1,7,10)}

class _FakeOldBank(nn.Module):
    def forward(self,features):
        operands=(features*256.).round().long()
        v=operands.sum(-1)
        return logits(v.tolist()).to(features.device)

class _FakeRouter(nn.Module):
    def forward(self,features,tokens):
        m=(features.abs()*256.).amax(-1)
        return (m>127.).float()*20-10


def test_full_synthetic_cumulative_run_is_independent(tmp_path,monkeypatch):
    checkpoint=tmp_path/'source.pt';checkpoint.write_bytes(b'gold')
    prior_bank=tmp_path/'prior.pt';prior_bank.write_bytes(b'previous-bank')
    prior_router=tmp_path/'prior-router.pt';prior_router.write_bytes(b'previous-route')
    base=_FakeBase()
    monkeypatch.setattr(cb,'load_certified_model',lambda path,device:(base,{
          'checkpoint':checkpoint,'checkpoint_sha256':cb.sha256(checkpoint),'step':4800}))
    monkeypatch.setattr(cb,'load_prior',lambda *a:(_FakeOldBank(),_FakeRouter(),True,
                                                prior_bank,prior_router,{'parameters':{'hidden':12}}))
    monkeypatch.setattr(cb,'deck',lambda r:{'x':torch.tensor([[0,0],[127,-127],[9,5]]),
                                           'y':torch.tensor([0,0,14])})
    def fake_sample(lo,hi,seed,per_stratum,extrapolation=False):
        rows=[(lo,-lo,0,0,1),(lo,-lo+1,1,0,1),(lo,-lo+2,2,0,1),
              (hi,-hi+16,16,0,1),(hi,-hi-16,-16,0,1),
              (hi,10,hi+10,0,1),(-hi,-lo,-hi-lo,0,-1)]
        return {'ood':rows} if extrapolation else {'train':rows,'dev':rows,'ood':rows}
    monkeypatch.setattr(cb,'sample_absolute_band',fake_sample)
    monkeypatch.setattr(cb,'_whole_shell',lambda low,high:{'x':torch.tensor([[128,-128],[159,-143],[140,-135]]),
                                                          'y':torch.tensor([0,16,5])})
    out=tmp_path/'run'
    args=cb.parser().parse_args(['--ladder-output',str(tmp_path/'ladder'),
        '--prior-bank-output',str(tmp_path/'existing-bank'),
        '--prior-router-output',str(tmp_path/'existing-router'),
        '--output',str(out),'--device','cpu','--steps-per-bank','2',
        '--probe-every','1','--router-steps','2','--router-probe-every','1',
        '--batch-size','6','--eval-batch-size','6','--hidden','8',
        '--router-hidden','8','--per-stratum','4'])
    original=[f.read_bytes() for f in (checkpoint,prior_bank,prior_router)]
    report=cb.run(args)
    assert report['no_python_fallback'] and report['no_oracle_inference']
    assert report['evaluations']['center_full']['prior_accuracy']==1.
    assert (out/'bank_candidate.pt').is_file()
    assert (out/'gate_candidate.pt').is_file()
    assert report['evaluations']['prior_band_full']['cards']==3
    assert original==[f.read_bytes() for f in (checkpoint,prior_bank,prior_router)]
