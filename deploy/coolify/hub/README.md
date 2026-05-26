# Coolify Hub Deployment Surface

This directory contains the remote-hub Docker Compose surface that can also be
run locally before deploying to Coolify.

The important default is: **no `.env` file is required for local Docker use**.
The hub code owns its normal defaults in `main_computer/config.py`. The compose
file only supplies container deployment facts that the application cannot infer:

- bind the hub to `0.0.0.0` inside the container
- listen on container port `8770`
- store hub runtime state under `/runtime/hub`
- persist `/runtime/hub` in a named Docker volume

## Slim Docker context

The hub image intentionally does **not** copy the whole repository.

`deploy/coolify/hub/Dockerfile` copies only:

- `pyproject.toml`
- `README.md`
- `main_computer/`

`deploy/coolify/hub/Dockerfile.dockerignore` is a Dockerfile-specific ignore
file that keeps local/generated directories out of this image build context,
including diagnostics, release reports, runtime state, tools output, browser
profiles, and old patch archives.

This keeps local Docker and Coolify builds focused on the hub runtime instead of
uploading the operator's whole working tree.

## Local container run

From the repository root:

```powershell
docker compose -f deploy/coolify/hub/docker-compose.yml up --build
```

Then, in another terminal:

```powershell
python scripts/smoke_remote_hub.py --hub-url http://127.0.0.1:8770
```

Stop the local container:

```powershell
docker compose -f deploy/coolify/hub/docker-compose.yml down
```

Remove the local runtime volume only when you intentionally want to reset hub
state:

```powershell
docker compose -f deploy/coolify/hub/docker-compose.yml down -v
```

## Optional local overrides

The local host port can be changed without creating a full env file:

```powershell
$env:MAIN_COMPUTER_HUB_PORT = "8870"
docker compose -f deploy/coolify/hub/docker-compose.yml up --build
```

Then smoke:

```powershell
python scripts/smoke_remote_hub.py --hub-url http://127.0.0.1:8870
```

## Coolify settings

Use Coolify's Docker Compose build pack with:

```text
Base Directory: /
Docker Compose Location: deploy/coolify/hub/docker-compose.yml
Service port: 8770
```

Do not add custom Compose networks for this app surface. Let Coolify attach the
application to the network it manages.

## Security status

This deployment surface is intentionally only packaging. It does not add auth
enforcement by itself. The current hub still accepts local-dev style API calls
unless/until the R6-lite token patch is added.

Before treating the remote hub as safe for untrusted public use, add and verify
token enforcement for:

- admin/control endpoints
- worker registration and heartbeat
- user request/session endpoints
- credit import/admin endpoints

## What this does not deploy yet

This compose file runs only the hub service. It does not start:

- an Ollama worker
- a tunnel/VPN sidecar
- RPC/indexer sync
- paid request charging

Workers should be added after the remote hub surface has passed the local and
Coolify smoke tests.
