# Isolated read-only T02 benchmark; existing checkpoint and teacher are never modified.
[CmdletBinding()]
param(
    [ValidateSet('All','Prepare','Train','Probe','Test')]
    [string]$Stage = 'All',
    [Parameter(Mandatory=$true)] [string]$Python,
    [string]$Donor = "$HOME\NanoJev\runs\hawk_dove_million_scientific_v2",
    [string]$RunRoot = "$HOME\NanoJev\runs\hawk_dove_t02_continuous_benchmark_v1",
    [string]$Teacher = 'runtime\hawk-dove-million\teacher-scientific-v2.sqlite3',
    [string]$Source = 'runtime\game-transform-experiment\source-v4.pt',
    [ValidateSet('cuda','cpu')] [string]$Device = 'cuda',
    [ValidateSet('cpu','cuda')] [string]$TrainingDevice = 'cpu',
    [ValidateRange(1,32)] [int]$CaptureBatchSize = 4,
    [ValidateRange(1,3000)] [int]$Epochs = 120,
    [ValidateRange(1,8192)] [int]$TrainBatchSize = 128,
    [int]$Seed = 20261019,
    [ValidateSet('direct','symlog','twohot','operands')] [string]$Winner = 'direct'
)
$ErrorActionPreference = 'Stop'
$subcommand = switch ($Stage) {
    'All' { 'all' }
    'Prepare' { 'prepare' }
    'Train' { 'train' }
    'Probe' { 'probe' }
    'Test' { 'test' }
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "PYTHON_EXECUTABLE_NOT_FOUND: $Python"
}
if ($Stage -eq 'Test' -and -not $PSBoundParameters.ContainsKey('Winner')) {
    throw 'TEST_REQUIRES_EXPLICIT_WINNER: -Winner direct|symlog|twohot|operands'
}
$argv = @(
    '-m','tools.nanojev_hawk_dove_continuous_benchmark',$subcommand,
    '--donor', $Donor,
    '--root', $RunRoot,
    '--teacher', $Teacher,
    '--source', $Source,
    '--device', $Device,
    '--train-device', $TrainingDevice,
    '--capture-batch-size', [string]$CaptureBatchSize,
    '--epochs', [string]$Epochs,
    '--train-batch-size', [string]$TrainBatchSize,
    '--seed', [string]$Seed
)
if ($Stage -eq 'Test') { $argv += @('--winner',$Winner) }
Write-Host ('> ' + $Python + ' ' + ($argv -join ' '))
& $Python @argv
if ($LASTEXITCODE -ne 0) { throw "CONTINUOUS_T02_STAGE_FAILED: $Stage (exit $LASTEXITCODE)" }
