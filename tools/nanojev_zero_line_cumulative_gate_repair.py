#!/usr/bin/env python3
"""Repair ONLY the 160:191 neural gate, warm-starting the saved cumulative candidate.

The certified base, the promoted 128:159 bank/router, and the trained 160:191
specialist stay frozen. No gold sum, correct-expert ID, magnitude rule, or
answer-correctness lookup is consulted by the inference path. Ground-truth
arithmetic is used for TRAIN supervision and for offline certification only.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import torch
from torch.nn import functional as F

from tools.nanojev_zero_line_ladder import deck
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_absolute_magnitude_banks import (
    AbsoluteBank, atomic_json, atomic_pt, exact_mask, sample_absolute_band, score,
)
from tools.nanojev_zero_line_learned_router import NeuralRouter, mixture_loss, frozen_bank_outputs
from tools.nanojev_zero_line_cumulative_bank import (
    NEW_BAND, PRIOR_BAND, SOURCE_RADIUS, _whole_shell, evaluate, infer,
    load_prior, prepare, require_paths,
)

SCHEMA = 'nanojev.zero-line-cumulative-gate-repair.v1'


def load_candidate(directory: Path, source: dict, base, previous_bank_file: Path,
                   previous_router_file: Path, device, *, include_tokens: bool):
    """Refuse stale, wrong-branch or promoted checkpoints; never modify originals."""
    report_path = directory/'result.json'
    bank_path = directory/'bank_candidate.pt'
    gate_path = directory/'gate_candidate.pt'
    if not all(p.is_file() for p in (report_path,bank_path,gate_path)):
        raise RuntimeError('ZERO_REPAIR_MISSING_CUMULATIVE_CANDIDATE')
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('schema') != 'nanojev.zero-line-cumulative-learned-bank.v1':
        raise RuntimeError('ZERO_REPAIR_UNSUPPORTED_SOURCE_REPORT')
    bp=torch.load(bank_path,map_location=device,weights_only=True)
    gp=torch.load(gate_path,map_location=device,weights_only=True)
    expected=(source['checkpoint_sha256'],sha256(previous_bank_file),sha256(previous_router_file))
    if (bp.get('source_sha256'),gp.get('source_sha256'),gp.get('prior_bank_sha256'),
        gp.get('prior_router_sha256')) != (expected[0],*expected):
        # Compare independently below: duplicated source element above is intentional
        # to catch inconsistent bank/gate provenance.
        raise RuntimeError('ZERO_REPAIR_SOURCE_PROVENANCE_MISMATCH')
    if (bp.get('band')!=list(NEW_BAND) or gp.get('new_band')!=list(NEW_BAND)
        or gp.get('new_bank_sha256')!=sha256(bank_path)
        or gp.get('include_tokens')!=include_tokens):
        raise RuntimeError('ZERO_REPAIR_BANK_GATE_MISMATCH')
    if gp.get('feature_width')!=int(base.query.shape[0]):
        raise RuntimeError('ZERO_REPAIR_FEATURE_WIDTH_MISMATCH')
    bank=AbsoluteBank(int(base.query.shape[0]),bp['hidden']).to(device)
    bank.load_state_dict(bp['bank'],strict=True)
    bank.eval().requires_grad_(False)
    gate=NeuralRouter(gp['feature_width'],gp['token_width'],gp['hidden'],gp['include_tokens']).to(device)
    gate.load_state_dict(gp['gate'],strict=True)
    return bank,gate,bank_path,gate_path,report,gp


def hard_negative_indices(prior: dict) -> torch.Tensor:
    """Training-only examples where new bank is wrong but incumbent is correct."""
    y=prior['target']
    mask=exact_mask(prior['incumbent'],y) & ~exact_mask(prior['new_logits'],y)
    return mask.nonzero(as_tuple=True)[0]


def conservative_replay_loss(logits: torch.Tensor, data: dict, masks: torch.Tensor,
                             *, margin: float, weight: float) -> torch.Tensor:
    """Penalize harmful switches on known TRAIN replay cards, never at inference.

    `masks` selects old-region replay rows, not new-band training rows.
    This loss is intentionally more conservative than answer mixture likelihood.
    """
    old_correct=exact_mask(data['incumbent'],data['target'])
    avoid=masks & old_correct
    if not bool(avoid.any()):
        return logits.sum()*0.
    return weight*F.softplus(logits[avoid] + margin).mean()


def _sample(rng: random.Random, data: dict, count: int, subset=None):
    size=len(data['target']) if subset is None else len(subset)
    if not size:raise RuntimeError('ZERO_REPAIR_EMPTY_TRAIN_REPLAY')
    ids=torch.tensor([rng.randrange(size) for _ in range(count)],
                     device=data['target'].device,dtype=torch.long)
    return ids if subset is None else subset[ids]


def train_repair(gate, center, old_train, new_train, old_full, old_dev, new_dev, *,
                 steps, probe_every, batch_size, lr, seed, margin, replay_weight,
                 output, conservative_shift):
    """TRAIN hard-negative replay; select on exact full historical retention + DEV.

    No OOD evaluation occurs here. Whole-shell history is evaluation-only, never
    included in the optimizer's examples. Old TRAIN is replayed separately.
    """
    if min(steps,probe_every,batch_size) < 1 or batch_size < 4:
        raise ValueError('ZERO_REPAIR_INVALID_TRAINING_STEPS')
    hard=hard_negative_indices(old_train)
    if len(hard)==0:
        raise RuntimeError('ZERO_REPAIR_NO_TRAIN_HARD_NEGATIVES')
    # A single scalar learned-parameter bias shift; not input-based inference logic.
    with torch.no_grad():
        gate.net[-1].bias.sub_(conservative_shift)
    rng=random.Random(seed)
    opt=torch.optim.AdamW(gate.parameters(),lr=lr,weight_decay=0.)
    best=None; best_quality=None;best_step=None;history=[]
    def probe(step, loss):
        nonlocal best,best_quality,best_step
        center_result=evaluate(gate,center)
        # Both complete domains checked at EVERY promotion-eligible probe.
        old_result=evaluate(gate,old_full)
        prev_dev=evaluate(gate,old_dev)
        new_result=evaluate(gate,new_dev)
        center_h=center_result['newly_wrong_vs_incumbent']
        old_h=old_result['newly_wrong_vs_incumbent']
        is_safe=(center_h==0 and old_h==0 and
                 prev_dev['newly_wrong_vs_incumbent']==0)
        quality=(int(is_safe),-center_h-old_h,
                 -prev_dev['newly_wrong_vs_incumbent'],
                 new_result['learned_accuracy'],
                 new_result['new_bank_selection_rate'])
        row={'step':step,'loss':loss,'center':center_result,
             'old_full':old_result,'old_dev':prev_dev,'new_dev':new_result,
             'protected_on_exhaustive_checks':is_safe}
        history.append(row)
        if best_quality is None or quality>best_quality:
            best_quality=quality;best_step=step
            best={k:v.detach().cpu().clone() for k,v in gate.state_dict().items()}
        print(f'ZERO_REPAIR step={step}/{steps} loss={loss if loss is not None else "baseline"} '
              f'CENTER_HARM={center_h} OLD_FULL_HARM={old_h} '
              f'NEW_DEV={new_result["learned_accuracy"]:.5f} '
              f'NEW_ROUTE={new_result["new_bank_selection_rate"]:.5f} '
              f'EXHAUSTIVE_SAFE={int(is_safe)}',flush=True)
        atomic_json(output/'progress.json',{'schema':SCHEMA,'history':history,'best_step':best_step})
        atomic_pt(output/'latest.pt',{'schema':SCHEMA+'.training',
             'step':step,'gate':{k:v.detach().cpu() for k,v in gate.state_dict().items()},
             'optimizer':opt.state_dict(),'rng':rng.getstate()})
    probe(0,None)
    for step in range(1,steps+1):
        counts=[batch_size//4]*4
        counts[3]+=batch_size-sum(counts)
        sources=[center,old_train,old_train,new_train]
        indices=[_sample(rng,center,counts[0]),
                 _sample(rng,old_train,counts[1]),
                 _sample(rng,old_train,counts[2],hard),
                 _sample(rng,new_train,counts[3])]
        # Assemble a training-only batch. No correctness metadata enters router.forward.
        tensor=lambda key:torch.cat([s[key][ids] for s,ids in zip(sources,indices)])
        batch={key:tensor(key) for key in ('features','tokens','incumbent','new_logits','target')}
        old_mask=torch.arange(batch_size,device=batch['target'].device)<sum(counts[:3])
        gate.train()
        route_logits=gate(batch['features'],batch['tokens'])
        mix=mixture_loss(route_logits,batch['incumbent'],batch['new_logits'],batch['target'])
        loss=mix+conservative_replay_loss(route_logits,batch,old_mask,
                                          margin=margin,weight=replay_weight)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(gate.parameters(),1.)
        opt.step()
        if step%probe_every==0 or step==steps:
            probe(step,float(loss.detach()))
    gate.load_state_dict(best,strict=True)
    gate.eval().requires_grad_(False)
    return history,best_step,int(len(hard))


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_REPAIR_CUDA_UNAVAILABLE')
    if (min(args.steps,args.probe_every,args.batch_size,args.eval_batch_size,
            args.per_stratum,args.cpu_threads)<1 or args.batch_size<4 or
        not 0<args.lr<1 or not 0<=args.conservative_shift<=15 or
        not 0<=args.replay_weight<=100 or not 0<=args.margin<=20):
        raise ValueError('ZERO_REPAIR_INVALID_OPTIONS')
    roots=[Path(x).expanduser().resolve() for x in (
        args.ladder_output,args.prior_bank_output,args.prior_router_output,args.cumulative_output)]
    out=Path(args.output).expanduser().resolve()
    require_paths(out,roots)
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    base,source=load_certified_model(roots[0],args.device)
    base.eval().requires_grad_(False)
    old_bank,old_router,include_tokens,old_bank_file,old_router_file,_=load_prior(
        source,base,roots[1],roots[2],args.device)
    new_bank,gate,newbankfile,newgatefile,original_report,source_gate=load_candidate(
        roots[3],source,base,old_bank_file,old_router_file,args.device,
        include_tokens=include_tokens)
    originals=[source['checkpoint'],old_bank_file,old_router_file,newbankfile,newgatefile]
    hashes=[sha256(x) for x in originals]
    out.mkdir(parents=True,exist_ok=True)
    atomic_json(out/'config.json',{
        'schema':SCHEMA,'source_hashes':hashes,'new_band':list(NEW_BAND),
        'derived_from_candidate':str(newgatefile),'only_trainable':'new_gate',
        'route_inference':'two_hard_neural_gates_no_python_fallback',
        'supervision':'final_signed_arithmetic_and_old_region_training_replay',
        'parameters':{k:v for k,v in vars(args).items() if k not in
                     ('ladder_output','prior_bank_output','prior_router_output','cumulative_output','output')}})
    new_split=sample_absolute_band(*NEW_BAND,args.seed,args.per_stratum)
    prev_split=sample_absolute_band(*PRIOR_BAND,args.seed,args.per_stratum)
    all_data={}
    for name,rows in (
        ('center',deck(SOURCE_RADIUS)),
        ('old_train',prev_split['train']),('old_dev',prev_split['dev']),
        ('old_ood',prev_split['ood']),('old_full',_whole_shell(*PRIOR_BAND)),
        ('new_train',new_split['train']),('new_dev',new_split['dev']),
        ('new_ood',new_split['ood'])):
        all_data[name]=prepare(base,old_bank,old_router,rows,args.device,
                              args.eval_batch_size,include_tokens,new_bank)
    if not bool(exact_mask(all_data['center']['incumbent'],all_data['center']['target']).all()):
        raise RuntimeError('ZERO_REPAIR_BASE_CERTIFICATION_BROKEN')
    hist,chosen_step,hard_count=train_repair(gate,
        all_data['center'],all_data['old_train'],all_data['new_train'],
        all_data['old_full'],all_data['old_dev'],all_data['new_dev'],
        steps=args.steps,probe_every=args.probe_every,batch_size=args.batch_size,
        lr=args.lr,seed=args.seed,margin=args.margin,replay_weight=args.replay_weight,
        output=out,conservative_shift=args.conservative_shift)
    metrics={key:evaluate(gate,all_data[key]) for key in
             ('center','old_full','old_dev','old_ood','new_dev','new_ood')}
    # Exhaustive retention never certified from a sparse DEV subset.
    keep=all(metrics[key]['newly_wrong_vs_incumbent']==0 for key in
             ('center','old_full','old_dev','old_ood'))
    new_ok=all(metrics[key]['learned_accuracy']>=args.min_accuracy and
               metrics[key]['new_bank_selection_rate']>=args.min_new_bank_rate and
               metrics[key]['near_zero_new_errors']==0 and
               metrics[key]['learned_accuracy']+args.allowed_gap>=
               metrics[key]['input_magnitude_reference_accuracy']
               for key in ('new_dev','new_ood'))
    specialist_ood=score(all_data['new_ood']['new_logits'],all_data['new_ood'])['accuracy']
    eligible=keep and new_ok and specialist_ood>=args.min_accuracy
    if [sha256(x) for x in originals]!=hashes:
        raise RuntimeError('ZERO_REPAIR_SOURCE_MUTATED')
    bp=torch.load(newbankfile,map_location='cpu',weights_only=True)
    gp={**source_gate,'schema':SCHEMA+'.gate-candidate',
        'gate':{k:v.detach().cpu().clone() for k,v in gate.state_dict().items()},
        'repair_source_sha256':hashes[-1], 'repair_chosen_step':chosen_step,
        'new_bank_sha256':None}
    atomic_pt(out/'bank_candidate.pt',bp)
    gp['new_bank_sha256']=sha256(out/'bank_candidate.pt')
    atomic_pt(out/'gate_candidate.pt',gp)
    if eligible:
        atomic_pt(out/'bank_promoted.pt',{**bp,'schema':SCHEMA+'.bank-promoted'})
        atomic_pt(out/'gate_promoted.pt',{**gp,'schema':SCHEMA+'.gate-promoted',
                  'new_bank_sha256':sha256(out/'bank_promoted.pt')})
    report={'schema':SCHEMA,'eligible':eligible,'historical_retention':keep,
            'new_band_eligible':new_ok,'specialist_ood':specialist_ood,
            'hard_training_replay_count':hard_count,'selected_step':chosen_step,
            'source_artifacts_immutable':True,'no_oracle_inference':True,
            'no_python_fallback':True,'evaluations':metrics,'history':hist,
            'checkpoint':'promoted' if eligible else 'candidate'}
    atomic_json(out/'result.json',report)
    print('ZERO_REPAIR_RESULT='+json.dumps({
        'eligible':eligible,'center_harm':metrics['center']['newly_wrong_vs_incumbent'],
        'old_full_harm':metrics['old_full']['newly_wrong_vs_incumbent'],
        'old_ood_harm':metrics['old_ood']['newly_wrong_vs_incumbent'],
        'new_dev':metrics['new_dev']['learned_accuracy'],
        'new_ood':metrics['new_ood']['learned_accuracy'],
        'new_ood_selection':metrics['new_ood']['new_bank_selection_rate'],
        'selected_step':chosen_step,'report':str(out/'result.json')},sort_keys=True),flush=True)
    print('ZERO_REPAIR_ALL_SOURCES_IMMUTABLE=TRUE NEW_BANK_FROZEN=TRUE OLD_SYSTEM_FROZEN=TRUE NO_INFERENCE_ORACLE=TRUE',flush=True)
    return report


def predict(args):
    # Delegate to the proven two-learned-gate live inference implementation,
    # while loading gate and bank exclusively from this repair's output.
    out=Path(args.output).expanduser().resolve()
    tag='candidate' if args.allow_candidate else 'promoted'
    bp_path=out/f'bank_{tag}.pt';gp_path=out/f'gate_{tag}.pt'
    if not bp_path.exists() or not gp_path.exists():
        raise RuntimeError('ZERO_REPAIR_CHECKPOINT_NOT_AVAILABLE')
    bp=torch.load(bp_path,map_location=args.device,weights_only=True)
    gp=torch.load(gp_path,map_location=args.device,weights_only=True)
    if gp.get('new_bank_sha256')!=sha256(bp_path):
        raise RuntimeError('ZERO_REPAIR_CHECKPOINT_MISMATCH')
    base,src=load_certified_model(Path(args.ladder_output).expanduser().resolve(),args.device)
    oldbank,oldrouter,include_tokens,oldbankfile,oldrouterfile,_=load_prior(
        src,base,Path(args.prior_bank_output).expanduser().resolve(),
        Path(args.prior_router_output).expanduser().resolve(),args.device)
    if (gp.get('source_sha256')!=src['checkpoint_sha256'] or
        gp.get('prior_bank_sha256')!=sha256(oldbankfile) or
        gp.get('prior_router_sha256')!=sha256(oldrouterfile)):
        raise RuntimeError('ZERO_REPAIR_PREDICT_PROVENANCE_MISMATCH')
    bank=AbsoluteBank(int(base.query.shape[0]),bp['hidden']).to(args.device)
    bank.load_state_dict(bp['bank'],strict=True);bank.eval().requires_grad_(False)
    gate=NeuralRouter(gp['feature_width'],gp['token_width'],gp['hidden'],gp['include_tokens']).to(args.device)
    gate.load_state_dict(gp['gate'],strict=True);gate.eval().requires_grad_(False)
    result=infer(base,oldbank,oldrouter,bank,gate,args.a,args.b,args.device,include_tokens)
    print('ZERO_REPAIR_PREDICT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('train','predict'),default='train')
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--prior-bank-output',required=True)
    p.add_argument('--prior-router-output',required=True)
    p.add_argument('--cumulative-output',default=None,
                   help='Original non-promoted 160:191 cumulative candidate run (training phase)')
    p.add_argument('--output',required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--steps',type=int,default=1200)
    p.add_argument('--probe-every',type=int,default=100)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--per-stratum',type=int,default=256)
    p.add_argument('--lr',type=float,default=.00015)
    p.add_argument('--conservative-shift',type=float,default=1.5)
    p.add_argument('--margin',type=float,default=1.5)
    p.add_argument('--replay-weight',type=float,default=1.0)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--min-accuracy',type=float,default=.90)
    p.add_argument('--min-new-bank-rate',type=float,default=.90)
    p.add_argument('--allowed-gap',type=float,default=.02)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--a',type=int,default=175)
    p.add_argument('--b',type=int,default=-159)
    p.add_argument('--allow-candidate',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.phase=='train' and not args.cumulative_output:
        raise ValueError('ZERO_REPAIR_CUMULATIVE_OUTPUT_REQUIRED')
    return predict(args) if args.phase=='predict' else run(args)

if __name__=='__main__':
    main()
