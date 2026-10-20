"""Read-only per-axis exponent/mantissa/sign and oracle-correction AB analysis."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_ordered_exponent_experiment as ab


def select_rows(teacher: Path, split: str, tier: str, per_mask: int):
    rows = hd.read_rows(teacher, split, tier)
    buckets = defaultdict(list)
    for row in rows:
        buckets[int(row['mask'])].append(row)
    picked = []
    for mask in sorted(buckets):
        group = sorted(buckets[mask], key=lambda r: int(r['instance']))
        if per_mask and len(group) > per_mask:
            # Deterministic spread across the full numeric range for both branches.
            indices = [(j * (len(group)-1)) // (per_mask-1) for j in range(per_mask)] if per_mask>1 else [len(group)//2]
            group = [group[i] for i in indices]
        picked.extend(group)
    return picked


def case_outputs(out, gold, v3):
    import torch
    s,e,f,nz = v3.target_parts(gold, out['exponent'].shape[-1]-1, gold.device)
    correct_exp = out['exponent'].clone().fill_(-100.)
    correct_exp.scatter_(-1, e.unsqueeze(-1), 100.)
    correct_sign = out['sign'].clone().fill_(-100.)
    correct_sign.scatter_(-1, s.unsqueeze(-1), 100.)
    cap=10_000_000
    def decode(sign, expo, frac):
        return v3.compose_logits(sign, expo, frac, cap)
    return {'raw':decode(out['sign'],out['exponent'],out['fraction']),
            'fix_exponent':decode(out['sign'],correct_exp,out['fraction']),
            'fix_sign':decode(correct_sign,out['exponent'],out['fraction']),
            'fix_fraction':decode(out['sign'],out['exponent'],f),
            'fix_all':decode(correct_sign,correct_exp,f),
            'true_exponent':e,
            'true_sign':s,
            'true_fraction':f,
            'pred_exponent':out['exponent'].argmax(-1),
            'pred_sign':out['sign'].argmax(-1),
            'exponent_prob':out['exponent'].softmax(-1).gather(-1,e.unsqueeze(-1)).squeeze(-1),
            'expected_exponent':(out['exponent'].softmax(-1)*torch.arange(out['exponent'].shape[-1],device=gold.device)).sum(-1),
            'nonzero':nz}


def evaluate(args):
    import torch
    from tools import nanojev_game_transform_experiment as ex
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    dest, manifest=ab.validate_branch(args.root,args.branch,args.source,args.teacher)
    ckpt = torch.load(dest/'latest.pt', map_location='cpu',weights_only=True)
    model=ex.load_current(args.source,args.device,'float16',False,ckpt['config']['seed'])
    model['decoder'].load_state_dict(ckpt['decoder'],strict=True)
    model['gamehead'].load_state_dict(ckpt['game_operator_head'],strict=True)
    model['decoder'].eval()
    model['gamehead'].eval()
    rows=select_rows(args.teacher,args.split,args.tier,args.per_mask)
    if not rows:raise ValueError('NO_PROBE_ROWS')
    cases=[]
    with torch.inference_mode():
        for i in range(0,len(rows),args.batch_size):
            part=rows[i:i+args.batch_size]
            eligible=[r for r in part if rt.token_eligible(model,r['prompt'])]
            if not eligible:
                continue
            out,_=ex.forward(model,[r['prompt'] for r in eligible])
            gold=torch.tensor([[float(r['x']),float(r['y'])] for r in eligible],device=args.device,dtype=torch.float32)
            fields=case_outputs(out,gold,model['v3'])
            for k,row in enumerate(eligible):
                item={'id':row['case_id'],'mask':int(row['mask']),'instance':int(row['instance'])}
                for col,axis in enumerate('xy'):
                    truth=float(gold[k,col]);correct_exp=int(fields['true_exponent'][k,col]);pred_exp=int(fields['pred_exponent'][k,col])
                    values={key:float(fields[key][k,col]) for key in ('raw','fix_exponent','fix_sign','fix_fraction','fix_all')}
                    item[axis]={'truth':truth,'predicted':values['raw'],
                                'true_sign':int(fields['true_sign'][k,col]),'predicted_sign':int(fields['pred_sign'][k,col]),
                                'true_exponent':correct_exp,'predicted_exponent':pred_exp,
                                'expected_exponent':float(fields['expected_exponent'][k,col]),
                                'true_fraction':float(fields['true_fraction'][k,col]),
                                'exponent_true_probability':float(fields['exponent_prob'][k,col]),
                                'nonzero':bool(fields['nonzero'][k,col]),
                                'relative_error':abs(values['raw']-truth)/max(1.,abs(truth)),
                                'passes':{key:abs(value-truth)/max(1.,abs(truth))<=0.05
                                          for key,value in values.items()}}
                cases.append(item)
    def metrics(group,axis):
        entries=[row[axis] for row in group]
        nonzero=[v for v in entries if v['nonzero']]
        num=len(entries)
        def acc(kind):return sum(v['passes'][kind] for v in entries)/num if num else None
        return {'n':num,'numeric_5pct':acc('raw'),
                'oracle_exponent_5pct':acc('fix_exponent'),
                'oracle_sign_5pct':acc('fix_sign'),
                'oracle_mantissa_5pct':acc('fix_fraction'),
                'all_components_oracle_5pct':acc('fix_all'),
                'exponent_class_accuracy':(sum(v['predicted_exponent']==v['true_exponent'] for v in nonzero)/len(nonzero) if nonzero else None),
                'sign_class_accuracy':sum(v['predicted_sign']==v['true_sign'] for v in entries)/num if num else None,
                'mean_exponent_class_error':(statistics.mean(abs(v['predicted_exponent']-v['true_exponent']) for v in nonzero) if nonzero else None),
                'mean_expected_exponent_distance':(statistics.mean(abs(v['expected_exponent']-v['true_exponent']) for v in nonzero) if nonzero else None),
                'mean_true_exponent_probability':(statistics.mean(v['exponent_true_probability'] for v in nonzero) if nonzero else None),
                'exponent_one_decade_errors':sum(abs(v['predicted_exponent']-v['true_exponent'])==1 for v in nonzero),
                'exponent_oracle_rescues':sum(not v['passes']['raw'] and v['passes']['fix_exponent'] for v in entries),
                'mean_relative_error':statistics.mean(v['relative_error'] for v in entries) if entries else None}
    summary={'all':{axis:metrics(cases,axis) for axis in 'xy'},
             'by_mask':{str(mask):{axis:metrics([r for r in cases if r['mask']==mask],axis) for axis in 'xy'}
                        for mask in sorted({r['mask'] for r in cases})}}
    summary['joint_5pct']=sum(r['x']['passes']['raw'] and r['y']['passes']['raw'] for r in cases)/len(cases)
    result={'schema':'nanojev.hawk-dove.ordered-exponent-probe.v1',
            'role':args.branch,'step':int(ckpt['step']), 'split':args.split,'tier':args.tier,
            'checkpoint_sha256':ab.sha(dest/'latest.pt'),
            'donor_sha256':manifest['donor_checkpoint_sha256'],
            'per_mask_requested':args.per_mask,
            'evaluated':len(cases),'summary':summary,
            'cases':cases,'no_training_performed':True}
    path=dest/f'ordered-exponent-{args.tier}-{args.split}-step-{ckpt["step"]}.json'
    ab.write_json(path,result)
    print(json.dumps({'status':'PASS','branch':args.branch,'step':ckpt['step'],'report':str(path),
                      'summary':summary['all'],'joint_5pct':summary['joint_5pct']},sort_keys=True))
    return result


def compare(args):
    root=args.root.expanduser().resolve()
    reports={}
    for role in ab.ROLE_OPTIONS:
        base=root/role
        files=sorted(base.glob(f'ordered-exponent-{args.tier}-{args.split}-step-*.json'),
                     key=lambda f:int(f.stem.rsplit('-',1)[-1]))
        if not files:raise FileNotFoundError('MISSING_AB_PROBE:'+role)
        reports[role]=json.loads(files[-1].read_text(encoding='utf-8'))
        if reports[role]['role']!=role:raise ValueError('ROLE_DRIFT')
    a=reports['control'];b=reports['ordered']
    if a['donor_sha256']!=b['donor_sha256']:raise ValueError('AB_DONOR_DRIFT')
    if a['step'] != b['step']:raise ValueError('AB_STEP_MISMATCH_RUN_EQUAL_ADDITIONAL_STEPS')
    if a['tier']!=b['tier'] or a['split']!=b['split'] or [r['id'] for r in a['cases']]!=[r['id'] for r in b['cases']]:
        raise ValueError('AB_DIFFERENT_EVALUATION_CASES')
    rows=[]
    for axis in 'xy':
        old=a['summary']['all'][axis];new=b['summary']['all'][axis]
        rows.append({'axis':axis,'control_numeric_5pct':old['numeric_5pct'],
                     'ordered_numeric_5pct':new['numeric_5pct'],
                     'numeric_delta':new['numeric_5pct']-old['numeric_5pct'],
                     'control_exponent_class_accuracy':old['exponent_class_accuracy'],
                     'ordered_exponent_class_accuracy':new['exponent_class_accuracy'],
                     'control_exponent_oracle_rescues':old['exponent_oracle_rescues'],
                     'ordered_exponent_oracle_rescues':new['exponent_oracle_rescues']})
    result={'schema':'nanojev.hawk-dove.ordered-exponent-comparison.v1',
            'step':a['step'],'tier':a['tier'],'split':a['split'],'n':len(a['cases']),
            'joint_5pct_control':a['summary']['joint_5pct'],
            'joint_5pct_ordered':b['summary']['joint_5pct'],
            'joint_delta':b['summary']['joint_5pct']-a['summary']['joint_5pct'],
            'axes':rows,'control_checkpoint':a['checkpoint_sha256'],
            'ordered_checkpoint':b['checkpoint_sha256']}
    ab.write_json(root/f'comparison-{args.tier}-{args.split}-step-{a["step"]}.json',result)
    print(json.dumps(result,indent=2,sort_keys=True))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('probe','compare'))
    p.add_argument('--root',type=Path,default=Path.home()/'NanoJev/runs/hawk_dove_million_ordered_exponent_ab_v1')
    p.add_argument('--source',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--teacher',type=Path,default=Path('runtime/hawk-dove-million/teacher-scientific-v2.sqlite3'))
    p.add_argument('--branch',choices=ab.ROLE_OPTIONS,default='ordered')
    p.add_argument('--split',choices=('train','dev'),default='dev')
    p.add_argument('--tier',choices=hd.TIERS,default='T02')
    p.add_argument('--per-mask',type=int,default=0,help='0=whole split; cap per mask for faster train diagnostics')
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.per_mask<0 or args.batch_size<1:raise ValueError('INVALID_PROBE_LIMIT')
    if args.mode=='probe':evaluate(args)
    else:compare(args)

if __name__=='__main__':main()
