$ErrorActionPreference = "Stop"
$ComposeProject = "main-computer-local-platform"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ComposeFile = Join-Path $RepoRoot "deploy\local-platform\docker-compose.yml"

python (Join-Path $RepoRoot "tools\local-platform\verify-docker.py")

docker compose -p $ComposeProject -f $ComposeFile up -d --build
