from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .common.errors import FdbControlError
from .common.models import (
    AddServiceDeployment,
    AddServiceRequest,
    ClusterIdentity,
    CreateClusterDeployment,
    CreateClusterRequest,
    FdbContext,
    RemoveServiceDeployment,
    RemoveServiceRequest,
    ServicePlacement,
)
from .common.state import read_accepted_state, require_operation
from .add_service import do as add_do
from .add_service import finalize as add_finalize
from .add_service import plan_from_accepted as add_plan_from_accepted
from .add_service import plan_from_operation as add_plan_from_operation
from .add_service import prep as add_prep
from .add_service import prep_inferred as add_prep_inferred
from .create_cluster import do as create_do
from .create_cluster import finalize as create_finalize
from .create_cluster import plan_from_operation, prep as create_prep
from .remove_service import do as remove_do
from .remove_service import finalize as remove_finalize
from .remove_service import plan_from_operation as remove_plan_from_operation
from .remove_service import prep as remove_prep
from .live_inspect import verify_accepted_cluster, verify_add_service, verify_birth, verify_remove_service


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Main Computer FoundationDB control surface")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-cluster")
    stages = create.add_subparsers(dest="stage", required=True)
    prep = stages.add_parser("prep")
    prep.add_argument("network")
    prep.add_argument("--service", required=True)
    prep.add_argument("--host", required=True)
    prep.add_argument("--address", required=True)
    prep.add_argument("--port", type=int, default=4550)
    prep.add_argument("--machine-id", default="")
    prep.add_argument("--zone-id", default="")
    prep.add_argument("--cluster-description", required=True)
    prep.add_argument("--cluster-id", required=True)
    prep.add_argument("--redundancy", default="single")
    prep.add_argument("--storage-engine", default="ssd")
    _add_deployment_args(prep)

    for stage in ("do", "finalize"):
        p = stages.add_parser(stage)
        p.add_argument("network")
        p.add_argument("--operation-id", required=True)

    add = sub.add_parser("add-service")
    add_stages = add.add_subparsers(dest="stage", required=True)
    add_prep_parser = add_stages.add_parser("prep")
    add_prep_parser.add_argument("network")
    add_prep_parser.add_argument("--service", required=True)
    for stage in ("do", "finalize"):
        p = add_stages.add_parser(stage)
        p.add_argument("network")
        p.add_argument("--operation-id", required=True)

    remove = sub.add_parser("remove-service")
    remove_stages = remove.add_subparsers(dest="stage", required=True)
    remove_prep_parser = remove_stages.add_parser("prep")
    remove_prep_parser.add_argument("network")
    remove_prep_parser.add_argument("--service", required=True)
    remove_prep_parser.add_argument("--allow-full-deletion", action="store_true")
    _add_deployment_args(remove_prep_parser)
    for stage in ("do", "finalize"):
        p = remove_stages.add_parser(stage)
        p.add_argument("network")
        p.add_argument("--operation-id", required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("network")
    inspect.add_argument("--operation-id", default="")
    return parser


def _add_deployment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-uuid", default="")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--environment-name", default="")
    parser.add_argument("--environment-uuid", default="")
    parser.add_argument("--server-uuid", default="")
    parser.add_argument("--server-name", default="")
    parser.add_argument("--destination-uuid", default="")
    parser.add_argument("--image", default="foundationdb/foundationdb:7.4.6")
    parser.add_argument("--data-root", default="/data/main-computer/fdb")
    parser.add_argument("--force-deploy", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ctx = FdbContext.from_repo(args.repo_root)
    try:
        result = _dispatch(ctx, args)
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


def _dispatch(ctx: FdbContext, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "create-cluster" and args.stage == "prep":
        service = ServicePlacement(
            service_id=args.service,
            host_id=args.host,
            address=args.address,
            port=args.port,
            machine_id=args.machine_id or args.host,
            zone_id=args.zone_id or args.host,
        )
        request = CreateClusterRequest(
            network=args.network,
            cluster=ClusterIdentity(args.cluster_description, args.cluster_id),
            services=(service,),
            coordinator_service_ids=(service.service_id,),
            redundancy_mode=args.redundancy,
            storage_engine=args.storage_engine,
        )
        deployment = CreateClusterDeployment(
            project_uuid=args.project_uuid,
            project_name=args.project_name,
            environment_name=args.environment_name,
            environment_uuid=args.environment_uuid,
            server_uuid=args.server_uuid,
            server_name=args.server_name,
            destination_uuid=args.destination_uuid,
            image=args.image,
            data_root=args.data_root,
            force_deploy=args.force_deploy,
        )
        return _result_dict(create_prep(ctx, request, deployment))
    if args.command == "create-cluster" and args.stage == "do":
        return _result_dict(create_do(ctx, args.network, args.operation_id))
    if args.command == "create-cluster" and args.stage == "finalize":
        return _result_dict(create_finalize(ctx, args.network, args.operation_id))
    if args.command == "add-service" and args.stage == "prep":
        return _result_dict(add_prep_inferred(ctx, args.network, args.service))
    if args.command == "add-service" and args.stage == "do":
        return _result_dict(add_do(ctx, args.network, args.operation_id))
    if args.command == "add-service" and args.stage == "finalize":
        return _result_dict(add_finalize(ctx, args.network, args.operation_id))
    if args.command == "remove-service" and args.stage == "prep":
        request = RemoveServiceRequest(
            network=args.network,
            service_id=args.service,
            allow_full_deletion=bool(args.allow_full_deletion),
        )
        deployment = RemoveServiceDeployment(
            project_uuid=args.project_uuid,
            project_name=args.project_name,
            environment_name=args.environment_name,
            environment_uuid=args.environment_uuid,
            server_uuid=args.server_uuid,
            server_name=args.server_name,
            destination_uuid=args.destination_uuid,
            image=args.image,
            force_deploy=args.force_deploy,
        )
        return _result_dict(remove_prep(ctx, request, deployment))
    if args.command == "remove-service" and args.stage == "do":
        return _result_dict(remove_do(ctx, args.network, args.operation_id))
    if args.command == "remove-service" and args.stage == "finalize":
        return _result_dict(remove_finalize(ctx, args.network, args.operation_id))
    if args.command == "inspect":
        if args.operation_id:
            op = require_operation(ctx, args.network, args.operation_id)
            deployment = op.get("deployment_result") if isinstance(op.get("deployment_result"), dict) else {}
            target = op.get("target") if isinstance(op.get("target"), dict) else {}
            if op.get("kind") == "create-cluster":
                plan, _ = plan_from_operation(ctx, args.network, args.operation_id)
                verification = verify_birth(
                    ctx,
                    plan,
                    service_name=str(deployment.get("service_name") or target.get("service_name") or ""),
                    service_uuid=str(deployment.get("service_uuid") or "") or None,
                )
                return {
                    "network": args.network,
                    "operation_id": args.operation_id,
                    "stage": op.get("stage"),
                    "birth_verification": _verification_dict(verification),
                }
            if op.get("kind") == "add-service":
                plan, _ = add_plan_from_operation(ctx, args.network, args.operation_id)
                verification = verify_add_service(
                    ctx,
                    plan,
                    service_name=str(deployment.get("service_name") or target.get("service_name") or ""),
                    service_uuid=str(deployment.get("service_uuid") or "") or None,
                )
                return {
                    "network": args.network,
                    "operation_id": args.operation_id,
                    "stage": op.get("stage"),
                    "add_service_verification": _verification_dict(verification),
                }
            if op.get("kind") == "remove-service":
                plan, _ = remove_plan_from_operation(ctx, args.network, args.operation_id)
                result = deployment
                verification = verify_remove_service(
                    ctx,
                    plan,
                    target_service_name=str(target.get("target_service_name") or ""),
                    target_service_uuid=str(target.get("target_service_uuid") or ""),
                    helper_service_name=str(result.get("helper_service_name") or target.get("helper_service_name") or ""),
                    helper_service_uuid=str(result.get("helper_service_uuid") or "") or None,
                )
                return {
                    "network": args.network,
                    "operation_id": args.operation_id,
                    "stage": op.get("stage"),
                    "remove_service_verification": {
                        "verified": verification.verified,
                        "service_id": verification.service_id,
                        "host_id": verification.host_id,
                        "target_service_name": verification.target_service_name,
                        "target_service_uuid": verification.target_service_uuid,
                        "target_status": verification.target_status,
                        "helper_service_name": verification.helper_service_name,
                        "helper_service_uuid": verification.helper_service_uuid,
                        "helper_status": verification.helper_status,
                        "reason": verification.reason,
                    },
                }
            raise ValueError(f"unsupported operation kind for inspection: {op.get('kind')!r}")
        accepted = read_accepted_state(ctx, args.network)
        if accepted is None:
            return {"network": args.network, "accepted": None, "status": "unborn"}
        if not accepted.services:
            if accepted.coordinators:
                raise ValueError("accepted empty FDB topology cannot retain coordinators")
            return {
                "network": args.network,
                "accepted_generation": accepted.generation,
                "status": "accepted-empty",
                "cluster": {"description": accepted.cluster.description, "cluster_id": accepted.cluster.cluster_id},
                "services": [],
                "coordinators": [],
                "redundancy_mode": accepted.redundancy_mode,
                "storage_engine": accepted.storage_engine,
                "empty_topology_verification": {
                    "verified": True,
                    "reason": "accepted-empty-topology",
                },
            }
        if len(accepted.services) == 1:
            from .common.models import BirthPlan
            from .common.cluster_file import render_cluster_file
            plan = BirthPlan(
                network=accepted.network,
                cluster=accepted.cluster,
                services=accepted.services,
                coordinators=accepted.coordinators,
                redundancy_mode=accepted.redundancy_mode,
                storage_engine=accepted.storage_engine,
                cluster_file_contents=render_cluster_file(accepted.cluster, accepted.coordinators),
            )
            service_name = f"main-computer-{accepted.services[0].service_id}"
            cluster_verification = verify_accepted_cluster(ctx, plan)
            birth_verification = None
            if not cluster_verification.verified:
                birth_verification = verify_birth(ctx, plan, service_name=service_name)
            result = {
                "network": args.network,
                "accepted_generation": accepted.generation,
                "status": "accepted",
                "cluster": {"description": accepted.cluster.description, "cluster_id": accepted.cluster.cluster_id},
                "services": [
                    {"service_id": item.service_id, "host_id": item.host_id, "endpoint": item.endpoint}
                    for item in accepted.services
                ],
                "coordinators": [item.endpoint for item in accepted.coordinators],
                "redundancy_mode": accepted.redundancy_mode,
                "storage_engine": accepted.storage_engine,
            }
            if cluster_verification.verified:
                result["cluster_verification"] = {
                    "verified": cluster_verification.verified,
                    "proof_service_id": cluster_verification.proof_service_id,
                    "coolify_service_uuid": cluster_verification.coolify_service_uuid,
                    "coolify_status": cluster_verification.coolify_status,
                    "reason": cluster_verification.reason,
                }
            else:
                result["birth_verification"] = _verification_dict(birth_verification)
            return result
        plan = add_plan_from_accepted(accepted)
        verification = verify_accepted_cluster(ctx, plan)
        return {
            "network": args.network,
            "accepted_generation": accepted.generation,
            "status": "accepted",
            "cluster": {"description": accepted.cluster.description, "cluster_id": accepted.cluster.cluster_id},
            "cluster_verification": {
                "verified": verification.verified,
                "proof_service_id": verification.proof_service_id,
                "coolify_service_uuid": verification.coolify_service_uuid,
                "coolify_status": verification.coolify_status,
                "reason": verification.reason,
            },
            "services": [
                {
                    "service_id": item.service_id,
                    "host_id": item.host_id,
                    "endpoint": item.endpoint,
                }
                for item in accepted.services
            ],
            "coordinators": [item.endpoint for item in accepted.coordinators],
            "redundancy_mode": accepted.redundancy_mode,
            "storage_engine": accepted.storage_engine,
        }
    raise ValueError("unsupported command")


def _result_dict(value: object) -> dict[str, Any]:
    return {
        "operation": getattr(value, "operation"),
        "stage": getattr(value, "stage"),
        "status": getattr(value, "status"),
        "details": dict(getattr(value, "details")),
    }


def _verification_dict(value: object) -> dict[str, Any]:
    return {
        "verified": getattr(value, "verified"),
        "service_id": getattr(value, "service_id"),
        "host_id": getattr(value, "host_id"),
        "coolify_service_name": getattr(value, "coolify_service_name"),
        "coolify_service_uuid": getattr(value, "coolify_service_uuid"),
        "coolify_status": getattr(value, "coolify_status"),
        "reason": getattr(value, "reason"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
