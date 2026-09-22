#!/usr/bin/env python3
"""
Remote Coolify smoke for the Mother service-line restart selector bug.

Runs locally, but creates a temporary docker:27-cli service on the Coolify
controller you point it at. The temporary helper mounts /var/run/docker.sock
on the remote server and performs an isolated smoke:

1. create a disposable Docker container with the deterministic
   "<service-line>-<project>" name but NO Docker Compose / Coolify labels;
2. prove the current production selector (Compose project + service labels)
   returns candidate_count=0;
3. prove an exact deterministic-name lookup resolves that same container;
4. start it and verify Running=true;
5. remove the disposable container.

It never touches the referenced Mother service. The reference service UUID is
used only to discover the target Coolify project/server/environment.

On success, the temporary Coolify helper service is deleted unless --keep-helper
is specified. On failure it is preserved by default for inspection.

Examples:

  python mother_service_line_restart_selector_remote_smoke.py \
    --coolify-url http://159.203.184.182:8000 \
    --api-token "$COOLIFY_API_TOKEN" \
    --reference-service-uuid hw9yipwwny6o6lan3dr7ts3n

  # Optional overrides if placement discovery is ambiguous:
  python mother_service_line_restart_selector_remote_smoke.py \
    --coolify-url http://159.203.184.182:8000 \
    --api-token "$COOLIFY_API_TOKEN" \
    --reference-service-uuid hw9yipwwny6o6lan3dr7ts3n \
    --project-uuid vbja82onq0xpuzy6mwi3v11r \
    --server-uuid <server-uuid> \
    --environment-uuid t5nnytfntg2kixknkzuu6x0l \
    --environment-name mainnet
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
import re
import sys
import textwrap
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request
import uuid as uuidlib


MARKER = "MOTHER_SERVICE_LINE_RESTART_SELECTOR_SMOKE"
DEFAULT_TIMEOUT = 30.0
DEFAULT_WAIT = 90.0
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class SmokeError(RuntimeError):
    pass


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_token(value: str, field: str) -> str:
    value = str(value or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise SmokeError(f"invalid {field}: {value!r}")
    return value


class Coolify:
    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        url = self.base_url + path
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "mother-service-line-restart-selector-remote-smoke/2",
        }
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        status = None
        raw = b""
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = int(resp.status)
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
        except Exception as exc:
            raise SmokeError(f"{method} {path} failed: {exc}") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise SmokeError(f"{method} {path} response exceeded {MAX_RESPONSE_BYTES} bytes")

        text = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            payload = text

        return {
            "status": status,
            "ok": status is not None and 200 <= status < 300,
            "payload": payload,
            "text": text,
            "path": path,
        }


def iter_mappings(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_mappings(child)


def find_service_record(payload: Any, service_uuid: str) -> dict[str, Any] | None:
    wanted = service_uuid.strip()
    for item in iter_mappings(payload):
        candidate = str(item.get("uuid") or item.get("id") or "").strip()
        if candidate == wanted:
            return item
    return None


def direct_or_nested_uuid(record: dict[str, Any], direct_key: str, nested_names: tuple[str, ...]) -> str | None:
    direct = record.get(direct_key)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    for name in nested_names:
        child = record.get(name)
        if isinstance(child, dict):
            value = child.get("uuid") or child.get("id")
            if isinstance(value, str) and value.strip():
                return value.strip()

    # Final conservative recursive fallback: only match the exact direct key.
    for item in iter_mappings(record):
        value = item.get(direct_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def discover_reference_service(api: Coolify, service_uuid: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(service_uuid, safe="")
    detail = api.request("GET", f"/api/v1/services/{encoded}")
    if detail["ok"] and isinstance(detail["payload"], dict):
        detail_record = detail["payload"]
    else:
        detail_record = {}

    listing = api.request("GET", "/api/v1/services")
    list_record = find_service_record(listing["payload"], service_uuid) if listing["ok"] else None

    merged: dict[str, Any] = {}
    if isinstance(list_record, dict):
        merged.update(list_record)
    if isinstance(detail_record, dict):
        # Preserve list-record placement fields when the detail payload omits them.
        for key, value in detail_record.items():
            if value is not None:
                merged[key] = value

    if not merged:
        raise SmokeError(
            f"reference service {service_uuid!r} was not found through "
            "GET /api/v1/services/<uuid> or GET /api/v1/services"
        )
    return merged


def environment_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("environments", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def list_records(payload: Any, preferred_keys: tuple[str, ...] = ("data",)) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def id_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def resolve_server_uuid(
    api: Coolify,
    reference: dict[str, Any],
    override_server_uuid: str | None,
) -> str:
    if override_server_uuid:
        return safe_token(override_server_uuid, "server UUID")

    direct = direct_or_nested_uuid(reference, "server_uuid", ("server",))
    if direct:
        return safe_token(direct, "server UUID")

    reference_server_id = id_text(reference.get("server_id"))
    response = api.request("GET", "/api/v1/servers")
    if not response["ok"]:
        raise SmokeError(
            "unable to resolve server UUID from reference service: "
            f"GET /api/v1/servers returned HTTP {response['status']}"
        )
    records = list_records(response["payload"], ("servers", "data"))
    if not records:
        raise SmokeError("Coolify returned no servers while resolving placement")

    matches: list[dict[str, Any]] = []
    if reference_server_id:
        matches = [item for item in records if id_text(item.get("id")) == reference_server_id]
    elif len(records) == 1:
        matches = [records[0]]

    if len(matches) != 1:
        available = [
            {"id": item.get("id"), "uuid": item.get("uuid"), "name": item.get("name")}
            for item in records
        ]
        raise SmokeError(
            "could not uniquely map the reference service's server_id to a server UUID; "
            "pass --server-uuid. "
            f"reference_server_id={reference_server_id!r} "
            f"available={json.dumps(available, sort_keys=True)}"
        )

    return safe_token(str(matches[0].get("uuid") or ""), "server UUID")


def project_environment_candidates(
    api: Coolify,
    project_uuid: str,
) -> list[dict[str, Any]]:
    path = f"/api/v1/projects/{urllib.parse.quote(project_uuid, safe='')}/environments"
    response = api.request("GET", path)
    if not response["ok"]:
        raise SmokeError(
            f"unable to list environments for project {project_uuid}: "
            f"HTTP {response['status']} {response['text'][:500]}"
        )
    return environment_records(response["payload"])


def resolve_project_and_environment(
    api: Coolify,
    reference: dict[str, Any],
    override_project_uuid: str | None,
    override_environment_uuid: str | None,
    override_environment_name: str | None,
) -> tuple[str, str, str | None]:
    reference_environment_id = id_text(reference.get("environment_id"))

    direct_project = override_project_uuid or direct_or_nested_uuid(
        reference, "project_uuid", ("project",)
    )
    direct_environment_uuid = override_environment_uuid or direct_or_nested_uuid(
        reference, "environment_uuid", ("environment",)
    )
    direct_environment_name = override_environment_name
    environment_obj = reference.get("environment")
    if not direct_environment_name and isinstance(environment_obj, dict):
        candidate_name = str(environment_obj.get("name") or "").strip()
        if candidate_name:
            direct_environment_name = candidate_name

    if direct_project:
        project_uuid = safe_token(direct_project, "project UUID")

        if direct_environment_uuid:
            env_uuid = safe_token(direct_environment_uuid, "environment UUID")
            path = (
                f"/api/v1/projects/{urllib.parse.quote(project_uuid, safe='')}/"
                f"{urllib.parse.quote(env_uuid, safe='')}"
            )
            response = api.request("GET", path)
            if response["ok"] and isinstance(response["payload"], dict):
                name = str(response["payload"].get("name") or "").strip()
                if name:
                    return project_uuid, safe_token(name, "environment name"), env_uuid

        records = project_environment_candidates(api, project_uuid)
        matches: list[dict[str, Any]] = []
        if direct_environment_name:
            wanted_name = safe_token(direct_environment_name, "environment name")
            matches = [
                item for item in records
                if str(item.get("name") or "").strip() == wanted_name
            ]
        elif reference_environment_id:
            matches = [
                item for item in records
                if id_text(item.get("id")) == reference_environment_id
            ]
        elif len(records) == 1:
            matches = [records[0]]

        if len(matches) != 1:
            available = [
                {"id": item.get("id"), "uuid": item.get("uuid"), "name": item.get("name")}
                for item in records
            ]
            raise SmokeError(
                "could not uniquely resolve the reference environment inside the selected project; "
                "pass --environment-name or --environment-uuid. "
                f"reference_environment_id={reference_environment_id!r} "
                f"available={json.dumps(available, sort_keys=True)}"
            )

        item = matches[0]
        env_name = safe_token(str(item.get("name") or ""), "environment name")
        env_uuid_raw = str(item.get("uuid") or "").strip()
        env_uuid = safe_token(env_uuid_raw, "environment UUID") if env_uuid_raw else None
        return project_uuid, env_name, env_uuid

    projects_response = api.request("GET", "/api/v1/projects")
    if not projects_response["ok"]:
        raise SmokeError(
            "unable to discover project placement: "
            f"GET /api/v1/projects returned HTTP {projects_response['status']}"
        )

    projects = list_records(projects_response["payload"], ("projects", "data"))
    if not projects:
        raise SmokeError("Coolify returned no projects while resolving placement")

    placement_matches: list[tuple[str, dict[str, Any]]] = []
    for project in projects:
        project_uuid_raw = str(project.get("uuid") or "").strip()
        if not project_uuid_raw:
            continue
        project_uuid = safe_token(project_uuid_raw, "project UUID")
        records = project_environment_candidates(api, project_uuid)
        for environment in records:
            env_id_matches = (
                bool(reference_environment_id)
                and id_text(environment.get("id")) == reference_environment_id
            )
            name_matches = (
                bool(direct_environment_name)
                and str(environment.get("name") or "").strip() == direct_environment_name
            )
            if env_id_matches or (not reference_environment_id and name_matches):
                placement_matches.append((project_uuid, environment))

    if len(placement_matches) != 1:
        compact = [
            {
                "project_uuid": project_uuid,
                "environment_id": environment.get("id"),
                "environment_uuid": environment.get("uuid"),
                "environment_name": environment.get("name"),
            }
            for project_uuid, environment in placement_matches
        ]
        raise SmokeError(
            "could not uniquely map the reference service's environment_id to a project/environment; "
            "pass --project-uuid and --environment-name. "
            f"reference_environment_id={reference_environment_id!r} "
            f"matches={json.dumps(compact, sort_keys=True)}"
        )

    project_uuid, environment = placement_matches[0]
    env_name = safe_token(str(environment.get("name") or ""), "environment name")
    env_uuid_raw = str(environment.get("uuid") or "").strip()
    env_uuid = safe_token(env_uuid_raw, "environment UUID") if env_uuid_raw else None
    return project_uuid, env_name, env_uuid


def resolve_destination_uuid(
    api: Coolify,
    reference: dict[str, Any],
    server_uuid: str,
    override_destination_uuid: str | None,
) -> str | None:
    if override_destination_uuid:
        return safe_token(override_destination_uuid, "destination UUID")

    direct = direct_or_nested_uuid(reference, "destination_uuid", ("destination",))
    if direct:
        return safe_token(direct, "destination UUID")

    response = api.request(
        "GET",
        f"/api/v1/servers/{urllib.parse.quote(server_uuid, safe='')}/destinations",
    )
    if not response["ok"]:
        return None

    records = list_records(response["payload"], ("destinations", "data"))
    if len(records) == 1:
        value = str(records[0].get("uuid") or "").strip()
        return safe_token(value, "destination UUID") if value else None
    return None

def helper_shell(probe_project: str, probe_line: str) -> str:
    target_name = f"{probe_line}-{probe_project}"
    return textwrap.dedent(
        f"""\
        set -eu

        project='{probe_project}'
        line='{probe_line}'
        target_name='{target_name}'

        emit() {{
          printf '%s %s\\n' '{MARKER}' "$*"
        }}

        cleanup() {{
          docker rm -f "$target_name" >/dev/null 2>&1 || true
        }}
        trap cleanup EXIT INT TERM

        cleanup

        emit phase=script_start project="$project" service_line="$line" target_name="$target_name"

        # Create the exact runtime shape that broke production selection:
        # deterministic name, but no Compose/Coolify identity labels.
        cid="$(docker create --name "$target_name" alpine:3.20 sh -lc 'while true; do sleep 30; done')"
        emit phase=synthetic_created container_id="$cid" container_name="$target_name"

        compose_project="$(docker inspect -f '{{{{ index .Config.Labels "com.docker.compose.project" }}}}' "$cid" 2>/dev/null || true)"
        compose_service="$(docker inspect -f '{{{{ index .Config.Labels "com.docker.compose.service" }}}}' "$cid" 2>/dev/null || true)"
        coolify_name="$(docker inspect -f '{{{{ index .Config.Labels "coolify.name" }}}}' "$cid" 2>/dev/null || true)"
        emit phase=synthetic_identity compose_project="${{compose_project:-missing}}" compose_service="${{compose_service:-missing}}" coolify_name="${{coolify_name:-missing}}"

        # Current production selector.
        current_ids="$(docker ps -a \
          --filter "label=com.docker.compose.project=$project" \
          --filter "label=com.docker.compose.service=$line" \
          --format '{{{{.ID}}}}')"
        current_count="$(printf '%s\\n' "$current_ids" | sed '/^$/d' | wc -l | tr -d ' ')"
        emit phase=current_selector candidate_count="$current_count"

        if [ "$current_count" != "0" ]; then
          emit phase=complete status=failed reason=current-selector-did-not-reproduce candidate_count="$current_count"
          exit 41
        fi

        # Proposed fix boundary: exact deterministic container name.
        fixed_id="$(docker inspect -f '{{{{.Id}}}}' "$target_name" 2>/dev/null || true)"
        fixed_name="$(docker inspect -f '{{{{.Name}}}}' "$target_name" 2>/dev/null | sed 's#^/##' || true)"
        fixed_count=0
        if [ -n "$fixed_id" ] && [ "$fixed_name" = "$target_name" ]; then
          fixed_count=1
        fi
        emit phase=fixed_selector candidate_count="$fixed_count" container_id="${{fixed_id:-missing}}" container_name="${{fixed_name:-missing}}"

        if [ "$fixed_count" != "1" ]; then
          emit phase=complete status=failed reason=exact-name-selector-failed candidate_count="$fixed_count"
          exit 42
        fi

        docker start "$fixed_id" >/dev/null
        running="$(docker inspect -f '{{{{.State.Running}}}}' "$fixed_id" 2>/dev/null || true)"
        status="$(docker inspect -f '{{{{.State.Status}}}}' "$fixed_id" 2>/dev/null || true)"
        emit phase=fixed_restart running="$running" status="$status"

        if [ "$running" != "true" ]; then
          emit phase=complete status=failed reason=exact-name-selected-container-not-running running="$running" status="$status"
          exit 43
        fi

        cleanup
        trap - EXIT INT TERM
        touch /tmp/mother-service-line-restart-selector-smoke-done
        emit phase=complete status=pass bug_reproduced=true fix_demonstrated=true current_candidate_count="$current_count" fixed_candidate_count="$fixed_count"

        # Linger so Coolify has time to expose application logs.
        sleep 120
        """
    )


def render_compose(helper_name: str, shell: str) -> str:
    # Compose interpolates $, including inside block scalars. Escape each literal
    # shell dollar in the same way Mother production helpers do.
    shell = shell.replace("$", "$$")
    indented = textwrap.indent(shell.rstrip() + "\n", "        ")
    return (
        "services:\n"
        f"  {helper_name}:\n"
        "    image: docker:27-cli\n"
        "    command:\n"
        "      - sh\n"
        "      - -lc\n"
        "      - |\n"
        f"{indented}"
        "    volumes:\n"
        "      - /var/run/docker.sock:/var/run/docker.sock\n"
        '    restart: "no"\n'
        "    labels:\n"
        "      main_computer.mother.component: service-line-restart-selector-remote-smoke\n"
        "      main_computer.mother.not_a_chain_service: \"true\"\n"
        "    healthcheck:\n"
        "      test:\n"
        "        - CMD-SHELL\n"
        "        - test -f /tmp/mother-service-line-restart-selector-smoke-done\n"
        "      interval: 5s\n"
        "      timeout: 2s\n"
        "      retries: 12\n"
        "      start_period: 1s\n"
    )


def extract_created_uuid(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("uuid", "id", "service_uuid"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return safe_token(value.strip(), "created service UUID")
        for item in iter_mappings(payload):
            for key in ("uuid", "id", "service_uuid"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return safe_token(value.strip(), "created service UUID")
    raise SmokeError(f"could not find created service UUID in response: {payload!r}")


def application_candidates(payload: Any, helper_name: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return candidates

    collections = []
    for key in ("applications", "service_applications", "serviceApplications"):
        value = payload.get(key)
        if isinstance(value, list):
            collections.extend(x for x in value if isinstance(x, dict))

    # Some Coolify payloads expose nested application records elsewhere.
    if not collections:
        for item in iter_mappings(payload):
            if item is payload:
                continue
            if any(k in item for k in ("uuid", "id")) and any(k in item for k in ("name", "status")):
                collections.append(item)

    seen: set[str] = set()
    for item in collections:
        uuid = str(item.get("uuid") or item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        status = str(item.get("status") or "").strip()
        if not uuid or uuid in seen:
            continue
        seen.add(uuid)
        candidates.append({"uuid": uuid, "name": name, "status": status})

    exact = [x for x in candidates if x["name"] == helper_name]
    if exact:
        return exact
    if len(candidates) == 1:
        return candidates
    return candidates


def iter_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"docker_compose_raw", "docker_compose", "compose", "source", "raw"}:
                continue
            yield from iter_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_text(child)


def marker_lines(payload: Any) -> list[str]:
    result: list[str] = []
    for text in iter_text(payload):
        for line in text.splitlines():
            if MARKER in line:
                result.append(line.strip())
    return result


def parse_marker(line: str) -> dict[str, str]:
    tail = line.split(MARKER, 1)[1].strip()
    event: dict[str, str] = {}
    for token in tail.split():
        if "=" in token:
            key, value = token.split("=", 1)
            event[key] = value
    return event


def collect_remote_markers(
    api: Coolify,
    helper_uuid: str,
    helper_name: str,
    wait_seconds: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    deadline = time.monotonic() + wait_seconds
    all_lines: list[str] = []
    probes: list[dict[str, Any]] = []
    seen_lines: set[str] = set()

    while time.monotonic() < deadline:
        detail_path = f"/api/v1/services/{urllib.parse.quote(helper_uuid, safe='')}"
        detail = api.request("GET", detail_path)
        app_candidates = application_candidates(detail["payload"], helper_name) if detail["ok"] else []

        endpoints: list[str] = []
        for app in app_candidates:
            app_uuid = urllib.parse.quote(app["uuid"], safe="")
            svc_uuid = urllib.parse.quote(helper_uuid, safe="")
            endpoints.extend(
                [
                    f"/api/v1/services/{svc_uuid}/applications/{app_uuid}/logs?lines=300&show_timestamps=true",
                    f"/api/v1/applications/{app_uuid}/logs?lines=300",
                ]
            )
        encoded_name = urllib.parse.quote(helper_name, safe="")
        svc_uuid = urllib.parse.quote(helper_uuid, safe="")
        endpoints.extend(
            [
                f"/api/v1/services/{svc_uuid}/logs?sub_service_name={encoded_name}",
                f"/api/v1/services/{svc_uuid}/logs",
            ]
        )

        # Preserve order while deduplicating.
        endpoints = list(dict.fromkeys(endpoints))

        for endpoint in endpoints:
            response = api.request("GET", endpoint)
            lines = marker_lines(response["payload"]) if response["ok"] else []
            probes.append(
                {
                    "endpoint": endpoint,
                    "status": response["status"],
                    "ok": response["ok"],
                    "marker_count": len(lines),
                }
            )
            for line in lines:
                if line not in seen_lines:
                    seen_lines.add(line)
                    all_lines.append(line)

        for line in all_lines:
            event = parse_marker(line)
            if event.get("phase") == "complete":
                return all_lines, probes

        time.sleep(2.0)

    return all_lines, probes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce the Mother label-only selector bug remotely through a temporary Coolify helper."
    )
    parser.add_argument("--coolify-url", required=True, help="Coolify base URL, e.g. http://159.203.184.182:8000")
    parser.add_argument(
        "--api-token",
        default=os.environ.get("COOLIFY_API_TOKEN", ""),
        help="Coolify API token. Defaults to COOLIFY_API_TOKEN.",
    )
    parser.add_argument(
        "--reference-service-uuid",
        required=True,
        help="Existing service on the target Coolify server; used only to discover placement metadata.",
    )
    parser.add_argument("--project-uuid", help="Override project UUID if discovery cannot obtain it.")
    parser.add_argument("--server-uuid", help="Override server UUID if discovery cannot obtain it.")
    parser.add_argument("--environment-uuid", help="Override environment UUID.")
    parser.add_argument("--environment-name", help="Override environment name.")
    parser.add_argument("--destination-uuid", help="Override destination UUID if the target server has multiple destinations.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP request timeout in seconds.")
    parser.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT, help="Maximum time to wait for smoke markers.")
    parser.add_argument("--keep-helper", action="store_true", help="Preserve temporary Coolify helper even on success.")
    args = parser.parse_args()

    if not args.api_token.strip():
        parser.error("--api-token or COOLIFY_API_TOKEN is required")
    if args.timeout <= 0 or args.wait_seconds <= 0:
        parser.error("--timeout and --wait-seconds must be positive")

    ref_uuid = safe_token(args.reference_service_uuid, "reference service UUID")
    api = Coolify(args.coolify_url, args.api_token, args.timeout)

    print(f"[1/7] Inspecting reference service {ref_uuid} on {args.coolify_url}")
    reference = discover_reference_service(api, ref_uuid)

    print("[2/7] Resolving project, environment, and server from Coolify IDs")
    project_uuid, env_name, env_uuid = resolve_project_and_environment(
        api,
        reference,
        args.project_uuid,
        args.environment_uuid,
        args.environment_name,
    )
    server_uuid = resolve_server_uuid(api, reference, args.server_uuid)
    destination_uuid = resolve_destination_uuid(
        api,
        reference,
        server_uuid,
        args.destination_uuid,
    )

    nonce = uuidlib.uuid4().hex[:10]
    helper_name = f"mother-restart-selector-smoke-{utc_stamp().lower()}-{nonce}"[:120]
    probe_project = f"mothersmoke{nonce}"
    probe_line = "mother-node-remove-voter-smoke_line"
    shell = helper_shell(probe_project, probe_line)
    compose = render_compose(helper_name, shell)

    body = {
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
        "environment_name": env_name,
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": helper_name,
        "description": "Ephemeral Mother remote smoke for service-line restart selector identity fallback",
        "instant_deploy": False,
    }
    if env_uuid:
        body["environment_uuid"] = env_uuid
    if destination_uuid:
        body["destination_uuid"] = destination_uuid

    helper_uuid: str | None = None
    passed = False
    result: dict[str, Any] = {
        "status": "failed",
        "coolify_url": args.coolify_url,
        "reference_service_uuid": ref_uuid,
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
        "environment_uuid": env_uuid,
        "environment_name": env_name,
        "destination_uuid": destination_uuid,
        "helper_name": helper_name,
        "helper_uuid": None,
        "probe_project": probe_project,
        "probe_line": probe_line,
        "markers": [],
    }

    try:
        print(f"[3/7] Creating temporary remote helper {helper_name}")
        created = api.request("POST", "/api/v1/services", body)
        if not created["ok"]:
            raise SmokeError(f"helper create failed: HTTP {created['status']} {created['text'][:1000]}")
        helper_uuid = extract_created_uuid(created["payload"])
        result["helper_uuid"] = helper_uuid

        start_path = f"/api/v1/services/{urllib.parse.quote(helper_uuid, safe='')}/start"
        print(f"[4/7] Starting helper {helper_uuid}")
        started = api.request("POST", start_path)
        if not started["ok"]:
            raise SmokeError(f"helper start failed: HTTP {started['status']} {started['text'][:1000]}")

        print("[5/7] Waiting for remote smoke markers")
        lines, probes = collect_remote_markers(api, helper_uuid, helper_name, args.wait_seconds)
        result["markers"] = lines
        result["log_probes"] = probes[-20:]

        for line in lines:
            print("  " + line)

        complete_events = [parse_marker(line) for line in lines if parse_marker(line).get("phase") == "complete"]
        completion = complete_events[-1] if complete_events else None
        result["completion"] = completion

        if completion is None:
            raise SmokeError(
                "no completion marker was observed; helper is being preserved for inspection. "
                "Use the Coolify Runtime Logs page for the helper service."
            )

        passed = (
            completion.get("status") == "pass"
            and completion.get("bug_reproduced") == "true"
            and completion.get("fix_demonstrated") == "true"
            and completion.get("current_candidate_count") == "0"
            and completion.get("fixed_candidate_count") == "1"
        )

        if not passed:
            raise SmokeError(f"remote smoke completed but did not pass: {completion}")

        result["status"] = "pass"
        print("[6/7] PASS: label-only selector reproduced candidate_count=0; exact-name fallback resolved and started the container")

    finally:
        if helper_uuid and (passed and not args.keep_helper):
            print(f"[7/7] Deleting temporary helper {helper_uuid}")
            delete_path = f"/api/v1/services/{urllib.parse.quote(helper_uuid, safe='')}"
            deleted = api.request("DELETE", delete_path)
            result["helper_delete"] = {
                "attempted": True,
                "status": deleted["status"],
                "ok": deleted["ok"],
            }
            if not deleted["ok"]:
                print(
                    f"WARNING: helper delete failed: HTTP {deleted['status']}; "
                    f"manual cleanup required for {helper_uuid}",
                    file=sys.stderr,
                )
        elif helper_uuid:
            result["helper_delete"] = {"attempted": False, "ok": None}
            print(
                f"[7/7] Preserving helper for inspection: {helper_name} ({helper_uuid})",
                file=sys.stderr,
            )

        # Always print a compact machine-readable summary. Do not include token.
        print(json.dumps(result, sort_keys=True, indent=2))

    return 0 if passed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
