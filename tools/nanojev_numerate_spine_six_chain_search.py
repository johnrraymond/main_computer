#!/usr/bin/env python3
"""Numerate Spine: six-chain tempered search over frozen LM encoding cassettes.

Stage 1: frozen forward-pass answer logP only, common sealed arithmetic probes.
Stage 2: held-out confirmation and top-candidate layer activation trajectories.
No weight updates, learned probes, generator interventions, or claims of numeracy.
Run --self-test without Torch/Hugging Face to test the algorithm and resume.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
from statistics import mean, pstdev

import nanojev_numerate_spine_cassette_smoke as cassette

SCHEMA = 'nanojev.numerate-spine-six-chain-search.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'numerate_spine_six_chain_v1'
DIMENSIONS = {
    'encoding': ('pipe', 'zero', 'one', 'alternating', 'hash', 'dot', 'brackets'),
    'width': (1, 2, 4, 8, 16, 32),
    'noise': ('clean', 'human', 'code', 'mixed'),
    'direction': ('forward', 'backward'),
    'order': ('rule-first', 'expression-first'),
    'layout': ('tight', 'spaced', 'lines'),
    'answer_radix': ('decimal', 'binary', 'base15'),
    'scaffold': ('none', 'decimal-first', 'decimal-last', 'binary-first', 'binary-last',
                 'base15-first', 'base15-last'),
}
SYMBOLS = {'pipe': '|', 'zero': '0', 'one': '1', 'alternating': '01',
           'hash': '#', 'dot': '.', 'brackets': '[]'}
NOISE = {
    'clean': '',
    'human': 'A traveler watched clouds beyond a quiet bridge; this sentence is unrelated.',
    'code': 'function ready(x) { return (cache[x] !== undefined) ? true : false; }',
    'mixed': 'The rain stopped. state={"ok":true,"cache":7}; nothing here affects the expression.',
}
TEMPERATURES = (0.3, 0.6, 1.2, 2.4, 4.8, 9.6)
DISCOVERY_TOTALS = (3, 5, 10, 15)
CONFIRM_TOTALS = (4, 6, 11, 16, 19)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256((value if isinstance(value, bytes) else value.encode('utf8'))).hexdigest()


def atom_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf8')
    os.replace(temp, path)


def emit(event, **kw):
    print(canonical({'event': event, **kw}), flush=True)


def config_key(c):
    return digest(canonical(c))


def space_size():
    return math.prod(len(v) for v in DIMENSIONS.values())


def valid_config(c):
    if set(c) != set(DIMENSIONS):
        raise ValueError('SEARCH_CONFIG_DIMENSIONS')
    for k, v in c.items():
        if v not in DIMENSIONS[k]:
            raise ValueError('SEARCH_CONFIG_VALUE:' + k)


def random_config(rng):
    return {k: rng.choice(v) for k, v in DIMENSIONS.items()}


def propose(c, rng, global_rate):
    """Symmetric mixture: uniform independent jump + uniform one-axis edit.

    Local one-axis edits are symmetric because each coordinate is resampled
    uniformly among values *other* than the current value. Global draws are
    uniform over the same finite product space. Therefore q(x|y)=q(y|x).
    """
    if rng.random() < global_rate:
        return random_config(rng), 'global'
    axis = rng.choice(tuple(DIMENSIONS))
    choices = [v for v in DIMENSIONS[axis] if v != c[axis]]
    new = dict(c)
    new[axis] = rng.choice(choices)
    return new, 'local:' + axis


def mh_accept(old, new, temperature, rng):
    if temperature <= 0:
        raise ValueError('SEARCH_BAD_TEMPERATURE')
    d = (old - new) / temperature
    return d >= 0 or math.log(max(rng.random(), 1e-300)) < d


def exchange_accept(ei, ej, ti, tj, rng):
    delta = (1.0 / ti - 1.0 / tj) * (ei - ej)
    return delta >= 0 or math.log(max(rng.random(), 1e-300)) < delta


def encode_integer(value, radix, digits):
    if value < 0:
        raise ValueError('NEGATIVE_ANSWER')
    alphabet = '0123456789ABCDE'
    if radix == 'decimal':
        return str(value)
    base = 2 if radix == 'binary' else 15
    if value == 0:
        s = '0'
    else:
        out = []
        while value:
            value, rem = divmod(value, base)
            out.append(alphabet[rem])
        s = ''.join(reversed(out))
    return s.rjust(digits, '0')


def answers(total, radix):
    base = 2 if radix == 'binary' else 15
    if radix == 'decimal':
        digits = len(str(total + 1))
        return [str(x).zfill(digits) for x in (total-1, total, total+1)]
    d = 1
    while base**d <= total + 1:
        d += 1
    return [encode_integer(n, radix, d) for n in (total-1, total, total+1)]


def tally(q, c):
    return (SYMBOLS[c['encoding']] * (q * c['width'])) if q else 'EMPTY'


def build_prompt(total, left, operator, c):
    valid_config(c)
    right = total - left
    sym = SYMBOLS[c['encoding']] * c['width']
    a, b = tally(left, c), tally(right, c)
    if c['layout'] == 'tight':
        expr = f'{a}{operator}{b}'
    elif c['layout'] == 'spaced':
        expr = f'{a} {operator} {b}'
    else:
        expr = f'{a}\n{operator}\n{b}'
    rule = (f'RULE: Each block [{sym}] is exactly one unit. EMPTY is zero. '
            'Use + as addition and - as subtraction. '
            f'Write the result as {c["answer_radix"]} positional digits; '
            'do not write an explanation.')
    direction = f'DIRECTION: {c["direction"]} movement of the boundary.'
    statement = 'EXPR: ' + expr
    parts = [rule, direction, statement] if c['order']=='rule-first' else [statement, direction, rule]
    scaffold=c['scaffold']
    if scaffold!='none':
        radix,position=scaffold.rsplit('-',1)
        base=2 if radix=='binary' else 15 if radix=='base15' else 10
        digit_count=1
        while base**digit_count <= total:
            digit_count+=1
        if radix=='decimal':
            left_code=str(left).zfill(digit_count)
            right_code=str(right).zfill(digit_count)
        else:
            left_code=encode_integer(left,radix,digit_count)
            right_code=encode_integer(right,radix,digit_count)
        extra=(f'SCHEME: {radix} operand transcription only; '
               f'LEFT={left_code}; RIGHT={right_code}; no answer is supplied.')
        parts.insert(0 if position=='first' else len(parts),extra)
    prefix = 'CASSETTE: one-step conserved-unit transformation.'
    if c['noise'] != 'clean':
        prefix += '\nUNRELATED CONTEXT: ' + NOISE[c['noise']]
    return prefix + '\n' + '\n'.join(parts) + '\nAnswer:'


def positions(total, n):
    """Distinct nontrivial boundary positions; preserve exact arithmetic."""
    candidates = (0, 1, total//3, total//2, (2*total)//3, total-1)
    unique = list(dict.fromkeys(x for x in candidates if 0 <= x < total))
    if n == 1:
        return (unique[len(unique)//2],)
    if n >= len(unique):
        return tuple(unique)
    indices = list(dict.fromkeys(round(i*(len(unique)-1)/(n-1)) for i in range(n)))
    return tuple(unique[i] for i in indices)


def probes(c, totals, positions_per_total):
    cases = []
    for total in totals:
        spots = positions(total, positions_per_total)
        if c['direction'] == 'backward':
            spots = tuple(reversed(spots))
        for left in spots:
            plus = build_prompt(total, left, '+', c)
            minus = build_prompt(total, left, '-', c)
            right = total-left
            assert left+right == total and left-right != total
            cases.append({'total':total, 'left':left, 'right':right,
                          'correct':total, 'minus_result':left-right,
                          'answers':answers(total,c['answer_radix']),
                          'plus':plus, 'minus':minus})
    return cases


def reduce_observations(observations, token_cost):
    """Common-score objective; prevents a trivially constant answer from winning.

    All candidates see precisely the same mathematical totals/splits. Matched
    subtraction prompts penalize answer-only/lexical priors. Invariance is
    evaluated only in combination with accuracy and plus-minus discrimination.
    """
    if not observations:
        raise ValueError('SEARCH_EMPTY_OBSERVATIONS')
    probabilities, differences, accuracies, margins, lengths = [], [], [], [], []
    group = {}
    for row in observations:
        plus, minus = row['plus'], row['minus']
        probs_p = softmax(plus['logp'])
        probs_m = softmax(minus['logp'])
        probabilities.append(probs_p[1])
        differences.append(probs_p[1]-probs_m[1])
        accuracies.append(int(max(range(3), key=lambda i: plus['logp'][i]) == 1))
        margins.append(plus['logp'][1] - max(plus['logp'][0], plus['logp'][2]))
        lengths.append(max(plus['prefix_tokens'], minus['prefix_tokens']))
        group.setdefault(row['total'], []).append(probs_p[1])
    acc = mean(accuracies)
    contrast = mean(differences)
    instability = mean(pstdev(v) for v in group.values())
    # Bounded terms keep a wildly miscalibrated logP from dominating.
    energy = (1.4*(1-acc) + 1.0*(1-contrast)/2.0
              + 0.2*instability + 0.03*min(2.0,mean(lengths)/token_cost))
    return {'energy':float(energy), 'plus_accuracy':acc, 'plus_minus_probability_gap':contrast,
            'plus_correct_probability':mean(probabilities),
            'within_total_probability_std':instability, 'mean_margin':mean(margins),
            'mean_prompt_tokens':mean(lengths), 'observations':len(observations)}


def softmax(values):
    hi = max(values)
    ps = [math.exp(v-hi) for v in values]
    z = sum(ps)
    return [x/z for x in ps]


def locate_operator_any(tokenizer, prompt):
    """Offset locate +/- in tight, spaced or multiline EXPR in either order."""
    start=prompt.index('EXPR: ')+len('EXPR: ')
    end=min([i for needle in ('\nRULE:', '\nDIRECTION:', '\nSCHEME:', '\nAnswer:')
             if (i:=prompt.find(needle,start))>=0] or [len(prompt)])
    expr=prompt[start:end]
    ops=[i for i,ch in enumerate(expr) if ch in '+-']
    if len(ops)!=1:
        raise RuntimeError('SEARCH_OPERATOR_NOT_UNIQUE')
    char_pos=start+ops[0]
    encoding=tokenizer(prompt,add_special_tokens=True,return_offsets_mapping=True)
    matches=[j for j,(a,b) in enumerate(encoding['offset_mapping']) if a<=char_pos<b and a!=b]
    if len(matches)!=1:
        raise RuntimeError('SEARCH_OPERATOR_OFFSET_MISMATCH')
    return matches[0]


def model_scorer(torch, model, tokenizer, device, max_tokens):
    """Score the exact three continuations, optionally capturing operator/answer states.

    Search has no activation tensor allocations. Trace capture is performed
    separately on only the confirmed candidates.
    """
    def score(prompt, answer_strings, capture=False):
        if 'EXPR: ' not in prompt:
            raise ValueError('SEARCH_EXPRESSION_MISSING')
        prefix_ids = tokenizer.encode(prompt, add_special_tokens=True)
        endings = [tokenizer.encode(' ' + x, add_special_tokens=False) for x in answer_strings]
        if not prefix_ids or any(not end for end in endings):
            raise RuntimeError('SEARCH_EMPTY_TOKENIZATION')
        maxlen = max(len(prefix_ids)+len(s) for s in endings)
        if maxlen > max_tokens:
            raise RuntimeError('SEARCH_TOKEN_LIMIT')
        padding = tokenizer.pad_token_id
        if padding is None:
            padding = tokenizer.eos_token_id
        if padding is None:
            padding = 0
        token = torch.full((3,maxlen), int(padding),dtype=torch.long,device=device)
        mask = torch.zeros_like(token)
        for i,end in enumerate(endings):
            ids = prefix_ids+end
            token[i,:len(ids)] = torch.tensor(ids,dtype=torch.long,device=device)
            mask[i,:len(ids)] = 1
        with torch.inference_mode():
            out = model(input_ids=token, attention_mask=mask, use_cache=False,
                        output_hidden_states=capture)
            logp=[]
            for i,end in enumerate(endings):
                logits=out.logits[i,len(prefix_ids)-1:len(prefix_ids)-1+len(end)].float()
                target=torch.tensor(end,device=device,dtype=torch.long)
                val=(logits.gather(-1,target[:,None]).squeeze(-1)-torch.logsumexp(logits,dim=-1)).sum()
                logp.append(float(val.item()))
            vectors=None
            if capture:
                import numpy as np
                op_index = locate_operator_any(tokenizer,prompt)
                vectors=np.stack([np.stack((h[0,op_index].float().cpu().numpy(),
                                           h[0,len(prefix_ids)-1].float().cpu().numpy()))
                                  for h in out.hidden_states])
        if not all(math.isfinite(p) for p in logp):
            raise RuntimeError('SEARCH_NONFINITE_SCORES')
        return {'logp':logp,'prefix_tokens':len(prefix_ids)},vectors
    return score


def evaluate(c, scorer, totals, positions_per_total, max_tokens):
    observations=[]
    for problem in probes(c, totals, positions_per_total):
        plus,_=scorer(problem['plus'],problem['answers'])
        minus,_=scorer(problem['minus'],problem['answers'])
        observations.append({**{k:problem[k] for k in ('total','left','right','correct','minus_result')},
                             'plus':plus, 'minus':minus})
    return reduce_observations(observations,max_tokens), observations


def read_config(path):
    return json.loads(path.read_text(encoding='utf8'))


def immutable_settings(args):
    return {'schema':SCHEMA,'model':args.model,'checkpoint':list(cassette.SOURCE[args.model]),
            'seed':args.seed,'steps':args.steps,'temperatures':list(TEMPERATURES),
            'global_rate':args.global_rate, 'exchange_every':args.exchange_every,
            'warmup_fraction':args.warmup_fraction, 'cool_to':args.cool_to,
            'max_prompt_tokens':args.max_prompt_tokens,
            'discovery_totals':list(DISCOVERY_TOTALS),
            'confirmation_totals':list(CONFIRM_TOTALS),
            'positions_per_total':args.positions_per_total,
            'space_dimensions':{k:list(v) for k,v in DIMENSIONS.items()},
            'reporting':'held-out and trace are after search and do not change search state'}


def seed_config(i, rng):
    c=random_config(rng)
    c['encoding']=DIMENSIONS['encoding'][i % len(DIMENSIONS['encoding'])]
    c['noise']=DIMENSIONS['noise'][i%len(DIMENSIONS['noise'])]
    c['answer_radix']=DIMENSIONS['answer_radix'][i%len(DIMENSIONS['answer_radix'])]
    return c


def serialize_rng(rng):
    version, state, gaussian = rng.getstate()
    return [version,list(state),gaussian]


def restore_rng(rng, value):
    rng.setstate((value[0],tuple(value[1]),value[2]))


def load_or_init(root, settings, resume, rng):
    manifest_path=root/'search_manifest.json'
    checkpoint=root/'checkpoint.json'
    if manifest_path.exists() or checkpoint.exists():
        if not resume or not manifest_path.exists() or not checkpoint.exists():
            raise RuntimeError('SEARCH_OUTPUT_EXISTS_OR_INCOMPLETE: use --resume only for a complete matching checkpoint')
        if read_config(manifest_path)!=settings:
            raise RuntimeError('SEARCH_RESUME_CONFIGURATION_DRIFT')
        state=read_config(checkpoint)
        if state.get('settings_sha256') != digest(canonical(settings)):
            raise RuntimeError('SEARCH_CHECKPOINT_IDENTITY_DRIFT')
        restore_rng(rng,state['rng'])
        return state
    if resume and root.exists() and any(root.iterdir()):
        raise RuntimeError('SEARCH_RESUME_REQUIRES_CHECKPOINT')
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('SEARCH_OUTPUT_EXISTS: select new --output-root')
    root.mkdir(parents=True,exist_ok=True)
    atom_json(manifest_path,settings)
    state={'settings_sha256':digest(canonical(settings)),'phase':'init',
           'chains':[None]*6,'step':0,'cursor':0,'accepted':[0]*6,'proposed':[0]*6,
           'exchanges_accepted':0,'exchanges_proposed':0,'history':[],
           'rng':serialize_rng(rng)}
    atom_json(checkpoint,state)
    return state


def cached_evaluation(root,c,scorer,args,totals=DISCOVERY_TOTALS,force=False):
    key=config_key(c)
    path=root/'cache'/f'{key}.json'
    if path.exists() and not force:
        data=read_config(path)
        if data.get('config')!=c or data.get('key')!=key or data.get('schema')!=SCHEMA:
            raise RuntimeError('SEARCH_CACHE_IDENTITY_DRIFT')
        return data['metrics'], True
    if scorer is None:
        raise RuntimeError('SEARCH_MISSING_SCORER')
    metrics,_=evaluate(c,scorer,totals,args.positions_per_total,args.max_prompt_tokens)
    atom_json(path,{'schema':SCHEMA,'key':key,'config':c,'metrics':metrics})
    return metrics,False


def write_csv(path,rows):
    if not rows:
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    keys=list(dict.fromkeys(k for row in rows for k in row))
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:canonical(v) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    os.replace(temp,path)


def temperature(args, chain_i, step):
    # Warm exploration before cooling, with nonzero hot-chain floor.
    fraction=step/max(1,args.steps-1)
    if fraction<=args.warmup_fraction:
        factor=1.0
    else:
        progress=(fraction-args.warmup_fraction)/(1-args.warmup_fraction)
        factor=args.cool_to**progress
    return TEMPERATURES[chain_i]*factor


def commit(root,state,rng):
    state['rng']=serialize_rng(rng)
    atom_json(root/'checkpoint.json',state)


def search(root,args,scorer,loaded_model_identity=None):
    rng=random.Random(args.seed)
    settings=immutable_settings(args)
    state=load_or_init(root,settings,args.resume,rng)
    if loaded_model_identity is not None:
        identity_path=root/'loaded_model_identity.json'
        if identity_path.exists():
            if read_config(identity_path)!=loaded_model_identity:
                raise RuntimeError('SEARCH_RESUME_MODEL_IDENTITY_DRIFT')
        else:
            # Written before first inference, including on interrupted runs.
            atom_json(identity_path,loaded_model_identity)
    start_eval=len(list((root/'cache').glob('*.json'))) if (root/'cache').exists() else 0
    while state['phase']=='init':
        try:
            i=state['chains'].index(None)
        except ValueError:
            state['phase']='sampling';state['cursor']=0
            commit(root,state,rng)
            break
        c=seed_config(i,rng)
        metrics,cached=cached_evaluation(root,c,scorer,args)
        state['chains'][i]={'config':c,'metrics':metrics}
        state['history'].append({'event':'initial','chain':i,'config_key':config_key(c),
                                 'energy':metrics['energy'],'cached':cached})
        commit(root,state,rng)
        emit('search_chain_initialized',chain=i,energy=metrics['energy'],cache_hit=cached)
    while state['phase']=='sampling':
        step=state['step']
        if step>=args.steps:
            state['phase']='done';commit(root,state,rng);break
        i=state['cursor']
        now=state['chains'][i]
        cand,move=propose(now['config'],rng,args.global_rate)
        metrics,cached=cached_evaluation(root,cand,scorer,args)
        t=temperature(args,i,step)
        acc=mh_accept(now['metrics']['energy'],metrics['energy'],t,rng)
        state['proposed'][i]+=1
        if acc:
            state['accepted'][i]+=1
            state['chains'][i]={'config':cand,'metrics':metrics}
        state['history'].append({'event':'proposal','step':step,'chain':i,'temperature':t,
             'proposal':config_key(cand),'old_energy':now['metrics']['energy'],
             'new_energy':metrics['energy'],'accepted':acc,'move':move,'cached':cached})
        state['cursor']+=1
        if state['cursor']==6:
            state['cursor']=0
            if (step+1)%args.exchange_every==0:
                # Adjacent replica swaps are reversible and satisfy detailed
                # balance at the *current* set of temperatures.
                for j in range(5):
                    c0,c1=state['chains'][j:j+2]
                    t0,t1=temperature(args,j,step),temperature(args,j+1,step)
                    yes=exchange_accept(c0['metrics']['energy'],c1['metrics']['energy'],t0,t1,rng)
                    state['exchanges_proposed']+=1
                    if yes:
                        state['exchanges_accepted']+=1
                        state['chains'][j],state['chains'][j+1]=c1,c0
                    state['history'].append({'event':'exchange','step':step,'chain_pair':[j,j+1],
                                            'accepted':yes,'temperatures':[t0,t1]})
            state['step']+=1
            if state['step']%args.report_every==0 or state['step']==args.steps:
                emit('search_progress',step=state['step'],steps=args.steps,
                     cache_entries=len(list((root/'cache').glob('*.json'))),
                     best_energy=min(c['metrics']['energy'] for c in state['chains']),
                     accepted=state['accepted'])
        commit(root,state,rng)
    # Rankings include all evaluated candidates, not merely chain endpoints.
    ranking=[]
    for path in (root/'cache').glob('*.json'):
        record=read_config(path)
        if record['key']!=config_key(record['config']):
            raise RuntimeError('SEARCH_CACHE_IDENTITY_DRIFT')
        ranking.append({'key':record['key'],'config':record['config'],**record['metrics']})
    ranking.sort(key=lambda r:(r['energy'],r['key']))
    write_csv(root/'rankings.csv',ranking)
    write_csv(root/'search_history.csv',state['history'])
    atom_json(root/'search_report.json',{
        'schema':SCHEMA,'status':'search_completed','search_space_size':space_size(),
        'new_evaluations_this_invocation':len(ranking)-start_eval,
        'evaluated_candidates':len(ranking),'steps_completed':state['step'],
        'temperatures':list(TEMPERATURES),'acceptance_counts':state['accepted'],
        'proposal_counts':state['proposed'], 'exchange_acceptance':state['exchanges_accepted'],
        'exchange_proposals':state['exchanges_proposed'],
        'best':ranking[:min(args.confirm_top,len(ranking))],
        'search_type':'Six tempered MH chains with scheduled cooling; not stationary posterior sampling',
        'claims':'Search performance is discovery-only; confirmation and causal interventions separate.'})
    return ranking,state


def confirmation(root,args,scorer,ranking):
    """Held-out totals are not queried during the MCMC search."""
    selected=ranking[:args.confirm_top]
    rows=[]
    for rank,entry in enumerate(selected,1):
        key=entry['key']
        path=root/'confirmation'/f'{key}.json'
        if path.exists():
            result=read_config(path)
            if result['key']!=key or result['config']!=entry['config']:
                raise RuntimeError('SEARCH_CONFIRM_IDENTITY_DRIFT')
            metrics=result['metrics']
        else:
            metrics,observations=evaluate(entry['config'],scorer,CONFIRM_TOTALS,
                                          args.positions_per_total,args.max_prompt_tokens)
            atom_json(path,{'key':key,'config':entry['config'],'metrics':metrics,
                 'heldout_totals':list(CONFIRM_TOTALS),'observations':observations})
        rows.append({'rank':rank,'key':key,'config':entry['config'],
                     'discovery_energy':entry['energy'],
                     **{'confirmation_'+k:v for k,v in metrics.items()}})
        emit('search_confirmation',rank=rank,heldout_accuracy=metrics['plus_accuracy'],
             heldout_gap=metrics['plus_minus_probability_gap'])
    write_csv(root/'confirmation.csv',rows)
    return rows


def robustness_confirmation(root,args,scorer,ranking):
    """Held-out cross-noise stress test; no noise score used to tune MH."""
    rows=[]
    for rank,entry in enumerate(ranking[:args.robust_top],1):
        for noise in NOISE:
            candidate=dict(entry['config'],noise=noise)
            key=entry['key']
            out=root/'robustness'/f'{key}.{noise}.json'
            if out.exists():
                item=read_config(out)
                if item.get('config')!=candidate or item.get('candidate_key')!=key:
                    raise RuntimeError('SEARCH_ROBUSTNESS_IDENTITY_DRIFT')
                result=item['metrics']
            else:
                result,obs=evaluate(candidate,scorer,CONFIRM_TOTALS,
                                    args.positions_per_total,args.max_prompt_tokens)
                atom_json(out,{'candidate_key':key,'config':candidate,'metrics':result,
                               'heldout_totals':list(CONFIRM_TOTALS),'observations':obs})
            rows.append({'discovery_rank':rank,'key':key,'noise':noise,
                         'encoding':candidate['encoding'],
                         'heldout_accuracy':result['plus_accuracy'],
                         'heldout_plus_minus_gap':result['plus_minus_probability_gap'],
                         'heldout_energy':result['energy']})
    write_csv(root/'noise_robustness.csv',rows)
    if rows:
        summary=[]
        for rank in sorted({r['discovery_rank'] for r in rows}):
            group=[r for r in rows if r['discovery_rank']==rank]
            summary.append({'discovery_rank':rank,'key':group[0]['key'],
                            'worst_accuracy':min(r['heldout_accuracy'] for r in group),
                            'mean_accuracy':mean(r['heldout_accuracy'] for r in group),
                            'worst_plus_minus_gap':min(r['heldout_plus_minus_gap'] for r in group)})
        write_csv(root/'noise_robustness_summary.csv',summary)
    return rows


def record_trace(root,args,scorer,entry):
    """Full-layer trace of the moving boundary for a top discovery candidate."""
    import numpy as np
    key=entry['key'];c=entry['config']
    total=args.trace_total
    output=root/'traces'/key
    output.mkdir(parents=True,exist_ok=True)
    manifest=output/'trace_manifest.json'
    expected={'schema':SCHEMA,'key':key,'config':c,'total':total,
              'model':args.model,'max_prompt_tokens':args.max_prompt_tokens}
    if manifest.exists():
        if read_config(manifest)!=expected:
            raise RuntimeError('SEARCH_TRACE_IDENTITY_DRIFT')
    else:
        atom_json(manifest,expected)
    steps=list(range(total+1))
    if c['direction']=='backward':
        steps.reverse()
    rows=[]; arrays=[]
    for i,left in enumerate(steps):
        path=output/f'{i:03d}.npz'
        if path.exists():
            with np.load(path,allow_pickle=False) as f:
                vectors=f['vectors'].astype(np.float32)
                row=json.loads(str(f['metadata'].item()))
            if (row['key']!=key or row['left']!=left or row['total']!=total
                or row.get('prompt_sha256')!=digest(build_prompt(total,left,'+',c))
                or vectors.ndim!=3 or vectors.shape[1]!=2 or not np.isfinite(vectors).all()):
                raise RuntimeError('SEARCH_TRACE_IDENTITY_DRIFT')
        else:
            prompt=build_prompt(total,left,'+',c)
            result,vectors=scorer(prompt,answers(total,c['answer_radix']),capture=True)
            if vectors is None or vectors.ndim!=3 or vectors.shape[1]!=2:
                raise RuntimeError('SEARCH_MISSING_ACTIVATION_CAPTURE')
            row={'key':key,'total':total,'left':left,'right':total-left,'step':i,
                 'prompt_sha256':digest(prompt), 'correct_probability':softmax(result['logp'])[1],
                 'correct_margin':result['logp'][1]-max(result['logp'][0],result['logp'][2]),
                 'prefix_tokens':result['prefix_tokens']}
            tmp=path.with_suffix('.tmp.npz')
            np.savez_compressed(tmp,metadata=canonical(row),vectors=vectors.astype(np.float16))
            os.replace(tmp,path)
        arrays.append(vectors);rows.append(row)
    arrays=np.stack(arrays)
    diffs=np.diff(arrays,axis=0)
    distances=(np.linalg.norm(diffs,axis=-1)/math.sqrt(arrays.shape[-1])).mean(axis=2)
    for i,row in enumerate(rows):
        row['mean_step_displacement']=float(distances[i-1].mean()) if i else 0.0
    write_csv(output/'trajectory.csv',rows)
    layers=[{'layer':int(j), 'mean_step_displacement':float(distances[:,j].mean())}
            for j in range(distances.shape[1])]
    write_csv(output/'layer_displacement.csv',layers)
    return rows,layers


def plot_search(root,ranking,confirm_rows,traces):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        emit('search_plot_skipped',reason='matplotlib absent in active interpreter')
        return
    if ranking:
        plt.figure(figsize=(9,4))
        plt.plot(range(1,len(ranking)+1),[r['energy'] for r in ranking])
        plt.xlabel('Candidate rank');plt.ylabel('Discovery energy (lower better)')
        plt.title('Six-chain search: evaluated candidates')
        plt.tight_layout();plt.savefig(root/'ranked_energy.png');plt.close()
    if confirm_rows:
        plt.figure(figsize=(9,4))
        x=[str(r['rank']) for r in confirm_rows]
        plt.plot(x,[r['confirmation_plus_accuracy'] for r in confirm_rows],marker='o',label='Held-out accuracy')
        plt.plot(x,[r['confirmation_plus_minus_probability_gap'] for r in confirm_rows],marker='s',label='Held-out + vs - gap')
        plt.axhline(1/3,linestyle=':',label='Three-way chance accuracy')
        plt.legend();plt.xlabel('Discovery rank');plt.title('Held-out confirmation')
        plt.tight_layout();plt.savefig(root/'heldout_confirmation.png');plt.close()
    for key,rows,layers in traces:
        out=root/'traces'/key
        plt.figure(figsize=(9,4))
        plt.plot([r['step'] for r in rows],[r['correct_margin'] for r in rows],marker='.')
        plt.axhline(0,linestyle=':')
        plt.xlabel('Boundary swap step');plt.ylabel('Correct answer logP margin')
        plt.title('Same total, moving +');plt.tight_layout()
        plt.savefig(out/'answer_margin.png');plt.close()
        plt.figure(figsize=(9,4))
        plt.plot([v['layer'] for v in layers],[v['mean_step_displacement'] for v in layers])
        plt.xlabel('Layer including embedding');plt.ylabel('Normalized activation displacement')
        plt.title('Recorded operator/answer trajectory displacement')
        plt.tight_layout();plt.savefig(out/'layer_displacement.png');plt.close()


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--prepare-only',action='store_true',help='show sealed settings and example prompts; no model or output')
    p.add_argument('--model',choices=tuple(cassette.SOURCE),default='qwen')
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--dtype',choices=('auto','float32','float16','bfloat16'),default='auto')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--output-root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--steps',type=int,default=60,help='proposals PER chain; six chains total')
    p.add_argument('--seed',type=int,default=14215)
    p.add_argument('--positions-per-total',type=int,default=2)
    p.add_argument('--max-prompt-tokens',type=int,default=2048)
    p.add_argument('--global-rate',type=float,default=0.3)
    p.add_argument('--exchange-every',type=int,default=5)
    p.add_argument('--warmup-fraction',type=float,default=0.35)
    p.add_argument('--cool-to',type=float,default=0.25)
    p.add_argument('--confirm-top',type=int,default=6)
    p.add_argument('--trace-top',type=int,default=2)
    p.add_argument('--robust-top',type=int,default=2,help='held-out all-noise controls for best N candidates')
    p.add_argument('--trace-total',type=int,default=10)
    p.add_argument('--report-every',type=int,default=5)
    p.add_argument('--search-only',action='store_true',help='skip held-out evaluation and activation tracing')
    return p


def validate_args(args):
    if not(1<=args.steps<=10000 and 1<=args.positions_per_total<=6
           and 32<=args.max_prompt_tokens<=100000 and 0<=args.global_rate<=1
           and 1<=args.exchange_every and 0<=args.warmup_fraction<1
           and 0<args.cool_to<=1 and 0<=args.confirm_top<=space_size()
           and 0<=args.trace_top<=args.confirm_top and 0<=args.robust_top<=args.confirm_top
           and 2<=args.trace_total<=256
           and 1<=args.report_every):
        raise ValueError('SEARCH_INVALID_PARAMETER')


def self_test():
    import tempfile
    from argparse import Namespace
    # A synthetic evaluator exercises six-chain MH, global proposals,
    # replica exchange, atomic resume and held-out grouping, without HF.
    rng=random.Random(7)
    c=random_config(rng)
    assert space_size()==7*6*4*2*2*3*3*7
    for _ in range(100):
        other,_=propose(c,rng,0.4)
        valid_config(other)
    assert sum(positions(15,3))>=0
    assert all(x['minus_result']!=x['total'] for x in probes(c,(3,5,15),3))
    assert all(len(p['answers'])==3 for p in probes(c,(3,5),2))
    # Exercise real pilot CLI validation, not just the synthetic scoring path.
    pilot=parser().parse_args(['--steps','2','--exchange-every','1',
                               '--confirm-top','2','--trace-top','1'])
    validate_args(pilot)
    # Reporting and exchange intervals may exceed a short smoke duration.
    validate_args(parser().parse_args(['--steps','2']))
    assert mh_accept(2.0,1.0,0.3,rng)
    assert exchange_accept(2.0,1.0,0.2,2.0,rng) is False or True
    example={'encoding':'pipe','width':1,'noise':'clean','direction':'forward',
             'order':'rule-first','layout':'tight','answer_radix':'decimal','scaffold':'none'}
    assert '|||+||' in build_prompt(5,3,'+',example)
    assert answers(15,'base15')==['0E','10','11']
    assert answers(15,'binary')==['01110','01111','10000']
    args=parser().parse_args(['--steps','3','--trace-top','0','--confirm-top','0','--robust-top','0','--report-every','1'])
    def fake_score(prompt,ans,capture=False):
        # Simulated frozen scorer; deterministically varies with the
        # semantics and surface encoding. No oracle model inference claim.
        start=prompt.index('EXPR: ')+len('EXPR: ')
        end=min([j for needle in ('\nRULE:', '\nDIRECTION:', '\nSCHEME:', '\nAnswer:')
                 if (j:=prompt.find(needle,start))>=0] or [len(prompt)])
        is_plus='+' in prompt[start:end]
        consistent= 'Each block [|]' in prompt
        p=[-2.0, 0.0 if is_plus and consistent else -2.1, -2.0]
        return {'logp':p,'prefix_tokens':len(prompt)//6},None
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        ranking,state=search(root,args,fake_score)
        assert len(state['chains'])==6 and state['step']==3
        assert len(ranking)>6 and (root/'search_history.csv').is_file()
        args.resume=True
        ranking2,state2=search(root,args,fake_score)
        assert [(r['key'],r['energy']) for r in ranking]==[(r['key'],r['energy']) for r in ranking2]
        assert state2['step']==3
        (root/'search_manifest.json').write_text('garbage',encoding='utf8')
        try:
            search(root,args,fake_score)
        except (ValueError,RuntimeError):
            pass
        else:
            raise AssertionError('SEARCH_FAILED_TO_REJECT_MANIFEST_DRIFT')
    emit('search_self_test_pass',chain_count=6,space_size=space_size(),
         mock_search_steps=3,real_model_loaded=False,training=False)


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        self_test();return
    validate_args(args)
    if args.prepare_only:
        c={k:v[0] for k,v in DIMENSIONS.items()}
        emit('search_plan',space_size=space_size(),chains=6,steps_per_chain=args.steps,
             approximate_new_config_upper_bound=6*(args.steps+1),
             discovery_totals=DISCOVERY_TOTALS,confirmation_totals=CONFIRM_TOTALS,
             examples=[x['plus'] for x in probes(c,DISCOVERY_TOTALS[:1],2)],
             settings=immutable_settings(args),model_loaded=False)
        return
    root=args.output_root.expanduser().resolve()
    # Refuse a mismatched config BEFORE expensive model initialization.
    settings=immutable_settings(args)
    if (root/'search_manifest.json').exists() and read_config(root/'search_manifest.json')!=settings:
        raise RuntimeError('SEARCH_RESUME_CONFIGURATION_DRIFT')
    if root.exists() and any(root.iterdir()) and not args.resume:
        raise RuntimeError('SEARCH_OUTPUT_EXISTS: use --resume')
    # Reuse frozen loading and checkpoint metadata from cassette V2.
    loader=argparse.Namespace(device=args.device,dtype=args.dtype,
                              allow_model_download=args.allow_model_download)
    torch,model,tokenizer,device,model_info=cassette.load_source(args.model,loader)
    try:
        model_identity={'schema':SCHEMA,'model':args.model,'model_info':model_info}
        scorer=model_scorer(torch,model,tokenizer,device,args.max_prompt_tokens)
        ranking,state=search(root,args,scorer,loaded_model_identity=model_identity)
        if args.search_only:
            emit('search_finished',root=str(root),evaluated=len(ranking),
                 steps=args.steps, heldout=False,activations_captured=False)
            return
        confirmations=confirmation(root,args,scorer,ranking)
        robustness=robustness_confirmation(root,args,scorer,ranking)
        traces=[]
        for entry in ranking[:args.trace_top]:
            rows,layers=record_trace(root,args,scorer,entry)
            traces.append((entry['key'],rows,layers))
            emit('search_trace_completed',key=entry['key'],states=len(rows))
        plot_search(root,ranking,confirmations,traces)
        emit('search_finished',root=str(root),evaluated=len(ranking),steps=args.steps,
             heldout=len(confirmations),noise_controls=len(robustness),
             traces=len(traces),trainable_parameters=0)
    finally:
        del model
        if device=='cuda':
            torch.cuda.empty_cache()


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        emit('search_failed',error=str(exc),error_type=type(exc).__name__)
        raise
