"""Read-only Coolify observation bound to committed Mother private state."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any, Callable, Iterable, Mapping
import urllib.error
import urllib.parse
import urllib.request

from . import atomic_files
from .canonical import canonical_json
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_OBSERVATION_KIND = "main_computer.mother.coolify_observation.v1"
_MAX_ENDPOINTS = 256
_DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_ITEMS = 1000
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9A-Fa-f]{64}$")
_COOLIFY_TOKEN_RE = re.compile(r"^[0-9]+\|[A-Za-z0-9._~-]{16,}$")
_UUID_KEYS = (
    "uuid",
    "id",
    "name",
    "description",
    "status",
    "state",
    "type",
    "fqdn",
    "domains",
    "project_uuid",
    "environment_uuid",
    "server_uuid",
    "destination_uuid",
    "source_uuid",
    "service_uuid",
    "application_uuid",
    "environment_name",
    "server_name",
    "git_repository",
    "git_branch",
)


class CoolifyObservationError(RuntimeError):
    """Deterministic read-only observation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True, repr=False)
class CoolifyController:
    network: str
    controller_id: str
    base_url: str
    api_token: str
    enabled: bool
    project_name_hint: str
    mutation_authority: str

    def __post_init__(self) -> None:
        _identifier(self.network, "network")
        _identifier(self.controller_id, "controller_id")
        _normalize_base_url(self.base_url)
        if type(self.api_token) is not str:
            raise TypeError("api_token must be a string")
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        if type(self.project_name_hint) is not str:
            raise TypeError("project_name_hint must be a string")
        if self.mutation_authority != "observe-only":
            raise ValueError("Coolify controller is not observe-only")

    def __repr__(self) -> str:
        return (
            "CoolifyController("
            f"network={self.network!r}, controller_id={self.controller_id!r}, "
            f"base_url={self.base_url!r}, enabled={self.enabled!r}, "
            f"project_name_hint={self.project_name_hint!r}, "
            "api_token='<redacted>', mutation_authority='observe-only')"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CoolifyHttpObservation:
    path: str
    status: int
    payload: Any
    response_sha256: str
    byte_length: int
    elapsed_ms: int
    content_type: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def __repr__(self) -> str:
        return (
            "CoolifyHttpObservation("
            f"path={self.path!r}, status={self.status!r}, "
            f"response_sha256={self.response_sha256!r}, byte_length={self.byte_length!r}, "
            f"elapsed_ms={self.elapsed_ms!r}, content_type={self.content_type!r}, "
            "payload='<not-rendered>')"
        )


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    label: str
    path: str
    preferred_keys: tuple[str, ...]
    item_kind: str


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_DEFAULT_OPENER = urllib.request.build_opener(_RejectRedirects())


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or not value or not _IDENTIFIER_RE.fullmatch(value):
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_IDENTIFIER", f"invalid {name}")
    if value in {".", ".."}:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_IDENTIFIER", f"invalid {name}")
    return value


def _normalize_base_url(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_CONTROLLER", "Coolify URL is missing")
    text = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_CONTROLLER", "Coolify URL must be HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_CONTROLLER", "Coolify URL contains forbidden components")
    if "\\" in parsed.path or "\x00" in parsed.path:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_CONTROLLER", "Coolify URL path is unsafe")
    return text


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be PrivateStateReadResult")
    value = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    if type(value) is not dict:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_STATE", "Mother private state is not an object")
    return value


def list_coolify_controllers(private_state: PrivateStateReadResult) -> tuple[CoolifyController, ...]:
    document = _document(private_state)
    networks = document.get("networks")
    if type(networks) is not dict:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_STATE", "Mother networks are missing")
    found: list[CoolifyController] = []
    for network in sorted(networks, key=lambda item: str(item).encode("utf-8")):
        body = networks[network]
        if type(network) is not str or type(body) is not dict:
            continue
        coolify = body.get("coolify")
        if type(coolify) is not dict:
            continue
        authority = coolify.get("mutation_authority")
        if authority != "observe-only":
            raise CoolifyObservationError(
                "MOTHER_COOLIFY_AUTHORITY_REJECTED",
                f"network {network!r} is not bound to observe-only Coolify authority",
            )
        controllers = coolify.get("controllers")
        if type(controllers) is not dict:
            raise CoolifyObservationError(
                "MOTHER_COOLIFY_INVALID_STATE",
                f"network {network!r} has no Coolify controller mapping",
            )
        for controller_id in sorted(controllers, key=lambda item: str(item).encode("utf-8")):
            wire = controllers[controller_id]
            if type(controller_id) is not str or type(wire) is not dict:
                raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_STATE", "Coolify controller entry is malformed")
            base_url = wire.get("url", wire.get("coolify_url"))
            token = wire.get("api_token", "")
            enabled = wire.get("enabled", True)
            hint = wire.get("project_name", wire.get("project_name_hint", ""))
            if type(token) is not str or type(enabled) is not bool or type(hint) is not str:
                raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_STATE", "Coolify controller fields are malformed")
            found.append(
                CoolifyController(
                    network=network,
                    controller_id=controller_id,
                    base_url=_normalize_base_url(base_url),
                    api_token=token,
                    enabled=enabled,
                    project_name_hint=hint,
                    mutation_authority=authority,
                )
            )
    return tuple(found)


def resolve_coolify_controller(
    private_state: PrivateStateReadResult,
    network: str,
    controller_id: str,
    *,
    require_enabled: bool = True,
    require_token: bool = True,
) -> CoolifyController:
    network_id = _identifier(network, "network")
    requested = _identifier(controller_id, "controller_id")
    matches = [
        item for item in list_coolify_controllers(private_state)
        if item.network == network_id and item.controller_id == requested
    ]
    if len(matches) != 1:
        raise CoolifyObservationError(
            "MOTHER_COOLIFY_CONTROLLER_NOT_FOUND",
            f"Coolify controller not found: {network_id}/{requested}",
        )
    controller = matches[0]
    if require_enabled and not controller.enabled:
        raise CoolifyObservationError(
            "MOTHER_COOLIFY_CONTROLLER_DISABLED",
            f"Coolify controller is disabled: {network_id}/{requested}",
        )
    if require_token and not controller.api_token.strip():
        raise CoolifyObservationError(
            "MOTHER_COOLIFY_TOKEN_MISSING",
            f"Coolify API token is missing: {network_id}/{requested}",
        )
    return controller


def _validate_api_path(path: object) -> str:
    if type(path) is not str or not path.startswith("/") or "\\" in path or "\x00" in path:
        raise CoolifyObservationError("MOTHER_COOLIFY_UNSAFE_ENDPOINT", "Coolify endpoint path is unsafe")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise CoolifyObservationError("MOTHER_COOLIFY_UNSAFE_ENDPOINT", "Coolify endpoint path is unsafe")
    pure = PurePosixPath(parsed.path)
    if any(part in {"..", "."} for part in pure.parts):
        raise CoolifyObservationError("MOTHER_COOLIFY_UNSAFE_ENDPOINT", "Coolify endpoint path is unsafe")
    if not (parsed.path == "/api/health" or parsed.path.startswith("/api/v1/")):
        raise CoolifyObservationError("MOTHER_COOLIFY_UNSAFE_ENDPOINT", "Coolify endpoint is outside the read-only API allowlist")
    return path


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open(request, timeout=...)")


def get_coolify_json(
    controller: CoolifyController,
    path: str,
    *,
    authenticated: bool,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
) -> CoolifyHttpObservation:
    """Issue one bounded GET request. No other HTTP method is representable."""

    if not isinstance(controller, CoolifyController):
        raise TypeError("controller must be CoolifyController")
    safe_path = _validate_api_path(path)
    if type(timeout) not in {int, float} or timeout <= 0:
        raise ValueError("timeout must be positive")
    if type(max_response_bytes) is not int or max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be a positive integer")
    if authenticated and not controller.api_token.strip():
        raise CoolifyObservationError("MOTHER_COOLIFY_TOKEN_MISSING", "Coolify API token is missing")

    url = controller.base_url + safe_path
    headers = {
        "Accept": "application/json",
        "User-Agent": "main-computer-mother-coolify-observer/1",
    }
    if authenticated:
        headers["Authorization"] = f"Bearer {controller.api_token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, float(timeout))
            status = int(getattr(response, "status", response.getcode()))
            content_type = str(response.headers.get("Content-Type", ""))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            content_type = str(exc.headers.get("Content-Type", "")) if exc.headers else ""
            raw = exc.read(max_response_bytes + 1)
    except urllib.error.URLError as exc:
        raise CoolifyObservationError("MOTHER_COOLIFY_REQUEST_FAILED", f"Coolify GET failed: {exc.reason}") from exc
    except OSError as exc:
        raise CoolifyObservationError("MOTHER_COOLIFY_REQUEST_FAILED", "Coolify GET failed") from exc

    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    if len(raw) > max_response_bytes:
        raise CoolifyObservationError(
            "MOTHER_COOLIFY_RESPONSE_TOO_LARGE",
            f"Coolify response exceeded {max_response_bytes} bytes",
        )
    text = raw.decode("utf-8", errors="replace")
    try:
        payload: Any = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        payload = text.strip()
    return CoolifyHttpObservation(
        path=safe_path,
        status=status,
        payload=payload,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
        elapsed_ms=elapsed_ms,
        content_type=content_type,
    )


def _items(payload: Any, preferred_keys: Iterable[str]) -> list[Any]:
    if type(payload) is list:
        return list(payload)
    if type(payload) is dict:
        for key in (*preferred_keys, "data"):
            value = payload.get(key)
            if type(value) is list:
                return list(value)
        if any(key in payload for key in ("uuid", "id", "name")):
            return [payload]
    return []


def _sensitive_label(value: object) -> bool:
    if type(value) is not str:
        return False
    lowered = value.lower().replace("-", "_")
    return any(part in lowered for part in ("token", "secret", "password", "private_key", "api_key", "credential"))


def _safe_scalar(value: Any) -> Any:
    if value is None or type(value) in {bool, int, float}:
        return value
    if type(value) is str:
        if _PRIVATE_KEY_RE.fullmatch(value) or _COOLIFY_TOKEN_RE.fullmatch(value) or value.lower().startswith("bearer "):
            return "<redacted>"
        return value[:2048]
    if type(value) is list and all(type(item) is str for item in value):
        return [str(item)[:512] for item in value[:32]]
    return None


def _identity_projection(item: Any, item_kind: str) -> dict[str, Any]:
    if type(item) is not dict:
        return {"kind": item_kind, "value_type": type(item).__name__}
    projected: dict[str, Any] = {"kind": item_kind}
    secret_row = _sensitive_label(item.get("key")) or _sensitive_label(item.get("name"))
    for key in _UUID_KEYS:
        if key not in item:
            continue
        if secret_row and key in {"description", "name"}:
            projected[key] = "<redacted>"
            continue
        safe = _safe_scalar(item[key])
        if safe is not None:
            projected[key] = safe
    return projected


def summarize_observation(
    observation: CoolifyHttpObservation,
    *,
    preferred_keys: tuple[str, ...],
    item_kind: str,
    max_items: int = _DEFAULT_MAX_ITEMS,
) -> dict[str, Any]:
    if type(max_items) is not int or max_items <= 0:
        raise ValueError("max_items must be a positive integer")
    items = _items(observation.payload, preferred_keys)
    summary: dict[str, Any] = {
        "byte_length": observation.byte_length,
        "content_type": observation.content_type,
        "elapsed_ms": observation.elapsed_ms,
        "item_count": len(items),
        "items": [_identity_projection(item, item_kind) for item in items[:max_items]],
        "ok": observation.ok,
        "path": observation.path,
        "response_sha256": observation.response_sha256,
        "status": observation.status,
        "truncated": len(items) > max_items,
    }
    if not items and type(observation.payload) is dict:
        summary["safe_fields"] = {
            key: safe
            for key in ("status", "state", "message", "version", "uuid", "name")
            if key in observation.payload and (safe := _safe_scalar(observation.payload[key])) is not None
        }
    elif not items and type(observation.payload) is str:
        text = _safe_scalar(observation.payload)
        summary["safe_text"] = text if text is not None else ""
    return summary


def _request_summary(
    controller: CoolifyController,
    spec: EndpointSpec,
    *,
    timeout: float,
    max_response_bytes: int,
    max_items: int,
    opener: Any,
) -> dict[str, Any]:
    try:
        observed = get_coolify_json(
            controller,
            spec.path,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        return summarize_observation(
            observed,
            preferred_keys=spec.preferred_keys,
            item_kind=spec.item_kind,
            max_items=max_items,
        )
    except CoolifyObservationError as exc:
        return {
            "error_code": exc.code,
            "error_message": str(exc),
            "item_count": 0,
            "items": [],
            "ok": False,
            "path": spec.path,
            "status": None,
            "truncated": False,
        }


def _project_uuids(summary: Mapping[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    items = summary.get("items")
    if type(items) is not list:
        return ()
    for item in items:
        if type(item) is not dict:
            continue
        value = item.get("uuid", item.get("id"))
        if type(value) is str and value and _IDENTIFIER_RE.fullmatch(value):
            found.append(value)
    return tuple(dict.fromkeys(found))


def observe_health(
    controller: CoolifyController,
    private_state: PrivateStateReadResult,
    *,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    created_at: str,
) -> dict[str, Any]:
    try:
        observed = get_coolify_json(
            controller,
            "/api/health",
            authenticated=False,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        result = summarize_observation(
            observed,
            preferred_keys=(),
            item_kind="health",
            max_items=1,
        )
    except CoolifyObservationError as exc:
        result = {
            "error_code": exc.code,
            "error_message": str(exc),
            "item_count": 0,
            "items": [],
            "ok": False,
            "path": "/api/health",
            "status": None,
            "truncated": False,
        }
    return _evidence_document(
        command="health",
        controller=controller,
        private_state=private_state,
        created_at=created_at,
        endpoints={"health": result},
    )


def observe_inventory(
    controller: CoolifyController,
    private_state: PrivateStateReadResult,
    *,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_items: int = _DEFAULT_MAX_ITEMS,
    opener: Any = _DEFAULT_OPENER,
    created_at: str,
) -> dict[str, Any]:
    specs = (
        EndpointSpec("version", "/api/v1/version", (), "version"),
        EndpointSpec("projects", "/api/v1/projects", ("projects",), "project"),
        EndpointSpec("servers", "/api/v1/servers", ("servers",), "server"),
        EndpointSpec("destinations", "/api/v1/destinations", ("destinations",), "destination"),
        EndpointSpec("applications", "/api/v1/applications", ("applications",), "application"),
        EndpointSpec("services", "/api/v1/services", ("services",), "service"),
        EndpointSpec("resources", "/api/v1/resources", ("resources",), "resource"),
    )
    endpoints: dict[str, Any] = {}
    for spec in specs:
        endpoints[spec.label] = _request_summary(
            controller,
            spec,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=max_items,
            opener=opener,
        )
        if endpoints[spec.label].get("status") in {401, 403}:
            break

    projects = endpoints.get("projects", {})
    for project_uuid in _project_uuids(projects):
        if len(endpoints) >= _MAX_ENDPOINTS:
            break
        encoded = urllib.parse.quote(project_uuid, safe="")
        label = f"environments:{project_uuid}"
        endpoints[label] = _request_summary(
            controller,
            EndpointSpec(
                label,
                f"/api/v1/projects/{encoded}/environments",
                ("environments",),
                "environment",
            ),
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_items=max_items,
            opener=opener,
        )

    return _evidence_document(
        command="inventory",
        controller=controller,
        private_state=private_state,
        created_at=created_at,
        endpoints=endpoints,
    )


def _evidence_document(
    *,
    command: str,
    controller: CoolifyController,
    private_state: PrivateStateReadResult,
    created_at: str,
    endpoints: Mapping[str, Any],
) -> dict[str, Any]:
    failed = sorted(label for label, result in endpoints.items() if not bool(result.get("ok")))
    counts = {
        label: int(result.get("item_count", 0))
        for label, result in endpoints.items()
    }
    return {
        "command": command,
        "created_at": created_at,
        "endpoints": dict(endpoints),
        "kind": _OBSERVATION_KIND,
        "mother_binding": {
            "content_sha256": private_state.binding.content_hash.digest,
            "generation": private_state.binding.generation,
            "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
        },
        "policy": {
            "allowed_method": "GET",
            "mutation_authority": controller.mutation_authority,
            "raw_response_persisted": False,
            "redirects_followed": False,
        },
        "summary": {
            "complete": not failed,
            "counts": counts,
            "failed_endpoints": failed,
            "successful_endpoints": sorted(label for label in endpoints if label not in failed),
        },
        "target": {
            "base_url": controller.base_url,
            "controller_id": controller.controller_id,
            "enabled": controller.enabled,
            "network": controller.network,
            "project_name_hint": controller.project_name_hint,
        },
    }


def write_coolify_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> Path:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if type(evidence) is not dict:
        evidence = dict(evidence)
    if evidence.get("kind") != _OBSERVATION_KIND:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_EVIDENCE", "invalid Coolify evidence kind")
    target = evidence.get("target")
    if type(target) is not dict:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_EVIDENCE", "Coolify evidence target is missing")
    network = _identifier(target.get("network"), "network")
    controller_id = _identifier(target.get("controller_id"), "controller_id")
    command = _identifier(evidence.get("command"), "command")
    created_at = evidence.get("created_at")
    if type(created_at) is not str or not created_at:
        raise CoolifyObservationError("MOTHER_COOLIFY_INVALID_EVIDENCE", "Coolify evidence timestamp is missing")

    payload = canonical_json(dict(evidence))
    digest = hashlib.sha256(payload).hexdigest()
    stamp = re.sub(r"[^0-9A-Za-z]+", "", created_at)[:32] or "observation"
    evidence_root = paths.root / "evidence" / "coolify"
    try:
        evidence_root.relative_to(paths.root)
    except ValueError as exc:
        raise CoolifyObservationError("MOTHER_COOLIFY_UNSAFE_EVIDENCE_PATH", "Coolify evidence path escapes Mother state") from exc

    current = paths.root
    for part in ("evidence", "coolify"):
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    filename = f"{stamp}-{network}-{controller_id}-{command}-{digest[:12]}.json"
    destination = evidence_root / filename
    if destination.exists():
        if destination.read_bytes() == payload:
            return destination
        raise CoolifyObservationError("MOTHER_COOLIFY_EVIDENCE_CONFLICT", "Coolify evidence target already exists")
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise CoolifyObservationError("MOTHER_COOLIFY_EVIDENCE_WRITE_FAILED", "Coolify evidence reread mismatch")
    return destination


def safe_controller_summary(controller: CoolifyController) -> dict[str, Any]:
    return {
        "base_url": controller.base_url,
        "controller_id": controller.controller_id,
        "enabled": controller.enabled,
        "has_api_token": bool(controller.api_token.strip()),
        "mutation_authority": controller.mutation_authority,
        "network": controller.network,
        "project_name_hint": controller.project_name_hint,
    }
