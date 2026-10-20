# T02-only candidate: frozen Qwen+V3 semantic features AND exact input-count channel.
# Original scientific checkpoint, teacher, previous benchmarks, and test split are read-only.
[CmdletBinding()]
param(
    [ValidateSet('All','Prepare','Train','Evaluate','Live')][string]$Stage = 'All',
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$Previous = "$HOME\NanoJev\runs\hawk_dove_t02_continuous_benchmark_v1",
    [string]$Pathway = "$HOME\NanoJev\runs\hawk_dove_numeric_pathway_probe_v1",
    [string]$RunRoot = "$HOME\NanoJev\runs\hawk_dove_numeric_fusion_v1",
    [string]$Donor = "$HOME\NanoJev\runs\hawk_dove_million_scientific_v2",
    [string]$Teacher = 'runtime\hawk-dove-million\teacher-scientific-v2.sqlite3',
    [string]$Source = 'runtime\game-transform-experiment\source-v4.pt',
    [ValidateSet('cuda','cpu')][string]$Device = 'cuda',
    [ValidateRange(1,32)][int]$CaptureBatchSize = 4,
    [ValidateRange(1,23)][int]$LiveCases = 2,
    [ValidateRange(1,2000)][int]$Epochs = 60,
    [ValidateRange(1,4096)][int]$TrainBatchSize = 128,
    [double]$LearningRate = .0003,
    [double]$SemanticScale = .01,
    [int]$Seed = 20261019
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "PYTHON_EXECUTABLE_NOT_FOUND: $Python"
}
$argv = @(
    '-m', 'tools.nanojev_hawk_dove_numeric_fusion_v1', $Stage.ToLowerInvariant(),
    '--previous', $Previous, '--pathway', $Pathway,
    '--root', $RunRoot, '--donor', $Donor,
    '--teacher', $Teacher, '--source', $Source,
    '--device', $Device, '--capture-batch-size', [string]$CaptureBatchSize,
    '--live-cases', [string]$LiveCases,
    '--epochs', [string]$Epochs,
    '--train-batch-size', [string]$TrainBatchSize,
    '--learning-rate', $LearningRate.ToString('R', [System.Globalization.CultureInfo]::InvariantCulture),
    '--semantic-scale', $SemanticScale.ToString('R', [System.Globalization.CultureInfo]::InvariantCulture),
    '--seed', [string]$Seed
)
Write-Host ('> ' + $Python + ' ' + ($argv -join ' '))
& $Python @argv
if ($LASTEXITCODE -ne 0) {
    throw "NANOJEV_NUMERIC_FUSION_FAILED: $Stage (exit $LASTEXITCODE)"
}
