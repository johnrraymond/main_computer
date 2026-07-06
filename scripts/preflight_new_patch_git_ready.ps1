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

$script:blockers = New-Object 'System.Collections.Generic.List[string]'
$script:warnings = New-Object 'System.Collections.Generic.List[string]'
$script:solutions = New-Object 'System.Collections.Generic.List[string]'

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

function Add-Blocker([string]$Message, $Commands = @()) {
    Add-UniqueLine $script:blockers $Message

    foreach ($command in @($Commands)) {
        Add-UniqueLine $script:solutions ([string]$command)
    }
}

function Add-Warning([string]$Message) {
    Add-UniqueLine $script:warnings $Message
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

function Git-Path([string]$Name) {
    $path = (& git rev-parse --git-path $Name 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($path)) {
        return ""
    }

    return $path.Trim()
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

function Write-NotReadyAndExit([string]$Title = "NOT READY") {
    Write-Host ""
    Write-Host $Title -ForegroundColor Red
    Show-List "Blockers:" $script:blockers

    if ($script:solutions.Count -gt 0) {
        Write-Host ""
        Write-Host "Suggested commands/actions. These were NOT run:" -ForegroundColor Yellow
        foreach ($solution in $script:solutions) {
            Write-Host "  $solution"
        }
    }

    Write-Host ""
    Write-Host "This script made no Git changes and did not apply the patch." -ForegroundColor Cyan
    exit 1
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

if ($script:blockers.Count -gt 0) {
    Write-NotReadyAndExit
}

$repoRoot = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repoRoot)) {
    Add-Blocker "This directory is not inside a Git repository." @(
        "cd <your-main-computer-repo>"
    )
    Write-NotReadyAndExit
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

foreach ($secret in @($SecretsFile)) {
    if (-not (Test-Path $secret)) {
        Add-Blocker "Required secrets file is missing: $secret" @(
            "Restore " + (Quote-Pwsh $secret) + " from your private backup/secrets source."
        )
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
    Add-Blocker "A merge is in progress." @(
        "git status",
        "After resolving conflicts: git add -- <resolved-files>",
        "Then finish the merge: git commit",
        "Or abort the merge: git merge --abort"
    )
}

if ((-not [string]::IsNullOrWhiteSpace($rebaseMerge) -and (Test-Path $rebaseMerge)) -or
    (-not [string]::IsNullOrWhiteSpace($rebaseApply) -and (Test-Path $rebaseApply))) {
    Add-Blocker "A rebase is in progress." @(
        "git status",
        "After resolving conflicts: git add -- <resolved-files>",
        "Then continue: git rebase --continue",
        "Or abort the rebase: git rebase --abort"
    )
}

if (-not [string]::IsNullOrWhiteSpace($cherryPickHead) -and (Test-Path $cherryPickHead)) {
    Add-Blocker "A cherry-pick is in progress." @(
        "git status",
        "After resolving conflicts: git add -- <resolved-files>",
        "Then continue: git cherry-pick --continue",
        "Or abort: git cherry-pick --abort"
    )
}

if (-not [string]::IsNullOrWhiteSpace($revertHead) -and (Test-Path $revertHead)) {
    Add-Blocker "A revert is in progress." @(
        "git status",
        "After resolving conflicts: git add -- <resolved-files>",
        "Then continue: git revert --continue",
        "Or abort: git revert --abort"
    )
}

if (-not [string]::IsNullOrWhiteSpace($bisectLog) -and (Test-Path $bisectLog)) {
    Add-Blocker "A bisect is in progress." @(
        "End bisect mode: git bisect reset"
    )
}

# ---------------------------------------------------------------------------
# Branch/HEAD check
# ---------------------------------------------------------------------------

$branch = (& git branch --show-current 2>$null)
if ($LASTEXITCODE -ne 0) {
    $branch = ""
} else {
    $branch = $branch.Trim()
}

$currentCommit = (& git rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($currentCommit)) {
    $currentCommit = "<unknown>"
} else {
    $currentCommit = $currentCommit.Trim()
}

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
# Use `git status --porcelain` instead of `git diff` here. On Windows with
# autocrlf/safecrlf warnings, native git stderr can become a terminating
# PowerShell NativeCommandError under `$ErrorActionPreference = "Stop"`.
# `git status` is enough for this guard and avoids mutating or refreshing files.

$statusLines = @(& git -c core.safecrlf=false status --porcelain=v1 --untracked-files=all)

if ($LASTEXITCODE -ne 0) {
    Add-Blocker "Could not read Git working-tree status." @(
        "git status",
        "Fix any Git errors shown above, then rerun this preflight script."
    )
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
    Add-Blocker "There are unresolved merge-conflict paths." @(
        "git status",
        "After resolving conflicts: git add -- <resolved-files>",
        "Then finish the active merge/rebase/cherry-pick/revert operation."
    )
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
                    Add-Blocker "Patch zip contains an unsafe path: $name" @(
                        "Do not apply this patch zip. Rebuild the patch with safe repo-relative paths."
                    )
                    continue
                }

                $patchEntriesRepoRelative += (Strip-KnownPatchRoot $name $repoLeaf)
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
foreach ($path in @($untrackedJson)) {
    $normalized = $path -replace '\\', '/'
    if ($patchEntriesRepoRelative -contains $normalized) {
        $untrackedJsonOverlapsPatch += $path
    }
}

if (@($untrackedJsonOverlapsPatch).Count -gt 0) {
    $jsonArgs = Join-PathArgs $untrackedJsonOverlapsPatch
    $jsonCommitMessage = Quote-Pwsh "checkpoint JSON files before patch"

    Add-Blocker "Untracked JSON files overlap files inside the patch zip." @(
        "git status --short",
        "Review overlapping JSON paths: $jsonArgs",
        "To commit them intentionally: git add -- $jsonArgs",
        "Then commit them: git commit -m $jsonCommitMessage",
        "Or move those JSON files outside the repo before patching."
    )
}

# ---------------------------------------------------------------------------
# Dirty tree decisions
# ---------------------------------------------------------------------------

$pathsToCommit = @($trackedDirty + $untrackedBlocking | Sort-Object -Unique)

if (@($pathsToCommit).Count -gt 0) {
    $commitPathArgs = Join-PathArgs $pathsToCommit
    $commitMessageArg = Quote-Pwsh $CommitMessage

    Add-Blocker "There are tracked changes or important untracked files that should be committed before patching." @(
        "git status --short",
        "git add -- $commitPathArgs",
        "git commit -m $commitMessageArg"
    )
}

if (@($untrackedJson).Count -gt 0) {
    Add-Warning "Untracked *.json files were found. They are non-blocking generated/local files unless they overlap the patch zip. They were not included in the suggested commit command. Make sure your secrets file is present so the app can regenerate local JSON state."
}

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

Show-List "Staged changes:" $staged
Show-List "Unstaged tracked changes:" $unstaged
Show-List "Untracked blocking files:" $untrackedBlocking
Show-List "Untracked non-blocking *.json files:" $untrackedJson
Show-List "Warnings:" $script:warnings

Write-Host ""

if ($script:blockers.Count -gt 0) {
    Write-Host "NOT READY TO PATCH" -ForegroundColor Red
    Show-List "Blockers:" $script:blockers

    if ($script:solutions.Count -gt 0) {
        Write-Host ""
        Write-Host "Suggested commands/actions. These were NOT run:" -ForegroundColor Yellow
        foreach ($solution in $script:solutions) {
            Write-Host "  $solution"
        }
    }

    Write-Host ""
    Write-Host "This script made no Git changes and did not apply the patch." -ForegroundColor Cyan
    exit 1
}

$dryRunCommand = "python " + (Quote-Pwsh $NewPatchScript) + " " + (Quote-Pwsh $PatchZip) + " --dry-run"
$applyCommand = "python " + (Quote-Pwsh $NewPatchScript) + " " + (Quote-Pwsh $PatchZip)
$rollbackCommand = "git reset --hard $currentCommit"

Write-Host "READY FOR PATCH DRY-RUN" -ForegroundColor Green
Write-Host ""
Write-Host "This script made no Git changes and did not apply the patch." -ForegroundColor Cyan
Write-Host ""
Write-Host "Next command to run manually:"
Write-Host "  $dryRunCommand"
Write-Host ""
Write-Host "After the dry-run succeeds, apply manually with:"
Write-Host "  $applyCommand"
Write-Host ""
Write-Host "Rollback checkpoint is the current commit:"
Write-Host "  $rollbackCommand"
exit 0
