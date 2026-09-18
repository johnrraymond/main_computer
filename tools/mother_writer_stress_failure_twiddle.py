#!/usr/bin/env python3
"""Read-only follow-up for a preserved Mother writer stress failure.

This twiddle consumes the stress evidence, resolves the preserved Coolify
service through Mother's private controller state, and captures service detail,
runtime-log responses, and matching deployment/server-resource records. It
never uses SSH or executes Docker/host-shell commands. Instead it prints a
read-only command set that an operator can run manually on the Coolify host.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.request
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.mother_bootnode_precleanup as bootnode
import tools.mother_static_node_writer_stress_smoke as smoke


EVIDENCE_SUBDIR = smoke.EVIDENCE_SUBDIR


class FailureTwiddleError(RuntimeError):
    pass


def _latest_evidence(runtime_state_root: str | Path) -> Path:
    root = Path(runtime_state_root) / "mother" / "evidence" / EVIDENCE_SUBDIR
    candidates = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FailureTwiddleError(f"no writer stress evidence found under {root}")
    return candidates[0]


def _load_evidence(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FailureTwiddleError(f"evidence not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FailureTwiddleError(f"invalid evidence JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise FailureTwiddleError("writer stress evidence must be a JSON object")
    return payload


def _failed_result(evidence: Mapping[str, Any], iteration: int | None) -> Mapping[str, Any]:
    results = evidence.get("results")
    if not isinstance(results, list):
        raise FailureTwiddleError("evidence has no results list")

    if iteration is not None:
        for item in results:
            if isinstance(item, Mapping) and int(item.get("iteration") or 0) == iteration:
                if item.get("status") == "pass":
                    raise FailureTwiddleError(f"iteration {iteration} passed; no preserved failure to inspect")
                return item
        raise FailureTwiddleError(f"iteration {iteration} not found in evidence")

    for item in reversed(results):
        if isinstance(item, Mapping) and item.get("status") != "pass":
            return item
    raise FailureTwiddleError("evidence contains no failed iteration")


def _resource_for_role(result: Mapping[str, Any], role: str) -> tuple[str, str, str]:
    resources = result.get("failed_resources_preserved")
    resources = resources if isinstance(resources, Mapping) else {}
    selected_role = smoke._failure_focus_role(result) if role == "auto" else role
    service_uuid = str(resources.get(f"{selected_role}_service_uuid") or "").strip()
    service_name = str(resources.get(f"{selected_role}_service_name") or "").strip()
    if not service_uuid or not service_name:
        raise FailureTwiddleError(
            f"preserved {selected_role} service is unavailable in this failure"
        )
    return selected_role, service_uuid, service_name


def _application_names(
    snapshot: Mapping[str, Any],
    *,
    fallback: Sequence[str],
) -> list[str]:
    names: list[str] = []
    for record in snapshot.get("service_records") or []:
        if not isinstance(record, Mapping):
            continue
        path = str(record.get("path") or "")
        name = str(record.get("name") or "").strip()
        if path.startswith("$.applications[") and name and name not in names:
            names.append(name)
    for name in fallback:
        clean = str(name or "").strip()
        if clean and clean not in names:
            names.append(clean)
    return names


def inspect_failure(
    *,
    evidence_path: str | Path,
    runtime_state_root: str | Path,
    role: str,
    iteration: int | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    path = Path(evidence_path)
    evidence = _load_evidence(path)
    result = _failed_result(evidence, iteration)
    selected_role, service_uuid, service_name = _resource_for_role(result, role)

    network = str(evidence.get("network") or "mainnet")
    controller_id = str(evidence.get("controller_id") or result.get("controller_id") or "").strip()
    if not controller_id:
        raise FailureTwiddleError("controller_id missing from evidence")

    private_state = bootnode._load_private_state(
        runtime_state_root,
        network=network,
        mode="execute",
    )
    controller = bootnode._controller(
        private_state,
        network=network,
        controller_id=controller_id,
    )
    controller_meta = bootnode._controller_config(
        private_state,
        network=network,
        controller_id=controller_id,
    )

    response, snapshot = smoke._detail(
        controller=controller,
        service_uuid=service_uuid,
        service_name=service_name,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        label="writer-stress-failure-twiddle",
    )
    logs = smoke._runtime_logs(
        controller=controller,
        service_uuid=service_uuid,
        service_name=service_name,
        service_detail_payload=response.get("payload"),
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        application_name=service_name,
    )
    inventory = smoke._matching_inventory(
        controller=controller,
        controller_meta=controller_meta,
        service_uuid=service_uuid,
        service_name=service_name,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )

    fallback_names = smoke._host_application_names(result, selected_role)
    application_names = _application_names(snapshot, fallback=fallback_names)
    host_commands = smoke._host_inspection_commands(
        service_uuid=service_uuid,
        service_name=service_name,
        application_names=application_names,
        started_at=str(result.get("started_at") or "") or None,
        completed_at=str(result.get("completed_at") or evidence.get("completed_at") or "") or None,
    )

    return {
        "clean": True,
        "twiddle": "mother-writer-stress-failure",
        "read_only": True,
        "network": network,
        "controller_id": controller_id,
        "server_uuid": controller_meta.get("server_uuid"),
        "evidence_path": str(path),
        "iteration": result.get("iteration"),
        "failure_reason": result.get("reason"),
        "role": selected_role,
        "service_uuid": service_uuid,
        "service_name": service_name,
        "service_detail_snapshot": snapshot,
        "runtime_logs": logs,
        "inventory": inventory,
        "coolify_host_inspection": {
            "run_on_controller_host": controller_id,
            "service_dir": f"/data/coolify/services/{service_uuid}",
            "application_names": application_names,
            "commands": host_commands,
        },
        "policy": {
            "coolify_api_requests": "GET-only",
            "ssh_used": False,
            "host_shell_used": False,
            "live_mutation_performed": False,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Coolify/API follow-up for a preserved Mother static-node "
            "writer stress failure."
        )
    )
    parser.add_argument(
        "--evidence",
        default="",
        help="writer stress evidence JSON; default is newest evidence under runtime state",
    )
    parser.add_argument("--runtime-state-root", default="runtime/state")
    parser.add_argument("--role", choices=["auto", "target", "observer", "writer"], default="auto")
    parser.add_argument("--iteration", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--max-response-bytes",
        type=int,
        default=bootnode.DEFAULT_MAX_RESPONSE_BYTES,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        evidence_path = Path(args.evidence) if args.evidence else _latest_evidence(args.runtime_state_root)
        payload = inspect_failure(
            evidence_path=evidence_path,
            runtime_state_root=args.runtime_state_root,
            role=args.role,
            iteration=args.iteration,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
    except (FailureTwiddleError, bootnode.MotherBootnodePrecleanupError) as exc:
        print(
            json.dumps(
                {
                    "clean": False,
                    "twiddle": "mother-writer-stress-failure",
                    "error": str(exc),
                    "read_only": True,
                    "ssh_used": False,
                    "host_shell_used": False,
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
