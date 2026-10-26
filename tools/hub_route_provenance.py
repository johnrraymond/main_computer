#!/usr/bin/env python3
"""Read-only feature provenance check for Git-backed Stable Hub deployment.

Report separately whether a requested route exists in working source, pushed
HEAD, the audit caller, and the public live Hub. A clean Git tree is NOT proof
that the requested route exists. This script never deploys or mutates Git.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HUB_FILE = 'main_computer/stable_hub.py'
AUDIT_FILE = 'tools/worker_browser_acceptance/captain_bridge_audit.py'
DEFAULT_ROUTE = '/api/hub/v1/credits/bridge-deposit'
# Match the existing, working Hub read-only client signature in
# main_computer/captain_cli.py without importing the Captain CLI (which has
# unrelated operational dependencies). This isn't a bypass of Cloudflare:
# requests remain subject to the same server-side access controls.
_DEFAULT_HUB_USER_AGENT = "main-computer-captain-cli/1.0 (+https://greatlibrary.io)"


def _hub_probe_headers() -> dict[str, str]:
    return {
        'Accept': 'application/json',
        'User-Agent': str(os.environ.get('MAIN_COMPUTER_HUB_USER_AGENT') or '').strip() or _DEFAULT_HUB_USER_AGENT,
        'X-Main-Computer-Client': 'captain-cli',
    }


def _access_control_hint(headers, body: bytes) -> str:
    """Return a bounded classification, never raw proxy error content."""
    get = getattr(headers, 'get', lambda *_: '')
    if str(get('cf-mitigated', '')).lower() == 'challenge':
        return 'upstream-cloudflare-challenge'
    if b'error code: 1010' in body.lower():
        return 'upstream-cloudflare-browser-integrity'
    if str(get('server', '')).lower().startswith('cloudflare'):
        return 'possible-upstream-cloudflare-rule'
    return 'undetermined-403-origin'


def _git(repo: Path, *args: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                              text=True, check=False, timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return False, 'git-command-unavailable'
    output = proc.stdout if proc.returncode == 0 else proc.stderr
    # Porcelain status encodes XY in two columns. Leading blank columns are
    # semantically meaningful: `.strip()` destroys the ` M path` prefix.
    if args and args[0] == 'status' and '--porcelain=v1' in args:
        return proc.returncode == 0, output.rstrip('\r\n')
    return proc.returncode == 0, output.strip()


def inspect_local_and_git(repo: Path, route: str = DEFAULT_ROUTE) -> dict[str, Any]:
    """Verify feature presence and committed provenance without changing Git."""
    repo = repo.resolve()
    hub_path = repo / HUB_FILE
    audit_path = repo / AUDIT_FILE
    local_hub = hub_path.read_text(encoding='utf-8') if hub_path.is_file() else ''
    local_audit = audit_path.read_text(encoding='utf-8') if audit_path.is_file() else ''
    ok_head, head = _git(repo, 'rev-parse', 'HEAD')
    ok_remote, remote = _git(repo, 'ls-remote', 'origin', 'refs/heads/main')
    remote_bits = remote.split() if ok_remote else []
    remote_sha = remote_bits[0] if len(remote_bits) == 2 and remote_bits[1] == 'refs/heads/main' else ''
    ok_source, committed = _git(repo, 'show', 'HEAD:' + HUB_FILE)
    ok_dirty, dirty_text = _git(repo, 'status', '--porcelain=v1', '--untracked-files=all', '--', HUB_FILE, AUDIT_FILE)
    # Compare the exact route as a quoted path string, not an incidental mention.
    quoted = repr(route)
    double = json.dumps(route)
    def contains_route(source: str) -> bool:
        return quoted in source or double in source
    dirty_paths: set[str] = set()
    status_parse_ok = bool(ok_dirty)
    if ok_dirty:
        for line in dirty_text.splitlines():
            if len(line) < 4 or line[2] != ' ':
                status_parse_ok = False
                break
            # Paths are scoped to these two files; support porcelain's
            # ordinary quoting plus staged renames without losing XY status.
            dirty_paths.update(part.strip('"') for part in line[3:].split(' -> '))
    result: dict[str, Any] = {
        'route': route,
        'workingHubRoutePresent': contains_route(local_hub),
        'committedHubRoutePresent': ok_source and contains_route(committed),
        'localAuditCallsExactLookup': 'inspect_hub_historical_deposit(' in local_audit
                                      and 'row.update(inspect_hub_historical_deposit(' in local_audit,
        'gitHead': head if ok_head and re.fullmatch('[0-9a-fA-F]{40}', head) else 'unavailable',
        'originMainHead': remote_sha or 'unavailable',
        'headMatchesOriginMain': bool(ok_head and remote_sha and head.lower() == remote_sha.lower()),
        # A local audit change does not require pushing to deploy a Hub image.
        # Only the Hub runtime file must be clean against the pushed revision.
        'hubSourceClean': bool(status_parse_ok and HUB_FILE not in dirty_paths),
        'auditChangedLocally': bool(status_parse_ok and AUDIT_FILE in dirty_paths),
        'gitStatusParseVerified': status_parse_ok,
        'featureFileStatus': dirty_text.splitlines()[:20] if ok_dirty else ['git-status-unavailable'],
    }
    return result


def probe_hub_route(url: str, route: str = DEFAULT_ROUTE, *, timeout_s: float = 12.0,
                    opener=None) -> dict[str, Any]:
    """GET with omitted parameters; the expected 400 proves route validation exists.

    Never submit a deposit ID/wallet, mutate a ledger, or infer deployment
    commit identity from a successful HTTP response.
    """
    url = str(url).strip().rstrip('/')
    if not url.startswith('https://'):
        return {'url': url, 'result': 'refused-non-https', 'routeVerified': False}
    if not route.startswith('/api/hub/v1/') or '?' in route or '#' in route:
        return {'url': url, 'result': 'invalid-route', 'routeVerified': False}
    request = urllib.request.Request(url + route, method='GET', headers=_hub_probe_headers())
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout_s) as response:
            status = response.status
            data = response.read(4096)
            response_headers = response.headers
    except urllib.error.HTTPError as exc:
        status = exc.code
        data = exc.read(4096)
        response_headers = exc.headers
    except (OSError, urllib.error.URLError, TimeoutError):
        return {'url': url, 'result': 'unreachable', 'routeVerified': False}
    if status == 403:
        # A forbidden response can come from upstream access control or the
        # route itself. It establishes neither presence nor absence of code.
        return {'url': url, 'result': 'access-denied-http-403',
                'httpStatus': status, 'routeVerified': False,
                'accessControlHint': _access_control_hint(response_headers, data)}
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError, TypeError):
        return {'url': url, 'result': 'unexpected-http-response', 'httpStatus': status, 'routeVerified': False}
    # This is specific to the read-only deposit lookup's validator. Other
    # endpoints require their own probes and should not be marked verified.
    confirmed = (route == DEFAULT_ROUTE and status == 400 and isinstance(payload, dict)
                 and payload.get('ok') is False and str(payload.get('error') or '').startswith('chain_id must be'))
    return {'url': url, 'result': 'route-validator-present' if confirmed else
            ('route-not-found' if status == 404 else 'route-unverified'),
            'httpStatus': status, 'routeVerified': confirmed}


def check(repo: Path, *, network: str, urls: list[str]) -> dict[str, Any]:
    if network not in {'testnet', 'mainnet'}:
        raise ValueError('Only testnet/mainnet supported')
    config_file = repo / 'main_computer/config/hub_networks.json'
    config = json.loads(config_file.read_text(encoding='utf-8'))
    primary = str(config['networks'][network]['hub_public_url']).rstrip('/')
    targets = list(dict.fromkeys([primary, *(u.rstrip('/') for u in urls)]))
    git = inspect_local_and_git(repo)
    endpoints = [probe_hub_route(u) for u in targets]
    source_ready = (git['workingHubRoutePresent'] and git['committedHubRoutePresent']
                    and git['localAuditCallsExactLookup'] and git['headMatchesOriginMain']
                    and git['hubSourceClean'])
    live_ready = all(item['routeVerified'] for item in endpoints)
    return {'schema': 'main-computer.hub-feature-provenance.v1', 'readOnly': True,
            'network': network, 'git': git, 'endpoints': endpoints,
            'sourceReady': bool(source_ready), 'liveRoutesVerified': bool(live_ready),
            'verified': bool(source_ready and live_ready),
            'interpretation': ('feature-ready' if source_ready and live_ready else
                'feature-missing-in-working-source-or-pushed-commit' if not git['workingHubRoutePresent'] or not git['committedHubRoutePresent'] else
                'local-audit-not-updated' if not git['localAuditCallsExactLookup'] else
                'git-feature-source-not-clean-or-not-pushed' if not source_ready else
                'runtime-route-missing-or-unverified')}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network', choices=['testnet', 'mainnet'], default='testnet')
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    parser.add_argument('--url', action='append', default=[], help='Additional deployed Hub URL to check (HTTPS only)')
    args = parser.parse_args(argv)
    try:
        report = check(args.repo_root.resolve(), network=args.network, urls=args.url)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {'verified': False, 'error': type(exc).__name__, 'readOnly': True}
    print(json.dumps(report, indent=2))
    return 0 if report.get('verified') is True else 2


if __name__ == '__main__':
    raise SystemExit(main())
