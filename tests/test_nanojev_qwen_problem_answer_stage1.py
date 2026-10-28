"""Qwen-only vector ladder: mechanics, state transitions, isolation and provenance."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import torch
from torch import nn
from tools import nanojev_qwen_problem_answer_stage1 as s
from tools import nanojev_qwen_zero_line_backbone as zero


def test_six_plus_six_and_recursive_targets_are_exact():
    d=s.make_deck(train_count=28,dev_count=14,ood_count=14,test_count=14)
    assert d['train'][0]['source']=='PROBLEM: 6+6'
    assert d['train'][0]['target']=='ANSWER: 12'
    assert d['train'][1]['source']=='PROBLEM: (3+5)+6'
    assert d['train'][1]['target']=='PROBLEM: 8+6'
    assert d['train'][2]['target']=='ANSWER: 14'
    assert d==s.make_deck(train_count=28,dev_count=14,ood_count=14,test_count=14)
    all_rows=[r for a in d.values() for r in a]
    assert len({r['source'] for r in all_rows})==len(all_rows)
    assert {r['action'] for r in all_rows}==set(s.ACTIONS)
    assert {r['initial_depth'] for r in d['train']}==set(s.DEPTHS)
    assert any(r['signed']==0 for r in d['train'])
    assert any(r['signed']==-1 for r in d['train'])
    for name,rows in d.items():
        for r in rows:
            assert r['source'].startswith('PROBLEM: ')
            assert r['target'].startswith(r['action']+': ')
            if r['action']=='ANSWER':
                assert r['target']==f'ANSWER: {r["answer"]}'
            else:
                assert r['depth_left']>1
        by_case={k:[r for r in rows if r['case_id']==k] for k in set(r['case_id'] for r in rows)}
        for case,sequence in by_case.items():
            for a,b in zip(sequence,sequence[1:]):assert a['target']==b['source']
            assert sequence[-1]['action']=='ANSWER'


def test_wrong_parent_is_rejected_and_preview_does_not_load_model(tmp_path,capsys,monkeypatch):
    p=tmp_path/'best.pt'
    with pytest.raises(ValueError,match='MISSING'):s._text_parent(p)
    p.write_bytes(b'fake')
    (tmp_path/'run_config.json').write_text(json.dumps({'schema':'wrong','stage':'train-text'}))
    with pytest.raises(RuntimeError,match='WRONG_PARENT'):s._text_parent(p)
    from tools import nanojev_hybridlink_micro_reduction as micro
    (tmp_path/'run_config.json').write_text(json.dumps({'schema':micro.SCHEMA,'stage':'train-text'}))
    monkeypatch.setattr(s,'_load_model',lambda args:(_ for _ in ()).throw(AssertionError('preview loaded model')))
    args=SimpleNamespace(text_checkpoint=str(p),seed=42,train_count=28,dev_count=14,ood_count=14,test_count=14)
    out=s.preview(args)
    assert out['model_loaded'] is False
    assert out['counts']['train']['answers']==28
    assert 'QWEN_STAGE1_PREVIEW' in capsys.readouterr().out


class Tokenizer:
    pad_token_id=0
    def __call__(self,string,add_special_tokens=False):
        return {'input_ids':[1+ord(c)%127 for c in string]}


class Decoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.emb=nn.Embedding(130,32)
        self.q_proj=nn.Linear(32,32,bias=False)
        self.v_proj=nn.Linear(32,32,bias=False)
    def forward(self,*,input_ids,attention_mask,use_cache,return_dict):
        assert use_cache is False
        h=self.emb(input_ids)*attention_mask[:,:,None]
        h=torch.cumsum(h,dim=1)/attention_mask.cumsum(1)[:,:,None].clamp(min=1)
        return SimpleNamespace(last_hidden_state=self.q_proj(h)+self.v_proj(h))


class Backbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.decoder=Decoder()
        self.config=SimpleNamespace(hidden_size=32)
        self.lm_head=nn.Linear(32,130,bias=False)
    def get_decoder(self):return self.decoder
    def forward(self,*args,**kwargs):raise AssertionError('Called Qwen LM head!')


class Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone=Backbone()
        self.head=nn.Linear(32,32)


def test_headless_vectors_and_backbone_lora_gradient():
    torch.manual_seed(0)
    model=Model();model.backbone.requires_grad_(False)
    model.backbone.lm_head.forward=lambda *a,**k:(_ for _ in ()).throw(AssertionError('LM_HEAD_INVOKED'))
    zero.install_lora(model.backbone,rank=2,alpha=2)
    parameters=[p for _,p in zero.lora_named_parameters(model.backbone)]
    for p in parameters:p.requires_grad_(True)
    t=Tokenizer()
    inputs=['PROBLEM: 6+6','PROBLEM: (6+6)+3','PROBLEM: -20+21']
    vec=s._encode(model.backbone,t,inputs,'cpu',2,128,grad=True)
    assert vec.shape==(3,32) and vec.requires_grad
    control=s._heads(32,'cpu')
    targets=s._encode(model.backbone,t,['ANSWER: 12','PROBLEM: 12+3','ANSWER: 1'],'cpu',2,128).detach()
    init=zero.lora_state(model.backbone)
    objective,detail=s._loss(control,vec,targets,torch.tensor([1,0,1]),['ANSWER: 12','PROBLEM: 12+3','ANSWER: 1'])
    assert all(bool(torch.isfinite(v).all()) for v in (objective,vec,targets))
    optimizer=torch.optim.AdamW([*parameters,*control.parameters()],lr=.05)
    objective.backward();optimizer.step()
    now=zero.lora_state(model.backbone)
    assert any(not torch.equal(init[k],now[k]) for k in init)
    assert all(p.grad is None for p in model.head.parameters())


def test_duplicate_targets_are_not_false_negatives():
    torch.manual_seed(42)
    p=s._heads(16,'cpu')
    x=torch.randn(4,16,requires_grad=True)
    y=torch.randn(4,16)
    act=torch.tensor([1,1,0,0])
    loss,parts=s._loss(p,x,y,act,['ANSWER: 12','ANSWER: 12','PROBLEM: 8+9','PROBLEM: 9+8'])
    assert torch.isfinite(loss) and parts['contrastive']>=0
    loss.backward()
    assert x.grad is not None


def test_retrieval_is_within_control_type_and_headless():
    torch.manual_seed(42)
    bb=Backbone();bb.requires_grad_(False)
    bb.lm_head.forward=lambda *a,**k:(_ for _ in ()).throw(AssertionError('LM_HEAD_INVOKED'))
    tok=Tokenizer()
    rows=[{'source':'PROBLEM: 6+6','target':'ANSWER: 12','action':'ANSWER'},
          {'source':'PROBLEM: (6+6)+3','target':'PROBLEM: 12+3','action':'PROBLEM'}]
    teacher=s._encode(bb,tok,[r['target'] for r in rows],'cpu',2,128)
    stats=s._evaluation(bb,tok,s._heads(32,'cpu'),rows,teacher,'cpu',2,128)
    assert stats['transitions']==2 and stats['candidate_pool']==2
    assert 'NOT generation' in stats['candidate_protocol']


def test_mock_train_saves_qwen_lora_not_hybridlink(tmp_path,monkeypatch):
    torch.manual_seed(24)
    model=Model();model.backbone.requires_grad_(False);model.head.requires_grad_(False)
    zero.install_lora(model.backbone,rank=2,alpha=2)
    no_change={k:v.detach().clone() for k,v in model.head.state_dict().items()}
    frozen=zero.frozen_backbone_sha256(model.backbone)
    p=tmp_path/'parent'/'best.pt';p.parent.mkdir();p.write_bytes(b'text checkpoint')
    monkeypatch.setattr(s,'_prepare',lambda args,split:(s.make_deck(args.seed,args.train_count,args.dev_count,args.ood_count,args.test_count),
                        model,Tokenizer(),{'m':'hash'},frozen,()))
    args=SimpleNamespace(text_checkpoint=str(p),output=str(tmp_path/'train'),seed=62421,
        train_count=28,dev_count=14,ood_count=14,test_count=14,device='cpu',batch_size=4,
        encoder_batch=8,max_tokens=128,steps=2,eval_every=1,lr=.03,probe_lr=.02)
    result=s.train(args)
    assert result['status']=='TRAINED_UNCERTIFIED'
    assert result['backbone_lora_updated']
    assert (Path(args.output)/'best.pt').is_file()
    ck=torch.load(Path(args.output)/'best.pt',map_location='cpu',weights_only=True)
    assert ck['schema']==s.SCHEMA and 'lora' in ck and 'probe' in ck and 'math_head' not in ck
    assert all(torch.equal(v,model.head.state_dict()[k]) for k,v in no_change.items())
    assert result['hybridlink_trained'] is False and result['lm_head_used'] is False
    with pytest.raises(RuntimeError,match='NONEMPTY_OUTPUT'):s.train(args)


def test_default_full_deck_is_disjoint_and_complete_after_retry_expansion():
    """Regression: original default exhausted scale-8 depth-1 cases at train-00175."""
    d=s.make_deck()
    assert {k:len(v) for k,v in d.items()}=={
        'train':2304,'dev':484,'ood':484,'test':484}
    all_rows=[r for rows in d.values() for r in rows]
    assert len({r['source'] for r in all_rows})==len(all_rows)
    assert len({r['case_id'] for r in all_rows})==448+96+96+96
    assert [r['source'] for r in d['train'] if r['case_id']=='train-00175']
    assert d['train'][0]['source']=='PROBLEM: 6+6'
    assert d['train'][0]['target']=='ANSWER: 12'
    assert d==s.make_deck()  # deterministic full run, including retry sequence
    for split,rows in d.items():
        by_case={}
        for r in rows:by_case.setdefault(r['case_id'],[]).append(r)
        for case in by_case.values():
            assert case[-1]['target'].startswith('ANSWER: ')
            assert case[-1]['target']==f'ANSWER: {case[-1]["answer"]}'
            for a,b in zip(case,case[1:]):assert a['target']==b['source']


def test_retry_expands_small_case_support_without_changing_initial_scale():
    assert [s._retry_scale(8,i) for i in range(9)]==[8]*8+[12]
    assert s._retry_scale(8,12)>s._retry_scale(8,8)
    assert s._retry_scale(1000000,0)==1000000
    assert s._retry_scale(1000000,4)==1000000
    for seed in (42,90271):
        d=s.make_deck(seed=seed)
        all_sources=[r['source'] for rows in d.values() for r in rows]
        assert len(set(all_sources))==len(all_sources)
