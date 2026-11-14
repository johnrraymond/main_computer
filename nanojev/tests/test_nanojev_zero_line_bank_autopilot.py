"""No-oracle, correct planning, resumable/fail-closed iteration smoke checks."""
import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_zero_line_bank_autopilot as auto
from tools.nanojev_addition_curriculum import AdditionNet, target_digits


def exact_logits(ys):
    yy=torch.as_tensor(ys,dtype=torch.long)
    out=torch.full((len(yy),73),-12.,dtype=torch.float)
    out[torch.arange(len(yy)),yy.sign()+1]=12.
    for j in range(7):
        out[torch.arange(len(yy)),3+10*j+target_digits(yy)[:,j]]=12.
    return out


class FakeInput(nn.Module):
    def forward(self,x):return (x.float()/256.)[:,None,:].expand(-1,14,-1)


class FakeBase(nn.Module):
    def __init__(self):
        super().__init__()
        self.query=nn.Parameter(torch.zeros(2))
        self.norm=nn.Identity()
        self.input=FakeInput()
    def forward(self,x):
        self.norm(x.float()/256.)
        o=exact_logits(x.sum(-1).tolist()).to(x.device)
        return {'sign':o[:,:3],'digits':o[:,3:].reshape(-1,7,10)}


class DummyBank(nn.Module):
    def forward(self,f):
        return exact_logits((f*256.).round().long().sum(-1).tolist()).to(f.device)


class FixedRouter(nn.Module):
    def __init__(self,logit):super().__init__();self.logit=logit
    def forward(self,f,t):return f.new_full((len(f),),self.logit)


def test_doubling_schedule_covers_every_magnitude():
    bands=auto.proposed_bands()
    assert len(bands)==30
    assert bands[:4]==[[192,223],[224,255],[256,319],[320,383]]
    assert bands[-1]==[1572992,2000000]
    p=191
    for lo,hi in bands:
        assert lo==p+1 and hi>=lo
        p=hi
    assert p==2_000_000


def test_sample_is_disjoint_and_scale_safe():
    for lo,hi in [(128,159),(192,223),(320,383),(1_572_992,2_000_000)]:
        s=auto.sample_band(lo,hi,17,8)
        keys=[]; mags=[]
        for name,rows in s.items():
            assert rows
            for a,b,y,_,_ in rows:
                assert y==a+b
                assert lo<=max(abs(a),abs(b))<=hi
                assert auto.fold(a,b,17)==name
                assert (a,b) not in keys
                keys.append((a,b))
            mags.append({max(abs(a),abs(b)) for a,b,*_ in rows})
        assert mags[2].isdisjoint(mags[0]|mags[1])
    assert auto.sample_band(192,223,17,8)==auto.sample_band(192,223,17,8)


def test_zero_line_and_far_cards_both_present():
    d=auto.sample_band(192,223,22,12)
    for split in d:
        y=[abs(x[2]) for x in d[split]]
        assert 0 in y and any(v in (1,2) for v in y)
        assert any(16<=v<=32 for v in y)
        assert any(v>=65 for v in y)


def test_hard_chain_does_not_rescue_wrong_routes_or_use_target():
    base=FakeBase()
    chain=auto.FrozenChain(base,DummyBank(),FixedRouter(-100.))
    bank=DummyBank()
    # Force choosing the bank and verify the decision cannot be changed by labels.
    chain.append((160,191),bank,FixedRouter(100.))
    operands=torch.tensor([[0,0],[175,-159]])
    cached=auto.cache_with_tokens(base,{'x':operands,'y':operands.sum(-1)},'cpu',2,True)
    a,_=chain.existing(cached)
    cached['target']=torch.tensor([9_999_999,9_999_999])
    b,_=chain.existing(cached)
    assert torch.equal(a,b)
    wrong=auto.eval_gate(FixedRouter(100.),{'features':torch.zeros(1,2),
        'tokens':torch.zeros(1,2),'target':torch.tensor([0]),
        'incumbent':exact_logits([0]),'new_logits':exact_logits([777])})
    assert wrong['new_harm']==1 and wrong['accuracy']==0.


def test_real_additionnet_features_handle_two_million():
    m=AdditionNet('digit_tokens',width=16,heads=2,layers=1,rounds=1,seed=3)
    m.eval().requires_grad_(False)
    rows={'x':torch.tensor([[2_000_000,-1_999_984],[1_800_001,199_999]]),
          'y':torch.tensor([16,2_000_000])}
    cache=auto.cache_with_tokens(m,rows,'cpu',2,True)
    assert cache['features'].shape==(2,16)
    assert cache['tokens'].shape==(2,14*16)
    assert cache['original'].shape==(2,73)


def test_bank_and_gate_resume_without_dropping_trained_parameters(tmp_path):
    torch.manual_seed(3)
    rows=torch.tensor([(a,b) for a,b,*_ in auto.sample_band(192,223,17,4)['train']])
    ys=rows.sum(-1)
    data={'features':rows.float()/256.,'tokens':rows.float()/256.,
          'target':ys,'incumbent':exact_logits([0]*len(rows)),
          'new_logits':exact_logits(ys)}
    meta={'fixed':True}
    bank=auto.AbsoluteBank(2,8)
    a,b=auto.train_bank_stage(bank,data,data,stage=(192,223),meta=meta,out=tmp_path,
        steps=2,probe_every=1,batch_size=6,lr=.0003,seed=5,device='cpu')
    assert b>=0 and (tmp_path/'bank_training.pt').is_file()
    # Finished stage may be resumed without resetting the model or optimizer.
    bank2=auto.AbsoluteBank(2,8)
    c,d=auto.train_bank_stage(bank2,data,data,stage=(192,223),meta=meta,out=tmp_path,
        steps=2,probe_every=1,batch_size=6,lr=.0003,seed=5,device='cpu')
    assert (a,b)==(c,d)
    assert all(torch.equal(x,y) for x,y in zip(bank.parameters(),bank2.parameters()))
    with pytest.raises(RuntimeError,match='RESUME_CONFIG_MISMATCH'):
        auto.train_bank_stage(auto.AbsoluteBank(2,8),data,data,stage=(192,223),meta={'different':1},out=tmp_path,
            steps=2,probe_every=1,batch_size=6,lr=.0003,seed=5,device='cpu')
    gate=auto.NeuralRouter(2,2,hidden=8,include_tokens=True)
    z=auto.train_gate_stage(gate,data,data,data,data,data,stage=(192,223),meta=meta,out=tmp_path,
         steps=2,probe_every=1,batch_size=8,lr=.0001,seed=6,device='cpu')
    assert z in (1,2)
    gate2=auto.NeuralRouter(2,2,hidden=8,include_tokens=True)
    z2=auto.train_gate_stage(gate2,data,data,data,data,data,stage=(192,223),meta=meta,out=tmp_path,
         steps=2,probe_every=1,batch_size=8,lr=.0001,seed=6,device='cpu')
    assert z==z2
    assert all(torch.equal(x,y) for x,y in zip(gate.parameters(),gate2.parameters()))


def test_manifest_fail_closed_and_sampled_certification_is_not_claimed_exhaustive(tmp_path):
    args=auto.parser().parse_args(['--ladder-output',str(tmp_path/'src'),
        '--prior-bank-output',str(tmp_path/'prior'),
        '--prior-router-output',str(tmp_path/'router'),
        '--repair-output',str(tmp_path/'repair'),
        '--output',str(tmp_path/'out'),'--device','cpu'])
    with pytest.raises(RuntimeError,match='OVERLAPS'):
        auto._check_output_safety(tmp_path/'src'/'other',[tmp_path/'src'])
    source={'checkpoint_sha256':'x'}
    refs=[{'band':[128,159]},{'band':[160,191]}]
    state=auto.initialize(args,tmp_path/'out',source,refs)
    assert not state['status'].startswith('target_reached')
    assert len(state['pending'])==30
    assert 'Sampled historical' in state['note']
    assert auto._validate_plan(state['pending'],state) is None
    state['pending'][0]=[193,223]
    with pytest.raises(RuntimeError,match='NONCONTIGUOUS'):
        auto._validate_plan(state['pending'],state)


def test_autopilot_end_to_end_refuses_unqualified_stage_and_preserves_sources(tmp_path,monkeypatch):
    """Use real bank/gate training and filesystem checkpoints, fake fast frozen base."""
    from tools.nanojev_zero_line_radius255_probe import sha256
    from tools import nanojev_zero_line_cumulative_bank as cb
    base=FakeBase()
    root=tmp_path/'base';root.mkdir()
    checkpoint=root/'passed.pt';checkpoint.write_bytes(b'frozen-source')
    prior=tmp_path/'prior';prior.mkdir()
    firstbp=prior/'first_bank.pt';firstbp.write_bytes(b'frozen-first-bank')
    firstgp=prior/'first_gate.pt';firstgp.write_bytes(b'frozen-first-gate')
    priorrouter=tmp_path/'priorrouter';priorrouter.mkdir()
    repair=tmp_path/'repair';repair.mkdir()
    oldhash=sha256(checkpoint)
    second=auto.AbsoluteBank(2,8)
    gate=auto.NeuralRouter(2,28,hidden=8,include_tokens=True)
    with torch.no_grad():
        gate.net[-1].weight.zero_();gate.net[-1].bias.fill_(-100.)
    torch.save({'source_sha256':oldhash,'band':[160,191],'hidden':8,
                'bank':second.state_dict()},repair/'bank_promoted.pt')
    torch.save({'source_sha256':oldhash,'new_band':[160,191],
                'new_bank_sha256':sha256(repair/'bank_promoted.pt'),
                'prior_bank_sha256':sha256(firstbp),
                'prior_router_sha256':sha256(firstgp),
                'feature_width':2,'token_width':28,'hidden':8,
                'include_tokens':True,'gate':gate.state_dict()},repair/'gate_promoted.pt')
    (repair/'result.json').write_text(json.dumps({'schema':'nanojev.zero-line-cumulative-gate-repair.v1',
                                                  'eligible':True}))
    monkeypatch.setattr(auto,'load_certified_model',lambda *a:(base,{'checkpoint':str(checkpoint),
                                                              'checkpoint_sha256':oldhash}))
    monkeypatch.setattr(cb,'load_prior',lambda *a:(DummyBank(),FixedRouter(-100.),True,
                                                   firstbp,firstgp,{}))
    monkeypatch.setattr(auto,'deck',lambda r:{'x':torch.tensor([[0,0],[2,-2],[9,-1]]),
                                             'y':torch.tensor([0,0,8])})
    args=auto.parser().parse_args(['--ladder-output',str(root),
      '--prior-bank-output',str(prior),'--prior-router-output',str(priorrouter),
      '--repair-output',str(repair),'--output',str(tmp_path/'new'),
      '--target','223','--max-stages','1','--device','cpu',
      '--bank-steps','2','--gate-steps','2',
      '--bank-probe-every','1','--gate-probe-every','1',
      '--sample-per-cell','3','--history-samples','2',
      '--batch-size','8','--eval-batch-size','48',
      '--bank-hidden','8','--gate-hidden','8',
      '--no-full-legacy-retention','--failure-policy','stop',
      '--cpu-threads','1'])
    checked=[checkpoint,firstbp,firstgp,repair/'bank_promoted.pt',repair/'gate_promoted.pt']
    hashes=[sha256(p) for p in checked]
    state=auto.run(args)
    assert state['status']=='halted_on_refusal'
    assert len(state['bands'])==2
    assert state['pending']==[[192,223]]
    assert (tmp_path/'new'/'stages'/'192_223'/'bank_training.pt').exists()
    assert (tmp_path/'new'/'stages'/'192_223'/'gate_training.pt').exists()
    assert not (tmp_path/'new'/'stages'/'192_223'/'bank_promoted.pt').exists()
    assert [sha256(p) for p in checked]==hashes
    assert (tmp_path/'new'/'manifest.json').exists()
    # A restart must not turn the unchanged failure into promotion.
    repeated=auto.run(args)
    assert repeated['status']=='halted_on_refusal'
    assert repeated['bands']==state['bands']


def test_expert_comparison_is_answer_aware_but_only_diagnostic():
    answers=[0,1,2,3,4,5]
    incumbent=[0,1,9,3,8,9]   # correct on 0,1,3
    specialist=[9,1,2,8,4,5]  # correct on 1,2,4,5
    d={'target':torch.tensor(answers),
       'incumbent':exact_logits(incumbent),'new_logits':exact_logits(specialist)}
    r=auto.compare_experts(d)
    assert r['cards']==6
    assert r['incumbent_accuracy']==pytest.approx(3/6)
    assert r['specialist_accuracy']==pytest.approx(4/6)
    assert r['offline_answer_aware_ceiling']==1.0
    assert (r['both_correct'],r['incumbent_only_correct'],r['specialist_only_correct'],
            r['both_wrong'])==(1,2,3,0)
    assert r['net_specialist_gain']==1
    # The answer-independent model can still route incorrectly: offline oracle
    # complementarity cannot be used as a live prediction rule.
    wrong=auto.eval_gate(FixedRouter(-100.),dict(d,features=torch.zeros(6,2),
                                                  tokens=torch.zeros(6,2)))
    assert wrong['accuracy']==pytest.approx(.5)


def test_promotion_requires_accuracy_and_net_fixes_not_new_bank_route_quota():
    from types import SimpleNamespace
    a=SimpleNamespace(min_accuracy=.90,min_zero_accuracy=1.,min_near_accuracy=.95)
    metrics={'center':{'new_harm':0},'history':{'new_harm':0}}
    for k in ('new_dev','new_ood'):
        metrics[k]={'accuracy':.95,'new_fixes':10,'new_harm':0,
                    'zero_accuracy':1.,'near_accuracy':.97,
                    'near_zero_harm':0,'new_bank_selection':.02}
    ok,why=auto.certification_decision(metrics,a)
    assert ok and why['bank_selection_is_diagnostic_not_required']
    metrics['new_dev']['new_fixes']=0
    ok,_=auto.certification_decision(metrics,a)
    assert not ok  # A no-op system is not a successful new bank.
    metrics['new_dev']['new_fixes']=10
    metrics['history']['new_harm']=1
    ok,_=auto.certification_decision(metrics,a)
    assert not ok


def test_adaptive_bank_extension_is_bounded_and_resumable(tmp_path):
    from types import SimpleNamespace
    torch.manual_seed(17)
    rows=torch.tensor([(a,b) for a,b,*_ in auto.sample_band(192,223,17,4)['train']])
    targets=rows.sum(-1)
    data={'features':rows.float()/256.,'target':targets}
    args=SimpleNamespace(bank_steps=2,bank_max_steps=4,bank_extension_steps=2,
                         bank_patience=5,bank_min_dev_gain=1e-3,
                         bank_target_accuracy=1.1,bank_probe_every=1,
                         batch_size=8,bank_lr=.0003,seed=17)
    model=auto.AbsoluteBank(2,8)
    step,best,milestones=auto.adaptive_bank_stage(model,data,data,stage=(192,223),
        meta={'adaptive':'test'},out=tmp_path,args=args,device='cpu')
    assert [m['step'] for m in milestones]==[2,4]
    assert milestones[-1]['stop_reason']=='max_steps'
    assert (tmp_path/'adaptive_progress.json').exists()
    # A restart returns exactly the selected state and never adds new steps.
    restored=auto.AbsoluteBank(2,8)
    step2,best2,again=auto.adaptive_bank_stage(restored,data,data,stage=(192,223),
        meta={'adaptive':'test'},out=tmp_path,args=args,device='cpu')
    assert (step,best)==(step2,best2)
    assert again==milestones
    assert all(torch.equal(x,y) for x,y in zip(model.parameters(),restored.parameters()))


def test_same_source_bank_warmstart_is_read_only(tmp_path):
    from types import SimpleNamespace
    source=tmp_path/'source';source.mkdir()
    prior=auto.AbsoluteBank(2,8)
    for p in prior.parameters():
        with torch.no_grad():p.fill_(.123)
    (source/'config.json').write_text(json.dumps({'band':[192,223],
        'source_sha256':'certified-hash','training_meta':{'bank_hidden':8}}))
    torch.save({'step':5000,'bank':prior.state_dict()},source/'bank_best.pt')
    original_sha=auto.sha256(source/'bank_best.pt')
    destination=tmp_path/'destination';destination.mkdir()
    args=SimpleNamespace(bank_warmstart_stage=str(source),bank_hidden=8)
    loaded=auto.AbsoluteBank(2,8)
    result=auto.optional_warmstart_bank(loaded,args,[192,223],
                                      'certified-hash',destination)
    assert result['prior_selected_step']==5000
    assert result['sha256']==original_sha
    assert all(torch.equal(x,y) for x,y in zip(prior.parameters(),loaded.parameters()))
    assert auto.optional_warmstart_bank(auto.AbsoluteBank(2,8),args,
               [224,255],'certified-hash',destination) is None
    with pytest.raises(RuntimeError,match='PROVENANCE_MISMATCH'):
        auto.optional_warmstart_bank(auto.AbsoluteBank(2,8),args,
                                   [192,223],'different',destination)
    assert auto.sha256(source/'bank_best.pt')==original_sha


def test_gate_plateau_early_stops_and_resumes(tmp_path):
    targets=torch.tensor([0,1,2,3,4,5,16,17],dtype=torch.long)
    inputs=torch.arange(16,dtype=torch.float).reshape(8,2)/100.
    fixed=exact_logits(targets.tolist())
    cache={'features':inputs,'tokens':inputs,'target':targets,
           'incumbent':fixed,'new_logits':fixed}
    model=auto.NeuralRouter(2,2,hidden=8,include_tokens=True)
    step=auto.train_gate_stage(model,cache,cache,cache,cache,cache,
        stage=(192,223),meta={'gate_test':1},out=tmp_path,
        steps=10,probe_every=1,batch_size=8,lr=.0001,seed=13,
        device='cpu',patience=2,min_dev_gain=.001)
    progress=torch.load(tmp_path/'gate_training.pt',map_location='cpu',weights_only=True)
    assert progress['stopped'] is True
    assert progress['step']<10
    resumed=auto.NeuralRouter(2,2,hidden=8,include_tokens=True)
    returned=auto.train_gate_stage(resumed,cache,cache,cache,cache,cache,
        stage=(192,223),meta={'gate_test':1},out=tmp_path,
        steps=10,probe_every=1,batch_size=8,lr=.0001,seed=13,
        device='cpu',patience=2,min_dev_gain=.001)
    assert returned==step
    assert all(torch.equal(x,y) for x,y in zip(model.parameters(),resumed.parameters()))


def test_diagnosis_ensures_oracle_comparison_does_not_grant_promotion():
    from types import SimpleNamespace
    args=SimpleNamespace(min_accuracy=.90,min_zero_accuracy=1.,min_near_accuracy=.95)
    md={'center':{'new_harm':0},'history':{'new_harm':0}}
    for name in ('new_dev','new_ood'):
        md[name]={'accuracy':.88,'new_fixes':20,'new_harm':0,
                  'zero_accuracy':1.,'near_accuracy':.99,'near_zero_harm':0,
                  'new_bank_selection':.99}
    ok,_=auto.certification_decision(md,args)
    assert not ok
    # An offline 100%-correct OR-of-experts reference does not change that.
    arr={'target':torch.tensor([1,2]),'incumbent':exact_logits([1,0]),
         'new_logits':exact_logits([0,2])}
    assert auto.compare_experts(arr)['offline_answer_aware_ceiling']==1.
