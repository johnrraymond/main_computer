"""Opt-in *real* Testnet acceptance for the native MCEL Worker.

Plan is read-only and does not provision wallets, start containers or mutate a
Hub. Execute uses a previously provisioned *disposable* official MetaMask test
profile, the real native Worker route, and the existing marketplace signer.
Each gate has its own report; a partial gate never certifies the cutover.

Plan:
 python -m tools.mcel_worker_full_functional_acceptance --mode live --action plan

First create a disposable MetaMask profile using the established wallet tool:
 python -m tools.worker_browser_acceptance cache-wallet

Then, after inspecting Testnet state and authorizing each real effect:
 python -m tools.mcel_worker_full_functional_acceptance --mode live \
   --action run --gate wallet-msk --authorize-wallet-signing
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import signal
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from tools import worker_browser_acceptance as legacy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = 'http://127.0.0.1:8765/applications/mcel-semantic-worker'
DEFAULT_REPORT = Path('runtime/reports/mcel-worker-live-functional/report.json')
GATES = ('wallet-msk', 'offer-registration', 'work-cycle', 'requester-enforcement')
SCHEMA = 'mcel.worker.live-functional.v1'
MAX_CREDIT_CEILING = 2


def valid_native_url(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or url.port != 8765
            or url.username is not None or url.password is not None
            or url.path != '/applications/mcel-semantic-worker' or url.query or url.fragment):
        raise ValueError('Live Worker target must be exactly http://127.0.0.1:8765/applications/mcel-semantic-worker')
    return value


def fingerprint() -> str:
    digest = hashlib.sha256()
    for rel in ('tools/mcel_worker_full_functional_acceptance.py',
                'tools/mcel_worker_live_functional_acceptance.py',
                'tools/worker_browser_acceptance/mcel_live_acceptance.mjs',
                'tools/worker_browser_acceptance/mcel_wallet_connect_evidence.mjs',
                'tools/worker_browser_acceptance/mcel_live_msk_inspection.mjs',
                'tools/worker_browser_acceptance/metamask_sidepanel_cdp.mjs',
                'tools/worker_browser_acceptance/create_wallet.mjs',
                'tools/worker_browser_acceptance/native_acceptance.mjs',
                'tools/worker_browser_acceptance/worker_work_cycle.mjs',
                'tools/worker_browser_acceptance/worker_wallet_approvals.mjs',
                'main_computer/web/applications/scripts/wallet.js',
                'main_computer/web/applications/scripts/worker.js',
                'main_computer/web/applications/scripts/mcel-semantic-worker-ports.js',
                'mcel_apps/worker/application.js' ):
        digest.update(rel.encode() + b'\0')
        item = ROOT / rel
        digest.update(hashlib.sha256(item.read_bytes()).digest() if item.exists() else b'MISSING')
    return digest.hexdigest()


def new_report(url: str, action: str, gate: str | None) -> dict:
    return {'schema': SCHEMA, 'generatedAt': datetime.now(timezone.utc).isoformat(),
            'source_fingerprint': fingerprint(), 'target': url,
            'surface': 'native-mcel', 'network': 'testnet',
            'action': action, 'requestedGate': gate, 'status': 'NOT_CERTIFIED',
            'gates': {name: {'status': 'NOT_TESTED'} for name in GATES},
            'cutover': {'status': 'BLOCKED', 'canonicalWorkerRouteChanged': False},
            'evidence': {'realWallet': 'NOT_PROVEN', 'realHub': 'NOT_PROVEN',
                         'realWork': 'NOT_PROVEN', 'realRequesterEnforcement': 'NOT_PROVEN'},
            'safety': {'maxTestnetCredits': MAX_CREDIT_CEILING,
                       'productionMutationPermitted': False,
                       'disposableWalletOnly': True, 'unmockedProviderRequired': True}}


def save(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(path.suffix + '.partial').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    path.with_suffix(path.suffix + '.partial').replace(path)


def safe_stage(row: dict | None) -> str:
    """Return a fixed-format stage token; never print raw browser errors."""
    value = row.get('stage') if isinstance(row, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value) else 'NOT_AVAILABLE'


def verify_evidence(row: dict, gate: str) -> tuple[bool, str]:
    if not isinstance(row, dict) or row.get('schema') != 'mcel.worker.native-live-evidence.v1':
        return False, 'wrong-evidence-schema'
    if row.get('gate') != gate or row.get('surface') != 'native-mcel' or row.get('network') != 'testnet':
        return False, 'wrong-surface-or-gate'
    if row.get('ok') is not True:
        return False, 'live-operation-failed'
    if row.get('cleanupOk') is not True:
        return False, 'browser-cleanup-unverified'
    # Live evidence has a bounded validity window; an old PASS cannot support
    # today's cutover decision after the Hub/account may have changed.
    try:
        when = datetime.fromisoformat(str(row['verifiedAt']).replace('Z', '+00:00'))
        age = datetime.now(timezone.utc) - when
        if when.tzinfo is None or not timedelta(seconds=-120) <= age <= timedelta(minutes=60):
            return False, 'stale-or-future-live-evidence'
    except (KeyError, TypeError, ValueError):
        return False, 'missing-live-verification-time'
    marks = row.get('proofs')
    required = {
        'wallet-msk': ('realExtension', 'testnetChain', 'walletConnected', 'hubMskVerified', 'hubMskRevoked'),
        'offer-registration': ('realExtension', 'testnetChain', 'hubMskVerified', 'hubOfferAccepted'),
        'work-cycle': ('realExtension', 'testnetChain', 'hubMskVerified', 'hubOfferAccepted',
                       'hubSessionLive', 'captainCompleted', 'workerDelivered'),
        'requester-enforcement': ('requesterPolicyRead', 'backendDeniedUnauthorizedSpend',
                                  'noPaidSubmission'),
    }[gate]
    if not isinstance(marks, dict) or any(marks.get(name) is not True for name in required):
        return False, 'missing-required-live-postcondition'
    # Requester limits must be enforced, not just displayed. A refused remote
    # overflow under disabled policy alone does not establish daily-budget
    # enforcement. Explicitly require both independent denial checks.
    if gate == 'requester-enforcement' and marks.get('budgetLimitDenied') is not True:
        return False, 'requester-budget-not-proven'
    return True, 'verified'


def provision_fresh_live_wallet(*, node: str, node_dir: Path, extension_dir: Path) -> tuple[Path, Path, Path]:
    """Onboard a new test-only account without overwriting any existing wallet.

    Preserve the isolated state if signing or remote revocation later fails:
    that account may still own an active Hub credential requiring recovery.
    The Node wallet factory remains the only generator of the secret.
    """
    root = legacy.WALLET_STATE / 'isolated-live-msk'
    if root.is_symlink():
        raise ValueError('Refusing symlinked isolated live wallet directory')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder = Path(tempfile.mkdtemp(prefix='wallet-msk-', dir=root))
    wallet_file = folder / 'wallet.json'
    profile_dir = folder / 'chromium-profile'
    marker_file = folder / 'profile-wallet-address.txt'
    base_env = {**os.environ,
                'MC_WORKER_TEST_WALLET_FILE': str(wallet_file),
                'MC_WORKER_TEST_PROFILE_DIR': str(profile_dir),
                'MC_WORKER_TEST_PROFILE_MARKER': str(marker_file),
                'MC_WORKER_TEST_EXTENSION_DIR': str(extension_dir),
                'MC_WORKER_TEST_ACTION': 'cache-wallet',
                'MC_WORKER_TEST_URL': DEFAULT_URL,
                'PYTHONDONTWRITEBYTECODE': '1'}
    # Do not leak seed material in console output or crash diagnostics.
    for command, limit, stage in (([node, str(node_dir / 'create_wallet.mjs')], 45, 'generation'),
                                  ([node, str(node_dir / 'native_acceptance.mjs')], 300, 'onboarding')):
        outcome = subprocess.run(command, cwd=node_dir, env=base_env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 check=False, timeout=limit)
        if outcome.returncode:
            raise ValueError('fresh-disposable-wallet-' + stage + '-failed')
    try:
        public = json.loads(wallet_file.read_text(encoding='utf-8'))['address']
        marker = marker_file.read_text(encoding='utf-8').strip()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('Fresh disposable wallet verification failed') from exc
    if (not isinstance(public, str) or
            re.fullmatch(r'0x[0-9a-fA-F]{40}', public) is None or
            marker.lower() != public.lower() or not profile_dir.is_dir()):
        raise ValueError('Fresh disposable profile has mismatched identity')
    return wallet_file, profile_dir, marker_file


def invoke_node(gate: str, report_path: Path, timeout: int, url: str,
                *, authorize_signing: bool, authorize_offer: bool,
                authorize_spend: bool, authorize_settings: bool,
                fresh_disposable_wallet: bool = False) -> tuple[int | None, str]:
    executable = legacy.shutil.which('node')
    if not executable:
        return None, 'Node.js missing'
    node_dir = ROOT / 'tools/worker_browser_acceptance'
    if not (node_dir / 'node_modules').is_dir():
        return None, 'Official MetaMask acceptance dependencies unavailable (run tools.worker_browser_acceptance install)'
    wallet_file, profile_dir, marker_file = legacy.WALLET_FILE, legacy.PROFILE_DIR, legacy.PROFILE_MARKER
    if gate != 'requester-enforcement':
        if not legacy.METAMASK_DIR.is_dir():
            return None, 'MetaMask test extension not installed; run tools.worker_browser_acceptance install first'
        try:
            extension_dir = legacy.ensure_metamask_extension()
        except legacy.AcceptanceError:
            return None, 'official-metamask-verification-refused'
        if fresh_disposable_wallet:
            # Always use an isolated newly provisioned account for this gate.
            # This is the legacy wallet factory and onboarding path, not a
            # replacement wallet implementation. An older active Hub key stays
            # untouched, and a failure cannot be misreported as fresh signing.
            if gate != 'wallet-msk':
                return None, 'fresh-wallet-only-allowed-for-wallet-msk'
            try:
                wallet_file, profile_dir, marker_file = provision_fresh_live_wallet(
                    node=executable, node_dir=node_dir, extension_dir=extension_dir)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                reason = str(exc)
                if reason not in ('fresh-disposable-wallet-generation-failed',
                                  'fresh-disposable-wallet-onboarding-failed',
                                  'fresh-disposable-wallet-identity-mismatch'):
                    reason = 'fresh-disposable-wallet-preparation-failed'
                return None, reason
        else:
            if not all(item.is_file() for item in (wallet_file, marker_file)) or not profile_dir.is_dir():
                return None, 'Disposable MetaMask profile not provisioned (run tools.worker_browser_acceptance cache-wallet)'
            # Verify cached identity without accessing or logging the secret.
            try:
                public_address = legacy.wallet_address()
                marker = marker_file.read_text(encoding='utf-8').strip().lower()
            except (legacy.AcceptanceError, OSError):
                return None, 'cached-disposable-wallet-unverified'
            if marker != public_address:
                return None, 'Cached browser profile and disposable wallet do not match'
    else:
        extension_dir = ''
    env = {**os.environ,
           'MC_WORKER_TEST_URL': url, 'MC_WORKER_TEST_WALLET_FILE': str(wallet_file),
           'MC_WORKER_TEST_PROFILE_DIR': str(profile_dir),
           'MC_WORKER_TEST_PROFILE_MARKER': str(marker_file),
           'MC_WORKER_TEST_EXTENSION_DIR': str(extension_dir),
           'MC_WORKER_LIVE_EVIDENCE': str(report_path),
           'MC_WORKER_LIVE_GATE': gate,
           'MC_WORKER_LIVE_AUTHORIZE_WALLET': 'yes' if authorize_signing else 'no',
           'MC_WORKER_LIVE_AUTHORIZE_OFFER': 'yes' if authorize_offer else 'no',
           'MC_WORKER_LIVE_AUTHORIZE_SPEND': 'yes' if authorize_spend else 'no',
           'MC_WORKER_LIVE_AUTHORIZE_SETTINGS': 'yes' if authorize_settings else 'no',
           'MC_WORKER_TEST_PYTHON': sys.executable,
           'MC_WORKER_TEST_ROOT': str(ROOT),
           'PYTHONDONTWRITEBYTECODE': '1'}
    script = node_dir / 'mcel_live_acceptance.mjs'
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    # Do not stream exception logs which may contain browser-controlled text.
    # The subprocess writes a specifically redacted evidence record.
    try:
        child = subprocess.Popen([executable, str(script)], cwd=node_dir, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=flags, start_new_session=os.name != 'nt')
    except OSError:
        return None, 'browser-launch-failed'
    try:
        exit_code = child.wait(timeout=timeout)
        return exit_code, 'exited-' + str(exit_code)
    except subprocess.TimeoutExpired:
        # Reap browser descendants too. A timed-out work cycle is NOT proof
        # that its Hub override was safely cleared; operator reconciliation
        # is required before retrying any spending gate.
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=12, check=False)
            else:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired, ProcessLookupError):
            child.kill()
        return None, 'live-timeout-cleanup-unverified'


def prior_verified_gates(target: Path, current: dict) -> dict:
    """Revalidate prior gates only for the same code and native route.

    A later command cannot silently inherit a stale or unproven PASS.
    """
    try:
        previous = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if (previous.get('schema') != SCHEMA or
            previous.get('source_fingerprint') != current['source_fingerprint'] or
            previous.get('target') != current['target']):
        return {}
    retained = {}
    for gate in GATES:
        prior = previous.get('gates', {}).get(gate, {})
        if prior.get('status') != 'PASS':
            continue
        path = target.parent / ('live-' + gate + '-evidence.json')
        try:
            evidence = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if verify_evidence(evidence, gate)[0]:
            retained[gate] = prior
    return retained


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('live',), default='live')
    parser.add_argument('--action', choices=('plan', 'run'), default='plan')
    parser.add_argument('--gate', choices=GATES,
                        help='One gate per run; no implicit credit-bearing work chain')
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--authorize-wallet-signing', action='store_true')
    parser.add_argument('--fresh-disposable-wallet', action='store_true',
                        help='Compatibility option: wallet-msk already provisions a new isolated wallet by default')
    parser.add_argument('--authorize-worker-offer', action='store_true')
    parser.add_argument('--authorize-captain-spend', action='store_true')
    parser.add_argument('--authorize-settings-change', action='store_true')
    parser.add_argument('--max-testnet-credits', type=int, default=2)
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    try:
        valid_native_url(args.url)
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    if args.fresh_disposable_wallet and (args.action != 'run' or args.gate != 'wallet-msk'):
        parser.error('--fresh-disposable-wallet applies only to --action run --gate wallet-msk')
    if args.timeout < 60 or args.timeout > 1800 or args.max_testnet_credits != MAX_CREDIT_CEILING:
        parser.error('Live profile requires timeout=60..1800 and exact 2-credit ceiling')
    if args.action == 'run' and (os.environ.get('MC_WORKER_TEST_MNEMONIC') or
                                 os.environ.get('MC_WORKER_TEST_PASSWORD')):
        parser.error('Do not supply wallet secrets via environment variables')
    if args.action == 'run' and not args.gate:
        parser.error('--gate is mandatory to execute real Testnet operations')
    required = {
        'wallet-msk': (args.authorize_wallet_signing,),
        'offer-registration': (args.authorize_wallet_signing, args.authorize_worker_offer),
        'work-cycle': (args.authorize_wallet_signing, args.authorize_worker_offer,
                       args.authorize_captain_spend, args.authorize_settings_change),
        'requester-enforcement': (args.authorize_settings_change,),
    }
    if args.action == 'run' and not all(required[args.gate]):
        parser.error('Selected gate lacks its explicit operation-specific authorization')
    target = args.report if args.report.is_absolute() else ROOT / args.report
    report = new_report(args.url, args.action, args.gate)
    if args.action == 'run':
        report['gates'].update(prior_verified_gates(target, report))
    if args.action == 'plan':
        save(report, target)
        print('WORKER_LIVE_PLAN', 'target=' + args.url,
              'gates=' + ','.join(GATES), 'max-testnet-credits=2',
              'no-mutations=TRUE', flush=True)
        return 0
    # No production operation is attempted without the exact route and a
    # positive real HTTP response from the running local native candidate.
    try:
        with urlopen(Request(args.url, headers={'Accept': 'text/html'}), timeout=8) as response:
            markup = response.read(3000000).decode('utf-8', errors='replace')
            if response.status != 200 or 'data-mcel-worker-phase="candidate"' not in markup:
                raise ValueError('native Worker candidate marker absent')
    except Exception:
        report['gates'][args.gate] = {'status': 'REFUSED', 'reason': 'local-native-route-not-verified'}
        save(report, target)
        print('WORKER_LIVE_REFUSED local-native-route-not-verified', flush=True)
        return 2
    detail_path = target.parent / ('live-' + args.gate + '-evidence.json')
    detail_path.unlink(missing_ok=True)  # never reuse a prior PASS
    report['gates'][args.gate] = {'status': 'IN_PROGRESS'}
    save(report, target)
    code, explanation = invoke_node(args.gate, detail_path, args.timeout, args.url,
                    authorize_signing=args.authorize_wallet_signing,
                    authorize_offer=args.authorize_worker_offer,
                    authorize_spend=args.authorize_captain_spend,
                    authorize_settings=args.authorize_settings_change,
                    fresh_disposable_wallet=(args.gate == 'wallet-msk'))
    try:
        evidence = json.loads(detail_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        evidence = None
    accepted, reason = verify_evidence(evidence, args.gate)
    status = 'PASS' if code == 0 and accepted else 'UNVERIFIED'
    if evidence is None and explanation not in ('exited-0','exited-1'):
        reason = explanation if re.fullmatch(r'[A-Za-z0-9_ -]{1,150}', explanation) else 'live-preflight-unverified'
    report['gates'][args.gate] = {'status': status,
                                 'lastVerifiedStage': safe_stage(evidence),
                                 'reason': reason if not accepted else 'verified-live-postconditions',
                                 'evidence': detail_path.name if evidence else None}
    complete = all(report['gates'][name]['status'] == 'PASS' for name in GATES)
    report['status'] = ('PASS_LIVE_FUNCTIONAL' if complete else
                        'PASS_LIVE_GATE' if status == 'PASS' else 'INCOMPLETE')
    # Four distinct real outcomes are required. Even a fully green live
    # profile only makes promotion eligible for review; it never cuts over.
    report['cutover']['status'] = 'ELIGIBLE_FOR_REVIEW' if complete else 'BLOCKED'
    report['cutover']['reason'] = ('all-four-live-gates-verified; manual-promotion-required'
                                   if complete else 'live-gates-incomplete')
    save(report, target)
    print('WORKER_LIVE_GATE', args.gate, status,
          'reason=' + (reason if not accepted else 'verified'),
          'stage=' + safe_stage(evidence),
          'error-class=' + (evidence.get('errorClass')
              if evidence and evidence.get('errorClass') in ('Error','TimeoutError','AssertionError','TypeError','UnknownError')
              else 'NOT_AVAILABLE'),
          'process=' + explanation, flush=True)
    # Report the actual failure screenshots before the user has to inspect a
    # JSON receipt. Only fixed, local image names are accepted; never print a
    # path supplied by a browser page or an untrusted error message.
    if isinstance(evidence, dict) and evidence.get('ok') is not True:
        images = evidence.get('failureScreenshots')
        if not isinstance(images, dict) or images.get('version') != 'failure-screens-v1':
            print('WORKER_LIVE_FAILURE_SCREENSHOTS status=absent-or-outdated', flush=True)
        else:
            captures = images.get('captures')
            if not isinstance(captures, list):
                captures = []
            image_pattern = re.compile(
                r'live-(?:wallet-msk|offer-registration|work-cycle|requester-enforcement)-failure-[A-Za-z0-9_-]{6,}/'
                r'(?:desktop|worker|metamask-(?:home|notification|popup|sidepanel)-[1-6])\.png')
            for row in captures[:10]:
                if not isinstance(row, dict):
                    continue
                surface = row.get('surface')
                if surface not in ('desktop','worker','metamask','metamask-home',
                                   'metamask-notification','metamask-popup','metamask-sidepanel','storage'):
                    continue
                filename = row.get('file')
                saved = (row.get('status') == 'saved' and isinstance(filename, str)
                         and image_pattern.fullmatch(filename) is not None)
                path = detail_path.parent / filename if saved else None
                if path is not None and path.is_file():
                    print('WORKER_LIVE_FAILURE_SCREENSHOT', 'surface=' + surface,
                          'status=saved', 'path=' + str(path.resolve()), flush=True)
                else:
                    print('WORKER_LIVE_FAILURE_SCREENSHOT', 'surface=' + surface,
                          'status=unavailable', flush=True)
    # Event-time images are distinct from catch-handler images. In particular,
    # request-start and approval-target are captured WHILE signing is underway,
    # before the Worker has reset its UI following request.failed.
    if isinstance(evidence, dict) and safe_stage(evidence).startswith('MSK_') and evidence.get('ok') is not True:
        moments = evidence.get('mskMoments')
        if not isinstance(moments, list):
            print('WORKER_LIVE_MSK_MOMENT_SCREENSHOTS status=absent-or-outdated', flush=True)
        else:
            moment_pattern = re.compile(
                r'live-(?:wallet-msk|offer-registration|work-cycle)-moment-'
                r'(?:request-start|approval-target|request-failed|revoke-start|revoke-approval-target|revoke-failed)-[A-Za-z0-9_-]{6,}/'
                r'(?:desktop|worker|metamask-(?:home|notification|popup|sidepanel)-[1-4]|metamask-cdp-(?:home|notification|popup|sidepanel)-[1-5])\.png')
            for snapshot in moments[:6]:
                if not isinstance(snapshot, dict) or snapshot.get('version') != 'msk-moment-screens-v1':
                    continue
                phase = snapshot.get('phase')
                if phase not in ('request-start','approval-target','request-failed','revoke-start','revoke-approval-target','revoke-failed'):
                    continue
                for row in snapshot.get('captures', [])[:8] if isinstance(snapshot.get('captures'), list) else []:
                    if not isinstance(row, dict):
                        continue
                    surface = row.get('surface')
                    if surface not in ('desktop','worker','storage','metamask-home',
                                       'metamask-notification','metamask-popup','metamask-sidepanel','metamask-cdp-home',
                                       'metamask-cdp-notification','metamask-cdp-popup',
                                       'metamask-cdp-sidepanel'):
                        continue
                    filename = row.get('file')
                    saved = (row.get('status') == 'saved' and isinstance(filename, str)
                             and moment_pattern.fullmatch(filename) is not None)
                    path = detail_path.parent / filename if saved else None
                    if path is not None and path.is_file():
                        print('WORKER_LIVE_MSK_MOMENT_SCREENSHOT', 'phase=' + phase,
                              'surface=' + surface, 'status=saved',
                              'path=' + str(path.resolve()), flush=True)
                    else:
                        print('WORKER_LIVE_MSK_MOMENT_SCREENSHOT', 'phase=' + phase,
                              'surface=' + surface, 'status=unavailable', flush=True)
    # Verify and print both the selected extension URL AND the Worker URL.
    # All extension URLs have been reduced by Node to a tiny allowlist of
    # canonical home/unlock routes. Never print untrusted fragments/queries.
    if isinstance(evidence, dict):
        extension_url_pattern = re.compile(
            r'chrome-extension://[a-p]{32}/home\.html(?:#/|#/unlock|#/onboarding/unlock)')
        def safe_extension_url(value):
            return value if isinstance(value, str) and extension_url_pattern.fullmatch(value) else 'unverified'
        for checkpoint, key in (('before-unlock', 'metaMaskPreconnectUrl'),
                                ('after-unlock', 'metaMaskPreconnectAfterUnlock'),
                                ('on-failure', 'metaMaskPreconnectFailure')):
            snapshot = evidence.get(key)
            if not isinstance(snapshot, dict):
                continue
            count = snapshot.get('tabCount')
            count = str(min(max(count, 0), 99)) if type(count) is int else 'unknown'
            print('WORKER_LIVE_METAMASK_ACTIVE_URL',
                  'checkpoint=' + checkpoint,
                  'expected=' + ('home-or-unlock' if checkpoint == 'before-unlock'
                                 else safe_extension_url(snapshot.get('expectedUrl'))),
                  'actual=' + safe_extension_url(snapshot.get('actualUrl')),
                  'route=' + (snapshot.get('selectedRoute') if snapshot.get('selectedRoute') in
                     ('home-root','unlock','other-extension-route','none') else 'unverified'),
                  'visible=' + ('yes' if snapshot.get('visibility') == 'visible' else 'no'),
                  'focused=' + ('yes' if snapshot.get('focused') is True else 'no'),
                  'wallet-match=' + ('yes' if snapshot.get('walletIdentityMatched') is True else 'no'),
                  'tab-count=' + count,
                  flush=True)
            others = snapshot.get('otherHomeTabs')
            if isinstance(others, list):
                for tab in others[:4]:
                    if isinstance(tab, dict):
                        print('WORKER_LIVE_OTHER_EXTENSION_HOME_URL',
                              'checkpoint=' + checkpoint,
                              'actual=' + safe_extension_url(tab.get('url')),
                              'visible=' + ('yes' if tab.get('visible') is True else 'no'),
                              flush=True)
        state = evidence.get('workerActiveUrl')
        if isinstance(state, dict):
            print('WORKER_LIVE_WORKER_ACTIVE_URL',
                  'expected=' + DEFAULT_URL,
                  'actual=' + (DEFAULT_URL if state.get('actualUrl') == DEFAULT_URL else 'unverified'),
                  'route-match=' + ('yes' if state.get('routeMatched') is True else 'no'),
                  'visible=' + ('yes' if state.get('visible') is True else 'no'),
                  flush=True)
        reason = evidence.get('metaMaskPreconnectFailureReason')
        if reason in ('METAMASK_ACTIVE_URL_UNVERIFIED','METAMASK_HOME_IDENTITY_UNVERIFIED',
                      'METAMASK_HOME_ACCOUNT_MISMATCH','WORKER_ACTIVE_URL_UNVERIFIED',
                      'METAMASK_FOREGROUND_UNVERIFIED','METAMASK_UNLOCK_INCOMPLETE',
                      'METAMASK_UNLOCK_BUTTON_DISABLED','METAMASK_UNLOCK_PASSWORD_REJECTED',
                      'METAMASK_UNLOCK_OR_IDENTITY_UNVERIFIED'):
            print('WORKER_LIVE_METAMASK_PRECONNECT_FAILURE', 'reason='+reason,flush=True)
        progress = evidence.get('metaMaskUnlockProgress')
        if isinstance(progress, list):
            for item in progress[-4:]:
                if item in ('unlock-submitted','identity-verified'):
                    print('WORKER_LIVE_METAMASK_UNLOCK_PROGRESS', 'phase='+item,flush=True)
        state = evidence.get('metaMaskUnlockLastState')
        if state in ('no-extension-home','wallet-home-not-verified',
                     'unlock-field-transitioning','unlock-page-transitioning',
                     'unlock-submit-transitioning','unlock-submitted-awaiting-verified-home'):
            print('WORKER_LIVE_METAMASK_UNLOCK_STATE', 'state='+state,flush=True)
    if isinstance(evidence, dict) and safe_stage(evidence).startswith('MSK_') and evidence.get('ok') is not True:
        if evidence.get('mskSigningDiagnosticVersion') != 'msk-signing-v1':
            print('WORKER_LIVE_MSK_DIAGNOSTIC_VERSION_MISMATCH',
                  'expected=msk-signing-v1', 'actual=absent-or-outdated', flush=True)
        else:
            print('WORKER_LIVE_MSK_DIAGNOSTIC_VERSION', 'version=msk-signing-v1', flush=True)
    # Offer and work-cycle registration share the same existing Worker bridge.
    # Report exact stage, seller-policy precondition, local POST and Hub result
    # without printing arbitrary backend/provider text or wallet identifiers.
    if args.gate in ('offer-registration','work-cycle') and status != 'PASS':
        offer = evidence.get('offerRegistration') if isinstance(evidence, dict) else None
        if not isinstance(offer, dict) or offer.get('version') != 'offer-registration-v2':
            print('WORKER_LIVE_OFFER_DIAGNOSTIC_VERSION_MISMATCH',
                  'expected=offer-registration-v2', 'actual=absent-or-outdated', flush=True)
        else:
            def yn(value):
                return 'yes' if value is True else 'no' if value is False else 'unknown'
            allowed_causes = ('seller-disabled-settings-authorization-required',
                'register-button-not-ready','hub-registration-failed',
                'hub-acceptance-unverified','confirmation-missing-or-duplicate',
                'unexpected-confirmation','seller-not-enabled','marketplace-session-mismatch',
                'legacy-registration-bridge-missing','marketplace-operation-busy',
                'registration-not-acknowledged','wallet-hub-session-mismatch',
                'local-worker-post-rejected','offer-deferred-by-local-policy',
                'offer-sync-not-accepted','offer-sync-http-rejected',
                'offer-sync-transport-failed','offer-operation-unclassified','none')
            cause = offer.get('failure')
            if cause not in allowed_causes: cause = 'unclassified'
            allowed_statuses = ('not_submitted','submitting','accepted','failed','stale','unknown')
            registration = offer.get('hubRegistrationStatus')
            if registration not in allowed_statuses: registration = 'unknown'
            allowed_rejections = ('none','msk-authorization','hub-network-context',
                'ring-mismatch','wallet-auth','seller-policy','hub-connection',
                'pricing-or-credit','invalid-registration-payload',
                'hub-route-not-found','other-rejection')
            rejection = offer.get('apiRejection')
            if rejection not in allowed_rejections: rejection = 'unknown'
            allowed_details = ('not-msk-specific','browser-key-id-missing',
                'specified-key-not-active','private-cache-no-active-key',
                'key-chain-mismatch','other-msk-rejection')
            detail = offer.get('apiRejectionDetail')
            if detail not in allowed_details: detail = 'unknown'
            http = offer.get('httpStatus')
            http = str(http) if type(http) is int and 100 <= http <= 599 else 'none'
            btn = offer.get('button') if isinstance(offer.get('button'),dict) else {}
            print('WORKER_LIVE_OFFER_REGISTRATION',
                  'stage=' + safe_stage(evidence), 'cause=' + cause,
                  'seller-enabled-before=' + yn(offer.get('sellerEnabledBefore')),
                  'seller-enabled-at-registration=' + yn(offer.get('sellerEnabledAtRegistration')),
                  'settings-authorized=' + yn(offer.get('settingsAuthorized')),
                  'settings-changed=' + yn(offer.get('settingsChanged')),
                  'button-enabled=' + yn(btn.get('enabled')),
                  'confirmation-completed=' + yn(offer.get('confirmationCompleted')),
                  'request-seen=' + yn(offer.get('requestSeen')),
                  'offer-http=' + http,
                  'transport-failed=' + yn(offer.get('transportFailed')),
                  'hub-registration=' + registration,
                  'offer-sync-status=' + str(offer.get('syncStatus')) if offer.get('syncStatus') in ('accepted','deferred','failed','pending','unchanged','unknown') else 'offer-sync-status=unknown',
                  'deferred-reason=' + str(offer.get('deferredReason')) if offer.get('deferredReason') in ('none','unspecified','windows-not-idle','local-ai-not-idle','active-worker-job','availability-changed','other-policy') else 'deferred-reason=unknown',
                  'hub-receipt-verified=' + yn(offer.get('hubReceiptVerified')),
                  'signed-hub-registered=' + yn(offer.get('backendHubRegistered')),
                  'worker-identity-match=' + yn(offer.get('workerIdentityMatched')),
                  'hub-url-match=' + yn(offer.get('hubUrlMatched')),
                  'assigned-ring-match=' + yn(offer.get('assignedRingMatched')),
                  'worker-id-present=' + yn(offer.get('workerIdPresent')),
                  'api-rejection=' + rejection, 'api-detail=' + detail, flush=True)
    if args.gate in ('offer-registration','work-cycle') and status != 'PASS':
        moments = evidence.get('offerMoments') if isinstance(evidence,dict) else None
        if isinstance(moments,list):
            pattern = re.compile(
                r'live-(?:offer-registration|work-cycle)-moment-'
                r'(?:offer-confirmed|offer-http-failed|offer-hub-failed)-[A-Za-z0-9_-]{6,}/'
                r'(?:desktop|worker|metamask-(?:home|notification|popup|sidepanel)-[1-4]|'
                r'metamask-cdp-(?:home|notification|popup|sidepanel)-[1-5])\.png')
            for moment in moments[:3]:
                if not isinstance(moment,dict) or moment.get('version') != 'offer-moment-screens-v1':
                    continue
                phase=moment.get('phase')
                if phase not in ('offer-confirmed','offer-http-failed','offer-hub-failed'):
                    continue
                captures=moment.get('captures')
                for shot in (captures[:10] if isinstance(captures,list) else []):
                    if not isinstance(shot,dict): continue
                    surface=shot.get('surface')
                    if surface not in ('desktop','worker','metamask-home','metamask-notification',
                                      'metamask-popup','metamask-sidepanel','metamask-cdp-home',
                                      'metamask-cdp-notification','metamask-cdp-popup',
                                      'metamask-cdp-sidepanel'): continue
                    path=shot.get('file')
                    if shot.get('status')!='saved' or not isinstance(path,str) or not pattern.fullmatch(path):
                        continue
                    absolute=detail_path.parent/path
                    if absolute.is_file():
                        print('WORKER_LIVE_OFFER_MOMENT_SCREENSHOT', 'phase='+phase,
                              'surface='+surface,'path='+str(absolute.resolve()),flush=True)
    # Report MSK signing failures by the operation that actually failed.
    # All text is categorical; do not echo raw MetaMask, request or console data.
    if isinstance(evidence, dict) and isinstance(evidence.get('mskSigning'), dict):
        signing = evidence['mskSigning']
        stage = safe_stage(evidence)
        if stage.startswith('MSK_') and evidence.get('ok') is not True:
            code = evidence.get('mskFailureCode')
            if not isinstance(code, str) or not re.fullmatch(r'[a-z-]{3,84}', code):
                code = 'unclassified'
            status = evidence.get('mskUiState')
            status = status if isinstance(status, dict) else {}
            category = status.get('status') if status.get('status') in (
                'awaiting-signature','hub-request-pending','request-failed','issued',
                'signer-missing','active-key-blocked','wallet-not-connected',
                'inspection-unavailable','other') else 'unknown'
            api = signing.get('apiStatus')
            http = str(api) if type(api) is int and 100 <= api <= 599 else 'none'
            print('WORKER_LIVE_MSK_SIGNING',
                  'stage=' + stage,
                  'cause=' + code,
                  'click=' + ('yes' if signing.get('clicked') is True else 'no'),
                  'approved=' + ('yes' if signing.get('approved') is True else 'no'),
                  'hub-verified=' + ('yes' if signing.get('hubVerified') is True else 'no'),
                  'ui=' + category,
                  'api-http=' + http,
                  'api-transport-failed=' + ('yes' if signing.get('apiRequestFailed') is True else 'no'),
                  flush=True)
            button = signing.get('buttonBefore')
            if isinstance(button, dict):
                count = button.get('count')
                count = str(min(max(count, 0), 99)) if type(count) is int else 'unknown'
                print('WORKER_LIVE_MSK_BUTTON', 'count=' + count,
                      'enabled=' + ('yes' if button.get('enabled') is True else 'no'),
                      'visible=' + ('yes' if button.get('visible') is True else 'no'),
                      flush=True)
            for event in signing.get('events', [])[-16:] if isinstance(signing.get('events'), list) else []:
                if event in ('request.start','request.blocked','request.ignored',
                             'signature.created','hub.response','request.failed'):
                    print('WORKER_LIVE_MSK_EVENT','type='+event,flush=True)
            for event in signing.get('approvalProgress', [])[-16:] if isinstance(signing.get('approvalProgress'), list) else []:
                if isinstance(event, str) and (
                        re.fullmatch(r'msk\.(?:target\.(?:home|approval|none)|inspect\.[a-z-]{1,60}|cdp\.(?:unavailable|target-unavailable))',event)
                        or event in ('metamask.msk-signature','metamask.msk-revocation-signature')):
                    print('WORKER_LIVE_MSK_APPROVAL','phase='+event,flush=True)
    # Revocation is a distinct signed Hub operation. Report its own HTTP and
    # state evidence; the issuance HTTP status must not masquerade as proof.
    if safe_stage(evidence).startswith('MSK_REVOCATION') and not (
            isinstance(evidence, dict) and
            isinstance(evidence.get('mskRevocation'), dict)):
        print('WORKER_LIVE_MSK_REVOCATION_VERSION_MISMATCH',
              'expected=msk-revocation-v1', 'actual=absent-or-outdated', flush=True)
    if isinstance(evidence, dict) and isinstance(evidence.get('mskRevocation'), dict):
        rev = evidence['mskRevocation']
        if rev.get('version') != 'msk-revocation-v1':
            print('WORKER_LIVE_MSK_REVOCATION_VERSION_MISMATCH', flush=True)
        else:
            http = rev.get('httpStatus')
            http = str(http) if type(http) is int and 100 <= http <= 599 else 'none'
            allowed = ('context-unverified','response-missing','http-rejected','hub-unverified',
                       'ui-still-active','approval-failed','click-failed','unclassified')
            failure = rev.get('failure') if rev.get('failure') in allowed else 'none'
            reason = rev.get('rejectionCategory')
            allowed_reasons = ('hub-cluster-mismatch','signed-intent-mismatch',
                               'wallet-owner-mismatch','key-not-active',
                               'hub-unverified','signed-intent-expired',
                               'signature-rejected','hub-unreachable',
                               'hub-http-5xx','hub-http-4xx','local-api-rejected',
                               'unclassified')
            reason = reason if reason in allowed_reasons else 'none'
            print('WORKER_LIVE_MSK_REVOCATION',
                  'stage=' + safe_stage(evidence),
                  'cause=' + failure,
                  'click=' + ('yes' if rev.get('click') is True else 'no'),
                  'approved=' + ('yes' if rev.get('approval') is True else 'no'),
                  'request-seen=' + ('yes' if rev.get('requestSeen') is True else 'no'),
                  'http=' + http,
                  'transport-failed=' + ('yes' if rev.get('requestTransportFailed') is True else 'no'),
                  'local-ok=' + ('yes' if rev.get('localOk') is True else 'no' if rev.get('localOk') is False else 'unknown'),
                  'hub-ok=' + ('yes' if rev.get('hubOk') is True else 'no' if rev.get('hubOk') is False else 'unknown'),
                  'revoked=' + ('yes' if rev.get('revoked') is True else 'no' if rev.get('revoked') is False else 'unknown'),
                  'ui-verified=' + ('yes' if rev.get('uiVerified') is True else 'no'),
                  'api-rejection=' + reason,
                  flush=True)
            # This is already reduced by safeMskApiRejection in the browser;
            # redact again at the reporting boundary. Never print raw HTTP
            # response bodies or signing material in acceptance output.
            detail = rev.get('rejectionDetail')
            if isinstance(detail, str) and detail:
                detail = re.sub(r'0x[0-9a-fA-F]{12,}', '[hex-redacted]', detail)
                detail = re.sub(r'https?://[^\s\"\']+', '[url-redacted]', detail)
                detail = re.sub(r'\b[A-Fa-f0-9]{64,}\b', '[hex-redacted]', detail)
                if re.search(r'(?i)mnemonic|seed[ _-]?phrase|recovery[ _-]?phrase|private[ _-]?key|password|authorization|bearer|access[ _-]?token|signature\s*[:=]|signed_request\s*[:=]', detail):
                    detail = 'sensitive-error-detail-redacted'
                detail = re.sub(r'[\x00-\x1f\x7f]', ' ', detail).strip()[:300]
                print('WORKER_LIVE_MSK_REVOCATION_ERROR',
                      'detail=' + json.dumps(detail, ensure_ascii=True), flush=True)
            ui = rev.get('uiState')
            if isinstance(ui, dict):
                outcome = ui.get('workerOutcome')
                if outcome not in ('worker-revoke-failed','worker-revoked','wallet-state','other','inspection-unavailable'):
                    outcome = 'unverified'
                print('WORKER_LIVE_MSK_REVOCATION_UI',
                      'key-active=' + ('yes' if ui.get('keyActive') is True else 'no'),
                      'key-inactive=' + ('yes' if ui.get('keyInactive') is True else 'no'),
                      'worker-outcome=' + outcome,flush=True)
    if isinstance(evidence, dict) and isinstance(evidence.get('mskSigning'), dict):
        events = evidence['mskSigning'].get('events')
        if isinstance(events, list):
            for event in events[-18:]:
                if event in ('revoke.start','revoke.signature.created',
                             'revoke.local.response','revoke.hub.verified','revoke.failed'):
                    print('WORKER_LIVE_MSK_REVOCATION_EVENT','type='+event,flush=True)
    # Print a fixed, versioned diagnostics marker. An old JavaScript harness
    # paired with this Python reporter must be detected instead of silently
    # repeating the same three-line failure after an allegedly applied patch.
    if safe_stage(evidence) in ('WALLET_CONTEXT_REFRESH','WALLET_PANEL_READY','WALLET_CONNECT'):
        version = evidence.get('diagnosticVersion') if isinstance(evidence, dict) else None
        if version != 'wallet-connect-v3':
            print('WORKER_LIVE_DIAGNOSTIC_VERSION_MISMATCH',
                  'expected=wallet-connect-v3', 'actual=absent-or-outdated', flush=True)
        else:
            missing = [name for name in ('walletConnectState','walletConnectTimeline',
                       'walletBackendSession','walletConnectProgress','walletEarlyEvents')
                       if not isinstance(evidence.get(name), (dict, list))]
            print('WORKER_LIVE_DIAGNOSTIC_VERSION', 'version=wallet-connect-v3',
                  'evidence=' + ('complete' if not missing else 'incomplete'),
                  'missing-fields=' + (','.join(missing) if missing else 'none'),flush=True)
    # A bounded diagnosis makes the failed wallet stage actionable without
    # exposing raw extension text, wallet state, or browser exception payloads.
    failure = evidence.get('walletConnectFailure') if isinstance(evidence, dict) else None
    if isinstance(failure, str) and re.fullmatch(r'[A-Z_]{3,72}', failure):
        state = evidence.get('walletConnectState')
        safe = state if isinstance(state, dict) else {}
        events = evidence.get('walletConnectEvents')
        last_event = events[-1] if isinstance(events, list) and events else ''
        if not isinstance(last_event, str) or not re.fullmatch(r'connect\.[A-Za-z0-9_.-]{1,96}', last_event):
            last_event = 'none'
        print('WORKER_LIVE_WALLET_CONNECT_DIAGNOSTIC',
              'reason=' + failure,
              'last-event=' + last_event,
              'panel=' + ('visible' if safe.get('panelVisible') is True else 'not-visible'),
              'button=' + ('present' if safe.get('buttonPresent') is True else 'missing'),
              'handler=' + ('bound' if safe.get('handlerBound') is True else 'unbound'),
              'disabled=' + ('yes' if safe.get('disabled') is True else 'no'),
              'hub-choice=' + (safe.get('hubChoice') if safe.get('hubChoice') in
                  ('none', 'mainnet', 'testnet', 'test', 'dev', 'slh') else 'unverified'),
              flush=True)
        # Print a bounded categorical trace of *when* the control was disabled
        # and which real Worker/MetaMask transition was observed. A post-failure
        # disabled button alone is not evidence of a pre-click disabled state.
        progress = evidence.get('walletConnectProgress')
        if isinstance(progress, list):
            for item in progress[-32:]:
                if isinstance(item, str) and re.fullmatch(
                        r'(?:worker\.(?:status\.|connect\.)?[A-Za-z0-9_.-]{1,96}|metamask\.(?:network-add|network-switch|account-connect|account-select))', item):
                    print('WORKER_LIVE_WALLET_PROGRESS', 'phase=' + item, flush=True)
        early = evidence.get('walletEarlyEvents')
        if isinstance(early, list):
            for item in early[-24:]:
                if isinstance(item, str) and re.fullmatch(
                        r'(?:connect|provider\.(?:hydrate|refresh|accountsChanged|chainChanged))\.[A-Za-z0-9_.-]{1,96}', item):
                    print('WORKER_LIVE_WALLET_EVENT', 'type=' + item, flush=True)
        timeline = evidence.get('walletConnectTimeline')
        if isinstance(timeline, list):
            for row in timeline[-35:]:
                if not isinstance(row, dict) or row.get('kind') not in ('start','mutation','end'):
                    continue
                state = row.get('state')
                if not isinstance(state, dict):
                    continue
                ms = row.get('elapsedMs')
                ms = ms if isinstance(ms, int) and 0 <= ms <= 600000 else 0
                def enum(value, allowed):
                    return value if value in allowed else 'unverified'
                print('WORKER_LIVE_WALLET_STATE',
                      't-ms=' + str(ms),
                      'when=' + row['kind'],
                      'button=' + enum(state.get('button'), ('idle','hydrating','requesting','finalizing','connected','other')),
                      'disabled=' + ('yes' if state.get('disabled') is True else 'no'),
                      'busy=' + ('yes' if state.get('busy') is True else 'no'),
                      'bound=' + ('yes' if state.get('handlerBound') is True else 'no'),
                      'context-verified=' + ('yes' if state.get('contextVerified') is True else 'no'),
                      'status=' + enum(state.get('status'),
                                      ('none','network-preflight','network-update-requested','rpc-ready',
                                       'rpc-backoff','network-switch-requested','account-requested',
                                       'account-approved','finalizing','connected','connect-failed','other')),
                      flush=True)
        faults = evidence.get('walletBrowserFaults')
        if isinstance(faults, list):
            for value in faults[-20:]:
                if value in ('TypeError','ReferenceError','SyntaxError','RangeError','Error','OtherError'):
                    print('WORKER_LIVE_BROWSER_FAULT', 'class=' + value, flush=True)
        network = evidence.get('walletBrowserNetworkFailures')
        if isinstance(network, list):
            for value in network[-20:]:
                if value in ('worker-api:transport','worker-api:4xx','worker-api:5xx',
                             'worker-app:transport','worker-app:4xx','worker-app:5xx'):
                    print('WORKER_LIVE_BROWSER_REQUEST_FAILURE', 'kind=' + value, flush=True)
        backend = evidence.get('walletBackendSession')
        if isinstance(backend, dict):
            print('WORKER_LIVE_WALLET_BACKEND',
                  'http-ok=' + ('yes' if backend.get('httpOk') is True else 'no'),
                  'selected=' + (backend.get('selectedNetwork') if backend.get('selectedNetwork') in
                                  ('none','mainnet','testnet','test','dev','slh') else 'unverified'),
                  'status=' + (backend.get('status') if backend.get('status') in
                                ('connected','disconnected','connecting','failed') else 'unverified'),
                  'ring=' + (backend.get('ring') if backend.get('ring') in ('1','2','3') else 'unverified'),
                  flush=True)
        extension = evidence.get('walletExtension')
        if isinstance(extension, dict):
            pages = extension.get('pageStates') if isinstance(extension.get('pageStates'), list) else []
            targets = extension.get('targets') if isinstance(extension.get('targets'), list) else []
            print('WORKER_LIVE_METAMASK_TARGETS',
                  'pages=' + str(min(len(pages),12)), 'cdp-targets=' + str(min(len(targets),12)), flush=True)
            for index, item in enumerate(pages[:12]):
                if not isinstance(item, dict):
                    continue
                kind = item.get('kind') if item.get('kind') in (
                    'notification','sidepanel','popup','home','other-extension') else 'other-extension'
                print('WORKER_LIVE_METAMASK_PAGE', 'index=' + str(index), 'kind=' + kind,
                      'network-add=' + ('yes' if item.get('networkAdd') is True else 'no'),
                      'network-switch=' + ('yes' if item.get('networkSwitch') is True else 'no'),
                      'account-connect=' + ('yes' if item.get('accountConnect') is True else 'no'),
                      'origin-matched=' + ('yes' if item.get('originMatched') is True else 'no'),
                      'rpc-matched=' + ('yes' if item.get('rpcMatched') is True else 'no'), flush=True)
            for index, item in enumerate(targets[:12]):
                if not isinstance(item, dict):
                    continue
                kind = item.get('kind') if item.get('kind') in (
                    'notification','sidepanel','popup','home','other-extension') else 'other-extension'
                typ = item.get('type') if item.get('type') in (
                    'page','other','background_page','service_worker') else 'other'
                print('WORKER_LIVE_METAMASK_TARGET', 'index=' + str(index),
                      'kind=' + kind, 'type=' + typ, flush=True)
    print('WORKER_LIVE_CUTOVER', report['cutover']['status'], 'canonical-route-unchanged', flush=True)
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
