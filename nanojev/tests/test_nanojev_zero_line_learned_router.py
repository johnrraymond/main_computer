import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_zero_line_learned_router as lr
from tools.nanojev_addition_curriculum import target_digits


def as_logits(values):
    y=torch.tensor(values,dtype=torch.long)
    out=torch.full((len(y),73),-10.,dtype=torch.float)
    out[torch.arange(len(y)),1+y.sign()]=10.
    digits=target_digits(y)
    for j in range(7):
        out[torch.arange(len(y)),3+10*j+digits[:,j]]=10.
    return out


def make_data(operands, expert, specialist, band=(128,159), features=None):
    x=torch.tensor(operands,dtype=torch.long)
    y=x.sum(1)
    if features is None:features=x.float()/256.
    return {'operands':x,'target':y,'original':as_logits(expert),
            'features':features,'tokens':x.float().flatten(1)/256.,
            'bank_logits':as_logits(specialist),'band':band}


def test_hard_selector_has_no_oracle_override():
    a=as_logits([0,1])
    b=as_logits([4,4])
    logits, route=lr.hard_select(torch.tensor([-100.,100.]),a,b)
    assert route.tolist()==[False,True]
    assert torch.equal(logits[0],a[0])
    assert torch.equal(logits[1],b[1])


def test_train_loss_only_uses_targets_in_loss():
    a=as_logits([0,3])
    b=as_logits([1,4])
    logit=torch.tensor([-2.,2.],requires_grad=True)
    loss=lr.mixture_loss(logit,a,b,torch.tensor([0,4]))
    loss.backward()
    assert torch.isfinite(logit.grad).all()
    assert torch.isfinite(loss)
    assert loss.item()>0.
    assert not torch.equal(lr.gold_log_likelihood(a,torch.tensor([0,4])),
                           lr.gold_log_likelihood(a,torch.tensor([1,4])))


def test_target_mutation_cannot_change_router_prediction():
    router=lr.NeuralRouter(2,2,16,True)
    features=torch.tensor([[.1,.2],[.3,.4]])
    tokens=torch.tensor([[.5,.6],[.7,.8]])
    data=make_data([[1,-1],[145,-130]],[0,0],[4,15],features=features)
    data['tokens']=tokens
    output=router(data['features'],data['tokens']).detach().clone()
    data['target']+=9000
    assert torch.equal(router(data['features'],data['tokens']),output)


def test_reference_score_is_not_training_selection():
    # The router is deliberately WRONG on both examples. It must not be rescued
    # by input-magnitude reference routing, even when the correct answer exists.
    center=make_data([[1,-1],[140,-124]],[0,0],[12,16])
    class WrongGate(nn.Module):
        def forward(self,features,tokens):return torch.full((len(features),),100.)
    metrics=lr.evaluate_router(WrongGate(),center,center['bank_logits'])
    assert metrics['learned_arithmetic_accuracy']==.5
    assert metrics['deterministic_input_magnitude_accuracy']==1.
    assert metrics['newly_wrong_vs_base']==1
    assert metrics['oracle_any_expert_upper_bound']==1.


def test_center_destruction_detected_even_when_model_correct_elsewhere():
    data=make_data([[1,-1],[30,31],[145,-129]],
                   [0,61,0],[99,99,16])
    class AlwaysBank(nn.Module):
        def forward(self,x,t):return torch.ones(len(x))*12
    m=lr.evaluate_router(AlwaysBank(),data,data['bank_logits'])
    assert m['learned_arithmetic_accuracy'] == pytest.approx(1/3)
    assert m['newly_wrong_vs_base']==2
    assert m['learned_selected_bank_count']==3


def test_cpu_router_optimizes_final_answers_and_not_labels():
    center=make_data([[0,0],[4,-4],[10,-8],[16,-15]]*8,
                     [0,0,2,1]*8,[19,19,19,19]*8)
    new=make_data([[128,-112],[130,-114],[135,-117],[159,-129]]*8,
                  [0,0,0,0]*8,[16,16,18,30]*8)
    router=lr.NeuralRouter(2,2,16,True)
    logs, best_step=lr.train_router(router,center,new,center,new,steps=4,batch_size=12,
                         probe_every=2,seed=3,learning_rate=.001)
    assert len(logs)==2 and best_step in (2,4)
    assert logs[-1]['center']['cards']==len(center['target'])
    assert logs[-1]['band_dev']['cards']==len(new['target'])


def test_source_and_output_overlap_rejected(tmp_path):
    original=tmp_path/'src'; original.mkdir()
    bank=tmp_path/'bank'; bank.mkdir()
    for target in (original,original/'nested',bank,bank/'sub'):
        with pytest.raises(RuntimeError,match='OVERLAP'):
            lr.is_safe_output(target,original,bank)
    out=tmp_path/'out';out.mkdir();(out/'file').write_text('untouched')
    with pytest.raises(FileExistsError):lr.is_safe_output(out,original,bank)
    assert (out/'file').read_text()=='untouched'


class FakeBase(nn.Module):
    def __init__(self):
        super().__init__()
        self.query=nn.Parameter(torch.zeros(2))
        self.input=nn.Identity()
        self.norm=nn.Identity()

    def forward(self,x):
        _=self.norm(x.float())
        logits=as_logits((x[:,0]+x[:,1]).tolist()).to(x.device)
        return {'sign':logits[:,:3], 'digits':logits[:,3:].reshape(-1,7,10)}

class FakeBank(nn.Module):
    def forward(self, features):
        # Synthetic logits-only specialist based on test-provided features.
        a=features[:,0].round().long()
        b=features[:,1].round().long()
        y=a+b
        guess=torch.where(a.abs().maximum(b.abs())>127,y,y+100)
        return as_logits(guess.tolist()).to(features.device)


def fake_make_cache(base, cards, device, batch_size):
    if isinstance(cards,dict):
        operands=cards['x'].clone()
        targets=cards['y'].clone()
    else:
        operands=torch.tensor([r[:2] for r in cards],dtype=torch.long)
        targets=torch.tensor([r[2] for r in cards],dtype=torch.long)
    a=operands[:,0];b=operands[:,1]
    correct=a+b
    original_guess=torch.where(operands.abs().amax(-1)<=127,correct,correct+100)
    return {'features':operands.float(),'original':as_logits(original_guess.tolist()),
            'target':targets,'operands':operands}


def test_end_to_end_cpu_synthetic_run_and_inference(tmp_path,monkeypatch):
    file=tmp_path/'certified.pt';file.write_bytes(b'certified')
    specialist=tmp_path/'specialist.pt';specialist.write_bytes(b'specialist')
    base=FakeBase()
    monkeypatch.setattr(lr,'load_certified_model',lambda root,device:(base,{
        'checkpoint':file,'checkpoint_sha256':lr.sha256(file),'step':4800}))
    monkeypatch.setattr(lr,'validate_bank',lambda source,trained,low,high,device,base:
                        (FakeBank(),specialist,{'parameters':{'hidden':8}}))
    monkeypatch.setattr(lr,'make_cache',fake_make_cache)
    monkeypatch.setattr(lr,'deck',lambda n:{'x':torch.tensor([[0,0],[3,-3],[127,-127]]),
                                          'y':torch.tensor([0,0,0])})
    def fake_sample(lo,hi,seed,per_stratum,extrapolation=False):
        cards=[(lo,-lo,0,0,1),(hi,-hi+1,1,0,1),
               (lo,0,lo,0,1),(hi,-lo,hi-lo,0,1)]
        if extrapolation:return {'ood':cards}
        return {'train':cards,'dev':cards,'ood':cards}
    monkeypatch.setattr(lr,'sample_absolute_band',fake_sample)
    args=lr.parser().parse_args(['--ladder-output',str(tmp_path/'ladder'),
         '--bank-output',str(tmp_path/'saved-bank'),'--output',str(tmp_path/'router-run'),
         '--device','cpu','--steps','2','--probe-every','1','--batch-size','4',
         '--eval-batch-size','16','--per-stratum','4','--hidden','8'])
    before=file.read_bytes(),specialist.read_bytes()
    r=lr.run(args)
    assert r['source_immutable'] and r['bank_immutable'] and r['no_python_fallback']
    assert (file.read_bytes(),specialist.read_bytes())==before
    assert (tmp_path/'router-run'/'candidate.pt').exists()
    assert json.loads((tmp_path/'router-run'/'result.json').read_text())['no_oracle_inference']
    prediction=lr.independent_inference(base,FakeBank(),
                           lr.NeuralRouter(2,2,8,True),149,-133,'cpu',True)
    assert prediction['selected'] in ('bank','base')
    assert prediction['a']==149 and prediction['b']==-133



def test_actual_additionnet_feature_path_cpu():
    from tools.nanojev_addition_curriculum import AdditionNet
    torch.manual_seed(7)
    model=AdditionNet('digit_tokens',width=16,heads=2,layers=1,rounds=1,seed=7)
    model.eval().requires_grad_(False)
    data={'x':torch.tensor([[0,0],[127,-127],[149,-133]],dtype=torch.long),
          'y':torch.tensor([0,0,16],dtype=torch.long)}
    cache=lr.cache_with_tokens(model,data,'cpu',2,True)
    assert cache['features'].shape==(3,16)
    assert cache['tokens'].shape==(3,14*16)
    router=lr.NeuralRouter(16,14*16,hidden=12)
    raw=router(cache['features'],cache['tokens'])
    assert raw.shape==(3,)
    altered={'x':data['x'].clone(),'y':data['y']+1000}
    altered_cache=lr.cache_with_tokens(model,altered,'cpu',2,True)
    assert torch.equal(cache['features'],altered_cache['features'])
    assert torch.equal(cache['tokens'],altered_cache['tokens'])
    assert torch.equal(raw,router(altered_cache['features'],altered_cache['tokens']))
