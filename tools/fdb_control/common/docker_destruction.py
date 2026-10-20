from __future__ import annotations

from typing import Any, Mapping

from .coolify import wait_for_destruction
from .errors import FdbControlError


def verify_service_destruction(
    client: Any,
    private_doc: Mapping[str, Any],
    network: str,
    controller_id: str,
    service_uuid: str,
    *,
    timeout_s: float = 300.0,
    poll_s: float = 5.0,
) -> None:
    """Wait for Coolify API disappearance, without SSH or Docker access.

    ``private_doc``, ``network`` and ``controller_id`` retain the existing
    call signature for in-flight FDB operations. They are not used to access
    the host. Coolify disappearance is not physical Docker destruction proof.
    """
    del private_doc, network, controller_id
    try:
        wait_for_destruction(
            client, service_uuid, timeout_s=timeout_s, poll_s=poll_s,
        )
    except FdbControlError as exc:
        if exc.envelope.effect_class != "none":
            raise
        # The deletion request has already been issued. Never report a
        # post-delete read failure as a no-effect operation.
        raise FdbControlError(
            code=exc.envelope.code,
            message=exc.envelope.message,
            module_id=exc.envelope.module_id,
            operation_id=exc.envelope.operation_id,
            retry_class=exc.envelope.retry_class,
            effect_class="live-deployment",
        ) from exc
