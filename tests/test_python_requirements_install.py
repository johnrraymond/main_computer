from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_venv_installs_requirements_before_editable_project() -> None:
    venv = (ROOT / "main_computer" / "bootstrap" / "venv.py").read_text(encoding="utf-8")
    cli = (ROOT / "main_computer" / "bootstrap" / "cli.py").read_text(encoding="utf-8")

    assert "pip_install_project" in cli
    assert 'requirements_path = project_root / "requirements.txt"' in venv
    assert '"-r"' in venv
    assert '"requirements.txt"' in venv
    assert "pip-install-requirements.log" in venv
    assert '"-e"' in venv
    assert '"."' in venv


def test_bootstrap_venv_runs_pip_check_after_installing_requirements() -> None:
    venv = (ROOT / "main_computer" / "bootstrap" / "venv.py").read_text(encoding="utf-8")

    assert 'pip_check_log_path = log_path.with_name("pip-check.log")' in venv
    assert '"check"' in venv
    assert "Pip check log" in venv
