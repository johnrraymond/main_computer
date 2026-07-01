"""Small .env-like runtime control file parser.

The Hub deployment path uses this for operator-editable runtime files such as
``hub-runtime.env``.  It intentionally supports only KEY=VALUE assignments and
optional ``export KEY=VALUE`` prefixes; it does not execute shell code.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Mapping, MutableMapping


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RuntimeEnvFileError(ValueError):
    """Raised when a runtime env file is malformed."""


def parse_runtime_env_text(text: str, *, source: str = "<runtime-env>") -> dict[str, str]:
    """Parse a strict .env-like text payload into environment key/value pairs.

    Supported lines are blank lines, comments, ``KEY=VALUE``, and
    ``export KEY=VALUE``. Values may be shell-quoted for spaces or ``#``.
    Command expansion, variable expansion, and multiline values are not
    evaluated.
    """

    # Accept files written by Windows PowerShell 5.1, which may create a
    # UTF-8 BOM even for otherwise empty files.
    text = text.lstrip("\ufeff")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parts = shlex.split(raw_line, comments=True, posix=True)
        except ValueError as exc:
            raise RuntimeEnvFileError(f"{source}:{line_number}: invalid quoting: {exc}") from exc
        if not parts:
            continue
        if parts[0] == "export":
            parts = parts[1:]
        if len(parts) != 1 or "=" not in parts[0]:
            raise RuntimeEnvFileError(
                f"{source}:{line_number}: expected KEY=VALUE or export KEY=VALUE"
            )
        key, value = parts[0].split("=", 1)
        if not _ENV_KEY_RE.match(key):
            raise RuntimeEnvFileError(f"{source}:{line_number}: invalid environment key {key!r}")
        values[key] = value
    return values


def load_runtime_env_file(path: str | os.PathLike[str] | None) -> dict[str, str]:
    """Load ``path`` as a runtime env file.

    Empty paths return an empty dict. Missing explicit files raise so a deploy
    does not silently start with stale settings.
    """

    if path is None:
        return {}
    clean = str(path).strip()
    if not clean:
        return {}
    env_path = Path(clean)
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise RuntimeEnvFileError(f"runtime env file does not exist: {clean}") from exc
    return parse_runtime_env_text(text, source=str(env_path))


def merged_runtime_env(
    environ: Mapping[str, str],
    runtime_values: Mapping[str, str],
) -> dict[str, str]:
    """Return ``environ`` with runtime-file values layered on top."""

    merged = {str(key): str(value) for key, value in environ.items()}
    for key, value in runtime_values.items():
        merged[str(key)] = str(value)
    return merged


def apply_runtime_env_file(
    path: str | os.PathLike[str] | None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Load ``path`` and write values into ``environ``; return loaded values."""

    loaded = load_runtime_env_file(path)
    if not loaded:
        return loaded
    target = os.environ if environ is None else environ
    for key, value in loaded.items():
        target[str(key)] = str(value)
    return loaded
