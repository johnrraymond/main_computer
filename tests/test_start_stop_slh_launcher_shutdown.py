"""Source-level lifecycle contract checks for Windows SLH shutdown.

The full Task Scheduler/process teardown test requires a Windows runner;
these checks guard the ordering and identity constraints on every platform.
"""
from __future__ import annotations

from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8-sig")


def _function(name: str, next_name: str) -> str:
    return SOURCE.split(f"function {name}", 1)[1].split(f"function {next_name}", 1)[0]


def test_stopped_or_absent_windows_task_is_successful_noop() -> None:
    get_task = _function("Get-MainComputerSLHBootTask(", "Get-MainComputerSLHListenerPid")
    assert 'if ($null -eq $task -or [string]$task.State -ne "Running")' in get_task
    assert "return $null" in get_task
    stop_task = _function("Stop-MainComputerSLHBootLauncher(", "Test-MainComputerOwnedSLHGateway")
    assert 'state = "not-running"; owned_listener = $null' in stop_task
    assert "Stop-ScheduledTask" in stop_task
    assert 'if ([string]$state.State -ne "Running")' in stop_task
    assert 'state = "launcher-stopped"' in stop_task


def test_both_slh_launchers_stop_before_killing_children() -> None:
    stop = _function("Stop-MainComputer(", "MISSING_END_OF_FUNCTION_MARKER")
    # Boot task first; supervisor second; all other children and SLH leftovers last.
    boot = stop.index("$slhBootStop = Stop-MainComputerSLHBootLauncher $RootPath")
    supervisor = stop.index('Where-Object { [string]$_.role -eq "supervisor" }')
    verified = stop.index("if ($supervisorFailures.Count -gt 0)")
    others = stop.index('Where-Object { [string]$_.role -ne "supervisor" }')
    orphan = stop.index("$slhChildStop = Stop-MainComputerSLHListenerAfterLaunchers")
    assert boot < supervisor < verified < others < orphan
    assert "refusing to terminate an SLH child while its launcher may still restart it" in stop


def test_boot_task_never_auto_restarts_and_supervisor_owns_new_slhs() -> None:
    start = _function("Start-MainComputer(", "Start-MainComputerDevHubOnly")
    assert "Assert-MainComputerSLHListenerReleased" in start
    assert '"MAIN_COMPUTER_SUPERVISE_SLH"' in start
    assert "Restart-MainComputerSLHBootTask" not in SOURCE
    assert "Start-ScheduledTask" not in SOURCE
    assert "Get-MainComputerSLHBootTask $RootPath" not in start


def test_orphans_require_root_or_verified_task_identity() -> None:
    owner = _function("Test-MainComputerOwnedSLHGateway(", "Test-MainComputerOwnedSLHLocalService")
    assert "--root" in owner
    assert "GetFullPath" in owner
    assert "OrdinalIgnoreCase" in owner
    separate = _function("Test-MainComputerOwnedSLHLocalService(", "Stop-MainComputerSLHListenerAfterLaunchers")
    assert 'Join-Path $RootPath ".venv"' in separate
    assert "main_computer\\.slh\\.local_service" in separate
    child = _function("Stop-MainComputerSLHListenerAfterLaunchers(", "Assert-MainComputerSLHListenerReleased")
    assert 'state = "not-running"' in child
    assert 'state = "not-owned"' in child
    assert "creation_date" in child
    assert "Test-MainComputerOwnedSLHGateway" in child
    assert "Test-MainComputerOwnedSLHLocalService" in child


def test_unverified_or_failed_owned_slh_cannot_disappear_from_shutdown_report() -> None:
    stop = _function("Stop-MainComputer(", "MISSING_END_OF_FUNCTION_MARKER")
    assert "$slhFailedResults" in stop
    assert "slh_listener_stop = $slhChildStop" in stop
    assert "slh_boot_launcher_stop = $slhBootStop" in stop
    assert "if (-not $slhFailure)" in stop
    assert "Verified SLH process did not terminate" in stop
    assert "Remove-ManagedRuntimeFiles" in stop


def test_supervisor_is_killed_without_killing_its_children_first() -> None:
    kill = _function("Invoke-ProcessKill(", "Stop-OnePid")
    assert 'if ($StopTree) { $killArgs += "/T" }' in kill
    stop_one = _function("Stop-OnePid(", "Remove-ManagedRuntimeFiles")
    assert 'Invoke-ProcessKill $processId ($role -ne "supervisor")' in stop_one
    stop = _function("Stop-MainComputer(", "MISSING_END_OF_FUNCTION_MARKER")
    assert 'Sort-Object -Property' in stop  # child stop order, including Main Log last


def test_org_pid_cleanup_follows_parent_shutdown_and_blocks_clean_report_on_failure() -> None:
    stop = _function("Stop-MainComputer(", "MISSING_END_OF_FUNCTION_MARKER")
    boot = stop.index("$slhBootStop = Stop-MainComputerSLHBootLauncher $RootPath")
    supervisor = stop.index('Where-Object { [string]$_.role -eq "supervisor" }')
    child = stop.index("$slhChildStop = Stop-MainComputerSLHListenerAfterLaunchers")
    org = stop.index("$orgChildStop = Stop-MainComputerORGService $RootPath $launchContext")
    cleanup = stop.index("$removedPidFiles = Remove-ManagedRuntimeFiles")
    assert boot < supervisor < child < org < cleanup
    assert '[string]$orgChildStop.state -eq "failed"' in stop
    assert "org_service_stop = $orgChildStop" in stop


def test_supervisor_reuses_cached_live_identity_only_with_matching_creation_time() -> None:
    candidates = _function("Add-CurrentMainComputerProcessCandidates(", "Get-ProcessCommandLine")
    assert '"verified live supervisor bootstrap for this tree" 90 $false $commandLine $process.CreationDate' in candidates
    command_lookup = _function("Get-ProcessCommandLine(", "Test-OwnedMainComputerPid")
    assert '$attempt -lt 3' in command_lookup
    kill = _function("Stop-OnePid(", "Remove-ManagedRuntimeFiles")
    assert '"verified_command_line"' in kill
    assert '"verified_creation_date"' in kill
    assert '$sameProcess' in kill
    assert 'if ([string]::IsNullOrWhiteSpace($commandLine)) { $commandLine = $cachedCommand }' in kill


def test_supervisor_revalidation_uses_cim_creation_date_not_get_process_starttime() -> None:
    # A verified live Win32_Process identity must not be rejected solely because
    # Get-Process.StartTime cannot be obtained or differs from CIM's timestamp.
    stop = _function("Stop-OnePid(", "Remove-ManagedRuntimeFiles")
    verified = stop.split('if ($role -eq "supervisor") {', 1)[1].split('  if (-not $verifiedSupervisor', 1)[0]
    assert 'Get-CimInstance Win32_Process -Filter ("ProcessId = " + $processId)' in verified
    assert '([datetime]$liveIdentity.CreationDate).ToUniversalTime()' in verified
    assert '([datetime]$cachedCreated).ToUniversalTime()' in verified
    assert '(Get-Process -Id $processId -ErrorAction Stop).StartTime' not in verified
    assert 'if ($null -eq $liveIdentity)' in verified
    assert 'if ($null -eq $liveIdentity.CreationDate)' in verified
    assert 'state = "not-running"' in verified
    assert 'if (-not $newIsSupervisor)' in verified
    assert 'state = "skipped-not-owned"' in verified
    assert 'state = "skipped-unverified"' in verified


def test_recycled_supervisor_pid_with_unknown_new_owner_must_fail_closed() -> None:
    stop = _function("Stop-OnePid(", "Remove-ManagedRuntimeFiles")
    recheck = stop.split('if (-not $sameProcess) {', 1)[1].split('if ([string]::IsNullOrWhiteSpace($commandLine))', 1)[0]
    # Missing command line is not positive evidence the replacement PID is foreign.
    assert 'if ([string]::IsNullOrWhiteSpace($newCommand)) {' in recheck
    unknown = recheck.split('if ([string]::IsNullOrWhiteSpace($newCommand)) {', 1)[1].split('}', 1)[0]
    assert 'state = "skipped-unverified"' in unknown
    assert recheck.index('if ([string]::IsNullOrWhiteSpace($newCommand)) {') < recheck.index('if (-not $newIsSupervisor)')
