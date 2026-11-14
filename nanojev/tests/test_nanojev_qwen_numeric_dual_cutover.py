import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import torch
from torch import nn
from tools import nanojev_qwen_numeric_dual_cutover as ct
from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_hybridlink_rung_reduction as base

@pytest.mark.parametrize('s,expected',[
    ('PROBLEM: 6+6',12),('PROBLEM: (3+5)+6',8),
    ('PROBLEM: ((3+5)+6)+4',8),('PROBLEM: -8+7',-1),
    ('PROBLEM: (-2)+(-3)',-5),('PROBLEM: ((-5)+3)+12',-2),
    ('ANSWER: 36',36),('ANSWER: -100000',-100000)])
def test_next_numeric_output_for_each_boundary(s,expected):
    assert ct.numeric_label(s)==expected

@pytest.mark.parametrize('bad',[
    'PROBLEM: __import__("os").system("exit")',
    'PROBLEM: 5*9','PROBLEM: foo+2','PROBLEM: 3.14+2',
    'PROBLEM: 2+3j', 'UNKNOWN: 12'])
def test_no_unsafe_expression_eval(bad):
    with pytest.raises((ValueError,SyntaxError)):ct.numeric_label(bad)


class DummyTokenizer:
    pad_token_id=0
    def __call__(self,s,add_special_tokens=False):return {'input_ids':[1+ord(c)%64 for c in s]}

class DummyDecoder(nn.Module):
    def __init__(self):
        super().__init__();self.embed=nn.Embedding(68,24);self.q_proj=nn.Linear(24,24)
        self.v_proj=nn.Linear(24,24)
    def forward(self,*,input_ids,attention_mask,use_cache,return_dict):
        x=self.embed(input_ids);h=(x*attention_mask[...,None]).sum(1)/attention_mask.sum(1)[:,None]
        return SimpleNamespace(last_hidden_state=self.q_proj(x)+self.v_proj(x)+h[:,None,:])
class DummyBackbone(nn.Module):
    def __init__(self):
        super().__init__();self.decoder=DummyDecoder();self.config=SimpleNamespace(hidden_size=24)
        self.lm_head=nn.Linear(24,68)
    def get_decoder(self):return self.decoder
    def forward(self,*a,**kw):raise AssertionError('VOCABULARY HEAD FORBIDDEN')
class DummyModel(nn.Module):
    def __init__(self):super().__init__();self.backbone=DummyBackbone();self.head=nn.Linear(24,24)


def fake_stage1(monkeypatch):
    import torch.nn.functional as F
    schema=ct.PARENT_SCHEMA
    def deck(seed,train_count,dev_count,ood_count,test_count):
        out={}
        cases=[('PROBLEM: 6+6','ANSWER: 12','ANSWER'),
               ('PROBLEM: (3+5)+6','PROBLEM: 8+6','PROBLEM'),
               ('PROBLEM: 8+6','ANSWER: 14','ANSWER'),
               ('PROBLEM: (-3)+4','ANSWER: 1','ANSWER')]
        for split,count in [('train',train_count),('dev',dev_count),('ood',ood_count),('test',test_count)]:
            rows=[]
            for i in range(count):
                a,b,action=cases[i%len(cases)]
                # Distinguish prompts while keeping reducible additions.
                n=i//len(cases)
                if n:
                    a=f'PROBLEM: {n}+{i}'
                    b=f'ANSWER: {n+i}'
                    action='ANSWER'
                rows.append({'source':a,'target':b,'action':action})
            out[split]=rows
        return out
    def encode(bb,tok,strings,device,batch_size,max_tokens,grad=False):
        from contextlib import nullcontext
        out=[]
        for k in range(0,len(strings),batch_size):
            texts=strings[k:k+batch_size];ids=[tok(s)['input_ids'] for s in texts];size=max(map(len,ids))
            xx=torch.zeros((len(ids),size),dtype=torch.long);mask=torch.zeros_like(xx)
            for i,v in enumerate(ids):xx[i,:len(v)]=torch.tensor(v);mask[i,:len(v)]=1
            with nullcontext() if grad else torch.no_grad():
                z=bb.get_decoder()(input_ids=xx,attention_mask=mask,use_cache=False,return_dict=True)
                out.append(z.last_hidden_state[torch.arange(len(ids)),torch.tensor([len(q)-1 for q in ids])])
        return torch.cat(out)
    class Probe(nn.Module):
        def __init__(self):super().__init__();self.fc=nn.Linear(24,2);self.next=nn.Linear(24,24)
        def forward(self,h):return self.fc(h.float()),F.normalize(self.next(h.float()),dim=-1)
    def objective(p,x,t,a,keys):
        ctrl,y=p(x)
        targ=F.normalize(t.float(),dim=-1)
        return F.cross_entropy(ctrl,a)+F.mse_loss(y,targ),{}
    def evaluate(bb,tok,p,rows,teacher,device,encode_batch,max_tokens):
        with torch.no_grad():
            logits,out=p(encode(bb,tok,[r['source'] for r in rows],device,encode_batch,max_tokens))
            acc=float((logits.argmax(-1)==torch.tensor([int(r['action']=='ANSWER') for r in rows])).float().mean())
            match=float(((out-F.normalize(teacher.float(),dim=-1))**2).mean())
        return {'payload_accuracy':max(0.,min(1.,1-match)),'control_accuracy':acc,'errors':[],
                'answer_payload_accuracy':0.5,'next_problem_payload_accuracy':0.5}
    import copy
    torch.manual_seed(33)
    template=DummyModel();template.backbone.requires_grad_(False);template.head.requires_grad_(False)
    zero.install_lora(template.backbone,rank=2,alpha=2)
    def make_model(args):
        model=copy.deepcopy(template)
        return model,DummyTokenizer(),{'dummy':'source-hash'},zero.frozen_backbone_sha256(model.backbone),(),{}
    fake=SimpleNamespace(SCHEMA=schema,ACTIONS=('PROBLEM','ANSWER'),make_deck=deck,
         deck_hash=base.digest,_load_model=make_model,_encode=encode,
         _heads=lambda h,dev:Probe(),_loss=objective,_evaluation=evaluate)
    monkeypatch.setattr(ct,'stage1',lambda:fake)
    return fake


def make_parent(tmp_path,fake):
    torch.manual_seed(111)
    model,tok,sources,frozen,_,_=fake._load_model(SimpleNamespace())
    parent=tmp_path/'stage1'/'best.pt';parent.parent.mkdir()
    text=tmp_path/'text'/'best.pt';text.parent.mkdir()
    old_lora=zero.lora_state(model.backbone)
    cfg1={'schema':'nanojev.hybridlink.anonymous-micro-reduction.v1'}
    (text.parent/'run_config.json').write_text(json.dumps(cfg1))
    torch.save({'schema':cfg1['schema'],'config_hash':base.digest(cfg1),'lora':old_lora},text)
    config={'schema':ct.PARENT_SCHEMA,'stage':'train-qwen',
            'text_parent_sha256':ct.file_hash(text),'source_sha256':sources,
            'frozen_sha256':frozen,'seed':1,'train_count':16,
            'dev_count':8,'ood_count':8,'test_count':8}
    deck=fake.make_deck(config['seed'],config['train_count'],config['dev_count'],config['ood_count'],config['test_count'])
    config['deck_sha256']=fake.deck_hash(deck)
    (parent.parent/'run_config.json').write_text(json.dumps(config))
    from torch import nn
    torch.save({'schema':ct.PARENT_SCHEMA,'config_hash':base.digest(config),
                'lora':old_lora,'probe':fake._heads(24,'cpu').state_dict()},parent)
    return text,parent,config


def args_for(tmp_path,text,parent):
    return SimpleNamespace(stage1_checkpoint=str(parent),text_checkpoint=str(text),output=str(tmp_path/'cutover'),
       device='cpu',seed=19,scale=64.,steps=2,eval_every=1,batch_size=4,encoder_batch=4,
       max_tokens=128,lr=.01,numeric_lr=.01,numeric_weight=.2,split='dev',checkpoint=None)


def test_full_cutover_train_probe_handoff_without_lm_or_hybridlink(monkeypatch,tmp_path):
    fake=fake_stage1(monkeypatch);text,parent,parentcfg=make_parent(tmp_path,fake)
    a=args_for(tmp_path,text,parent)
    saved_sha=ct.file_hash(parent)
    prev=ct.preview(a)
    assert prev['model_loaded'] is False and prev['counts']['train']['numerical_examples']==16
    result=ct.train(a)
    assert result['status']=='TRAINED_UNCERTIFIED' and result['lm_head_used'] is False
    assert result['original_checkpoint_unchanged'] is True and ct.file_hash(parent)==saved_sha
    ck=torch.load(tmp_path/'cutover'/'best.pt',weights_only=True)
    assert 'numeric' in ck and 'lora' in ck and 'probe' in ck and 'lm_head' not in ck
    a.checkpoint=str(tmp_path/'cutover'/'best.pt');a.output=str(tmp_path/'dev_probe')
    report=ct.probe(a)
    assert report['metrics']['numeric']['count']==8
    assert 'complex_code_mae' in report['metrics']['numeric']
    a.state='PROBLEM: 6+6'
    monkeypatch.setattr(ct,'numeric_label',lambda _:(_ for _ in ()).throw(AssertionError('ORACLE_CALLED_AT_INFERENCE')))
    peek=ct.inspect(a)
    assert peek['lm_head_called'] is False and peek['oracle_inference'] is False
    assert 'complex_number' in peek and 'direct_number' in peek
    a.output=str(tmp_path/'handoff')
    handoff=ct.handoff(a)
    assert handoff['status']=='PREPARED_NOT_ACTIVATED' and handoff['requires_hybridlink_stage2']
    assert handoff['feature_names']==['direct_rho','complex_rho','a','b','delta_rho']


def test_fail_closed_on_wrong_parent_hash(monkeypatch,tmp_path):
    fake=fake_stage1(monkeypatch);text,parent,_=make_parent(tmp_path,fake)
    a=args_for(tmp_path,text,parent)
    text.write_bytes(b'corrupted')
    with pytest.raises(RuntimeError,match='TEXT_PARENT_MISMATCH'):ct.load_parent(a)
