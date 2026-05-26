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
    assert '${NSD_CreateRadioButton} 0 32u 100% 12u "Main Computer - Unleashed"' in text
    assert '${NSD_CreateRadioButton} 0 70u 100% 12u "Main Computer - Debug"' in text
    assert '${NSD_CreateRadioButton} 0 108u 100% 12u "Main Computer - Safe"' in text
    assert 'StrCpy $ResolvedInstallRoot "$PROFILE\\.main-computer-tools\\installs\\main_computer_test-test-$InstallModeKey"' in text
    assert 'CreateShortCut "$SMPROGRAMS\\Main Computer\\Main Computer - $ShortcutModeName.lnk"' in text
    assert 'CreateShortCut "$DESKTOP\\Main Computer - $ShortcutModeName.lnk"' in text
    assert '"$ResolvedInstallRoot\\start_v2.bat" "-OpenBrowser"' in text


def test_v7_hard_gates_docker_with_official_install_url() -> None:
    text = read_builder()

    assert "Assert-DockerRequirement" in text
    assert "Fail-DockerRequirement" in text
    assert "https://docs.docker.com/desktop/setup/install/windows-install/" in text
    assert '@("version")' in text
    assert '@("compose", "version")' in text
    assert '@("ps")' in text
    assert "Docker Desktop is required for this version of Main Computer." in text
    assert "Install Docker Desktop for Windows, start Docker Desktop once" in text


def test_v7_installs_or_repairs_owned_host_tools() -> None:
    text = read_builder()

    assert "Install-WingetPackage" in text
    assert 'PackageId "Git.Git"' in text
    assert 'PackageId "Ollama.Ollama"' in text
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


def test_v7_nsis_calls_wrapper_without_skipping_requirements() -> None:
    text = read_builder()

    nsis_call = (
        'nsExec::ExecToLog '
        '\'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\\'
        'Install-MainComputer-from-Package.nsis-experimental-v7.ps1" '
        '-RuntimeProfile test -Mode "$InstallModeArg" -InstallRoot "$ResolvedInstallRoot" -VerboseBootstrap\''
    )

    assert nsis_call in text
    assert "-SkipDockerRequirement" in text
    assert "-SkipHostRequirementInstall" in text
    assert "-SkipDockerRequirement" not in nsis_call
    assert "-SkipHostRequirementInstall" not in nsis_call


def test_v7_package_manifest_documents_requirement_policy() -> None:
    text = read_builder()

    assert "hostRequirementsPolicy" in text
    assert 'docker = "required; fail with official Docker Desktop Windows install link' in text
    assert 'git = "install or repair with winget when missing"' in text
    assert 'opensshClient = "install or repair Windows OpenSSH Client capability' in text
    assert 'python = "bootstrap installs requirements.txt before editable project install and then runs pip check"' in text
