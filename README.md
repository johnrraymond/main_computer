# Main Computer Test

This repository is the active **test/development checkout** for the `main_computer`
Python package. It is no longer just a small project-folder router. The current
snapshot contains a local AI control layer, browser viewport, application
workspace, Docker/WSL executor plumbing, Git/Gitea tooling, RAG smoke harnesses,
ONLYOFFICE workbook integration, local website publishing tools, and dev-chain
contract utilities.

The repository root is `main_computer_test`; the importable package is
`main_computer`.

## Current snapshot facts

These details come from the files in this snapshot, not from older README text:

- Package metadata: `pyproject.toml` declares `main-computer-test` version `0.1.0`
  and the console script `main-computer = main_computer.cli:main`.
- Python requirement: `>=3.10`.
- Native CLI default provider/model from `main_computer/config.py`:
  `MAIN_COMPUTER_PROVIDER=ollama`, `MAIN_COMPUTER_MODEL=gemma4:26b`,
  `OLLAMA_BASE_URL=http://localhost:11434`, and `MAIN_COMPUTER_WORKSPACE`
  defaults to `Path.home() / "dsl"`.
- OpenAI remains optional. When `MAIN_COMPUTER_PROVIDER=openai` and no explicit
  model is set, the code uses `OPENAI_MODEL` or `gpt-5.2`.
- The Docker dev stack intentionally has its own defaults. In
  `docker-compose.dev.yml`, the app and worker use
  `${MAIN_COMPUTER_MODEL:-qwen2.5:1.5b}` and the containerized app disables the
  executor with `MAIN_COMPUTER_EXECUTOR_ENABLED: "0"`.
- `dev-control.ps1` is **not** at the repository root in this uploaded tree. The
  file that exists is `tools/dev-control.ps1`. Some helper output and older tests
  still mention `.\dev-control.ps1`; treat those as stale until the wrapper is
  restored to the root or the script is repaired to resolve root-level companion
  files from `tools\`.
- Dev-chain helper scripts in this snapshot live under `tools\`, for example
  `tools\dev-chain-reset.py`, `tools\dev-chain-flow.py`, and
  `tools\dev-chain-ledger-bridge.py`. Root-level `dev-chain-reset.py`,
  `dev-chain-smoke.py`, and `dev-verify.py` are not present in this export.

## Install and dependency shape

For a minimal editable install:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"
python -m pip install -e .
```

For the full development surface, install the pinned requirements:

```powershell
python -m pip install -r requirements.txt
```

The full requirements currently include Playwright, Mathics3, PySide6, pytest,
pypdf, paramiko, TenSEAL, and other development/runtime dependencies. Browser
harnesses and PDF export also need Chromium installed for Playwright:

```powershell
python -m playwright install chromium
```

Ollama, Docker Desktop, WSL, ONLYOFFICE Docs, Gitea, and Foundry/Anvil are
external services used by specific features. They are not required for every
unit test or for reading the project map.

## CLI entry points

Use either the module form or the installed script:

```powershell
python -m main_computer.cli <command>
main-computer <command>
```

Current subcommands from `main_computer/cli.py`:

```text
chat
projects
project
providers
viewport
openclaw-bridge
heartbeat
hub
hub-worker
hub-register-worker
harness
diagnostics
rotate-logs
code-stats
static-code-stats
recurrent-thinking
```

Common examples:

```powershell
python -m main_computer.cli providers
python -m main_computer.cli projects
python -m main_computer.cli project main_computer_test
python -m main_computer.cli chat "What projects are available?"
python -m main_computer.cli viewport --host 127.0.0.1 --port 8765
python -m main_computer.cli harness
python -m main_computer.cli diagnostics --level health
python -m main_computer.cli code-stats .
python -m main_computer.cli recurrent-thinking --repo-dir .
```

Provider overrides:

```powershell
$env:MAIN_COMPUTER_PROVIDER = "ollama"
$env:MAIN_COMPUTER_MODEL = "gemma4:26b"
$env:MAIN_COMPUTER_OLLAMA_TIMEOUT_S = "600"
python -m main_computer.cli chat "Summarize this workspace."
```

```powershell
$env:MAIN_COMPUTER_PROVIDER = "openai"
$env:OPENAI_API_KEY = "..."
python -m main_computer.cli chat "Summarize this workspace."
```

```powershell
python -m main_computer.cli chat `
  --provider hub `
  --hub-url http://127.0.0.1:8770 `
  "Use the hub worker pool for this request."
```

## Local viewport

Start the viewport directly:

```powershell
python -m main_computer.cli viewport --host 127.0.0.1 --port 8765
```

Open `127.0.0.1` instead of `localhost` when comparing local, WSL, and Docker
listeners:

```text
http://127.0.0.1:8765/
http://127.0.0.1:8765/text
http://127.0.0.1:8765/graphical
http://127.0.0.1:8765/applications
http://127.0.0.1:8765/energy
http://127.0.0.1:8765/revision
http://127.0.0.1:8765/debug/text
http://127.0.0.1:8765/debug/graphical
```

The viewport starts a heartbeat helper, writes viewport PID state under the
runtime control root, and serves route/API signal output unless `-noverbose` is
passed.

The applications workspace currently routes these apps:

- Game Surface
- Calculator
- Document Editor
- Spreadsheet
- ONLYOFFICE
- Task Manager
- Terminal
- Chat Console
- Git Tools
- Code Editor
- File Explorer
- Game Editor
- Website Builder
- Worker

Representative API groups include `/api/chat`, `/api/projects`,
`/api/executor/*`, `/api/applications/git/*`, `/api/applications/docs/*`,
`/api/applications/spreadsheet/*`, `/api/applications/onlyoffice/*`,
`/api/applications/websites/*`, `/api/revisions/*`, and `/api/energy/*`.

## Windows runner and service-control reality

The most coherent Windows runner in this snapshot is
`run-main-computer-test.ps1`:

```powershell
.\run-main-computer-test.ps1 -Action check -Mode Unleashed
.\run-main-computer-test.ps1 -Action start -Mode Unleashed
.\run-main-computer-test.ps1 -Action status -Mode Unleashed
.\run-main-computer-test.ps1 -Action shutdown -Mode Unleashed
```

Supported actions in that script are:

```text
start, run, restart, status, stop, shutdown, install, install-run, smoke, check
```

Supported modes are:

```text
Unleashed, Debug, Safe
```

`control-main-computer.ps1` is a legacy automation helper. It exits with a
user-facing redirect unless an automated caller passes `--auto-allow`.

`tools/dev-control.ps1` contains the newer local-vs-Docker control design
(status/start/shutdown/restart/doctor/setup-renderer, local port `8765`, Docker
port `18765`, `-Polite`, and renderer setup), but in this export it is located
under `tools\` while its current path resolution expects companion files next to
it. Use the direct CLI or `run-main-computer-test.ps1` for local starts until that
wrapper placement is corrected.

## Dev Docker stack

The development Compose stack is `docker-compose.dev.yml`. It no longer starts
or defines the hub service; use `deploy/coolify/hub/docker-compose.yml` for the
standalone hub container.

Start shared dev support services:

```powershell
docker compose -f docker-compose.dev.yml up --build ollama ethereum-dev
```

Start the standalone hub container:

```powershell
docker compose -f deploy/coolify/hub/docker-compose.yml up --build
```

Pull the model used by the Compose worker default:

```powershell
docker compose -f docker-compose.dev.yml exec ollama ollama pull qwen2.5:1.5b
```

Start the optional Ollama-backed hub worker after the standalone hub is running,
or set `MAIN_COMPUTER_HUB_URL` to a remote hub:

```powershell
docker compose -f docker-compose.dev.yml --profile worker up --build hub-worker
```

Start the optional containerized viewport:

```powershell
docker compose -f docker-compose.dev.yml --profile app up --build main-computer
```

By default that app publishes the viewport on:

```text
http://127.0.0.1:18765
```

Use `MAIN_COMPUTER_DOCKER_VIEWPORT_PORT=8765` only when Docker is intentionally
taking the normal local viewport port.

The standalone local Gitea stack is separate:

```powershell
docker compose -f docker-compose.gitea.yml up -d gitea
```

It publishes HTTP Gitea on `127.0.0.1:3000`, disables SSH, and reuses the old
`main-computer-applications_gitea-data` volume by default. The Git Tools app has
local-server setup and publish flows for this stack.

Executor image build and smoke targets:

```powershell
docker compose -f docker-compose.dev.yml --profile executor build executor-image
docker compose -f docker-compose.dev.yml --profile smoke run --rm executor-smoke
```

## Executor and RAG-assisted execution

The executor code supports Docker and WSL-style backends. Important environment
variables from `main_computer/config.py` include:

```text
MAIN_COMPUTER_EXECUTOR_ENABLED
MAIN_COMPUTER_EXECUTOR_BACKEND=docker
MAIN_COMPUTER_EXECUTOR_IMAGE=main-computer-executor:latest
MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION=MainComputerExecutorTest
MAIN_COMPUTER_EXECUTOR_ROOT=runtime/executor
MAIN_COMPUTER_EXECUTOR_TIMEOUT_S=120
MAIN_COMPUTER_EXECUTOR_MAX_UPLOAD_BYTES=2147483648
MAIN_COMPUTER_EXECUTOR_MAX_OUTPUT_CHARS=128000
MAIN_COMPUTER_EXECUTOR_TOOL_LOOP_ENABLED=1
MAIN_COMPUTER_RAG_DOCKER_ENABLED=1
MAIN_COMPUTER_EXECUTOR_AI_AUTO_RUN=0
MAIN_COMPUTER_EXECUTOR_AI_ALLOW_NETWORK=0
MAIN_COMPUTER_EXECUTOR_AI_MAX_STEPS=4
```

The native `MainComputerConfig.from_env()` path currently treats the executor as
enabled by default unless the environment disables it. The Docker app profile
overrides that to disabled because the in-process Docker executor uses host-path
mounts.

Manual executor routes include:

```text
GET  /api/executor/status
GET  /api/executor/uploads
POST /api/executor/uploads?filename=<name>
POST /api/executor/run
POST /api/executor/ai
GET  /api/executor/artifacts/<job_id>/<relative_path>
```

RAG and smoke modules are numerous in this snapshot. The reusable harness entry
points are:

```powershell
python tools/test_rag_bootstrap.py --prompt "Inspect the executor integration" --no-model
python -m main_computer.rag_harness --prompt "Plan a safe backend change" --repo-dir . --no-model
python tools/test_rag_model_csv_audit.py --repo-dir . --run-id csv_audit_model_smoke
```

## Hub and worker broker

Run a local hub:

```powershell
python -m main_computer.cli hub --host 127.0.0.1 --port 8770
```

Run a worker and register it with the hub:

```powershell
python -m main_computer.cli hub-worker `
  --provider ollama `
  --model gemma4:26b `
  --host 127.0.0.1 `
  --port 8771 `
  --hub-url http://127.0.0.1:8770 `
  --hub-worker-node-id gpu-worker-01 `
  --hub-credits-per-request 3
```

Register an already-running worker endpoint:

```powershell
python -m main_computer.cli hub-register-worker `
  --hub-url http://127.0.0.1:8770 `
  --node-id gpu-worker-01 `
  --endpoint http://127.0.0.1:8771
```

The hub provider uses the energy-credit ledger shape for local payout records.
High-security hub transport is enabled in config by default. Use the standalone
hub deployment surface under `deploy/coolify/hub/` for containerized hub runs;
the dev Docker stack only provides optional workers and support services.

## ONLYOFFICE workbook editor

The `ONLYOFFICE` app is separate from the JSON/RevoGrid `Spreadsheet` app. It
stores native `.xlsx` files under `runtime/onlyoffice/workbooks` by default and
uses ONLYOFFICE Docs for editing.

Default local URLs and secret from config:

```text
MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=http://127.0.0.1:18084
MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://127.0.0.1:18084
MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET=main-computer-onlyoffice-local-secret
```

Windows local control script:

```powershell
.\tools\onlyoffice\onlyoffice-control.ps1 install -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 start -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 status -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 doctor -Mode wsl -Port 18084
```

Docker fallback:

```powershell
docker compose -f docker-compose.onlyoffice.yml up -d onlyoffice
```

`18084` is reserved because local platform site publishing uses `18080` through
`18083`.

## Local website publishing and applications services

Local site publishing assets live under `deploy/local-platform`. The generated
site Compose file is under:

```text
deploy/local-platform/generated/docker-compose.websites.yml
```

Helper scripts:

```powershell
.\tools\local-platform\up-local-platform.ps1
.\tools\local-platform\publish-website.ps1
.\tools\local-platform\down-local-platform.ps1
```

The Website Builder app uses `/api/applications/websites/*` routes and the
`main_computer.local_platform_*` modules to create, save, archive, target, and
publish sites.

`docker-compose.applications.yml` is a broader applications stack for
ONLYOFFICE plus Coolify dependencies. It expects several required Coolify
environment variables and is not the same as the lightweight dev stack.

## Git tooling

There are three Git-related surfaces:

1. `git-control.py`, a root-level command planner/runner that writes shim and
   run state under `.git/git-control`.
2. The browser Git Tools app under `/applications/git-tools`.
3. The standalone local Gitea stack in `docker-compose.gitea.yml`.

Useful commands:

```powershell
python git-control.py --plan --prompt "inspect the current branch and suggest the next git action"
python git-control.py --git status --short
python git-control.py --git diff --stat
python git-control.py --sum
```

The Git Tools app can inspect repositories, stage common actions, manage patch
inbox items, run `new_patch.py` dry runs, configure local Gitea remotes, and set
up external push mirrors.

## Patch artifacts for ChatGPT/new_patch.py

`new_patch.py` accepts either a raw snapshot zip or a verified patch-set bundle.

Recommended dry-run from the repository root:

```powershell
python new_patch.py new_zipfile_for_patching.zip --dry-run
```

Raw snapshot mode compares only files included in the zip to the live repository.
It does **not** infer deletions from omitted files.

Verified bundle mode contains:

```text
manifest.json
reference.patch
files/<repo-relative replacement files>
```

`new_diff.py` shows what a zip would change without applying it:

```powershell
python new_diff.py patch_or_snapshot.zip
```

Exports for ChatGPT are produced by:

```powershell
.\export-main-computer-test.ps1
```

The export script writes a timestamped archive under `$env:USERPROFILE\dsl\archive`
and includes `aider.log` when present. The current export list still contains
some root-level script names that are missing from this snapshot; those entries
are skipped when the files do not exist.

## Contracts and dev chain

Smart contracts live under `contracts/`:

```text
contracts/AlphaBetaLockout.sol
contracts/src/XLagBridgeReserve.sol
contracts/test/XLagBridgeReserve.t.sol
contracts/foundry.toml
```

The current dev-chain reset helper is:

```powershell
python .\tools\dev-chain-reset.py --yes --run-id local-smoke --port-strategy auto
```

It publishes the production-shaped runtime file:

```text
runtime/deployments/current.json
```

It also keeps legacy dev-chain state files for operator scripts:

```text
runtime/dev-chain/latest.json
runtime/dev-chain/latest.env
```

Available follow-up helpers in this snapshot:

```powershell
python .\tools\dev-chain-flow.py
python .\tools\dev-chain-ledger-bridge.py --register-node --node-id gpu-worker-01 --queue-credits 0.125
python .\tools\dev-chain-wallet-smoke-guide.py
```

The reserve flow treats Compute Credits as native dev-chain base units for local settlement smoke tests:

```text
1 Compute Credit = 1,000,000,000,000,000,000 base units
```

No public token contract is deployed by the dev-chain tools. Legacy `--queue-eng`, `--fund-eng`, and `--payout-eng` flags remain as deprecated operator aliases only.

C0/C1 hub contract direction:

- `ENG` language is deprecated because it collides with an existing token symbol namespace.
- Users purchase **Compute Credits** rather than a public token.
- `contracts/src/HubCreditSale.sol` is a purchase-intent receipt contract. It accepts native payment, forwards payment to treasury, and emits `CreditPurchased(...)` for the hub backend to index.
- Compute Credits are still internal service credits in C1; worker payouts and transferable token behavior are later phases.

## Diagnostics and tests

Layered diagnostics:

```powershell
python -m main_computer.cli diagnostics --level health
python -m main_computer.cli diagnostics --level server
python -m main_computer.cli diagnostics --level widgets
python -m main_computer.cli diagnostics --level live
python -m main_computer.cli diagnostics --level functional
```

Special diagnostics:

```powershell
python -m main_computer.cli diagnostics --level ollama-probe --output-dir diagnostics_output_ollama_probe
python -m main_computer.cli diagnostics --level ollama-primer --output-dir diagnostics_output_ollama_primer
python -m main_computer.cli diagnostics --level ollama-visibility --output-dir diagnostics_output_ollama_visibility
```

Browser harness:

```powershell
python -m main_computer.cli harness --output-dir harness_output
python -m main_computer.cli harness --url http://127.0.0.1:8765
```

Unit/integration tests:

```powershell
python -m unittest discover -s tests
python -m pytest tests
```

The test tree is broad. Some tests are pure unit tests; others expect Docker,
Playwright, WSL, Ollama, ONLYOFFICE, Gitea, or local ports to be available. Use
focused tests for the subsystem you are changing.

## Runtime/generated data

Common runtime and generated folders include:

```text
runtime/
diagnostics_output*/
harness_output*/
debug_assets/
debug_asset_revisions/
revision_control/
energy_credits/
tools/patching/reports/new_patch_runs/
```

Do not treat generated runtime output as the primary source for code changes.

## Repository map

Important top-level paths:

```text
main_computer/                 Python package
main_computer/web/             viewport HTML/CSS/JS assets
tests/                         unit, browser, smoke, and integration tests
tools/                         operator scripts and smoke helpers
contracts/                     Foundry contract project
docker/                        dev and executor Dockerfiles
deploy/local-platform/         local static site publishing stack
deploy/coolify/local-docker/   local Coolify deployment assets
generated_component_docs/      component documentation generated for the app
pretty_docs/                   drafted polished docs
game_projects/                 sample game/editor projects
proto-dev/                     prototype development assets
runtime/                       local runtime state
```

## Development rules of thumb

Make code changes in this test checkout first. Run focused tests for the area you
changed, then run broader diagnostics or browser harnesses when viewport,
application, executor, RAG, or Docker behavior changes. Promote to any production
checkout only after the test checkout is verified.

For README work, prefer this file as a snapshot-grounded operator map. When a
script path, default model, port, or service mode changes, update the exact
command and call out any transitional mismatch rather than preserving old
examples that no longer exist in the tree.
