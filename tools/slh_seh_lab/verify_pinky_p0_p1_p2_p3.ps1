<#
.SYNOPSIS
  Run all P0-P3 focused suites. No game assets or fixture setup required.
.EXAMPLE
  .\tools\slh_seh_lab\verify_pinky_p0_p1_p2_p3.ps1
.EXAMPLE
  .\tools\slh_seh_lab\verify_pinky_p0_p1_p2_p3.ps1 -TransportRegression
#>
[CmdletBinding()]
param(
    [switch]$TransportRegression,
    [switch]$LabSmoke,
    [switch]$LabFull
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $root
try {
    $options = @()
    if ($TransportRegression) { $options += '-TransportRegression' }
    if ($LabSmoke) { $options += '-LabSmoke' }
    if ($LabFull) { $options += '-LabFull' }
    & (Join-Path $PSScriptRoot 'verify_pinky_p0_p1_p2.ps1') @options
    Write-Host "`n=== P3 fixed 0.1-credit Pinky Promise assessment ==="
    & python -m pytest -q tests/test_slh_pinky_obligations.py
    if ($LASTEXITCODE -ne 0) { throw 'P3 fixed-price assessment tests failed' }
    Write-Host "`nP0 + P1 + P2 + P3 focused suites PASS. P4 Reserve settlement and full lab acceptance remain outstanding."
}
finally {
    Pop-Location
}
