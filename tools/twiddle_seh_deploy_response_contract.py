"""Read-only regression Twiddle for the MCEL SEH response contract.

Run from the repository root:
  python tools/twiddle_seh_deploy_response_contract.py

Exercises the real compiled SLH DSL, Python deployment adapter, and JavaScript
MCEL response validator. Fake LMS stays entirely in memory; this script does
not call LMS, the SEH harness, or Coolify, and does not modify repository files.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ('register', 'select', 'preview-deploy', 'deploy', 'preview-undeploy', 'undeploy')


def main() -> int:
    if shutil.which('node') is None:
        raise RuntimeError('Node.js is required for the real MCEL runtime validator')
    sys.path.insert(0, str(ROOT))
    from main_computer.mcel_slh_application import compile_slh_application
    from main_computer.mcel_slh_seh_deployment import execute_seh_deployment

    ir, _ = compile_slh_application(ROOT)
    capabilities = [cap for cap in ir['capabilities']
                    if cap['id'] == 'capability:seh-deployment-control']
    assert len(capabilities) == 1, 'SEH deployment capability must be unique'
    schemas = {op['name']: op['resultSchema'] for op in capabilities[0]['operations']}
    assert set(schemas) == set(OPERATIONS), f'Unexpected SEH operations: {set(schemas)}'

    class FakeLMS:
        def request(self, method, path, body=None):
            if (method, path) == ('GET', '/v1/local/seh/deployment'):
                return {'generation': 33}
            if (method, path) == ('POST', '/v1/local/seh/deployment/harness'):
                if body['execute'] is False:
                    return {'status': 'PLAN_ONLY'}
                assert body['execute'] is True
                return {'status': 'ACCEPTED', 'operation_id': body['operation_id'],
                        'application_uuid': 'rzwldrq1npiqwrztpfnkbj5q'}
            raise AssertionError(f'Unexpected network request: {method} {path}')

    response = execute_seh_deployment('deploy', {}, target='local',
                                      local_client=FakeLMS(),
                                      operation_id='mcel-seh-71678cdfd8ba46479fb5a348bb69b70d')
    assert set(response) == {'outcome', 'message', 'operationId', 'applicationUuid'}
    assert response['outcome'] == 'accepted-deferred'
    assert response['operationId'] == 'mcel-seh-71678cdfd8ba46479fb5a348bb69b70d'

    runtime = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'
    js = r'''const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {};
vm.runInNewContext(fs.readFileSync(input.runtime, 'utf8'), context, {filename:'mcel-semantic-operational-runtime.js'});
const check = context.McelSemanticOperationalRuntime.checkSchema;
const result = {};
for (const [name, schema] of Object.entries(input.schemas)) {
  try { check(schema, input.response, 'capability-response'); result[name] = 'PASS'; }
  catch (e) { result[name] = String(e.message || e); }
}
process.stdout.write(JSON.stringify(result));'''
    completed = subprocess.run(['node', '-e', js],
                               input=json.dumps({'runtime': str(runtime),
                                                 'schemas': schemas, 'response': response}),
                               text=True, capture_output=True, timeout=20, check=True)
    results = json.loads(completed.stdout)
    for name in OPERATIONS:
        actual = results[name]
        assert actual == 'PASS', f'{name}: {actual}'
        print(f'{name:18s} PASS')
    print('PASS: 6 operations, actual compiled DSL and JS runtime validator')
    print('No files in the repository changed. No LMS, harness or Coolify requests were made.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
