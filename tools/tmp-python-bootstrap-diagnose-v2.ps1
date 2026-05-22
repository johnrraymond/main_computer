$ErrorActionPreference = "Continue"

function Section($Name) {
    Write-Host ""
    Write-Host "==== $Name ===="
}

function Run-Cmd($Label, $Exe, [string[]]$Args) {
    Write-Host ""
    Write-Host "-- $Label"
    Write-Host "EXE: $Exe"
    Write-Host "ARGS: $($Args -join ' ')"

    if (-not (Test-Path $Exe)) {
        Write-Host "MISSING: $Exe"
        return
    }

    try {
        & $Exe @Args 2>&1 | ForEach-Object { "  $_" }
        Write-Host "EXIT: $LASTEXITCODE"
    } catch {
        Write-Host "ERROR: $($_.Exception.GetType().FullName): $($_.Exception.Message)"
    }
}

function Test-VenvRoot {
    param([AllowEmptyString()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    return (
        (Test-Path -LiteralPath (Join-Path $expanded "pyvenv.cfg") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $expanded "Scripts\python.exe") -PathType Leaf)
    )
}

function Resolve-ActiveVenvRoot {
    $candidates = New-Object System.Collections.Generic.List[string]

    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) {
        $candidates.Add($env:VIRTUAL_ENV)
    }

    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $candidates.Add((Join-Path $PSScriptRoot ".venv"))
        $parent = Split-Path -Parent $PSScriptRoot
        if (-not [string]::IsNullOrWhiteSpace($parent)) {
            $candidates.Add((Join-Path $parent ".venv"))
        }
    }

    $candidates.Add((Join-Path (Get-Location).Path ".venv"))

    foreach ($candidate in $candidates) {
        if (Test-VenvRoot -Path $candidate) {
            return (Resolve-Path -LiteralPath ([Environment]::ExpandEnvironmentVariables($candidate))).Path
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) {
        return [Environment]::ExpandEnvironmentVariables($env:VIRTUAL_ENV)
    }

    return (Join-Path (Get-Location).Path ".venv")
}

function Get-WindowsAppsPythonExecutables {
    if ([string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        return @()
    }

    $windowsApps = Join-Path $env:ProgramFiles "WindowsApps"
    if (-not (Test-Path -LiteralPath $windowsApps -PathType Container)) {
        return @()
    }

    Get-ChildItem -Path (Join-Path $windowsApps "PythonSoftwareFoundation.Python.*_*__*\python.exe") -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName } |
        Sort-Object FullName -Descending |
        ForEach-Object { $_.FullName }
}

Section "Context"
Write-Host "PWD=$PWD"
Write-Host "VIRTUAL_ENV=$env:VIRTUAL_ENV"
Write-Host "PATH first entries:"
$env:PATH.Split(';') | Select-Object -First 12 | ForEach-Object { "  $_" }

Section "Command discovery"
Write-Host "Get-Command python.exe -All:"
Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object { "  $($_.Source)" }

Write-Host "Get-Command py.exe -All:"
Get-Command py.exe -All -ErrorAction SilentlyContinue | ForEach-Object { "  $($_.Source)" }

Section "where.exe discovery"
& "$env:WINDIR\System32\where.exe" python.exe 2>&1 | ForEach-Object { "where python.exe: $_" }
& "$env:WINDIR\System32\where.exe" py.exe 2>&1 | ForEach-Object { "where py.exe: $_" }

Section "Active venv config"
$venvRoot = Resolve-ActiveVenvRoot
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$venvCfg = Join-Path $venvRoot "pyvenv.cfg"

Write-Host "venvPython=$venvPython"
Write-Host "venvPython exists=$(Test-Path $venvPython)"
Write-Host "pyvenv.cfg exists=$(Test-Path $venvCfg)"
if (Test-Path $venvCfg) {
    Get-Content $venvCfg | ForEach-Object { "  $_" }
}

Section "Create Python probe file"
$probePy = Join-Path $PWD "tmp_probe_python_identity.py"
@"
import json
import os
import sys

info = {
    "sys.executable": sys.executable,
    "sys._base_executable": getattr(sys, "_base_executable", ""),
    "sys.base_executable": getattr(sys, "base_executable", ""),
    "sys.prefix": sys.prefix,
    "sys.base_prefix": sys.base_prefix,
    "sys.exec_prefix": sys.exec_prefix,
    "sys.base_exec_prefix": sys.base_exec_prefix,
    "in_venv": sys.prefix != sys.base_prefix,
    "version": sys.version,
}

for key in ("sys._base_executable", "sys.base_executable"):
    value = info.get(key) or ""
    info[key + ".exists"] = bool(value and os.path.exists(value))

base_prefix_python = os.path.join(sys.base_prefix, "python.exe")
info["sys.base_prefix_python"] = base_prefix_python
info["sys.base_prefix_python.exists"] = os.path.exists(base_prefix_python)

print(json.dumps(info, indent=2))
"@ | Set-Content -Path $probePy -Encoding UTF8

Write-Host "probePy=$probePy"

Section "Probe active venv Python"
Run-Cmd "active venv identity" $venvPython @($probePy)

Section "Probe HKCU registry Python installs"
$registryRoots = @(
    "HKCU:\Software\Python\PythonCore",
    "HKLM:\Software\Python\PythonCore",
    "HKLM:\Software\WOW6432Node\Python\PythonCore"
)

$registryPythons = New-Object System.Collections.Generic.List[string]

foreach ($root in $registryRoots) {
    Write-Host ""
    Write-Host "Registry root: $root"

    if (-not (Test-Path $root)) {
        Write-Host "  missing"
        continue
    }

    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
        $installPathKey = Join-Path $_.PsPath "InstallPath"
        if (-not (Test-Path $installPathKey)) {
            return
        }

        $item = Get-Item $installPathKey -ErrorAction SilentlyContinue
        $installDir = $item.GetValue("")
        Write-Host "  version=$($_.PSChildName)"
        Write-Host "  installDir=$installDir"

        if ($installDir) {
            $exe = Join-Path $installDir "python.exe"
            Write-Host "  pythonExe=$exe"
            Write-Host "  exists=$(Test-Path $exe)"
            if (Test-Path $exe) {
                $registryPythons.Add($exe)
            }
        }
    }
}

$registryPythons = $registryPythons | Sort-Object -Unique

foreach ($exe in $registryPythons) {
    Run-Cmd "registry python identity" $exe @($probePy)
}

Section "Direct test of discovered WindowsApps Python executables"
$windowsAppsPythons = @(Get-WindowsAppsPythonExecutables)
if (-not $windowsAppsPythons) {
    Write-Host "No WindowsApps Python executables discovered."
} else {
    foreach ($exe in $windowsAppsPythons) {
        Run-Cmd "WindowsApps python identity" $exe @($probePy)
    }
}

Section "Can registry/WindowsApps Python create a venv?"
$testRoot = Join-Path $env:TEMP ("mc-bootstrap-venv-test-" + [guid]::NewGuid().ToString("N"))
Write-Host "testRoot=$testRoot"

$venvMaker = $null
foreach ($exe in @($windowsAppsPythons) + @($registryPythons)) {
    if ($exe -and (Test-Path $exe)) {
        $venvMaker = $exe
        break
    }
}

if (-not $venvMaker) {
    Write-Host "No registry or WindowsApps Python executable found to test venv creation."
} else {
    Run-Cmd "venv module available" $venvMaker @("-c", "import sys, venv; print(sys.executable); print('venv ok')")
    Run-Cmd "create temporary venv" $venvMaker @("-m", "venv", $testRoot)

    $testPython = Join-Path $testRoot "Scripts\python.exe"
    Write-Host "created test python=$testPython"
    Write-Host "created test python exists=$(Test-Path $testPython)"

    if (Test-Path $testPython) {
        Run-Cmd "created venv identity" $testPython @($probePy)
    }

    if (Test-Path $testRoot) {
        Remove-Item -Recurse -Force $testRoot -ErrorAction SilentlyContinue
        Write-Host "removed testRoot=$testRoot"
    }
}

Section "Summary"
Write-Host "Expected verification outcomes:"
Write-Host "1. If the WindowsApps/registry python identity works and can create a temp venv, the bootstrapper should use that registry Python."
Write-Host "2. If WindowsApps python exists but cannot run or cannot create a venv, then pass a non-Store Python with -PythonCommand or install python.org Python."
Write-Host "3. If active venv sys._base_executable does not exist, the earlier base-from-venv fallback cannot work on this machine."
