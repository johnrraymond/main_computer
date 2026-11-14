#!/usr/bin/env python3
"""Persistent, checkpoint-preserving reverse curriculum for the non-hybrid Hard EMP.

Opt in through tools.nanojev_emp_hard_join --reverse --resume. This scheduler
never mutates historical outward stage checkpoints or acceptance certificates.
It starts at the highest outward latest.pt, carries its live weights AND optimizer
state through successively smaller shells, and finishes at the exact zero-line
with small nonzero controls. Hard EMP continues at one-probe lag throughout.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import torch
from torch import nn

from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_emp_hard_join as hard
from tools import nanojev_zero_line_outward_return as outward
from tools import nanojev_zero_line_sampled_radius_ladder as old
from tools.nanojev_emp_population import example_emphases
from tools.nanojev_zero_line_zero_only import BANDS, input_tensor
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256

SCHEMA = 'nanojev.emp-hard-reverse.v1'


def reverse_radii(frontier):
    """Reverse the authentic outward radius ladder, ending at the zero-line."""
    if not 512 <= frontier <= outward.OUTER_TARGET:
        raise RuntimeError('HARD_REVERSE_UNSUPPORTED_FRONTIER')
    stages = outward.outward_plan(frontier)
    if stages[-1] != frontier:
        raise RuntimeError('HARD_REVERSE_UNSUPPORTED_FRONTIER')
    return [r for r in reversed(stages[:-1]) if r >= 512] + [0]


def active_frontier(root):
    """Use LIVE latest, not the older accepted weight snapshot."""
    candidates = []
    for folder in (root / 'stages').glob('outward_*'):
        try:
            radius = int(folder.name.removeprefix('outward_'))
        except ValueError:
            raise RuntimeError('HARD_REVERSE_BAD_OUTWARD_STAGE') from None
        cp = folder / 'latest.pt'
        if cp.is_file():
            candidates.append((radius, cp))
    if not candidates:
        raise RuntimeError('HARD_REVERSE_NO_FORWARD_CHECKPOINT')
    radius, cp = max(candidates)
    saved = torch.load(cp, map_location='cpu', weights_only=True)
    if saved.get('schema') != ladder.SCHEMA+'.checkpoint' or saved.get('radius') != radius:
        raise RuntimeError('HARD_REVERSE_BAD_FORWARD_CHECKPOINT')
    if 'banks' not in saved or 'optimizers' not in saved:
        raise RuntimeError('HARD_REVERSE_MISSING_TRAINING_STATE')
    return radius, cp


def reverse_accuracy_cards(radius, frontier, seed, serial, count):
    """An independent TRAIN-family accuracy sample, using return curriculum."""
    if frontier is None or frontier < 512:
        raise RuntimeError('HARD_REVERSE_MISSING_FRONTIER')
    rng = random.Random(f'hard-reverse-accuracy:{seed}:{radius}:{serial}')
    cursor, seen, rows = 0, set(), []
    for _ in range(max(16, count // 16)):
        cards, cursor = outward.generate_batch(rng, radius, seed,
                    max(16, min(256, count - len(rows) + 32)), [1.]*len(BANDS),
                    cursor, 'return', radius, frontier)
        for card in cards:
            row = tuple(int(v) for v in card)
            if (row[0], row[1]) not in seen:
                if row[0] + row[1] != row[2]:
                    raise RuntimeError('HARD_REVERSE_INVALID_CARD')
                rows.append(row)
                seen.add((row[0], row[1]))
                if len(rows) == count:
                    return rows
    raise RuntimeError('HARD_REVERSE_INSUFFICIENT_ACCURACY_CARDS')


def exact_zero_probe(banks, args):
    """DEV exact-zero and small signed controls; forbid all-zero collapse."""
    views = old.evaluation_cards(64, 'dev', args.seed, 0, max(1,args.breadth_anchors),
                                 2, 1)
    scores = {name: {'frontier':[], 'outer':[]} for name in dual.BANKS}
    for view in views:
        zeros = [row for row in view if row[2] == 0]
        controls = [row for row in view if 0 < abs(row[2]) <= 5]
        if not zeros or not controls:
            raise RuntimeError('HARD_REVERSE_ZERO_PROBE_EMPTY')
        for name,bank in banks.items():
            bank.eval()
            preds = []
            with torch.inference_mode():
                for rows in (zeros, controls):
                    computed = []
                    for offset in range(0,len(rows),args.eval_batch_size):
                        x=input_tensor(rows[offset:offset+args.eval_batch_size], args.device)
                        computed += dual.decoded(bank(x))
                    preds.append(computed)
            zero_accuracy=sum(pred==0 for pred in preds[0])/len(zeros)
            near_accuracy=sum(hard.is_correct(card[2],pred) for card,pred in zip(controls,preds[1]))/len(controls)
            scores[name]['frontier'].append({'nonzero':len(zeros),'within_5pct':zero_accuracy,
                        'zero_exact':zero_accuracy,'cards':len(zeros)})
            scores[name]['outer'].append({'nonzero':len(controls),'within_5pct':near_accuracy,
                        'near_zero_accuracy':near_accuracy,'cards':len(controls)})
    return scores


def probe(banks, args, radius):
    if radius == 0:
        return exact_zero_probe(banks,args)
    return ladder.stage_probe(banks,args,radius)[0]


def read_plan(root, args):
    folder = root / 'hard_emp_reverse'
    marker = folder / 'plan.json'
    if marker.is_file():
        plan = json.loads(marker.read_text('utf-8'))
        if (plan.get('schema') != SCHEMA or plan.get('campaign_config_sha256') != sha256(root/'config.json')
                or plan.get('hard_state_dir') != args.hard_state_dir
                or plan.get('radii') != reverse_radii(plan['frontier_radius'])
                or sha256(Path(plan['source_checkpoint'])) != plan['source_sha256']):
            raise RuntimeError('HARD_REVERSE_PLAN_DRIFT')
        return folder, plan
    radius, source_cp = active_frontier(root)
    candidate = {'schema':SCHEMA, 'frontier_radius':radius,
                 'source_checkpoint':str(source_cp.resolve()),
                 'source_sha256':sha256(source_cp),
                 'campaign_config_sha256':sha256(root/'config.json'),
                 'hard_state_dir':args.hard_state_dir,
                 'radii':reverse_radii(radius),
                 'note':'Persistent reverse direction. Forward stages left immutable; latest weights and optimizers carried downward.'}
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(marker,candidate)
    print('EMP_HARD_REVERSE_STARTED='+json.dumps({'from_radius':radius,'radii':candidate['radii'],
        'source_step':torch.load(source_cp,map_location='cpu',weights_only=True)['step']},sort_keys=True),flush=True)
    return folder,candidate


def initialize_banks(args, root, state):
    # The campaign snapshots of the original Left and Right sources are the
    # canonical model structures; never read mutable live source-run weights.
    _, _, _, left, right = ladder.source_config(args)
    model, _ = load_certified_model(args.ladder_output,'cpu')
    banks=ladder.from_source(model,left,right,state['banks'],args.device)
    controls=dual.checked_controls(copy.deepcopy(state['controls']))
    optimizers=dual.make_bank_optimizers(banks,controls)
    for name,opt in optimizers.items():
        opt.load_state_dict(state['optimizers'][name])
    return banks, optimizers, controls


def attach_hard_probe(root, args, banks, radius, step, source_cp, frontier):
    # The specialist is fully independent: don't perturb ordinary torch RNG.
    state=torch.get_rng_state()
    cuda=torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
    try:
        hard.run_probe(root,banks,args,radius,step,phase='reverse',
                       source_checkpoint=source_cp,training_radius=frontier)
    finally:
        torch.set_rng_state(state)
        if cuda is not None: torch.cuda.set_rng_state_all(cuda)


def stage_loop(root, folder, plan, args, radius, parent, parent_sha):
    stage=folder/'stages'/('zero_line' if radius == 0 else f'return_{radius}')
    cp=stage/'latest.pt'
    config={'schema':SCHEMA+'.stage','radius':radius,'parent_sha256':parent_sha,
            'reverse_plan_sha256':sha256(folder/'plan.json')}
    if (stage/'config.json').is_file():
        if json.loads((stage/'config.json').read_text('utf-8')) != config:
            raise RuntimeError('HARD_REVERSE_STAGE_PARENT_DRIFT')
    else:
        if stage.exists() and any(stage.iterdir()):
            raise RuntimeError('HARD_REVERSE_UNTRACKED_STAGE')
        stage.mkdir(parents=True, exist_ok=True)
        atomic_json(stage/'config.json',config)
    banks,opt,controls=initialize_banks(args,root,parent)
    rng=random.Random(args.seed+radius*13+18017)
    step=cursor=streak=0
    weights=[1.]*len(BANDS)
    events=list(parent.get('control_log',[]))
    history=[]
    if cp.is_file():
        state=torch.load(cp,map_location='cpu',weights_only=True)
        if state.get('schema')!=SCHEMA+'.checkpoint' or state.get('radius')!=radius:
            raise RuntimeError('HARD_REVERSE_CHECKPOINT_DRIFT')
        banks.load_state_dict(state['banks'],strict=True)
        for name, optimizer in opt.items():optimizer.load_state_dict(state['optimizers'][name])
        controls=dual.checked_controls(state['controls'])
        step,cursor,weights=state['step'],state['cursor'],state['weights']
        streak=state['streak'] if state.get('gate_revision')==hard.gate_revision(root) else 0
        rng.setstate(state['rng'])
        torch.set_rng_state(state['torch_rng'])
        if torch.cuda.is_available() and state['cuda_rng'] is not None:
            torch.cuda.set_rng_state_all(state['cuda_rng'])
        events=state['control_log']
        if (stage/'history.json').is_file():history=json.loads((stage/'history.json').read_text('utf-8'))
        # A crash after atomic main checkpoint but before specialist intake is
        # recovered without repeating a four-head optimizer update.
        attach_hard_probe(root,args,banks,radius,step,cp,plan['frontier_radius'])
    else:
        if parent.get('torch_rng') is not None:
            torch.set_rng_state(parent['torch_rng'])
            if torch.cuda.is_available() and parent.get('cuda_rng') is not None:
                torch.cuda.set_rng_state_all(parent['cuda_rng'])
        scores=probe(banks,args,radius)
        selection=ladder.select_pair(scores,args.left_gate,args.right_gate)
        atomic_json(stage/'baseline.json',{'radius':radius,'step':0,
              'selected_pair':selection,'scores':scores,'gate_revision':hard.gate_revision(root)})
    for i in range(step+1,args.stage_steps+1):
        if i==step+1 or (i-1)%args.probe_every==0:
            current=dual.checked_controls(json.loads((root/'controls.json').read_text('utf-8')))
            if current['revision'] < controls['revision'] or (current['revision']==controls['revision'] and current!=controls):
                raise RuntimeError('HARD_REVERSE_CONTROLS_DRIFT')
            if current!=controls:
                events.append({'effective_return_radius':radius,'effective_step':i,'controls':copy.deepcopy(current)})
            controls=current;dual.update_rates(opt,controls)
        rows,cursor=outward.generate_batch(rng,radius,args.seed,args.batch_size,weights,cursor,
                                           'return',radius,plan['frontier_radius'])
        x=input_tensor(rows,args.device)
        y=torch.tensor([r[2] for r in rows],device=args.device,dtype=torch.long)
        outputs={name:bank(x) for name,bank in banks.items()}
        emphasis={emp:dual.right_answer_emphasis(rows,
                    dual.decoded(outputs[f'{emp}.left']),dual.decoded(outputs[f'{emp}.right']),
                    controls['banks'][f'{emp}.left']['right_answer_boost'],args.device)
                    for emp in dual.EMPS}
        for name in dual.BANKS:
            settings=controls['banks'][name]
            if i % settings['train_every']:continue
            weight=example_emphases(rows,max(64,radius),settings,args.device)
            if name.endswith('.left'):weight=weight*emphasis[name.split('.')[0]]
            loss=(dual.loss_per_card(outputs[name],y)*weight).sum()/weight.sum()
            if not torch.isfinite(loss):raise FloatingPointError('HARD_REVERSE_NONFINITE_LOSS')
            optimizer=opt[name];optimizer.zero_grad(set_to_none=True)
            loss.backward();nn.utils.clip_grad_norm_(banks[name].parameters(),1.,error_if_nonfinite=True)
            optimizer.step()
        if i%args.probe_every and i!=args.stage_steps:continue
        scores=probe(banks,args,radius)
        selection=ladder.select_pair(scores,args.left_gate,args.right_gate)
        streak=streak+1 if selection['qualified'] else 0
        record={'radius':radius,'step':i,'selected_pair':selection,
                'qualified':selection['qualified'],'streak':streak,
                'gate_revision':hard.gate_revision(root),
                'left_gate':args.left_gate,'right_gate':args.right_gate,
                'scores':scores}
        history.append(record)
        atomic_json(stage/'history.json',history)
        state={'schema':SCHEMA+'.checkpoint','radius':radius,'step':i,
                'banks':banks.state_dict(),'optimizers':{n:o.state_dict() for n,o in opt.items()},
                'controls':copy.deepcopy(controls),'control_log':events,
                'rng':rng.getstate(),'torch_rng':torch.get_rng_state(),
                'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
                'cursor':cursor,'weights':weights,'streak':streak,
                'gate_revision':hard.gate_revision(root), 'selected_pair':selection}
        atomic_checkpoint(cp,state)
        attach_hard_probe(root,args,banks,radius,i,cp,plan['frontier_radius'])
        print('EMP_HARD_REVERSE_PROBE='+json.dumps({'radius':radius,'step':i,'streak':streak,
          'qualified':selection['qualified'],'selected_pair':selection,
          'gate_revision':hard.gate_revision(root)},sort_keys=True),flush=True)
        if streak >= args.gate_streak:
            accepted=stage/'accepted.pt'
            if not accepted.is_file():
                atomic_checkpoint(accepted,state)
            acceptance={'schema':SCHEMA+'.acceptance','radius':radius,'step':i,
                'checkpoint_sha256':sha256(accepted),'stage_config_sha256':sha256(stage/'config.json'),
                'selected_pair':selection,'gate_revision':hard.gate_revision(root)}
            atomic_json(stage/'acceptance.json',acceptance)
            atomic_json(stage/'result.json',{'status':'reverse_stage_accepted',**acceptance})
            print('EMP_HARD_REVERSE_ACCEPTED='+json.dumps({'radius':radius,'step':i},sort_keys=True),flush=True)
            return True
    atomic_json(stage/'result.json',{'status':'reverse_budget_exhausted','radius':radius,
                                     'step':args.stage_steps,'resume_available':True})
    return False


def run_reverse(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('HARD_REVERSE_CUDA_UNAVAILABLE')
    if args.stage_steps<1 or args.max_stages<1 or args.probe_every<1 or args.gate_streak<1:
        raise ValueError('HARD_REVERSE_INVALID_ARGS')
    torch.set_num_threads(args.cpu_threads)
    root=Path(args.output).expanduser().resolve()
    # SHA/gate migration validation before committing a one-way reverse plan.
    ladder.source_config(args)
    folder,plan=read_plan(root,args)
    parent_cp=Path(plan['source_checkpoint'])
    parent_sha=plan['source_sha256']
    parent=torch.load(parent_cp,map_location='cpu',weights_only=True)
    completed=[];attempts=0
    for radius in plan['radii']:
        stage=folder/'stages'/('zero_line' if radius==0 else f'return_{radius}')
        accepted=stage/'accepted.pt'
        record_path=stage/'acceptance.json'
        if accepted.is_file() or record_path.is_file():
            if not accepted.is_file() or not record_path.is_file():
                raise RuntimeError('HARD_REVERSE_PARTIAL_ACCEPTANCE')
            record=json.loads(record_path.read_text('utf-8'))
            cfg=json.loads((stage/'config.json').read_text('utf-8'))
            if (record.get('radius')!=radius or record.get('checkpoint_sha256')!=sha256(accepted)
                    or record.get('stage_config_sha256')!=sha256(stage/'config.json')
                    or cfg.get('parent_sha256')!=parent_sha):
                raise RuntimeError('HARD_REVERSE_ACCEPTANCE_DRIFT')
            parent=torch.load(accepted,map_location='cpu',weights_only=True)
            parent_sha=sha256(accepted)
            completed.append(radius)
            continue
        if attempts>=args.max_stages:
            break
        if not stage_loop(root,folder,plan,args,radius,parent,parent_sha):
            result={'status':'reverse_budget_exhausted','pending_radius':radius,'completed_radii':completed}
            print('EMP_HARD_REVERSE_SUMMARY='+json.dumps(result,sort_keys=True),flush=True)
            return result
        accepted=stage/'accepted.pt'
        parent=torch.load(accepted,map_location='cpu',weights_only=True)
        parent_sha=sha256(accepted)
        completed.append(radius)
        attempts+=1
    finished=completed==plan['radii']
    result={'status':'zero_line_complete' if finished else 'reverse_stage_limit_reached',
            'completed_radii':completed,'next_radius':None if finished else plan['radii'][len(completed)],
            'forward_abandoned':True}
    atomic_json(folder/'result.json',result)
    print('EMP_HARD_REVERSE_SUMMARY='+json.dumps(result,sort_keys=True),flush=True)
    return result
