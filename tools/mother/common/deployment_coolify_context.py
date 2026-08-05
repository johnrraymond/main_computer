"""Shared read-only Coolify controller metadata helpers for Mother deployment tools."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
import json
from typing import Any

from .deployment_post_admission_steady_state import _mapping
from .private_state import PrivateStateReadResult


def load_controller_config(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    allowed_controllers: Collection[str],
    error_factory: Callable[[str, str], Exception],
    rejected_code: str,
    invalid_code: str,
    placement_description: str,
) -> dict[str, Any]:
    """Return canonical project/server bindings without authorizing mutation."""

    if controller_id not in allowed_controllers:
        allowed = ", ".join(sorted(allowed_controllers))
        raise error_factory(
            rejected_code,
            f"{placement_description} is limited to {allowed}",
        )
    try:
        document = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except Exception as exc:
        raise error_factory(
            invalid_code,
            "Mother private state is not canonical JSON",
        ) from exc

    networks = _mapping(document.get("networks"), "private_state.networks")
    body = _mapping(networks.get(network), f"private_state.networks.{network}")
    coolify = _mapping(body.get("coolify"), "private_state.coolify")
    if coolify.get("mutation_authority") != "observe-only":
        raise error_factory(
            invalid_code,
            "Coolify private state must remain observe-only",
        )

    controllers = _mapping(coolify.get("controllers"), "private_state.controllers")
    wire = _mapping(controllers.get(controller_id), f"controller {controller_id}")
    project_uuid = wire.get("project_uuid")
    server_uuid = wire.get("server_uuid")
    enabled = wire.get("enabled", True)
    if not (
        type(project_uuid) is str
        and bool(project_uuid)
        and type(server_uuid) is str
        and bool(server_uuid)
        and enabled is True
    ):
        raise error_factory(
            invalid_code,
            f"{controller_id} lacks an enabled project/server binding",
        )

    result = {
        "controller_id": controller_id,
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
    }
    observed = wire.get("observed_environments")
    if isinstance(observed, Mapping):
        result["observed_environments"] = dict(observed)
    return result
