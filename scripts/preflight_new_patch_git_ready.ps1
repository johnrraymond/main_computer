param(
    [Parameter(Mandatory = $true)]
    [string]$PatchZip,

    [string]$NewPatchScript = "new_patch.py",

    [string]$CommitMessage = "checkpoint before Podman/OpenClaw container runtime patch",

    [string[]]$SecretsFile = @(),

    [switch]$BlockUntrackedJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$blockers = @()
$warnings = @()
$solutions = @()

function Quote-Pwsh([string]$Value) {
    return "'" + ($Value -replace "'", "''") + "'"
}

function Path-Args([string[]]$Paths) {
    return ($Paths | ForEach-Object { Quote-Pwsh $_ }) -join " "
}

function Add-Blocker([string]$Message, [string[]]$Commands = @()) {
    $script:blockers += $Message
    foreach ($command in $Commands) {
        if (-not [string]::IsNullOrWhiteSpace($command)) {
            $script:solutions += $command
        }
    }
}

function Add-Warning([string]$Message) {
    $script:warnings += $Message
}

function Show-List([string]$Title, [string[]]$Items) {
    if ($Items.Count -eq 0) {
        return
    }

    Write-Host ""
    Write-Host $Title -ForegroundColor Yellow
    foreach ($item in $Items) {
        Write-Host "  $item"
    }
}

# ---------------------------------------------------------------------------
# Read-only tool/repo checks
# ---------------------------------------------------------------------------

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Add-Blocker "git is not available on PATH." @(
        "Install Git, then reopen this shell so git is on PATH."
    )
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Add-Blocker "python is not available on PATH." @(
        "Install Python, then reopen this shell so python is on PATH."
    )
}

if ($blockers.Count -gt 0) {
    Write-Host ""
    Write-Host "NOT READY" -ForegroundColor Red
    Show-List "Blockers:" $blockers
    Show-List "Suggested next commands/actions:" $solutions
    exit 1
}

$repoRoot = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repoRoot)) {
    Write-Host ""
    Write-Host "NOT READY" -ForegroundColor Red
    Write-Host "This directory is not inside a Git repository."
    Write-Host ""
    Write-Host "Suggested next command:"
    Write-Host "cd <your-main_computer_test-repo>"
    exit 1
}

$repoRoot = $repoRoot.Trim()
Set-Location $repoRoot

Write-Host "Repository root: $repoRoot" -ForegroundColor Cyan

if (-not (Test-Path $NewPatchScript)) {
    Add-Blocker "Cannot find $NewPatchScript at the repository root." @(
        "cd " + (Quote-Pwsh $repoRoot),
        "dir",
        "Confirm that new_patch.py exists at the repo root."
    )
}

if (-not (Test-Path $PatchZip)) {
    Add-Blocker "Cannot find patch zip: $PatchZip" @(
        "Copy the patch zip into this repo or pass the full path with -PatchZip."
    )
}

foreach ($secret in $SecretsFile) {
    if (-not (Test-Path $secret)) {
        Add-Blocker "Required secrets file is missing: $secret" @(
            "Restore " + (Quote-Pwsh $secret) + " from your private backup/secrets source."
        )
    }
}

# ---------------------------------------------------------------------------
# Refuse active Git operations
# ---------------------------------------------------------------------------

function Git-Path([string]$Name) {
    $path = (& git rev-parse --git-path $Name).Trim()
    return $path
}

$mergeHead = Git-Path "MERGE_HEAD"
$rebaseMerge = Git-Path "rebase-merge"
$rebaseApply = Git-Path "rebase-apply"
$cherryPickHead = Git-Path "CHERRY_PICK_HEAD"
$revertHead = Git-Path "REVERT_HEAD"
$bisectLog = Git-Path "BISECT_LOG"

if (Test-Path $mergeHead) {
    Add-Blocker "A merge is in progress." @(
        "Resolve conflicts, then: git add -- <resolved-files>",
        "Then commit: git commit",
        "Or abort the merge: git merge --abort"
    )
}

if ((Test-Path $rebaseMerge) -or (Test-Path $rebaseApply)) {
    Add-Blocker "A rebase is in progress." @(
        "Resolve conflicts, then: git add -- <resolved-files>",
        "Then continue: git rebase --continue",
        "Or abort the rebase: git rebase --abort"
    )
}

if (Test-Path $cherryPickHead) {
    Add-Blocker "A cherry-pick is in progress." @(
        "Resolve conflicts, then: git add -- <resolved-files>",
        "Then continue: git cherry-pick --continue",
        "Or abort: git cherry-pick --abort"
    )
}

if (Test-Path $revertHead) {
    Add-Blocker "A revert is in progress." @(
        "Resolve conflicts, then: git add -- <resolved-files>",
        "Then continue: git revert --continue",
        "Or abort: git revert --abort"
    )
}

if (Test-Path $bisectLog) {
    Add-Blocker "A bisect is in progress." @(
        "End bisect mode: git bisect reset"
    )
}

# ---------------------------------------------------------------------------
# Branch/HEAD check
# ---------------------------------------------------------------------------

$branch = (& git branch --show-current).Trim()
$currentCommit = (& git rev-parse HEAD).Trim()

if ([string]::IsNullOrWhiteSpace($branch)) {
    Add-Blocker "Repository is in detached HEAD state." @(
        "Create a branch here: git switch -c pre-patch-work",
        "Or return to an existing branch: git switch <branch-name>"
    )
} else {
    Write-Host "Current branch: $branch" -ForegroundColor Cyan
}

Write-Host "Current commit: $currentCommit" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Read-only dirty tree analysis
# ---------------------------------------------------------------------------

$staged = @(& git diff --cached --name-only --)
$unstaged = @(& git diff --name-only --)
$untracked = @(& git ls-files --others --exclude-standard)

$unmergedStatus = @(& git status --porcelain=v1) | Where-Object {
    $_.Length -ge 2 -and @("DD", "AU", "UD", "UA", "DU", "AA", "UU") -contains $_.Substring(0, 2)
}

if ($unmergedStatus.Count -gt 0) {
    Add-Blocker "There are unresolved merge-conflict paths." @(
        "Inspect conflicts: git status",
        "After resolving: git add -- <resolved-files>",
        "Then finish the active merge/rebase/cherry-pick/revert operation."
    )
}

$trackedDirty = @($staged + $unstaged | Sort-Object -Unique)

$untrackedJson = @()
$untrackedBlocking = @()

foreach ($path in $untracked) {
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

                $name = $entry.FullName -replace '\\', '/'

                if ($name.EndsWith("/")) {
                    continue
                }

                if ($name.StartsWith("/") -or $name.Contains("../") -or $name.Contains("/../")) {
                    Add-Blocker "Patch zip contains an unsafe path: $name" @(
                        "Do not apply this patch zip. Rebuild the patch with safe repo-relative paths."
                    )
                    continue
                }

                if ($name.StartsWith("$repoLeaf/")) {
                    $name = $name.Substring($repoLeaf.Length + 1)
                }

                $patchEntriesRepoRelative += $name
            }
        } finally {
            $zip.Dispose()
        }
    } catch {
        Add-Blocker "Could not inspect patch zip entries: $($_.Exception.Message)" @(
            "Confirm the patch zip is valid and not blocked by antivirus/cloud sync."
        )
    }
}

$patchEntriesRepoRelative = @($patchEntriesRepoRelative | Sort-Object -Unique)

$untrackedJsonOverlapsPatch = @()
foreach ($path in $untrackedJson) {
    $normalized = $path -replace '\\', '/'
    if ($patchEntriesRepoRelative -contains $normalized) {
        $untrackedJsonOverlapsPatch += $path
    }
}

if ($untrackedJsonOverlapsPatch.Count -gt 0) {
    Add-Blocker "Untracked JSON files overlap files inside the patch zip." @(
        "Review these paths: " + (Path-Args $untrackedJsonOverlapsPatch),
        "Either commit them intentionally: git add -- " + (Path-Args $untrackedJsonOverlapsPatch),
        "Then: git commit -m " + (Quote-Pwsh "checkpoint JSON files before patch"),
        "Or move them outside the repo before patching."
    )
}

# ---------------------------------------------------------------------------
# Dirty tree decisions
# ---------------------------------------------------------------------------

$pathsToCommit = @($trackedDirty + $untrackedBlocking | Sort-Object -Unique)

if ($pathsToCommit.Count -gt 0) {
    Add-Blocker "There are tracked changes or important untracked files that should be committed before patching." @(
        "git status --short",
        "git add -- " + (Path-Args $pathsToCommit),
        "git commit -m " + (Quote-Pwsh $CommitMessage)
    )
}

if ($untrackedJson.Count -gt 0) {
    Add-Warning "Untracked *.json files were found. They are treated as non-blocking generated/local files unless they overlap the patch zip."
}

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

Show-List "Staged changes:" $staged
Show-List "Unstaged tracked changes:" $unstaged
Show-List "Untracked blocking files:" $untrackedBlocking
Show-List "Untracked non-blocking *.json files:" $untrackedJson
Show-List "Warnings:" $warnings

Write-Host ""

if ($blockers.Count -gt 0) {
    Write-Host "NOT READY TO PATCH" -ForegroundColor Red
    Show-List "Blockers:" $blockers

    $solutions = @($solutions | Sort-Object -Unique)

    Write-Host ""
    Write-Host "Suggested commands/actions. These were NOT run:" -ForegroundColor Yellow
    foreach ($solution in $solutions) {
        Write-Host $solution
    }

    Write-Host ""
    Write-Host "This script made no Git changes." -ForegroundColor Cyan
    exit 1
}

Write-Host "READY FOR PATCH DRY-RUN" -ForegroundColor Green
Write-Host ""
Write-Host "This script made no Git changes." -ForegroundColor Cyan
Write-Host ""
Write-Host "Next command to run manually:"
Write-Host "python " + (Quote-Pwsh $NewPatchScript) + " " + (Quote-Pwsh $PatchZip) + " --dry-run"
Write-Host ""
Write-Host "After the dry-run succeeds, apply manually with:"
Write-Host "python " + (Quote-Pwsh $NewPatchScript) + " " + (Quote-Pwsh $PatchZip)
Write-Host ""
Write-Host "Rollback checkpoint is the current commit:"
Write-Host "git reset --hard $currentCommit"
exit 0