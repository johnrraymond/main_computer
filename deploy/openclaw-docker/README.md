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

## High-fidelity memory extraction

After the direct memory smoke passes, export the OpenClaw Markdown persistence
surface without summarizing it:

```powershell
python scripts\extract_openclaw_persistence.py --memory-root "%LOCALAPPDATA%\MainComputer\openclaw-docker\workspace" --out "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.json" --jsonl-out "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.jsonl" --markdown-out "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.md" --summary-json
```

The exporter preserves exact source text, SHA-256 hashes, line spans, heading
paths, and section records. This gives Main Computer a deterministic persistence
ingest surface before any `/v1/responses` agent integration is added.

You can also ask the helper to run the extraction:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b -Port 18790 -NoSmoke -ExtractMemory
```


## High-fidelity memory pushback


### Automated pushback smoke

To prove the pushback surface without manually editing JSON, run:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b -Port 18790 -NoSmoke -PushbackSmoke
```

That one command creates or reuses `memory/YYYY-MM-DD-main-computer-pushback-smoke.md`,
exports high-fidelity persistence, edits the exported JSON text payload with a
unique marker, applies it back with expected-current SHA checks, re-extracts the
workspace, verifies the marker, asks the running OpenClaw container to read the
marker from its mounted workspace, restarts the container, and verifies the
marker again.

The standalone form is:

```powershell
python scripts\smoke_openclaw_persistence_pushback.py --memory-root "%LOCALAPPDATA%\MainComputer\openclaw-docker\workspace" --export-dir "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports" --container main-computer-openclaw-gateway --restart-container --gateway-url http://127.0.0.1:18790 --json
```

Round-trip edits through the JSON or JSONL export, not the rendered Markdown
review file. The `sha256` value in each file record is treated as the expected
current hash from extraction time, so you can edit `file.text` while leaving
`sha256` unchanged. That lets the apply step detect whether OpenClaw memory
changed underneath you before writing.

Dry run first:

```powershell
python scripts\apply_openclaw_persistence.py --memory-root "%LOCALAPPDATA%\MainComputer\openclaw-docker\workspace" --export "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.json" --dry-run --summary-json
```

Apply and verify readback:

```powershell
python scripts\apply_openclaw_persistence.py --memory-root "%LOCALAPPDATA%\MainComputer\openclaw-docker\workspace" --export "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.json" --verify-after --summary-json
```

The helper can also apply the edited export and restart the OpenClaw container so
the runtime sees the pushed-back Markdown files:

```powershell
.\scripts\start_openclaw_docker_for_ollama.ps1 -Model gemma4:26b -Port 18790 -NoSmoke -ApplyMemoryExport "%LOCALAPPDATA%\MainComputer\openclaw-docker\exports\openclaw-persistence.json"
```

Use `-ApplyMemoryDryRun` to test the helper path without writing. Use
`-ApplyMemoryForce` only when you intentionally want to bypass the expected-current
SHA guard and allow creates.
