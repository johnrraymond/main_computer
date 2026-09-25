param(
    [string]$MainComputer = 'C:\Users\subsi\main_computer',
    [string]$NanoJev = 'C:\Users\subsi\NanoJev',
    [int]$Cycles = 100,
    [int]$CycleSeconds = 75,
    [int]$TrainFilesPerCycle = 40,
    [int]$TrainRecordsPerCycle = 256,
    [int]$DevRecords = 64,
    [int]$Seed = 17,
    [switch]$Resume,
    [switch]$RestartPartial
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUNBUFFERED = '1'
$Py = Join-Path $NanoJev '.venv\Scripts\python.exe'
$Trainer = Join-Path $MainComputer 'tools\nanojev_round1_stream_train.py'
$Builder = Join-Path $MainComputer 'tools\nanojev_round1_build_code_choices.py'
$Run = Join-Path $NanoJev ("runs\main_computer_code_round1_stream_seed{0}" -f $Seed)
$Tokenizer = Join-Path $NanoJev 'checkpoints\NanoJev-unified\tokenizer'

function Stamp([string]$Message) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

foreach ($Required in @($Py, $Trainer, $Builder, $Tokenizer)) {
    if (-not (Test-Path $Required)) { throw "Missing required path: $Required" }
}
if ($Resume -and $RestartPartial) { throw 'Use either -Resume or -RestartPartial, not both.' }

if (Test-Path $Run) {
    $Existing = Get-ChildItem -Force $Run -ErrorAction SilentlyContinue
    if ($Existing) {
        if ($RestartPartial) {
            Stamp "Removing prior streaming run: $Run"
            Remove-Item -Recurse -Force $Run
        } elseif (-not $Resume) {
            throw "Streaming training output already exists: $Run. Use -Resume or -RestartPartial."
        }
    }
}

Stamp "Starting Round-1 STREAMING frozen-backbone head training"
Stamp "Each cycle: small code shard -> ~${CycleSeconds}s training -> train/dev error -> checkpoint"

$Args = @(
    $Trainer,
    '--repo-root', $MainComputer,
    '--nanojev-root', $NanoJev,
    '--output-dir', $Run,
    '--tokenizer-dir', $Tokenizer,
    '--cycles', $Cycles,
    '--cycle-seconds', $CycleSeconds,
    '--train-files-per-cycle', $TrainFilesPerCycle,
    '--train-records-per-cycle', $TrainRecordsPerCycle,
    '--dev-records', $DevRecords,
    '--candidate-counts', '2,4,8,16',
    '--context-tokens', '192',
    '--lookahead-tokens', '128',
    '--max-file-tokens', '4096',
    '--char-window', '24000',
    '--batch-questions', '8',
    '--microbatch-questions', '4',
    '--max-microbatch-tokens', '8192',
    '--max-length', '384',
    '--head-lr', '1e-3',
    '--seed', $Seed,
    '--precision', 'bf16'
)
if ($Resume) { $Args += '--resume' }

& $Py @Args
if ($LASTEXITCODE -ne 0) { throw "Round-1 streaming head-only training failed" }

Stamp "Round-1 streaming checkpoint: $Run"
Stamp "Cycle history: $(Join-Path $Run 'history.json')"
Stamp "Summary: $(Join-Path $Run 'summary.json')"
