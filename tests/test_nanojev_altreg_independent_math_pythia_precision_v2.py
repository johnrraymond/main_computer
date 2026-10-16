"""Standalone precision-v2 pythia tests; does not use another backbone's trainer."""
from pathlib import Path
import json
import sys
import types

import numpy as np
import pytest
import torch
from torch import nn

ROOT=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(ROOT))
import nanojev_altreg_independent_math_pythia as parent
import nanojev_altreg_independent_math_pythia_precision_v2 as target

class ToyTokenizer:
    pad_token_id=0
    eos_token_id=1
    def __call__(self,text,add_special_tokens=False):
        return {'input_ids':[2+ord(c)%251 for c in text]}

class ToyLM(nn.Module):
    def __init__(self):
        super().__init__()
        self.emb=nn.Embedding(260,4)
        self.config=types.SimpleNamespace(num_hidden_layers=2,model_type='toy',_commit_hash='toy')
        self.eval()
        for p in self.parameters():p.requires_grad_(False)

    def get_input_embeddings(self):return self.emb

    def forward(self,input_ids,attention_mask,output_hidden_states=True,use_cache=False):
        x=self.emb(input_ids)
        seq=torch.arange(x.shape[1],device=x.device).reshape(1,-1,1).float()
        h1=(x*(1.+seq*.01)).cumsum(1)/torch.clamp(seq+1,min=1.)
        h2=h1*1.17+x*.05
        return types.SimpleNamespace(hidden_states=(x,h1,h2))

def loader(name,args):
    assert name==target.BACKBONE
    return torch,ToyLM(),ToyTokenizer(),'cpu',{
       'source':target.MODEL_ID,'resolved_commit':'toy',
       'dtype':'torch.float32','num_hidden_layers':2,'model_type':'toy'}

def args(mod,root,*extra):
    return mod.parser().parse_args(['--results-root',str(root),'--device','cpu',
       '--dtype','float32','--batch-size','2','--width','16',
       '--val-examples','2','--test-examples','2','--max-prompt-tokens','512',
       '--eval-every','5','--report-every','5',*extra])

def test_independent_identity_and_smoke():
    assert target.BACKBONE=='pythia'
    assert target.MODEL_ID==parent.MODEL_ID
    assert target.DEFAULT_ROOT.name=='independent_altreg_math_pythia_precision_v2'
    source=Path(target.__file__).read_text()
    assert 'import nanojev_altreg_independent_math_' not in source
    assert '16:3:1' in source
    assert 'precision_aware_loss' in source
    assert target.self_test()==0

def test_precision_tolerance_is_exact_and_has_finite_gradients():
    values=np.array([[0.,1.],[-3.,7.],[2e6,-2e6],[100.,-100.]])
    tolerance=target.numeric_coordinate_tolerance(values,.05,'cpu').numpy()
    asinh=np.arcsinh(values)/target.X_SCALE
    for sign in [-1,1]:
        decoded=target.numeric_unscale(asinh+sign*tolerance,target.X_SCALE)
        err=np.abs(decoded-values)/np.maximum(1.,np.abs(values))
        assert np.max(err)<.050001
    t=torch.tensor(asinh,dtype=torch.float32)
    assert float(target.precision_aware_loss(t,t,values))==0.0
    prediction=(t+.08).detach().requires_grad_(True)
    loss=target.precision_aware_loss(prediction,t,values)
    assert torch.isfinite(loss)
    loss.backward()
    assert prediction.grad is not None
    assert bool(torch.isfinite(prediction.grad).all())
    assert float(loss.detach())>0
    assert target.parser().parse_args(['--precision-weight','0']).precision_weight==0.0
    with pytest.raises(ValueError,match='INVALID_PRECISION_TOLERANCE'):
        target.numeric_coordinate_tolerance(values,0,'cpu')

def test_fork_train_resume_and_evaluation_independent(tmp_path,monkeypatch):
    monkeypatch.setattr(parent.cassette,'load_source',loader)
    # Construct a genuine v1 training + best checkpoint with correct optimizer
    # moments and matching frozen held-out captures.
    src=tmp_path/'v1'
    assert parent.execute(args(parent,src,'--steps','20'))==0
    old=torch.load(src/'best.pt',map_location='cpu',weights_only=True)
    assert old['step']>0
    first=tmp_path/'v2'
    flags=['--fork-v1-root',str(src),'--precision-weight','0.01',
           '--precision-relative-tolerance','0.05','--precision-huber-beta','1']
    seen=[]
    capture_before=target.capture
    def capture_watched(frozen,tokenizer,device,prompts,max_tokens):
        seen.extend(prompts)
        return capture_before(frozen,tokenizer,device,prompts,max_tokens)
    monkeypatch.setattr(target,'capture',capture_watched)
    assert target.execute(args(target,first,'--steps','20',*flags))==0
    # Every non-INTERMEDIATE input is kept in original prompt order.
    assert len(seen)==40
    assert all(p.startswith(target.registers.FIRST+' =') for p in seen[32:])
    for p0,p1 in zip(seen[:32:2],seen[1:32:2]):
        assert p0.startswith(target.registers.FIRST+' =')
        assert p1.startswith(target.registers.SECOND+' =')
    fork=torch.load(first/'fork.pt',map_location='cpu',weights_only=True)
    v2=torch.load(first/'latest.pt',map_location='cpu',weights_only=True)
    assert fork['step']==old['step']
    assert v2['step']==old['step']+20
    assert fork['config']['parent_v1']['sha256']==target.digest((src/'best.pt').read_bytes())
    assert fork['stream']==old['stream']
    assert fork['phase_counts']==old['phase_counts']
    assert fork['head'].keys()==old['head'].keys()
    for key in fork['head']:
        assert torch.equal(fork['head'][key],old['head'][key])
    assert set(fork['optimizer'])==set(old['optimizer'])
    assert (first/'best-asinh.pt').is_file()
    assert (first/'best-precision.pt').is_file()
    assert (first/'test-last.json').is_file()
    assert (first/'fork.json').is_file()
    assert (first/'validation-cache.pt').is_file()
    val=json.loads((first/('validation-step-%08d.json'%v2['step'])).read_text())['validation']
    for order in ('original','reversed','averaged'):
        for metric in ('x_5pct','y_5pct','x_1pct','y_1pct','x_0p1pct','y_0p1pct'):
            assert metric in val[order]
    assert v2['best']['asinh']['step']>=old['step']
    assert v2['best']['precision']['step']>=old['step']
    # No v1 checkpoint is changed by the precision trainer.
    assert target.digest((src/'best.pt').read_bytes())==fork['config']['parent_v1']['sha256']
    assert target.execute(args(target,first,'--resume','--additional-steps','5',*flags))==0
    resumed=torch.load(first/'latest.pt',map_location='cpu',weights_only=True)
    assert resumed['step']==old['step']+25
    assert resumed['stream']['draws']==old['stream']['draws']+50
    assert target.execute(args(target,first,'--eval-only',*flags))==0
    assert target.execute(args(target,first,'--eval-only','--eval-checkpoint','best-precision',*flags))==0
    assert target.execute(args(target,first,'--eval-only','--eval-checkpoint','best-asinh',*flags))==0
    assert target.execute(args(target,first,'--eval-only','--eval-checkpoint','fork',*flags))==0
    assert len(list(tmp_path.iterdir()))==2
    with pytest.raises(RuntimeError,match='RESULTS_ROOT_NOT_EMPTY'):
        target.execute(args(target,first,'--steps','2',*flags))
    with pytest.raises(RuntimeError,match='RESUME_IDENTITY_OR_CONFIG_DRIFT'):
        target.execute(args(target,first,'--resume','--additional-steps','1','--precision-weight','0.02',*flags[4:]))
    ck=torch.load(first/'latest.pt',map_location='cpu',weights_only=True)
    ck['config']['backbone']='forbidden-cross-backbone'
    torch.save(ck,first/'latest.pt')
    with pytest.raises(RuntimeError,match='RESUME_IDENTITY_OR_CONFIG_DRIFT|V2_WRONG_BACKBONE'):
        target.execute(args(target,first,'--resume','--additional-steps','1',*flags))

def test_v1_source_validation_and_wrong_backbone_fail_closed(tmp_path,monkeypatch):
    monkeypatch.setattr(parent.cassette,'load_source',loader)
    with pytest.raises(FileNotFoundError,match='V1_BEST_OR_CACHE_MISSING'):
        target.execute(args(target,tmp_path/'v2','--steps','1','--fork-v1-root',str(tmp_path/'missing')))
    source=tmp_path/'v1'
    assert parent.execute(args(parent,source,'--steps','20'))==0
    ck=torch.load(source/'best.pt',map_location='cpu',weights_only=True)
    ck['backbone']='other'
    torch.save(ck,source/'best.pt')
    with pytest.raises(RuntimeError,match='V1_WRONG_BACKBONE'):
        target.execute(args(target,tmp_path/'v2','--steps','1','--fork-v1-root',str(source)))
