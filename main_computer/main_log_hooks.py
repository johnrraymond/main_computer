from __future__ import annotations

import builtins
import io
import os
from pathlib import Path
import sys
import threading
from typing import Any, Callable

from main_computer.main_log_client import default_main_log_url, emit_main_log_text, main_log_is_disabled


ENV_MAIN_LOG_HOOKS = "MAIN_COMPUTER_MAIN_LOG_HOOKS"
ENV_MAIN_LOG_HOOK_OS_WRITE = "MAIN_COMPUTER_MAIN_LOG_HOOK_OS_WRITE"
ENV_MAIN_LOG_HOOK_SUFFIXES = "MAIN_COMPUTER_MAIN_LOG_HOOK_SUFFIXES"
ENV_MAIN_LOG_SERVICE_NAME = "MAIN_COMPUTER_SERVICE_NAME"
ENV_MAIN_LOG_ROOT = "MAIN_COMPUTER_ROOT"

DEFAULT_LOG_SUFFIXES = (".log", ".out", ".err", ".trace", ".jsonl")
DEFAULT_HOOK_TIMEOUT_S = 0.05
MAX_FILE_WRITE_EVENT_CHARS = 64_000


_OriginalOpen = Callable[..., Any]

_ORIGINAL_BUILTINS_OPEN: _OriginalOpen = builtins.open
_ORIGINAL_IO_OPEN: _OriginalOpen = io.open
_ORIGINAL_OS_WRITE = os.write

_INSTALLED = False
_CAPTURE_OS_WRITE = False
_SERVICE_NAME = ""
_ROOT: Path | None = None
_URL = ""
_SUFFIXES: tuple[str, ...] = DEFAULT_LOG_SUFFIXES
_THREAD_LOCAL = threading.local()
_INSTALL_LOCK = threading.RLock()


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _suffixes_from_env() -> tuple[str, ...]:
    raw = str(os.environ.get(ENV_MAIN_LOG_HOOK_SUFFIXES) or "").strip()
    if not raw:
        return DEFAULT_LOG_SUFFIXES
    values = []
    for item in raw.replace(";", ",").split(","):
        suffix = item.strip().lower()
        if not suffix:
            continue
        if not suffix.startswith("."):
            suffix = "." + suffix
        values.append(suffix)
    return tuple(values or DEFAULT_LOG_SUFFIXES)


def _path_from_open_target(file: Any) -> Path | None:
    if isinstance(file, int):
        return None
    try:
        return Path(os.fspath(file))
    except (TypeError, ValueError):
        return None


def _path_is_main_log(path: Path) -> bool:
    root = _ROOT
    try:
        resolved = path.expanduser().resolve()
    except Exception:
        resolved = path.absolute()
    candidates: list[Path] = []
    if root is not None:
        candidates.append(root / "runtime" / "main_log" / "main.log.jsonl")
        candidates.append(root / "runtime" / "main_log" / "main.log.lex")
        candidates.append(root / "runtime" / "main_log" / "state.json")
    for candidate in candidates:
        try:
            if resolved == candidate.expanduser().resolve():
                return True
        except Exception:
            if str(resolved) == str(candidate):
                return True
    parts = {part.casefold() for part in resolved.parts}
    return "runtime" in parts and "main_log" in parts and resolved.name.casefold() in {"main.log.jsonl", "main.log.lex", "state.json"}


def _mode_writes(mode: object) -> bool:
    text = str(mode or "r")
    return any(flag in text for flag in ("w", "a", "x", "+"))


def _should_capture_file(file: Any, mode: object) -> tuple[bool, Path | None]:
    if main_log_is_disabled() or getattr(_THREAD_LOCAL, "inside_emit", False):
        return False, None
    if not _mode_writes(mode):
        return False, None
    path = _path_from_open_target(file)
    if path is None:
        return False, None
    if path.suffix.casefold() not in _SUFFIXES:
        return False, path
    if _path_is_main_log(path):
        return False, path
    return True, path


def _repo_relative(path: Path) -> str | None:
    root = _ROOT
    if root is None:
        return None
    try:
        return str(path.expanduser().resolve().relative_to(root.expanduser().resolve()))
    except Exception:
        return None


def _coerce_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _emit_hook_text(
    *,
    kind: str,
    stream: str,
    message: str,
    path: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if not message or getattr(_THREAD_LOCAL, "inside_emit", False):
        return
    event_extra = dict(extra or {})
    if path is not None:
        event_extra["path"] = str(path)
        repo_path = _repo_relative(path)
        if repo_path:
            event_extra["repo_path"] = repo_path
    _THREAD_LOCAL.inside_emit = True
    try:
        emit_main_log_text(
            service=_SERVICE_NAME or os.environ.get(ENV_MAIN_LOG_SERVICE_NAME) or "main-computer-python-process",
            source_service=_SERVICE_NAME or os.environ.get(ENV_MAIN_LOG_SERVICE_NAME) or "main-computer-python-process",
            kind=kind,
            stream=stream,
            message=message,
            url=_URL or default_main_log_url(),
            timeout_s=DEFAULT_HOOK_TIMEOUT_S,
            max_chunk_chars=MAX_FILE_WRITE_EVENT_CHARS,
            **event_extra,
        )
    finally:
        _THREAD_LOCAL.inside_emit = False


class TeeLogFile:
    """Small proxy that mirrors write-like calls to the main-log service.

    The wrapped file remains the source of truth.  Main-log forwarding is
    best-effort and never changes the return value or exception behavior of the
    original file write.
    """

    def __init__(self, handle: Any, path: Path) -> None:
        self._handle = handle
        self._path = path

    def write(self, data: Any) -> Any:
        result = self._handle.write(data)
        try:
            text = _coerce_text(data)
            if text:
                _emit_hook_text(kind="file-write", stream="file-log", message=text, path=self._path)
        except Exception:
            pass
        return result

    def writelines(self, lines: Any) -> Any:
        materialized = list(lines)
        result = self._handle.writelines(materialized)
        try:
            text = "".join(_coerce_text(item) for item in materialized)
            if text:
                _emit_hook_text(kind="file-write", stream="file-log", message=text, path=self._path)
        except Exception:
            pass
        return result

    def __enter__(self) -> "TeeLogFile":
        self._handle.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return self._handle.__exit__(exc_type, exc, tb)

    def __iter__(self) -> Any:
        return iter(self._handle)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def _wrap_open(open_func: _OriginalOpen) -> _OriginalOpen:
    def _open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        capture, path = _should_capture_file(file, mode)
        handle = open_func(file, mode, *args, **kwargs)
        if capture and path is not None:
            try:
                return TeeLogFile(handle, path)
            except Exception:
                return handle
        return handle

    return _open


def _patched_os_write(fd: int, data: bytes | bytearray | memoryview) -> int:
    written = _ORIGINAL_OS_WRITE(fd, data)
    try:
        if _CAPTURE_OS_WRITE and int(fd) in (1, 2) and not getattr(_THREAD_LOCAL, "inside_emit", False):
            stream = "stdout" if int(fd) == 1 else "stderr"
            _emit_hook_text(
                kind="os-write",
                stream=stream,
                message=bytes(data[:written]).decode("utf-8", errors="replace"),
                extra={"fd": int(fd)},
            )
    except Exception:
        pass
    return written


def install_main_log_hooks(
    *,
    service_name: str | None = None,
    root: str | Path | None = None,
    url: str | None = None,
    capture_os_write: bool = False,
    suffixes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Install best-effort Python write hooks for Main Computer-owned services.

    The supervisor already captures child stdout/stderr at the process boundary,
    so ``capture_os_write`` defaults to false to avoid duplicate stdout/stderr
    records in normal supervised children.  It can be enabled for nested worker
    processes whose stdout/stderr are piped to a parent instead of the supervisor.
    """

    global _INSTALLED, _CAPTURE_OS_WRITE, _SERVICE_NAME, _ROOT, _URL, _SUFFIXES
    if main_log_is_disabled():
        return {"ok": False, "state": "disabled", "message": "main log hooks disabled by environment"}
    with _INSTALL_LOCK:
        _SERVICE_NAME = str(service_name or os.environ.get(ENV_MAIN_LOG_SERVICE_NAME) or "").strip()
        root_value = root if root is not None else os.environ.get(ENV_MAIN_LOG_ROOT)
        _ROOT = Path(root_value).expanduser().resolve() if root_value else None
        _URL = str(url or os.environ.get("MAIN_COMPUTER_MAIN_LOG_URL") or "").strip()
        _SUFFIXES = tuple(s.casefold() for s in (suffixes or _suffixes_from_env()))
        _CAPTURE_OS_WRITE = bool(capture_os_write)

        if not _INSTALLED:
            builtins.open = _wrap_open(_ORIGINAL_BUILTINS_OPEN)  # type: ignore[assignment]
            io.open = _wrap_open(_ORIGINAL_IO_OPEN)  # type: ignore[assignment]
            os.write = _patched_os_write  # type: ignore[assignment]
            _INSTALLED = True
        return {
            "ok": True,
            "state": "installed",
            "service_name": _SERVICE_NAME,
            "root": str(_ROOT) if _ROOT is not None else "",
            "url": _URL or default_main_log_url(),
            "capture_os_write": _CAPTURE_OS_WRITE,
            "suffixes": list(_SUFFIXES),
        }


def install_main_log_hooks_from_env(
    *,
    default_service_name: str,
    root: str | Path | None = None,
    capture_os_write: bool = False,
) -> dict[str, Any]:
    if not _truthy(os.environ.get(ENV_MAIN_LOG_HOOKS)):
        return {"ok": False, "state": "disabled", "message": f"{ENV_MAIN_LOG_HOOKS} is not enabled"}
    return install_main_log_hooks(
        service_name=os.environ.get(ENV_MAIN_LOG_SERVICE_NAME) or default_service_name,
        root=root,
        capture_os_write=_truthy(os.environ.get(ENV_MAIN_LOG_HOOK_OS_WRITE)) or capture_os_write,
    )


def uninstall_main_log_hooks() -> None:
    """Restore original functions.

    This is primarily for tests.  Production code should install once during
    process startup and leave the hooks in place for the process lifetime.
    """

    global _INSTALLED, _CAPTURE_OS_WRITE, _SERVICE_NAME, _ROOT, _URL, _SUFFIXES
    with _INSTALL_LOCK:
        if _INSTALLED:
            builtins.open = _ORIGINAL_BUILTINS_OPEN  # type: ignore[assignment]
            io.open = _ORIGINAL_IO_OPEN  # type: ignore[assignment]
            os.write = _ORIGINAL_OS_WRITE  # type: ignore[assignment]
        _INSTALLED = False
        _CAPTURE_OS_WRITE = False
        _SERVICE_NAME = ""
        _ROOT = None
        _URL = ""
        _SUFFIXES = DEFAULT_LOG_SUFFIXES
