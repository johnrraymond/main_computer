# Main Computer

Main Computer is a Windows-first local AI application. This repository is the source/development checkout for the `main_computer` Python package.

## Requirements

Install these before trying to run the system from a checkout:

- Windows 10 or Windows 11.
- Python 3.10 or newer, available from PowerShell and Command Prompt as `python`.
- Git, if you are checking the code out directly.
- Ollama, running locally. The default provider is Ollama and the default model is `gemma4:26b` unless you override `MAIN_COMPUTER_MODEL`.
- Docker Desktop with the WSL 2 backend enabled.
- WSL installed and working.
- PowerShell.

Optional, feature-specific requirements:

- NSIS 3.x or newer is required only when creating the Windows installer. The installer build needs `makensis.exe` on `PATH`, in the standard NSIS install location, or passed explicitly to the build script.
- A MetaMask wallet is required to use the blockchain/dev-chain wallet elements. It is not required for the basic local app startup.
- Playwright Chromium is only needed for browser automation/harness tests.

## First-time source checkout setup

From the repository root:

```bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Make sure Ollama is running, then pull the model you intend to use. The project default is:

```bat
ollama pull gemma4:26b
```

To use a different installed Ollama model, set `MAIN_COMPUTER_MODEL` before starting:

```bat
set MAIN_COMPUTER_MODEL=qwen2.5:1.5b
```

## Developer dev environment setup

Use this flow when a developer needs the local app plus the local dev-chain, faucet runtime, Hub credit escrow contract, Hub server, and a worker.

Run from the repository root in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

Make sure the local prerequisites are reachable:

```powershell
docker version
wsl --status
ollama list
```

Build and test the Solidity contracts before publishing a dev runtime:

```powershell
python .\tools\build_contracts.py --test
```

Deploy the local test chain and publish the app-facing deployment runtime:

```powershell
python .\tools\dev-chain-reset.py --yes --run-id test-machine-dev --environment dev --port-strategy replace-project
python .\tools\dev-chain-diagnosis.py --state .\runtime\deployments\current.json
```

This generates local runtime files such as `runtime/deployments/current.json`, `runtime/dev-chain/latest.json`, `runtime/dev-chain/latest.env`, and `runtime/deployments/hub-admin-wallet.json`. They are machine-local state and should stay out of Git.

To publish the same app-facing deployment runtime from the local QBFT testnet
instead of Anvil, restart the QBFT lab with the current genesis, deploy the
contracts through its non-validator RPC node, then start the Hub with the test
profile:

```powershell
python .\tools\smoke_besu_qbft_one_validator.py down
python .\tools\smoke_besu_qbft_one_validator.py up
python .\tools\smoke_besu_qbft_one_validator.py deploy
python -m main_computer.cli hub --network test
```

The testnet deployment writes `runtime/deployments/current.json` with
`environment=test`, chain id `42424241`, and RPC URL `http://127.0.0.1:30010`,
so the existing golden runtime lookup can consume it without a separate testnet
code path.

Start or restart the viewport after the runtime exists:

```powershell
.\control-main-computer.ps1 restart -AutoAllow -Workspace "$PWD" -Port 8765 -HeartbeatPort 8766
```

Verify the faucet readiness endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/xlag/dev/faucet
```

A healthy dev-chain setup reports `ready=True` with `deployment_runtime=True`, `faucet_account=True`, `dev_chain_reachable=True`, and `chain_id_ok=True`. If the UI reports `Deployment runtime is missing or has no faucet account.`, rerun the dev-chain reset command above from the repository root and restart the viewport.

To exercise the Hub path, use separate PowerShell windows from the repository root.

Start the Hub with the documented network profile. The default `dev`
profile listens on `127.0.0.1:8770`, uses `runtime/hub/dev`, and talks to the
Anvil dev-chain RPC at `http://127.0.0.1:18545`:

```powershell
$env:MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"
python -m main_computer.cli hub --network dev
```

To run the Hub against the local QBFT testnet after the smoke lab and testnet
deployment publication are up, use the `test` profile. It listens on
`127.0.0.1:8780`, uses `runtime/hub/test`, and talks to the non-validator RPC
node at `http://127.0.0.1:30010`:

```powershell
$env:MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"
python -m main_computer.cli hub --network test
```

The old explicit form remains available for one-off overrides:

```powershell
python -m main_computer.cli hub --network test --host 127.0.0.1 --port 8888 --hub-runtime-dir .\runtime\hub\test-alt
```

Start a local worker and register it with the Hub:

```powershell
$env:MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"
python -m main_computer.cli hub-worker `
  --provider ollama `
  --model qwen2.5:1.5b `
  --host 127.0.0.1 `
  --port 8771 `
  --hub-url http://127.0.0.1:8770 `
  --public-endpoint http://127.0.0.1:8771 `
  --hub-worker-node-id test-machine-worker-01 `
  --hub-credits-per-request 1
```

Replace `qwen2.5:1.5b` with the Ollama model installed on the developer machine when needed.

Start the viewport as a Hub client:

```powershell
$env:MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"
python -m main_computer.cli viewport --provider hub --hub-url http://127.0.0.1:8770 --host 127.0.0.1 --port 8765
```

Useful Hub checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8770/api/hub/status
Invoke-RestMethod http://127.0.0.1:8770/api/hub/v1/credits/indexer
```

## Starting from a direct checkout

When running the code directly from this repository, start the system with `start_v2.bat`:

```bat
start_v2.bat
```

To open the browser automatically after startup:

```bat
start_v2.bat -OpenBrowser
```

`start_v2.bat` is location-aware and uses the Python installation available on `PATH` when running from a source/development checkout.

Stop the system with:

```bat
stop_v2.bat
```

By default, `stop_v2.bat` leaves Docker infrastructure running. To stop tracked Docker infrastructure too:

```bat
stop_v2.bat --with-docker
```

## Docker, WSL, and local services

Docker Desktop and WSL are used by the local service stack, executor paths, and application support services. Before starting the system, confirm these commands work from PowerShell or Command Prompt:

```bat
docker version
wsl --status
```

If Docker Desktop is not running, start it before using the local application stack.

## Ollama configuration

The default local Ollama URL is:

```text
http://localhost:11434
```

Useful environment variables:

```bat
set MAIN_COMPUTER_PROVIDER=ollama
set MAIN_COMPUTER_MODEL=gemma4:26b
set OLLAMA_BASE_URL=http://localhost:11434
```

OpenAI support may exist in the codebase, but the normal local setup is Ollama.

## Blockchain wallet use

Blockchain/dev-chain features require a MetaMask wallet in the browser. Create or import a wallet, then connect it to the network required by the dev-chain workflow you are using.

You do not need MetaMask for the basic local app, README work, normal source startup, or non-blockchain tests.

## Building the Windows installer

Install NSIS 3.x or newer before building the Windows installer.

From the repository root:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\build-main-computer-nsis-installer.experimental-v7.ps1
```

If `makensis.exe` is not on `PATH` or in the standard NSIS install directory, pass the compiler path explicitly:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\build-main-computer-nsis-installer.experimental-v7.ps1 -MakeNsisCompiler "C:\Program Files (x86)\NSIS\makensis.exe"
```

NSIS is only needed for installer creation. It is not required to run the system from a source checkout with `start_v2.bat`.

## Tests and diagnostics

Run focused tests for the part of the system you changed:

```bat
python -m pytest tests
```

Browser automation tests may require Playwright Chromium:

```bat
python -m playwright install chromium
```

Some tests expect Docker Desktop, WSL, Ollama, local ports, or other external services to be available.


## Dev Docker stack

The dev Compose stack is for optional containerized app/worker/support targets. It intentionally does not start a fallback chain on port `18545`; the app-facing blockchain golden path is published by `tools/dev-chain-reset.py`.

```powershell
python .\tools\dev-chain-reset.py --yes --run-id test-machine-dev --environment dev --port-strategy replace-project
python -m main_computer.cli hub --host 127.0.0.1 --port 8770
docker compose -f docker-compose.gitea.yml up -d gitea
docker compose -f docker-compose.dev.yml --profile smoke run --rm executor-smoke
```

