"""Crash-aware local atomic-file primitives for Mother authority data."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable, TypeVar

from .errors import MotherError
from . import faultpoints as faultpoint_module


_T = TypeVar("_T")
_MODULE_ID = "MOTHER-OFM-CORE-011"
_OPERATION_ID = "MOTHER-OP-INTERNAL"

_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


def _mother_error(
    code: str,
    message: str,
    *,
    retry_class: str,
    authority_effect: str = "none",
) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=_OPERATION_ID,
        module_id=_MODULE_ID,
        retry_class=retry_class,
        authority_effect=authority_effect,
    )


def _as_path(value: str | os.PathLike[str]) -> Path:
    if isinstance(value, str):
        if "\x00" in value:
            raise _mother_error(
                "MOTHER_INPUT_UNSAFE_PATH",
                "path contains a NUL byte",
                retry_class="never",
            )
        path = Path(value)
    elif isinstance(value, os.PathLike):
        path = Path(value)
    else:
        raise TypeError("path must be a string or path-like value")
    return path.absolute()


def _existing_symlink_component(path: Path) -> Path | None:
    """Return the first symlink in the lexical path, if any.

    The walk intentionally avoids ``resolve`` because resolving first would hide
    the very substitution that durable authority paths must reject.
    """

    parts = path.parts
    if not parts:
        return None

    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return current
        except OSError:
            # A path that cannot be safely inspected is not suitable for
            # authority publication.
            return current
    return None


def _validate_safe_target(path: Path) -> None:
    unsafe = _existing_symlink_component(path)
    if unsafe is not None:
        raise _mother_error(
            "MOTHER_INPUT_UNSAFE_PATH",
            f"unsafe symlink path component: {unsafe}",
            retry_class="never",
        )


def _bytes(value: bytes | bytearray | memoryview, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    return bytes(value)


def _faultpoint(controller: Any, name: str) -> None:
    if controller is None:
        faultpoint_module.hit(name)
    else:
        hit = getattr(controller, "hit", None)
        if not callable(hit):
            raise TypeError("faultpoints must provide hit(name, context=None)")
        hit(name)


def _lock_key(path: Path) -> str:
    normalized = os.path.normcase(str(path))
    return hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()


def _thread_lock(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


def _acquire_platform_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) not in (None, 33, 36) and exc.errno not in (
                    errno.EACCES,
                    errno.EDEADLK,
                ):
                    raise
                time.sleep(0.01)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _release_platform_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


class _ExclusiveTargetLock:
    """Class-based context manager safe for frozen typed exceptions."""

    __slots__ = ("_fd", "_thread_lock", "_lock_path")

    def __init__(self, path: Path) -> None:
        key = _lock_key(path)
        lock_root = Path(tempfile.gettempdir()) / "main_computer_mother_locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        self._lock_path = lock_root / f"{key}.lock"
        self._thread_lock = _thread_lock(key)
        self._fd: int | None = None

    def __enter__(self) -> "_ExclusiveTargetLock":
        self._thread_lock.acquire()
        try:
            self._fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            _acquire_platform_lock(self._fd)
        except BaseException:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if self._fd is not None:
                _release_platform_lock(self._fd)
                os.close(self._fd)
                self._fd = None
        finally:
            self._thread_lock.release()
        return False


def _exclusive_target_lock(path: Path) -> _ExclusiveTargetLock:
    return _ExclusiveTargetLock(path)

def flush_directory(path: str | os.PathLike[str]) -> None:
    """Durably flush a directory using the host platform's native primitive."""

    directory = _as_path(path)
    _validate_safe_target(directory)
    if not directory.is_dir():
        raise NotADirectoryError(directory)

    if os.name != "nt":
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(directory, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return

    # Windows requires a directory handle opened with backup semantics.  Keep
    # the ctypes dependency local so importing this module remains portable.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    CreateFileW.restype = ctypes.c_void_p
    FlushFileBuffers = kernel32.FlushFileBuffers
    FlushFileBuffers.argtypes = [ctypes.c_void_p]
    FlushFileBuffers.restype = ctypes.c_int
    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [ctypes.c_void_p]
    CloseHandle.restype = ctypes.c_int

    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    handle = CreateFileW(
        str(directory),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        CloseHandle(handle)


def _prepare_temp(parent: Path, target_name: str, data: bytes, controller: Any) -> Path:
    fd, raw_path = tempfile.mkstemp(
        prefix=f".{target_name}.mother-",
        suffix=".tmp",
        dir=parent,
    )
    temp_path = Path(raw_path)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            _faultpoint(controller, "immutable.after_temp_write")
            os.fsync(handle.fileno())
            _faultpoint(controller, "immutable.after_file_fsync")
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return temp_path


def stable_read(
    pointer: str | os.PathLike[str],
    load: Callable[[bytes], _T],
    *,
    max_attempts: int = 3,
) -> _T:
    """Load a view only when the pointer bytes are stable around the load."""

    if not callable(load):
        raise TypeError("load must be callable")
    if type(max_attempts) is not int or max_attempts <= 0:
        raise ValueError("max_attempts must be a positive integer")
    path = _as_path(pointer)
    _validate_safe_target(path)

    for _attempt in range(max_attempts):
        before = path.read_bytes()
        loaded = load(before)
        after = path.read_bytes()
        if before == after:
            return loaded

    raise _mother_error(
        "MOTHER_STATE_UNSTABLE_READ",
        "pointer changed during every bounded stable-read attempt",
        retry_class="after-reobserve",
    )


def durable_create(
    target: str | os.PathLike[str],
    data: bytes | bytearray | memoryview,
    *,
    faultpoints: Any = None,
) -> None:
    """Publish exact bytes only when the target is absent."""

    path = _as_path(target)
    payload = _bytes(data, "data")
    _validate_safe_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_safe_target(path)

    with _exclusive_target_lock(path):
        _validate_safe_target(path)
        if path.exists():
            raise _mother_error(
                "MOTHER_CONFLICT_DURABLE_TARGET_EXISTS",
                "durable target already exists",
                retry_class="after-reobserve",
            )

        temp_path = _prepare_temp(path.parent, path.name, payload, faultpoints)
        try:
            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise _mother_error(
                    "MOTHER_CONFLICT_DURABLE_TARGET_EXISTS",
                    "durable target was published by a competing writer",
                    retry_class="after-reobserve",
                ) from exc
            temp_path.unlink()
            _faultpoint(faultpoints, "immutable.after_publish_before_dir_fsync")
            flush_directory(path.parent)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def durable_replace(
    target: str | os.PathLike[str],
    data: bytes | bytearray | memoryview,
    *,
    faultpoints: Any = None,
) -> None:
    """Atomically replace or create a durable target with exact bytes."""

    path = _as_path(target)
    payload = _bytes(data, "data")
    _validate_safe_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_safe_target(path)

    with _exclusive_target_lock(path):
        _validate_safe_target(path)
        temp_path = _prepare_temp(path.parent, path.name, payload, faultpoints)
        try:
            os.replace(temp_path, path)
            _faultpoint(faultpoints, "immutable.after_publish_before_dir_fsync")
            flush_directory(path.parent)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _prepare_pointer_temp(parent: Path, target_name: str, data: bytes) -> Path:
    fd, raw_path = tempfile.mkstemp(
        prefix=f".{target_name}.mother-pointer-",
        suffix=".tmp",
        dir=parent,
    )
    temp_path = Path(raw_path)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return temp_path


def atomic_pointer_cas(
    pointer: str | os.PathLike[str],
    *,
    expected: bytes | bytearray | memoryview | None,
    replacement: bytes | bytearray | memoryview,
    faultpoints: Any = None,
) -> bool:
    """Replace a pointer iff its current exact bytes equal ``expected``."""

    path = _as_path(pointer)
    expected_bytes = None if expected is None else _bytes(expected, "expected")
    replacement_bytes = _bytes(replacement, "replacement")
    _validate_safe_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_safe_target(path)

    with _exclusive_target_lock(path):
        _validate_safe_target(path)
        current = path.read_bytes() if path.exists() else None
        if current != expected_bytes:
            return False

        temp_path = _prepare_pointer_temp(path.parent, path.name, replacement_bytes)
        try:
            _faultpoint(faultpoints, "pointer.before_cas")
            if current is None:
                try:
                    os.link(temp_path, path)
                except FileExistsError:
                    return False
                temp_path.unlink()
            else:
                os.replace(temp_path, path)
            _faultpoint(faultpoints, "pointer.after_cas_before_dir_fsync")
            flush_directory(path.parent)
            return True
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


__all__ = [
    "atomic_pointer_cas",
    "durable_create",
    "durable_replace",
    "flush_directory",
    "stable_read",
]
