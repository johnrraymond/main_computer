"""Read-only, multi-application semantic vocabulary acceptance (no hosted claims)."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from main_computer.mcel_knowledge import GRAPH_SCHEMA, interpret, load_catalog

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json'


def evaluate() -> dict:
    cat = load_catalog()
    src = json.loads(FIXTURE.read_text(encoding='utf8'))
    before = deepcopy(src)
    graph = interpret(src, cat)
    file_rows = graph['sources']['file-explorer.entries']['records']
    leases = graph['sources']['worker.market-workers']['records']
    sessions = graph['sources']['worker.sessions']['records']
    scheduler = graph['sources']['task-manager.schedules']['records']
    result = {
      'concept-registry-consolidated': len(cat['concepts']) >= 45 and len(cat['concepts']) == len(set(cat['concepts'])),
      'real-producer-families': all(k in cat['observations'] for k in ('file-explorer.roots','file-explorer.entries','file-explorer.read-preview','task-manager.schedules','worker.market-workers','worker.sessions','worker.runtime')),
      'ui-independent-interpreter': graph['schema'] == GRAPH_SCHEMA,
      'root-scoped-file-identity': file_rows[0]['identity']['parts'] == ['workspace', 'tools/runs'],
      'directory-absence-is-not-file-zero': file_rows[0]['properties']['size_bytes']['state'] == 'unavailable',
      'genuine-empty-file-zero': file_rows[1]['properties']['size_bytes']['state'] == 'present' and file_rows[1]['properties']['size_bytes']['raw'] == 0,
      'stat-sentinel-not-timestamp': file_rows[0]['properties']['modified_at']['state'] == 'unavailable',
      'schedule-planned-not-executed': scheduler[0]['properties']['run_at']['state'] == 'present' and not graph['operations']['scheduler.create-server-action']['executable'],
      'worker-lease-generation-scope': leases[0]['identity']['parts'] == ['test-cluster','w1','socket-B',2],
      'worker-session-request-identity': sessions[0]['identity']['parts'] == ['test-cluster','s1'],
      'exact-lease-candidate-not-authority': sessions[0]['relationships'][0]['status'] == 'candidate-only' and sessions[0]['relationships'][0]['candidateIndices'] == [0],
      'old-worker-socket-is-not-same-lease': sessions[1]['relationships'][0]['status'] == 'unresolved',
      'worker-policy-is-not-permission': graph['sources']['worker.runtime']['records'][0]['properties']['allowed_to_accept']['raw'] is True and all(not x['authorized'] and not x['executable'] for x in graph['operations'].values()),
      'source-payload-unchanged': before == src,
      'task-manager-regressions-still-mapped': sum(k.startswith('task-manager.') for k in cat['observations']) == 8,
      'missing-external-sources-not-fabricated': graph['sources']['task-manager.processes']['state'] == 'missing-source',
    }
    return {'schema':'mcel.semantic-knowledge-acquisition-frob.v1',
            'status':'PASS' if all(result.values()) else 'FAIL',
            'scope':'offline-read-only-source-shaped-fixtures',
            'checks':result,
            'counts':{k:len(cat[k]) for k in ('concepts','observations','operations')},
            'fragments':cat['fragments'],
            'hostedValidated':False,'layoutValidated':False,'effectsExecuted':False,
            'caveat':'Representative envelopes assembled from existing producer shapes; not live HTTP snapshots.'}


def main() -> int:
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,default=ROOT/'runtime/reports/mcel-semantic-knowledge-acquisition.json')
    args=p.parse_args();report=evaluate();args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print('MCEL_SEMANTIC_KNOWLEDGE_ACQUISITION_'+report['status'])
    print('concepts={concepts} observation_families={observations} operations={operations}'.format(**report['counts']))
    for key,val in report['checks'].items(): print(('PASS' if val else 'FAIL')+' '+key)
    print('report:',args.report)
    return 0 if report['status']=='PASS' else 1

if __name__ == '__main__':raise SystemExit(main())
