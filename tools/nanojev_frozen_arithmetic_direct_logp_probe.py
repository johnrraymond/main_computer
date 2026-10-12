#!/usr/bin/env python3
"""Direct conditional continuation-logP audit of three FROZEN LM backbones.

No CLEF head, no residual adapters, no optimizer, no generation and no training.
All models score the same 1,000 never-trained arithmetic problems. Both sum and
per-token mean logP are reported because label-token lengths can differ.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from nanojev_arithmetic_task_bank import LABELS
from nanojev_antirom_compression import render,renderer_fingerprint,LEVELS
from nanojev_frozen_arithmetic_direct_logp_bank import (
    DEFAULT_OUTPUT,DEFAULT_SEED,load as load_bank,create as create_bank,
)
from nanojev_arithmetic_runtime import DEFAULT_BANK
from nanojev_antirom_v2_stream_bank import DEFAULT_OUTPUT as V2_BANK

SCHEMA='nanojev.frozen-backbone-arithmetic-direct-logp.v1'
SOURCES={
    'qwen':{'id':'Qwen/Qwen3-0.6B','revision':'c1899de289a04d12100db370d81485cdf75e47ca'},
    'pythia':{'id':'EleutherAI/pythia-70m','revision':None},
    'tinystories':{'id':'roneneldan/TinyStories-33M','revision':None},
}


def emit(event,**kw):
    print(json.dumps({'event':event,**kw},sort_keys=True),flush=True)


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--legacy-bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--v2-bank-dir',type=Path,default=V2_BANK)
    p.add_argument('--count',type=int,default=1000)
    p.add_argument('--seed',type=int,default=DEFAULT_SEED)
    p.add_argument('--models',nargs='+',choices=tuple(SOURCES),default=list(SOURCES))
    p.add_argument('--levels',nargs='+',choices=LEVELS,default=['C0'],
                   help='C0 is the exact original English prompt; C5 can be added for symbolic comparison')
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--dtype',choices=('auto','float32','float16','bfloat16'),default='auto')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true',help='continue only verified result prefixes')
    p.add_argument('--prepare-only',action='store_true',help='create/verify 1000 cases, do not load models')
    p.add_argument('--report-every',type=int,default=50)
    a=p.parse_args(argv)
    if a.count<3 or a.report_every<=0:p.error('count must be >=3 and report-every > 0')
    if len(set(a.models))!=len(a.models) or len(set(a.levels))!=len(a.levels):
        p.error('duplicate models or levels are not allowed')
    return a


def softmax(values):
    if len(values)!=3 or not all(math.isfinite(v) for v in values):
        raise RuntimeError('FROZEN_LOGP_BAD_SCORES')
    top=max(values)
    weights=[math.exp(v-top) for v in values]
    norm=sum(weights)
    return [w/norm for w in weights]


def score_from_logits(*,torch,logits,prompt_length,answer_ids):
    """Scores P(answer|prompt), taking logits at prefix/tail prediction positions.

    logits: [3, max_prefix_plus_answer_length, vocab]. Continuations are
    tokenized SEPARATELY with add_special_tokens=False and concatenated to
    the shared prefix. This avoids accidentally scoring the prompt or pad.
    """
    if logits.ndim!=3 or logits.shape[0]!=len(LABELS) or prompt_length<1:
        raise RuntimeError('FROZEN_LOGP_INVALID_LOGIT_LAYOUT')
    sums=[];means=[];lengths=[]
    for i,ids in enumerate(answer_ids):
        if not ids or prompt_length+len(ids)>logits.shape[1]:
            raise RuntimeError('FROZEN_LOGP_ANSWER_TOKENS_NOT_VISIBLE')
        positions=torch.arange(prompt_length-1,prompt_length+len(ids)-1,device=logits.device)
        selected=logits[i,positions].float()
        targets=torch.tensor(ids,device=logits.device,dtype=torch.long)
        if (targets<0).any() or (targets>=selected.shape[-1]).any():
            raise RuntimeError('FROZEN_LOGP_TOKEN_ID_OUT_OF_RANGE')
        per_token=(selected.gather(1,targets[:,None]).squeeze(1)
                   -torch.logsumexp(selected,dim=-1))
        total=float(per_token.sum().item())
        if not math.isfinite(total):raise RuntimeError('FROZEN_LOGP_NONFINITE_SCORE')
        sums.append(total);means.append(total/len(ids));lengths.append(len(ids))
    return sums,means,lengths


def score_one(*,torch,model,tokenizer,prompt,device):
    # No chat templates: this tests the exact raw CLEF arithmetic text.
    prefix=tokenizer.encode(prompt,add_special_tokens=True)
    suffixes=[tokenizer.encode(' '+label,add_special_tokens=False) for label in LABELS]
    if not prefix or any(not x for x in suffixes):
        raise RuntimeError('FROZEN_LOGP_EMPTY_TOKEN_SEQUENCE')
    sequences=[prefix+suffix for suffix in suffixes]
    maximum=max(map(len,sequences))
    pad=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    if pad is None:pad=0
    x=torch.full((3,maximum),int(pad),dtype=torch.long,device=device)
    mask=torch.zeros_like(x)
    for i,ids in enumerate(sequences):
        x[i,:len(ids)]=torch.tensor(ids,dtype=torch.long,device=device)
        mask[i,:len(ids)]=1
    with torch.inference_mode():
        logits=model(input_ids=x,attention_mask=mask,use_cache=False).logits
        sums,means,lengths=score_from_logits(torch=torch,logits=logits,
                              prompt_length=len(prefix),answer_ids=suffixes)
    return {'sum_logp':sums,'mean_logp':means,'token_counts':lengths,
            'sum_probs':softmax(sums),'mean_probs':softmax(means),
            'prefix_tokens':len(prefix)}


def choose(probabilities):
    return LABELS[max(range(len(LABELS)),key=lambda k:probabilities[k])]


def record(row,score,level):
    if set(score)!={'sum_logp','mean_logp','token_counts','sum_probs','mean_probs','prefix_tokens'}:
        raise RuntimeError('FROZEN_LOGP_SCORE_SCHEMA_INVALID')
    prompt=render(level,row['a'],row['b'],row['threshold'])
    return {'id':row['id'],'gold':row['gold'],'a':row['a'],'b':row['b'],
            'threshold':row['threshold'],'scale':row['scale'],'carry':row['carry'],
            'distance':row['distance'],'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            **score,'sum_pred':choose(score['sum_probs']),
            'mean_pred':choose(score['mean_probs'])}


def summary(rows):
    if not rows:raise RuntimeError('FROZEN_LOGP_NO_SCORED_ROWS')
    result={'cases':len(rows),'class_counts':dict(Counter(r['gold'] for r in rows)),
            'answer_token_lengths':{label:dict(Counter(r['token_counts'][i] for r in rows))
                                    for i,label in enumerate(LABELS)},
            'modes':{}}
    for mode in ('sum','mean'):
        correct=0;ce=0.;conf={g:{p:0 for p in LABELS} for g in LABELS}
        by_gold=defaultdict(list);by_scale=defaultdict(list)
        margins=[];true_probs=[]
        for row in rows:
            probs=row[mode+'_probs'];p=row[mode+'_pred'];gold=row['gold']
            if len(probs)!=3 or not math.isclose(sum(probs),1.,rel_tol=0,abs_tol=1e-5):
                raise RuntimeError('FROZEN_LOGP_BAD_PROBABILITIES')
            k=LABELS.index(gold)
            if p==gold:correct+=1
            conf[gold][p]+=1
            ce-=math.log(max(probs[k],1e-300))
            true_probs.append(probs[k])
            margins.append(row[mode+'_logp'][k]-max(row[mode+'_logp'][i]
                                      for i in range(3) if i!=k))
            by_gold[gold].append(p==gold)
            by_scale[row['scale']].append(p==gold)
        result['modes'][mode]={'accuracy':correct/len(rows),'cross_entropy':ce/len(rows),
            'correct':correct,'mean_gold_probability':sum(true_probs)/len(rows),
            'mean_gold_logp_margin':sum(margins)/len(rows),
            'confusion':conf,
            'by_class_accuracy':{k:sum(v)/len(v) for k,v in sorted(by_gold.items())},
            'by_scale_accuracy':{k:sum(v)/len(v) for k,v in sorted(by_scale.items())},
            'predicted_counts':dict(Counter(r[mode+'_pred'] for r in rows))}
    return result


def checkpoint(root,model_name,level,bank_hash,rows,model_info):
    path=root/'results'/f'{model_name}.{level}.jsonl'
    meta=root/'results'/f'{model_name}.{level}.meta.json'
    expected={'schema':SCHEMA,'model':model_name,'source':SOURCES[model_name],
              'level':level,'renderer_sha256':renderer_fingerprint(level),
              'bank_sha256':bank_hash,
              'score_contract':'prefix-tokenized-with-specials + separate-raw-answer-tokens; exact conditional next-token sum and mean'}
    if model_info is not None:
        expected['loaded_revision']=model_info
    return path,meta,expected


def inspect_results(path,meta,expected,bank_rows,require_completed=False):
    if not path.exists() or not meta.exists():
        raise RuntimeError('FROZEN_LOGP_RESUME_PARTIAL_MISSING_FILES')
    actual=json.loads(meta.read_text(encoding='utf-8'))
    # actual revision may be available only once the model has been loaded;
    # ensure its source, renderer and bank were identical.
    for field,value in expected.items():
        if actual.get(field)!=value:raise RuntimeError('FROZEN_LOGP_RESUME_IDENTITY_DRIFT: '+field)
    recorded=[]
    with path.open('r',encoding='utf-8') as stream:
        for index,line in enumerate(stream):
            try:r=json.loads(line)
            except json.JSONDecodeError as exc:raise RuntimeError('FROZEN_LOGP_PARTIAL_JSONL_RECORD') from exc
            if index>=len(bank_rows):raise RuntimeError('FROZEN_LOGP_TOO_MANY_RECORDS')
            source=bank_rows[index]
            if (r.get('id')!=source['id'] or r.get('gold')!=source['gold']
                or r.get('a')!=source['a'] or r.get('b')!=source['b']
                or r.get('threshold')!=source['threshold']
                or r.get('prompt_sha256')!=hashlib.sha256(render(expected['level'],source['a'],source['b'],source['threshold']).encode()).hexdigest()
                or r.get('sum_pred')!=choose(r['sum_probs'])
                or r.get('mean_pred')!=choose(r['mean_probs'])):
                raise RuntimeError('FROZEN_LOGP_SAVED_RECORD_DRIFT')
            recorded.append(r)
    if require_completed and len(recorded)!=len(bank_rows):
        raise RuntimeError('FROZEN_LOGP_INCOMPLETE_RESULT_SET')
    return recorded


def load_source(model_name,args):
    import torch
    from transformers import AutoModelForCausalLM,AutoTokenizer
    src=SOURCES[model_name]
    device='cuda' if args.device=='auto' and torch.cuda.is_available() else (
        'cpu' if args.device=='auto' else args.device)
    if device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('FROZEN_LOGP_CUDA_NOT_AVAILABLE')
    dtype=(torch.float32 if device=='cpu' else torch.float16) if args.dtype=='auto' else getattr(torch,args.dtype)
    if device=='cpu' and dtype!=torch.float32:
        raise RuntimeError('FROZEN_LOGP_CPU_REQUIRES_FLOAT32')
    kw={'local_files_only':not args.allow_model_download,'trust_remote_code':False}
    if src['revision']:kw['revision']=src['revision']
    emit('frozen_logp_model_load_start',model=model_name,checkpoint=src,device=device,dtype=str(dtype))
    tokenizer=AutoTokenizer.from_pretrained(src['id'],**kw)
    model=AutoModelForCausalLM.from_pretrained(src['id'],torch_dtype=dtype,**kw)
    model.eval().to(device)
    for param in model.parameters():
        param.requires_grad_(False)
    resolved=getattr(model.config,'_commit_hash',None)
    metadata={'model':src['id'],'requested_revision':src['revision'],'resolved_commit':resolved,
              'model_type':getattr(model.config,'model_type',None),'device':device,'dtype':str(dtype)}
    emit('frozen_logp_model_ready',model=model_name,metadata=metadata,
         params_trainable=sum(p.numel() for p in model.parameters() if p.requires_grad))
    return torch,model,tokenizer,device,metadata


def run(args):
    root=Path(args.output_root).expanduser().resolve()
    if not (root/'bank_manifest.json').exists():
        if args.resume:raise RuntimeError('FROZEN_LOGP_CANNOT_RESUME_ABSENT_BANK')
        manifest=create_bank(root,args.legacy_bank_dir,args.v2_bank_dir,args.count,args.seed)
        emit('frozen_logp_bank_created',count=args.count,bank_sha256=manifest['questions_sha256'])
    questions,manifest=load_bank(root,args.legacy_bank_dir,args.v2_bank_dir,args.count,args.seed)
    emit('frozen_logp_bank_verified',count=len(questions),bank_sha256=manifest['questions_sha256'],
         class_counts=manifest['classes'])
    if args.prepare_only:
        emit('frozen_logp_prepared',model_loaded=False,scored=False)
        return
    (root/'results').mkdir(exist_ok=True)
    for model_name in args.models:
        torch,model,tokenizer,device,info=load_source(model_name,args)
        for level in args.levels:
            path,meta,expected=checkpoint(root,model_name,level,manifest['questions_sha256'],questions,info)
            if path.exists() or meta.exists():
                if not args.resume:raise RuntimeError('FROZEN_LOGP_RESULTS_EXIST_USE_RESUME: '+str(path))
                completed=inspect_results(path,meta,expected,questions)
            else:
                completed=[]
                meta.write_text(json.dumps(expected,sort_keys=True,indent=2)+'\n',encoding='utf-8')
            emit('frozen_logp_scoring_start',model=model_name,level=level,completed=len(completed),total=len(questions))
            with path.open('a',encoding='utf-8',newline='\n') as stream:
                for index in range(len(completed),len(questions)):
                    row=questions[index]
                    prompt=render(level,row['a'],row['b'],row['threshold'])
                    scored=score_one(torch=torch,model=model,tokenizer=tokenizer,prompt=prompt,device=device)
                    result=record(row,scored,level)
                    stream.write(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n')
                    # Per-row flush provides interruption safety without repeating questions.
                    stream.flush()
                    if (index+1)%args.report_every==0:
                        emit('frozen_logp_progress',model=model_name,level=level,
                             completed=index+1,total=len(questions))
            complete=inspect_results(path,meta,expected,questions,require_completed=True)
            report={'schema':SCHEMA,'model':model_name,'model_info':info,'level':level,
                    'bank_sha256':manifest['questions_sha256'],'question_count':len(questions),
                    'metrics':summary(complete)}
            result_path=root/'results'/f'{model_name}.{level}.summary.json'
            temp=result_path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n',encoding='utf-8')
            os.replace(temp,result_path)
            emit('frozen_logp_model_result',model=model_name,level=level,
                 sum=report['metrics']['modes']['sum'],mean=report['metrics']['modes']['mean'])
        del model
        if device=='cuda':torch.cuda.empty_cache()
    summarize_all(root,manifest,args)


def summarize_all(root,manifest,args):
    rows=[]
    for model in args.models:
        for level in args.levels:
            r=json.loads((root/'results'/f'{model}.{level}.summary.json').read_text(encoding='utf-8'))
            for mode in ('sum','mean'):
                item=r['metrics']['modes'][mode]
                rows.append({'model':model,'level':level,'scoring':mode,'questions':r['question_count'],
                             'accuracy':item['accuracy'],'cross_entropy':item['cross_entropy'],
                             'correct':item['correct'],'mean_gold_logp_margin':item['mean_gold_logp_margin'],
                             'mean_gold_probability':item['mean_gold_probability'],
                             'predicted_counts':item['predicted_counts']})
    result={'schema':SCHEMA,'case_sha256':manifest['questions_sha256'],
            'chance_accuracy':1/3,'uniform_cross_entropy':math.log(3),'rows':rows}
    (root/'summary.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    import csv
    with (root/'summary.csv').open('w',encoding='utf-8',newline='') as f:
        columns=['model','level','scoring','questions','accuracy','cross_entropy','correct',
                 'mean_gold_logp_margin','mean_gold_probability','predicted_counts']
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row,'predicted_counts':json.dumps(row['predicted_counts'],sort_keys=True)})
    emit('frozen_logp_probe_complete',summary=str(root/'summary.csv'),models=args.models,
         levels=args.levels,questions=manifest['count'])


if __name__=='__main__':
    try:run(parse())
    except Exception as exc:
        emit('frozen_logp_probe_error',error=str(exc))
        raise SystemExit(1)
