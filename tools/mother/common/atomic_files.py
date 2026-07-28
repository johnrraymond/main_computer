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

from . import faultpoints as faultpoint_module
from .errors import MotherError
from .models import ContentHash, DurableEffectRef, OperationIdentity


_T = TypeVar("_T")
_MODULE_ID = "MOTHER-OFM-CORE-011"

_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


def _operation(value: OperationIdentity) -> OperationIdentity:
    if not isinstance(value, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    return value


def _content_hash(data: bytes) -> ContentHash:
    return ContentHash(algorithm="sha256", digest=hashlib.sha256(data).hexdigest())


def _effect_ref(effect_kind: str, target: Path, data: bytes) -> DurableEffectRef:
    return DurableEffectRef(
        effect_kind=effect_kind,
        target=str(target),
        content_hash=_content_hash(data),
    )


def _mother_error(
    operation: OperationIdentity,
    code: str,
    message: str,
    *,
    retry_class: str,
    authority_effect: str = "none",
    durable_effect_refs: tuple[DurableEffectRef, ...] = (),
    cause: BaseException | None = None,
) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=operation.operation_id,
        module_id=_MODULE_ID,
        retry_class=retry_class,
        authority_effect=authority_effect,
        durable_effect_refs=durable_effect_refs,
        cause_class="" if cause is None else type(cause).__name__,
    )


def _as_path(value: str | os.PathLike[str]) -> Path:
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("path contains a NUL byte")
        path = Path(value)
    elif isinstance(value, os.PathLike):
        path = Path(value)
    else:
        raise TypeError("path must be a string or path-like value")
    return path.absolute()


def _existing_symlink_component(path: Path) -> Path | None:
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
            return current
    return None


def _unsafe_path(
    operation: OperationIdentity,
    message: str,
    cause: BaseException | None = None,
) -> MotherError:
    return _mother_error(
        operation,
        "MOTHER_INPUT_UNSAFE_PATH",
        message,
        retry_class="never",
        cause=cause,
    )


def _validate_safe_path(path: Path, operation: OperationIdentity) -> None:
    unsafe = _existing_symlink_component(path)
    if unsafe is not None:
        raise _unsafe_path(operation, f"unsafe symlink path component: {unsafe}")

    current = Path(path.parts[0]) if path.parts else path
    for part in path.parts[1:-1]:
        current = current / part
        try:
            exists = current.exists()
        except OSError as exc:
            raise _unsafe_path(
                operation,
                f"cannot inspect durable path component: {current}",
                exc,
            ) from exc
        if exists and not current.is_dir():
            raise _unsafe_path(
                operation,
                f"durable path component is not a directory: {current}",
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
    __slots__ = ("_fd", "_thread_lock", "_lock_path", "_operation")

    def __init__(self, path: Path, operation: OperationIdentity) -> None:
        key = _lock_key(path)
        lock_root = Path(tempfile.gettempdir()) / "main_computer_mother_locks"
        self._lock_path = lock_root / f"{key}.lock"
        self._thread_lock = _thread_lock(key)
        self._fd: int | None = None
        self._operation = operation

    def __enter__(self) -> "_ExclusiveTargetLock":
        self._thread_lock.acquire()
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            _acquire_platform_lock(self._fd)
        except BaseException as exc:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._thread_lock.release()
            if isinstance(exc, MotherError):
                raise
            raise _mother_error(
                self._operation,
                "MOTHER_STATE_DURABLE_WRITE_FAILED",
                "failed to acquire the cross-process durable-target lock",
                retry_class="same-request",
                cause=exc,
            ) from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        release_error: BaseException | None = None
        try:
            if self._fd is not None:
                try:
                    _release_platform_lock(self._fd)
                except BaseException as caught:
                    release_error = caught
                finally:
                    os.close(self._fd)
                    self._fd = None
        finally:
            self._thread_lock.release()
        if release_error is not None and exc is None:
            raise _mother_error(
                self._operation,
                "MOTHER_STATE_DURABLE_WRITE_FAILED",
                "failed to release the cross-process durable-target lock",
                retry_class="same-request",
                cause=release_error,
            ) from release_error
        return False


def _exclusive_target_lock(
    path: Path,
    operation: OperationIdentity,
) -> _ExclusiveTargetLock:
    return _ExclusiveTargetLock(path, operation)


def flush_directory(path: str | os.PathLike[str]) -> None:
    """Durably flush a directory using the host platform's native primitive."""

    directory = _as_path(path)
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


def ensure_durable_directory(
    path: str | os.PathLike[str],
    *,
    operation: OperationIdentity,
    reconcile_existing: bool = False,
) -> Path:
    """Create each missing directory level and durably commit each parent entry."""

    op = _operation(operation)
    directory = _as_path(path)
    _validate_safe_path(directory, op)

    missing: list[Path] = []
    cursor = directory
    while True:
        try:
            exists = cursor.exists()
        except OSError as exc:
            raise _unsafe_path(op, f"cannot inspect directory path: {cursor}", exc) from exc
        if exists:
            if not cursor.is_dir():
                raise _unsafe_path(op, f"directory path is occupied by a file: {cursor}")
            break
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise _unsafe_path(op, f"no existing directory ancestor for: {directory}")
        cursor = parent

    for child in reversed(missing):
        try:
            os.mkdir(child)
        except FileExistsError:
            if not child.is_dir() or child.is_symlink():
                raise _unsafe_path(op, f"directory path is unsafe: {child}")
        except OSError as exc:
            raise _mother_error(
                op,
                "MOTHER_STATE_DURABLE_WRITE_FAILED",
                f"failed to create durable directory: {child}",
                retry_class="same-request",
                cause=exc,
            ) from exc
        try:
            flush_directory(child.parent)
        except OSError as exc:
            ref = _effect_ref("local-directory-creation", child, b"directory")
            raise _mother_error(
                op,
                "MOTHER_STATE_DURABILITY_UNCONFIRMED",
                f"directory entry exists but parent durability is unconfirmed: {child}",
                retry_class="after-reobserve",
                authority_effect="local-pointer-determined",
                durable_effect_refs=(ref,),
                cause=exc,
            ) from exc

    if reconcile_existing and not missing and directory.parent != directory:
        try:
            flush_directory(directory.parent)
        except OSError as exc:
            ref = _effect_ref("local-directory-creation", directory, b"directory")
            raise _mother_error(
                op,
                "MOTHER_STATE_DURABILITY_UNCONFIRMED",
                f"directory exists but parent durability is unconfirmed: {directory}",
                retry_class="after-reobserve",
                authority_effect="local-pointer-determined",
                durable_effect_refs=(ref,),
                cause=exc,
            ) from exc
    return directory


def _prepare_temp(
    parent: Path,
    target_name: str,
    data: bytes,
    controller: Any,
    operation: OperationIdentity,
) -> Path:
    try:
        fd, raw_path = tempfile.mkstemp(
            prefix=f".{target_name}.mother-",
            suffix=".tmp",
            dir=parent,
        )
    except OSError as exc:
        raise _mother_error(
            operation,
            "MOTHER_STATE_DURABLE_WRITE_FAILED",
            "failed to create the same-directory temporary file",
            retry_class="same-request",
            cause=exc,
        ) from exc

    temp_path = Path(raw_path)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            _faultpoint(controller, "immutable.after_temp_write")
            os.fsync(handle.fileno())
        _faultpoint(controller, "immutable.after_file_fsync")
        try:
            reread = temp_path.read_bytes()
        except OSError as exc:
            raise _mother_error(
                operation,
                "MOTHER_STATE_DURABLE_WRITE_FAILED",
                "failed to reread the flushed temporary file",
                retry_class="same-request",
                cause=exc,
            ) from exc
        if reread != data or _content_hash(reread) != _content_hash(data):
            raise _mother_error(
                operation,
                "MOTHER_STATE_DURABLE_WRITE_FAILED",
                "flushed temporary bytes failed exact reread/hash verification",
                retry_class="same-request",
            )
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return temp_path


def _publication_unconfirmed(
    operation: OperationIdentity,
    target: Path,
    data: bytes,
    *,
    effect_kind: str,
    cause: BaseException,
) -> MotherError:
    return _mother_error(
        operation,
        "MOTHER_STATE_DURABILITY_UNCONFIRMED",
        "publication completed but parent-directory durability is unconfirmed",
        retry_class="after-reobserve",
        authority_effect="local-pointer-determined",
        durable_effect_refs=(_effect_ref(effect_kind, target, data),),
        cause=cause,
    )


def stable_read(
    pointer: str | os.PathLike[str],
    load: Callable[[bytes], _T],
    *,
    operation: OperationIdentity,
    max_attempts: int = 3,
) -> _T:
    """Load a view only when pointer bytes are stable around the load."""

    op = _operation(operation)
    if not callable(load):
        raise TypeError("load must be callable")
    if type(max_attempts) is not int or max_attempts <= 0:
        raise ValueError("max_attempts must be a positive integer")
    try:
        path = _as_path(pointer)
    except ValueError as exc:
        raise _unsafe_path(op, str(exc), exc) from exc
    _validate_safe_path(path, op)
    if path.exists() and not path.is_file():
        raise _unsafe_path(op, f"stable-read target is not a regular file: {path}")

    for _attempt in range(max_attempts):
        try:
            before = path.read_bytes()
        except FileNotFoundError as exc:
            raise _mother_error(
                op,
                "MOTHER_STATE_DURABLE_TARGET_MISSING",
                "stable-read pointer is absent",
                retry_class="after-reobserve",
                cause=exc,
            ) from exc
        except OSError as exc:
            raise _mother_error(
                op,
                "MOTHER_STATE_DURABLE_READ_FAILED",
                "failed to read the stable-read pointer",
                retry_class="after-reobserve",
                cause=exc,
            ) from exc
        loaded = load(before)
        try:
            after = path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _mother_error(
                op,
                "MOTHER_STATE_DURABLE_READ_FAILED",
                "failed to reread the stable-read pointer",
                retry_class="after-reobserve",
                cause=exc,
            ) from exc
        if before == after:
            return loaded

    raise _mother_error(
        op,
        "MOTHER_STATE_UNSTABLE_READ",
        "pointer changed during every bounded stable-read attempt",
        retry_class="after-reobserve",
    )


def durable_create(
    target: str | os.PathLike[str],
    data: bytes | bytearray | memoryview,
    *,
    operation: OperationIdentity,
    faultpoints: Any = None,
) -> None:
    """Publish exact bytes only when the target is absent."""

    op = _operation(operation)
    try:
        path = _as_path(target)
    except ValueError as exc:
        raise _unsafe_path(op, str(exc), exc) from exc
    payload = _bytes(data, "data")
    _validate_safe_path(path, op)
    ensure_durable_directory(path.parent, operation=op)
    _validate_safe_path(path, op)

    with _exclusive_target_lock(path, op):
        _validate_safe_path(path, op)
        if path.exists():
            raise _mother_error(
                op,
                "MOTHER_CONFLICT_DURABLE_TARGET_EXISTS",
                "durable target already exists",
                retry_class="after-reobserve",
            )

        temp_path = _prepare_temp(path.parent, path.name, payload, faultpoints, op)
        published = False
        try:
            try:
                os.link(temp_path, path)
                published = True
            except FileExistsError as exc:
                raise _mother_error(
                    op,
                    "MOTHER_CONFLICT_DURABLE_TARGET_EXISTS",
                    "durable target was published by a competing writer",
                    retry_class="after-reobserve",
                    cause=exc,
                ) from exc
            except OSError as exc:
                raise _mother_error(
                    op,
                    "MOTHER_STATE_DURABLE_WRITE_FAILED",
                    "failed to publish the durable target",
                    retry_class="same-request",
                    cause=exc,
                ) from exc
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            _faultpoint(faultpoints, "immutable.after_publish_before_dir_fsync")
            try:
                flush_directory(path.parent)
            except OSError as exc:
                raise _publication_unconfirmed(
                    op,
                    path,
                    payload,
                    effect_kind="local-file-publication",
                    cause=exc,
                ) from exc
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass


def durable_replace(
    target: str | os.PathLike[str],
    data: bytes | bytearray | memoryview,
    *,
    operation: OperationIdentity,
    faultpoints: Any = None,
) -> None:
    """Atomically replace or create a durable target with exact bytes."""

    op = _operation(operation)
    try:
        path = _as_path(target)
    except ValueError as exc:
        raise _unsafe_path(op, str(exc), exc) from exc
    payload = _bytes(data, "data")
    _validate_safe_path(path, op)
    ensure_durable_directory(path.parent, operation=op)
    _validate_safe_path(path, op)

    with _exclusive_target_lock(path, op):
        _validate_safe_path(path, op)
        temp_path = _prepare_temp(path.parent, path.name, payload, faultpoints, op)
        try:
            try:
                os.replace(temp_path, path)
            except OSError as exc:
                raise _mother_error(
                    op,
                    "MOTHER_STATE_DURABLE_WRITE_FAILED",
                    "failed to replace the durable target",
                    retry_class="same-request",
                    cause=exc,
                ) from exc
            _faultpoint(faultpoints, "immutable.after_publish_before_dir_fsync")
            try:
                flush_directory(path.parent)
            except OSError as exc:
                raise _publication_unconfirmed(
                    op,
                    path,
                    payload,
                    effect_kind="local-file-publication",
                    cause=exc,
                ) from exc
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _prepare_pointer_temp(
    parent: Path,
    target_name: str,
    data: bytes,
    operation: OperationIdentity,
) -> Path:
    return _prepare_temp(parent, target_name, data, None, operation)


def atomic_pointer_cas(
    pointer: str | os.PathLike[str],
    *,
    operation: OperationIdentity,
    expected: bytes | bytearray | memoryview | None,
    replacement: bytes | bytearray | memoryview,
    faultpoints: Any = None,
) -> bool:
    """Replace a pointer iff its current exact bytes equal ``expected``."""

    op = _operation(operation)
    try:
        path = _as_path(pointer)
    except ValueError as exc:
        raise _unsafe_path(op, str(exc), exc) from exc
    expected_bytes = None if expected is None else _bytes(expected, "expected")
    replacement_bytes = _bytes(replacement, "replacement")
    _validate_safe_path(path, op)
    ensure_durable_directory(path.parent, operation=op)
    _validate_safe_path(path, op)

    with _exclusive_target_lock(path, op):
        _validate_safe_path(path, op)
        try:
            current = path.read_bytes() if path.exists() else None
        except OSError as exc:
            raise _mother_error(
                op,
                "MOTHER_STATE_DURABLE_READ_FAILED",
                "failed to read the pointer predecessor",
                retry_class="after-reobserve",
                cause=exc,
            ) from exc
        if current != expected_bytes:
            return False

        temp_path = _prepare_pointer_temp(path.parent, path.name, replacement_bytes, op)
        try:
            _faultpoint(faultpoints, "pointer.before_cas")
            try:
                if current is None:
                    try:
                        os.link(temp_path, path)
                    except FileExistsError:
                        return False
                    temp_path.unlink()
                else:
                    os.replace(temp_path, path)
            except OSError as exc:
                raise _mother_error(
                    op,
                    "MOTHER_STATE_DURABLE_WRITE_FAILED",
                    "failed to publish the pointer compare-and-swap result",
                    retry_class="same-request",
                    cause=exc,
                ) from exc
            _faultpoint(faultpoints, "pointer.after_cas_before_dir_fsync")
            try:
                flush_directory(path.parent)
            except OSError as exc:
                raise _publication_unconfirmed(
                    op,
                    path,
                    replacement_bytes,
                    effect_kind="local-pointer-publication",
                    cause=exc,
                ) from exc
            return True
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass


__all__ = [
    "atomic_pointer_cas",
    "durable_create",
    "durable_replace",
    "ensure_durable_directory",
    "flush_directory",
    "stable_read",
]
