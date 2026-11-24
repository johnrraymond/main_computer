"""Narrow, read-only ONLYOFFICE pre-cutover failure recheck.

Usage from Main Computer checkout:
    python -m tools.mcel_onlyoffice_failed_acceptance run --focus restart-sensitive
    python -m tools.mcel_onlyoffice_failed_acceptance run --focus source-blockers
    python -m tools.mcel_onlyoffice_failed_acceptance run --focus combined

This deliberately DOES NOT run the full certification, promote handlers, or
change routing. It rechecks the three potentially interruption-related failures
in the Nov 23 full report, or (on request) the previously failing source gates.
A focused PASS must never be interpreted as production readiness.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
from typing import Any

from tools.mcel_onlyoffice_pre_cutover_certification import (
    ROOT, command_run, fingerprint, marker_summary,
)

REPORT_BASE = ROOT / 'runtime' / 'reports' / 'mcel-onlyoffice-failed-acceptance'
PREVIOUS_FULL_RUN = '20261123T192357Z-ae8b79'


@dataclass(frozen=True)
class Stage:
    name: str
    purpose: str
    command: tuple[str, ...]
    marker: str
    timeout: int
    category: str  # restart-sensitive or source-blocker


def stages(tag: str) -> tuple[Stage, ...]:
    py = sys.executable
    lab = (py, '-m', 'tools.mcel_onlyoffice_real_editor_lab', 'run', '--onlyoffice-tag', tag)
    return (
        Stage('offline-final-close-journal', 'Recheck the Windows WinError 10053 positive-control failure',
              (py, '-m', 'tools.test_mcel_onlyoffice_final_close_journal'),
              'MC_OO_FINAL_CLOSE_JOURNAL SUMMARY ', 180, 'restart-sensitive'),
        Stage('docker-real-documentserver-editor', 'Verify real editor changes the workbook and reopens saved bytes',
              lab, 'MC_ONLYOFFICE_REAL SUMMARY ', 1500, 'restart-sensitive'),
        Stage('docker-shadow-revision-editor', 'Verify real editor save/reopen through the shadow revision fence',
              (*lab, '--shadow-revision'), 'MC_ONLYOFFICE_REAL SUMMARY ', 1500, 'restart-sensitive'),
        Stage('offline-storage-and-revision-faults', 'Recheck atomic-write cleanup and stale callback rejection in original source',
              (py, '-m', 'tools.mcel_onlyoffice_pre_cutover_storage'),
              'MC_OO_STORAGE SUMMARY ', 120, 'source-blocker'),
        Stage('source-hostname-and-auth-acceptance', 'Recheck production hostnames, auth and document capabilities',
              (py, '-m', 'tools.mcel_security_ingress_smoke', 'run', '--hostname-only'),
              'MC_SECURITY_INGRESS SUMMARY ', 240, 'source-blocker'),
        Stage('source-ingress-baseline', 'Recheck production callback/download and other ingress bypass canaries',
              (py, '-m', 'tools.mcel_security_ingress_smoke', 'run'),
              'MC_SECURITY_INGRESS SUMMARY ', 240, 'source-blocker'),
    )


def select_stages(focus: str, tag: str) -> tuple[Stage, ...]:
    if focus not in ('restart-sensitive', 'source-blockers', 'combined'):
        raise ValueError(f'unknown focus: {focus}')
    return tuple(s for s in stages(tag) if focus == 'combined' or s.category == (
        'restart-sensitive' if focus == 'restart-sensitive' else 'source-blocker'))


def check_stage(stage: Stage, result: dict[str, Any], log: str) -> tuple[str, str]:
    """Validate actual acceptance evidence, not just a process exit code."""
    if result.get('timeout'):
        return 'UNVERIFIED', 'TIMEOUT'
    if result.get('launch_error'):
        return 'UNVERIFIED', 'LAUNCH_ERROR'
    summary = result.get('summary')
    if not isinstance(summary, dict):
        return 'UNVERIFIED', 'NO_SUMMARY'
    if result.get('returncode') != 0 or summary.get('status') != 'PASS':
        if 'WinError 10053' in log or 'ConnectionAbortedError' in log:
            return 'FAIL', 'HOST_CONNECTION_ABORT'
        if 'genuine-editor-callback-saved-changed-cell FAIL' in log:
            return 'FAIL', 'EDITOR_SAVE_UNCHANGED'
        if stage.category == 'source-blocker':
            return 'FAIL', 'SOURCE_BLOCKER_REPRODUCED'
        return 'FAIL', 'ACCEPTANCE_ASSERTION_FAILED'

    if stage.name == 'offline-final-close-journal':
        valid = (summary.get('tests') == 16 and summary.get('failures') == 0
                 and summary.get('errors') == 0 and summary.get('production_modified') is False
                 and 'test_source_backed_http_status2_admission_and_replay' in log)
        return ('PASS', 'JOURNAL_16_OF_16') if valid else ('UNVERIFIED', 'INCOMPLETE_JOURNAL_PROOF')
    if stage.name.startswith('docker-'):
        required = [
            'genuine-editor-callback-saved-changed-cell PASS',
            'reopened-workbook-cell-persisted PASS',
            'editor-origin-never-received-mc-session-cookie PASS',
            'credentialless-bob-container-attacks-blocked PASS',
        ]
        if stage.name == 'docker-shadow-revision-editor':
            required.extend([
                'shadow-real-editor-callback-committed-through-revision-fence PASS',
                'shadow-replayed-old-grant-rejected-after-real-editor-save PASS',
            ])
        valid = (summary.get('real_documentserver_edit_save_reopen') is True
                 and summary.get('production_modified') is False
                 and all(item in log for item in required)
                 and (stage.name != 'docker-shadow-revision-editor'
                      or summary.get('shadow_revision_fence') is True))
        return ('PASS', 'REAL_EDIT_SAVE_REOPEN_VERIFIED') if valid else ('UNVERIFIED', 'INCOMPLETE_EDITOR_PROOF')
    if stage.name == 'offline-storage-and-revision-faults':
        valid = (summary.get('fails') == 0 and not summary.get('failed')
                 and summary.get('production_modified') is False)
        return ('PASS', 'SOURCE_FAULTS_CLEAR') if valid else ('UNVERIFIED', 'INCOMPLETE_STORAGE_PROOF')
    if stage.name.startswith('source-'):
        counts = summary.get('counts') or {}
        if summary.get('failed') or counts.get('FAIL', 0):
            return 'FAIL', 'SOURCE_BLOCKER_REPRODUCED'
        if summary.get('unverified') or counts.get('UNVERIFIED', 0):
            return 'UNVERIFIED', 'SOURCE_CHECKS_STILL_UNVERIFIED'
        if summary.get('production_modified') is False:
            return 'PASS', 'SOURCE_CHECKS_CLEAR'
        return 'UNVERIFIED', 'INCOMPLETE_SOURCE_PROOF'
    return 'UNVERIFIED', 'UNKNOWN_STAGE'


def run(focus: str, tag: str, report_dir: Path, repeat: int = 1) -> int:
    if not 1 <= repeat <= 3:
        raise ValueError('repeat must be 1..3')
    report_dir.mkdir(parents=True, exist_ok=False)
    chosen = select_stages(focus, tag)
    before = fingerprint()
    started = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    interrupted = False
    try:
        for stage in chosen:
            for attempt in range(1, repeat + 1):
                filename = f'{stage.name}{"-repeat-" + str(attempt) if repeat > 1 else ""}.log'
                print(f'MC_OO_FOCUSED {stage.name} START attempt={attempt}/{repeat}', flush=True)
                if stage.name.startswith('docker-') and not shutil.which('docker'):
                    process: dict[str, Any] = {'command': list(stage.command), 'returncode': None,
                                               'timeout': False, 'launch_error': 'Docker unavailable'}
                    log = ''
                else:
                    process = command_run(list(stage.command), report_dir / filename, stage.timeout)
                    log = (report_dir / filename).read_text(encoding='utf-8', errors='replace')
                process['summary'] = marker_summary(log, stage.marker)
                status, reason = check_stage(stage, process, log)
                rows.append({'stage': stage.name, 'category': stage.category,
                             'attempt': attempt, 'status': status, 'reason': reason,
                             'purpose': stage.purpose,
                             'log': filename if (report_dir / filename).exists() else None,
                             **process})
                print(f'MC_OO_FOCUSED {stage.name} {status} reason={reason} attempt={attempt}/{repeat}', flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print('MC_OO_FOCUSED INTERRUPTED; no readiness claim permitted', flush=True)
    finally:
        after = fingerprint()
        unchanged = before == after
        counts = {state: sum(row['status'] == state for row in rows)
                  for state in ('PASS', 'FAIL', 'UNVERIFIED')}
        verdict = ('PASS_SUBSET_ONLY' if not interrupted and unchanged
                   and len(rows) == len(chosen) * repeat and counts['FAIL'] == 0
                   and counts['UNVERIFIED'] == 0 else 'BLOCKED')
        payload = {
            'verdict': verdict, 'focus': focus, 'baseline_full_run': PREVIOUS_FULL_RUN,
            'onlyoffice_image_tag': tag, 'repeat': repeat,
            'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
            'counts': counts, 'stages': rows,
            'production_source_unchanged': unchanged,
            'production_cutover_performed': False,
            'full_pre_cutover_certification': 'NOT_RUN',
            'uncovered_gates_remain_blocking': True,
            'limitations': [
                'Only the selected stages were rerun; a subset PASS is not full certification',
                'Source findings are not disproved by a passing disposable editor experiment',
                'No real Windows second-account or reboot lifecycle test is included',
                'The runner does not attribute a failure to MC restarts without direct evidence',
            ],
        }
        (report_dir / 'summary.json').write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
        with (report_dir / 'summary.txt').open('w', encoding='utf-8') as output:
            output.write(f'MC ONLYOFFICE FOCUSED ACCEPTANCE: {verdict}\n')
            output.write(f'focus={focus} PASS={counts["PASS"]} FAIL={counts["FAIL"]} UNVERIFIED={counts["UNVERIFIED"]}\n')
            for row in rows:
                output.write(f'{row["status"]} {row["stage"]} {row["reason"]}\n')
            output.write('PRODUCTION CUTOVER NOT PERFORMED. FULL CERTIFICATION NOT RUN.\n')
        print('MC_OO_FOCUSED SUMMARY ' + json.dumps({
            'verdict': verdict, 'focus': focus, 'counts': counts,
            'production_source_unchanged': unchanged,
            'production_cutover_performed': False, 'report_dir': str(report_dir),
        }, sort_keys=True), flush=True)
    return 130 if interrupted else 0 if verdict == 'PASS_SUBSET_ONLY' else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='run', choices=('run',))
    parser.add_argument('--focus', choices=('restart-sensitive', 'source-blockers', 'combined'),
                        default='restart-sensitive')
    parser.add_argument('--onlyoffice-tag', default=os.environ.get('MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG', 'latest'))
    parser.add_argument('--repeat', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--report-dir', type=Path)
    args = parser.parse_args(argv)
    report_dir = args.report_dir or (REPORT_BASE / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)))
    if report_dir.exists():
        parser.error(f'report directory already exists: {report_dir}')
    return run(args.focus, args.onlyoffice_tag, report_dir, args.repeat)


if __name__ == '__main__':
    raise SystemExit(main())
