# Dev Docker Stack

This is the local-development Docker support stack. It deliberately does not
wrap the hub runtime in Docker.

The old dev-stack hub service has been removed, and this stack no longer
includes an Ollama service. Run the hub on the host or point workers at a
remote hub; run Ollama on the host or point workers at a remote model server.
This stack keeps only optional workers, ONLYOFFICE, and executor image targets.
The app-facing Anvil dev chain is managed by `tools/dev-chain-reset.py`, not Docker Compose.

## What this brings up

```text
docker-compose.dev.yml
├─ hub-worker       optional Ollama-backed worker on :8771
├─ main-computer    optional viewport container on :8765
├─ onlyoffice       optional ONLYOFFICE Docs service
└─ executor-image   build target for the existing DockerExecutor image
```

## Start the shared dev chain

```powershell
python .\tools\dev-chain-reset.py --yes --run-id test-machine-dev --environment dev --port-strategy replace-project
```

This publishes `runtime/deployments/current.json`, which the app verifies before using bridge contracts.

## Start the hub on the host

Use the named Hub network profiles rather than hardcoding the Hub bind port.
The dev profile listens on `127.0.0.1:8770` and talks to the Anvil dev chain:

```powershell
python -m main_computer.cli hub --network dev
```

The local QBFT test profile listens on `127.0.0.1:8780` and talks to the
non-validator RPC node at `http://127.0.0.1:30010` after the smoke lab has
published `runtime/deployments/current.json`. The smoke lab uses London/EIP-1559
from genesis and does not deploy through legacy transactions or older-EVM compiler downgrades:

```powershell
python .\tools\smoke_besu_qbft_one_validator.py up --deploy-contracts
python -m main_computer.cli hub --network test
```

Equivalent split form:

```powershell
python .\tools\smoke_besu_qbft_one_validator.py up
python .\tools\smoke_besu_qbft_one_validator.py deploy
python -m main_computer.cli hub --network test
```

Override the Hub bind port only when running extra instances:

```powershell
python -m main_computer.cli hub --network test --port 8888 --hub-runtime-dir .\runtime\hub\test-alt
```


Verify the running Hub against the selected network with the end-to-end client
smoke. It checks Hub status, chain RPC identity, EIP-1559/Shanghai support,
deployed contract code, the funded smoke client wallet, and a paid
worker/credit/claim flow:

```powershell
python .\scripts\smoke_hub_network_client.py --network dev
python .\scripts\smoke_hub_network_client.py --network test
```

## Pull a model into host or remote Ollama

```powershell
ollama pull qwen2.5:1.5b
```

Or choose another model:

```powershell
$env:MAIN_COMPUTER_MODEL = "gemma4:26b"
ollama pull $env:MAIN_COMPUTER_MODEL
```

The Docker worker defaults to `http://host.docker.internal:11434` for Ollama.
Set `MAIN_COMPUTER_DOCKER_OLLAMA_BASE_URL` before starting the worker when the
model server is somewhere else.


## Start the viewport in Docker on a Windows host

After the Stage 2 host-drive path support is applied, use the Windows launcher
to generate a Compose override that mounts the available Windows filesystem
drives into the Linux app container under `/host/<drive-letter>`:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-main-computer-docker-windows.ps1
```

The launcher auto-discovers Windows drives such as `C:\` and `D:\`, writes a
runtime override at `runtime\docker-windows-host\compose.host-drives.yml`, and
starts the existing `main-computer` Compose service with:

```text
MAIN_COMPUTER_PATH_MODE=mounted-windows
MAIN_COMPUTER_HOST_OS=windows
MAIN_COMPUTER_HOST_DRIVE_ROOT=/host
```

The server still opens Linux container paths like `/host/c/<relative-path>`, while the
file explorer API/UI displays Windows paths like `C:\<relative-path>`.

Useful variants:

```powershell
# Show the generated Compose config without starting the app.
powershell -ExecutionPolicy Bypass -File .\start-main-computer-docker-windows.ps1 -Action config

# Mount only selected drives.
powershell -ExecutionPolicy Bypass -File .\start-main-computer-docker-windows.ps1 -IncludeDrives C,D

# Safer read-only smoke.
powershell -ExecutionPolicy Bypass -File .\start-main-computer-docker-windows.ps1 -ReadOnlyDrives

# Stop the stack started by the launcher.
powershell -ExecutionPolicy Bypass -File .\start-main-computer-docker-windows.ps1 -Action down
```

## Add the hub worker

Start the host hub first, or point the worker at a remote hub with
`MAIN_COMPUTER_HUB_URL`. The default is `http://host.docker.internal:8770`,
which targets a host-running hub from inside Docker Desktop containers.

```powershell
docker compose -f docker-compose.dev.yml --profile worker up --build hub-worker
```

If the hub needs a different callback URL for the worker endpoint, set
`MAIN_COMPUTER_HUB_WORKER_PUBLIC_ENDPOINT` before starting the worker.

## Add the shared local Gitea server

Gitea is no longer part of `docker-compose.dev.yml` or
`docker-compose.applications.yml`. Main Computer uses one standalone,
machine-wide Gitea Docker stack on HTTP `localhost:3000`:

```powershell
docker compose -f docker-compose.gitea.yml up -d gitea
```

The service is `gitea` in `docker-compose.gitea.yml` under the
`main-computer-gitea` Compose project. It uses persistent `gitea-data` storage
and defaults that storage to the former `main-computer-applications_gitea-data`
Docker volume so existing local repositories survive the breakout. It has install lock enabled, disables open registration by default,
stores repository data in SQLite, and disables Gitea SSH so local SSH ports are
not published. HTTP remotes use `http://localhost:3000/<owner>/<repo>.git`.

The Git application page has a hidden Docker control pane for this shared stack. Press
`Ctrl+Shift+G` while the Git page is focused, or add `?gitServerPane=1` to the
URL, to reveal Status/Start/Restart/Stop/Logs controls. Click **Use Local
Server** to fill owner `local`, the detected repo name, HTTP localhost, and the
recommended `local-gitea` remote mode. **Set Up Local Server** starts or verifies
the standalone Gitea stack, creates or verifies the local Gitea user and
repository, and then configures the selected local Git remote. **Push to Local
Server** performs the first push as `git push -u local-gitea HEAD` using a
temporary local Gitea token, without storing that token in `.git/config`.

When a checkout came from GitHub or another external service, the UI preserves
that original `origin` by default and adds or updates a separate `local-gitea`
remote. Older worktrees may still have a stale `local` remote from earlier docs;
remove or ignore it unless it already points at the same Gitea repository. Switch
the mode to **Switch origin to local server** only when you want origin itself to
point at Gitea. The hidden pane also includes external direct remotes for
bypassing local Gitea. It also includes server-to-external push-mirror controls:
provide an external HTTPS repo plus username/token and the Git page will ask
local Gitea to create the push mirror. That token is sent to local Gitea for the
mirror configuration; it is not stored in `.git/config`.

## Optional containerized viewport

```powershell
docker compose -f docker-compose.dev.yml --profile app up --build main-computer
```

The app container intentionally has `MAIN_COMPUTER_EXECUTOR_ENABLED=0` by default.
The current `DockerExecutor` builds host-path `docker run -v ...` commands, so
executor development is most reliable with the viewport running on the host.

## Current executor development path

Build the executor image:

```powershell
docker build -t main-computer-executor:latest -f docker/executor/Dockerfile .
```

Run a cheap image smoke:

```powershell
docker compose -f docker-compose.dev.yml --profile smoke run --rm executor-smoke
```

Then run the viewport on the host:

```powershell
$env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"
$env:MAIN_COMPUTER_EXECUTOR_IMAGE = "main-computer-executor:latest"
$env:MAIN_COMPUTER_EXECUTOR_ROOT = "runtime/executor"
python -m main_computer.cli viewport --port 8765
```

## Hub client smoke

After the host/remote hub and `hub-worker` are running:

```powershell
python -m main_computer.cli chat `
  --provider hub `
  --hub-url http://127.0.0.1:8770 `
  "Say hello from the host hub."
```

## Ethereum dev chain

The app-facing Anvil soft chain runs on chain id `42424242` and is owned by
`tools/dev-chain-reset.py`. It publishes `runtime/deployments/current.json` and
keeps Docker Compose from starting a second chain on the same RPC port.
