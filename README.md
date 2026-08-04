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


## Agent harness workflow packet

Main Computer includes a small ECC-inspired local harness helper for agentic development work. It does not vendor or install ECC; it captures the useful operating loop locally: intent, plan, focused tests, implementation, independent review, verification, memory, and patch-artifact safety.

Generate a task packet from the repository root:

```bat
python tools\ecc_workflow.py profile --repo . --profile developer --stack python --task "describe the task" --out runtime\agent_harness\latest
```

The command writes a JSON manifest and Markdown packet under `runtime/agent_harness/latest`. The developer profile selects only the skills relevant to this repository instead of loading the entire catalog into context.

Evaluate the delivery gate before calling a task complete:

```bat
python tools\ecc_workflow.py gate --changed-file main_computer/ecc_workflow.py --check pytest=pass --check dry-run=pass --review implementation=approved --review safety=approved
```

The gate blocks empty changed-file inventories, failing checks, missing review signoffs, unsafe paths, and rationalized completion language such as “should work” or “probably fine.”


## Docker and Podman compatibility

Main Computer resolves container commands through a small Docker-compatible runtime layer. Docker remains the default when it is available, but Podman can be selected without changing code:

```powershell
$env:MAIN_COMPUTER_CONTAINER_RUNTIME = "podman"
```

Useful overrides:

```powershell
$env:MAIN_COMPUTER_CONTAINER_COMMAND = "podman"
$env:MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND = "podman compose"
```

Legacy Docker-named overrides are still honored for existing scripts:

```powershell
$env:MAIN_COMPUTER_DOCKER_COMMAND = "docker"
$env:MAIN_COMPUTER_DOCKER_COMPOSE = "docker compose"
```

The Astrometric 3D renderer uses the same layer for both Compose lifecycle commands and direct container diagnostics such as `inspect`, `logs`, `port`, `rm`, and `exec`. Its CUDA/GPU mode still requires the selected runtime to have working NVIDIA GPU container support. Docker Compose uses the existing `gpus: all` service declaration; Podman setups may also need Podman/NVIDIA CDI configuration on the host or Podman machine.

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
python .\tools\dev-chain-diagnosis.py --state .\runtime\deployments\dev\latest.json
```

This generates local runtime files such as `runtime/deployments/dev/latest.json`, `runtime/dev-chain/latest.json`, `runtime/dev-chain/latest.env`, and `runtime/deployments/hub-admin-wallet.json`. They are machine-local state and should stay out of Git.

To publish the same app-facing deployment runtime from the local QBFT test
network instead of Anvil, use the local Coolify-managed `test` seed. This is the
normal local QBFT deploy path; it reuses the Website Builder local-Coolify token
file/bootstrap contract and publishes the network-scoped manifest:

```powershell
python .\tools\coolify_qbft_network.py apply test --deploy-contracts
python -m main_computer.cli hub --network test
```

The lower-level Besu/QBFT smoke harness remains available when you need to prove
the raw four-validator backend without Coolify:

```powershell
python .\tools\smoke_besu_qbft_one_validator.py restart --deploy-contracts --deployment-environment test --docker-subnet 10.241.0.0/24
```

The local Coolify test deployment writes `runtime/deployments/test/latest.json`
with `environment=test`, chain id `42424241`, and RPC URL
`http://127.0.0.1:30010`.


Remote QBFT and mainnet operator docs:

```text
pretty_docs/crypto-network-coolify-testnet-runbook.md
pretty_docs/mainnet-chain-redeploy-runbook.md
pretty_docs/hub-chain-testnet-mainnet-architecture.md
```

The stable rule is that private state carries secrets, private keys, and
operator topology. Contract addresses are public deployment facts and live in
`runtime/deployments/<network>/latest.json`,
`runtime/deployments/<network>/runs/<run_id>/deployment.json`, and
`main_computer/config/<network>_contracts.json`, not in
`runtime/state/main_computer.private.yaml`.


Local Hub topology fixtures live under `deploy/hub-topology/` so they are not
tied to the stable-Hub lab or exp/FDB implementation names:

```text
deploy/hub-topology/dev-topology.json
deploy/hub-topology/smoke-topology.json
deploy/hub-topology/test-topology.json
```

The `test` topology is the local Besu/QBFT topology. It uses chain id
`42424241`, RPC `http://127.0.0.1:30010`, and Hub entry URLs
`http://127.0.0.1:8780`, `http://127.0.0.1:8781`, and
`http://127.0.0.1:8782`.



Each dev/test deployment also publishes a chain-funded smoke client wallet in the
network-scoped deployment directory. Use the network-aware Hub client smoke to
verify the Hub, chain RPC, deployment manifest, contract code, smoke wallet
balance, and a paid worker/credit/claim flow:

```powershell
python .\scripts\smoke_hub_network_client.py --network dev
python .\scripts\smoke_hub_network_client.py --network test
```

The smoke reads `runtime/deployments/<network>/latest.json` by default and uses
the Hub network registry to find the Hub URL and chain RPC URL.

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

## MCEL documentation

Start with the canonical status document. It distinguishes declared maturity, adapter coverage, repository-bound proof, and authorized future work.

### Conceptual and contract path

```text
pretty_docs/mcel-status-and-roadmap.md
pretty_docs/mcel-system-guide.md
pretty_docs/mcel-user-space-contract.md
```

### Build and edit an application

```text
pretty_docs/mcel-ai-authoring-language-executive-overview.md
pretty_docs/mcel-ai-authoring-semantic-boundary.md
pretty_docs/mcel-application-ir-and-compiler-migration.md
pretty_docs/mcel-application-ir-schema-and-normalization.md
pretty_docs/mcel-existing-application-definition-migration-inventory.md
pretty_docs/mcel-constrained-expression-model.md
pretty_docs/mcel-consequential-effects-and-proof-accounting.md
pretty_docs/mcel-official-vanilla-javascript-dsl.md
pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md
pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md
pretty_docs/mcel-ai-application-authoring-cycle.md
pretty_docs/mcel-ai-authoring-pattern-catalog.md
pretty_docs/mcel-semantic-change-and-evidence-impact.md
pretty_docs/mcel-ai-authoring-and-migration-benchmark.md
pretty_docs/mcel-ai-authoring-documentation-completeness-review.md
pretty_docs/mcel-application-authoring.md
pretty_docs/mcel-code-studio-example.md
pretty_docs/mcel-project-edit-transaction.md
pretty_docs/mcel-requirements-language.md
```

The AI authoring overview explains the current authoring problem through concrete code examples: repeated field declarations, item-key plumbing, distributed mutations, lifecycle wiring, proof duplication, diagnostic quality, and feature-edit cost. The semantic-authoring-boundary specification then states, for each application concept, what the AI must declare, what MCEL may generate, what must be rejected, and what proof must explain. `pretty_docs/mcel-application-ir-and-compiler-migration.md` defines the stable center of the migration: requirements-driven apps, scaffolded/explicit packages, the current high-level `application.js`, and the future official DSL must converge on one comparable MCEL Application IR while independent acceptance, browser, receipt, and proof evidence remain outside the compiler. `pretty_docs/mcel-application-ir-schema-and-normalization.md` specifies the first concrete `mcel.application-ir.v1` shape, normalization rules, semantic/source-binding fingerprints, and Counter/Workbench worked slices. `pretty_docs/mcel-existing-application-definition-migration-inventory.md` records the current requirements-registry, surface-led, scaffolded, normalized, blueprint, and legacy definition families so Git Tools, Code Editor, Document Editor, and the other current apps are not lost during reorganization. `pretty_docs/mcel-constrained-expression-model.md` defines the typed, inspectable expression graph that replaces opaque callbacks while preserving pure calculation, canonical transitions, provisional reconciliation, domain operators, and the capability/effect boundary. `pretty_docs/mcel-consequential-effects-and-proof-accounting.md` then defines effect ownership, runtime effect instances, terminal dispositions, minimum evidence, cleanup and retained residue, uncertainty and recovery, and the closed accounting rule required before proof may claim that every consequential effect is explained. `pretty_docs/mcel-official-vanilla-javascript-dsl.md` fixes the one official `mcel.dsl.v1` source form: strict CommonJS vanilla JavaScript, `@mcel/app`, explicit semantic IDs, constrained builder callbacks, semantic handles, app-local modules, capability lifecycles, surface/layout declarations, and ordered proof scenarios that compile into the IR. `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md` specifies the stable diagnostic envelope, semantic paths, repair classes, dependency ordering, candidate-versus-last-proven truth, evidence invalidation, narrow reruns, and reviewable repair transactions that let an AI return to the correct authoring stage without guessing. `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md` now fixes the file paths and ownership classes, candidate-versus-promoted package boundary, versioned explicit and DSL scaffold modes, legacy importer rules, generated projections, feature-level compatibility reports, atomic promotion, rollback, drift detection, and preservation paths for Counter, Workbench, Git Tools, Code Editor, Document Editor, and the remaining application families. `pretty_docs/mcel-ai-application-authoring-cycle.md` turns those contracts into the stage-gated path an AI follows from requirements and legacy inventory through modeling, effects, surface, scenarios, candidate compilation, diagnostics, compatibility, evidence, proof, promotion, and later modification. `pretty_docs/mcel-ai-authoring-pattern-catalog.md` supplies task-oriented examples for canonical mutations, forms, keyed collections, derived queries, refusals, async progress, cancellation, per-item concurrency, Git mutation, stale-safe file save, document export, multi-instance isolation, cross-intent workflows, and feature changes. `pretty_docs/mcel-semantic-change-and-evidence-impact.md` defines dependency-aware modification: semantic change sets, impact closure, authoring-stage re-entry, evidence renewal and audited reuse, conservative fallback, and worked Git Tools, Code Editor, Document Editor, Counter, and Workbench change cases. `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` defines the controlled creation, migration, modification, repair, evidence, proof, reliability, and economy trials that must pass before MCEL may claim the DSL is better for AI authoring. `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` audits the entire chain, closes the remaining v1-level ambiguities, distinguishes documentation completeness from implementation or benchmark success, and limits the first permissible implementation wave to the stable IR kernel after explicit authorization. Together these documents are the entry point for the documentation-first MCEL AI Authoring Language program. The bounded implementation now includes the read-only IR kernel in `main_computer/mcel_application_ir.py`, the Wave 2A constrained-expression kernel in `main_computer/mcel_constrained_expression.py`, and the Counter-only Wave 2B DSL front end in `main_computer/mcel_dsl_compiler.py` plus `main_computer/mcel_dsl_runtime.js`. Validate the Counter IR with `python tools/mcel_application_ir.py --input tests/fixtures/mcel_application_ir/contract-counter.ir.json`, inspect its typed expression graph with `python tools/mcel_constrained_expression.py --input tests/fixtures/mcel_application_ir/contract-counter.ir.json`, and compile the official Counter DSL candidate with `python tools/mcel_dsl_compile.py --input tests/fixtures/mcel_dsl/contract-counter.application.js --compare-ir tests/fixtures/mcel_application_ir/contract-counter.ir.json`. Wave 2B constructs and compares candidate IR only. It does not execute application behavior, perform capabilities, generate contracts, change the live Counter package, promote a candidate, reuse evidence, or retire the legacy path.

The authoring guide distinguishes live global APIs from the application-local layout facades used by Code Editor and Git Tools. It supports repository-aware development today, but it is not yet a standalone SDK or generated starter-app workflow. The project-edit transaction provides the shared hash-guarded, multi-file replacement boundary for staged validation, overlay packaging, dry-run, and explicit reviewed apply. It does not yet provide semantic edit planning or Code Editor and MCEL Lab bindings. The user-space contract is the planning surface for deciding what application authors may rely on; internal law-module names are implementation detail.

### Truth and evidence

```text
pretty_docs/mcel-app-truth-gate.md
pretty_docs/mcel-repository-truth-audit.md
pretty_docs/mcel-acceptance-evidence.md
pretty_docs/mcel-observation-and-inference.md
pretty_docs/mcel-contract-guarantees.md
```

### Application requirements

```text
pretty_docs/mcel-requirements-language.md
pretty_docs/mcel-code-editor-requirements.md
pretty_docs/mcel-git-tools-requirements.md
pretty_docs/mcel-calculator-requirements.md
pretty_docs/mcel-file-explorer-requirements.md
pretty_docs/mcel-website-builder-requirements.md
```

### Semantic surface and Lab guides

```text
pretty_docs/mcel-code-studio-example.md
pretty_docs/mcel-lab-blueprint-studio.md
pretty_docs/mcel-semantic-surface-ir.md
pretty_docs/mcel-shared-layout-grammar.md
pretty_docs/mcel-surface-extractors.md
pretty_docs/mcel-surface-roundtrip.md
```

MCEL Lab is the self-hosting app-aspect inspector for developing good-looking solid apps. Its blueprint workflow preserves source bindings, findings, and refactor annotations as reviewable evidence.

The system guide explains the source/runtime/serialization boundary, evidence packets, proof obligations, the subsumption lattice, and the adoption-case gate. The application-authoring guide gives the current repository-aware build sequence and maps its conceptual file responsibilities to the live Git Tools implementation. The requirements language defines the shared `mcel-*` grammar and registry workflow. The truth-gate and repository-audit documents define how requirements, adapters, surface policy, runtime evidence, acceptance evidence, and epistemic claims are combined without treating prose as proof. The status and roadmap document is the sole human-readable authority for current MCEL state and upcoming code work.

## Git Tools documentation

The Git Tools documentation-first MCEL requirements contract is documented in:

```text
pretty_docs/mcel-git-tools-requirements.md
```

The project-level publishing redesign remains documented as a workflow slice in:

```text
pretty_docs/git-tools-project-level-publishing.md
```

The MCEL requirements contract defines Git Tools as a repository evidence and governed-publishing workbench. Its registered adapter now reports full application semantic readiness for the documented preflight-oriented scope, including read-only inspection, governed push preparation, evidence-only ignore-rule preview, commit-plan preflight, and Local Gitea target preparation. Repository-bound runtime and acceptance evidence remain separate proof inputs. The project-level publishing note keeps everyday project publishing actions on the project card while leaving server lifecycle, recovery, mirror setup, and advanced Git/Gitea controls in the support/server area.

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

## Operator runbooks

Hosted Hub deployment, including the regular Hub and the experimental
FoundationDB-backed Hub, is documented in:

```text
pretty_docs/hub-coolify-deploy-runbook.md
```

The Cloudflare hidden-origin mail worker plan is documented in:

```text
pretty_docs/cloudflare-mail-worker-hidden-ingest-runbook.md
```

The Great Library hosted email account roadmap is documented in:

```text
pretty_docs/great-library-email-account-system-plan.md
```

The Hub runbook explains how to use `tools/coolify_hub_service.py` for regular
`testnet`/`mainnet` deployment, side-by-side experimental FDB deployment, and
explicit experimental replacement of the regular Hub. The mail worker runbook
explains the staged contract flow for `tools/cloudflare_mail_worker.py`.
The account-system plan explains how the working ingest path grows into
signup-driven Great Library mailboxes, user passwords, aliases, webmail, and
outbound sending.


## Counter DSL promotion rehearsal

After the Counter DSL candidate has passed isolated projection and evidence, rehearse the authority transition without modifying the live package:

```powershell
python tools/mcel_counter_promotion_rehearsal.py --check
```

Wave 6 stages an exact promotion plan, generated-file ownership manifest, rollback material, and a disposable promoted repository workspace beneath candidate runtime state. It reruns compatibility, package/runtime projection, acceptance, Chromium observation, effect accounting, and application proof, then restores the original Counter package byte-for-byte. A passing rehearsal may report promotion eligibility, but it never executes promotion.
