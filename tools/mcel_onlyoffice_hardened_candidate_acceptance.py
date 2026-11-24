"""Source-backed hardened ONLYOFFICE candidate acceptance; never switches routing.

Run from the repository root:
    python -m tools.mcel_onlyoffice_hardened_candidate_acceptance
    python -m tools.mcel_onlyoffice_hardened_candidate_acceptance --focus tls-auth

The focused mode retries only TLS/SSO and the incumbent authentication proof tests.
This is an OFFLINE gate, not Windows Docker/Edge or promotion certification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEST_GROUPS = (
    'test_mcel_onlyoffice_hardened_candidate.py',
    'test_mcel_onlyoffice_hardened_handlers.py',
    'test_mcel_onlyoffice_hardened_tls_sso.py',
    'test_mcel_hardened_side_services.py',
    'test_onlyoffice_atomic_cleanup.py',
)


def _output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors='replace')
    return value or ''


def _execute(args: list[str], *, env: dict[str, str], deadline: int) -> tuple[str, str]:
    try:
        result = subprocess.run(args, cwd=ROOT, env=env, text=True,
                                capture_output=True, timeout=deadline, check=False)
    except subprocess.TimeoutExpired as exc:
        return 'UNVERIFIED', (_output(exc.stdout) + _output(exc.stderr) +
                              f'\nUNVERIFIED SUBPROCESS_TIMEOUT limit_seconds={deadline}\n')
    return ('PASS' if result.returncode == 0 else 'FAIL',
            result.stdout + result.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--focus', choices=('all', 'tls-auth'), default='all',
                        help='rerun TLS/SSO and the incumbent authentication proofs only')
    opts = parser.parse_args(argv)
    when = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report_dir = ROOT / 'runtime' / 'reports' / 'mcel-onlyoffice-hardened-candidate' / when
    report_dir.mkdir(parents=True, exist_ok=True)
    records = []
    env = dict(os.environ)
    env.pop('MAIN_COMPUTER_HARDENED_CANDIDATE', None)
    patterns = (TEST_GROUPS if opts.focus == 'all'
                else ('test_mcel_onlyoffice_hardened_tls_sso.py',))
    for pattern in patterns:
        args = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v']
        # The TLS test child now permits 660 s on Windows, including real ACL checks.
        # The parent leaves time for unittest startup and cleanup.
        deadline = (720 if os.name == 'nt' and pattern == 'test_mcel_onlyoffice_hardened_tls_sso.py'
                    else 300 if os.name == 'nt' else 90)
        status, output = _execute(args, env=env, deadline=deadline)
        log = report_dir / (pattern + '.log')
        log.write_text(output, encoding='utf-8')
        print(f'MC_OO_HARDENED {pattern} {status} log={log}', flush=True)
        records.append({'test': pattern, 'status': status})

    # Real Windows proof/session tests use hundreds of native ACL checks. A
    # single serial pytest process exceeded 360 s after only four completed
    # cases. Preserve ALL tests and real ACL behavior, but run them as separately
    # timed disposable fixtures with bounded concurrency and per-case evidence.
    auth_args = [sys.executable, '-m', 'tools.mcel_onlyoffice_auth_proof_fanout']
    status, output = _execute(auth_args, env=env,
                              deadline=1300 if os.name == 'nt' else 300)
    log = report_dir / 'auth_bootstrap_proof_session.log'
    log.write_text(output, encoding='utf-8')
    print(f'MC_OO_HARDENED incumbent-auth-proofs {status} log={log}', flush=True)
    records.append({'test': 'incumbent-auth-proofs', 'status': status})

    counts = {state: sum(item['status'] == state for item in records)
              for state in ('PASS', 'FAIL', 'UNVERIFIED')}
    if counts['PASS'] == len(records):
        verdict = ('PASS_OFFLINE_CANDIDATE_ONLY' if opts.focus == 'all'
                   else 'PASS_TLS_AUTH_ONLY')
    else:
        verdict = 'BLOCKED'
    summary = {'verdict': verdict, 'focus': opts.focus, 'counts': counts,
               'tests': records, 'production_cutover_performed': False,
               'production_routes_promoted': False, 'real_docker_tested': False,
               'real_windows_multi_lane_tested': False, 'report_dir': str(report_dir)}
    (report_dir / 'summary.json').write_text(
        json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('MC_OO_HARDENED SUMMARY ' + json.dumps(summary, sort_keys=True), flush=True)
    return 0 if verdict.startswith('PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
