"""P3.6 evidence accounting. No gate is implied by another gate.

Raw HTTP secrets and process command-lines are deliberately not evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit

SCHEMA = 'mcel.p36-acceptance.v1'
STATES = frozenset({'PASS', 'FAIL', 'BLOCKED', 'UNVERIFIED'})
GATES = ('P36_BASELINE','P3_WINDOWS_HOST','P36_PROCESS_EFFECT','P36_SCHEDULE_EFFECT',
         'P36_SERVER_LIFECYCLE_EFFECT','P3_REAL_SIDE_EFFECT','P3_PRODUCTION_PARITY',
         'P3_HUMAN_DESIGN','P3_PROMOTION')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gate(state='UNVERIFIED', *, reason='', next_action='', files=()):
    if state not in STATES:
        raise ValueError('invalid-gate-status')
    return {'status':state,'reason':reason,'nextAction':next_action,'evidence':list(files)}


def init_report(run_id: str, source_hashes: dict[str,str]):
    return {'schema':SCHEMA,'runId':run_id,'createdAt':datetime.now(timezone.utc).isoformat(),
            'sourceHashes':source_hashes,'gates':{name:gate() for name in GATES},
            'threatBoundary':'Loopback/CSRF does not authenticate against untrusted programs of the same OS user',
            'promotionAuthorized':False}


def set_gate(report,name,status,reason='',next_action='',files=()):
    if name not in GATES:raise ValueError('unknown-gate')
    report['gates'][name]=gate(status,reason=reason,next_action=next_action,files=files)


def reconcile(report):
    g=report['gates']
    required=['P36_BASELINE','P3_WINDOWS_HOST','P36_PROCESS_EFFECT','P36_SCHEDULE_EFFECT',
              'P36_SERVER_LIFECYCLE_EFFECT']
    values=[g[k]['status'] for k in required[2:]]
    if all(v=='PASS' for v in values):
        set_gate(report,'P3_REAL_SIDE_EFFECT','PASS','All real effect classes independently verified')
    elif 'FAIL' in values:
        set_gate(report,'P3_REAL_SIDE_EFFECT','FAIL','A required effect class failed')
    else:
        set_gate(report,'P3_REAL_SIDE_EFFECT','BLOCKED','Some required effects are unverified or blocked',
                 'Run isolated service lifecycle and process and schedule evidence')
    # This is an acceptance report, never an authorization to replace the production route.
    set_gate(report,'P3_PROMOTION','BLOCKED','Promotion is an independent explicit operator action',
             'Complete all gates, review source/preservation and perform separate reversible promotion')
    if any(g[k]['status']=='FAIL' for k in g if k!='P3_PROMOTION'):
        report['outcome']='FAIL_ACCEPTANCE'
    elif all(g[k]['status']=='PASS' for k in ('P36_BASELINE','P3_WINDOWS_HOST','P3_REAL_SIDE_EFFECT','P3_PRODUCTION_PARITY','P3_HUMAN_DESIGN')):
        report['outcome']='READY_FOR_SEPARATE_PROMOTION_REVIEW'
    else: report['outcome']='BLOCKED_INCOMPLETE_EVIDENCE'
    return report


def validate_host_url(value: str) -> str:
    p=urlsplit(value)
    if (p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost','::1')
            or p.username or p.password or p.query or p.fragment or p.path not in ('','/')
            or p.port is None or not 1<=p.port<=65535):
        raise ValueError('host-url-must-be-explicit-loopback-http-with-port')
    return f'http://{p.netloc}'


def evidence_manifest(folder:Path,exclude=()):
    excluded={str(x) for x in exclude}
    return {str(p.relative_to(folder)).replace('\\','/'):digest(p) for p in sorted(folder.rglob('*'))
            if p.is_file() and str(p.relative_to(folder)).replace('\\','/') not in excluded
            and not p.is_symlink()}


def safe_json(path:Path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
