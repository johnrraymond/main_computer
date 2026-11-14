import pytest
import torch
from torch import nn

from tools import nanojev_zero_line_absolute_bank_audit as audit
from tools.nanojev_addition_curriculum import target_digits


def logits_for(values):
    y=torch.as_tensor(values,dtype=torch.long)
    z=torch.full((len(y),73),-20.,dtype=torch.float32)
    z[torch.arange(len(y)),y.sign()+1]=20.
    d=target_digits(y)
    for k in range(7):z[torch.arange(len(y)),3+10*k+d[:,k]]=20.
    return z


class FakeBank(nn.Module):
    def __init__(self):super().__init__()
    def forward(self,features):
        # Echo target stored in fake FEATURE rather than evaluator's target.
        return logits_for(features[:,0].long())


def fake_make_cache(_model,rows,_device,_batch_size):
    x=torch.tensor([(a,b) for a,b,*_ in rows],dtype=torch.long)
    y=torch.tensor([d for a,b,d,*_ in rows],dtype=torch.long)
    # Features computed from operands only.
    feat=(x[:,0]+x[:,1]).float().unsqueeze(1)
    # Deliberately flawed base, to exercise new corrections and regressions.
    orig=logits_for(torch.zeros(len(y),dtype=torch.long))
    return {'features':feat,'target':y,'operands':x,'original':orig}


def test_full_shell_exact_cartesian_completeness():
    vals=list(audit.full_shell(3,4))
    assert len(vals)==(2*4+1)**2-(2*2+1)**2
    assert len({(a,b) for a,b,*_ in vals})==len(vals)
    assert all(s==a+b for a,b,s,*_ in vals)
    with pytest.raises(ValueError):list(audit.full_shell(0,5))


def test_every_pair_audited_with_base_and_specialist(monkeypatch):
    monkeypatch.setattr(audit,'make_cache',fake_make_cache)
    report=audit.evaluate(None,FakeBank(),3,4,'cpu',7,20261025)
    assert report['total']['cards']==56
    assert report['total']['bank_accuracy']==1.
    assert report['total']['baseline_accuracy']<1.
    assert report['total']['newly_wrong']==0
    assert report['commutativity']['prediction_agreement']==1.
    assert report['sign_negation']['prediction_agreement']==1.
    assert sum(v['cards'] for v in report['holdout_partition'].values())==56


def test_mutating_labels_cannot_modify_neural_output(monkeypatch):
    monkeypatch.setattr(audit,'make_cache',fake_make_cache)
    assert audit.target_independence_check(None,FakeBank(),'cpu')


def test_oracle_shortcut_detected(monkeypatch):
    def oracle_cache(model,rows,device,batch_size):
        c=fake_make_cache(model,rows,device,batch_size)
        c['features']=c['target'].float().unsqueeze(1)
        return c
    monkeypatch.setattr(audit,'make_cache',oracle_cache)
    with pytest.raises(RuntimeError,match='TARGET_DEPENDENCE'):
        audit.target_independence_check(None,FakeBank(),'cpu')
