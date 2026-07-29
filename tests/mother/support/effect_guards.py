"""Runtime guards for effect-free Mother contract tests."""

from __future__ import annotations

import builtins
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
import importlib
import os
from pathlib import Path
import socket
import subprocess
import threading
from typing import Any, Iterator


@contextmanager
def forbid_observable_reader_effects(monkeypatch: Any, module: Any) -> Iterator[None]:
    """Fail if a pure reader reaches an observable effect-bearing seam."""

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("pure Wave 1C reader attempted an observable effect")

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

    provider_seams = {
        "tools.mother.common.atomic_files": ("synchronized_target",),
        "tools.mother.common.object_store": ("put_immutable",),
        "tools.mother.common.models": ("EvidenceRef", "ContentHash"),
        "tools.mother.common.evidence": ("store_evidence",),
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
            "store_evidence",
            "dispatch",
            "dispatch_call",
            "EvidenceRef",
            "ContentHash",
        ):
            if hasattr(module, name):
                guard.setattr(module, name, forbidden)

        for provider, seam_names in providers:
            for name in seam_names:
                if hasattr(provider, name):
                    guard.setattr(provider, name, forbidden)
        yield


def assert_no_owned_effect_outputs(value: Any) -> None:
    """Reject effect, evidence, hash, or dispatch ownership in a returned value."""

    for name in (
        "object_hash",
        "content_hash",
        "evidence_ref",
        "evidence_refs",
        "durable_effect_ref",
        "durable_effect_refs",
        "authority_effect",
        "dispatch_result",
        "dispatch_results",
    ):
        assert not hasattr(value, name)

    if not is_dataclass(value):
        return
    for field in fields(value):
        nested = getattr(value, field.name)
        if is_dataclass(nested):
            assert_no_owned_effect_outputs(nested)
        elif isinstance(nested, tuple):
            for item in nested:
                if is_dataclass(item):
                    assert_no_owned_effect_outputs(item)
