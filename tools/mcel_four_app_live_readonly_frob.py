"""Bounded *actual-host* read-only acceptance for four MCEL semantic candidates.

No mocks, no substitutions for service responses, and no write operations.
Only an explicit loopback host can be contacted. Non-read APIs, unexpected GET
APIs, websockets, redirects to other origins and off-origin browser requests
are blocked. The report contains status, not filesystem contents or process
command lines. This diagnostic is NOT full operational parity or promotion.

Example:
  python -u -m tools.mcel_four_app_live_readonly_frob --host-url http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from tools.mcel_v1_four_app_hosted_acceptance import (
    APP_IDS, candidate_ir, json_fingerprint, validate_loopback_base,
    _is_pinned_monaco_asset,
)
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_semantic_reference_parity_frob import APPLICATIONS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / 'runtime/reports/mcel-four-app-live-readonly/report.json'
APPS = ('calculator', 'file-manager', 'task-manager', 'code-editor')
READ_ONLY_POSTS = {
    'calculator': frozenset(),
    'file-manager': frozenset({
        '/api/applications/file-explorer/roots',
        '/api/applications/file-explorer/list',
        '/api/applications/file-explorer/search',
        '/api/applications/file-explorer/read',
    }),
    'task-manager': frozenset({
        '/api/applications/task/overview',
        '/api/applications/task/schedules',
    }),
    'code-editor': frozenset({
        '/api/applications/editor/files',
        '/api/applications/editor/read',
        '/api/applications/component-docs/manifest',
        '/api/applications/component-docs/read',
        '/api/applications/component-docs/viewport-config',
    }),
}
READ_ONLY_GETS = {
    'code-editor': frozenset({
        '/api/applications/aider/context', '/api/applications/aider/jobs',
    }),
}


def _relative_path(value: object, *, empty_ok: bool = False) -> bool:
    if not isinstance(value, str) or len(value) > 512:
        return False
    if not value:
        return empty_ok
    return (not value.startswith(('/', '\\')) and '\\' not in value and ':' not in value
            and all(part not in ('', '.', '..') for part in value.split('/')))


def _record(value: object, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def allowed_post(app: str, path: str, body: object) -> bool:
    """Fail closed on both URL and *shape* of the read request."""
    if app not in READ_ONLY_POSTS or path not in READ_ONLY_POSTS[app] or not isinstance(body, dict):
        return False
    if path.endswith('/roots') or path.endswith('/schedules') or path.endswith('/manifest') or path.endswith('/viewport-config'):
        return body == {}
    if path.startswith('/api/applications/file-explorer/'):
        root = body.get('root_id')
        if not isinstance(root, str) or not root or len(root) > 128:
            return False
        if path.endswith('/list'):
            return _record(body, {'root_id', 'relative_path'}) and _relative_path(body['relative_path'], empty_ok=True)
        if path.endswith('/search'):
            return (_record(body, {'root_id', 'relative_path', 'query', 'limit'})
                    and _relative_path(body['relative_path'], empty_ok=True)
                    and isinstance(body['query'], str) and len(body['query']) <= 256
                    and type(body['limit']) is int and 1 <= body['limit'] <= 200)
        if path.endswith('/read'):
            return _record(body, {'root_id', 'relative_path'}) and _relative_path(body['relative_path'])
    if path == '/api/applications/task/overview':
        return (set(body) in ({'query', 'limit', 'include_all', 'include_connections'},
                               {'limit', 'query', 'include_all', 'include_connections'})
                and isinstance(body.get('query'), str) and len(body['query']) <= 256
                and type(body.get('limit')) is int and 5 <= body['limit'] <= 100
                and type(body.get('include_all')) is bool
                and type(body.get('include_connections')) is bool)
    if path == '/api/applications/editor/files':
        return (_record(body, {'repo_dir', 'path', 'limit'}) and body['repo_dir'] == '.'
                and _relative_path(body['path'], empty_ok=True)
                and type(body['limit']) is int and 1 <= body['limit'] <= 500)
    if path == '/api/applications/editor/read':
        return (_record(body, {'repo_dir', 'files'}) and body['repo_dir'] == '.'
                and isinstance(body['files'], list) and 1 <= len(body['files']) <= 4
                and all(_relative_path(path) for path in body['files']))
    if path == '/api/applications/component-docs/read':
        return _record(body, {'id'}) and isinstance(body['id'], str) and 0 < len(body['id']) <= 128
    return False


def request_decision(app: str, base: str, url: str, method: str,
                     body_text: str | None = None) -> str:
    """Returns an auditable decision; default is deny. No network occurs here."""
    parsed = urlsplit(url)
    target = urlsplit(base)
    if parsed.username or parsed.password or parsed.scheme != target.scheme or parsed.netloc != target.netloc:
        if app == 'code-editor' and method == 'GET' and _is_pinned_monaco_asset(url):
            return 'allow-pinned-monaco'
        return 'deny-off-origin'
    if parsed.fragment or not parsed.path.startswith('/'):
        return 'deny-query-or-path'
    if parsed.query and (method != 'GET' or parsed.path.startswith('/api/')):
        return 'deny-query-or-path'
    if method == 'GET':
        if parsed.path.startswith('/api/'):
            return ('allow-api-read' if parsed.path in READ_ONLY_GETS.get(app, ()) else 'deny-api-get')
        if parsed.path.startswith(('/applications/', '/assets/', '/static/')) or parsed.path == '/favicon.ico':
            return 'allow-local-asset'
        return 'deny-nonasset-get'
    if method != 'POST':
        return 'deny-method'
    if body_text is None or len(body_text) > 8192:
        return 'deny-body'
    try:
        body = json.loads(body_text)
    except (ValueError, TypeError):
        return 'deny-body'
    return 'allow-api-read' if allowed_post(app, parsed.path, body) else 'deny-api-post'


def _summary(page, app: str, expected_hash: str) -> dict:
    script = '#mcel-expanded-ir' if app == 'calculator' else '#mcel-workbench-ir'
    status = '#mcel-expanded-diagnostic' if app == 'calculator' else '#mcel-workbench-status'
    raw = page.locator(script).text_content(timeout=8000)
    parsed = json.loads(raw)
    mounted = page.locator('[data-mcel-semantic-operational]').count() > 0
    return {
        'ready': page.locator(status).inner_text(timeout=8000) == (
            'Semantic Calculator ready' if app == 'calculator' else 'Semantic candidate ready'),
        'served_ir_sha256': json_fingerprint(parsed),
        'source_fingerprint_matches': json_fingerprint(parsed) == expected_hash,
        'app_id_matches': parsed.get('application', {}).get('appId') == APP_IDS[app],
        'mounted': mounted,
        'no_horizontal_overflow': page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'),
    }


def _receipt_marker(page, intent_id: str) -> dict:
    before = page.evaluate("""(id) => window.__mcelWorkbench.app.receipts()
        .filter(r => r.intentId === id).length""", intent_id)
    return {'before': before, 'intent': intent_id}


def _settled_receipt(page, marker: dict, *, timeout_ms: int = 25000) -> dict:
    """Observe a specific intent's terminal receipt, including a bounded timeout.

    This is a service observation, not a fixed-duration UI grace period.
    Do not emit request contents or sensitive host telemetry into the report.
    """
    try:
        page.wait_for_function("""({id, before}) => {
          const items = window.__mcelWorkbench?.app?.receipts?.() || [];
          return items.filter(r => r.intentId === id).length > before;
        }""", arg={'id': marker['intent'], 'before': marker['before']}, timeout=timeout_ms)
    except Exception as exc:
        if 'Timeout' not in type(exc).__name__:
            raise
        return {'disposition': 'NOT_SETTLED_WITHIN_BOUND'}
    receipts = page.evaluate("""(id) => window.__mcelWorkbench.app.receipts()
        .filter(r => r.intentId === id)""", marker['intent'])
    latest = receipts[-1]
    # A refusal category is useful diagnostic evidence, but never copy an
    # arbitrary server exception or monitored-process detail into the report.
    reason = str(latest.get('reason') or '').split(':', 1)[0]
    reason_class = reason if re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', reason) else 'UNSPECIFIED'
    return {'disposition': latest['disposition'], 'reason_class': reason_class}


def _exercise(page, app: str) -> dict:
    """Exercise only safe UI intents; lack of service response remains incomplete."""
    if app == 'calculator':
        form = page.locator('form[data-intent="intent:evaluate"]')
        form.locator('input[name="expression"]').fill('2+2')
        form.locator('button[type="submit"]').click()
        page.wait_for_timeout(200)
        answer = page.locator('[data-mcel-region*="answer"]').inner_text(timeout=4000)
        return {'local_arithmetic': 'PASS' if '4' in answer else 'FAIL',
                'symbolic_model_chat': 'UNVERIFIED_NOT_REQUESTED'}
    if app == 'file-manager':
        page.wait_for_function('''() =>
            [...document.querySelectorAll('[data-mcel-region*="locations"] [aria-selected="true"]')].length > 0 ||
            [...document.querySelectorAll('[data-mcel-region*="directory"] [data-record-key]')].length > 0''', timeout=8000)
        return {'startup_root_and_listing': 'OBSERVED', 'file_preview': 'UNVERIFIED_NOT_REQUESTED',
                'cross_app_handoff': 'UNVERIFIED_NOT_REQUESTED'}
    if app == 'task-manager':
        # Live overview requests can be much slower than a hermetic fake.
        # Verify completed semantic receipts, not arbitrary 350 ms sleeps.
        observations = {}
        page.locator('[data-mcel-mode-tabs] [data-value="cpu-gpu"]').click(timeout=6000)
        hardware_form = page.locator('form[data-intent="intent:refresh-hardware"]')
        marker = _receipt_marker(page, 'intent:refresh-hardware')
        hardware_form.locator('button[type="submit"]').click(timeout=6000)
        observations['hardware'] = _settled_receipt(page, marker)
        metrics = page.locator('[data-mcel-region*="hardware"] [data-mcel-metric-value]').count()
        observations['hardware']['metric_cards'] = metrics
        page.locator('[data-mcel-mode-tabs] [data-value="connections"]').click(timeout=6000)
        connection_form = page.locator('form[data-intent="intent:refresh-connections"]')
        marker = _receipt_marker(page, 'intent:refresh-connections')
        connection_form.locator('button[type="submit"]').click(timeout=6000)
        observations['connections'] = _settled_receipt(page, marker)
        con = page.locator('[data-mcel-region*="connections"]').inner_text(timeout=6000)
        return {'cpu_gpu_view': 'VISIBLE' if observations['hardware']['disposition']=='completed' and metrics >= 2 else 'EMPTY',
                'connections_view': 'VISIBLE' if observations['connections']['disposition']=='completed' and con.strip() else 'EMPTY',
                'monitor_receipts': observations,
                'process_mutations': 'BLOCKED_NOT_TESTED', 'schedule_mutations': 'BLOCKED_NOT_TESTED'}
    if app == 'code-editor':
        controls = page.locator('button[data-intent="intent:list-files"],form[data-intent="intent:list-files"] button[type="submit"]')
        if controls.count():
            controls.first.click(timeout=6000)
        page.wait_for_timeout(500)
        return {'file_inventory': 'VISIBLE' if page.locator('[data-record-key]').count() else 'EMPTY',
                'monaco_file_model': 'UNVERIFIED_NOT_REQUESTED',
                'source_writes': 'BLOCKED_NOT_TESTED', 'aider_effects': 'BLOCKED_NOT_TESTED'}
    raise ValueError(app)


def run(host_url: str, report: Path = DEFAULT_REPORT, apps: tuple[str, ...] = APPS) -> dict:
    from playwright.sync_api import sync_playwright

    base = validate_loopback_base(host_url)
    if not apps or any(app not in APPS for app in apps) or len(set(apps)) != len(apps):
        raise ValueError('Invalid application selection')
    result = {'schema': 'mcel.four-app-live-readonly.v1',
              'scope': 'real-loopback-browser-read-only-no-mutation-no-promotion',
              'host': base, 'apps': {}, 'overall': 'INCOMPLETE',
              'live_mutations': 'NOT_TESTED', 'human_approval': 'NOT_INFERRED'}
    with sync_playwright() as p:
        browser, browser_type = launch_semantic_browser(p)
        result['browser'] = browser_type
        try:
            for app in apps:
                entries: list[dict] = []
                attempted: list[dict] = []
                failures: list[dict] = []
                blocked: list[dict] = []
                errors: list[str] = []
                context = browser.new_context(viewport={'width': 1272, 'height': 900},
                                              accept_downloads=False, service_workers='block')
                try:
                    def on_route(route, request):
                        decision = request_decision(app, base, request.url, request.method,
                                                    request.post_data)
                        if decision.startswith('allow-'):
                            route.continue_()
                        else:
                            blocked.append({'method': request.method,
                                            'path': urlsplit(request.url).path[:150],
                                            'decision': decision})
                            route.abort()
                    context.route('**/*', on_route)
                    context.route_web_socket('**/*', lambda ws: (blocked.append(
                        {'method': 'WEBSOCKET', 'decision': 'deny-websocket'}), ws.close()))
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)[:250]))
                    def on_request(request):
                        path = urlsplit(request.url).path
                        if path.startswith('/api/') and len(attempted) < 100:
                            attempted.append({'method':request.method,'path':path[:150]})
                    page.on('request', on_request)
                    def on_failed(request):
                        path = urlsplit(request.url).path
                        if path.startswith('/api/') and len(failures) < 100:
                            failures.append({'method':request.method,'path':path[:150],
                                             'failure':'REQUEST_FAILED'})
                    page.on('requestfailed', on_failed)
                    def on_response(response):
                        path = urlsplit(response.url).path
                        if path.startswith('/api/') and len(entries) < 100:
                            entries.append({'method': response.request.method, 'path': path[:150],
                                            'status': response.status})
                    page.on('response', on_response)
                    evidence = {'status': 'INCOMPLETE', 'requests': entries, 'request_attempts': attempted,
                                'request_failures': failures, 'blocked': blocked,
                                'page_errors': errors}
                    result['apps'][app] = evidence
                    expected = json_fingerprint(candidate_ir(app))
                    response = page.goto(base + APPLICATIONS[app]['semantic_route'],
                                         wait_until='domcontentloaded', timeout=15000)
                    evidence['route_http'] = response.status if response else None
                    page.wait_for_timeout(350)
                    evidence['mount'] = _summary(page, app, expected)
                    evidence['checks'] = _exercise(page, app)
                    expected_post = {
                        'calculator': (),
                        'file-manager': ('/api/applications/file-explorer/roots', '/api/applications/file-explorer/list'),
                        'task-manager': ('/api/applications/task/overview',),
                        'code-editor': ('/api/applications/editor/files',),
                    }[app]
                    evidence['required_live_requests'] = {
                        path: any(x['path'] == path and x['status'] == 200 for x in entries)
                        for path in expected_post
                    }
                    # A service 200 is a transport observation, not a guarantee of
                    # meaningful returned content; explicit UI gates remain required.
                    if app == 'task-manager':
                        evidence['three_monitor_submissions'] = sum(
                            x['path'] == '/api/applications/task/overview' and x['status'] == 200
                            for x in entries) >= 3
                    else:
                        evidence['three_monitor_submissions'] = True
                    visible_ok = all(v in ('PASS', 'VISIBLE', 'OBSERVED')
                                     for k, v in evidence['checks'].items()
                                     if k in ('local_arithmetic', 'startup_root_and_listing',
                                              'cpu_gpu_view', 'connections_view', 'file_inventory'))
                    evidence['status'] = ('PASS_LIVE_READ_ONLY' if
                        evidence['route_http'] == 200 and
                        all(evidence['mount'][k] for k in ('ready', 'source_fingerprint_matches',
                             'app_id_matches', 'mounted', 'no_horizontal_overflow')) and
                        visible_ok and evidence['three_monitor_submissions'] and
                        all(evidence['required_live_requests'].values()) and
                        not blocked and not errors else 'INCOMPLETE')
                except Exception as exc:
                    evidence['error'] = f'{type(exc).__name__}: {str(exc)[:350]}'
                finally:
                    context.close()
                print(f'MCEL_LIVE_READONLY {app}: {evidence["status"]}', flush=True)
        finally:
            browser.close()
    result['overall'] = ('PASS_LIVE_READ_ONLY' if all(
        evidence['status'] == 'PASS_LIVE_READ_ONLY' for evidence in result['apps'].values())
        else 'INCOMPLETE')
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-url', required=True, help='Explicit loopback URL, e.g. http://127.0.0.1:8765')
    parser.add_argument('--app', choices=APPS, action='append', help='Optional subset; repeatable')
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = run(args.host_url, args.report, tuple(args.app) if args.app else APPS)
    print('MCEL_FOUR_APP_LIVE_READONLY', result['overall'])
    print('  report:', args.report)
    print('  real writes/process effects/provider operations: NOT CERTIFIED; promotion: BLOCKED')
    raise SystemExit(0 if result['overall'] == 'PASS_LIVE_READ_ONLY' else 1)


if __name__ == '__main__':
    main()
