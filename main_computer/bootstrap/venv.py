from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

from .process import run_command


def venv_python_path(venv_root: Path) -> Path:
    windows_python = venv_root / "Scripts" / "python.exe"
    if windows_python.exists() or os.name == "nt":
        return windows_python
    return venv_root / "bin" / "python"


def create_venv_without_pip(base_python: Path, venv_root: Path, *, timeout_seconds: int = 120) -> Path:
    venv_python = venv_python_path(venv_root)
    pyvenv_cfg = venv_root / "pyvenv.cfg"
    if venv_python.exists() and pyvenv_cfg.exists():
        print(f"Using existing virtual environment: {venv_root}", flush=True)
        return venv_python

    if venv_root.exists():
        print(f"Removing incomplete virtual environment: {venv_root}", flush=True)
        shutil.rmtree(venv_root)

    venv_root.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [base_python, "-m", "venv", "--without-pip", venv_root],
        timeout_seconds=timeout_seconds,
    )

    if not venv_python.exists():
        raise RuntimeError(f"Virtual environment did not create expected Python: {venv_python}")
    return venv_python


def get_site_packages(venv_python: Path) -> Path:
    result = run_command(
        [
            venv_python,
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        timeout_seconds=30,
    )
    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"Could not determine site-packages for {venv_python}")
    path = Path(lines[-1])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _remove_existing_pip(site_packages: Path) -> None:
    for child in site_packages.iterdir():
        name = child.name.lower()
        if name == "pip" or (name.startswith("pip-") and name.endswith(".dist-info")):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()


def seed_pip_from_wheel(venv_python: Path, venv_root: Path, wheel_path: Path) -> None:
    site_packages = get_site_packages(venv_python)
    print(f"Seeding pip from wheel: {wheel_path}", flush=True)
    print(f"Site-packages: {site_packages}", flush=True)

    _remove_existing_pip(site_packages)
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(site_packages)

    run_command([venv_python, "-m", "pip", "--version"], timeout_seconds=30)


def _pip_base_args(venv_python: Path) -> list[Path | str]:
    return [
        venv_python,
        "-m",
        "pip",
        "install",
        "-vvv",
        "--disable-pip-version-check",
        "--no-input",
        "--progress-bar",
        "off",
        "--timeout",
        "30",
        "--retries",
        "2",
    ]


def pip_install_project(venv_python: Path, project_root: Path, log_path: Path) -> None:
    requirements_path = project_root / "requirements.txt"
    if requirements_path.exists():
        requirements_log_path = log_path.with_name("pip-install-requirements.log")
        print(f"Pip requirements log: {requirements_log_path}", flush=True)
        run_command(
            [
                *_pip_base_args(venv_python),
                "-r",
                "requirements.txt",
            ],
            cwd=project_root,
            timeout_seconds=1800,
            log_path=requirements_log_path,
        )
    else:
        print(f"No requirements.txt found at {requirements_path}; installing editable project only.", flush=True)

    print(f"Pip install log: {log_path}", flush=True)
    run_command(
        [
            *_pip_base_args(venv_python),
            "-e",
            ".",
        ],
        cwd=project_root,
        timeout_seconds=600,
        log_path=log_path,
    )

    pip_check_log_path = log_path.with_name("pip-check.log")
    print(f"Pip check log: {pip_check_log_path}", flush=True)
    run_command(
        [
            venv_python,
            "-m",
            "pip",
            "check",
        ],
        cwd=project_root,
        timeout_seconds=180,
        log_path=pip_check_log_path,
    )
