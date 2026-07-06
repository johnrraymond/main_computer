param(
    [Parameter(Mandatory = $true)]
    [string]$PatchZip,

    [string]$NewPatchScript = "new_patch.py",

    [string]$CommitMessage = "checkpoint before Podman/OpenClaw container runtime patch",

    [string[]]$SecretsFile = @(),

    [switch]$BlockUntrackedJson,

    [string[]]$ProtectedBranch = @("main", "master", "develop", "trunk"),

    [switch]$AllowProtectedBranch,

    [string]$PatchBranchPrefix = "patch"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:blockers = New-Object 'System.Collections.Generic.List[string]'
$script:warnings = New-Object 'System.Collections.Generic.List[string]'

function Quote-Pwsh([string]$Value) {
    return "'" + ($Value -replace "'", "''") + "'"
}

function Add-UniqueLine($List, [string]$Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return
    }

    if (-not $List.Contains($Text)) {
        [void]$List.Add($Text)
    }
}

function Add-Blocker([string]$Message) {
    Add-UniqueLine $script:blockers $Message
}

function Add-Warning([string]$Message) {
    Add-UniqueLine $script:warnings $Message
}

function Join-PathArgs($Paths) {
    $quoted = @()

    foreach ($path in @($Paths)) {
        $text = [string]$path
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            $quoted += Quote-Pwsh $text
        }
    }

    return ($quoted -join " ")
}

function Show-List([string]$Title, $Items) {
    $safeItems = @()

    foreach ($item in @($Items)) {
        $text = [string]$item
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            $safeItems += $text
        }
    }

    if ($safeItems.Count -eq 0) {
        return
    }

    Write-Host ""
    Write-Host $Title -ForegroundColor Yellow
    foreach ($item in $safeItems) {
        Write-Host "  $item"
    }
}

function Show-CommandBlock([string]$Title, [string]$Why, $Commands) {
    $safeCommands = @()

    foreach ($command in @($Commands)) {
        $text = [string]$command
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            $safeCommands += $text
        }
    }

    if ($safeCommands.Count -eq 0) {
        return
    }

    Write-Host ""
    Write-Host $Title -ForegroundColor Yellow
    if (-not [string]::IsNullOrWhiteSpace($Why)) {
        Write-Host "  Why: $Why"
    }

    foreach ($command in $safeCommands) {
        Write-Host "  $command"
    }
}

function Invoke-GitLines($Arguments) {
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = @(& git @Arguments 2>$null)
        $exitCode = $LASTEXITCODE
        return [pscustomobject]@{
            Output = $output
            ExitCode = $exitCode
        }
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
}

function Git-Path([string]$Name) {
    $result = Invoke-GitLines @("rev-parse", "--git-path", $Name)
    if ($result.ExitCode -ne 0 -or @($result.Output).Count -eq 0) {
        return ""
    }

    return ([string]$result.Output[0]).Trim()
}

function Normalize-ZipEntry([string]$Name) {
    return ($Name -replace '\\', '/').Trim()
}

function Strip-KnownPatchRoot([string]$Name, [string]$RepoLeaf) {
    $normalized = Normalize-ZipEntry $Name

    foreach ($root in @($RepoLeaf, "main_computer_test")) {
        if (-not [string]::IsNullOrWhiteSpace($root) -and $normalized.StartsWith("$root/")) {
            return $normalized.Substring($root.Length + 1)
        }
    }

    return $normalized
}

function Normalize-BranchSegment([string]$Value) {
    $segment = ([string]$Value).ToLowerInvariant()
    $segment = $segment -replace '[^a-z0-9._-]+', '-'
    $segment = $segment.Trim("-.".ToCharArray())

    if ([string]::IsNullOrWhiteSpace($segment)) {
        return "work"
    }

    if ($segment.Length -gt 32) {
        $segment = $segment.Substring(0, 32).Trim("-.".ToCharArray())
    }

    if ([string]::IsNullOrWhiteSpace($segment)) {
        return "work"
    }

    return $segment
}

function New-PatchBranchName([string]$PatchZipPath, [string]$CurrentBranch) {
    $base = [System.IO.Path]::GetFileNameWithoutExtension($PatchZipPath)

    if ([string]::IsNullOrWhiteSpace($base)) {
        $base = "new-patch"
    }

    $base = $base.ToLowerInvariant()
    $base = $base -replace '^main[-_]?computer[-_]?test[-_]?', ''
    $base = $base -replace '[-_]?patch$', ''
    $base = $base -replace '[\s_]+', '-'
    $base = $base -replace '[^a-z0-9._-]+', '-'
    $base = $base.Trim("-.".ToCharArray())

    if ([string]::IsNullOrWhiteSpace($base)) {
        $base = "new-patch"
    }

    if ($base.Length -gt 48) {
        $base = $base.Substring(0, 48).Trim("-.".ToCharArray())
    }

    $branchSegment = Normalize-BranchSegment $CurrentBranch
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $prefix = ([string]$PatchBranchPrefix).Trim("/".ToCharArray())

    if ([string]::IsNullOrWhiteSpace($prefix)) {
        $prefix = "patch"
    }

    return "$prefix/$base-$branchSegment-$timestamp"
}

function Test-ProtectedBranch([string]$BranchName) {
    if ([string]::IsNullOrWhiteSpace($BranchName)) {
        return $false
    }

    foreach ($protected in @($ProtectedBranch)) {
        if (-not [string]::IsNullOrWhiteSpace($protected) -and $BranchName -ieq $protected) {
            return $true
        }
    }

    return $false
}

function Get-ScriptCommandPrefix() {
    $scriptPath = $PSCommandPath
    if ([string]::IsNullOrWhiteSpace($scriptPath)) {
        $scriptPath = "scripts/preflight_new_patch_git_ready.ps1"
    }

    return "& " + (Quote-Pwsh $scriptPath)
}

function Build-RerunCommand($ExtraSwitches = @()) {
    $parts = @()
    $parts += Get-ScriptCommandPrefix
    $parts += "-PatchZip"
    $parts += Quote-Pwsh $PatchZip

    if ($NewPatchScript -ne "new_patch.py") {
        $parts += "-NewPatchScript"
        $parts += Quote-Pwsh $NewPatchScript
    }

    if ($CommitMessage -ne "checkpoint before Podman/OpenClaw container runtime patch") {
        $parts += "-CommitMessage"
        $parts += Quote-Pwsh $CommitMessage
    }

    foreach ($secret in @($SecretsFile)) {
        $parts += "-SecretsFile"
        $parts += Quote-Pwsh $secret
    }

    if ($BlockUntrackedJson) {
        $parts += "-BlockUntrackedJson"
    }

    foreach ($protected in @($ProtectedBranch)) {
        if (-not [string]::IsNullOrWhiteSpace($protected) -and
            @("main", "master", "develop", "trunk") -notcontains $protected) {
            $parts += "-ProtectedBranch"
            $parts += Quote-Pwsh $protected
        }
    }

    foreach ($switch in @($ExtraSwitches)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$switch)) {
            $parts += [string]$switch
        }
    }

    return ($parts -join " ")
}

function Build-PrepCommands([string]$RerunCommand, [bool]$IncludeCommitCommands) {
    $commands = @()

    if ($IncludeCommitCommands) {
        $commands += "git status --short"

        if (@($script:pathsToCommit).Count -gt 0) {
            $commitPathArgs = Join-PathArgs $script:pathsToCommit
            $commitMessageArg = Quote-Pwsh $CommitMessage
            $commands += "git add -- $commitPathArgs"
            $commands += "git commit -m $commitMessageArg"
        }
    }

    $commands += $RerunCommand
    return $commands
}

function Write-ChoiceGuidance([bool]$ReadyForDryRun) {
    if ([string]::IsNullOrWhiteSpace($script:branchCommand)) {
        return
    }

    $branchCommands = @($script:branchCommand)
    $branchCommands += Build-PrepCommands $script:rerunCommand $script:hasCommitWork

    Show-CommandBlock `
        "Option A - switch to a patch branch first (safer)" `
        "Use this for broad patches; it keeps $script:branchName at $script:currentCommit as the rollback point and keeps the patch isolated." `
        $branchCommands

    if (-not [string]::IsNullOrWhiteSpace($script:branchName)) {
        $currentCommands = Build-PrepCommands $script:currentBranchRerunCommand $script:hasCommitWork

        if ($ReadyForDryRun) {
            $currentCommands = @($script:dryRunCommand, $script:applyCommand)
        }

        Show-CommandBlock `
            "Option B - stay on the current branch" `
            "Use this when you intentionally want the checkpoint and patch work directly on $script:branchName." `
            $currentCommands
    } else {
        Show-CommandBlock `
            "Option B - return to an existing branch" `
            "Use this when detached HEAD was intentional and you know which branch should receive the work." `
            @("git switch <branch-name>", $script:rerunCommand)
    }
}

function Write-NotReadyAndExit([string]$Title = "NOT READY") {
    Write-Host ""
    Write-Host $Title -ForegroundColor Red
    Show-List "Blockers:" $script:blockers

    Write-Host ""
    Write-Host "Suggested paths. These were NOT run:" -ForegroundColor Yellow
    Write-ChoiceGuidance $false

    Write-Host ""
    Write-Host "This script made no Git changes and did not apply the patch." -ForegroundColor Cyan
    exit 1
}

# ---------------------------------------------------------------------------
# Read-only tool/repo checks
# ---------------------------------------------------------------------------

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Add-Blocker "git is not available on PATH."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Add-Blocker "python is not available on PATH."
}

if ($script:blockers.Count -gt 0) {
    Write-Host ""
    Write-Host "NOT READY" -ForegroundColor Red
    Show-List "Blockers:" $script:blockers
    Write-Host ""
    Write-Host "Suggested actions. These were NOT run:" -ForegroundColor Yellow
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "  Install Git, then reopen this shell so git is on PATH."
    }
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "  Install Python, then reopen this shell so python is on PATH."
    }
    Write-Host ""
    Write-Host "This script made no Git changes and did not apply the patch." -ForegroundColor Cyan
    exit 1
}

$repoResult = Invoke-GitLines @("rev-parse", "--show-toplevel")
if ($repoResult.ExitCode -ne 0 -or @($repoResult.Output).Count -eq 0 -or [string]::IsNullOrWhiteSpace([string]$repoResult.Output[0])) {
    Write-Host ""
    Write-Host "NOT READY" -ForegroundColor Red
    Write-Host "This directory is not inside a Git repository."
    Write-Host ""
    Write-Host "Suggested action. This was NOT run:" -ForegroundColor Yellow
    Write-Host "  cd <your-main-computer-repo>"
    Write-Host ""
    Write-Host "This script made no Git changes and did not apply the patch." -ForegroundColor Cyan
    exit 1
}

$repoRoot = ([string]$repoResult.Output[0]).Trim()
Set-Location $repoRoot

Write-Host "Repository root: $repoRoot" -ForegroundColor Cyan

if (-not (Test-Path $NewPatchScript)) {
    Add-Blocker "Cannot find $NewPatchScript at the repository root."
}

if (-not (Test-Path $PatchZip)) {
    Add-Blocker "Cannot find patch zip: $PatchZip"
}

foreach ($secret in @($SecretsFile)) {
    if (-not (Test-Path $secret)) {
        Add-Blocker "Required secrets file is missing: $secret"
    }
}

# ---------------------------------------------------------------------------
# Refuse active Git operations
# ---------------------------------------------------------------------------

$mergeHead = Git-Path "MERGE_HEAD"
$rebaseMerge = Git-Path "rebase-merge"
$rebaseApply = Git-Path "rebase-apply"
$cherryPickHead = Git-Path "CHERRY_PICK_HEAD"
$revertHead = Git-Path "REVERT_HEAD"
$bisectLog = Git-Path "BISECT_LOG"

if (-not [string]::IsNullOrWhiteSpace($mergeHead) -and (Test-Path $mergeHead)) {
    Add-Blocker "A merge is in progress. Resolve it before choosing either patch path."
}

if ((-not [string]::IsNullOrWhiteSpace($rebaseMerge) -and (Test-Path $rebaseMerge)) -or
    (-not [string]::IsNullOrWhiteSpace($rebaseApply) -and (Test-Path $rebaseApply))) {
    Add-Blocker "A rebase is in progress. Resolve or abort it before choosing either patch path."
}

if (-not [string]::IsNullOrWhiteSpace($cherryPickHead) -and (Test-Path $cherryPickHead)) {
    Add-Blocker "A cherry-pick is in progress. Resolve or abort it before choosing either patch path."
}

if (-not [string]::IsNullOrWhiteSpace($revertHead) -and (Test-Path $revertHead)) {
    Add-Blocker "A revert is in progress. Resolve or abort it before choosing either patch path."
}

if (-not [string]::IsNullOrWhiteSpace($bisectLog) -and (Test-Path $bisectLog)) {
    Add-Blocker "A bisect is in progress. End bisect mode before choosing either patch path."
}

# ---------------------------------------------------------------------------
# Branch/HEAD check
# ---------------------------------------------------------------------------

$branchResult = Invoke-GitLines @("branch", "--show-current")
if ($branchResult.ExitCode -ne 0 -or @($branchResult.Output).Count -eq 0) {
    $branch = ""
} else {
    $branch = ([string]$branchResult.Output[0]).Trim()
}

$commitResult = Invoke-GitLines @("rev-parse", "HEAD")
if ($commitResult.ExitCode -ne 0 -or @($commitResult.Output).Count -eq 0 -or [string]::IsNullOrWhiteSpace([string]$commitResult.Output[0])) {
    $currentCommit = "<unknown>"
} else {
    $currentCommit = ([string]$commitResult.Output[0]).Trim()
}

$script:branchName = $branch
$script:currentCommit = $currentCommit

if ([string]::IsNullOrWhiteSpace($branch)) {
    Add-Blocker "Repository is in detached HEAD state."
    Write-Host "Current branch: <detached HEAD>" -ForegroundColor Cyan
} else {
    Write-Host "Current branch: $branch" -ForegroundColor Cyan
}

Write-Host "Current commit: $currentCommit" -ForegroundColor Cyan

$patchBranchName = New-PatchBranchName $PatchZip $branch
$script:branchCommand = "git switch -c " + (Quote-Pwsh $patchBranchName)

$script:rerunCommand = Build-RerunCommand
$script:currentBranchRerunCommand = $script:rerunCommand

$isProtectedBranch = Test-ProtectedBranch $branch
if ($isProtectedBranch -and -not $AllowProtectedBranch) {
    Add-Blocker "Current branch '$branch' is protected for broad patches."

    $script:currentBranchRerunCommand = Build-RerunCommand @("-AllowProtectedBranch")
    Add-Warning "To continue directly on '$branch', rerun the guard with -AllowProtectedBranch after deciding that is intentional."
}

# ---------------------------------------------------------------------------
# Read-only dirty tree analysis
# ---------------------------------------------------------------------------
# Use `git status --porcelain` instead of `git diff` here. On Windows with
# autocrlf/safecrlf warnings, native git stderr can become a terminating
# PowerShell NativeCommandError under `$ErrorActionPreference = "Stop"`.
# `git status` is enough for this guard and avoids mutating or refreshing files.

$statusResult = Invoke-GitLines @("-c", "core.safecrlf=false", "status", "--porcelain=v1", "--untracked-files=all")
$statusLines = @($statusResult.Output)

if ($statusResult.ExitCode -ne 0) {
    Add-Blocker "Could not read Git working-tree status."
    $statusLines = @()
}

$staged = @()
$unstaged = @()
$untracked = @()
$unmergedStatus = @()

foreach ($line in @($statusLines)) {
    $text = [string]$line
    if ($text.Length -lt 3) {
        continue
    }

    $xy = $text.Substring(0, 2)
    $path = $text.Substring(3)

    # Porcelain v1 represents renames/copies as: old -> new. The new path is
    # the one a broad replacement-file patch would collide with.
    if ($path -like "* -> *") {
        $path = ($path -split " -> ", 2)[1]
    }

    if (@("DD", "AU", "UD", "UA", "DU", "AA", "UU") -contains $xy) {
        $unmergedStatus += $text
        continue
    }

    if ($xy -eq "??") {
        $untracked += $path
        continue
    }

    if ($xy.Substring(0, 1) -ne " ") {
        $staged += $path
    }

    if ($xy.Substring(1, 1) -ne " ") {
        $unstaged += $path
    }
}

$staged = @($staged | Sort-Object -Unique)
$unstaged = @($unstaged | Sort-Object -Unique)
$untracked = @($untracked | Sort-Object -Unique)

if (@($unmergedStatus).Count -gt 0) {
    Add-Blocker "There are unresolved merge-conflict paths."
}

$trackedDirty = @($staged + $unstaged | Sort-Object -Unique)

$untrackedJson = @()
$untrackedBlocking = @()

foreach ($path in @($untracked)) {
    if ($path -match '(?i)\.json$' -and -not $BlockUntrackedJson) {
        $untrackedJson += $path
    } else {
        $untrackedBlocking += $path
    }
}

# ---------------------------------------------------------------------------
# Patch zip read-only safety scan
# This does not apply the patch. It only reads the zip file list.
# ---------------------------------------------------------------------------

$patchEntriesRepoRelative = @()

if (Test-Path $PatchZip) {
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue

        $resolvedPatchZip = (Resolve-Path $PatchZip).Path
        $repoLeaf = Split-Path $repoRoot -Leaf

        $zip = [System.IO.Compression.ZipFile]::OpenRead($resolvedPatchZip)
        try {
            foreach ($entry in $zip.Entries) {
                if ([string]::IsNullOrWhiteSpace($entry.FullName)) {
                    continue
                }

                $name = Normalize-ZipEntry $entry.FullName

                if ($name.EndsWith("/")) {
                    continue
                }

                $parts = @($name.Split("/") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
                if ($name.StartsWith("/") -or ($parts -contains "..")) {
                    Add-Blocker "Patch zip contains an unsafe path: $name"
                    continue
                }

                $patchEntriesRepoRelative += (Strip-KnownPatchRoot $name $repoLeaf)
            }
        } finally {
            $zip.Dispose()
        }
    } catch {
        Add-Blocker "Could not inspect patch zip entries: $($_.Exception.Message)"
    }
}

$patchEntriesRepoRelative = @($patchEntriesRepoRelative | Sort-Object -Unique)

$untrackedJsonOverlapsPatch = @()
foreach ($path in @($untrackedJson)) {
    $normalized = $path -replace '\\', '/'
    if ($patchEntriesRepoRelative -contains $normalized) {
        $untrackedJsonOverlapsPatch += $path
    }
}

if (@($untrackedJsonOverlapsPatch).Count -gt 0) {
    Add-Blocker "Untracked JSON files overlap files inside the patch zip."
}

# ---------------------------------------------------------------------------
# Dirty tree decisions
# ---------------------------------------------------------------------------

$script:pathsToCommit = @($trackedDirty + $untrackedBlocking | Sort-Object -Unique)
$script:hasCommitWork = (@($script:pathsToCommit).Count -gt 0)

if ($script:hasCommitWork) {
    Add-Blocker "There are tracked changes or important untracked files that should be committed before patching."
}

if (@($untrackedJson).Count -gt 0) {
    Add-Warning "Untracked *.json files were found. They are non-blocking generated/local files unless they overlap the patch zip. They were not included in commit commands. Make sure your secrets file is present so the app can regenerate local JSON state."
}

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

Show-List "Staged changes:" $staged
Show-List "Unstaged tracked changes:" $unstaged
Show-List "Untracked blocking files:" $untrackedBlocking
Show-List "Untracked non-blocking *.json files:" $untrackedJson
Show-List "Warnings:" $script:warnings

$dryRunCommand = "python " + (Quote-Pwsh $NewPatchScript) + " " + (Quote-Pwsh $PatchZip) + " --dry-run"
$applyCommand = "python " + (Quote-Pwsh $NewPatchScript) + " " + (Quote-Pwsh $PatchZip)
$rollbackCommand = "git reset --hard $currentCommit"

$script:dryRunCommand = $dryRunCommand
$script:applyCommand = $applyCommand

if ($script:blockers.Count -gt 0) {
    Write-NotReadyAndExit "NOT READY TO PATCH"
}

Write-Host ""
Write-Host "READY FOR PATCH DRY-RUN" -ForegroundColor Green
Write-Host ""
Write-Host "This script made no Git changes and did not apply the patch." -ForegroundColor Cyan

Write-Host ""
Write-Host "Two valid paths. These were NOT run:" -ForegroundColor Yellow
Write-ChoiceGuidance $true

Write-Host ""
Write-Host "Rollback checkpoint is the current commit:"
Write-Host "  $rollbackCommand"
exit 0
