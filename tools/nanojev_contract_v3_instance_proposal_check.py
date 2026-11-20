#!/usr/bin/env python3
"""Read-only V3 proposal consistency check. Does not authorize or deploy."""
import hashlib
import json
from pathlib import Path
from tools import nanojev_contract_v3_specification as gate
from tools import nanojev_contract_v3_governance_language as compiler_language

def check(root=Path('.')):
    docs=Path(root)/'docs'/'nanojev'
    spec=docs/'v3-authoritative-specification.v1.json'
    proposed=docs/'v3-original-12eth-instance-PROPOSED.json'
    payload=docs/'v3-original-12eth-compiler-input-PROPOSED.json'
    manifest=docs/'v3-original-12eth-manifest-PROPOSED.json'
    x=json.loads(proposed.read_text(encoding='utf-8'))
    y=json.loads(payload.read_text(encoding='utf-8'))
    m=json.loads(manifest.read_text(encoding='utf-8'))
    problems=[]
    for k,p in [('proposal_sha256',proposed),('compiler_input_sha256',payload),('target_spec_sha256',spec)]:
        if hashlib.sha256(p.read_bytes()).hexdigest()!=m[k]:problems.append('Hash mismatch: '+k)
    if x.get('status')!='DRAFT_INSTANCE_PROPOSED_NOT_AUTHORIZED' or x.get('authorization',{}).get('owner') is not None or x.get('authorization',{}).get('owner_approval_record_sha256') is not None or x.get('authorization',{}).get('payer_and_worker_approval_documents') or x.get('authorization',{}).get('jurisdiction_or_external_terms') is not None:
        problems.append('Proposal has unexpected authorization or status fields')
    if any(m.get(k) is not False for k in ('owner_approved','expert_gold_independence_verified','deployment_authorized')):problems.append('Manifest claims certification')
    problems.extend(gate.check_instance(json.loads(spec.read_text(encoding='utf-8')),x))
    if not problems:
        try:normalized=compiler_language.validate(y)
        except Exception as exc:problems.append('Compiler language rejects payload: '+str(exc))
        else:
            if normalized != y:problems.append('Compiler normalized proposal differently')
            if any(y[k]!=x[k] for k in ('language','asset','settlement_mode','payer','worker','arbitrator','amount_wei','policy')) or len(y['milestones'])!=len(x['milestones']) or any(any(a[k]!=b[k] for k in a) for a,b in zip(y['milestones'],x['milestones'])):
                problems.append('Human review and compiler payload diverge')
    return {'status':'PASS_INSTANCE_PROPOSAL_COMPLETE_NOT_AUTHORIZED' if not problems else 'FAIL_INSTANCE_PROPOSAL_INTEGRITY',
            'errors':problems,'owner_approved':False,'expert_gold_independence_verified':False,'deployment_authorized':False,
            'parameter_decisions':16}

def main():
    out=check()
    print(json.dumps(out,indent=2))
    return bool(out['errors'])

if __name__=='__main__':raise SystemExit(main())
