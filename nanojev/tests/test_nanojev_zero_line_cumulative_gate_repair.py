"""Targeted tests for repairing a cumulative learned gate without oracle routing."""
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_zero_line_cumulative_gate_repair as repair
from tools.nanojev_zero_line_cumulative_bank import NeuralRouter
from tools.nanojev_addition_curriculum import target_digits


def logits(values):
    y=torch.tensor(values,dtype=torch.long)
    x=torch.full((len(y),73),-12.,dtype=torch.float)
    x[torch.arange(len(y)),y.sign()+1]=12.
    d=target_digits(y)
    for i in range(7):x[torch.arange(len(y)),3+i*10+d[:,i]]=12.
    return x


def cache(operands,incumbent,bank):
    x=torch.tensor(operands,dtype=torch.long)
    f=x.float()/256.
    return {'operands':x,'features':f,'tokens':f.clone(),'target':x.sum(-1),
            'incumbent':logits(incumbent),'new_logits':logits(bank),
            'prior_route':torch.zeros(len(x),dtype=torch.bool)}


def test_replay_targets_only_true_prior_wins():
    d=cache([[10,-8],[16,-11],[32,-16]],[2,5,100],[900,5,16])
    assert repair.hard_negative_indices(d).tolist()==[0]


def test_conservative_loss_penalizes_routing_to_bad_bank():
    d=cache([[2,-2],[170,-154]],[0,77],[999,16])
    original=torch.tensor([2.,-2.],requires_grad=True)
    masked=torch.tensor([True,False])
    loss=repair.conservative_replay_loss(original,d,masked,margin=1.5,weight=2.)
    loss.backward()
    assert original.grad[0] > 0.
    assert original.grad[1] == 0.
    assert repair.conservative_replay_loss(original,d,torch.tensor([False,False]),
                 margin=1.5,weight=2.).item()==0.


def test_inference_route_is_target_independent():
    gate=NeuralRouter(2,2,hidden=12)
    d=cache([[1,-1],[175,-159]],[0,100],[200,16])
    a=gate(d['features'],d['tokens']).detach().clone()
    d['target'][:]+=555
    b=gate(d['features'],d['tokens']).detach()
    assert torch.equal(a,b)


def test_repair_training_keeps_checkpoint_and_reports_full_retention(tmp_path):
    center=cache([[0,0],[1,-1],[2,5],[127,-127]]*5,[0,0,7,0]*5,[99]*20)
    old=cache([[128,-112],[159,-143],[150,-148],[142,-110]]*5,
              [16,16,2,32]*5,[77,16,2,77]*5)
    new=cache([[160,-144],[175,-153],[191,-159],[185,-169]]*5,
              [999]*20,[16,22,32,16]*5)
    gate=NeuralRouter(2,2,hidden=12)
    r,s,n=repair.train_repair(gate,center,old,new,old,old,new,
                  steps=4,probe_every=2,batch_size=12,lr=.001,seed=4,
                  margin=1.5,replay_weight=1.,conservative_shift=1.5,output=tmp_path)
    assert s in [0,2,4]
    assert n>0
    assert len(r)==3
    assert all('old_full' in row and 'protected_on_exhaustive_checks' in row for row in r)
    assert (tmp_path/'latest.pt').is_file()
    assert (tmp_path/'progress.json').is_file()


def test_provenance_refuses_modified_source(tmp_path):
    src=tmp_path/'original';src.mkdir()
    (src/'result.json').write_text(json.dumps({'schema':'nanojev.zero-line-cumulative-learned-bank.v1'}))
    (src/'bank_candidate.pt').write_bytes(b'broken')
    (src/'gate_candidate.pt').write_bytes(b'broken')
    # The provenance check must not quietly load missing or invalid weights.
    with pytest.raises(Exception):
        repair.load_candidate(src,{'checkpoint_sha256':'expected'},
                              type('Base',(),{'query':torch.zeros(2)})(),
                              tmp_path/'oldbank',tmp_path/'oldrouter','cpu',include_tokens=True)


def test_unavailable_checkpoint_refuses(tmp_path):
    with pytest.raises(RuntimeError, match='MISSING_CUMULATIVE_CANDIDATE'):
        repair.load_candidate(tmp_path,{'checkpoint_sha256':'x'},
                              object(),tmp_path/'old',tmp_path/'old2','cpu',include_tokens=True)


def test_refuses_training_without_original_candidate(tmp_path):
    args=['--ladder-output',str(tmp_path/'base'),
          '--prior-bank-output',str(tmp_path/'bank'),
          '--prior-router-output',str(tmp_path/'router'),
          '--output',str(tmp_path/'out'),'--device','cpu']
    with pytest.raises(ValueError,match='CUMULATIVE_OUTPUT_REQUIRED'):
        repair.main(args)

class FakeInput(torch.nn.Module):
    def forward(self,x):return x.float().unsqueeze(-1)/256.

class FakeBase(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.query=torch.nn.Parameter(torch.zeros(2))
        self.norm=torch.nn.Identity()
        self.input=FakeInput()
    def forward(self,x):
        _=self.norm(x.float()/256.)
        o=logits(x.sum(-1).tolist()).to(x.device)
        return {'sign':o[:,:3],'digits':o[:,3:].reshape(-1,7,10)}

class FakeOldBank(torch.nn.Module):
    def forward(self,f):
        v=(f*256.).round().long().sum(-1)
        return logits(v.tolist()).to(f.device)

class FakeOldRouter(torch.nn.Module):
    def forward(self,feats,tokens):
        return ((feats.abs()*256.).amax(-1)>127.).float()*20.-10.


def test_end_to_end_repairs_saved_gate_without_retraining_bank(tmp_path,monkeypatch):
    from tools.nanojev_zero_line_radius255_probe import sha256
    root=tmp_path/'base';root.mkdir()
    saved=tmp_path/'original_cumulative';saved.mkdir()
    checkpoint=root/'passed.pt';checkpoint.write_bytes(b'frozen-backbone')
    pb=root/'priorbank.pt';pb.write_bytes(b'frozen-prior-bank')
    pr=root/'priorrouter.pt';pr.write_bytes(b'frozen-prior-router')
    base=FakeBase()
    monkeypatch.setattr(repair,'load_certified_model',lambda *a:(base,{
        'checkpoint':checkpoint,'checkpoint_sha256':sha256(checkpoint)}))
    monkeypatch.setattr(repair,'load_prior',lambda *a:(FakeOldBank(),FakeOldRouter(),True,
                                                      pb,pr,{}))
    bank=repair.AbsoluteBank(2,8)
    gate=NeuralRouter(2,2,12,True)
    # Intentionally unpromoted warm-start artifacts from the ORIGINAL run.
    bp={'schema':'nanojev.zero-line-cumulative-learned-bank.v1.bank-candidate',
        'source_sha256':sha256(checkpoint),'band':[160,191],
        'hidden':8,'bank':bank.state_dict()}
    torch.save(bp,saved/'bank_candidate.pt')
    gp={'schema':'nanojev.zero-line-cumulative-learned-bank.v1.gate-candidate',
        'source_sha256':sha256(checkpoint),'prior_bank_sha256':sha256(pb),
        'prior_router_sha256':sha256(pr),'new_bank_sha256':sha256(saved/'bank_candidate.pt'),
        'new_band':[160,191],'feature_width':2,'token_width':2,
        'hidden':12,'include_tokens':True,'gate':gate.state_dict()}
    torch.save(gp,saved/'gate_candidate.pt')
    (saved/'result.json').write_text(json.dumps({
        'schema':'nanojev.zero-line-cumulative-learned-bank.v1','eligible':False}))
    monkeypatch.setattr(repair,'deck',lambda r:{'x':torch.tensor([[0,0],[127,-127],[9,5]]),
                                                'y':torch.tensor([0,0,14])})
    monkeypatch.setattr(repair,'_whole_shell',lambda *a:{
        'x':torch.tensor([[128,-128],[159,-143],[140,-135]]),
        'y':torch.tensor([0,16,5])})
    def samples(low,high,seed,per_stratum):
        x=[(low,-low,0,0,1),(low,-low+1,1,0,1),
           (high,-high+16,16,0,1),(high,-high-16,-16,0,1),
           (high,10,high+10,0,1),(-high,-low,-high-low,0,-1)]
        return {'train':x,'dev':x,'ood':x}
    monkeypatch.setattr(repair,'sample_absolute_band',samples)
    files=[checkpoint,pb,pr,saved/'bank_candidate.pt',saved/'gate_candidate.pt']
    before=[sha256(f) for f in files]
    output=tmp_path/'repair'
    args=repair.parser().parse_args([
        '--ladder-output',str(root),'--prior-bank-output',str(tmp_path/'bank'),
        '--prior-router-output',str(tmp_path/'router'),
        '--cumulative-output',str(saved),'--output',str(output),
        '--device','cpu','--steps','2','--probe-every','1',
        '--batch-size','8','--eval-batch-size','6','--per-stratum','6',
        '--cpu-threads','1'])
    report=repair.run(args)
    assert report['source_artifacts_immutable']
    assert [sha256(f) for f in files]==before
    assert (output/'gate_candidate.pt').is_file()
    assert (output/'bank_candidate.pt').is_file()
    assert report['evaluations']['old_full']['cards']==3
    assert report['evaluations']['center']['cards']==3
    assert report['selected_step'] in (0,1,2)
    assert report['hard_training_replay_count']>0
