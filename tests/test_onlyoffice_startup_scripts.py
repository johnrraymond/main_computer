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
    assert 'MAIN_COMPUTER_ONLYOFFICE_MODE = "wsl"' in text
    assert 'MAIN_COMPUTER_ONLYOFFICE_PORT = "18084"' in text


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
