[CmdletBinding()]
param(
  [string]$SourceRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path),
  [string]$ArchiveRoot = "",
  [string]$ProjectName = "main_computer_test",
  [Alias("ForInstallerReHome")]
  [switch]$InstallerReHome,
  [string]$InstallerReHomeRoot = "",
  [Alias("f")]
  [switch]$Follow,
  [ValidateRange(1, 3600)]
  [int]$FollowCalmSeconds = 4,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$RemainingArguments = @()
)

$ErrorActionPreference = "Stop"

$scriptDefaultSourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dashDashFollowWasSourceRoot = ($SourceRoot -ieq "--follow")

if ($dashDashFollowWasSourceRoot) {
  $Follow = $true
  $SourceRoot = $scriptDefaultSourceRoot
}

if ($RemainingArguments.Count -gt 0) {
  foreach ($argument in $RemainingArguments) {
    if ($argument -ieq "--follow") {
      $Follow = $true
      continue
    }

    if ($dashDashFollowWasSourceRoot -and $SourceRoot -eq $scriptDefaultSourceRoot -and -not $argument.StartsWith("-")) {
      $SourceRoot = $argument
      continue
    }

    throw "Unexpected argument for export-main-computer-test.ps1: $argument"
  }
}


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
    "runtime/main-computer-runtime.json",
    "runtime/deployments/dev/latest.json",
    "runtime/deployments/dev/latest.json",
    "runtime/deployments/test/latest.json",
    "runtime/deployments/testnet/latest.json",
    "runtime/deployments/mainnet/latest.json"
  )

  $allowedGeneratedPrefixes = @(
    "runtime/websites/hub-site/",
    "runtime/websites/johnrraymond/"
  )

  $blockedGeneratedExactPaths = @(
    "deploy/local-platform/generated/docker-compose.websites.yml"
  )

  $blockedGeneratedPrefixes = @(
    "deploy/local-platform/generated/"
  )

  $blockedGeneratedSuffixes = @(
    "/.main-computer/local-platform/docker-compose.yml"
  )

  if ($allowedGeneratedExactPaths -contains $repoPath) {
    return $true
  }

  $isAllowedGeneratedPrefixPath = $false
  foreach ($prefix in $allowedGeneratedPrefixes) {
    $prefixRoot = $prefix.TrimEnd("/")

    if ($repoPath -eq $prefixRoot -or $repoPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      $isAllowedGeneratedPrefixPath = $true
      break
    }
  }

  if ($blockedGeneratedExactPaths -contains $repoPath) {
    return $false
  }

  foreach ($prefix in $blockedGeneratedPrefixes) {
    $prefixRoot = $prefix.TrimEnd("/")

    if ($repoPath -eq $prefixRoot -or $repoPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $false
    }
  }

  foreach ($suffix in $blockedGeneratedSuffixes) {
    if ($repoPath.EndsWith($suffix, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $false
    }
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
    ".env",
    "aider.log",
    "small_aider.log",
    "solidity-files-cache.json",
    "ssh_password.local"
  )

  $blockedFileNamePrefixes = @(
    ".env."
  )

  $blockedExtensions = @(
    ".pyc",
    ".pyo",
    ".tmp",
    ".bak",
    ".pid",
    ".local"
  )

  if (($blockedExactPaths -contains $repoPath) -and -not $isAllowedGeneratedPrefixPath) {
    return $false
  }

  foreach ($prefix in $blockedPrefixes) {
    $prefixRoot = $prefix.TrimEnd("/")

    if (-not $isAllowedGeneratedPrefixPath -and $repoPath -eq $prefixRoot) {
      return $false
    }

    if (-not $isAllowedGeneratedPrefixPath -and $repoPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
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

    foreach ($prefix in $blockedFileNamePrefixes) {
      if ($name.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
      }
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

  $badFileNames = @(
    ".env",
    "ssh_password.local"
  )

  $badFileNamePrefixes = @(
    ".env."
  )

  $badExtensions = @(
    ".pyc",
    ".pyo",
    ".tmp",
    ".bak",
    ".pid",
    ".local"
  )

  $allowedGeneratedExactPaths = @(
    "runtime/main-computer-runtime.json",
    "runtime/deployments/dev/latest.json",
    "runtime/deployments/dev/latest.json",
    "runtime/deployments/test/latest.json",
    "runtime/deployments/testnet/latest.json",
    "runtime/deployments/mainnet/latest.json"
  )

  $allowedGeneratedPrefixes = @(
    "runtime/websites/hub-site/",
    "runtime/websites/johnrraymond/"
  )

  $blockedGeneratedExactPaths = @(
    "deploy/local-platform/generated/docker-compose.websites.yml"
  )

  $blockedGeneratedPrefixes = @(
    "deploy/local-platform/generated/"
  )

  $blockedGeneratedSuffixes = @(
    "/.main-computer/local-platform/docker-compose.yml"
  )

  $allowedGeneratedParentDirs = @(
    "runtime",
    "runtime/websites",
    "runtime/deployments",
    "runtime/deployments/dev",
    "runtime/deployments/test",
    "runtime/deployments/testnet",
    "runtime/deployments/mainnet"
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

    $isAllowedGeneratedPrefixPath = $false
    foreach ($prefix in $allowedGeneratedPrefixes) {
      $prefixRoot = $prefix.TrimEnd("/")

      if ($repoPath -eq $prefixRoot -or $repoPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $isAllowedGeneratedPrefixPath = $true
        break
      }
    }

    $isBlockedGeneratedPath = $false
    if ($blockedGeneratedExactPaths -contains $repoPath) {
      $isBlockedGeneratedPath = $true
    }

    foreach ($prefix in $blockedGeneratedPrefixes) {
      $prefixRoot = $prefix.TrimEnd("/")

      if ($repoPath -eq $prefixRoot -or $repoPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $isBlockedGeneratedPath = $true
        break
      }
    }

    foreach ($suffix in $blockedGeneratedSuffixes) {
      if ($repoPath.EndsWith($suffix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $isBlockedGeneratedPath = $true
        break
      }
    }

    if ($isBlockedGeneratedPath) {
      $bad.Add($repoPath)
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
      if (-not $isAllowedGeneratedPrefixPath -and ($repoPath -eq $pattern.TrimEnd("/") -or $repoPath.StartsWith($pattern, [System.StringComparison]::OrdinalIgnoreCase))) {
        $bad.Add($repoPath)
        return
      }
    }

    if (-not $_.PSIsContainer) {
      $name = $_.Name
      if ($badFileNames -contains $name) {
        $bad.Add($repoPath)
        return
      }

      foreach ($prefix in $badFileNamePrefixes) {
        if ($name.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
          $bad.Add($repoPath)
          return
        }
      }

      $extension = [System.IO.Path]::GetExtension($name)
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

if ($Follow -and $InstallerReHome) {
  throw "--follow cannot be combined with -InstallerReHome because installer rehome export uses a one-shot extracted run root."
}

if ((-not $InstallerReHome) -and [string]::IsNullOrWhiteSpace($ArchiveRoot)) {
  $ArchiveRoot = Join-Path (Split-Path -Parent $SourceRoot) "archive"
}

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
  "exp-fdb-hub.py",
  "exp-fdb-agent.py",
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
  "Dockerfile.hub.exp-fdb",
  "docker-compose.astrometric.yml",
  "docker/astrometric-renderer/",
  "run-exp-fdb-hub.py",
  ".githooks",
  "docker",
  "deploy/local-platform",
  "deploy/scheduler-lab/docker-compose.worker-lab.yml",
  "deploy/scheduler-lab/Dockerfile.simulator",
  "deploy/scheduler-lab/README.md",
  "deploy/coolify/local-docker",
  "deploy/coolify/push_site_scp.py",
  "deploy/hub-topology",
  "proto-dev",
  "control-main-computer.ps1",
  "dev-control.ps1",
  "prod-command.py",
  "export-main-computer-test.ps1",
  "tools",
  "scripts",
  "runtime/main-computer-runtime.json",
  "runtime/deployments/dev/latest.json",
  "runtime/deployments/dev/latest.json",
  "runtime/deployments/test/latest.json",
  "runtime/deployments/testnet/latest.json",
  "runtime/deployments/mainnet/latest.json",
  "runtime/websites/hub-site",
  "runtime/websites/johnrraymond",
  "diagnosis-docker-windows-host-paths-v5.ps1",
  "start.bat",
  "stop.bat",
  "start_v2.bat",
  "stop_v2.bat",
  "start.sh",
  "stop.sh",
  "coolify_twiddle.py",
  "coolify_deploy_twiddle.py",
  ".gitignore",
  "conftest.py"
)

function Test-PathInsideRoot {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,

    [Parameter(Mandatory = $true)]
    [string]$Root
  )

  if ([string]::IsNullOrWhiteSpace($Root)) {
    return $false
  }

  $fullPath = Resolve-FullPath $Path
  $fullRoot = Resolve-FullPath $Root

  if ($fullPath.Equals($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    return $true
  }

  $rootPrefix = $fullRoot + [System.IO.Path]::DirectorySeparatorChar
  return $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Convert-ToSourceRelativeRepoPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  if (-not (Test-PathInsideRoot -Path $Path -Root $sourceFull)) {
    return ""
  }

  $fullPath = Resolve-FullPath $Path
  $fullRoot = Resolve-FullPath $sourceFull

  if ($fullPath.Length -le $fullRoot.Length) {
    return ""
  }

  return Convert-ToRepoPath ($fullPath.Substring($fullRoot.Length).TrimStart("\", "/"))
}

function Test-FollowEventAllowed {
  param(
    [Parameter(Mandatory = $true)]
    $EventRecord
  )

  $eventArgs = $EventRecord.SourceEventArgs
  if ($null -eq $eventArgs) {
    return $false
  }

  $eventPath = $eventArgs.FullPath
  if ([string]::IsNullOrWhiteSpace($eventPath)) {
    return $false
  }

  if (-not [string]::IsNullOrWhiteSpace($ArchiveRoot)) {
    if (Test-PathInsideRoot -Path $eventPath -Root $ArchiveRoot) {
      return $false
    }
  }

  $repoPath = Convert-ToSourceRelativeRepoPath -Path $eventPath
  if ([string]::IsNullOrWhiteSpace($repoPath)) {
    return $false
  }

  $isDirectory = Test-Path -LiteralPath $eventPath -PathType Container
  return (Test-RepoPathAllowed -RepoPath $repoPath -IsDirectory:$isDirectory)
}

function Test-PathCoveredByRecursiveWatcher {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,

    [Parameter(Mandatory = $true)]
    [AllowEmptyCollection()]
    [System.Collections.Generic.List[string]]$RecursiveRoots
  )

  foreach ($root in $RecursiveRoots) {
    if (Test-PathInsideRoot -Path $Path -Root $root) {
      return $true
    }
  }

  return $false
}

function New-FollowFileWatcher {
  param(
    [Parameter(Mandatory = $true)]
    [string]$DirectoryPath,

    [Parameter(Mandatory = $true)]
    [string]$Filter,

    [bool]$IncludeSubdirectories = $false,

    [Parameter(Mandatory = $true)]
    [string]$SourceIdentifierPrefix,

    [Parameter(Mandatory = $true)]
    [AllowEmptyCollection()]
    [System.Collections.Generic.List[System.IO.FileSystemWatcher]]$Watchers,

    [Parameter(Mandatory = $true)]
    [AllowEmptyCollection()]
    [System.Collections.Generic.List[string]]$SubscriptionSourceIdentifiers,

    [Parameter(Mandatory = $true)]
    [ref]$Counter
  )

  $fullDirectoryPath = (Resolve-Path -LiteralPath $DirectoryPath).Path

  $watcher = New-Object System.IO.FileSystemWatcher
  $watcher.Path = $fullDirectoryPath
  $watcher.Filter = $Filter
  $watcher.IncludeSubdirectories = $IncludeSubdirectories
  $watcher.NotifyFilter = [System.IO.NotifyFilters]"FileName, DirectoryName, LastWrite, Size, CreationTime"
  $watcher.InternalBufferSize = 65536
  $watcher.EnableRaisingEvents = $false
  $Watchers.Add($watcher) | Out-Null

  foreach ($eventName in @("Changed", "Created", "Deleted", "Renamed")) {
    $sourceIdentifier = "{0}-{1}-{2}" -f $SourceIdentifierPrefix, $Counter.Value, $eventName
    $Counter.Value += 1

    Register-ObjectEvent `
      -InputObject $watcher `
      -EventName $eventName `
      -SourceIdentifier $sourceIdentifier | Out-Null

    $SubscriptionSourceIdentifiers.Add($sourceIdentifier) | Out-Null
  }

  $watcher.EnableRaisingEvents = $true
}

function Start-FollowExportLoop {
  $sourceIdentifierPrefix = "main-computer-export-follow-{0}" -f ([guid]::NewGuid().ToString("N"))
  $watchers = New-Object "System.Collections.Generic.List[System.IO.FileSystemWatcher]"
  $subscriptionSourceIdentifiers = New-Object "System.Collections.Generic.List[string]"
  $recursiveRoots = New-Object "System.Collections.Generic.List[string]"
  $counter = 0

  try {
    foreach ($relative in ($exportItems | Sort-Object { (Convert-ToRepoPath $_).Length })) {
      $repoPath = Convert-ToRepoPath $relative
      $path = Join-Path $sourceFull $repoPath

      if (Test-Path -LiteralPath $path -PathType Container) {
        $fullDirectoryPath = (Resolve-Path -LiteralPath $path).Path

        if (Test-PathCoveredByRecursiveWatcher -Path $fullDirectoryPath -RecursiveRoots $recursiveRoots) {
          continue
        }

        New-FollowFileWatcher `
          -DirectoryPath $fullDirectoryPath `
          -Filter "*" `
          -IncludeSubdirectories:$true `
          -SourceIdentifierPrefix $sourceIdentifierPrefix `
          -Watchers $watchers `
          -SubscriptionSourceIdentifiers $subscriptionSourceIdentifiers `
          -Counter ([ref]$counter)

        $recursiveRoots.Add((Resolve-FullPath $fullDirectoryPath)) | Out-Null
        continue
      }

      $parent = Split-Path -Parent $path
      $leaf = Split-Path -Leaf $path
      if ([string]::IsNullOrWhiteSpace($parent) -or [string]::IsNullOrWhiteSpace($leaf)) {
        continue
      }

      if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        continue
      }

      if (Test-PathCoveredByRecursiveWatcher -Path $path -RecursiveRoots $recursiveRoots) {
        continue
      }

      New-FollowFileWatcher `
        -DirectoryPath $parent `
        -Filter $leaf `
        -IncludeSubdirectories:$false `
        -SourceIdentifierPrefix $sourceIdentifierPrefix `
        -Watchers $watchers `
        -SubscriptionSourceIdentifiers $subscriptionSourceIdentifiers `
        -Counter ([ref]$counter)
    }

    if ($watchers.Count -eq 0) {
      throw "No export paths are available to watch under: $sourceFull"
    }

    Write-Host ("following export inputs under {0}" -f $sourceFull)
    Write-Host ("watchers: {0}" -f $watchers.Count)
    Write-Host ("quiet window: {0} second(s)" -f $FollowCalmSeconds)
    Write-Host "press Ctrl+C to stop following"

    $pendingExport = $false
    $quietUntilUtc = [DateTime]::MinValue

    while ($true) {
      $eventRecord = Wait-Event -Timeout 1

      if ($null -ne $eventRecord) {
        $sawRelevantEvent = $false

        while ($null -ne $eventRecord) {
          if (
            ($null -ne $eventRecord.SourceIdentifier) -and
            $eventRecord.SourceIdentifier.StartsWith($sourceIdentifierPrefix, [System.StringComparison]::Ordinal)
          ) {
            if (Test-FollowEventAllowed -EventRecord $eventRecord) {
              $sawRelevantEvent = $true
            }

            Remove-Event -EventIdentifier $eventRecord.EventIdentifier -ErrorAction SilentlyContinue
          }

          $eventRecord = Get-Event |
            Where-Object {
              ($null -ne $_.SourceIdentifier) -and
              $_.SourceIdentifier.StartsWith($sourceIdentifierPrefix, [System.StringComparison]::Ordinal)
            } |
            Select-Object -First 1
        }

        if ($sawRelevantEvent) {
          $pendingExport = $true
          $quietUntilUtc = [DateTime]::UtcNow.AddSeconds($FollowCalmSeconds)
          Write-Host ("detected export input changes; exporting after {0} quiet second(s)" -f $FollowCalmSeconds)
        }
      }

      if ($pendingExport -and ([DateTime]::UtcNow -ge $quietUntilUtc)) {
        Write-Host "quiet window elapsed; creating export"
        Invoke-ExportSnapshot
        $pendingExport = $false
      }
    }
  } finally {
    foreach ($sourceIdentifier in $subscriptionSourceIdentifiers) {
      if (-not [string]::IsNullOrWhiteSpace($sourceIdentifier)) {
        Unregister-Event -SourceIdentifier $sourceIdentifier -ErrorAction SilentlyContinue
      }
    }

    Get-Event |
      Where-Object {
        ($null -ne $_.SourceIdentifier) -and
        $_.SourceIdentifier.StartsWith($sourceIdentifierPrefix, [System.StringComparison]::Ordinal)
      } |
      ForEach-Object { Remove-Event -EventIdentifier $_.EventIdentifier -ErrorAction SilentlyContinue }

    foreach ($watcher in $watchers) {
      $watcher.EnableRaisingEvents = $false
      $watcher.Dispose()
    }
  }
}

function Invoke-ExportSnapshot {
  $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $nonce = [guid]::NewGuid().ToString("N").Substring(0, 8)
  $runArchiveRoot = $ArchiveRoot
  $installerReHomeRunRoot = ""
  $installerReHomeExtractRoot = ""
  $installerReHomeRepoRoot = ""

  if ($InstallerReHome) {
    $installerReHomeBaseRoot = Resolve-InstallerReHomeBaseRoot
    $installerReHomeRunRoot = Join-Path $installerReHomeBaseRoot $nonce
    if ([string]::IsNullOrWhiteSpace($runArchiveRoot)) {
      $ArchiveRoot = Join-Path $installerReHomeRunRoot "z"
      $runArchiveRoot = $ArchiveRoot
    }
    $installerReHomeExtractRoot = Join-Path $installerReHomeRunRoot "x"
    $installerReHomeRepoRoot = Join-Path $installerReHomeExtractRoot $ProjectName
  }
  elseif ([string]::IsNullOrWhiteSpace($runArchiveRoot)) {
    $runArchiveRoot = Join-Path (Split-Path -Parent $SourceRoot) "archive"
  }

  if (-not (Test-Path -LiteralPath $runArchiveRoot)) {
    New-Item -ItemType Directory -Path $runArchiveRoot -Force | Out-Null
  }

  $zipPath = Join-Path $runArchiveRoot ("{0}-{1}.zip" -f $ProjectName, $timestamp)

  if ($InstallerReHome) {
    $tempRoot = Join-Path $installerReHomeRunRoot "s"
  }
  else {
    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mct-{0}" -f $nonce)
  }
  $stageRoot = Join-Path $tempRoot $ProjectName

  New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

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
  } finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Invoke-ExportSnapshot

if ($Follow) {
  Start-FollowExportLoop
}

return
