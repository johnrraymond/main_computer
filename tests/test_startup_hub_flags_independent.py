"""P1/P2 contract: two independent, opt-out, local service lifecycles."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _start_body() -> str:
    source = (ROOT / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    return source[source.index('function Start-MainComputer('):source.index('function Start-MainComputerDevHubOnly')]


def test_slh_and_dev_hub_controls_are_disjoint():
    body = _start_body()
    slh = body[body.index('# SLH and Dev-Test Hub'):body.index('  if ($NanoJevManagedRequested)')]
    dev = body[body.index('  $devHubStart = [ordered]@{'):]

    assert '$NoSLHHubRequested' in slh
    assert '$NoDevHubRequested' not in slh
    assert '$NoDevHubRequested' in dev
    assert '$NoSLHHubRequested' not in dev
    assert 'Set-MainComputerLaunchEnvironmentValue $launchContext "MAIN_COMPUTER_SUPERVISE_SLH" $(if ($NoSLHHubRequested) { "0" } else { "1" })' in slh
    assert 'if (-not $NoDevHubRequested) {\n    try {\n      $devHubStart = Start-MainComputerDevHubFresh' in dev
    assert 'if (-not $NoSLHHubRequested) {\n    $slhBootRestart = Restart-MainComputerSLHBootTask' in slh
    assert 'if (-not $NoSLHHubRequested) {\n    # Check ownership BEFORE' in slh


def test_separate_startup_policy_recorded_without_claiming_listener_ready():
    source = (ROOT / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    body = _start_body()
    assert '[switch]$NoDevHub' in source
    assert '[switch]$NoSLHHub' in source
    assert 'Start-MainComputer $resolvedRoot $StartedBy ([bool]$NoDevHub) ([bool]$NoSLHHub)' in source
    assert 'dev_hub = $DevHubStart' in source
    assert 'slh = $SLHStart' in source
    assert 'SLH supervision requested; check supervisor state for listener readiness.' in body
    assert 'SLH startup disabled by --no-slh-hub; separately owned SLH services are untouched.' in body
    assert 'Get-MainComputerSLHBootTask $RootPath' in body
    assert 'Register-ScheduledTask' not in body
    assert 'New-ScheduledTaskTrigger' not in body


def test_global_viewport_stop_cannot_adopt_independent_slh():
    script = (ROOT / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    ports = script[script.index('function Get-MainComputerManagedPorts'):script.index('function Test-MainComputerServiceCommandLine')]
    scanner = script[script.index('function Add-CurrentMainComputerProcessCandidates'):script.index('function Get-ProcessCommandLine')]
    assert 'MAIN_COMPUTER_SLH_PORT' not in ports
    assert 'function Test-MainComputerSeparatelyOwnedSLH' in script
    assert 'main_computer.slh.gateway_service' in script
    assert scanner.count('Test-MainComputerSeparatelyOwnedSLH $commandLine') == 2
    assert 'Add-PidsFromSupervisorState $candidates' in script
    assert 'Add-PidCandidate $Candidates $child.pid $name' in script


def test_batch_switches_are_composed_independently():
    source = (ROOT / 'start_v2.bat').read_text(encoding='utf-8')
    assert 'set "MC_NO_DEV_HUB=0"' in source
    assert 'set "MC_NO_SLH_HUB=0"' in source
    assert 'if "%MC_NO_DEV_HUB%"=="1" set "MC_HUB_START_ARGS=%MC_HUB_START_ARGS% -NoDevHub"' in source
    assert 'if "%MC_NO_SLH_HUB%"=="1" set "MC_HUB_START_ARGS=%MC_HUB_START_ARGS% -NoSLHHub"' in source
    assert 'powershell -NoProfile -ExecutionPolicy Bypass -File "%MC_START_STOP%" -Action start -Root "%MC_ROOT%" -StartedBy "start_v2.bat" %MC_HUB_START_ARGS% %MC_NANOJEV_ARGS%' in source


@pytest.mark.skipif(os.name != 'nt', reason='Runs isolated cmd.exe with a stubbed PowerShell on Windows')
@pytest.mark.parametrize('flags, dev_disabled, slh_disabled', [
    ([], False, False),
    (['--no-dev-hub'], True, False),
    (['--no-slh-hub'], False, True),
    (['--no-dev-hub', '--no-slh-hub'], True, True),
])
def test_windows_start_bat_flags_reach_powershell_independently(tmp_path, flags, dev_disabled, slh_disabled):
    """Exercise real Windows cmd parsing; never start actual Main Computer services."""
    shutil.copy2(ROOT / 'start.bat', tmp_path / 'start.bat')
    shutil.copy2(ROOT / 'start_v2.bat', tmp_path / 'start_v2.bat')
    (tmp_path / 'scripts').mkdir()
    # Run the real PowerShell executable against a harmless stub *script*.
    # A powershell.cmd replacement would terminate the calling .bat unless
    # invoked with CALL, hiding the launcher's second (status) invocation.
    (tmp_path / 'scripts/main-computer-start-stop.ps1').write_text(r'''param(
  [string]$Action,
  [string]$Root,
  [string]$StartedBy,
  [switch]$NoDevHub,
  [switch]$NoSLHHub,
  [switch]$NanoJevManaged,
  [int]$NanoJevIdleSeconds
)
if ($Action -notin @('start', 'status')) { exit 3 }
$record = '{0}|{1}|{2}' -f $Action, $NoDevHub.IsPresent, $NoSLHHub.IsPresent
Add-Content -LiteralPath $env:MC_CAPTURE_ARGS -Value $record
exit 0
''', encoding='utf-8')
    capture = tmp_path / 'calls.txt'
    env = {**os.environ, 'MC_CAPTURE_ARGS': str(capture)}
    completed = subprocess.run(['cmd.exe', '/d', '/c', 'start.bat', *flags], cwd=tmp_path,
                               env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    calls = capture.read_text(encoding='utf-8').splitlines()
    assert calls == [
        f'start|{dev_disabled}|{slh_disabled}',
        'status|False|False',
    ]
