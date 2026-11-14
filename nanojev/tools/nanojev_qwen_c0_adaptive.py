#!/usr/bin/env python3
"""Opt-in adaptive C0 numerical frontier.

A magnitude/radius cell is acquired only on a disjoint development deck.
A failed candidate is never promoted over already acquired competencies.
The held-out audit is evaluated only after checkpoint choice; it cannot
influence training, weighting, or the acquisition decision.
"""
from __future__ import annotations
import copy
import csv
import json
import random
from pathlib import Path
import numpy as np

from tools.nanojev_qwen_c0_balance import error_stats, save_balance_report
from tools.nanojev_qwen_c0_compressive_train import (
    VARIANTS, capture_features, eval_heads, make_network, neural_loss, signed_log,
)
from tools.nanojev_qwen_c0_zero_line import (
    PROGRAMS, FORMS, DELTA_CLASSES, parse_focus_weights, focused_prompt,
    make_focus_rows, focus_sampling_weights, gate_summary,
)


def program_gate_rows(source, programs, forms, forbidden=()):
    """Create independent per-program/form probes from development originals only.

    Never generate probe prompts from training examples. Identical prompt text is
    deduplicated, and the inverse check rejects leakage against training prompts.
    """
    forbidden = set(forbidden)
    result, seen = [], set()
    for row in source:
        for program in PROGRAMS:
            if not programs[program]: continue
            for form in FORMS:
                if not forms[form]: continue
                item = focused_prompt(row, program, form)
                if item is None: continue
                prompt = item['prompt']
                if prompt in forbidden:
                    # A transformed program can coincide with a distinct train
                    # source's arithmetic string. Exclude, never evaluate leakage.
                    continue
                if prompt in seen: continue
                seen.add(prompt)
                result.append(item)
    return result


def probe_cells(rows, predictions):
    """Numerical errors grouped by program/form/target/magnitude, signed separately."""
    groups = {}
    for i,row in enumerate(rows):
        key = (row['focus_program'],row['focus_form'],int(row['answer']),int(row['focus_magnitude']))
        groups.setdefault(key,[]).append(i)
    actual = np.asarray([r['answer'] for r in rows],np.float64)
    pred = np.asarray(predictions,np.float64)
    result=[]
    for (program,form,answer,magnitude),ids in sorted(groups.items()):
        metrics=error_stats(actual[ids],pred[ids])
        result.append({'program':program,'form':form,'answer':answer,
                       'delta':abs(answer),'magnitude':magnitude,
                       'exact':metrics['rounded_exact_pct']/100., **metrics})
    return result


def failure_weights(rows, base, failed, boost):
    """Relative, capped boost: per-(program,form,answer,magnitude) observed misses."""
    difficulty = {(c['program'],c['form'],c['answer'],c['magnitude']):1-c['exact'] for c in failed}
    factors=np.asarray([1+(boost-1)*difficulty.get(
        (r['focus_program'],r['focus_form'],int(r['answer']),int(r['focus_magnitude'])),0.)
        for r in rows],np.float64)
    out=np.asarray(base,dtype=np.float64)*factors
    return out/out.sum()


def sample_batch(rng, x, y, base, repair, repair_fraction, replay_x, replay_y,
                 retention_fraction, ordinary_x, ordinary_y, ordinary_fraction, batch_size):
    """Mutually exclusive proportions; percentages cannot double-count ordinary C0."""
    ordinary_n=int(rng.binomial(batch_size,ordinary_fraction))
    count=batch_size-ordinary_n
    retention_n=min(count,int(rng.binomial(count,retention_fraction))) if len(replay_y) else 0
    focus_n=count-retention_n
    repair_n=int(rng.binomial(focus_n,repair_fraction))
    base_n=focus_n-repair_n
    chunks=[]; labels=[]
    for n, features, targets, prob in (
        (base_n,x,y,base),(repair_n,x,y,repair),
        (retention_n,replay_x,replay_y,None),
        (ordinary_n,ordinary_x,ordinary_y,None),
    ):
        if n:
            ids=rng.choice(len(targets),size=n,replace=True,p=prob)
            chunks.append(features[ids]);labels.append(targets[ids])
    return np.concatenate(chunks),np.concatenate(labels),{
        'base':base_n,'repair':repair_n,'retention':retention_n,'ordinary':ordinary_n}


def candidate_eligible(gates, ordinary_mae, ordinary_limit, accepted):
    """Never replace mastered cells with a checkpoint that breaks their gates."""
    return bool(ordinary_mae<=ordinary_limit and all(gates[k]['pass'] for k in accepted))


def capture_with_progress(torch, backbone, tok, rows, args, label):
    """Incremental capture with visible heartbeat and no future-frontier prefetch."""
    if not rows:return np.empty((0,int(backbone.config.hidden_size)),dtype='float32')
    count=len(rows);stride=max(1,args.capture_batch*20)
    chunks=[]
    for start in range(0,count,stride):
        stop=min(count,start+stride)
        chunks.append(capture_features(torch,backbone,tok,rows[start:stop],args))
        print(f'CAPTURE {label}: {stop}/{count}',flush=True)
    return np.concatenate(chunks)


def run_adaptive(args,torch,backbone,tok,shells,radii,magnitudes,variants,
                 ordinary_train,ordinary_val,out):
    from tools.nanojev_qwen_c0_zero_line import cell_metrics
    programs=parse_focus_weights(args.zero_line_program_weights,PROGRAMS)
    forms=parse_focus_weights(args.zero_line_form_weights,FORMS)
    deltas=parse_focus_weights(args.zero_line_delta_weights,DELTA_CLASSES)
    magweights=parse_focus_weights(args.zero_line_magnitude_weights,(str(m) for m in magnitudes))
    ordinary_train=ordinary_train[:args.zero_line_ordinary_pool]
    ordinary_val=ordinary_val[:args.outer_points]
    ordinary_x=capture_with_progress(torch,backbone,tok,ordinary_train,args,'ordinary.train')
    ordinary_y=np.asarray([r['answer'] for r in ordinary_train],np.float64)
    ordinary_val_x=capture_with_progress(torch,backbone,tok,ordinary_val,args,'ordinary.validation')
    ordinary_val_y=np.asarray([r['answer'] for r in ordinary_val],np.float64)
    input_width=int(backbone.config.hidden_size)
    # Cache only visited cells. Keys also include prompt form for probe rows.
    cache={}
    def features(key, rows):
        if key not in cache:
            cache[key]=capture_with_progress(torch,backbone,tok,rows,args,str(key))
        return cache[key]

    result={'schema':'nanojev.c0.zero-line.adaptive.v1','mode':'adaptive_zero_line',
            'magnitudes':magnitudes,'radii':radii,'model':args.model,
            'neural_supervision':'exact_answers_only','deterministic_role':'independent',
            'mixture':{'program':programs,'form':forms,'delta':deltas,'magnitude':magweights},
            'variants':{n:[] for n in variants}}
    # All training rows originate only from train shells. Development, training and
    # audit remain disjoint even after transforming natural prompts to program forms.
    for name in variants:
        torch.manual_seed(args.seed+list(VARIANTS).index(name)*100003)
        net=make_network(torch,input_width,VARIANTS[name]).to(args.device)
        init_path=None
        if args.zero_line_init_dir:
            init_path=Path(args.zero_line_init_dir)/name/'encoder.pt'
            cp=torch.load(init_path,map_location='cpu',weights_only=True)
            if tuple(cp['hidden'])!=VARIANTS[name] or int(cp['input_width'])!=input_width:
                raise ValueError(f'incompatible checkpoint architecture: {init_path}')
            net.load_state_dict(cp['model'],strict=True)
        opt=torch.optim.AdamW(net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
        base_pred,_=eval_heads(torch,net,ordinary_val_x,ordinary_val_y,0.,1.,args.device,args.eval_batch)
        ordinary_limit=error_stats(ordinary_val_y,base_pred)['mae']/.95
        accepted={}   # (radius,magnitude) -> development rows and feature matrix
        blocked=set() # magnitudes that failed their present radius
        print(f'ADAPTIVE variant={name} ordinary_MAE={ordinary_limit*.95:.5g} ceiling={ordinary_limit:.5g}',flush=True)
        for radius in radii:
            available=[m for m in magnitudes if m not in blocked]
            if not available:break
            for magnitude in available:
                # No future radius is captured unless this cell is reached.
                source=shells[radius]
                current_dev=[r for r in source['gate'] if int(r['magnitude_target'])==magnitude]
                current_audit=[r for r in source['audit'] if int(r['magnitude_target'])==magnitude]
                if not current_dev:
                    blocked.add(magnitude);continue
                focus_rows=make_focus_rows(shells,radii,radius,args,programs,forms,deltas,magweights)
                focus_rows=[r for r in focus_rows if int(r['focus_magnitude'])==magnitude]
                if {r['prompt'] for r in focus_rows} & {r['prompt'] for shell in shells.values() for kind in ('gate','audit') for r in shell[kind]}:
                    raise AssertionError('training mixture overlaps held-out natural-language prompts')
                if not focus_rows:
                    raise ValueError(f'no feasible focus examples at radius={radius} magnitude={magnitude}')
                base=focus_sampling_weights(focus_rows,programs,forms,deltas,magweights)
                focus_x=features(('focus',radius,magnitude),focus_rows)
                focus_y=np.asarray([r['answer'] for r in focus_rows],np.float64)
                # Transform only disjoint development source prompts. Probes are
                # used for feedback/weighting, never as training examples.
                probe_rows=program_gate_rows(current_dev,programs,forms,
                                              forbidden=(r['prompt'] for r in focus_rows))
                probe_x=features(('probe',radius,magnitude),probe_rows)
                gate_x=features(('gate',radius,magnitude),current_dev)
                gate_y=np.asarray([r['answer'] for r in current_dev],np.float64)
                prior_keys=list(accepted)
                replay_x=np.concatenate([accepted[k]['replay_x'] for k in prior_keys]) if prior_keys else np.empty((0,input_width),np.float32)
                replay_y=np.concatenate([accepted[k]['replay_y'] for k in prior_keys]) if prior_keys else np.empty((0,),np.float64)
                rng=np.random.default_rng(args.seed+radius*1013+magnitude*37+list(VARIANTS).index(name)*100003)
                baseline_model=copy.deepcopy(net.state_dict());baseline_opt=copy.deepcopy(opt.state_dict())
                best=None;best_state=None;best_opt=None;best_report=None
                failures=[]; sampling_counts={'base':0,'repair':0,'retention':0,'ordinary':0}
                history=[];success=False
                for round_id in range(1+args.zero_line_max_repairs):
                    for group in opt.param_groups:
                        group['lr']=args.lr*(args.zero_line_repair_lr_factor if round_id else 1.)
                    for local_step in range(1,args.zero_line_steps+1):
                        step=round_id*args.zero_line_steps+local_step
                        adaptive=failure_weights(focus_rows,base,failures,args.zero_line_repair_boost) if failures else base
                        batch_x,batch_y,counts=sample_batch(
                            rng,focus_x,focus_y,base,adaptive,
                            args.zero_line_repair_fraction if failures else 0.,
                            replay_x,replay_y,args.zero_line_retention_fraction,
                            ordinary_x,ordinary_y,args.zero_line_ordinary_fraction,args.batch_size)
                        for k,n in counts.items():sampling_counts[k]+=n
                        x=torch.as_tensor(batch_x.copy(),dtype=torch.float32,device=args.device)
                        y=torch.as_tensor(signed_log(batch_y).astype('float32'),device=args.device)
                        net.train();corrected,uncorrected=net(x)
                        loss=neural_loss(torch,corrected,uncorrected,y,beta=args.huber_beta,aux_weight=args.aux_weight)
                        opt.zero_grad(set_to_none=True);loss.backward()
                        torch.nn.utils.clip_grad_norm_(net.parameters(),args.grad_clip);opt.step()
                        if step%args.eval_every and local_step!=args.zero_line_steps:continue
                        pred,_=eval_heads(torch,net,gate_x,gate_y,0.,1.,args.device,args.eval_batch)
                        new_gate=gate_summary(current_dev,pred,args.zero_line_exact_threshold)
                        new_gate['mae']=error_stats(gate_y,pred)['mae']
                        gates={}
                        for key in prior_keys:
                            data=accepted[key]
                            yp,_=eval_heads(torch,net,data['gate_x'],data['gate_y'],0.,1.,args.device,args.eval_batch)
                            gates[key]=gate_summary(data['rows'],yp,args.zero_line_exact_threshold)
                        op,_=eval_heads(torch,net,ordinary_val_x,ordinary_val_y,0.,1.,args.device,args.eval_batch)
                        ordinary_mae=error_stats(ordinary_val_y,op)['mae']
                        eligible=candidate_eligible(gates,ordinary_mae,ordinary_limit,prior_keys)
                        probepred,_=eval_heads(torch,net,probe_x,np.asarray([r['answer'] for r in probe_rows]),0.,1.,args.device,args.eval_batch)
                        failures=probe_cells(probe_rows,probepred)
                        failures.sort(key=lambda c:(c['exact'], -c['mae']))
                        # A complete acquisition beats a partial acquisition;
                        # retention must hold regardless of global macro accuracy.
                        score=(int(new_gate['pass']),new_gate['worst_cell_exact_fraction'],
                               new_gate['macro_cell_exact_fraction'],-new_gate['mae'])
                        if eligible and (best is None or score>best):
                            best=score;best_state=copy.deepcopy(net.state_dict())
                            best_opt=copy.deepcopy(opt.state_dict())
                            best_report={'gate':copy.deepcopy(new_gate),'ordinary_mae':ordinary_mae,
                                         'retained_cells':len(prior_keys),'step':step,
                                         'worst_probes':copy.deepcopy(failures[:args.zero_line_report_worst])}
                        event={'step':step,'exact':new_gate['macro_cell_exact_fraction'],
                               'worst':new_gate['worst_cell_exact_fraction'],
                               'eligible':eligible,'accepted':bool(eligible and new_gate['pass']),
                               'ordinary_mae':ordinary_mae}
                        event['worst_probes']=copy.deepcopy(failures[:args.zero_line_report_worst])
                        history.append(event)
                        worst=failures[0] if failures else None
                        print(f'ADAPTIVE {name} magnitude={magnitude} radius={radius} step={step} '
                              f'worst={event["worst"]:.3f} macro={event["exact"]:.3f} '
                              f'retention={eligible} failed={worst["program"]+"/"+worst["form"]+"/"+str(worst["answer"]) if worst else "none"}',flush=True)
                        if eligible and new_gate['pass']:
                            success=True;break
                    if success:break
                    # Begin repair from best eligible checkpoint, not latest,
                    # whenever an improvement was discovered.
                    if best_state is not None:
                        net.load_state_dict(best_state);opt.load_state_dict(best_opt)
                # A failed cell must not destroy accepted earlier competence.
                if best_state is not None:
                    net.load_state_dict(best_state);opt.load_state_dict(best_opt)
                else:
                    net.load_state_dict(baseline_model);opt.load_state_dict(baseline_opt)
                acquired=bool(best_report and best_report['gate']['pass'])
                if acquired:
                    accepted[(radius,magnitude)]={'rows':current_dev,'gate_x':gate_x,'gate_y':gate_y,
                        'replay_x':features(('train',radius,magnitude),
                           [r for r in source['train'] if int(r['magnitude_target'])==magnitude]),
                        'replay_y':np.asarray([r['answer'] for r in source['train'] if int(r['magnitude_target'])==magnitude],np.float64)}
                    ordinary_limit=min(ordinary_limit,best_report['ordinary_mae']/.95)
                else:
                    blocked.add(magnitude)
                    # If no competence was acquired anywhere, allow partial
                    # improvement to serve as a seed for the next magnitude.
                    # Once any cell is acquired, failed-cell candidates may be
                    # retained only if their earlier acquisition gates still pass.
                stage_dir=out/'zero_line'/name/f'radius_{radius}'/f'magnitude_{magnitude}'
                stage_dir.mkdir(parents=True,exist_ok=True)
                # Audit is captured only after model selection and never graded
                # to adjust the optimizer or checkpoint selection.
                # Selected development predictions are diagnostic; they do not
                # participate in training, and their exact prompts are never
                # copied into training examples. Audit remains untouched.
                def write_predictions(path, rows, matrix):
                    if not rows:return
                    truth=np.asarray([r['answer'] for r in rows],np.float64)
                    predicted,_=eval_heads(torch,net,matrix,truth,0.,1.,args.device,args.eval_batch)
                    with path.open('w',newline='',encoding='utf-8') as stream:
                        writer=csv.writer(stream)
                        writer.writerow(('program','form','magnitude','actual','predicted','signed_error','exact_round','prompt'))
                        for i,row in enumerate(rows):
                            writer.writerow((row.get('focus_program','P7'),row.get('focus_form','natural'),
                                row.get('focus_magnitude',row.get('magnitude_target')),
                                float(truth[i]),float(predicted[i]),float(predicted[i]-truth[i]),
                                int(np.rint(predicted[i])==truth[i]),row['prompt']))
                write_predictions(stage_dir/'development_predictions.csv',current_dev,gate_x)
                write_predictions(stage_dir/'program_form_predictions.csv',probe_rows,probe_x)
                audit_x=features(('audit',radius,magnitude),current_audit)
                audit_y=np.asarray([r['answer'] for r in current_audit],np.float64)
                audit_pred,_=eval_heads(torch,net,audit_x,audit_y,0.,1.,args.device,args.eval_batch)
                save_balance_report(stage_dir/'audit',radius,current_audit,
                                    {f'{name}.zdoubleprime':audit_pred},
                                    {'kind':'heldout_audit','radius':radius,'magnitude':magnitude})
                torch.save({'model':net.state_dict(),'optimizer':opt.state_dict(),
                            'input_width':input_width,'hidden':VARIANTS[name],
                            'radius':radius,'magnitude':magnitude,'variant':name,
                            'accepted':acquired,'training_mode':'adaptive_zero_line_exact'},stage_dir/'encoder.pt')
                report={'radius':radius,'magnitude':magnitude,'accepted':acquired,
                        'best':best_report,'history':history,'sampling_counts':sampling_counts,
                        'effective_mixture':'configured_base_plus_failure_boost',
                        'focus_training_count':len(focus_rows),'probe_count':len(probe_rows),
                        'audit_location':str(stage_dir/'audit'/'balance_report.json')}
                (stage_dir/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                result['variants'][name].append({'radius':radius,'magnitude':magnitude,
                                                 'accepted':acquired,'report':str(stage_dir/'report.json')})
                (out/'zero_line_report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
                print(f'ADAPTIVE {name} magnitude={magnitude} radius={radius} ACCEPTED={acquired}',flush=True)
        print(f'ADAPTIVE {name} completed acquired={len(accepted)} blocked_magnitudes={sorted(blocked)}',flush=True)
    return result
