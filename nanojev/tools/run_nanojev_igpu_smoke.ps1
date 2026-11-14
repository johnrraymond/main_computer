# Intel Iris Xe only: DirectML smoke for NanoJev-sized head proxy. No CUDA tests.
[CmdletBinding()]
param(
    [string] $DmlPython = (Join-Path $HOME '.venv-nanojev-directml-smoke\Scripts\python.exe'),
    [string] $BasePython = '',
    [switch] $InstallDml,
    [int] $IntelDeviceIndex = 1,
    [int] $Steps = 60,
    [int] $Warmup = 12
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'nanojev_igpu_smoke.py'
$projectRoot = Split-Path $PSScriptRoot -Parent
$reportDir = Join-Path $projectRoot 'runtime\reports\nanojev-igpu-smoke'
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null

Write-Host 'Intel graphics adapter:'
Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'Intel|Iris' } |
    Select-Object Name, DriverVersion | Format-Table -AutoSize

if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Benchmark implementation missing: $scriptPath"
}

# Avoid nested quotes in native Python -c commands on Windows PowerShell 5.1.
$probeCode = 'import sys;print(sys.version_info.major,sys.version_info.minor,sys._base_executable,int(sys.prefix!=sys.base_prefix),sep=chr(124))'
function Read-PythonProbe {
    param([string] $Program, [string[]] $PrefixArgs = @())
    try {
        $lines = @(& $Program @PrefixArgs -c $probeCode 2>$null)
        if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) { return $null }
        $parts = ([string]$lines[0]).Trim().Split('|')
        if ($parts.Count -ne 4) { return $null }
        $major = 0
        $minor = 0
        $isVenvInt = 0
        if (-not [int]::TryParse($parts[0], [ref]$major)) { return $null }
        if (-not [int]::TryParse($parts[1], [ref]$minor)) { return $null }
        if (-not [int]::TryParse($parts[3], [ref]$isVenvInt)) { return $null }
        if (-not (Test-Path -LiteralPath $parts[2] -PathType Leaf)) { return $null }
        return [pscustomobject]@{
            Major = $major
            Minor = $minor
            Base = [string]$parts[2]
            IsVenv = ($isVenvInt -eq 1)
        }
    } catch { return $null }
}
function Get-CompatiblePython {
    param([string] $Program, [string[]] $PrefixArgs = @())
    $info = Read-PythonProbe -Program $Program -PrefixArgs $PrefixArgs
    if ($null -eq $info) { return $null }
    if ($info.Major -ne 3 -or $info.Minor -lt 10 -or $info.Minor -gt 12) { return $null }
    return [pscustomobject]@{ Executable = $info.Base; Version = "3.$($info.Minor)" }
}

$dmlRoot = Split-Path (Split-Path $DmlPython -Parent) -Parent
if (-not (Test-Path -LiteralPath $DmlPython -PathType Leaf)) {
    if (-not $InstallDml) {
        throw "DirectML environment missing: $DmlPython. Re-run with -InstallDml."
    }
    $selected = $null
    if (-not [string]::IsNullOrWhiteSpace($BasePython)) {
        $selected = Get-CompatiblePython -Program $BasePython
        if ($null -eq $selected) {
            throw "-BasePython must point to CPython 3.10-3.12: $BasePython"
        }
    } elseif ($null -ne (Get-Command py -ErrorAction SilentlyContinue)) {
        foreach ($version in @('3.11', '3.12', '3.10')) {
            $selected = Get-CompatiblePython -Program 'py' -PrefixArgs @("-$version")
            if ($null -ne $selected) { break }
        }
    }
    if ($null -eq $selected) {
        throw "No compatible Python runtime found. Use -BasePython with your Python 3.11 executable."
    }
    Write-Host "Creating isolated DirectML environment at $dmlRoot using $($selected.Version)"
    & $selected.Executable -m venv $dmlRoot
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $DmlPython -PathType Leaf)) {
        throw 'DirectML virtual environment creation failed.'
    }
}

if ($InstallDml) {
    $venvData = Read-PythonProbe -Program $DmlPython
    if ($null -eq $venvData -or -not $venvData.IsVenv) {
        throw 'Refusing to install DirectML outside a verified isolated virtual environment.'
    }
    if ($venvData.Major -ne 3 -or $venvData.Minor -lt 10 -or $venvData.Minor -gt 12) {
        throw 'DirectML environment must use Python 3.10-3.12.'
    }
    Write-Host 'Installing torch-directml only in the isolated DirectML environment...'
    & $DmlPython -m pip install torch-directml
    if ($LASTEXITCODE -ne 0) { throw 'DirectML package installation failed.' }
}

Write-Host 'Checking torch-directml import...'
& $DmlPython -c 'import torch,torch_directml;print(torch.__version__)'
if ($LASTEXITCODE -ne 0) {
    throw 'torch-directml import failed. Re-run with -InstallDml.'
}

$dmlReport = Join-Path $reportDir 'intel-dml.json'
Write-Host "`nRunning Intel Iris Xe head-proxy smoke only..."
& $DmlPython $scriptPath --backend dml --dml-index $IntelDeviceIndex --batches 1 64 --warmup $Warmup --steps $Steps --report $dmlReport
if ($LASTEXITCODE -ne 0) {
    throw 'Intel DirectML smoke failed; inspect the error above. No NVIDIA computation was scheduled.'
}
Write-Host "Intel report: $dmlReport"
