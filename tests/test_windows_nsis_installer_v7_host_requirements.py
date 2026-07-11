from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/windows/build-main-computer-nsis-installer.experimental-v7.ps1"


def read_builder() -> str:
    return BUILDER.read_text(encoding="utf-8")


def test_v7_builder_is_additive_and_uses_separate_output_tree() -> None:
    text = read_builder()

    assert BUILDER.is_file()
    assert "build-main-computer-nsis-installer.experimental-v7.ps1" in text
    assert "installer-nsis-experimental-v7" in text
    assert "mc-nsis-v7" in text
    assert "MainComputer.experimental-v7.generated.nsi" in text
    assert "Install-MainComputer-from-Package.nsis-experimental-v7.ps1" in text


def test_v7_keeps_mode_shortcut_open_browser_contract() -> None:
    text = read_builder()

    assert "Page custom ModePage ModePageLeave" in text
    assert "Page custom ContainerRuntimePage ContainerRuntimePageLeave" in text
    assert '${NSD_CreateRadioButton} 0 34u 100% 12u "Main Computer - Unleashed"' in text
    assert '${NSD_CreateRadioButton} 0 52u 100% 12u "Main Computer - Debug"' in text
    assert '${NSD_CreateRadioButton} 0 70u 100% 12u "Main Computer - Safe"' in text
    assert "Choose the Main Computer install mode." in text
    assert "Choose the Docker-compatible container runtime Main Computer should use." in text
    assert "This installer will not auto-detect here. Pick Docker Desktop or Podman before files are installed." in text
    assert '${NSD_CreateRadioButton} 0 54u 100% 12u "Docker Desktop"' in text
    assert '${NSD_CreateRadioButton} 0 76u 100% 12u "Podman"' in text
    assert "Auto-detect on this computer" not in text
    assert 'StrCpy $ResolvedInstallRoot "$PROFILE\\.main-computer-tools\\installs\\main_computer_test-test-$InstallModeKey"' in text
    assert 'CreateShortCut "$SMPROGRAMS\\Main Computer\\Main Computer - $ShortcutModeName.lnk"' in text
    assert 'CreateShortCut "$DESKTOP\\Main Computer - $ShortcutModeName.lnk"' in text
    assert '"$ResolvedInstallRoot\\start_v2.bat" "-OpenBrowser"' in text


def test_v7_hard_gates_selected_container_runtime_with_runtime_specific_guidance() -> None:
    text = read_builder()

    assert "Assert-ContainerRuntimeRequirement" in text
    assert "Fail-ContainerRuntimeRequirement" in text
    assert "Resolve-InstallerContainerRuntime" in text
    assert "Page custom ModePage ModePageLeave" in text
    assert "Page custom ContainerRuntimePage ContainerRuntimePageLeave" in text
    assert "Function ContainerRuntimePage" in text
    assert "Function ContainerRuntimePageLeave" in text
    assert "Choose Docker Desktop or Podman before continuing." in text
    assert "Auto-detect on this computer" not in text
    assert 'StrCpy $ContainerRuntimeArg ""' in text
    assert 'StrCpy $ContainerRuntimeName ""' in text
    assert 'StrCpy $ContainerRuntimeArg "podman"' in text
    assert 'StrCpy $ContainerRuntimeName "Docker Desktop"' in text
    assert "-ContainerRuntime \"$ContainerRuntimeArg\"" in text
    assert "The setup maker's" in text
    assert "https://docs.docker.com/desktop/setup/install/windows-install/" in text
    assert "https://podman.io/docs/installation" in text
    assert '@("version")' in text
    assert '@("compose", "version")' in text
    assert '@("ps")' in text
    assert 'Resolve-ApplicationCommand -Name "podman"' in text
    assert 'Resolve-ApplicationCommand -Name "podman-compose"' in text
    assert '$env:LOCALAPPDATA\\Programs\\Podman\\podman.exe' in text
    assert 'Add-ProcessPathIfPresent -Directory "$env:LOCALAPPDATA\\Programs\\Podman"' in text
    assert "podman compose version   # or: podman-compose version" in text
    assert "podmanCompose = \"installed into the Main Computer Python venv from requirements.txt" in text
    assert "PODMAN_COMPOSE_PROVIDER" in text
    assert "A Docker-compatible container runtime is required for this version of Main Computer." in text
    assert "The NSIS page requires an explicit Docker Desktop or Podman choice." in text
    assert 'StrCpy $ContainerRuntimeArg "docker"' in text
    assert "No selected Docker-compatible container runtime is ready." in text
    assert "auto-detected Docker-compatible" not in text



def test_v7_collects_mode_and_runtime_before_instfiles_on_separate_required_page() -> None:
    text = read_builder()

    mode_page_order = text.index("Page custom ModePage ModePageLeave")
    runtime_page_order = text.index("Page custom ContainerRuntimePage ContainerRuntimePageLeave")
    instfiles_order = text.index("Page instfiles")
    section_order = text.index('Section "Install Main Computer package files"')

    assert mode_page_order < runtime_page_order < instfiles_order < section_order
    assert text.index("Function ContainerRuntimePage") < text.index("Function ContainerRuntimePageLeave")
    assert text.index('"Choose the Docker-compatible container runtime Main Computer should use."') < text.index("Function ContainerRuntimePageLeave")
    assert text.index('${NSD_GetState} $ContainerRuntimeRadioDocker $0') < text.index('${NSD_GetState} $ContainerRuntimeRadioPodman $0')
    assert text.index('MessageBox MB_ICONEXCLAMATION "Choose Docker Desktop or Podman before continuing."') < text.index('Section "Install Main Computer package files"')

def test_v7_installs_or_repairs_owned_host_tools() -> None:
    text = read_builder()

    assert "Install-WingetPackage" in text
    assert 'PackageId "Git.Git"' in text
    assert 'PackageId "Ollama.Ollama"' in text
    assert 'PackageId $packageId -DisplayName "Podman"' in text
    assert '@("Podman.CLI", "RedHat.Podman")' in text
    assert "Ensure-PodmanRequirement" in text
    assert '@("machine", "init")' in text
    assert '@("machine", "start")' in text
    assert "Ensure-GitRequirement" in text
    assert "Ensure-OpenSSHClientRequirement" in text
    assert "Ensure-OllamaRequirement" in text
    assert "OpenSSH.Client~~~~0.0.1.0" in text
    assert 'Resolve-ApplicationCommand -Name "ssh"' in text
    assert 'Resolve-ApplicationCommand -Name "scp"' in text
    assert 'Resolve-ApplicationCommand -Name "ssh-keygen"' in text
    assert "http://127.0.0.1:11434/api/tags" in text


def test_v7_logs_wsl_status_without_making_wsl_the_docker_escape_hatch() -> None:
    text = read_builder()

    assert "Check-WslRequirement" in text
    assert 'Resolve-ApplicationCommand -Name "wsl"' in text
    assert '@("--status")' in text
    assert '@("--list", "--verbose")' in text
    assert "WARNING: wsl.exe was not found" in text
    assert "The container runtime may still be usable" in text


def test_v7_nsis_calls_wrapper_without_skipping_requirements() -> None:
    text = read_builder()

    nsis_call = (
        'nsExec::ExecToLog '
        '\'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\\'
        'Install-MainComputer-from-Package.nsis-experimental-v7.ps1" '
        '-RuntimeProfile test -Mode "$InstallModeArg" '
        '-ContainerRuntime "$ContainerRuntimeArg" '
        '-InstallRoot "$ResolvedInstallRoot" -VerboseBootstrap\''
    )

    assert nsis_call in text
    assert "-SkipDockerRequirement" in text
    assert "-SkipHostRequirementInstall" in text
    assert "-SkipDockerRequirement" not in nsis_call
    assert "-SkipHostRequirementInstall" not in nsis_call
    assert "-ContainerRuntime" in nsis_call


def test_v7_wrapper_fences_stale_container_runtime_environment() -> None:
    text = read_builder()

    assert "Fencing container runtime environment for Python bootstrap child" in text
    assert "$env:MAIN_COMPUTER_CONTAINER_RUNTIME = $EffectiveContainerRuntime" in text
    assert "MAIN_COMPUTER_CONTAINER_COMMAND" in text
    assert "MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND" in text
    assert "MAIN_COMPUTER_DOCKER_COMMAND" in text
    assert "Ignoring inherited $containerOverrideName because installer selected container runtime" in text
    assert '[Environment]::SetEnvironmentVariable($containerOverrideName, $null, "Process")' in text
    assert text.index("$env:MAIN_COMPUTER_CONTAINER_RUNTIME = $EffectiveContainerRuntime") < text.index("$process = Start-Process")


def test_v7_package_manifest_documents_requirement_policy() -> None:
    text = read_builder()

    assert "hostRequirementsPolicy" in text
    assert 'containerRuntime = "required; installer shows a separate required Docker Desktop or Podman choice page; setup-maker environment variables are ignored' in text
    assert 'git = "install or repair with winget when missing"' in text
    assert 'opensshClient = "install or repair Windows OpenSSH Client capability' in text
    assert 'python = "bootstrap installs requirements.txt before editable project install and then runs pip check"' in text


def test_v7_deletes_stale_setup_exe_before_compile() -> None:
    text = read_builder()

    assert '$expectedExe = Join-Path $outputRootFull "MainComputer-$Version-Setup.exe"' in text
    assert 'Removing stale setup EXE before compile' in text
    assert 'Remove-Item -LiteralPath $expectedExe -Force' in text
    assert text.index('Remove-Item -LiteralPath $expectedExe -Force') < text.index('& $compiler.Path @nsisArgs')


def test_v7_does_not_report_archive_failures_as_runtime_failures() -> None:
    text = read_builder()

    assert 'Archive verification failed' in text
    assert 'installer-specific exit code 61' in text
    assert 'StrCmp $0 "61" bootstrap_archive_failed' in text
    assert 'This is not a Docker/Podman runtime failure.' in text
    assert 'StrCmp $0 "43" bootstrap_runtime_failed' in text
    assert 'This was not classified as a container runtime failure.' in text

def test_v7_verifies_critical_payload_staging_integrity_before_compile() -> None:
    text = read_builder()

    assert "Assert-PayloadFileStagedFromRepo" in text
    assert "Assert-RepoPayloadStagingIntegrity" in text
    assert "NSIS payload staging mismatch" in text
    assert "The installer would package stale payload. Aborting." in text
    assert "payloadIntegrityVerified = $payloadIntegrity" in text

    assert 'RelativePath = "scripts/main-computer-start-stop.ps1"' in text
    assert '"Ensure-MainComputerPodmanMachineStarted"' in text
    assert '"podman machine start"' in text
    assert 'RelativePath = "bootstrap-main-computer-python-windows.ps1"' in text
    assert '"The installer-selected runtime must win over stale user/machine"' in text
    assert 'RelativePath = "requirements.txt"' in text
    assert '"podman-compose"' in text
    assert 'RelativePath = "main_computer/bootstrap/cli.py"' in text
    assert '"podman_compose_provider_path"' in text
    assert '"apply_podman_compose_provider_env"' in text
    assert 'RelativePath = "main_computer/bootstrap/install_root.py"' in text
    assert '"INSTALL_ROOT_ARCHIVE_BLOCKED_PREFIXES"' in text
    assert 'RelativePath = "main_computer/container_runtime.py"' in text
    assert '"podman_command_cwd"' in text
    assert '"MAIN_COMPUTER_PODMAN_COMMAND_CWD"' in text
    assert '"win-sshproxy.exe"' in text
    assert 'RelativePath = "scripts/windows/build-main-computer-nsis-installer.experimental-v7.ps1"' in text

    copy_order = text.index("Copy-RepoPayload -RepoRoot $repoRoot -PayloadRoot $payloadRoot")
    verify_order = text.index("$payloadIntegrity = Assert-RepoPayloadStagingIntegrity")
    wrapper_order = text.index("Write-PackageWrapper -WrapperPath $wrapperPath")
    compile_order = text.index('Write-Section "Compiling experimental NSIS setup EXE (v7)"')

    assert copy_order < verify_order < wrapper_order < compile_order

