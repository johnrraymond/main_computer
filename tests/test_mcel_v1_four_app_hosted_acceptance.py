"""Guardrails for the opt-in four-app evidence runner (not production parity)."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from tools.mcel_v1_four_app_hosted_acceptance import (
    validate_loopback_base, json_fingerprint, APP_IDS, candidate_ir, observe, _route_guard,
    AcceptanceInputError, _deny_websocket,
)


@pytest.mark.parametrize('address', [
    'http://localhost:8765', 'http://127.0.0.2:8765',
    'https://127.0.0.1:8765', 'http://127.0.0.1:8765/foo',
    'http://127.0.0.1:8765/?x=1', 'http://example.com:8765',
    'http://user:pass@127.0.0.1:8765', 'file:///tmp/a',
])
def test_only_explicit_plain_http_127_loopback_origin_allowed(address):
    with pytest.raises(AcceptanceInputError):
        validate_loopback_base(address)


def test_canonical_loopback_accepted():
    assert validate_loopback_base('http://127.0.0.1:8765/') == 'http://127.0.0.1:8765'


def test_fingerprint_is_order_independent_and_changed_by_contents():
    assert json_fingerprint({'a':1,'b':2}) == json_fingerprint({'b':2,'a':1})
    assert json_fingerprint({'a':1}) != json_fingerprint({'a':2})


@pytest.mark.parametrize('app', APP_IDS)
def test_candidate_compiles_to_expected_identity_and_no_authored_layout(app):
    ir = candidate_ir(app)
    assert ir['application']['appId'] == APP_IDS[app]
    assert len(ir['provenance']['frontend']['sourceFiles']) == 2
    assert ir['layouts'] == []


class _Request:
    def __init__(self, url, method='GET'):
        self.url=url; self.method=method


class _Route:
    def __init__(self):self.action=None
    def abort(self):self.action='abort'
    def continue_(self):self.action='continue'


@pytest.mark.parametrize('url,method,expected', [
    ('http://127.0.0.1:8765/applications/mcel-semantic-file-manager', 'GET', 'continue'),
    ('http://127.0.0.1:8765/api/something', 'POST', 'abort'),
    ('https://127.0.0.1:8765/any', 'GET', 'abort'),
    ('http://example.com/any', 'GET', 'abort'),
    ('http://127.0.0.1:8766/any', 'GET', 'abort'),
])
def test_network_guard_blocks_writes_and_outside_origins(url, method, expected):
    route=_Route();blocked=[]
    _route_guard(route,_Request(url,method),'http://127.0.0.1:8765',blocked)
    assert route.action == expected
    assert bool(blocked) == (expected=='abort')


def test_preview_cannot_retarget_live_host(tmp_path):
    with pytest.raises(AcceptanceInputError):
        observe('preview','http://127.0.0.1:8765',tmp_path)
    with pytest.raises(AcceptanceInputError):
        observe('hosted',None,tmp_path)


def test_isolated_render_produces_explicit_noncertification_report(tmp_path):
    result=observe('preview',report_dir=tmp_path,widths=(390,1100))
    assert result['total_viewports']==8
    assert result['rendered_viewports_passed']==8
    assert not result['promotion_authorized']
    assert result['human_design_approval']=='REQUIRED_NOT_GIVEN'
    assert result['runtime_pass']=='NOT_CERTIFIED'
    assert result['verdict']=='OFFLINE_PREVIEW_RECORDED_NOT_HOSTED'
    assert all(not a['production_route_observed'] for a in result['applications'].values())
    assert all(a['source_status']=='SOURCE_CONTRACT_PASS' for a in result['applications'].values())
    assert len(list(tmp_path.glob('*.png')))==8
    saved=json.loads((tmp_path/'report.json').read_text())
    assert saved['v1_suite_certified'] is False


def test_websocket_is_explicitly_refused():
    class WS:
        url='ws://127.0.0.1:8765/some-connection'
        closed=False
        def close(self):self.closed=True
    ws=WS();blocked=[]
    _deny_websocket(ws,blocked)
    assert ws.closed and blocked[0]['reason']=='websocket-disabled'


def test_diagnostic_subset_reports_live_progress_and_checkpoints(tmp_path, capsys):
    result = observe('preview', report_dir=tmp_path, widths=(1100,), apps=('calculator',))
    assert result['scope'] == 'diagnostic_subset'
    assert result['observations_completed'] == 1
    assert result['observations_expected'] == 1
    assert result['total_viewports'] == 1
    assert result['rendered_viewports_passed'] == 1
    assert result['verdict'] == 'DIAGNOSTIC_SUBSET_RECORDED_NOT_ACCEPTED'
    assert result['state'] == 'COMPLETE'
    saved = json.loads((tmp_path / 'report.json').read_text())
    assert saved['applications']['calculator']['viewports'][0]['width'] == 1100
    assert 'MCEL acceptance [1/1]: calculator width=1100 completed' in capsys.readouterr().out


def test_diagnostic_subset_rejects_unsupported_or_duplicate_scope(tmp_path):
    for scope in ({'apps': ('unknown-app',)}, {'apps': ('calculator', 'calculator')},
                  {'widths': (999,)}, {'widths': (1100, 1100)}):
        with pytest.raises(AcceptanceInputError):
            observe('preview', report_dir=tmp_path, **scope)


def test_production_probe_refuses_other_hosts_and_invalid_apps(tmp_path):
    from tools.mcel_v1_four_app_hosted_acceptance import _bounded_production_probe
    with pytest.raises(AcceptanceInputError):
        _bounded_production_probe('http://example.com:8765', 'code-editor', 320, tmp_path)
    with pytest.raises(AcceptanceInputError):
        _bounded_production_probe('http://127.0.0.1:8765', 'unknown', 320, tmp_path)


def test_production_worker_hard_deadline_reports_unverified_and_terminates(monkeypatch, tmp_path):
    import subprocess
    from tools import mcel_v1_four_app_hosted_acceptance as mod

    class HungWorker:
        pid = 77
        returncode = None
        def communicate(self, timeout):
            raise subprocess.TimeoutExpired(cmd='worker', timeout=timeout)
    worker = HungWorker()
    terminated = []
    monkeypatch.setattr(mod.subprocess, 'Popen', lambda *a, **kw: worker)
    monkeypatch.setattr(mod, '_terminate_probe_tree', lambda process: terminated.append(process))
    result = mod._bounded_production_probe('http://127.0.0.1:8765', 'code-editor', 320,
                                           tmp_path, deadline_seconds=1)
    assert result['production_error'].startswith('PRODUCTION_CAPTURE_TIMEOUT_DURING_')
    assert result['production_observation'] == 'UNVERIFIED_TIMEOUT'
    assert result['production_capture_trace'] == {}
    assert terminated == [worker]


def test_production_probe_accepts_only_structured_child_result(monkeypatch, tmp_path):
    from tools import mcel_v1_four_app_hosted_acceptance as mod
    class FinishedWorker:
        returncode = 0
        def communicate(self, timeout):
            return '{"production_http_status": 200, "production_body_nonempty": true}\n', ''
    monkeypatch.setattr(mod.subprocess, 'Popen', lambda *a, **kw: FinishedWorker())
    result = mod._bounded_production_probe('http://127.0.0.1:8765', 'code-editor', 320, tmp_path)
    assert result['production_http_status'] == 200
    assert result['production_body_nonempty'] is True


def test_production_probe_rejects_unstructured_worker_success(monkeypatch, tmp_path):
    from tools import mcel_v1_four_app_hosted_acceptance as mod
    class FinishedWorker:
        returncode = 0
        def communicate(self, timeout):
            return '{"some_other_field": true}\n', ''
    monkeypatch.setattr(mod.subprocess, 'Popen', lambda *a, **kw: FinishedWorker())
    result = mod._bounded_production_probe('http://127.0.0.1:8765', 'code-editor', 320, tmp_path)
    assert result['production_observation'] == 'UNVERIFIED_ERROR'
    assert 'production_http_status' not in result


def test_production_trace_persists_phase_timing_and_completion(tmp_path):
    from tools.mcel_v1_four_app_hosted_acceptance import _CaptureTrace, _load_capture_trace
    record = tmp_path / 'probe.json'
    trace = _CaptureTrace(record, 'code-editor', 320)
    with trace.phase('browser_launch'):
        pass
    with trace.phase('navigation'):
        assert _load_capture_trace(record)['current_phase'] == 'navigation'
        assert _load_capture_trace(record)['phase_state'] == 'RUNNING'
    saved = _load_capture_trace(record)
    assert [s['phase'] for s in saved['completed_phases']] == ['browser_launch', 'navigation']
    assert all(s['duration_ms'] >= 0 and s['result'] == 'PASS' for s in saved['completed_phases'])


def test_worker_timeout_identifies_exact_stalled_phase(monkeypatch, tmp_path):
    import subprocess
    from tools import mcel_v1_four_app_hosted_acceptance as mod

    class Worker:
        pid = 33
        returncode = None
        def communicate(self, timeout):
            raise subprocess.TimeoutExpired(cmd='worker', timeout=timeout)
    def spawn(argv, **kw):
        checkpoint = Path(argv[argv.index('--_phase-file') + 1])
        checkpoint.write_text(json.dumps({'current_phase': 'screenshot',
            'phase_state': 'RUNNING', 'elapsed_ms': 39000,
            'completed_phases': [{'phase': 'navigation', 'duration_ms': 188, 'result': 'PASS'}]}))
        return Worker()
    monkeypatch.setattr(mod.subprocess, 'Popen', spawn)
    monkeypatch.setattr(mod, '_terminate_probe_tree', lambda proc: None)
    result = mod._bounded_production_probe('http://127.0.0.1:8765', 'code-editor', 320,
                                            tmp_path, deadline_seconds=1)
    assert result['production_error'] == 'PRODUCTION_CAPTURE_TIMEOUT_DURING_SCREENSHOT_AFTER_1s'
    assert result['production_capture_trace']['completed_phases'][0]['duration_ms'] == 188
    assert result['production_observation'] == 'UNVERIFIED_TIMEOUT'


def _capture_with_fake_browser(monkeypatch, tmp_path, *, fail_phase=None, root_visible=True, monaco_source='cdn'):
    import playwright.sync_api
    from tools import mcel_v1_four_app_hosted_acceptance as mod
    from types import SimpleNamespace

    calls = []
    class Page:
        def on(self, *a): pass
        def wait_for_function(self, *a, **kw):
            calls.append(('wait_for_function', kw))
            if fail_phase == 'monaco_mount': raise TimeoutError('Monaco did not mount')
            return True
        def set_default_timeout(self, *a): pass
        def goto(self, *a, **kw):
            calls.append(('goto', kw))
            if fail_phase == 'navigation': raise TimeoutError('navigation deadline')
            return SimpleNamespace(status=200)
        def evaluate(self, code, *args):
            if args:
                return {'present': True, 'visible': root_visible, 'hasContent': True}
            if 'MainComputerCodeEditorRuntime' in code:
                return {'runtime_mounted': True, 'adapter_mounted': True, 'mount_ok': True,
                        'mount_outcome': 'model-mounted', 'mount_source': monaco_source,
                        'mount_message': ''}
            return {'title': 'Huge production host', 'hasBody': True, 'state': 'interactive'}
        def screenshot(self, **kw):
            calls.append(('screenshot', kw))
            if fail_phase == 'screenshot': raise TimeoutError('screenshot deadline')
            Path(kw['path']).write_bytes(b'FAKEPNG')
        def close(self): pass
    class Context:
        def route(self, *a): pass
        def route_web_socket(self, *a): pass
        def new_page(self): return Page()
        def close(self): pass
    class Browser:
        def new_context(self, **kw):
            calls.append(('context', kw))
            return Context()
        def close(self): pass
    class Driver:
        def start(self):
            if fail_phase == 'browser_launch': raise RuntimeError('browser cannot start')
            return self
        def stop(self): pass
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', lambda: Driver())
    monkeypatch.setattr(mod, 'launch_semantic_browser', lambda p: (Browser(), 'chromium'))
    result = mod._production_worker_probe('http://127.0.0.1:8765', 'code-editor', 320,
                                           tmp_path, tmp_path / 'stages.json')
    return result, calls


def test_large_production_page_uses_viewport_capture_and_app_root(monkeypatch, tmp_path):
    result, calls = _capture_with_fake_browser(monkeypatch, tmp_path)
    assert result['production_http_status'] == 200
    assert result['production_observation'] == 'OBSERVED'
    assert result['production_app_root_id'] == 'code-editor-app'
    assert result['production_screenshot_mode'] == 'viewport'
    assert next(v for k,v in calls if k == 'screenshot')['full_page'] is False
    assert next(v for k,v in calls if k == 'goto')['wait_until'] == 'domcontentloaded'
    assert [x['phase'] for x in result['production_capture_trace']['completed_phases']] == [
        'browser_launch', 'browser_context', 'navigation', 'dom_readiness',
        'application_initialization', 'monaco_mount', 'screenshot', 'browser_shutdown']


def test_production_screenshot_failure_preserves_precise_phase(monkeypatch, tmp_path):
    result, calls = _capture_with_fake_browser(monkeypatch, tmp_path, fail_phase='screenshot')
    assert result['production_observation'] == 'UNVERIFIED_ERROR'
    assert 'PRODUCTION_CAPTURE_SCREENSHOT_FAILED' in result['production_error']
    failed = [x for x in result['production_capture_trace']['completed_phases'] if x['result']=='FAILED']
    assert len(failed) == 1 and failed[0]['phase'] == 'screenshot'
    assert result.get('production_http_status') == 200
    assert 'production_screenshot' not in result


def test_production_navigation_timeout_reports_navigation_phase(monkeypatch, tmp_path):
    result, _ = _capture_with_fake_browser(monkeypatch, tmp_path, fail_phase='navigation')
    assert result['production_observation'] == 'UNVERIFIED_ERROR'
    assert 'PRODUCTION_CAPTURE_NAVIGATION_FAILED' in result['production_error']


def test_production_browser_startup_failure_is_not_misreported_as_route_failure(monkeypatch, tmp_path):
    result, _ = _capture_with_fake_browser(monkeypatch, tmp_path, fail_phase='browser_launch')
    assert result['production_observation'] == 'UNVERIFIED_ERROR'
    assert 'PRODUCTION_CAPTURE_BROWSER_LAUNCH_FAILED' in result['production_error']


def test_missing_or_hidden_application_root_is_not_accepted(monkeypatch, tmp_path):
    result, _ = _capture_with_fake_browser(monkeypatch, tmp_path, root_visible=False)
    assert result['production_http_status'] == 200
    assert result['production_app_dom_present']
    assert not result['production_app_visible']
    assert result['production_observation'] == 'UNVERIFIED_RENDER_STATE'


def test_full_page_capture_is_explicit_opt_in(monkeypatch, tmp_path):
    from tools import mcel_v1_four_app_hosted_acceptance as mod
    with pytest.raises(AcceptanceInputError):
        mod._bounded_production_probe('http://127.0.0.1:8765', 'code-editor', 320,
                                      tmp_path, screenshot_mode='invalid')


def test_parent_reports_phase_progress_before_child_finishes(monkeypatch, tmp_path, capsys):
    import subprocess
    from tools import mcel_v1_four_app_hosted_acceptance as mod

    class Worker:
        pid = 44
        returncode = 0
        attempts = 0
        def communicate(self, timeout):
            self.attempts += 1
            if self.attempts == 1:
                raise subprocess.TimeoutExpired(cmd='worker', timeout=timeout)
            return json.dumps({'production_http_status': 200,
                               'production_observation': 'OBSERVED'}), ''
    def spawn(argv, **kw):
        checkpoint = Path(argv[argv.index('--_phase-file') + 1])
        checkpoint.write_text(json.dumps({'current_phase': 'navigation',
            'phase_state': 'RUNNING', 'elapsed_ms': 1200,
            'completed_phases': []}))
        return Worker()
    monkeypatch.setattr(mod.subprocess, 'Popen', spawn)
    result = mod._bounded_production_probe('http://127.0.0.1:8765', 'code-editor', 320, tmp_path)
    assert result['production_http_status'] == 200
    assert 'production browser phase=navigation' in capsys.readouterr().out


@pytest.mark.parametrize("uri,method,allowed", [
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js", "GET", True),
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/editor/editor.main.js", "GET", True),
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/editor/editor.main.css", "GET", True),
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/base/browser/ui/codicons/codicon/codicon.ttf", "GET", True),
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js", "POST", False),
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.3/min/vs/loader.js", "GET", False),
    ("https://cdn.jsdelivr.net/npm/another-package@0.52.2/min/vs/loader.js", "GET", False),
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/../secret.js", "GET", False),
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/%2e%2e/secret.js", "GET", False),
    ("https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js?token=abc", "GET", False),
    ("https://cdn.jsdelivr.net.evil.com/npm/monaco-editor@0.52.2/min/vs/loader.js", "GET", False),
    ("http://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js", "GET", False),
    ("https://cdn.jsdelivr.net/", "GET", False),
    ("https://example.com/npm/monaco-editor@0.52.2/min/vs/loader.js", "GET", False),
])
def test_monaco_cdn_exact_version_read_only_allowlist(uri, method, allowed):
    from tools.mcel_v1_four_app_hosted_acceptance import _route_guard
    permitted, blocked = [], []
    route = _Route()
    _route_guard(route, _Request(uri,method), 'http://127.0.0.1:8765', blocked,
                 allow_monaco_cdn=True, permitted_cdn=permitted)
    assert (route.action == 'continue') == allowed
    assert bool(permitted) == allowed
    assert bool(blocked) != allowed


def test_monaco_cdn_denied_for_other_apps_and_preview():
    url = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js'
    for origin in (None, 'http://127.0.0.1:8765'):
        for allow in (False,):
            r, blocked = _Route(), []
            _route_guard(r, _Request(url), origin, blocked, allow_monaco_cdn=allow)
            assert r.action == 'abort' and blocked
    r, blocked = _Route(), []
    _route_guard(r, _Request(url), None, blocked, allow_monaco_cdn=True)
    assert r.action == 'abort' and blocked


@pytest.mark.parametrize('source,expected', [('local-vendor', 'LOCAL_MOUNTED'),('cdn', 'CDN_MOUNTED')])
def test_real_adapter_mount_receipt_accepts_local_or_cdn(monkeypatch, tmp_path, source, expected):
    result, calls = _capture_with_fake_browser(monkeypatch, tmp_path, monaco_source=source)
    assert result['production_observation'] == 'OBSERVED'
    assert result['production_monaco_pathway'] == expected
    assert result['production_monaco_mount']['mount_outcome'] == 'model-mounted'
    assert result['production_screenshot'] == 'code-editor-production-320.png'
    assert any(k == 'wait_for_function' and v['timeout'] == 18000 for k, v in calls)


def test_monaco_mount_timeout_does_not_count_as_production_observed(monkeypatch, tmp_path):
    result, calls = _capture_with_fake_browser(monkeypatch, tmp_path, fail_phase='monaco_mount')
    assert result['production_monaco_pathway'] == 'MONACO_UNAVAILABLE'
    assert result['production_observation'] == 'UNVERIFIED_ERROR'
    assert 'PRODUCTION_CAPTURE_MONACO_MOUNT_FAILED' in result['production_error']
    assert not any(k == 'screenshot' for k, v in calls)
    assert any(x['phase']=='monaco_mount' and x['result']=='FAILED'
               for x in result['production_capture_trace']['completed_phases'])


def test_monaco_loaded_but_no_mount_receipt_is_not_accepted():
    from tools.mcel_v1_four_app_hosted_acceptance import _monaco_pathway
    loader = [{'source': 'cdn', 'status': 200}]
    assert _monaco_pathway({'runtime_mounted': False, 'adapter_mounted': False}, loader) == 'MONACO_UNAVAILABLE'
    assert _monaco_pathway({'runtime_mounted': True, 'adapter_mounted': True, 'mount_ok': False,
                            'mount_outcome': 'loader-exception', 'mount_source': 'cdn'}, loader) == 'MONACO_UNAVAILABLE'
    assert _monaco_pathway({'runtime_mounted': True, 'adapter_mounted': True, 'mount_ok': True,
                            'mount_outcome': 'model-mounted', 'mount_source': 'window.monaco'}, loader) == 'CDN_MOUNTED'


def test_transient_windows_trace_replace_denial_retries_without_losing_phase(monkeypatch, tmp_path):
    from tools import mcel_v1_four_app_hosted_acceptance as mod
    original = Path.replace
    attempts = []

    def contended(self, target):
        attempts.append((self, target))
        if len(attempts) <= 3:
            raise PermissionError(13, 'Access is denied by concurrent reader')
        return original(self, target)

    monkeypatch.setattr(Path, 'replace', contended)
    monkeypatch.setattr(mod.time, 'sleep', lambda duration: None)
    trace = mod._CaptureTrace(tmp_path / 'progress.json', 'calculator', 1100)
    with trace.phase('navigation'):
        pass
    assert len(attempts) >= 6
    assert trace.checkpoint_warnings == []
    saved = mod._load_capture_trace(tmp_path / 'progress.json')
    assert saved['phase_state'] == 'PASS'
    assert saved['completed_phases'][-1]['phase'] == 'navigation'


def test_permanent_trace_file_lock_cannot_turn_browser_success_into_failure(monkeypatch, tmp_path):
    from tools import mcel_v1_four_app_hosted_acceptance as mod
    monkeypatch.setattr(Path, 'replace', lambda self, target: (
        (_ for _ in ()).throw(PermissionError(13, 'Access is denied'))))
    monkeypatch.setattr(mod.time, 'sleep', lambda duration: None)
    result, calls = _capture_with_fake_browser(monkeypatch, tmp_path)
    assert result['production_http_status'] == 200
    assert result['production_observation'] == 'OBSERVED'
    assert result.get('production_error') is None
    assert result['production_screenshot'] == 'code-editor-production-320.png'
    assert result['production_capture_trace']['checkpoint_warnings']
    assert all('PHASE_CHECKPOINT_WRITE_FAILED' in warning
               for warning in result['production_capture_trace']['checkpoint_warnings'])


def test_main_report_checkpoint_retries_transient_windows_replace_denial(monkeypatch, tmp_path):
    from tools import mcel_v1_four_app_hosted_acceptance as mod
    original = Path.replace
    count = [0]

    def contended(self, target):
        count[0] += 1
        if count[0] < 3:
            raise PermissionError(13, 'Access is denied')
        return original(self, target)

    monkeypatch.setattr(Path, 'replace', contended)
    monkeypatch.setattr(mod.time, 'sleep', lambda duration: None)
    mod._checkpoint(tmp_path, {'state': 'COMPLETE'})
    assert count[0] == 3
    assert json.loads((tmp_path / 'report.json').read_text())['state'] == 'COMPLETE'


def test_finished_worker_trace_overrides_stale_polled_checkpoint(monkeypatch, tmp_path):
    from tools import mcel_v1_four_app_hosted_acceptance as mod
    final_trace = {'current_phase': 'browser_shutdown', 'phase_state': 'PASS',
                   'completed_phases': [{'phase': 'screenshot', 'duration_ms': 145, 'result': 'PASS'}],
                   'checkpoint_warnings': ['PHASE_CHECKPOINT_WRITE_FAILED: PermissionError']}

    class Worker:
        returncode = 0
        def communicate(self, timeout):
            return json.dumps({'production_http_status': 200,
                               'production_observation': 'OBSERVED',
                               'production_capture_trace': final_trace}), ''

    def spawn(argv, **kwargs):
        checkpoint = Path(argv[argv.index('--_phase-file') + 1])
        checkpoint.write_text(json.dumps({'current_phase': 'navigation',
                                          'phase_state': 'RUNNING', 'completed_phases': []}))
        return Worker()

    monkeypatch.setattr(mod.subprocess, 'Popen', spawn)
    result = mod._bounded_production_probe('http://127.0.0.1:8765',
                                            'code-editor', 320, tmp_path)
    assert result['production_observation'] == 'OBSERVED'
    assert result['production_capture_trace'] == final_trace
