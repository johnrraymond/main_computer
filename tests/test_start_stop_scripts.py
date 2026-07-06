from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_and_stop_bats_use_source_tree_runtime_session() -> None:
    start_script = (ROOT / "start.bat").read_text(encoding="utf-8")
    start_v2_script = (ROOT / "start_v2.bat").read_text(encoding="utf-8")
    stop_script = (ROOT / "stop.bat").read_text(encoding="utf-8")
    stop_v2_script = (ROOT / "stop_v2.bat").read_text(encoding="utf-8")

    assert "start_v2.bat" in start_script
    assert "scripts\\main-computer-start-stop.ps1" in start_v2_script
    assert "-Action start -Root" in start_v2_script
    assert "run-main-computer.ps1" not in start_script
    assert "run-main-computer.ps1" not in start_v2_script

    assert "stop_v2.bat" in stop_script
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")
    assert 'return Join-Path $RootPath "runtime\\start_stop"' in helper
    assert 'return Join-Path (Get-StartStopRuntime $RootPath) "start-session.json"' in helper
    assert "-Action stop -Root" in stop_v2_script
    assert "run-main-computer.ps1" not in stop_script
    assert "run-main-computer.ps1" not in stop_v2_script


def test_start_stop_helper_stops_children_before_supervisor_and_verifies_taskkill_result() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert 'Add-PidCandidate $candidates $session.launcher.pid "supervisor" "runtime/start_stop/start-session.json launcher.pid" 90' in helper
    assert 'Add-PidCandidate $Candidates $State.service.pid "supervisor" "runtime/service_supervisor/state.json service.pid" 90' in helper
    assert 'Add-PidCandidate $Candidates $child.pid $name ("runtime/service_supervisor/state.json children." + $name + ".pid") 20' in helper
    assert 'return [pscustomobject]@{ role = "supervisor"; order = 90 }' in helper
    assert 'if ([string]$existing.role -eq "supervisor")' in helper

    assert 'Sort-Object -Property @{ Expression = { [int]$_[\'order\'] } }, @{ Expression = { [int]$_[\'pid\'] } }' in helper

    assert "function Wait-ProcessGone" in helper
    assert "process_gone_after_attempt" in helper
    assert 'state = $(if ($lastResult.exit_code -eq 0) { "terminated" } else { "terminated-after-check" })' in helper


def test_start_stop_helper_waits_for_docker_compose_stacks_to_disappear() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert '"down", "--remove-orphans", "--timeout", "30"' in helper
    assert "function Wait-DockerComposeStackGone" in helper
    assert '"down-after-wait"' in helper
    assert '"ps", "-a", "-q"' in helper


def test_v2_batch_files_are_location_aware_and_do_not_require_env_files() -> None:
    start_script = (ROOT / "start_v2.bat").read_text(encoding="utf-8")
    stop_script = (ROOT / "stop_v2.bat").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert 'cd /d "%~dp0"' in start_script
    assert 'cd /d "%~dp0"' in stop_script
    assert '-StartedBy "start_v2.bat"' in start_script
    assert '-StartedBy "stop_v2.bat"' in stop_script
    assert "force-stops current app processes before launching" in start_script
    assert "Force-stopping current Main Computer app processes before launch; Docker stacks are left alone" in start_script
    assert "Force-stopping current Main Computer app processes; Docker stacks are left alone" in stop_script
    assert 'MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT=main-computer-applications' in start_script
    assert 'set "MAIN_COMPUTER_COOLIFY_PROJECT="' in start_script
    assert 'set "COOLIFY_COMPOSE_PROJECT="' in start_script
    assert 'set "COMPOSE_PROJECT_NAME="' in start_script
    assert 'set "MC_DOCKER_FLAG=-NoDocker"' in stop_script
    assert 'stop_v2.bat --with-docker' in stop_script
    assert "runtime\\start_stop\\main-computer-launcher.json" in start_script
    assert ".env" not in start_script
    assert ".env" not in stop_script

    assert 'ValidateSet("start", "stop", "status", "dev-hub-start")' in helper
    assert "Force-stopping current Main Computer app processes before launch; Docker stacks are left alone" in helper
    assert "Stop-MainComputer $RootPath $true" in helper
    assert "Docker stacks are left alone for app-only stop" in helper
    assert "function Add-CurrentMainComputerProcessCandidates" in helper
    assert "Get-NetstatListenRows -Ports (Get-MainComputerManagedPorts $LaunchContext)" in helper
    assert "allow_foreign_main_computer" in helper
    assert "Resolve-MainComputerLaunchContext" in helper
    assert "main-computer-launcher.json" in helper
    assert "venv_python" in helper
    assert 'tree_kind = "source"' in helper
    assert "Resolve-MainComputerPythonCommand" in helper
    assert 'python = $defaultPython' in helper
    assert "-FilePath $pythonCommand" in helper
    assert "Set-MainComputerLaunchEnvironment" in helper
    assert "Get-ControlRoot" in helper
    assert "Control root:" in helper
    assert '"--port", $controlPort' in helper
    assert '"--python-command", $pythonCommand' in helper
    assert 'MAIN_COMPUTER_PYTHON_COMMAND' in helper
    assert '.venv\\Scripts\\python.exe' in helper
    assert 'PYTHONPATH' in helper
    assert 'VIRTUAL_ENV' in helper
    assert 'treeKind -eq "installed"' in helper
    assert 'MAIN_COMPUTER_ETHEREUM_RPC_PORT' not in helper
    assert 'MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL = "http://127.0.0.1:18545"' in helper
    assert 'MAIN_COMPUTER_HUB_NETWORK = "dev"' in helper
    assert 'MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK = "1"' in helper
    assert 'MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL = "http://127.0.0.1:8545"' not in helper


def test_dev_compose_file_defaults_to_unleashed_project_and_mode_ports() -> None:
    compose = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "name: main-computer-unleashed" in compose
    assert "${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765" in compose
    assert "${MAIN_COMPUTER_HUB_PORT:-8770}:8770" not in compose
    assert "\n  hub:\n" not in compose
    assert "${MAIN_COMPUTER_HUB_WORKER_PORT:-8771}:8771" in compose
    assert ("ollama" + "/ollama") not in compose
    assert ("ollama" + "-data") not in compose
    assert ("MAIN_COMPUTER_" + "OLLAMA_HOST_PORT") not in compose
    assert ("http://" + "ollama" + ":11434") not in compose
    assert "http://host.docker.internal:11434" in compose
    assert "MAIN_COMPUTER_DOCKER_OLLAMA_BASE_URL" in compose
    assert "${MAIN_COMPUTER_ETHEREUM_RPC_PORT:-18545}:8545" not in compose
    assert "MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL: ${MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL:-http://host.docker.internal:18545}" in compose


def test_v2_helper_uses_mode_scoped_non_gitea_docker_projects() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "MAIN_COMPUTER_GITEA_SCOPE" in helper
    assert "MAIN_COMPUTER_GITEA_COMPOSE_PROJECT" in helper
    assert "MAIN_COMPUTER_DEV_COMPOSE_PROJECT" in helper
    assert "MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT" in helper
    assert "MAIN_COMPUTER_BLOCKCHAIN_COMPOSE_PROJECT" not in helper
    assert ("MAIN_COMPUTER_" + "OLLAMA_HOST_PORT") not in helper
    assert "MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT" in helper
    assert "MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT" in helper
    assert "main-computer-unleashed" in helper
    assert "main-computer-local-platform-unleashed" in helper
    assert '"main-computer-dev"' not in helper
    assert 'Get-EnvFirstValue @("MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT") "main-computer-applications"' in helper
    assert '"MAIN_COMPUTER_COOLIFY_PROJECT", "COOLIFY_COMPOSE_PROJECT", "COMPOSE_PROJECT_NAME"' in helper
    assert 'Get-EnvFirstValue @("MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT", "MAIN_COMPUTER_COOLIFY_PROJECT", "COMPOSE_PROJECT_NAME")' not in helper
    assert "function Start-MainComputerGiteaIfMissing" in helper
    assert "Get-MainComputerContainerRuntime $RootPath $PythonCommand" in helper
    assert "Assert-MainComputerExplicitContainerRuntimeAvailable $RootPath $launchContext $pythonCommand" in helper
    assert "Shared Gitea already present on port" in helper
    assert "installer/start path will not recreate it" in helper
    assert '"ps", "-a", "-q", "gitea"' in helper
    assert '"start", "gitea"' in helper
    assert '"up", "-d", "gitea"' in helper
    assert "function Start-MainComputerLocalPlatform" in helper
    assert '"local-platform"' in helper
    assert '"--project-name", $devComposeProject' in helper
    assert '"--project-name", $localPlatformProject' in helper


def test_v2_local_platform_publish_failure_warns_and_allows_supervisor_start() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "function Write-MainComputerGiteaWarning" in helper
    assert "Shared Gitea preparation failed" in helper
    assert "function Write-MainComputerLocalPlatformWarning" in helper
    assert 'Write-Warning ("Local platform startup failed ({0}). Continuing Main Computer startup; local hub/blog website containers may be unavailable."' in helper
    assert 'try {\n    $localPlatformStart = Start-MainComputerLocalPlatform $RootPath $pythonCommand' in helper
    assert 'state = "exception"' in helper
    assert 'if (-not $localPlatformStart.ok) {\n    Write-MainComputerLocalPlatformWarning $localPlatformStart\n  }' in helper
    assert 'throw ("Local platform startup failed: {0}" -f $localPlatformStart.state)' not in helper
    assert '$process = Start-Process `' in helper


def test_start_path_dev_hub_failure_warns_and_allows_startup_to_continue() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "function Write-MainComputerDevHubWarning" in helper
    assert 'Write-Warning ("Dev Hub startup failed ({0}). Continuing Main Computer startup; Hub-dependent features may be unavailable."' in helper
    assert "if (-not $NoDevHubRequested) {" in helper
    assert 'try {\n      $devHubStart = Start-MainComputerDevHubFresh $RootPath $launchContext $pythonCommand' in helper
    assert 'message = "Start-MainComputerDevHubFresh returned no status."' in helper
    assert 'if (-not $devHubStart.ok) {\n      Write-MainComputerDevHubWarning $devHubStart\n    }' in helper
    assert 'throw ("Dev Hub startup failed: {0}" -f $message)' not in helper
    assert 'throw "Dev Hub startup returned no status."' not in helper
    assert "New-StartSession $RootPath $launchContext $process.Id" in helper

def test_start_path_defers_dev_chain_reset_to_blockchain_service() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "function Test-MainComputerDevChainRpc" in helper
    assert 'method = "eth_chainId"' in helper
    assert "function Start-MainComputerDevChainIfNeeded" in helper
    assert "Start-MainComputerDevChainIfNeeded $RootPath $launchContext $pythonCommand" in helper
    assert "if ($probe.ok)" in helper
    assert "start path will not reset it" in helper
    assert 'state = "already-running"' in helper

    assert "blockchain service will retry dev-chain reset after executor Docker readiness" in helper
    assert 'state = "deferred-to-blockchain-service"' in helper
    assert 'retry_owner = "main_computer.blockchain_service"' in helper
    dev_chain_helper = helper[
        helper.index("function Start-MainComputerDevChainIfNeeded"):
        helper.index("function Resolve-MainComputerDevHubEndpoint")
    ]
    assert 'state = "reset-failed"' not in dev_chain_helper


def test_start_session_records_dev_chain_and_dev_hub_startup_status() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "[object]$DevChainStart" in helper
    assert "[object]$DevHubStart" in helper
    assert "dev_chain = $DevChainStart" in helper
    assert "dev_hub = $DevHubStart" in helper
    assert "$StartedByName $giteaStart $localPlatformStart $devChainStart $devHubStart" in helper


def test_start_path_starts_dev_hub_by_default_and_can_opt_out() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "function Resolve-MainComputerDevHubEndpoint" in helper
    assert "function Test-MainComputerDevHubStatus" in helper
    assert "function Wait-MainComputerDevHubStatus" in helper
    assert "function Stop-MainComputerDevHubForRestart" in helper
    assert "function Start-MainComputerDevHubFresh" in helper
    assert 'state = "skipped-disabled"' in helper
    assert "Run start.bat --no-dev-hub to skip it." in helper
    assert "Start-MainComputer $resolvedRoot $StartedBy ([bool]$NoDevHub)" in helper
    assert "if (-not $NoDevHubRequested) {" in helper


    assert "$hubBindHost" in helper
    assert "$hubBindPort" in helper
    assert "$hubPortText" in helper
    assert "$host =" not in helper.lower()
    assert 'MAIN_COMPUTER_DEV_HUB_AUTO_START' in helper
    assert '"MAIN_COMPUTER_HUB_NETWORK" "dev"' in helper
    assert '"MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK", "1", "Process"' in helper
    assert '"/api/hub/status"' in helper
    assert 'MAIN_COMPUTER_DEV_HUB_KIND" "exp-fdb"' in helper
    assert '"exp-fdb-hub.py"' in helper
    assert '"--runtime-env-file", [string]$runtimeEnvFile' in helper
    assert '"MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE"' in helper
    assert 'function Read-MainComputerRuntimeEnvFile' in helper
    assert 'function Merge-MainComputerRuntimeEnvFile' in helper
    assert 'function Ensure-MainComputerRuntimeEnvFile' in helper
    assert 'Created default dev Hub runtime env file at {0}.' in helper
    assert '$runtimeEnvStatus["created"] = [bool]$runtimeEnvEnsure.created' in helper
    assert '"--network-key", [string]$endpoint.network' in helper
    assert '"--topology", [string]$topologyPath' in helper
    assert '"-ports", [string]$hubPortsText' in helper
    assert '"--hub-id", [string]$hubId' in helper
    assert '"--require-multisession-auth"' in helper
    assert "function Resolve-MainComputerDevHubEndpoints" in helper
    assert "Wait-MainComputerDevHubEndpointsStatus $endpoints" in helper
    assert "Previous dev Hub stop check completed." in helper
    assert "Scanning for existing dev Hub listeners on ports" in helper
    assert "Waiting for dev Hub topology health on ports" in helper
    assert '"MAIN_COMPUTER_HUB_ENTRY_URLS"' in helper
    assert '"-m", "main_computer.cli"' in helper
    assert '"hub",' in helper
    assert '"--network", [string]$endpoint.network' in helper
    assert '"--chain-rpc-url", [string]$endpoint.chain_rpc_url' in helper
    assert '"--chain-id", [string]$endpoint.chain_id' in helper
    assert '"--bridge-backend", [string]$bridgeBackend' in helper
    assert '"--dev-chain-deployment-path", [string]$devChainDeploymentPath' in helper
    assert '"--contracts-path", [string]$contractsPath' in helper
    assert '"--allow-missing-bridge-signer"' in helper
    assert 'MAIN_COMPUTER_HUB_BRIDGE_BACKEND" "dev-chain"' in helper
    assert 'MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE" "1"' in helper
    assert 'MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER" "0"' in helper
    assert 'runtime\\deployments\\' in helper
    assert 'main_computer\\config\\' in helper
    assert 'MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH' in helper
    assert 'MAIN_COMPUTER_HUB_CONTRACTS_PATH' in helper
    assert 'MAIN_COMPUTER_HUB_TOPOLOGY' in helper
    assert 'deploy\\hub-topology\\dev-topology.json' in helper
    assert '"-noverbose"' in helper
    assert 'Set-Content -LiteralPath $pidPath -Value ([string]$process.Id) -Encoding ASCII' in helper

    assert "Resetting dev Hub topology on start path" in helper
    assert "Stop-MainComputerDevHubForRestart $RootPath $LaunchContext" in helper
    assert "Start-MainComputerDevHubFresh $RootPath $launchContext $pythonCommand" in helper
    assert 'Write-MainComputerDevHubWarning $devHubStart' in helper
    assert 'Get-DevHubPidPath $RootPath' in helper
    assert '".main_computer_dev_hub.pid"' in helper
    assert '"MAIN_COMPUTER_HUB_PORT" "8871"' in helper
    assert 'listener on dev Hub topology port' in helper
    assert 'Dev Hub PID:' in helper
    assert 'dev-hub' in helper



def test_dev_hub_start_bat_starts_only_the_dev_hub() -> None:
    dev_hub_start = (ROOT / "dev-hub-start.bat").read_text(encoding="utf-8")
    start_v2 = (ROOT / "start_v2.bat").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert 'cd /d "%~dp0"' in dev_hub_start
    assert "-Action dev-hub-start -Root" in dev_hub_start
    assert '-StartedBy "dev-hub-start.bat"' in dev_hub_start
    assert 'if not defined MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS set "MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS=20"' in dev_hub_start
    assert "Start-MainComputerDevHubOnly" in helper
    assert "Starting only the Main Computer dev Hub; app/supervisor startup is not requested." in helper
    assert 'action = "dev-hub-start"' in helper
    assert '"dev-hub-start.json"' in helper
    assert "Start-MainComputerDevHubFresh $RootPath $launchContext $pythonCommand" in helper
    assert "Dev-Hub-only startup uses MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS=20 by default" in helper
    assert "MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS" in helper
    assert "Start-MainComputerDevChainIfNeeded" in helper
    only_function = helper[
        helper.index("function Start-MainComputerDevHubOnly"):
        helper.index("function Show-MainComputerStatus")
    ]
    assert "Start-MainComputerDevChainIfNeeded" not in only_function
    assert '"main_computer.app_control"' not in only_function
    assert "Start-MainComputerGiteaIfMissing" not in only_function
    assert "Start-MainComputerLocalPlatform" not in only_function
    assert 'if /I "%~1"=="--no-dev-hub" goto mc_disable_dev_hub' in start_v2
    assert '-NoDevHub' in start_v2

def test_exp_fdb_dev_hub_waits_for_executor_prepared_fdb_before_starting() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    start_function = helper[
        helper.index("function Start-MainComputer("):
        helper.index('function Stop-MainComputer(', helper.index("function Start-MainComputer("))
        if 'function Stop-MainComputer(' in helper[helper.index("function Start-MainComputer("):]
        else len(helper)
    ]

    assert start_function.index("Start-MainComputerGiteaIfMissing") < start_function.index("Start-MainComputerDevHubFresh")
    assert start_function.index("Start-MainComputerLocalPlatform") < start_function.index("Start-MainComputerDevHubFresh")
    assert start_function.index('"main_computer.app_control"') < start_function.index("Start-MainComputerDevHubFresh")

    assert "function Test-MainComputerExpFdbHubPrerequisites" in helper
    assert "function Test-MainComputerFdbClusterReady" in helper
    assert "function Wait-MainComputerFdbClusterReady" in helper
    assert "Checking dev Hub prerequisite: FoundationDB cluster file" in helper
    assert "Wait-MainComputerFdbClusterReady $ClusterFile $TimeoutSeconds" in helper
    assert "function Wait-MainComputerDevChainRpc" in helper
    assert "Checking dev Hub prerequisite: dev-chain RPC" in helper
    assert '"--no-fdb-autostart"' in helper
    assert '"--cluster-file", [string]$clusterFile' in helper
    assert "resident executor service should start or reuse the default Docker/FDB container after Docker is ready" in helper
    assert "FoundationDB is not ready; run start.bat first or wait for the resident executor service to bootstrap Docker/FDB" in helper
    assert "The dev-chain RPC is not healthy yet; run start.bat first or wait for the resident blockchain service to prepare it" in helper



def test_dev_hub_start_bat_launches_dev_hub_only() -> None:
    dev_hub_start = (ROOT / "dev-hub-start.bat").read_text(encoding="utf-8")
    assert 'scripts\\main-computer-start-stop.ps1' in dev_hub_start
    assert '-Action dev-hub-start -Root' in dev_hub_start
    assert 'MAIN_COMPUTER_DEV_HUB_START_TIMEOUT_SECONDS=20' in dev_hub_start
    assert 'start_v2.bat' not in dev_hub_start
