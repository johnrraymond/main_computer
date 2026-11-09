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
                'configured': False, 'enabled': False, 'state': 'not_configured', 'listening': False,
                'bind_host': None, 'api_port': None, 'network_access': 'NOT_CONFIGURED'}
            assert data['administrator_enrollment'] == {
                'state': 'NOT_ESTABLISHED', 'local_credential': 'NOT_VERIFIED'}
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
                                     admin_id='owner', admin_name='Owner')
            assert result['status'] == 'INITIALIZED'
            status, data = _read_local(server.server_port)
            assert status == 200
            assert data['initialization']['initialized'] is True
            assert data['administrator_enrollment']['state'] == 'ENROLLED'
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


def test_start_slh_is_independent_of_dev_hub_and_never_registers_a_boot_task():
    """Only --no-slh-hub may suppress the local SLH startup pathway."""
    script = (Path(__file__).resolve().parents[1] / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    start = script[script.index('function Start-MainComputer('):script.index('function Start-MainComputerDevHubOnly')]
    assert 'if (-not $NoSLHHubRequested) {\n    # Check ownership BEFORE' in start
    assert 'Get-MainComputerSLHBootTask $RootPath' in start
    assert start.index('Get-MainComputerSLHBootTask $RootPath') < start.index('Stop-MainComputer $RootPath $true')
    assert 'if (-not $NoSLHHubRequested) {\n    $slhBootRestart = Restart-MainComputerSLHBootTask' in start
    assert start.index('Stop-MainComputer $RootPath $true') < start.index('Restart-MainComputerSLHBootTask $RootPath $slhBootTask')
    assert start.index('Restart-MainComputerSLHBootTask $RootPath $slhBootTask') < start.index('Start-Process `')
    assert 'SLH startup disabled by --no-slh-hub' in start
    assert 'Set-MainComputerLaunchEnvironmentValue $launchContext "MAIN_COMPUTER_SUPERVISE_SLH" $(if ($NoSLHHubRequested) { "0" } else { "1" })' in start
    assert start.index('MAIN_COMPUTER_SUPERVISE_SLH') < start.index('Set-MainComputerLaunchEnvironment $launchContext')
    assert 'Register-ScheduledTask' not in start
    assert 'New-ScheduledTaskTrigger' not in start
    # Dev-Hub opt-out remains independent of SLH policy.
    slh_section = start[start.index('# SLH and Dev-Test Hub'):start.index('  if ($NanoJevManagedRequested)')]
    assert '$NoDevHubRequested' not in slh_section
    starter = (Path(__file__).resolve().parents[1] / 'start_v2.bat').read_text(encoding='utf-8')
    assert 'if /I "%~1"=="--no-dev-hub" goto mc_disable_dev_hub' in starter
    assert 'if /I "%~1"=="--no-slh-hub" goto mc_disable_slh_hub' in starter
    assert 'set "MC_NO_DEV_HUB=0"' in starter
    assert 'set "MC_NO_SLH_HUB=0"' in starter
    assert 'set "MC_HUB_START_ARGS=%MC_HUB_START_ARGS% -NoDevHub"' in starter
    assert 'set "MC_HUB_START_ARGS=%MC_HUB_START_ARGS% -NoSLHHub"' in starter


def test_boot_task_restart_verifies_exact_checkout_and_only_owns_local_api():
    script = (Path(__file__).resolve().parents[1] / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    identity = script[script.index('function Get-MainComputerSLHBootTask('):script.index('function Get-MainComputerSLHListenerPid')]
    restart = script[script.index('function Restart-MainComputerSLHBootTask('):script.index('function Start-MainComputer(')]
    assert '"MainComputer-SLH-Local"' in identity
    assert 'scripts\\slh-local-service-boot.ps1' in identity
    assert '-Root\\s+' in identity and '-File\\s+' in identity and '-Python\\s+' in identity
    assert 'OrdinalIgnoreCase' in identity and 'different installation' in identity
    assert 'Stop-ScheduledTask' not in identity
    assert 'Get-NetstatListenRows -Ports @(8897)' in script
    assert 'supervisor-will-own-listener' in restart
    assert 'Refusing to attach stale or foreign code' in restart
    assert restart.index('Stop-ScheduledTask') < restart.index('Start-ScheduledTask')
    assert 'if (-not $released)' in restart
    assert 'main-computer.slh-local-status.v1' in restart
    assert 'boot-task-restarted' in restart
    assert 'throw "SLH boot task did not start a fresh' in restart
    # Normal application startup has no role in the public Coolify deployment.
    assert 'coolify' not in restart.lower()


def test_windows_boot_task_restart_isolated_mock(tmp_path):
    """On Windows, execute the handoff logic with mocked Task Scheduler and port calls."""
    import os
    import shutil
    import subprocess
    import pytest

    powershell = shutil.which('powershell.exe') if os.name == 'nt' else None
    if not powershell:
        pytest.skip('Windows PowerShell is required for the isolated Task Scheduler mock')
    script = (Path(__file__).resolve().parents[1] / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    get_task = script[script.index('function Get-MainComputerSLHBootTask('):script.index('function Get-MainComputerSLHListenerPid')]
    restart = script[script.index('function Restart-MainComputerSLHBootTask('):script.index('function Start-MainComputer(')]
    # The restart function uses the genuine helper, with all external calls mocked.
    get_listener = script[script.index('function Get-MainComputerSLHListenerPid'):script.index('function Restart-MainComputerSLHBootTask(')]
    root = str(tmp_path)
    (tmp_path / 'python.exe').write_bytes(b'placeholder executable - never launched')

    def quote(s):
        return "'" + s.replace("'", "''") + "'"

    body = f'''
$ErrorActionPreference = 'Stop'
$script:listenerPid = 101
$script:stops = 0
$script:starts = 0
$script:taskArgs = {quote('-NoProfile -NonInteractive -File "' + str(tmp_path / 'scripts' / 'slh-local-service-boot.ps1') + '" -Root "' + root + '" -Python "' + str(tmp_path / 'python.exe') + '"')}
function Get-ScheduledTask {{
  param($TaskName, $ErrorAction)
  return [pscustomobject]@{{ State = 'Running'; Actions = @([pscustomobject]@{{ Execute = 'powershell.exe'; Arguments = $script:taskArgs }}) }}
}}
function Get-NetstatListenRows {{
  param([int[]]$Ports)
  if ($script:listenerPid -ne 0) {{ return [pscustomobject]@{{ OwningProcess = $script:listenerPid; LocalPort = 8897 }} }}
}}
function Stop-ScheduledTask {{
  param($TaskName, $ErrorAction)
  $script:stops += 1
  $script:listenerPid = 0
}}
function Start-ScheduledTask {{
  param($TaskName, $ErrorAction)
  $script:starts += 1
  $script:listenerPid = 202
}}
function Invoke-RestMethod {{
  param($Method, $Uri, $TimeoutSec, $ErrorAction)
  return [pscustomobject]@{{ schema='main-computer.slh-local-status.v1'; local_service=[pscustomobject]@{{ binding='loopback' }} }}
}}
{get_task}
{get_listener}
{restart}
$task = Get-MainComputerSLHBootTask {quote(root)}
$result = Restart-MainComputerSLHBootTask {quote(root)} $task
if ($result.state -ne 'boot-task-restarted' -or $result.old_pid -ne 101 -or $result.new_pid -ne 202) {{ throw 'Wrong restart receipt' }}
if ($script:stops -ne 1 -or $script:starts -ne 1) {{ throw 'Wrong task action count' }}
# Wrong installation MUST refuse before calling Stop-ScheduledTask.
try {{
  [void](Get-MainComputerSLHBootTask {quote(str(tmp_path / 'different'))})
  throw 'Different installation was accepted'
}} catch {{
  if ($_.Exception.Message -notlike '*different installation*') {{ throw }}
}}
if ($script:stops -ne 1) {{ throw 'An unowned task was stopped' }}
Write-Output 'MOCKED_SLH_RESTART_PASS'
'''
    result = subprocess.run(
        [powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', body],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    assert 'MOCKED_SLH_RESTART_PASS' in result.stdout
