from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "start-main-computer-docker-windows.ps1"


def test_windows_docker_launcher_script_exists_and_generates_host_drive_override() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "compose.host-drives.yml" in text
    assert "MAIN_COMPUTER_PATH_MODE: mounted-windows" in text
    assert "MAIN_COMPUTER_HOST_OS: windows" in text
    assert "MAIN_COMPUTER_HOST_DRIVE_ROOT: /host" in text
    assert "Get-PSDrive -PSProvider FileSystem" in text
    assert 'target: $($drive.Target)' in text
    assert "/host/$($letter.ToLowerInvariant())" in text


def test_windows_docker_launcher_preserves_base_compose_and_uses_app_profile() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "docker-compose.dev.yml" in text
    assert '"--profile", "app"' in text
    assert '"up", "-d"' in text
    assert '"main-computer"' in text
    assert "docker compose @Arguments" in text


def test_windows_docker_launcher_supports_safe_drive_filters_and_readonly_mode() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "[string[]]$IncludeDrives" in text
    assert "[string[]]$ExcludeDrives" in text
    assert "[switch]$ReadOnlyDrives" in text
    assert "read_only: true" in text
    assert "Normalize-DriveLetter" in text
    assert "Get-Variable IsWindows" in text


def test_dockerignore_excludes_tmp_smoke_output() -> None:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".tmp/" in text


def test_windows_docker_launcher_avoids_function_calls_inside_hashtable_indices() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "$includeSet[Normalize-DriveLetter" not in text
    assert "$excludeSet[Normalize-DriveLetter" not in text
    assert "$includeLetter = Normalize-DriveLetter $item" in text
    assert "$includeSet[$includeLetter] = $true" in text
    assert "$excludeLetter = Normalize-DriveLetter $item" in text
    assert "$excludeSet[$excludeLetter] = $true" in text


def test_windows_docker_launcher_forces_recreate_by_default() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "[switch]$NoForceRecreate" in text
    assert "--force-recreate" in text
    assert "if (-not $NoForceRecreate)" in text
    assert "local/auto" in text


def test_base_compose_accepts_host_drive_environment_interpolation() -> None:
    text = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "MAIN_COMPUTER_PATH_MODE: ${MAIN_COMPUTER_PATH_MODE:-local}" in text
    assert "MAIN_COMPUTER_HOST_OS: ${MAIN_COMPUTER_HOST_OS:-auto}" in text
    assert "MAIN_COMPUTER_HOST_DRIVE_ROOT: ${MAIN_COMPUTER_HOST_DRIVE_ROOT:-/host}" in text


def test_windows_docker_launcher_sets_compose_interpolation_environment() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert '$env:MAIN_COMPUTER_PATH_MODE = "mounted-windows"' in text
    assert '$env:MAIN_COMPUTER_HOST_OS = "windows"' in text
    assert '$env:MAIN_COMPUTER_HOST_DRIVE_ROOT = "/host"' in text


def test_windows_docker_launcher_verifies_api_before_success() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "[switch]$SkipVerify" in text
    assert "Wait-ForMountedWindowsPathMode" in text
    assert 'path_mode -eq "mounted-windows"' in text
    assert 'host_os -eq "windows"' in text
    assert "docker app did not report mounted-windows/windows" in text.lower()


def test_base_compose_supports_docker_viewport_port_interpolation() -> None:
    text = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765" in text


def test_windows_docker_launcher_verifies_container_internal_api_and_host_api() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "[int]$HostPort = 8765" in text
    assert '$env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT = [string]$HostPort' in text
    assert "Write-InternalPathMountProbe" in text
    assert "Wait-ForContainerInternalPathMode" in text
    assert "Wait-ForMountedWindowsPathMode" in text
    assert '"docker exec $ContainerId python $ContainerProbePath 2>&1"' in text
    assert "cmd.exe /d /s /c $command" in text
    assert "Container-internal /api/path-mounts probe did not pass before timeout" in text
    assert "Container-internal /api/path-mounts is correct, but host API is not." in text
    assert "-HostPort 8875" in text
