<#
.SYNOPSIS
  Verify Pinky Promise patches P0, P1, P2 without asserting lab acceptance.
.EXAMPLE
  pwsh -File .\tools\slh_seh_lab\verify_pinky_p0_p1_p2.ps1
.EXAMPLE
  pwsh -File .\tools\slh_seh_lab\verify_pinky_p0_p1_p2.ps1 -LabSmoke
#>
[CmdletBinding()]
param(
    [switch]$LabSmoke,
    [switch]$LabFull,
    [switch]$TransportRegression
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $root
try {
    # Tests must not require unrelated game projects or generated manifests.
    $groups = @(
        @{Name='P0 laboratory exit'; Tests=@('tests/test_slh_lab_exit_acceptance.py')},
        @{Name='P1 wallet and marketplace'; Tests=@('tests/test_slh_organization_wallet.py','tests/test_slh_marketplace_automatic.py','tests/test_slh_scheduler_policy.py')},
        @{Name='P2 authenticated private work'; Tests=@('tests/test_slh_private_requests.py','tests/test_slh_private_execution_integration.py')},
        @{Name='Operational-service regression'; Tests=@('tests/test_slh_operational_service.py')}
    )
    foreach ($group in $groups) {
        Write-Host ("`n=== " + $group.Name + ' ===')
        & python -m pytest -q @($group.Tests)
        if ($LASTEXITCODE -ne 0) { throw ("Tests failed: " + $group.Name) }
    }
    if ($TransportRegression) {
        Write-Host "`n=== Extended managed gateway network regression ==="
        & python -m pytest -q tests/test_slh_managed_gateway.py
        if ($LASTEXITCODE -ne 0) { throw 'Managed gateway regression failed' }
    }
    if ($LabSmoke -or $LabFull) {
        $profile = if ($LabFull) {'full'} else {'smoke'}
        Write-Host ("`n=== Existing SLH/SEH laboratory exit: " + $profile + ' ===')
        # This is intentionally NOT_ACCEPTED until P3-P6 and older resilience
        # blockers are completed. Do not count nonzero lab exit as acceptance.
        $output = & python -m tools.slh_seh_lab.run_lab lab-exit --exit-profile $profile --json
        $code = $LASTEXITCODE
        $report = ($output -join "`n") | ConvertFrom-Json
        if ($report.status -ne 'NOT_ACCEPTED' -or $report.accepted -ne $false) {
            throw 'Unexpected acceptance status or invalid lab report'
        }
        Write-Host ('Lab report status: ' + $report.status)
        Write-Host ('Lab report: ' + $report.report_path)
        Write-Host ('Runner exit code (expected nonzero for NOT_ACCEPTED): ' + $code)
        if ($profile -eq 'full' -and -not $report.required_gaps.pinky_promise_economic_cycle) {
            throw 'P0 sixth mandatory economic gate missing from full lab report'
        }
    }
    Write-Host "`nP0 + P1 + P2 focused test suites PASS. Full Pinky Promise economic acceptance is not yet implemented."
}
finally {
    Pop-Location
}
