from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dev_control_script_exposes_explicit_modes_and_polite_guard() -> None:
    script = (ROOT / "dev-control.ps1").read_text(encoding="utf-8")

    assert 'ValidateSet("", "local", "docker")' in script
    assert "[switch]$Polite" in script
    assert "[switch]$EnsureRenderer" in script
    assert '"setup-renderer"' in script
    assert '"doctor"' in script
    assert "Refuse-MismatchedModeIfPolite" in script
    assert "detected running mode: Docker" in script
    assert "detected running mode: local" in script
    assert "cleanup command: .\\dev-control.ps1 shutdown -Mode docker" in script
    assert "cleanup command: .\\dev-control.ps1 shutdown -Mode local" in script
    assert "Run one of these next time to skip this question:" in script
    assert '"  .\\dev-control.ps1 {0} -Mode local"' in script
    assert '"  .\\dev-control.ps1 {0} -Mode docker"' in script
    assert "Invoke-LegacyLocalControl" in script
    assert '$legacyArgs += "--auto-allow"' in script
    assert "& $controlScript @legacyArgs" in script
    assert "start --auto-allow -BindHost" not in script
    assert "shutdown --auto-allow -BindHost" not in script


def test_dev_control_script_prefers_127001_and_separate_ports() -> None:
    script = (ROOT / "dev-control.ps1").read_text(encoding="utf-8")

    assert '[string]$BindHost = "0.0.0.0"' in script
    assert "[int]$LocalPort = 8765" in script
    assert "[int]$DockerHostPort = 18765" in script
    assert "Avoid localhost" in script
    assert "http://127.0.0.1:$LocalPort" in script
    assert "http://127.0.0.1:${DockerHostPort}" in script


def test_dev_control_script_checks_renderer_for_local_and_docker() -> None:
    script = (ROOT / "dev-control.ps1").read_text(encoding="utf-8")

    assert "Resolve-MainComputerPython" in script
    assert '[Environment]::GetFolderPath("UserProfile")' in script
    assert 'Join-Path $userDslRoot ".venv\\Scripts\\python.exe"' in script
    assert "C:\\Users\\" not in script
    assert "WindowsApps" in script
    assert 'pip install "playwright>=1.40.0"' in script
    assert "playwright install chromium" in script
    assert "chromium.launch(headless=True)" in script
    assert "docker compose -f docker-compose.dev.yml --profile app build main-computer" in script
    assert "Test-DockerRenderer" in script


def test_docker_default_host_port_and_renderer_are_configured() -> None:
    compose = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "dev" / "app.Dockerfile").read_text(encoding="utf-8")

    assert "${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765" in compose
    assert '"8765:8765"' not in compose
    assert "PLAYWRIGHT_BROWSERS_PATH: /ms-playwright" in compose

    assert "playwright>=1.40.0" in dockerfile
    assert "python -m playwright install --with-deps chromium" in dockerfile
    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in dockerfile


def test_readme_documents_dev_control_modes_urls_and_renderer_setup() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "## Dev Server Control" in readme
    assert ".\\dev-control.ps1 start -Mode local" in readme
    assert ".\\dev-control.ps1 start -Mode docker" in readme
    assert "Local Windows viewport: http://127.0.0.1:8765" in readme
    assert "Docker viewport:        http://127.0.0.1:18765" in readme
    assert ".\\dev-control.ps1 setup-renderer -Mode local" in readme
    assert ".\\dev-control.ps1 setup-renderer -Mode docker" in readme
    assert "docker compose -f docker-compose.dev.yml --profile app build main-computer" in readme


def test_export_script_keeps_dev_control_in_snapshots() -> None:
    script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

    assert '"control-main-computer.ps1"' in script
    assert '"dev-control.ps1"' in script
