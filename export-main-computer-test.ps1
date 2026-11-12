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

# NanoJev development is exported separately as the nanojev component.
# Main Computer and game archives must not contain the nanojev/ tree. The
# allowlists below preserve the existing Main Computer runtime dependencies
# under tools/ and tests/, without reintroducing experiment-only files left
# behind during an interrupted relocation. Keep these lists explicit.
$retainedNanoJevToolPaths = @(
  "tools/nanojev_game_native_objectives_v6.py",
  "tools/nanojev_game_transformation_library.py",
  "tools/nanojev_gamer_v1_balanced_repair.py",
  "tools/nanojev_gamer_v1_boundary_isolation_v12.py",
  "tools/nanojev_gamer_v1_c0_boundary.py",
  "tools/nanojev_gamer_v1_c0_information_preservation.py",
  "tools/nanojev_gamer_v1_c0_sign_causality.py",
  "tools/nanojev_gamer_v1_c0_sign_head.py",
  "tools/nanojev_gamer_v1_candidate_span_causality_v27.py",
  "tools/nanojev_gamer_v1_capacity_retention_v11.py",
  "tools/nanojev_gamer_v1_catalog_extensions.py",
  "tools/nanojev_gamer_v1_causal_path.py",
  "tools/nanojev_gamer_v1_compositional_belief_lab.py",
  "tools/nanojev_gamer_v1_conflict_projection_v10.py",
  "tools/nanojev_gamer_v1_corrected_margin_v14.py",
  "tools/nanojev_gamer_v1_deconfounded_operand_audit.py",
  "tools/nanojev_gamer_v1_dual_sign_head.py",
  "tools/nanojev_gamer_v1_encoder_bypass.py",
  "tools/nanojev_gamer_v1_exact_belief_lab.py",
  "tools/nanojev_gamer_v1_expanded_head.py",
  "tools/nanojev_gamer_v1_generalization.py",
  "tools/nanojev_gamer_v1_gradient_sign_audit_v17.py",
  "tools/nanojev_gamer_v1_handoff.py",
  "tools/nanojev_gamer_v1_head_only_lab.py",
  "tools/nanojev_gamer_v1_information_localization.py",
  "tools/nanojev_gamer_v1_joint_constraint_v18.py",
  "tools/nanojev_gamer_v1_joint_head_repair.py",
  "tools/nanojev_gamer_v1_joint_sign_magnitude_lab.py",
  "tools/nanojev_gamer_v1_lora_sign_stress.py",
  "tools/nanojev_gamer_v1_margin_consolidation_v13.py",
  "tools/nanojev_gamer_v1_margin_feasibility_v15.py",
  "tools/nanojev_gamer_v1_math_retention_v9.py",
  "tools/nanojev_gamer_v1_math_transition_v8.py",
  "tools/nanojev_gamer_v1_mathematical_acquisition_v22.py",
  "tools/nanojev_gamer_v1_nonlinear_feasibility_v16.py",
  "tools/nanojev_gamer_v1_number_distance.py",
  "tools/nanojev_gamer_v1_operand_causality_v23.py",
  "tools/nanojev_gamer_v1_operand_span_causality_v26.py",
  "tools/nanojev_gamer_v1_operand_sufficiency_audit.py",
  "tools/nanojev_gamer_v1_operand_token_causality_v24.py",
  "tools/nanojev_gamer_v1_ordered_token_causality_v25.py",
  "tools/nanojev_gamer_v1_pairs.py",
  "tools/nanojev_gamer_v1_payoff_precision_lab.py",
  "tools/nanojev_gamer_v1_polarity_magnitude_repair.py",
  "tools/nanojev_gamer_v1_polarity_magnitude_v2.py",
  "tools/nanojev_gamer_v1_positional_digit_audit.py",
  "tools/nanojev_gamer_v1_precision_continuation_v19.py",
  "tools/nanojev_gamer_v1_precision_resolution_v20.py",
  "tools/nanojev_gamer_v1_protected_alignment_lab.py",
  "tools/nanojev_gamer_v1_protected_math_repair_v7.py",
  "tools/nanojev_gamer_v1_radius_activations.py",
  "tools/nanojev_gamer_v1_representation_transfer_audit.py",
  "tools/nanojev_gamer_v1_role_reversal_retention_v29.py",
  "tools/nanojev_gamer_v1_s_payoff_operational_acceptance.py",
  "tools/nanojev_gamer_v1_selector_retention_training_v28.py",
  "tools/nanojev_gamer_v1_separability.py",
  "tools/nanojev_gamer_v1_shadow_precision_v21.py",
  "tools/nanojev_gamer_v1_shared_digit_repair_lab.py",
  "tools/nanojev_gamer_v1_sign_information_probe.py",
  "tools/nanojev_gamer_v1_sign_only.py",
  "tools/nanojev_gamer_v1_structured_head_lab.py",
  "tools/nanojev_gamer_v1_sufficiency.py",
  "tools/nanojev_gamer_v1_threshold_calibration.py",
  "tools/nanojev_gamer_v1_to_numerical_shadow.py",
  "tools/nanojev_gamer_v1_token_localization_lab.py",
  "tools/nanojev_gamer_v1_transition_games.py",
  "tools/nanojev_gamer_v1_upstream_path.py",
  "tools/nanojev_gamer_v1_v30_live_inference.py",
  "tools/nanojev_gamer_v1_v31_live_regression.py",
  "tools/nanojev_gamer_v1_v33_game_learning.py",
  "tools/nanojev_gamer_v1_v34_grounded_game_learning.py",
  "tools/nanojev_gamer_v1_zero_line_decoder_v30.py",
  "tools/nanojev_gamer_v1_zero_sign_frob.py",
  "tools/nanojev_hub_request.py",
  "tools/nanojev_image_bootstrap.py",
  "tools/nanojev_lifecycle_service.py",
  "tools/nanojev_objective_api.py",
  "tools/nanojev_three_backbone_clef_sized_live_train.py",
  "tools/nanojev_three_backbone_clef_sized_live_train_smoke.py",
  "tools/nanojev_three_backbone_clef_tinystories_structured_supervision_train.py",
  "tools/nanojev_two_game_spine_smoke.py"
)

$retainedNanoJevTestPaths = @(
  "tests/test_bridge_boarding_nanojev_choices.py",
  "tests/test_bridge_nanojev_captain_decisions.py",
  "tests/test_bridge_nanojev_live_endpoint_smoke.py",
  "tests/test_nanojev_clef_service.py",
  "tests/test_nanojev_gamer_v1_v30_live_inference.py",
  "tests/test_nanojev_gamer_v1_v31_live_regression.py",
  "tests/test_nanojev_hub_integration.py",
  "tests/test_nanojev_image_bootstrap.py",
  "tests/test_nanojev_managed_startup.py",
  "tests/test_nanojev_provider_local.py",
  "tests/test_nanojev_shared_head_patch2.py",
  "tests/test_nanojev_source_image_refresh.py",
  "tests/test_nanojev_startup.py"
)

$excludedNanoJevResearchToolPaths = @(
  "tools/clef_flash_joint_head_train_smoke.py",
  "tools/hawk_dove_continuation_head.py",
  "tools/hawk_dove_head_smoke.py"
)

function Test-NanoJevExportPathAllowed {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,

    [ValidateSet("main", "game", "nanojev")]
    [string]$ComponentName = "main"
  )

  $repoPath = Convert-ToRepoPath $RepoPath
  $inNanoJevTree = ($repoPath -eq "nanojev" -or $repoPath.StartsWith("nanojev/", [System.StringComparison]::OrdinalIgnoreCase))
  if ($ComponentName -eq "nanojev") {
    # Only the NanoJev component may stage its development tree.
    return $inNanoJevTree
  }
  if ($inNanoJevTree) {
    return $false
  }

  if ($repoPath.StartsWith("tools/", [System.StringComparison]::OrdinalIgnoreCase)) {
    if ($excludedNanoJevResearchToolPaths -contains $repoPath) {
      return $false
    }
    if ($repoPath.IndexOf("nanojev", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
      return ($retainedNanoJevToolPaths -contains $repoPath)
    }
  }

  if ($repoPath.StartsWith("tests/", [System.StringComparison]::OrdinalIgnoreCase)) {
    if ($repoPath.IndexOf("nanojev", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
      return ($retainedNanoJevTestPaths -contains $repoPath)
    }
  }

  if (
    $repoPath.StartsWith("pretty_docs/", [System.StringComparison]::OrdinalIgnoreCase) -or
    $repoPath.StartsWith("scripts/", [System.StringComparison]::OrdinalIgnoreCase)
  ) {
    if ($repoPath.IndexOf("nanojev", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
      return $false
    }
  }

  return $true
}

function Test-RepoPathAllowed {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,
    [bool]$IsDirectory = $false,
    [ValidateSet("main", "game", "nanojev")]
    [string]$ComponentName = "main"
  )

  $repoPath = Convert-ToRepoPath $RepoPath
  $name = Split-Path -Leaf $repoPath

  if (-not (Test-NanoJevExportPathAllowed -RepoPath $repoPath -ComponentName $ComponentName)) {
    return $false
  }

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
    "node_modules",
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
    [string]$RepoRelativePath,

    [Parameter(Mandatory = $true)]
    [string]$ComponentName
  )

  foreach ($child in Get-ChildItem -LiteralPath $DirectoryPath -Force) {
    $childRepoPath = Convert-ToRepoPath (Join-Path $RepoRelativePath $child.Name)

    if (-not (Test-RepoPathAllowed -RepoPath $childRepoPath -IsDirectory:$child.PSIsContainer -ComponentName $ComponentName)) {
      continue
    }

    if ($child.PSIsContainer) {
      Copy-ExportDirectoryPruned `
        -DirectoryPath $child.FullName `
        -StageRoot $StageRoot `
        -RepoRelativePath $childRepoPath `
        -ComponentName $ComponentName
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
    [string]$RelativePath,

    [Parameter(Mandatory = $true)]
    [string]$ComponentName
  )

  $item = Get-Item -LiteralPath $SourcePath -Force
  $relativePath = Convert-ToRepoPath $RelativePath

  if (-not (Test-RepoPathAllowed -RepoPath $relativePath -IsDirectory:$item.PSIsContainer -ComponentName $ComponentName)) {
    return
  }

  if ($item.PSIsContainer) {
    Copy-ExportDirectoryPruned `
      -DirectoryPath $item.FullName `
      -StageRoot $StageRoot `
      -RepoRelativePath $relativePath `
      -ComponentName $ComponentName

    return
  }

  $destination = Join-Path $StageRoot $relativePath
  Copy-ExportFile -FilePath $item.FullName -DestinationPath $destination
}

function Assert-CleanExportStage {
  param(
    [Parameter(Mandatory = $true)]
    [string]$StageRoot,

    [Parameter(Mandatory = $true)]
    [string]$ComponentName
  )

  $badDirectoryNames = @(
    "node_modules",
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

    # Defense in depth: reject NanoJev research files if staging rules change.
    if (-not (Test-NanoJevExportPathAllowed -RepoPath $repoPath -ComponentName $ComponentName)) {
      $bad.Add($repoPath)
      return
    }

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

# Main and game remain separate payloads. The NanoJev research tree gets its
# own development ZIP, while runtime-required NanoJev tools/docker stay in main.
$mainExportItems = @(
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
  "pretty_docs",  # Architectural Mother/Hub/FDB specifications are now here; never export stale root copies
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
  "docker-compose.nanojev.yml",
  "docker-compose.astrometric.yml",
  "docker/astrometric-renderer/",
  "run-exp-fdb-hub.py",
  ".githooks",
  "docker",
  "fdb_mutate_harness.py",
  "hub_mutate_harness.py",
  "seh_mutate_harness.py",
  # Required SEH build/test sources, explicitly scoped: never export the whole
  # deployment directory and risk inadvertently copying a local target secret.
  "deploy/seh/Dockerfile",
  "deploy/seh/target.example.json",
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
  "mother_mutate_harness.py",
  "fdb_mutate_harness.py",
  "mcel_apps",
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

$gameExportItems = @(
  "game_projects"
)

$nanojevExportItems = @(
  "nanojev"
)

$allExportItems = @(
  @($mainExportItems)
  @($gameExportItems)
  @($nanojevExportItems)
) | Select-Object -Unique

function Get-ExportComponentForRepoPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath
  )

  $repoPath = Convert-ToRepoPath $RepoPath

  if (
    $repoPath -eq "game_projects" -or
    $repoPath.StartsWith("game_projects/", [System.StringComparison]::OrdinalIgnoreCase)
  ) {
    return "game"
  }

  if (
    $repoPath -eq "nanojev" -or
    $repoPath.StartsWith("nanojev/", [System.StringComparison]::OrdinalIgnoreCase)
  ) {
    return "nanojev"
  }

  return "main"
}

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

function Get-FollowEventComponents {
  param(
    [Parameter(Mandatory = $true)]
    $EventRecord
  )

  $eventArgs = $EventRecord.SourceEventArgs
  if ($null -eq $eventArgs) {
    return @()
  }

  $candidatePaths = New-Object System.Collections.Generic.List[string]

  if (-not [string]::IsNullOrWhiteSpace($eventArgs.FullPath)) {
    $candidatePaths.Add($eventArgs.FullPath) | Out-Null
  }

  if (
    ($eventArgs -is [System.IO.RenamedEventArgs]) -and
    -not [string]::IsNullOrWhiteSpace($eventArgs.OldFullPath)
  ) {
    $candidatePaths.Add($eventArgs.OldFullPath) | Out-Null
  }

  $components = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

  foreach ($eventPath in $candidatePaths) {
    if (-not [string]::IsNullOrWhiteSpace($ArchiveRoot)) {
      if (Test-PathInsideRoot -Path $eventPath -Root $ArchiveRoot) {
        continue
      }
    }

    $repoPath = Convert-ToSourceRelativeRepoPath -Path $eventPath
    if ([string]::IsNullOrWhiteSpace($repoPath)) {
      continue
    }

    $component = Get-ExportComponentForRepoPath -RepoPath $repoPath
    $isDirectory = Test-Path -LiteralPath $eventPath -PathType Container
    if (-not (Test-RepoPathAllowed -RepoPath $repoPath -IsDirectory:$isDirectory -ComponentName $component)) {
      continue
    }
    if (-not [string]::IsNullOrWhiteSpace($component)) {
      $components.Add($component) | Out-Null
    }
  }

  return @($components)
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
    foreach ($relative in ($allExportItems | Sort-Object { (Convert-ToRepoPath $_).Length })) {
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
    Write-Host "component ownership: game_projects/** => game; nanojev/** => nanojev; everything else => main"
    Write-Host "press Ctrl+C to stop following"

    $pendingMainExport = $false
    $pendingGameExport = $false
    $pendingNanoJevExport = $false
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
            $eventComponents = @(Get-FollowEventComponents -EventRecord $eventRecord)

            if ($eventComponents -contains "main") {
              $pendingMainExport = $true
              $sawRelevantEvent = $true
            }

            if ($eventComponents -contains "game") {
              $pendingGameExport = $true
              $sawRelevantEvent = $true
            }

            if ($eventComponents -contains "nanojev") {
              $pendingNanoJevExport = $true
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
          $quietUntilUtc = [DateTime]::UtcNow.AddSeconds($FollowCalmSeconds)
          $pendingNames = @()
          if ($pendingMainExport) { $pendingNames += "main" }
          if ($pendingGameExport) { $pendingNames += "game" }
          if ($pendingNanoJevExport) { $pendingNames += "nanojev" }
          Write-Host ("detected {0} export input changes; exporting after {1} quiet second(s)" -f ($pendingNames -join "+"), $FollowCalmSeconds)
        }
      }

      if (
        ($pendingMainExport -or $pendingGameExport -or $pendingNanoJevExport) -and
        ([DateTime]::UtcNow -ge $quietUntilUtc)
      ) {
        $pendingNames = @()
        if ($pendingMainExport) { $pendingNames += "main" }
        if ($pendingGameExport) { $pendingNames += "game" }
        if ($pendingNanoJevExport) { $pendingNames += "nanojev" }
        Write-Host ("quiet window elapsed; creating {0} export(s)" -f ($pendingNames -join "+"))

        Invoke-ExportSnapshot `
          -ExportMain:$pendingMainExport `
          -ExportGame:$pendingGameExport `
          -ExportNanoJev:$pendingNanoJevExport

        $pendingMainExport = $false
        $pendingGameExport = $false
        $pendingNanoJevExport = $false
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

function New-ComponentExportArchive {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ComponentName,

    [Parameter(Mandatory = $true)]
    [AllowEmptyCollection()]
    [string[]]$Items,

    [Parameter(Mandatory = $true)]
    [string]$ArchiveBaseName,

    [Parameter(Mandatory = $true)]
    [string]$Timestamp,

    [Parameter(Mandatory = $true)]
    [string]$RunArchiveRoot,

    [Parameter(Mandatory = $true)]
    [string]$TempRoot
  )

  $componentTempRoot = Join-Path $TempRoot $ComponentName
  $stageRoot = Join-Path $componentTempRoot $ProjectName
  $zipPath = Join-Path $RunArchiveRoot ("{0}-{1}.zip" -f $ArchiveBaseName, $Timestamp)

  if (Test-Path -LiteralPath $componentTempRoot) {
    Remove-Item -LiteralPath $componentTempRoot -Recurse -Force
  }

  New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

  $stagedSourceCount = 0

  if ($ComponentName -eq "main") {
    foreach ($required in @("deploy/seh/Dockerfile", "deploy/seh/target.example.json")) {
      if (-not (Test-Path -LiteralPath (Join-Path $sourceFull $required) -PathType Leaf)) {
        throw "Required SEH repository source is missing: $required"
      }
    }
  }

  foreach ($relative in $Items) {
    $path = Join-Path $sourceFull $relative

    if (Test-Path -LiteralPath $path) {
      Copy-ExportItem -SourcePath $path -StageRoot $stageRoot -RelativePath $relative -ComponentName $ComponentName
      $stagedSourceCount += 1
    }
  }

  if ($stagedSourceCount -eq 0) {
    throw "The $ComponentName export has no source inputs available under: $sourceFull"
  }

  Assert-CleanExportStage -StageRoot $stageRoot -ComponentName $ComponentName

  if ($ComponentName -eq "main") {
    foreach ($required in @("deploy/seh/Dockerfile", "deploy/seh/target.example.json")) {
      if (-not (Test-Path -LiteralPath (Join-Path $stageRoot $required) -PathType Leaf)) {
        throw "Required SEH repository source was omitted from export: $required"
      }
    }
  }

  if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
  }

  Compress-Archive -Path $stageRoot -DestinationPath $zipPath -Force

  $count = (Get-ChildItem -LiteralPath $stageRoot -Recurse -File -Force).Count

  Write-Host ("created {0}" -f $zipPath)
  Write-Host ("component: {0}" -f $ComponentName)
  Write-Host ("files: {0}" -f $count)
  Write-Host "clean export exclusions enforced"

  return [PSCustomObject]@{
    Component = $ComponentName
    ZipPath = $zipPath
    FileCount = $count
  }
}

function Invoke-ExportSnapshot {
  param(
    [switch]$ExportMain,
    [switch]$ExportGame,
    [switch]$ExportNanoJev
  )

  if (-not $ExportMain -and -not $ExportGame -and -not $ExportNanoJev) {
    throw "Invoke-ExportSnapshot requires at least one component: main, game, or nanojev."
  }

  $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $nonce = [guid]::NewGuid().ToString("N").Substring(0, 8)
  $runArchiveRoot = $ArchiveRoot
  $installerReHomeRunRoot = ""
  $installerReHomeExtractRoot = ""
  $installerReHomeRepoRoot = ""

  if ($InstallerReHome) {
    if (-not $ExportMain -or -not $ExportGame -or -not $ExportNanoJev) {
      throw "Installer rehome requires the main, game, and nanojev exports."
    }

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

  if ($InstallerReHome) {
    $tempRoot = Join-Path $installerReHomeRunRoot "s"
  }
  else {
    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mct-{0}" -f $nonce)
  }

  New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

  $mainResult = $null
  $gameResult = $null
  $nanojevResult = $null

  try {
    if ($ExportMain) {
      $mainResult = New-ComponentExportArchive `
        -ComponentName "main" `
        -Items $mainExportItems `
        -ArchiveBaseName $ProjectName `
        -Timestamp $timestamp `
        -RunArchiveRoot $runArchiveRoot `
        -TempRoot $tempRoot
    }

    if ($ExportGame) {
      $gameResult = New-ComponentExportArchive `
        -ComponentName "game" `
        -Items $gameExportItems `
        -ArchiveBaseName ("{0}-game" -f $ProjectName) `
        -Timestamp $timestamp `
        -RunArchiveRoot $runArchiveRoot `
        -TempRoot $tempRoot
    }

    if ($ExportNanoJev) {
      $nanojevResult = New-ComponentExportArchive `
        -ComponentName "nanojev" `
        -Items $nanojevExportItems `
        -ArchiveBaseName ("{0}-nanojev" -f $ProjectName) `
        -Timestamp $timestamp `
        -RunArchiveRoot $runArchiveRoot `
        -TempRoot $tempRoot
    }

    if ($InstallerReHome) {
      if (Test-Path -LiteralPath $installerReHomeExtractRoot) {
        Remove-Item -LiteralPath $installerReHomeExtractRoot -Recurse -Force
      }

      New-Item -ItemType Directory -Path $installerReHomeExtractRoot -Force | Out-Null

      Expand-Archive -LiteralPath $mainResult.ZipPath -DestinationPath $installerReHomeExtractRoot -Force
      Expand-Archive -LiteralPath $gameResult.ZipPath -DestinationPath $installerReHomeExtractRoot -Force
      Expand-Archive -LiteralPath $nanojevResult.ZipPath -DestinationPath $installerReHomeExtractRoot -Force

      if (-not (Test-Path -LiteralPath $installerReHomeRepoRoot -PathType Container)) {
        throw "Installer rehome export did not extract the expected repository root: $installerReHomeRepoRoot"
      }

      $bootstrapDriver = Join-Path $installerReHomeRepoRoot "tools\bootstrap_main_computer.py"
      if (-not (Test-Path -LiteralPath $bootstrapDriver -PathType Leaf)) {
        throw "Installer rehome export is missing the Python bootstrap driver: $bootstrapDriver"
      }

      $gameRoot = Join-Path $installerReHomeRepoRoot "game_projects"
      if (-not (Test-Path -LiteralPath $gameRoot -PathType Container)) {
        throw "Installer rehome export is missing the game project payload: $gameRoot"
      }

      $nanojevRoot = Join-Path $installerReHomeRepoRoot "nanojev"
      if (-not (Test-Path -LiteralPath $nanojevRoot -PathType Container)) {
        throw "Installer rehome export is missing the NanoJev development payload: $nanojevRoot"
      }

      Write-Host ("extracted {0}" -f $installerReHomeRepoRoot)
      Write-Host "installer rehome export ready"
      Write-Output $installerReHomeRepoRoot
    }
  } finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}

# Every invocation starts with all three components. In -Follow mode, subsequent
# exports are selective: only the ownership domains that changed are rebuilt.
Invoke-ExportSnapshot -ExportMain -ExportGame -ExportNanoJev

if ($Follow) {
  Start-FollowExportLoop
}

return
