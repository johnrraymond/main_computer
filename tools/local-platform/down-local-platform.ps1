$ErrorActionPreference = "Stop"
$ComposeProject = "main-computer-local-platform"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ComposeFile = Join-Path $RepoRoot "deploy\local-platform\docker-compose.yml"

docker compose -p $ComposeProject -f $ComposeFile down
