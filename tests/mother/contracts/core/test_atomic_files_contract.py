from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing as mp
import os
from pathlib import Path
from threading import Barrier, Event, Lock
from typing import Any

import pytest

from tests.mother.support.implementation import require_mother_module
from tests.mother.support.wave1b_process_workers import (
    durable_create_worker,
    pointer_cas_worker,
)


def _atomic_files():
    return require_mother_module(
        "common.atomic_files",
        "MOTHER-OFM-CORE-011",
        phase="WAVE1B",
    )


def _errors():
    return require_mother_module(
        "common.errors",
        "MOTHER-OFM-CORE-002",
        phase="WAVE1A",
    )


def _faultpoints():
    return require_mother_module(
        "common.faultpoints",
        "MOTHER-OFM-CORE-013",
        phase="WAVE1A",
    )


def _models():
    return require_mother_module(
        "common.models",
        "MOTHER-OFM-CORE-001",
        phase="WAVE1A",
    )


def _operation():
    models = _models()
    return models.OperationIdentity(
        operation_id="MOTHER-TEST-WAVE1B-OPERATION",
        request_id="MOTHER-TEST-WAVE1B-REQUEST",
        network="test-network",
        operation_kind="MOTHER-OP-SYNC-STATE",
    )


def _assert_error(
    caught: pytest.ExceptionInfo[BaseException],
    *,
    code: str,
    retry_class: str,
    authority_effect: str,
) -> None:
    errors = _errors()
    assert isinstance(caught.value, errors.MotherError)
    assert caught.value.code == code
    assert caught.value.retry_class == retry_class
    assert caught.value.authority_effect == authority_effect
    assert caught.value.operation_id == _operation().operation_id


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-001"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestStableRead:
    def test_returns_only_a_view_loaded_between_identical_pointer_reads(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        pointer = tmp_path / "head.json"
        pointer.write_bytes(b'{"generation":1}\n')
        calls: list[bytes] = []

        def load(pointer_bytes: bytes) -> dict[str, int]:
            calls.append(pointer_bytes)
            return {"loaded_generation": 1}

        result = atomic_files.stable_read(pointer, load, max_attempts=2, operation=_operation())

        assert result == {"loaded_generation": 1}
        assert calls == [b'{"generation":1}\n']

    def test_retries_the_complete_sequence_after_pointer_change(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        pointer = tmp_path / "head.json"
        pointer.write_bytes(b"generation-1\n")
        calls: list[bytes] = []

        def load(pointer_bytes: bytes) -> bytes:
            calls.append(pointer_bytes)
            if len(calls) == 1:
                pointer.write_bytes(b"generation-2\n")
            return b"objects-for:" + pointer_bytes

        result = atomic_files.stable_read(pointer, load, max_attempts=3, operation=_operation())

        assert calls == [b"generation-1\n", b"generation-2\n"]
        assert result == b"objects-for:generation-2\n"

    def test_exhaustion_returns_exact_typed_unstable_read(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        pointer = tmp_path / "head.json"
        pointer.write_bytes(b"generation-0\n")
        count = 0

        def load(pointer_bytes: bytes) -> bytes:
            nonlocal count
            count += 1
            pointer.write_bytes(f"generation-{count}\n".encode())
            return b"partial:" + pointer_bytes

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.stable_read(pointer, load, max_attempts=2, operation=_operation())

        _assert_error(
            caught,
            code="MOTHER_STATE_UNSTABLE_READ",
            retry_class="after-reobserve",
            authority_effect="none",
        )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-018"],
    operations=["MOTHER-OP-SYNC-STATE"],
    functionalities=["MOTHER-OF-SYNC-005"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestDurableCreateAndReplace:
    def test_durable_create_existing_target_is_exact_conflict(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        target = tmp_path / "activation-prepared.json"
        target.write_bytes(b"predecessor")

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_create(target, b"successor", operation=_operation())

        _assert_error(
            caught,
            code="MOTHER_CONFLICT_DURABLE_TARGET_EXISTS",
            retry_class="after-reobserve",
            authority_effect="none",
        )
        assert target.read_bytes() == b"predecessor"

    def test_durable_create_publishes_exact_bytes_and_cleans_temporary_siblings(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        target = tmp_path / "activation-prepared.json"

        atomic_files.durable_create(target, b"exact-bytes\n", operation=_operation())

        assert target.read_bytes() == b"exact-bytes\n"
        assert [path for path in tmp_path.iterdir() if path != target] == []

    def test_replace_uses_same_directory_temp_and_flush_directory_abstraction(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        target = tmp_path / "head.json"
        target.write_bytes(b"old")
        events: list[tuple[str, Path]] = []

        real_replace = atomic_files.os.replace

        def recording_replace(source: os.PathLike[str] | str, destination: os.PathLike[str] | str) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            assert source_path.parent == destination_path.parent == target.parent
            events.append(("publish", source_path))
            real_replace(source, destination)

        def recording_flush_directory(path: os.PathLike[str] | str) -> None:
            events.append(("flush-directory", Path(path)))

        monkeypatch.setattr(atomic_files.os, "replace", recording_replace)
        monkeypatch.setattr(
            atomic_files,
            "flush_directory",
            recording_flush_directory,
        )

        atomic_files.durable_replace(target, b"new", operation=_operation())

        assert target.read_bytes() == b"new"
        assert [label for label, _ in events] == ["publish", "flush-directory"]
        assert events[-1][1] == target.parent

    @pytest.mark.parametrize(
        "name",
        [
            "immutable.after_temp_write",
            "immutable.after_file_fsync",
        ],
    )
    def test_interruption_before_publish_preserves_predecessor(
        self,
        tmp_path: Path,
        name: str,
    ) -> None:
        atomic_files = _atomic_files()
        faultpoints = _faultpoints()
        target = tmp_path / "head.json"
        target.write_bytes(b"predecessor")
        controller = faultpoints.FaultpointController.injected({name: 1})

        with pytest.raises(faultpoints.SimulatedInterruption) as caught:
            atomic_files.durable_replace(
                target,
                b"successor",
                faultpoints=controller,
            operation=_operation(),
        )

        assert caught.value.faultpoint == name
        assert caught.value.hit_number == 1
        assert target.read_bytes() == b"predecessor"

    def test_interruption_after_publish_is_forward_reconcilable(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        faultpoints = _faultpoints()
        target = tmp_path / "head.json"
        target.write_bytes(b"predecessor")
        name = "immutable.after_publish_before_dir_fsync"
        controller = faultpoints.FaultpointController.injected({name: 1})

        with pytest.raises(faultpoints.SimulatedInterruption) as caught:
            atomic_files.durable_replace(
                target,
                b"successor",
                faultpoints=controller,
            operation=_operation(),
        )

        assert caught.value.faultpoint == name
        assert target.read_bytes() == b"successor"
        atomic_files.durable_replace(target, b"successor", operation=_operation())
        assert target.read_bytes() == b"successor"

    def test_durable_publication_uses_only_normative_faultpoint_sequence(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        faultpoints = _faultpoints()
        target = tmp_path / "record.json"
        controller = faultpoints.FaultpointController.injected({})

        atomic_files.durable_create(target, b"value", faultpoints=controller, operation=_operation())

        assert controller.hit_count("immutable.after_temp_write") == 1
        assert controller.hit_count("immutable.after_file_fsync") == 1
        assert controller.hit_count("immutable.after_publish_before_dir_fsync") == 1

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
    def test_symlink_target_is_exact_unsafe_path_error(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        referent = tmp_path / "referent"
        referent.write_bytes(b"safe")
        link = tmp_path / "link"
        link.symlink_to(referent)

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_replace(link, b"unsafe", operation=_operation())

        _assert_error(
            caught,
            code="MOTHER_INPUT_UNSAFE_PATH",
            retry_class="never",
            authority_effect="none",
        )
        assert referent.read_bytes() == b"safe"
        assert link.is_symlink()

    def test_simultaneous_competing_creates_have_one_publication_winner(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        target = tmp_path / "immutable"
        barrier = Barrier(2)

        def publish(payload: bytes) -> tuple[str, Any]:
            barrier.wait()
            try:
                atomic_files.durable_create(target, payload, operation=_operation())
                return ("winner", payload)
            except errors.MotherError as exc:
                return ("error", exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, (b"first", b"second")))

        winners = [value for kind, value in results if kind == "winner"]
        losers = [value for kind, value in results if kind == "error"]
        assert len(winners) == 1
        assert len(losers) == 1
        assert losers[0].code == "MOTHER_CONFLICT_DURABLE_TARGET_EXISTS"
        assert losers[0].authority_effect == "none"
        assert target.read_bytes() == winners[0]
        assert [path for path in tmp_path.iterdir() if path != target] == []


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-019"],
    operations=["MOTHER-OP-REPAIR-PROJECTIONS"],
    functionalities=["MOTHER-OF-PRJ-005"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestAtomicPointerCas:
    def test_matching_predecessor_is_replaced_atomically(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        pointer = tmp_path / "current"
        pointer.write_bytes(b"generation-1\n")

        changed = atomic_files.atomic_pointer_cas(
            pointer,
            expected=b"generation-1\n",
            replacement=b"generation-2\n",
        operation=_operation(),
    )

        assert changed is True
        assert pointer.read_bytes() == b"generation-2\n"

    def test_predecessor_mismatch_performs_no_overwrite_or_temp_publish(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        pointer = tmp_path / "current"
        pointer.write_bytes(b"generation-2\n")

        changed = atomic_files.atomic_pointer_cas(
            pointer,
            expected=b"generation-1\n",
            replacement=b"generation-3\n",
        operation=_operation(),
    )

        assert changed is False
        assert pointer.read_bytes() == b"generation-2\n"
        assert [path for path in tmp_path.iterdir() if path != pointer] == []

    def test_absent_predecessor_can_be_created_only_with_expected_none(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        pointer = tmp_path / "current"

        assert atomic_files.atomic_pointer_cas(
            pointer,
            expected=b"missing",
            replacement=b"generation-1\n",
            operation=_operation(),
        ) is False
        assert not pointer.exists()
        assert atomic_files.atomic_pointer_cas(
            pointer,
            expected=None,
            replacement=b"generation-1\n",
            operation=_operation(),
        ) is True
        assert pointer.read_bytes() == b"generation-1\n"

    @pytest.mark.parametrize(
        "name",
        [
            "pointer.before_cas",
            "pointer.after_cas_before_dir_fsync",
        ],
    )
    def test_pointer_interruptions_are_exact_and_reconcilable(
        self,
        tmp_path: Path,
        name: str,
    ) -> None:
        atomic_files = _atomic_files()
        faultpoints = _faultpoints()
        pointer = tmp_path / "current"
        pointer.write_bytes(b"generation-1\n")
        controller = faultpoints.FaultpointController.injected({name: 1})

        with pytest.raises(faultpoints.SimulatedInterruption) as caught:
            atomic_files.atomic_pointer_cas(
                pointer,
                expected=b"generation-1\n",
                replacement=b"generation-2\n",
                faultpoints=controller,
            operation=_operation(),
        )

        assert caught.value.faultpoint == name
        if name == "pointer.before_cas":
            assert pointer.read_bytes() == b"generation-1\n"
        else:
            assert pointer.read_bytes() == b"generation-2\n"

    def test_simultaneous_cas_from_same_predecessor_has_exactly_one_winner(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        pointer = tmp_path / "current"
        pointer.write_bytes(b"generation-1\n")
        barrier = Barrier(2)
        result_lock = Lock()

        def attempt(replacement: bytes) -> tuple[bool, bytes]:
            barrier.wait()
            changed = atomic_files.atomic_pointer_cas(
                pointer,
                expected=b"generation-1\n",
                replacement=replacement,
            operation=_operation(),
        )
            with result_lock:
                observed = pointer.read_bytes()
            return changed, observed

        replacements = (b"generation-2\n", b"generation-3\n")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, replacements))

        assert sum(changed for changed, _ in results) == 1
        assert pointer.read_bytes() in replacements
        assert [path for path in tmp_path.iterdir() if path != pointer] == []

def _collect_spawned_results(processes, ready_queue, start_event, result_queue):
    ready = [ready_queue.get(timeout=20) for _ in processes]
    start_event.set()
    results = [result_queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return ready, results


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-018"],
    operations=["MOTHER-OP-SYNC-STATE"],
    functionalities=["MOTHER-OF-SYNC-005"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestSpawnedDurableCreate:
    def test_competing_processes_have_one_exclusive_publication_winner(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        assert hasattr(atomic_files, "durable_create")
        target = tmp_path / "immutable"
        context = mp.get_context("spawn")
        ready_queue = context.Queue()
        result_queue = context.Queue()
        start_event = context.Event()
        payloads = (b"process-first", b"process-second")
        processes = [
            context.Process(
                target=durable_create_worker,
                args=(
                    str(target),
                    payload,
                    ready_queue,
                    start_event,
                    result_queue,
                ),
            )
            for payload in payloads
        ]
        for process in processes:
            process.start()

        ready, results = _collect_spawned_results(
            processes,
            ready_queue,
            start_event,
            result_queue,
        )

        assert ready == [{"absent": True}, {"absent": True}]
        winners = [result for result in results if result["status"] == "ok"]
        losers = [result for result in results if result["status"] == "error"]
        assert len(winners) == 1
        assert len(losers) == 1
        assert losers[0] == {
            "status": "error",
            "code": "MOTHER_CONFLICT_DURABLE_TARGET_EXISTS",
            "retry_class": "after-reobserve",
            "authority_effect": "none",
        }
        assert target.read_bytes() == winners[0]["payload"]
        assert [path for path in tmp_path.iterdir() if path != target] == []


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-019"],
    operations=["MOTHER-OP-REPAIR-PROJECTIONS"],
    functionalities=["MOTHER-OF-PRJ-005"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestSpawnedAtomicPointerCas:
    def test_same_predecessor_has_exactly_one_cross_process_winner(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        assert hasattr(atomic_files, "atomic_pointer_cas")
        pointer = tmp_path / "current"
        predecessor = b"generation-1\n"
        replacements = (b"generation-2\n", b"generation-3\n")
        pointer.write_bytes(predecessor)

        context = mp.get_context("spawn")
        ready_queue = context.Queue()
        result_queue = context.Queue()
        start_event = context.Event()
        processes = [
            context.Process(
                target=pointer_cas_worker,
                args=(
                    str(pointer),
                    predecessor,
                    replacement,
                    ready_queue,
                    start_event,
                    result_queue,
                ),
            )
            for replacement in replacements
        ]
        for process in processes:
            process.start()

        ready, results = _collect_spawned_results(
            processes,
            ready_queue,
            start_event,
            result_queue,
        )

        assert ready == [
            {"observed": predecessor},
            {"observed": predecessor},
        ]
        assert all(result["status"] == "ok" for result in results)
        assert sum(bool(result["changed"]) for result in results) == 1
        winning = next(result["replacement"] for result in results if result["changed"])
        assert pointer.read_bytes() == winning
        assert [path for path in tmp_path.iterdir() if path != pointer] == []



@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-001"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestStableReadErrorBoundaryHardening:
    def test_missing_pointer_is_typed_and_carries_real_operation_identity(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        pointer = tmp_path / "missing-head.json"

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.stable_read(
                pointer,
                lambda payload: payload,
                operation=_operation(),
            )

        _assert_error(
            caught,
            code="MOTHER_STATE_DURABLE_TARGET_MISSING",
            retry_class="after-reobserve",
            authority_effect="none",
        )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-018"],
    operations=["MOTHER-OP-SYNC-STATE"],
    functionalities=["MOTHER-OF-SYNC-005"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestDurabilityErrorBoundaryHardening:
    def test_file_where_parent_directory_is_required_is_exact_unsafe_path(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        parent = tmp_path / "not-a-directory"
        parent.write_bytes(b"occupied")
        target = parent / "object"

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_create(
                target,
                b"payload",
                operation=_operation(),
            )

        _assert_error(
            caught,
            code="MOTHER_INPUT_UNSAFE_PATH",
            retry_class="never",
            authority_effect="none",
        )
        assert parent.read_bytes() == b"occupied"

    def test_prepublication_host_failure_is_typed_with_no_authority_effect(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        target = tmp_path / "immutable"

        def denied_link(*_args: Any, **_kwargs: Any) -> None:
            raise PermissionError("publication denied")

        monkeypatch.setattr(atomic_files.os, "link", denied_link)

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_create(
                target,
                b"payload",
                operation=_operation(),
            )

        _assert_error(
            caught,
            code="MOTHER_STATE_DURABLE_WRITE_FAILED",
            retry_class="same-request",
            authority_effect="none",
        )
        assert caught.value.durable_effect_refs == ()
        assert not target.exists()

    def test_postpublication_flush_failure_reports_typed_durable_effect(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        models = _models()
        target = tmp_path / "head.json"
        target.write_bytes(b"old")

        def fail_flush(_path: os.PathLike[str] | str) -> None:
            raise OSError("directory flush failed")

        monkeypatch.setattr(atomic_files, "flush_directory", fail_flush)

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_replace(
                target,
                b"new",
                operation=_operation(),
            )

        _assert_error(
            caught,
            code="MOTHER_STATE_DURABILITY_UNCONFIRMED",
            retry_class="after-reobserve",
            authority_effect="local-pointer-determined",
        )
        assert target.read_bytes() == b"new"
        assert len(caught.value.durable_effect_refs) == 1
        effect = caught.value.durable_effect_refs[0]
        assert isinstance(effect, models.DurableEffectRef)
        assert effect.effect_kind == "local-file-publication"
        assert effect.target == str(target.absolute())
        assert effect.content_hash.digest == atomic_files.hashlib.sha256(b"new").hexdigest()

    def test_every_new_directory_ancestor_is_committed_through_its_parent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        target = tmp_path / "objects" / "sha256" / "ab" / "value"
        flushed: list[Path] = []

        def record_flush(path: os.PathLike[str] | str) -> None:
            flushed.append(Path(path))

        monkeypatch.setattr(atomic_files, "flush_directory", record_flush)

        atomic_files.durable_create(
            target,
            b"payload",
            operation=_operation(),
        )

        assert target.read_bytes() == b"payload"
        assert flushed == [
            tmp_path,
            tmp_path / "objects",
            tmp_path / "objects" / "sha256",
            tmp_path / "objects" / "sha256" / "ab",
        ]


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-018"],
    operations=["MOTHER-OP-SYNC-STATE"],
    functionalities=["MOTHER-OF-SYNC-005"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestPostpublicationBoundaryClassification:
    def test_durable_create_cleanup_failure_after_link_does_not_hide_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        target = tmp_path / "immutable"
        real_unlink = Path.unlink
        cleanup_attempts = 0

        def deny_temp_unlink(path: Path, *args: Any, **kwargs: Any) -> None:
            nonlocal cleanup_attempts
            if path.name.endswith(".tmp"):
                cleanup_attempts += 1
                raise PermissionError("temporary cleanup denied")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", deny_temp_unlink)

        atomic_files.durable_create(
            target,
            b"published",
            operation=_operation(),
        )

        assert cleanup_attempts >= 1
        assert target.read_bytes() == b"published"

    def test_absent_pointer_cas_cleanup_failure_preserves_durable_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        pointer = tmp_path / "current"
        real_unlink = Path.unlink

        def deny_temp_unlink(path: Path, *args: Any, **kwargs: Any) -> None:
            if path.name.endswith(".tmp"):
                raise PermissionError("temporary cleanup denied")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", deny_temp_unlink)

        changed = atomic_files.atomic_pointer_cas(
            pointer,
            expected=None,
            replacement=b"generation-1\n",
            operation=_operation(),
        )

        assert changed is True
        assert pointer.read_bytes() == b"generation-1\n"

    def test_explicit_unlock_failure_after_durable_replace_returns_success_when_close_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        target = tmp_path / "head.json"
        target.write_bytes(b"old")

        def fail_unlock(_fd: int) -> None:
            raise PermissionError("explicit unlock denied")

        monkeypatch.setattr(atomic_files, "_release_platform_lock", fail_unlock)

        atomic_files.durable_replace(
            target,
            b"new",
            operation=_operation(),
        )

        assert target.read_bytes() == b"new"

    def test_stable_read_metadata_probe_failure_is_typed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        pointer = tmp_path / "head.json"
        pointer.write_bytes(b"head")
        real_exists = Path.exists

        def denied_exists(path: Path) -> bool:
            if path == pointer:
                raise PermissionError("metadata denied")
            return real_exists(path)

        monkeypatch.setattr(Path, "exists", denied_exists)

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.stable_read(
                pointer,
                lambda value: value,
                operation=_operation(),
            )

        _assert_error(
            caught,
            code="MOTHER_STATE_DURABLE_READ_FAILED",
            retry_class="after-reobserve",
            authority_effect="none",
        )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-018"],
    operations=["MOTHER-OP-SYNC-STATE"],
    functionalities=["MOTHER-OF-SYNC-005"],
    modules=["MOTHER-OFM-CORE-011"],
)
class TestFinalCore011ErrorEnvelope:
    def test_temporary_file_write_failure_is_exact_typed_prepublication_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        target = tmp_path / "immutable"
        real_fdopen = atomic_files.os.fdopen

        class FailingWriter:
            def __init__(self, handle: Any) -> None:
                self._handle = handle

            def __enter__(self) -> "FailingWriter":
                return self

            def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
                self._handle.close()

            def write(self, _data: bytes) -> int:
                raise PermissionError("temporary write denied")

            def flush(self) -> None:
                self._handle.flush()

            def fileno(self) -> int:
                return self._handle.fileno()

        def failing_fdopen(fd: int, *args: Any, **kwargs: Any) -> FailingWriter:
            return FailingWriter(real_fdopen(fd, *args, **kwargs))

        monkeypatch.setattr(atomic_files.os, "fdopen", failing_fdopen)

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_create(target, b"payload", operation=_operation())

        _assert_error(
            caught,
            code="MOTHER_STATE_DURABLE_WRITE_FAILED",
            retry_class="same-request",
            authority_effect="none",
        )
        assert caught.value.module_id == "MOTHER-OFM-CORE-011"
        assert caught.value.cause_class == "PermissionError"
        assert not target.exists()

    def test_temporary_file_fsync_failure_is_exact_typed_prepublication_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        target = tmp_path / "immutable"

        def denied_fsync(_fd: int) -> None:
            raise OSError("temporary fsync denied")

        monkeypatch.setattr(atomic_files.os, "fsync", denied_fsync)

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_create(target, b"payload", operation=_operation())

        _assert_error(
            caught,
            code="MOTHER_STATE_DURABLE_WRITE_FAILED",
            retry_class="same-request",
            authority_effect="none",
        )
        assert caught.value.module_id == "MOTHER-OFM-CORE-011"
        assert caught.value.cause_class == "OSError"
        assert not target.exists()

    def test_fdopen_failure_closes_original_mkstemp_descriptor(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        target = tmp_path / "immutable"
        real_mkstemp = atomic_files.tempfile.mkstemp
        real_close = atomic_files.os.close
        created_fds: list[int] = []
        closed_fds: list[int] = []

        def recording_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
            fd, path = real_mkstemp(*args, **kwargs)
            created_fds.append(fd)
            return fd, path

        def denied_fdopen(_fd: int, *_args: Any, **_kwargs: Any) -> Any:
            raise PermissionError("fdopen denied")

        def recording_close(fd: int) -> None:
            closed_fds.append(fd)
            real_close(fd)

        monkeypatch.setattr(atomic_files.tempfile, "mkstemp", recording_mkstemp)
        monkeypatch.setattr(atomic_files.os, "fdopen", denied_fdopen)
        monkeypatch.setattr(atomic_files.os, "close", recording_close)

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_create(target, b"payload", operation=_operation())

        _assert_error(
            caught,
            code="MOTHER_STATE_DURABLE_WRITE_FAILED",
            retry_class="same-request",
            authority_effect="none",
        )
        assert len(created_fds) == 1
        assert created_fds[0] in closed_fds
        with pytest.raises(OSError):
            os.fstat(created_fds[0])
        assert not target.exists()

    def test_absolute_path_resolution_failure_is_typed_read_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        target = tmp_path / "immutable"

        def denied_absolute(_path: Path) -> Path:
            raise PermissionError("absolute path resolution denied")

        monkeypatch.setattr(Path, "absolute", denied_absolute)

        with pytest.raises(errors.MotherError) as caught:
            atomic_files.durable_create(target, b"payload", operation=_operation())

        _assert_error(
            caught,
            code="MOTHER_STATE_DURABLE_READ_FAILED",
            retry_class="after-reobserve",
            authority_effect="none",
        )
        assert caught.value.module_id == "MOTHER-OFM-CORE-011"
        assert caught.value.cause_class == "PermissionError"

    def test_read_only_target_lock_close_failure_has_no_publication_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        atomic_files = _atomic_files()
        errors = _errors()
        target = tmp_path / "target"
        target.write_bytes(b"stable")
        lock = atomic_files.synchronized_target(target, operation=_operation())
        lock.__enter__()
        lock_fd = lock._fd
        assert isinstance(lock_fd, int)
        real_close = atomic_files.os.close

        def denied_lock_close(fd: int) -> None:
            if fd == lock_fd:
                raise PermissionError("lock descriptor close denied")
            real_close(fd)

        monkeypatch.setattr(atomic_files.os, "close", denied_lock_close)
        try:
            with pytest.raises(errors.MotherError) as caught:
                lock.__exit__(None, None, None)
        finally:
            monkeypatch.setattr(atomic_files.os, "close", real_close)
            real_close(lock_fd)

        _assert_error(
            caught,
            code="MOTHER_STATE_TARGET_LOCK_CLEANUP_FAILED",
            retry_class="after-reobserve",
            authority_effect="none",
        )
        assert caught.value.module_id == "MOTHER-OFM-CORE-011"
        assert caught.value.durable_effect_refs == ()
