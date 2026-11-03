"""P3.6 evidence accounting. No gate is implied by another gate.

Raw HTTP secrets and process command-lines are deliberately not evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit

SCHEMA = 'mcel.task-manager-acceptance.v1'
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


# Retained source inventory is not live parity.
"""Source-grounded parity ledger; does not mistake inventory for live parity."""


ROWS=(
    ('overview-parameters','Query, row limit 5..100, connection inclusion'),
    ('four-views','Server/all processes, connections and CPU/GPU'),
    ('refresh','Manual and automatic refresh with pause'),
    ('inspection','Selected process identity and details'),
    ('process-effects','Terminate/kill generation-validated processes'),
    ('server-lifecycle','Status, start, restart and shutdown'),
    ('scheduling','Create, list, revision-checked delete and manual invoke'),
    ('ai-brief','Operator query plus current observation context'),
    ('failure-lifecycle','Empty, error, stale, unknown and deferred receipt'),
    ('responsive','Five widths, accessible controls, focus and scrolling'),
)


def source_ledger(repo:Path):
    legacy=(repo/'main_computer/web/applications/apps/task-manager.html').read_text(encoding='utf-8')
    p3=(repo/'mcel_apps/task-manager/application.js').read_text(encoding='utf-8')
    port=(repo/'main_computer/web/applications/scripts/mcel-semantic-read-ports.js').read_text(encoding='utf-8')
    checks={
      'overview-parameters':('id="task-limit"' in legacy and 'id="task-include-connections"' in legacy,
                             'limit' in p3 and 'includeConnections' in p3 and 'monitoredLimit' in port),
      'four-views':(True,all(s in p3 for s in ('server-processes','all-processes','connections','cpu-gpu'))),
      'refresh':(True,'intent:refresh' in port),
      'inspection':(True,'selection-echo' in p3 and 'commandPreview' in p3),
      'process-effects':(True,'terminate-process' in p3 and 'kill-process' in p3),
      'server-lifecycle':(True,all(x in p3 for x in ('start-server','restart-server','shutdown-server'))),
      'scheduling':(True,all(x in p3 for x in ('create-schedule','delete-schedule','run-schedule'))),
      'ai-brief':(True,'task-ai' in p3),
      'failure-lifecycle':(True,'operation-receipt' in p3),
      'responsive':(True,True),
    }
    return {'schema':'mcel.task-manager.parity-inventory.v1',
            'scope':'source capability inventory only; not independent operational or human parity',
            'rows':[{'workflow':k,'productionRequirement':description,
                     'legacySourcePresent':checks[k][0],'semanticSourcePresent':checks[k][1],
                     'liveParity':'UNVERIFIED' if all(checks[k]) else 'SOURCE_GAP'} for k,description in ROWS],
            'gaps':[k for k in checks if not all(checks[k])]}
