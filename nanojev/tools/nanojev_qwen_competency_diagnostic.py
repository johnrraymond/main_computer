#!/usr/bin/env python3
"""Non-training cross-domain competency / acquisition-rate audit for Qwen LoRA.

T00-T08: one-step canonical reductions; full-chain execution; zero-line and
fresh signed/operand frontier problems.  HDB00-HDB08: *diagnostic* balanced
Hawk-Dove presentations from the legacy million-count templates, with an
explicit known payoff binding.  HDB is NOT part of the T trainer curriculum.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import random
import re
import time

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_hawk_dove_million as hd

SCHEMA = 'nanojev.qwen.competency-cross-domain.v1'
PAYOFFS = dict(zip(hd.TAGS, (Decimal(2), Decimal(1), Decimal(1), Decimal(2))))
# All thresholds refer to counts around one million encounters.  The canonical
# symmetric matrix yields delta=2*k when Hawk encounters = 500000+k.
DEFAULT_OFFSETS = (-32, -8, -4, -2, -1, 0, 1, 2, 4, 8, 32)


def _settings(args):
    k = int(getattr(args, 'competency_per_rung', 8))
    hdb = int(getattr(args, 'competency_hdb_cases', 9))
    chains = int(getattr(args, 'competency_chain_cases', 6))
    tokens = int(getattr(args, 'competency_max_tokens', 512))
    new_tokens = int(getattr(args, 'competency_max_new_tokens', 24))
    if not (2 <= k <= 128 and 3 <= hdb <= 45 and hdb % 2 == 1 and 1 <= chains <= 64 and
            128 <= tokens <= 4096 and 3 <= new_tokens < tokens):
        raise ValueError('COMPETENCY_INVALID_DIAGNOSTIC_BUDGET')
    if not (args.max_rung <= 8 and args.max_rung >= 0 and args.terminal_mode == 'numeric'):
        raise ValueError('COMPETENCY_REQUIRES_NUMERIC_T00_T08')
    return {'per_rung': k, 'hdb_cases': hdb, 'chain_cases': chains,
            'max_tokens': tokens, 'max_new_tokens': new_tokens,
            'seed': args.seed, 'operand_limit': args.operand_limit,
            'max_rung': args.max_rung, 'radii': frontier._radii(args),
            'hdb_payoffs': {key: str(value) for key, value in PAYOFFS.items()},
            'schema': SCHEMA}


def _forbidden_keys(args, settings):
    """Exclude the complete planned frontier training and initial evaluation.

    Intervals of training pools are deterministic under the saved frontier
    policy.  Include all potential pool epochs because an interrupted run
    regenerates the pool at its resume step.  This eliminates exact overlaps
    with the current experiment; it cannot certify disjointness against the
    historical parent model's earlier training data.
    """
    excluded=set()
    radii=settings['radii']
    for row in frontier._evaluation_deck(args,radii):
        excluded.add(frontier._key(row))
    epochs=set((min(len(radii)-1,i*len(radii)//args.frontier_steps),
                i//args.frontier_check_every)
               for i in range(args.frontier_steps))
    for stage,epoch in sorted(epochs):
        radius=radii[stage]
        lower=0 if stage==0 else radii[stage-1]+1
        for row in frontier._deck(radius,lower,args.frontier_train_cases,args,
                   f'TRAIN-{stage}-C{epoch}'):
            excluded.add(frontier._key(row))
    return excluded


def _signed_rows(args, settings, family, excluded):
    """Disjoint deterministic diagnostic deck, *never* an optimizer input."""
    result = []
    seen = set()
    limit = args.operand_limit
    for rung in range(args.max_rung + 1):
        for i in range(settings['per_rung']):
            if family == 'near_zero':
                target = (-2, -1, 0, 1, 2)[i % 5]
                lower, upper = max(1, limit - 64), limit
            elif family == 'operand_edge':
                target = (-32, -8, -1, 0, 1, 8, 32)[i % 7]
                lower, upper = max(1, limit - 16), limit
            elif family == 'outer_result':
                # Out of the stressed maximum result shell when representable.
                start = settings['radii'][-1] + 1
                if start > limit:
                    return []
                width = min(limit, max(start, settings['radii'][-1] * 4))
                sign = -1 if i % 2 else 1
                target = sign * (start + i % (width - start + 1))
                lower, upper = max(1, limit // 2), limit
            else:
                raise ValueError('UNKNOWN_COMPETENCY_FAMILY')
            row = None
            for attempt in range(256):
                nums = ladder._bounded_numbers(rung, target, lower, upper, limit,
                        args.seed + 904433 + 101 * rung,
                        i + attempt * 43, attempt % 13)
                if nums is None:
                    continue
                candidate = ladder._case(rung, nums,
                    f'COMP-{family}-T{rung:02d}-{i}-{attempt}', 'numeric')
                candidate['radius'] = abs(target)
                candidate['band'] = family
                key = frontier._key(candidate)
                if key not in seen and key not in excluded:
                    row = candidate
                    seen.add(key)
                    break
            if row is None:
                raise RuntimeError(f'COMPETENCY_CANNOT_BUILD:{family}:T{rung:02d}:{i}')
            result.append(row)
    return result


def _hdb_offsets(n):
    # Balanced selection keeps equal +/- counts and exact equality.  When a
    # smaller count is requested, preserve the smallest near-zero cases.
    if n % 2 == 0:
        n += 1
    positives = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 4096, 8192, 16384, 65536,
                 131072, 196608, 262144, 327680, 393216, 458752, 499999]
    if (n - 1) // 2 > len(positives):
        raise ValueError('COMPETENCY_HDB_TOO_MANY_OFFSETS')
    selected = positives[:(n - 1) // 2]
    return sorted([-v for v in selected] + [0] + selected)


def _hdb_prompt(presentation, payoffs):
    """Unified HDB input contract for diagnostic and direct LoRA training."""
    prefix = ('Known payoff values per encounter:\n' +
              ''.join(f'{tag} = {int(payoffs[tag])}\n' for tag in hd.TAGS) +
              'Choose the pure strategy with the higher TOTAL payoff. '
              'If totals are exactly equal, answer TIE.\n')
    return prefix + '\n' + presentation + '\nReturn only HAWK, DOVE, or TIE.'


def _hdb_deck(settings):
    """New balanced HDB pilot, tier structure reused from original teacher.

    Bindings are explicitly furnished because the original teacher hid the
    payoff coefficients, and this T-only model has not received that training.
    These results must not be called canonical hidden-binding HDB certification.
    """
    # Prompt contract is shared with the opt-in HDB training frontier.
    result=[]
    for tier in hd.TIERS:
        for offset in _hdb_offsets(settings['hdb_cases']):
            a=500000+offset; b=500000-offset
            x,y,decision=hd.oracle(a,b,PAYOFFS)
            # A supported legacy syntax mutation: raw numbers with no LogBars.
            presentation=hd.render(tier,a,b,PAYOFFS,full_slots=())
            # Instructor does not supply the answer; only payoff constants.
            prompt=_hdb_prompt(presentation,PAYOFFS)
            result.append({'family':'hdb', 'tier':'HDB'+tier[1:],
                           'offset':offset, 'residual':int(x-y), 'expected':decision,
                           'x':str(x),'y':str(y), 'source':prompt,
                           'margin_ratio':str(abs(x-y)/max(abs(x),abs(y),Decimal(1))),
                           'zero_side': 'positive' if x>y else 'negative' if x<y else 'zero',
                           'presentation':presentation})
    return result


def _chain_deck(args, settings, excluded):
    rows = []
    for rung in (3, 5, 8):
        if rung > args.max_rung: continue
        for i in range(settings['chain_cases']):
            target = [-16, -2, 0, 2, 16, 32][i % 6]
            for attempt in range(150):
                nums = ladder._bounded_numbers(rung, target,
                    max(1, args.operand_limit // 2), args.operand_limit,
                    args.operand_limit, args.seed + 534377 + rung,
                    i + attempt * 41, attempt % 11)
                if nums is not None:
                    row = ladder._case(rung, nums, f'COMP-CHAIN-{rung}-{i}-{attempt}', 'numeric')
                    if frontier._key(row) in excluded: continue
                    row['radius'] = abs(target);row['band'] = 'chain'
                    rows.append(row)
                    excluded.add(frontier._key(row))
                    break
            else:
                raise RuntimeError('COMPETENCY_CHAIN_GENERATION_FAILED')
    return rows


def _deck(args, settings):
    forbidden=_forbidden_keys(args,settings)
    arithmetic={}
    for name in ('near_zero','operand_edge','outer_result'):
        arithmetic[name]=_signed_rows(args,settings,name,forbidden)
        forbidden.update(frontier._key(r) for r in arithmetic[name])
    chains=_chain_deck(args,settings,forbidden)
    hdb=_hdb_deck(settings)
    return {'arithmetic':arithmetic,'chains':chains,'hdb':hdb}


def _digest(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,default=str).encode('utf8')).hexdigest()


def _grade_choice(emission):
    # Never infer a choice from a long narrative or a Qwen PROBLEM: packet.
    text=emission.strip().upper()
    return text if text in ('HAWK','DOVE','TIE') else None


def _cached_greedy(bb, tok, sources, device, max_tokens, max_new_tokens, *, return_metadata=False):
    """Diagnostic-only greedy decoder with incremental KV caching.

    The training and certification decoder is unchanged. Inputs are left padded
    with the same masks and greedy choice as ladder._greedy; decoder receives
    only the last newly generated token after the first forward pass. This
    optimization relies on the causal model's supported past_key_values API.
    """
    import torch
    head = bb.get_output_embeddings()
    decoder = bb.get_decoder()
    if head is None:
        raise RuntimeError('LADDER_LM_HEAD_UNAVAILABLE')
    pieces = [tok(src + '\n', add_special_tokens=False)['input_ids'] for src in sources]
    if any(not p or len(p) + max_new_tokens > max_tokens for p in pieces):
        raise ValueError('LADDER_INFERENCE_CONTEXT_LIMIT')
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    if pad is None:
        raise ValueError('LADDER_NO_PAD_ID')
    size = len(pieces)
    width = max(map(len, pieces))
    ids = torch.full((size, width), int(pad), dtype=torch.long, device=device)
    mask = torch.zeros_like(ids)
    for i, piece in enumerate(pieces):
        ids[i, width-len(piece):] = torch.tensor(piece, device=device)
        mask[i, width-len(piece):] = 1
    past = None
    append = None
    output = [[] for _ in pieces]
    done = [False] * size
    reasons = [None] * size
    eos = tok.eos_token_id
    eos_ids = {int(eos)} if isinstance(eos, int) else set(eos or ())
    # DirectML cannot use PyTorch inference tensors with version counters.
    context = (torch.no_grad if getattr(device, 'type', str(device)).startswith('privateuseone')
               else torch.inference_mode)
    with context():
        for _ in range(max_new_tokens):
            if past is None:
                result = decoder(input_ids=ids, attention_mask=mask,
                                 use_cache=True, return_dict=True)
            else:
                result = decoder(input_ids=append, attention_mask=mask,
                                 past_key_values=past, use_cache=True,
                                 return_dict=True)
            past = getattr(result, 'past_key_values', None)
            if past is None:
                raise NotImplementedError('COMPETENCY_DECODER_NO_KV_CACHE')
            hidden = result.last_hidden_state[:, -1, :]
            weight = getattr(head, 'weight', None)
            logits = head(hidden.to(dtype=weight.dtype if weight is not None else hidden.dtype))
            tokens = logits.argmax(dim=-1).tolist()
            for i, token in enumerate(tokens):
                if done[i]:
                    continue
                if token in eos_ids:
                    done[i] = True
                    reasons[i] = 'eos'
                    continue
                output[i].append(token)
                txt = tok.decode(output[i], skip_special_tokens=True)
                if '\n' in txt or '\r' in txt:
                    done[i] = True
                    reasons[i] = 'newline'
            if all(done):
                break
            append = torch.tensor([[int(pad) if done[i] else int(tokens[i])]
                                   for i in range(size)], device=device)
            active = torch.tensor([[0 if done[i] else 1] for i in range(size)], device=device)
            mask = torch.cat((mask, active), dim=1)
    texts = [tok.decode(row, skip_special_tokens=True) for row in output]
    if return_metadata:
        return [{'text':t, 'finish_reason':reasons[i] or 'length',
                 'generated_tokens':len(output[i])} for i,t in enumerate(texts)]
    return texts


def _diagnostic_greedy(args, *, return_metadata=False):
    if bool(getattr(args, 'competency_legacy_decode', False)):
        if not return_metadata:return ladder._greedy
        return lambda *a:ladder._greedy(*a,return_metadata=True)
    fallback = [False]
    def generate(bb, tok, sources, device, max_tokens, max_new_tokens):
        if fallback[0]:
            return ladder._greedy(bb,tok,sources,device,max_tokens,max_new_tokens,return_metadata=return_metadata)
        try:
            return _cached_greedy(bb,tok,sources,device,max_tokens,max_new_tokens,return_metadata=return_metadata)
        except (TypeError, AttributeError, NotImplementedError) as exc:
            fallback[0] = True
            frontier._emit('QWEN_COMPETENCY_DECODER_FALLBACK', {
                'reason':type(exc).__name__,'detail':str(exc)[:200],
                'decoder':'legacy_uncached'})
            return ladder._greedy(bb,tok,sources,device,max_tokens,max_new_tokens,return_metadata=return_metadata)
    return generate


def _progress(args, phase, family, done, total, *, active=None, started=None):
    frequency=max(1,int(getattr(args,'competency_progress_every',1)))
    batch=max(1,int(args.eval_batch))
    if done < total and (done + batch - 1)//batch % frequency != 0:
        return
    value={'phase':phase,'family':family,'done':done,'total':total}
    if started is not None:
        value['elapsed_seconds']=round(time.perf_counter()-started,2)
    if active is not None:
        value['active_chains']=active
    frontier._emit('QWEN_COMPETENCY_PROGRESS',value)


def _eval_hdb(bb,tok,rows,args,settings,*,greedy=None,progress=None):
    greedy = greedy or ladder._greedy
    out = {}
    for at in range(0,len(rows),args.eval_batch):
        chunk=rows[at:at+args.eval_batch]
        try:
            emitted=greedy(bb,tok,[r['source'] for r in chunk],args.device,
                settings['max_tokens'],settings['max_new_tokens'])
        except ValueError as e:
            if 'LADDER_INFERENCE_CONTEXT_LIMIT' not in str(e):
                raise
            # Isolate longer presentations; mark overflow as untested, not wrong.
            if len(chunk)>1:
                for row in chunk:
                    out.update(_eval_hdb(bb,tok,[row],args,settings,greedy=greedy))
            else:
                out[_digest(chunk[0]['source'])] = {'correct':None, 'emitted':None,
                    'reason':'context_limit', 'parsed':None}
            if progress is not None:
                progress(min(at+len(chunk),len(rows)),len(rows))
            continue
        for row, emission in zip(chunk,emitted):
            parsed=_grade_choice(emission)
            out[_digest(row['source'])]={'correct':parsed == row['expected'],
                'emitted':emission, 'parsed':parsed,'reason': None if parsed else 'format'}
        if progress is not None:
            progress(min(at+len(chunk),len(rows)),len(rows))
    return out


def _eval_arithmetic(bb,tok,rows,args,*,greedy=None,progress=None):
    return frontier._score(bb,tok,rows,args,greedy=greedy,progress=progress) if rows else {}


def _metrics(rows, previous, now, family):
    by = {}
    totals = {'tested':0,'correct':0,'incorrect':0,'not_tested':0,'gained':0,'lost':0,'net':0}
    for r in rows:
        key=frontier._key(r) if family!='hdb' else _digest(r['source'])
        a=previous.get(key,{}).get('correct')
        b=now[key]['correct']
        tier = r['tier'] if family=='hdb' else f'T{r["rung"]:02d}'
        side=(r['zero_side'] if family=='hdb' else 'positive' if r['residual']>0 else
              'negative' if r['residual']<0 else 'zero')
        d=by.setdefault(tier, {'tested':0,'correct':0,'incorrect':0,'not_tested':0,
                'gained':0,'lost':0,'net':0,'zero_side': {}})
        for bucket in (d,totals):
            if b is None:
                bucket['not_tested']+=1
                continue
            bucket['tested']+=1;bucket['correct']+=int(b);bucket['incorrect']+=int(not b)
            if a is not None:
                bucket['gained']+=int(not a and b);bucket['lost']+=int(a and not b)
                bucket['net']=bucket['gained']-bucket['lost']
        if b is not None:
            zs=d['zero_side'].setdefault(side, {'tested':0,'correct':0})
            zs['tested']+=1; zs['correct']+=int(b)
    for obj in [totals,*by.values()]:
        obj['accuracy']=obj['correct']/obj['tested'] if obj['tested'] else None
    return {'total': totals,'by_tier':by}


def _chain_metrics(rows,prev,now):
    groups={}
    for row in rows:
        rung=f'T{row["rung"]:02d}'
        key=frontier._key(row)
        d=groups.setdefault(rung,{'tested':0,'strict_correct':0,'terminal_correct':0,
             'gained':0,'lost':0,'net':0,'first_failure_by_rung':{}})
        a=prev.get(key,{}).get('correct')
        b=bool(now[key]['correct'])
        d['tested']+=1;d['strict_correct']+=int(b)
        d['terminal_correct']+=int(now[key]['terminal_correct'])
        if a is not None:
            d['gained']+=int(not a and b);d['lost']+=int(a and not b)
            d['net']=d['gained']-d['lost']
        if not b:
            reason=now[key].get('first_wrong_rung') or now[key].get('failure_reason') or 'unknown'
            d['first_failure_by_rung'][reason]=d['first_failure_by_rung'].get(reason,0)+1
    for d in groups.values():
        d['strict_accuracy']=d['strict_correct']/d['tested']
        d['terminal_accuracy']=d['terminal_correct']/d['tested']
    return groups


def _exposures(args, steps):
    """Deterministically reconstruct *actual* mixed T minibatch membership.

    Supports existing pre-patch 800-step checkpoints without altering a byte of
    the model's run policy or optimizer.  No HDB rows ever enter this sampler.
    Shared steps are counted once globally, not nine independent GPU budgets.
    """
    totals={f'T{i:02d}':{'examples':0,'steps_with_exposure':0,
             'by_radius':{}} for i in range(args.max_rung+1)}
    radii=frontier._radii(args)
    prior_stage=-1;pool=[]
    for i in range(steps):
        stage=min(len(radii)-1,i*len(radii)//args.frontier_steps)
        if stage!=prior_stage or not pool:
            low=0 if stage==0 else radii[stage-1]+1
            pool=frontier._deck(radii[stage],low,args.frontier_train_cases,args,
                            f'TRAIN-{stage}-C{i//args.frontier_check_every}')
            prior_stage=stage
        batch=frontier._training_batch(pool,args.batch_size,
                                      random.Random(args.seed^(i*104729)))
        present=set()
        for row in batch:
            t=f'T{row["rung"]:02d}'
            totals[t]['examples']+=1
            r=str(row['radius'])
            totals[t]['by_radius'][r]=totals[t]['by_radius'].get(r,0)+1
            present.add(t)
        for t in present:totals[t]['steps_with_exposure']+=1
    return totals


def _rate(metrics, before, after, duration, step_delta, exposure_before, exposure_after, *, trained):
    """Net accuracy transitions and observed acquisition velocity (not causal attribution)."""
    r={}
    for tier,d in metrics['by_tier'].items():
        past=before['by_tier'].get(tier,{}) if before else {}
        # Gains/losses are computed by calling _metrics with previous snapshot.
        gain=d['net']
        old_count=(exposure_before or {}).get(tier,{}).get('examples',0)
        new_count=(exposure_after or {}).get(tier,{}).get('examples',0)
        exposure_delta=max(0,new_count-old_count) if trained else 0
        r[tier]={'correct':d['correct'],'tested':d['tested'],'accuracy':d['accuracy'],
                 'gained':d['gained'],'lost':d['lost'],'net':gain,
                 'training_exposures':exposure_delta,
                 'net_per_100_optimizer_steps':(100*gain/step_delta if step_delta>0 else None),
                 'net_per_training_hour':(3600*gain/duration if duration>0 else None),
                 'net_per_100_exposures':(100*gain/exposure_delta if exposure_delta else None),
                 'trained_in_this_run':trained,
                 'interpretation': ('observational_mixed_T_training' if trained else
                                    'transfer_only_no_HDB_training')}
    return r


def _observe(bb,tok,args,settings,deck,*,phase='current',section_done=None):
    """Track per-population progress; optionally commit a completed section.

    section_done(name, payload, elapsed) persists a family result. It is only
    invoked once all examples in that family have been scored.
    """
    def clock_sync():
        if args.device=='cuda':
            import torch
            torch.cuda.synchronize()
    clock_sync()
    started=time.perf_counter()
    times={}
    arithmetic={}
    greedy=_diagnostic_greedy(args)
    families=[(f,len(rows)) for f,rows in deck['arithmetic'].items()]
    families.extend([('chains',len(deck['chains'])),('hdb',len(deck['hdb']))])
    frontier._emit('QWEN_COMPETENCY_PHASE_START',{
        'phase':phase,'families':dict(families),
        'decoder':'legacy_uncached' if getattr(args,'competency_legacy_decode',False)
                  else 'cached_with_legacy_fallback'})
    for family,rows in deck['arithmetic'].items():
        clock_sync();t=time.perf_counter()
        _progress(args,phase,family,0,len(rows),started=t)
        arithmetic[family]=_eval_arithmetic(bb,tok,rows,args,greedy=greedy,
            progress=lambda done,total,f=family,b=t:_progress(args,phase,f,done,total,started=b))
        clock_sync();times[family]=time.perf_counter()-t
        if section_done:section_done(family,arithmetic[family],times[family])
    clock_sync();t=time.perf_counter()
    _progress(args,phase,'chains',0,len(deck['chains']),started=t)
    chains=frontier._chain_score(bb,tok,deck['chains'],args,greedy=greedy,
        progress=lambda hop,total,active:_progress(args,phase,'chains',hop,total,
                                    active=active,started=t))
    clock_sync();times['chains']=time.perf_counter()-t
    if section_done:section_done('chains',chains,times['chains'])
    clock_sync();t=time.perf_counter()
    _progress(args,phase,'hdb',0,len(deck['hdb']),started=t)
    hdb=_eval_hdb(bb,tok,deck['hdb'],args,settings,greedy=greedy,
        progress=lambda done,total:_progress(args,phase,'hdb',done,total,started=t))
    clock_sync();times['hdb']=time.perf_counter()-t
    if section_done:section_done('hdb',hdb,times['hdb'])
    overall=time.perf_counter()-started
    frontier._emit('QWEN_COMPETENCY_PHASE_DONE',{'phase':phase,
        'elapsed_seconds':round(overall,2),
        'by_family_seconds':{k:round(v,2) for k,v in times.items()}})
    return {'arithmetic':arithmetic,'chains':chains,'hdb':hdb},overall,times


def _check(state,bb,tok,args,out,source,zero):
    """Run periodic / standalone diagnostic in-place; restore LoRA after baseline."""
    frontier._emit('QWEN_COMPETENCY_PREPARE',{'phase':'constructing_disjoint_diagnostic_deck'})
    settings=_settings(args);deck=_deck(args,settings)
    frontier._emit('QWEN_COMPETENCY_DECK_READY',{'arithmetic':{k:len(v) for k,v in deck['arithmetic'].items()},
        'chains':len(deck['chains']),'hdb':len(deck['hdb'])})
    fp=_digest({'settings':settings,'deck':deck})
    report_path=out/'competency_report.json'
    prior=json.loads(report_path.read_text(encoding='utf8')) if report_path.is_file() else None
    if prior and (prior.get('deck_sha256')!=fp or
                  prior.get('source_sha256')!=state['source_sha256']):
        raise RuntimeError('COMPETENCY_DECK_OR_SOURCE_DRIFT')
    step=state['phase_step']
    if prior and prior['step']>step:
        raise RuntimeError('COMPETENCY_CHECKPOINT_REWOUND')
    if prior and prior['step']==step:
        frontier._emit('QWEN_COMPETENCY_UNCHANGED',{'step':step,'report':str(report_path)})
        return prior
    prior_score=prior['latest_score'] if prior else None
    cache_path=out/'competency_baseline_cache.json'
    decoder_contract=('legacy_uncached' if getattr(args,'competency_legacy_decode',False)
                      else 'cached_with_legacy_fallback')
    baseline_cache=(json.loads(cache_path.read_text(encoding='utf8'))
                    if cache_path.is_file() else None)
    if prior is None and baseline_cache and (
            baseline_cache.get('deck_sha256')!=fp or
            baseline_cache.get('source_sha256')!=state['source_sha256'] or
            baseline_cache.get('decoder_contract')!=decoder_contract):
        # A distinct deck/source must never reuse the cached baseline.
        baseline_cache=None
    if prior is None and baseline_cache:
        baseline=baseline_cache['score']
        baseline_secs=baseline_cache['inference_seconds']
        baseline_family_times=baseline_cache['by_family_seconds']
        frontier._emit('QWEN_COMPETENCY_BASELINE_REUSED',{
            'path':str(cache_path),'seconds_saved':round(baseline_secs,2)})
        last_score=baseline;last_step=0;last_training_seconds=0.
    elif prior is None:
        current=zero.lora_state(bb)
        try:
            zero.restore_lora(bb,source['lora'])
            bb.eval()
            baseline, baseline_secs, baseline_family_times=_observe(bb,tok,args,settings,deck,phase='baseline')
        finally:
            zero.restore_lora(bb,current)
        ladder._atomic_json(cache_path,{'deck_sha256':fp,
            'decoder_contract':decoder_contract,
            'source_sha256':state['source_sha256'],
            'score':baseline,'inference_seconds':baseline_secs,
            'by_family_seconds':baseline_family_times})
        last_score=baseline;last_step=0;last_training_seconds=0.
    else:
        baseline=prior['baseline_score'];baseline_secs=prior['baseline_inference_seconds']
        baseline_family_times=prior['baseline_inference_by_family']
        last_score=prior_score;last_step=prior['step']
        last_training_seconds=prior['training_seconds']
    bb.eval()
    latest, inference_seconds, inference_by_family=_observe(bb,tok,args,settings,deck,phase='current')
    interval_steps=step-last_step
    interval_training_seconds=max(0.,state['training_seconds']-last_training_seconds)
    interval_total_seconds=interval_training_seconds+inference_seconds+(baseline_secs if prior is None else 0.)
    frontier._emit('QWEN_COMPETENCY_EXPOSURE_START',{'steps':step})
    observed_exposure=_exposures(args,step)
    previous_exposure=_exposures(args,last_step) if last_step else {}
    from_start={};from_previous={};velocities={}
    for family,rows in deck['arithmetic'].items():
        from_start[family]=_metrics(rows,baseline['arithmetic'][family],latest['arithmetic'][family],family)
        from_previous[family]=_metrics(rows,last_score['arithmetic'][family],latest['arithmetic'][family],family)
        velocities[family]=_rate(from_previous[family], None,None,interval_training_seconds,
                        interval_steps,previous_exposure,observed_exposure,trained=True)
    from_start['hdb']=_metrics(deck['hdb'],baseline['hdb'],latest['hdb'],'hdb')
    from_previous['hdb']=_metrics(deck['hdb'],last_score['hdb'],latest['hdb'],'hdb')
    velocities['hdb']=_rate(from_previous['hdb'],None,None,interval_training_seconds,
                            interval_steps,{}, {},trained=False)
    # Recompute end-to-end velocity with evaluation costs charged to the interval.
    for family,rates in velocities.items():
        for tier,d in rates.items():
            d['net_per_measured_end_to_end_hour']=(
                3600*d['net']/interval_total_seconds if interval_total_seconds>0 else None)
            d['measurement_includes_baseline_scoring']=(prior is None)
    chains_start=_chain_metrics(deck['chains'],baseline['chains'],latest['chains'])
    chains_interval=_chain_metrics(deck['chains'],last_score['chains'],latest['chains'])
    report={'schema':SCHEMA,'source_sha256':state['source_sha256'],
            'deck_sha256':fp,'step':step,'baseline_step':0,'previous_step':last_step,
            'training_seconds':state['training_seconds'],
            'interval_training_seconds':interval_training_seconds,
            'interval_optimizer_steps':interval_steps,
            'baseline_inference_seconds':baseline_secs,
            'baseline_inference_by_family':baseline_family_times,
            'inference_seconds':inference_seconds,
            'inference_seconds_by_family':inference_by_family,
            'interval_total_measured_seconds':interval_total_seconds,
            'training_exposure_cumulative':observed_exposure,
            'hdb_training_exposures':0,
            'interpretation':'T trained; HDB benchmark not present in optimizer training examples; progress is transfer only',
            'from_start':from_start,'from_previous':from_previous,
            'velocity_by_tier':velocities,'chains_from_start':chains_start,
            'chains_from_previous':chains_interval,
            'baseline_score':baseline,'latest_score':latest,
            'case_counts':{'arithmetic':{f:len(rows) for f,rows in deck['arithmetic'].items()},
                           'chains':len(deck['chains']),'hdb':len(deck['hdb'])},
            'hdb_presentation_contract':'balanced self-contained diagnostic with explicit known payoff bindings; not canonical hidden-binding certification'}
    if not (out/'competency_eval_deck.json').exists():
        ladder._atomic_json(out/'competency_eval_deck.json',deck)
    ladder._atomic_json(report_path,report)
    # Compact timeline: results, rates, and config identity, never thousands of
    # repeated source strings or hidden logits.
    frontier._append(out/'competency_history.jsonl',{
        'step':step,'deck_sha256':fp,'interval_training_seconds':interval_training_seconds,
        'interval_steps':interval_steps,'inference_seconds':inference_seconds,
        'inference_seconds_by_family':inference_by_family,
        'from_start':from_start,'from_previous':from_previous,
        'velocity_by_tier':velocities,'chains':chains_start,
        'hdb_training_exposures':0})
    frontier._emit('QWEN_COMPETENCY_CHECK', {
        'step':step,'interval_training_seconds':round(interval_training_seconds,3),
        'interval_steps':interval_steps,
        'inference_seconds':round(inference_seconds,3),
        'inference_seconds_by_family':{k:round(v,3) for k,v in inference_by_family.items()},
        'arithmetic':{name:{'total':obj['total'], 'by_tier':obj['by_tier']}
                      for name,obj in from_start.items() if name!='hdb'},
        'hdb':from_start['hdb'], 'chains':chains_start,
        'report':str(report_path),'hdb_training_exposures':0})
    return report


def run_standalone(args):
    """Evaluate the latest frontier LoRA without performing an optimizer step."""
    import torch
    policy=frontier._policy(args)
    frontier._emit('QWEN_COMPETENCY_START',{'mode':'read_only'})
    source,digest=frontier._load_source(args)
    out=frontier._out(args)
    checkpoint=out/'working.pt'
    if not checkpoint.is_file():
        raise RuntimeError('COMPETENCY_REQUIRES_EXISTING_FRONTIER_WORKING_PT')
    bb,tok,zero,_=frontier._load_model(args,source)
    state,_=frontier._load(checkpoint,bb,zero,policy,digest)
    return _check(state,bb,tok,args,out,source,zero)


def run_at_check(state,bb,tok,args,out,source,zero):
    return _check(state,bb,tok,args,out,source,zero)
