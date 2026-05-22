param(
    [string]$VenvRoot = "",
    [string]$RepoRoot = "",
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Continue"

function Test-MainComputerRoot {
    param([AllowEmptyString()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if (-not (Test-Path -LiteralPath $expanded -PathType Container)) {
        return $false
    }

    $markers = @(
        "pyproject.toml",
        "bootstrap-main-computer-windows.ps1",
        "main_computer",
        "runtime\main-computer-install.json",
        "main-computer-install.json",
        "run-main-computer.ps1"
    )

    foreach ($marker in $markers) {
        if (Test-Path -LiteralPath (Join-Path $expanded $marker)) {
            return $true
        }
    }

    return $false
}

function Resolve-MainComputerRoot {
    param([AllowEmptyString()][string]$Path)

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($Path)) { $candidates.Add($Path) }
    if (-not [string]::IsNullOrWhiteSpace($env:MAIN_COMPUTER_ROOT)) { $candidates.Add($env:MAIN_COMPUTER_ROOT) }
    if (-not [string]::IsNullOrWhiteSpace($env:MC_INSTALL)) { $candidates.Add($env:MC_INSTALL) }
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $candidates.Add($PSScriptRoot)
        $parent = Split-Path -Parent $PSScriptRoot
        if (-not [string]::IsNullOrWhiteSpace($parent)) { $candidates.Add($parent) }
    }
    $candidates.Add((Get-Location).Path)

    foreach ($candidate in $candidates) {
        if (Test-MainComputerRoot -Path $candidate) {
            return (Resolve-Path -LiteralPath ([Environment]::ExpandEnvironmentVariables($candidate))).Path
        }
    }

    return (Get-Location).Path
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

function Resolve-VenvRoot {
    param(
        [AllowEmptyString()][string]$Path,
        [AllowEmptyString()][string]$Root
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($Path)) { $candidates.Add($Path) }
    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) { $candidates.Add($env:VIRTUAL_ENV) }
    if (-not [string]::IsNullOrWhiteSpace($Root)) {
        $candidates.Add((Join-Path $Root ".venv"))
        $candidates.Add((Join-Path $Root "venv"))
    }

    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $managedPatterns = @(
            (Join-Path $env:USERPROFILE ".main-computer-*\instances\*\*\venv"),
            (Join-Path $env:USERPROFILE ".main-computer-tools\instances\*\*\venv")
        )
        foreach ($pattern in $managedPatterns) {
            Resolve-Path -Path $pattern -ErrorAction SilentlyContinue | ForEach-Object {
                $candidates.Add($_.Path)
            }
        }
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
        $key = $expanded.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-VenvRoot -Path $expanded) {
            return (Resolve-Path -LiteralPath $expanded).Path
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        return [Environment]::ExpandEnvironmentVariables($Path)
    }
    if (-not [string]::IsNullOrWhiteSpace($Root)) {
        return (Join-Path $Root ".venv")
    }
    return (Join-Path (Get-Location).Path ".venv")
}

$RepoRoot = Resolve-MainComputerRoot -Path $RepoRoot
$VenvRoot = Resolve-VenvRoot -Path $VenvRoot -Root $RepoRoot

function Q([string]$s) {
    if ($null -eq $s) { return '""' }
    return '"' + ($s -replace '"', '\"') + '"'
}

function Run-Limited {
    param(
        [Parameter(Mandatory=$true)][string]$Exe,
        [string[]]$ArgList = @(),
        [string]$WorkingDirectory = "",
        [int]$Timeout = 120,
        [string]$Label = ""
    )

    Write-Host ""
    Write-Host "================================================================"
    Write-Host $Label
    Write-Host "EXE: $Exe"
    Write-Host "ARGS: $($ArgList -join ' ')"
    Write-Host "CWD: $WorkingDirectory"
    Write-Host "TIMEOUT: ${Timeout}s"

    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
        Write-Host "RESULT: MISSING EXE"
        return [pscustomobject]@{ Label=$Label; Ok=$false; TimedOut=$false; ExitCode=$null; Seconds=0; OutFile=$null; ErrFile=$null }
    }

    if ([string]::IsNullOrWhiteSpace($WorkingDirectory) -or -not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        $WorkingDirectory = (Get-Location).Path
    }

    $stamp = [guid]::NewGuid().ToString("N")
    $outFile = Join-Path $env:TEMP "mc-install-probe-$stamp.out.txt"
    $errFile = Join-Path $env:TEMP "mc-install-probe-$stamp.err.txt"
    $argLine = ($ArgList | ForEach-Object { Q $_ }) -join " "
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        $p = Start-Process `
            -FilePath $Exe `
            -ArgumentList $argLine `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $outFile `
            -RedirectStandardError $errFile `
            -PassThru

        if (-not $p.WaitForExit($Timeout * 1000)) {
            $sw.Stop()
            Write-Host "RESULT: TIMEOUT after $([math]::Round($sw.Elapsed.TotalSeconds, 1))s"
            try {
                & "$env:WINDIR\System32\taskkill.exe" /PID $p.Id /T /F 2>&1 | Out-Null
            } catch {
                try { $p.Kill() } catch {}
            }

            Write-Host "STDOUT LOG: $outFile"
            Write-Host "STDERR LOG: $errFile"

            if (Test-Path $outFile) {
                Write-Host "STDOUT TAIL:"
                Get-Content $outFile -Tail 120 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
            }
            if (Test-Path $errFile) {
                Write-Host "STDERR TAIL:"
                Get-Content $errFile -Tail 160 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
            }

            return [pscustomobject]@{ Label=$Label; Ok=$false; TimedOut=$true; ExitCode=$null; Seconds=[math]::Round($sw.Elapsed.TotalSeconds, 1); OutFile=$outFile; ErrFile=$errFile }
        }

        $p.Refresh()
        $sw.Stop()
        Write-Host "RESULT: EXIT $($p.ExitCode) after $([math]::Round($sw.Elapsed.TotalSeconds, 1))s"
        Write-Host "STDOUT LOG: $outFile"
        Write-Host "STDERR LOG: $errFile"

        if (Test-Path $outFile) {
            $stdout = Get-Content $outFile -Raw -ErrorAction SilentlyContinue
            if ($stdout) {
                Write-Host "STDOUT:"
                $stdout.TrimEnd() -split "`r?`n" | Select-Object -First 220 | ForEach-Object { Write-Host "  $_" }
            }
        }

        if (Test-Path $errFile) {
            $stderr = Get-Content $errFile -Raw -ErrorAction SilentlyContinue
            if ($stderr) {
                Write-Host "STDERR:"
                $stderr.TrimEnd() -split "`r?`n" | Select-Object -First 260 | ForEach-Object { Write-Host "  $_" }
            }
        }

        return [pscustomobject]@{ Label=$Label; Ok=($p.ExitCode -eq 0); TimedOut=$false; ExitCode=$p.ExitCode; Seconds=[math]::Round($sw.Elapsed.TotalSeconds, 1); OutFile=$outFile; ErrFile=$errFile }
    } catch {
        $sw.Stop()
        Write-Host "RESULT: START ERROR after $([math]::Round($sw.Elapsed.TotalSeconds, 1))s"
        Write-Host "ERROR: $($_.Exception.GetType().FullName): $($_.Exception.Message)"
        return [pscustomobject]@{ Label=$Label; Ok=$false; TimedOut=$false; ExitCode=$null; Seconds=[math]::Round($sw.Elapsed.TotalSeconds, 1); OutFile=$outFile; ErrFile=$errFile }
    }
}

Write-Host "==== Main Computer dependency install slowness probe ===="
Write-Host "VenvRoot: $VenvRoot"
Write-Host "RepoRoot: $RepoRoot"
Write-Host "TimeoutSeconds: $TimeoutSeconds"

$python = Join-Path $VenvRoot "Scripts\python.exe"
Write-Host "Venv python: $python"
Write-Host "Venv python exists: $(Test-Path -LiteralPath $python -PathType Leaf)"
Write-Host "Repo root exists: $(Test-Path -LiteralPath $RepoRoot -PathType Container)"

Write-Host ""
Write-Host "==== Existing python/pip processes touching this venv ===="
Get-CimInstance Win32_Process |
    Where-Object {
        ($_.CommandLine -match [regex]::Escape($VenvRoot)) -or
        ($_.CommandLine -match "pip") -or
        ($_.CommandLine -match "maincomputer.*debug.*venv")
    } |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine |
    Format-List

Write-Host ""
Write-Host "==== Relevant pip/network env ===="
"HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","PIP_INDEX_URL","PIP_EXTRA_INDEX_URL","PIP_NO_INDEX","PIP_REQUIRE_VIRTUALENV","PIP_CERT","REQUESTS_CA_BUNDLE","SSL_CERT_FILE","PYTHONPATH","PYTHONHOME" | ForEach-Object {
    Write-Host "$_=$([Environment]::GetEnvironmentVariable($_))"
}

$results = @()

$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 20 -Label "python identity" -ArgList @(
    "-c",
    "import sys, os; print(sys.executable); print(sys.version); print('prefix=' + sys.prefix); print('cwd=' + os.getcwd())"
)

$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 20 -Label "pip --version" -ArgList @(
    "-m", "pip", "--version"
)

$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 30 -Label "pip config debug" -ArgList @(
    "-m", "pip", "config", "debug", "-v"
)

Write-Host ""
Write-Host "==== Parse pyproject optional dependencies ===="
$showDepsPy = Join-Path $env:TEMP ("mc-show-deps-" + [guid]::NewGuid().ToString("N") + ".py")
@"
import os, tomllib, json
root = r'''$RepoRoot'''
path = os.path.join(root, 'pyproject.toml')
print('pyproject:', path)
data = tomllib.load(open(path, 'rb'))
proj = data.get('project', {})
print('project dependencies:')
for dep in proj.get('dependencies', []) or []:
    print('  ' + dep)
print('optional dependencies:')
for name, deps in (proj.get('optional-dependencies', {}) or {}).items():
    print('  [' + name + ']')
    for dep in deps:
        print('    ' + dep)
"@ | Set-Content -LiteralPath $showDepsPy -Encoding UTF8

$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 20 -Label "show pyproject dependencies" -ArgList @($showDepsPy)

Write-Host ""
Write-Host "==== Split install into stages ===="

$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 45 -Label "STAGE 1: local editable metadata only, no deps" -ArgList @(
    "-m", "pip", "install",
    "--no-input",
    "--disable-pip-version-check",
    "--no-deps",
    "-e", ".[mathics]",
    "-vv"
)

$reportNoDeps = Join-Path $env:TEMP ("mc-pip-report-nodeps-" + [guid]::NewGuid().ToString("N") + ".json")
$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 60 -Label "STAGE 2: dry-run local project only, no deps, report" -ArgList @(
    "-m", "pip", "install",
    "--dry-run",
    "--report", $reportNoDeps,
    "--no-input",
    "--disable-pip-version-check",
    "--no-deps",
    "-e", ".[mathics]",
    "-vv"
)
Write-Host "Report no-deps: $reportNoDeps"

$reportDeps = Join-Path $env:TEMP ("mc-pip-report-deps-" + [guid]::NewGuid().ToString("N") + ".json")
$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout $TimeoutSeconds -Label "STAGE 3: dry-run full resolver for -e .[mathics], no install" -ArgList @(
    "-m", "pip", "install",
    "--dry-run",
    "--report", $reportDeps,
    "--no-input",
    "--disable-pip-version-check",
    "--timeout", "20",
    "--retries", "1",
    "-e", ".[mathics]",
    "-vv"
)
Write-Host "Report deps: $reportDeps"

Write-Host ""
Write-Host "==== Network/index spot checks ===="
$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 45 -Label "INDEX: lookup pip versions" -ArgList @(
    "-m", "pip", "index", "versions", "pip",
    "--disable-pip-version-check",
    "--timeout", "20",
    "--retries", "1",
    "-vv"
)

$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 45 -Label "INDEX: lookup Mathics3 versions" -ArgList @(
    "-m", "pip", "index", "versions", "Mathics3",
    "--disable-pip-version-check",
    "--timeout", "20",
    "--retries", "1",
    "-vv"
)

$results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 45 -Label "INDEX: lookup mathics-scanner versions" -ArgList @(
    "-m", "pip", "index", "versions", "mathics-scanner",
    "--disable-pip-version-check",
    "--timeout", "20",
    "--retries", "1",
    "-vv"
)

Write-Host ""
Write-Host "==== Optional: actual install with timeout ===="
Write-Host "This is closest to bootstrap. It may install packages into the venv."
$runActual = Read-Host "Run actual install -e .[mathics] now? Type YES to run"
if ($runActual -eq "YES") {
    $results += Run-Limited -Exe $python -WorkingDirectory $RepoRoot -Timeout 900 -Label "ACTUAL INSTALL: -e .[mathics]" -ArgList @(
        "-m", "pip", "install",
        "--no-input",
        "--disable-pip-version-check",
        "--timeout", "30",
        "--retries", "2",
        "-e", ".[mathics]",
        "-vv"
    )
} else {
    Write-Host "Skipped actual install."
}

Write-Host ""
Write-Host "==== Summary ===="
$results |
    Select-Object Label, Ok, TimedOut, ExitCode, Seconds, OutFile, ErrFile |
    Format-Table -AutoSize

Write-Host ""
Write-Host "Interpretation:"
Write-Host "- STAGE 1 slow/timeout = local editable metadata/build backend problem."
Write-Host "- STAGE 1 fast but STAGE 3 slow/timeout = dependency resolver or index/network problem."
Write-Host "- INDEX checks slow/timeout = pip network/index/proxy/SSL problem."
Write-Host "- STAGE 3 fast but ACTUAL INSTALL slow = wheel download/build/install problem."
Write-Host ""
Write-Host "Paste the Summary table and the tail of whichever STAGE timed out."
