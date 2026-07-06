# OpenClaw Docker Gateway for host Ollama

This compose profile starts a local Docker OpenClaw Gateway that can use the Ollama
models already installed on the Windows host.

The PowerShell helper writes generated state outside the repository under:

```text
%LOCALAPPDATA%\MainComputer\openclaw-docker
```

That state contains the generated OpenClaw config, bearer token, and workspace
memory files. The repository only contains the compose template and helper script.

## Why Docker uses `host.docker.internal:11434`

Inside a Docker container, `127.0.0.1` is the container itself, not the Windows
host. The helper writes OpenClaw config with:

```text
models.providers.ollama.baseUrl = "http://host.docker.internal:11434"
```

It also expands `models.providers.ollama.models` from the host
`http://127.0.0.1:11434/api/tags` response so OpenClaw sees the exact local model
names already installed by Ollama.

The compose file maps the host-published Gateway port to the fixed container
port. For example, `-Port 18790` publishes `127.0.0.1:18790`, while the container OpenClaw still listens on `18789`.

## Default smoke

Run:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b -Port 18790
```

The default smoke is now deterministic. It verifies:

1. Docker can start the OpenClaw Gateway.
2. The container can see the host Ollama model through `host.docker.internal:11434`.
3. `/v1/models` responds.
4. `smoke_openclaw_persistence.py --direct-memory` can write a marker into the
   Docker-mounted OpenClaw Markdown memory workspace.
5. The OpenClaw container can read that marker from `/home/node/.openclaw/workspace`.
6. The marker remains visible after a container restart.

This proves the persistence layer surface before Main Computer grows an
OpenClaw provider.

## Optional agent smoke

The old `/v1/responses` agent smoke is intentionally opt-in because local
OpenClaw agent runs can be slow or can decide to use unrelated tools. After the
direct memory smoke passes, run:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b -Port 18790 -AgentSmoke
```

For the fuller recall test:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b -Port 18790 -FullSmoke
```

Stop the stack with:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Down
```
