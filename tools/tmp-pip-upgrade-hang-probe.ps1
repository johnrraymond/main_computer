param(
    [string]$VenvRoot = "",
    [string]$WorkRoot = "",
    [int]$TimeoutSeconds = 60
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

$WorkRoot = Resolve-MainComputerRoot -Path $WorkRoot
$VenvRoot = Resolve-VenvRoot -Path $VenvRoot -Root $WorkRoot

function Q([string]$s) {
    if ($null -eq $s) { return '""' }
    return '"' + ($s -replace '"', '\"') + '"'
}

function Run-Limited {
    param(
        [Parameter(Mandatory=$true)][string]$Exe,
        [string[]]$ArgList = @(),
        [string]$WorkingDirectory = "",
        [int]$Timeout = 60,
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
        return [pscustomobject]@{ Ok=$false; TimedOut=$false; ExitCode=$null; OutFile=$null; ErrFile=$null }
    }

    if ([string]::IsNullOrWhiteSpace($WorkingDirectory) -or -not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        $WorkingDirectory = (Get-Location).Path
    }

    $stamp = [guid]::NewGuid().ToString("N")
    $outFile = Join-Path $env:TEMP "mc-pip-probe-$stamp.out.txt"
    $errFile = Join-Path $env:TEMP "mc-pip-probe-$stamp.err.txt"
    $argLine = ($ArgList | ForEach-Object { Q $_ }) -join " "

    try {
        $p = Start-Process `
            -FilePath $Exe `
            -ArgumentList $argLine `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $outFile `
            -RedirectStandardError $errFile `
            -PassThru

        if (-not $p.WaitForExit($Timeout * 1000)) {
            Write-Host "RESULT: TIMEOUT after ${Timeout}s"
            try {
                & "$env:WINDIR\System32\taskkill.exe" /PID $p.Id /T /F 2>&1 | Out-Null
            } catch {
                try { $p.Kill() } catch {}
            }

            Write-Host "STDOUT LOG: $outFile"
            Write-Host "STDERR LOG: $errFile"

            if (Test-Path $outFile) {
                Write-Host "STDOUT TAIL:"
                Get-Content $outFile -Tail 80 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
            }
            if (Test-Path $errFile) {
                Write-Host "STDERR TAIL:"
                Get-Content $errFile -Tail 120 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
            }

            return [pscustomobject]@{ Ok=$false; TimedOut=$true; ExitCode=$null; OutFile=$outFile; ErrFile=$errFile }
        }

        $p.Refresh()
        Write-Host "RESULT: EXIT $($p.ExitCode)"
        Write-Host "STDOUT LOG: $outFile"
        Write-Host "STDERR LOG: $errFile"

        if (Test-Path $outFile) {
            $stdout = Get-Content $outFile -Raw -ErrorAction SilentlyContinue
            if ($stdout) {
                Write-Host "STDOUT:"
                $stdout.TrimEnd() -split "`r?`n" | Select-Object -First 160 | ForEach-Object { Write-Host "  $_" }
            }
        }

        if (Test-Path $errFile) {
            $stderr = Get-Content $errFile -Raw -ErrorAction SilentlyContinue
            if ($stderr) {
                Write-Host "STDERR:"
                $stderr.TrimEnd() -split "`r?`n" | Select-Object -First 220 | ForEach-Object { Write-Host "  $_" }
            }
        }

        return [pscustomobject]@{ Ok=($p.ExitCode -eq 0); TimedOut=$false; ExitCode=$p.ExitCode; OutFile=$outFile; ErrFile=$errFile }
    } catch {
        Write-Host "RESULT: START ERROR"
        Write-Host "ERROR: $($_.Exception.GetType().FullName): $($_.Exception.Message)"
        return [pscustomobject]@{ Ok=$false; TimedOut=$false; ExitCode=$null; OutFile=$outFile; ErrFile=$errFile }
    }
}

Write-Host "==== Main Computer pip-upgrade hang probe ===="
Write-Host "VenvRoot: $VenvRoot"
Write-Host "WorkRoot: $WorkRoot"
Write-Host "TimeoutSeconds: $TimeoutSeconds"

$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
Write-Host "VenvPython: $VenvPython"
Write-Host "VenvPython exists: $(Test-Path -LiteralPath $VenvPython -PathType Leaf)"
Write-Host "WorkRoot exists: $(Test-Path -LiteralPath $WorkRoot -PathType Container)"

Write-Host ""
Write-Host "==== Relevant environment ===="
"HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","PIP_INDEX_URL","PIP_EXTRA_INDEX_URL","PIP_NO_INDEX","PIP_REQUIRE_VIRTUALENV","PIP_CERT","REQUESTS_CA_BUNDLE","SSL_CERT_FILE","PYTHONPATH","PYTHONHOME" | ForEach-Object {
    Write-Host "$_=$([Environment]::GetEnvironmentVariable($_))"
}

Run-Limited -Exe $VenvPython -ArgList @("-c", "import sys, os; print(sys.executable); print(sys.version); print('prefix=' + sys.prefix); print('base_prefix=' + sys.base_prefix); print('cwd=' + os.getcwd())") -WorkingDirectory $WorkRoot -Timeout 15 -Label "venv python identity" | Out-Null

Run-Limited -Exe $VenvPython -ArgList @("-c", "import pip, sys; print('pip module=' + pip.__file__); print('pip version=' + pip.__version__); print('python=' + sys.executable)") -WorkingDirectory $WorkRoot -Timeout 15 -Label "pip import from venv python" | Out-Null

Run-Limited -Exe $VenvPython -ArgList @("-m", "pip", "--version") -WorkingDirectory $WorkRoot -Timeout 15 -Label "pip --version" | Out-Null

Run-Limited -Exe $VenvPython -ArgList @("-m", "pip", "config", "debug", "-v") -WorkingDirectory $WorkRoot -Timeout 20 -Label "pip config debug" | Out-Null

Run-Limited -Exe $VenvPython -ArgList @("-m", "pip", "debug", "--verbose") -WorkingDirectory $WorkRoot -Timeout 25 -Label "pip debug verbose" | Out-Null

Write-Host ""
Write-Host "==== Upgrade split tests ===="
Write-Host "Test A should be fast. If A hangs, seeded pip itself is broken."
Write-Host "Test B uses the network/index. If A passes and B hangs, the problem is network/index/proxy/version-check."

$offline = Run-Limited `
    -Exe $VenvPython `
    -ArgList @("-m", "pip", "install", "--upgrade", "pip", "--no-index", "--no-input", "--disable-pip-version-check", "-vv") `
    -WorkingDirectory $WorkRoot `
    -Timeout 45 `
    -Label "TEST A: offline pip upgrade/no-index/no-version-check"

$normal = Run-Limited `
    -Exe $VenvPython `
    -ArgList @("-m", "pip", "install", "--upgrade", "pip", "--no-input", "--disable-pip-version-check", "-vv") `
    -WorkingDirectory $WorkRoot `
    -Timeout $TimeoutSeconds `
    -Label "TEST B: normal pip upgrade with default index, no input, no version-check"

$versionCheck = Run-Limited `
    -Exe $VenvPython `
    -ArgList @("-m", "pip", "index", "versions", "pip", "--disable-pip-version-check", "-vv") `
    -WorkingDirectory $WorkRoot `
    -Timeout $TimeoutSeconds `
    -Label "TEST C: pip index lookup only"

Write-Host ""
Write-Host "==== Process snapshot for stuck Python/pip children ===="
Get-CimInstance Win32_Process |
    Where-Object {
        ($_.CommandLine -match [regex]::Escape($VenvRoot)) -or
        ($_.CommandLine -match "pip") -or
        ($_.CommandLine -match "maincomputer\\debug\\venv")
    } |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine |
    Format-List

Write-Host ""
Write-Host "==== Summary hints ===="
if ($offline.TimedOut) {
    Write-Host "OFFLINE RESULT: TIMEOUT. Seeded pip or venv Python is broken before networking."
} elseif ($offline.Ok) {
    Write-Host "OFFLINE RESULT: OK."
} else {
    Write-Host "OFFLINE RESULT: FAILED EXIT $($offline.ExitCode). Check its stderr log."
}

if ($normal.TimedOut) {
    Write-Host "NORMAL UPGRADE RESULT: TIMEOUT. Likely network/index/proxy/SSL wait unless offline also timed out."
} elseif ($normal.Ok) {
    Write-Host "NORMAL UPGRADE RESULT: OK. The bootstrap hang may be later than pip upgrade."
} else {
    Write-Host "NORMAL UPGRADE RESULT: FAILED EXIT $($normal.ExitCode). Check stderr log."
}

if ($versionCheck.TimedOut) {
    Write-Host "INDEX LOOKUP RESULT: TIMEOUT. This strongly points at pip network/index/proxy behavior."
} elseif ($versionCheck.Ok) {
    Write-Host "INDEX LOOKUP RESULT: OK."
} else {
    Write-Host "INDEX LOOKUP RESULT: FAILED EXIT $($versionCheck.ExitCode). Check stderr log."
}

Write-Host ""
Write-Host "Probe complete. Paste the Summary hints plus TEST A/B/C outputs."
