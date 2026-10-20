"""Read-only Coolify evidence for an unverified FoundationDB observer.

Does not deploy, redeploy, repair, create, or accept anything.  Uses the same
Mother-owned private controller coordinates as normal FDB Control.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any

from .common.coolify import CoolifyClient, find_service, get_service, service_health_status
from .common.models import FdbContext
from .common.privates import load_private_infrastructure, resolve_host_binding
from .common.service_descriptors import BIRTH_PROOF_PREFIX, birth_observer_subservice_name
from .common.service_placement import parse_service_identity, resolve_host_for_placement


def _subresources(detail: Mapping[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for kind in ("applications", "databases"):
        records = detail.get(kind)
        if not isinstance(records, list):
            continue
        for item in records:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                result.append({"kind": kind, "name": name, "status": str(item.get("status") or "unknown")})
    return result


def _log_observation(client: CoolifyClient, uuid: str, subservice: str, *, lines: int = 120) -> dict[str, Any]:
    query = urllib.parse.urlencode({
        "sub_service_name": subservice,
        "lines": lines,
        "show_timestamps": "false",
    })
    response = client.request("GET", f"/api/v1/services/{urllib.parse.quote(uuid)}/logs?{query}")
    body = response.body
    logs = str(body.get("logs") or "") if isinstance(body, Mapping) else str(body or "")
    # A log failure must NOT be presented as an empty observer log.  The caller
    # can distinguish API permission/name errors from missing runtime proof.
    if not response.ok:
        return {
            "sub_service_name": subservice,
            "http_status": response.status,
            "retrieved": False,
            "error": str(body.get("message") or body.get("error") or "HTTP error")[:300]
                     if isinstance(body, Mapping) else "HTTP error",
            "log_tail": [],
        }
    lines_found = logs.splitlines()
    return {
        "sub_service_name": subservice,
        "http_status": response.status,
        "retrieved": True,
        "line_count": len(lines_found),
        "birth_proof_marker_present": any(line.strip().startswith(BIRTH_PROOF_PREFIX + " ") for line in lines_found),
        "log_tail": lines_found[-min(60, lines):],
    }


def diagnose_observer(ctx: FdbContext, network: str, service_id: str, *, client_factory=CoolifyClient) -> dict[str, Any]:
    """Collect safe, bounded read-only observation; no mutation capability."""
    parsed = parse_service_identity(network, service_id)
    private_doc = load_private_infrastructure(ctx)
    host = resolve_host_for_placement(private_doc, network, parsed.placement_token)
    binding = resolve_host_binding(private_doc, network, host.host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    coolify_name = f"main-computer-{service_id}"
    uuid, _ = find_service(client, coolify_name)
    report: dict[str, Any] = {
        "network": network,
        "service_id": service_id,
        "controller_id": host.controller_id,
        "coolify_service_name": coolify_name,
        "coolify_service_uuid": uuid or None,
        "read_only": True,
        "network_mutation_performed": False,
    }
    if not uuid:
        report.update({"status": "service-not-found", "subresources": []})
        return report
    detail = get_service(client, uuid)
    if detail is None:
        report.update({"status": "service-not-found", "subresources": []})
        return report
    subresources = _subresources(detail)
    observer = birth_observer_subservice_name(service_id)
    names = [item["name"] for item in subresources]
    report.update({
        "status": "observed",
        "coolify_status": service_health_status(detail),
        "subresources": subresources,
        "observer_expected_name": observer,
        "observer_listed": observer in names if subresources else None,
        "observer_logs": _log_observation(client, uuid, observer),
        "server_logs": _log_observation(client, uuid, service_id),
    })
    return report
