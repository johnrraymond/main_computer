from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .common.coolify import CoolifyClient, delete_service, find_service, wait_for_missing
from .common.errors import FdbControlError
from .common.models import FdbContext
from .common.privates import load_private_infrastructure, resolve_host_binding
from .common.state import read_accepted_state, require_operation, update_operation
from .create_cluster import plan_from_operation


def delete_unaccepted_birth(
    ctx: FdbContext,
    network: str,
    operation_id: str,
    *,
    timeout_s: float = 300.0,
    client_factory=CoolifyClient,
) -> dict[str, Any]:
    """Delete the Coolify service for an unaccepted create-cluster birth attempt.

    This is a development/test cleanup seam, not the high-level FDB remove-service
    operation. It deliberately refuses to delete an accepted/finalized cluster.
    """

    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "create-cluster":
        raise FdbControlError(
            code="FDB_BIRTH_CLEANUP_WRONG_OPERATION",
            message="birth cleanup requires a create-cluster operation",
            module_id="FDB-OFM-APP-004",
            operation_id=operation_id,
        )
    if op.get("stage") == "finalized" or read_accepted_state(ctx, network) is not None:
        raise FdbControlError(
            code="FDB_BIRTH_CLEANUP_ACCEPTED_REFUSED",
            message="birth cleanup refuses an accepted/finalized FDB cluster; use an explicit lifecycle operation instead",
            module_id="FDB-OFM-APP-004",
            operation_id=operation_id,
            retry_class="never",
            effect_class="none",
        )

    plan, op = plan_from_operation(ctx, network, operation_id)
    if len(plan.services) != 1:
        raise FdbControlError(
            code="FDB_BIRTH_CLEANUP_SERVICE_COUNT",
            message="birth cleanup currently supports exactly one initial service",
            module_id="FDB-OFM-APP-004",
            operation_id=operation_id,
        )

    service = plan.services[0]
    target = op.get("target") if isinstance(op.get("target"), dict) else {}
    deployment_result = op.get("deployment_result") if isinstance(op.get("deployment_result"), dict) else {}
    service_name = str(deployment_result.get("service_name") or target.get("service_name") or "").strip()
    service_uuid = str(deployment_result.get("service_uuid") or "").strip()
    if not service_name:
        raise FdbControlError(
            code="FDB_BIRTH_CLEANUP_NO_SERVICE_NAME",
            message="prepared birth operation has no service name",
            module_id="FDB-OFM-APP-004",
            operation_id=operation_id,
        )

    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, service.host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)

    if not service_uuid:
        service_uuid, _ = find_service(client, service_name)
    if not service_uuid:
        update_operation(
            ctx,
            network,
            operation_id,
            stage="prepared",
            deployment_result=None,
            cleanup_result={"status": "already-missing", "service_name": service_name},
        )
        return {
            "operation_id": operation_id,
            "network": network,
            "service_name": service_name,
            "service_uuid": None,
            "status": "already-missing",
            "reusable_operation": True,
        }

    delete_service(client, service_uuid)
    wait_for_missing(client, service_uuid, timeout_s=timeout_s, poll_s=5.0)
    update_operation(
        ctx,
        network,
        operation_id,
        stage="prepared",
        deployment_result=None,
        cleanup_result={
            "status": "deleted",
            "service_name": service_name,
            "service_uuid": service_uuid,
        },
    )
    return {
        "operation_id": operation_id,
        "network": network,
        "service_name": service_name,
        "service_uuid": service_uuid,
        "status": "deleted",
        "reusable_operation": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete an unaccepted FDB birth service so the same birth operation can be retried")
    parser.add_argument("network")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ctx = FdbContext.from_repo(args.repo_root)
    try:
        result = delete_unaccepted_birth(
            ctx,
            args.network,
            args.operation_id,
            timeout_s=args.timeout_seconds,
        )
        payload = {"ok": True, "result": result}
        code = 0
    except FdbControlError as exc:
        payload = {
            "ok": False,
            "error": {
                "code": exc.envelope.code,
                "message": exc.envelope.message,
                "module_id": exc.envelope.module_id,
                "operation_id": exc.envelope.operation_id,
                "retry_class": exc.envelope.retry_class,
                "effect_class": exc.envelope.effect_class,
            },
        }
        code = 2
    except (ValueError, KeyError, TypeError) as exc:
        payload = {"ok": False, "error": {"code": "FDB_INVALID_INPUT", "message": str(exc)}}
        code = 2
    print(json.dumps(payload, sort_keys=True, indent=None if args.json else 2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
