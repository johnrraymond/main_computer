# Test Install

This folder is the test install of `main_computer`.

Default runtime:

- provider: `ollama`
- model: `qwen2.5:1.5b`
- workspace: `$env:USERPROFILE\dsl` on Windows, or the value of `MAIN_COMPUTER_WORKSPACE`
- optional debug passcode: `MAIN_COMPUTER_OLLAMA_DEBUG_PASSCODE`

This install includes the test suite so changes can be verified before copying or promoting them into `main_copmputer_production`.

Run tests:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"
python -m unittest discover -s tests -v
```

## Temporal / FoundationDB lab after a reboot

The Hub-backed Temporal/FDB smokes need Docker Desktop, the local Temporal dev
server, and the local FoundationDB smoke container. From the repository root in
PowerShell with the virtualenv active:

```powershell
.\.venv\Scripts\Activate.ps1

python -m tools.temporal_lab.local_temporal up --pull
python -m tools.temporal_lab.local_temporal status

python .\scripts\smoke_foundationdb_credit_ledger_primitives.py --keep-container
```

Then run the current golden-path smokes:

```powershell
python .\scripts\smoke_temporal_fdb_hub_node_market.py
python .\scripts\smoke_temporal_fdb_hub_multi_hub.py
```

The Hub processes are auto-started by those smokes by default. If the
multi-Hub smoke reports that ports `8870` or `8871` are already listening, stop
the stale Hub process or pass alternate `--hub-a-url` / `--hub-b-url` ports.

Start the console viewport directly:

```powershell
python -m main_computer.cli viewport --host 127.0.0.1 --port 8765
```

Quiet server signals:

```powershell
python -m main_computer.cli viewport --host 127.0.0.1 --port 8765 -noverbose
```

For local restart/start automation, run the root helper from the repository root and pass the explicit automation marker:

```powershell
.\control-main-computer.ps1 status -AutoAllow -Workspace "$PWD" -Port 8765 -HeartbeatPort 8766
.\control-main-computer.ps1 restart -AutoAllow -Workspace "$PWD" -Port 8765 -HeartbeatPort 8766
```

The root helper is useful after publishing a fresh dev-chain deployment runtime because an already-running viewport must be restarted before it sees the new `runtime/deployments/dev/latest.json`.


## ONLYOFFICE local workbook editor

The `ONLYOFFICE` application is separate from the existing `Spreadsheet` app. It stores native `.xlsx`
workbooks and opens them through ONLYOFFICE Docs.

Windows local installs default to ONLYOFFICE Docs running in Docker and published on the Windows loopback address. The control wrapper starts the Docker Compose service and waits for the Document Server health check:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"
.\tools\onlyoffice\onlyoffice-control.ps1 install -Mode docker -Port 18085
.\tools\onlyoffice\onlyoffice-control.ps1 start -Mode docker -Port 18085
.\tools\onlyoffice\onlyoffice-control.ps1 doctor -Mode docker -Port 18085
```

Main Computer defaults to:

```text
MAIN_COMPUTER_ONLYOFFICE_MODE=docker
MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=http://127.0.0.1:18085
MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://127.0.0.1:18085
MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL=http://127.0.0.1:18085
MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL=http://host.docker.internal:8765
MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED=false
MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS=true
MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS=true
```

For the default local Docker topology, the browser opens Main Computer at
`http://127.0.0.1:8765`, loads ONLYOFFICE from `http://127.0.0.1:18085`,
and the Document Server receives workbook file and callback URLs through
`http://host.docker.internal:8765`.

For a Dockerized Main Computer service running on the same Compose network as ONLYOFFICE,
set the callback base to the service URL that the ONLYOFFICE container can reach, for example
`http://main-computer:8765`.


## Developer dev-chain, faucet, contracts, and Hub setup

Use this flow when a developer needs the full local test network: local Anvil,
contract deployment, faucet runtime, Hub credit escrow contract, Hub server,
worker, and viewport.

Run from the repository root in PowerShell with the project virtual environment
activated.

Build and test the contracts:

```powershell
python .\tools\build_contracts.py --test
```

Deploy a local dev chain and publish the current app-facing deployment runtime:

```powershell
python .\tools\dev-chain-reset.py --yes --run-id test-machine-dev --environment dev --port-strategy auto
```

Then verify the generated deployment state:

```powershell
python .\tools\dev-chain-diagnosis.py --state .\runtime\deployments\dev\latest.json
```

Dev contract deployments publish the same app-facing runtime shape that
production should use:

```text
runtime/deployments/dev/latest.json
```

The reset/deploy flow also writes local operator state:

```text
runtime/dev-chain/latest.json
runtime/dev-chain/latest.env
runtime/deployments/hub-admin-wallet.json
```

These files are generated local runtime state. They can include host-specific
metadata or local development account material, so they stay ignored by Git and
should not be copied into source control.

After the dev-chain reset succeeds, restart the app so it reloads
`runtime/deployments/dev/latest.json`:

```powershell
.\control-main-computer.ps1 restart -AutoAllow -Workspace "$PWD" -Port 8765 -HeartbeatPort 8766
```

Check the faucet readiness endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/xlag/dev/faucet
```

A healthy response reports `ready=True` and these checks as true:

```text
deployment_runtime
faucet_account
loopback_rpc
dev_chain_reachable
dev_chain_id
```

If the UI reports `Deployment runtime is missing or has no faucet account.`, do
not commit files from `runtime/`. Run the dev-chain reset command above from the
repository root, then restart the viewport.

Optional contract/faucet smoke:

```powershell
python .\tools\smoke_fund_contract_preflight.py
```

The smoke command reads the locally published dev deployment runtime and
exercises the faucet-to-escrow path.

### Hub server and worker

The dev-chain reset deploys `hub_credit_bridge_escrow` by default and publishes
it in `runtime/deployments/dev/latest.json`. Start the Hub after that file exists.

Start the Hub in its own PowerShell window with a named network profile:

```powershell
$env:MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"
python -m main_computer.cli hub --network dev
```

For the local QBFT test path, deploy the Coolify-managed `test` seed and use
the test Hub profile. The local Coolify helper owns the dashboard/API token file
under `runtime/coolify-local-docker`, while the QBFT deployer publishes
`runtime/deployments/test/latest.json`.

```powershell
python .\tools\coolify_qbft_network.py apply test --all
$env:MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"
python -m main_computer.cli hub --network test
```

The lower-level smoke harness is still useful for proving raw Besu/QBFT mechanics
without Coolify:

```powershell
python .\tools\smoke_besu_qbft_one_validator.py restart --deploy-contracts --deployment-environment test --docker-subnet 10.241.0.0/24
```

The dev Hub defaults to `http://127.0.0.1:8770`; the test Hub defaults to
`http://127.0.0.1:8780`. Override bind/runtime details only when needed, for
example `--network test --port 8888 --hub-runtime-dir .\runtime\hub\test-alt`.

Start a local worker in another PowerShell window:

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

Start a viewport as a Hub client in another PowerShell window:

```powershell
$env:MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"
python -m main_computer.cli viewport --provider hub --hub-url http://127.0.0.1:8770 --host 127.0.0.1 --port 8765
```

Useful Hub checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8770/api/hub/status
Invoke-RestMethod http://127.0.0.1:8770/api/hub/v1/credits/indexer
```


End-to-end Hub client smoke checks are network-aware. They verify the selected
Hub profile, chain RPC, deployment manifest, deployed contract code, the funded
smoke client wallet, and a real paid worker/credit/claim flow:

```powershell
python .\scripts\smoke_hub_network_client.py --network dev
python .\scripts\smoke_hub_network_client.py --network test
```

Run the matching Hub first: `python -m main_computer.cli hub --network dev` or
`python -m main_computer.cli hub --network test`.

Export helper for ChatGPT:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"
.\export-main-computer-test.ps1
```

It writes a timestamped zip to `$env:USERPROFILE\dsl\archive` and keeps `aider.log` in the bundle.

Default text console:

```text
http://127.0.0.1:8765
```

Graphical widget test:

```text
http://127.0.0.1:8765/graphical
```

Text and graphical modes share the same browser session, so switching modes preserves the transcript and prompt draft.

The graphical mode includes a Buddhabrot widget rendered in the browser. Axis convention: x is imaginary, y is real. It runs continuously with controls for orbits per slice and delay between displayed slices.

Prompt sends show a spinner and progress strip while the computer is working.

The viewport polls `/api/workspace-timestamp` every few seconds and warns when the loaded console is older than the local directory timestamp.

Ollama Debug Mode is separate from the normal command viewports:

```text
http://127.0.0.1:8765/debug/text
http://127.0.0.1:8765/debug/graphical
```

Enable it from either debug interface to talk directly to local Ollama with `qwen2.5:1.5b` and to read, write, or ask the local model to revise files inside the running project. Leave `MAIN_COMPUTER_OLLAMA_DEBUG_PASSCODE` unset for local open debug mode, or set it to require the passcode before activation.

Debug assets are stored in `debug_assets` under the running project. Use them for scan logs, model notes, generated snippets, and other debug artifacts that need to be listed and reloaded later.

Run the widget harness:

```powershell
python -m main_computer.cli harness
```

Run layered diagnostics:

```powershell
python -m main_computer.cli diagnostics --level widgets
```

The viewport has five diagnostic buttons:

```text
Level 1 functional
Level 2 live
Level 3 widgets
Level 4 server
Level 5 health
```

## ONLYOFFICE local workbook editor

The standalone `ONLYOFFICE` app uses native `.xlsx` files and keeps the existing
`Spreadsheet` app unchanged.

Windows local installs default to **Docker ONLYOFFICE Docs**. The default local Document Server URL is:

```text
http://127.0.0.1:18085
```

Install and control the Docker server:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"

.\tools\onlyoffice\onlyoffice-control.ps1 install
.\tools\onlyoffice\onlyoffice-control.ps1 start
.\tools\onlyoffice\onlyoffice-control.ps1 status
```

Run the viewport with the matching local defaults; no ONLYOFFICE environment variables are
required for the standard Windows Docker setup:

```powershell
.\control-main-computer.ps1 restart -AutoAllow -Workspace "$PWD" -Port 8765 -HeartbeatPort 8766
```

Doctor check:

```powershell
.\tools\onlyoffice\onlyoffice-control.ps1 doctor
```

For production, set stable public/internal URLs and enable a long-lived JWT secret if the
Document Server is configured to require one:

```text
MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=https://office.example.com
MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://onlyoffice:80
MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL=https://main.example.com
MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED=true
MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET=<stable-secret>
```

ONLYOFFICE Docs uses port `18085` by default because local platform site publishing owns ports `18080`-`18083`.

## Temporal/FDB Hub lab after reboot

From the repository root in PowerShell after activating the virtualenv:

```powershell
.\.venv\Scripts\Activate.ps1

python -m tools.temporal_lab.local_temporal up --pull
python -m tools.temporal_lab.local_temporal status

python .\scripts\smoke_foundationdb_credit_ledger_primitives.py --keep-container
```

Then run the acceptance smokes:

```powershell
python .\scripts\smoke_temporal_fdb_hub_node_market.py
python .\scripts\smoke_temporal_fdb_hub_multi_hub.py
python .\scripts\smoke_temporal_fdb_hub_stress.py
```

The stress smoke is the mock-chain bridge soak/freeze test. It should run before swapping the mock bridge for a dev-chain backend.

