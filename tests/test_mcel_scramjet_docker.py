"""Critical production lifecycle invariants; no Docker daemon required."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from main_computer.mcel_scramjet_docker import ScramjetDockerManager, IP, PRODUCTION_IMAGE
from main_computer.mcel_browser_host import render_browser_page


def test_main_browser_surface_is_native_and_strictly_separate_origin():
    html = render_browser_page()
    assert 'id="browser-frame"' in html
    assert '<iframe' in html
    assert 'id="browser-image"' not in html
    assert 'surface-start' in html
    assert 'surface-touch' in html
    assert 'surface-close' in html
    assert "base.hostname !== '127.0.0.3'" in html
    assert '/api/applications/browser/frame' not in html


def test_multiple_browser_leases_hold_one_container_until_final_close(tmp_path):
    manager = ScramjetDockerManager(tmp_path)
    container = {'container': 'mcel-browser-scramjet-app-1234abcd',
                 'url': 'http://127.0.0.3:49152/', 'image': PRODUCTION_IMAGE}
    counts = {'launch': 0, 'stop': 0}

    def fake_start():
        counts['launch'] += 1
        manager._container = container
        return container

    def fake_stop():
        counts['stop'] += 1
        manager._container = None

    try:
        with patch.object(manager, '_start', side_effect=fake_start), \
             patch.object(manager, '_healthy', return_value=True), \
             patch.object(manager, '_stop', side_effect=fake_stop):
            first, second = manager.open(), manager.open()
            assert first['session'] != second['session']
            assert first['sidecar_url'] == second['sidecar_url']
            assert first['engine'] == 'scramjet-docker'
            assert counts['launch'] == 1
            manager.touch(first['session'])
            assert manager.release(first['session'])['active_sessions'] == 1
            assert counts['stop'] == 0
            assert manager.release(second['session'])['active_sessions'] == 0
            assert counts['stop'] == 1
    finally:
        manager.close()


def test_abandoned_lease_is_reaped_and_container_stops(tmp_path):
    manager = ScramjetDockerManager(tmp_path)
    calls = []
    try:
        with patch.object(manager, '_start', return_value={'url':'http://127.0.0.3:3333/'}), \
             patch.object(manager, '_healthy', return_value=True), \
             patch.object(manager, '_stop', side_effect=lambda: (calls.append('stop'), setattr(manager, '_container', None))):
            token = manager.open()['session']
            manager._container = {'url':'http://127.0.0.3:3333/'}
            manager._leases[token] -= 10000
            manager._expire()
            assert not manager._leases
            assert calls == ['stop']
    finally:
        manager.close()


def test_docker_only_publishes_to_isolated_ip_and_disables_fixture_mode(tmp_path):
    source = Path('main_computer/mcel_scramjet_docker.py').read_text(encoding='utf8')
    patch = Path('docker/mcel-browser-scramjet/patch-server.mjs').read_text(encoding='utf8')
    assert "IP = '127.0.0.3'" in source
    assert "f'{IP}:{port}:8080'" in source
    assert "'MCEL_SCRAMJET_SMOKE=0'" in source
    assert "'MCEL_SCRAMJET_BROWSER=1'" in source
    assert 'wisp.options.allow_private_ips = false' in patch
    assert 'wisp.options.allow_loopback_ips = false' in patch
    assert "req.headers.origin !== 'http://' + host" in patch
    assert "String.raw" in patch


def test_production_start_command_uses_distinct_image_and_never_smoke_policy(tmp_path):
    manager = ScramjetDockerManager(tmp_path)
    commands = []
    from unittest.mock import Mock

    def docker(*parts, **kw):
        commands.append(parts)
        if parts[:2] == ('image','inspect'):
            return SimpleNamespace(returncode=0, stdout='')  # Pinned image already cached
        if parts[0] == 'run':
            return SimpleNamespace(returncode=0, stdout='test-container-id')
        return SimpleNamespace(returncode=0, stdout='')

    try:
        with patch('tools.mcel_browser_scramjet_smoke.require_docker'), \
             patch('tools.mcel_browser_scramjet_smoke.port_for', return_value=50991), \
             patch.object(manager,'_docker',side_effect=docker), \
             patch.object(manager,'_healthy',return_value=True), \
             patch.object(manager,'_stop',side_effect=lambda: setattr(manager,'_container',None)):
            state = manager._start()
            cmd = next(parts for parts in commands if parts[0]=='run')
            assert '--publish' in cmd and '127.0.0.3:50991:8080' in cmd
            assert 'MCEL_SCRAMJET_SMOKE=0' in cmd
            assert 'MCEL_SCRAMJET_BROWSER=1' in cmd
            assert any(x.startswith('--cap-drop') for x in cmd) and any(x.startswith('--security-opt') for x in cmd)
            assert '--read-only' in cmd
            assert cmd[-1] == PRODUCTION_IMAGE
            assert state['url'] == 'http://127.0.0.3:50991/'
            manager._container = None
    finally:
        manager.close()


def test_surface_command_requires_same_origin_and_valid_session():
    import json
    from io import BytesIO
    from main_computer.mcel_backend_browser_route import handle_browser_post

    class Handler:
        client_address = ('127.0.0.1',40000)
        path = '/api/applications/browser/control'
        def __init__(self, action, origin='http://127.0.0.1:8765', session=None):
            data = {'operation': action}
            if session is not None:data['session'] = session
            raw = json.dumps(data).encode()
            self.headers = {'Host':'127.0.0.1:8765','Origin':origin,
                'X-MCEL-Browser-Operation':'browser-control','Content-Type':'application/json',
                'Content-Length':str(len(raw))}
            manager = Mock()
            manager.open.return_value = {'ok':True,'session':'a'*32,'sidecar_url':'http://127.0.0.3:50120/'}
            manager.release.return_value = {'ok':True,'active_sessions':0}
            self.server = SimpleNamespace(server_port=8765,scramjet_docker=manager)
            self.rfile = BytesIO(raw)
            self.sent = None
        def _send_json(self, value, status=None):self.sent = (value,status)

    accepted = Handler('surface-start')
    handle_browser_post(accepted)
    assert accepted.sent[0]['sidecar_url'] == 'http://127.0.0.3:50120/'
    accepted.server.scramjet_docker.open.assert_called_once_with()
    denied = Handler('surface-start', origin='http://127.0.0.3:50120')
    handle_browser_post(denied)
    assert denied.sent[1] == 403
    denied.server.scramjet_docker.open.assert_not_called()
    bad = Handler('surface-close',session='bad')
    handle_browser_post(bad)
    assert bad.sent[1] == 400
    bad.server.scramjet_docker.release.assert_not_called()


def test_cached_old_image_requires_rebuild(tmp_path):
    from main_computer.mcel_scramjet_docker import ScramjetDockerManager
    manager = ScramjetDockerManager(tmp_path)
    try:
        with patch.object(manager, '_owned', return_value=True), patch('tools.mcel_browser_scramjet_smoke.fetch_status') as fetch:
            assert manager._healthy({'image': 'mcel-browser-scramjet-app:f6f83cbc9309-v1',
                                     'url': 'http://127.0.0.3:34567/'}) is False
            fetch.assert_not_called()
    finally:
        manager.close()

def test_health_checks_surface_not_only_homepage(tmp_path):
    from main_computer.mcel_scramjet_docker import ScramjetDockerManager
    manager = ScramjetDockerManager(tmp_path)
    try:
        calls = []
        def fake_get(url, timeout=2):
            calls.append(url)
            return (404, 0) if url.endswith('mcel-surface.js') else (200, 2000)
        with patch.object(manager, '_owned', return_value=True), patch('tools.mcel_browser_scramjet_smoke.fetch_status', side_effect=fake_get):
            assert manager._healthy({'image': PRODUCTION_IMAGE, 'url':'http://127.0.0.3:45678/'}) is False
            assert any(url.endswith('mcel-surface.js') for url in calls)
    finally:
        manager.close()

def test_frontend_initialization_has_probe_errors_and_deadline():
    surface = Path('docker/mcel-browser-scramjet/mcel-surface.js').read_text(encoding='utf-8')
    parent = Path('main_computer/web/applications/scripts/browser-page.js').read_text(encoding='utf-8')
    assert "m.action === 'probe'" in surface
    assert "notify('ready')" in surface
    assert "service-worker-registration" in surface and "network-transport" in surface
    assert "window.addEventListener('unhandledrejection'" in surface
    assert "BOOT_DEADLINE_MS = 45000" in parent
    assert "postMessage({mcelBrowserControl:true, action:'probe'}" in parent
    assert "bootFailure('initialization timed out" in parent
    assert "mcel-main-surface-controller" in Path('tools/mcel_browser_scramjet_smoke.py').read_text()
