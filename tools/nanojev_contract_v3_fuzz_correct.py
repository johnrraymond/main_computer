#!/usr/bin/env python3
"""Fuzz-and-correct the live V3 language head through verified meta-instructions.

Experimental inference-time meta-update, NOT weight training. A failed original
answer triggers local semantic-preserving and contrast fuzz probes; fixed
allowlisted meta-instructions compete on training probes. Winner must survive
held-out *local mutations* before optional promotion. The authored reference
never enters model prompts. An unsafe guess is never turned into a contract by
postprocessing. No keys, deployment, RPC or frozen compiler edits.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from tools import nanojev_contract_v3_evidence_reconcile as evidence
from tools import nanojev_contract_v3_generalization_eval as corpus_io
from tools import nanojev_contract_v3_guarded_single_pass as guarded
from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_make_fresh_challenge as challenge
from tools import nanojev_contract_v3_meta_prompts as meta
from tools import nanojev_contract_v3_source_fidelity as fidelity
from tools import nanojev_contract_v3_governance_language as gov
from tools import nanojev_contract_v3_governance_verifier as verifier

REPORT_SCHEMA='nanojev.contract.v3.fuzz_correct.v1'
MODEL_RETRIES_PER_PROBE=1
TRAIN_IDS=('original','blank_line_invariance')
HOLDOUT_IDS=('trailing_space_invariance','review_window_contrast','milestone_deadline_contrast')

class FuzzCorrectError(Exception):
    pass


def _sha(text:str)->str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _now()->str:
    return datetime.now(timezone.utc).isoformat()


def _clone(case:dict,ident:str,source:str,gold:dict|None=None,changed_path:str|None=None)->dict:
    return {'id':ident,'request':source,'gold':deepcopy(case['gold'] if gold is None else gold),
            'expected':case['expected'],'expected_refusal_topic':case['expected_refusal_topic'],
            'changed_path':changed_path,'request_sha256':_sha(source)}


def _change_one_day(source:str,regex:str)->tuple[str,re.Match|None]:
    pattern=re.compile(regex,re.I|re.M)
    matches=list(pattern.finditer(source))
    if not matches:return source,None
    m=matches[0]
    old=int(m.group('days'))
    # Do not replace a huge or invalid term. Stay inside bounded V3 durations.
    if old>200000:return source,None
    a,b=m.span('days')
    return source[:a]+str(old+1)+source[b:],m


def make_probes(case:dict)->list[dict]:
    """Generate 2 pure invariances + 2 explicit one-field semantic contrasts.

    No random guesses, duplicate source facts, paraphrase rewriting or inconsistent
    new gold. Contrast fixtures are regenerated, not manually post-hoc labeled.
    """
    source=case['request'].strip()+'\n'
    probes=[_clone(case,'original',source)]
    lines=source.splitlines()
    probes.append(_clone(case,'blank_line_invariance','\n'.join(
        item for line in lines for item in (line,''))+'\n'))
    probes.append(_clone(case,'trailing_space_invariance','\n'.join(line+' ' for line in lines)+'\n'))
    if case['expected']=='contract':
        mod,m=_change_one_day(source,r'^(?P<prefix>The review window (?:is|lasts)\s+)(?P<days>\d+)(?P<suffix>\s+days?\b)')
        if m is not None:
            gold=deepcopy(case['gold']);gold['policy']['review_seconds']+=86400
            try:gov.validate(gold)
            except Exception:pass
            else:probes.append(_clone(case,'review_window_contrast',mod,gold,'policy.review_seconds'))
        mod,m=_change_one_day(source,r'^The\s+(?P<ident>[a-z][a-z0-9_]*)\s+(?:deadline\s+is|delivery\s+is\s+due\s+within)\s+(?P<days>\d+)\s+days?\b')
        if m is not None:
            gold=deepcopy(case['gold']);ident=m.group('ident')
            changed=None
            for item in gold['milestones']:
                if item['id']==ident:
                    item['deadline_seconds']+=86400
                    changed='milestones.'+ident+'.deadline_seconds'
                    break
            if changed:
                try:gov.validate(gold)
                except Exception:pass
                else:probes.append(_clone(case,'milestone_deadline_contrast',mod,gold,changed))
    # All fuzz probes must remain analyzable, with no accidental source conflicts
    # and no changes to the expected truth status. Invalid input remains invalid.
    base_issue_codes={item['code'] for item in fidelity.audit_source(source)['issues']}
    for p in probes:
        if p['id']!='original':
            observed={item['code'] for item in fidelity.audit_source(p['request'])['issues']}
            if observed!=base_issue_codes:raise FuzzCorrectError('Fuzzer altered the source issue class')
        if p['gold'] is not None:
            expected=gov.validate(p['gold'])
            assessment=fidelity.audit_candidate(p['request'],expected)
            if assessment['issues']:
                raise FuzzCorrectError('Fuzzer generated an inconsistent positive case: '+p['id'])
    return probes


def _diff_paths(a:dict,b:dict)->list[str]:
    left=corpus_io._flatten(a);right=corpus_io._flatten(b)
    return sorted(k for k in set(left)|set(right) if left.get(k)!=right.get(k))


def evaluate_response(probe:dict, raw:str)->dict:
    """Grade one proposed answer; never correct the proposed IR with reference gold.

    Read authored gold ONLY for measurement after source/evidence/graph gating.
    """
    source=probe['request']
    result={'outcome':'MODEL_OUTPUT_INVALID','correct':False,'safe':False,
            'source_guard_blocked':False,'model_correct':False,
            'evidence_recovered':[],'blockers':[],'source_sha256':_sha(source),
            'model_response_sha256':_sha(raw)}
    audit=fidelity.audit_source(source)
    if audit['issues']:
        result.update(outcome='SOURCE_GUARD_BLOCKED_BEFORE_MODEL',safe=True,
                      source_guard_blocked=True,blockers=audit['issues'])
        return result
    prepared,attribute=evidence.reconcile(source,raw)
    result['citation_attribution']=attribute
    try:
        spec,missing=bridge.interpret(source,prepared)
        if missing:
            result['clarification_needed']=missing
            if probe['expected']=='needs_clarification':
                correct=corpus_io._topic_matches(missing,probe['expected_refusal_topic'])
                result.update(outcome='CORRECT_TARGETED_REFUSAL' if correct else 'WRONG_REFUSAL_REASON',
                              correct=correct,safe=True,model_correct=correct)
            else:
                result.update(outcome='FALSE_REFUSAL',safe=True)
            return result
        cand=fidelity.audit_candidate(source,spec,source_audit=audit)
        if cand['issues']:
            result.update(outcome='SOURCE_CANDIDATE_MISMATCH_BLOCKED',safe=True,
                          blockers=cand['issues'])
            return result
        graph=verifier.verify(spec)
        if not graph.get('emission_authorized_by_graph_audit'):
            result.update(outcome='V3_GRAPH_BLOCKED',safe=True,
                          blockers=[{'stage':'graph','reason':graph.get('status')}])
            return result
        result['normalized_ir_sha256']=_sha(bridge.canonical(spec))
        if probe['expected']!='contract':
            result.update(outcome='UNSAFE_ACCEPTANCE',safe=False)
            return result
        correct=(spec==gov.validate(probe['gold']))
        result.update(outcome='CORRECT_CONTRACT' if correct else 'INCORRECT_CONTRACT',
                      correct=correct,safe=correct,model_correct=correct,
                      differing_paths=[] if correct else _diff_paths(spec,probe['gold']))
        return result
    except bridge.BridgeError as exc:
        result['blockers']=[{'stage':exc.stage,'reason':exc.reason[:350]}]
        result['safe']=True
        return result
    except Exception as exc:
        # Unanticipated errors are not a model score, not a correct repair.
        result.update(outcome='EVALUATOR_EXCEPTION_NOT_SCORED',correct=None,safe=False,
                      blockers=[{'stage':'evaluator','reason':type(exc).__name__+': '+str(exc)[:250]}])
        return result



def targeted_feedback(original:dict, source:str)->str:
    """Feedback derived from observed source/citation errors, NEVER reference gold.

    A targeted correction can repair one answer, but it is not a transferable
    meta-update and is never promoted as a general prompt by this function.
    """
    lines=source.splitlines()
    notes=[]
    attr=original.get('citation_attribution') or {}
    for path in attr.get('missing_paths',[])[:12]:
        notes.append('Missing source citation for field '+str(path))
    for path in attr.get('extra_paths',[])[:12]:
        notes.append('Unexpected source citation key '+str(path))
    for issue in original.get('blockers',[])[:8]:
        if not isinstance(issue,dict):continue
        if issue.get('code')=='CANDIDATE_SOURCE_VALUE_MISMATCH':
            n=(issue.get('lines') or [None])[0]
            if isinstance(n,int) and 1<=n<=len(lines):
                notes.append('Proposed field '+str(issue.get('path'))+
                             ' did not agree with literal source line: '+lines[n-1].strip()[:300])
        else:
            notes.append('Last attempt was rejected at '+str(issue.get('stage',issue.get('code','output')))+
                         ': '+str(issue.get('reason',issue.get('detail','invalid output')))[:160])
    if original.get('outcome')=='FALSE_REFUSAL':
        for claim in original.get('clarification_needed',[])[:8]:
            notes.append('Recheck whether this claimed missing term is actually explicit: '+claim[:160])
    if not notes:
        notes.append('Re-derive the answer from the complete original source; do not reuse the prior contract template.')
    return ('Your immediately preceding answer was NOT accepted. Here are observed failures '
            '(not a substitute contract, not ground-truth IR):\n'+
            '\n'.join('- '+note for note in notes)+
            '\nRe-read all original contract clauses. Produce a new answer from the source '
            'with correct, literal, per-field quotations. Never invent absent values; '
            'if there is an actual conflict, name it precisely.')


def _score(results:list[dict])->dict:
    counted=[x for x in results if x.get('correct') is not None]
    return {'evaluated':len(counted), 'correct':sum(x['correct'] for x in counted),
            'unsafe_acceptances':sum(x['outcome']=='UNSAFE_ACCEPTANCE' for x in counted),
            'gated':sum(x['outcome'] in ('SOURCE_GUARD_BLOCKED_BEFORE_MODEL','SOURCE_CANDIDATE_MISMATCH_BLOCKED','V3_GRAPH_BLOCKED') for x in counted),
            'outcomes':{x['probe_id']:x['outcome'] for x in results}}


def _favourable(original:dict,trial:dict)->bool:
    return trial['correct'] is True or (original['correct'] is not True and trial['safe'] is True and trial['outcome']!='UNSAFE_ACCEPTANCE')


def execute(*,case_id:str,output:Path, model:str=bridge.DEFAULT_MODEL,
            endpoint:str=bridge.MODEL_ENDPOINT,timeout:float=600,
            corpus:Path=challenge.OUT,corpus_sha256:str|None=None,
            infer:Callable[[str,str|None],str]|None=None,
            baseline_response:Path|None=None,
            profiles:list[str]|None=None,
            promote:bool=False,emit:bool=False)->dict:
    """Fuzz the live model, attempt meta-update, test held-out local mutations.

    All model calls independent, temperature=0; feedback never contains gold.
    `infer` is test injection only and cannot create proof of live inference.
    """
    output=Path(output).resolve()
    if output.exists():raise FuzzCorrectError('Refusing to overwrite the original experiment')
    work=output.parent/(output.stem+'.cases')
    if work.exists():raise FuzzCorrectError('Refusing existing experiment workspace')
    _,cases,corpus_hash=corpus_io.validate_corpus(corpus)
    pinned=corpus_sha256
    if pinned is None and Path(corpus).resolve()==challenge.OUT.resolve():
        pinned=hashlib.sha256(challenge.OUT.read_bytes()).hexdigest()
    # Any custom corpus must be pinned explicitly to avoid answer shopping.
    if not pinned or corpus_hash!=pinned:
        raise FuzzCorrectError('Benchmark corpus is unpinned or modified')
    case=next((c for c in cases if c['id']==case_id),None)
    if case is None:raise FuzzCorrectError('Unknown case ID: '+case_id)
    selected=profiles or list(meta.PROFILES)
    if not selected or len(set(selected))!=len(selected) or any(x not in meta.PROFILES for x in selected):
        raise FuzzCorrectError('Profiles must be nonempty distinct allowlisted names')
    bridge.check_kernel()
    probes=make_probes(case)
    lookup={p['id']:p for p in probes}
    train=[p for p in probes if p['id'] in TRAIN_IDS]
    holdout=[p for p in probes if p['id'] not in TRAIN_IDS]
    rows=[];calls=0;incomplete=False
    targeted=None
    work.mkdir(parents=True)
    def run(probe:dict,profile:str|None,*,baseline:bool=False,feedback_override:str|None=None)->dict:
        nonlocal calls,incomplete
        folder=work/(profile or 'baseline')/probe['id']
        folder.mkdir(parents=True,exist_ok=False)
        source=probe['request']
        (folder/'request.txt').write_text(source,encoding='utf-8')
        # A demonstrable source conflict is a guard outcome; do not ask a model
        # to override it or call this a neural correction.
        source_audit=fidelity.audit_source(source)
        if source_audit['issues']:
            verdict={'outcome':'SOURCE_GUARD_BLOCKED_BEFORE_MODEL','correct':False,
                     'safe':True,'model_correct':False,'source_guard_blocked':True,
                     'blockers':source_audit['issues']}
            called=False
        else:
            called=False
            raw=None
            error=None
            if baseline and probe['id']=='original' and baseline_response:
                raw=Path(baseline_response).read_text(encoding='utf-8')
            else:
                fb=feedback_override if feedback_override is not None else (meta.PROFILES.get(profile) if profile else None)
                called=True;calls+=1
                try:
                    if infer is None:
                        raw=bridge.ollama(source,model,endpoint,timeout,feedback=fb)
                    else:
                        raw=infer(source,fb)
                except bridge.BridgeError as exc:
                    incomplete=True
                    error={'stage':exc.stage,'reason':exc.reason[:350]}
            if error is not None:
                verdict={'outcome':'INFERENCE_NOT_SCORED','correct':None,'safe':False,
                         'model_correct':False,'source_guard_blocked':False,
                         'blockers':[error]}
            else:
                if not isinstance(raw,str) or len(raw.encode('utf-8'))>bridge.MAX_RESPONSE_BYTES:
                    raise FuzzCorrectError('Model response is not a valid bounded string')
                (folder/'model-response.json').write_text(raw,encoding='utf-8')
                verdict=evaluate_response(probe,raw)
        row={'profile':profile or 'baseline','probe_id':probe['id'],
             'expected':probe['expected'],'changed_path':probe['changed_path'],
             'request_sha256':probe['request_sha256'],
             'inference_performed':called and infer is None,
             'injected_inference':called and infer is not None,
             'result_path':str(folder/'verdict.json'),**verdict}
        (folder/'verdict.json').write_text(json.dumps(row,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        rows.append(row)
        print(json.dumps({'profile':row['profile'],'probe':row['probe_id'],
                          'outcome':row['outcome'],'model_call':called}),flush=True)
        return row
    # Preserve first error; do not relabel it after successful correction.
    initial=run(lookup['original'],None,baseline=True)
    if not incomplete:
        for p in train[1:]:
            run(p,None)
            if incomplete:break
    if not incomplete and initial['correct'] is not True and not initial['source_guard_blocked']:
        # Case-specific evidence-derived feedback is NEVER eligible for global promotion.
        targeted=run(lookup['original'],'targeted_error_correction',
                     feedback_override=targeted_feedback(initial,lookup['original']['request']))
    if incomplete:
        candidate='INFERENCE_UNAVAILABLE_NOT_SCORED'
    elif initial['source_guard_blocked']:
        candidate='SOURCE_GUARD_BLOCK_NOT_MODEL_UPDATE'
    else:
        candidates=[]
        train_base=[r for r in rows if r['profile']=='baseline' and r['probe_id'] in TRAIN_IDS]
        for profile in selected:
            trial=[]
            for p in train:
                trial.append(run(p,profile))
                if incomplete:break
            if incomplete:break
            good=(all(x['correct'] is True for x in trial) and
                  all(_favourable(base,other) for base,other in zip(train_base,trial)))
            if good:candidates.append(profile)
        candidate=('INFERENCE_UNAVAILABLE_NOT_SCORED' if incomplete else
                   candidates[0] if candidates else 'NO_PROFILE_PASSED_TRAIN')
    # Selection is frozen before the holdout probes run. No answer shopping.
    if not incomplete:
        for p in holdout:
            run(p,None)
            if incomplete:break
    if not incomplete and candidate in meta.PROFILES:
        for p in holdout:
            run(p,candidate)
            if incomplete:break
    def of(profile,group):return [r for r in rows if r['profile']==profile and r['probe_id'] in group]
    trial_all=of(candidate,{p['id'] for p in probes}) if candidate in meta.PROFILES else []
    base_all=of('baseline',{p['id'] for p in probes})
    eligible=(not incomplete and candidate in meta.PROFILES and len(trial_all)==len(probes) and
              all(r['correct'] is True and r['outcome']!='UNSAFE_ACCEPTANCE' for r in trial_all) and
              all(r['correct'] is True for r in trial_all if r['probe_id'] in HOLDOUT_IDS) and
              any(b['correct'] is False and t['correct'] is True for b,t in zip(
                  sorted(base_all,key=lambda r:r['probe_id']), sorted(trial_all,key=lambda r:r['probe_id']))))
    replay=None
    replay_status=None
    if emit and candidate in meta.PROFILES and eligible and case['expected']=='contract':
        # Replay the corrected *live-model response* through the original
        # source guard and the real frozen V3 Solidity generator. This replay
        # is not an extra live model call and cannot manufacture model accuracy.
        folder=work/candidate/'original'
        gold_path=folder/'author-reference.json'
        gold_path.write_text(json.dumps(lookup['original']['gold'],indent=2)+'\n',encoding='utf-8')
        replay_path=folder/'guarded-replay.json'
        replay=guarded.execute(request=folder/'request.txt',output=replay_path,
                               response_file=folder/'model-response.json',gold=gold_path,forge=False)
        replay_status=replay['status']
        if replay_status!='PASS_GUARDED_OFFLINE_NOT_LIVE':
            eligible=False
    elif emit and candidate in meta.PROFILES and eligible:
        replay_status='NOT_APPLICABLE_TO_CORRECT_REFUSAL'
    selected_status=('PROMOTED_ON_LOCAL_PROBES_NOT_INDEPENDENTLY_VALIDATED' if eligible else
                     'NO_PROMOTION_FAILED_LOCAL_PROBE_CRITERIA')
    update=None
    if eligible:
        feedback=meta.PROFILES[candidate]
        update={'schema':meta.SCHEMA,'status':selected_status,'profile':candidate,
                'feedback':feedback,'feedback_sha256':meta.hash_feedback(feedback),
                'evidence':{'case_id':case_id,'corpus_sha256':corpus_hash,
                            'local_probe_count':len(probes),'local_probe_ids':[p['id'] for p in probes],
                            'model':model,'original_request_sha256':lookup['original']['request_sha256'],
                            'weights_unchanged':True,'not_independent_generalization':True}}
        if promote:
            path=output.parent/(output.stem+'.meta-update.json')
            if path.exists():raise FuzzCorrectError('Meta update output already exists')
            path.write_text(json.dumps(update,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    report={'schema':REPORT_SCHEMA,
            'status':('INCOMPLETE_INFERENCE_UNAVAILABLE_NOT_SCORED' if incomplete else
                      'INJECTED_FUZZ_TEST_NOT_LIVE' if infer is not None else
                      'LIVE_FUZZ_WITH_REPLAYED_INITIAL_NOT_CERTIFIED' if baseline_response else
                      'LIVE_META_FUZZ_MEASURED_NOT_CERTIFIED'),
            'started_at':_now(),'case_id':case_id,'model':model,'live_model_calls':calls if infer is None else 0,
            'injected_calls':calls if infer is not None else 0,
            'initial_response_was_replay':baseline_response is not None,
            'corpus_sha256':corpus_hash,'original_request_sha256':lookup['original']['request_sha256'],
            'source_fuzz_not_independent_gold':True,'benchmark_reference_not_in_prompts':True,
            'inference_only_meta_update_no_weight_change':True,'frozen_v3_kernel_unchanged':True,
            'selected_profile':candidate,'promotion':selected_status,
            'targeted_error_correction':targeted['outcome'] if targeted else None,
            'incomplete':incomplete,
            'meta_update':update,'model_accuracy_not_equal_safety_gate_blocks':True,
            'baseline':_score(base_all),'candidate':_score(trial_all) if trial_all else None,
            'generated_replay_status':replay_status,
            'probes':[{'id':p['id'],'request_sha256':p['request_sha256'],
                       'expected':p['expected'],'changed_path':p['changed_path']} for p in probes],
            'results':rows}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',required=True,action='append',help='May repeat for multiple failures')
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--output',type=Path,help='One case only, explicit result JSON')
    group.add_argument('--output-dir',type=Path,help='One unique result per case')
    p.add_argument('--model',default=bridge.DEFAULT_MODEL)
    p.add_argument('--ollama-endpoint',default=bridge.MODEL_ENDPOINT)
    p.add_argument('--timeout-seconds',type=float,default=600)
    p.add_argument('--corpus',type=Path,default=challenge.OUT)
    p.add_argument('--corpus-sha256',default=None)
    p.add_argument('--baseline-response',type=Path,help='One case only: previously saved model response')
    p.add_argument('--profile',action='append',choices=sorted(meta.PROFILES),help='Subset of allowlisted profiles')
    p.add_argument('--promote',action='store_true',help='Write opt-in meta update only if all local probes pass')
    p.add_argument('--emit',action='store_true',help='Replay successful correction through frozen V3 generator')
    args=p.parse_args(argv)
    if len(set(args.case)) != len(args.case) or (args.output is not None and len(args.case)>1):
        p.error('Multiple or duplicate cases require --output-dir and unique IDs')
    if len(args.case)>1 and args.baseline_response:
        p.error('--baseline-response is supported only with one case')
    all_results=[]
    try:
        for cid in args.case:
            filename=args.output if args.output is not None else args.output_dir/(cid+'.fuzz-correct.json')
            rep=execute(case_id=cid,output=filename,model=args.model,
                        endpoint=args.ollama_endpoint,timeout=args.timeout_seconds,
                        corpus=args.corpus,corpus_sha256=args.corpus_sha256,
                        baseline_response=args.baseline_response,profiles=args.profile,
                        promote=args.promote,emit=args.emit)
            item={'status':rep['status'],'case':rep['case_id'],
                  'baseline':rep['baseline'],'candidate':rep['candidate'],
                  'targeted_correction':rep['targeted_error_correction'],
                  'selected_profile':rep['selected_profile'],'promotion':rep['promotion'],
                  'live_model_calls':rep['live_model_calls'],'output':str(filename)}
            all_results.append(item)
            print(json.dumps(item),flush=True)
    except (FuzzCorrectError,ValueError,OSError,bridge.BridgeError) as exc:
        print(json.dumps({'status':'FAIL_FUZZ_CORRECT','reason':str(exc),
                          'completed_cases':[x['case'] for x in all_results]}))
        return 2
    return 1 if any(x['status'].startswith('INCOMPLETE_') for x in all_results) else 0

if __name__=='__main__':raise SystemExit(main())
