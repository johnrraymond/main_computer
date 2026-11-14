#!/usr/bin/env python3
"""Optional exact-answer C0 zero-line acquisition with gated signed-radius expansion.

Training, development, and final audit decks are independently seeded/disjoint.
Frozen Qwen features are cached for each shell. The deterministic comparator is
not involved in gradients or checkpoint selection.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import numpy as np

from tools.nanojev_qwen_c0_balance import make_balance_deck, error_stats, positive_ints, save_balance_report
from tools.nanojev_qwen_c0_compressive_train import (
    VARIANTS, capture_features, eval_heads, holdout_key, make_network,
    neural_loss, resolve_variants, signed_log, split,
)
from tools.nanojev_qwen_five_centroids import make_rows



# Mixture weights affect only training examples, never gate/audit examples.
PROGRAMS = tuple(f'P{i}' for i in range(8))
FORMS = ('arithmetic', 'natural')
DELTA_CLASSES = ('zero', 'near', 'outer')


def parse_focus_weights(text, keys, *, default=1.0):
    """Strict, normalized-independent key:weight overrides; zero disables a key."""
    result = {str(k): float(default) for k in keys}
    if not text:
        return result
    seen = set()
    for entry in text.split(','):
        parts = entry.strip().split(':')
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f'invalid mixture weight {entry!r}; expected key:nonnegative_weight')
        key = parts[0].strip()
        if key not in result or key in seen:
            raise ValueError(f'unknown or repeated mixture key {key!r}; available: {", ".join(result)}')
        value = float(parts[1])
        if not np.isfinite(value) or value < 0:
            raise ValueError('mixture weights must be finite and nonnegative')
        result[key] = value
        seen.add(key)
    if not any(result.values()):
        raise ValueError('every mixture weight cannot be zero')
    return result


def classify_delta(answer):
    return 'zero' if answer == 0 else ('near' if abs(answer) == 1 else 'outer')


def focused_prompt(row, program, form):
    """Paired arithmetic and natural-language programs using the same integers.

    P0: identical-term subtraction; P1: +/-1 subtraction; P2: outer
    subtraction; P3: one multiplication; P4/P5/P6: compound expression
    restricted to zero/near/outer; P7: original C0 payoff grammar.
    """
    p, a, q, b = (int(row[k]) for k in ('p', 'a', 'q', 'b'))
    left, right = p * a, q * b
    delta = int(row['answer'])
    if program == 'P0':
        a_term, b_term, answer = left, left, 0
    elif program == 'P1' and abs(delta) == 1:
        a_term, b_term, answer = left, right, delta
    elif program == 'P2' and abs(delta) >= 2:
        a_term, b_term, answer = left, right, delta
    elif program == 'P3':
        answer = left
    elif program == 'P4' and delta == 0:
        answer = 0
    elif program == 'P5' and abs(delta) == 1:
        answer = delta
    elif program == 'P6' and abs(delta) >= 2:
        answer = delta
    elif program == 'P7':
        answer = delta
    else:
        return None
    if program in ('P0', 'P1', 'P2'):
        prompt = (f'{a_term} - {b_term} = ? Answer with one integer.' if form == 'arithmetic' else
                  f'What is {a_term} minus {b_term}? Answer with one integer.')
    elif program == 'P3':
        prompt = (f'{p} * {a} = ? Answer with one integer.' if form == 'arithmetic' else
                  f'What is the total from {a} encounters worth {p} payoff units each? Answer with one integer.')
    elif program in ('P4', 'P5', 'P6'):
        prompt = (f'({p} * {a}) - ({q} * {b}) = ? Answer with one integer.' if form == 'arithmetic' else
                  f'An agent earns {p} units on each of {a} encounters and loses {q} units on each of {b} encounters. What is its exact net payoff? Answer with one integer.')
    else:
        prompt = (row['prompt'] if form == 'natural' else
                  f'({p} * {a}) - ({q} * {b}) = ? Answer with one integer.')
    return {'prompt': prompt, 'answer': answer, 'focus_program': program,
            'focus_form': form, 'focus_delta': classify_delta(answer),
            'focus_magnitude': int(row['magnitude_target'])}


def make_focus_rows(shells, radii, radius, args, program_weights, form_weights,
                    delta_weights, magnitude_weights):
    """Construct a capped, deterministic training-only pool, including lookahead."""
    rng = np.random.default_rng(args.seed + radius * 479)
    current_index = radii.index(radius)
    allowed = radii[:min(len(radii), current_index + 1 + args.zero_line_nonzero_lookahead)]
    rows = []
    seen = set()
    for source_radius in allowed:
        source = shells[source_radius]['train']
        # For every magnitude/delta/sign, cap the number of source cases while
        # preserving every enabled form and program. Prevents huge capture costs.
        groups = {}
        for row in source:
            key = (row['magnitude_target'], row['answer'])
            groups.setdefault(key, []).append(row)
        for group in groups.values():
            indices = rng.permutation(len(group))[:args.zero_line_mixture_per_cell]
            for ix in indices:
                base = group[int(ix)]
                for program in PROGRAMS:
                    if not program_weights[program]: continue
                    for form in FORMS:
                        if not form_weights[form]: continue
                        item = focused_prompt(base, program, form)
                        if item is None or item['prompt'] in seen:continue
                        if not delta_weights[item['focus_delta']] or not magnitude_weights[str(item['focus_magnitude'])]:continue
                        seen.add(item['prompt'])
                        item['focus_source_radius'] = source_radius
                        rows.append(item)
    return rows


def focus_sampling_weights(rows, program_weights, form_weights, delta_weights,
                           magnitude_weights):
    """Weights normalized per stratum, so pool cardinality cannot dictate mix."""
    counts = {}
    for row in rows:
        key = (row['focus_program'],row['focus_form'],row['focus_delta'],row['focus_magnitude'])
        counts[key] = counts.get(key,0)+1
    raw = []
    for row in rows:
        key = (row['focus_program'],row['focus_form'],row['focus_delta'],row['focus_magnitude'])
        raw.append(program_weights[key[0]]*form_weights[key[1]]*delta_weights[key[2]]*
                   magnitude_weights[str(key[3])]/counts[key])
    result = np.asarray(raw,np.float64)
    if not len(result) or result.sum() <= 0:
        raise ValueError('mixture has no feasible examples for this radius; enable a zero-line rung/form/magnitude')
    return result/result.sum()

def radius_ladder(max_radius):
    if max_radius < 1 or (max_radius & (max_radius - 1)):
        raise ValueError('--zero-line-max-radius must be a power of two >= 1')
    result = [0]
    radius = 1
    while radius <= max_radius:
        result.append(radius)
        radius *= 2
    return result


def make_disjoint_shells(magnitudes, radii, train_per_cell, gate_per_cell, audit_per_cell, seed, *, excluded=()):
    """Construct train/dev/audit shells with globally disjoint natural-language prompts.

    A radius-zero training shell includes +/-1 contrast examples to prevent a
    constant-zero shortcut; those guards are also present on the development
    deck and must meet the same exactness threshold.
    """
    used = set(excluded)
    shells = {}
    for index, radius in enumerate(radii):
        distances = (0, 1) if radius == 0 else (radius,)
        data = {}
        for kind, per_cell, salt in (
            ('train', train_per_cell, 10003),
            ('gate', gate_per_cell, 20011),
            ('audit', audit_per_cell, 30013),
        ):
            rows = make_balance_deck(
                deltas=distances, magnitudes=magnitudes,
                samples_per_cell=per_cell, seed=seed + salt + index * 10007,
                excluded_prompts=used,
            )
            for row in rows:
                if row['prompt'] in used:
                    raise AssertionError('zero-line prompt leaked across partitions or shells')
                used.add(row['prompt'])
            data[kind] = rows
        if not data['train'] or not data['gate'] or not data['audit']:
            raise ValueError(f'empty zero-line shell at radius {radius}')
        shells[radius] = data
    return shells


def cell_metrics(rows, pred):
    """Aggregate exactness per delta/magnitude; missing cells are not invented."""
    y = np.asarray([r['answer'] for r in rows], np.float64)
    prediction = np.asarray(pred, np.float64)
    groups = {}
    for i, row in enumerate(rows):
        key = (int(row['imbalance']), int(row['magnitude_target']))
        groups.setdefault(key, []).append(i)
    cells = []
    for (delta, magnitude), idx in sorted(groups.items()):
        metrics = error_stats(y[idx], prediction[idx])
        cells.append({'delta': delta, 'magnitude': magnitude, **metrics})
    return cells


def gate_summary(rows, pred, threshold, retention_floor=None):
    cells = cell_metrics(rows, pred)
    worst = min((c['rounded_exact_pct'] / 100. for c in cells), default=0.)
    mean = float(np.mean([c['rounded_exact_pct'] / 100. for c in cells])) if cells else 0.
    gate = worst >= threshold
    result = {'pass': gate, 'worst_cell_exact_fraction': worst,
              'macro_cell_exact_fraction': mean,
              'minimum_exact_fraction': threshold, 'cells': cells}
    if retention_floor:
        # A previously acquired shell may not regress by > 5 percentage points
        # relative to its accepted cell accuracy. This does not replace the
        # absolute exactness requirement.
        table = {(c['delta'], c['magnitude']): c['rounded_exact_pct'] / 100. for c in cells}
        floors = {(int(d), int(m)): value for d, m, value in retention_floor}
        missed = [{'delta': d, 'magnitude': m, 'required': value,
                   'actual': table.get((d,m), -1.)}
                  for (d,m), value in floors.items()
                  if table.get((d,m), -1.) < value]
        result['retention_failures'] = missed
        result['pass'] = gate and not missed
    return result


def checkpoint_score(gates):
    """Prefer worst-cell exactness, then macro-cell exactness, then MAE."""
    if not gates:
        return (0, -1., -1., -float('inf'))
    return (
        int(all(g['pass'] for g in gates.values())),
        min(g['worst_cell_exact_fraction'] for g in gates.values()),
        float(np.mean([g['macro_cell_exact_fraction'] for g in gates.values()])),
        -float(np.mean([g['mae'] for key,g in gates.items() if key != 'ordinary_c0_retention'])),
    )


def validate_stage_args(args):
    if args.zero_line_steps < 1 or args.zero_line_train_samples_per_cell < 1:
        raise ValueError('zero-line steps and samples must be positive')
    if args.zero_line_gate_samples_per_cell < 1 or args.zero_line_audit_samples_per_cell < 1:
        raise ValueError('zero-line gate and audit samples must be positive')
    if args.zero_line_max_repairs < 0 or args.zero_line_replay_per_shell < 1:
        raise ValueError('zero-line repairs must be nonnegative and replay must be positive')
    if not (0 < args.zero_line_exact_threshold <= 1):
        raise ValueError('zero-line exact threshold must be in (0,1]')
    if not (0 <= args.zero_line_ordinary_fraction < 1):
        raise ValueError('zero-line ordinary replay fraction must be in [0,1)')
    if args.zero_line_ordinary_pool < 1 or args.outer_points < 1:
        raise ValueError('ordinary training pool and holdout counts must be positive')
    if getattr(args, 'zero_line_mixture', False):
        if args.zero_line_mixture_per_cell < 1 or args.zero_line_nonzero_lookahead < 0:
            raise ValueError('mixture samples per cell must be positive and lookahead nonnegative')
        parse_focus_weights(args.zero_line_program_weights, PROGRAMS)
        parse_focus_weights(args.zero_line_form_weights, FORMS)
        parse_focus_weights(args.zero_line_delta_weights, DELTA_CLASSES)
        parse_focus_weights(args.zero_line_magnitude_weights, (str(m) for m in positive_ints(args.balance_magnitudes)))
    if getattr(args, 'zero_line_adaptive', False):
        if not getattr(args, 'zero_line_mixture', False):
            raise ValueError('--zero-line-adaptive requires --zero-line-mixture')
        if not (0 <= args.zero_line_repair_fraction < 1 and
                0 <= args.zero_line_retention_fraction < 1 and
                args.zero_line_repair_fraction + args.zero_line_retention_fraction < 1):
            raise ValueError('repair and retention fractions must be nonnegative and sum to less than 1')
        if args.zero_line_repair_boost < 1 or not 0 < args.zero_line_repair_lr_factor <= 1:
            raise ValueError('repair boost must be >=1 and repair lr factor in (0,1]')
        if args.zero_line_report_worst < 1:
            raise ValueError('report-worst must be positive')
        if getattr(args, 'zero_line_lazy_capture', None) is False:
            raise ValueError('adaptive mode requires lazy capture; remove --no-zero-line-lazy-capture')
    if args.teacher_weight != 0:
        raise ValueError('the deterministic comparator cannot train the neural path')
    return radius_ladder(args.zero_line_max_radius)


def run_zero_line(args):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    radii = validate_stage_args(args)
    magnitudes = positive_ints(args.balance_magnitudes)
    variants = resolve_variants(args.variants)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable')
    all_rows = make_rows('C0_games', n=args.source_samples, seed=args.seed)
    ordinary_train, ordinary_val = split(all_rows, args.seed)
    ordinary_val = ordinary_val[:args.outer_points]
    shells = make_disjoint_shells(
        magnitudes, radii, args.zero_line_train_samples_per_cell,
        args.zero_line_gate_samples_per_cell, args.zero_line_audit_samples_per_cell,
        args.seed, excluded=(r['prompt'] for r in all_rows),
    )
    audit_prompts = {r['prompt'] for s in shells.values() for r in s['audit']}
    gate_prompts = {r['prompt'] for s in shells.values() for r in s['gate']}
    train_prompts = {r['prompt'] for s in shells.values() for r in s['train']}
    if (audit_prompts & gate_prompts) or (audit_prompts & train_prompts) or (gate_prompts & train_prompts):
        raise AssertionError('zero-line split leakage')

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=False)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = 'right'
    dtype = torch.float16 if args.device.startswith('cuda') else torch.float32
    backbone = AutoModelForCausalLM.from_pretrained(
        args.model, dtype=dtype, trust_remote_code=False,
    ).to(args.device).eval()
    if args.adapter:
        from peft import PeftModel
        backbone = PeftModel.from_pretrained(backbone, args.adapter).eval()
    if getattr(args,'zero_line_backbone_phase',False):
        from tools.nanojev_qwen_c0_backbone_phase import adapt_backbone
        backbone, backbone_report = adapt_backbone(
            args,torch,backbone,tok,shells,radii,magnitudes,ordinary_val,out)
    for p in backbone.parameters():
        p.requires_grad_(False)

    if getattr(args, 'zero_line_adaptive', False):
        from tools.nanojev_qwen_c0_adaptive import run_adaptive
        return run_adaptive(args, torch, backbone, tok, shells, radii, magnitudes,
                            variants, ordinary_train, ordinary_val, out)

    use_mix = getattr(args,'zero_line_mixture',False)
    if use_mix:
        program_weights=parse_focus_weights(args.zero_line_program_weights,PROGRAMS)
        form_weights=parse_focus_weights(args.zero_line_form_weights,FORMS)
        delta_weights=parse_focus_weights(args.zero_line_delta_weights,DELTA_CLASSES)
        magnitude_weights=parse_focus_weights(args.zero_line_magnitude_weights,(str(m) for m in magnitudes))
        # Source train rows only; never transform gate or audit rows into training.
        focus_decks={}
        for radius in radii:
            rows=make_focus_rows(shells,radii,radius,args,program_weights,form_weights,delta_weights,magnitude_weights)
            if not rows: raise ValueError(f'empty mixture at radius={radius}; change enabled focus dimensions')
            prob=focus_sampling_weights(rows,program_weights,form_weights,delta_weights,magnitude_weights)
            expected={dimension:{str(k):float(sum(prob[i] for i,r in enumerate(rows) if str(r[field])==str(k)))
                                 for k in keys}
                      for dimension,field,keys in (
                          ('program','focus_program',PROGRAMS),
                          ('form','focus_form',FORMS),
                          ('delta','focus_delta',DELTA_CLASSES),
                          ('magnitude','focus_magnitude',magnitudes))}
            focus_decks[radius]={'rows':rows,'prob':prob,'expected':expected}
            print(f'ZERO LINE mixture radius={radius} focus_train={len(rows)} programs=' +
                  str({p:sum(r['focus_program']==p for r in rows) for p in PROGRAMS}),flush=True)
    # Collect features once; never backpropagate into Qwen. The audit is
    # captured separately and never used for optimization/checkpoint selection.
    features = {}
    for radius in radii:
        features[radius] = {}
        for kind in ('train','gate','audit'):
            rows = shells[radius][kind]
            features[radius][kind] = capture_features(torch, backbone, tok, rows, args)
        print(f'ZERO LINE shell={radius}: train={len(shells[radius]["train"])} gate={len(shells[radius]["gate"])} audit={len(shells[radius]["audit"])}',flush=True)
    if use_mix:
        for radius in radii:
            focus_decks[radius]['features']=capture_features(torch,backbone,tok,focus_decks[radius]['rows'],args)
    ordinary_x = capture_features(torch, backbone, tok, ordinary_train[:args.zero_line_ordinary_pool], args)
    ordinary_val_x = capture_features(torch, backbone, tok, ordinary_val, args)
    ordinary_val_y = np.asarray([r['answer'] for r in ordinary_val], np.float64)
    ordinary_train = ordinary_train[:args.zero_line_ordinary_pool]
    input_width = int(backbone.config.hidden_size)

    result = {'schema':'nanojev.c0.zero-line.v1', 'mode':'hard_zero_line',
              'radii':radii,'magnitudes':magnitudes,'model':args.model,
              'exact_threshold':args.zero_line_exact_threshold,
              'ordinary_replay_fraction':args.zero_line_ordinary_fraction,
              'neural_supervision':'exact_answers_only',
              'deterministic_role':'independent_not_used_in_zero_line_training',
              'variants':{name:[] for name in variants}}
    if use_mix:
        result['mixture']={'program':program_weights,'form':form_weights,
                           'delta':delta_weights,'magnitude':magnitude_weights,
                           'nonzero_lookahead':args.zero_line_nonzero_lookahead,
                           'samples_per_cell':args.zero_line_mixture_per_cell,
                           'sampling_probabilities_by_radius':{
                               str(r):focus_decks[r]['expected'] for r in radii}}

    for name in variants:
        torch.manual_seed(args.seed + list(VARIANTS).index(name)*100003)
        net = make_network(torch, input_width, VARIANTS[name]).to(args.device)
        init_path = None
        if args.zero_line_init_dir:
            init_path = Path(args.zero_line_init_dir)/name/'encoder.pt'
            if not init_path.is_file():
                raise FileNotFoundError(f'missing C0 initialization checkpoint: {init_path}')
            checkpoint = torch.load(init_path,map_location='cpu',weights_only=True)
            if tuple(checkpoint['hidden']) != VARIANTS[name] or int(checkpoint['input_width']) != input_width:
                raise ValueError(f'incompatible checkpoint architecture: {init_path}')
            net.load_state_dict(checkpoint['model'], strict=True)
        opt = torch.optim.AdamW(net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
        initial_ordinary,_=eval_heads(torch,net,ordinary_val_x,ordinary_val_y,0.,1.,args.device,args.eval_batch)
        # Preserve original C0 validation against pre-zero-line checkpoint;
        # from later stages preserve prior accepted ordinary validation as well.
        ordinary_reference_mae=error_stats(ordinary_val_y,initial_ordinary)['mae']
        ordinary_ceiling=ordinary_reference_mae/.95
        print(f'ZERO LINE variant={name} initial_ordinary_MAE={ordinary_reference_mae:.5g} init={init_path or "random"}',flush=True)
        print(f'ZERO LINE: TRAIN variant={name}, layers={VARIANTS[name]}',flush=True)
        accepted_floors = {}
        for radius in radii:
            # Pool current and previous shell data using matching captured rows.
            rng = np.random.default_rng(args.seed + radius*1009 + list(VARIANTS).index(name)*100003)
            if use_mix:
                focus=focus_decks[radius]
                x_zero=focus['features']
                y_zero=np.asarray([r['answer'] for r in focus['rows']],np.float64)
                focus_prob=focus['prob']
                # Explicit ordinary fraction is independent of program/form mix.
                # Sample replay with replacement, which avoids accidentally
                # exhausting a small ordinary source pool.
                ordinary_pool_y=np.asarray([r['answer'] for r in ordinary_train],np.float64)
            else:
                x_parts=[features[radius]['train']]
                y_parts=[[r['answer'] for r in shells[radius]['train']]]
                for previous in radii:
                    if previous >= radius:break
                    n = min(len(shells[previous]['train']),args.zero_line_replay_per_shell)
                    idx = rng.choice(len(shells[previous]['train']),size=n,replace=False)
                    x_parts.append(features[previous]['train'][idx])
                    y_parts.append([shells[previous]['train'][int(i)]['answer'] for i in idx])
                x_zero=np.concatenate(x_parts)
                y_zero=np.concatenate([np.asarray(y,np.float64) for y in y_parts])
                ordinary_n=min(len(ordinary_train),round(len(y_zero)*args.zero_line_ordinary_fraction/(1-args.zero_line_ordinary_fraction)))
                if ordinary_n:
                    ids=rng.choice(len(ordinary_train),size=ordinary_n,replace=False)
                    x_zero=np.concatenate([x_zero,ordinary_x[ids]])
                    y_zero=np.concatenate([y_zero,np.asarray([ordinary_train[int(i)]['answer'] for i in ids],np.float64)])
            # Gate consists of every acquired radius, not just new shell.
            acquired=[r for r in radii if r<=radius]
            y_gates={r:np.asarray([row['answer'] for row in shells[r]['gate']],np.float64) for r in acquired}
            best_score=None;best_state=None;best_opt=None;best_gates=None;history=[]
            total_steps=args.zero_line_steps*(1+args.zero_line_max_repairs)
            for step in range(1,total_steps+1):
                if use_mix:
                    ordinary_count=rng.binomial(args.batch_size,args.zero_line_ordinary_fraction)
                    focus_ids=rng.choice(len(y_zero),size=args.batch_size-ordinary_count,p=focus_prob)
                    batch_x=x_zero[focus_ids]
                    batch_y=y_zero[focus_ids]
                    if ordinary_count:
                        ordinary_ids=rng.integers(0,len(ordinary_train),size=ordinary_count)
                        batch_x=np.concatenate((batch_x,ordinary_x[ordinary_ids]))
                        batch_y=np.concatenate((batch_y,ordinary_pool_y[ordinary_ids]))
                else:
                    ids=rng.integers(0,len(y_zero),size=args.batch_size)
                    batch_x=x_zero[ids];batch_y=y_zero[ids]
                x=torch.as_tensor(batch_x.copy(),dtype=torch.float32,device=args.device)
                y=torch.as_tensor(signed_log(batch_y).astype('float32'),device=args.device)
                net.train()
                corrected,uncorrected=net(x)
                loss=neural_loss(torch,corrected,uncorrected,y,beta=args.huber_beta,aux_weight=args.aux_weight)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(),args.grad_clip)
                opt.step()
                if step%args.eval_every and step!=total_steps:continue
                gates={}
                for acquired_radius in acquired:
                    rows=shells[acquired_radius]['gate']
                    pred,_=eval_heads(torch,net,features[acquired_radius]['gate'],y_gates[acquired_radius],0.,1.,args.device,args.eval_batch)
                    gate=gate_summary(rows,pred,args.zero_line_exact_threshold)
                    gate['mae']=error_stats(y_gates[acquired_radius],pred)['mae']
                    if acquired_radius in accepted_floors:
                        updated=gate_summary(rows,pred,args.zero_line_exact_threshold,accepted_floors[acquired_radius])
                        gate['retention_failures']=updated.get('retention_failures',[])
                        gate['pass']=gate['pass'] and not gate['retention_failures']
                    gates[str(acquired_radius)]=gate
                ordinary_pred,_=eval_heads(torch,net,ordinary_val_x,ordinary_val_y,0.,1.,args.device,args.eval_batch)
                ordinary_mae=error_stats(ordinary_val_y,ordinary_pred)['mae']
                ordinary_ok=ordinary_mae<=ordinary_ceiling
                gates['ordinary_c0_retention']={'pass':ordinary_ok,'mae':ordinary_mae,
                   'ceiling':ordinary_ceiling,'worst_cell_exact_fraction':float(ordinary_ok),
                   'macro_cell_exact_fraction':float(ordinary_ok)}
                score=checkpoint_score(gates)
                if best_score is None or score>best_score:
                    best_score=score
                    best_state=copy.deepcopy(net.state_dict())
                    best_opt=copy.deepcopy(opt.state_dict())
                    best_gates=copy.deepcopy(gates)
                accepted=all(g['pass'] for g in gates.values())
                event={'step':step,'worst_cell_exact':score[1],
                       'macro_cell_exact':score[2],'gates_pass':accepted}
                history.append(event)
                print(f'  {name} radius={radius} step={step} worst_exact={score[1]:.3f} macro_exact={score[2]:.3f} accepted={accepted}',flush=True)
                if accepted:
                    best_state=copy.deepcopy(net.state_dict());best_opt=copy.deepcopy(opt.state_dict());best_gates=copy.deepcopy(gates)
                    break
            net.load_state_dict(best_state)
            opt.load_state_dict(best_opt)
            # Final, untouched audit data is evaluated only after checkpoint
            # selection, and cannot affect training or expansion decisions.
            audited={}
            for acquired_radius in acquired:
                rows=shells[acquired_radius]['audit']
                ys=np.asarray([r['answer'] for r in rows],np.float64)
                predictions,_=eval_heads(torch,net,features[acquired_radius]['audit'],ys,0.,1.,args.device,args.eval_batch)
                audited[f'{name}.zdoubleprime.radius_{acquired_radius}']=(rows,predictions)
            stage_dir=out/'zero_line'/name/f'radius_{radius}'
            stage_dir.mkdir(parents=True,exist_ok=True)
            audit_report={}
            for method,(rows,pred) in audited.items():
                audit_report[method]=save_balance_report(stage_dir/method, radius, rows,
                    {method:pred}, {'kind':'heldout_audit','radius':radius,'seed':args.seed})
            accepted=all(g['pass'] for g in best_gates.values())
            torch.save({'model':net.state_dict(),'optimizer':opt.state_dict(),
                        'input_width':input_width,'hidden':VARIANTS[name],
                        'radius':radius,'variant':name,'accepted':accepted,
                        'training_mode':'zero_line_exact_supervised'},stage_dir/'encoder.pt')
            stage={'radius':radius,'accepted':accepted,'gate':best_gates,
                   'steps_run':history[-1]['step'],'history':history,
                   'training_count':len(y_zero),'audit_dirs':list(audit_report),
                   'checkpoint_score':best_score}
            (stage_dir/'report.json').write_text(json.dumps(stage,indent=2),encoding='utf-8')
            result['variants'][name].append(stage)
            (out/'zero_line_report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(f'ZERO LINE variant={name} radius={radius} ACCEPTED={accepted}',flush=True)
            if not accepted and not args.zero_line_advance_on_fail:
                print(f'ZERO LINE variant={name}: STOP at radius={radius}, next radius locked (use --zero-line-advance-on-fail for research-only continuation)',flush=True)
                break
            if accepted:
                ordinary_ceiling=min(ordinary_reference_mae/.95,best_gates['ordinary_c0_retention']['mae']/.95)
                for r in acquired:
                    accepted_floors[r]=[(c['delta'],c['magnitude'],max(0.,c['rounded_exact_pct']/100.-0.05))
                                         for c in best_gates[str(r)]['cells']]
    return result
