from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "main-computer-start-stop.ps1"


def read_helper() -> str:
    return HELPER.read_text(encoding="utf-8")


def extract_function(text: str, name: str) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\b.*?(?=\nfunction\s+|\Z)", text, re.S)
    assert match is not None, name
    return match.group(0)


def test_mode_scoped_port_kill_list_uses_launch_context_only() -> None:
    helper = read_helper()
    function = extract_function(helper, "Get-MainComputerManagedPorts")

    assert "Only inspect ports assigned to the launch context" in function
    assert 'MAIN_COMPUTER_CONTROL_PORT" "8765"' in function
    assert 'MAIN_COMPUTER_HEARTBEAT_PORT" "8766"' in function
    assert 'MAIN_COMPUTER_MAIN_LOG_PORT" "8767"' in function
    assert 'MAIN_COMPUTER_DOCKER_VIEWPORT_PORT" ""' in function

    # These ports still exist as mode defaults elsewhere, but this function must
    # not sweep every mode's ports while starting/stopping a single mode.
    assert '"28865"' not in function
    assert '"28866"' not in function
    assert '"38865"' not in function
    assert '"38866"' not in function
    assert '"18765"' not in function


def test_current_service_scan_requires_this_tree_before_force_kill() -> None:
    helper = read_helper()
    function = extract_function(helper, "Add-CurrentMainComputerProcessCandidates")

    assert "(Get-NetstatListenRows -Ports (Get-MainComputerManagedPorts $LaunchContext))" in function

    # The broad process scan must be root-owned so Safe/Debug starts do not kill
    # an Unleashed/dev server just because it has main_computer in the command line.
    assert "(Test-MainComputerServiceCommandLine $commandLine $RootPath) -and (Test-OwnedMainComputerPid" in function
    assert "live Main Computer service command line for this tree" in function

    # Port-listener cleanup remains allowed, but only for the scoped mode ports
    # returned by Get-MainComputerManagedPorts.
    assert '"current-main-computer-port-listener"' in function


def test_start_stop_manages_main_log_service_as_first_class_child() -> None:
    helper = read_helper()

    assert 'MAIN_COMPUTER_MAIN_LOG_HOST = "127.0.0.1"' in helper
    assert 'MAIN_COMPUTER_MAIN_LOG_PORT = $mainLogPort' in helper
    assert 'MAIN_COMPUTER_MAIN_LOG_URL = "http://127.0.0.1:$mainLogPort"' in helper
    assert '".main_computer_main_log_service.pid"' in helper
    assert '"runtime\\main_log\\state.json"' in helper
    assert 'name = "main-log"' in helper
    assert 'module = "main_computer.main_log_service serve"' in helper
    assert 'health_url = Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_MAIN_LOG_URL" "http://127.0.0.1:8767"' in helper
    assert '"main_computer.main_log_service"' in helper
    assert 'Write-Host ("Main Log state:   " + (Join-Path $RootPath "runtime\\main_log\\state.json"))' in helper
    assert 'Write-Host ("Main Log PID:     " + (Join-Path $RootPath ".main_computer_main_log_service.pid"))' in helper
