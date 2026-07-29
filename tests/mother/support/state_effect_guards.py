"""Runtime guards for effect-free STATE-001/002 contract tests."""

from __future__ import annotations

import builtins
from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import socket
import subprocess
import threading
from typing import Any, Iterator


@contextmanager
def forbid_state_owned_effects(monkeypatch: Any, module: Any) -> Iterator[None]:
    """Reject effects that STATE readers and pure builders do not own."""

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("STATE reader or builder attempted an owned effect")

    real_open = builtins.open
    real_os_open = os.open

    def checked_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            forbidden(file, mode)
        return real_open(file, mode, *args, **kwargs)

    def checked_os_open(path: Any, flags: int, *args: Any, **kwargs: Any):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        if flags & write_flags:
            forbidden(path, flags)
        return real_os_open(path, flags, *args, **kwargs)

    # Import providers before disabling lock construction. Some modules create
    # process-local synchronization objects at import time.
    provider_seams = {
        "tools.mother.common.atomic_files": ("synchronized_target",),
        "tools.mother.common.object_store": ("put_immutable", "copy_verified_closure"),
        "tools.mother.common.evidence": ("store_evidence", "export_manifest"),
    }
    providers: list[tuple[Any, tuple[str, ...]]] = []
    for provider_name, seam_names in provider_seams.items():
        try:
            provider = importlib.import_module(provider_name)
        except ModuleNotFoundError:
            continue
        providers.append((provider, seam_names))

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", checked_open)
        guard.setattr(os, "open", checked_os_open)
        for name in ("write", "replace", "rename", "unlink", "remove", "mkdir", "makedirs"):
            guard.setattr(os, name, forbidden)
        for name in (
            "write_text",
            "write_bytes",
            "touch",
            "mkdir",
            "rename",
            "replace",
            "unlink",
        ):
            guard.setattr(Path, name, forbidden)
        guard.setattr(threading, "Lock", forbidden)
        guard.setattr(threading, "RLock", forbidden)
        guard.setattr(subprocess, "run", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(subprocess, "call", forbidden)
        guard.setattr(subprocess, "check_call", forbidden)
        guard.setattr(subprocess, "check_output", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        guard.setattr(socket, "socket", forbidden)

        for name in (
            "synchronized_target",
            "put_immutable",
            "copy_verified_closure",
            "store_evidence",
            "export_manifest",
            "dispatch",
            "dispatch_call",
        ):
            if hasattr(module, name):
                guard.setattr(module, name, forbidden)

        for provider, seam_names in providers:
            for name in seam_names:
                if hasattr(provider, name):
                    guard.setattr(provider, name, forbidden)
        yield
