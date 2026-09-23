from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_nanojev_compose_is_loopback_gpu_service_on_fast_path() -> None:
    compose_path = ROOT / "docker-compose.nanojev.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    service = compose["services"]["nanojev"]

    assert compose["name"] == "main-computer-nanojev"
    assert service["image"] == "main-computer/nanojev:unified-games-v1"
    assert service["gpus"] == "all"
    assert service["ports"] == ["127.0.0.1:${MAIN_COMPUTER_NANOJEV_BIND_PORT:-9765}:9765"]
    assert service["restart"] == "unless-stopped"
    assert "healthcheck" in service

    dockerfile = (ROOT / "docker" / "nanojev" / "Dockerfile").read_text(encoding="utf-8")
    assert "torch==2.14.0 --index-url https://download.pytorch.org/whl/cu126" in dockerfile
    assert '"--port", "9765"' in dockerfile
    assert '"--precision", "bf16"' in dockerfile
    assert '"--disable-native-triton"' not in dockerfile


def test_nanojev_image_uses_frozen_unified_release_and_verifies_weights() -> None:
    downloader = (ROOT / "docker" / "nanojev" / "download_release.py").read_text(encoding="utf-8")

    assert 'REVISION = "unified-games-v1"' in downloader
    assert 'REPO_ID = "C-Tianyu/NanoJev"' in downloader
    assert 'EXPECTED_WEIGHTS_SHA256 = "f68c47d66998231b86b7e91b4ed5e82ae23acf104c8b7cd6d165c3ac7b7ffe1b"' in downloader
    assert '"source/scripts/*"' in downloader
    assert '"source/requirements-toy.txt"' in downloader


def test_start_v2_boots_and_tracks_nanojev_container() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert 'MAIN_COMPUTER_NANOJEV_ENABLED = "1"' in helper
    assert 'MAIN_COMPUTER_NANOJEV_PORT = "9765"' in helper
    assert 'MAIN_COMPUTER_NANOJEV_URL = "http://127.0.0.1:9765"' in helper
    assert "function Start-MainComputerNanoJev" in helper
    assert '"up", "-d", "--build", "nanojev"' in helper
    assert "Test-MainComputerNanoJevHealth $baseUrl" in helper
    assert '$nanoJevStart = Start-MainComputerNanoJev $RootPath $launchContext $pythonCommand' in helper
    assert 'nanojev = $NanoJevStart' in helper
    assert 'name = "nanojev"' in helper
    assert 'compose_file = $nanoJevCompose' in helper
    assert 'NanoJev API:' in helper
    assert "Show-MainComputerNanoJevStatus $launchContext" in helper
