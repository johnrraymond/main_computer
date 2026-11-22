"""Regression: cold Docker startup is not judged by the reuse-only health check."""
from __future__ import annotations
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scramjet_smoke_0012', HERE / 'tools/mcel_browser_scramjet_smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def fixture_repo(tmp_path):
    folder = tmp_path / 'docker' / 'mcel-browser-scramjet'
    folder.mkdir(parents=True)
    for asset in smoke.DOCKER_ASSETS:
        (folder / asset).write_text(asset, encoding='utf-8')
    smoke.report_dir(tmp_path).mkdir(parents=True)
    return tmp_path


def docker_alive(*args, **kwargs):
    if args[0] == 'run':
        return SimpleNamespace(stdout='container-id', returncode=0)
    if args[0] == 'inspect':
        return SimpleNamespace(stdout='true|0|', returncode=0)
    return SimpleNamespace(stdout='test-logs', returncode=0)


def do_start(repo, fetch, resource):
    with patch.object(smoke, 'load_state', return_value=None), \
         patch.object(smoke, 'status', return_value=None) as status, \
         patch.object(smoke, 'build_image', return_value=smoke.expected_image(repo)), \
         patch.object(smoke, 'docker_cmd', side_effect=docker_alive), \
         patch.object(smoke, 'port_for', return_value=50123), \
         patch.object(smoke, 'fetch_status', side_effect=fetch) as req, \
         patch.object(smoke, 'resource_preflight', side_effect=resource) as pre, \
         patch.object(smoke.time, 'sleep', return_value=None):
        result = smoke.start(repo)
        status.assert_called_once_with(repo)
        return result, req.call_count, pre.call_count


def test_transient_connection_refused_then_service_starts(tmp_path):
    repo=fixture_repo(tmp_path)
    state, requests, preflights = do_start(repo,
        [smoke.URLError('connection refused'), smoke.URLError('remote closed'), (200, 1000)],
        [(True, 'assets are ready')])
    assert state['url'] == 'http://127.0.0.2:50123/'
    assert requests == 3 and preflights == 1


def test_transient_resource_disconnect_is_retried(tmp_path):
    repo=fixture_repo(tmp_path)
    state, requests, preflights = do_start(repo,
        [(200, 1000), (200, 1000), (200, 1000)],
        [(False, 'mcel-surface.html: RemoteDisconnected: server booting'),
         (False, 'mcel-surface.js: HTTP 503'), (True, 'all assets ready')])
    assert requests == preflights == 3
    assert state['image'].startswith('mcel-browser-scramjet-smoke:')


def test_crashed_server_reports_exit_code_and_retains_logs(tmp_path):
    repo=fixture_repo(tmp_path)
    seen=[]
    def docker(*args,**kw):
        seen.append(args)
        if args[0]=='run': return SimpleNamespace(returncode=0,stdout='container-id')
        if args[0]=='inspect': return SimpleNamespace(returncode=0,stdout='false|42|server crashed')
        if args[0]=='logs': return SimpleNamespace(returncode=0,stdout='MCEL_FIXTURE_READY\nTypeError: bad startup')
        return SimpleNamespace(returncode=0,stdout='')
    with patch.object(smoke,'load_state', return_value=None), \
         patch.object(smoke, 'status', return_value=None) as status, \
         patch.object(smoke, 'build_image', return_value=smoke.expected_image(repo)), \
         patch.object(smoke, 'docker_cmd', side_effect=docker), \
         patch.object(smoke, 'port_for', return_value=50123), \
         patch.object(smoke,'fetch_status',side_effect=smoke.URLError('remote closed')), \
         patch.object(smoke,'stop') as stop:
        with pytest.raises(RuntimeError,match='Docker Scramjet sidecar exited: Docker process exited: false\\|42\\|server crashed') as exc:
            smoke.start(repo)
        stop.assert_called_once_with(repo)
        status.assert_called_once_with(repo)
    report=smoke.report_dir(repo)/'startup-failure.log'
    assert 'TypeError: bad startup' in report.read_text()
    assert 'Full diagnostics' in str(exc.value)
    assert any(x[0]=='logs' for x in seen)
