# Existing scientific training lineage; opt-in exact numerical input adapter.
# -NumericAdapter migrates the current checkpoint IN PLACE, with a verified backup.
# No V3 math-head change, new lineage, or optimizer reset.
[CmdletBinding()]
param(
    [ValidateSet('Train','Probe','Diagnose','Upgrade','SidecarPreflight','SidecarUpgrade')][string]$Stage = 'Train',
    [string]$Python = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe',
    [string]$Run = "$HOME\NanoJev\runs\hawk_dove_million_scientific_v2",
    [string]$Teacher = 'runtime\hawk-dove-million\teacher-scientific-v2.sqlite3',
    [string]$Source = 'runtime\game-transform-experiment\source-v4.pt',
    [long]$AdditionalSteps = 6000000,
    [int]$ChunkSteps = 300,
    [int]$CacheMemoryMB = 1024,
    [switch]$NumericAdapter,
    [switch]$NumericSidecar,
    [switch]$DiagnoseBoth,
    [ValidateSet('T00','T01','T02','T03','T04','T05','T06','T07','T08')][string]$DiagnoseTier = 'T01'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ck = Join-Path $Run 'latest.pt'
if (-not (Test-Path -LiteralPath $ck -PathType Leaf)) {throw "Missing checkpoint: $ck"}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {throw "Missing Python: $Python"}
function Invoke-NanoJev([string[]]$Parts) {
    # Existing NanoJev modules use imports like
    # "import nanojev_numerate_spine_cassette_smoke" from inside tools/.
    # python -m tools.module puts the repository root, not tools/, on sys.path.
    # Set PYTHONPATH *for this child process only* so both import styles work,
    # regardless of whether the adapter module itself bootstraps sys.path.
    $savedPythonPath = $env:PYTHONPATH
    $separator = [System.IO.Path]::PathSeparator
    $env:PYTHONPATH = if ([string]::IsNullOrEmpty($savedPythonPath)) {
        $PSScriptRoot
    } else {
        "$PSScriptRoot$separator$savedPythonPath"
    }
    try {
        & $Python @Parts
        if ($LASTEXITCODE -ne 0) { throw "NanoJev command failed with exit code $LASTEXITCODE" }
    } finally {
        $env:PYTHONPATH = $savedPythonPath
    }
}
# Once upgraded, this run must always use the numerical-capable loader.
# Fail closed rather than silently dropping the registered adapter.
$code='import sys,torch; p=torch.load(sys.argv[1],map_location=sys.argv[2],weights_only=True); print(int(sys.argv[3] in p))'
$isUpgraded=& $Python -c $code $ck 'cpu' 'numeric_adapter_contract'
$isSidecar=& $Python -c $code $ck 'cpu' 'numeric_sidecar_contract'
if ($LASTEXITCODE -ne 0 -or "$isSidecar" -notmatch '^[01]$') {throw 'Cannot inspect numerical sidecar checkpoint contract'}
if ($isSidecar -eq '1' -and -not $NumericSidecar) {
    throw 'NUMERIC_SIDECAR_RUNNER_REQUIRED: checkpoint has sidecar; add -NumericSidecar.'
}
if ($NumericSidecar -and $isUpgraded -ne '1') {throw 'Install -NumericAdapter checkpoint before numerical sidecar'}
if ($NumericSidecar -and -not $NumericAdapter) {throw 'Use -NumericSidecar -NumericAdapter together'}
if ($LASTEXITCODE -ne 0 -or "$isUpgraded" -notmatch '^[01]$') {throw 'Cannot inspect numeric-adapter checkpoint contract'}
if ($isUpgraded -eq '1' -and -not $NumericAdapter) {
    throw 'NUMERIC_ADAPTER_RUNNER_REQUIRED: this checkpoint was upgraded. Add -NumericAdapter.'
}
if ($Stage -eq 'Upgrade' -and $NumericSidecar) {throw 'Use SidecarUpgrade for a numerical sidecar'}
if ($Stage -eq 'Upgrade' -and -not $NumericAdapter) {
    throw 'Upgrade requires -NumericAdapter'
}
$module=if ($NumericSidecar) {'tools.nanojev_hawk_dove_numeric_sidecar_train'} elseif ($NumericAdapter) {'tools.nanojev_hawk_dove_numeric_adapter_train'} else {'tools.nanojev_hawk_dove_cached_train'}
$prefix=@('-m',$module,'--cache-memory-mb',"$CacheMemoryMB")
if ($NumericSidecar -and -not $PSBoundParameters.ContainsKey('AdditionalSteps')) {
    $AdditionalSteps=300
} elseif ($NumericAdapter -and -not $PSBoundParameters.ContainsKey('AdditionalSteps')) {
    $AdditionalSteps=12000  # bounded first trial; ordinary runner retains existing default
}
$baseArgs=@('--teacher',$Teacher,'--source',$Source,'--output',$Run,'--device','cuda')
if ($Stage -eq 'SidecarPreflight') {
    if (-not $NumericSidecar) {throw 'Sidecar preflight requires -NumericSidecar -NumericAdapter'}
    Invoke-NanoJev ($prefix + @('--preflight-only','probe') + $baseArgs)
    return
}
if ($Stage -eq 'SidecarUpgrade') {
    if (-not $NumericSidecar) {throw 'Sidecar upgrade requires -NumericSidecar -NumericAdapter'}
    Invoke-NanoJev ($prefix + @('--upgrade-only','probe') + $baseArgs)
    Write-Host 'Numerical sidecar upgraded in place; no optimizer steps taken.'
    return
}
if ($Stage -eq 'Upgrade') {
    Invoke-NanoJev ($prefix + @('--upgrade-only','probe') + $baseArgs)
    Write-Host 'Checkpoint upgraded in place; adapter starts at exactly zero. No optimizer steps taken.'
    return
}
if ($Stage -eq 'Train') {
    if ($NumericSidecar -and $isSidecar -ne '1') {throw 'NUMERIC_SIDECAR_UPGRADE_REQUIRED before training'}
    if ($AdditionalSteps -le 0 -or $ChunkSteps -le 0) { throw 'Steps must be positive' }
    $code='import sys,torch; p=torch.load(sys.argv[1],map_location=sys.argv[2],weights_only=True); print(int(p[sys.argv[3]]))'
    $raw=& $Python -c $code $ck 'cpu' 'step'
    if ($LASTEXITCODE -ne 0 -or "$raw" -notmatch '^[0-9]+$') { throw 'Cannot read checkpoint step' }
    $step=[long]$raw
    $target=$step + $AdditionalSteps
    if ($target -gt [int]::MaxValue) { throw 'Step target out of range' }
    Write-Host "Cached scientific training, starting at $step; target=$target; additional=$AdditionalSteps"
    Invoke-NanoJev ($prefix + @('train') + $baseArgs + @('--steps',"$target",'--chunk-steps',"$ChunkSteps"))
    Write-Host 'Training complete; beginning post-training diagnoses'
}
if ($Stage -eq 'Probe' -or $Stage -eq 'Train') {
    if ($NumericSidecar -and $isSidecar -ne '1') {throw 'NUMERIC_SIDECAR_UPGRADE_REQUIRED before probing'}
    Invoke-NanoJev ($prefix + @('probe') + $baseArgs)
}
$tiers = if ($DiagnoseBoth) { @('T00','T01') } else { @($DiagnoseTier) }
if ($Stage -eq 'Diagnose' -or $Stage -eq 'Train') {
    if ($NumericSidecar -and $isSidecar -ne '1') {throw 'NUMERIC_SIDECAR_UPGRADE_REQUIRED before diagnosis'}
    foreach ($tier in $tiers) {
        Invoke-NanoJev ($prefix + @('diagnose') + $baseArgs + @('--diagnose-tier',$tier))
    }
}
