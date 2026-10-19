# A lossless frozen-Qwen feature-cache accelerator for the existing scientific v2 lineage.
# Existing checkpoints and trainer source SHA remain unchanged.
[CmdletBinding()]
param(
    [ValidateSet('Train','Probe','Diagnose')][string]$Stage = 'Train',
    [string]$Python = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe',
    [string]$Run = "$HOME\NanoJev\runs\hawk_dove_million_scientific_v2",
    [string]$Teacher = 'runtime\hawk-dove-million\teacher-scientific-v2.sqlite3',
    [string]$Source = 'runtime\game-transform-experiment\source-v4.pt',
    [long]$AdditionalSteps = 6000000,
    [int]$ChunkSteps = 300,
    [int]$CacheMemoryMB = 1024,
    [switch]$DiagnoseBoth,
    [ValidateSet('T00','T01','T02','T03','T04','T05','T06','T07','T08')][string]$DiagnoseTier = 'T01'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ck = Join-Path $Run 'latest.pt'
if (-not (Test-Path -LiteralPath $ck -PathType Leaf)) {throw "Missing checkpoint: $ck"}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {throw "Missing Python: $Python"}
function Invoke-NanoJev([string[]]$Parts) {
    & $Python @Parts
    if ($LASTEXITCODE -ne 0) { throw "NanoJev command failed with exit code $LASTEXITCODE" }
}
$prefix=@('-m','tools.nanojev_hawk_dove_cached_train','--cache-memory-mb',"$CacheMemoryMB")
$baseArgs=@('--teacher',$Teacher,'--source',$Source,'--output',$Run,'--device','cuda')
if ($Stage -eq 'Train') {
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
    Invoke-NanoJev ($prefix + @('probe') + $baseArgs)
}
$tiers = if ($DiagnoseBoth) { @('T00','T01') } else { @($DiagnoseTier) }
if ($Stage -eq 'Diagnose' -or $Stage -eq 'Train') {
    foreach ($tier in $tiers) {
        Invoke-NanoJev ($prefix + @('diagnose') + $baseArgs + @('--diagnose-tier',$tier))
    }
}
