param(
    [string]$Model = "Qwen/Qwen3-0.6B",
    [string]$Revision = "c1899de289a04d12100db370d81485cdf75e47ca",
    [string]$ExperimentDir = "C:\Users\subsi\NanoJev\runs\main_computer_code_lexeme_v1",
    [string]$Python = "C:\Users\subsi\NanoJev\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
& $Python .\tools\nanojev_code_initialize.py `
    --repo-root "C:\Users\subsi\main_computer" `
    --nanojev-root "C:\Users\subsi\NanoJev" `
    --experiment-dir $ExperimentDir `
    --model $Model `
    --revision $Revision
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
