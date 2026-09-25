from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import FdbControlError
from .privates import HostBinding


@dataclass(frozen=True, slots=True)
class CoolifyResponse:
    ok: bool
    status: int
    method: str
    path: str
    body: Any


@dataclass(frozen=True, slots=True)
class CoolifyContext:
    project_uuid: str
    environment_name: str
    environment_uuid: str | None
    server_uuid: str
    destination_uuid: str | None = None


class CoolifyClient:
    def __init__(self, binding: HostBinding, *, timeout_s: float = 30.0, retries: int = 2, retry_sleep_s: float = 1.0) -> None:
        if not binding.coolify_url.startswith(("http://", "https://")):
            raise ValueError("Coolify URL must start with http:// or https://")
        self.base_url = binding.coolify_url.rstrip("/")
        self.token = binding.token
        self.timeout_s = float(timeout_s)
        self.retries = max(0, int(retries))
        self.retry_sleep_s = max(0.0, float(retry_sleep_s))

    def request(self, method: str, path: str, payload: Any | None = None) -> CoolifyResponse:
        api_path = path if path.startswith("/") else f"/{path}"
        url = self.base_url + api_path
        data = None
        headers = {"Accept": "application/json,text/plain,*/*", "Authorization": f"Bearer {self.token}"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        last_error: BaseException | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    return CoolifyResponse(True, int(response.status), method.upper(), api_path, _parse_body(raw))
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                return CoolifyResponse(False, int(exc.code), method.upper(), api_path, _parse_body(raw))
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_sleep_s)
        return CoolifyResponse(False, 0, method.upper(), api_path, {"error": "request_failed", "message": str(last_error)})


def resolve_context(
    client: CoolifyClient,
    *,
    project_uuid: str = "",
    project_name: str = "",
    environment_name: str,
    environment_uuid: str = "",
    server_uuid: str = "",
    server_name: str = "",
    destination_uuid: str = "",
    allow_create_environment: bool = False,
) -> CoolifyContext:
    project = _resolve_resource(
        client,
        path="/api/v1/projects",
        preferred_keys=("projects",),
        kind="project",
        explicit_uuid=project_uuid,
        explicit_name=project_name,
        infer_single=not (project_uuid or project_name),
    )
    server = _resolve_resource(
        client,
        path="/api/v1/servers",
        preferred_keys=("servers",),
        kind="server",
        explicit_uuid=server_uuid,
        explicit_name=server_name,
        infer_single=not (server_uuid or server_name),
    )
    env_name = str(environment_name or "").strip()
    if not env_name:
        raise ValueError("environment_name must be non-empty")
    env_uuid = str(environment_uuid or "").strip()
    if not env_uuid:
        envs = _list_items(client, f"/api/v1/projects/{urllib.parse.quote(project)}/environments", "environments")
        env_uuid, matches = _select_exact_name(envs, env_name)
        if len(matches) > 1:
            raise _coolify_error("FDB_COOLIFY_ENVIRONMENT_AMBIGUOUS", f"multiple Coolify environments named {env_name!r}")
        if not env_uuid and allow_create_environment:
            response = client.request("POST", f"/api/v1/projects/{urllib.parse.quote(project)}/environments", {"name": env_name})
            if not response.ok and response.status not in {409, 422}:
                raise _coolify_error("FDB_COOLIFY_ENVIRONMENT_CREATE_FAILED", f"Coolify environment create failed: HTTP {response.status}: {response.body}", effect="live-deployment")
            envs = _list_items(client, f"/api/v1/projects/{urllib.parse.quote(project)}/environments", "environments")
            env_uuid, matches = _select_exact_name(envs, env_name)
        if not env_uuid and not allow_create_environment:
            # Prep is read-only. It may freeze a missing environment by name; do creates it.
            env_uuid = ""
        elif not env_uuid:
            raise _coolify_error("FDB_COOLIFY_ENVIRONMENT_MISSING", f"could not resolve Coolify environment {env_name!r} after create")
    return CoolifyContext(project, env_name, env_uuid or None, server, destination_uuid or None)


def ensure_environment(client: CoolifyClient, context: CoolifyContext) -> CoolifyContext:
    if context.environment_uuid:
        return context
    return resolve_context(
        client,
        project_uuid=context.project_uuid,
        environment_name=context.environment_name,
        server_uuid=context.server_uuid,
        destination_uuid=context.destination_uuid or "",
        allow_create_environment=True,
    )


def find_service(client: CoolifyClient, service_name: str) -> tuple[str, Mapping[str, Any] | None]:
    items = _list_items(client, "/api/v1/services", "services")
    uuid, matches = _select_exact_name(items, service_name)
    if len(matches) > 1:
        raise _coolify_error("FDB_COOLIFY_SERVICE_AMBIGUOUS", f"multiple Coolify services named {service_name!r}")
    return uuid, matches[0] if matches else None


def create_service(client: CoolifyClient, context: CoolifyContext, *, service_name: str, description: str, compose_b64: str) -> str:
    if not context.environment_uuid:
        raise ValueError("context.environment_uuid is required for create_service")
    payload: dict[str, Any] = {
        "server_uuid": context.server_uuid,
        "project_uuid": context.project_uuid,
        "environment_name": context.environment_name,
        "environment_uuid": context.environment_uuid,
        "name": service_name,
        "description": description,
        "docker_compose_raw": compose_b64,
        "instant_deploy": False,
    }
    if context.destination_uuid:
        payload["destination_uuid"] = context.destination_uuid
    response = client.request("POST", "/api/v1/services", payload)
    if not response.ok:
        raise _coolify_error("FDB_COOLIFY_SERVICE_CREATE_FAILED", f"Coolify service create failed: HTTP {response.status}: {response.body}", effect="live-deployment")
    uuid = _extract_uuid(response.body)
    if not uuid:
        raise _coolify_error("FDB_COOLIFY_SERVICE_CREATE_NO_UUID", "Coolify created the service but returned no UUID", effect="live-deployment", retry="inspect-first")
    return uuid


def update_service(client: CoolifyClient, service_uuid: str, *, service_name: str, compose_b64: str) -> None:
    quoted = urllib.parse.quote(service_uuid)
    attempts = [
        (f"/api/v1/services/{quoted}", {"docker_compose_raw": compose_b64, "name": service_name}),
        (f"/api/v1/services/{quoted}", {"docker_compose_raw": compose_b64}),
        (f"/api/v1/services/{quoted}/compose", {"docker_compose_raw": compose_b64}),
    ]
    for path, payload in attempts:
        response = client.request("PATCH", path, payload)
        if response.ok:
            return
        if response.status == 405:
            response = client.request("PUT", path, payload)
            if response.ok:
                return
        if response.status not in {400, 404, 405, 422}:
            raise _coolify_error("FDB_COOLIFY_SERVICE_UPDATE_FAILED", f"Coolify service update failed: HTTP {response.status}: {response.body}", effect="live-deployment")
    raise _coolify_error("FDB_COOLIFY_SERVICE_UPDATE_FAILED", "Coolify service update failed on all known endpoints", effect="live-deployment")


def deploy_service(client: CoolifyClient, service_uuid: str, *, force: bool = False) -> None:
    quoted = urllib.parse.quote(service_uuid)
    query = urllib.parse.urlencode({"uuid": service_uuid, "force": "true" if force else "false"})
    paths = [
        ("POST", f"/api/v1/deploy?{query}"),
        ("POST", f"/api/v1/services/{quoted}/start"),
        ("POST", f"/api/v1/services/{quoted}/restart"),
        ("POST", f"/api/v1/services/{quoted}/deploy"),
    ]
    for method, path in paths:
        response = client.request(method, path)
        if response.ok:
            return
    raise _coolify_error("FDB_COOLIFY_DEPLOY_FAILED", "Coolify service deploy failed on all known endpoints", effect="live-deployment", retry="inspect-first")


def delete_service(client: CoolifyClient, service_uuid: str) -> None:
    quoted = urllib.parse.quote(service_uuid)
    response = client.request("DELETE", f"/api/v1/services/{quoted}")
    if response.ok or response.status == 404:
        return
    raise _coolify_error(
        "FDB_COOLIFY_SERVICE_DELETE_FAILED",
        f"Coolify service delete failed: HTTP {response.status}: {response.body}",
        effect="live-deployment",
        retry="inspect-first",
    )


def get_service(client: CoolifyClient, service_uuid: str) -> Mapping[str, Any] | None:
    response = client.request("GET", f"/api/v1/services/{urllib.parse.quote(service_uuid)}")
    if response.status == 404:
        return None
    if not response.ok:
        raise _coolify_error("FDB_COOLIFY_SERVICE_READ_FAILED", f"Coolify service read failed: HTTP {response.status}: {response.body}")
    return response.body if isinstance(response.body, Mapping) else {"value": response.body}


def service_health_status(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return "missing"
    candidates: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in {"status", "human_status", "humanized_status", "service_status", "health", "health_status"}:
                    text = str(child or "").strip().lower()
                    if text:
                        candidates.append(text)
                if isinstance(child, (Mapping, list, tuple)):
                    walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(payload)
    for candidate in candidates:
        if "unhealthy" in candidate:
            return "running:unhealthy" if "running" in candidate else "unhealthy"
    for candidate in candidates:
        if candidate == "running:healthy" or ("running" in candidate and "healthy" in candidate):
            return "running:healthy"
    for candidate in candidates:
        if "running" in candidate:
            return "running"
    return candidates[0] if candidates else "unknown"


def wait_for_running(client: CoolifyClient, service_uuid: str, *, timeout_s: float = 300.0, poll_s: float = 5.0) -> Mapping[str, Any]:
    """Wait for Coolify to materialize the service into a running state.

    This is deliberately not an FDB health proof. Coolify service start is
    asynchronous, and its aggregate service payload is only used here to prove
    that the deployment has materialized far enough to be running. FDB truth is
    established separately by the observer proof consumed by live inspection.
    """

    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        detail = get_service(client, service_uuid)
        status = service_health_status(detail)
        if status.startswith("running"):
            return detail or {}
        if time.monotonic() >= deadline:
            raise _coolify_error(
                "FDB_BIRTH_MATERIALIZE_TIMEOUT",
                f"Coolify service {service_uuid} did not become running before timeout; last status={status}",
                retry="inspect-first",
            )
        time.sleep(max(0.1, poll_s))


def get_service_logs(
    client: CoolifyClient,
    service_uuid: str,
    *,
    sub_service_name: str,
    lines: int = 200,
) -> str:
    name = str(sub_service_name or "").strip()
    if not name:
        raise ValueError("sub_service_name must be non-empty")
    query = urllib.parse.urlencode(
        {
            "sub_service_name": name,
            "lines": max(1, int(lines)),
            "show_timestamps": "false",
        }
    )
    path = f"/api/v1/services/{urllib.parse.quote(service_uuid)}/logs?{query}"
    response = client.request("GET", path)
    if response.status in {400, 404}:
        return ""
    if not response.ok:
        raise _coolify_error(
            "FDB_COOLIFY_SERVICE_LOG_READ_FAILED",
            f"Coolify service log read failed: HTTP {response.status}: {response.body}",
            retry="inspect-first",
        )
    body = response.body
    if isinstance(body, Mapping):
        value = body.get("logs")
        return str(value or "")
    return str(body or "")



def wait_for_missing(client: CoolifyClient, service_uuid: str, *, timeout_s: float = 300.0, poll_s: float = 5.0) -> None:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if get_service(client, service_uuid) is None:
            return
        if time.monotonic() >= deadline:
            raise _coolify_error(
                "FDB_COOLIFY_SERVICE_DELETE_TIMEOUT",
                f"Coolify service {service_uuid} still exists after {timeout_s:.0f}s",
                effect="live-deployment",
                retry="inspect-first",
            )
        time.sleep(max(0.1, poll_s))

def _resolve_resource(client: CoolifyClient, *, path: str, preferred_keys: tuple[str, ...], kind: str, explicit_uuid: str, explicit_name: str, infer_single: bool) -> str:
    if str(explicit_uuid or "").strip():
        return str(explicit_uuid).strip()
    items = _list_items(client, path, *preferred_keys)
    name = str(explicit_name or "").strip()
    if name:
        uuid, matches = _select_exact_name(items, name)
        if uuid:
            return uuid
        if len(matches) > 1:
            raise _coolify_error(f"FDB_COOLIFY_{kind.upper()}_AMBIGUOUS", f"multiple Coolify {kind}s named {name!r}")
        raise _coolify_error(f"FDB_COOLIFY_{kind.upper()}_MISSING", f"no Coolify {kind} named {name!r}")
    candidates = [item for item in items if _item_uuid(item)]
    if infer_single and len(candidates) == 1:
        return _item_uuid(candidates[0])
    summaries = [{"uuid": _item_uuid(item), "name": _item_name(item)} for item in candidates]
    raise _coolify_error(
        f"FDB_COOLIFY_{kind.upper()}_SELECTION_REQUIRED",
        f"could not infer one Coolify {kind}; pass its UUID/name. Available: {summaries}",
    )


def _list_items(client: CoolifyClient, path: str, *preferred_keys: str) -> list[dict[str, Any]]:
    response = client.request("GET", path)
    if not response.ok:
        raise _coolify_error("FDB_COOLIFY_LIST_FAILED", f"Coolify list failed for {path}: HTTP {response.status}: {response.body}")
    body = response.body
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if isinstance(body, Mapping):
        for key in (*preferred_keys, "data", "items", "resources", "projects", "servers", "environments", "services"):
            value = body.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _select_exact_name(items: list[dict[str, Any]], name: str) -> tuple[str, list[dict[str, Any]]]:
    clean = str(name or "").strip().lower()
    matches = [item for item in items if _item_name(item).lower() == clean or str(item.get("name") or "").strip().lower() == clean]
    if len(matches) == 1:
        return _item_uuid(matches[0]), matches
    return "", matches


def _item_uuid(item: Mapping[str, Any]) -> str:
    for key in ("uuid", "id", "service_uuid", "project_uuid", "server_uuid", "environment_uuid"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _item_name(item: Mapping[str, Any]) -> str:
    for key in ("name", "description", "fqdn", "urls"):
        value = item.get(key)
        if isinstance(value, list) and value:
            return str(value[0]).strip()
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_uuid(body: object) -> str:
    if isinstance(body, Mapping):
        value = _item_uuid(body)
        if value:
            return value
        for key in ("service", "data"):
            nested = body.get(key)
            if isinstance(nested, Mapping):
                value = _extract_uuid(nested)
                if value:
                    return value
    return ""


def _parse_body(raw: str) -> Any:
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _coolify_error(code: str, message: str, *, effect: str = "none", retry: str = "never") -> FdbControlError:
    return FdbControlError(
        code=code,
        message=message,
        module_id="FDB-OFM-DEPLOY-001",
        retry_class=retry,  # type: ignore[arg-type]
        effect_class=effect,  # type: ignore[arg-type]
    )
