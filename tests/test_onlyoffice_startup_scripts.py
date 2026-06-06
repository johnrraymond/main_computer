from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_onlyoffice_control_manages_both_wsl_bridges() -> None:
    text = read("tools/onlyoffice/onlyoffice-control.ps1")

    assert '"bridge-start"' in text
    assert '"bridge-status"' in text
    assert '"bridge-stop"' in text
    assert '"bridge-start-elevated"' in text
    assert '"bridge-stop-elevated"' in text

    # Browser/Windows -> WSL-hosted ONLYOFFICE Docs API.
    assert '127.0.0.1:$Port -> $ResolvedWslIp`:$Port' in text
    assert 'Set-PortProxyEntry -ListenAddress "127.0.0.1" -ListenPort $Port' in text

    # WSL-hosted ONLYOFFICE -> Windows Main Computer file/callback endpoints.
    assert '$ResolvedWslGatewayIp`:$AppPort -> 127.0.0.1:$AppPort' in text
    assert 'Set-PortProxyEntry -ListenAddress $ResolvedWslGatewayIp -ListenPort $AppPort' in text

    # ONLYOFFICE must be allowed to fetch the WSL gateway private IP URL in local mode.
    assert 'allowPrivateIPAddress' in text
    assert 'Ensure-WslPrivateIpDownloadAllowed' in text

    # The embedded Python must be passed in a way that preserves quotes through
    # PowerShell -> wsl.exe -> Python. A here-doc string passed through bash -lc
    # lost quotes around Path("/etc/...") on Windows.
    assert 'function Invoke-WslPythonAsRoot' in text
    assert 'base64.b64decode' in text
    assert "python3 - <<'PY'" not in text

    # bridge-start should be idempotent: if the existing bridges are healthy,
    # normal start_v2.bat must not request elevation again.
    assert '$initialStatus = Get-OnlyOfficeBridgeStatus' in text
    assert 'ONLYOFFICE WSL bridges are already ready; no elevated changes are needed.' in text
    assert text.index('$initialStatus.ready') < text.index('Invoke-ElevatedSelf "bridge-start-elevated"')

    # The elevated UAC child must not re-enter WSL startup/configuration work.
    assert 'Start-OnlyOfficeWindowsBridgeEntries' in text
    assert 'Ensure-WslPrivateIpDownloadAllowed' in text
    assert text.index('Ensure-WslPrivateIpDownloadAllowed') < text.index('Invoke-ElevatedSelf "bridge-start-elevated"')
    elevated_case = text.split('"bridge-start-elevated" {', 1)[1].split('"bridge-status"', 1)[0]
    assert 'Ensure-WslDistroInstalledAndReady' not in elevated_case
    assert 'Get-WslOnlyOfficeIp' not in elevated_case
    assert 'Get-WslGatewayIp' not in elevated_case
    assert 'wsl.exe' not in elevated_case


def test_onlyoffice_wsl_start_removes_stale_applications_docker_container() -> None:
    text = read("tools/onlyoffice/onlyoffice-control.ps1")

    assert 'function Remove-StaleApplicationsDockerOnlyOffice' in text
    assert '--filter "name=main-computer-applications-onlyoffice"' in text
    assert "'^onlyoffice/documentserver(?::|$)'" in text
    assert 'Remove-StaleApplicationsDockerOnlyOffice' in text
    assert text.index('"start" {') < text.index('Remove-StaleApplicationsDockerOnlyOffice', text.index('"start" {')) < text.index('Invoke-WslOnlyOffice "wsl-start-onlyoffice.sh"')


def test_onlyoffice_wsl_start_installs_native_package_when_missing() -> None:
    text = read("tools/onlyoffice/onlyoffice-control.ps1")

    assert 'function Test-WslNativeOnlyOfficeInstalled' in text
    assert 'function Ensure-WslNativeOnlyOfficeInstalled' in text
    assert 'dpkg-query -s onlyoffice-documentserver' in text
    assert 'wsl-install-onlyoffice.sh' in text

    start_case = text.split('"start" {', 1)[1].split('"stop"', 1)[0]
    assert start_case.index('Remove-StaleApplicationsDockerOnlyOffice') < start_case.index('Ensure-WslNativeOnlyOfficeInstalled')
    assert start_case.index('Ensure-WslNativeOnlyOfficeInstalled') < start_case.index('Invoke-WslOnlyOffice "wsl-start-onlyoffice.sh"')


def test_start_v2_helper_invokes_onlyoffice_startup_control() -> None:
    text = read("scripts/main-computer-start-stop.ps1")

    assert 'function Invoke-MainComputerOnlyOfficeControl' in text
    assert 'Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "start"' in text
    assert 'Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "bridge-status"' in text
    assert 'Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "bridge-stop"' in text
    assert 'ONLYOFFICE bridge-status reported not-ready diagnostics; see output above.' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_REMOVE_BRIDGES_ON_STOP' in text
    assert 'Leaving ONLYOFFICE WSL bridge portproxies installed' in text

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


def test_onlyoffice_docker_compose_uses_18085_no_jwt_and_private_ip_allowance() -> None:
    text = read("docker-compose.onlyoffice.yml")
    control = read("tools/onlyoffice/onlyoffice-control.ps1")

    assert 'container_name: ${MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME:-main-computer-onlyoffice-documentserver}' in text
    assert 'JWT_ENABLED: ${MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED:-false}' in text
    assert 'ALLOW_PRIVATE_IP_ADDRESS: ${MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS:-true}' in text
    assert 'ALLOW_META_IP_ADDRESS: ${MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS:-true}' in text
    assert '127.0.0.1:${MAIN_COMPUTER_ONLYOFFICE_PORT:-18085}:80' in text

    assert '[ValidateSet("wsl", "docker")]' in control
    assert '[string]$JwtEnabled = ""' in control
    assert '$JwtEnabled = ConvertTo-MainComputerBoolText $JwtEnabled ($Mode -ne "docker")' in control
    assert '$env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS = "true"' in control
    assert '$env:MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS = "true"' in control
    assert 'Invoke-DockerOnlyOffice "status"' in control


def test_onlyoffice_wsl_distro_is_managed_and_configurable() -> None:
    control = read("tools/onlyoffice/onlyoffice-control.ps1")
    start_stop = read("scripts/main-computer-start-stop.ps1")

    assert 'MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO' in control
    assert 'Get-WslDistroNames' in control
    assert 'Test-WslDistroExists' in control
    assert 'Install-WslDistro' in control
    assert 'Ensure-WslDistroInstalledAndReady -DistroName $Distro' in control
    assert 'wsl.exe --install -d $DistroName' in control
    assert 'wsl.exe -d $DistroName -u root -- bash -lc "true"' in control
    assert 'lsb_release' not in control

    assert '$onlyOfficeWslDistro = $env:MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO' in start_stop
    assert 'MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO = $onlyOfficeWslDistro' in start_stop
    assert 'Get-LaunchEnvironmentValue $LaunchContext "MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO" "Ubuntu"' in start_stop
    assert '-Distro $distro' in start_stop


def test_onlyoffice_docker_browser_load_smoke_helper_is_isolated() -> None:
    text = read("tools/onlyoffice/test-onlyoffice-docker-browser-load.ps1")

    assert '[int]$Port = 18085' in text
    assert '[int]$CurrentWslPort = 18084' in text
    assert 'mc-onlyoffice-docker-twiddle' in text
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
