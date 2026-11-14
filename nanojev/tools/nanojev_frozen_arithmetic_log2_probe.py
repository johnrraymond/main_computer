#!/usr/bin/env python3
"""Paired, intentionally non-equivalent log2 rescaling of frozen arithmetic logP.

Take the SAME sealed 1,000-question bank and labels as the direct C0 probe.
Replace ONLY the three positive integers in each English question by log2(n),
printed to 8 decimal places. In particular, do NOT transform the gold label:
log2(a)+log2(b) != log2(a+b). This mismatch is intentional and audited.
No CLEF, adapter training, fine tuning, optimizer, or answer generation.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
from collections import Counter

import nanojev_frozen_arithmetic_direct_logp_probe as direct
from nanojev_arithmetic_task_bank import render_prompt

SCHEMA='nanojev.frozen-arithmetic-direct-logp-log2.v1'
DEFAULT_OUTPUT=Path(r'C:\Users\subsi\NanoJev\runs\frozen_arithmetic_direct_logp_log2_v1')
DECIMALS=8


def emit(event,**kw):
    direct.emit(event,**kw)


def log2_number(n:int)->str:
    if type(n) is not int or n<=0:
        raise ValueError('FROZEN_LOG2_REQUIRES_POSITIVE_INTEGER: '+str(n))
    return f'{math.log2(n):.{DECIMALS}f}'


def literal_display_gold(case:dict)->str:
    a,b,t=(float(log2_number(case[key])) for key in ('a','b','threshold'))
    delta=a+b-t
    return 'LESS' if delta<0 else ('MORE' if delta>0 else 'EQUAL')


def render_log2(case:dict)->str:
    """Preserve English syntax and candidate labels; rescale ONLY a,b,t."""
    return render_prompt(*(log2_number(case[key]) for key in ('a','b','threshold')))


def fingerprint()->str:
    body=(SCHEMA,str(DECIMALS),inspect.getsource(log2_number),
          inspect.getsource(render_log2),inspect.getsource(literal_display_gold),inspect.getsource(render_prompt))
    return hashlib.sha256(json.dumps(body,separators=(',',':')).encode()).hexdigest()


def digest(text:str)->str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-root',type=Path,default=direct.DEFAULT_OUTPUT,
                   help='Existing original C0 probe root, including its 1000-question sealed bank and results')
    p.add_argument('--output-root',type=Path,default=DEFAULT_OUTPUT,
                   help='Separate output; existing original C0 results are never overwritten')
    p.add_argument('--legacy-bank-dir',type=Path,default=direct.DEFAULT_BANK)
    p.add_argument('--v2-bank-dir',type=Path,default=direct.V2_BANK)
    p.add_argument('--count',type=int,default=1000)
    p.add_argument('--seed',type=int,default=direct.DEFAULT_SEED)
    p.add_argument('--models',nargs='+',choices=tuple(direct.SOURCES),default=list(direct.SOURCES))
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--dtype',choices=('auto','float32','float16','bfloat16'),default='auto')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--report-every',type=int,default=50)
    args=p.parse_args(argv)
    if args.count<3 or args.report_every<=0 or len(set(args.models))!=len(args.models):
        p.error('invalid count, report interval, or duplicated model names')
    return args


def paths(root:Path,model_name:str):
    return (root/'results'/f'{model_name}.C0.log2.jsonl',
            root/'results'/f'{model_name}.C0.log2.meta.json')


def expected_meta(model_name,bank_sha,model_info):
    return {'schema':SCHEMA,'model':model_name,'source':direct.SOURCES[model_name],
            'level':'C0','numeric_transform':'log2_each_numeric_literal',
            'precision_decimal_places':DECIMALS,'renderer_sha256':fingerprint(),
            'original_bank_sha256':bank_sha,'original_gold_rule':'a+b COMPARE threshold',
            'transformed_gold_rule':'PRESERVE ORIGINAL; not comparison of displayed decimal sums',
            'score_contract':'direct raw LM prefix+candidate conditional logP, total and per-token mean',
            'loaded_revision':model_info}


def make_record(case,score):
    result=direct.record(case,score,'C0')
    result['prompt_sha256']=digest(render_log2(case))
    result['numeric_transform']='log2'
    result['rescaled_values']={key:log2_number(case[key]) for key in ('a','b','threshold')}
    # Audit intentionally false log identity separately from the gold labeling.
    result['log2_addition_identity_error']=math.log2(case['a'])+math.log2(case['b'])-math.log2(case['a']+case['b'])
    result['literal_displayed_gold']=literal_display_gold(case)
    result['deliberate_label_mismatch']=result['literal_displayed_gold']!=case['gold']
    return result


def inspect_records(path,meta_path,expected,cases,complete=False):
    if not path.exists() or not meta_path.exists():
        raise RuntimeError('FROZEN_LOG2_INCOMPLETE_RESULTS_PAIR')
    meta=json.loads(meta_path.read_text(encoding='utf-8'))
    if meta!=expected:
        raise RuntimeError('FROZEN_LOG2_IDENTITY_DRIFT')
    rows=[]
    for idx,line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        if idx>=len(cases):raise RuntimeError('FROZEN_LOG2_TOO_MANY_RESULTS')
        row=json.loads(line);src=cases[idx]
        if (row.get('id')!=src['id'] or row.get('gold')!=src['gold']
            or any(row.get(k)!=src[k] for k in ('a','b','threshold'))
            or row.get('prompt_sha256')!=digest(render_log2(src))
            or row.get('rescaled_values')!={k:log2_number(src[k]) for k in ('a','b','threshold')}
            or row.get('numeric_transform')!='log2'
            or row.get('literal_displayed_gold')!=literal_display_gold(src)
            or row.get('deliberate_label_mismatch')!=(row.get('literal_displayed_gold')!=src['gold'])
            or not math.isclose(row.get('log2_addition_identity_error',float('inf')),
                math.log2(src['a'])+math.log2(src['b'])-math.log2(src['a']+src['b']),abs_tol=1e-12)
            or row.get('sum_pred')!=direct.choose(row['sum_probs'])
            or row.get('mean_pred')!=direct.choose(row['mean_probs'])):
            raise RuntimeError('FROZEN_LOG2_SAVED_RESULT_DRIFT: '+str(idx))
        rows.append(row)
    if complete and len(rows)!=len(cases):raise RuntimeError('FROZEN_LOG2_INCOMPLETE_PROBE')
    return rows


def paired_report(cases,baseline,log2_rows):
    if len(baseline)!=len(cases) or len(log2_rows)!=len(cases):
        raise RuntimeError('FROZEN_LOG2_PAIRED_LENGTH_MISMATCH')
    result={'original_c0':direct.summary(baseline),'log2_c0':direct.summary(log2_rows),'paired':{},
            'deliberate_label_mismatch_cases':sum(r['deliberate_label_mismatch'] for r in log2_rows),
            'literal_displayed_label_counts':dict(Counter(r['literal_displayed_gold'] for r in log2_rows))}
    for mode in ('sum','mean'):
        win=lose=flips=0
        for case,ref,new in zip(cases,baseline,log2_rows):
            if case['id']!=ref['id'] or case['id']!=new['id'] or ref['gold']!=new['gold']:
                raise RuntimeError('FROZEN_LOG2_UNPAIRED_DATA')
            before=ref[mode+'_pred']==case['gold'];after=new[mode+'_pred']==case['gold']
            win+=after and not before
            lose+=before and not after
            flips+=ref[mode+'_pred']!=new[mode+'_pred']
        result['paired'][mode]={'improved_cases':win,'regressed_cases':lose,
                               'prediction_flips':flips,'net_correct_gain':win-lose,
                               'accuracy_gain':(win-lose)/len(cases)}
    return result


def write_atomic_json(path:Path,data):
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    os.replace(tmp,path)


def run(args):
    baseline_root=args.baseline_root.expanduser().resolve(strict=True)
    root=args.output_root.expanduser().resolve()
    if root==baseline_root:
        raise RuntimeError('FROZEN_LOG2_OUTPUT_MUST_DIFFER_FROM_ORIGINAL')
    cases,manifest=direct.load_bank(baseline_root,args.legacy_bank_dir,args.v2_bank_dir,args.count,args.seed)
    bank_sha=manifest['questions_sha256']
    if any(min(case[k] for k in ('a','b','threshold'))<=0 for case in cases):
        raise RuntimeError('FROZEN_LOG2_ZERO_OR_NEGATIVE_BANK_VALUES')
    if args.prepare_only:
        emit('frozen_log2_bank_verified',bank_sha256=bank_sha,count=len(cases),
             example={'source':{k:cases[0][k] for k in ('a','b','threshold','gold')},
                      'prompt':render_log2(cases[0])},training=False,model_loaded=False)
        return
    (root/'results').mkdir(parents=True,exist_ok=True)
    summary_rows=[]
    for name in args.models:
        orig_path,orig_meta,orig_expected=direct.checkpoint(
            baseline_root,name,'C0',bank_sha,cases,None)
        baseline=direct.inspect_results(orig_path,orig_meta,orig_expected,cases,require_completed=True)
        torch,model,tokenizer,device,info=direct.load_source(name,args)
        output,meta_path=paths(root,name)
        expected=expected_meta(name,bank_sha,info)
        if output.exists() or meta_path.exists():
            if not args.resume:raise RuntimeError('FROZEN_LOG2_OUTPUT_EXISTS_USE_RESUME: '+str(output))
            collected=inspect_records(output,meta_path,expected,cases)
        else:
            collected=[]
            write_atomic_json(meta_path,expected)
        emit('frozen_log2_scoring_start',model=name,done=len(collected),total=len(cases))
        with output.open('a',encoding='utf-8',newline='\n') as stream:
            for idx in range(len(collected),len(cases)):
                case=cases[idx];prompt=render_log2(case)
                score=direct.score_one(torch=torch,model=model,tokenizer=tokenizer,prompt=prompt,device=device)
                item=make_record(case,score)
                stream.write(json.dumps(item,sort_keys=True,separators=(',',':'))+'\n');stream.flush()
                if (idx+1)%args.report_every==0:
                    emit('frozen_log2_progress',model=name,done=idx+1,total=len(cases))
        scored=inspect_records(output,meta_path,expected,cases,complete=True)
        report=paired_report(cases,baseline,scored)
        write_atomic_json(root/'results'/f'{name}.C0.log2.summary.json',
                         {'schema':SCHEMA,'model':name,'bank_sha256':bank_sha,'results':report})
        for mode in ('sum','mean'):
            before=report['original_c0']['modes'][mode]
            after=report['log2_c0']['modes'][mode]
            comp=report['paired'][mode]
            row={'model':name,'level':'C0','transformation':'log2_original_gold','scoring':mode,
                 'questions':len(cases),'raw_accuracy':before['accuracy'],
                 'log2_accuracy':after['accuracy'],'accuracy_gain':comp['accuracy_gain'],
                 'raw_ce':before['cross_entropy'],'log2_ce':after['cross_entropy'],
                 'prediction_flips':comp['prediction_flips'],
                 'deliberate_label_mismatch_cases':report['deliberate_label_mismatch_cases'],
                 'improved':comp['improved_cases'],
                 'regressed':comp['regressed_cases'],
                 'log2_predicted_counts':json.dumps(after['predicted_counts'],sort_keys=True)}
            summary_rows.append(row)
            emit('frozen_log2_model_result',**row)
        del model
        if device=='cuda':torch.cuda.empty_cache()
    write_atomic_json(root/'summary.json',{'schema':SCHEMA,'bank_sha256':bank_sha,
                                          'identity':fingerprint(),'rows':summary_rows})
    dst=root/'summary.csv';temp=dst.with_name(dst.name+'.tmp')
    with temp.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(summary_rows[0]));writer.writeheader();writer.writerows(summary_rows)
    os.replace(temp,dst)
    emit('frozen_log2_probe_complete',summary=str(dst),questions=len(cases))


if __name__=='__main__':
    try:run(parse())
    except Exception as exc:
        emit('frozen_log2_probe_error',error=str(exc))
        raise SystemExit(1)
