"""Regression contract for live diagnostics on unverified SLH process IDs.

The PowerShell source is executed on Windows; this offline contract checks that
both force policies can expose the same identity inputs and stop-report record.
"""
from pathlib import Path

PS = Path(__file__).resolve().parents[1] / "scripts" / "main-computer-start-stop.ps1"


def _ps():
    return PS.read_text(encoding="utf-8-sig")


def test_gateway_verifier_and_diagnostics_share_exact_same_predicate():
    ps = _ps()
    assert 'function Get-MainComputerSLHGatewayIdentityFacts' in ps
    assert 'return [bool](Get-MainComputerSLHGatewayIdentityFacts $CommandLine $RootPath).owned' in ps
    for cause in ('command-line-unavailable', 'gateway-module-missing',
                  'root-argument-missing', 'checkout-root-mismatch',
                  'root-normalization-failed'):
        assert cause in ps
    assert 'reason = $gatewayFacts.reason' in ps


def test_sl_harness_shares_discovery_identity_and_rechecks_creation_time():
    ps = _ps()
    assert 'Add-PidCandidate $Candidates $process.ProcessId "slh" "verified live gateway for this checkout" 20 $false $commandLine $process.CreationDate' in ps
    assert 'same CIM creation date verified, using discovery command line' in ps
    assert 'Test-MainComputerOwnedSLHGateway $cachedCommand $RootPath' in ps
    assert '([datetime]$cachedCreated).ToUniversalTime()' in ps


def test_unverified_pid_prints_live_vs_expected_identity_before_kill():
    ps = _ps()
    begin = ps.index('function Force-StopUnverifiedPid(')
    ending = ps.index('function Stop-OnePid(', begin)
    force = ps[begin:ending]
    assert force.index('Get-UnverifiedPidDiagnostic') < force.index('Invoke-ProcessKill')
    assert force.index('Write-UnverifiedPidDiagnostic') < force.index('Invoke-ProcessKill')
    for field in ('previous_command_line', 'discovery_command_line',
                  'discovery_creation_date', 'live_command_line',
                  'live_executable', 'live_name', 'live_creation_date',
                  'live_parent_pid', 'live_cim_state', 'expected_root',
                  'live_matches_discovery_creation', 'gateway_identity'):
        assert field in ps
    assert 'identity_diagnostic = $identityDiagnostic' in force
    assert 'Write-Host ("  gateway decision:' in ps
    assert 'Write-Host ("  source:' in ps


def test_all_force_entry_points_pass_checkout_to_diagnostics():
    ps = _ps()
    expected = (
        'Force-StopUnverifiedPid $candidate $stopResult $RootPath',
        'Force-StopUnverifiedPid $slhCandidate $slhChildStop $RootPath',
        'Force-StopUnverifiedPid $listener ([ordered]@{ state = "listener-occupied"; reason = "slh-listener-still-occupied-after-cleanup" }) $RootPath',
    )
    for call in expected:
        assert call in ps
    assert 'Stop-MainComputer $RootPath $true $true | Out-Null' in ps
    assert 'Stop-MainComputer $resolvedRoot ([bool]$NoDocker) $true | Out-Null' in ps
