"""Patch 1: Main Computer -> permanent localhost control service, no org listener."""
from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import sys
from threading import Thread

from main_computer.slh import gateway_service
from main_computer.slh.local_service import create_server, _existing_local_service
from main_computer.service_supervisor import CHILD_SPECS
from main_computer.slh.organization_state import initialize_disabled, organization_status
from main_computer.slh.storage import slh_state_dir


def _read_local(port, path='/v1/local/status'):
    conn = HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        conn.request('GET', path)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_resident_control_plane_serves_before_bootstrap_and_never_launches_gateway(tmp_path: Path):
    with create_server(state_dir=tmp_path, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, data = _read_local(server.server_port)
            assert status == 200
            assert data['schema'] == 'main-computer.slh-local-status.v1'
            assert data['local_service']['host'] == '127.0.0.1'
            assert data['local_service']['port'] == server.server_port
            assert data['initialization']['initialized'] is False
            assert data['organization_service'] == {
                'configured': False, 'enabled': False, 'state': 'not_configured', 'listening': False}
            assert _existing_local_service(server.server_port)
            assert _read_local(server.server_port, '/v1/bootstrap/status')[0] == 200
            assert (tmp_path / 'bootstrap.secret').exists()
            assert (tmp_path / 'organization-service.json').exists()
        finally:
            server.shutdown()
            thread.join(timeout=3)



def test_real_local_service_bootstrap_does_not_start_organization(tmp_path: Path):
    from tools.slh_authenticator_cli import bootstrap_admin, save_identity
    identity_dir = tmp_path.parent / (tmp_path.name + '-admin-identity')
    save_identity(identity_dir, 'first-admin-device')
    with create_server(state_dir=tmp_path, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = bootstrap_admin(port=server.server_port, state_dir=tmp_path,
                                     identity_dir=identity_dir, device_id='first-admin-device',
                                     org_id='example-org', admin_id='owner', admin_name='Owner')
            assert result['status'] == 'INITIALIZED'
            status, data = _read_local(server.server_port)
            assert status == 200
            assert data['initialization']['initialized'] is True
            assert data['organization_service']['enabled'] is False
            assert data['organization_service']['listening'] is False
            assert not (tmp_path / 'bootstrap.secret').exists()
        finally:
            server.shutdown()
            thread.join(timeout=3)

def test_config_does_not_activate_organizational_listener(tmp_path: Path):
    initialize_disabled(tmp_path)
    path = tmp_path / 'organization-service.json'
    path.write_text(json.dumps({'schema': 'main-computer.slh-organization-config.v1',
                                'configured': True, 'enabled': True}))
    with create_server(state_dir=tmp_path, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, data = _read_local(server.server_port)
            assert status == 200
            assert data['organization_service']['state'] == 'not_started'
            assert data['organization_service']['listening'] is False
        finally:
            server.shutdown()
            thread.join(timeout=3)
    assert organization_status(tmp_path)['enabled'] is True  # Never overwrite existing state.


def test_supervisor_child_is_local_only(tmp_path: Path):
    specs = {spec.name: spec for spec in CHILD_SPECS}
    assert 'slh' in specs
    assert specs['slh'].module == 'main_computer.slh.gateway_service'
    assert specs['slh'].command(python_command=sys.executable, root=tmp_path) == [
        sys.executable, '-m', 'main_computer.slh.gateway_service', '--root', str(tmp_path), 'run']
    assert specs['slh'].stop_priority < specs['main-log'].stop_priority
    assert gateway_service.default_state_dir() == slh_state_dir()


def test_launcher_still_supervises_slh_without_rebinding_local_boot_task():
    root = Path(__file__).resolve().parents[1]
    launcher = (root / 'start.bat').read_text(encoding='utf-8')
    starter = (root / 'start_v2.bat').read_text(encoding='utf-8')
    stop_ps = (root / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    boot = (root / 'scripts/slh-local-service-autostart.ps1').read_text(encoding='utf-8')
    assert 'start_v2.bat' in launcher and '-Action start' in starter
    assert '"main_computer.slh.gateway_service"' in stop_ps
    assert '"tools.slh_managed_gateway"' in stop_ps
    assert 'New-ScheduledTaskTrigger -AtStartup' in boot
    assert 'main_computer.slh.local_service' in (root / 'scripts/slh-local-service-boot.ps1').read_text()
