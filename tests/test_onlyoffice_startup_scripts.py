from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_onlyoffice_control_is_docker_only() -> None:
    text = read("tools/onlyoffice/onlyoffice-control.ps1")

    assert '[ValidateSet("install", "start", "stop", "status", "doctor")]' in text
    assert '[ValidateSet("docker")]' in text
    assert '[int]$Port = 18085' in text
    assert 'Invoke-DockerOnlyOffice "start"' in text
    assert 'Invoke-DockerOnlyOffice "status"' in text
    assert 'function Get-DockerOnlyOfficeContainerId' in text
    assert 'function Wait-DockerOnlyOfficeContainer' in text
    assert 'docker compose up -d onlyoffice returned exit code $composeExitCode' in text
    assert 'function Test-LocalTcpPortOpen' in text
    assert 'function Start-DockerOnlyOfficeNamedContainerIfPresent' in text
    assert 'Shared ONLYOFFICE already reachable on port $Port; start path will not recreate it.' in text
    assert 'Compose up will not recreate it.' in text
    assert 'docker compose -f $composePath -p $ProjectName ps -q onlyoffice' in text
    assert 'docker ps -q --filter $nameFilter' in text
    assert 'docker ps -aq --filter $nameFilter' in text
    assert 'docker inspect --format "{{.State.Running}} {{.Id}}" $containerName' in text
    assert 'ONLYOFFICE container is visible after $attempt docker/container inspection attempt(s)' in text
    assert '$script:MainComputerOnlyOfficeNeedsFinalStatus = $true' in text
    assert 'ONLYOFFICE final readiness recheck' in text
    assert 'http://127.0.0.1:$Port' in text
    assert 'http://host.docker.internal:$AppPort' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS = "true"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS = "true"' in text

    forbidden = [
        "wsl.exe",
        "netsh interface portproxy",
        "New-NetFirewallRule",
        "Get-NetFirewallRule",
        "bridge-start",
        "bridge-status",
        "bridge-stop",
        "Invoke-WslOnlyOffice",
        "Set-PortProxyEntry",
        "Ensure-WslPrivateIpDownloadAllowed",
    ]
    for needle in forbidden:
        assert needle not in text


def test_onlyoffice_wsl_helper_scripts_removed() -> None:
    for rel in [
        "tools/onlyoffice/wsl-install-onlyoffice.sh",
        "tools/onlyoffice/wsl-start-onlyoffice.sh",
        "tools/onlyoffice/wsl-status-onlyoffice.sh",
        "tools/onlyoffice/wsl-stop-onlyoffice.sh",
    ]:
        assert not (REPO / rel).exists(), rel


def test_onlyoffice_docs_install_script_is_tracked_with_onlyoffice_tools() -> None:
    script = REPO / "tools/onlyoffice/docs-install.sh"

    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "Copyright (C) Ascensio System SIA" in text
    assert "SPDX-License-Identifier: AGPL-3.0-only" in text


def test_main_computer_underscore_runtime_typo_is_ignored() -> None:
    text = read(".gitignore")

    assert ".main-computer/" in text
    assert ".main_computer/" in text


def test_start_v2_helper_invokes_docker_onlyoffice_control_without_bridge_management() -> None:
    text = read("scripts/main-computer-start-stop.ps1")

    assert 'function Invoke-MainComputerOnlyOfficeControl' in text
    assert 'Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "start"' in text
    assert 'Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "status"' in text
    assert 'Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "stop"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_STOP_ON_STOP' in text
    assert 'Leaving ONLYOFFICE Docker container running for faster next startup.' in text

    assert 'MAIN_COMPUTER_ONLYOFFICE_ENABLED = "1"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_MODE = "docker"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_PORT = "18085"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME = "main-computer-onlyoffice-documentserver"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL = "http://127.0.0.1:18085"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL = "http://127.0.0.1:18085"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL = "http://127.0.0.1:18085"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = "http://host.docker.internal:$port"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED = "false"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS = "true"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS = "true"' in text
    assert '"-JwtEnabled", $jwtEnabled' in text
    assert '$controlArgs = @(' in text
    assert 'if (-not [string]::IsNullOrWhiteSpace($jwtSecret))' in text
    assert '$controlArgs += @("-JwtSecret", $jwtSecret)' in text
    assert '& powershell @controlArgs' in text

    forbidden = [
        "bridge-status",
        "bridge-stop",
        "ONLYOFFICE WSL bridge",
        "MAIN_COMPUTER_ONLYOFFICE_REMOVE_BRIDGES_ON_STOP",
        "MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO",
        '"-Distro", $distro',
    ]
    for needle in forbidden:
        assert needle not in text


def test_onlyoffice_docker_compose_uses_18085_no_jwt_and_private_ip_allowance() -> None:
    text = read("docker-compose.onlyoffice.yml")
    control = read("tools/onlyoffice/onlyoffice-control.ps1")

    assert 'container_name: ${MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME:-main-computer-onlyoffice-documentserver}' in text
    assert 'JWT_ENABLED: ${MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED:-false}' in text
    assert 'ALLOW_PRIVATE_IP_ADDRESS: ${MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS:-true}' in text
    assert 'ALLOW_META_IP_ADDRESS: ${MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS:-true}' in text
    assert '127.0.0.1:${MAIN_COMPUTER_ONLYOFFICE_PORT:-18085}:80' in text
    assert 'ONLYOFFICE Docker uses 18085' in text

    assert '[ValidateSet("docker")]' in control
    assert '[string]$JwtEnabled = ""' in control
    assert '$JwtEnabled = ConvertTo-MainComputerBoolText $JwtEnabled $false' in control
    assert '$env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS = "true"' in control
    assert '$env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS = "true"' in control
    assert 'Invoke-DockerOnlyOffice "status"' in control


def test_onlyoffice_docker_browser_load_smoke_helper_is_isolated() -> None:
    text = read("tools/onlyoffice/test-onlyoffice-docker-browser-load.ps1")

    assert '[int]$Port = 18085' in text
    assert 'main-computer-onlyoffice-documentserver' in text
    assert 'mc-onlyoffice-docker-twiddle' in text
    assert 'CurrentWslPort' not in text
    assert 'Current WSL ONLYOFFICE port' not in text
    assert 'main-computer-applications-onlyoffice-1' in text
    assert '--restart", "no"' in text
    assert '"-p", $Publish' in text
    assert '$Publish = "127.0.0.1:${Port}:80"' in text
    assert '"-e", "JWT_ENABLED=false"' in text
    assert '"-e", "ALLOW_PRIVATE_IP_ADDRESS=true"' in text
    assert '"-e", "ALLOW_META_IP_ADDRESS=true"' in text
    assert 'onlyoffice/documentserver:latest' in text
    assert '$BaseUrl = "http://127.0.0.1:${Port}"' in text
    assert '$HealthUrl = "$BaseUrl/healthcheck"' in text
    assert '$ApiJsUrl = "$BaseUrl/web-apps/apps/api/documents/api.js"' in text
    assert '$ApiJsQueryUrl = "${ApiJsUrl}?twiddle=1"' in text
    assert '$ApiJsUrl?twiddle=1' not in text
    assert 'ExpectedContentTypePrefix "application/javascript"' in text
    assert 'delete window.DocsAPI;' in text
    assert 'console.log("Docker api.js loaded", Boolean(window.DocsAPI && window.DocsAPI.DocEditor));' in text
    assert 'console.error("Docker api.js script.onerror", e);' in text
    assert 'Get-DockerContainerIdByExactName' in text
    assert 'Remove-DockerContainerIfPresent -Name $ContainerName' in text
    assert 'docker ps -aq --filter $nameFilter' in text
    assert '& docker rm -f $ContainerName 2>$null' not in text
