"""Source-level integration tests for the local defect-repair A/B/C branch."""
from types import SimpleNamespace

import torch
from tools import nanojev_qwen_lm_frontier_local_glass as local
from tools import nanojev_qwen_lm_frontier_cached_glass as cached
from tools import nanojev_qwen_lm_deformation_smoke as ab
from tools import nanojev_qwen_lm_frontier_rate_diagnostic as rate

# Reuse the actual existing first-divergence mock Qwen to avoid inventing
# a second fixture with different tokenization/termination conventions.
from test_nanojev_qwen_lm_failure_mode_glass import Backbone, Tokenizer


def rollout_args():
    return SimpleNamespace(device='cpu',max_new_tokens=4,max_tokens=50,
                           feature_layers=4)


def test_local_branch_repairs_first_wrong_token_and_preserves_unmatched(monkeypatch):
    monkeypatch.setattr(ab.ladder,'correctness',
                        lambda source,target,output:(output.strip()==target,None))
    bb=Backbone();tok=Tokenizer();args=rollout_args()
    bb.eval();bb.requires_grad_(False)
    glass=local.FrontierLocalGlass(4,4,device='cpu')
    failed={'id':'p','source':'P','target':'a'}
    correct={'id':'q','source':'Q','target':'a'}
    before=[p.detach().clone() for p in bb.parameters()]
    assert not ab._rollout(bb,tok,failed,args,glass=glass)['correct']
    assert ab._rollout(bb,tok,correct,args,glass=glass)['correct']
    updated=local.repair_step(glass,bb,tok,failed,args,
                              token_steps=60,lr=.3,margin=1.,max_token_fixes=3)
    assert updated['correct_after'] and glass.n_modes==1
    assert ab._rollout(bb,tok,failed,args,glass=glass)['correct']
    # The repaired mode does not fire on a distinct, already correct input.
    unaffected=ab._rollout(bb,tok,correct,args,glass=glass)
    assert unaffected['correct'] and unaffected['max_delta_norm']==0
    assert all(torch.equal(p,old) for p,old in zip(bb.parameters(),before))
    saved=glass.state()
    restored=local.FrontierLocalGlass.restore(saved,device='cpu')
    assert ab._rollout(bb,tok,failed,args,glass=restored)['correct']
    assert ab._rollout(bb,tok,correct,args,glass=restored)['max_delta_norm']==0


def test_local_branch_reports_conflicting_mode_targets():
    g=local.FrontierLocalGlass(4,device='cpu')
    feat=torch.cat([torch.tensor([1.,0.,0.,0.])]*4).reshape(1,-1)
    mode,created,ambiguous=g.add(feat,1)
    assert (mode,created,ambiguous)==(0,True,False)
    mode,created,ambiguous=g.add(feat,2)
    assert (mode,created,ambiguous)==(0,False,True)
    assert g.n_modes==1 and g.targets=={0:1}


def test_correctness_aware_cached_step_never_rolls_out_during_repeated_training():
    torch.manual_seed(4)
    # A/B training receives frozen hidden state and features; backbone decode
    # must not be called again for a site already built.
    head=torch.nn.Linear(4,5,bias=False)
    head.requires_grad_(False)
    glass=ab.ContinuousGlass(4,4,16,gated=True)
    opt=torch.optim.AdamW(glass.net.parameters(),lr=.001)
    class BB:
        def get_output_embeddings(self):return head
        def get_decoder(self):raise AssertionError('uncached Qwen decoder call')
    class Sites:
        def __init__(self):self.sites={}
        def get(self,row,**kwargs):
            key=row['id']
            if key not in self.sites:
                self.sites[key]=(torch.randn(1,4),torch.randn(1,16),1)
            return self.sites[key]
    sites=Sites()
    args=SimpleNamespace(glass_preserve_weight=1.,glass_preserve_margin=1.,
                         glass_delta_l2=.001,glass_gate_loss=.002)
    bad={'id':'hard'};good={'id':'replay'}
    before=[p.detach().clone() for p in glass.net.parameters()]
    result=cached.train_step(glass,opt,BB(),bad,good,sites,args,step=0)
    assert result['loss']>0 and result['cached_sites']==2
    assert any(not torch.equal(p,old) for p,old in zip(glass.net.parameters(),before))
    cached.train_step(glass,opt,BB(),bad,good,sites,args,step=1)
    assert len(sites.sites)==2


def test_pair_sampler_covers_coprime_and_non_coprime_lengths():
    args=SimpleNamespace(seed=100)
    train=[{'id':str(i)} for i in range(39)]
    baseline={str(i):(i>=13) for i in range(39)}
    pair,hard,correct=rate._training_pairs(train,baseline,args)
    assert (hard,correct)==(13,26)
    assert len({pair(i)[0]['id'] for i in range(13)})==13
    assert len({pair(i)[1]['id'] for i in range(26)})==26


def test_local_state_is_weights_only_checkpoint_safe(tmp_path):
    g=local.FrontierLocalGlass(4,device='cpu')
    features=torch.cat([torch.tensor([0.,1.,0.,0.])]*4).reshape(1,-1)
    g.add(features,3)
    path=tmp_path/'local.pt'
    torch.save({'local':g.state()},path)
    saved=torch.load(path,map_location='cpu',weights_only=True)
    restored=local.FrontierLocalGlass.restore(saved['local'])
    assert restored.n_modes==1 and restored.targets=={0:3}
    assert restored.add(features,4)[-1] is True
