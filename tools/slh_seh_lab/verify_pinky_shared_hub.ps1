# Explicit real three-Hub/FDB Pinky Promise integrated acceptance helper.
param(
    [switch]$RealHubs,
    [switch]$UseExistingHubs,
    [string]$ReserveWallet = ''
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $root
try {
    Write-Host "=== P0-P4 regression harness ==="
    & (Join-Path $PSScriptRoot 'verify_pinky_p0_p1_p2_p3_p4.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'P0-P4 regression failed' }
    Write-Host "=== Shared-Hub Pinky accounting regression ==="
    python -m pytest -q tests/test_slh_pinky_shared_hub_lab.py tests/test_stable_hub_fdb_atomic_accounting.py
    if ($LASTEXITCODE -ne 0) { throw 'Pinky shared-Hub accounting regression failed' }
    if ($RealHubs) {
        Write-Host "=== REAL three-Hub FoundationDB Pinky Promise economic cycle ==="
        $arguments = @('-m','tools.slh_seh_lab.run_lab','run','--pinky-integrated','--json')
        if ($UseExistingHubs) {
            if ([string]::IsNullOrWhiteSpace($ReserveWallet)) {
                throw 'Existing Hubs require -ReserveWallet set to the already configured smoke Reserve address'
            }
            $arguments += @('--use-existing-hubs','--pinky-reserve-wallet',$ReserveWallet)
        } elseif (-not [string]::IsNullOrWhiteSpace($ReserveWallet)) {
            throw 'Do not set -ReserveWallet for newly launched disposable smoke Hubs'
        }
        & python @arguments
        if ($LASTEXITCODE -ne 0) { throw 'REAL smoke/FDB Pinky integrated acceptance failed' }
        Write-Host 'Real three-Hub Pinky economic cycle PASS. Other full lab exit blockers remain.'
    } else {
        Write-Host 'Unit/regression tests PASS. REAL HUB ECONOMIC CYCLE NOT RUN (use -RealHubs).'
    }
} finally {
    Pop-Location
}
