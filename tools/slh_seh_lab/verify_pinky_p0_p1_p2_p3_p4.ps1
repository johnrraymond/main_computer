# Focused verification for P0..P4. No unrelated game-manifest manipulation.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $root
try {
    & (Join-Path $PSScriptRoot 'verify_pinky_p0_p1_p2_p3.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'P0-P3 regression harness failed' }
    Write-Host "`n=== P4 periodic aggregate Reserve sweep ==="
    python -m pytest -q tests/test_slh_pinky_periodic_sweep.py
    if ($LASTEXITCODE -ne 0) { throw 'P4 periodic sweep tests failed' }
    Write-Host "`n=== Stable Hub atomic ledger ==="
    python -m pytest -q tests/test_stable_hub_fdb_atomic_accounting.py
    if ($LASTEXITCODE -ne 0) { throw 'Stable Hub atomic ledger tests failed' }
    Write-Host 'P0-P4 focused suites PASS. Full laboratory-exit acceptance remains blocked.'
} finally {
    Pop-Location
}
