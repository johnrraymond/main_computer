from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing as mp
import os
from pathlib import Path
from threading import Barrier, Lock
from typing import Any, Iterable, Mapping

import pytest

from tests.mother.support.implementation import require_mother_module
from tests.mother.support.wave1b_process_workers import object_put_worker


def _object_store():
    return require_mother_module(
        "common.object_store",
        "MOTHER-OFM-CORE-012",
        phase="WAVE1B",
    )


def _hashing():
    return require_mother_module(
        "common.hashing",
        "MOTHER-OFM-CORE-004",
        phase="WAVE1A",
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


def _regular_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def _graph(*pairs: tuple[Any, Iterable[Any]]) -> dict[Any, tuple[Any, ...]]:
    return {key: tuple(children) for key, children in pairs}


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


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-027"],
    operations=["MOTHER-OP-UPGRADE-HUB"],
    functionalities=["MOTHER-OF-AUTH-004"],
    modules=["MOTHER-OFM-CORE-012"],
)
class TestImmutableObjects:
    def test_put_addresses_exact_bytes_by_content_hash_and_get_rehashes(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        hashing = _hashing()
        root = tmp_path / "objects"
        payload = b"immutable\x00mother\n"

        reference = object_store.put_immutable(root, payload)

        assert reference == hashing.sha256(payload)
        assert type(reference).__name__ == "ContentHash"
        assert object_store.get_verified(root, reference) == payload

    def test_identical_put_is_idempotent_and_does_not_duplicate_storage(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        root = tmp_path / "objects"
        payload = b"same bytes"

        first = object_store.put_immutable(root, payload)
        files_after_first = _regular_files(root)
        second = object_store.put_immutable(root, payload)

        assert second == first
        assert _regular_files(root) == files_after_first

    def test_existing_hash_path_with_different_bytes_is_exact_corruption(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        errors = _errors()
        root = tmp_path / "objects"
        payload = b"authoritative bytes"
        reference = object_store.put_immutable(root, payload)
        files = _regular_files(root)
        assert len(files) == 1
        files[0].write_bytes(b"substituted bytes")

        with pytest.raises(errors.MotherError) as put_error:
            object_store.put_immutable(root, payload)
        _assert_error(
            put_error,
            code="MOTHER_STATE_OBJECT_CORRUPT",
            retry_class="never",
            authority_effect="none",
        )

        with pytest.raises(errors.MotherError) as get_error:
            object_store.get_verified(root, reference)
        _assert_error(
            get_error,
            code="MOTHER_STATE_OBJECT_CORRUPT",
            retry_class="never",
            authority_effect="none",
        )

    def test_missing_and_corrupt_objects_have_distinct_exact_codes(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        hashing = _hashing()
        errors = _errors()
        root = tmp_path / "objects"
        existing_ref = object_store.put_immutable(root, b"existing")
        _regular_files(root)[0].write_bytes(b"corrupt")
        missing_ref = hashing.sha256(b"missing")

        with pytest.raises(errors.MotherError) as missing:
            object_store.get_verified(root, missing_ref)
        _assert_error(
            missing,
            code="MOTHER_STATE_OBJECT_MISSING",
            retry_class="after-reobserve",
            authority_effect="none",
        )

        with pytest.raises(errors.MotherError) as corrupt:
            object_store.get_verified(root, existing_ref)
        _assert_error(
            corrupt,
            code="MOTHER_STATE_OBJECT_CORRUPT",
            retry_class="never",
            authority_effect="none",
        )

    def test_put_uses_core_011_durable_create(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        object_store = _object_store()
        calls: list[tuple[Path, bytes]] = []
        real = object_store.atomic_files.durable_create

        def recording_create(path: Path, data: bytes, **kwargs: Any) -> Any:
            calls.append((Path(path), bytes(data)))
            return real(path, data, **kwargs)

        monkeypatch.setattr(
            object_store.atomic_files,
            "durable_create",
            recording_create,
        )

        object_store.put_immutable(tmp_path / "objects", b"payload")

        assert len(calls) == 1
        assert calls[0][1] == b"payload"

    def test_interrupted_put_uses_normative_typed_faultpoint(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        hashing = _hashing()
        errors = _errors()
        faultpoints = _faultpoints()
        root = tmp_path / "objects"
        payload = b"payload"
        expected = hashing.sha256(payload)
        name = "immutable.after_temp_write"
        controller = faultpoints.FaultpointController.injected({name: 1})

        with pytest.raises(faultpoints.SimulatedInterruption) as interrupted:
            object_store.put_immutable(root, payload, faultpoints=controller)

        assert interrupted.value.faultpoint == name
        assert interrupted.value.hit_number == 1
        with pytest.raises(errors.MotherError) as missing:
            object_store.get_verified(root, expected)
        _assert_error(
            missing,
            code="MOTHER_STATE_OBJECT_MISSING",
            retry_class="after-reobserve",
            authority_effect="none",
        )

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
    def test_symlink_root_and_object_path_are_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        errors = _errors()

        outside = tmp_path / "outside"
        outside.mkdir()
        root_link = tmp_path / "root-link"
        root_link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(errors.MotherError) as root_error:
            object_store.put_immutable(root_link, b"payload")
        _assert_error(
            root_error,
            code="MOTHER_INPUT_UNSAFE_PATH",
            retry_class="never",
            authority_effect="none",
        )

        root = tmp_path / "objects"
        reference = object_store.put_immutable(root, b"trusted")
        object_path = _regular_files(root)[0]
        object_path.unlink()
        referent = tmp_path / "substitute"
        referent.write_bytes(b"trusted")
        object_path.symlink_to(referent)
        with pytest.raises(errors.MotherError) as path_error:
            object_store.get_verified(root, reference)
        _assert_error(
            path_error,
            code="MOTHER_INPUT_UNSAFE_PATH",
            retry_class="never",
            authority_effect="none",
        )

    def test_simultaneous_identical_puts_share_one_exclusive_publication(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        object_store = _object_store()
        root = tmp_path / "objects"
        payload = b"same payload"
        barrier = Barrier(2)
        lock = Lock()
        publication_winners = 0
        real_create = object_store.atomic_files.durable_create

        def counted_create(path: Path, data: bytes, **kwargs: Any) -> Any:
            nonlocal publication_winners
            result = real_create(path, data, **kwargs)
            with lock:
                publication_winners += 1
            return result

        monkeypatch.setattr(
            object_store.atomic_files,
            "durable_create",
            counted_create,
        )

        def put(_: int):
            barrier.wait()
            return object_store.put_immutable(root, payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            references = list(pool.map(put, (1, 2)))

        assert references[0] == references[1]
        assert publication_winners == 1
        assert len(_regular_files(root)) == 1

    def test_simultaneous_conflicting_puts_cannot_publish_competing_bytes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        object_store = _object_store()
        hashing = _hashing()
        errors = _errors()
        root = tmp_path / "objects"
        forced = hashing.sha256(b"forced-address")
        barrier = Barrier(2)

        monkeypatch.setattr(object_store.hashing, "sha256", lambda _data: forced)

        def put(payload: bytes) -> tuple[str, Any]:
            barrier.wait()
            try:
                return ("ok", object_store.put_immutable(root, payload))
            except errors.MotherError as exc:
                return ("error", exc)

        payloads = (b"first bytes", b"second bytes")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(put, payloads))

        successes = [value for kind, value in results if kind == "ok"]
        failures = [value for kind, value in results if kind == "error"]
        assert successes == [forced]
        assert len(failures) == 1
        assert failures[0].code == "MOTHER_STATE_OBJECT_CORRUPT"
        stored = _regular_files(root)
        assert len(stored) == 1
        assert stored[0].read_bytes() in payloads


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-016"],
    operations=["MOTHER-OP-RECOVER-HEAD"],
    functionalities=["MOTHER-OF-REC-003"],
    modules=["MOTHER-OFM-CORE-012"],
)
class TestVerifiedClosure:
    def _stored_graph(
        self,
        object_store: Any,
        root: Path,
    ) -> tuple[Any, Any, Any, Mapping[Any, tuple[Any, ...]]]:
        leaf = object_store.put_immutable(root, b"leaf")
        child = object_store.put_immutable(root, b"child")
        head = object_store.put_immutable(root, b"head")
        references = _graph(
            (head, (child,)),
            (child, (leaf,)),
            (leaf, ()),
        )
        return head, child, leaf, references

    def test_verify_closure_walks_every_transitive_reference(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        root = tmp_path / "objects"
        head, child, leaf, references = self._stored_graph(object_store, root)

        verified = object_store.verify_closure(
            root,
            roots=(head,),
            references=references,
            expected_members=(head, child, leaf),
        )

        assert tuple(verified) == (head, child, leaf)

    def test_missing_or_corrupt_transitive_member_is_exact_invalid_closure(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        hashing = _hashing()
        errors = _errors()
        root = tmp_path / "objects"
        head = object_store.put_immutable(root, b"head")
        missing = hashing.sha256(b"missing")
        references = _graph((head, (missing,)), (missing, ()))

        with pytest.raises(errors.MotherError) as missing_error:
            object_store.verify_closure(
                root,
                roots=(head,),
                references=references,
                expected_members=(head, missing),
            )
        _assert_error(
            missing_error,
            code="MOTHER_RECOVERY_INVALID_CLOSURE",
            retry_class="never",
            authority_effect="none",
        )

        head, child, leaf, references = self._stored_graph(
            object_store,
            tmp_path / "corrupt-objects",
        )
        leaf_file = next(
            path
            for path in _regular_files(tmp_path / "corrupt-objects")
            if path.read_bytes() == b"leaf"
        )
        leaf_file.write_bytes(b"substituted")
        with pytest.raises(errors.MotherError) as corrupt_error:
            object_store.verify_closure(
                tmp_path / "corrupt-objects",
                roots=(head,),
                references=references,
                expected_members=(head, child, leaf),
            )
        _assert_error(
            corrupt_error,
            code="MOTHER_RECOVERY_INVALID_CLOSURE",
            retry_class="never",
            authority_effect="none",
        )

    @pytest.mark.parametrize("duplicate_kind", ["root", "member", "child"])
    def test_duplicate_closure_members_are_exact_schema_errors(
        self,
        tmp_path: Path,
        duplicate_kind: str,
    ) -> None:
        object_store = _object_store()
        errors = _errors()
        root = tmp_path / "objects"
        head, child, leaf, references = self._stored_graph(object_store, root)
        roots = (head,)
        expected = (head, child, leaf)

        if duplicate_kind == "root":
            roots = (head, head)
        elif duplicate_kind == "member":
            expected = (head, child, leaf, leaf)
        else:
            references = _graph(
                (head, (child, child)),
                (child, (leaf,)),
                (leaf, ()),
            )

        with pytest.raises(errors.MotherError) as caught:
            object_store.verify_closure(
                root,
                roots=roots,
                references=references,
                expected_members=expected,
            )
        _assert_error(
            caught,
            code="MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER",
            retry_class="never",
            authority_effect="none",
        )

    def test_unreachable_extra_graph_row_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        errors = _errors()
        root = tmp_path / "objects"
        head, child, leaf, references = self._stored_graph(object_store, root)
        extra = object_store.put_immutable(root, b"unreachable")
        references = dict(references)
        references[extra] = ()

        with pytest.raises(errors.MotherError) as caught:
            object_store.verify_closure(
                root,
                roots=(head,),
                references=references,
                expected_members=(head, child, leaf),
            )
        _assert_error(
            caught,
            code="MOTHER_RECOVERY_INVALID_CLOSURE",
            retry_class="never",
            authority_effect="none",
        )

    @pytest.mark.parametrize("case", ["extra-member", "omitted-member", "cycle", "missing-row"])
    def test_malformed_closure_graphs_are_exact_invalid_closure(
        self,
        tmp_path: Path,
        case: str,
    ) -> None:
        object_store = _object_store()
        errors = _errors()
        root = tmp_path / "objects"
        head, child, leaf, references = self._stored_graph(object_store, root)
        expected = (head, child, leaf)

        if case == "extra-member":
            extra = object_store.put_immutable(root, b"extra")
            expected = (*expected, extra)
        elif case == "omitted-member":
            expected = (head, child)
        elif case == "cycle":
            references = _graph(
                (head, (child,)),
                (child, (leaf,)),
                (leaf, (head,)),
            )
        else:
            references = _graph((head, (child,)), (child, (leaf,)))

        with pytest.raises(errors.MotherError) as caught:
            object_store.verify_closure(
                root,
                roots=(head,),
                references=references,
                expected_members=expected,
            )
        _assert_error(
            caught,
            code="MOTHER_RECOVERY_INVALID_CLOSURE",
            retry_class="never",
            authority_effect="none",
        )

    def test_copy_verifies_source_and_publishes_complete_destination(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        source = tmp_path / "source"
        destination = tmp_path / "destination"
        head, child, leaf, references = self._stored_graph(object_store, source)

        copied = object_store.copy_verified_closure(
            source,
            destination,
            roots=(head,),
            references=references,
            expected_members=(head, child, leaf),
        )

        assert tuple(copied) == (head, child, leaf)
        assert tuple(
            object_store.verify_closure(
                destination,
                roots=(head,),
                references=references,
                expected_members=(head, child, leaf),
            )
        ) == (head, child, leaf)

    def test_interrupted_copy_is_not_accepted_and_retry_completes(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        errors = _errors()
        faultpoints = _faultpoints()
        source = tmp_path / "source"
        destination = tmp_path / "destination"
        head, child, leaf, references = self._stored_graph(object_store, source)
        name = "immutable.after_publish_before_dir_fsync"
        controller = faultpoints.FaultpointController.injected({name: 1})

        with pytest.raises(faultpoints.SimulatedInterruption) as interrupted:
            object_store.copy_verified_closure(
                source,
                destination,
                roots=(head,),
                references=references,
                expected_members=(head, child, leaf),
                faultpoints=controller,
            )
        assert interrupted.value.faultpoint == name

        with pytest.raises(errors.MotherError) as incomplete:
            object_store.verify_closure(
                destination,
                roots=(head,),
                references=references,
                expected_members=(head, child, leaf),
            )
        _assert_error(
            incomplete,
            code="MOTHER_RECOVERY_INVALID_CLOSURE",
            retry_class="never",
            authority_effect="none",
        )

        object_store.copy_verified_closure(
            source,
            destination,
            roots=(head,),
            references=references,
            expected_members=(head, child, leaf),
        )
        assert tuple(
            object_store.verify_closure(
                destination,
                roots=(head,),
                references=references,
                expected_members=(head, child, leaf),
            )
        ) == (head, child, leaf)

    @pytest.mark.parametrize("layout", ["same", "destination-inside-source", "source-inside-destination"])
    def test_overlapping_source_destination_trees_are_exact_input_errors(
        self,
        tmp_path: Path,
        layout: str,
    ) -> None:
        object_store = _object_store()
        errors = _errors()

        if layout == "same":
            source = destination = tmp_path / "objects"
        elif layout == "destination-inside-source":
            source = tmp_path / "source"
            destination = source / "nested-destination"
        else:
            destination = tmp_path / "destination"
            source = destination / "nested-source"

        head, child, leaf, references = self._stored_graph(object_store, source)

        with pytest.raises(errors.MotherError) as caught:
            object_store.copy_verified_closure(
                source,
                destination,
                roots=(head,),
                references=references,
                expected_members=(head, child, leaf),
            )
        _assert_error(
            caught,
            code="MOTHER_INPUT_OVERLAPPING_STORAGE_ROOTS",
            retry_class="never",
            authority_effect="none",
        )

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
    def test_copy_rejects_symlink_source_or_destination_roots(
        self,
        tmp_path: Path,
    ) -> None:
        object_store = _object_store()
        errors = _errors()
        real_source = tmp_path / "real-source"
        head, child, leaf, references = self._stored_graph(
            object_store,
            real_source,
        )
        source_link = tmp_path / "source-link"
        source_link.symlink_to(real_source, target_is_directory=True)
        destination_real = tmp_path / "destination-real"
        destination_real.mkdir()
        destination_link = tmp_path / "destination-link"
        destination_link.symlink_to(destination_real, target_is_directory=True)

        for source, destination in (
            (source_link, tmp_path / "destination"),
            (real_source, destination_link),
        ):
            with pytest.raises(errors.MotherError) as caught:
                object_store.copy_verified_closure(
                    source,
                    destination,
                    roots=(head,),
                    references=references,
                    expected_members=(head, child, leaf),
                )
            _assert_error(
                caught,
                code="MOTHER_INPUT_UNSAFE_PATH",
                retry_class="never",
                authority_effect="none",
            )

def _collect_object_process_results(processes, ready_queue, start_event, result_queue):
    ready = [ready_queue.get(timeout=20) for _ in processes]
    start_event.set()
    results = [result_queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return ready, results


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-027"],
    operations=["MOTHER-OP-UPGRADE-HUB"],
    functionalities=["MOTHER-OF-AUTH-004"],
    modules=["MOTHER-OFM-CORE-012"],
)
class TestSpawnedImmutablePublication:
    def _run(
        self,
        root: Path,
        payloads: tuple[bytes, bytes],
        *,
        forced_digest: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        object_store = _object_store()
        assert hasattr(object_store, "put_immutable")
        context = mp.get_context("spawn")
        ready_queue = context.Queue()
        result_queue = context.Queue()
        start_event = context.Event()
        processes = [
            context.Process(
                target=object_put_worker,
                args=(
                    str(root),
                    payload,
                    forced_digest,
                    ready_queue,
                    start_event,
                    result_queue,
                ),
            )
            for payload in payloads
        ]
        for process in processes:
            process.start()
        return _collect_object_process_results(
            processes,
            ready_queue,
            start_event,
            result_queue,
        )

    def test_identical_bytes_converge_across_spawned_processes(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "objects"
        payload = b"identical process payload"
        ready, results = self._run(
            root,
            (payload, payload),
            forced_digest=None,
        )

        assert ready == [{"ready": True}, {"ready": True}]
        assert all(result["status"] == "ok" for result in results)
        assert len({result["digest"] for result in results}) == 1
        assert len(_regular_files(root)) == 1
        assert _regular_files(root)[0].read_bytes() == payload

    def test_conflicting_bytes_cannot_share_one_forced_address_across_processes(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "objects"
        payloads = (b"process-first", b"process-second")
        forced_digest = "f" * 64
        ready, results = self._run(
            root,
            payloads,
            forced_digest=forced_digest,
        )

        assert ready == [{"ready": True}, {"ready": True}]
        winners = [result for result in results if result["status"] == "ok"]
        losers = [result for result in results if result["status"] == "error"]
        assert len(winners) == 1
        assert len(losers) == 1
        assert winners[0]["digest"] == forced_digest
        assert losers[0] == {
            "status": "error",
            "code": "MOTHER_STATE_OBJECT_CORRUPT",
            "retry_class": "never",
            "authority_effect": "none",
        }
        stored = _regular_files(root)
        assert len(stored) == 1
        assert stored[0].read_bytes() == winners[0]["payload"]

