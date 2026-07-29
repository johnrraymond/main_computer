"""Runtime guards for effect-free STATE-001/002 contract tests."""

from __future__ import annotations

import builtins
from contextlib import contextmanager
import inspect
import os
from pathlib import Path
import socket
import subprocess
import threading
from typing import Any, Callable, Iterator

from tools.mother.common import atomic_files, object_store


_DELEGATED_READ_ROOTS = frozenset(
    {
        ("tools.mother.common.atomic_files", "stable_read"),
        ("tools.mother.common.object_store", "get_verified"),
    }
)

_PROVIDER_WRITE_ROOTS = frozenset(
    {
        ("tools.mother.common.atomic_files", "durable_create"),
        ("tools.mother.common.atomic_files", "durable_replace"),
        ("tools.mother.common.atomic_files", "atomic_pointer_cas"),
        ("tools.mother.common.object_store", "put_immutable"),
        ("tools.mother.common.object_store", "copy_verified_closure"),
    }
)


def _effect_context(state_module_name: str) -> str | None:
    """Classify the active effect as STATE-owned or an exact delegated read."""

    delegated_read = False
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            module_name = frame.f_globals.get("__name__", "")
            function_name = frame.f_code.co_name
            identity = (module_name, function_name)
            if identity in _PROVIDER_WRITE_ROOTS:
                return "state"
            if identity in _DELEGATED_READ_ROOTS:
                delegated_read = True
            if module_name == state_module_name:
                return "delegated-reader" if delegated_read else "state"
            frame = frame.f_back
        return "delegated-reader" if delegated_read else None
    finally:
        del frame


@contextmanager
def forbid_state_owned_effects(monkeypatch: Any, module: Any) -> Iterator[None]:
    """Reject STATE-owned mutation while permitting exact CORE read delegates."""

    state_module_name = getattr(module, "__name__", "")
    if not state_module_name:
        raise TypeError("module must expose __name__")

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("STATE reader or builder attempted an owned effect")

    def delegated_or_forbidden(
        real: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if _effect_context(state_module_name) == "delegated-reader":
            return real(*args, **kwargs)
        forbidden(*args, **kwargs)

    real_open = builtins.open
    real_os_open = os.open
    real_os_write = os.write
    real_lock = threading.Lock
    real_rlock = threading.RLock
    real_synchronized_target = atomic_files.synchronized_target

    def checked_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            return delegated_or_forbidden(real_open, file, mode, *args, **kwargs)
        return real_open(file, mode, *args, **kwargs)

    def checked_os_open(path: Any, flags: int, *args: Any, **kwargs: Any):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        if flags & write_flags:
            return delegated_or_forbidden(real_os_open, path, flags, *args, **kwargs)
        return real_os_open(path, flags, *args, **kwargs)

    def checked_os_write(fd: int, data: bytes) -> int:
        return delegated_or_forbidden(real_os_write, fd, data)

    def checked_lock(*args: Any, **kwargs: Any):
        return delegated_or_forbidden(real_lock, *args, **kwargs)

    def checked_rlock(*args: Any, **kwargs: Any):
        return delegated_or_forbidden(real_rlock, *args, **kwargs)

    def guarded_function(real: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return delegated_or_forbidden(real, *args, **kwargs)

        return wrapper

    def checked_synchronized_target(*args: Any, **kwargs: Any):
        return delegated_or_forbidden(real_synchronized_target, *args, **kwargs)

    path_mutators = {
        name: getattr(Path, name)
        for name in (
            "write_text",
            "write_bytes",
            "touch",
            "mkdir",
            "rename",
            "replace",
            "unlink",
        )
    }
    os_mutators = {
        name: getattr(os, name)
        for name in (
            "replace",
            "rename",
            "unlink",
            "remove",
            "mkdir",
            "makedirs",
        )
    }

    provider_writers = {
        object_store: ("put_immutable", "copy_verified_closure"),
        atomic_files: ("durable_create", "durable_replace", "atomic_pointer_cas"),
    }
    original_provider_writers = tuple(
        getattr(provider, name)
        for provider, names in provider_writers.items()
        for name in names
    )

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", checked_open)
        guard.setattr(os, "open", checked_os_open)
        guard.setattr(os, "write", checked_os_write)
        for name, real in os_mutators.items():
            guard.setattr(os, name, guarded_function(real))
        for name, real in path_mutators.items():
            guard.setattr(Path, name, guarded_function(real))
        guard.setattr(threading, "Lock", checked_lock)
        guard.setattr(threading, "RLock", checked_rlock)
        guard.setattr(subprocess, "run", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(subprocess, "call", forbidden)
        guard.setattr(subprocess, "check_call", forbidden)
        guard.setattr(subprocess, "check_output", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        guard.setattr(socket, "socket", forbidden)

        # Synchronization is permitted only while executing an exact delegated
        # stable_read/get_verified call. Direct STATE use remains prohibited.
        guard.setattr(atomic_files, "synchronized_target", checked_synchronized_target)

        # Provider mutation is prohibited even when invoked module-qualified.
        for provider, names in provider_writers.items():
            for name in names:
                guard.setattr(provider, name, forbidden)

        # Direct aliases imported by the STATE module are prohibited too.
        for attribute, value in tuple(vars(module).items()):
            if any(value is original for original in original_provider_writers) or attribute in {
                "synchronized_target",
                "durable_create",
                "durable_replace",
                "atomic_pointer_cas",
                "put_immutable",
                "copy_verified_closure",
                "store_evidence",
                "export_manifest",
                "dispatch",
                "dispatch_call",
            }:
                guard.setattr(module, attribute, forbidden)
        yield
