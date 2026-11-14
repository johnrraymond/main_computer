param(
    [string]$ExperimentDir = "C:\Users\subsi\NanoJev\runs\main_computer_code_lexeme_v1",
    [int]$CyclesThisRun = 100,
    [double]$CycleSeconds = 75,
    [string]$Python = "C:\Users\subsi\NanoJev\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
& $Python .\tools\nanojev_code_train.py `
    --experiment-dir $ExperimentDir `
    --cycles-this-run $CyclesThisRun `
    --cycle-seconds $CycleSeconds
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
