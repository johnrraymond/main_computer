# Main Computer

`main_computer` is the local control layer for this workspace. It scans the project folders in your configured workspace, such as `$env:USERPROFILE\dsl` on Windows, adds that project map as context, and passes prompts through to an LLM provider.

Default provider:

- `ollama`
- model: `qwen2.5:1.5b`
- base URL: `http://localhost:11434`

OpenAI support is included as an optional provider. Set `OPENAI_API_KEY` before using it.

## Quick Start

```powershell
cd "$env:USERPROFILE\dsl\main_computer"
python -m main_computer.cli projects
python -m main_computer.cli chat "What projects are available?"
```

Use a different Ollama model:

```powershell
python -m main_computer.cli chat --model llama3.1 "Summarize this workspace."
```

Use a longer Ollama timeout for larger local models:

```powershell
$env:MAIN_COMPUTER_OLLAMA_TIMEOUT_S = "600"
python -m main_computer.cli viewport --port 8765
```

Or pass it per command:

```powershell
python -m main_computer.cli chat --ollama-timeout-s 600 "Summarize this workspace."
```

Use OpenAI:

```powershell
$env:OPENAI_API_KEY = "your_api_key_here"
pip install openai
python -m main_computer.cli chat --provider openai --model gpt-5.2 "Summarize this workspace."
```

## Environment

```text
MAIN_COMPUTER_PROVIDER=ollama
MAIN_COMPUTER_MODEL=qwen2.5:1.5b
MAIN_COMPUTER_WORKSPACE=%USERPROFILE%\dsl
OLLAMA_BASE_URL=http://localhost:11434
MAIN_COMPUTER_OLLAMA_TIMEOUT_S=600
MAIN_COMPUTER_OLLAMA_DEBUG_PASSCODE=
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
OPENAI_MODEL=gpt-5.2
MAIN_COMPUTER_HUB_URL=http://127.0.0.1:8770
MAIN_COMPUTER_HUB_TIMEOUT_S=600
MAIN_COMPUTER_HUB_CLIENT_NODE_ID=main-computer-client
MAIN_COMPUTER_HUB_WORKER_NODE_ID=main-computer-worker
MAIN_COMPUTER_HUB_WORKER_ENDPOINT=
MAIN_COMPUTER_HUB_CREDITS_PER_REQUEST=1
MAIN_COMPUTER_HUB_ROOT=runtime/hub
MAIN_COMPUTER_EXECUTOR_ENABLED=0
MAIN_COMPUTER_EXECUTOR_IMAGE=main-computer-executor:latest
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

## Commands

```powershell
python -m main_computer.cli providers
python -m main_computer.cli projects
python -m main_computer.cli project covenant_grid_demo
python -m main_computer.cli chat "Find the fractal projects."
python -m main_computer.cli viewport
python -m main_computer.cli hub --port 8770
python -m main_computer.cli hub-worker --port 8771 --hub-url http://127.0.0.1:8770
python -m main_computer.cli chat --provider hub --hub-url http://127.0.0.1:8770 "Summarize this workspace."
python -m main_computer.cli openclaw-bridge --host 127.0.0.1 --port 8767
```


## Hub broker and GPU workers

The `hub` provider lets a local Main Computer instance send model calls to a hub instead of directly calling a local model. The hub keeps a small JSON registry of workers, forwards each request to an available worker endpoint, and records a stubbed energy-credit payout for the GPU worker after a successful response.

Start the hub:

```powershell
python -m main_computer.cli hub --host 127.0.0.1 --port 8770
```

Start a worker on another machine or terminal and register it with the hub:

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

Switch a client from local Ollama to the hub:

```powershell
python -m main_computer.cli chat `
  --provider hub `
  --hub-url http://127.0.0.1:8770 `
  "Use the hub worker pool for this request."
```

Hub API endpoints:

```text
GET  /api/hub/status
POST /api/hub/workers/register
POST /api/hub/chat
POST /api/hub/worker/chat   # worker-side endpoint
```

The current payment path is intentionally a local settlement stub: completed hub calls create `hub_worker_payout` transactions in the existing energy credit ledger. That preserves the accounting shape for later chain or contract settlement without enabling automatic external transfers yet.


## Dev Server Control

Use `dev-control.ps1` when choosing between the native Windows viewport and the optional Docker viewport. It makes the run mode explicit, prints the exact URL to open, and avoids the confusing `localhost` split where IPv6, WSL, Docker, and Windows can each own a different listener.

```powershell
.\dev-control.ps1 status
.\dev-control.ps1 start -Mode local
.\dev-control.ps1 restart -Mode local
.\dev-control.ps1 start -Mode docker
.\dev-control.ps1 restart -Mode docker
.\dev-control.ps1 shutdown -Mode local
.\dev-control.ps1 shutdown -Mode docker
```

When `-Mode` is omitted for `start`, `restart`, `shutdown`, `doctor`, or `setup-renderer`, the script prompts for:

```text
local  - native Windows venv viewport
docker - Docker app container viewport
cancel
```

Open the printed `127.0.0.1` URL, not `localhost`:

```text
Local Windows viewport: http://127.0.0.1:8765
Docker viewport:        http://127.0.0.1:18765
```

`-Polite` refuses to replace the other mode. For example, `.\dev-control.ps1 start -Mode local -Polite` will not stop a running Docker viewport; it reports the detected Docker container, the current URL, and the cleanup command instead. Without `-Polite`, the script asks before stopping the other mode and does not silently kill unrelated processes.

PDF export and PDF smoke screenshots require backend Playwright plus Chromium in the same environment that is serving the viewport.

Native Windows renderer setup uses the intended venv Python, preferring `$env:USERPROFILE\dsl\.venv\Scripts\python.exe` or the parent `.venv` beside this repo, and refuses WindowsApps Python:

```powershell
.\dev-control.ps1 setup-renderer -Mode local
.\dev-control.ps1 doctor -Mode local
.\dev-control.ps1 start -Mode local -EnsureRenderer
```

Docker renderer support is built into `docker/dev/app.Dockerfile`; rebuild the image after Dockerfile changes so the container has Playwright and Chromium:

```powershell
docker compose -f docker-compose.dev.yml --profile app build main-computer
.\dev-control.ps1 setup-renderer -Mode docker
.\dev-control.ps1 doctor -Mode docker
.\dev-control.ps1 start -Mode docker -EnsureRenderer
```

The Docker viewport publishes to host port `18765` by default through `MAIN_COMPUTER_DOCKER_VIEWPORT_PORT`. Use host port `8765` for Docker only when intentionally taking over the native local viewport port.



## ONLYOFFICE workbook editor

The standalone `ONLYOFFICE` app is `.xlsx`-native and leaves the existing JSON/RevoGrid `Spreadsheet`
app untouched. The editor needs ONLYOFFICE Docs to be running separately.

Windows local default is WSL-native ONLYOFFICE Docs:

Local platform site publishing owns ports `18080`-`18083`, so ONLYOFFICE Docs uses the reserved local service port `18084` by default.

```powershell
.\tools\onlyoffice\onlyoffice-control.ps1 install -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 start -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 status -Mode wsl -Port 18084
```

Docker remains available for production-like local runs and server deployments:

```powershell
.\tools\onlyoffice\onlyoffice-control.ps1 start -Mode docker -Port 18084
```

Useful environment variables:

```text
MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=http://127.0.0.1:18084
MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://127.0.0.1:18084
MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL=<auto-detected for local WSL unless explicitly set>
MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET=<stable secret>
```

For local Windows/WSL development, Main Computer defaults to the reserved local URLs and
`main-computer-onlyoffice-local-secret`, matching the WSL-native installer. When the callback base
is not explicitly set, the backend uses WSL's Windows-host gateway address for ONLYOFFICE workbook
download and save callbacks, because the Document Server runs inside WSL and cannot use Windows
`127.0.0.1:8765` to reach the Main Computer process. The local dev controller binds the Windows
viewport on `0.0.0.0` by default so that WSL-native ONLYOFFICE can reach those workbook endpoints;
open the app through `http://127.0.0.1:8765`.

Production deployments must override the URLs and JWT secret with deployment-specific values.

In Compose deployments, `MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL` can point at `http://onlyoffice:80`
while `MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL` points at the browser-facing reverse-proxy URL.


## Dev Docker Stack

The development Docker stack is now explicit and separate from the future production deployment. It brings up the hub, an optional Ollama-backed worker, Ollama, an Anvil Ethereum dev chain, and a build target for the existing Docker executor image. Gitea is a separate machine-wide Docker stack on HTTP localhost:3000.

Start the shared services:

```powershell
docker compose -f docker-compose.dev.yml up --build hub ollama ethereum-dev
```

Pull the default worker model into the Ollama container:

```powershell
docker compose -f docker-compose.dev.yml exec ollama ollama pull qwen2.5:1.5b
```

Start the optional hub worker:

```powershell
docker compose -f docker-compose.dev.yml --profile worker up --build hub-worker
```

Start the optional containerized viewport. It publishes to host port `18765` by default so it cannot steal the local Windows viewport on `8765`:

```powershell
docker compose -f docker-compose.dev.yml --profile app up --build main-computer
```

Use `MAIN_COMPUTER_DOCKER_VIEWPORT_PORT=8765` only when you intentionally want Docker to own the normal viewport port.

Start or verify the shared local Gitea Git server:

```powershell
docker compose -f docker-compose.gitea.yml up -d gitea
```

The underlying Compose service is `gitea` in `docker-compose.gitea.yml` under the `main-computer-gitea` project. It is intentionally not part of `docker-compose.applications.yml`, so starting ONLYOFFICE/Coolify cannot collide with an already-running Gitea on localhost:3000. By default the standalone stack reuses the old `main-computer-applications_gitea-data` volume so existing local Gitea repositories survive the breakout. The frontend Git page can own the local Gitea setup for you. Press `Ctrl+Shift+G` on the Git page, or open it with `?gitServerPane=1`, then click **Use Local Server**. The hidden pane starts or verifies the standalone Gitea stack, creates or verifies the local `local/<repo>` Gitea repository, and configures the recommended HTTP-only `local-gitea` remote, for example `http://localhost:3000/local/<repo>.git`.

By default the UI preserves an existing `origin` remote from GitHub/GitLab and adds or updates a separate `local-gitea` remote. Choose **Switch origin to local server** when you intentionally want `origin` to point at local Gitea instead. The publish action pushes `HEAD`, so the remote branch follows the current checkout: a `master` checkout publishes `refs/heads/master`, while a `main` checkout publishes `refs/heads/main`. Older worktrees may still have a stale `local` remote from earlier docs; treat it as unrelated unless it already points at the same Gitea repository.

To verify a local Gitea publish, compare `git rev-parse HEAD` with `git ls-remote http://localhost:3000/local/<repo>.git HEAD refs/heads/<current-branch>`, where `<current-branch>` is `git branch --show-current`. The same pane also has direct external-remote presets for GitHub/GitLab/Gitea Cloud and server-to-external push-mirror controls. The mirror setup asks for an external HTTPS URL plus your external username/token, sends those credentials only to local Gitea for its mirror configuration, and does not write them into `.git/config`.

The app container keeps `MAIN_COMPUTER_EXECUTOR_ENABLED=0` by default because the current in-process `DockerExecutor` creates host-path Docker mounts. For executor work, build the dev executor image and run the viewport on the host:

```powershell
docker build -t main-computer-executor:latest -f docker/executor/Dockerfile .

$env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"
$env:MAIN_COMPUTER_EXECUTOR_IMAGE = "main-computer-executor:latest"
$env:MAIN_COMPUTER_EXECUTOR_ROOT = "runtime/executor"
python -m main_computer.cli viewport --port 8765
```


Create a soft-chrooted local dev chain run and deploy the dev contracts into a fresh isolated Anvil state machine:

```powershell
python .\dev-chain-reset.py --dry-run --run-id first-soft-test
python .\dev-chain-reset.py --yes --run-id first-soft-test
python .\dev-verify.py
```

The helper does not remove the Compose `ethereum-dev` volume. Instead, each run creates `runtime/dev-chain/runs/<run-id>/`, creates an isolated Docker network and Anvil container, sets the Anvil account pool size to four by default, records the initial four local-only office keys, and deploys the root contract suite from `contracts/` through a one-shot Foundry Docker container on that isolated network.

Deployment outputs are written to:

```text
runtime/dev-chain/runs/<run-id>/deploy.json
runtime/dev-chain/runs/<run-id>/deploy.env
runtime/dev-chain/latest.json
runtime/dev-chain/latest.env
```

After a live soft deploy, smoke-check the deployed contracts before pointing the UI or MetaMask at the run:

```powershell
python .\dev-chain-smoke.py
```

The smoke check reads `runtime/dev-chain/latest.json`, checks the live RPC chain id, verifies that bytecode exists at each deployed root contract address, verifies the four office addresses on both `AlphaBetaLockout` and `XLagBridgeReserve`, and checks the X-LAG reserve configuration values (`maxPayoutWei`, payout delay, reset delay, and initial proposal id). It writes `runtime/dev-chain/smoke-latest.json`.

Run the native ENG payout flow to prove the reserve can operate with the chain-native coin:

```powershell
python .\dev-chain-flow.py
```

The flow reads the latest soft deploy state, funds `XLagBridgeReserve` with native ENG, proposes a payout from O0, seconds it from O2, mines the delay, executes the payout, verifies the recipient native ENG balance increased, verifies the proposal state is `EXECUTED`, and writes `runtime/dev-chain/flow-latest.json`.

ENG is native to the energy chain in the same way ETH is native to Ethereum:

```text
1 ENG = 1,000,000,000,000,000,000 base units
```

The dev-chain tools therefore keep settlement amounts as integer base units and display them as ENG for operators. No ENG token contract is deployed.

Bridge a local ledger payout entitlement through the native ENG reserve after the soft deploy and smoke checks pass:

```powershell
python .\dev-chain-ledger-bridge.py --register-node --node-id gpu-worker-01 --queue-eng 0.125
```

The bridge queues a local worker payout in ENG base units, claims it into the local `EnergyCreditLedger`, runs the native reserve payout lifecycle, spends/reconciles the local claim back to zero, and appends a `native_eng_reserve_payout_executed` audit transaction with the proposal id, recipient, reserve contract, chain id, and transaction hashes. It writes `runtime/dev-chain/ledger-bridge-latest.json`.

The default host RPC URL is `http://127.0.0.1:18545` so the soft deploy does not collide with the shared Compose `ethereum-dev` service on `8545`. The generated office keys are deterministic Anvil development keys only; never use them on a value-bearing chain.

A cheap executor-image smoke is available:

```powershell
docker compose -f docker-compose.dev.yml --profile smoke run --rm executor-smoke
```

See `docker/dev/README.md` for the full dev stack notes and service map.



## Recurrent Thinking Scan

Mine visible AI/runtime artifacts for project ideas that keep being rediscovered, then write a compact preload file and structured JSON memory.

```powershell
python -m main_computer.cli recurrent-thinking
```

Default scan roots are tuned for this repository: `aider.log`, `aider_responses`, `aider_web_context`, `debug_assets`, diagnostics/harness outputs, generated component docs, pretty docs, and the project context files. Outputs default to:

```text
debug_assets/recurrent_thoughts.md
debug_assets/recurrent_thoughts.json
```

Optional fine-tuning seed output is available, but the Markdown/JSON preload files are easier to audit and refresh:

```powershell
python -m main_computer.cli recurrent-thinking --fine-tune debug_assets/recurrent_thinking_seed.jsonl
```

The scan only uses visible artifacts and does not preserve hidden chain-of-thought.


## Docker Linux Executor

Pass 1/2 of the Linux execution backend adds a manually callable Docker executor plus raw upload/artifact plumbing. It is disabled by default so the AI cannot run shell commands until the local operator opts in. Chat Console RAG-AT uses the same global executor switch instead of a separate Docker-specific notebook toggle; set `MAIN_COMPUTER_RAG_DOCKER_ENABLED=0` only when RAG-AT should stay retrieval-only after the executor is enabled.

Build the default executor image:

```powershell
docker build -t main-computer-executor:latest -f docker/executor/Dockerfile .
```

Enable it for the viewport server:

```powershell
$env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"
$env:MAIN_COMPUTER_EXECUTOR_IMAGE = "main-computer-executor:latest"
$env:MAIN_COMPUTER_EXECUTOR_ROOT = "runtime/executor"
$env:MAIN_COMPUTER_EXECUTOR_TIMEOUT_S = "120"
# Optional RAG-AT kill switch if Docker should stay off for RAG only:
# $env:MAIN_COMPUTER_RAG_DOCKER_ENABLED = "0"
python -m main_computer.cli viewport --port 8765
```

Executor endpoints:

```text
GET  /api/executor/status
GET  /api/executor/uploads
POST /api/executor/uploads?filename=<name>
POST /api/executor/run
POST /api/executor/ai
GET  /api/executor/artifacts/<job_id>/<relative_path>
```

Large uploads should post the raw file body directly instead of base64 JSON:

```powershell
Invoke-WebRequest `
  -Method POST `
  -Uri "http://127.0.0.1:8765/api/executor/uploads?filename=data.csv" `
  -ContentType "text/csv" `
  -InFile ".\data.csv"
```

A manual executor run takes a controlled JSON command. Inputs appear read-only under `/inputs/<upload_id>/payload.bin`; job outputs written under `/outputs` are returned as artifact download URLs.

```json
{
  "command": "python - <<'PY'\nfrom pathlib import Path\nprint(Path('/inputs').exists())\nPath('/outputs/result.txt').write_text('ok\\n')\nPY",
  "cwd": "/workspace",
  "timeout_s": 30,
  "network": false,
  "input_ids": []
}
```

The executor runs Docker with `--network none` by default, `--cap-drop ALL`, `no-new-privileges`, memory/CPU/PID limits, read-only `/inputs`, and writable per-job `/workspace` and `/outputs`.

### Executor AI tool loop

Part 3 adds an explicit AI-to-executor bridge at `/api/executor/ai`. The normal `/api/chat` path is unchanged. The model is asked to return either one `execute_shell` JSON request or a final JSON answer. By default the endpoint returns the first tool request for operator approval instead of running it.

Proposal-only call:

```json
{
  "prompt": "Inspect the uploaded CSV and tell me what columns it has.",
  "upload_ids": ["upload_0123456789abcdef"],
  "max_steps": 2,
  "auto_run": false
}
```

To allow bounded multi-step execution, both the server environment and request must opt in:

```powershell
$env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"
$env:MAIN_COMPUTER_EXECUTOR_AI_AUTO_RUN = "1"
$env:MAIN_COMPUTER_EXECUTOR_AI_MAX_STEPS = "4"
```

Then:

```json
{
  "prompt": "Inspect the uploaded CSV, compute a summary, and write summary.json.",
  "upload_ids": ["upload_0123456789abcdef"],
  "max_steps": 4,
  "auto_run": true
}
```

Network remains blocked unless `MAIN_COMPUTER_EXECUTOR_AI_ALLOW_NETWORK=1` is also set. Tool results are fed back into the model as `command_output` messages until it returns a final answer or the step limit is reached.



## RAG Bootstrap Harness

The backend-only RAG harness lets you test the thinking/retrieval bootstrap flow before wiring it to the frontend. It does not run Docker commands and defaults to deterministic no-model retrieval.

Run the convenience script from the repository root:

```powershell
python tools/test_rag_bootstrap.py --prompt "Inspect the Docker executor integration and propose the next safe backend step" --no-model
```

Or run the reusable module directly:

```powershell
python -m main_computer.rag_harness `
  --prompt "Inspect the Docker executor integration and propose the next safe backend step" `
  --repo-dir . `
  --queries "executor routes,docker executor,tool loop,tests" `
  --max-context-chars 30000 `
  --no-model
```

Optional model-backed decomposition and grounded planning:

```powershell
python -m main_computer.rag_harness `
  --prompt "Plan the next executor approval-loop backend change" `
  --repo-dir . `
  --use-model
```

Each run writes replayable artifacts under:

```text
diagnostics_output/rag_runs/<run_id>/
  run.json
  01_intake.json
  02_task_decomposition.json
  03_context_inventory.json
  04_retrieval.json
  context_chunks.json
  grounded_prompt.txt
  05_context_brief.json
  06_grounded_plan.json
  final_plan.json
```

The harness flow is:

```text
prompt
  -> intake
  -> task decomposition
  -> context inventory
  -> deterministic retrieval
  -> bounded context chunks
  -> context brief
  -> grounded plan
```

Use `--upload-id upload_<id>` to include executor upload metadata in the context inventory. Uploaded file bytes are not read by the RAG harness; later executor stages can inspect `/inputs/<upload_id>/payload.bin` only after approval.



### Real-model RAG smoke test

Use this before wiring the thinking pathway into the frontend. It runs the CSV Audit Script Builder scenario through the model-backed RAG harness, validates the returned plan shape, and writes a replayable report. It does not run Docker, write repo files, or create the script yet.

```powershell
$env:MAIN_COMPUTER_PROVIDER = "ollama"
$env:MAIN_COMPUTER_MODEL = "qwen2.5:1.5b"
$env:MAIN_COMPUTER_OLLAMA_TIMEOUT_S = "600"

python -m main_computer.rag_model_smoke `
  --repo-dir . `
  --max-context-chars 30000 `
  --run-id csv_audit_model_smoke
```

Convenience wrapper:

```powershell
python tools/test_rag_model_csv_audit.py --repo-dir . --run-id csv_audit_model_smoke
```

The smoke test writes:

```text
diagnostics_output/rag_runs/csv_audit_model_smoke/
  final_plan.json
  model_smoke_report.json
  grounded_prompt.txt
  run.json
```

Use `--strict` when you want warnings, such as missing evidence or missing approval flags, to fail the run.

## Git Control CLI

`git-control.py` is the repository-root command planner, git-command runner, and shim library. The human-facing planning entrypoint is:

```powershell
python git-control.py --plan
```

Every plan now creates a first-class plan shim. The plan shim is documentation-only, but it includes the reusable read-only inspection shims created by the same plan. That gives the local AI a stable context handle to feed into later questions without regenerating commands:

```powershell
python git-control.py --plan --prompt "inspect the current branch and suggest the next git action"
python git-control.py --include-shim <plan-shim-id> --git status --short
```

The same script can run arbitrary git arguments directly. Every direct command is saved as a reusable `.shim` file under `.git/git-control/shims`, and command/run records are stored under `.git/git-control` so the local AI can include the shim chain and latest computed sum as future context.

```powershell
python git-control.py --git status --short
python git-control.py --git diff --stat
python git-control.py --run-shim <shim-id>
python git-control.py --sum
```

Shims are intentionally metadata-rich text files instead of opaque generated scripts. The top of a shim uses `#` comments for parseable metadata such as id, type, title, summary, safety, cwd, display command, included shims, tags, and extra state. The body uses small git-control shim-code directives:

```text
# git-control-shim-format: 2
# id: git-status-short-...
# shim-type: git
# safety: read-only
# display-command: git status --short
shim-doc "Executable git command: git status --short"
shim-include "plan-git-control-plan-context-..."
git ["status", "--short"]
```

Documentation-only shims use the same format but contain `shim-doc`, `shim-meta`, and `shim-note` instead of a `git` directive. That supports simple shims that explain a standard git command without running it:

```powershell
python git-control.py --doc-shim clean
python git-control.py --doc-shim restore
python git-control.py --show-shim <shim-id>
```

Plan mode writes a plan JSON, a plan shim, reusable read-only inspection shims, and `.git/git-control/sum.json`. It does not guess write commands when the prompt is empty; it records the current git state so the next local AI question can decide from real status, diff, branch, remote, stash, and recent-commit context.


## OpenClaw Integration

This repo now includes a narrow local bridge plus a sample OpenClaw tool plugin so OpenClaw can call the existing `main_computer` runtime instead of shelling out blindly.

Recommended architecture:

1. Run `main_computer` locally with the dedicated bridge:
   ```powershell
   cd "$env:USERPROFILE\dsl\main_computer"_test
   $env:MAIN_COMPUTER_OLLAMA_TIMEOUT_S = "600"
   $env:MAIN_COMPUTER_WORKSPACE = Join-Path $env:USERPROFILE "dsl"
   $env:MAIN_COMPUTER_PROVIDER = "ollama"
   $env:MAIN_COMPUTER_MODEL = "qwen2.5:1.5b"
   $env:MAIN_COMPUTER_OPENCLAW_TOKEN = "replace-this-if-you-want-auth"
   python -m main_computer.cli openclaw-bridge --host 127.0.0.1 --port 8767 --token $env:MAIN_COMPUTER_OPENCLAW_TOKEN
   ```

2. Install the local OpenClaw plugin from:
   ```text
   integrations/openclaw/main-computer-local-plugin
   ```

3. Point the plugin at the bridge in `openclaw.json`:
   ```json
   {
     "plugins": {
       "entries": {
         "main-computer-local": {
           "enabled": true,
           "config": {
             "baseUrl": "http://127.0.0.1:8767",
             "token": "replace-this-if-you-set-a-token",
             "timeoutMs": 120000
           }
         }
       }
     }
   }
   ```

The bridge currently exposes a deliberately small surface:

- `GET /v1/health`
- `GET /v1/capabilities`
- `GET /v1/projects`
- `POST /v1/chat`
- `POST /v1/project/inspect`

That keeps the OpenClaw plugin pointed at a stable, loopback-safe API instead of the much broader browser viewport endpoints.

## Development Workflow

Current folder roles:

- `$env:USERPROFILE\dsl\main_computer`: planning/source area for the main computer idea and the long-running `TODO.md`.
- `$env:USERPROFILE\dsl\main_computer_test`: active development and test copy. Make normal code changes here first.
- `$env:USERPROFILE\dsl\main_copmputer_production`: promoted production export. Copy tested changes here only after the test copy is green.

Normal change process:

1. Edit files in `main_computer_test`.
2. Run focused unit tests for the area you changed.
3. Run the browser harness when viewport, widget, fullscreen, diagnostics, debug, or graphical behavior changes.
4. Restart the local viewport so the browser is using the new code.
5. Copy the changed files to `main_copmputer_production` after verification.
6. Update `TODO.md` in `main_computer` and copy it to `main_computer_test` when backlog items change.

Useful development commands:

```powershell
cd "$env:USERPROFILE\dsl\main_computer"_test
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B -m unittest discover -s tests
python -B -m main_computer.cli harness --url http://127.0.0.1:8765
```

Start or restart the viewport during development:

```powershell
cd "$env:USERPROFILE\dsl\main_computer"_test
$env:MAIN_COMPUTER_OLLAMA_TIMEOUT_S = "600"
python -B -m main_computer.cli viewport --host 127.0.0.1 --port 8765
```

Manual service control for the test backend:

```powershell
cd "$env:USERPROFILE\dsl\main_computer"_test
.\dev-control.ps1 status
.\dev-control.ps1 start -Mode local
.\dev-control.ps1 shutdown -Mode local
.\dev-control.ps1 restart -Mode local
.\dev-control.ps1 start -Mode docker
.\dev-control.ps1 shutdown -Mode docker
```

Use `dev-control.ps1` directly for manual work so the run mode and URL are explicit. `control-main-computer.ps1` is a legacy local-mode helper for automated callers and refuses manual runs unless the caller passes `--auto-allow`.

To make a timestamped export zip for ChatGPT:

```powershell
cd "$env:USERPROFILE\dsl\main_computer"_test
.\export-main-computer-test.ps1
```

The archive is written under `$env:USERPROFILE\dsl\archive` and includes `aider.log` by default.

Development notes:

- The server emits route and API signals by default. Use `-noverbose` only when the signal stream is too noisy.
- Generated folders such as `harness_output*`, `diagnostics_output*`, `revision_control`, `debug_asset_revisions`, `debug_assets`, and `energy_credits` are runtime artifacts. Do not treat them as the primary source for code changes.
- The production sync is still manual. The TODO list includes adding a repeatable export/sync command.
- The default local Ollama model is the fast `qwen2.5:1.5b`. For larger optional local models, use `MAIN_COMPUTER_OLLAMA_TIMEOUT_S=600` or `--ollama-timeout-s 600`.
- If the viewport says the console is out of date, refresh the browser after restarting the local server.

## Console Viewport

Start the local browser console:

```powershell
python -m main_computer.cli viewport --port 8765
```

The server emits request and route signals by default. Suppress them only when needed:

```powershell
python -m main_computer.cli viewport --port 8765 -noverbose
```

Open:

```text
http://127.0.0.1:8765
```

The viewport uses the same provider settings as the CLI. By default it passes prompts through local Ollama with the fast `qwen2.5:1.5b` model.

Ollama Debug Mode now lives in separate interfaces so it does not occupy the normal command viewports:

```text
http://127.0.0.1:8765/debug/text
http://127.0.0.1:8765/debug/graphical
```

Debug mode is disabled until the user enables it in a debug interface, uses local Ollama with `qwen2.5:1.5b` by default, can ask the model debug questions, and can read, write, or ask the local model to revise files inside the running `main_computer` project. Set `MAIN_COMPUTER_OLLAMA_DEBUG_PASSCODE` to require a passcode before enabling or using the debug routes.

The debug interfaces also include a debug asset framework. Saved debug assets are stored under `debug_assets` in the running project and can hold scan results, model notes, generated snippets, or other text artifacts that should survive past the current debug pane.

Debug assets have their own isolated asset-state history. Asset changes can be added, removed, reset, and restored without reverting project code or the rest of the main computer state. Asset-state snapshots are stored under `debug_asset_revisions`.

Revision control is built into the viewport as a local checkpoint store:

```text
http://127.0.0.1:8765/revision
```

Snapshots are stored under `revision_control` in the running project. The revision page can create checkpoints, diff a file against a checkpoint, restore a single file, or restore the system files from a checkpoint. Each system checkpoint records the matching debug asset state. Restoring a system checkpoint also sets `debug_assets` back to that moment, while later asset-only states remain available under `debug_asset_revisions`. Debug writes and Gemma-powered revisions automatically create a checkpoint before replacing an existing file.

The default page is the text console. The graphical widget test is layered as an optional view:

```text
http://127.0.0.1:8765/graphical
```

The graphical page is a widget test surface with command bands, system readouts, a local systems sweep, quick prompt controls, project tiles, and a central command channel. Large panels stay inside the browser viewport; overflowing project/widget areas are scrollable and searchable.

Switching between the text console and graphical widget view preserves the shared browser session: transcript history and the current prompt draft are restored in both modes.

The graphical widget view includes a client-side Buddhabrot renderer. Its coordinate convention is intentionally swapped for this project: the horizontal x-axis is imaginary and the vertical y-axis is real. The renderer runs continuously in browser-side slices, with controls for orbits per slice and delay after each displayed slice.

When a prompt is sent, both text and graphical modes show a working indicator with a spinner and progress strip until the response returns.

Both modes poll the local workspace timestamp every few seconds. The viewport shows when the console loaded, the newest local directory timestamp, and an out-of-date warning when the local directory is newer than the loaded console.

## Widget Harness

Run the browser harness against a disposable test server:

```powershell
python -m main_computer.cli harness
```

Run it against an already running viewport:

```powershell
python -m main_computer.cli harness --url http://127.0.0.1:8765
```

The harness checks both the text console and `/graphical`, verifies search, quick controls, prompt submission, bounded layout, and scrollable project/widget panels. It writes screenshots and `widget_harness_report.json` to `harness_output`.

## Diagnostics

Run layered diagnostics:

```powershell
python -m main_computer.cli diagnostics --level health
python -m main_computer.cli diagnostics --level server
python -m main_computer.cli diagnostics --level widgets
python -m main_computer.cli diagnostics --level live
python -m main_computer.cli diagnostics --level functional
```

Levels:

- `health`: configuration, workspace, catalog, provider construction
- `server`: `health` plus disposable viewport routes and API round-trips
- `widgets`: `server` plus the browser widget harness
- `live`: `widgets` plus a real provider chat call through the configured provider
- `functional`: `live` plus an exact-response provider assertion

Special opt-in diagnostics are separate from Levels 1-5 and only run when explicitly requested:

```powershell
python -m main_computer.cli diagnostics --level ollama-probe --output-dir diagnostics_output_ollama_probe
python -m main_computer.cli diagnostics --level ollama-visibility --output-dir diagnostics_output_ollama_visibility
```

`ollama-probe` checks Ollama's raw transport before asking the model to reason: `/api/tags`, `/api/generate`, and `/api/chat`, recording response length, `thinking` length, `done_reason`, eval counts, and duration fields. The small diagnostic calls use `think: false` so the output budget is not spent on hidden reasoning.

`ollama-primer` is the lightest follow-up after a simple ping. It uses `think: false` and asks for `READY`, then `COUNT-3`, then two one-label `YES` checks:

```powershell
python -m main_computer.cli diagnostics --level ollama-primer --output-dir diagnostics_output_ollama_primer
```

`ollama-visibility` calls local Ollama directly using a low-output primer ladder: first a tiny `READY` response, then project labels, then a compact main-computer manifest. It records the model response and expected-file checks in `diagnostics_report.json`.

The same real Ollama visibility check also exists as a skipped-by-default unittest:

```powershell
$env:MAIN_COMPUTER_RUN_SPECIAL_OLLAMA_TESTS = "1"
python -m unittest tests.test_special_ollama_visibility
```

The viewport includes five diagnostic buttons:

- Level 1: `functional`
- Level 2: `live`
- Level 3: `widgets`
- Level 4: `server`
- Level 5: `health`

The viewport also includes a separate `Ollama Visibility` diagnostic control. It is not part of Level 1-5 and only runs when that control is clicked.

Use `live` and `functional` only when Ollama or OpenAI is actually available. Lower levels use a deterministic harness provider for chat checks.

## Design

- `main_computer.catalog` scans local project folders and marker files.
- `main_computer.providers.ollama` talks to Ollama's local HTTP API.
- `main_computer.providers.openai_provider` talks to OpenAI through the Python SDK.
- `main_computer.router.MainComputer` builds workspace context and passes requests to the selected provider.

This is intentionally small for version one. The next natural step is tool execution: let the model ask the main computer to inspect files, run tests, or launch specific subproject commands through explicit, auditable tools.


## ONLYOFFICE local workbook editor

The `ONLYOFFICE` app stores native `.xlsx` workbooks and opens them through ONLYOFFICE Docs.
Windows local installs use WSL-native ONLYOFFICE by default; Docker remains available for
production-style local deployments. The reserved local port is `18084`, and Docker binds it to
`127.0.0.1` so it is not exposed outside the machine.

```powershell
.\tools\onlyoffice\onlyoffice-control.ps1 install -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 start -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 status -Mode wsl -Port 18084
```
