from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Barrier, Lock
from typing import Any

import pytest

from tests.mother.support.implementation import require_mother_module


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

        result = atomic_files.stable_read(pointer, load, max_attempts=2)

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

        result = atomic_files.stable_read(pointer, load, max_attempts=3)

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
            atomic_files.stable_read(pointer, load, max_attempts=2)

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
            atomic_files.durable_create(target, b"successor")

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

        atomic_files.durable_create(target, b"exact-bytes\n")

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

        atomic_files.durable_replace(target, b"new")

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
            )

        assert caught.value.faultpoint == name
        assert target.read_bytes() == b"successor"
        atomic_files.durable_replace(target, b"successor")
        assert target.read_bytes() == b"successor"

    def test_durable_publication_uses_only_normative_faultpoint_sequence(
        self,
        tmp_path: Path,
    ) -> None:
        atomic_files = _atomic_files()
        faultpoints = _faultpoints()
        target = tmp_path / "record.json"
        controller = faultpoints.FaultpointController.injected({})

        atomic_files.durable_create(target, b"value", faultpoints=controller)

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
            atomic_files.durable_replace(link, b"unsafe")

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
                atomic_files.durable_create(target, payload)
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
        ) is False
        assert not pointer.exists()
        assert atomic_files.atomic_pointer_cas(
            pointer,
            expected=None,
            replacement=b"generation-1\n",
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
