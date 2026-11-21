#!/usr/bin/env python3
"""Original one-pass Gemma-to-V3 inference with source-based safety, no model veto.

All source checks are narrow and source evidenced. The model produces the same
candidate as the existing single-pass bridge; a non-generative check takes
place after interpreting IR and BEFORE any Solidity compilation. This is an
alternative experimental entrypoint, not a modification to the frozen kernel.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time
from typing import Callable

from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_evidence_reconcile as evidence_reconcile
from tools import nanojev_contract_v3_meta_prompts as meta_prompts
from tools import nanojev_contract_v3_source_fidelity as fidelity
from tools import nanojev_contract_v3_governance_language as gov
from tools import nanojev_contract_v3_governance_verifier as verifier
from tools import nanojev_contract_v3_governance_smoke as downstream


def _now():return datetime.now(timezone.utc).isoformat()


def execute(*,request:Path,output:Path,live_ollama:bool=False,
            response_file:Path|None=None, model:str=bridge.DEFAULT_MODEL,
            endpoint:str=bridge.MODEL_ENDPOINT,timeout:float=600,forge:bool=False,
            gold:Path|None=None,post:Callable|None=None,meta_update:Path|None=None)->dict:
    output=Path(output).resolve();request=Path(request).resolve()
    start=time.monotonic()
    result={'schema':'nanojev.contract.v3.guarded_single_pass.v1',
            'status':'FAIL_NOT_VERIFIED','mode':'LIVE_OLLAMA' if live_ollama else 'OFFLINE_REPLAY_NOT_LIVE',
            'model':model if live_ollama else 'RECORDED_RESPONSE_NOT_LIVE',
            'llm_inference_performed':False,'model_request_attempted':False,'model_calls':0,
            'weights_changed':False,'v3_kernel_mutated':False,'external_deployment':False,
            'semantic_comparison':'NOT_CHECKED','downstream':None,'blockers':[],
            'source_guard_enabled':True, 'source_guard_model_based':False,
            'started_at':_now()}
    try:
        if live_ollama==(response_file is not None):
            raise bridge.BridgeError('preflight','Exactly one mode required')
        if output.exists() or output==request or not request.is_file():
            raise bridge.BridgeError('preflight','Missing source or output conflict')
        if response_file is not None and output==Path(response_file).resolve():
            raise bridge.BridgeError('preflight','Response file conflicts with output')
        if gold is not None and output==Path(gold).resolve():
            raise bridge.BridgeError('preflight','Gold file conflicts with output')
        feedback=None
        if meta_update is not None:
            if not live_ollama:
                raise bridge.BridgeError('preflight','Meta update applies only to live Ollama inference')
            try:
                feedback, meta_info = meta_prompts.load_meta_update(Path(meta_update))
            except (OSError,ValueError,KeyError,TypeError) as exc:
                raise bridge.BridgeError('preflight','Invalid or unpromoted meta update: '+str(exc)) from exc
            result['meta_update']={'profile':meta_info['profile'],
                                   'feedback_sha256':meta_info['feedback_sha256'],
                                   'status':'LOCAL_PROBES_ONLY_NOT_MODEL_TRAINING'}
        raw=request.read_bytes()
        if not raw or len(raw)>bridge.MAX_REQUEST_BYTES or b'\x00' in raw:
            raise bridge.BridgeError('preflight','Invalid source bytes')
        text=raw.decode('utf-8')
        result['request_sha256']=bridge.digest(raw)
        result['frozen_kernel_sha256']=bridge.check_kernel()
        source_audit=fidelity.audit_source(text)
        result['source_audit']={'status':source_audit['status'],
                                'issues':source_audit['issues'],
                                'recognized_values':source_audit['recognized_values'],
                                'unparsed_lines':source_audit['unparsed_lines']}
        if source_audit['issues']:
            result['status']='SOURCE_GUARD_BLOCKED_BEFORE_MODEL'
            result['clarification_needed']=[f"line(s) {issue['lines']}: {issue['detail']} ({issue.get('path') or issue['code']})" for issue in source_audit['issues']]
            return result
        if live_ollama:
            result['model_request_attempted']=True
            result['model_calls']=1
            diag={}
            content=bridge.ollama(text,model,endpoint,timeout,post,diag,feedback=feedback)
            result['model_diagnostics']=diag
            result['llm_inference_performed']=post is None
            if post is not None:result['mode']='INJECTED_TRANSPORT_NOT_LIVE_PROOF'
        else:
            response=Path(response_file).read_bytes()
            if len(response)>bridge.MAX_RESPONSE_BYTES:raise bridge.BridgeError('model','Response fixture too large')
            content=response.decode('utf-8')
        result['model_response_sha256']=bridge.digest(content)
        # Diagnose a citation-field mismatch without persisting contract text or
        # untrusted model output. Only a missing, uniquely sourced literal quote
        # may be recovered. Extra, contradictory, or ambiguous citations still fail.
        prepared, attribution=evidence_reconcile.reconcile(text,content)
        result['citation_attribution']=attribution
        align=[]
        spec,missing=bridge.interpret(text,prepared,evidence_alignment=align)
        result['evidence_alignments']=align
        if missing:
            result['status']='MODEL_REQUESTED_CLARIFICATION'
            result['clarification_needed']=missing
            result['false_refusal_evidence']=bridge.audit_false_clarification(text,missing)
            return result
        candidate_audit=fidelity.audit_candidate(text,spec,source_audit=source_audit)
        result['candidate_audit']=candidate_audit
        if candidate_audit['issues']:
            result['status']='SOURCE_GUARD_BLOCKED_CANDIDATE_BEFORE_SOLIDITY'
            result['clarification_needed']=[f"line(s) {issue['lines']}: {issue['detail']} ({issue.get('path')})" for issue in candidate_audit['issues']]
            return result
        result['normalized_ir_sha256']=bridge.digest(bridge.canonical(spec))
        result['evidence_status']='PASS_LITERAL_EVIDENCE_NOT_SEMANTIC_PROOF'
        if gold is not None:
            expected=gov.validate(json.loads(Path(gold).read_text(encoding='utf-8')))
            result['gold_sha256']=bridge.digest(bridge.canonical(expected))
            result['semantic_comparison']='MATCH_AUTHOR_SUPPLIED_GOLD' if spec==expected else 'MISMATCH_AUTHOR_SUPPLIED_GOLD'
            if spec!=expected:
                raise bridge.BridgeError('semantic_gold','Model candidate differs from supplied reference')
        else:result['semantic_comparison']='NO_GOLD_SUPPLIED'
        graph=verifier.verify(spec)
        result['graph_status']=graph['status']
        result['graph_nodes']=graph['reachable_nodes']
        if not graph['emission_authorized_by_graph_audit']:
            raise bridge.BridgeError('graph','Frozen V3 graph refused the candidate')
        workspace=output.parent/(output.stem+'.guarded.workspace')
        if workspace.exists():raise bridge.BridgeError('preflight','Workspace already exists')
        workspace.mkdir(parents=True)
        spec_file=workspace/'normalized-governance-ir.json'
        spec_file.write_text(json.dumps(spec,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
        child=downstream.execute(spec_file,workspace/'v3-governance-smoke.json',forge=forge)
        result['downstream']={'status':child.get('status'),'blockers':child.get('blockers'),
                             'solidity_sha256':child.get('generated',{}).get('solidity_sha256'),
                             'forge':child.get('forge')}
        if not str(child.get('status')).startswith('PASS_'):
            raise bridge.BridgeError('downstream','Frozen V3 generation was not verified')
        result['status']=('PASS_GUARDED_LIVE_FORGE_NOT_CERTIFIED' if forge else 'PASS_GUARDED_LIVE_IR_FORGE_NOT_RUN') if live_ollama and post is None else 'PASS_GUARDED_OFFLINE_NOT_LIVE'
    except (bridge.BridgeError,ValueError,KeyError,OSError,UnicodeError,TypeError) as exc:
        stage=getattr(exc,'stage','preflight')
        result['blockers'].append({'stage':stage,'reason':getattr(exc,'reason',str(exc))[:350]})
        if stage in ('model_timeout','model_transport','model_http'):
            result['status']='INFERENCE_UNAVAILABLE_NOT_SCORED'
        elif stage=='model_empty_answer':result['status']='MODEL_NO_FINAL_ANSWER_NOT_SCORED'
    finally:
        result['finished_at']=_now();result['elapsed_seconds']=round(time.monotonic()-start,3)
        if not output.exists():
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live-ollama',action='store_true')
    mode.add_argument('--response-file',type=Path)
    p.add_argument('--model',default=bridge.DEFAULT_MODEL)
    p.add_argument('--ollama-endpoint',default=bridge.MODEL_ENDPOINT)
    p.add_argument('--timeout-seconds',type=float,default=600)
    p.add_argument('--gold',type=Path)
    p.add_argument('--forge',action='store_true')
    p.add_argument('--meta-update',type=Path,help='Opt-in locally tested inference-time prompt update')
    args=p.parse_args(argv)
    out=execute(request=args.request,output=args.output,live_ollama=args.live_ollama,
                response_file=args.response_file,model=args.model,endpoint=args.ollama_endpoint,
                timeout=args.timeout_seconds,gold=args.gold,forge=args.forge,meta_update=args.meta_update)
    print(json.dumps({'status':out['status'],'model_calls':out['model_calls'],
                      'model_semantic_comparison':out['semantic_comparison'],
                      'source_issues':len(out.get('source_audit',{}).get('issues',[])),
                      'blockers':out['blockers'],'output':str(args.output)}))
    return 0 if out['status'].startswith(('PASS_','SOURCE_GUARD_BLOCKED','MODEL_REQUESTED_CLARIFICATION')) else 1

if __name__=='__main__':raise SystemExit(main())
