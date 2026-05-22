from __future__ import annotations

from pathlib import Path

from .process import CommandError, run_command


MATHICS_PIN = "Mathics3==10.0.0"


def install_mathics_if_requested(
    *,
    mode: str,
    venv_python: Path,
    wheelhouse: Path,
    log_dir: Path,
) -> None:
    if mode == "disabled":
        print("Skipping Mathics optional dependency install by default.", flush=True)
        return

    wheelhouse.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        run_command(
            [
                venv_python,
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--no-input",
                "--progress-bar",
                "off",
                "--only-binary",
                ":all:",
                "--dest",
                wheelhouse,
                MATHICS_PIN,
            ],
            timeout_seconds=300,
            log_path=log_dir / "pip-download-mathics.log",
        )
        run_command(
            [
                venv_python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--progress-bar",
                "off",
                "--no-index",
                "--find-links",
                wheelhouse,
                MATHICS_PIN,
            ],
            timeout_seconds=300,
            log_path=log_dir / "pip-install-mathics.log",
        )
    except CommandError:
        if mode == "required":
            raise
        print("Mathics optional dependency could not be installed wheel-only; continuing because mode is auto.", flush=True)
