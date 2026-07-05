# OpenClaw Docker Gateway for host Ollama

This stack runs OpenClaw in Docker while routing its `ollama` provider to the
Ollama daemon on the Windows host. It is intended for the Main Computer
persistence smoke path, where OpenClaw supplies session/memory behavior and
Ollama remains the model runtime.

The important network detail is that `127.0.0.1` inside the OpenClaw container is
the container itself, not Windows. The compose file therefore publishes the
Gateway on host loopback and maps host services through:

```text
http://host.docker.internal:11434
```

## Start

From the repository root in PowerShell:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b
```

The script:

1. checks that Docker is available;
2. checks host Ollama at `http://127.0.0.1:11434/api/tags`;
3. writes an OpenClaw config under `%LOCALAPPDATA%\MainComputer\openclaw-docker`;
4. lists every locally installed Ollama model in `models.providers.ollama.models`;
5. sets `agents.defaults.model.primary` to `ollama/<model>`;
6. starts the OpenClaw Gateway container;
7. probes `http://host.docker.internal:11434/api/tags` from inside the container;
8. probes the Gateway `/v1/models` surface;
9. runs `scripts\smoke_openclaw_persistence.py` when that smoke script exists.

## Stop

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Down
```

## Smoke environment

After start, the script prints the environment values needed by the existing
persistence smoke:

```powershell
$env:MAIN_COMPUTER_OPENCLAW_BASE_URL = "http://127.0.0.1:18789"
$env:MAIN_COMPUTER_OPENCLAW_TOKEN = "<generated-token>"
python scripts\smoke_openclaw_persistence.py --backend-model ollama/gemma4:26b --json
```

The `-Port` option changes only the host-published Gateway port. Inside the
container OpenClaw still listens on `18789`, and Docker maps your chosen host
port to that internal port.

The generated OpenClaw state is intentionally outside the repository so tokens,
memory, session logs, and auth material do not get committed.
