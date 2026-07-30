from __future__ import annotations

from types import ModuleType, SimpleNamespace
import os

import pytest

from tests.mother.support.state_effect_guards import forbid_state_owned_effects
from tools.mother.common import atomic_files, object_store
from tools.mother.common.models import OperationIdentity


def _operation() -> OperationIdentity:
    return OperationIdentity(
        operation_id="state-effect-guard",
        request_id="state-effect-guard-request",
        network="testnet",
        operation_kind="MOTHER-OP-DIAGNOSE",
    )


def _state_framed_module(**bindings) -> ModuleType:
    module = ModuleType("tools.mother.common.journal")
    module.__dict__.update(bindings)
    exec(
        """
def invoke_alias(name, *args, **kwargs):
    return globals()[name](*args, **kwargs)

def invoke_provider(provider_name, name, *args, **kwargs):
    return getattr(globals()[provider_name], name)(*args, **kwargs)
""",
        module.__dict__,
    )
    return module


def test_state_effect_guard_permits_delegated_core012_verified_read(
    monkeypatch,
    tmp_path,
) -> None:
    operation = _operation()
    root = tmp_path / "objects"
    payload = b'{"value":"verified"}'
    object_hash = object_store.put_immutable(root, payload, operation=operation)
    state_module = _state_framed_module(get_verified=object_store.get_verified)

    with forbid_state_owned_effects(monkeypatch, state_module):
        assert state_module.invoke_alias(
            "get_verified",
            root,
            object_hash,
            operation=operation,
        ) == payload


def test_state_effect_guard_still_rejects_unowned_direct_lock(
    monkeypatch,
) -> None:
    import threading

    state_module = SimpleNamespace(__name__="tools.mother.common.journal")
    with forbid_state_owned_effects(monkeypatch, state_module):
        with pytest.raises(
            AssertionError,
            match="STATE reader or builder attempted an owned effect",
        ):
            threading.Lock()


def test_state_effect_guard_permits_delegated_core011_stable_read(
    monkeypatch,
    tmp_path,
) -> None:
    operation = _operation()
    pointer = tmp_path / "head.json"
    pointer.write_bytes(b'{"head":1}')
    state_module = _state_framed_module(stable_read=atomic_files.stable_read)

    with forbid_state_owned_effects(monkeypatch, state_module):
        assert state_module.invoke_alias(
            "stable_read",
            pointer,
            lambda data: data,
            operation=operation,
        ) == b'{"head":1}'


@pytest.mark.parametrize(
    "provider,name",
    (
        (object_store, "put_immutable"),
        (object_store, "copy_verified_closure"),
        (atomic_files, "durable_create"),
        (atomic_files, "durable_replace"),
        (atomic_files, "atomic_pointer_cas"),
    ),
)
@pytest.mark.parametrize("invocation", ("alias", "module-qualified"))
def test_state_effect_guard_rejects_provider_writers_before_publication(
    provider,
    name: str,
    invocation: str,
    monkeypatch,
    tmp_path,
) -> None:
    operation = _operation()
    original = getattr(provider, name)
    state_module = SimpleNamespace(
        __name__="tools.mother.common.journal",
        writer=original,
    )
    pointer = tmp_path / "head.json"
    if name == "atomic_pointer_cas":
        pointer.write_bytes(b"old")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    def invoke(callable_):
        if name == "put_immutable":
            return callable_(tmp_path / "objects", b"payload", operation=operation)
        if name == "copy_verified_closure":
            return callable_(
                tmp_path / "source",
                tmp_path / "destination",
                operation=operation,
                roots=(),
                references={},
                expected_members=(),
            )
        if name in {"durable_create", "durable_replace"}:
            return callable_(tmp_path / "target.bin", b"payload", operation=operation)
        return callable_(
            pointer,
            operation=operation,
            expected=b"old",
            replacement=b"new",
        )

    with forbid_state_owned_effects(monkeypatch, state_module):
        callable_ = (
            state_module.writer
            if invocation == "alias"
            else getattr(provider, name)
        )
        with pytest.raises(
            AssertionError,
            match="STATE reader or builder attempted an owned effect",
        ):
            invoke(callable_)

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize("invocation", ("alias", "module-qualified"))
def test_state_effect_guard_rejects_direct_directory_flush_from_state_frame(
    invocation: str,
    monkeypatch,
    tmp_path,
) -> None:
    state_module = _state_framed_module(
        flush_directory=atomic_files.flush_directory,
        atomic_files_module=atomic_files,
    )
    before = tuple(sorted(tmp_path.iterdir()))
    with forbid_state_owned_effects(monkeypatch, state_module):
        with pytest.raises(
            AssertionError,
            match="STATE reader or builder attempted an owned effect",
        ):
            if invocation == "alias":
                state_module.invoke_alias("flush_directory", tmp_path)
            else:
                state_module.invoke_provider(
                    "atomic_files_module",
                    "flush_directory",
                    tmp_path,
                )
    assert tuple(sorted(tmp_path.iterdir())) == before


@pytest.mark.parametrize("invocation", ("alias", "module-qualified"))
def test_state_effect_guard_rejects_direct_durable_directory_creation_from_state_frame(
    invocation: str,
    monkeypatch,
    tmp_path,
) -> None:
    operation = _operation()
    target = tmp_path / "new" / "nested"
    state_module = _state_framed_module(
        ensure_durable_directory=atomic_files.ensure_durable_directory,
        atomic_files_module=atomic_files,
    )
    with forbid_state_owned_effects(monkeypatch, state_module):
        with pytest.raises(
            AssertionError,
            match="STATE reader or builder attempted an owned effect",
        ):
            if invocation == "alias":
                state_module.invoke_alias(
                    "ensure_durable_directory",
                    target,
                    operation=operation,
                )
            else:
                state_module.invoke_provider(
                    "atomic_files_module",
                    "ensure_durable_directory",
                    target,
                    operation=operation,
                )
    assert not target.exists()


@pytest.mark.parametrize(
    "name",
    tuple(
        candidate
        for candidate in ("fsync", "fdatasync")
        if hasattr(os, candidate)
    ),
)
@pytest.mark.parametrize("invocation", ("alias", "module-qualified"))
def test_state_effect_guard_rejects_direct_file_flush_from_state_frame(
    name: str,
    invocation: str,
    monkeypatch,
    tmp_path,
) -> None:
    target = tmp_path / "flush.bin"
    target.write_bytes(b"unchanged")
    fd = os.open(target, os.O_RDONLY)
    try:
        state_module = _state_framed_module(
            flush=getattr(os, name),
            os_module=os,
        )
        before = target.read_bytes()
        with forbid_state_owned_effects(monkeypatch, state_module):
            with pytest.raises(
                AssertionError,
                match="STATE reader or builder attempted an owned effect",
            ):
                if invocation == "alias":
                    state_module.invoke_alias("flush", fd)
                else:
                    state_module.invoke_provider("os_module", name, fd)
        assert target.read_bytes() == before
    finally:
        os.close(fd)


@pytest.mark.parametrize("name", ("flock", "lockf"))
@pytest.mark.parametrize("invocation", ("alias", "module-qualified"))
def test_state_effect_guard_rejects_direct_posix_lock_from_state_frame(
    name: str,
    invocation: str,
    monkeypatch,
    tmp_path,
) -> None:
    fcntl = pytest.importorskip("fcntl")
    target = tmp_path / "lock.bin"
    target.write_bytes(b"lock")
    fd = os.open(target, os.O_RDWR)
    try:
        state_module = _state_framed_module(
            platform_lock=getattr(fcntl, name),
            fcntl_module=fcntl,
        )
        with forbid_state_owned_effects(monkeypatch, state_module):
            with pytest.raises(
                AssertionError,
                match="STATE reader or builder attempted an owned effect",
            ):
                if invocation == "alias":
                    state_module.invoke_alias(
                        "platform_lock",
                        fd,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                else:
                    state_module.invoke_provider(
                        "fcntl_module",
                        name,
                        fd,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
    finally:
        os.close(fd)


@pytest.mark.parametrize("invocation", ("alias", "module-qualified"))
def test_state_effect_guard_rejects_direct_windows_lock_from_state_frame(
    invocation: str,
    monkeypatch,
    tmp_path,
) -> None:
    msvcrt = pytest.importorskip("msvcrt")
    target = tmp_path / "lock.bin"
    target.write_bytes(b"x")
    fd = os.open(target, os.O_RDWR)
    try:
        state_module = _state_framed_module(
            platform_lock=msvcrt.locking,
            msvcrt_module=msvcrt,
        )
        with forbid_state_owned_effects(monkeypatch, state_module):
            with pytest.raises(
                AssertionError,
                match="STATE reader or builder attempted an owned effect",
            ):
                if invocation == "alias":
                    state_module.invoke_alias(
                        "platform_lock",
                        fd,
                        msvcrt.LK_NBLCK,
                        1,
                    )
                else:
                    state_module.invoke_provider(
                        "msvcrt_module",
                        "locking",
                        fd,
                        msvcrt.LK_NBLCK,
                        1,
                    )
    finally:
        os.close(fd)
