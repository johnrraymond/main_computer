"""Minimal live proof that Playwright tests can use the secured MC gateway.

Creates and revokes only its own disposable authentication session. No Worker
JavaScript is executed, and no application mutation is allowed. Uses the
normal proof challenge, the normal browser cookie and normal TLS verification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from tools.mcel_functional_auth import (ORIGIN, AUTH, SESSION, CHALLENGE, VERIFY, LOGOUT,
    WORKER, IDENTITIES, COOKIE, TOKEN, SmokeFailure, allowed, proof_signature, FETCH,
    authenticate_context, revoke_context)

def run(proof):
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    result = {'status': 'UNVERIFIED', 'stage': 'browser-launch', 'checks': {}}
    checks = result['checks']

    def check(stage, condition, http=None):
        result['stage'] = stage
        if http is not None:
            result['http'] = http
        if not condition:
            raise SmokeFailure(stage + '-failed')
        checks[stage] = True
        print('MCEL_SECURE_SMOKE ' + stage + ' PASS' + ('' if http is None else f' http={http}'), flush=True)

    try:
        with sync_playwright() as p:
            browser, _ = launch_semantic_browser(p)
            try:
                ctx = browser.new_context(ignore_https_errors=False, service_workers='block')
                try:
                    authenticate_context(ctx, proof, check)
                    page = ctx.new_page()
                    page.route('**/*', lambda route: route.continue_() if allowed(route.request.url, route.request.method) else route.abort())
                    page.goto(ORIGIN + SESSION, wait_until='domcontentloaded', timeout=10000)
                    for stage, path, kind, field in (
                        ('protected-worker-shell', WORKER, 'worker', 'shell'),
                        ('protected-api', IDENTITIES, 'identities', 'inventory')):
                        result['stage'] = stage
                        r = page.evaluate(FETCH, {'path': path, 'kind': kind, 'body': None})
                        check(stage, r['http'] == 200 and r.get(field) is True, r['http'])
                finally:
                    try:
                        checks['disposable-session-revoked'] = revoke_context(ctx)
                    except Exception:
                        checks['disposable-session-revoked'] = False
                check('disposable-session-revoked', checks.get('disposable-session-revoked') is True)
                result.update(status='PASS', stage='complete')
            finally:
                browser.close()
    except Exception as exc:
        result['errorClass'] = type(exc).__name__
        if isinstance(exc, SmokeFailure):
            result['reason'] = str(exc)
        # Never echo exception payloads, paths, proofs, cookies, or HTML.
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof-file', type=Path, default=Path.home() / 'AAA_AUTH_MAIN_COMPUTER.json')
    args = parser.parse_args(argv)
    result = run(args.proof_file)
    print('MCEL_SECURE_SMOKE SUMMARY ' + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
