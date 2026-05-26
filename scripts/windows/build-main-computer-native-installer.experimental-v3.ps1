[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$OutputRoot = "",
    [string]$StageRoot = "",
    [string]$InnoSetupCompiler = ""
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
    throw "[main-computer-native-installer-experimental-v3] $Message"
}

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

function Convert-ToLongPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
        return $Path
    }

    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full.StartsWith("\\?\", [System.StringComparison]::Ordinal)) {
        return $full
    }
    if ($full.StartsWith("\\", [System.StringComparison]::Ordinal)) {
        return "\\?\UNC\" + $full.Substring(2)
    }
    return "\\?\" + $full
}

function New-DirectoryLongPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    [System.IO.Directory]::CreateDirectory((Convert-ToLongPath $Path)) | Out-Null
}

function Copy-FileLongPath {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $destinationParent = Split-Path -Parent $Destination
    if ([string]::IsNullOrWhiteSpace($destinationParent)) {
        Fail "Could not resolve destination parent for: $Destination"
    }

    New-DirectoryLongPath -Path $destinationParent
    [System.IO.File]::Copy((Convert-ToLongPath $Source), (Convert-ToLongPath $Destination), $true)
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

    $allowedExactPaths = @(
        "runtime/main-computer-runtime.json"
    )
    if ($allowedExactPaths -contains $repoPath) {
        return $false
    }

    $allowedDirectoryPaths = @(
        "runtime"
    )
    if ($IsDirectory -and ($allowedDirectoryPaths -contains $repoPath)) {
        return $false
    }

    $name = ($repoPath.Split("/") | Select-Object -Last 1)
    $parts = @($repoPath.Split("/") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

    $blockedExactPaths = @(
        ".prod.lock",
        "aider.log",
        ".main-computer-install-archives",
        ".main-computer-tools",
        "energy_credits",
        "release_reports",
        "generated_component_docs/work",
        "generated_component_docs/archive",
        "generated_component_docs/doc-build.json",
        "generated_component_docs/doc-health.json",
        "generated_component_docs/graph.json",
        "main_computer/.main_computer_browser_profile",
        "main_computer/debug_assets",
        "contracts/cache",
        "contracts/out"
    )
    if ($blockedExactPaths -contains $repoPath) {
        return $true
    }

    $blockedPrefixes = @(
        ".main-computer-install-archives/",
        ".main-computer-tools/",
        "runtime/",
        "energy_credits/",
        "release_reports/",
        "aider.log/",
        "generated_component_docs/work/",
        "generated_component_docs/archive/",
        "tools/documentation/plan-",
        "main_computer/.main_computer_browser_profile/",
        "main_computer/debug_assets/",
        "contracts/cache/",
        "contracts/out/",
        "revision_control/",
        "tools/patching/"
    )

    foreach ($prefix in $blockedPrefixes) {
        $trimmedPrefix = $prefix.TrimEnd("/")
        if ($repoPath -ieq $trimmedPrefix -or $repoPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    $blockedDirectoryNames = @(
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
        ".main-computer",
        ".main-computer-install-archives",
        ".main-computer-tools",
        ".proto-dev",
        ".venv",
        "venv",
        "env",
        ".env",
        ".tox",
        ".nox",
        "aider_web_context",
        "chat_console_shared_variables",
        "debug_asset_revisions",
        "node_modules",
        ".next",
        "dist",
        "build",
        ".eggs",
        ".tmp",
        "harness_output",
        "harness_output_pretty_docs",
        "harness_output_game_editor",
        "migration",
        "rag_smoke_logpack_runs",
        "release_reports",
        "debug_assets",
        ".main_computer_browser_profile",
        "cache",
        "spreadsheets",
        "tmp_diag_server_debug",
        "tools - Copy",
        "diagnostics_output"
    )

    foreach ($part in $parts) {
        if ($blockedDirectoryNames -contains $part) {
            return $true
        }
        if ($part.EndsWith(".egg-info", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    $blockedDirectoryNamePrefixes = @(
        "diagnostics_output",
        "golden_path_diag_",
        "harness_output_",
        "ollama_prompt_space_"
    )

    foreach ($part in $parts) {
        foreach ($prefix in $blockedDirectoryNamePrefixes) {
            if ($part.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
    }

    if (-not $IsDirectory) {
        $blockedFileNames = @(
            ".DS_Store",
            "Thumbs.db",
            "aider.log",
            "small_aider.log",
            "solidity-files-cache.json"
        )
        if ($blockedFileNames -contains $name) {
            return $true
        }

        $blockedExtensions = @(
            ".pyc",
            ".pyo",
            ".tmp",
            ".bak",
            ".pid",
            ".map"
        )
        $extension = [System.IO.Path]::GetExtension($name)
        if ($blockedExtensions -contains $extension) {
            return $true
        }
    }

    return $false
}

function Test-LooksLikeNestedInstallRoot {
    param([Parameter(Mandatory = $true)][string]$Path)

    $envFile = Join-Path $Path "main-computer-env.ps1"
    $runnerFile = Join-Path $Path "run-main-computer.ps1"
    $launcherFile = Join-Path $Path "runtime\start_stop\main-computer-launcher.json"

    return (
        (Test-Path -LiteralPath $envFile -PathType Leaf) -and
        (
            (Test-Path -LiteralPath $runnerFile -PathType Leaf) -or
            (Test-Path -LiteralPath $launcherFile -PathType Leaf)
        )
    )
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

function Resolve-InnoSetupCompiler {
    param([string]$RequestedCompiler)

    if (-not [string]::IsNullOrWhiteSpace($RequestedCompiler)) {
        $candidate = Resolve-FullPath $RequestedCompiler
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            Fail "Requested Inno Setup compiler was not found: $candidate"
        }
        return $candidate
    }

    $command = Get-Command "ISCC.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
        return $command.Source
    }

    $wellKnownPaths = @()
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $wellKnownPaths += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $wellKnownPaths += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    }

    foreach ($candidate in $wellKnownPaths) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-FullPath $candidate)
        }
    }

    Fail "Inno Setup compiler not found. Install Inno Setup 6 or pass -InnoSetupCompiler."
}

function Copy-RepoPayload {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PayloadRoot
    )

    New-DirectoryLongPath -Path $PayloadRoot

    $copiedFiles = 0
    $skippedFiles = 0
    $skippedDirectories = 0
    $repoRootFull = Resolve-FullPath $RepoRoot

    $stack = New-Object "System.Collections.Generic.Stack[string]"
    $stack.Push($repoRootFull)

    while ($stack.Count -gt 0) {
        $currentDir = $stack.Pop()
        $children = @(Get-ChildItem -LiteralPath $currentDir -Force -ErrorAction Stop)

        $directories = @($children | Where-Object { $_.PSIsContainer } | Sort-Object FullName -Descending)
        foreach ($directory in $directories) {
            $relativeDirectory = Convert-ToRepoPath (Get-RelativePathCompat -BasePath $repoRootFull -FullPath $directory.FullName)
            if (Test-RepoPathSkipped -RepoPath $relativeDirectory -IsDirectory:$true) {
                $skippedDirectories += 1
                continue
            }
            if (Test-LooksLikeNestedInstallRoot -Path $directory.FullName) {
                $skippedDirectories += 1
                continue
            }

            $stack.Push($directory.FullName)
        }

        $files = @($children | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName)
        foreach ($file in $files) {
            $relative = Convert-ToRepoPath (Get-RelativePathCompat -BasePath $repoRootFull -FullPath $file.FullName)
            if (Test-RepoPathSkipped -RepoPath $relative -IsDirectory:$false) {
                $skippedFiles += 1
                continue
            }

            $destination = Join-Path $PayloadRoot ($relative -replace "/", [System.IO.Path]::DirectorySeparatorChar)
            Copy-FileLongPath -Source $file.FullName -Destination $destination
            $copiedFiles += 1
        }
    }

    Write-Host "Payload files copied:       $copiedFiles"
    Write-Host "Payload files skipped:      $skippedFiles"
    Write-Host "Payload directories skipped: $skippedDirectories"
}

function Write-EmbeddedInstallerWrapper {
    param([Parameter(Mandatory = $true)][string]$Destination)

    $content = @'
# Experimental native-installer package launcher.
#
# This wrapper is generated into the installer staging directory by
# build-main-computer-native-installer.experimental-v3.ps1. It delegates to the
# existing Python-owned bootstrap script inside the packaged payload.

[CmdletBinding()]
param(
    [ValidateSet("test", "prod")]
    [string]$RuntimeProfile = "prod",

    [string]$Mode = "Unleashed",

    [string]$InstallRoot = "",

    [switch]$AllowReHome,

    [switch]$SkipAppStart,

    [switch]$PrecheckOnly,

    [switch]$NoPythonDownload,

    [switch]$VerboseBootstrap,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingBootstrapArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw "[main-computer-package-installer] $Message"
}

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

$packageRoot = Resolve-FullPath (Split-Path -Parent $MyInvocation.MyCommand.Path)
$payloadRoot = Join-Path $packageRoot "payload\main_computer_test"
$bootstrapScript = Join-Path $payloadRoot "bootstrap-main-computer-python-windows.ps1"

if (-not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    Fail "Packaged payload directory was not found: $payloadRoot"
}
if (-not (Test-Path -LiteralPath $bootstrapScript -PathType Leaf)) {
    Fail "Packaged Python bootstrap script was not found: $bootstrapScript"
}

$bootstrapArgs = @(
    "-RepoRoot", $payloadRoot,
    "-RuntimeProfile", $RuntimeProfile,
    "-Mode", $Mode
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

Write-Host "Running packaged Main Computer Python installer from:"
Write-Host "  $payloadRoot"
Write-Host ""

& $bootstrapScript @bootstrapArgs
exit $LASTEXITCODE
'@

    $destinationParent = Split-Path -Parent $Destination
    New-DirectoryLongPath -Path $destinationParent
    Set-Content -LiteralPath $Destination -Value $content -Encoding UTF8
}

function Write-EmbeddedInnoDefinition {
    param([Parameter(Mandatory = $true)][string]$Destination)

    $content = @'
; MainComputer.experimental-v3.generated.iss
;
; Generated by build-main-computer-native-installer.experimental-v3.ps1.
; The builder is intentionally standalone while the native installer is being
; proven out, so it does not require replacing or editing existing installer
; source files.

#ifndef AppVersion
#define AppVersion "0.1.0"
#endif

#ifndef StageRoot
#error StageRoot must be passed by build-main-computer-native-installer.experimental-v3.ps1
#endif

#ifndef OutputDir
#error OutputDir must be passed by build-main-computer-native-installer.experimental-v3.ps1
#endif

[Setup]
AppId={{7D6089B2-A0C9-42D8-AE21-6E8F3F86B8B7}
AppName=Main Computer
AppVersion={#AppVersion}
AppPublisher=Main Computer
DefaultDirName={localappdata}\Programs\Main Computer
DefaultGroupName=Main Computer
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=MainComputer-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayName=Main Computer
SetupLogging=yes

[Files]
Source: "{#StageRoot}\Install-MainComputer-from-Package.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageRoot}\installer-package.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageRoot}\payload\main_computer_test\*"; DestDir: "{app}\payload\main_computer_test"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Main Computer\Run Main Computer Installer"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Install-MainComputer-from-Package.ps1"""; WorkingDir: "{app}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Install-MainComputer-from-Package.ps1"""; WorkingDir: "{app}"; Description: "Run the Main Computer Python installer"; Flags: postinstall skipifsilent unchecked
'@

    $destinationParent = Split-Path -Parent $Destination
    New-DirectoryLongPath -Path $destinationParent
    Set-Content -LiteralPath $Destination -Value $content -Encoding UTF8
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Get-PyProjectVersion -RepoRoot $repoRoot
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot "release_reports\installer-native-experimental-v3"
}
if ([string]::IsNullOrWhiteSpace($StageRoot)) {
    $StageRoot = Join-Path $OutputRoot "stage"
}

$outputRootFull = Resolve-FullPath $OutputRoot
$stageRootFull = Resolve-FullPath $StageRoot
$payloadRoot = Join-Path $stageRootFull "payload\main_computer_test"
$wrapperDestination = Join-Path $stageRootFull "Install-MainComputer-from-Package.ps1"
$innoDefinition = Join-Path $stageRootFull "MainComputer.experimental-v3.generated.iss"

Write-Section "Preparing experimental native installer payload (v3)"
if (Test-Path -LiteralPath $stageRootFull) {
    Remove-Item -LiteralPath $stageRootFull -Recurse -Force
}
New-DirectoryLongPath -Path $stageRootFull
New-DirectoryLongPath -Path $outputRootFull

Copy-RepoPayload -RepoRoot $repoRoot -PayloadRoot $payloadRoot
Write-EmbeddedInstallerWrapper -Destination $wrapperDestination
Write-EmbeddedInnoDefinition -Destination $innoDefinition

$packageJson = [ordered]@{
    name = "Main Computer"
    version = $Version
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    mode = "experimental-native-installer-v3-clean-payload"
    payloadRoot = "payload/main_computer_test"
    installerWrapper = "Install-MainComputer-from-Package.ps1"
    sourceRepo = $repoRoot
    payloadPolicy = "source-clean-blocklist-long-path-copy"
}
$packageJson | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stageRootFull "installer-package.json") -Encoding UTF8

Write-Section "Compiling experimental native installer (v3)"
$iscc = Resolve-InnoSetupCompiler -RequestedCompiler $InnoSetupCompiler
$isccArgs = @(
    "/Qp",
    "/DAppVersion=$Version",
    "/DStageRoot=$stageRootFull",
    "/DOutputDir=$outputRootFull",
    $innoDefinition
)

& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$setupPath = Join-Path $outputRootFull ("MainComputer-{0}-Setup.exe" -f $Version)
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    Fail "Expected setup executable was not produced: $setupPath"
}

Write-Host ""
Write-Host "Experimental native installer created by v3 builder:"
Write-Host "  $setupPath"
