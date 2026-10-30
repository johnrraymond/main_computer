"""CPU mechanism and cutover proofs for the immutable v1 glass refinement."""
from types import SimpleNamespace
import json
import pytest
from tools import nanojev_qwen_lm_deformation_smoke as base
from tools import nanojev_qwen_lm_deformation_refine as r


def test_exact_match_gate_keeps_outside_activations_zero():
    torch=pytest.importorskip('torch')
    anchor=torch.randn(1,16)
    glass=base.RelativeDeformationGlass(None,hidden=16,keys=anchor,
                                        projection_dim=16,threshold=1.,seed=12)
    with torch.no_grad():glass.net.corrections[0].fill_(3.)
    assert torch.equal(glass.net(-anchor),-anchor)
    assert int(r._match(glass.net,anchor)[0])==0
    assert int(r._match(glass.net,-anchor)[0])==-1


def test_offline_refinement_updates_only_glass_and_protects_corrects(tmp_path):
    torch=pytest.importorskip('torch')
    torch.manual_seed(7)
    head=torch.nn.Linear(8,3,bias=False)
    head.requires_grad_(False)
    with torch.no_grad():
        head.weight.zero_();head.weight[0,0]=2;head.weight[1,1]=2
    h=torch.tensor([[1.,0.,0.,0.,0.,0.,0.,0.]])
    glass=base.RelativeDeformationGlass(None,hidden=8,keys=h,
                                        projection_dim=16,threshold=1.,seed=1)
    glass.net.to('cpu')
    cache={'h':h,'labels':torch.tensor([1]),'modes':torch.tensor([0]),
           'erroneous':torch.tensor([True])}
    args=SimpleNamespace(device='cpu',refine_epochs=30,refine_lr=.3,
         refine_batch_size=1,preserve_weight=1.,refine_l2=0.,seed=1,
         checkpoint_every=5,progress_every=0)
    before=head.weight.detach().clone()
    result=r.train_refinement(head,glass,cache,args,tmp_path,{'seed':1})
    assert result['steps']==30
    assert torch.equal(before,head.weight)
    assert int(head(glass.net(h)).argmax(-1))==1
    with torch.no_grad():
        after=glass.net.corrections.detach().clone()
    another=r.train_refinement(head,glass,cache,args,tmp_path,{'seed':1})
    assert another['steps']==30
    assert torch.equal(after,glass.net.corrections)


def test_greedy_audit_accounts_for_all_cases_and_resumes(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    class BB(torch.nn.Module):
        def __init__(self):
            super().__init__();self.head=torch.nn.Linear(3,3)
        def get_output_embeddings(self):return self.head
    bb=BB()
    glass=base.RelativeDeformationGlass(None,hidden=3,keys=torch.randn(1,3),
                                   projection_dim=3,threshold=.999,seed=2)
    head=bb.head
    rows=[{'id':f'x{i}','source':f'SRC{i}','target':'ANSWER: 0',
           'rung':i%2,'residual':-1+i%3} for i in range(7)]
    # Simulated generation returns an incorrect baseline answer, corrected
    # when the head hook is installed. This tests accounting, not model skill.
    def mock_greedy(bb,tok,sources,*args):
        corrected=len(head._forward_pre_hooks)>0
        return ['ANSWER: 0' if corrected else 'ANSWER: 1' for _ in sources]
    monkeypatch.setattr(base.ladder,'_greedy',mock_greedy)
    monkeypatch.setattr(base.ladder,'correctness',lambda src,target,out:(target==out,None))
    (tmp_path/'refined_glass.pt').write_bytes(b'fake-completed')
    args=SimpleNamespace(greedy_audit='full',controls_per_cell=1,eval_batch=3,
       device='cpu',max_tokens=128,max_new_tokens=16,progress_every=0,
       greedy_checkpoint_every=1)
    result=r.greedy_audit(bb,None,head,rows,glass,args,tmp_path,{'x':1},set())
    assert result['tested']==7
    assert result['full_corpus_tested']
    assert result['outcomes']['fixed']==7
    assert not (tmp_path/'greedy_full_partial.json').exists()
    result2=r.greedy_audit(bb,None,head,rows,glass,args,tmp_path,{'x':1},set())
    assert result2==result


def test_train_and_audit_without_retraining_qwen(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    from torch import nn
    import torch.nn.functional as F
    class Tok:
        pad_token_id=0;eos_token_id=1
        def __call__(self,text,add_special_tokens=False):
            return {'input_ids':[ord(c) for c in text]}
    class BB(nn.Module):
        def __init__(self):
            super().__init__();self.lora=nn.Parameter(torch.zeros(128))
            self.head=nn.Linear(128,128,bias=False);nn.init.normal_(self.head.weight,std=.02)
            self.head.requires_grad_(False)
        def get_output_embeddings(self):return self.head
        def forward(self,input_ids=None,attention_mask=None,labels=None,
                    return_dict=True,use_cache=False):
            hidden=F.one_hot(input_ids,128).float()+self.lora.view(1,1,-1)
            logits=self.head(hidden)
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
    torch.manual_seed(11);parent=BB()
    root=tmp_path/'ladder';root.mkdir();working=root/'working.pt'
    state={'state':{'source_sha256':'sha','frozen_sha256':'frozen'},
           'lora':Zero.lora_state(parent)}
    torch.save(state,working)
    s1=tmp_path/'s1';s1.mkdir();(s1/'best.pt').write_bytes(b'1')
    text=tmp_path/'text';text.mkdir();(text/'best.pt').write_bytes(b'2')
    out=tmp_path/'output'
    def loader(args):
        model=BB();model.load_state_dict(parent.state_dict());model.requires_grad_(False)
        return model,Tok(),'sha','frozen',[s1,text],Zero
    monkeypatch.setattr(base.ladder,'_fresh_parent',loader)
    monkeypatch.setattr(base,'_sample_rollouts',lambda *args,**kw:{})
    args=SimpleNamespace(stage='backbone',device='cpu',stage1_checkpoint=str(s1/'best.pt'),
      text_checkpoint=str(text/'best.pt'),ladder_working=str(working),output=str(out),
      radii='0,1',max_rung=0,operand_limit=8,per_cell=1,t00_mode='sampled',seed=44,
      batch_size=2,eval_batch=2,case_cap=1,backbone_epochs=1,glass_epochs=1,
      backbone_lr=.01,glass_lr=.2,glass_l2=.001,glass_modes=8,
      candidates_per_group=4,glass_projection_dim=32,glass_threshold=1.,
      max_seq=192,max_tokens=256,max_new_tokens=64,greedy_cases_per_cell=0,
      progress_every=0,checkpoint_every=1,baseline=None,backbone=None,activation=None,
      format=None,allow_model_download=False)
    base.run(args);args.stage='glass';base.run(args)
    source_hash=base._file_hash(working)
    baseline_hash=base._file_hash(out/'baseline.pt')
    original_glass_hash=base._file_hash(out/'glass.pt')
    args.stage='refine';args.protect_per_mode=3;args.protect_ratio=2;args.refine_epochs=2
    args.refine_lr=.02;args.refine_batch_size=2;args.refine_l2=.0001
    args.preserve_weight=.5;args.greedy_audit='targeted';args.controls_per_cell=1
    args.greedy_checkpoint_every=1
    result=r.run(args)
    assert (out/'glass_refinement'/'refined_glass.pt').exists()
    assert result['cache_stats']['total_valid_tokens']>0
    assert result['training']['additional_trainable_parameters']==0
    assert base._file_hash(out/'baseline.pt')==baseline_hash
    assert base._file_hash(out/'glass.pt')==original_glass_hash
    assert base._file_hash(working)==source_hash
    # Original Qwen remains frozen during the token audit and greedy audit.
    monkeypatch.setattr(base.ladder,'_greedy',lambda bb,tok,inputs,*a:['ANSWER: 0']*len(inputs))
    args.stage='audit'
    audited=r.run(args)
    assert audited['greedy']['tested']>0
    assert (out/'glass_refinement'/'teacher_forced_audit.json').exists()
    assert base._file_hash(out/'baseline.pt')==baseline_hash
    assert base._file_hash(out/'glass.pt')==original_glass_hash
    assert base._file_hash(working)==source_hash


def test_offline_replay_uses_frozen_head_precision(tmp_path):
    torch=pytest.importorskip('torch')
    head=torch.nn.Linear(8,4,bias=False,dtype=torch.float16)
    head.requires_grad_(False)
    h=torch.zeros(1,8);h[0,0]=2.
    glass=base.RelativeDeformationGlass(None,hidden=8,keys=h,
                      projection_dim=12,threshold=1.,seed=1)
    cache={'h':h.float(),'labels':torch.tensor([1]),'modes':torch.tensor([0]),
           'erroneous':torch.tensor([True])}
    args=SimpleNamespace(device='cpu',refine_epochs=1,refine_lr=.001,
       refine_batch_size=1,preserve_weight=.1,refine_l2=.0001,seed=1,
       checkpoint_every=1,progress_every=0)
    result=r.train_refinement(head,glass,cache,args,tmp_path,{'test':'half-head'})
    assert result['steps']==1
    assert glass.net.corrections.dtype==torch.float32
    assert head.weight.grad is None
