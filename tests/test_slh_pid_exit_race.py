"""Regression checks for stale SLH gateway PID after supervisor teardown.

The source is deliberately tested without contacting a live Windows service.
"""
from pathlib import Path

PS = (Path(__file__).resolve().parents[1] / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8-sig")


def _section(begin, end):
    return PS.split(begin, 1)[1].split(end, 1)[0]


def test_gateway_rechecks_liveness_after_identity_retries():
    stop = _section("function Stop-OnePid(", "function Remove-ManagedRuntimeFiles(")
    gateway = stop.split('if ($role -in @("slh", "slh-supervised-orphan")) {', 1)[1].split('if ($role -eq "slh-local-orphan"', 1)[0]
    assert gateway.index("$commandLine = $cachedCommand") < gateway.index("Test-ProcessStillRunning $processId")
    assert gateway.index("Test-ProcessStillRunning $processId") < gateway.index("Get-MainComputerSLHGatewayIdentityFacts $commandLine $RootPath")
    assert 'state = "not-running"; reason = "exited-during-identity-recheck"' in gateway


def test_forced_stop_does_not_attempt_to_kill_an_exited_pid():
    force = _section("function Force-StopUnverifiedPid(", "function Stop-OnePid(")
    assert force.index("Get-UnverifiedPidDiagnostic") < force.index("Test-ProcessStillRunning $processId")
    assert force.index("Test-ProcessStillRunning $processId") < force.index("Write-UnverifiedPidDiagnostic")
    assert force.index("Test-ProcessStillRunning $processId") < force.index("Invoke-ProcessKill")
    assert 'state = "not-running"; reason = "exited-before-forced-termination"' in force
    assert 'Write-Warning ("FORCED STOP: killing unverified PID' in force


def test_diagnostics_separate_discovery_evidence_from_live_evidence():
    diag = _section("function Get-UnverifiedPidDiagnostic(", "function Force-StopUnverifiedPid(")
    assert "discovery_gateway_identity" in diag
    assert "live_process_present" in diag
    assert 'Get-MainComputerSLHGatewayIdentityFacts $diag.discovery_command_line $RootPath' in diag
    assert 'Get-MainComputerSLHGatewayIdentityFacts $diag.live_command_line $RootPath' in diag
    assert 'not-evaluated (live command unavailable)' in diag
    assert 'live PID visible to Get-Process:' in diag
