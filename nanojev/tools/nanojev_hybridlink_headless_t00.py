#!/usr/bin/env python3
"""Headless T00: PROBLEM: a+b -> (ANSWER, signed numerical vector).

This deliberately tests one arithmetic computation and an output-control gate,
not the as-yet-untrained recursive PROBLEM payload or parent-stack unwinding.
The Qwen decoder (never its vocabulary LM head) encodes each input once. The
trainable HybridLink plus tiny numerical/control readout do the remainder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random

from tools import nanojev_hybridlink_micro_latent_transfer as transfer
from tools import nanojev_hybridlink_micro_reduction as micro
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA = 'nanojev.hybridlink.headless-t00.v1'
ACTIONS = ('PROBLEM','ANSWER')
DIGITS = 8
CAP = 10**DIGITS - 1
SCALES = (8, 64, 256, 4096, 65536, 1000000)
OOD_SCALES = (97, 997, 9997, 99997, 1999999)


def _sha(path):
    from tools import nanojev_qwen_hybridagent_activation as activation
    return activation.file_hash(Path(path))


def parents(args):
    text=Path(args.text_checkpoint)
    cfg=transfer._parent_config(text)
    link=Path(args.link_checkpoint)
    if not link.is_file():raise ValueError('T00_LINK_PARENT_MISSING')
    cpath=link.parent/'run_config.json'
    if not cpath.is_file() or json.loads(cpath.read_text())['schema']!=transfer.SCHEMA:
        raise RuntimeError('T00_LINK_PARENT_WRONG_FAMILY')
    return cfg


def _sample(rng, i, scale, kind):
    # Multiple arithmetic families, including near cancellation at large A.
    pattern=i%8
    if pattern==0:
        a=rng.randint(max(1,scale//2),scale);b=-a
    elif pattern in (1,2,3,4):
        a=rng.randint(max(1,scale//2),scale)
        d={1:1,2:-1,3:4,4:-4}[pattern]
        b=-a+d
    elif pattern==5:
        a=rng.randint(-scale,scale);b=rng.randint(-scale,scale)
    elif pattern==6:
        a=rng.randint(1,scale);b=rng.randint(1,scale)
    else:
        a=-rng.randint(1,scale);b=-rng.randint(1,scale)
    if rng.randrange(2):a,b=b,a
    return a,b


def make_deck(seed=9127,train_count=1024,dev_count=256,ood_count=256,test_count=256):
    counts={'train':train_count,'dev':dev_count,'ood':ood_count,'test':test_count}
    if any(x<16 for x in counts.values()):raise ValueError('T00_MINIMUM_SPLIT_16')
    rng=random.Random(seed)
    seen=set();deck={}
    for split,n in counts.items():
        rows=[]
        scales=OOD_SCALES if split=='ood' else SCALES
        for i in range(n):
            kind='PROBLEM' if i%5==4 else 'ANSWER'
            for attempt in range(500):
                scale=scales[(i//8+rng.randrange(2))%len(scales)]
                a,b=_sample(rng,i,scale,kind)
                if split=='train' and i<3:
                    a,b=((6,6),(6,-6),(-6,6))[i]
                c=rng.randint(-max(4,min(512,scale)),max(4,min(512,scale)))
                prompt=(f'PROBLEM: {a}+{b}' if kind=='ANSWER' else
                        f'PROBLEM: ({a}+{b})+{c}')
                if prompt not in seen and abs(a+b)<=CAP and abs(a+b+c)<=CAP:
                    seen.add(prompt);break
            else:raise RuntimeError('T00_DECK_EXHAUSTED')
            rows.append({'id':f'{split}-{i:05d}','prompt':prompt,'action':kind,
                         'answer':a+b if kind=='ANSWER' else None,
                         'a':a,'b':b,'c':c if kind=='PROBLEM' else None,
                         'depth':1 if kind=='ANSWER' else 2,
                         'sign':('zero' if a+b==0 else 'positive' if a+b>0 else 'negative'),
                         'scale':scale})
        deck[split]=rows
    assert len(seen)==sum(counts.values())
    return deck


def deck_hash(deck):
    return base.digest(deck)


def digit_labels(value):
    if not isinstance(value,int) or abs(value)>CAP:raise ValueError('T00_VALUE_OUT_OF_RANGE')
    sign=0 if value<0 else 1 if value==0 else 2
    mag=str(abs(value)).zfill(DIGITS)
    return sign,[int(c) for c in mag]


def number_from_logits(sign_logits,digit_logits):
    import torch
    assert sign_logits.ndim==2 and digit_logits.shape[:2]==(len(sign_logits),DIGITS)
    sign=sign_logits.argmax(-1).tolist()
    digits=digit_logits.argmax(-1).tolist()
    out=[]
    for s,ds in zip(sign,digits):
        mag=int(''.join(str(v) for v in ds))
        out.append(0 if s==1 else -mag if s==0 else mag)
    return out


def _numeric_module(hidden,device):
    import torch
    from torch import nn
    class Head(nn.Module):
        def __init__(self):
            super().__init__()
            self.pre=nn.Sequential(nn.LayerNorm(hidden),nn.Linear(hidden,hidden//2),nn.GELU())
            self.control=nn.Linear(hidden//2,2)
            self.sign=nn.Linear(hidden//2,3)
            self.digits=nn.Linear(hidden//2,DIGITS*10)
        def forward(self,x):
            h=self.pre(x.float())
            return self.control(h),self.sign(h),self.digits(h).reshape(-1,DIGITS,10)
    return Head().to(device)


def _encoded_features(backbone,tokenizer,rows,device,batch_size,limit=96):
    """No LM head. Qwen decoder hidden states only, batched/cached read-only."""
    import torch
    if any(p.requires_grad for p in backbone.parameters()):
        raise RuntimeError('T00_BACKBONE_MUST_BE_FROZEN')
    backbone.eval()
    decoder=backbone.get_decoder()
    pad=tokenizer.pad_token_id
    if pad is None:pad=tokenizer.eos_token_id
    if pad is None:raise RuntimeError('T00_NO_PAD_ID')
    chunks=[]
    with torch.inference_mode():
        for first in range(0,len(rows),batch_size):
            part=rows[first:first+batch_size]
            sequences=[tokenizer(r['prompt'],add_special_tokens=False)['input_ids'] for r in part]
            if not all(0<len(seq)<=limit for seq in sequences):raise RuntimeError('T00_PROMPT_TOKEN_LIMIT')
            lengths=torch.tensor([len(s) for s in sequences],device=device)
            ids=torch.full((len(part),int(lengths.max())),pad,device=device,dtype=torch.long)
            mask=torch.zeros_like(ids)
            for idx,seq in enumerate(sequences):
                ids[idx,:len(seq)]=torch.tensor(seq,dtype=torch.long,device=device)
                mask[idx,:len(seq)]=1
            # get_decoder() returns the transformer, bypassing LM projection.
            out=decoder(input_ids=ids,attention_mask=mask,use_cache=False,return_dict=True)
            features=out.last_hidden_state[torch.arange(len(part),device=device),lengths-1]
            chunks.append(features.detach().float().cpu())
    return torch.cat(chunks,dim=0).contiguous()


def _targets(rows):
    import torch
    action=torch.tensor([ACTIONS.index(row['action']) for row in rows],dtype=torch.long)
    signs=[];digits=[]
    for row in rows:
        s,ds=digit_labels(row['answer'] if row['answer'] is not None else 0)
        signs.append(s);digits.append(ds)
    return action,torch.tensor(signs,dtype=torch.long),torch.tensor(digits,dtype=torch.long)


def compute_loss(link,numeric,feat,action,sign,digits):
    import torch
    from torch.nn import functional as F
    latent=link(feat.float())
    logits,sgn,dig=numeric(latent)
    control=F.cross_entropy(logits,action)
    target=action==1
    if target.any():
        num=F.cross_entropy(sgn[target],sign[target])
        dd=F.cross_entropy(dig[target].reshape(-1,10),digits[target].reshape(-1))
    else:
        num=control*0;dd=control*0
    return control+num+dd,{'control':float(control.detach()),'sign':float(num.detach()),'digits':float(dd.detach())}


def measure(link,numeric,feat,rows,device,batch=128,ablation=False):
    import torch
    metrics={'action_correct':0,'answer_correct':0,'answers':0,
             'problem_correct':0,'problems':0,'zero_correct':0,'zeros':0,
             'near_correct':0,'near':0,'sign_correct':0,'exact_emitted':0}
    failures=[]
    with torch.inference_mode():
        for first in range(0,len(rows),batch):
            part=rows[first:first+batch]
            inputs=feat[first:first+len(part)].to(device)
            vec=link(inputs)
            if ablation:vec=torch.zeros_like(vec)
            ctrl,sgn,digits=numeric(vec)
            choices=ctrl.argmax(-1).tolist()
            values=number_from_logits(sgn,digits)
            for item,c,value in zip(part,choices,values):
                right=c==ACTIONS.index(item['action'])
                metrics['action_correct']+=right
                if item['action']=='PROBLEM':
                    metrics['problems']+=1;metrics['problem_correct']+=right
                else:
                    metrics['answers']+=1
                    correct=right and value==item['answer']
                    metrics['answer_correct']+=correct
                    metrics['sign_correct']+=(value>0)-(value<0)==(item['answer']>0)-(item['answer']<0) and right
                    if item['answer']==0:
                        metrics['zeros']+=1;metrics['zero_correct']+=correct
                    if abs(item['answer'])<=4:
                        metrics['near']+=1;metrics['near_correct']+=correct
                if not right or (item['answer'] is not None and value!=item['answer']):
                    if len(failures)<30:failures.append({'id':item['id'],'input':item['prompt'],
                      'expected_action':item['action'],'predicted_action':ACTIONS[c],
                      'expected_answer':item['answer'],'predicted_number':value if item['action']=='ANSWER' else None})
    for num,denom,label in (
        ('action_correct',len(rows),'action_accuracy'),
        ('answer_correct',metrics['answers'],'answer_accuracy'),
        ('problem_correct',metrics['problems'],'problem_gate_accuracy'),
        ('zero_correct',metrics['zeros'],'zero_accuracy'),
        ('near_correct',metrics['near'],'near_zero_accuracy'),
        ('sign_correct',metrics['answers'],'sign_accuracy')):
        metrics[label]=round(metrics[num]/denom,6) if denom else None
    return metrics,failures


def _load_backbone(args,config):
    model,tok,source,frozen,dirs=micro._load(args,checkpoint=Path(args.text_checkpoint))
    if source!=config['sources'] or frozen!=config['frozen_backbone']:
        raise RuntimeError('T00_TEXT_SOURCE_DRIFT')
    if args.link_checkpoint:
        transfer._load_link_into(model,Path(args.link_checkpoint),config,_sha(args.text_checkpoint))
    model.backbone.requires_grad_(False)
    model.head.requires_grad_(True)
    model.backbone.eval()
    return model,tok,source,frozen,dirs


def preview(args,config):
    deck=make_deck(args.seed,args.train_count,args.dev_count,args.ood_count,args.test_count)
    print('HEADLESS_T00_PREVIEW='+json.dumps({'schema':SCHEMA,'stage':'preview','model_loaded':False,
      'counts':{k:len(v) for k,v in deck.items()},'deck_sha256':deck_hash(deck),
      'sample':'PROBLEM: 6+6 -> ANSWER: 12','controls':ACTIONS,'number_codec':'sign + 8 fixed decimal digits',
      'lm_head_called':False,'recursion_implemented':False,
      'problem_payload_trained':False,'test_evaluated':False},sort_keys=True),flush=True)


def train(args,config):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    if args.steps<1 or args.eval_every<1 or args.batch_size<1 or args.encoder_batch<1:
        raise ValueError('T00_BAD_TRAIN_ARGUMENTS')
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('T00_REFUSES_NONEMPTY_OUTPUT')
    deck=make_deck(args.seed,args.train_count,args.dev_count,args.ood_count,args.test_count)
    model,tok,source,frozen,dirs=_load_backbone(args,config)
    for d in (*dirs,Path(args.text_checkpoint).parent,Path(args.link_checkpoint).parent):
        if out.resolve()==d.resolve() or out.resolve() in d.resolve().parents or d.resolve() in out.resolve().parents:
            raise RuntimeError('T00_OUTPUT_OVERLAPS_SOURCE')
    print('HEADLESS_T00_ENCODING='+json.dumps({'train':len(deck['train']),'dev':len(deck['dev']),
      'encoder_batch':args.encoder_batch,'lm_head_called':False},sort_keys=True),flush=True)
    xtrain=_encoded_features(model.backbone,tok,deck['train'],args.device,args.encoder_batch)
    xdev=_encoded_features(model.backbone,tok,deck['dev'],args.device,args.encoder_batch)
    assert xtrain.shape[1]==int(model.backbone.config.hidden_size)
    a_train,s_train,d_train=_targets(deck['train']);a_dev,s_dev,d_dev=_targets(deck['dev'])
    torch.manual_seed(args.seed)
    numeric=_numeric_module(xtrain.shape[1],args.device)
    model.head.to(dtype=torch.float32)
    model.head.train();numeric.train()
    optimizer=torch.optim.AdamW([*model.head.parameters(),*numeric.parameters()],lr=args.lr,weight_decay=0)
    old_lora=zero.lora_state(model.backbone)
    before_head={k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()}
    meta={'schema':SCHEMA,'stage':'headless-t00-train','sources':source,
       'frozen_backbone_sha256':frozen,'text_parent_sha256':_sha(args.text_checkpoint),
       'link_parent_sha256':_sha(args.link_checkpoint),'seed':args.seed,
       'train_count':args.train_count,'dev_count':args.dev_count,
       'ood_count':args.ood_count,'test_count':args.test_count,
       'deck_sha256':deck_hash(deck),'steps':args.steps,'batch_size':args.batch_size,
       'lr':args.lr,'eval_every':args.eval_every,'selection':'dev numerical + control loss',
       'trainable':'hybridlink and numeric/control projection, Qwen frozen',
       'runtime_lm_head':'never invoked','recursive_payload':'NOT_TRAINED'}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'run_config.json',meta)
    rng=random.Random(args.seed+123)
    best=float('inf');best_step=None
    for step in range(1,args.steps+1):
        indices=rng.sample(range(len(xtrain)),min(args.batch_size,len(xtrain)))
        ii=torch.tensor(indices,dtype=torch.long)
        features=xtrain[ii].to(args.device)
        optimizer.zero_grad(set_to_none=True)
        loss,parts=compute_loss(model.head,numeric,features,a_train[ii].to(args.device),
                                s_train[ii].to(args.device),d_train[ii].to(args.device))
        if not torch.isfinite(loss):raise RuntimeError('T00_TRAIN_NONFINITE')
        loss.backward();torch.nn.utils.clip_grad_norm_([*model.head.parameters(),*numeric.parameters()],1.)
        optimizer.step()
        if step%args.eval_every and step!=args.steps:continue
        model.head.eval();numeric.eval()
        with torch.inference_mode():
            losses=[]
            for i in range(0,len(xdev),args.batch_size):
                f=xdev[i:i+args.batch_size].to(args.device)
                dev_loss,_=compute_loss(model.head,numeric,f,a_dev[i:i+args.batch_size].to(args.device),
                                        s_dev[i:i+args.batch_size].to(args.device),d_dev[i:i+args.batch_size].to(args.device))
                losses.append((float(dev_loss),len(f)))
            avg=sum(v*n for v,n in losses)/len(xdev)
            scores,_=measure(model.head,numeric,xdev,deck['dev'],args.device)
        if avg<best:
            best=avg;best_step=step
            base._atomic_torch(out/'best.pt',{'schema':SCHEMA,'status':'T00_TRAINED_UNCERTIFIED',
                 'config_hash':base.digest(meta),'step':step,'dev_loss':avg,
                 'head':{k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()},
                 'numeric':{k:v.detach().cpu().clone() for k,v in numeric.state_dict().items()}})
        print('HEADLESS_T00_PROGRESS='+json.dumps({'step':step,'train_loss':round(float(loss.detach()),6),
           'dev_loss':round(avg,6),'dev_action_accuracy':scores['action_accuracy'],
           'dev_answer_accuracy':scores['answer_accuracy'],'dev_zero_accuracy':scores['zero_accuracy'],
           'dev_near_zero_accuracy':scores['near_zero_accuracy'],'best_step':best_step,
           'headless':True},sort_keys=True),flush=True)
        model.head.train();numeric.train()
    assert all(torch.equal(old_lora[k],zero.lora_state(model.backbone)[k]) for k in old_lora)
    assert zero.frozen_backbone_sha256(model.backbone)==frozen
    assert any(not torch.equal(before_head[k],model.head.state_dict()[k].detach().cpu()) for k in before_head)
    result={'schema':SCHEMA,'status':'T00_TRAINED_UNCERTIFIED','best_step':best_step,
            'best_dev_loss':best,'dev_only':True,'ood_evaluated':False,'test_evaluated':False,
            'lm_head_used_for_inference':False,'recursion_implemented':False}
    base._atomic_json(out/'result.json',result)
    print('HEADLESS_T00_TRAIN_RESULT='+json.dumps(result,sort_keys=True),flush=True)


def probe(args,config):
    import torch
    from tools import nanojev_qwen_hybridagent_activation as activation
    ck=Path(args.checkpoint)
    if not ck.is_file():raise ValueError('T00_CHECKPOINT_MISSING')
    saved=json.loads((ck.parent/'run_config.json').read_text(encoding='utf8'))
    if saved['schema']!=SCHEMA or saved['stage']!='headless-t00-train':raise RuntimeError('T00_BAD_SAVED_CONFIG')
    if saved['text_parent_sha256']!=_sha(args.text_checkpoint) or saved['link_parent_sha256']!=_sha(args.link_checkpoint):
        raise RuntimeError('T00_PARENT_DRIFT')
    deck=make_deck(saved['seed'],saved['train_count'],saved['dev_count'],saved['ood_count'],saved['test_count'])
    if saved['deck_sha256']!=deck_hash(deck):raise RuntimeError('T00_DECK_DRIFT')
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('T00_PROBE_REFUSES_NONEMPTY_OUTPUT')
    if args.split=='test' and (ck.parent/'test_consumed.json').exists():raise RuntimeError('T00_TEST_ALREADY_CONSUMED')
    model,tok,sources,frozen,dirs=_load_backbone(args,config)
    if sources!=saved['sources'] or frozen!=saved['frozen_backbone_sha256']:
        raise RuntimeError('T00_BACKBONE_PROVENANCE_DRIFT')
    check=torch.load(ck,weights_only=True,map_location='cpu')
    if check['schema']!=SCHEMA or check['config_hash']!=base.digest(saved):raise RuntimeError('T00_CHECKPOINT_DRIFT')
    model.head.load_state_dict(check['head'],strict=True)
    numeric=_numeric_module(int(model.backbone.config.hidden_size),args.device)
    numeric.load_state_dict(check['numeric'],strict=True)
    model.head.eval();numeric.eval()
    rows=deck[args.split]
    if args.case_limit:
        if args.split=='test':raise RuntimeError('T00_TEST_REQUIRES_FULL_SPLIT')
        rows=rows[:args.case_limit]
    print('HEADLESS_T00_PROBE_BEGIN='+json.dumps({'split':args.split,'cases':len(rows),
      'controls':['intact','zero_vector'],'lm_head_called':False},sort_keys=True),flush=True)
    x=_encoded_features(model.backbone,tok,rows,args.device,args.encoder_batch)
    normal,errors=measure(model.head,numeric,x,rows,args.device)
    zero,zero_errors=measure(model.head,numeric,x,rows,args.device,ablation=True)
    result={'schema':SCHEMA,'status':'PROBE_UNCERTIFIED','split':args.split,
        'cases':len(rows),'intact':normal,'zero_vector':zero,
        'lm_head_used':False,'text_generation_used':False,
        'problem_payload_trained':False,'recursion_implemented':False,
        'test_evaluated':args.split=='test','checkpoint_sha256':activation.file_hash(ck)}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'result.json',result)
    base._atomic_json(out/'errors.json',{'intact':errors,'zero_vector':zero_errors})
    if args.split=='test':base._atomic_json(ck.parent/'test_consumed.json',{'result_dir':str(out),'checkpoint_sha256':_sha(ck)})
    print('HEADLESS_T00_PROBE_RESULT='+json.dumps(result,sort_keys=True),flush=True)



def predict(args,config):
    """Single user-supplied input; no oracle arithmetic, LM logits, or token decode."""
    import re
    import torch
    pattern=r'(?:[+-]?\d+\+[+-]?\d+|\([+-]?\d+\+[+-]?\d+\)\+[+-]?\d+)'
    if not args.problem or not re.fullmatch(pattern,args.problem.strip()):
        raise ValueError('T00_PREDICT_SUPPORTS_SIGNED_BINARY_SUM_OR_ONE_NESTED_SUM')
    ck=Path(args.checkpoint)
    if not ck.is_file():raise ValueError('T00_CHECKPOINT_MISSING')
    saved=json.loads((ck.parent/'run_config.json').read_text())
    if saved['schema']!=SCHEMA or saved['stage']!='headless-t00-train':
        raise RuntimeError('T00_BAD_SAVED_CONFIG')
    if saved['text_parent_sha256']!=_sha(args.text_checkpoint) or saved['link_parent_sha256']!=_sha(args.link_checkpoint):
        raise RuntimeError('T00_PARENT_DRIFT')
    check=torch.load(ck,weights_only=True,map_location='cpu')
    if check['schema']!=SCHEMA or check['config_hash']!=base.digest(saved):
        raise RuntimeError('T00_CHECKPOINT_DRIFT')
    model,tok,sources,frozen,dirs=_load_backbone(args,config)
    if sources!=saved['sources'] or frozen!=saved['frozen_backbone_sha256']:
        raise RuntimeError('T00_BACKBONE_PROVENANCE_DRIFT')
    model.head.load_state_dict(check['head'],strict=True)
    numeric=_numeric_module(int(model.backbone.config.hidden_size),args.device)
    numeric.load_state_dict(check['numeric'],strict=True)
    model.head.eval();numeric.eval()
    prompt='PROBLEM: '+args.problem.strip()
    features=_encoded_features(model.backbone,tok,[{'prompt':prompt}],args.device,1)
    with torch.inference_mode():
        ctrl,sgn,dig=numeric(model.head(features.to(args.device)))
        typ=ACTIONS[int(ctrl[0].argmax())]
        val=number_from_logits(sgn,dig)[0] if typ=='ANSWER' else None
    result={'schema':SCHEMA,'input':prompt,'control':typ,'answer':val,
        'lm_head_used':False,'language_generated':False,
        'problem_payload_trained':False,'certification':'UNCERTIFIED'}
    print('HEADLESS_T00_PREDICTION='+json.dumps(result,sort_keys=True),flush=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('preview','train','probe','predict'),default='preview')
    parser.add_argument('--text-checkpoint',required=True)
    parser.add_argument('--link-checkpoint',required=True)
    parser.add_argument('--checkpoint')
    parser.add_argument('--problem',help='For --stage predict, e.g. 6+6')
    parser.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    parser.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_headless_t00_v1'))
    parser.add_argument('--seed',type=int,default=9127)
    parser.add_argument('--train-count',type=int,default=1024)
    parser.add_argument('--dev-count',type=int,default=256)
    parser.add_argument('--ood-count',type=int,default=256)
    parser.add_argument('--test-count',type=int,default=256)
    parser.add_argument('--steps',type=int,default=2400)
    parser.add_argument('--batch-size',type=int,default=64)
    parser.add_argument('--encoder-batch',type=int,default=16)
    parser.add_argument('--eval-every',type=int,default=200)
    parser.add_argument('--lr',type=float,default=.0001)
    parser.add_argument('--split',choices=('dev','ood','test'),default='dev')
    parser.add_argument('--case-limit',type=int)
    parser.add_argument('--baseline');parser.add_argument('--backbone');parser.add_argument('--activation');parser.add_argument('--format')
    parser.add_argument('--allow-model-download',action='store_true')
    args=parser.parse_args(argv)
    if args.case_limit is not None and args.case_limit<1:parser.error('T00_BAD_CASE_LIMIT')
    if args.stage in ('probe','predict') and not args.checkpoint:parser.error('T00_NEEDS_TRAINED_CHECKPOINT')
    if args.stage!='probe' and args.case_limit:parser.error('T00_CASE_LIMIT_ONLY_FOR_PROBE')
    cfg=parents(args)
    if args.stage=='preview':preview(args,cfg)
    elif args.stage=='train':train(args,cfg)
    elif args.stage=='predict':predict(args,cfg)
    else:probe(args,cfg)


if __name__=='__main__':main()
