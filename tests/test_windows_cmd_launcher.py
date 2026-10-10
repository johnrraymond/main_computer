"""Windows CLI bootstrap shim: keep ``main-computer`` without an unsigned exe."""

from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.bootstrap.windows_cmd_launcher import LAUNCHER, install_windows_cmd_launcher


ROOT = Path(__file__).resolve().parents[1]


def fake_venv(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.write_bytes(b"fake python")
    return scripts, python


def test_creates_venv_scoped_cmd_and_removes_only_conflicting_exe(tmp_path: Path) -> None:
    scripts, python = fake_venv(tmp_path)
    exe = scripts / "main-computer.exe"
    exe.write_bytes(b"pip launcher")
    other_exe = scripts / "another-tool.exe"
    other_exe.write_bytes(b"keep")

    cmd = install_windows_cmd_launcher(python)

    assert cmd == scripts / "main-computer.cmd"
    assert not exe.exists()
    assert other_exe.read_bytes() == b"keep"
    content = cmd.read_text(encoding="utf-8")
    assert '"%~dp0python.exe" -m main_computer.cli %*' in content
    assert 'exit /b %ERRORLEVEL%' in content
    assert cmd.read_bytes().count(b"\r\n") == content.count("\n")


def test_reinstall_is_idempotent_even_when_pip_recreates_exe(tmp_path: Path) -> None:
    scripts, python = fake_venv(tmp_path)
    old = install_windows_cmd_launcher(python)
    (scripts / "main-computer.exe").write_bytes(b"freshly generated")
    new = install_windows_cmd_launcher(python)
    assert old == new
    assert new.read_text(encoding="utf-8") == LAUNCHER
    assert not (scripts / "main-computer.exe").exists()


def test_refuses_global_python_or_non_scripts_location(tmp_path: Path) -> None:
    global_python = tmp_path / "python.exe"
    global_python.write_bytes(b"fake")
    with pytest.raises(ValueError, match="virtualenv"):
        install_windows_cmd_launcher(global_python)
    assert not (tmp_path / "main-computer.cmd").exists()


def test_does_not_remove_exe_if_python_missing(tmp_path: Path) -> None:
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    exe = scripts / "main-computer.exe"
    exe.write_bytes(b"keep")
    with pytest.raises(FileNotFoundError):
        install_windows_cmd_launcher(scripts / "python.exe")
    assert exe.read_bytes() == b"keep"


def test_refuses_symlinked_executable(tmp_path: Path) -> None:
    scripts, python = fake_venv(tmp_path)
    real = tmp_path / "other.exe"
    real.write_bytes(b"outside")
    try:
        (scripts / "main-computer.exe").symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    with pytest.raises(RuntimeError, match="symlinked"):
        install_windows_cmd_launcher(python)
    assert real.read_bytes() == b"outside"
    assert not (scripts / "main-computer.cmd").exists()


def test_both_windows_install_paths_run_launcher_after_pip() -> None:
    python_bootstrap = (ROOT / "main_computer" / "bootstrap" / "venv.py").read_text(encoding="utf-8")
    ps_bootstrap = (ROOT / "bootstrap-main-computer-windows.ps1").read_text(encoding="utf-8")
    assert '"-m", "main_computer.bootstrap.windows_cmd_launcher"' in python_bootstrap
    assert python_bootstrap.index('"-e",') < python_bootstrap.index("install-main-computer-cmd.log")
    start = ps_bootstrap.index("function Install-PythonDependencies")
    end = ps_bootstrap.index("function Test-PythonImport", start)
    block = ps_bootstrap[start:end]
    assert block.index('"-e", "."') < block.index('"main_computer.bootstrap.windows_cmd_launcher"')
    assert '"Main Computer CLI command launcher"' in block


def test_launcher_does_not_use_global_python_or_shell_activation() -> None:
    assert '"%~dp0python.exe"' in LAUNCHER
    assert "activate" not in LAUNCHER.lower()
    assert "main-computer.exe" not in LAUNCHER.lower()
