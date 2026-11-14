# Intel Iris Xe only, actual Qwen3-0.6B HDB rescore, read-only.
# Requires torch-directml and Transformers in the isolated DML environment.
param(
  [string]$Python = "$HOME\.venv-nanojev-directml-smoke\Scripts\python.exe",
  [int]$DmlIndex = 1,
  [int]$SmokePerTier = 1
)
$ErrorActionPreference='Stop'
if (-not (Test-Path $Python)) { throw "DirectML Python missing: $Python" }
if ($SmokePerTier -lt 0) { throw 'SmokePerTier must be nonnegative' }
& $Python -m pip show torch-directml
if ($LASTEXITCODE -ne 0) { throw 'Install torch-directml in isolated venv first' }
& $Python -m pip show transformers
if ($LASTEXITCODE -ne 0) { throw 'Run: isolated-python -m pip install transformers==4.56.2 safetensors' }
& $Python -m tools.nanojev_qwen_lm_radius_ladder `
 --stage hdb-rescore --rescore-live --frontier --frontier-hdb `
 --device dml --dml-index $DmlIndex --intel-smoke-per-tier $SmokePerTier `
 --terminal-mode numeric `
 --stage1-checkpoint "$HOME\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
 --text-checkpoint "$HOME\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
 --output "$HOME\NanoJev\runs\qwen_lm_numeric_terminal_v1" `
 --radii 0,1,2,3,4 --frontier-max-radius 64 `
 --operand-limit 1024 --max-rung 8 --batch-size 4
if ($LASTEXITCODE -ne 0) { throw 'Intel-only rescore failed; CUDA was not invoked by this wrapper.' }
