"""Live, actual-host functional lane for the existing MCEL test battery.

Usage:
  python -m tools.mcel_functional_live_acceptance --host-url http://127.0.0.1:8765
  python -m tools.mcel_functional_live_acceptance --host-url http://127.0.0.1:8765 --app slh --protected-reads

All requests are browser-routed through fail-closed read allowlists. Identity
approval/rejection, deployment/undeployment, file/process mutations and Aider
writes are NEVER attempted. Protected reads require explicit opt-in because
they create an authentication session. Host must be explicit loopback.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.mcel_four_app_live_readonly_frob import APPS as FOUR_APPS, run as four_app_run
from tools.mcel_slh_live_readonly_frob import run as slh_run
from tools.mcel_v1_four_app_hosted_acceptance import validate_loopback_base

ALL_APPS = (*FOUR_APPS, 'slh')
DEFAULT_REPORT = Path('runtime/reports/mcel-functional-live-acceptance/report.json')


def run(host_url: str, report: Path = DEFAULT_REPORT, apps: tuple[str, ...] = ALL_APPS,
        *, protected_reads: bool = False) -> dict:
    base = validate_loopback_base(host_url)
    if not apps or len(set(apps)) != len(apps) or any(app not in ALL_APPS for app in apps):
        raise ValueError('Select one or more distinct supported applications')
    if protected_reads and 'slh' not in apps:
        raise ValueError('--protected-reads requires an SLH selection')
    report.parent.mkdir(parents=True, exist_ok=True)
    output = {'schema': 'mcel.functional-live-acceptance.v1',
              'host': base, 'scope': 'real-host-read-only-no-mutations',
              'status': 'INCOMPLETE', 'apps': {},
              'requested': list(apps), 'protected_reads': protected_reads,
              'mutating_acceptance': 'NOT_RUN_NEEDS_ISOLATED_FIXTURES'}
    selected = tuple(app for app in apps if app in FOUR_APPS)
    if selected:
        try:
            four = four_app_run(base, report.parent / 'four-app-evidence.json', selected)
            output['apps'].update({name: {'status': item['status'],
                                          'evidence': 'four-app-evidence.json'}
                                   for name, item in four['apps'].items()})
        except Exception as exc:
            # Failure to launch a browser or reach a host is not a skip/pass.
            for name in selected:
                output['apps'][name] = {'status': 'INCOMPLETE',
                                        'failure_class': type(exc).__name__}
    if 'slh' in apps:
        try:
            slh = slh_run(base, report.parent / 'slh-evidence.json',
                          protected_reads=protected_reads)
            output['apps']['slh'] = {'status': slh['status'],
                                    'protected_reads': slh['protected_reads'],
                                    'evidence': 'slh-evidence.json'}
        except Exception as exc:
            output['apps']['slh'] = {'status': 'INCOMPLETE',
                                    'failure_class': type(exc).__name__}
    output['status'] = ('PASS_LIVE_READ_ONLY' if len(output['apps']) == len(apps) and
                        all(v['status'] == 'PASS_LIVE_READ_ONLY' for v in output['apps'].values())
                        else 'INCOMPLETE')
    report.write_text(json.dumps(output, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-url', required=True, help='Explicit http://127.0.0.1:<port>')
    parser.add_argument('--app', choices=ALL_APPS, action='append', help='Repeatable; defaults to all five apps')
    parser.add_argument('--protected-reads', action='store_true',
                        help='Allow SLH authentication and *read-only* protected notebook inspection')
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    try:
        result = run(args.host_url, args.report, tuple(args.app or ALL_APPS),
                     protected_reads=args.protected_reads)
    except ValueError as exc:
        parser.error(str(exc))
    for app, outcome in result['apps'].items():
        print('MCEL_LIVE_FUNCTIONAL', app, outcome['status'], flush=True)
    print('MCEL_LIVE_FUNCTIONAL_OVERALL', result['status'], 'report:', args.report)
    return 0 if result['status'] == 'PASS_LIVE_READ_ONLY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
