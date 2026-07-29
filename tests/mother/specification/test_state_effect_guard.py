from __future__ import annotations

from types import SimpleNamespace

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


def test_state_effect_guard_permits_delegated_core012_verified_read(
    monkeypatch,
    tmp_path,
) -> None:
    operation = _operation()
    root = tmp_path / "objects"
    payload = b'{"value":"verified"}'
    object_hash = object_store.put_immutable(root, payload, operation=operation)
    state_module = SimpleNamespace(__name__="tools.mother.common.journal")

    with forbid_state_owned_effects(monkeypatch, state_module):
        assert object_store.get_verified(
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
    state_module = SimpleNamespace(__name__="tools.mother.common.journal")

    with forbid_state_owned_effects(monkeypatch, state_module):
        assert atomic_files.stable_read(
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
