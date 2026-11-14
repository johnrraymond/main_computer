#!/usr/bin/env python3
"""Cross saved backbone/readout checkpoints for an actual learned arithmetic repair.

A backbone is every AdditionNet parameter EXCEPT sign_head.* and digit_head.*.
Both sign/digit heads travel together. No weights are trained, saved, or altered.
The full 30-case local control deck is read from the prior frozen evidence.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
from pathlib import Path

import torch

from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_hard_lasso as lasso
from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_ladder import atomic_json
from tools.nanojev_zero_line_zero_only import input_tensor

SCHEMA = 'nanojev.emp-backbone-readout-crossover.v1'
READOUT = ('sign_head.', 'digit_head.')
ARMS = ('B0R0', 'B0R1', 'B1R0', 'B1R1')


def is_readout(name):
    return name.startswith(READOUT)


def verify_parts(before, after):
    """Reject incompatible snapshots, missing heads, or nonfinite tensors."""
    if set(before) != set(after):
        raise RuntimeError('CROSSOVER_INCOMPATIBLE_KEYS')
    expected = {'sign_head.weight', 'sign_head.bias', 'digit_head.weight', 'digit_head.bias'}
    if {key for key in before if is_readout(key)} != expected:
        raise RuntimeError('CROSSOVER_UNEXPECTED_READOUT')
    groups = {'backbone': [], 'sign_head': [], 'digit_head': []}
    for name in sorted(before):
        a, b = before[name], after[name]
        if not isinstance(a, torch.Tensor) or not isinstance(b, torch.Tensor):
            raise RuntimeError('CROSSOVER_INVALID_STATE_TENSOR')
        if a.shape != b.shape or a.dtype != b.dtype:
            raise RuntimeError(f'CROSSOVER_INCOMPATIBLE_TENSOR:{name}')
        if not (torch.isfinite(a).all() and torch.isfinite(b).all()):
            raise RuntimeError(f'CROSSOVER_NONFINITE_TENSOR:{name}')
        section = ('sign_head' if name.startswith('sign_head.') else
                   'digit_head' if name.startswith('digit_head.') else 'backbone')
        groups[section].append(name)
    return groups


def mix_state(before, after, backbone_after, readout_after):
    verify_parts(before, after)
    return {k: (after if (readout_after if is_readout(k) else backbone_after) else before)[k]
            for k in before}


def param_drift(before, after):
    groups = verify_parts(before, after)
    report = {}
    for group, keys in groups.items():
        old_sq = new_sq = changed_sq = 0.
        changes = []
        for k in keys:
            a = before[k].detach().to(dtype=torch.float64, device='cpu')
            b = after[k].detach().to(dtype=torch.float64, device='cpu')
            old_sq += float(a.square().sum())
            new_sq += float(b.square().sum())
            d = float((b-a).square().sum())
            changed_sq += d
            changes.append({'name':k,'l2_change':d**.5})
        report[group] = {'tensor_count':len(keys),'old_l2':old_sq**.5,
                         'new_l2':new_sq**.5,'change_l2':changed_sq**.5,
                         'relative_change':changed_sq**.5/max(old_sq**.5,1e-12),
                         'tensors':sorted(changes,key=lambda x:-x['l2_change'])}
    return report


def read_evidence(root):
    path = Path(root)/'natural_repair.csv'
    if not path.is_file():
        raise FileNotFoundError(f'CROSSOVER_MISSING_EVIDENCE:{path}')
    result = []
    seen = set()
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        reader = csv.DictReader(f)
        required = {'a','b','target','before','after','previously_wrong','repaired','regressed'}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise RuntimeError('CROSSOVER_BAD_EVIDENCE_COLUMNS')
        for r in reader:
            a,b,target,old,new = [int(r[k]) for k in ('a','b','target','before','after')]
            key=(a,b)
            if key in seen or a+b!=target:
                raise RuntimeError('CROSSOVER_INVALID_OR_DUPLICATED_CARD')
            seen.add(key)
            was_wrong = old!=target
            if (r['previously_wrong'].lower()=='true') != was_wrong:
                raise RuntimeError('CROSSOVER_INVALID_OLD_CORRECTNESS')
            if (r['repaired'].lower()=='true') != (was_wrong and new==target):
                raise RuntimeError('CROSSOVER_INVALID_REPAIR_FLAG')
            if (r['regressed'].lower()=='true') != (not was_wrong and new!=target):
                raise RuntimeError('CROSSOVER_INVALID_REGRESSION_FLAG')
            result.append({'a':a,'b':b,'target':target,'before':old,'after':new,
                           'previously_wrong':was_wrong})
    if not result or not any(x['previously_wrong'] for x in result) or not any(
            not x['previously_wrong'] for x in result):
        raise RuntimeError('CROSSOVER_NEEDS_DEFECTS_AND_STABLE_CONTROLS')
    return result


def check_evidence_provenance(cross_root, run, before_stage, after_stage, before_path, after_path):
    cfg_path=Path(cross_root)/'config.json'
    result_path=Path(cross_root)/'result.json'
    if not (cfg_path.is_file() and result_path.is_file()):
        raise RuntimeError('CROSSOVER_MISSING_CROSS_TRANSFER_MANIFEST')
    cfg=json.loads(cfg_path.read_text('utf-8'))
    result=json.loads(result_path.read_text('utf-8'))
    if (cfg.get('schema')!=cross.SCHEMA or result.get('schema')!=cross.SCHEMA or
        int(cfg.get('before_stage',-1))!=before_stage or
        int(cfg.get('after_stage',-1))!=after_stage or
        int(result.get('before_stage',-1))!=before_stage or
        int(result.get('after_stage',-1))!=after_stage or
        Path(cfg.get('source_run','')).resolve()!=Path(run).resolve() or
        cfg.get('before_sha256')!=cross.sha256(before_path) or
        cfg.get('after_sha256')!=cross.sha256(after_path)):
        raise RuntimeError('CROSSOVER_EVIDENCE_CHECKPOINT_MISMATCH')
    return cfg


def head_details(output, cards):
    """Native sign/digit decisions and original-model gold log score.

    The native training CE uses sign CE PLUS MEAN digit CE. It is not
    a normalized probability distribution over seven-digit integers.
    """
    sign=output['sign'].float()
    digits=output['digits'].float()
    targets=torch.tensor([int(r['target']) for r in cards],device=sign.device,dtype=torch.long)
    sign_label=targets.sign()+1
    digit_labels=target_digits(targets)
    gold_log_score=-dual.loss_per_card(output,targets)
    sign_competitor=sign.clone()
    sign_competitor.scatter_(1,sign_label[:,None],-torch.inf)
    sign_margin=sign.gather(1,sign_label[:,None]).squeeze(1)-sign_competitor.amax(1)
    d_competitor=digits.clone()
    d_competitor.scatter_(2,digit_labels[:,:,None],-torch.inf)
    d_margin=digits.gather(2,digit_labels[:,:,None]).squeeze(2)-d_competitor.amax(2)
    d_pred=digits.argmax(-1)
    s_pred=sign.argmax(-1)
    predictions=dual.decoded(output)
    results=[]
    for i,r in enumerate(cards):
        results.append({'prediction':int(predictions[i]),'correct':int(predictions[i])==r['target'],
                        'gold_log_score':float(gold_log_score[i]),
                        'sign_pred_class':int(s_pred[i]),
                        'sign_gold_class':int(sign_label[i]),
                        'sign_gold_margin':float(sign_margin[i]),
                        'digit_pred':[int(x) for x in d_pred[i].tolist()],
                        'digit_gold':[int(x) for x in digit_labels[i].tolist()],
                        'digit_gold_margins':[float(x) for x in d_margin[i].tolist()],
                        'sign_logits':[float(x) for x in sign[i].tolist()],
                        'digit_logits':[[float(x) for x in y] for y in digits[i].tolist()]})
    return results


def evaluate_arm(model, states, rows, device, batch):
    model.load_state_dict(states,strict=True)
    model.requires_grad_(False).eval()
    result=[]
    with torch.inference_mode():
        for start in range(0,len(rows),batch):
            chunk=rows[start:start+batch]
            tensor=input_tensor([(r['a'],r['b'],r['target'],0,max(abs(r['a']),abs(r['b'])))
                                 for r in chunk],device)
            out=model(tensor)
            result.extend(head_details(out,chunk))
    return result


def classify_case(row, predictions):
    """Answer sufficiency on this one arithmetic case; no causal claim about internals."""
    if not row['previously_wrong']:
        return 'previously_correct'
    good=lambda name: predictions[name]==row['target']
    if good('B0R1') and good('B1R0'):
        return 'either_single_component_suffices'
    if good('B0R1'):
        return 'new_readout_suffices'
    if good('B1R0'):
        return 'new_backbone_suffices'
    return 'neither_single_component_suffices'


def aggregate(rows, arms):
    case_rows=[]; detail_rows=[]; logit_rows=[]
    for i,original in enumerate(rows):
        p={arm:arms[arm][i]['prediction'] for arm in ARMS}
        if p['B0R0']!=original['before'] or p['B1R1']!=original['after']:
            raise RuntimeError(f'CROSSOVER_BASELINE_DRIFT:{original["a"]}:{original["b"]}')
        goldscores={arm:arms[arm][i]['gold_log_score'] for arm in ARMS}
        interaction=(goldscores['B1R1']-goldscores['B1R0']-
                     goldscores['B0R1']+goldscores['B0R0'])
        decision=classify_case(original,p)
        case={**original,'category':decision,
              **{f'{arm}_prediction':p[arm] for arm in ARMS},
              **{f'{arm}_correct':p[arm]==original['target'] for arm in ARMS},
              **{f'{arm}_gold_log_score':goldscores[arm] for arm in ARMS},
              'readout_gain_old_backbone':goldscores['B0R1']-goldscores['B0R0'],
              'backbone_gain_old_readout':goldscores['B1R0']-goldscores['B0R0'],
              'interaction_gold_log_score':interaction}
        case_rows.append(case)
        for arm in ARMS:
            v=arms[arm][i]
            detail_rows.append({'a':original['a'],'b':original['b'],'target':original['target'],
                                'previously_wrong':original['previously_wrong'],'arm':arm,
                                'prediction':v['prediction'],'correct':v['correct'],
                                'gold_log_score':v['gold_log_score'],
                                'sign_gold_class':v['sign_gold_class'],
                                'sign_pred_class':v['sign_pred_class'],
                                'sign_gold_margin':v['sign_gold_margin'],
                                'digit_gold':json.dumps(v['digit_gold']),
                                'digit_pred':json.dumps(v['digit_pred']),
                                'digit_gold_margins':json.dumps(v['digit_gold_margins'])})
            logit_rows.append({'a':original['a'],'b':original['b'],'target':original['target'],
                               'arm':arm,'sign_logits':v['sign_logits'],
                               'digit_logits':v['digit_logits']})
    old_bad=[r for r in case_rows if r['previously_wrong']]
    old_good=[r for r in case_rows if not r['previously_wrong']]
    stats={}
    for arm in ARMS:
        stats[arm]={'repaired_previous_failures':sum(r[f'{arm}_correct'] for r in old_bad),
                    'previous_failures':len(old_bad),
                    'damaged_previous_controls':sum(not r[f'{arm}_correct'] for r in old_good),
                    'previous_controls':len(old_good),
                    'total_correct':sum(r[f'{arm}_correct'] for r in case_rows),
                    'total':len(case_rows),
                    'mean_gold_log_score':sum(r[f'{arm}_gold_log_score'] for r in case_rows)/len(case_rows)}
    classes={name:sum(r['category']==name for r in old_bad) for name in (
        'either_single_component_suffices','new_readout_suffices',
        'new_backbone_suffices','neither_single_component_suffices')}
    summary={'schema':SCHEMA,'status':'complete','cases':len(rows),
             'initial_failures':len(old_bad),'stable_controls':len(old_good),
             'arms':stats,'failure_categories':classes,
             'mean_interaction_on_failures':sum(r['interaction_gold_log_score'] for r in old_bad)/len(old_bad),
             'limitations':[
                'Hybrid heads/backbones from different checkpoints may be incompatible',
                'Native gold log score is sign CE plus mean digit CE, not integer-class probability',
                'Component swaps identify sufficiency for tested predictions, not unique localization',
                'Thirty local cards, not a population-level accuracy estimate']}
    return summary,case_rows,detail_rows,logit_rows


def atomic_csv(path, rows):
    if not rows: raise RuntimeError('CROSSOVER_EMPTY_TABLE')
    p=Path(path);temp=p.with_suffix(p.suffix+'.tmp')
    with temp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp,p)


def atomic_gzip_jsonl(path,rows):
    p=Path(path);temp=p.with_suffix(p.suffix+'.tmp')
    with gzip.open(temp,'wt',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,sort_keys=True)+'\n')
    os.replace(temp,p)


def run(args):
    if args.before_stage<0 or args.after_stage<=args.before_stage or args.batch<1:
        raise ValueError('CROSSOVER_INVALID_ARGUMENTS')
    run_root=Path(args.run).resolve()
    cross_root=Path(args.cross_transfer).resolve()
    out=Path(args.output).resolve()
    if out==run_root or out==cross_root or out==Path(args.campaign).resolve():
        raise ValueError('CROSSOVER_OUTPUT_MUST_BE_SEPARATE')
    config=json.loads((run_root/'config.json').read_text('utf-8'))
    p0=run_root/f'stage_{args.before_stage:03d}'/'mastered_snapshot.pt'
    p1=run_root/f'stage_{args.after_stage:03d}'/'mastered_snapshot.pt'
    check_evidence_provenance(cross_root,run_root,args.before_stage,args.after_stage,p0,p1)
    before=cross.snapshot(p0,config,args.before_stage)
    after=cross.snapshot(p1,config,args.after_stage)
    rows=read_evidence(cross_root)
    changes=param_drift(before,after)
    model,_,_,_,_=lasso.initialize(Path(args.campaign).resolve(),args.ladder_output,
                      Path(args.campaign).resolve()/args.hard_state_dir/'latest.pt',args.device)
    model.requires_grad_(False).eval()
    # Reject incompatible shape even if two snapshots agree with one another.
    model_keys=set(model.state_dict())
    if model_keys!=set(before):
        raise RuntimeError('CROSSOVER_MODEL_SNAPSHOT_KEYS_DIFFER')
    out.mkdir(parents=True,exist_ok=True)
    params={'schema':SCHEMA,'run':str(run_root),'cross_transfer':str(cross_root),
            'before_stage':args.before_stage,'after_stage':args.after_stage,
            'before_sha256':cross.sha256(p0),'after_sha256':cross.sha256(p1),
            'natural_repair_sha256':cross.sha256(cross_root/'natural_repair.csv'),
            'campaign':str(Path(args.campaign).resolve()),
            'ladder_output':str(Path(args.ladder_output).resolve()),
            'hard_state_dir':args.hard_state_dir,'batch':args.batch,
            'device':args.device,'source_manifest_sha256':cross.sha256(cross_root/'config.json')}
    cfg_path=out/'config.json'
    if cfg_path.is_file() and json.loads(cfg_path.read_text('utf-8'))!=params:
        raise RuntimeError('CROSSOVER_OUTPUT_CONFIG_DRIFT')
    atomic_json(cfg_path,params)
    arms={}
    for arm,ba,ra in (("B0R0",False,False),("B0R1",False,True),
                      ("B1R0",True,False),("B1R1",True,True)):
        state=mix_state(before,after,ba,ra)
        arms[arm]=evaluate_arm(model,state,rows,args.device,args.batch)
        print('EMP_BACKBONE_READOUT_ARM='+json.dumps({'arm':arm,
              'correct':sum(r['correct'] for r in arms[arm]),'cases':len(rows)},sort_keys=True),flush=True)
    result,case_rows,details,logits=aggregate(rows,arms)
    result.update({'before_stage':args.before_stage,'after_stage':args.after_stage,
                   'checkpoint_sha256':[params['before_sha256'],params['after_sha256']],
                   'output':str(out),
                   'parameter_changes':{k:{kk:v for kk,v in d.items() if kk!='tensors'}
                                        for k,d in changes.items()}})
    atomic_json(out/'component_changes.json',changes)
    atomic_csv(out/'cases.csv',case_rows)
    atomic_csv(out/'head_diagnostics.csv',details)
    atomic_gzip_jsonl(out/'head_logits.jsonl.gz',logits)
    atomic_json(out/'result.json',result)
    print('EMP_BACKBONE_READOUT_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True)
    p.add_argument('--cross-transfer',required=True)
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--before-stage',type=int,default=27)
    p.add_argument('--after-stage',type=int,default=28)
    p.add_argument('--batch',type=int,default=128)
    return p


if __name__=='__main__':
    run(parser().parse_args())
