[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$OutputRoot = "",
    [string]$StageRoot = "",
    [string]$MakeNsisCompiler = "",
    [switch]$IncludeRevisionControl,
    [switch]$IncludePatchingTools
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([Parameter(Mandatory = $true)][string]$Title)
    Write-Host ""
    Write-Host $Title
    Write-Host ("-" * $Title.Length)
}

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw "[main-computer-nsis-installer-experimental-v7] $Message"
}

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

function Convert-ToExtendedLengthPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)

    if ($fullPath.StartsWith("\\?\", [System.StringComparison]::Ordinal)) {
        return $fullPath
    }

    if ($fullPath.StartsWith("\\", [System.StringComparison]::Ordinal)) {
        return "\\?\UNC\" + $fullPath.Substring(2)
    }

    return "\\?\" + $fullPath
}

function New-DirectoryLongPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    [System.IO.Directory]::CreateDirectory((Convert-ToExtendedLengthPath $Path)) | Out-Null
}

function Remove-DirectoryLongPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $extendedPath = Convert-ToExtendedLengthPath $Path
    if ([System.IO.Directory]::Exists($extendedPath)) {
        [System.IO.Directory]::Delete($extendedPath, $true)
    }
}

function Get-RepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

function Get-RelativePathCompat {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$FullPath
    )

    $baseFull = [System.IO.Path]::GetFullPath($BasePath).TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
    $targetFull = [System.IO.Path]::GetFullPath($FullPath)
    $baseUri = New-Object System.Uri($baseFull)
    $targetUri = New-Object System.Uri($targetFull)
    $relativeUri = $baseUri.MakeRelativeUri($targetUri)
    return [System.Uri]::UnescapeDataString($relativeUri.ToString()).Replace('/', [System.IO.Path]::DirectorySeparatorChar)
}

function Convert-ToRepoPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return ($Path -replace "\\", "/").Trim("/")
}

function Test-RepoPathSkipped {
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [bool]$IsDirectory = $false
    )

    $repoPath = Convert-ToRepoPath $RepoPath
    if ([string]::IsNullOrWhiteSpace($repoPath)) {
        return $false
    }

    $parts = @($repoPath.Split("/") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

    if (-not $IncludeRevisionControl) {
        if ($repoPath.Equals("revision_control", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
        if ($repoPath.StartsWith("revision_control/", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    if (-not $IncludePatchingTools) {
        if ($repoPath.Equals("tools/patching", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
        if ($repoPath.StartsWith("tools/patching/", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    if ($repoPath.IndexOf("/new_patch_runs/", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    if ($repoPath.EndsWith("/new_patch_runs", [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $blockedDirectoryNames = @(
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "release_reports",
        "harness_output",
        "harness_output_pretty_docs",
        "harness_output_game_editor",
        "debug_assets",
        "diagnostics_output",
        "cache",
        "tmp",
        "temp",
        "new_patch_runs"
    )

    foreach ($part in $parts) {
        if ($blockedDirectoryNames -contains $part) {
            return $true
        }
        if ($part.EndsWith(".egg-info", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
        if ($part.StartsWith("diagnostics_output", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
        if ($part.StartsWith("ollama_prompt_space_", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
        if ($part.StartsWith("harness_output", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
        if ($part.StartsWith("installer-nsis-experimental", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    $blockedPrefixes = @(
        "runtime/",
        "energy_credits/",
        "generated_component_docs/work/",
        "generated_component_docs/archive/",
        "main_computer/.main_computer_browser_profile/",
        "main_computer/debug_assets/",
        "contracts/cache/",
        "contracts/out/",
        "tools/patching/reports/",
        "tools/patching/patches/incoming/",
        "reports/new_patch_runs/"
    )

    foreach ($prefix in $blockedPrefixes) {
        if ($repoPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    $blockedExtensions = @(
        ".map",
        ".pyc",
        ".pyo",
        ".tmp"
    )

    $extension = [System.IO.Path]::GetExtension($repoPath)
    if ($blockedExtensions -contains $extension) {
        return $true
    }

    $blockedExactPaths = @(
        ".prod.lock",
        "aider.log",
        "generated_component_docs/doc-build.json",
        "generated_component_docs/doc-health.json",
        "generated_component_docs/graph.json"
    )

    return ($blockedExactPaths -contains $repoPath)
}

function Get-PyProjectVersion {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)

    $pyproject = Join-Path $RepoRoot "pyproject.toml"
    if (-not (Test-Path -LiteralPath $pyproject -PathType Leaf)) {
        return "0.1.0"
    }

    $match = Select-String -LiteralPath $pyproject -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($null -eq $match) {
        return "0.1.0"
    }

    return $match.Matches[0].Groups[1].Value
}

function Get-MakeNsisVersionText {
    param([Parameter(Mandatory = $true)][string]$CompilerPath)

    $versionOutput = & $CompilerPath /VERSION 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "makensis version probe failed for: $CompilerPath`n$versionOutput"
    }

    return (($versionOutput | Out-String).Trim())
}

function Resolve-MakeNsisCompiler {
    param([string]$RequestedCompiler)

    $candidates = @()

    if (-not [string]::IsNullOrWhiteSpace($RequestedCompiler)) {
        $candidates += (Resolve-FullPath $RequestedCompiler)
    }

    $command = Get-Command "makensis.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
        $candidates += $command.Source
    }

    $command = Get-Command "makensis" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
        $candidates += $command.Source
    }

    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidates += (Join-Path $env:ProgramFiles "NSIS\makensis.exe")
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $versionText = Get-MakeNsisVersionText -CompilerPath $candidate
            if ($versionText -match "v?([0-9]+)\.") {
                $major = [int]$Matches[1]
                if ($major -lt 3) {
                    Fail "NSIS 3.x or newer is required for this experiment. Found $versionText at $candidate"
                }
            }
            else {
                Fail "Could not parse makensis version from: $versionText"
            }

            return [pscustomobject]@{
                Path = (Resolve-FullPath $candidate)
                Version = $versionText
            }
        }
    }

    Fail "makensis.exe was not found. Install NSIS 3.x, or pass -MakeNsisCompiler with the full path to makensis.exe."
}

function Copy-FileLongPath {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    $parent = [System.IO.Path]::GetDirectoryName($DestinationPath)
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-DirectoryLongPath -Path $parent
    }

    [System.IO.File]::Copy(
        (Convert-ToExtendedLengthPath $SourcePath),
        (Convert-ToExtendedLengthPath $DestinationPath),
        $true
    )
}

function Copy-DirectoryFiltered {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$DestinationDir,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    foreach ($directory in [System.IO.Directory]::EnumerateDirectories($SourceDir)) {
        $relativeDirectory = Get-RelativePathCompat -BasePath $RepoRoot -FullPath $directory
        if (Test-RepoPathSkipped -RepoPath $relativeDirectory -IsDirectory $true) {
            continue
        }

        $destinationSubdir = Join-Path $DestinationDir ([System.IO.Path]::GetFileName($directory))
        New-DirectoryLongPath -Path $destinationSubdir
        Copy-DirectoryFiltered -SourceDir $directory -DestinationDir $destinationSubdir -RepoRoot $RepoRoot
    }

    foreach ($file in [System.IO.Directory]::EnumerateFiles($SourceDir)) {
        $relativeFile = Get-RelativePathCompat -BasePath $RepoRoot -FullPath $file
        if (Test-RepoPathSkipped -RepoPath $relativeFile -IsDirectory $false) {
            continue
        }

        $destinationFile = Join-Path $DestinationDir ([System.IO.Path]::GetFileName($file))
        Copy-FileLongPath -SourcePath $file -DestinationPath $destinationFile
    }
}

function Copy-RepoPayload {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PayloadRoot
    )

    if (Test-Path -LiteralPath $PayloadRoot) {
        Remove-DirectoryLongPath -Path $PayloadRoot
    }

    New-DirectoryLongPath -Path $PayloadRoot
    Copy-DirectoryFiltered -SourceDir $RepoRoot -DestinationDir $PayloadRoot -RepoRoot $RepoRoot
}

function Assert-PayloadFileStagedFromRepo {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PayloadRoot,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [string[]]$RequiredMarkers = @()
    )

    $nativeRelativePath = $RelativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $repoFile = Join-Path $RepoRoot $nativeRelativePath
    $payloadFile = Join-Path $PayloadRoot $nativeRelativePath

    if (-not (Test-Path -LiteralPath $repoFile -PathType Leaf)) {
        Fail "NSIS payload staging source is missing: $RelativePath ($repoFile)"
    }
    if (-not (Test-Path -LiteralPath $payloadFile -PathType Leaf)) {
        Fail "NSIS payload staging missing staged copy: $RelativePath ($payloadFile). The installer would package incomplete payload. Aborting."
    }

    $repoHash = (Get-FileHash -LiteralPath $repoFile -Algorithm SHA256).Hash
    $payloadHash = (Get-FileHash -LiteralPath $payloadFile -Algorithm SHA256).Hash
    if ($repoHash -ne $payloadHash) {
        Fail "NSIS payload staging mismatch for ${RelativePath}: repo hash ${repoHash}; stage hash ${payloadHash}. The installer would package stale payload. Aborting."
    }

    if ($RequiredMarkers.Count -gt 0) {
        $payloadText = [System.IO.File]::ReadAllText($payloadFile)
        foreach ($marker in $RequiredMarkers) {
            if (-not $payloadText.Contains($marker)) {
                Fail "NSIS payload staging marker missing for ${RelativePath}: ${marker}. The installer would package stale or incomplete payload. Aborting."
            }
        }
    }

    return [ordered]@{
        relativePath = $RelativePath
        sha256 = $payloadHash
    }
}

function Assert-RepoPayloadStagingIntegrity {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PayloadRoot
    )

    $criticalPayloadFiles = @(
        @{
            RelativePath = "scripts/main-computer-start-stop.ps1"
            RequiredMarkers = @(
                "Ensure-MainComputerPodmanMachineStarted",
                "podman machine start",
                "MAIN_COMPUTER_CONTAINER_RUNTIME"
            )
        },
        @{
            RelativePath = "bootstrap-main-computer-python-windows.ps1"
            RequiredMarkers = @(
                "The installer-selected runtime must win over stale user/machine",
                "MAIN_COMPUTER_CONTAINER_RUNTIME",
                "MAIN_COMPUTER_CONTAINER_COMMAND"
            )
        },
        @{
            RelativePath = "requirements.txt"
            RequiredMarkers = @(
                "podman-compose"
            )
        },
        @{
            RelativePath = "main_computer/bootstrap/cli.py"
            RequiredMarkers = @(
                "podman_compose_provider_path",
                "PODMAN_COMPOSE_PROVIDER",
                "apply_podman_compose_provider_env"
            )
        },
        @{
            RelativePath = "main_computer/bootstrap/install_root.py"
            RequiredMarkers = @(
                "INSTALL_ROOT_ARCHIVE_BLOCKED_PREFIXES",
                "iter_install_root_archive_files",
                "Archive note"
            )
        },
        @{
            RelativePath = "main_computer/container_runtime.py"
            RequiredMarkers = @(
                "podman_command_cwd",
                "MAIN_COMPUTER_PODMAN_COMMAND_CWD",
                "win-sshproxy.exe"
            )
        },
        @{
            RelativePath = "scripts/windows/build-main-computer-nsis-installer.experimental-v7.ps1"
            RequiredMarkers = @(
                "Assert-RepoPayloadStagingIntegrity",
                "NSIS payload staging mismatch",
                "The installer would package stale payload. Aborting."
            )
        }
    )

    $verified = @()
    foreach ($file in $criticalPayloadFiles) {
        $verified += Assert-PayloadFileStagedFromRepo `
            -RepoRoot $RepoRoot `
            -PayloadRoot $PayloadRoot `
            -RelativePath $file["RelativePath"] `
            -RequiredMarkers $file["RequiredMarkers"]
    }

    Write-Host "Verified NSIS payload staging integrity for critical files:"
    foreach ($file in $verified) {
        Write-Host ("  {0} sha256={1}" -f $file["relativePath"], $file["sha256"])
    }

    return $verified
}


function Write-PackageWrapper {
    param([Parameter(Mandatory = $true)][string]$WrapperPath)

    $wrapper = @'
# Experimental NSIS package launcher with diagnostic logging.
#
# This file is generated into the installer staging directory by
# build-main-computer-nsis-installer.experimental-v7.ps1. It delegates to the
# Python-owned Windows bootstrap inside the packaged repository payload.
#
# v5 runs the Python bootstrap in a child PowerShell process so its stdout,
# stderr, command line, and exit code can be written to an install log before
# this wrapper exits back to NSIS.

[CmdletBinding()]
param(
    [ValidateSet("test", "prod")]
    [string]$RuntimeProfile = "test",

    [string]$Mode = "Unleashed",

    [ValidateSet("docker", "podman")]
    [string]$ContainerRuntime = "docker",

    [string]$InstallRoot = "",

    [switch]$AllowReHome,

    [switch]$SkipAppStart,

    [switch]$PrecheckOnly,

    [switch]$NoPythonDownload,

    [switch]$VerboseBootstrap,

    [switch]$SkipDockerRequirement,

    [switch]$SkipHostRequirementInstall,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingBootstrapArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw "[main-computer-nsis-package-installer-v7] $Message"
}

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

function Quote-ProcessArgument {
    param([AllowEmptyString()][string]$Value)

    if ($null -eq $Value) {
        return '""'
    }

    if ($Value.Length -gt 0 -and $Value.IndexOfAny([char[]]@(' ', "`t", '"')) -lt 0) {
        return $Value
    }

    # Conservative quoting for Windows command-line arguments used by
    # Start-Process -ArgumentList. The paths/values generated here do not
    # contain embedded newlines.
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Write-LogLine {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [AllowEmptyString()][string]$Message = ""
    )

    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value $Message
}

function Write-LogBlock {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$Title,
        [AllowEmptyString()][string]$Text = ""
    )

    Write-LogLine -LogPath $LogPath -Message ""
    Write-LogLine -LogPath $LogPath -Message "[$Title]"
    Write-LogLine -LogPath $LogPath -Message $Text
}


function Write-RequirementLog {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [AllowEmptyString()][string]$Message = ""
    )

    Write-Host $Message
    Write-LogLine -LogPath $LogPath -Message $Message
}

function Add-ProcessPathIfPresent {
    param([Parameter(Mandatory = $true)][string]$Directory)

    if ([string]::IsNullOrWhiteSpace($Directory) -or -not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return
    }

    $parts = @($env:Path -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    foreach ($part in $parts) {
        if ($part.TrimEnd('\') -ieq $Directory.TrimEnd('\')) {
            return
        }
    }

    $env:Path = "$Directory;$env:Path"
}

function Refresh-CommonToolPaths {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    foreach ($pathText in @($machinePath, $userPath)) {
        if (-not [string]::IsNullOrWhiteSpace($pathText)) {
            foreach ($part in ($pathText -split ';')) {
                if (-not [string]::IsNullOrWhiteSpace($part) -and (Test-Path -LiteralPath $part -PathType Container)) {
                    Add-ProcessPathIfPresent -Directory $part
                }
            }
        }
    }

    Add-ProcessPathIfPresent -Directory "$env:ProgramFiles\Git\cmd"
    Add-ProcessPathIfPresent -Directory "$env:ProgramFiles\Git\bin"
    Add-ProcessPathIfPresent -Directory "${env:ProgramFiles(x86)}\Git\cmd"
    Add-ProcessPathIfPresent -Directory "${env:ProgramFiles(x86)}\Git\bin"
    Add-ProcessPathIfPresent -Directory "$env:LOCALAPPDATA\Programs\Ollama"
    Add-ProcessPathIfPresent -Directory "$env:ProgramFiles\Ollama"
    Add-ProcessPathIfPresent -Directory "$env:LOCALAPPDATA\Programs\Podman"
    Add-ProcessPathIfPresent -Directory "$env:SystemRoot\System32\OpenSSH"
    Add-ProcessPathIfPresent -Directory "$env:SystemRoot\Sysnative\OpenSSH"
    Add-ProcessPathIfPresent -Directory "$env:LOCALAPPDATA\Microsoft\WindowsApps"
    Add-ProcessPathIfPresent -Directory "$env:ProgramFiles\Docker\Docker\resources\bin"
    Add-ProcessPathIfPresent -Directory "${env:ProgramFiles(x86)}\Docker\Docker\resources\bin"
    Add-ProcessPathIfPresent -Directory "$env:ProgramFiles\RedHat\Podman"
    Add-ProcessPathIfPresent -Directory "$env:ProgramFiles\Podman"
    Add-ProcessPathIfPresent -Directory "${env:ProgramFiles(x86)}\RedHat\Podman"
    Add-ProcessPathIfPresent -Directory "${env:ProgramFiles(x86)}\Podman"
}

function Resolve-ApplicationCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$CandidatePaths = @()
    )

    Refresh-CommonToolPaths

    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
        return (Resolve-FullPath $command.Source)
    }

    foreach ($candidate in $CandidatePaths) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-FullPath $candidate)
        }
    }

    return ""
}

function Invoke-ToolCapture {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$Check
    )

    $display = $FilePath
    if ($Arguments.Count -gt 0) {
        $display = "$FilePath $($Arguments -join ' ')"
    }
    Write-RequirementLog -LogPath $LogPath -Message "Running: $display"

    $output = ""
    $exitCode = 0
    try {
        $output = (& $FilePath @Arguments 2>&1 | Out-String)
        if ($null -eq $LASTEXITCODE) {
            $exitCode = 0
        }
        else {
            $exitCode = [int]$LASTEXITCODE
        }
    }
    catch {
        $output = $_.Exception.Message
        $exitCode = 1
    }

    if (-not [string]::IsNullOrWhiteSpace($output)) {
        Write-LogBlock -LogPath $LogPath -Title "output: $display" -Text $output
    }
    Write-RequirementLog -LogPath $LogPath -Message "Exit code: $exitCode"

    if ($Check -and $exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $display"
    }

    return @{
        ExitCode = $exitCode
        Output = $output
    }
}

function Resolve-ContainerRuntimeCli {
    param([Parameter(Mandatory = $true)][ValidateSet("docker", "podman")][string]$Runtime)

    if ($Runtime -eq "podman") {
        return Resolve-ApplicationCommand -Name "podman" -CandidatePaths @(
            "$env:LOCALAPPDATA\Programs\Podman\podman.exe",
            "$env:ProgramFiles\RedHat\Podman\podman.exe",
            "$env:ProgramFiles\Podman\podman.exe",
            "${env:ProgramFiles(x86)}\RedHat\Podman\podman.exe",
            "${env:ProgramFiles(x86)}\Podman\podman.exe"
        )
    }

    return Resolve-ApplicationCommand -Name "docker" -CandidatePaths @(
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\resources\bin\docker.exe"
    )
}

function Test-ContainerRuntimeRequirement {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][ValidateSet("docker", "podman")][string]$Runtime,
        [switch]$Quiet
    )

    $containerCli = Resolve-ContainerRuntimeCli -Runtime $Runtime
    if ([string]::IsNullOrWhiteSpace($containerCli)) {
        if (-not $Quiet) {
            Write-RequirementLog -LogPath $LogPath -Message "$Runtime executable was not found on PATH or in the expected install directory."
        }
        return $false
    }

    if (-not $Quiet) {
        Write-RequirementLog -LogPath $LogPath -Message "Requested container runtime: $Runtime"
        Write-RequirementLog -LogPath $LogPath -Message "Container CLI: $containerCli"
    }

    $version = Invoke-ToolCapture -LogPath $LogPath -FilePath $containerCli -Arguments @("version")
    if ([int]$version.ExitCode -ne 0) {
        if (-not $Quiet) {
            Write-RequirementLog -LogPath $LogPath -Message "$Runtime version failed. The container runtime may not be installed correctly or may not be initialized/running."
        }
        return $false
    }

    $composeOk = $false
    $compose = Invoke-ToolCapture -LogPath $LogPath -FilePath $containerCli -Arguments @("compose", "version")
    if ([int]$compose.ExitCode -eq 0) {
        $composeOk = $true
        if (-not $Quiet) {
            Write-RequirementLog -LogPath $LogPath -Message "Container Compose CLI: $containerCli compose"
        }
    }
    elseif ($Runtime -eq "podman") {
        $podmanCompose = Resolve-ApplicationCommand -Name "podman-compose" -CandidatePaths @()
        if (-not [string]::IsNullOrWhiteSpace($podmanCompose)) {
            $compose = Invoke-ToolCapture -LogPath $LogPath -FilePath $podmanCompose -Arguments @("version")
            if ([int]$compose.ExitCode -eq 0) {
                $composeOk = $true
                if (-not $Quiet) {
                    Write-RequirementLog -LogPath $LogPath -Message "Container Compose CLI: $podmanCompose"
                }
            }
        }
    }

    if (-not $composeOk) {
        if (-not $Quiet) {
            if ($Runtime -eq "podman") {
                Write-RequirementLog -LogPath $LogPath -Message "podman compose version and podman-compose version both failed. Podman Compose support is required."
            }
            else {
                Write-RequirementLog -LogPath $LogPath -Message "docker compose version failed. Docker Compose v2 is required."
            }
        }
        return $false
    }

    $ps = Invoke-ToolCapture -LogPath $LogPath -FilePath $containerCli -Arguments @("ps")
    if ([int]$ps.ExitCode -ne 0) {
        if (-not $Quiet) {
            Write-RequirementLog -LogPath $LogPath -Message "$Runtime ps failed. Start or initialize the selected container runtime and wait for the engine to be ready."
        }
        return $false
    }

    if (-not $Quiet) {
        Write-RequirementLog -LogPath $LogPath -Message "Container runtime requirement: OK ($Runtime)"
    }
    return $true
}

function Resolve-InstallerContainerRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][ValidateSet("docker", "podman")][string]$SelectedRuntime
    )

    # Deliberately use the installer selection, not the environment of the shell
    # that built or launched the setup EXE. The setup maker's
    # MAIN_COMPUTER_CONTAINER_RUNTIME must not change the generated installer.
    # The NSIS page requires an explicit Docker Desktop or Podman choice.
    return $SelectedRuntime.Trim().ToLowerInvariant()
}

function Get-ContainerRuntimeDisplayName {
    param([Parameter(Mandatory = $true)][ValidateSet("docker", "podman")][string]$Runtime)

    if ($Runtime -eq "podman") {
        return "Podman"
    }
    return "Docker Desktop"
}

function Fail-ContainerRuntimeRequirement {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][ValidateSet("docker", "podman")][string]$Runtime,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $dockerInstallUrl = "https://docs.docker.com/desktop/setup/install/windows-install/"
    $podmanInstallUrl = "https://podman.io/docs/installation"
    $displayName = Get-ContainerRuntimeDisplayName -Runtime $Runtime

    Write-RequirementLog -LogPath $LogPath -Message ""
    Write-RequirementLog -LogPath $LogPath -Message "A Docker-compatible container runtime is required for this version of Main Computer."
    Write-RequirementLog -LogPath $LogPath -Message "Requested runtime: $Runtime ($displayName)"
    Write-RequirementLog -LogPath $LogPath -Message "Reason: $Reason"
    Write-RequirementLog -LogPath $LogPath -Message ""

    if ($Runtime -eq "podman") {
        Write-RequirementLog -LogPath $LogPath -Message "The installer Podman runtime choice requires Podman to be installed and resolvable on PATH or in the standard per-user Podman install directory."
        Write-RequirementLog -LogPath $LogPath -Message "Install guide: $podmanInstallUrl"
        Write-RequirementLog -LogPath $LogPath -Message ""
        Write-RequirementLog -LogPath $LogPath -Message "After installing Podman, these commands should work:"
        Write-RequirementLog -LogPath $LogPath -Message "  podman version"
        Write-RequirementLog -LogPath $LogPath -Message "  podman compose version   # or: podman-compose version"
        Write-RequirementLog -LogPath $LogPath -Message "  podman ps"
        exit 43
    }

    Write-RequirementLog -LogPath $LogPath -Message "The installer Docker Desktop runtime choice requires Docker Desktop to be installed, running, and resolvable on PATH."
    Write-RequirementLog -LogPath $LogPath -Message "Install Docker Desktop for Windows, start Docker Desktop once, complete any first-run prompts, then rerun this installer."
    Write-RequirementLog -LogPath $LogPath -Message "Install guide: $dockerInstallUrl"
    Write-RequirementLog -LogPath $LogPath -Message ""
    Write-RequirementLog -LogPath $LogPath -Message "After installing Docker, these commands should work:"
    Write-RequirementLog -LogPath $LogPath -Message "  docker version"
    Write-RequirementLog -LogPath $LogPath -Message "  docker compose version"
    Write-RequirementLog -LogPath $LogPath -Message "  docker ps"
    exit 43
}

function Assert-ContainerRuntimeRequirement {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][ValidateSet("docker", "podman")][string]$SelectedRuntime
    )

    if (-not (Test-ContainerRuntimeRequirement -LogPath $LogPath -Runtime $SelectedRuntime)) {
        if ($SelectedRuntime -eq "podman") {
            Fail-ContainerRuntimeRequirement -LogPath $LogPath -Runtime $SelectedRuntime -Reason "The selected Podman runtime did not pass version, compose version, and ps checks."
        }
        Fail-ContainerRuntimeRequirement -LogPath $LogPath -Runtime $SelectedRuntime -Reason "The selected Docker Desktop runtime did not pass version, compose version, and ps checks."
    }
}

function Resolve-WingetRequirement {
    param([Parameter(Mandatory = $true)][string]$LogPath)

    $winget = Resolve-ApplicationCommand -Name "winget" -CandidatePaths @(
        "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe"
    )

    if ([string]::IsNullOrWhiteSpace($winget)) {
        Write-RequirementLog -LogPath $LogPath -Message "winget.exe was not found. Automatic install/repair of Git and Ollama cannot continue."
        throw "winget.exe was not found. Install App Installer from Microsoft Store or install missing tools manually, then rerun this installer."
    }

    Write-RequirementLog -LogPath $LogPath -Message "winget: $winget"
    return $winget
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$PackageId,
        [Parameter(Mandatory = $true)][string]$DisplayName
    )

    $winget = Resolve-WingetRequirement -LogPath $LogPath
    Write-RequirementLog -LogPath $LogPath -Message "Installing $DisplayName with winget package id: $PackageId"
    Invoke-ToolCapture -LogPath $LogPath -FilePath $winget -Arguments @(
        "install",
        "--id", $PackageId,
        "-e",
        "--source", "winget",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--silent"
    ) -Check | Out-Null

    Refresh-CommonToolPaths
}

function Install-PodmanWithWinget {
    param([Parameter(Mandatory = $true)][string]$LogPath)

    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($packageId in @("Podman.CLI", "RedHat.Podman")) {
        try {
            Install-WingetPackage -LogPath $LogPath -PackageId $packageId -DisplayName "Podman"
            return
        }
        catch {
            $message = $_.Exception.Message
            $errors.Add("${packageId}: ${message}") | Out-Null
            Write-RequirementLog -LogPath $LogPath -Message "Podman winget package attempt failed for ${packageId}: ${message}"
        }
    }

    throw "Podman could not be installed with winget. Attempts: $($errors -join ' ; ')"
}

function Ensure-PodmanRequirement {
    param([Parameter(Mandatory = $true)][string]$LogPath)

    $podman = Resolve-ContainerRuntimeCli -Runtime "podman"
    if ([string]::IsNullOrWhiteSpace($podman)) {
        Write-RequirementLog -LogPath $LogPath -Message "Podman was selected but podman.exe was not found. Attempting winget install."
        Install-PodmanWithWinget -LogPath $LogPath
        Refresh-CommonToolPaths
        $podman = Resolve-ContainerRuntimeCli -Runtime "podman"
    }

    if ([string]::IsNullOrWhiteSpace($podman)) {
        Write-RequirementLog -LogPath $LogPath -Message "Podman was installed or requested, but podman.exe could not be found afterward. Expected locations include `%LOCALAPPDATA%\Programs\Podman\podman.exe`."
        exit 43
    }

    Write-RequirementLog -LogPath $LogPath -Message "Podman CLI: $podman"

    $inspect = Invoke-ToolCapture -LogPath $LogPath -FilePath $podman -Arguments @("machine", "inspect")
    if ([int]$inspect.ExitCode -ne 0) {
        Write-RequirementLog -LogPath $LogPath -Message "No initialized Podman machine was detected. Running podman machine init."
        Invoke-ToolCapture -LogPath $LogPath -FilePath $podman -Arguments @("machine", "init") -Check | Out-Null
    }

    $start = Invoke-ToolCapture -LogPath $LogPath -FilePath $podman -Arguments @("machine", "start")
    if ([int]$start.ExitCode -ne 0) {
        Write-RequirementLog -LogPath $LogPath -Message "podman machine start did not complete cleanly. The final runtime check will report whether Podman is usable."
    }

    Refresh-CommonToolPaths
}

function Ensure-GitRequirement {
    param([Parameter(Mandatory = $true)][string]$LogPath)

    $git = Resolve-ApplicationCommand -Name "git" -CandidatePaths @(
        "$env:ProgramFiles\Git\cmd\git.exe",
        "$env:ProgramFiles\Git\bin\git.exe",
        "${env:ProgramFiles(x86)}\Git\cmd\git.exe",
        "${env:ProgramFiles(x86)}\Git\bin\git.exe"
    )

    if ([string]::IsNullOrWhiteSpace($git)) {
        Install-WingetPackage -LogPath $LogPath -PackageId "Git.Git" -DisplayName "Git"
        $git = Resolve-ApplicationCommand -Name "git" -CandidatePaths @(
            "$env:ProgramFiles\Git\cmd\git.exe",
            "$env:ProgramFiles\Git\bin\git.exe",
            "${env:ProgramFiles(x86)}\Git\cmd\git.exe",
            "${env:ProgramFiles(x86)}\Git\bin\git.exe"
        )
    }

    if ([string]::IsNullOrWhiteSpace($git)) {
        throw "Git was installed or requested, but git.exe could not be found afterward."
    }

    Write-RequirementLog -LogPath $LogPath -Message "Git: $git"
    Invoke-ToolCapture -LogPath $LogPath -FilePath $git -Arguments @("--version") -Check | Out-Null
    Write-RequirementLog -LogPath $LogPath -Message "Git requirement: OK"
}

function Ensure-OpenSSHClientRequirement {
    param([Parameter(Mandatory = $true)][string]$LogPath)

    $sshCandidates = @("$env:SystemRoot\System32\OpenSSH\ssh.exe", "$env:SystemRoot\Sysnative\OpenSSH\ssh.exe")
    $scpCandidates = @("$env:SystemRoot\System32\OpenSSH\scp.exe", "$env:SystemRoot\Sysnative\OpenSSH\scp.exe")
    $keygenCandidates = @("$env:SystemRoot\System32\OpenSSH\ssh-keygen.exe", "$env:SystemRoot\Sysnative\OpenSSH\ssh-keygen.exe")

    $ssh = Resolve-ApplicationCommand -Name "ssh" -CandidatePaths $sshCandidates
    $scp = Resolve-ApplicationCommand -Name "scp" -CandidatePaths $scpCandidates
    $sshKeygen = Resolve-ApplicationCommand -Name "ssh-keygen" -CandidatePaths $keygenCandidates

    if ([string]::IsNullOrWhiteSpace($ssh) -or [string]::IsNullOrWhiteSpace($scp) -or [string]::IsNullOrWhiteSpace($sshKeygen)) {
        Write-RequirementLog -LogPath $LogPath -Message "OpenSSH Client tools are missing. Attempting to enable the Windows OpenSSH Client capability."
        try {
            $capabilityName = "OpenSSH.Client~~~~0.0.1.0"
            $capability = Get-WindowsCapability -Online -Name $capabilityName -ErrorAction Stop
            if ($null -eq $capability -or $capability.State -ne "Installed") {
                Add-WindowsCapability -Online -Name $capabilityName -ErrorAction Stop | Out-Null
            }
        }
        catch {
            Write-RequirementLog -LogPath $LogPath -Message "OpenSSH Client automatic installation failed: $($_.Exception.Message)"
            throw "OpenSSH Client is required for website publishing. Enable the Windows OpenSSH Client optional feature or run PowerShell as Administrator: Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0"
        }

        Refresh-CommonToolPaths
        $ssh = Resolve-ApplicationCommand -Name "ssh" -CandidatePaths $sshCandidates
        $scp = Resolve-ApplicationCommand -Name "scp" -CandidatePaths $scpCandidates
        $sshKeygen = Resolve-ApplicationCommand -Name "ssh-keygen" -CandidatePaths $keygenCandidates
    }

    if ([string]::IsNullOrWhiteSpace($ssh) -or [string]::IsNullOrWhiteSpace($scp) -or [string]::IsNullOrWhiteSpace($sshKeygen)) {
        throw "OpenSSH Client installation completed, but ssh.exe/scp.exe/ssh-keygen.exe could not all be found."
    }

    Write-RequirementLog -LogPath $LogPath -Message "ssh: $ssh"
    Write-RequirementLog -LogPath $LogPath -Message "scp: $scp"
    Write-RequirementLog -LogPath $LogPath -Message "ssh-keygen: $sshKeygen"
    Invoke-ToolCapture -LogPath $LogPath -FilePath $ssh -Arguments @("-V") | Out-Null
    Write-RequirementLog -LogPath $LogPath -Message "OpenSSH Client requirement: OK"
}

function Test-OllamaApi {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 3 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Ensure-OllamaRequirement {
    param([Parameter(Mandatory = $true)][string]$LogPath)

    $ollama = Resolve-ApplicationCommand -Name "ollama" -CandidatePaths @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe",
        "${env:ProgramFiles(x86)}\Ollama\ollama.exe"
    )

    if ([string]::IsNullOrWhiteSpace($ollama)) {
        Install-WingetPackage -LogPath $LogPath -PackageId "Ollama.Ollama" -DisplayName "Ollama"
        $ollama = Resolve-ApplicationCommand -Name "ollama" -CandidatePaths @(
            "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
            "$env:ProgramFiles\Ollama\ollama.exe",
            "${env:ProgramFiles(x86)}\Ollama\ollama.exe"
        )
    }

    if ([string]::IsNullOrWhiteSpace($ollama)) {
        throw "Ollama was installed or requested, but ollama.exe could not be found afterward."
    }

    Write-RequirementLog -LogPath $LogPath -Message "Ollama: $ollama"
    Invoke-ToolCapture -LogPath $LogPath -FilePath $ollama -Arguments @("--version") | Out-Null

    if (-not (Test-OllamaApi)) {
        Write-RequirementLog -LogPath $LogPath -Message "Ollama API is not reachable at http://127.0.0.1:11434/api/tags. Attempting to start ollama serve in the background."
        try {
            Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
            Start-Sleep -Seconds 3
        }
        catch {
            Write-RequirementLog -LogPath $LogPath -Message "Could not start ollama serve automatically: $($_.Exception.Message)"
        }
    }

    if (Test-OllamaApi) {
        Write-RequirementLog -LogPath $LogPath -Message "Ollama API requirement: OK"
    }
    else {
        Write-RequirementLog -LogPath $LogPath -Message "WARNING: Ollama CLI is installed, but the local API is not reachable yet. Start Ollama after installation if local model features are needed."
    }
}

function Check-WslRequirement {
    param([Parameter(Mandatory = $true)][string]$LogPath)

    $wsl = Resolve-ApplicationCommand -Name "wsl" -CandidatePaths @(
        "$env:SystemRoot\System32\wsl.exe",
        "$env:SystemRoot\Sysnative\wsl.exe"
    )

    if ([string]::IsNullOrWhiteSpace($wsl)) {
        Write-RequirementLog -LogPath $LogPath -Message "WARNING: wsl.exe was not found. The container runtime may still be usable, but WSL-backed Main Computer features will need WSL installed."
        Write-RequirementLog -LogPath $LogPath -Message "Install WSL with: wsl --install"
        return
    }

    Write-RequirementLog -LogPath $LogPath -Message "WSL: $wsl"
    Invoke-ToolCapture -LogPath $LogPath -FilePath $wsl -Arguments @("--status") | Out-Null
    Invoke-ToolCapture -LogPath $LogPath -FilePath $wsl -Arguments @("--list", "--verbose") | Out-Null
}

function Invoke-HostRequirementPreparation {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][ValidateSet("docker", "podman")][string]$SelectedRuntime,
        [switch]$SkipDockerRequirement,
        [switch]$SkipHostRequirementInstall
    )

    Write-LogBlock -LogPath $LogPath -Title "host requirements" -Text "Checking container runtime, Git, OpenSSH Client, Ollama, and WSL host requirements."
    Write-RequirementLog -LogPath $LogPath -Message ""
    Write-RequirementLog -LogPath $LogPath -Message "Checking Main Computer host requirements."

    if ($SkipDockerRequirement) {
        Write-RequirementLog -LogPath $LogPath -Message "Skipping container runtime requirement check because -SkipDockerRequirement was set."
    }
    else {
        if ($SelectedRuntime -eq "podman") {
            Ensure-PodmanRequirement -LogPath $LogPath
        }
        Assert-ContainerRuntimeRequirement -LogPath $LogPath -SelectedRuntime $SelectedRuntime
    }

    if ($SkipHostRequirementInstall) {
        Write-RequirementLog -LogPath $LogPath -Message "Skipping Git/OpenSSH/Ollama install/repair because -SkipHostRequirementInstall was set."
    }
    else {
        Ensure-GitRequirement -LogPath $LogPath
        Ensure-OpenSSHClientRequirement -LogPath $LogPath
        Ensure-OllamaRequirement -LogPath $LogPath
    }

    Check-WslRequirement -LogPath $LogPath
    Write-RequirementLog -LogPath $LogPath -Message "Host requirement preparation complete."
    Write-RequirementLog -LogPath $LogPath -Message ""
}

$packageRoot = Resolve-FullPath (Split-Path -Parent $MyInvocation.MyCommand.Path)
$payloadRoot = Join-Path $packageRoot "payload\main_computer_test"
$bootstrapScript = Join-Path $payloadRoot "bootstrap-main-computer-python-windows.ps1"
$logDir = Join-Path $packageRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "main-computer-python-installer-$timestamp.log"
$stdoutPath = Join-Path $logDir "main-computer-python-installer-$timestamp.stdout.txt"
$stderrPath = Join-Path $logDir "main-computer-python-installer-$timestamp.stderr.txt"

Write-Host "Main Computer package root: $packageRoot"
Write-Host "Main Computer wrapper log: $logPath"

Write-LogLine -LogPath $logPath -Message "Main Computer NSIS package installer v7"
Write-LogLine -LogPath $logPath -Message "Timestamp UTC: $((Get-Date).ToUniversalTime().ToString('o'))"
Write-LogLine -LogPath $logPath -Message "Package root:  $packageRoot"
Write-LogLine -LogPath $logPath -Message "Payload root:  $payloadRoot"
Write-LogLine -LogPath $logPath -Message "Bootstrap:     $bootstrapScript"
Write-LogLine -LogPath $logPath -Message "Container runtime selected by installer UI/parameter: $ContainerRuntime"
$EffectiveContainerRuntime = Resolve-InstallerContainerRuntime -LogPath $logPath -SelectedRuntime $ContainerRuntime
Write-LogLine -LogPath $logPath -Message "Effective container runtime for this install: $EffectiveContainerRuntime"

Invoke-HostRequirementPreparation -LogPath $logPath -SelectedRuntime $EffectiveContainerRuntime -SkipDockerRequirement:$SkipDockerRequirement -SkipHostRequirementInstall:$SkipHostRequirementInstall

if (-not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    Write-LogLine -LogPath $logPath -Message "ERROR: Packaged payload directory was not found: $payloadRoot"
    Fail "Packaged payload directory was not found: $payloadRoot"
}
if (-not (Test-Path -LiteralPath $bootstrapScript -PathType Leaf)) {
    Write-LogLine -LogPath $logPath -Message "ERROR: Packaged Python bootstrap script was not found: $bootstrapScript"
    Fail "Packaged Python bootstrap script was not found: $bootstrapScript"
}

$bootstrapArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $bootstrapScript,
    "-RepoRoot", $payloadRoot,
    "-RuntimeProfile", $RuntimeProfile,
    "-Mode", $Mode,
    "-ContainerRuntime", $EffectiveContainerRuntime
)

if (-not [string]::IsNullOrWhiteSpace($InstallRoot)) {
    $bootstrapArgs += @("-InstallRoot", $InstallRoot)
}
if (-not $AllowReHome) {
    $bootstrapArgs += "-NoReHome"
}
if ($SkipAppStart) {
    $bootstrapArgs += "-SkipAppStart"
}
if ($PrecheckOnly) {
    $bootstrapArgs += "-PrecheckOnly"
}
if ($NoPythonDownload) {
    $bootstrapArgs += "-NoPythonDownload"
}
if ($VerboseBootstrap) {
    $bootstrapArgs += "-VerboseBootstrap"
}
if ($RemainingBootstrapArgs.Count -gt 0) {
    $bootstrapArgs += $RemainingBootstrapArgs
}

$powershellCommand = (Get-Command "powershell.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($null -eq $powershellCommand -or [string]::IsNullOrWhiteSpace($powershellCommand.Source)) {
    Write-LogLine -LogPath $logPath -Message "ERROR: powershell.exe was not found on PATH."
    Fail "powershell.exe was not found on PATH."
}

$argumentLine = ($bootstrapArgs | ForEach-Object { Quote-ProcessArgument ([string]$_) }) -join " "

# The installer-selected runtime must win over stale inherited environment
# variables.  The Python bootstrap and installed services still receive an
# explicit --container-runtime argument, but the lower-level resolver also
# honors MAIN_COMPUTER_CONTAINER_RUNTIME for spawned helpers.
Write-LogLine -LogPath $logPath -Message "Fencing container runtime environment for Python bootstrap child: MAIN_COMPUTER_CONTAINER_RUNTIME=$EffectiveContainerRuntime"
$env:MAIN_COMPUTER_CONTAINER_RUNTIME = $EffectiveContainerRuntime
foreach ($containerOverrideName in @(
    "MAIN_COMPUTER_CONTAINER_COMMAND",
    "MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND",
    "MAIN_COMPUTER_DOCKER_COMMAND",
    "MAIN_COMPUTER_DOCKER",
    "MAIN_COMPUTER_DOCKER_COMPOSE",
    "MAIN_COMPUTER_DOCKER_COMPOSE_COMMAND"
)) {
    if (-not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($containerOverrideName, "Process"))) {
        Write-LogLine -LogPath $logPath -Message "Ignoring inherited $containerOverrideName because installer selected container runtime $EffectiveContainerRuntime."
    }
    [Environment]::SetEnvironmentVariable($containerOverrideName, $null, "Process")
}

Write-Host "Running packaged Main Computer Python installer from:"
Write-Host "  $payloadRoot"
Write-Host "Python bootstrap log:"
Write-Host "  $logPath"
Write-Host "Selected container runtime:"
Write-Host "  $ContainerRuntime"
Write-Host "Effective container runtime:"
Write-Host "  $EffectiveContainerRuntime"
Write-Host ""

Write-LogBlock -LogPath $logPath -Title "command" -Text "$($powershellCommand.Source) $argumentLine"

$process = Start-Process `
    -FilePath $powershellCommand.Source `
    -ArgumentList $argumentLine `
    -Wait `
    -PassThru `
    -NoNewWindow `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath

$exitCode = [int]$process.ExitCode

$stdoutText = ""
$stderrText = ""
if (Test-Path -LiteralPath $stdoutPath -PathType Leaf) {
    $stdoutText = Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
    $stderrText = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
}

$combinedBootstrapOutput = "$stdoutText`n$stderrText"
if ($exitCode -ne 0 -and $combinedBootstrapOutput.IndexOf("Archive verification failed", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
    Write-Host ""
    Write-Host "Detected install-root archive verification failure; returning installer-specific exit code 61."
    Write-LogLine -LogPath $logPath -Message "Detected install-root archive verification failure; returning installer-specific exit code 61."
    $exitCode = 61
}
elseif ($exitCode -ne 0 -and $combinedBootstrapOutput.IndexOf("Could not archive existing install root", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
    Write-Host ""
    Write-Host "Detected install-root archive preservation failure; returning installer-specific exit code 61."
    Write-LogLine -LogPath $logPath -Message "Detected install-root archive preservation failure; returning installer-specific exit code 61."
    $exitCode = 61
}
elseif ($exitCode -ne 0 -and $combinedBootstrapOutput.IndexOf("ContainerRuntimeResolutionError", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
    Write-Host ""
    Write-Host "Detected container runtime resolution failure; returning installer-specific exit code 43."
    Write-LogLine -LogPath $logPath -Message "Detected container runtime resolution failure; returning installer-specific exit code 43."
    $exitCode = 43
}
elseif ($exitCode -ne 0 -and $combinedBootstrapOutput.IndexOf("MAIN_COMPUTER_CONTAINER_RUNTIME=", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
    Write-Host ""
    Write-Host "Detected container runtime selection failure; returning installer-specific exit code 43."
    Write-LogLine -LogPath $logPath -Message "Detected container runtime selection failure; returning installer-specific exit code 43."
    $exitCode = 43
}

Write-LogBlock -LogPath $logPath -Title "stdout" -Text $stdoutText
Write-LogBlock -LogPath $logPath -Title "stderr" -Text $stderrText
Write-LogBlock -LogPath $logPath -Title "exit" -Text "Exit code: $exitCode"

if (-not [string]::IsNullOrWhiteSpace($stdoutText)) {
    Write-Host $stdoutText
}
if (-not [string]::IsNullOrWhiteSpace($stderrText)) {
    Write-Host ""
    Write-Host "[stderr]"
    Write-Host $stderrText
}

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Main Computer Python installer failed with exit code $exitCode."
    Write-Host "Log file: $logPath"
}
else {
    Write-Host ""
    Write-Host "Main Computer Python installer completed successfully."
    Write-Host "Log file: $logPath"
}

exit $exitCode

'@

    $parent = Split-Path -Parent $WrapperPath
    New-DirectoryLongPath -Path $parent
    $wrapper | Set-Content -LiteralPath $WrapperPath -Encoding UTF8
}

function Write-NsisDefinition {
    param([Parameter(Mandatory = $true)][string]$NsiPath)

    $nsi = @'
; MainComputer.experimental-v7.generated.nsi
;
; Generated by build-main-computer-nsis-installer.experimental-v7.ps1.
; This keeps the NSIS experiment additive and avoids depending on an external
; installer definition while the installer flow is still being proven.
;
; v7 keeps v6 mode/shortcut behavior, checks host requirements, and uses requirements.txt during the Python install path. It keeps the real installer question page for Unleashed/Debug/Safe mode, adds a separate required Docker/Podman runtime page, and
; creates a user-visible shortcut that starts the installed tree with
; start_v2.bat -OpenBrowser.

!ifndef MainComputerVersion
  !define MainComputerVersion "0.1.0"
!endif

!ifndef StageRoot
  !error "StageRoot must be passed by build-main-computer-nsis-installer.experimental-v7.ps1"
!endif

!ifndef OutputRoot
  !error "OutputRoot must be passed by build-main-computer-nsis-installer.experimental-v7.ps1"
!endif

!include "LogicLib.nsh"
!include "nsDialogs.nsh"

Unicode true
Name "Main Computer"
OutFile "${OutputRoot}\MainComputer-${MainComputerVersion}-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\Main Computer"
RequestExecutionLevel user
ShowInstDetails show
ShowUninstDetails show
SetCompressor /SOLID lzma
XPStyle on

Var ModeDialog
Var ModeRadioUnleashed
Var ModeRadioDebug
Var ModeRadioSafe
Var DesktopShortcutCheckbox
Var InstallModeKey
Var InstallModeArg
Var ShortcutModeName
Var ResolvedInstallRoot
Var ShouldCreateDesktopShortcut
Var ContainerRuntimeDialog
Var ContainerRuntimeRadioDocker
Var ContainerRuntimeRadioPodman
Var ContainerRuntimeArg
Var ContainerRuntimeName

Page custom ModePage ModePageLeave
Page custom ContainerRuntimePage ContainerRuntimePageLeave
Page instfiles

Function .onInit
  StrCpy $InstallModeKey "unleashed"
  StrCpy $InstallModeArg "Unleashed"
  StrCpy $ShortcutModeName "Unleashed"
  StrCpy $ResolvedInstallRoot "$PROFILE\.main-computer-tools\installs\main_computer_test-test-unleashed"
  StrCpy $ShouldCreateDesktopShortcut "1"
  StrCpy $ContainerRuntimeArg ""
  StrCpy $ContainerRuntimeName ""
FunctionEnd

Function ResolveSelectedInstallRoot
  StrCpy $ResolvedInstallRoot "$PROFILE\.main-computer-tools\installs\main_computer_test-test-$InstallModeKey"
FunctionEnd

Function ModePage
  nsDialogs::Create 1018
  Pop $ModeDialog

  ${If} $ModeDialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 16u "Choose the Main Computer install mode."
  Pop $0

  ${NSD_CreateLabel} 0 20u 100% 10u "Install mode"
  Pop $0

  ${NSD_CreateRadioButton} 0 34u 100% 12u "Main Computer - Unleashed"
  Pop $ModeRadioUnleashed

  ${NSD_CreateRadioButton} 0 52u 100% 12u "Main Computer - Debug"
  Pop $ModeRadioDebug

  ${NSD_CreateRadioButton} 0 70u 100% 12u "Main Computer - Safe"
  Pop $ModeRadioSafe

  ${NSD_CreateCheckbox} 0 92u 100% 12u "Create a desktop shortcut that starts Main Computer and opens the browser"
  Pop $DesktopShortcutCheckbox

  ${If} $InstallModeKey == "debug"
    ${NSD_Check} $ModeRadioDebug
  ${ElseIf} $InstallModeKey == "safe"
    ${NSD_Check} $ModeRadioSafe
  ${Else}
    ${NSD_Check} $ModeRadioUnleashed
  ${EndIf}

  ${If} $ShouldCreateDesktopShortcut == "1"
    ${NSD_Check} $DesktopShortcutCheckbox
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function ModePageLeave
  ${NSD_GetState} $ModeRadioDebug $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $InstallModeKey "debug"
    StrCpy $InstallModeArg "Debug"
    StrCpy $ShortcutModeName "Debug"
  ${Else}
    ${NSD_GetState} $ModeRadioSafe $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $InstallModeKey "safe"
      StrCpy $InstallModeArg "Safe"
      StrCpy $ShortcutModeName "Safe"
    ${Else}
      StrCpy $InstallModeKey "unleashed"
      StrCpy $InstallModeArg "Unleashed"
      StrCpy $ShortcutModeName "Unleashed"
    ${EndIf}
  ${EndIf}

  ${NSD_GetState} $DesktopShortcutCheckbox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $ShouldCreateDesktopShortcut "1"
  ${Else}
    StrCpy $ShouldCreateDesktopShortcut "0"
  ${EndIf}

  Call ResolveSelectedInstallRoot
FunctionEnd

Function ContainerRuntimePage
  nsDialogs::Create 1018
  Pop $ContainerRuntimeDialog

  ${If} $ContainerRuntimeDialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 16u "Choose the Docker-compatible container runtime Main Computer should use."
  Pop $0

  ${NSD_CreateLabel} 0 22u 100% 20u "This installer will not auto-detect here. Pick Docker Desktop or Podman before files are installed."
  Pop $0

  ${NSD_CreateRadioButton} 0 54u 100% 12u "Docker Desktop"
  Pop $ContainerRuntimeRadioDocker

  ${NSD_CreateRadioButton} 0 76u 100% 12u "Podman"
  Pop $ContainerRuntimeRadioPodman

  ${If} $ContainerRuntimeArg == "docker"
    ${NSD_Check} $ContainerRuntimeRadioDocker
  ${ElseIf} $ContainerRuntimeArg == "podman"
    ${NSD_Check} $ContainerRuntimeRadioPodman
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function ContainerRuntimePageLeave
  ${NSD_GetState} $ContainerRuntimeRadioDocker $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $ContainerRuntimeArg "docker"
    StrCpy $ContainerRuntimeName "Docker Desktop"
    Return
  ${EndIf}

  ${NSD_GetState} $ContainerRuntimeRadioPodman $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $ContainerRuntimeArg "podman"
    StrCpy $ContainerRuntimeName "Podman"
    Return
  ${EndIf}

  MessageBox MB_ICONEXCLAMATION "Choose Docker Desktop or Podman before continuing."
  Abort
FunctionEnd

Function CreateMainComputerModeShortcuts
  SetShellVarContext current

  CreateDirectory "$SMPROGRAMS\Main Computer"
  CreateShortCut "$SMPROGRAMS\Main Computer\Main Computer - $ShortcutModeName.lnk" "$ResolvedInstallRoot\start_v2.bat" "-OpenBrowser" "$ResolvedInstallRoot\start_v2.bat" 0

  ${If} $ShouldCreateDesktopShortcut == "1"
    CreateShortCut "$DESKTOP\Main Computer - $ShortcutModeName.lnk" "$ResolvedInstallRoot\start_v2.bat" "-OpenBrowser" "$ResolvedInstallRoot\start_v2.bat" 0
  ${EndIf}
FunctionEnd

Function un.RemoveMainComputerModeShortcuts
  SetShellVarContext current

  Delete "$SMPROGRAMS\Main Computer\Main Computer - Unleashed.lnk"
  Delete "$SMPROGRAMS\Main Computer\Main Computer - Debug.lnk"
  Delete "$SMPROGRAMS\Main Computer\Main Computer - Safe.lnk"

  Delete "$DESKTOP\Main Computer - Unleashed.lnk"
  Delete "$DESKTOP\Main Computer - Debug.lnk"
  Delete "$DESKTOP\Main Computer - Safe.lnk"
FunctionEnd

Section "Install Main Computer package files" SecInstall
  SetShellVarContext current
  SetOutPath "$INSTDIR"

  DetailPrint "Installing Main Computer package files to $INSTDIR"
  DetailPrint "Selected mode: $ShortcutModeName"
  DetailPrint "Container runtime selection: $ContainerRuntimeName"
  DetailPrint "Resolved install root: $ResolvedInstallRoot"

  RMDir /r "$INSTDIR\payload\main_computer_test"
  Delete "$INSTDIR\Install-MainComputer-from-Package.nsis-experimental-v7.ps1"
  Delete "$INSTDIR\installer-package.json"

  File "${StageRoot}\Install-MainComputer-from-Package.nsis-experimental-v7.ps1"
  File "${StageRoot}\installer-package.json"

  SetOutPath "$INSTDIR\payload"
  File /r "${StageRoot}\payload\main_computer_test"

  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Run Main Computer Python installer" SecBootstrap
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  Call ResolveSelectedInstallRoot

  DetailPrint "Checking host requirements and running the packaged Main Computer Python installer."
  DetailPrint "Mode: $ShortcutModeName"
  DetailPrint "Container runtime selection: $ContainerRuntimeName"
  DetailPrint "Install root: $ResolvedInstallRoot"
  DetailPrint "Installer diagnostics will be written under: $INSTDIR\logs"
  DetailPrint "A Docker-compatible container runtime is required. Git, OpenSSH Client, and Ollama will be installed or repaired when missing."

  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\Install-MainComputer-from-Package.nsis-experimental-v7.ps1" -RuntimeProfile test -Mode "$InstallModeArg" -ContainerRuntime "$ContainerRuntimeArg" -InstallRoot "$ResolvedInstallRoot" -VerboseBootstrap'
  Pop $0

  StrCmp $0 "0" bootstrap_ok
  StrCmp $0 "43" bootstrap_runtime_failed
  StrCmp $0 "61" bootstrap_archive_failed
    MessageBox MB_ICONSTOP "Main Computer installer failed with exit code $0.$\r$\n$\r$\nThis was not classified as a container runtime failure. See installer details and log files under:$\r$\n$INSTDIR\logs"
    Abort "Main Computer installer failed with exit code $0. See $INSTDIR\logs."

  bootstrap_runtime_failed:
    MessageBox MB_ICONSTOP "Main Computer installer failed with exit code $0.$\r$\n$\r$\nContainer runtime selection: $ContainerRuntimeName.$\r$\nNo selected Docker-compatible container runtime is ready.$\r$\n$\r$\nDocker Desktop: install/start Docker Desktop for Windows.$\r$\nPodman: install/initialize Podman, then select Podman on the runtime page.$\r$\n$\r$\nSee installer details and log files under:$\r$\n$INSTDIR\logs"
    Abort "Main Computer installer failed with exit code $0. See $INSTDIR\logs."

  bootstrap_archive_failed:
    MessageBox MB_ICONSTOP "Main Computer installer failed with exit code $0.$\r$\n$\r$\nThe existing install root could not be archived and verified before replacement, so it was left in place.$\r$\n$\r$\nThis is not a Docker/Podman runtime failure. Close running Main Computer windows and rerun the installer, or remove the managed install slot manually after backing up anything you need.$\r$\n$\r$\nSee installer details and log files under:$\r$\n$INSTDIR\logs"
    Abort "Main Computer installer failed with exit code $0. See $INSTDIR\logs."

  bootstrap_ok:

  DetailPrint "Creating shortcut: Main Computer - $ShortcutModeName"
  DetailPrint "Shortcut target: $ResolvedInstallRoot\start_v2.bat -OpenBrowser"
  Call CreateMainComputerModeShortcuts
SectionEnd

Section "Uninstall"
  SetShellVarContext current

  Call un.RemoveMainComputerModeShortcuts
  RMDir "$SMPROGRAMS\Main Computer"

  Delete "$INSTDIR\Install-MainComputer-from-Package.nsis-experimental-v7.ps1"
  Delete "$INSTDIR\installer-package.json"
  RMDir /r "$INSTDIR\payload"

  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd

'@

    $parent = Split-Path -Parent $NsiPath
    New-DirectoryLongPath -Path $parent
    $nsi | Set-Content -LiteralPath $NsiPath -Encoding ASCII
}

$repoRoot = Resolve-FullPath (Get-RepoRoot)
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Get-PyProjectVersion -RepoRoot $repoRoot
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot "release_reports\installer-nsis-experimental-v7"
}
if ([string]::IsNullOrWhiteSpace($StageRoot)) {
    # Keep the NSIS source tree short. NSIS opens each source file while
    # compiling, and long repository-relative history/report paths can still
    # fail even when PowerShell/.NET can stage them successfully.
    $shortStageBase = Join-Path ([System.IO.Path]::GetTempPath()) "mc-nsis-v7"
    $StageRoot = Join-Path $shortStageBase "stage"
}

$outputRootFull = Resolve-FullPath $OutputRoot
$stageRootFull = Resolve-FullPath $StageRoot
$payloadRoot = Join-Path $stageRootFull "payload\main_computer_test"
$nsiDefinition = Join-Path $stageRootFull "MainComputer.experimental-v7.generated.nsi"
$wrapperPath = Join-Path $stageRootFull "Install-MainComputer-from-Package.nsis-experimental-v7.ps1"

$compiler = Resolve-MakeNsisCompiler -RequestedCompiler $MakeNsisCompiler

Write-Section "Preparing experimental NSIS installer payload (v7)"
if (Test-Path -LiteralPath $stageRootFull) {
    Remove-DirectoryLongPath -Path $stageRootFull
}
New-DirectoryLongPath -Path $stageRootFull
New-DirectoryLongPath -Path $outputRootFull

Copy-RepoPayload -RepoRoot $repoRoot -PayloadRoot $payloadRoot
$payloadIntegrity = Assert-RepoPayloadStagingIntegrity -RepoRoot $repoRoot -PayloadRoot $payloadRoot
Write-PackageWrapper -WrapperPath $wrapperPath
Write-NsisDefinition -NsiPath $nsiDefinition

$packageJson = [ordered]@{
    name = "Main Computer"
    version = $Version
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    mode = "nsis-experimental-v7"
    builder = "scripts/windows/build-main-computer-nsis-installer.experimental-v7.ps1"
    nsisCompiler = $compiler.Path
    nsisVersion = $compiler.Version
    payloadRoot = "payload/main_computer_test"
    payloadIntegrityVerified = $payloadIntegrity
    hostRequirementsPolicy = [ordered]@{
        containerRuntime = "required; installer shows a separate required Docker Desktop or Podman choice page; setup-maker environment variables are ignored; fail with runtime-specific install guidance when unusable"
        podmanCompose = "installed into the Main Computer Python venv from requirements.txt and pinned through PODMAN_COMPOSE_PROVIDER when Podman is selected so Podman does not delegate Compose to Docker Desktop"
        git = "install or repair with winget when missing"
        opensshClient = "install or repair Windows OpenSSH Client capability when ssh/scp/ssh-keygen are missing"
        ollama = "install or repair with winget when missing; warn if local API is not reachable after install"
        wsl = "check and log status; warn when missing"
        python = "bootstrap installs requirements.txt before editable project install and then runs pip check"
    }
    excludedGeneratedTrees = @(
        "revision_control",
        "tools/patching",
        "tools/patching/reports",
        "new_patch_runs",
        "diagnostics_output*",
        "ollama_prompt_space_*",
        "harness_output*",
        "release_reports",
        "runtime",
        "energy_credits"
    )
} | ConvertTo-Json -Depth 6
$packageJson | Set-Content -LiteralPath (Join-Path $stageRootFull "installer-package.json") -Encoding UTF8

Write-Section "Compiling experimental NSIS setup EXE (v7)"
Write-Host "NSIS compiler: $($compiler.Path)"
Write-Host "NSIS version:  $($compiler.Version)"
Write-Host "Definition:    $nsiDefinition"
Write-Host "Output root:   $outputRootFull"

$expectedExe = Join-Path $outputRootFull "MainComputer-$Version-Setup.exe"
if (Test-Path -LiteralPath $expectedExe -PathType Leaf) {
    Write-Host "Removing stale setup EXE before compile: $expectedExe"
    Remove-Item -LiteralPath $expectedExe -Force
}

$nsisArgs = @(
    "/V2",
    "/DMainComputerVersion=$Version",
    "/DStageRoot=$stageRootFull",
    "/DOutputRoot=$outputRootFull",
    $nsiDefinition
)

& $compiler.Path @nsisArgs
if ($LASTEXITCODE -ne 0) {
    Fail "makensis failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path -LiteralPath $expectedExe -PathType Leaf)) {
    Fail "NSIS completed but the expected setup EXE was not found: $expectedExe"
}

Write-Section "Experimental NSIS installer ready (v7)"
Write-Host $expectedExe
