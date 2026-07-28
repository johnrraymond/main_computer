from __future__ import annotations

from pathlib import Path
from typing import Any


def _operation():
    from tools.mother.common import models

    return models.OperationIdentity(
        operation_id="MOTHER-TEST-WAVE1B-PROCESS-OPERATION",
        request_id="MOTHER-TEST-WAVE1B-PROCESS-REQUEST",
        network="test-network",
        operation_kind="MOTHER-OP-SYNC-STATE",
    )


def _error_result(exc: BaseException) -> dict[str, Any]:
    return {
        "status": "error",
        "code": getattr(exc, "code", type(exc).__name__),
        "retry_class": getattr(exc, "retry_class", None),
        "authority_effect": getattr(exc, "authority_effect", None),
    }


def durable_create_worker(
    target: str,
    payload: bytes,
    ready_queue: Any,
    start_event: Any,
    result_queue: Any,
) -> None:
    from tools.mother.common import atomic_files, errors

    path = Path(target)
    ready_queue.put({"absent": not path.exists()})
    start_event.wait()
    try:
        atomic_files.durable_create(path, payload, operation=_operation())
    except errors.MotherError as exc:
        result_queue.put(_error_result(exc))
    else:
        result_queue.put({"status": "ok", "payload": payload})


def pointer_cas_worker(
    pointer: str,
    expected: bytes,
    replacement: bytes,
    ready_queue: Any,
    start_event: Any,
    result_queue: Any,
) -> None:
    from tools.mother.common import atomic_files

    path = Path(pointer)
    ready_queue.put({"observed": path.read_bytes()})
    start_event.wait()
    changed = atomic_files.atomic_pointer_cas(
        path,
        operation=_operation(),
        expected=expected,
        replacement=replacement,
    )
    result_queue.put(
        {
            "status": "ok",
            "changed": changed,
            "replacement": replacement,
        }
    )


def object_put_worker(
    root: str,
    payload: bytes,
    forced_digest: str | None,
    ready_queue: Any,
    start_event: Any,
    result_queue: Any,
) -> None:
    from tools.mother.common import errors, models, object_store

    if forced_digest is not None:
        forced = models.ContentHash(algorithm="sha256", digest=forced_digest)
        object_store.hashing.sha256 = lambda _data: forced

    ready_queue.put({"ready": True})
    start_event.wait()
    try:
        reference = object_store.put_immutable(
            Path(root),
            payload,
            operation=_operation(),
        )
    except errors.MotherError as exc:
        result_queue.put(_error_result(exc))
    else:
        result_queue.put(
            {
                "status": "ok",
                "algorithm": reference.algorithm,
                "digest": reference.digest,
                "payload": payload,
            }
        )
