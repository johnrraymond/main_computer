#!/usr/bin/env python3
"""Zero-shot direct backbone test: exact unary comparison and exact unary addition.

Same sealed cases in decimal and literal | tally notation. Directly scores
LESS/EQUAL/MORE continuation probabilities, no CLEF, no adapters, no training.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import sys

import nanojev_frozen_arithmetic_direct_logp_probe as direct
import nanojev_frozen_exact_unary_bank as bank

SCHEMA='nanojev.frozen-exact-unary-direct-logp.v1'
DEFAULT_OUTPUT=Path(r'C:\Users\subsi\NanoJev\runs\frozen_exact_unary_direct_logp_v1')


def emit(event,**kwargs):
    direct.emit(event,**kwargs)


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--count',type=int,default=bank.DEFAULT_COUNT,help='questions per task, divisible by 3; default 300')
    p.add_argument('--seed',type=int,default=bank.DEFAULT_SEED)
    p.add_argument('--comparison-max',type=int,default=bank.DEFAULT_COMPARE_MAX)
    p.add_argument('--addition-max-operand',type=int,default=bank.DEFAULT_ADD_OPERAND_MAX)
    p.add_argument('--tasks',nargs='+',choices=bank.TASKS,default=list(bank.TASKS))
    p.add_argument('--representations',nargs='+',choices=bank.REPRESENTATIONS,default=list(bank.REPRESENTATIONS))
    p.add_argument('--models',nargs='+',choices=tuple(direct.SOURCES),default=list(direct.SOURCES))
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--dtype',choices=('auto','float32','float16','bfloat16'),default='auto')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--report-every',type=int,default=50)
    args=p.parse_args(argv)
    if args.report_every<=0:p.error('report-every must be positive')
    for group in (args.tasks,args.representations,args.models):
        if len(group)!=len(set(group)):p.error('duplicate task, representation or model')
    return args


def tally(n):
    if type(n) is not int or n<1:raise ValueError('EXACT_UNARY_BAD_QUANTITY')
    return '|'*n


def render(case,representation):
    if representation not in bank.REPRESENTATIONS:raise ValueError('EXACT_UNARY_BAD_REPRESENTATION')
    to_text=str if representation=='decimal' else tally
    meaning='Each | is exactly one unit.' if representation=='tally' else 'Values are ordinary positive integers.'
    if case['task']=='compare':
        expression=f'{to_text(case["a"])} ? {to_text(case["b"])}'
        instruction='Reply LESS if the left quantity is smaller, EQUAL if the quantities are equal, or MORE if the left quantity is larger.'
        task='Compare the two quantities.'
    elif case['task']=='add_compare':
        expression=f'{to_text(case["a"])} + {to_text(case["b"])} ? {to_text(case["target"])}'
        instruction='Reply LESS if the sum is smaller than the right quantity, EQUAL if equal, or MORE if the sum is larger.'
        task='Add the two quantities on the left and compare their sum to the quantity on the right.'
    else:raise ValueError('EXACT_UNARY_UNKNOWN_TASK')
    return f'{task}\n{meaning}\nExpression: {expression}\n{instruction}\nAnswer:'


def renderer_sha256():
    source='\n'.join((SCHEMA,inspect.getsource(tally),inspect.getsource(render)))
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def file_digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def result_row(case,score,representation):
    expected={'sum_logp','mean_logp','token_counts','sum_probs','mean_probs','prefix_tokens'}
    if set(score)!=expected:raise RuntimeError('EXACT_UNARY_SCORE_SCHEMA_MISMATCH')
    if any(len(score[field])!=len(bank.LABELS) for field in ('sum_logp','mean_logp','token_counts','sum_probs','mean_probs')):
        raise RuntimeError('EXACT_UNARY_SCORE_LENGTH_MISMATCH')
    if any(not all(math.isfinite(float(x)) for x in score[k]) for k in ('sum_logp','mean_logp','sum_probs','mean_probs')):
        raise RuntimeError('EXACT_UNARY_SCORE_NONFINITE')
    if any(abs(sum(score[k])-1.)>1e-5 or min(score[k])<0 for k in ('sum_probs','mean_probs')):
        raise RuntimeError('EXACT_UNARY_INVALID_SOFTMAX')
    return {'id':case['id'],'task':case['task'],'a':case['a'],'b':case['b'],
            'target':case['target'],'gold':case['gold'],'representation':representation,
            'prompt_sha256':file_digest(render(case,representation)),**score,
            'sum_pred':direct.choose(score['sum_probs']),
            'mean_pred':direct.choose(score['mean_probs'])}


def metadata(model_name,task,representation,manifest,info):
    return {'schema':SCHEMA,'model':model_name,'source':direct.SOURCES[model_name],
            'task':task,'representation':representation,
            'bank_sha256':manifest['cases_sha256'],
            'renderer_sha256':renderer_sha256(),
            'model_info':info,
            'scoring_contract':'no CLEF; direct prefix+label conditional logP; sum and mean; label preceded by ASCII space'}


def inspect_rows(path,meta_path,expected,cases,representation,*,complete=False):
    if not path.exists() or not meta_path.exists():
        raise RuntimeError('EXACT_UNARY_INCOMPLETE_OUTPUT_PAIR')
    actual=json.loads(meta_path.read_text(encoding='utf-8'))
    if actual!=expected:raise RuntimeError('EXACT_UNARY_RESUME_IDENTITY_DRIFT')
    rows=[]
    for i,line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        if i>=len(cases):raise RuntimeError('EXACT_UNARY_TOO_MANY_RESULTS')
        try:row=json.loads(line)
        except json.JSONDecodeError as exc:raise RuntimeError('EXACT_UNARY_CORRUPT_JSONL') from exc
        case=cases[i]
        score={key:row[key] for key in ('sum_logp','mean_logp','token_counts','sum_probs','mean_probs','prefix_tokens')}
        expected_record=result_row(case,score,representation)
        if row!=expected_record:
            raise RuntimeError('EXACT_UNARY_RECORDED_RESULT_DRIFT')
        rows.append(row)
    if complete and len(rows)!=len(cases):
        raise RuntimeError('EXACT_UNARY_INCOMPLETE_RESULTS')
    return rows


def report(rows):
    if not rows:raise RuntimeError('EXACT_UNARY_NO_ROWS')
    metrics={'questions':len(rows),'by_class':{},'by_scoring':{},
             'answer_tokens':{label:dict(Counter(r['token_counts'][i] for r in rows))
                              for i,label in enumerate(bank.LABELS)}}
    for mode in ('sum','mean'):
        matches=[];prob_correct=[];loss=[];margins=[]
        confused={label:{pred:0 for pred in bank.LABELS} for label in bank.LABELS}
        classes=defaultdict(list)
        for row in rows:
            gold=row['gold'];pred=row[mode+'_pred'];idx=bank.LABELS.index(gold)
            probs=row[mode+'_probs'];logps=row[mode+'_logp']
            matches.append(pred==gold)
            confused[gold][pred]+=1
            classes[gold].append(pred==gold)
            prob_correct.append(probs[idx])
            loss.append(-math.log(max(probs[idx],1e-300)))
            margins.append(logps[idx]-max(logps[j] for j in range(3) if j!=idx))
        swap_pairs=swap_correct=0
        if rows[0]['task']=='compare':
            lookup={(r['a'],r['b']):r[mode+'_pred'] for r in rows}
            for row in rows:
                if row['a']>=row['b']:continue
                swap_pairs+=1
                original=lookup[(row['a'],row['b'])]
                reversed_pred=lookup[(row['b'],row['a'])]
                opposite={'LESS':'MORE','MORE':'LESS','EQUAL':'EQUAL'}[original]
                swap_correct+=reversed_pred==opposite
        metrics['by_scoring'][mode]={
            'operand_swap_pairs':swap_pairs,
            'operand_swap_consistency':swap_correct/swap_pairs if swap_pairs else None,
            'accuracy':sum(matches)/len(rows),'correct':sum(matches),
            'cross_entropy':sum(loss)/len(loss),
            'mean_gold_probability':sum(prob_correct)/len(prob_correct),
            'mean_gold_logp_margin':sum(margins)/len(margins),
            'predicted_counts':dict(Counter(r[mode+'_pred'] for r in rows)),
            'by_class_accuracy':{k:sum(v)/len(v) for k,v in sorted(classes.items())},
            'confusion':confused}
    return metrics


def paired(decimal_rows,tally_rows):
    if len(decimal_rows)!=len(tally_rows):raise RuntimeError('EXACT_UNARY_PAIR_COUNT_MISMATCH')
    result={}
    for mode in ('sum','mean'):
        flips=improved=regressed=0
        for a,b in zip(decimal_rows,tally_rows):
            if a['id']!=b['id'] or a['gold']!=b['gold'] or a['representation']!='decimal' or b['representation']!='tally':
                raise RuntimeError('EXACT_UNARY_PAIR_IDENTITY_MISMATCH')
            before=a[mode+'_pred']==a['gold']
            after=b[mode+'_pred']==b['gold']
            flips+=a[mode+'_pred']!=b[mode+'_pred']
            improved+=not before and after
            regressed+=before and not after
        result[mode]={'prediction_flips':flips,'improved':improved,'regressed':regressed,
                      'net_correct':improved-regressed}
    return result


def save_json(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    os.replace(temp,path)


def export(root,models,tasks,reps,manifest):
    all_data=[]
    all_rows=[]
    for model_name in models:
        for task in tasks:
            compare_rows={}
            for representation in reps:
                path=root/'results'/f'{model_name}.{task}.{representation}.summary.json'
                if not path.exists():raise RuntimeError('EXACT_UNARY_EXPECTED_SUMMARY_MISSING: '+str(path))
                document=json.loads(path.read_text(encoding='utf-8'))
                if (document.get('schema')!=SCHEMA or document.get('model')!=model_name
                    or document.get('task')!=task or document.get('representation')!=representation
                    or document.get('bank_sha256')!=manifest['cases_sha256']):
                    raise RuntimeError('EXACT_UNARY_SUMMARY_IDENTITY_DRIFT')
                all_data.append(document)
                compare_rows[representation]=[json.loads(x) for x in
                    (root/'results'/f'{model_name}.{task}.{representation}.jsonl').read_text(encoding='utf-8').splitlines()]
            changes=paired(compare_rows['decimal'],compare_rows['tally']) if set(reps)==set(bank.REPRESENTATIONS) else None
            for document in all_data[-len(reps):]:
                for mode in ('sum','mean'):
                    metrics=document['metrics']['by_scoring'][mode]
                    change=changes[mode] if changes and document['representation']=='tally' else {}
                    all_rows.append({
                        'model':model_name,'task':task,'representation':document['representation'],
                        'scoring':mode,'questions':document['metrics']['questions'],
                        'correct':metrics['correct'],'accuracy':metrics['accuracy'],
                        'cross_entropy':metrics['cross_entropy'],
                        'mean_gold_logp_margin':metrics['mean_gold_logp_margin'],
                        'operand_swap_pairs':metrics['operand_swap_pairs'],
                        'operand_swap_consistency':(metrics['operand_swap_consistency'] if metrics['operand_swap_consistency'] is not None else ''),
                        'predicted_counts':json.dumps(metrics['predicted_counts'],sort_keys=True),
                        'by_class_accuracy':json.dumps(metrics['by_class_accuracy'],sort_keys=True),
                        'vs_decimal_prediction_flips':change.get('prediction_flips',''),
                        'vs_decimal_improved':change.get('improved',''),
                        'vs_decimal_regressed':change.get('regressed',''),
                        'vs_decimal_net_correct':change.get('net_correct','')})
    report_data={'schema':SCHEMA,'bank_sha256':manifest['cases_sha256'],
                 'chance_accuracy':1/3,'uniform_cross_entropy':math.log(3),
                 'case_count_per_task':manifest['count_per_task'],'results':all_data,'table':all_rows}
    save_json(root/'summary.json',report_data)
    keys=['model','task','representation','scoring','questions','correct','accuracy',
          'cross_entropy','mean_gold_logp_margin','operand_swap_pairs','operand_swap_consistency','predicted_counts','by_class_accuracy',
          'vs_decimal_prediction_flips','vs_decimal_improved','vs_decimal_regressed','vs_decimal_net_correct']
    out=root/'summary.csv';temp=out.with_suffix('.csv.tmp')
    with temp.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys)
        writer.writeheader();writer.writerows(all_rows)
    os.replace(temp,out)
    emit('exact_unary_probe_complete',summary=str(out),rows=len(all_rows),
         bank_sha256=manifest['cases_sha256'])


def run(args):
    root=Path(args.output_root).expanduser().resolve()
    kwargs={'count':args.count,'seed':args.seed,
            'compare_max':args.comparison_max,'add_operand_max':args.addition_max_operand}
    if (root/'bank_manifest.json').exists():
        rows,manifest=bank.load(root,**kwargs)
    else:
        if args.resume:raise RuntimeError('EXACT_UNARY_CANNOT_RESUME_MISSING_BANK')
        rows,manifest=bank.prepare(root,**kwargs)
    emit('exact_unary_bank_verified',questions=len(rows),sha256=manifest['cases_sha256'],
         class_counts=manifest['class_counts'],training=False,
         prompt_examples={task:render(next(r for r in rows if r['task']==task),'tally')
                          for task in bank.TASKS})
    if args.prepare_only:return
    output=root/'results';output.mkdir(parents=True,exist_ok=True)
    for model_name in args.models:
        torch,model,tokenizer,device,info=direct.load_source(model_name,args)
        try:
            for task in args.tasks:
                cases=[r for r in rows if r['task']==task]
                for rep in args.representations:
                    base=f'{model_name}.{task}.{rep}'
                    path=output/f'{base}.jsonl';meta=output/f'{base}.meta.json'
                    identity=metadata(model_name,task,rep,manifest,info)
                    if path.exists() or meta.exists():
                        if not args.resume:
                            raise RuntimeError('EXACT_UNARY_RESULTS_EXIST_USE_RESUME: '+base)
                        prior=inspect_rows(path,meta,identity,cases,rep)
                    else:
                        prior=[]
                        save_json(meta,identity)
                    emit('exact_unary_scoring_start',model=model_name,task=task,representation=rep,
                         completed=len(prior),total=len(cases))
                    with path.open('a',encoding='utf-8',newline='\n') as out:
                        for i in range(len(prior),len(cases)):
                            case=cases[i]
                            response=direct.score_one(torch=torch,model=model,tokenizer=tokenizer,
                                                      prompt=render(case,rep),device=device)
                            record=result_row(case,response,rep)
                            out.write(json.dumps(record,sort_keys=True,separators=(',',':'))+'\n')
                            out.flush()
                            if (i+1)%args.report_every==0:
                                emit('exact_unary_scoring_progress',model=model_name,task=task,
                                     representation=rep,done=i+1,total=len(cases))
                    scored=inspect_rows(path,meta,identity,cases,rep,complete=True)
                    document={'schema':SCHEMA,'model':model_name,'model_info':info,
                              'task':task,'representation':rep,
                              'bank_sha256':manifest['cases_sha256'],'metrics':report(scored)}
                    save_json(output/f'{base}.summary.json',document)
                    for mode,values in document['metrics']['by_scoring'].items():
                        emit('exact_unary_stage_result',model=model_name,task=task,
                             representation=rep,scoring=mode,accuracy=values['accuracy'],
                             cross_entropy=values['cross_entropy'],
                             predicted_counts=values['predicted_counts'])
        finally:
            del model
            if device=='cuda':torch.cuda.empty_cache()
    export(root,args.models,args.tasks,args.representations,manifest)


if __name__=='__main__':
    try:run(parse())
    except Exception as exc:
        emit('exact_unary_probe_error',error=str(exc))
        raise SystemExit(1)
