[CmdletBinding()]
param(
  [string]$SourceRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path),
  [string]$ArchiveRoot = "",
  [string]$ProjectName = "main_computer_test",
  [Alias("ForInstallerReHome")]
  [switch]$InstallerReHome,
  [string]$InstallerReHomeRoot = ""
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

function Resolve-UserToolRoot {
  $userProfileRoot = [Environment]::GetFolderPath("UserProfile")
  if ([string]::IsNullOrWhiteSpace($userProfileRoot)) {
    $userProfileRoot = $env:USERPROFILE
  }
  if ([string]::IsNullOrWhiteSpace($userProfileRoot)) {
    throw "Could not determine the user profile directory for the Main Computer tool cache."
  }

  return (Join-Path (Resolve-FullPath $userProfileRoot) ".main-computer-tools")
}

function Resolve-InstallerReHomeBaseRoot {
  if (-not [string]::IsNullOrWhiteSpace($InstallerReHomeRoot)) {
    return (Resolve-FullPath $InstallerReHomeRoot)
  }

  $publicRoot = $env:PUBLIC
  if (-not [string]::IsNullOrWhiteSpace($publicRoot)) {
    return (Join-Path (Resolve-FullPath $publicRoot) "mcrh")
  }

  $systemDrive = $env:SystemDrive
  if (-not [string]::IsNullOrWhiteSpace($systemDrive)) {
    return (Join-Path (Join-Path $systemDrive "Users\Public") "mcrh")
  }

  return (Join-Path (Resolve-FullPath ([System.IO.Path]::GetTempPath())) "mcrh")
}

function Convert-ToRepoPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  $repoPath = ($Path -replace "\\", "/").Trim()

  while ($repoPath.StartsWith("./", [System.StringComparison]::Ordinal)) {
    $repoPath = $repoPath.Substring(2)
  }

  while ($repoPath.StartsWith("/", [System.StringComparison]::Ordinal)) {
    $repoPath = $repoPath.Substring(1)
  }

  return $repoPath
}

function Test-RepoPathAllowed {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,
    [bool]$IsDirectory = $false
  )

  $repoPath = Convert-ToRepoPath $RepoPath
  $name = Split-Path -Leaf $repoPath

  if (-not $repoPath) {
    return $true
  }

  $allowedGeneratedExactPaths = @(
    "runtime/main-computer-runtime.json"
  )

  if ($allowedGeneratedExactPaths -contains $repoPath) {
    return $true
  }

  $blockedDirectoryNames = @(
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".venv",
    "venv",
    "chat_console_shared_variables",
    "spreadsheets",
    ".tmp",
    "harness_output",
    "harness_output_pretty_docs",
    "harness_output_game_editor",
    "migration",
    "release_reports",
    "debug_assets",
    ".main_computer_browser_profile",
    "cache"
  )

  $blockedDirectoryNameSuffixes = @(
    ".egg-info"
  )

  $blockedExactPaths = @(
    ".prod.lock",
    "aider.log",
    "runtime",
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

  $blockedPrefixes = @(
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

  $blockedFileNames = @(
    ".DS_Store",
    "Thumbs.db",
    "aider.log",
    "small_aider.log",
    "solidity-files-cache.json"
  )

  $blockedExtensions = @(
    ".pyc",
    ".pyo",
    ".tmp",
    ".bak",
    ".pid"
  )

  if ($blockedExactPaths -contains $repoPath) {
    return $false
  }

  foreach ($prefix in $blockedPrefixes) {
    $prefixRoot = $prefix.TrimEnd("/")

    if ($repoPath -eq $prefixRoot) {
      return $false
    }

    if ($repoPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $false
    }
  }

  $parts = $repoPath -split "/+"
  foreach ($part in $parts) {
    if ($blockedDirectoryNames -contains $part) {
      return $false
    }

    foreach ($suffix in $blockedDirectoryNameSuffixes) {
      if ($part.EndsWith($suffix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
      }
    }
  }

  if (-not $IsDirectory) {
    if ($blockedFileNames -contains $name) {
      return $false
    }

    $extension = [System.IO.Path]::GetExtension($name)
    if ($blockedExtensions -contains $extension) {
      return $false
    }
  }

  return $true
}

function Copy-ExportFile {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [Parameter(Mandatory = $true)]
    [string]$DestinationPath
  )

  $destinationParent = Split-Path -Parent $DestinationPath

  if ($destinationParent -and -not (Test-Path -LiteralPath $destinationParent)) {
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
  }

  Copy-Item -LiteralPath $FilePath -Destination $DestinationPath -Force
}

function Copy-ExportDirectoryPruned {
  param(
    [Parameter(Mandatory = $true)]
    [string]$DirectoryPath,

    [Parameter(Mandatory = $true)]
    [string]$StageRoot,

    [Parameter(Mandatory = $true)]
    [string]$RepoRelativePath
  )

  foreach ($child in Get-ChildItem -LiteralPath $DirectoryPath -Force) {
    $childRepoPath = Convert-ToRepoPath (Join-Path $RepoRelativePath $child.Name)

    if (-not (Test-RepoPathAllowed -RepoPath $childRepoPath -IsDirectory:$child.PSIsContainer)) {
      continue
    }

    if ($child.PSIsContainer) {
      Copy-ExportDirectoryPruned `
        -DirectoryPath $child.FullName `
        -StageRoot $StageRoot `
        -RepoRelativePath $childRepoPath
    } else {
      $destination = Join-Path $StageRoot $childRepoPath
      Copy-ExportFile -FilePath $child.FullName -DestinationPath $destination
    }
  }
}

function Copy-ExportItem {
  param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [string]$StageRoot,

    [Parameter(Mandatory = $true)]
    [string]$RelativePath
  )

  $item = Get-Item -LiteralPath $SourcePath -Force
  $relativePath = Convert-ToRepoPath $RelativePath

  if (-not (Test-RepoPathAllowed -RepoPath $relativePath -IsDirectory:$item.PSIsContainer)) {
    return
  }

  if ($item.PSIsContainer) {
    Copy-ExportDirectoryPruned `
      -DirectoryPath $item.FullName `
      -StageRoot $StageRoot `
      -RepoRelativePath $relativePath

    return
  }

  $destination = Join-Path $StageRoot $relativePath
  Copy-ExportFile -FilePath $item.FullName -DestinationPath $destination
}

function Assert-CleanExportStage {
  param(
    [Parameter(Mandatory = $true)]
    [string]$StageRoot
  )

  $badDirectoryNames = @(
    "chat_console_shared_variables",
    "spreadsheets"
  )

  $badDirectoryNameSuffixes = @(
    ".egg-info"
  )

  $badPatterns = @(
    "aider.log",
    ".prod.lock",
    "runtime/",
    "energy_credits/",
    "release_reports/",
    "generated_component_docs/work/",
    "generated_component_docs/archive/",
    "generated_component_docs/doc-build.json",
    "generated_component_docs/doc-health.json",
    "generated_component_docs/graph.json",
    "tools/documentation/plan-",
    "contracts/out/",
    ".venv/",
    "venv/",
    ".git/",
    ".pytest_cache/",
    "__pycache__/",
    ".tmp/",
    "main_computer/.main_computer_browser_profile/",
    "main_computer/debug_assets/",
    "contracts/cache/",
    "debug_assets/",
    "revision_control/",
    "tools/patching/"
  )

  $badExtensions = @(
    ".pyc",
    ".pyo",
    ".tmp",
    ".bak",
    ".pid"
  )

  $allowedGeneratedExactPaths = @(
    "runtime/main-computer-runtime.json"
  )

  $allowedGeneratedParentDirs = @(
    "runtime"
  )

  $bad = New-Object System.Collections.Generic.List[string]

  Get-ChildItem -LiteralPath $StageRoot -Recurse -Force | ForEach-Object {
    $relative = $_.FullName.Substring($StageRoot.Length).TrimStart("\", "/")
    $repoPath = Convert-ToRepoPath $relative

    if ($allowedGeneratedExactPaths -contains $repoPath) {
      return
    }

    if ($_.PSIsContainer -and ($allowedGeneratedParentDirs -contains $repoPath)) {
      return
    }

    $parts = $repoPath -split "/+"
    foreach ($part in $parts) {
      if ($badDirectoryNames -contains $part) {
        $bad.Add($repoPath)
        return
      }

      foreach ($suffix in $badDirectoryNameSuffixes) {
        if ($part.EndsWith($suffix, [System.StringComparison]::OrdinalIgnoreCase)) {
          $bad.Add($repoPath)
          return
        }
      }
    }

    foreach ($pattern in $badPatterns) {
      if ($repoPath -eq $pattern.TrimEnd("/") -or $repoPath.StartsWith($pattern, [System.StringComparison]::OrdinalIgnoreCase)) {
        $bad.Add($repoPath)
        return
      }
    }

    if (-not $_.PSIsContainer) {
      $extension = [System.IO.Path]::GetExtension($_.Name)
      if ($badExtensions -contains $extension) {
        $bad.Add($repoPath)
        return
      }
    }
  }

  if ($bad.Count -gt 0) {
    $sample = ($bad | Select-Object -First 40) -join "`n  "
    throw "Export is dirty. Blocked paths were staged:`n  $sample"
  }
}

$sourceFull = (Resolve-Path -LiteralPath $SourceRoot).Path
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$nonce = [guid]::NewGuid().ToString("N").Substring(0, 8)
$installerReHomeExtractRoot = ""
$installerReHomeRepoRoot = ""

if ($InstallerReHome) {
  $installerReHomeBaseRoot = Resolve-InstallerReHomeBaseRoot
  $installerReHomeRunRoot = Join-Path $installerReHomeBaseRoot $nonce
  if ([string]::IsNullOrWhiteSpace($ArchiveRoot)) {
    $ArchiveRoot = Join-Path $installerReHomeRunRoot "z"
  }
  $installerReHomeExtractRoot = Join-Path $installerReHomeRunRoot "x"
  $installerReHomeRepoRoot = Join-Path $installerReHomeExtractRoot $ProjectName
}
elseif (-not $ArchiveRoot) {
  $ArchiveRoot = Join-Path (Split-Path -Parent $SourceRoot) "archive"
}

if (-not (Test-Path -LiteralPath $ArchiveRoot)) {
  New-Item -ItemType Directory -Path $ArchiveRoot -Force | Out-Null
}

$zipPath = Join-Path $ArchiveRoot ("{0}-{1}.zip" -f $ProjectName, $timestamp)

if ($InstallerReHome) {
  $tempRoot = Join-Path $installerReHomeRunRoot "s"
}
else {
  $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mct-{0}" -f $nonce)
}
$stageRoot = Join-Path $tempRoot $ProjectName

New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

$exportItems = @(
  "main_computer",
  "tests",
  "contracts",
  "dev-diagnosis.py",
  "dev-chain-diagnosis.py",
  "dev-chain-reset.py",
  "dev-chain-flow.py",
  "dev-chain-ledger-bridge.py",
  "dev-chain-wallet-smoke-guide.py",
  "docker-compose.executor.yml",
  "Dockerfile.full_executor",
  "Dockerfile.executor",
  "start-main-computer-docker-windows.ps1",
  "bootstrap-main-computer-windows.ps1",
  "bootstrap-main-computer-python-windows.ps1",
  "run-main-computer-test.ps1",
  "install-main-computer-python-target.ps1",
  "pretty_docs",
  "game_projects",
  "new_patch.py",
  "new_diff.py",
  "git-control.py",
  "git_dirty.py",
  "missing.txt",
  "README.md",
  "ENVIRONMENT.md",
  "TODO.md",
  "generated_component_docs",
  "pyproject.toml",
  "requirements.txt",
  ".dockerignore",
  "docker-compose.dev.yml",
  "docker-compose.onlyoffice.yml",
  "docker-compose.applications.yml",
  "docker-compose.gitea.yml",
  "docker",
  "deploy/local-platform",
  "deploy/coolify/local-docker",
  "proto-dev",
  "control-main-computer.ps1",
  "dev-control.ps1",
  "prod-command.py",
  "export-main-computer-test.ps1",
  "tools",
  "scripts",
  "runtime/main-computer-runtime.json",
  "diagnosis-docker-windows-host-paths-v5.ps1",
  "start.bat",
  "stop.bat",
  "start_v2.bat",
  "stop_v2.bat",
  "coolify_twiddle.py",
  "coolify_deploy_twiddle.py",
  ".gitignore",
  "conftest.py"
)

try {
  foreach ($relative in $exportItems) {
    $path = Join-Path $sourceFull $relative

    if (Test-Path -LiteralPath $path) {
      Copy-ExportItem -SourcePath $path -StageRoot $stageRoot -RelativePath $relative
    }
  }

  Assert-CleanExportStage -StageRoot $stageRoot

  if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
  }

  Compress-Archive -Path $stageRoot -DestinationPath $zipPath -Force

  $count = (Get-ChildItem -LiteralPath $stageRoot -Recurse -File -Force).Count

  Write-Host ("created {0}" -f $zipPath)
  Write-Host ("files: {0}" -f $count)
  Write-Host "clean export exclusions enforced"

  if ($InstallerReHome) {
    if (Test-Path -LiteralPath $installerReHomeExtractRoot) {
      Remove-Item -LiteralPath $installerReHomeExtractRoot -Recurse -Force
    }

    New-Item -ItemType Directory -Path $installerReHomeExtractRoot -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $installerReHomeExtractRoot)

    if (-not (Test-Path -LiteralPath $installerReHomeRepoRoot -PathType Container)) {
      throw "Installer rehome export did not extract the expected repository root: $installerReHomeRepoRoot"
    }

    $bootstrapDriver = Join-Path $installerReHomeRepoRoot "tools\bootstrap_main_computer.py"
    if (-not (Test-Path -LiteralPath $bootstrapDriver -PathType Leaf)) {
      throw "Installer rehome export is missing the Python bootstrap driver: $bootstrapDriver"
    }

    Write-Host ("extracted {0}" -f $installerReHomeRepoRoot)
    Write-Host "installer rehome export ready"
    Write-Output $installerReHomeRepoRoot
  }

  return
} finally {
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
