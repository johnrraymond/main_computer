"""CPU-only proofs for the two-phase zero-default deformation smoke."""
from __future__ import annotations

from types import SimpleNamespace
import json

import pytest

from tools import nanojev_qwen_lm_deformation_smoke as m


def test_complete_signed_corpus_has_no_duplicate_nested_radii():
    rows, scopes = m.corpus_for_schedule('0,1,2',1,8,2,3,'exhaustive')
    assert scopes[0]['count']==(5*17-6)
    assert {r['residual'] for r in rows if r['rung']==0}=={-2,-1,0,1,2}
    assert {r['residual'] for r in rows if r['rung']==1}=={-2,-1,0,1,2}
    assert len({(r['rung'],r['id']) for r in rows})==len(rows)
    assert len(rows)==sum(s['count'] for s in scopes)


def test_exact_zero_default_sparse_localization_and_isolated_trainability():
    torch=pytest.importorskip('torch')
    hidden=32
    anchors=torch.randn(4,hidden)
    glass=m.RelativeDeformationGlass(None,hidden=hidden,keys=anchors,
                     projection_dim=32,threshold=1.0,seed=50)
    layer=glass.net
    original=torch.randn(2,4,hidden)
    assert torch.equal(layer(original),original)
    local=anchors[0:1].clone()
    with torch.no_grad():
        layer.corrections[0,0]=2.5
    # Exact same signature is recognized, novel signatures cannot pass a
    # 1.0 threshold except for a degenerate exact projection match.
    result=layer(local)
    assert not torch.equal(result,local)
    assert float((result-local).detach()[0,0])==2.5


def test_real_frozen_head_hook_glass_learns_without_updating_head():
    torch=pytest.importorskip('torch')
    hidden=8
    head=torch.nn.Linear(hidden,3,bias=False)
    with torch.no_grad():
        head.weight.zero_();head.weight[0,0]=1.;head.weight[1,1]=1.;head.weight[2,2]=1.
    head.requires_grad_(False)
    good=torch.zeros(1,hidden);good[0,0]=4.
    bad=torch.zeros(1,hidden);bad[0,0]=2.;bad[0,1]=1.
    glass=m.RelativeDeformationGlass(None,hidden=hidden,keys=bad,
                         projection_dim=32,threshold=1.,seed=33)
    before=head.weight.detach().clone()
    opt=torch.optim.Adam([glass.net.corrections],lr=.8)
    with glass.hook(head):
        with torch.no_grad():
            assert head(bad).argmax(-1).item()==0
            assert head(good).argmax(-1).item()==0
        for _ in range(40):
            opt.zero_grad()
            loss=torch.nn.functional.cross_entropy(head(bad),torch.tensor([1]))
            loss.backward();opt.step()
        assert head(bad).argmax(-1).item()==1
        assert head(good).argmax(-1).item()==0
    assert torch.equal(before,head.weight.detach())
    assert all(p.grad is None for p in head.parameters())
    assert torch.count_nonzero(glass.net.corrections)>0


def test_corpus_selection_balances_modes_across_signed_residual_and_depth():
    torch=pytest.importorskip('torch')
    candidates={(0,0):[(str(i),torch.full((4,),float(i))) for i in range(10)],
                (0,1):[('p1',torch.ones(4))],
                (1,-1):[('n1',torch.ones(4)*-1)]}
    keys,ids=m._choose_keys(candidates,4)
    assert keys.shape==(4,4)
    assert ids[:3]==['0','p1','n1']


def test_projection_preserves_zero_on_unseen_modes():
    torch=pytest.importorskip('torch')
    seed=123
    anchors=torch.randn(3,48)
    glass=m.RelativeDeformationGlass(None,hidden=48,keys=anchors,
        projection_dim=24,threshold=.9999,seed=seed)
    with torch.no_grad():glass.net.corrections.fill_(1.)
    far=-anchors[0:1]
    assert torch.equal(glass.net(far),far)


def test_token_mask_keeps_only_baseline_errors():
    torch=pytest.importorskip('torch')
    class Tok:
        pad_token_id=0
        eos_token_id=1
        def __call__(self,text,add_special_tokens=False):
            return {'input_ids':[ord(c) for c in text]}
    case={'id':'k','source':'PROBLEM: 1+(-1)','target':'ANSWER: 0'}
    x,y,mask=m._token_batch(Tok(),[case],'cpu',192,error_positions={'k':[18,19]})
    assert len(torch.nonzero(y!=-100))<=2
    assert int(mask.sum())==len(case['source'])+len(case['target'])+2
    assert y.shape==x.shape


def test_report_compares_all_signed_delta_cells():
    a={'T00/delta+0':{'mean_loss':1.5}}
    b={'T00/delta+0':{'mean_loss':0.7,'wrong_tokens':7}}
    c={'T00/delta+0':{'mean_loss':0.3,'wrong_tokens':2}}
    assert m._compare(a,b,c)['T00/delta+0']=={
       'initial_loss':1.5,'backbone_loss':0.7,'glass_loss':0.3,
       'backbone_wrong_tokens':7,'glass_wrong_tokens':2}


def test_two_phase_cutover_real_cpu_training_and_frozen_lora(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    from torch import nn
    import torch.nn.functional as F
    class Tok:
        pad_token_id=0
        eos_token_id=1
        def __call__(self,text,add_special_tokens=False):
            return {'input_ids':[ord(c) for c in text]}
    class BB(nn.Module):
        def __init__(self):
            super().__init__()
            self.lora=nn.Parameter(torch.zeros(128))
            self.head=nn.Linear(128,128,bias=False)
            nn.init.normal_(self.head.weight,std=.03)
            self.head.requires_grad_(False)
        def get_output_embeddings(self):return self.head
        def forward(self,input_ids=None,attention_mask=None,labels=None,
                    return_dict=True,use_cache=False):
            h=F.one_hot(input_ids,128).float()+self.lora.view(1,1,-1)
            logits=self.head(h)
            loss=None
            if labels is not None:
                loss=F.cross_entropy(logits[:,:-1].reshape(-1,128),
                                     labels[:,1:].reshape(-1),ignore_index=-100)
            return SimpleNamespace(logits=logits,loss=loss)
    class Zero:
        @staticmethod
        def restore_lora(bb,state):
            with torch.no_grad():bb.lora.copy_(state['lora'])
        @staticmethod
        def lora_named_parameters(bb):return [('lora',bb.lora)]
        @staticmethod
        def lora_state(bb):return {'lora':bb.lora.detach().clone()}
    torch.manual_seed(44)
    parent=BB()
    parent_model={'state':{'source_sha256':'sha','frozen_sha256':'frozen'},
                  'lora':Zero.lora_state(parent)}
    ladder_dir=tmp_path/'ladder';ladder_dir.mkdir()
    source=ladder_dir/'working.pt';torch.save(parent_model,source)
    s1=tmp_path/'s1';s1.mkdir();(s1/'best.pt').write_bytes(b'placeholder')
    text=tmp_path/'text';text.mkdir();(text/'best.pt').write_bytes(b'placeholder')
    out=tmp_path/'glass_output'
    def parent_loader(args):
        model=BB();model.load_state_dict(parent.state_dict())
        model.requires_grad_(False)
        return model,Tok(),'sha','frozen',[s1,text],Zero
    monkeypatch.setattr(m.ladder,'_fresh_parent',parent_loader)
    # Greedy output mechanics are independently tested at the head hook; use a
    # deterministic no-op rollout reporter for this end-to-end phase test.
    monkeypatch.setattr(m,'_sample_rollouts',lambda *a,**kw:{})
    args=SimpleNamespace(stage='backbone',device='cpu',stage1_checkpoint=str(s1/'best.pt'),
       text_checkpoint=str(text/'best.pt'),ladder_working=str(source),output=str(out),
       radii='0,1',max_rung=0,operand_limit=8,per_cell=1,t00_mode='sampled',seed=44,
       batch_size=2,eval_batch=2,case_cap=1,backbone_epochs=1,glass_epochs=1,
       backbone_lr=.01,glass_lr=.2,glass_l2=.001,glass_modes=8,
       candidates_per_group=4,glass_projection_dim=32,glass_threshold=1.,
       max_seq=192,max_tokens=256,max_new_tokens=64,
       greedy_cases_per_cell=0,progress_every=0,checkpoint_every=1,
       baseline=None,backbone=None,activation=None,format=None,allow_model_download=False)
    original_hash=m._file_hash(source)
    stage_one=m.run(args)
    assert (out/'baseline.pt').is_file()
    assert not (out/'glass.pt').exists()
    assert stage_one['corpus_cases']==3 and not stage_one['full_corpus']
    frozen_backbone=torch.load(out/'baseline.pt',map_location='cpu',weights_only=True)['lora']['lora'].clone()
    # Simulate ongoing independent training modifying the original ladder.
    # The experiment must use its immutable cutover snapshot, not the live file.
    with torch.no_grad():parent.lora.add_(3.)
    torch.save({'state':parent_model['state'],'lora':Zero.lora_state(parent)},source)
    args.stage='glass'
    stage_two=m.run(args)
    assert (out/'glass.pt').is_file()
    assert stage_two['all_glass_training_backbone_frozen']
    assert stage_two['backbone']['tokens']==stage_two['glass']['tokens']
    assert set(stage_two['comparison'])==set(stage_two['backbone_per_cell'])
    assert m._file_hash(source)!=original_hash
    assert m._file_hash(out/'source_snapshot.pt')==original_hash
    after=torch.load(out/'baseline.pt',map_location='cpu',weights_only=True)['lora']['lora']
    assert torch.equal(frozen_backbone,after)
    assert not (out/'glass_partial.pt').exists()
    assert not (out/'backbone_partial.pt').exists()
