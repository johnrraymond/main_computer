# OpenClaw container helper for local Ollama

This helper runs the OpenClaw Gateway container against the host Ollama server and keeps all generated OpenClaw state outside the repository.

By default, state is written under `%LOCALAPPDATA%\MainComputer\openclaw-docker` on Windows, or `~/.main-computer/openclaw-docker` when `LOCALAPPDATA` is not available. The helper writes the Gateway config, workspace, auth profile secrets, extraction exports, and smoke outputs there so repo checkouts stay clean.

## Runtime selection

The PowerShell launcher can use Docker or Podman:

```powershell
# historical default
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b

# Podman
$env:MAIN_COMPUTER_CONTAINER_RUNTIME = "podman"
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b
```

You can also pass `-ContainerRuntime podman`, `-ContainerCommand podman`, or `-ComposeCommand "podman compose"` directly. The launcher still keeps the historical `openclaw-docker` folder and script name for compatibility.

Docker containers reach host Ollama through `host.docker.internal:11434`; the compose file includes a `host.docker.internal:host-gateway` mapping for Linux Docker hosts. Podman runs default to `host.containers.internal:11434`. Override either case with `-ContainerOllamaUrl` or `OPENCLAW_CONTAINER_OLLAMA_URL`.

The host-published Gateway port is controlled by `-Port` / `OPENCLAW_GATEWAY_PORT`, but the container OpenClaw still listens on `18789`. The launcher writes that internal port into the Gateway config.

## Generated config shape

The launcher discovers local Ollama models from `http://127.0.0.1:11434/api/tags`, then writes an OpenClaw config whose `models.providers.ollama.models` value is always a JSON array. The Gateway uses the native Ollama provider, not `/v1`, so the generated provider base URL is either `http://host.docker.internal:11434` or `http://host.containers.internal:11434`.

## Smoke tests

The default smoke first runs `smoke_openclaw_persistence.py` in direct memory mode. This direct memory smoke writes a Markdown fact under the mounted workspace, then asks the running OpenClaw container to read that marker from `/home/node/.openclaw/workspace`.

Pass `-AgentSmoke` to additionally exercise `/v1/responses`; pass `-FullSmoke` to keep the recall turns enabled. The direct smoke is intentionally the default because it proves the persistence mount quickly without waiting on a large local model.

## High-fidelity memory extraction

Use `extract_openclaw_persistence.py` to create a JSON, JSONL, and Markdown export from the mounted workspace:

```powershell
python scripts\extract_openclaw_persistence.py --memory-root "%LOCALAPPDATA%\MainComputer\openclaw-docker\workspace" --out "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.json" --jsonl-out "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.jsonl" --markdown-out "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.md" --summary-json
```

The export records include source paths, byte counts, and SHA-256 hashes so later pushback can verify the expected-current content before modifying files.

## High-fidelity memory pushback

Use `apply_openclaw_persistence.py` after editing the JSON export:

```powershell
python scripts\apply_openclaw_persistence.py --memory-root "%LOCALAPPDATA%\MainComputer\openclaw-docker\workspace" --export "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.json" --dry-run --summary-json
python scripts\apply_openclaw_persistence.py --memory-root "%LOCALAPPDATA%\MainComputer\openclaw-docker\workspace" --export "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.json" --verify-after --summary-json
```

Pushback uses expected-current SHA-256 checks by default and writes backups before replacing Markdown memory files.

## Automated pushback smoke

Pass `-PushbackSmoke` to run `smoke_openclaw_persistence_pushback.py`. It extracts the workspace, edits a memory Markdown record in memory, applies that edited export with expected-current checks, re-extracts, and optionally probes the running container before and after restart.

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b -NoSmoke -PushbackSmoke
```

For one-shot pushback from a hand-edited export, use `-ApplyMemoryExport`:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b -NoSmoke -ApplyMemoryExport "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.json"
```
