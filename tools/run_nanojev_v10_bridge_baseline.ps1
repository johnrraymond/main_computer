# Run from the main_computer repository root after installing this source patch.
# Rebuilds into a NEW immutable teacher path. Does not alter V8, V9, original
# V10 teacher, parent weights, or an existing V10 training lineage.
$ErrorActionPreference = 'Stop'
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$source = 'runtime/game-math-source-smoke-v8/game_transformations.sqlite3'
$old = 'runtime/game-math-curriculum-v10/teacher.sqlite3'
$new = 'runtime/game-math-curriculum-v10-bridge/teacher.sqlite3'
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_parent.pt"
$oldReport = "$HOME\NanoJev\runs\game_math_xy_v10\baseline-probe.json"
$output = "$HOME\NanoJev\runs\game_math_xy_v10_bridge\baseline-bridge.json"
$sourceWeights = 'runtime/game-transform-experiment/source-v4.pt'

foreach ($path in @($py,$source,$old,$parent,$sourceWeights)) {
    if (-not (Test-Path $path)) { throw "Missing required input: $path" }
}
if (-not (Test-Path $new)) {
    & $py -m tools.nanojev_game_math_curriculum_v10_build build `
      --native-db $source --output $new
    if ($LASTEXITCODE -ne 0) { throw 'Bridge teacher build failed' }
}
& $py -m tools.nanojev_game_math_curriculum_v10_build verify --teacher-db $new
if ($LASTEXITCODE -ne 0) { throw 'Bridge teacher verification failed' }

$argsCompare = @(
  '-m', 'tools.nanojev_game_math_curriculum_v10_bridge_compare',
  '--old-teacher', $old, '--teacher-db', $new,
  '--source-checkpoint', $sourceWeights, '--checkpoint', $parent,
  '--split', 'dev', '--limit', '48', '--device', 'cuda',
  '--tiers', 'T00', 'T01', 'T02', '--output', $output
)
if (Test-Path $oldReport) { $argsCompare += @('--old-report', $oldReport) }
& $py @argsCompare
if ($LASTEXITCODE -ne 0) { throw 'Bridge same-case baseline failed' }
Write-Host "PASS: verified teacher, same 48 cases, and neural baseline at $output"
