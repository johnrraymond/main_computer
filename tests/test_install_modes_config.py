from __future__ import annotations

from main_computer.config import MainComputerConfig


def test_config_reads_installer_mode_environment(monkeypatch) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_INSTALL_MODE", "safe")
    monkeypatch.setenv("MAIN_COMPUTER_MODE_LABEL", "Safe Mode")
    monkeypatch.setenv("MAIN_COMPUTER_GUIDANCE_LEVEL", "guided")
    monkeypatch.setenv("MAIN_COMPUTER_SAFE_MODE", "1")

    config = MainComputerConfig.from_env()

    assert config.install_mode == "safe"
    assert config.mode_label == "Safe Mode"
    assert config.guidance_level == "guided"
    assert config.safe_mode is True


def test_config_defaults_to_unleashed_mode(monkeypatch) -> None:
    monkeypatch.delenv("MAIN_COMPUTER_INSTALL_MODE", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_MODE_LABEL", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_GUIDANCE_LEVEL", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_SAFE_MODE", raising=False)

    config = MainComputerConfig.from_env()

    assert config.install_mode == "unleashed"
    assert config.mode_label == "Unleashed Mode"
    assert config.guidance_level == "developer"
    assert config.safe_mode is False
