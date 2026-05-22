from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ToolPaths:
    root: Path
    cpython_root: Path
    current_python_pointer: Path
    wheelhouse: Path
    manifests: Path


def user_tool_root() -> Path:
    return Path.home() / ".main-computer-tools"


def tool_paths() -> ToolPaths:
    root = user_tool_root()
    return ToolPaths(
        root=root,
        cpython_root=root / "cpython",
        current_python_pointer=root / "cpython" / "current-python.txt",
        wheelhouse=root / "wheels",
        manifests=root / "manifests",
    )


def same_file_text(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))


def verify_managed_python(managed_python: Path | None) -> None:
    current_executable = Path(sys.executable)
    if managed_python is not None and not same_file_text(current_executable, managed_python):
        raise RuntimeError(
            "Python bootstrap driver is not running under the managed Python it was handed. "
            f"sys.executable={current_executable}; managed_python={managed_python}"
        )

    print(f"Python executable: {current_executable}", flush=True)
    print(f"Python version: {sys.version.splitlines()[0]}", flush=True)
    if sys.version_info < (3, 10):
        raise RuntimeError("Main Computer bootstrap requires Python >= 3.10")

    paths = tool_paths()
    if paths.current_python_pointer.exists():
        pointer_value = paths.current_python_pointer.read_text(encoding="utf-8", errors="replace").strip()
        if pointer_value and managed_python is not None and not same_file_text(pointer_value, managed_python):
            raise RuntimeError(
                "Managed Python pointer does not match the Python used for this bootstrap. "
                f"pointer={pointer_value}; managed_python={managed_python}"
            )
