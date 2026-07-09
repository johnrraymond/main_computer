from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_WINDOWS_ROOT = PureWindowsPath("C:/main-computer-fixtures")
FIXTURE_MANAGED_INSTALLS_ROOT = PureWindowsPath("C:/main-computer-fixtures/.main-computer-tools/installs")


def test_windows_bootstrapper_targets_native_windows_wsl_executor_shape() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Windows-first Main Computer bootstrapper" in script
    assert "Primary runtime: native Windows Python process." in script
    assert 'ValidateSet("auto", "disabled", "docker")' in script
    assert '$OnlyOfficeMode = "auto"' in script

    assert '$env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"' in script
    assert '$env:MAIN_COMPUTER_EXECUTOR_BACKEND = "wsl"' in script
    assert "$env:MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION = $Distribution" in script
    assert "$env:MAIN_COMPUTER_EXECUTOR_WSL_COMMAND = $WslPath" in script

    assert '"start"' in script
    assert 'Mode = "local"' in script
    assert "dev-control.ps1" in script
    assert "start-main-computer-docker-windows.ps1" not in script
    assert '"-Mode", "docker"' not in script


def test_windows_bootstrapper_composes_existing_wsl_and_onlyoffice_controls() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "scripts\\windows\\build-main-computer-runtime.ps1" in script
    assert "scripts\\windows\\install-main-computer-runtime.ps1" in script
    assert "ConvertTo-RuntimeImageFileName" in script
    assert 'Join-Path $WslRuntimeRoot "images"' in script
    assert "RuntimeImagePath = $runtimeImage" in script

    assert "tools\\onlyoffice\\onlyoffice-control.ps1" in script
    assert "ONLYOFFICE" in script
    assert "ONLYOFFICE auto mode will use Docker" in script
    assert "DefaultOnlyOfficePort" in script
    assert 'OnlyOfficeProjectName = "main-computer-onlyoffice"' in script


def test_windows_bootstrapper_verifies_app_executor_route_smoke() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "api/executor/status" in script
    assert "api/executor/run" in script
    assert "main-computer-bootstrap-wsl-ok" in script
    assert "Windows app -> WSL executor" in script


def test_dev_control_forwards_foreign_port_override_for_bootstrapper() -> None:
    script = (ROOT / "dev-control.ps1").read_text(encoding="utf-8")

    assert "[switch]$AllowForeignPortListener" in script
    assert '$legacyArgs += "-AllowForeignPortListener"' in script


def test_export_script_keeps_windows_bootstrapper_in_snapshots() -> None:
    export_script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

    assert '"bootstrap-main-computer-windows.ps1"' in export_script
    assert '"run-main-computer-test.ps1"' in export_script


def test_windows_bootstrapper_has_user_facing_install_modes_and_runner() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert '[ValidateSet("Unleashed", "Unleashed Mode", "Debug", "Safe", "Safe Mode")]' in script
    assert '$Mode = "Unleashed"' in script
    assert '[string]$InstallRoot = ""' in script
    assert '[string]$RunnerName = "run-main-computer.ps1"' in script
    assert "Resolve-MainComputerUserMode" in script
    assert "Unleashed Mode" in script
    assert "MainComputerExecutorProtoDev" in script
    assert "Safe Mode" in script
    assert "main-computer-install.json" in script
    assert "Write-ModeRunner" in script
    assert ('"debug_assets"' in script) or ("clean export script" in script)


def test_windows_bootstrapper_verifies_ollama_and_exposes_mode_env() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Test-OllamaAvailability" in script
    assert "OLLAMA_BASE_URL" in script
    assert "/api/tags" in script
    assert "MAIN_COMPUTER_INSTALL_MODE" in script
    assert "MAIN_COMPUTER_MODE_LABEL" in script
    assert "MAIN_COMPUTER_GUIDANCE_LEVEL" in script
    assert "MAIN_COMPUTER_SAFE_MODE" in script


def test_windows_bootstrapper_has_first_class_precheck_isolation_and_firewall_model() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "[switch]$PrecheckOnly" in script
    assert "[string]$InstanceName = \"\"" in script
    assert "[string]$InstanceStoreRoot = \"\"" in script
    assert '[ValidateSet("auto", "disabled", "required")]' in script
    assert "Invoke-MainComputerBootstrapPrecheck" in script
    assert "Bootstrap dependency, Git, Python, WSL, firewall, Ollama, and isolation precheck" in script
    assert "Test-GitPrecheck" in script
    assert "Git is not required for bootstrap install-root population" in script
    assert "Clean export script is present; bootstrap does not require git archive" in script
    assert "Test-BasePythonCapabilityPrecheck" in script
    assert "Main Computer requires Python >= 3.10" in script
    assert "venv module is importable" in script
    assert "Pinned pip wheel can be extracted into a --without-pip venv" in script
    assert "Resolve-MainComputerInstanceName" in script
    assert "Resolve-MainComputerInstanceStoreRoot" in script
    assert '".main-computer-$safeInstance"' in script
    assert "Get-MainComputerModeIsolationProfiles" in script
    assert 'PropertyName "VenvRoot" -DisplayName "venv roots"' in script
    assert 'PropertyName "ControlRoot" -DisplayName "control roots"' in script
    assert 'PropertyName "DefaultExecutorDistribution" -DisplayName "WSL distro names"' in script
    assert "Shared dependencies" in script
    assert "Ollama and host WSL are intentionally shared" in script
    assert "Instance store root:" in script
    assert "Precheck only: no install files, venvs, WSL distros, firewall rules, or runners were created." in script


def test_windows_bootstrapper_scopes_wsl_firewall_without_requiring_mirrored_networking() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Resolve-WslNetworkEndpoint" in script
    assert "ip -4 route show default" in script
    assert "hostname -I" in script
    assert "Ensure-WslScopedFirewallRule" in script
    assert "New-NetFirewallRule" in script
    assert "-LocalAddress $endpoint.HostGatewayIp" in script
    assert "-RemoteAddress $endpoint.GuestIp" in script
    assert "-LocalPort ([int]$ModeProfile.DefaultPort)" in script
    assert "-EdgeTraversalPolicy Block" in script
    assert "Test-BroadFirewallExposureSet" in script
    assert "Firewall broad exposure scan for Main Computer mode ports" in script
    assert "broad Python/port allow rules" in script
    assert "MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL" in script
    assert '"WslHostGatewayIp",' in script
    assert '"WslGuestIp",' in script
    assert 'Add-Member -NotePropertyName "WslHostGatewayIp" -NotePropertyValue $endpoint.HostGatewayIp -Force' in script
    assert 'Add-Member -NotePropertyName "WslGuestIp" -NotePropertyValue $endpoint.GuestIp -Force' in script


def test_windows_bootstrapper_namespaces_runner_modes_per_install() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "instance_name = $InstallInstanceName" in script
    assert "MAIN_COMPUTER_INSTANCE_NAME" in script
    assert "MAIN_COMPUTER_STATE_ROOT" in script
    assert "MAIN_COMPUTER_CONTROL_ROOT" in script
    assert "venv_root" in script
    assert "control_root" in script
    assert "state_root" in script
    assert "executor_root" in script
    assert "wsl_distro" in script
    assert "wsl_firewall_rule" in script
    assert "MainComputer-$InstallInstanceName-$DistributionSuffix" in script
    assert "StateRoot = $SelectedMode.StateRoot" in script
    assert "ControlRoot = $SelectedMode.ControlRoot" in script



def test_windows_bootstrapper_documents_fixed_three_lane_concurrency() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "fixed-three-lane" in script
    assert "max_concurrent_active_lanes = 3" in script
    assert "one_active_install_per_mode_lane = $true" in script
    assert "same_mode_installs_on_disk_allowed = $true" in script
    assert "run at most one active Unleashed, one Debug, and one Safe runner per machine" in script
    assert "one active install per mode lane" in script
    assert "Ports are fixed by mode lane rather than namespaced per install." in script


def test_windows_bootstrapper_uses_timeout_safe_netstat_for_lane_port_precheck() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Get-PortListenersFromNetstat" in script
    assert "Mode lane TCP listener scan via netstat" in script
    assert "netstat -ano -p tcp" in script
    assert "Invoke-PrecheckTimedJob" in script
    assert "TCP listener scan timed out" in script
    assert "skipping this advisory check so bootstrap can continue" in script
    assert "[AllowEmptyCollection()][AllowEmptyString()][string[]]$NetstatLines = @()" in script
    assert "foreach ($line in @($NetstatLines))" in script
    assert "Get-NetTCPConnection" not in script


def test_windows_bootstrapper_prechecks_firewall_sanity_for_all_mode_lanes() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Test-FirewallPrecheck" in script
    assert "Firewall cmdlets" in script
    assert "Firewall admin rights" in script
    assert "Firewall scoped rule: $($profile.Label)" in script
    assert "Test-BroadFirewallExposureSet -PortChecks $broadExposureChecks" in script
    assert "LocalPort = [int]$profile.DefaultPort" in script
    assert "LocalPort = [int]$profile.DefaultHeartbeatPort" in script
    assert "No obvious broad inbound allow rule was found" in script
    assert "prefer WSL-scoped allow rules" in script


def test_dev_control_accepts_isolated_control_root_from_unleashed_runner() -> None:
    script = (ROOT / "dev-control.ps1").read_text(encoding="utf-8")

    assert "[string]$ControlRoot = \"\"" in script
    assert "[int]$HeartbeatPort = 8766" in script
    assert "$localPidFile = Join-Path $ControlRoot \".main_computer_viewport.pid\"" in script
    assert '"-HeartbeatPort", ([string]$HeartbeatPort)' in script
    assert '"-ControlRoot", $ControlRoot' in script


def test_windows_bootstrapper_accepts_python_probe_output_even_when_launcher_exit_is_noisy() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Get-PythonVersionTupleFromText" in script
    assert "Test-PythonVersionAtLeast" in script
    assert "accepted from probe output even though the native launcher returned exit code" in script
    assert "import venv; print('venv import ok')" in script
    assert "venv import ok" in script


def test_windows_bootstrapper_scans_broad_firewall_exposure_once_for_all_lanes() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "$broadExposureChecks = @()" in script
    assert "Test-BroadFirewallExposureSet -PortChecks $broadExposureChecks" in script
    assert "Timed out after $PrecheckFirewallTimeoutSeconds seconds while inspecting broad inbound allow rules for all Main Computer mode ports" in script
    assert script.count("Invoke-PrecheckTimedJob -Name \"Firewall broad exposure scan for Main Computer mode ports\"") == 1


def test_windows_bootstrapper_uses_dotnet_process_runner_for_reliable_exit_codes() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "System.Diagnostics.ProcessStartInfo" in script
    assert "RedirectStandardOutput = $true" in script
    assert "ReadToEndAsync()" in script
    assert "No output was captured. Try running this command directly" in script
    assert "Start-Process" not in script


def test_windows_bootstrapper_recovers_incomplete_mode_venv() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Removing incomplete Windows virtual environment" in script
    assert "Remove-Item -LiteralPath $resolvedVenv -Recurse -Force" in script
    assert 'Invoke-NativeCheckedWithPreview -Label "Venv creation" -FilePath $BasePython -Arguments @("-m", "venv", "--without-pip", $resolvedVenv) -TimeoutSeconds 60' in script
    assert "Test-VenvPythonMatchesBasePython -VenvPython $venvPython -BasePython $BasePython -Root $Root" in script
    assert "Existing Windows virtual environment uses a different base Python; it will be rebuilt." in script
    assert "Ensure-VenvPip -PythonPath $resolvedVenvPython -VenvRoot $resolvedVenv -Root $Root" in script
    assert "if (-not $SkipDependencyInstall)" in script
    assert "Seeding pip in Windows virtual environment from pinned wheel." in script
    assert "Seed-VenvPipFromWheel -VenvPython $PythonPath -VenvRoot $VenvRoot -Root $Root" in script
    assert "Expand-ZipArchiveToDirectory -ZipPath $PipWheelPath -Destination $tempRoot" in script
    assert "venv python -m pip still failed" in script


def test_windows_bootstrapper_does_not_self_upgrade_seeded_pip() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")
    start = script.index("function Install-PythonDependencies")
    end = script.index("\nfunction Test-PythonImport", start)
    body = script[start:end]

    assert "Upgrading pip in Windows venv." not in body
    assert '"--upgrade", "pip"' not in body
    assert "Checking pip in Windows venv." in body
    assert "Skipping pip self-upgrade in Windows venv; seeded pip is already validated." in body
    assert "Installing Main Computer package without Mathics optional dependency." in body
    assert "Mathics is handled as a separate optional dependency so it cannot block the core bootstrap." in body
    assert '"--disable-pip-version-check"' in body
    assert '"--no-input"' in body
    assert '"--timeout", "30"' in body
    assert '"--retries", "2"' in body
    assert '"-e", "."' in body
    assert '"-e", ".[mathics]"' not in body
    assert "-TimeoutSeconds 300" in body


def test_windows_bootstrapper_escapes_awk_fields_in_wsl_endpoint_probe() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "awk '{print `$3; exit}'" in script
    assert "awk '`$2 == `\"00000000`\" { print `$3; exit }'" in script
    assert "awk '{print $3; exit}'" not in script
    assert "awk '{print $1; exit}'" not in script

def test_write_mode_runner_resolves_lane_profiles_before_manifest_uses_them() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")
    start = script.index("function Write-ModeRunner")
    end = script.index("\nfunction Get-DefaultExecutorDistribution", start)
    body = script[start:end]

    assert body.index("$unleashedProfile = Find-RunnerModeProfile -Key \"unleashed\"") < body.index("default_ports = [ordered]@")
    assert body.index("$debugProfile = Find-RunnerModeProfile -Key \"debug\"") < body.index("debug_app = $debugProfile.DefaultPort")
    assert body.index("$safeProfile = Find-RunnerModeProfile -Key \"safe\"") < body.index("safe_app = $safeProfile.DefaultPort")



def test_windows_bootstrapper_does_not_use_powershell_automatic_args_for_child_commands() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")
    start = script.index("function Start-WindowsApp")
    end = script.index("\nfunction Invoke-JsonRequest", start)
    body = script[start:end]

    assert "$args =" not in script
    assert "@args" not in script
    assert "$commandArgs = @(" not in body
    assert "@commandArgs" not in body
    assert "$protoParams = @{" in body
    assert "& $proto @protoParams" in body
    assert "$controlParams = @{" in body
    assert "& $control @controlParams" in body
    assert "$devControlParams = @{" in body
    assert "& $devControl @devControlParams" in body


def test_generated_runner_uses_hashtable_splatting_for_script_parameters() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")
    start = script.index("$runnerTemplate = @'")
    end = script.index("'@", start)
    runner = script[start:end]

    assert "$commandArgs = @(" not in runner
    assert "@commandArgs" not in runner
    assert "Action = $debugAction" in runner
    assert "& $proto @protoParams" in runner
    assert "Action = $controlAction" in runner
    assert "& $control @controlParams" in runner
    assert "Action = $controlAction" in runner
    assert "& $devControl @devControlParams" in runner


def test_proto_dev_restarts_stale_non_wsl_executor_backend() -> None:
    script = (ROOT / "proto-dev" / "proto-dev.ps1").read_text(encoding="utf-8")

    assert "Ensure-ProtoDevWslExecutorBackend" in script
    assert "app reported a non-WSL executor backend" in script
    assert 'Invoke-ProtoLocalControl -Paths $Paths -PythonPath $PythonPath -ControlAction "restart"' in script
    assert 'MAIN_COMPUTER_EXECUTOR_BACKEND=wsl' in script
    assert 'Debug viewport still is not using the WSL executor after restart' in script


def test_windows_bootstrapper_repairs_stale_wsl_executor_entrypoint_contract() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "ConvertTo-WslHostPath" in script
    assert "ConvertTo-ShellSingleQuotedLiteral" in script
    assert "main-computer-exec-contract-ok" in script
    assert "Entrypoint contract failed; refreshing /usr/local/bin/main-computer-exec" in script
    assert 'Join-Path $Root "docker\\executor\\main-computer-exec"' in script
    assert "cp $quotedSourceEntrypointWsl /usr/local/bin/main-computer-exec" in script
    assert "chmod 0755 /usr/local/bin/main-computer-exec" in script
    assert "Entrypoint contract still failed after refresh" in script



def test_windows_bootstrapper_uses_shared_onlyoffice_port_and_project() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert script.count("-DefaultOnlyOfficePort 18085") >= 3
    assert "-DefaultOnlyOfficePort 28085" not in script
    assert "-DefaultOnlyOfficePort 38085" not in script
    assert "ONLYOFFICE shared service" in script
    assert "ProjectName = $ModeProfile.OnlyOfficeProjectName" in script
    assert "http://host.docker.internal:$Port" in script
    assert "unleashed_onlyoffice = $unleashedProfile.DefaultOnlyOfficePort" in script
    assert "debug_onlyoffice = $debugProfile.DefaultOnlyOfficePort" in script
    assert "safe_onlyoffice = $safeProfile.DefaultOnlyOfficePort" in script
    assert '"DefaultHeartbeatPort",\n        "DefaultOnlyOfficePort",\n        "OnlyOfficeProjectName",\n        "CoolifyProjectName",' in script
    assert '"CoolifySoketiTerminalPort",\n        "GuidanceLevel",' in script


def test_installed_runner_has_fast_mode_environment_check_and_shared_gitea_policy() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    for source in (script, cli):
        assert '"check"' in source
        assert "Invoke-InstalledModeCheck" in source
        assert "Main Computer quick installed environment check" in source
        assert "Gitea shared service" in source
        assert "machine-wide Gitea" in source
        assert "MAIN_COMPUTER_GITEA_SCOPE" in source
        assert "MAIN_COMPUTER_GITEA_COMPOSE_PROJECT" in source
        assert "Ensure-SharedGiteaInstalledIfMissing" in source
        assert "installer/start path will not recreate it" in source
        assert "ps -a -q gitea" in source
        assert "ONLYOFFICE shared service" in source
        assert "Local Coolify for mode" in source
        assert "WSL distro for mode" in source
        assert "Ollama shared service" in source

    assert "__LOCAL_COOLIFY_ENABLED__" in script
    assert "SharedDependencies = @(\"Ollama\", \"Gitea\", \"ONLYOFFICE\", \"Windows host services\", \"WSL host feature\")" in script
    assert '"shared_dependencies": ["Ollama", "Gitea", "ONLYOFFICE", "Windows host services", "WSL host feature"]' in cli
    assert "check_command" in cli
    assert "$env:MC_CHECK" in cli


def test_dev_checkout_runner_mirrors_installed_runner_without_install_root() -> None:
    runner = (ROOT / "run-main-computer-test.ps1").read_text(encoding="utf-8")

    assert "Development checkout runner" in runner
    assert "Resolve-RunnerArguments" in runner
    assert "Resolve-RunnerMode" in runner
    assert "Invoke-DevModeCheck" in runner
    assert "Main Computer quick development environment check" in runner
    assert "run-main-computer-test.ps1 Debug" in runner
    assert '"start", "run", "restart", "status", "stop", "shutdown", "install", "install-run", "smoke", "check"' in runner
    assert "Gitea shared service" in runner
    assert "machine-wide Gitea" in runner
    assert "MAIN_COMPUTER_GITEA_SCOPE" in runner
    assert "main-computer-gitea" in runner
    assert "Ensure-SharedGiteaInstalledIfMissing" in runner
    assert "installer/start path will not recreate it" in runner
    assert "ps -a -q gitea" in runner
    assert "ONLYOFFICE shared service" in runner
    assert "Local Coolify for mode" in runner
    assert "WSL distro for mode" in runner
    assert 'OnlyOfficeProject = "main-computer-onlyoffice"' in runner
    assert '$localPlatformProject = "main-computer-local-platform-$modeSegment"' in runner
    assert "MainComputer-$instanceName-$DistributionSuffix" in runner
    assert "dev-control.ps1" in runner
    assert "proto-dev\\proto-dev.ps1" in runner
    assert "control-main-computer.ps1" in runner
    assert "main-computer-install.json" not in runner


def test_start_helper_installs_shared_gitea_only_when_missing_before_runner() -> None:
    script = (ROOT / "start.bat").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "scripts\\main-computer-start-stop.ps1" in script
    assert "Start-MainComputerGiteaIfMissing" in helper
    assert "Shared Gitea already present on port" in helper
    assert "installer/start path will not recreate it" in helper
    assert "ps -a -q gitea" in helper
    assert '"start", "gitea"' in helper
    assert '"up", "-d", "gitea"' in helper
    assert helper.index("$giteaStart = Start-MainComputerGiteaIfMissing") < helper.index("$localPlatformStart = Start-MainComputerLocalPlatform")


def test_onlyoffice_control_waits_for_docker_readiness_with_compose_project() -> None:
    control = (ROOT / "tools" / "onlyoffice" / "onlyoffice-control.ps1").read_text(encoding="utf-8")
    check = (ROOT / "tools" / "onlyoffice" / "check-onlyoffice.py").read_text(encoding="utf-8")

    assert "[string]$ProjectName = \"\"" in control
    assert "[int]$ReadyTimeoutSeconds = 300" in control
    assert 'Invoke-DockerComposeOnlyOffice @("up", "-d", "onlyoffice")' in control
    assert "--wait-seconds $ReadyTimeoutSeconds --poll-seconds $ReadyPollSeconds" in control
    assert "COMPOSE_PROJECT_NAME" in control

    assert "--wait-seconds" in check
    assert "--poll-seconds" in check
    assert "ONLYOFFICE is not ready yet; retrying" in check
    assert "checks[\"healthcheck\"][\"ok\"]" in check
    assert "checks[\"editor_api\"][\"ok\"]" in check


def test_proto_dev_preserves_bootstrap_onlyoffice_environment() -> None:
    script = (ROOT / "proto-dev" / "proto-dev.ps1").read_text(encoding="utf-8")

    assert "if (-not $env:MAIN_COMPUTER_ONLYOFFICE_ENABLED)" in script
    assert "if (-not $env:MAIN_COMPUTER_ONLYOFFICE_STORAGE_ROOT)" in script
    assert "onlyoffice_public_url = $env:MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL" in script
    assert "onlyoffice_callback_base_url = $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL" in script



def test_windows_bootstrapper_seeds_mode_scoped_local_server_publishing() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert '[string]$LocalServerMode = "auto"' in script
    assert "-DefaultLocalServerPortStart 18080" in script
    assert "-DefaultLocalServerPortStart 28080" in script
    assert "-DefaultLocalServerPortStart 38080" in script
    assert "-LocalServerGeneratedPortStart 28100" in script
    assert "-LocalServerGeneratedPortEnd 28199" in script
    assert "MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT" in script
    assert "MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH" in script
    assert "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH" in script
    assert "Initialize-LocalServerPublishingIfRequested" in script
    assert '"publish",' in script
    assert '"--dry-run"' in script
    assert '$localPlatformProjectName = "main-computer-local-platform-$modeSegment"' in script


def test_windows_bootstrapper_starts_install_scoped_local_coolify() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert '[string]$LocalCoolifyMode = "auto"' in script
    assert "CoolifyProjectName = $coolifyProjectName" in script
    assert 'CoolifyStateRoot = Join-Path $stateRoot "coolify-local-docker"' in script
    assert 'MAIN_COMPUTER_COOLIFY_LOCAL_URL' in script
    assert 'MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE' in script
    assert 'Start-LocalCoolifyIfRequested -Root $RepoRoot -PythonPath $venvPython -ModeProfile $selectedMode' in script
    assert '"--project-name", $ModeProfile.CoolifyProjectName' in script
    assert '"--state-dir", $ModeProfile.CoolifyStateRoot' in script
    assert '"--app-port", "$($ModeProfile.CoolifyPort)"' in script
    assert '"setup"' in script
    assert "setup-local-coolify.py" in script
    assert 'MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN = $tokenValue' in script
    assert 'Test-UniqueModeValue -ModeProfiles $ModeProfiles -PropertyName "CoolifyProjectName"' in script


def test_windows_bootstrapper_archives_replaceable_install_before_refresh_unless_auto_force() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert '[Alias("AutoForce", "auto-force")]' in script
    assert "[switch]$AutoForceInstall" in script
    assert "[Parameter(ValueFromRemainingArguments = $true)]" in script
    assert '"--auto-force"' in script
    assert "Unknown bootstrap argument" in script
    assert "Protect-ExistingInstallRoot" in script
    assert "Get-InstallRootArchiveRoot" in script
    assert "Test-InstallRootArchiveZip" in script
    assert "Test-InstallTreeSnapshot" in script
    assert "CreateFromDirectory" in script
    assert "OpenRead($ZipPath)" in script
    assert "archive stream read returned" in script
    assert "Move-Item -LiteralPath $destination -Destination $movedPath -Force" in script
    assert "Zip verified:" in script
    assert "Install roots are replaceable code trees" in script
    assert "Persistent runtime state was not reset." in script
    assert "-AutoForceInstall destroyed existing install root without preserving an archive." in script
    assert '"Install root archive"' in script
    assert "Install root active runners" in script
    assert "bootstrap does not use CIM/WMI" in script


def test_windows_bootstrapper_keeps_runtime_state_outside_replaceable_install_root() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "[string]$InstanceStoreRoot = \"\"" in script
    assert "Resolve-MainComputerInstanceStoreRoot" in script
    assert "[Environment]::GetFolderPath(\"UserProfile\")" in script
    assert "$userProfileRoot = [Environment]::GetFolderPath(\"UserProfile\")" in script
    assert "$home = [Environment]::GetFolderPath" not in script
    assert "$home = $env:USERPROFILE" not in script
    assert '".main-computer-$safeInstance"' in script
    assert "InstanceStoreRoot = $InstanceStoreRoot" in script
    assert "$instanceRoot = Join-Path $InstanceStoreRoot $InstallInstanceName" in script
    assert "instance_store_root = $ModeProfile.InstanceStoreRoot" in script
    assert "Mode state, control roots, venvs, executor roots, WSL distro names, and WSL-scoped firewall rules live under the external instance store" in script
    assert "Add-BootstrapStatus \"Instance store root\" \"OK\" $resolvedInstanceStoreRoot" in script


def test_windows_bootstrapper_uses_base_python_outside_activated_virtualenv() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Get-PythonExecutableVirtualEnvironmentRoot" in script
    assert "Resolve-BasePythonFromVirtualEnvironment" in script
    assert "pyvenv.cfg" in script
    assert 'Resolve-BasePythonFromVirtualEnvironment -VirtualEnvPython $resolved' in script
    assert "Python command points inside a virtual environment" in script
    assert "Pass -PythonCommand with a real CPython executable" in script
    assert "PATH aliases such as '$PythonCommand' are not supported" in script
    assert "active virtualenv Python are not supported" in script



def test_windows_bootstrapper_does_not_use_path_python_for_base_resolution() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "function Resolve-CommandPaths" in script
    assert "Get-Command $CommandName -CommandType Application -All" in script
    assert 'foreach ($pythonName in @("python.exe", "python"))' not in script
    assert "PATH aliases such as '$PythonCommand' are not supported" in script
    assert "Read-ManagedPythonCurrentPointer" in script
    assert "Get-WindowsPythonInstallCandidates" in script
    assert r"Registry::HKEY_CURRENT_USER\Software\Python\PythonCore" in script
    assert r"Programs\Python\Python*\python.exe" in script



def test_windows_bootstrapper_rejects_windowsapps_and_seeds_pip_from_wheel() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "Test-BasePythonCandidateLaunches" in script
    assert "Microsoft Store / WindowsApps Python is not supported" in script
    assert "[switch]$AllowWindowsAppsPython" in script
    assert "Test-PythonWheelPipPrecheck -BasePython $BasePython -SourceRoot $SourceRoot" in script
    assert 'Invoke-BasePythonPrecheck -BasePython $BasePython -Arguments @("-m", "venv", "--without-pip", $tempRoot)' in script
    assert "Seed-VenvPipFromWheel -VenvPython $tempPython -VenvRoot $tempRoot -Root $SourceRoot -PipWheelPath $wheelPath -ReturnResult" in script
    assert "Get-PipWheelUrl" in script
    assert "https://pypi.org/pypi/pip/$PipWheelVersion/json" in script
    assert "pip-$PipWheelVersion-py3-none-any.whl" in script
    assert 'Add-PrecheckStatus "Python pip seed" "OK" "Pinned pip wheel can be extracted into a --without-pip venv."' in script
    assert "ensurepip" not in script
    assert "get-pip.py" not in script
    assert '"-m", "pip", "install"' in script



def test_windows_bootstrapper_splits_mathics_from_core_package_install() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert '[ValidateSet("disabled", "auto", "required")]' in script
    assert '$MathicsInstallMode = "disabled"' in script
    assert "function Install-MathicsOptionalDependency" in script
    assert "Skipping Mathics optional dependency install by default." in script
    assert "Mathics3==10.0.0" in script
    assert '"--only-binary", ":all:"' in script
    assert "Mathics optional dependency is required but could not be installed without source builds" in script
    assert "Installed Main Computer package without Mathics optional dependency." in script


def test_windows_bootstrapper_prints_exact_python_commands_before_package_install() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "function Write-NativeCommandPreview" in script
    assert "function Invoke-NativeCheckedWithPreview" in script
    assert 'Write-PythonRuntimeIdentity -PythonPath $basePython -Label "Selected base Python path" -Root $RepoRoot' in script
    assert 'Write-PythonRuntimeIdentity -PythonPath $PythonPath -Label "Package install Python path" -Root $Root' in script
    assert 'Invoke-NativeCheckedWithPreview -Label "Main Computer package install"' in script
    assert 'Invoke-NativeCheckedWithPreview -Label "Pip check"' in script


def test_windows_bootstrapper_populates_install_root_from_clean_export_script_not_raw_repo_copy() -> None:
    script = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")

    assert "function Copy-CleanExportToInstallRoot" in script
    assert "export-main-computer-test.ps1" in script
    assert "Copy mode:    clean export script (not git, not a raw recursive repository copy)" in script
    assert "Invoke-NativeChecked `" in script
    assert '"-File",' in script
    assert "$exportScript," in script
    assert "[System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $expandRoot)" in script
    assert '$destinationParent = Split-Path -Parent $destination' in script
    assert '(".mcx-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))' in script
    assert '$archiveRoot = Join-Path $tempRoot "a"' in script
    assert '$expandRoot = Join-Path $tempRoot "x"' in script
    assert 'Write-Host "Export staging root: $tempRoot"' in script
    assert 'Add-BootstrapStatus "Install root export" "OK"' in script

    copy_region = script.split("function Copy-RepositoryToInstallRoot", 1)[1].split("function Test-OllamaAvailability", 1)[0]
    assert "Get-ChildItem -LiteralPath $source -Force -Recurse" not in copy_region
    assert "$excludedRootNames" not in copy_region


def test_python_windows_bootstrapper_is_boring_stage0_launcher() -> None:
    script = (ROOT / "bootstrap-main-computer-python-windows.ps1").read_text(encoding="utf-8")

    assert "Boring Windows stage-0 launcher" in script
    assert "tools\\bootstrap_main_computer.py" in script
    assert "https://www.nuget.org/api/v2/package/python/$PythonNuGetVersion" in script
    assert "Resolve-ManagedPython" in script
    assert "& $pythonExe @driverArgs" in script
    assert "exit $bootstrapExitCode" in script
    assert "--managed-python" in script
    assert "--pip-wheel-version" in script
    assert "[ValidateSet(\"docker\", \"podman\")]" in script
    assert "[string]$ContainerRuntime = \"docker\"" in script
    assert '"--container-runtime", $ContainerRuntime' in script
    assert "$env:MAIN_COMPUTER_CONTAINER_RUNTIME = $ContainerRuntime" in script
    assert "MAIN_COMPUTER_CONTAINER_COMMAND" in script
    assert "MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND" in script
    assert "MAIN_COMPUTER_DOCKER_COMMAND" in script
    assert 'Remove-Item -LiteralPath "Env:\\$containerOverrideName" -ErrorAction SilentlyContinue' in script
    assert script.index("$env:MAIN_COMPUTER_CONTAINER_RUNTIME = $ContainerRuntime") < script.index("& $pythonExe @driverArgs")

    forbidden = [
        "ensurepip",
        "get-pip.py",
        "Start-Process",
        "Get-CimInstance",
        "Win32_Process",
        '"-m", "pip", "install"',
        '"-m", "venv"',
        ".[mathics]",
    ]
    for value in forbidden:
        assert value not in script


def test_python_golden_path_installs_and_pins_native_podman_compose_provider() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert "podman-compose" in requirements
    assert "def podman_compose_provider_path" in cli
    assert "def apply_podman_compose_provider_env" in cli
    assert "PODMAN_COMPOSE_PROVIDER" in cli
    assert '"podman-compose.exe"' in cli
    assert 'env["PODMAN_COMPOSE_PROVIDER"] = str(podman_compose_provider_path(venv_python))' in cli
    assert '$env:PODMAN_COMPOSE_PROVIDER = Join-Path (Split-Path -Parent $SelectedMode.PythonPath) "podman-compose.exe"' in cli
    assert '"podman_compose_provider": str(podman_compose_provider_path(venv_python)) if args.container_runtime == "podman" else ""' in cli


def test_python_windows_bootstrapper_rehomes_from_clean_export_by_default() -> None:
    script = (ROOT / "bootstrap-main-computer-python-windows.ps1").read_text(encoding="utf-8")

    assert "[switch]$NoReHome" in script
    assert "Invoke-InstallerReHome" in script
    assert "& $exportScript -SourceRoot $sourceFull -InstallerReHome" in script
    assert "Push-Location -LiteralPath $resolvedRepoRoot" in script
    assert 'Write-Host "  cwd: $(Get-Location)"' in script
    assert 'Write-Host "Installer rehome disabled by -NoReHome; using requested repo root."' in script



def test_python_bootstrap_driver_owns_golden_path_install() -> None:
    driver = (ROOT / "tools" / "bootstrap_main_computer.py").read_text(encoding="utf-8")
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")
    process = (ROOT / "main_computer" / "bootstrap" / "process.py").read_text(encoding="utf-8")
    venv = (ROOT / "main_computer" / "bootstrap" / "venv.py").read_text(encoding="utf-8")
    mathics = (ROOT / "main_computer" / "bootstrap" / "mathics.py").read_text(encoding="utf-8")

    assert "sys.path.insert(0, str(REPO_ROOT))" in driver
    assert "Python-owned golden path installer" in cli
    assert "copy_clean_tree" in cli
    assert "create_venv_without_pip" in cli
    assert "seed_pip_from_wheel" in cli
    assert "pip_install_project" in cli
    assert "write_runner" in cli
    assert "main-computer-install.json" in cli

    assert "Running command:" in process
    assert "Working directory:" in process
    assert "Timeout seconds:" in process
    assert "subprocess.Popen" in process
    assert "stderr=subprocess.STDOUT" in process
    assert "threading.Thread" in process

    assert '"--without-pip"' in venv
    assert "zipfile.ZipFile" in venv
    assert '"pip"' in venv
    assert '"install"' in venv
    assert '"-e"' in venv
    assert '"."' in venv
    assert '".[mathics]"' not in venv

    assert 'MATHICS_PIN = "Mathics3==10.0.0"' in mathics
    assert '"--only-binary"' in mathics
    assert '":all:"' in mathics
    assert '"--no-index"' in mathics


def test_export_script_keeps_python_windows_bootstrapper_in_snapshots() -> None:
    export_script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

    assert '"bootstrap-main-computer-python-windows.ps1"' in export_script
    assert '"start_v2.bat"' in export_script
    assert '"stop_v2.bat"' in export_script


def test_python_bootstrap_defaults_to_managed_install_root_and_prints_status_command() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")
    install_root = (ROOT / "main_computer" / "bootstrap" / "install_root.py").read_text(encoding="utf-8")

    assert '".main-computer-tools" / "installs"' in install_root
    assert "default_install_root" in cli
    assert "resolve_install_root" in cli
    assert '"env:MC_INSTALL"' in cli
    assert "destructive_replace_install_root = args.auto_force_install" in cli
    assert "Install refresh: any existing install root will be archived and moved aside before copying." in cli
    assert "powershell_status_command" in cli
    assert '"status_command"' in cli
    assert '"start_command"' in cli
    assert '"stop_command"' in cli
    assert '"launcher_context"' in cli
    assert '"selected_start_script"' in cli
    assert '"env_header"' in cli
    assert '"compact_status_command"' in cli
    assert '"browser_url": browser_url(app_port)' in cli
    assert 'print(f"Browser: {browser_url(app_port)}"' in cli
    assert 'print(f"Status command: {powershell_status_command(runner_path)}"' in cli
    assert 'print(f"Shell header: {powershell_env_header_command(env_header_path)}"' in cli



def test_python_bootstrap_installs_from_clean_archive_and_handles_long_windows_paths() -> None:
    install_root = (ROOT / "main_computer" / "bootstrap" / "install_root.py").read_text(encoding="utf-8")

    assert "zipfile.ZipFile" in install_root
    assert "main-computer-install-export.zip" in install_root
    assert "Extracting install tree to:" in install_root
    assert "windows_long_path" in install_root
    assert "\\\\\\\\?\\\\UNC\\\\" in install_root
    assert "\\\\\\\\?\\\\" in install_root
    assert "extract_clean_tree_archive" in install_root
    assert "ensure_within_root" in install_root
    assert "iter_clean_tree_files" in install_root
    assert "protect_existing_install_root" in install_root
    assert "install_archive_root" in install_root
    assert '".main-computer-install-archives"' in install_root
    assert "verify_install_root_archive" in install_root
    assert "Archive verification failed; leaving existing install root in place" in install_root
    assert "Pass --auto-force-install to replace the code tree" not in install_root
    assert "os.walk(source_root)" in install_root
    assert "dirnames[:] = kept_dirnames" in install_root
    assert "source_root.rglob" not in install_root
    assert "shutil.copy2(path, target)" not in install_root


def test_install_archive_failure_twiddle_mirrors_python_archive_verify_without_move() -> None:
    twiddle = (ROOT / "tools" / "twiddle-install-root-archive-failure.py").read_text(encoding="utf-8")

    assert "Mirror the installer install-root archive step without moving the install root" in twiddle
    assert "write_install_root_archive" in twiddle
    assert 'zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED)' in twiddle
    assert "verify_install_root_archive" in twiddle
    assert "archive contains {len(entries)} file entries, expected {expected_file_count}" in twiddle
    assert "archive reports {entry_bytes} uncompressed bytes, expected {expected_total_bytes}" in twiddle
    assert "archive stream read returned {read_bytes} bytes, expected {expected_total_bytes}" in twiddle
    assert "Archive verification failed; leaving existing install root in place" in twiddle
    assert "Move step: skipped intentionally" in twiddle
    assert "twiddle-probes" in twiddle
    assert "shutil.move" not in twiddle

def test_python_bootstrap_prunes_nested_install_artifacts_and_refuses_child_install_root(tmp_path) -> None:
    from main_computer.bootstrap.install_root import copy_clean_tree, iter_clean_tree_files, repo_path_allowed

    source = tmp_path / "repo"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.1'\n", encoding="utf-8")

    archive_dir = source / ".main-computer-install-archives" / "debug1"
    archive_dir.mkdir(parents=True)
    (archive_dir / "old-debug.zip").write_bytes(b"old archive")

    tool_state = source / ".main-computer-tools" / "instances" / "debug1" / "debug" / "wsl"
    tool_state.mkdir(parents=True)
    (tool_state / "ext4.vhdx").write_bytes(b"large runtime state")

    stale_install = source / "main_computer_debug1"
    (stale_install / "runtime" / "start_stop").mkdir(parents=True)
    (stale_install / "main-computer-env.ps1").write_text("# generated env\n", encoding="utf-8")
    (stale_install / "runtime" / "start_stop" / "main-computer-launcher.json").write_text("{}", encoding="utf-8")
    (stale_install / "huge-copied-state.bin").write_bytes(b"not for export")

    noisy_dirs = [
        ".main-computer/onlyoffice",
        ".proto-dev/runtime",
        "aider_web_context/histories/20260424002814-6d6ed9e3",
        "chat_console_shared_variables",
        "debug_asset_revisions",
        "diagnostics_output_ollama_probe_run",
        "golden_path_diag_20260514_210530",
        "harness_output_buddhabrot_live",
        "main_computer_test.egg-info",
        "ollama_prompt_space_20260430-212555",
        "rag_smoke_logpack_runs",
        "spreadsheets",
        "tmp_diag_server_debug",
        "tools - Copy",
    ]
    for noisy_dir in noisy_dirs:
        path = source / noisy_dir
        path.mkdir(parents=True)
        (path / "copied-state.txt").write_text("not for export\n", encoding="utf-8")

    assert not repo_path_allowed(".main-computer-install-archives", is_dir=True)
    assert not repo_path_allowed(".main-computer-tools", is_dir=True)
    assert not repo_path_allowed(".proto-dev", is_dir=True)
    assert not repo_path_allowed("diagnostics_output_ollama_probe_run", is_dir=True)
    assert not repo_path_allowed("chat_console_shared_variables", is_dir=True)
    assert not repo_path_allowed("harness_output_buddhabrot_live", is_dir=True)
    assert not repo_path_allowed("main_computer_test.egg-info", is_dir=True)
    assert not repo_path_allowed("spreadsheets", is_dir=True)

    exported = {relative for _path, relative in iter_clean_tree_files(source)}
    assert "pyproject.toml" in exported
    assert ".main-computer-install-archives/debug1/old-debug.zip" not in exported
    assert ".main-computer-tools/instances/debug1/debug/wsl/ext4.vhdx" not in exported
    assert "main_computer_debug1/huge-copied-state.bin" not in exported
    for noisy_dir in noisy_dirs:
        assert f"{noisy_dir}/copied-state.txt" not in exported

    with pytest.raises(RuntimeError, match="Install root cannot be inside RepoRoot"):
        copy_clean_tree(source, source / "main_computer_debug2")


def test_python_bootstrap_writes_dot_source_env_header_for_compact_status_commands() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert "main-computer-env.ps1" in cli
    assert "write_env_header" in cli
    assert "$env:MC_RUN" in cli
    assert "$env:MAIN_COMPUTER_RUNNER" in cli
    assert "powershell_env_header_command" in cli
    assert "compact_status_command" in cli
    assert 'return "& $env:MC_RUN -Action status"' in cli
    assert 'return "& $env:MC_RUN -Action start"' in cli
    assert '"env_header_command"' in cli
    assert "start_installed_app" in cli
    assert "select_batch_script" in cli
    assert 'start_bat = select_batch_script(install_root, "start")' in cli
    assert '"cmd.exe"' in cli
    assert 'f\'call "{start_bat}"\'' in cli
    assert "Installed Main Computer launcher command completed." in cli
    assert "start_bat_command" in cli
    assert "stop_bat_command" in cli
    assert '"start_command": start_bat_command(install_root)' in cli
    assert '"stop_command": stop_bat_command(install_root)' in cli
    assert "Point your browser at after starting:" in cli
    assert 'print(f"After header status: {compact_status_command()}"' in cli


def test_python_windows_bootstrapper_prints_env_target_before_handoff() -> None:
    script = (ROOT / "bootstrap-main-computer-python-windows.ps1").read_text(encoding="utf-8")

    assert '$env:MC_INSTALL' in script
    assert '$env:MAIN_COMPUTER_INSTALL_ROOT' in script
    assert '$installTargetSource = "managed default selected by Python"' in script
    assert '$installTargetSource = "env:MC_INSTALL"' in script
    assert 'Write-Host "  install target: $installTargetDisplay"' in script
    assert 'Write-Host "  target source: $installTargetSource"' in script


def test_python_bootstrap_manifest_records_install_target_source() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert '"install_root_source": install_root_source' in cli
    assert '"managed_default_root": str(managed_default_root)' in cli
    assert 'print(f"Target source:   {install_root_source}"' in cli
    assert 'print(f"Target source: {install_root_source}"' in cli


def test_python_target_install_launcher_sets_mc_install_and_calls_python_installer() -> None:
    script = (ROOT / "install-main-computer-python-target.ps1").read_text(encoding="utf-8")

    assert "Thin target launcher for the Python-owned Windows installer" in script
    assert "[string]$Slot = \"debug1\"" in script
    assert "$env:MC_INSTALL = $targetInstallRoot" in script
    assert "$env:MC_ENV = Join-Path $env:MC_INSTALL \"main-computer-env.ps1\"" in script
    assert "$env:MC_RUN = Join-Path $env:MC_INSTALL $RunnerName" in script
    assert "$env:MAIN_COMPUTER_INSTALL_ROOT = $env:MC_INSTALL" in script
    assert '$targetSource = "slot:$Slot"' in script
    assert '$targetSource = "-InstallRoot"' in script
    assert "bootstrap-main-computer-python-windows.ps1" in script
    assert "$installerParams = @{" in script
    assert "InstallRoot = $targetInstallRoot" in script
    assert "ContainerRuntime = $ContainerRuntime" in script
    assert "[ValidateSet(\"docker\", \"podman\")]" in script
    assert "[switch]$NoReHome" in script
    assert "$installerParams.NoReHome = $true" in script
    assert "& $bootstrapScript @installerParams" in script
    assert "& $bootstrapScript @installerArgs" not in script
    assert "$installerArgs = @(" not in script


def test_python_target_install_launcher_has_help_reminder_command_lines() -> None:
    script = (ROOT / "install-main-computer-python-target.ps1").read_text(encoding="utf-8")

    assert "[switch]$HelpRun" in script
    assert "Show-RunHelp" in script
    assert ".\\$ScriptName -Slot debug1 -Mode Debug" in script
    assert ".\\$ScriptName -Slot debug2 -Mode Debug" in script
    assert ".\\$ScriptName -Slot debug1 -Mode Debug -PrecheckOnly" in script
    assert "[Alias(\"Target\", \"TargetRoot\", \"TargetInstallRoot\")]" in script
    assert "-Target, -TargetRoot, -TargetInstallRoot" in script
    assert "D:\\mc-targets\\debug1" in script
    assert "$env:USERPROFILE\\dsl\\main_computer_debug12" in script
    assert ". $`env:MC_ENV" not in script
    assert "& `$env:MC_RUN -Action status" in script
    assert ".\\$scriptName -HelpRun" in script


def test_python_bootstrap_treats_managed_install_slots_as_replaceable_code_trees() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")
    install_root = (ROOT / "main_computer" / "bootstrap" / "install_root.py").read_text(encoding="utf-8")

    assert "is_managed_install_root" in cli
    assert "destructive_replace_install_root = args.auto_force_install" in cli
    assert "def managed_installs_root" in install_root
    assert "def is_managed_install_root" in install_root
    assert '".main-computer-tools" / "installs"' in install_root
    assert "Named slots such as debug1/debug2 live here" in install_root


def test_export_script_keeps_python_target_launcher_in_snapshots() -> None:
    export_script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

    assert '"install-main-computer-python-target.ps1"' in export_script


def test_export_script_blocks_local_generated_bootstrap_leaks() -> None:
    export_script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

    assert '"chat_console_shared_variables"' in export_script
    assert '"spreadsheets"' in export_script
    assert '".egg-info"' in export_script
    assert "blockedDirectoryNameSuffixes" in export_script
    assert "badDirectoryNameSuffixes" in export_script


def test_python_stage0_accepts_regular_bootstrap_compatibility_knobs() -> None:
    script = (ROOT / "bootstrap-main-computer-python-windows.ps1").read_text(encoding="utf-8")

    for value in [
        "[string]$VenvPath",
        "[string]$PythonCommand",
        "[string]$WslCommand = \"wsl.exe\"",
        "[string]$ExecutorDistribution",
        "[int]$Port = 8765",
        "[int]$HeartbeatPort = 0",
        "[int]$SafePort = 38865",
        "[int]$SafeHeartbeatPort = 38866",
        "[string]$BindHost = \"0.0.0.0\"",
        "[string]$Workspace = \"\"",
        "[int]$StartTimeoutSeconds = 90",
        "[ValidateSet(\"auto\", \"disabled\", \"docker\")]",
        "[string]$OnlyOfficeMode = \"auto\"",
        "[ValidateSet(\"docker\", \"podman\")]",
        "[string]$ContainerRuntime = \"docker\"",
        "[string]$LocalServerMode = \"auto\"",
        "[string]$LocalCoolifyMode = \"auto\"",
        "[switch]$InstallOnlyOffice",
        "[switch]$SkipWslRuntimeInstall",
        "[switch]$BuildWslRuntimeIfMissing",
        "[switch]$ResetWslRuntime",
        "[switch]$SkipExecutorSmoke",
        "[switch]$SkipInstallRootCopy",
        "[switch]$SkipRunnerCreation",
        "[switch]$SkipAppStart",
        "[switch]$SkipMathicsCheck",
        "[switch]$AllowForeignPortListener",
        "[switch]$NoReHome",
    ]:
        assert value in script

    for value in [
        '"--venv-path"',
        '"--wsl-command"',
        '"--executor-distribution"',
        '"--port"',
        '"--heartbeat-port"',
        '"--safe-port"',
        '"--safe-heartbeat-port"',
        '"--bind-host"',
        '"--workspace"',
        '"--start-timeout-seconds"',
        '"--onlyoffice-mode"',
        '"--container-runtime"',
        '"--local-server-mode"',
        '"--local-coolify-mode"',
        '"--install-onlyoffice"',
        '"--skip-wsl-runtime-install"',
        '"--build-wsl-runtime-if-missing"',
        '"--reset-wsl-runtime"',
        '"--skip-executor-smoke"',
        '"--skip-install-root-copy"',
        '"--skip-runner-creation"',
        '"--skip-app-start"',
        '"--skip-mathics-check"',
        '"--allow-foreign-port-listener"',
    ]:
        assert value in script


def test_python_driver_duplicates_regular_runner_mode_action_surface() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert "duplicates the regular bootstrap-main-computer-windows.ps1 runner shape" in cli
    assert "build_mode_profiles" in cli
    assert '"debug": {' in cli
    assert '"port": 28865' in cli
    assert 'ValidateSet("start", "run", "restart", "status", "stop", "shutdown", "install", "install-run", "smoke", "check")' in cli
    assert 'ValidateSet("", "Unleashed", "Unleashed Mode", "Debug", "Safe", "Safe Mode")' in cli
    assert "Resolve-RunnerMode" in cli
    assert "Set-RunnerEnvironment" in cli
    assert "MAIN_COMPUTER_GUIDANCE_LEVEL" in cli
    assert "MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION" in cli
    assert "MAIN_COMPUTER_COOLIFY_STATE_DIR" in cli
    assert "MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE" in cli
    assert "Test-WslDistributionInstalled" in cli
    assert '"install-run"' in cli
    assert "BuildWslRuntimeIfMissing" in cli
    assert "Invoke-UnleashedMode" in cli
    assert "Invoke-DebugMode" in cli
    assert "Invoke-SafeMode" in cli
    assert "dev-control.ps1" in cli
    assert "proto-dev\\\\proto-dev.ps1" in cli
    assert "control-main-computer.ps1" in cli
    assert '"runtime" / "main-computer-install.json"' in cli
    assert '"modes": _serializable_profiles(mode_profiles)' in cli


def test_python_target_launcher_passes_regular_knobs_to_python_installer() -> None:
    script = (ROOT / "install-main-computer-python-target.ps1").read_text(encoding="utf-8")

    for value in [
        "[string]$VenvPath",
        "[string]$PythonCommand",
        "[string]$ManagedPythonRoot",
        "[string]$PythonDownloadRoot",
        "[string]$WslCommand = \"wsl.exe\"",
        "[string]$ExecutorDistribution",
        "[int]$Port = 8765",
        "[int]$HeartbeatPort = 0",
        "[string]$BindHost = \"0.0.0.0\"",
        "[string]$Workspace = \"\"",
        "[ValidateSet(\"auto\", \"disabled\", \"docker\")]",
        "[string]$OnlyOfficeMode = \"auto\"",
        "[switch]$InstallOnlyOffice",
        "[switch]$BuildWslRuntimeIfMissing",
        "[switch]$SkipWslRuntimeInstall",
        "[switch]$SkipExecutorSmoke",
        "[switch]$SkipRunnerCreation",
        "[switch]$SkipMathicsCheck",
        "[switch]$AllowForeignPortListener",
    ]:
        assert value in script

    for value in [
        "WslCommand = $WslCommand",
        "SafePort = $SafePort",
        "SafeHeartbeatPort = $SafeHeartbeatPort",
        "BindHost = $BindHost",
        "StartTimeoutSeconds = $StartTimeoutSeconds",
        "OnlyOfficeMode = $OnlyOfficeMode",
        "LocalServerMode = $LocalServerMode",
        "LocalCoolifyMode = $LocalCoolifyMode",
        "$installerParams.InstallOnlyOffice = $true",
        "$installerParams.BuildWslRuntimeIfMissing = $true",
        "$installerParams.SkipWslRuntimeInstall = $true",
        "$installerParams.SkipExecutorSmoke = $true",
        "PythonNuGetVersion = $PythonNuGetVersion",
        "PipWheelVersion = $PipWheelVersion",
        "$installerParams.VenvPath = Resolve-FullPath $VenvPath",
        "$installerParams.ExecutorDistribution = $ExecutorDistribution",
        "$installerParams.SkipRunnerCreation = $true",
    ]:
        assert value in script


def test_python_installer_warns_instead_of_stopping_existing_target_app_ports() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert "warn_existing_app_listeners" in cli
    assert "active_control_ports" in cli
    assert "the installer will not stop them" in cli
    assert "Use the tree-local stop_v2.bat/stop.bat when you are ready." in cli
    assert "warn_existing_app_listeners(" in cli
    assert "request_existing_app_shutdown(" in cli
    main_body = cli[cli.index("def main") :]
    assert "warn_existing_app_listeners(" in main_body
    assert "request_existing_app_shutdown(" not in main_body


def test_python_generated_runner_requests_shutdown_before_starting_on_target_ports() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert "Request-ExistingAppShutdown" in cli
    assert "Test-LocalTcpPortOpen" in cli
    assert "Invoke-JsonPost" in cli
    assert "http://127.0.0.1:$heartbeatPort/api/heartbeat/control" in cli
    assert "http://127.0.0.1:$appPort/system/hard-halt" in cli
    assert '@("start", "run", "restart", "install", "install-run") -contains $Action' in cli
    assert "Existing app did not release the target ports after shutdown request." in cli


def test_heartbeat_shutdown_action_stops_viewport_and_heartbeat_server() -> None:
    heartbeat = (ROOT / "main_computer" / "heartbeat.py").read_text(encoding="utf-8")

    assert "def request_shutdown" in heartbeat
    assert "main-computer-heartbeat-shutdown" in heartbeat
    assert 'payload["heartbeat_shutdown_requested"] = True' in heartbeat
    assert "Viewport and heartbeat shutdown requested through heartbeat." in heartbeat
    assert 'self.server.request_shutdown(source="heartbeat-control-shutdown")' in heartbeat



def test_python_bootstrap_starts_install_scoped_services_and_prints_urls() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert "initialize_local_server_publishing_if_requested" in cli
    assert "start_local_coolify_if_requested" in cli
    assert "start_onlyoffice_if_requested" in cli
    assert "setup-local-coolify.py" in cli
    assert "website-docker.py" in cli
    assert "onlyoffice-control.ps1" in cli
    assert "Local Server built-ins:" in cli
    assert "Local Coolify startup deferred to the tree launcher." in cli
    assert "ONLYOFFICE startup deferred to the tree launcher." in cli
    assert "setup-local-coolify.log" in cli
    assert '"setup"' in cli
    assert "read_local_coolify_token_file" in cli
    assert "Local Coolify remote-prod publish rehearsal passed." in cli
    assert '"effective_onlyoffice_mode": _effective_onlyoffice_mode(args.onlyoffice_mode)' in cli
    assert '"coolify_state_root": state_root / "coolify-local-docker"' in cli


def test_python_bootstrapper_writes_mode_scoped_template_ports_and_shared_onlyoffice() -> None:
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert '"instance_name": instance_name' in cli
    assert '"install_tree_id": safe_name(install_root.name).replace("_", "-")' in cli
    assert '"dev_compose_project": _mode_scoped_dev_compose_project(instance_name, key)' in cli
    assert '"local_server_project": _mode_scoped_local_platform_project(instance_name, key)' in cli
    assert '"onlyoffice_project": "main-computer-onlyoffice"' in cli
    assert '"onlyoffice_project": f"main-computer-onlyoffice-{key}"' not in cli
    assert 'shared_onlyoffice_port = args.onlyoffice_port if args.onlyoffice_port is not None else 18085' in cli
    assert 'env["MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME"] = "main-computer-onlyoffice-documentserver"' in cli
    assert '"docker_viewport_port": defaults["docker_viewport_port"]' in cli
    assert '"hub_port": defaults["hub_port"]' in cli
    assert '"ethereum_rpc_port": defaults["ethereum_rpc_port"]' in cli
    assert 'env["MAIN_COMPUTER_INSTANCE_NAME"] = instance_name' in cli
    assert 'env["MAIN_COMPUTER_DEV_COMPOSE_PROJECT"] = dev_compose_project' in cli
    assert 'env["MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT"] = dev_compose_project' in cli
    assert 'env["MAIN_COMPUTER_BLOCKCHAIN_COMPOSE_PROJECT"]' not in cli
    assert 'env["MAIN_COMPUTER_HUB_PORT"] = str(profile.get("hub_port", mode_defaults["hub_port"]))' in cli
    assert 'env["MAIN_COMPUTER_ETHEREUM_RPC_PORT"]' not in cli
    assert 'env["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"' in cli
    assert ("ollama" + "_host_port") not in cli
    assert ("MAIN_COMPUTER_" + "OLLAMA_HOST_PORT") not in cli
    assert ("Ollama" + "HostPort") not in cli
    assert ("1" + "8034") not in cli
    assert ("2" + "8034") not in cli
    assert ("3" + "8034") not in cli

    assert 'base_env={}' in cli
    assert 'env.pop("MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN", None)' in cli


def test_python_bootstrap_uses_mode_scoped_docker_projects_without_checkout_identity() -> None:
    from main_computer.bootstrap.cli import (
        _mode_scoped_coolify_project,
        _mode_scoped_dev_compose_project,
        _mode_scoped_local_platform_project,
    )

    for polluted_instance in [
        "main-computer-test",
        "main-computer-test-debug",
        "main-computer-test-test-debug",
        "main-computer-debug20",
        "x" * 100,
    ]:
        assert _mode_scoped_dev_compose_project(polluted_instance, "debug") == "main-computer-debug"
        assert (
            _mode_scoped_local_platform_project(polluted_instance, "debug")
            == "main-computer-local-platform-debug"
        )
        assert _mode_scoped_coolify_project(polluted_instance, "debug") == "main-computer-coolify-debug"

    assert _mode_scoped_dev_compose_project("anything", "unleashed") == "main-computer-unleashed"
    assert (
        _mode_scoped_local_platform_project("anything", "unleashed")
        == "main-computer-local-platform-unleashed"
    )


def test_python_bootstrap_default_identity_does_not_leak_checkout_profile_or_mode_labels() -> None:
    from main_computer.bootstrap.cli import default_instance_name

    assert (
        default_instance_name(
            "test",
            "debug",
            FIXTURE_MANAGED_INSTALLS_ROOT / "main_computer_test-test-debug",
        )
        == "main-computer"
    )
    assert (
        default_instance_name(
            "prod",
            "safe",
            FIXTURE_MANAGED_INSTALLS_ROOT / "main-computer-safe",
        )
        == "main-computer"
    )
    assert (
        default_instance_name(
            "test",
            "debug",
            FIXTURE_WINDOWS_ROOT / "main_computer_debug17",
        )
        == "main-computer-debug17"
    )
