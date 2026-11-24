"""Promotion is a Worker-only browser-route switch, never a spending action."""
from __future__ import annotations
import json
from pathlib import Path

from main_computer.mcel_worker_application import render_worker_page
from main_computer.mcel_applications_registry import read_registry
from main_computer.mcel_worker_route_promotion import canonical_worker_mode, set_canonical_worker_mode
from main_computer import viewport_route_dispatch as routes

ROOT = Path(__file__).resolve().parents[1]


def test_default_promoted_and_atomic_local_rollback(tmp_path):
    assert canonical_worker_mode(tmp_path) == 'promoted'
    target = set_canonical_worker_mode('legacy', tmp_path)
    assert target.exists()
    assert canonical_worker_mode(tmp_path) == 'legacy'
    set_canonical_worker_mode('promoted', tmp_path)
    assert canonical_worker_mode(tmp_path) == 'promoted'
    assert not list(target.parent.glob('.mcel-worker-route-*'))


def test_corrupt_rollback_file_fails_closed(tmp_path):
    path = tmp_path / 'runtime/state/mcel_worker_canonical_route.json'
    path.parent.mkdir(parents=True)
    path.write_text('{bad json')
    assert canonical_worker_mode(tmp_path) == 'legacy'
    path.write_text(json.dumps({'schema': 'mcel.worker.canonical-route.v1', 'mode': 'deleted'}))
    assert canonical_worker_mode(tmp_path) == 'legacy'


def test_only_worker_routes_promote_and_all_aliases_revert(monkeypatch):
    from main_computer import mcel_worker_route_promotion as cutover
    monkeypatch.setattr(cutover, 'canonical_worker_mode', lambda: 'promoted')
    for route in ('/applications/worker', '/apps/worker', '/app/worker'):
        assert routes._semantic_canonical_route(route) == ('worker', None)
    assert routes._semantic_canonical_route('/applications/worker/extra') is None
    assert routes._semantic_canonical_route('/applications/wallet') is None
    monkeypatch.setattr(cutover, 'canonical_worker_mode', lambda: 'legacy')
    for route in ('/applications/worker', '/apps/worker', '/app/worker'):
        assert routes._semantic_canonical_route(route) is None
    assert routes._semantic_canonical_route('/applications/calculator') == ('calculator', None)


def test_canonical_worker_uses_native_semantics_and_preview_remains_independent():
    assert next(a for a in read_registry(ROOT)['apps'] if a['id'] == 'worker')['mount'] == 'semantic'
    promoted = render_worker_page(ROOT, canonical=True)
    preview = render_worker_page(ROOT)
    assert 'data-mcel-worker-phase="promoted"' in promoted
    assert 'data-mcel-worker-phase="candidate"' in preview
    for page in (promoted, preview):
        assert 'McelSemanticWorkerPorts.create' in page
        assert 'initWorkerApp();' not in page
        assert 'function initWorkerWalletOnly()' in page
        assert 'McelSemanticOperationalRuntime.mount' in page


def test_canonical_http_shell_and_worker_child_are_distinct(monkeypatch):
    import threading
    from urllib.request import urlopen
    from main_computer.config import MainComputerConfig
    from main_computer.viewport_server import ViewportServer
    from main_computer import mcel_worker_route_promotion as cutover

    server = ViewportServer(('127.0.0.1', 0), MainComputerConfig(workspace=ROOT), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = 'http://127.0.0.1:' + str(server.server_port)
        monkeypatch.setattr(cutover, 'canonical_worker_mode', lambda: 'promoted')
        with urlopen(host + '/applications/worker', timeout=20) as response:
            parent = response.read().decode('utf-8')
        assert 'data-mcel-shell-canonical-promoted="true"' in parent
        assert '"id":"worker"' in parent
        with urlopen(host + '/applications/worker?mcel-shell-child=1', timeout=20) as response:
            child = response.read().decode('utf-8')
        assert 'data-mcel-worker-phase="promoted"' in child
        assert 'McelSemanticWorkerPorts.create' in child
        assert 'data-mcel-shell-canonical-promoted="true"' not in child
        with urlopen(host + '/applications/mcel-semantic-worker', timeout=20) as response:
            diagnostic = response.read().decode('utf-8')
        assert 'data-mcel-worker-phase="candidate"' in diagnostic
        monkeypatch.setattr(cutover, 'canonical_worker_mode', lambda: 'legacy')
        with urlopen(host + '/applications/worker?mcel-shell-child=1', timeout=20) as response:
            rollback = response.read().decode('utf-8')
        assert 'id="mcel-native-child-chrome"' in rollback
        assert 'data-mcel-worker-phase="promoted"' not in rollback
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
