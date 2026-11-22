"""Evidence-driven native MCEL Worker functional acceptance.

Based on the SLH case-by-case supervisor and Task Manager isolated functional
runner. A mock browser or disposable service is *never* live cutover proof.

Examples:
  python -m tools.mcel_worker_full_functional_acceptance --mode inventory
  python -m tools.mcel_worker_full_functional_acceptance --mode isolated --profile smoke
  python -m tools.mcel_worker_full_functional_acceptance --mode isolated --profile full --timeout 120
  python -m tools.mcel_worker_full_functional_acceptance --mode isolated --case marketplace-ports

Every case runs in its own bounded pytest subprocess. Reports are updated after
each case and are scoped to the exact tested source/test fingerprint. A missing,
skipped, timed-out, or unexecuted assertion can never count as functional proof.
The runner never starts a Hub, signs a transaction or modifies production state.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

from main_computer.mcel_worker_application import compile_worker_application

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = Path('runtime/reports/mcel-worker-full-functional/report.json')
SCHEMA = 'mcel.worker.full-functional.v1'


@dataclass(frozen=True)
class Case:
    id: str
    lane: str
    node: str
    intents: tuple[str, ...]
    proves: str
    smoke: bool = False


def case(id: str, lane: str, node: str, intents: str, proves: str, smoke=False) -> Case:
    return Case(id, lane, node, tuple('intent:' + token for token in intents.split()), proves, smoke)


# Attach a test to an intent only when its named assertion exercises that action.
# Checking that a control exists is NOT an execution acceptance test.
CASES = (
    case('native-package', 'contract', 'mcel_apps/worker/tests/test_acceptance.py::test_package_acceptance_candidate', '', 'compiled source has typed capabilities and semantic layout', True),
    case('route-and-wallet-ownership', 'contract', 'tests/test_mcel_worker_native_migration.py::test_worker_semantic_page_uses_live_mcel_runtime_without_legacy_worker_ownership', '', 'native route uses runtime; legacy full controller is not mounted'),
    case('observation-isolated', 'adapter', 'tests/test_mcel_worker_native_migration.py::test_native_worker_observation_is_redacted_and_read_only', 'refresh-destinations refresh-destination refresh-policy refresh-runtime refresh-slh-target', 'read-only Worker observation excludes signing material and Hub mutation'),
    case('observation-local-only', 'adapter', 'tests/test_mcel_worker_native_migration.py::test_native_worker_observation_denies_remote_clients', '', 'observation refuses nonlocal access'),
    case('operation-admission', 'adapter', 'tests/test_mcel_worker_native_migration.py::test_native_worker_port_enforces_operation_identity_and_minimal_settings_patch', 'select-destination disconnect save-policy save-slh-target commit-hub-choice', 'typed capability boundary validates operation identity and scoped settings'),
    case('hub-choice-ui', 'ui', 'tests/test_mcel_worker_hub_navigation.py::test_hub_choices_and_slh_target_are_separate_from_navigation_and_authorization', 'switch-view choose-network commit-hub-choice refresh-destinations refresh-destination refresh-slh-target save-slh-target', 'real Chromium radio choice, persistence, target save and navigation', True),
    case('ring-configuration-ui', 'ui', 'tests/test_mcel_worker_hub_navigation.py::test_worker_ring_config_remains_separate_from_automatic_hub_selection', 'select-destination', 'Worker ring form posts network session and verifies result'),
    case('hub-refusal-ui', 'ui', 'tests/test_mcel_worker_hub_navigation.py::test_refused_hub_choice_has_visible_diagnostic_in_hub_workarea', 'commit-hub-choice', 'refused Hub write rolls back choice and exposes failure'),
    case('hub-rapid-changes-ui', 'ui', 'tests/test_mcel_worker_hub_navigation.py::test_rapid_radio_changes_commit_last_choice_without_duplicate_concurrent_mutations', 'choose-network', 'rapid changes retain final Hub selection'),
    case('identity-inventory-adapter', 'adapter', 'tests/test_mcel_worker_identity_navigation.py::test_worker_identity_inventory_is_guarded_and_separate_from_worker_observation', 'refresh-identities refresh-identity-status', 'canonical guarded LMS identity inventory, not fabricated credentials'),
    case('identity-selection-ui', 'ui', 'tests/test_mcel_worker_identity_navigation.py::test_browser_identity_choice_visible_in_worker_navigation_and_not_an_enrollment', 'choose-identity', 'selection is local state and cannot enroll or authenticate'),
    case('identity-availability-ui', 'ui', 'tests/test_mcel_worker_identity_navigation.py::test_unavailable_identity_directory_is_not_presented_as_empty_verified_inventory', 'refresh-identities refresh-identity-status', 'unavailable directory does not impersonate an empty verified inventory'),
    case('identity-startup-ui', 'ui', 'tests/test_mcel_worker_identity_startup_presentation.py::test_startup_recovers_a_first_failed_identity_read_without_twiddle', 'refresh-identities refresh-identity-status', 'browser retries transient identity read and populates navigation', True),
    case('presentation-policy-ui', 'ui', 'tests/test_mcel_worker_identity_startup_presentation.py::test_policy_applies_to_worker_main_surface_without_affecting_wallet', '', 'all three presentation laws reach Worker candidate, not legacy registry'),
    case('wallet-msk-ui', 'ui', 'tests/test_mcel_worker_wallet_compat.py::test_wallet_browser_mount_and_msk_actions_without_implicit_worker_mutations', '', 'legacy wallet/MSK buttons invoke existing handlers without startup mutation', True),
    case('wallet-hub-guard-ui', 'ui', 'tests/test_mcel_worker_wallet_compat.py::test_wallet_controls_refuse_mismatched_connected_hub', '', 'mismatched Hub disables wallet signing'),
    case('wallet-choice-ui', 'ui', 'tests/test_mcel_worker_wallet_compat.py::test_hub_navigation_and_wallet_workspace_follow_radio_before_server_save', '', 'wallet placement tracks local Hub selection without early signing'),
    case('slh-placeholder-ui', 'ui', 'tests/test_mcel_worker_url_placeholder.py::test_endpoint_url_placeholder_never_becomes_saved_value', '', 'localhost URL hint never persists as a configured endpoint'),
    case('compact-navigation-ui', 'ui', 'tests/test_mcel_worker_visual_density.py::test_worker_layout_preserves_navigation_radio_semantics_and_responsive_structure', '', 'Hub and identity panes remain contained at narrow viewport'),
    case('marketplace-ports', 'adapter', 'tests/test_mcel_worker_marketplace_parity.py::test_native_worker_marketplace_ports_preserve_legacy_authority_and_scoped_settings', 'refresh-seller refresh-requester refresh-marketplace-status save-seller save-requester register-offer work-now finish-work-now', 'real JS adapter verifies backend reads, scoped saves, signed operation bridge and authorization refusal', True),
    case('seller-requester-ui', 'ui', 'tests/test_mcel_worker_marketplace_parity.py::test_rendered_native_marketplace_roles_and_explicit_actions', 'refresh-seller refresh-requester save-seller save-requester', 'Chromium saves seller and requester settings in distinct forms', True),
    # Note: this existing test only checks button visibility for Register and
    # Finish; their *execution* is proved by marketplace-ports (adapter lane).
    # No UI execution claim is assigned until the controls are clicked in a test.
    case('marketplace-action-ui', 'ui', 'tests/test_mcel_worker_functional_paths.py::test_marketplace_buttons_reach_existing_signer_and_finish_pause_are_distinct', 'register-offer work-now finish-work-now pause-selling refresh-marketplace-status', 'Chromium clicks marketplace actions into existing signer and explicit pause settings', True),
    case('hub-retry-disconnect-ui', 'ui', 'tests/test_mcel_worker_functional_paths.py::test_hub_retry_and_disconnect_are_explicit_and_preserve_saved_choice', 'retry-hub disconnect', 'Chromium retries, disconnects and reconciles active session without changing saved Hub choice'),
    case('pause-selling-adapter', 'adapter', 'tests/test_mcel_worker_remaining_functional_coverage.py::test_pause_selling_adapter_enforces_scoped_disable_and_verified_state', 'pause-selling', 'pause writes only seller/rental flags, verifies persisted state and refuses missing postcondition'),
    case('retry-hub-adapter', 'adapter', 'tests/test_mcel_worker_remaining_functional_coverage.py::test_retry_hub_adapter_reconciles_selection_and_refuses_unverified_retries', 'retry-hub', 'retry preserves saved Hub choice and ring, rejects SLH target, transport refusals and missing postcondition'),
    case('refresh-policy-ui', 'ui', 'tests/test_mcel_worker_remaining_functional_coverage.py::test_refresh_policy_ui_reads_current_server_policy_without_writing', 'refresh-policy', 'Chromium Refresh Policy reads current observed state without settings mutation'),
    case('refresh-runtime-ui', 'ui', 'tests/test_mcel_worker_remaining_functional_coverage.py::test_refresh_runtime_ui_reconciles_new_observation_without_writing', 'refresh-runtime', 'Chromium Refresh Runtime updates supervisor state without backend mutation'),
    case('save-policy-ui', 'ui', 'tests/test_mcel_worker_remaining_functional_coverage.py::test_save_policy_ui_scopes_settings_and_verifies_published_policy', 'save-policy', 'Chromium Apply Policy submits three scoped fields and publishes verified saved policy'),
    case('legacy-registration-backend', 'isolated-effect', 'tests/test_worker_registration_subpatch.py', 'register-offer', 'legacy Worker registration backend behavior against test fixture'),
)

LIVE_GATES = (
    {'id': 'wallet-msk-live', 'requires': 'real MetaMask connection, MSK request/revocation and signing against selected Hub'},
    {'id': 'offer-registration-live', 'requires': 'signed offer registration accepted by running Hub, including refusal handling'},
    {'id': 'work-cycle-live', 'requires': 'Work Now starts a real job, countdown and Finish/drain reconcile with Hub'},
    {'id': 'requester-enforcement-live', 'requires': 'Hub/requester backend enforces limits and refuses unauthorized spend'},
)


def inventory() -> dict:
    ir, plan = compile_worker_application(ROOT)
    if ir.get('application', {}).get('appId') != 'worker' or plan.get('policy') != 'mcel.semantic-structure.p3':
        raise RuntimeError('Worker semantic source or generated layout is not canonical')
    intents = list(ir.get('intents', []))
    if not intents or len({intent['id'] for intent in intents}) != len(intents):
        raise RuntimeError('missing or duplicate Worker intents')
    names = {intent['id'] for intent in intents}
    for item in CASES:
        if not item.node.startswith(('tests/', 'mcel_apps/worker/tests/')) or not item.node.split('::')[0].endswith('.py'):
            raise RuntimeError('invalid acceptance node: ' + item.id)
        if not (ROOT / item.node.split('::')[0]).is_file():
            raise RuntimeError('missing acceptance test: ' + item.node)
        if unknown := set(item.intents) - names:
            raise RuntimeError('stale acceptance map for ' + item.id + ': ' + ','.join(sorted(unknown)))
    return {'schema': 'mcel.worker.functional-inventory.v1',
            'source': 'mcel_apps/worker/application.js',
            'intents': [{'id': entry['id'], 'risk': entry.get('risk'),
                         'operationKind': entry.get('operationKind')} for entry in intents],
            'count': len(intents)}


def source_fingerprint() -> str:
    files = {'tools/mcel_worker_full_functional_acceptance.py',
             'mcel_apps/worker/application.js',
             'main_computer/mcel_worker_application.py',
             'main_computer/web/applications/scripts/mcel-semantic-worker-ports.js',
             'main_computer/web/applications/scripts/worker.js',
             'main_computer/viewport_routes_energy.py',
             'main_computer/viewport_route_dispatch.py',
             'main_computer/mcel_applications_registry.py',
             'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'}
    files.update(item.node.split('::')[0] for item in CASES)
    digest = hashlib.sha256()
    for file in sorted(files):
        path = ROOT / file
        digest.update(file.encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b'MISSING')
    return digest.hexdigest()


def run_case(item: Case, timeout: int) -> dict:
    """Run one case with a hard deadline, including spawned descendants."""
    started = time.monotonic()
    cmd = [sys.executable, '-m', 'pytest', '-q', '-ra', '--disable-warnings', item.node]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    with tempfile.TemporaryFile(mode='w+t', encoding='utf-8', errors='replace') as output:
        process = subprocess.Popen(cmd, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                   creationflags=flags, start_new_session=os.name != 'nt')
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == 'nt':
                try:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=10, check=False)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            code = None
        output.seek(0)
        log = output.read()
    passed = sum(int(n) for n in re.findall(r'\b(\d+) passed\b', log))
    skipped = sum(int(n) for n in re.findall(r'\b(\d+) skipped\b', log))
    status = ('TIMEOUT' if timed_out else 'PASS' if code == 0 and passed > 0 and skipped == 0
              else 'UNVERIFIED' if code == 0 else 'FAIL')
    return {'status': status, 'passedTests': passed, 'skippedTests': skipped,
            'seconds': round(time.monotonic() - started, 2), 'exitCode': code,
            'node': item.node, 'outputTail': log[-2200:] if status != 'PASS' else ''}


def required_lanes(intent: dict) -> set[str]:
    if intent['risk'] == 'local-state':
        return {'ui'}
    if intent['risk'] in ('external-read', 'consequential'):
        return {'adapter', 'ui'}
    return {'contract', 'ui'}


def summarize(results: dict, *, profile: str, selected: list[Case], mode: str) -> dict:
    source = inventory()
    rows = []
    for intent in source['intents']:
        lanes = {}
        for lane in sorted(required_lanes(intent)):
            supporting = [x.id for x in CASES if x.lane == lane and intent['id'] in x.intents]
            statuses = [results.get(name, {}).get('status', 'NOT_RUN') for name in supporting]
            lane_status = ('FAIL' if any(x in ('FAIL', 'TIMEOUT') for x in statuses) else
                           'UNVERIFIED' if any(x == 'UNVERIFIED' for x in statuses) else
                           'PASS' if statuses and all(x == 'PASS' for x in statuses) else 'NOT_TESTED')
            lanes[lane] = {'status': lane_status, 'evidence': supporting}
        lane_statuses = {v['status'] for v in lanes.values()}
        status = ('FAIL' if 'FAIL' in lane_statuses else
                  'UNVERIFIED' if 'UNVERIFIED' in lane_statuses else
                  'PASS' if lane_statuses == {'PASS'} else 'NOT_TESTED')
        rows.append({**intent, 'status': status, 'lanes': lanes})
    counts = {s: sum(row['status'] == s for row in rows)
              for s in ('PASS', 'FAIL', 'NOT_TESTED', 'UNVERIFIED')}
    case_results = [{**{'id': item.id, 'lane': item.lane, 'assertion': item.proves,
                        'node': item.node, 'intents': list(item.intents)},
                     'result': results.get(item.id, {'status': 'NOT_RUN'})}
                    for item in CASES]
    checked = [results[item.id] for item in selected if item.id in results]
    run_status = ('NOT_RUN' if mode == 'inventory' or not checked else
                  'FAIL' if any(x['status'] in ('FAIL', 'TIMEOUT') for x in checked) else
                  'UNVERIFIED' if any(x['status'] == 'UNVERIFIED' for x in checked) else
                  'INCOMPLETE' if len(checked) < len(selected) else 'PASS')
    isolated_status = ('FAIL' if run_status == 'FAIL' else
                       'INCOMPLETE' if run_status != 'PASS' or counts['NOT_TESTED'] or counts['UNVERIFIED'] else
                       'PASS_ISOLATED_FUNCTIONAL')
    return {'schema': SCHEMA, 'generatedAt': datetime.now(timezone.utc).isoformat(),
            'mode': mode, 'profile': profile,
            'source_fingerprint': source_fingerprint(),
            'status': isolated_status, 'executionStatus': run_status,
            'intentCoverage': {'status': 'COMPLETE' if counts['PASS'] == source['count'] else 'INCOMPLETE',
                               'declared': source['count'], 'counts': counts,
                               'intents': rows},
            'cases': case_results,
            'passedTests': sum(x['passedTests'] for x in checked),
            'liveGates': [{**gate, 'status': 'NOT_TESTED'} for gate in LIVE_GATES],
            'cutover': {'status': 'BLOCKED',
                        'reason': 'Real MetaMask/MSK, Hub registration, real work and requester enforcement are not certified',
                        'canonicalWorkerRouteChanged': False},
            'safety': {'hostMutations': 'NEVER_DISPATCHED', 'testExecution': 'isolated pytest subprocesses',
                       'walletSignatures': 'MOCKED_OR_NOT_RUN', 'productionHubConnection': 'NEVER_DISPATCHED'}}


def main(argv=None) -> int:
    incoming = list(sys.argv[1:] if argv is None else argv)
    # The live lane has a separate parser, report and explicit authorizations.
    # It may not inherit the isolated subprocess runner's safety assumptions.
    if any(incoming[i:i+2] == ['--mode', 'live'] for i in range(len(incoming)-1)):
        from tools.mcel_worker_live_functional_acceptance import main as live_main
        return live_main(incoming)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('inventory', 'isolated'), default='isolated')
    parser.add_argument('--profile', choices=('smoke', 'full'), default='full')
    parser.add_argument('--timeout', type=int, default=120, help='hard timeout per test case in seconds')
    parser.add_argument('--case', action='append', default=[], help='case ID; repeat to select more than one')
    parser.add_argument('--max-cases', type=int, default=0, help='run at most this many pending cases; 0=unlimited')
    parser.add_argument('--resume', action='store_true', help='reuse only results from same exact source fingerprint')
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    if not 5 <= args.timeout <= 900 or args.max_cases < 0:
        parser.error('--timeout must be 5..900 and --max-cases must be >=0')
    names = {x.id for x in CASES}
    if unknown := set(args.case) - names:
        parser.error('unknown case(s): ' + ', '.join(sorted(unknown)))
    try:
        catalog = inventory()
    except Exception as exc:
        print('WORKER_FUNCTIONAL_INVENTORY_ERROR', type(exc).__name__, str(exc), flush=True)
        return 2
    print('WORKER_FUNCTIONAL_INVENTORY', 'intents=' + str(catalog['count']),
          'cases=' + str(len(CASES)), flush=True)
    targets = [x for x in CASES if not args.case or x.id in args.case]
    if args.profile == 'smoke' and not args.case:
        targets = [x for x in targets if x.smoke]
    target = args.report if args.report.is_absolute() else ROOT / args.report
    target.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = source_fingerprint()
    results = {}
    if args.resume and target.is_file():
        previous = json.loads(target.read_text(encoding='utf-8'))
        if previous.get('schema') != SCHEMA or previous.get('source_fingerprint') != fingerprint:
            print('WORKER_FUNCTIONAL_RESUME_REFUSED source or test changed', flush=True)
            return 2
        results = {item['id']: item['result'] for item in previous.get('cases', [])
                   if item.get('result', {}).get('status') in ('PASS', 'FAIL', 'UNVERIFIED', 'TIMEOUT')}
    def checkpoint():
        report = summarize(results, profile=args.profile, selected=targets, mode=args.mode)
        temp = target.with_name(target.name + '.partial')
        temp.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        temp.replace(target)
        return report
    checkpoint()
    if args.mode == 'inventory':
        for row in catalog['intents']:
            print('WORKER_FUNCTIONAL_INTENT', row['id'], 'risk=' + str(row['risk']))
        print('WORKER_FUNCTIONAL_OVERALL INVENTORY_ONLY report=' + str(target))
        return 0
    pending = 0
    for item in targets:
        if args.resume and item.id in results and results[item.id]['status'] == 'PASS':
            print('WORKER_FUNCTIONAL', item.id, 'REUSED_PASS', flush=True)
            continue
        if args.max_cases and pending >= args.max_cases:
            break
        pending += 1
        outcome = run_case(item, args.timeout)
        results[item.id] = outcome
        checkpoint()
        print('WORKER_FUNCTIONAL', item.id, outcome['status'],
              'passed=' + str(outcome['passedTests']),
              'seconds=' + str(outcome['seconds']), flush=True)
        if outcome['status'] != 'PASS':
            print('WORKER_FUNCTIONAL_FAILURE', item.id, outcome['outputTail'][-1200:], flush=True)
    report = checkpoint()
    print('WORKER_FUNCTIONAL_TESTS', report['executionStatus'],
          'passed=' + str(report['passedTests']), flush=True)
    counts = report['intentCoverage']['counts']
    print('WORKER_FUNCTIONAL_COVERAGE', report['intentCoverage']['status'],
          'intents=' + str(report['intentCoverage']['declared']),
          'covered=' + str(counts['PASS']),
          'untested=' + str(counts['NOT_TESTED']),
          'unverified=' + str(counts['UNVERIFIED']), flush=True)
    print('WORKER_FUNCTIONAL_CUTOVER BLOCKED live-gates=' + str(len(LIVE_GATES)), flush=True)
    print('WORKER_FUNCTIONAL_OVERALL', report['status'], 'report=' + str(target), flush=True)
    # A selected/smoke battery can pass independently while the complete
    # coverage and live cutover gates correctly remain incomplete.
    if args.case or args.profile == 'smoke' or args.max_cases:
        return 0 if report['executionStatus'] == 'PASS' else 1
    return 0 if report['status'] == 'PASS_ISOLATED_FUNCTIONAL' else 1


if __name__ == '__main__':
    raise SystemExit(main())
