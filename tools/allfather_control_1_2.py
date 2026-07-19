#!/usr/bin/env python3
"""Allfather control-surface Stage 1/2 reconciler.

This script intentionally performs only the first two prerequisites for a clean
Allfather deployment:

Stage 1
    Reconcile the per-host Allfather control surface through the Coolify API.

Stage 2
    Force-redeploy that same control surface with the build request, then have it
    build and verify one real full-hub image on its host and report through Coolify metadata.

It never calls SSH, never calls 10-net/internal URLs from the local runner, never
creates one-off helper services for the build, and never deploys super-nodes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import allfather_control as afc


OPERATION = "allfather-control-1-2"
DEFAULT_OUTPUT_ROOT = afc.REPO_ROOT / "runtime" / "allfather-control-stage-1-2"
DEFAULT_BUILD_WAIT_S = 2400.0
DEFAULT_POLL_S = 5.0


def log(args: argparse.Namespace, message: str) -> None:
    print(f"[allfather {OPERATION}] {message}", flush=True)


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def clean_image_part(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip("-").lower()
    return cleaned or "unknown"


def b64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def redact_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, raw in value.items():
            lower = str(key).lower()
            if any(token in lower for token in ("token", "secret", "password", "private_key", "api_key")):
                redacted[str(key)] = "<redacted>" if raw not in (None, "") else raw
            elif str(key) in {"dockerfile_b64"}:
                redacted[str(key)] = "<base64>"
                redacted[str(key) + "_bytes"] = len(str(raw or ""))
            else:
                redacted[str(key)] = redact_mapping(raw)
        return redacted
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value



def ensure_afc_arg_compat(args: argparse.Namespace) -> None:
    """Populate CLI attributes expected by reused allfather_control helpers.

    This script has its own small CLI, but it deliberately reuses lower-level
    helpers from tools/allfather_control.py and tools/coolify_fdb_cluster.py.
    Those helpers were written for the full allfather_control.py argparse
    namespace, so keep every expected remote/Coolify attribute present even when
    this staged runner does not expose or use it directly.
    """

    defaults: dict[str, Any] = {
        "command": OPERATION,
        "json": False,
        "verbose": False,
        "progress": False,
        "quiet": False,
        "include_compose": False,
        "coolify_token": "",
        "coolify_token_env": afc.fdb_tool().DEFAULT_TOKEN_ENV,
        "coolify_token_file": "",
        "set_coolify_token": [],
        "set_coolify_token_env": [],
        "set_coolify_token_file": [],
        "set_coolify_url": [],
        "coolify_project_uuid": "",
        "set_coolify_project_uuid": [],
        "coolify_project_name": afc.DEFAULT_COOLIFY_PROJECT_NAME,
        "coolify_environment_name": afc.DEFAULT_CONTROL_ENVIRONMENT,
        "coolify_environment_uuid": "",
        "set_coolify_environment_uuid": [],
        "no_create_environment": False,
        "coolify_server_uuid": "",
        "coolify_server_name": "",
        "set_coolify_server_uuid": [],
        "set_coolify_server_name": [],
        "coolify_destination_uuid": "",
        "set_coolify_destination_uuid": [],
        "coolify_service_uuid": "",
        "set_coolify_service_uuid": [],
        "coolify_timeout_s": afc.DEFAULT_COOLIFY_TIMEOUT_S,
        "coolify_retries": afc.DEFAULT_COOLIFY_RETRIES,
        "coolify_retry_sleep_s": afc.DEFAULT_COOLIFY_RETRY_SLEEP_S,
        "guard_host_base": afc.DEFAULT_GUARD_HOST_BASE,
        "guard_container_port": afc.DEFAULT_GUARD_CONTAINER_PORT,
        "state_root_prefix": afc.DEFAULT_STATE_ROOT_PREFIX,
        "dockerfile": afc.DEFAULT_DOCKERFILE,
        "image": afc.DEFAULT_IMAGE,
    }
    for name, value in defaults.items():
        if not hasattr(args, name):
            if isinstance(value, list):
                value = list(value)
            setattr(args, name, value)


def selected_plan(plan: afc.HeadPlan, selected_hosts: Sequence[str]) -> afc.HeadPlan:
    if not selected_hosts:
        return plan
    wanted = {str(item).strip() for item in selected_hosts if str(item).strip()}
    heads = tuple(head for head in plan.heads if head.coolify_server in wanted or head.slot in wanted or head.head_id in wanted)
    matched = {head.coolify_server for head in heads} | {head.slot for head in heads} | {head.head_id for head in heads}
    missing = sorted(wanted - matched)
    if missing:
        raise afc.AllfatherControlError("Unknown selected host(s): " + ", ".join(missing))
    return afc.HeadPlan(
        kind=plan.kind,
        private_state_path=plan.private_state_path,
        heads=heads,
        desired_counts=plan.desired_counts,
        guardrails=plan.guardrails,
    )


def build_request_for_head(args: argparse.Namespace, head: afc.HeadNode, *, run_id: str, run_dir: Path) -> dict[str, Any]:
    network = afc.clean_node_network_key(args.network)
    image_tag = str(args.fullhub_image_tag or "").strip()
    if image_tag and len(args.host or []) > 1:
        raise afc.AllfatherControlError("--fullhub-image-tag is only safe with one selected host")
    if not image_tag:
        image_tag = (
            f"main-computer/allfather-fullhub:"
            f"{clean_image_part(network)}-{clean_image_part(head.coolify_server)}-{clean_image_part(run_id)}"
        )

    request_id = f"fullhub-image-build-{network}-{head.coolify_server}-{run_id}"
    build_id = f"{network}-{head.coolify_server}-{run_id}"
    context = afc.super_node_dockerfile_context_inline(
        getattr(args, "super_image", afc.DEFAULT_SUPER_IMAGE),
        build_id=build_id,
    )
    dockerfile = str(context.get("dockerfile") or "")
    context_files_b64 = context.get("context_files_b64") if isinstance(context.get("context_files_b64"), Mapping) else {}
    payload_manifest = context.get("payload_manifest") if isinstance(context.get("payload_manifest"), list) else []
    dockerfile_sha256 = hashlib.sha256(dockerfile.encode("utf-8")).hexdigest()
    context_manifest_sha256 = hashlib.sha256(
        json.dumps(
            {
                "files": {
                    str(path): hashlib.sha256(str(value or "").encode("ascii")).hexdigest()
                    for path, value in sorted(context_files_b64.items())
                },
                "payload_manifest": payload_manifest,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    dockerfile_path = run_dir / "dockerfiles" / f"{head.coolify_server}.fullhub.Dockerfile"
    dockerfile_path.parent.mkdir(parents=True, exist_ok=True)
    dockerfile_path.write_text(dockerfile, encoding="utf-8")
    write_json(
        run_dir / "dockerfiles" / f"{head.coolify_server}.fullhub-build-context-manifest.json",
        {
            "payload_mode": context.get("payload_mode") or "docker-build-context",
            "context_file_count": len(context_files_b64),
            "context_manifest_sha256": context_manifest_sha256,
            "payload_manifest": redact_mapping(payload_manifest),
        },
    )

    return {
        "kind": "main_computer.allfather.fullhub_image_build_request.v2",
        "service_name": "allfather-fullhub-image-build",
        "network": network,
        "host": head.coolify_server,
        "run_id": run_id,
        "request_id": request_id,
        "image_tag": image_tag,
        "build_id": build_id,
        "dockerfile_b64": b64_text(dockerfile),
        "dockerfile_sha256": dockerfile_sha256,
        "build_context_files_b64": dict(context_files_b64),
        "build_context_file_count": len(context_files_b64),
        "build_context_manifest_sha256": context_manifest_sha256,
        "payload_manifest": payload_manifest,
        "payload_mode": context.get("payload_mode") or "docker-build-context",
        "docker_timeout_s": int(args.docker_build_timeout_s),
        "verification": {
            "requires_hub_full_capability": True,
            "requires_pythonpath": "/opt/main-computer-src",
            "requires_hub_import": True,
            "requires_full_hub_runtime_sha256_file": True,
        },
        "transport": "allfather-control-surface-coolify-metadata",
        "local_runner_uses_ssh": False,
        "local_runner_uses_10_net": False,
    }



def reconcile_head_and_build_image(
    plan: afc.HeadPlan,
    head: afc.HeadNode,
    args: argparse.Namespace,
    *,
    build_request: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tried: list[dict[str, Any]] = []
    client, token_source = afc.fdb_tool().client_for_server(head.coolify_server, args)
    version = afc.request_coolify_version(client, tried, args=args, host=head.coolify_server)
    if not version.ok:
        raise afc.AllfatherControlError(
            f"Coolify API version check failed for {head.coolify_server!r} with HTTP {version.status}: {version.body}"
        )

    context = afc.resolve_context(client, args, head, tried)
    service_uuid, action, existing = afc.sync_head_service(
        client,
        plan,
        head,
        args,
        context,
        tried,
        probe_targets=afc.probe_target_records_for_plan(plan, super_inventory=[]),
        fullhub_image_build_request=build_request,
    )

    marker_prime = afc.prime_head_fullhub_image_build_marker(
        client,
        service_uuid,
        tried,
        build_request,
        args=args,
    )

    deploy_result = None
    if not (args.no_deploy_control or args.dry_run):
        log(args, f"stage1: triggering control-surface deploy host={head.coolify_server}")
        deploy_result = afc.hub_service_tool().trigger_deploy_service(
            client,
            service_uuid=service_uuid,
            force=True,
            tried=tried,
        )

    stage1 = {
        "host": head.coolify_server,
        "head_id": head.head_id,
        "service_name": head.service_name,
        "service_uuid": service_uuid,
        "service_action": action,
        "token_source": token_source,
        "context": redact_mapping(context),
        "guard_url": head.guard_url,
        "internal_guard_url_metadata_only": head.guard_url,
        "local_runner_probe_attempted": False,
        "local_runner_uses_ssh": False,
        "local_runner_uses_10_net": False,
        "deployed": deploy_result is not None,
        "deploy_result": redact_mapping(deploy_result),
        "existing": redact_mapping(existing),
        "marker_prime": redact_mapping(marker_prime),
        "tried": redact_mapping(tried),
    }

    if args.no_wait or args.dry_run:
        stage2 = {
            "host": head.coolify_server,
            "request_id": str(build_request.get("request_id") or ""),
            "image_tag": str(build_request.get("image_tag") or ""),
            "status": "submitted" if not args.dry_run else "planned",
            "verified": False,
            "reason": "--no-wait or --dry-run",
            "source": "coolify-service-description",
            "local_runner_probe_attempted": False,
            "local_runner_uses_ssh": False,
            "local_runner_uses_10_net": False,
        }
        return stage1, stage2

    log(args, f"stage2: waiting for control-surface fullhub image build marker host={head.coolify_server}")
    wait = afc.wait_for_head_fullhub_image_build_ready(
        client,
        service_uuid,
        tried,
        wait_s=float(args.build_wait_s),
        poll_s=float(args.poll_s),
        expected_request_id=str(build_request.get("request_id") or ""),
        args=args,
    )
    result = wait.get("result") if isinstance(wait.get("result"), Mapping) else {}
    image = result.get("image") if isinstance(result.get("image"), Mapping) else {}
    stage2 = {
        "host": head.coolify_server,
        "request_id": str(build_request.get("request_id") or ""),
        "image_tag": str(image.get("tag") or build_request.get("image_tag") or ""),
        "image_id": str(image.get("id") or ""),
        "image_created": str(image.get("created") or ""),
        "verified": bool(wait.get("ready")),
        "status": str(result.get("status") or result.get("phase") or ("ready" if wait.get("ready") else "failed")),
        "phase": str(result.get("phase") or ""),
        "reason": str(wait.get("reason") or result.get("reason") or result.get("error") or ""),
        "log_relay": redact_mapping(result.get("log_relay") if isinstance(result.get("log_relay"), Mapping) else {}),
        "build_log_tail": redact_mapping(result.get("build_log_tail") if isinstance(result.get("build_log_tail"), list) else []),
        "wait": redact_mapping(wait),
        "source": "coolify-service-description",
        "local_runner_probe_attempted": False,
        "local_runner_uses_ssh": False,
        "local_runner_uses_10_net": False,
    }
    return stage1, stage2


def run(args: argparse.Namespace) -> dict[str, Any]:
    ensure_afc_arg_compat(args)
    network = afc.clean_node_network_key(args.network)
    if network == "mainnet" and not args.allow_mainnet:
        raise afc.AllfatherControlError("Refusing to run mainnet stage 1/2 without --allow-mainnet.")

    run_id = args.run_id or utc_run_id()
    output_root = Path(args.output_root)
    run_dir = output_root / network / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    log(args, f"stage0: building desired control plan network={network}")
    plan = selected_plan(afc.build_plan_from_args(args), args.host or [])
    if not plan.heads:
        raise afc.AllfatherControlError("No selected heads remain after host filtering.")

    stage: dict[str, Any] = {
        "ok": False,
        "operation": OPERATION,
        "network": network,
        "run_id": run_id,
        "created_at": afc.utc_now_iso(),
        "run_dir": str(run_dir),
        "desired": {
            "hosts": [head.coolify_server for head in plan.heads],
            "control_services": [head.service_name for head in plan.heads],
            "super_node_deploys": False,
            "traefik_writes": False,
            "bootstrap_image": False,
            "fullhub_image_per_host": True,
        },
        "safety": {
            "coolify_api_only_from_local_runner": True,
            "local_runner_uses_ssh": False,
            "local_runner_uses_10_net": False,
            "control_surface_writes": not args.dry_run,
            "control_surface_deploys": not args.no_deploy_control and not args.dry_run,
            "control_surface_deploy_force_recreate": not args.no_deploy_control and not args.dry_run,
            "control_surface_docker_builds": not args.no_wait and not args.dry_run,
            "super_node_deploys": False,
            "super_node_recreates": False,
            "docker_tags_to_service_images": False,
            "traefik_writes": False,
            "bootstrap_image": False,
        },
        "stage1_control_surface": [],
        "stage2_fullhub_images": [],
        "next_stage_inputs": {
            "verified_fullhub_image_by_host": {},
            "control_service_uuid_by_host": {},
        },
        "errors": [],
        "skipped_hosts": [],
    }

    write_json(run_dir / "plan.json", plan.to_dict())

    for head in plan.heads:
        log(args, f"stage1: reconciling allfather control surface host={head.coolify_server}")
        build_request = build_request_for_head(args, head, run_id=run_id, run_dir=run_dir)
        write_json(
            run_dir / "build-requests" / f"{head.coolify_server}.fullhub-build-request.redacted.json",
            redact_mapping(build_request),
        )

        if args.dry_run:
            compose = afc.render_head_compose(
                plan,
                head,
                image=args.image,
                probe_targets=afc.probe_target_records_for_plan(plan, super_inventory=[]),
                fullhub_image_build_request=build_request,
            )
            compose_path = run_dir / "compose" / f"{head.coolify_server}.control.compose.yml"
            compose_path.parent.mkdir(parents=True, exist_ok=True)
            compose_path.write_text(compose, encoding="utf-8")
            stage1 = {
                "host": head.coolify_server,
                "service_name": head.service_name,
                "guard_url": head.guard_url,
                "internal_guard_url_metadata_only": head.guard_url,
                "local_runner_probe_attempted": False,
                "local_runner_uses_ssh": False,
                "local_runner_uses_10_net": False,
                "dry_run": True,
                "compose": str(compose_path),
                "service_action": "planned",
            }
            stage2 = {
                "host": head.coolify_server,
                "request_id": str(build_request.get("request_id") or ""),
                "image_tag": str(build_request.get("image_tag") or ""),
                "status": "planned",
                "verified": False,
                "dry_run": True,
                "source": "coolify-service-description",
            }
        else:
            stage1, stage2 = reconcile_head_and_build_image(plan, head, args, build_request=build_request)

        stage["stage1_control_surface"].append(stage1)
        stage["stage2_fullhub_images"].append(stage2)
        write_json(run_dir / "hosts" / f"{head.coolify_server}.stage1.json", stage1)
        write_json(run_dir / "hosts" / f"{head.coolify_server}.stage2.json", stage2)

        if stage1.get("service_uuid"):
            stage["next_stage_inputs"]["control_service_uuid_by_host"][head.coolify_server] = stage1.get("service_uuid")
        if stage2.get("verified"):
            stage["next_stage_inputs"]["verified_fullhub_image_by_host"][head.coolify_server] = {
                "tag": stage2.get("image_tag"),
                "id": stage2.get("image_id"),
                "created": stage2.get("image_created"),
                "request_id": stage2.get("request_id"),
            }
        elif not args.dry_run and not args.no_wait:
            stage["errors"].append(
                {
                    "host": head.coolify_server,
                    "stage": "stage2_fullhub_image",
                    "reason": stage2.get("reason") or "full-hub image was not verified",
                    "status": stage2.get("status"),
                }
            )
            remaining_heads = plan.heads[plan.heads.index(head) + 1 :]
            if remaining_heads:
                for skipped in remaining_heads:
                    skipped_record = {
                        "host": skipped.coolify_server,
                        "stage": "stage2_fullhub_image",
                        "status": "skipped",
                        "reason": f"prior full-hub image build failed on {head.coolify_server}; stopping Stage 2 fail-fast",
                    }
                    stage["skipped_hosts"].append(skipped_record)
                    stage["errors"].append(skipped_record)
                log(args, f"stage2: stopping after failed fullhub image build host={head.coolify_server}; skipped={','.join(item.coolify_server for item in remaining_heads)}")
            break

    stage["ok"] = not stage["errors"] and (
        args.dry_run
        or args.no_wait
        or len(stage["next_stage_inputs"]["verified_fullhub_image_by_host"]) == len(plan.heads)
    )
    write_json(run_dir / "stage_1_2.json", stage)
    latest = output_root / network / "latest-stage-1-2.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(stage, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "ok": bool(stage["ok"]),
        "operation": OPERATION,
        "network": network,
        "run_id": run_id,
        "selected_hosts": [head.coolify_server for head in plan.heads],
        "stage1_control_surface_count": len(stage["stage1_control_surface"]),
        "stage2_fullhub_image_count": len(stage["stage2_fullhub_images"]),
        "verified_fullhub_image_count": len(stage["next_stage_inputs"]["verified_fullhub_image_by_host"]),
        "skipped_hosts": stage.get("skipped_hosts", []),
        "errors": stage["errors"],
        "safety": stage["safety"],
        "output": {
            "run_dir": str(run_dir),
            "stage_file": str(run_dir / "stage_1_2.json"),
            "latest_file": str(latest),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 1/2 Allfather reconciler: deploy/update the control surface, then ask that same "
            "control surface to build and verify one real full-hub image per host. No SSH, no 10-net "
            "local probes, no helper services, no super-node deployment."
        )
    )
    parser.add_argument("network", choices=afc.SUPPORTED_NODE_NETWORKS)
    parser.add_argument("--allow-mainnet", action="store_true")
    parser.add_argument("--host", action="append", default=[], help="Coolify host name/slot/head id to include; repeatable.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--private-state", default=str(afc.DEFAULT_PRIVATE_STATE_PATH))
    parser.add_argument("--image", default=afc.DEFAULT_IMAGE, help="Image used for the Allfather control surface service.")
    parser.add_argument("--super-image", default=afc.DEFAULT_SUPER_IMAGE, help="Base image for the generated full-hub super-node image.")
    parser.add_argument("--fullhub-image-tag", default="", help="Explicit full-hub image tag; only allowed with one --host.")
    parser.add_argument("--docker-build-timeout-s", type=int, default=7200)
    parser.add_argument("--build-wait-s", type=float, default=DEFAULT_BUILD_WAIT_S)
    parser.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-deploy-control", action="store_true", help="Update/create the control service but do not trigger deploy.")
    parser.add_argument("--no-wait", action="store_true", help="Submit the control-surface build request and return without waiting.")
    parser.add_argument("--operator-log-interval-s", type=float, default=15.0)

    parser.add_argument("--dockerfile", default=afc.DEFAULT_DOCKERFILE)
    parser.add_argument("--guard-host-base", type=int, default=afc.DEFAULT_GUARD_HOST_BASE)
    parser.add_argument("--guard-container-port", type=int, default=afc.DEFAULT_GUARD_CONTAINER_PORT)
    parser.add_argument("--state-root-prefix", default=afc.DEFAULT_STATE_ROOT_PREFIX)

    parser.add_argument("--coolify-timeout-s", type=float, default=afc.DEFAULT_COOLIFY_TIMEOUT_S)
    parser.add_argument("--coolify-retries", type=int, default=afc.DEFAULT_COOLIFY_RETRIES)
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=afc.DEFAULT_COOLIFY_RETRY_SLEEP_S)
    parser.add_argument("--coolify-project-name", default=afc.DEFAULT_COOLIFY_PROJECT_NAME)
    parser.add_argument("--coolify-environment-name", default=afc.DEFAULT_CONTROL_ENVIRONMENT)
    parser.add_argument("--coolify-destination-uuid", default="")
    parser.add_argument("--set-coolify-destination-uuid", action="append", default=[])
    parser.add_argument("--coolify-service-uuid", default="")
    parser.add_argument("--set-coolify-service-uuid", action="append", default=[])
    parser.add_argument("--set-coolify-url", action="append", default=[])
    parser.add_argument("--coolify-url", default="")
    parser.add_argument("--coolify-token", default="", help="One Coolify token for every selected host. Prefer private-state token references.")
    parser.add_argument("--set-coolify-token", action="append", default=[], help="Per-host token. Format: <host>:<token>")
    parser.add_argument("--set-coolify-token-env", action="append", default=[])
    parser.add_argument("--set-coolify-token-file", action="append", default=[])
    parser.add_argument("--coolify-token-env", default=afc.fdb_tool().DEFAULT_TOKEN_ENV)
    parser.add_argument("--coolify-token-file", default="")

    parser.add_argument("--coolify-project-uuid", default="")
    parser.add_argument("--set-coolify-project-uuid", action="append", default=[])
    parser.add_argument("--coolify-environment-uuid", default="")
    parser.add_argument("--set-coolify-environment-uuid", action="append", default=[])
    parser.add_argument("--no-create-environment", action="store_true")
    parser.add_argument("--coolify-server-uuid", default="")
    parser.add_argument("--coolify-server-name", default="")
    parser.add_argument("--set-coolify-server-uuid", action="append", default=[])
    parser.add_argument("--set-coolify-server-name", action="append", default=[])

    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        payload = {
            "ok": False,
            "operation": OPERATION,
            "network": getattr(args, "network", ""),
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
