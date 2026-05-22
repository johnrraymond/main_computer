#!/usr/bin/env python3
"""Verify which local Coolify stack the start code actually targets.

This is intentionally a twiddle, not the production Publish path.

It asks ``main_computer.applications_service.ApplicationsService`` to generate
the same runtime env files that ``start.bat`` eventually uses, then compares
those generated targets against docker-compose and, optionally, live Docker.

The destructive command is deliberately explicit because it removes the
start-code-derived Coolify containers and can remove the generated Coolify bind
state directory. It still targets only the containers and state path derived
from the current repository/runtime configuration.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


COOLIFY_ENV_KEYS = {
    "coolify": "COOLIFY_CONTAINER_NAME",
    "postgres": "COOLIFY_POSTGRES_CONTAINER_NAME",
    "redis": "COOLIFY_REDIS_CONTAINER_NAME",
    "soketi": "COOLIFY_SOKETI_CONTAINER_NAME",
}

STACK_ENV_KEYS = [
    "COOLIFY_COMPOSE_PROJECT",
    "COOLIFY_LOCAL_STATE",
    "COOLIFY_SOURCE_ENV_FILE",
    "COOLIFY_CONTAINER_NAME",
    "COOLIFY_POSTGRES_CONTAINER_NAME",
    "COOLIFY_REDIS_CONTAINER_NAME",
    "COOLIFY_SOKETI_CONTAINER_NAME",
    "COOLIFY_NETWORK_NAME",
    "APP_PORT",
    "SOKETI_PORT",
    "SOKETI_TERMINAL_PORT",
]

COMPOSE_REQUIRED_LITERALS = {
    "coolify container env": "${COOLIFY_CONTAINER_NAME",
    "postgres container env": "${COOLIFY_POSTGRES_CONTAINER_NAME",
    "redis container env": "${COOLIFY_REDIS_CONTAINER_NAME",
    "soketi container env": "${COOLIFY_SOKETI_CONTAINER_NAME",
    "coolify app port": "${APP_PORT",
    "soketi websocket port": "${SOKETI_PORT",
    "soketi terminal port": "${SOKETI_TERMINAL_PORT",
    "coolify local state": "${COOLIFY_LOCAL_STATE",
    "coolify env file": "${COOLIFY_SOURCE_ENV_FILE",
}

START_CHAIN_FILES = [
    "start.bat",
    "scripts/main-computer-start-stop.ps1",
    "main_computer/app_control.py",
    "main_computer/applications_service.py",
    "docker-compose.applications.yml",
]


class TwiddleError(RuntimeError):
    """Raised for expected twiddle failures."""


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def command_display(command: list[str]) -> str:
    return " ".join(_quote_command_part(part) for part in command)


def _quote_command_part(value: object) -> str:
    text = str(value)
    if not text:
        return '""'
    if re.search(r'[\s"]', text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def repo_import(repo_root: Path) -> None:
    repo_text = str(repo_root)
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)


def load_applications_service(repo_root: Path):
    repo_import(repo_root)
    try:
        from main_computer.applications_service import ApplicationsService
    except Exception as exc:  # pragma: no cover - environment-specific import failure.
        raise TwiddleError(f"Could not import ApplicationsService from {repo_root}: {exc}") from exc
    return ApplicationsService


def parse_env_text(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_env_text(path.read_text(encoding="utf-8", errors="replace"))


def safe_relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def run_command(command: list[str], *, cwd: Path, timeout: float = 30.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "command": command,
            "display": command_display(command),
            "stdout": truncate(completed.stdout),
            "stderr": truncate(completed.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        stdout = getattr(exc, "stdout", "") or getattr(exc, "output", "") or ""
        stderr = getattr(exc, "stderr", "") or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "ok": False,
            "returncode": 124,
            "command": command,
            "display": command_display(command),
            "stdout": truncate(stdout),
            "stderr": truncate((stderr + "\n" if stderr else "") + f"timed out after {timeout:g} seconds"),
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": 127,
            "command": command,
            "display": command_display(command),
            "stdout": "",
            "stderr": str(exc),
        }


def truncate(value: object, limit: int = 5000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def tail_text(value: object, limit: int = 1200) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return "...[tail]\n" + text[-limit:]


def command_failure_brief(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result or result.get("ok") is not False:
        return None
    return {
        "returncode": result.get("returncode"),
        "display": result.get("display") or command_display(result.get("command") or []),
        "stdout_tail": tail_text(result.get("stdout", "")),
        "stderr_tail": tail_text(result.get("stderr", "")),
    }


def verify_check(
    name: str,
    ok: object,
    *,
    required: bool = True,
    details: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact, user-facing verification check record."""

    record: dict[str, Any] = {
        "name": name,
        "ok": bool(ok),
        "required": bool(required),
    }
    if details:
        record["details"] = details
    if failure:
        record["failure"] = failure
    return record


def build_verify_checks(
    start_chain: dict[str, Any],
    derived: dict[str, Any],
    compose_static: dict[str, Any],
    docker: dict[str, Any],
    api: dict[str, Any] | None = None,
    *,
    check_docker: bool,
    check_api: bool = False,
    check_server_readiness: bool = False,
) -> list[dict[str, Any]]:
    """Return the exact checks used to explain top-level verify success/failure."""

    target = derived.get("target") if isinstance(derived.get("target"), dict) else {}
    checks = [
        verify_check(
            "start_chain",
            start_chain.get("ok"),
            details={
                "missing_files": [
                    rel
                    for rel, item in (start_chain.get("files") or {}).items()
                    if not (isinstance(item, dict) and item.get("exists"))
                ],
                "failed_markers": [
                    label
                    for label, ok in (start_chain.get("markers") or {}).items()
                    if not ok
                ],
            },
        ),
        verify_check(
            "derived_targets",
            derived.get("ok"),
            details={
                "env_file": derived.get("env_file"),
                "compose_file": derived.get("compose_file"),
                "compose_project": target.get("compose_project"),
                "missing_required_env_keys": derived.get("missing_required_env_keys") or [],
                "missing_container_env_keys": target.get("missing_container_env_keys") or [],
            },
        ),
        verify_check(
            "compose_static",
            compose_static.get("ok"),
            details={
                "compose_file": compose_static.get("compose_file"),
                "missing": compose_static.get("missing") or [],
            },
        ),
    ]

    if not check_docker:
        return checks

    docker_version = docker.get("docker_version") if isinstance(docker.get("docker_version"), dict) else {}
    compose_config = docker.get("compose_config") if isinstance(docker.get("compose_config"), dict) else {}
    compose_ps = docker.get("compose_ps") if isinstance(docker.get("compose_ps"), dict) else {}
    health = docker.get("health") if isinstance(docker.get("health"), dict) else {}

    checks.extend(
        [
            verify_check(
                "docker_version",
                docker_version.get("ok"),
                failure=command_failure_brief(docker_version),
            ),
            verify_check(
                "compose_config",
                compose_config.get("ok"),
                failure=command_failure_brief(compose_config),
            ),
            verify_check(
                "live_containers_present",
                not docker.get("missing_containers"),
                details={"missing_containers": docker.get("missing_containers") or []},
            ),
            verify_check(
                "live_container_inspect",
                not docker.get("inspect_failures"),
                details={"inspect_failures": docker.get("inspect_failures") or {}},
            ),
            verify_check(
                "live_containers_running",
                not docker.get("non_running_containers"),
                details={"non_running_containers": docker.get("non_running_containers") or {}},
            ),
            verify_check(
                "coolify_app_port_bound",
                docker.get("coolify_app_port_bound"),
                details={
                    "app_port": target.get("app_port"),
                    "coolify_container": (target.get("containers") or {}).get("coolify")
                    if isinstance(target.get("containers"), dict)
                    else None,
                },
            ),
            verify_check(
                "coolify_health",
                health.get("ok") is not False,
                required=bool(health.get("state") != "skipped"),
                details={
                    "state": health.get("state"),
                    "url": health.get("url"),
                    "status": health.get("status"),
                    "error": health.get("error"),
                },
            ),
            verify_check(
                "compose_ps",
                compose_ps.get("ok"),
                required=False,
                failure=command_failure_brief(compose_ps),
            ),
        ]
    )

    api_report = api if isinstance(api, dict) else {}
    if check_api or check_server_readiness:
        token = api_report.get("token") if isinstance(api_report.get("token"), dict) else {}
        controller = api_report.get("controller") if isinstance(api_report.get("controller"), dict) else {}
        endpoints = api_report.get("endpoints") if isinstance(api_report.get("endpoints"), dict) else {}

        checks.extend(
            [
                verify_check(
                    "coolify_token_source",
                    token.get("ok"),
                    details={
                        "selected_source": token.get("selected_source"),
                        "selected_path": token.get("selected_path"),
                        "state_token_exists": token.get("state_token_exists"),
                        "repo_token_exists": token.get("repo_token_exists"),
                        "token_length": token.get("token_length"),
                    },
                ),
                verify_check(
                    "coolify_controller_config",
                    controller.get("ok"),
                    details={
                        "base_url": controller.get("base_url"),
                        "expected_base_url": controller.get("expected_base_url"),
                        "token_ref": controller.get("token_ref"),
                        "token_env_loaded": controller.get("token_env_loaded"),
                        "error": controller.get("error"),
                    },
                ),
            ]
        )

        for endpoint in ["/api/v1/projects", "/api/v1/servers", "/api/v1/services", "/api/v1/deployments"]:
            item = endpoints.get(endpoint) if isinstance(endpoints.get(endpoint), dict) else {}
            checks.append(
                verify_check(
                    f"coolify_api_{endpoint.rsplit('/', 1)[-1]}",
                    item.get("ok"),
                    details={
                        "path": endpoint,
                        "status": item.get("status"),
                        "count": item.get("count"),
                        "error": item.get("error"),
                    },
                )
            )

    if check_server_readiness:
        readiness = api_report.get("server_readiness") if isinstance(api_report.get("server_readiness"), dict) else {}
        checks.append(
            verify_check(
                "coolify_server_readiness",
                readiness.get("ok"),
                details={
                    "usable_count": readiness.get("usable_count"),
                    "reachable_count": readiness.get("reachable_count"),
                    "servers": readiness.get("servers"),
                    "message": readiness.get("message"),
                },
            )
        )

    return checks


def required_checks_ok(checks: list[dict[str, Any]]) -> bool:
    return all(bool(check.get("ok")) for check in checks if check.get("required") is not False)


def failed_required_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        check
        for check in checks
        if check.get("required") is not False and not check.get("ok")
    ]


def parse_json_object(value: object) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def docker_missing_object(result: dict[str, Any]) -> bool:
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
    missing_markers = (
        "no such object",
        "no such container",
        "no such container:",
        "no such container",
        "error: no such",
        "not found",
    )
    return any(marker in text for marker in missing_markers)


def docker_container_presence(name: str, *, repo_root: Path, docker_command: str, timeout: float) -> dict[str, Any]:
    inspect = run_command([docker_command, "container", "inspect", name], cwd=repo_root, timeout=timeout)
    if inspect.get("ok"):
        return {
            "ok": True,
            "name": name,
            "exists": True,
            "inspect": inspect,
        }
    if docker_missing_object(inspect):
        return {
            "ok": True,
            "name": name,
            "exists": False,
            "inspect": inspect,
            "state": "already-absent",
        }
    return {
        "ok": False,
        "name": name,
        "exists": False,
        "inspect": inspect,
        "state": "inspect-failed",
    }


def meaningful_inspect_failures(containers: dict[str, Any]) -> dict[str, Any]:
    """Return only actionable inspect failures.

    Older versions of this twiddle could build ``{"container": None}`` entries,
    which made a healthy stack fail verification even though there was no
    concrete inspect failure to report. Null/empty details are informational
    noise, not a failed required check.
    """

    failures: dict[str, Any] = {}
    failure_states = {"inspect-failed", "inspect-parse-failed", "inspect-empty"}
    for item in containers.values():
        if not isinstance(item, dict) or item.get("exists"):
            continue
        state = str(item.get("state") or "")
        if state in {"missing", "already-absent"}:
            continue
        failure = command_failure_brief(item.get("inspect") if isinstance(item.get("inspect"), dict) else None)
        name = str(item.get("name") or "<unknown>")
        if failure:
            if state:
                failure = {**failure, "state": state}
            failures[name] = failure
        elif state in failure_states:
            failures[name] = {
                "state": state,
                "parse_error": item.get("parse_error"),
            }
    return failures


def state_dir_is_derived_local_coolify_path(state_dir_text: str) -> bool:
    normalized = str(state_dir_text or "").replace("\\", "/").strip().lower()
    normalized = re.sub(r"/+", "/", normalized)
    if not normalized:
        return False
    if not normalized.endswith("/coolify-local-docker"):
        return False
    return "/.main-computer-tools/instances/" in normalized


def build_start_chain_report(repo_root: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for rel in START_CHAIN_FILES:
        path = repo_root / rel
        files[rel] = {
            "exists": path.is_file(),
            "path": str(path),
        }

    markers = {
        "start.bat calls start-stop helper": "scripts\\main-computer-start-stop.ps1",
        "start-stop helper bootstraps app_control": "main_computer.app_control",
        "app_control uses ServiceSupervisor": "ServiceSupervisor",
        "applications service uses compose file": "docker-compose.applications.yml",
    }
    marker_results: dict[str, bool] = {}
    haystack_parts: list[str] = []
    for rel in START_CHAIN_FILES:
        path = repo_root / rel
        if path.is_file():
            haystack_parts.append(path.read_text(encoding="utf-8", errors="replace"))
    haystack = "\n".join(haystack_parts)
    for label, marker in markers.items():
        marker_results[label] = marker in haystack

    ok = all(item["exists"] for item in files.values()) and all(marker_results.values())
    return {
        "ok": ok,
        "files": files,
        "markers": marker_results,
    }


def build_compose_static_report(repo_root: Path, compose_file: Path) -> dict[str, Any]:
    if not compose_file.exists():
        return {
            "ok": False,
            "compose_file": str(compose_file),
            "missing": ["compose file"],
            "message": "docker-compose.applications.yml was not found",
        }
    text = compose_file.read_text(encoding="utf-8", errors="replace")
    missing = [label for label, literal in COMPOSE_REQUIRED_LITERALS.items() if literal not in text]
    return {
        "ok": not missing,
        "compose_file": str(compose_file),
        "repo_relative": safe_relpath(compose_file, repo_root),
        "missing": missing,
        "required_literals": COMPOSE_REQUIRED_LITERALS,
    }


def expected_targets_from_env(env: dict[str, str]) -> dict[str, Any]:
    containers = {
        service: env.get(env_key, "").strip()
        for service, env_key in COOLIFY_ENV_KEYS.items()
    }
    missing_container_keys = [
        env_key for service, env_key in COOLIFY_ENV_KEYS.items()
        if not containers.get(service)
    ]

    return {
        "compose_project": env.get("COOLIFY_COMPOSE_PROJECT", "").strip(),
        "local_state": env.get("COOLIFY_LOCAL_STATE", "").strip(),
        "source_env_file": env.get("COOLIFY_SOURCE_ENV_FILE", "").strip(),
        "network": env.get("COOLIFY_NETWORK_NAME", "").strip(),
        "app_port": env.get("APP_PORT", "").strip(),
        "soketi_port": env.get("SOKETI_PORT", "").strip(),
        "soketi_terminal_port": env.get("SOKETI_TERMINAL_PORT", "").strip(),
        "containers": containers,
        "container_names": [name for name in containers.values() if name],
        "missing_container_env_keys": missing_container_keys,
    }


def derive_start_targets(repo_root: Path, *, docker_command: str = "docker", generate_env: bool = True) -> dict[str, Any]:
    ApplicationsService = load_applications_service(repo_root)
    service = ApplicationsService(root=repo_root, docker_command=docker_command, output_func=None)

    env_component: dict[str, Any] = {
        "ok": True,
        "state": "already-present",
        "message": "using existing applications service env file",
    }
    if generate_env:
        env_component = service._ensure_env_files()  # type: ignore[attr-defined]

    env_file = Path(service.env_file)
    compose_file = Path(service.compose_file)
    env_values = read_env_file(env_file)
    target = expected_targets_from_env(env_values)

    compose_config_command = service._compose_command("config", "--quiet")  # type: ignore[attr-defined]
    compose_up_command = service._compose_command("up", "-d", "--remove-orphans", "postgres", "redis", "soketi", "coolify")  # type: ignore[attr-defined]
    compose_ps_command = service._compose_command("ps", "--format", "json")  # type: ignore[attr-defined]

    missing_required = [key for key in STACK_ENV_KEYS if not env_values.get(key)]
    return {
        "ok": bool(env_component.get("ok") and not missing_required and not target["missing_container_env_keys"]),
        "env_component": env_component,
        "env_file": str(env_file),
        "env_file_repo_relative": safe_relpath(env_file, repo_root),
        "compose_file": str(compose_file),
        "compose_file_repo_relative": safe_relpath(compose_file, repo_root),
        "compose_project_from_service": service.compose_project,
        "env": {key: env_values.get(key, "") for key in STACK_ENV_KEYS},
        "target": target,
        "missing_required_env_keys": missing_required,
        "planned_commands": {
            "compose_config": compose_config_command,
            "compose_up_coolify_core": compose_up_command,
            "compose_ps": compose_ps_command,
        },
    }


DOCKER_INSPECT_SUMMARY_FORMAT = "\n".join(
    [
        "ID={{.Id}}",
        "IMAGE={{.Config.Image}}",
        "STATUS={{.State.Status}}",
        "RUNNING={{.State.Running}}",
        # Do not read optional .State.Health here. Docker's Go templates error on
        # missing map keys for containers without health checks. The twiddle already
        # verifies Coolify readiness through /api/health when --check-health is used.
        "NETWORKS={{range $name, $_ := .NetworkSettings.Networks}}{{$name}},{{end}}",
        # Do not read .Mounts[].Name. Bind mounts may omit it, and Docker treats the
        # missing map key as a template error. Type/source/destination are enough to
        # prove the start-code-derived bind state path without parsing full inspect JSON.
        "MOUNTS={{range .Mounts}}{{.Type}}|{{.Source}}|{{.Destination}};;{{end}}",
    ]
)


def _clean_docker_template_value(value: object) -> str:
    text = str(value or "").strip()
    return "" if text == "<no value>" else text


def parse_docker_inspect_summary(text: object) -> dict[str, Any]:
    """Parse the compact inspect summary produced by DOCKER_INSPECT_SUMMARY_FORMAT.

    The twiddle previously parsed full ``docker inspect`` JSON from ``run_command``.
    ``run_command`` deliberately truncates large stdout in reports, which made valid
    Docker inspect output invalid JSON. This parser consumes only focused fields that
    are small enough to keep in the report safely.
    """

    fields: dict[str, str] = {}
    for line in str(text or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key.strip().upper()] = value.strip()

    networks = [
        item.strip()
        for item in fields.get("NETWORKS", "").split(",")
        if item.strip()
    ]
    mounts: list[dict[str, str]] = []
    for raw_mount in fields.get("MOUNTS", "").split(";;"):
        raw_mount = raw_mount.strip()
        if not raw_mount:
            continue
        parts = raw_mount.split("|")
        # Current compact inspect emits type|source|destination because Docker's
        # templates fail on missing .Mounts[].Name for bind mounts. Keep support for
        # the older four-column type|name|source|destination shape so reports written
        # by previous twiddle versions remain parseable in tests and local debugging.
        if len(parts) >= 4:
            mount_type, mount_name, source, destination = parts[:4]
        else:
            while len(parts) < 3:
                parts.append("")
            mount_type, source, destination = parts[:3]
            mount_name = ""
        mounts.append(
            {
                "Type": _clean_docker_template_value(mount_type),
                "Name": _clean_docker_template_value(mount_name),
                "Source": _clean_docker_template_value(source),
                "Destination": _clean_docker_template_value(destination),
            }
        )

    return {
        "id": fields.get("ID", "")[:12],
        "image": fields.get("IMAGE", ""),
        "status": fields.get("STATUS", ""),
        "running": fields.get("RUNNING", "").lower() == "true",
        "health": fields.get("HEALTH", ""),
        "labels": {},
        "mounts": mounts,
        "networks": sorted(networks),
    }


def docker_inspect_container(name: str, *, repo_root: Path, docker_command: str, timeout: float) -> dict[str, Any]:
    result = run_command(
        [docker_command, "container", "inspect", "--format", DOCKER_INSPECT_SUMMARY_FORMAT, name],
        cwd=repo_root,
        timeout=timeout,
    )
    payload: dict[str, Any] = {
        "name": name,
        "exists": False,
        "inspect": result,
    }
    if not result["ok"]:
        payload["state"] = "missing" if docker_missing_object(result) else "inspect-failed"
        return payload

    parsed = parse_docker_inspect_summary(result.get("stdout") or "")
    if not parsed.get("id"):
        payload["state"] = "inspect-empty"
        return payload

    payload.update(
        {
            "exists": True,
            "state": "found",
            **parsed,
        }
    )
    return payload



def parse_port_bindings(raw: str) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        if " -> " in text:
            left, right = text.split(" -> ", 1)
            bindings.append({"container_port": left.strip(), "host_binding": right.strip()})
        else:
            bindings.append({"raw": text})
    return bindings


def docker_live_report(
    repo_root: Path,
    target: dict[str, Any],
    planned_commands: dict[str, list[str]],
    *,
    docker_command: str,
    timeout: float,
    check_health: bool,
) -> dict[str, Any]:
    docker_version = run_command([docker_command, "version"], cwd=repo_root, timeout=timeout)
    compose_config = run_command(planned_commands["compose_config"], cwd=repo_root, timeout=timeout)

    containers: dict[str, Any] = {}
    expected_network = str(target.get("network") or "")
    for service, name in (target.get("containers") or {}).items():
        if not name:
            continue
        item = docker_inspect_container(str(name), repo_root=repo_root, docker_command=docker_command, timeout=timeout)
        if expected_network and item.get("exists"):
            item["on_expected_network"] = expected_network in item.get("networks", [])
        containers[service] = item

    ports: dict[str, Any] = {}
    for service, name in (target.get("containers") or {}).items():
        if not name:
            continue
        result = run_command([docker_command, "port", str(name)], cwd=repo_root, timeout=timeout)
        ports[service] = {
            **result,
            "bindings": parse_port_bindings(result.get("stdout", "")),
        }

    app_port = str(target.get("app_port") or "")
    coolify_port_text = ports.get("coolify", {}).get("stdout", "")
    coolify_app_port_bound = bool(app_port and re.search(rf"(^|[:\s]){re.escape(app_port)}($|\s)", coolify_port_text))

    compose_ps = run_command(planned_commands["compose_ps"], cwd=repo_root, timeout=timeout)
    health: dict[str, Any] = {"ok": None, "state": "skipped"}
    if check_health and app_port:
        health = http_health_report(app_port, timeout=timeout)

    expected_names = set(target.get("container_names") or [])
    existing_names = {str(item.get("name")) for item in containers.values() if item.get("exists")}
    port_observed_names = {
        str(target.get("containers", {}).get(service))
        for service, result in ports.items()
        if result.get("ok") and target.get("containers", {}).get(service)
    }
    observed_names = existing_names | port_observed_names
    missing_containers = sorted(expected_names - observed_names)
    inspect_failures = meaningful_inspect_failures(containers)
    non_running = {
        str(item.get("name")): item.get("status")
        for item in containers.values()
        if item.get("exists") and not item.get("running")
    }

    ok = bool(
        docker_version.get("ok")
        and compose_config.get("ok")
        and not missing_containers
        and not inspect_failures
        and not non_running
        and coolify_app_port_bound
        and (health.get("ok") is not False)
    )

    return {
        "ok": ok,
        "docker_version": docker_version,
        "compose_config": compose_config,
        "compose_ps": compose_ps,
        "containers": containers,
        "ports": ports,
        "expected_network": expected_network,
        "observed_containers": sorted(observed_names),
        "missing_containers": missing_containers,
        "inspect_failures": inspect_failures,
        "non_running_containers": non_running,
        "coolify_app_port_bound": coolify_app_port_bound,
        "health": health,
    }


def http_health_report(app_port: str, *, timeout: float) -> dict[str, Any]:
    url = f"http://127.0.0.1:{app_port}/api/health"
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(5000).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "url": url,
                "status": int(response.status),
                "elapsed_s": round(time.monotonic() - started, 3),
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(5000).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "url": url,
            "status": int(exc.code),
            "elapsed_s": round(time.monotonic() - started, 3),
            "body": body,
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": str(exc),
        }


def parse_coolify_token_text(raw: object) -> str:
    """Return the token from either a raw token file or a token=<value> file."""

    text = str(raw or "").strip()
    match = re.search(r"(?im)^\s*token\s*=\s*(.+?)\s*$", text)
    if match:
        return match.group(1).strip().strip('"').strip("'")
    return text


def coolify_token_report(repo_root: Path, target: dict[str, Any]) -> dict[str, Any]:
    """Load the token from the derived Coolify state dir, not stale repo-local state."""

    local_state = str(target.get("local_state") or "").strip()
    state_token_path = Path(local_state) / "api-token.txt" if local_state else None
    repo_token_path = repo_root / "runtime" / "coolify-local-docker" / "api-token.txt"

    state_exists = bool(state_token_path and state_token_path.exists())
    repo_exists = repo_token_path.exists()

    selected_source = ""
    selected_path: Path | None = None
    message = ""
    if state_exists and state_token_path is not None:
        selected_source = "derived_local_state"
        selected_path = state_token_path
        message = "using token from COOLIFY_LOCAL_STATE/api-token.txt"
    elif repo_exists:
        selected_source = "repo_runtime_fallback"
        selected_path = repo_token_path
        message = "using repo runtime token fallback because derived local-state token was missing"
    else:
        message = "no token file found in derived local state or repo runtime fallback"

    token = ""
    read_error = ""
    if selected_path is not None:
        try:
            token = parse_coolify_token_text(selected_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            read_error = str(exc)

    return {
        "ok": bool(token and not read_error and selected_source == "derived_local_state"),
        "selected_source": selected_source,
        "selected_path": str(selected_path) if selected_path else "",
        "state_token_exists": state_exists,
        "state_token_path": str(state_token_path) if state_token_path else "",
        "repo_token_exists": repo_exists,
        "repo_token_path": str(repo_token_path),
        "token_length": len(token),
        "token_loaded": bool(token),
        "read_error": read_error,
        "message": message,
        # Keep the secret out of summaries and final reports unless a caller
        # explicitly looks inside this private in-memory key before write_report.
        "_token": token,
    }


def load_coolify_controller_report(repo_root: Path, target: dict[str, Any], token_ref_value: str) -> dict[str, Any]:
    expected_base_url = f"http://127.0.0.1:{target.get('app_port')}" if target.get("app_port") else ""
    try:
        repo_import(repo_root)
        from main_computer.deployment_controllers import load_deployment_controller_registry

        registry = load_deployment_controller_registry(repo_root)
        controller = registry.get("coolify-local")
        data = controller.to_dict()
        base_url = str(data.get("base_url") or "").rstrip("/")
        token_ref = str(data.get("token_ref") or "")
        if token_ref and token_ref_value:
            os.environ[token_ref] = token_ref_value
        return {
            "ok": bool(base_url == expected_base_url and token_ref and token_ref_value),
            "base_url": base_url,
            "expected_base_url": expected_base_url,
            "token_ref": token_ref,
            "token_env_loaded": bool(token_ref and os.environ.get(token_ref)),
            "controller": data,
        }
    except Exception as exc:  # pragma: no cover - environment-specific import failure.
        return {
            "ok": False,
            "expected_base_url": expected_base_url,
            "error": str(exc),
        }


def http_api_json_report(base_url: str, path: str, token: str, *, timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(20000).decode("utf-8", errors="replace")
            parsed = parse_json_object(body)
            count = len(parsed) if isinstance(parsed, list) else None
            return {
                "ok": 200 <= int(response.status) < 300,
                "path": path,
                "url": url,
                "status": int(response.status),
                "elapsed_s": round(time.monotonic() - started, 3),
                "count": count,
                "sample": truncate(body, 400),
                "json": parsed,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(5000).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "path": path,
            "url": url,
            "status": int(exc.code),
            "elapsed_s": round(time.monotonic() - started, 3),
            "body": truncate(body, 400),
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": path,
            "url": url,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": str(exc),
        }


def boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def summarize_coolify_servers(servers_payload: object) -> dict[str, Any]:
    servers = servers_payload if isinstance(servers_payload, list) else []
    summarized: list[dict[str, Any]] = []
    for item in servers:
        if not isinstance(item, dict):
            continue
        summarized.append(
            {
                "uuid": item.get("uuid"),
                "name": item.get("name"),
                "ip": item.get("ip"),
                "user": item.get("user"),
                "port": item.get("port"),
                "is_reachable": item.get("is_reachable"),
                "is_usable": item.get("is_usable"),
            }
        )

    reachable_count = sum(1 for item in summarized if boolish(item.get("is_reachable")))
    usable_count = sum(1 for item in summarized if boolish(item.get("is_usable")))
    ok = bool(usable_count > 0)
    return {
        "ok": ok,
        "server_count": len(summarized),
        "reachable_count": reachable_count,
        "usable_count": usable_count,
        "servers": summarized,
        "message": "at least one Coolify server is usable" if ok else "no Coolify server reports is_usable=true",
    }


def coolify_api_report(repo_root: Path, target: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    token = coolify_token_report(repo_root, target)
    base_url = f"http://127.0.0.1:{target.get('app_port')}" if target.get("app_port") else ""
    token_value = str(token.get("_token") or "")
    controller = load_coolify_controller_report(repo_root, target, token_value)

    endpoints: dict[str, Any] = {}
    if token_value and base_url:
        for path in ["/api/v1/projects", "/api/v1/servers", "/api/v1/services", "/api/v1/deployments"]:
            endpoints[path] = http_api_json_report(base_url, path, token_value, timeout=timeout)
    else:
        for path in ["/api/v1/projects", "/api/v1/servers", "/api/v1/services", "/api/v1/deployments"]:
            endpoints[path] = {
                "ok": False,
                "path": path,
                "error": "missing token or base URL",
            }

    server_readiness = summarize_coolify_servers(
        endpoints.get("/api/v1/servers", {}).get("json")
        if isinstance(endpoints.get("/api/v1/servers"), dict)
        else None
    )

    endpoint_statuses = {
        path: {
            "ok": item.get("ok"),
            "status": item.get("status"),
            "count": item.get("count"),
            "error": item.get("error"),
        }
        for path, item in endpoints.items()
        if isinstance(item, dict)
    }

    # Remove the secret before reports are written.
    token.pop("_token", None)

    ok = bool(
        token.get("ok")
        and controller.get("ok")
        and all(bool(item.get("ok")) for item in endpoints.values() if isinstance(item, dict))
    )
    return {
        "ok": ok,
        "base_url": base_url,
        "token": token,
        "controller": controller,
        "endpoints": endpoints,
        "endpoint_statuses": endpoint_statuses,
        "server_readiness": server_readiness,
    }


def write_report(repo_root: Path, payload: dict[str, Any], report_path: Path | None) -> None:
    if report_path is None:
        report_path = repo_root / "runtime" / "deployment" / "coolify-stack-target-twiddle.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload.setdefault("report_path", str(report_path))


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    start_chain = build_start_chain_report(repo_root)
    derived = derive_start_targets(repo_root, docker_command=args.docker_command, generate_env=not args.no_generate_env)
    compose_static = build_compose_static_report(repo_root, Path(derived["compose_file"]))

    docker: dict[str, Any] = {"ok": None, "state": "skipped"}
    if args.check_docker:
        docker = docker_live_report(
            repo_root,
            derived["target"],
            derived["planned_commands"],
            docker_command=args.docker_command,
            timeout=float(args.timeout),
            check_health=bool(args.check_health),
        )

    check_api = bool(getattr(args, "check_api", False) or getattr(args, "check_server_readiness", False))
    api: dict[str, Any] = {"ok": None, "state": "skipped"}
    if check_api:
        api = coolify_api_report(
            repo_root,
            derived["target"],
            timeout=float(args.timeout),
        )

    checks = build_verify_checks(
        start_chain,
        derived,
        compose_static,
        docker,
        api,
        check_docker=bool(args.check_docker),
        check_api=check_api,
        check_server_readiness=bool(getattr(args, "check_server_readiness", False)),
    )
    summary = build_summary(derived, docker, api)
    summary.update(
        {
            "failed_checks": failed_required_checks(checks),
            "check_status": {check["name"]: check["ok"] for check in checks},
            "start_chain_ok": bool(start_chain.get("ok")),
            "derived_ok": bool(derived.get("ok")),
            "compose_static_ok": bool(compose_static.get("ok")),
            "docker_ok": docker.get("ok"),
        }
    )

    payload = {
        "ok": required_checks_ok(checks),
        "action": getattr(args, "command", "verify") or "verify",
        "repo_root": str(repo_root),
        "checks": checks,
        "start_chain": start_chain,
        "derived": derived,
        "compose_static": compose_static,
        "docker": docker,
        "api": api,
        "summary": summary,
    }
    write_report(repo_root, payload, Path(args.report) if args.report else None)
    return payload


def build_summary(derived: dict[str, Any], docker: dict[str, Any], api: dict[str, Any] | None = None) -> dict[str, Any]:
    target = derived.get("target") or {}
    containers = target.get("containers") or {}
    health = docker.get("health") or {}
    docker_version = docker.get("docker_version") if isinstance(docker.get("docker_version"), dict) else {}
    compose_config = docker.get("compose_config") if isinstance(docker.get("compose_config"), dict) else {}
    compose_ps = docker.get("compose_ps") if isinstance(docker.get("compose_ps"), dict) else {}
    api_report = api if isinstance(api, dict) else {}
    api_token = api_report.get("token") if isinstance(api_report.get("token"), dict) else {}
    api_controller = api_report.get("controller") if isinstance(api_report.get("controller"), dict) else {}
    server_readiness = (
        api_report.get("server_readiness")
        if isinstance(api_report.get("server_readiness"), dict)
        else {}
    )
    summary = {
        "compose_project": target.get("compose_project"),
        "coolify_url": f"http://127.0.0.1:{target.get('app_port')}" if target.get("app_port") else "",
        "local_state": target.get("local_state"),
        "network": target.get("network"),
        "containers": containers,
        "docker_checked": docker.get("ok") is not None,
        "docker_version_ok": docker_version.get("ok"),
        "docker_version_failure": command_failure_brief(docker_version),
        "compose_config_ok": compose_config.get("ok"),
        "compose_config_failure": command_failure_brief(compose_config),
        "compose_ps_ok": compose_ps.get("ok"),
        "compose_ps_failure": command_failure_brief(compose_ps),
        "observed_live_containers": docker.get("observed_containers", []),
        "missing_live_containers": docker.get("missing_containers", []),
        "inspect_failures": docker.get("inspect_failures", {}),
        "non_running_live_containers": docker.get("non_running_containers", {}),
        "coolify_app_port_bound": docker.get("coolify_app_port_bound"),
        "health_ok": health.get("ok"),
        "health_status": health.get("status"),
        "health_error": health.get("error"),
        "api_checked": api_report.get("ok") is not None,
        "api_ok": api_report.get("ok"),
        "api_base_url": api_report.get("base_url"),
        "api_endpoint_statuses": api_report.get("endpoint_statuses", {}),
        "token_ok": api_token.get("ok"),
        "token_selected_source": api_token.get("selected_source"),
        "token_selected_path": api_token.get("selected_path"),
        "token_length": api_token.get("token_length"),
        "controller_ok": api_controller.get("ok"),
        "controller_base_url": api_controller.get("base_url"),
        "controller_token_ref": api_controller.get("token_ref"),
        "server_readiness_ok": server_readiness.get("ok"),
        "server_usable_count": server_readiness.get("usable_count"),
        "server_reachable_count": server_readiness.get("reachable_count"),
        "servers": server_readiness.get("servers", []),
    }
    return summary


def assert_destroy_allowed(args: argparse.Namespace, derived: dict[str, Any]) -> None:
    if not args.yes_destroy:
        raise TwiddleError("Refusing destructive action without --yes-destroy.")
    target = derived.get("target") or {}
    names = target.get("container_names") or []
    if not names:
        raise TwiddleError("No derived Coolify container names were found; refusing destructive action.")
    if len(names) != len(set(names)):
        raise TwiddleError(f"Duplicate derived container names are unsafe: {names!r}")
    for name in names:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", str(name)):
            raise TwiddleError(f"Derived container name looks unsafe: {name!r}")


def command_destroy(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    derived = derive_start_targets(repo_root, docker_command=args.docker_command, generate_env=not args.no_generate_env)
    assert_destroy_allowed(args, derived)
    names = [str(name) for name in derived["target"]["container_names"]]

    presence = [
        docker_container_presence(name, repo_root=repo_root, docker_command=args.docker_command, timeout=float(args.timeout))
        for name in names
    ]
    presence_errors = [item for item in presence if not item.get("ok")]
    existing_names = [str(item["name"]) for item in presence if item.get("exists")]
    already_absent_names = [str(item["name"]) for item in presence if item.get("ok") and not item.get("exists")]

    if presence_errors:
        remove_containers = {
            "ok": False,
            "returncode": None,
            "command": [],
            "display": "docker container inspect <derived Coolify containers>",
            "stdout": "",
            "stderr": "One or more derived containers could not be inspected. Docker may be unavailable.",
        }
    elif existing_names:
        remove_containers = run_command([args.docker_command, "rm", "-f", *existing_names], cwd=repo_root, timeout=float(args.timeout))
    else:
        remove_containers = {
            "ok": True,
            "returncode": 0,
            "command": [args.docker_command, "rm", "-f", *names],
            "display": command_display([args.docker_command, "rm", "-f", *names]),
            "stdout": "",
            "stderr": "",
            "state": "already-absent",
            "message": "All derived Coolify containers were already absent.",
        }

    state_result: dict[str, Any] = {
        "ok": None,
        "state": "skipped",
        "message": "state directory was not removed; pass --remove-state-dir to remove it",
    }
    state_dir_text = str(derived["target"].get("local_state") or "")
    if args.remove_state_dir:
        state_result = remove_state_dir(repo_root, state_dir_text, allow_outside_root=bool(args.allow_outside_root))

    summary = build_summary(derived, {})
    summary.update(
        {
            "existing_target_containers": existing_names,
            "already_absent_target_containers": already_absent_names,
            "remove_containers_ok": remove_containers.get("ok"),
            "remove_containers_failure": command_failure_brief(remove_containers),
            "presence_failures": [
                {
                    "name": item.get("name"),
                    "failure": command_failure_brief(item.get("inspect") if isinstance(item.get("inspect"), dict) else None),
                }
                for item in presence_errors
            ],
            "state_dir_state": state_result.get("state"),
            "state_dir_path": state_result.get("path"),
            "state_dir_message": state_result.get("message"),
        }
    )

    payload = {
        "ok": bool(not presence_errors and remove_containers.get("ok") and state_result.get("ok") is not False),
        "action": "destroy",
        "repo_root": str(repo_root),
        "derived": derived,
        "container_presence": presence,
        "removed_containers": remove_containers,
        "state_dir": state_result,
        "summary": summary,
    }
    write_report(repo_root, payload, Path(args.report) if args.report else None)
    return payload


def remove_state_dir(repo_root: Path, state_dir_text: str, *, allow_outside_root: bool) -> dict[str, Any]:
    if not state_dir_text:
        return {"ok": False, "state": "missing", "message": "COOLIFY_LOCAL_STATE was empty"}
    path = Path(state_dir_text)
    if not path.is_absolute():
        path = (repo_root / path).resolve()
    else:
        path = path.resolve()

    root_resolved = repo_root.resolve()
    inside_root = False
    try:
        path.relative_to(root_resolved)
        inside_root = True
    except ValueError:
        inside_root = False

    derived_external_state = state_dir_is_derived_local_coolify_path(state_dir_text)
    if not inside_root and not allow_outside_root and not derived_external_state:
        return {
            "ok": False,
            "state": "blocked",
            "path": str(path),
            "message": "state directory is outside the repo root and does not look like the derived Main Computer Coolify state path; pass --allow-outside-root to remove it",
        }

    if not path.exists():
        return {
            "ok": True,
            "state": "already-missing",
            "path": str(path),
            "message": "state directory was already absent",
        }
    if not path.is_dir():
        return {
            "ok": False,
            "state": "not-directory",
            "path": str(path),
            "message": "state path exists but is not a directory",
        }
    shutil.rmtree(path)
    return {
        "ok": True,
        "state": "removed",
        "path": str(path),
        "derived_external_state": not inside_root and derived_external_state,
        "message": "state directory was removed",
    }


def command_boot_once(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    derived = derive_start_targets(repo_root, docker_command=args.docker_command, generate_env=not args.no_generate_env)
    command = [
        sys.executable,
        "-m",
        "main_computer.applications_service",
        "--root",
        str(repo_root),
        "--docker-command",
        args.docker_command,
        "boot",
    ]
    boot_timeout = float(getattr(args, "boot_timeout", args.timeout))
    boot = run_command(command, cwd=repo_root, timeout=boot_timeout)
    boot_state = parse_json_object(boot.get("stdout"))
    summary = build_summary(derived, {})
    summary.update(
        {
            "boot_ok": boot.get("ok"),
            "boot_timeout_s": boot_timeout,
            "boot_returncode": boot.get("returncode"),
            "boot_failure": command_failure_brief(boot),
            "boot_state_ok": boot_state.get("ok") if isinstance(boot_state, dict) else None,
        }
    )
    payload = {
        "ok": bool(boot.get("ok")),
        "action": "boot-once",
        "repo_root": str(repo_root),
        "derived": derived,
        "boot": boot,
        "boot_state": boot_state,
        "summary": summary,
    }
    write_report(repo_root, payload, Path(args.report) if args.report else None)
    return payload


def coolify_local_docker_command(repo_root: Path, target: dict[str, Any], action: str) -> list[str]:
    """Build a coolify-local-docker.py command scoped to the derived app-stack state."""

    tool = repo_root / "tools" / "local-prod" / "coolify-local-docker.py"
    command = [
        sys.executable,
        str(tool),
        action,
        "--project-name",
        str(target.get("compose_project") or ""),
        "--state-dir",
        str(target.get("local_state") or ""),
    ]
    optional_args = [
        ("--app-port", target.get("app_port")),
        ("--soketi-port", target.get("soketi_port")),
        ("--soketi-terminal-port", target.get("soketi_terminal_port")),
    ]
    for flag, value in optional_args:
        if value not in (None, ""):
            command.extend([flag, str(value)])
    return command


def command_repair_server_readiness(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    derived = derive_start_targets(repo_root, docker_command=args.docker_command, generate_env=not args.no_generate_env)
    target = derived.get("target") if isinstance(derived.get("target"), dict) else {}
    command = coolify_local_docker_command(repo_root, target, "ensure-infra")
    repair_timeout = float(getattr(args, "repair_timeout", args.timeout))
    repair = run_command(command, cwd=repo_root, timeout=repair_timeout)

    api = coolify_api_report(repo_root, target, timeout=float(args.timeout))
    summary = build_summary(derived, {}, api)
    summary.update(
        {
            "repair_ok": repair.get("ok"),
            "repair_returncode": repair.get("returncode"),
            "repair_timeout_s": repair_timeout,
            "repair_command": repair.get("display"),
            "repair_failure": command_failure_brief(repair),
            "repair_stdout_tail": tail_text(repair.get("stdout", "")),
            "repair_stderr_tail": tail_text(repair.get("stderr", "")),
        }
    )

    payload = {
        "ok": bool(repair.get("ok")) and bool((api.get("server_readiness") or {}).get("ok")),
        "action": "repair-server-readiness",
        "repo_root": str(repo_root),
        "derived": derived,
        "repair": repair,
        "api": api,
        "summary": summary,
    }
    write_report(repo_root, payload, Path(args.report) if args.report else None)
    return payload


def print_payload(payload: dict[str, Any], *, summary_only: bool) -> None:
    if summary_only:
        print(json.dumps({"ok": payload.get("ok"), "action": payload.get("action"), "summary": payload.get("summary"), "report_path": payload.get("report_path")}, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Twiddle that verifies the start-code-derived local Coolify stack target.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to the current directory.")
    parser.add_argument("--docker-command", default=os.environ.get("MAIN_COMPUTER_DOCKER_COMMAND", "docker"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--report", default="", help="Optional JSON report path.")
    parser.add_argument("--no-generate-env", action="store_true", help="Use the existing runtime applications.env instead of asking start code to refresh it.")
    parser.add_argument("--summary", action="store_true", help="Print only the compact summary JSON.")

    parser.set_defaults(func=command_verify, command="verify", check_docker=True, check_health=False, check_api=False, check_server_readiness=False)
    subparsers = parser.add_subparsers(dest="command")

    verify = subparsers.add_parser("verify", help="Derive stack targets and compare them with compose, live Docker, and optional Coolify API checks.")
    verify.add_argument("--no-docker", dest="check_docker", action="store_false", help="Skip live Docker checks.")
    verify.add_argument("--check-health", action="store_true", help="Also call http://127.0.0.1:<APP_PORT>/api/health.")
    verify.add_argument("--check-api", action="store_true", help="Load the token from the derived Coolify state dir and check core Coolify API endpoints.")
    verify.add_argument("--check-server-readiness", action="store_true", help="Require at least one Coolify server to report is_usable=true. Implies --check-api.")
    verify.set_defaults(func=command_verify, check_docker=True, check_api=False, check_server_readiness=False)

    show = subparsers.add_parser("show", help="Derive and print stack targets without live Docker checks.")
    show.set_defaults(func=command_verify, check_docker=False, check_health=False)

    destroy = subparsers.add_parser("destroy", help="Remove only the start-code-derived Coolify containers, optionally its bind state directory.")
    destroy.add_argument("--yes-destroy", action="store_true", help="Required for destructive action.")
    destroy.add_argument("--remove-state-dir", action="store_true", help="Also remove COOLIFY_LOCAL_STATE after removing containers.")
    destroy.add_argument("--allow-outside-root", action="store_true", help="Allow removing COOLIFY_LOCAL_STATE even when it is outside the repo root.")
    destroy.set_defaults(func=command_destroy)

    boot_once = subparsers.add_parser("boot-once", help="Run main_computer.applications_service boot once using the derived target.")
    boot_once.add_argument("--boot-timeout", type=float, default=360.0, help="Outer timeout for the one-shot application-service boot command.")
    boot_once.set_defaults(func=command_boot_once)

    repair = subparsers.add_parser(
        "repair-server-readiness",
        help="Run the derived-state Coolify ensure-infra repair, then verify API server readiness.",
    )
    repair.add_argument("--repair-timeout", type=float, default=420.0, help="Timeout for coolify-local-docker.py ensure-infra.")
    repair.set_defaults(func=command_repair_server_readiness, check_docker=False, check_health=False, check_api=True, check_server_readiness=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
    except TwiddleError as exc:
        payload = {"ok": False, "action": getattr(args, "command", "verify"), "error": str(exc)}
        print_payload(payload, summary_only=bool(getattr(args, "summary", False)))
        return 2
    print_payload(payload, summary_only=bool(getattr(args, "summary", False)))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
