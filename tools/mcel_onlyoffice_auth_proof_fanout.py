"""Run *every* incumbent proof/session test in an isolated process.

Windows proof tests exercise real native ACL hardening, which invokes separate
PowerShell and icacls processes many times. Running the eight cases serially
under one blanket deadline can time out without identifying the slow case.

This runner does not stub Windows ACLs, skip tests, contact production services,
or enable candidate routing. It uses the existing pytest tests unchanged with
disposable pytest tmp_path state and a unique ephemeral TLS port per test.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROOF_TEST = Path('tests/test_auth_bootstrap_proof_session_binding.py')
# Guard against accidentally passing a newly truncated or renamed test suite.
REQUIRED_CASES = frozenset({
    'test_deleting_proof_immediately_revokes_old_session_and_blocks_login',
    'test_proof_replacement_same_secret_still_revokes_session',
    'test_deleted_proof_during_shutdown_reissues_but_revokes_old_sessions',
    'test_wrong_key_and_corrupt_proof_deny_both_cookie_and_challenge',
    'test_pre_link_session_store_is_revoked_on_new_proof_format',
    'test_old_proof_link_and_cookie_are_revoked_without_migration',
    'test_reissuance_does_not_require_an_old_named_proof',
    'test_other_windows_user_cannot_be_modeled_by_disposable_directory',
})


@dataclass(frozen=True)
class Outcome:
    name: str
    status: str
    seconds: float
    output: str


def discover_cases(path: Path) -> list[str]:
    module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    names = [node.name for node in module.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name.startswith('test_')]
    missing = REQUIRED_CASES.difference(names)
    if missing or len(names) != len(set(names)) or not names:
        raise RuntimeError('AUTH_PROOF_SUITE_INCOMPLETE missing=' + ','.join(sorted(missing)))
    if any(not re.fullmatch(r'test_[a-zA-Z0-9_]+', name) for name in names):
        raise RuntimeError('AUTH_PROOF_INVALID_TEST_NAME')
    return names


def _run_case(name: str, *, root: Path, env: dict[str, str], deadline: int) -> Outcome:
    cmd = [sys.executable, '-m', 'pytest', '-q', '-s',
           f'{PROOF_TEST.as_posix()}::{name}']
    started = time.monotonic()
    try:
        result = subprocess.run(cmd, cwd=root, env=env,
                                text=True, capture_output=True, check=False,
                                timeout=deadline)
        output = (result.stdout or '') + (result.stderr or '')
        status = 'PASS' if result.returncode == 0 else 'FAIL'
    except subprocess.TimeoutExpired as exc:
        def decoded(value: bytes | str | None) -> str:
            return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
        output = (decoded(exc.stdout) + decoded(exc.stderr) +
                  f'\nUNVERIFIED CASE_TIMEOUT seconds={deadline} case={name}\n')
        status = 'UNVERIFIED'
    return Outcome(name, status, round(time.monotonic() - started, 2), output)


def run_suite(*, root: Path = ROOT, report_dir: Path,
              workers: int = 3, case_timeout: int = 360,
              run_case=None) -> dict:
    cases = discover_cases(root / PROOF_TEST)
    report_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop('MAIN_COMPUTER_HARDENED_CANDIDATE', None)
    probe = _run_case if run_case is None else run_case
    results: dict[str, Outcome] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(cases))) as pool:
        futures = {pool.submit(probe, name, root=root, env=env,
                               deadline=case_timeout): name for name in cases}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = Outcome(name, 'FAIL', 0.0,
                                 f'RUNNER_EXCEPTION {type(exc).__name__}: {exc}\n')
            results[name] = result
            log_path = report_dir / (name + '.log')
            log_path.write_text(result.output, encoding='utf-8')
            print(f'MC_OO_AUTH_PROOF {name} {result.status} '
                  f'seconds={result.seconds:.2f} log={log_path}', flush=True)
    counts = {key: sum(outcome.status == key for outcome in results.values())
              for key in ('PASS', 'FAIL', 'UNVERIFIED')}
    summary = {
        'status': 'PASS' if counts['PASS'] == len(cases) else 'BLOCKED',
        'counts': counts, 'cases_expected': len(cases),
        'cases': [{'name': name, 'status': results[name].status,
                   'seconds': results[name].seconds} for name in cases],
        'isolated_disposable_test_state': True,
        'windows_acl_checks_stubbed': False,
        'production_modified': False,
        'production_cutover_performed': False,
        'report_dir': str(report_dir),
    }
    (report_dir / 'summary.json').write_text(
        json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('MC_OO_AUTH_PROOF SUMMARY ' + json.dumps(summary, sort_keys=True), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--case-timeout', type=int, default=360)
    args = parser.parse_args(argv)
    if not (1 <= args.workers <= 4 and 30 <= args.case_timeout <= 1200):
        parser.error('workers must be 1..4; case-timeout must be 30..1200 seconds')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = ROOT / 'runtime/reports/mcel-onlyoffice-auth-proofs' / stamp
    verdict = run_suite(report_dir=path, workers=args.workers,
                        case_timeout=args.case_timeout)
    return 0 if verdict['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
