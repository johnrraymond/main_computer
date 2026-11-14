#!/usr/bin/env python3
"""GAMER V1 exact-domain numerical request route with reversible authority.

This is a GAMER CLI inference route, NOT the unrelated CLEF/worker provider.
The original Wide checkpoint and original adapter stay byte-immutable.
Default authority is Wide. Exact mode requires a fresh live serving-route
acceptance attestation bound to the existing successful 160-case shadow.

An admitted prompt must be a canonical two-surface bounded game. Parsing is
used ONLY for admission and post-inference acceptance labels: exact mode
predicts q/S from frozen embedding activations and R/T/P from the frozen
layer-9 payoff heads. Unsupported inputs fail; they never fall back to Wide.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tools import nanojev_gamer_v1_to_numerical_shadow as shadow
from tools import nanojev_gamer_v1_s_payoff_operational_acceptance as s_lab
from tools import nanojev_gamer_v1_exact_belief_lab as exact
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_zero_sign_frob as prompts
from tools import nanojev_two_game_spine_smoke as spine
from tools import nanojev_gamer_v1_c0_sign_head as c0sign
from tools import nanojev_gamer_v1_lora_sign_stress as stress
from tools import nanojev_gamer_v1_compositional_belief_lab as composition

SCHEMA='nanojev.gamer.v1.to-numerical-serving-acceptance.v1'
AUTH_SCHEMA='nanojev.gamer.v1.to-numerical-serving-authority.v1'
ROUTE_SCHEMA='nanojev.gamer.v1.to-numerical-route.v1'
FRESH_DENOMS=(43,45,47,49)
MAX_CASES=256
MAX_BYTES=1024*1024


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _shadow_evidence(path):
    root=Path(path).resolve()
    report_file=root/'final_report.json'
    report=_read_json(report_file)
    gate=report.get('gate',{})
    cand=report.get('candidate',{})
    if (report.get('schema')!=shadow.SCHEMA or
        report.get('status')!='SHADOW_CANDIDATE_PASS' or
        report.get('promotion')!=shadow.PROMOTION or
        gate.get('candidate_bounded_passed') is not True or
        gate.get('complete_zero_line') is not True or
        gate.get('original_wide_unchanged') is not True or
        cand.get('passed') is not True or
        cand.get('zero_triples',{}).get('strict_full_triples')!=32 or
        report.get('wide_shadow',{}).get('cases')!=160 or
        report.get('wide_shadow',{}).get('candidate_exact')!=160 or
        report.get('production_output_authority')!='existing_to_numerical_wide' or
        report.get('forward_pass_strategy') not in (
            'SEPARATE_FROZEN_ADAPTER_FORWARD_PASSES','SAME_FROZEN_FORWARD_PASS')):
        raise ValueError('GAMER_ROUTE_REQUIRES_COMPLETE_BLOCKED_160_CASE_SHADOW')
    details=Path(report['case_detail']['path']).resolve()
    if (not details.is_relative_to(root) or not details.is_file() or
        token._sha(details)!=report['case_detail']['sha256']):
        raise ValueError('GAMER_ROUTE_SHADOW_DETAIL_CHANGED')
    rows=[json.loads(line) for line in details.read_text(encoding='utf-8').splitlines()]
    if (len(rows)!=160 or len({r['case_id'] for r in rows})!=160 or
        not all(r.get('candidate_exact') is True for r in rows)):
        raise ValueError('GAMER_ROUTE_SHADOW_DETAIL_INCOMPLETE')
    return {'root':root,'report':report,'report_path':report_file,
            'sha256':token._sha(report_file),'rows':rows}


def _context(output,closure,evidence):
    src=shadow._source(closure)
    proof=_shadow_evidence(evidence)
    model_id,snapshot,cfg=s_lab._model_inputs(src['source'])
    payload=stress._verify_snapshot(snapshot,composition.STEP)
    wide_path=shadow._select_wide(output)
    wide_adapter,wide_sha,strategy=shadow._resolve_adapter(wide_path,payload,True)
    wide=shadow._wide(wide_path,model_id,wide_sha)
    wide['original_adapter']=wide_adapter
    wide['original_adapter_sha256']=wide_sha
    rep=proof['report']
    if (rep['s_closure_sha256']!=src['report_sha256'] or
        rep['s_checkpoint_sha256']!=src['s_sha256'] or
        rep['frozen_lora_sha256']!=payload or
        rep['wide_sha256']!=wide['sha256'] or
        rep['wide_adapter_sha256']!=wide_sha or
        rep['forward_pass_strategy']!=strategy or
        Path(rep['wide_checkpoint']).resolve()!=wide_path):
        raise ValueError('GAMER_ROUTE_SHADOW_CHECKPOINT_LINEAGE_MISMATCH')
    # Verify the exact accepted deck; no role, order or prompt may be substituted.
    deck,tags=shadow._deck(src)
    from tools.nanojev_gamer_v1_sign_information_probe import deck_hash
    if deck_hash(deck)!=rep['deck_sha256']:
        raise ValueError('GAMER_ROUTE_SHADOW_DECK_CHANGED')
    return {'src':src,'proof':proof,'model_id':model_id,'snapshot':snapshot,
            'config':cfg,'payload':payload,'wide':wide,'wide_sha':wide_sha,
            'wide_adapter':wide_adapter,'strategy':strategy,'deck':deck,'tags':tags,
            'root':Path(output).resolve()}


def _admit(prompt):
    """Canonical template, range and width admission; NEVER a predictor."""
    if not isinstance(prompt,str) or len(prompt.encode('utf-8'))>4096:
        raise ValueError('GAMER_ROUTE_UNSUPPORTED_PROMPT_LENGTH')
    try:
        spans=token._spans(prompt)
        qtext=prompt[slice(*spans['belief'][0])]
        ptexts=[prompt[slice(*x)] for x in spans['payoffs']]
        if not ('/' in qtext and all(x.isascii() and x.isdecimal() for x in qtext.split('/'))):
            raise ValueError('GAMER_ROUTE_REQUIRES_CANONICAL_RATIONAL_Q')
        parts=qtext.split('/')
        if len(parts)!=2 or not all(1<=len(v)<=6 for v in parts):
            raise ValueError('GAMER_ROUTE_UNSUPPORTED_RATIONAL_COMPONENT_WIDTH')
        if any(not x.isascii() or not x.isdecimal() or not 1<=len(x)<=3 for x in ptexts):
            raise ValueError('GAMER_ROUTE_UNSUPPORTED_PAYOFF_FORMAT')
        q=Fraction(qtext)
        values=[int(v) for v in ptexts]
        if not 0<q<1 or not all(100<=x<=140 for x in values):
            raise ValueError('GAMER_ROUTE_NUMERICAL_DOMAIN_REFUSED')
        stage=spine.Stage(*(Fraction(v) for v in values))
        renderers={'spine_belief':prompts._spine_belief_prompt,
                   'game_prose_belief':prompts._game_prose_belief_prompt}
        surfaces=[k for k,fn in renderers.items() if prompt==fn(stage,q)]
        if len(surfaces)!=1:
            raise ValueError('GAMER_ROUTE_UNSUPPORTED_PROMPT_SURFACE')
        return surfaces[0]
    except (ZeroDivisionError,TypeError,IndexError) as exc:
        raise ValueError('GAMER_ROUTE_MALFORMED_NUMERICAL_PROMPT') from exc


def _cases(requests):
    if not 1<=len(requests)<=MAX_CASES:
        raise ValueError('GAMER_ROUTE_REQUEST_COUNT_OUT_OF_BOUNDS')
    cases=[];surfaces=[];seen=set()
    for r in requests:
        if not isinstance(r,dict) or set(r)!={'id','prompt'}:
            raise ValueError('GAMER_ROUTE_REQUEST_SCHEMA_INVALID')
        key,prompt=r['id'],r['prompt']
        if not isinstance(key,str) or not key or len(key)>128 or key in seen:
            raise ValueError('GAMER_ROUTE_DUPLICATE_OR_INVALID_REQUEST_ID')
        seen.add(key)
        surfaces.append(_admit(prompt))
        cases.append(SimpleNamespace(id=key,prompt=prompt))
    return cases,surfaces


def _candidate(ctx,cases,device,batch):
    """Live candidate inference only. No oracle, parsed operand, or target used."""
    _,tok,model=gamer.model_components(ctx['model_id'],device,
                                       adapter_dir=ctx['snapshot'])
    try:
        groups,views=s_lab._live_capture(model,tok,cases,ctx['config'],device,batch)
        qhat,shat=s_lab._predict_live_groups(groups,ctx['src']['source']['digit_model'])
        pay=s_lab._new_checkpoint_predictions(ctx['src']['source'],views)
    finally:
        del model,tok
        import gc
        gc.collect()
        import torch
        if torch.cuda.is_available():torch.cuda.empty_cache()
    values=[]
    for i in range(len(cases)):
        if qhat[i] is None or shat[i] is None:
            raise ValueError(f'GAMER_ROUTE_CANDIDATE_UNSUPPORTED_OPERAND: {cases[i].id}')
        q=qhat[i]
        if not isinstance(q,Fraction) or not 0<q<1:
            raise ValueError('GAMER_ROUTE_CANDIDATE_BAD_FRACTION')
        r,t,p=(int(pay[k][i]) for k in ('R','T','P'))
        s=int(shat[i])
        if not all(100<=x<=140 for x in (r,s,t,p)):
            raise ValueError('GAMER_ROUTE_CANDIDATE_OUT_OF_CERTIFIED_PAYOFF_RANGE')
        values.append(q*(t-r)+(1-q)*(p-s))
    return values


def _legacy(ctx,cases,device,batch,eval_batch):
    wide=ctx['wide']
    features=shadow._wide_original_features(ctx['model_id'],ctx['wide_adapter'],cases,
        device,batch,ctx['config']['max_length'])
    return shadow._wide_predict(wide,features,device,eval_batch)


def _fresh_deck():
    """New locked acceptance cases, disjoint from the prior 160 prompt hashes."""
    cases=[];tags=[]
    for i,d in enumerate(FRESH_DENOMS):
        b=d//2;a=d-b
        stage=spine.Stage(Fraction(134),Fraction(109),Fraction(134-a),Fraction(109+b))
        center=Fraction(b,d);offset=Fraction(1,300*d)
        for edit,q in (('negative',center+offset),('zero',center),
                       ('positive',center-offset)):
            for surface,fn in (('spine_belief',prompts._spine_belief_prompt),
                               ('game_prose_belief',prompts._game_prose_belief_prompt)):
                target=stage.margin(q)
                if (target>0)-(target<0)!={'negative':-1,'zero':0,'positive':1}[edit]:
                    raise ValueError('GAMER_ROUTE_FRESH_ZERO_CONSTRUCTION_INVALID')
                key=f'live-route.{i:02d}.{edit}.{surface}'
                cases.append(SimpleNamespace(id=key,prompt=fn(stage,q),target=target))
                tags.append({'panel':'zero_anchor','profile':f'live-route.{i:02d}',
                             'surface':surface,'edit':edit,'id':key})
    assert len(cases)==24
    return cases,tags


def _attestation(ctx,path):
    root=Path(path).resolve()
    file=root/'final_report.json'
    if not file.is_file():
        raise ValueError('GAMER_ROUTE_ACCEPTANCE_MISSING_OR_STALE')
    report=_read_json(file)
    if (report.get('schema')!=SCHEMA or report.get('status')!='PASS_LIVE_ROUTE_ACCEPTANCE' or
        report.get('gate',{}).get('passed') is not True or
        report.get('gate',{}).get('shadow_160_exact') is not True or
        report.get('gate',{}).get('zero_triples_40_of_40') is not True or
        report.get('gate',{}).get('legacy_rollback_replay') is not True or
        report.get('source',{}).get('shadow_sha256')!=ctx['proof']['sha256'] or
        report.get('source',{}).get('closure_sha256')!=ctx['src']['report_sha256'] or
        report.get('source',{}).get('wide_sha256')!=ctx['wide']['sha256'] or
        report.get('source',{}).get('candidate_adapter_sha256')!=ctx['payload'] or
        report.get('source',{}).get('s_checkpoint_sha256')!=ctx['src']['s_sha256'] or
        report.get('capture',{}).get('cases')!=184 or
        report.get('capture',{}).get('exact')!=184):
        raise ValueError('GAMER_ROUTE_ACCEPTANCE_MISSING_OR_STALE')
    return report,token._sha(file)


def _guard_destination(ctx,target):
    path=Path(target).resolve()
    for root in (ctx['root'],ctx['src']['root'],ctx['src']['source']['root'],
                 ctx['proof']['root']):
        if path==root or path.is_relative_to(root):
            raise ValueError('GAMER_ROUTE_OUTPUT_MUST_NOT_OVERWRITE_SOURCE_ARTIFACTS')
    return path


def accept(args):
    ctx=_context(args.output,args.shadow_closure,args.shadow_evidence)
    out=_guard_destination(ctx,args.route_report)
    if out.exists():raise FileExistsError('GAMER_ROUTE_ACCEPTANCE_OUTPUT_EXISTS')
    old_deck=ctx['deck'];fresh,tags=_fresh_deck()
    ids={x.prompt for x in old_deck}
    if any(x.prompt in ids for x in fresh):
        raise ValueError('GAMER_ROUTE_FRESH_CASE_REUSES_SHADOW_PROMPT')
    all_cases=old_deck+fresh
    _cases([{'id':x.id,'prompt':x.prompt} for x in all_cases])
    print('GAMER ROUTE ACCEPTANCE: 160 shadow prompts + 24 NEW zero-neighbors',flush=True)
    # The inference interfaces NEVER receive oracle targets, including during
    # acceptance. Expected labels are used strictly after both predictions.
    blind=[SimpleNamespace(id=c.id,prompt=c.prompt) for c in all_cases]
    result=_candidate(ctx,blind,args.device,args.capture_batch)
    wide=_legacy(ctx,blind[:160],args.device,args.capture_batch,args.eval_batch)
    # Candidate predictions are complete before any labels are examined.
    oracle=[x.target for x in all_cases]
    exact_flags=[a==b for a,b in zip(result,oracle)]
    legacy_valid=(len(wide)==160 and np.isfinite(wide).all())
    rows=ctx['proof']['rows']
    expected_by_id={r['case_id']:r for r in rows}
    comparison=[]
    for i,c in enumerate(old_deck):
        recorded=expected_by_id.get(c.id)
        expected=recorded and recorded.get('candidate_result')
        # The previously measured result is a control, never a predictor.
        comparison.append(bool(recorded and
            recorded.get('prompt_sha256')==gamer.sha_bytes(c.prompt.encode()) and
            expected==str(result[i]) and
            math.isclose(float(wide[i]),float(recorded['production_wide_result']),
                         rel_tol=0,abs_tol=1e-5)))
    fresh_triples=[]
    for j in range(0,24,6):
        for surface_offset in (0,1):
            indexes=[160+j+k+surface_offset for k in (0,2,4)]
            group=[result[i] for i in indexes]
            targets=[oracle[i] for i in indexes]
            fresh_triples.append(group==targets and len(group)==3 and
                                 group[0]<0 and group[1]==0 and group[2]>0)
    old_triples=ctx['proof']['report']['candidate']['zero_triples']['strict_full_triples']
    # Recomputed 160 prompts match the immutable prior shadow case records.
    exact_160=all(exact_flags[:160]) and all(comparison)
    zero_ok=old_triples==32 and exact_160 and all(fresh_triples)
    old_wide_rows=[float(expected_by_id[c.id]['production_wide_result']) for c in old_deck]
    rollback_ok=legacy_valid and all(math.isclose(float(a),b,rel_tol=0,abs_tol=1e-5)
                                     for a,b in zip(wide,old_wide_rows))
    accepted=(all(exact_flags) and exact_160 and zero_ok and rollback_ok)
    shadow._verify_unchanged(ctx['src'],ctx['wide'])
    out.mkdir(parents=True,exist_ok=False)
    report={'schema':SCHEMA,
        'status':'PASS_LIVE_ROUTE_ACCEPTANCE' if accepted else 'FAIL_LIVE_ROUTE_ACCEPTANCE',
        'source':{'shadow_sha256':ctx['proof']['sha256'],
                  'closure_sha256':ctx['src']['report_sha256'],
                  'wide_sha256':ctx['wide']['sha256'],
                  'candidate_adapter_sha256':ctx['payload'],
                  'wide_adapter_sha256':ctx['wide_sha'],
                  's_checkpoint_sha256':ctx['src']['s_sha256']},
        'capture':{'cases':len(all_cases),'exact':sum(exact_flags),
                   'fresh_cases':len(fresh),'fresh_exact':sum(exact_flags[160:]),
                   'shadow_case_replay_exact':sum(comparison),
                   'fresh_zero_triples':sum(fresh_triples),
                   'prior_zero_triples':old_triples},
        'gate':{'passed':bool(accepted),'shadow_160_exact':bool(exact_160),
                'zero_triples_40_of_40':bool(zero_ok),
                'legacy_rollback_replay':bool(rollback_ok)},
        'authority_after_test':'wide (not modified)',
        'promotion':'BLOCKED_UNTIL_EXPLICIT_ROUTE_ACTIVATION',
        'limitations':'Only the bounded canonical two-surface GAME numerical inference route is certified. Not the CLEF/worker service or unbounded game input.'}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER ROUTE ACCEPTANCE',report['status'],out/'final_report.json',flush=True)
    return report


def _state_path(path,root):
    if path is None:
        path=root.parent/'c0-to-numerical-authority.json'
    p=Path(path).resolve()
    if p==root or p.is_relative_to(root):
        raise ValueError('GAMER_ROUTE_AUTHORITY_OUTSIDE_FROZEN_RUN_REQUIRED')
    if p.parent != root.parent:
        raise ValueError('GAMER_ROUTE_AUTHORITY_MUST_BE_IN_RUN_PARENT')
    return p


def _load_state(path):
    if not path.exists():return {'schema':AUTH_SCHEMA,'authority':'wide','revision':0}
    obj=_read_json(path)
    if (obj.get('schema')!=AUTH_SCHEMA or obj.get('authority') not in ('wide','exact') or
        type(obj.get('revision')) is not int or obj['revision']<0):
        raise ValueError('GAMER_ROUTE_AUTHORITY_STATE_INVALID')
    return obj


def activate(args):
    ctx=_context(args.output,args.shadow_closure,args.shadow_evidence)
    proof,sha=_attestation(ctx,args.route_report)
    path=_state_path(args.authority_file,ctx['root'])
    _guard_destination(ctx,path)
    prior=_load_state(path)
    state={'schema':AUTH_SCHEMA,'authority':'exact','revision':prior['revision']+1,
           'acceptance_report':str((Path(args.route_report)/'final_report.json').resolve()),
           'acceptance_sha256':sha,
           'shadow_sha256':ctx['proof']['sha256'],
           'wide_sha256':ctx['wide']['sha256'],
           'candidate_adapter_sha256':ctx['payload'],
           'source_run':str(ctx['root'])}
    gamer.atomic_json(path,state)
    print('GAMER ROUTE AUTHORITY ACTIVATED: exact',path,flush=True)
    return state


def rollback(args):
    root=Path(args.output).resolve()
    path=_state_path(args.authority_file,root)
    prior=_load_state(path)
    state={'schema':AUTH_SCHEMA,'authority':'wide','revision':prior['revision']+1,
           'source_run':str(root)}
    gamer.atomic_json(path,state)
    print('GAMER ROUTE AUTHORITY ROLLED BACK: wide',path,flush=True)
    return state


def _requests(args):
    if args.route_demo:
        if args.route_request_file:
            raise ValueError('GAMER_ROUTE_DEMO_AND_REQUEST_FILE_MUTUALLY_EXCLUSIVE')
        deck,_=s_lab._operational_deck()
        examples=(deck[0],deck[2],deck[4],deck[6])
        return [{'id':c.id,'prompt':c.prompt} for c in examples]
    if args.route_request_file is None:
        raise ValueError('GAMER_ROUTE_REQUEST_FILE_REQUIRED')
    path=Path(args.route_request_file)
    if path.stat().st_size>MAX_BYTES:
        raise ValueError('GAMER_ROUTE_REQUEST_FILE_TOO_LARGE')
    content=path.read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in content if line.strip()]


def route(args):
    root=Path(args.output).resolve()
    state=_load_state(_state_path(args.authority_file,root))
    if 'source_run' in state and Path(state['source_run']).resolve()!=root:
        raise ValueError('GAMER_ROUTE_AUTHORITY_BOUND_TO_DIFFERENT_RUN')
    req=_requests(args)
    cases,surfaces=_cases(req)  # fail before loading GPU or producing output
    ctx=_context(args.output,args.shadow_closure,args.shadow_evidence)
    if state['authority']=='exact':
        att_path=Path(state.get('acceptance_report',''))
        if not att_path.is_file() or token._sha(att_path)!=state.get('acceptance_sha256'):
            raise ValueError('GAMER_ROUTE_ACTIVE_ATTESTATION_CHANGED')
        _,sha=_attestation(ctx,att_path.parent)
        if (sha!=state['acceptance_sha256'] or
            state.get('shadow_sha256')!=ctx['proof']['sha256'] or
            state.get('wide_sha256')!=ctx['wide']['sha256'] or
            state.get('candidate_adapter_sha256')!=ctx['payload']):
            raise ValueError('GAMER_ROUTE_ACTIVE_LINEAGE_CHANGED')
        predicted=_candidate(ctx,cases,args.device,args.capture_batch)
    else:
        predicted=_legacy(ctx,cases,args.device,args.capture_batch,args.eval_batch)
    shadow._verify_unchanged(ctx['src'],ctx['wide'])
    records=[]
    for i,(r,surface,value) in enumerate(zip(req,surfaces,predicted)):
        if state['authority']=='exact':
            if not isinstance(value,Fraction):raise ValueError('GAMER_ROUTE_EXACT_NON_RATIONAL')
            numeric=str(value)
            sign=(value>0)-(value<0)
        else:
            numeric=None
            if not math.isfinite(float(value)):
                raise ValueError('GAMER_ROUTE_WIDE_NONFINITE')
            sign=(float(value)>0)-(float(value)<0)
        records.append({'schema':ROUTE_SCHEMA,'id':r['id'],'surface':surface,
             'prompt_sha256':gamer.sha_bytes(r['prompt'].encode('utf-8')),
             'authority':state['authority'],'authority_revision':state['revision'],
             'exact_rational':numeric if state['authority']=='exact' else None,
             'wide_float':float(value) if state['authority']=='wide' else None,
             'sign':sign,'candidate_adapter_sha256':ctx['payload'] if state['authority']=='exact' else None,
             'wide_sha256':ctx['wide']['sha256'] if state['authority']=='wide' else None})
    if args.route_response_file:
        dest=_guard_destination(ctx,args.route_response_file)
        if dest.exists():raise FileExistsError('GAMER_ROUTE_RESPONSE_EXISTS')
        dest.parent.mkdir(parents=True,exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=dest.parent,delete=False) as tmp:
            tmp.write(''.join(gamer.canonical(rec)+'\n' for rec in records))
            tmp_path=Path(tmp.name)
        os.replace(tmp_path,dest)
    for row in records:print('GAMER_ROUTE_RESULT '+gamer.canonical(row),flush=True)
    return records


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('route-accept','route-activate','route-rollback','route'))
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--shadow-closure',type=Path)
    parser.add_argument('--shadow-evidence',type=Path)
    parser.add_argument('--route-report',type=Path)
    parser.add_argument('--authority-file',type=Path)
    parser.add_argument('--route-demo',action='store_true')
    parser.add_argument('--route-request-file',type=Path)
    parser.add_argument('--route-response-file',type=Path)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--capture-batch',type=int,default=2)
    parser.add_argument('--eval-batch',type=int,default=64)
    a=parser.parse_args(argv)
    if a.capture_batch<1 or a.eval_batch<1:
        raise ValueError('GAMER_ROUTE_INVALID_BATCH')
    if a.stage=='route-rollback':return rollback(a)
    if not a.shadow_closure or not a.shadow_evidence:
        raise ValueError('GAMER_ROUTE_CLOSURE_AND_SHADOW_EVIDENCE_REQUIRED')
    if a.stage in ('route-accept','route-activate') and not a.route_report:
        raise ValueError('GAMER_ROUTE_REPORT_REQUIRED')
    return {'route-accept':accept,'route-activate':activate,'route':route}[a.stage](a)

if __name__=='__main__':main()
