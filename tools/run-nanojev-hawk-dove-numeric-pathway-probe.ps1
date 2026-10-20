# Separate, read-only numerical pathway experiment; does not edit old benchmark or donor.
[CmdletBinding()]
param(
    [ValidateSet('All','Prepare','Capture','Fit','Report')][string]$Stage = 'All',
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$Previous = "$HOME\NanoJev\runs\hawk_dove_t02_continuous_benchmark_v1",
    [string]$RunRoot = "$HOME\NanoJev\runs\hawk_dove_numeric_pathway_probe_v1",
    [string]$Donor = "$HOME\NanoJev\runs\hawk_dove_million_scientific_v2",
    [string]$Teacher = 'runtime\hawk-dove-million\teacher-scientific-v2.sqlite3',
    [string]$Source = 'runtime\game-transform-experiment\source-v4.pt',
    [ValidateSet('cpu','cuda')][string]$Device = 'cuda',
    [ValidateSet('cpu','cuda')][string]$TrainingDevice = 'cpu',
    [ValidateRange(1,32)][int]$CaptureBatchSize = 4,
    [ValidateRange(1,3000)][int]$Epochs = 80,
    [ValidateRange(1,8192)][int]$TrainBatchSize = 128,
    [int]$Seed = 20261019
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "PYTHON_EXECUTABLE_NOT_FOUND: $Python" }
$argv = @('-m','tools.nanojev_hawk_dove_numeric_pathway_probe', $Stage.ToLowerInvariant(),
    '--previous',$Previous,'--root',$RunRoot,'--donor',$Donor,
    '--teacher',$Teacher,'--source',$Source,
    '--device',$Device,'--train-device',$TrainingDevice,
    '--capture-batch-size',[string]$CaptureBatchSize,
    '--epochs',[string]$Epochs,'--train-batch-size',[string]$TrainBatchSize,'--seed',[string]$Seed)
Write-Host ('> ' + $Python + ' ' + ($argv -join ' '))
& $Python @argv
if ($LASTEXITCODE -ne 0) { throw "NUMERIC_PATHWAY_PROBE_FAILED: $Stage (exit $LASTEXITCODE)" }
