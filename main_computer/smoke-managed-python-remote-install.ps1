# smoke-managed-python-remote-install.ps1
#
# Low-risk remote CPython smoke installer.
#
# Does NOT run:
#   - existing managed python.exe
#   - ensurepip
#   - get-pip.py
#   - python -m pip install
#   - default python -m venv
#   - python.org .exe installer / MSI
#   - CIM/WMI
#   - Get-Process
#   - taskkill
#   - PATH python
#   - py.exe
#   - global PATH mutation
#
# It manually unzips pip's wheel into the fresh venv site-packages.

[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12.10",
    [string]$PipVersion = "25.0.1",
    [string]$ToolRoot = (Join-Path $env:USERPROFILE ".main-computer-tools"),
    [int]$DownloadTimeoutSec = 180,
    [int]$ProcessTimeoutSec = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Say-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "==> $Text"
}

function Say-Ok {
    param([string]$Text)
    Write-Host "OK  - $Text"
}

function Say-Fail {
    param([string]$Text)
    Write-Host "FAIL - $Text" -ForegroundColor Red
}

function Quote-Arg {
    param([AllowNull()][string]$Arg)

    if ($null -eq $Arg -or $Arg -eq "") {
        return '""'
    }

    if ($Arg -notmatch '[\s"]') {
        return $Arg
    }

    $s = New-Object System.Text.StringBuilder
    [void]$s.Append('"')
    $slashes = 0

    foreach ($ch in $Arg.ToCharArray()) {
        if ($ch -eq [char]'\') {
            $slashes++
            continue
        }

        if ($ch -eq [char]'"') {
            if ($slashes -gt 0) {
                [void]$s.Append(('\' * ($slashes * 2)))
                $slashes = 0
            }
            [void]$s.Append('\"')
            continue
        }

        if ($slashes -gt 0) {
            [void]$s.Append(('\' * $slashes))
            $slashes = 0
        }

        [void]$s.Append($ch)
    }

    if ($slashes -gt 0) {
        [void]$s.Append(('\' * ($slashes * 2)))
    }

    [void]$s.Append('"')
    return $s.ToString()
}

function Run-Exe {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Args = @(),
        [int]$TimeoutSec = 60,
        [string]$WorkingDirectory = $PWD.Path
    )

    if (-not (Test-Path -LiteralPath $Exe)) {
        throw "Executable not found: $Exe"
    }

    $argLine = (($Args | ForEach-Object { Quote-Arg $_ }) -join " ")

    Write-Host "RUN ${TimeoutSec}s:"
    Write-Host "  $Exe $argLine"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = $argLine
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    [void]$psi.EnvironmentVariables.Remove("PYTHONHOME")
    [void]$psi.EnvironmentVariables.Remove("PYTHONPATH")
    $psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1"
    $psi.EnvironmentVariables["PYTHONUTF8"] = "1"

    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi

    [void]$p.Start()

    $outTask = $p.StandardOutput.ReadToEndAsync()
    $errTask = $p.StandardError.ReadToEndAsync()

    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        try { $p.Kill() } catch {}
        throw "Timed out after ${TimeoutSec}s: $Exe $argLine"
    }

    $stdout = $outTask.GetAwaiter().GetResult()
    $stderr = $errTask.GetAwaiter().GetResult()

    return [pscustomobject]@{
        ExitCode = $p.ExitCode
        Stdout   = $stdout
        Stderr   = $stderr
    }
}

function Download-File {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [int]$TimeoutSec = 180
    )

    $dir = Split-Path -Parent $OutFile
    New-Item -ItemType Directory -Force -Path $dir | Out-Null

    if (Test-Path -LiteralPath $OutFile) {
        $old = Get-Item -LiteralPath $OutFile
        if ($old.Length -gt 100000) {
            Say-Ok "Using cached file: $OutFile"
            return
        }

        Remove-Item -LiteralPath $OutFile -Force
    }

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    } catch {}

    $req = [System.Net.HttpWebRequest]::Create($Url)
    $req.Method = "GET"
    $req.Timeout = $TimeoutSec * 1000
    $req.ReadWriteTimeout = $TimeoutSec * 1000
    $req.AllowAutoRedirect = $true
    $req.UserAgent = "main-computer-smoke/1.0"

    $resp = $null
    $inStream = $null
    $outStream = $null
    $timer = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        $resp = $req.GetResponse()
        $inStream = $resp.GetResponseStream()
        $outStream = [System.IO.File]::Open(
            $OutFile,
            [System.IO.FileMode]::Create,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )

        $buf = New-Object byte[] 1048576

        while ($true) {
            if ($timer.Elapsed.TotalSeconds -gt $TimeoutSec) {
                throw "Download timed out after ${TimeoutSec}s: $Url"
            }

            $n = $inStream.Read($buf, 0, $buf.Length)
            if ($n -le 0) {
                break
            }

            $outStream.Write($buf, 0, $n)
        }
    }
    finally {
        if ($outStream) { $outStream.Dispose() }
        if ($inStream) { $inStream.Dispose() }
        if ($resp) { $resp.Dispose() }
        $timer.Stop()
    }

    if (-not (Test-Path -LiteralPath $OutFile)) {
        throw "Download did not create file: $OutFile"
    }

    $item = Get-Item -LiteralPath $OutFile
    if ($item.Length -lt 100000) {
        throw "Downloaded file is too small: $OutFile ($($item.Length) bytes)"
    }
}

function Download-Text {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 180
    )

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    } catch {}

    $req = [System.Net.HttpWebRequest]::Create($Url)
    $req.Method = "GET"
    $req.Timeout = $TimeoutSec * 1000
    $req.ReadWriteTimeout = $TimeoutSec * 1000
    $req.AllowAutoRedirect = $true
    $req.UserAgent = "main-computer-smoke/1.0"

    $resp = $null
    $stream = $null
    $reader = $null

    try {
        $resp = $req.GetResponse()
        $stream = $resp.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        return $reader.ReadToEnd()
    }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($stream) { $stream.Dispose() }
        if ($resp) { $resp.Dispose() }
    }
}

function Extract-Zip {
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path -LiteralPath $Destination) {
        throw "Refusing to extract over existing path: $Destination"
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $Destination)
}

function Copy-Tree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path -LiteralPath $Destination) {
        throw "Refusing to copy over existing path: $Destination"
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $Destination $item.Name) -Recurse -Force
    }
}

function Get-PipWheelUrl {
    param(
        [Parameter(Mandatory = $true)][string]$PipVersion,
        [int]$TimeoutSec = 180
    )

    $jsonUrl = "https://pypi.org/pypi/pip/$PipVersion/json"
    $jsonText = Download-Text -Url $jsonUrl -TimeoutSec $TimeoutSec
    $data = $jsonText | ConvertFrom-Json

    foreach ($u in $data.urls) {
        if ($u.packagetype -eq "bdist_wheel" -and $u.filename -like "pip-$PipVersion-py3-none-any.whl") {
            return $u.url
        }
    }

    throw "Could not find pip wheel URL in PyPI metadata for pip $PipVersion"
}

try {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"

    $downloadRoot = Join-Path $ToolRoot "downloads"
    $cpyRoot = Join-Path $ToolRoot "cpython"

    $pythonPackageUrl = "https://www.nuget.org/api/v2/package/python/$PythonVersion"
    $pythonPackagePath = Join-Path $downloadRoot "python.$PythonVersion.nupkg"

    $pipWheelUrl = $null
    $pipWheelPath = Join-Path $downloadRoot "pip-$PipVersion-py3-none-any.whl"

    $extractRoot = Join-Path $downloadRoot "extract-python-$PythonVersion-$stamp"
    $freshRoot = Join-Path $cpyRoot "$PythonVersion-nuget-amd64-$stamp"

    $toolsRoot = Join-Path $extractRoot "tools"
    $pythonExe = Join-Path $freshRoot "python.exe"

    $tmpRoot = Join-Path $env:TEMP "mc-smoke-venv-$stamp"
    $venvRoot = Join-Path $tmpRoot "venv"
    $venvPy = Join-Path $venvRoot "Scripts\python.exe"
    $venvSitePackages = Join-Path $venvRoot "Lib\site-packages"

    Say-Step "Smoke config"
    Write-Host "Python version:       $PythonVersion"
    Write-Host "Pip wheel version:    $PipVersion"
    Write-Host "Python package URL:   $pythonPackageUrl"
    Write-Host "Fresh managed root:   $freshRoot"
    Write-Host "Existing Python:      not executed"
    Write-Host "ensurepip:            not used"
    Write-Host "get-pip.py:           not used"
    Write-Host "pip install:          not used"
    Write-Host "default venv:         not used"
    Write-Host "CIM/WMI:              not used"
    Write-Host "Installer/MSI:        not used"

    New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $cpyRoot | Out-Null

    Say-Step "Downloading Python NuGet package"
    Download-File -Url $pythonPackageUrl -OutFile $pythonPackagePath -TimeoutSec $DownloadTimeoutSec
    Say-Ok "Python package ready"

    Say-Step "Finding pip wheel URL"
    $pipWheelUrl = Get-PipWheelUrl -PipVersion $PipVersion -TimeoutSec $DownloadTimeoutSec
    Write-Host $pipWheelUrl
    Say-Ok "pip wheel URL found"

    Say-Step "Downloading pip wheel"
    Download-File -Url $pipWheelUrl -OutFile $pipWheelPath -TimeoutSec $DownloadTimeoutSec
    Say-Ok "pip wheel ready"

    Say-Step "Extracting Python package"
    Extract-Zip -ZipPath $pythonPackagePath -Destination $extractRoot

    if (-not (Test-Path -LiteralPath $toolsRoot)) {
        throw "Package did not contain tools directory: $toolsRoot"
    }

    if (-not (Test-Path -LiteralPath (Join-Path $toolsRoot "python.exe"))) {
        throw "Package did not contain tools\python.exe"
    }

    Say-Ok "Python package extracted"

    Say-Step "Copying fresh Python payload"
    Copy-Tree -Source $toolsRoot -Destination $freshRoot

    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw "Fresh python.exe missing: $pythonExe"
    }

    Say-Ok "Fresh Python copied"

    Say-Step "Checking fresh Python identity"
    $identity = Run-Exe -Exe $pythonExe -Args @(
        "-c",
        "import sys, platform; print(sys.executable); print(sys.version); print(platform.architecture()[0]); print(sys.prefix); print(sys.base_prefix)"
    ) -TimeoutSec $ProcessTimeoutSec

    if ($identity.ExitCode -ne 0) {
        throw "Fresh Python failed identity check:`n$($identity.Stdout)`n$($identity.Stderr)"
    }

    Write-Host $identity.Stdout.Trim()
    Say-Ok "Fresh Python starts"

    Say-Step "Creating temp venv without pip"
    New-Item -ItemType Directory -Force -Path $tmpRoot | Out-Null

    $venv = Run-Exe -Exe $pythonExe -Args @(
        "-m",
        "venv",
        "--without-pip",
        $venvRoot
    ) -TimeoutSec $ProcessTimeoutSec

    if ($venv.ExitCode -ne 0) {
        throw "venv --without-pip failed:`n$($venv.Stdout)`n$($venv.Stderr)"
    }

    if (-not (Test-Path -LiteralPath $venvPy)) {
        throw "venv python.exe missing: $venvPy"
    }

    if (-not (Test-Path -LiteralPath $venvSitePackages)) {
        throw "venv site-packages missing: $venvSitePackages"
    }

    Say-Ok "venv --without-pip works"

    Say-Step "Manually extracting pip wheel into venv site-packages"
    Extract-Zip -ZipPath $pipWheelPath -Destination (Join-Path $tmpRoot "pip-wheel-expanded")

    $pipExpanded = Join-Path $tmpRoot "pip-wheel-expanded"

    foreach ($item in Get-ChildItem -LiteralPath $pipExpanded -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $venvSitePackages $item.Name) -Recurse -Force
    }

    Say-Ok "pip wheel extracted into venv site-packages"

    Say-Step "Checking venv pip without install step"
    $venvPip = Run-Exe -Exe $venvPy -Args @(
        "-m",
        "pip",
        "--version"
    ) -TimeoutSec $ProcessTimeoutSec

    if ($venvPip.ExitCode -ne 0) {
        throw "venv python -m pip failed after wheel extraction:`n$($venvPip.Stdout)`n$($venvPip.Stderr)"
    }

    Write-Host $venvPip.Stdout.Trim()
    Say-Ok "venv pip works"

    Say-Step "Writing pointer file"
    $pointer = Join-Path $cpyRoot "current-python.txt"
    Set-Content -LiteralPath $pointer -Value $pythonExe -Encoding ASCII
    Say-Ok "Pointer written: $pointer"

    Write-Host ""
    Write-Host "SMOKE PASS"
    Write-Host "Trusted Python:"
    Write-Host "  $pythonExe"
    Write-Host ""
    Write-Host "Pointer file:"
    Write-Host "  $pointer"
    exit 0
}
catch {
    Write-Host ""
    Say-Fail $_.Exception.Message
    Write-Host ""
    Write-Host "SMOKE FAIL"
    exit 1
}
finally {
    if ($tmpRoot -and (Test-Path -LiteralPath $tmpRoot)) {
        Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }

    if ($extractRoot -and (Test-Path -LiteralPath $extractRoot)) {
        Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}