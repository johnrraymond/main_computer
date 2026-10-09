#!/usr/bin/env python3
"""Captain->helm->physics authority smoke; test-only and expected RED until integrated.

Usage:
  python .../space_captain_enemy_helm_authority_smoke.py --expect-red
  python .../space_captain_enemy_helm_authority_smoke.py

The former exits 0 *only* for the known authority defect. The latter must turn
GREEN after a captain-order integration fix. Neither mutates production code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'web' / 'scripts'
PROBE = Path(__file__).with_name('space_captain_enemy_helm_authority_probe.js')
REQUIRED_RED = {
    'noOrderMeansNoAutonomousThrust',
    'captainCommandsAccepted',
    'liveSceneHasCaptainOrderIngress',
}
DRIVER = r'''
const fs=require('fs'),vm=require('vm');
const paths=JSON.parse(process.argv[1]);
try {
  for(const p of paths.slice(0,-1)) vm.runInThisContext(fs.readFileSync(p,'utf8'),{filename:p});
  const result=new Function(fs.readFileSync(paths[paths.length-1],'utf8'))();
  process.stdout.write(JSON.stringify({kind:'result',result}));
} catch(e) {
  process.stdout.write(JSON.stringify({kind:'error',error:String(e?.stack||e)}));
  process.exitCode=2;
}
'''


def run() -> dict:
    inputs = [str(SCRIPTS / 'space-captain-multirate-contract.js'),
              str(SCRIPTS / 'bridge-encounter-runtime.js'), str(PROBE)]
    result = subprocess.run(['node', '-e', DRIVER, json.dumps(inputs), str(ROOT)],
                            capture_output=True, text=True, timeout=90)
    try:
        packet = json.loads(result.stdout)
    except ValueError:
        return {'ok': False, 'executionError': 'non-JSON Node output',
                'stdoutTail': result.stdout[-2000:], 'stderrTail': result.stderr[-2000:]}
    if packet.get('kind') != 'result' or result.returncode != 0:
        return {'ok': False, 'executionError': packet.get('error') or result.stderr[-2000:]}
    report = packet['result']
    if not isinstance(report, dict):
        return {'ok': False, 'executionError': 'probe returned non-object'}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect-red', action='store_true', help='Confirm specifically the current missing captain authority; returns 0 only for expected failing checks')
    parser.add_argument('--output', type=Path, help='Write the full JSON report')
    args = parser.parse_args()
    result = run()
    checks = result.get('checks') or {}
    errors = result.get('errors') or []
    # RED is valid only when all prerequisite components load, the default
    # defects are observable, and the harness itself did not error.
    controlled_red = (
        not result.get('ok')
        and not result.get('executionError')
        and not errors
        and checks.get('productionRuntimeLoaded') is True
        and checks.get('playerPhysicalAuthorityStillEnabled') is True
        and all(checks.get(name) is False for name in REQUIRED_RED)
    )
    exit_ok = controlled_red if args.expect_red else result.get('ok') is True
    report = {
        'ok': bool(exit_ok),
        'schema': 'game.spaceCaptainEnemyHelmAuthoritySmokeReport.v1',
        'mode': 'expected-red-baseline' if args.expect_red else 'required-green',
        'productionCodeModified': False,
        'checks': checks,
        'failedChecks': result.get('failedChecks', []),
        'probeOk': result.get('ok', False),
        'probeErrors': errors,
        'executionError': result.get('executionError'),
        'metrics': result.get('metrics', {}),
    }
    encoded = json.dumps(report, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded)
    return 0 if exit_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
