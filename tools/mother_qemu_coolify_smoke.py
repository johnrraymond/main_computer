#!/usr/bin/env python3
"""Mother-native nested Coolify/QEMU architecture smoke.

The smoke deliberately starts from Mother's committed global Coolify-host binding:

    Mother runtime main_computer.private.yaml
      -> coolify.hosts entry (controller URL/token/project name/host identity)
      -> Coolify API placement discovery (project UUID/server UUID)
      -> separate Coolify environment (default: qemu-coolify-smoke)
      -> Docker service with /dev/kvm
      -> QEMU Ubuntu guest
      -> guest Docker Engine
      -> guest Coolify
      -> host-published TCP port

It never uses or mutates the normal ``mainnet`` environment.  A successful run
leaves the nested Coolify appliance running so an operator can connect to it.
Use ``--cleanup`` to remove the smoke service; the environment is removed only
when the local smoke receipt proves this tool created it.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import time
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

import yaml


def _install_repo_import_path(explicit_repo_root: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit_repo_root:
        candidates.append(Path(explicit_repo_root).expanduser().resolve(strict=False))
    candidates.append(Path.cwd().resolve(strict=False))
    here = Path(__file__).resolve(strict=False)
    candidates.extend([here.parent, *(here.parents[:4])])
    for candidate in candidates:
        if (candidate / "tools" / "mother" / "common").is_dir():
            text = str(candidate)
            if text not in sys.path:
                sys.path.insert(0, text)
            return candidate
    raise SystemExit("Could not find repo root containing tools/mother/common; run from the repository root or pass --repo-root.")


def _preparse_repo_root(argv: list[str]) -> str | None:
    for index, item in enumerate(argv):
        if item == "--repo-root" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--repo-root="):
            return item.split("=", 1)[1]
    return None


REPO_ROOT = _install_repo_import_path(_preparse_repo_root(sys.argv[1:]))

from tools.mother.common.coolify_state import CoolifyController


KIND = "main_computer.mother.qemu_coolify_nested_smoke.v1"
STATE_SCOPE = "infrastructure"
DEFAULT_CONTROLLER = "coolify-c"
DEFAULT_ENVIRONMENT = "qemu-coolify-smoke"
DEFAULT_SERVICE = "mother-qemu-coolify-smoke"
DEFAULT_HOST_PORT = 18000
DEFAULT_WAIT_SECONDS = 1800.0
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_VM_CPUS = 4
DEFAULT_VM_RAM_MIB = 4096
DEFAULT_VM_DISK_GIB = 40
DEFAULT_UBUNTU_IMAGE = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SmokeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> SmokeError:
    return SmokeError(code, message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower()


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _IDENTIFIER_RE.fullmatch(value.strip()):
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_INVALID_ARGUMENT", f"invalid {label}: {value!r}")
    return value.strip()


def _separate_environment(value: Any) -> str:
    environment = _identifier(value, "smoke environment")
    if environment.lower() == "mainnet":
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_NOT_ISOLATED",
            "the QEMU/Coolify smoke must run in a separate environment; mainnet is forbidden",
        )
    return environment


def _private_state_candidates(runtime_state_root: str | Path, explicit_path: str | Path | None = None) -> list[Path]:
    if explicit_path:
        return [Path(explicit_path).expanduser().resolve(strict=False)]
    root = Path(runtime_state_root).expanduser().resolve(strict=False)
    return [root / "main_computer.private.yaml"]


def _load_runtime_private_document(
    runtime_state_root: str | Path,
    *,
    explicit_path: str | Path | None = None,
) -> tuple[dict[str, Any], Path, str]:
    candidates = _private_state_candidates(runtime_state_root, explicit_path)
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        rendered = ", ".join(str(candidate) for candidate in candidates)
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_PRIVATE_STATE_MISSING",
            f"Mother runtime main_computer.private.yaml was not found; checked: {rendered}",
        )
    try:
        raw = path.read_bytes()
        loaded = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_PRIVATE_STATE_INVALID",
            f"could not parse Mother runtime private state at {path}: {exc}",
        ) from exc
    if not isinstance(loaded, Mapping):
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_PRIVATE_STATE_INVALID",
            f"Mother runtime private state at {path} is not a YAML mapping",
        )
    return dict(loaded), path, hashlib.sha256(raw).hexdigest()


def _global_coolify_binding_from_document(document: Mapping[str, Any], controller_id: str) -> tuple[CoolifyController, dict[str, Any]]:
    requested = _identifier(controller_id, "controller id")
    coolify = document.get("coolify")
    if not isinstance(coolify, Mapping):
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_PRIVATE_STATE_INVALID", "Mother runtime coolify state is missing")
    hosts = coolify.get("hosts")
    if not isinstance(hosts, Mapping):
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_PRIVATE_STATE_INVALID", "Mother runtime coolify.hosts mapping is missing")

    matches: list[tuple[str, Mapping[str, Any]]] = []
    for slot, value in hosts.items():
        if not isinstance(slot, str) or not isinstance(value, Mapping):
            continue
        names = {slot}
        for key in ("name", "controller_id", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                names.add(candidate.strip())
        if requested in names:
            matches.append((slot, value))

    if not matches:
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_CONTROLLER_NOT_FOUND",
            f"Mother runtime Coolify host not found in coolify.hosts: {requested}",
        )
    if len(matches) != 1:
        slots = ", ".join(sorted(slot for slot, _ in matches))
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_CONTROLLER_AMBIGUOUS",
            f"Mother runtime Coolify host {requested!r} matched multiple slots: {slots}",
        )

    slot, wire = matches[0]
    base_url = wire.get("url", wire.get("coolify_url"))
    api_token = wire.get("api_token", "")
    enabled = wire.get("enabled", True)
    project_name = wire.get("project_name", coolify.get("project_name", ""))

    if not isinstance(base_url, str) or not base_url.strip():
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_PRIVATE_STATE_INVALID", f"{requested} lacks coolify.hosts.{slot}.url")
    if not isinstance(api_token, str) or not api_token.strip():
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_PRIVATE_STATE_INVALID", f"{requested} lacks coolify.hosts.{slot}.api_token")
    if enabled is not True:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_CONTROLLER_DISABLED", f"Mother runtime Coolify host is disabled: {requested}")
    if not isinstance(project_name, str) or not project_name.strip():
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_PRIVATE_STATE_INVALID",
            f"{requested} lacks project_name in coolify.hosts.{slot} (or coolify.project_name)",
        )

    controller = CoolifyController(
        network=STATE_SCOPE,
        controller_id=requested,
        base_url=base_url.strip().rstrip("/"),
        api_token=api_token,
        enabled=True,
        project_name_hint=project_name.strip(),
        mutation_authority="observe-only",
    )
    config: dict[str, Any] = {
        "controller_id": requested,
        "host_slot": slot,
        "project_name": project_name.strip(),
    }
    # Optional explicit placement hints are honored when present, but the file
    # does not need them.  The common state currently carries project_name and
    # physical-host identity, so API discovery is the normal path.
    for key in (
        "project_uuid",
        "server_uuid",
        "server_name",
        "droplet_hostname",
        "public_ip",
        "vpn_ip",
    ):
        value = wire.get(key)
        if isinstance(value, str) and value.strip():
            config[key] = value.strip()
    return controller, config


@dataclass(slots=True)
class ApiResponse:
    status: int
    ok: bool
    payload: Any
    response_sha256: str


class CoolifyApi:
    def __init__(self, controller: CoolifyController, *, timeout: float = 30.0, max_response_bytes: int = 4 * 1024 * 1024) -> None:
        self.controller = controller
        self.timeout = float(timeout)
        self.max_response_bytes = int(max_response_bytes)

    def request(self, method: str, endpoint: str, body: Mapping[str, Any] | None = None) -> ApiResponse:
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            not endpoint.startswith("/api/v1/")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or "\\" in endpoint
            or "\x00" in endpoint
            or any(part in {"..", "."} for part in Path(parsed.path).parts)
        ):
            raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_UNSAFE_ENDPOINT", f"unsafe Coolify endpoint: {endpoint!r}")
        raw_body = json.dumps(dict(body), sort_keys=True, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "Authorization": f"Bearer {self.controller.api_token}",
            "User-Agent": "main-computer-mother-qemu-coolify-smoke/1",
        }
        if raw_body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.controller.base_url + endpoint,
            data=raw_body,
            headers=headers,
            method=method.upper(),
        )
        try:
            try:
                response = urllib.request.urlopen(request, timeout=self.timeout)
                status = int(getattr(response, "status", response.getcode()))
                raw = response.read(self.max_response_bytes + 1)
                response.close()
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                raw = exc.read(self.max_response_bytes + 1)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_REQUEST_FAILED", f"Coolify request failed for {method} {endpoint}: {exc}") from exc
        if len(raw) > self.max_response_bytes:
            raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_RESPONSE_TOO_LARGE", f"Coolify response exceeded {self.max_response_bytes} bytes")
        try:
            payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = raw.decode("utf-8", errors="replace")
        return ApiResponse(status=status, ok=200 <= status < 300, payload=payload, response_sha256=hashlib.sha256(raw).hexdigest())


def _records(payload: Any, *preferred_keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in (*preferred_keys, "data", "items", "services", "environments"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _record_uuid(record: Mapping[str, Any]) -> str:
    for key in ("uuid", "service_uuid", "application_uuid", "environment_uuid"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _extract_uuid(payload: Any) -> str:
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in {"uuid", "service_uuid", "application_uuid", "environment_uuid"} and child is not None:
                    text = str(child).strip()
                    if text:
                        found.add(text)
                elif isinstance(child, (Mapping, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    if len(found) == 1:
        return next(iter(found))
    return ""


def _exact_named(records: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [record for record in records if str(record.get("name") or "").strip() == name]



def _resolve_project_uuid(api: "CoolifyApi", controller_config: Mapping[str, Any]) -> str:
    explicit = str(controller_config.get("project_uuid") or "").strip()
    if explicit:
        return _identifier(explicit, "project UUID")
    project_name = str(controller_config.get("project_name") or "").strip()
    if not project_name:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_PROJECT_UNRESOLVED", "Mother runtime state did not provide a project_name")
    response = api.request("GET", "/api/v1/projects")
    if not response.ok:
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_PROJECT_LIST_FAILED",
            f"GET /api/v1/projects failed: HTTP {response.status}",
        )
    matches = _exact_named(_records(response.payload, "projects"), project_name)
    with_uuid = [item for item in matches if _record_uuid(item)]
    if len(with_uuid) != 1:
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_PROJECT_UNRESOLVED",
            f"expected exactly one Coolify project named {project_name!r}; matched {len(with_uuid)}",
        )
    return _record_uuid(with_uuid[0])


def _server_identity_matches(record: Mapping[str, Any], controller_config: Mapping[str, Any]) -> bool:
    names = {
        str(controller_config.get(key) or "").strip()
        for key in ("controller_id", "server_name", "droplet_hostname")
        if str(controller_config.get(key) or "").strip()
    }
    ips = {
        str(controller_config.get(key) or "").strip()
        for key in ("public_ip", "vpn_ip")
        if str(controller_config.get(key) or "").strip()
    }
    record_names = {
        str(record.get(key) or "").strip()
        for key in ("name", "hostname", "server_name")
        if str(record.get(key) or "").strip()
    }
    record_ips = {
        str(record.get(key) or "").strip()
        for key in ("ip", "public_ip", "private_ip", "host")
        if str(record.get(key) or "").strip()
    }
    return bool((names & record_names) or (ips & record_ips))


def _resolve_server_uuid(api: "CoolifyApi", controller_config: Mapping[str, Any]) -> str:
    explicit = str(controller_config.get("server_uuid") or "").strip()
    if explicit:
        return _identifier(explicit, "server UUID")
    response = api.request("GET", "/api/v1/servers")
    if not response.ok:
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_SERVER_LIST_FAILED",
            f"GET /api/v1/servers failed: HTTP {response.status}",
        )
    candidates = [record for record in _records(response.payload, "servers") if _record_uuid(record)]
    identity_matches = [record for record in candidates if _server_identity_matches(record, controller_config)]
    if len(identity_matches) == 1:
        return _record_uuid(identity_matches[0])
    if len(identity_matches) > 1:
        names = [str(item.get("name") or "") for item in identity_matches]
        raise _fail(
            "MOTHER_QEMU_COOLIFY_SMOKE_SERVER_AMBIGUOUS",
            f"Mother host identity matched multiple Coolify servers: {names}",
        )
    if len(candidates) == 1:
        # Existing Main Computer Coolify tooling uses the same single-server
        # inference when no explicit server selector is present.
        return _record_uuid(candidates[0])
    names = [str(item.get("name") or "") for item in candidates]
    raise _fail(
        "MOTHER_QEMU_COOLIFY_SMOKE_SERVER_UNRESOLVED",
        f"could not uniquely resolve the Coolify server for {controller_config.get('controller_id')!r}; available server names={names}",
    )


def _resolve_placement(api: "CoolifyApi", controller_config: Mapping[str, Any]) -> dict[str, Any]:
    resolved = dict(controller_config)
    resolved["project_uuid"] = _resolve_project_uuid(api, resolved)
    resolved["server_uuid"] = _resolve_server_uuid(api, resolved)
    return resolved


def _credentials_dir(repo_root: Path, service_name: str) -> Path:
    return repo_root / "runtime" / "qemu-coolify-smoke" / service_name


def _credentials_path(repo_root: Path, service_name: str) -> Path:
    return _credentials_dir(repo_root, service_name) / "credentials.json"


def _receipt_path(repo_root: Path, service_name: str) -> Path:
    return _credentials_dir(repo_root, service_name) / "receipt.json"


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_or_create_credentials(repo_root: Path, service_name: str) -> dict[str, str]:
    path = _credentials_path(repo_root, service_name)
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        required = ("username", "email", "password")
        if isinstance(payload, dict) and all(isinstance(payload.get(key), str) and payload[key] for key in required):
            return {key: str(payload[key]) for key in required}
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_CREDENTIALS_INVALID", f"invalid credentials file: {path}")
    credentials = {
        "username": "SmokeAdmin",
        "email": "smoke-admin@example.com",
        "password": "Sm0ke-" + secrets.token_urlsafe(18) + "-Aa1",
    }
    _write_private_json(path, credentials)
    return credentials


def render_guest_user_data(credentials: Mapping[str, str]) -> str:
    username = str(credentials["username"])
    email = str(credentials["email"])
    password = str(credentials["password"])
    bootstrap = "\n".join(
        [
            "set -Eeuo pipefail",
            "echo MOTHER_QEMU_COOLIFY_GUEST_BOOTED",
            f"export ROOT_USERNAME={shlex.quote(username)}",
            f"export ROOT_USER_EMAIL={shlex.quote(email)}",
            f"export ROOT_USER_PASSWORD={shlex.quote(password)}",
            "export DOCKER_ADDRESS_POOL_BASE=172.20.0.0/14",
            "export DOCKER_ADDRESS_POOL_SIZE=24",
            "export AUTOUPDATE=false",
            "curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash",
            "until curl -fsS --max-time 5 http://127.0.0.1:8000/ >/dev/null; do sleep 3; done",
            "echo MOTHER_QEMU_COOLIFY_INNER_READY",
        ]
    )
    # cloud-init executes a scalar runcmd item through /bin/sh.  Ubuntu's /bin/sh
    # is dash, which does not implement `set -o pipefail`.  Use argv form so the
    # bootstrap is explicitly owned by Bash and failures in the installer pipe are
    # still surfaced by `set -Eeuo pipefail`.
    payload = {
        "package_update": True,
        "packages": ["curl", "ca-certificates"],
        "runcmd": [["bash", "-lc", bootstrap]],
        "final_message": "MOTHER_QEMU_COOLIFY_CLOUD_INIT_COMPLETE",
    }
    return "#cloud-config\n" + yaml.safe_dump(payload, sort_keys=False)


def render_outer_bootstrap(*, credentials: Mapping[str, str], vm_cpus: int, vm_ram_mib: int, vm_disk_gib: int, ubuntu_image_url: str) -> str:
    guest_user_data = render_guest_user_data(credentials)
    return f"""set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends qemu-system-x86 qemu-utils cloud-image-utils curl ca-certificates

test -c /dev/kvm || {{ echo MOTHER_QEMU_COOLIFY_NO_KVM >&2; exit 41; }}
qemu-system-x86_64 -accel help | grep -q kvm || {{ echo MOTHER_QEMU_COOLIFY_KVM_UNSUPPORTED >&2; exit 42; }}

BASE=/state/noble-server-cloudimg-amd64.img
ROOT=/state/root.qcow2
SEED=/state/seed.img

if [ ! -s \"$BASE\" ]; then
  curl -fL --retry 3 --output \"$BASE.part\" {ubuntu_image_url}
  mv \"$BASE.part\" \"$BASE\"
fi
qemu-img check \"$BASE\"

if [ ! -s \"$ROOT\" ]; then
  qemu-img create -f qcow2 -F qcow2 -b \"$BASE\" \"$ROOT\" {int(vm_disk_gib)}G
fi

cat >/state/user-data <<'MC_USER_DATA'
{guest_user_data}MC_USER_DATA
cat >/state/meta-data <<'MC_META_DATA'
instance-id: mother-qemu-coolify-smoke-v1
local-hostname: nested-coolify
MC_META_DATA
cloud-localds \"$SEED\" /state/user-data /state/meta-data

echo MOTHER_QEMU_COOLIFY_QEMU_STARTING
exec qemu-system-x86_64 \\
  -name mother-qemu-coolify-smoke \\
  -machine q35,accel=kvm \\
  -cpu host \\
  -smp {int(vm_cpus)} \\
  -m {int(vm_ram_mib)}M \\
  -drive if=virtio,format=qcow2,file=\"$ROOT\",cache=writeback,discard=unmap \\
  -drive if=virtio,format=raw,readonly=on,file=\"$SEED\" \\
  -device virtio-net-pci,netdev=net0 \\
  -netdev user,id=net0,hostfwd=tcp:0.0.0.0:8000-:8000 \\
  -display none \\
  -monitor none \\
  -serial stdio
"""


def render_compose(*, service_name: str, host_port: int, credentials: Mapping[str, str], vm_cpus: int, vm_ram_mib: int, vm_disk_gib: int, ubuntu_image_url: str) -> str:
    service = _identifier(service_name, "service name")
    if not 1024 <= int(host_port) <= 65535:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_INVALID_PORT", "host port must be between 1024 and 65535")
    shell = render_outer_bootstrap(
        credentials=credentials,
        vm_cpus=vm_cpus,
        vm_ram_mib=vm_ram_mib,
        vm_disk_gib=vm_disk_gib,
        ubuntu_image_url=ubuntu_image_url,
    ).replace("$", "$$")
    compose = {
        "services": {
            service: {
                "image": "ubuntu:24.04",
                "restart": "unless-stopped",
                "devices": ["/dev/kvm:/dev/kvm"],
                "ports": [f"{int(host_port)}:8000"],
                "volumes": ["qemu-state:/state"],
                "command": ["bash", "-lc", shell],
                "healthcheck": {
                    "test": ["CMD-SHELL", "curl -fsS --max-time 5 http://127.0.0.1:8000/ >/dev/null || exit 1"],
                    "interval": "10s",
                    "timeout": "6s",
                    "retries": 6,
                    "start_period": "90s",
                },
                "labels": {
                    "main_computer.mother.component": "qemu-coolify-nested-smoke",
                    "main_computer.mother.not_a_validator": "true",
                    "main_computer.mother.not_a_chain_service": "true",
                },
            }
        },
        "volumes": {"qemu-state": {}},
    }
    text = yaml.safe_dump(compose, sort_keys=False)
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, Mapping) or service not in parsed.get("services", {}):
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_COMPOSE_INVALID", "generated Compose did not round-trip")
    if "/dev/kvm:/dev/kvm" not in text or f"{int(host_port)}:8000" not in text:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_COMPOSE_INVALID", "generated Compose lost KVM or published-port binding")
    if "DOCKER_ADDRESS_POOL_BASE=172.20.0.0/14" not in text:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_COMPOSE_INVALID", "generated guest bootstrap lost isolated Docker pool")
    return text


def build_service_body(*, controller_config: Mapping[str, Any], environment_name: str, environment_uuid: str, service_name: str, compose: str) -> dict[str, Any]:
    environment = _separate_environment(environment_name)
    body: dict[str, Any] = {
        "project_uuid": str(controller_config["project_uuid"]),
        "server_uuid": str(controller_config["server_uuid"]),
        "environment_name": environment,
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": _identifier(service_name, "service name"),
        "description": "Mother-native isolated Docker -> QEMU/KVM -> Docker -> Coolify architecture smoke",
        "instant_deploy": False,
    }
    if environment_uuid:
        body["environment_uuid"] = environment_uuid
    return body


def _probe_url(controller: CoolifyController, *, host_port: int, probe_host: str) -> str:
    host = str(probe_host or "").strip()
    if not host:
        host = urllib.parse.urlsplit(controller.base_url).hostname or ""
    if not host:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_PROBE_HOST_MISSING", "could not derive a routable probe host from the Mother-bound Coolify URL")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{int(host_port)}/"


def _probe_http(url: str, timeout: float) -> tuple[bool, int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "main-computer-mother-qemu-coolify-smoke/1"}, method="GET")
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        status = int(getattr(response, "status", response.getcode()))
        response.read(4096)
        response.close()
        return 200 <= status < 400, status, ""
    except urllib.error.HTTPError as exc:
        return 200 <= int(exc.code) < 400, int(exc.code), str(exc.reason or "")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, 0, str(exc)


def _service_status(payload: Any) -> str:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in {"status", "health", "health_status", "service_status"} and isinstance(child, str):
                    values.append(child)
                if isinstance(child, (Mapping, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    for value in values:
        low = value.lower()
        if "running" in low and "healthy" in low and "unhealthy" not in low:
            return value
    return values[0] if values else "unknown"


def _best_effort_service_status(api: CoolifyApi, service_uuid: str) -> str:
    """Return diagnostic Coolify service status without affecting smoke acceptance.

    A service-detail request can block while Coolify is busy starting a heavy
    Compose service.  The authoritative acceptance signal for this smoke is the
    routable inner-Coolify HTTP probe, so diagnostic API failures must not abort
    the run.
    """
    try:
        response = api.request("GET", f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}")
    except SmokeError as exc:
        return f"unavailable:{exc.code}:{exc}"
    if not response.ok:
        return f"http-{response.status}"
    return _service_status(response.payload)


def _service_logs(api: CoolifyApi, service_uuid: str, service_name: str) -> str:
    query = urllib.parse.urlencode({"sub_service_name": service_name, "lines": 250, "show_timestamps": "false"})
    try:
        response = api.request("GET", f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/logs?{query}")
    except SmokeError as exc:
        return f"[Coolify logs unavailable: {exc.code}: {exc}]"
    if not response.ok:
        return f"[Coolify logs unavailable: HTTP {response.status}]"
    if isinstance(response.payload, Mapping):
        return str(response.payload.get("logs") or "")
    return str(response.payload or "")


def _delete_service(api: CoolifyApi, service_uuid: str) -> ApiResponse:
    return api.request("DELETE", f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}")


def _cleanup(args: argparse.Namespace, controller: CoolifyController, controller_config: Mapping[str, Any], api: CoolifyApi) -> int:
    receipt_path = _receipt_path(REPO_ROOT, args.service_name)
    if not receipt_path.is_file():
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_RECEIPT_MISSING", f"cleanup requires the local smoke receipt: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if str(receipt.get("project_uuid") or "") != str(controller_config["project_uuid"]):
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_BINDING_CHANGED", "Mother project binding no longer matches the smoke receipt")
    if str(receipt.get("server_uuid") or "") != str(controller_config["server_uuid"]):
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_BINDING_CHANGED", "Mother server binding no longer matches the smoke receipt")
    service_uuid = str(receipt.get("service_uuid") or "")
    environment_name = _separate_environment(receipt.get("environment_name"))
    result: dict[str, Any] = {"ok": False, "mode": "cleanup", "service_uuid": service_uuid, "environment_name": environment_name}
    if service_uuid:
        deleted = _delete_service(api, service_uuid)
        if not deleted.ok and deleted.status != 404:
            raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_DELETE_FAILED", f"service delete failed: HTTP {deleted.status} {deleted.payload}")
        result["service_deleted"] = True
    if bool(receipt.get("created_environment")):
        deadline = time.monotonic() + 120.0
        endpoint = f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments/{urllib.parse.quote(environment_name, safe='')}"
        while True:
            deleted_env = api.request("DELETE", endpoint)
            if deleted_env.ok or deleted_env.status == 404:
                result["environment_deleted"] = True
                break
            if time.monotonic() >= deadline:
                raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_DELETE_FAILED", f"environment delete failed: HTTP {deleted_env.status} {deleted_env.payload}")
            time.sleep(3.0)
    result["ok"] = True
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def run(args: argparse.Namespace) -> int:
    controller_id = _identifier(args.controller_id, "controller id")
    environment_name = _separate_environment(args.environment_name)
    service_name = _identifier(args.service_name, "service name")

    document, private_state_path, private_state_sha256 = _load_runtime_private_document(
        args.runtime_state_root,
        explicit_path=args.private_state,
    )
    controller, controller_config = _global_coolify_binding_from_document(document, controller_id)
    api = CoolifyApi(controller, timeout=args.api_timeout)
    controller_config = _resolve_placement(api, controller_config)

    if args.cleanup:
        return _cleanup(args, controller, controller_config, api)

    credentials_path = _credentials_path(REPO_ROOT, service_name)
    if args.dry_run and not credentials_path.is_file():
        credentials = {
            "username": "SmokeAdmin",
            "email": "smoke-admin@example.com",
            "password": "Sm0ke-dry-run-placeholder-Aa1",
        }
    else:
        credentials = _load_or_create_credentials(REPO_ROOT, service_name)
    compose = render_compose(
        service_name=service_name,
        host_port=args.host_port,
        credentials=credentials,
        vm_cpus=args.vm_cpus,
        vm_ram_mib=args.vm_ram_mib,
        vm_disk_gib=args.vm_disk_gib,
        ubuntu_image_url=args.ubuntu_image_url,
    )

    project_uuid = str(controller_config["project_uuid"])
    server_uuid = str(controller_config["server_uuid"])
    environments_endpoint = f"/api/v1/projects/{urllib.parse.quote(project_uuid, safe='')}/environments"
    env_response = api.request("GET", environments_endpoint)
    if not env_response.ok:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_LIST_FAILED", f"environment list failed: HTTP {env_response.status} {env_response.payload}")
    env_matches = _exact_named(_records(env_response.payload, "environments"), environment_name)
    if len(env_matches) > 1:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_AMBIGUOUS", f"multiple environments named {environment_name!r}")

    created_environment = False
    environment_uuid = _record_uuid(env_matches[0]) if env_matches else ""

    services_response = api.request("GET", "/api/v1/services")
    if not services_response.ok:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_LIST_FAILED", f"service list failed: HTTP {services_response.status} {services_response.payload}")
    service_matches = _exact_named(_records(services_response.payload, "services"), service_name)
    if service_matches:
        if len(service_matches) != 1:
            raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_AMBIGUOUS", f"multiple services named {service_name!r}")
        if not args.replace:
            raise _fail(
                "MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_EXISTS",
                f"Coolify already contains a service named {service_name!r}; rerun with --replace or --cleanup",
            )
        if not args.dry_run:
            old_uuid = _record_uuid(service_matches[0])
            if not old_uuid:
                raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_UUID_MISSING", "existing smoke service has no UUID")
            deleted = _delete_service(api, old_uuid)
            if not deleted.ok:
                raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_DELETE_FAILED", f"replace delete failed: HTTP {deleted.status} {deleted.payload}")
            time.sleep(2.0)

    if args.dry_run:
        result = {
            "ok": True,
            "mode": "dry-run",
            "mother_binding": {
                "scope": "runtime-main-computer-private-yaml",
                "private_state_path": str(private_state_path),
                "private_state_sha256": private_state_sha256,
                "host_slot": controller_config.get("host_slot"),
                "controller_id": controller_id,
                "coolify_url": controller.base_url,
                "project_uuid": project_uuid,
                "server_uuid": server_uuid,
            },
            "environment": {"name": environment_name, "existing": bool(env_matches), "uuid": environment_uuid or None},
            "service_name": service_name,
            "replace_existing_service": bool(service_matches and args.replace),
            "host_port": args.host_port,
            "probe_url": _probe_url(controller, host_port=args.host_port, probe_host=args.probe_host),
            "compose_sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if not env_matches:
        created = api.request("POST", environments_endpoint, {"name": environment_name})
        if not created.ok and created.status not in {409, 422}:
            raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_CREATE_FAILED", f"environment create failed: HTTP {created.status} {created.payload}")
        created_environment = created.ok
        environment_uuid = _extract_uuid(created.payload) if created.ok else ""
        refreshed = api.request("GET", environments_endpoint)
        if not refreshed.ok:
            raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_LIST_FAILED", "could not refresh environment list after create")
        matches = _exact_named(_records(refreshed.payload, "environments"), environment_name)
        if len(matches) != 1:
            raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_CREATE_UNVERIFIED", f"expected one environment named {environment_name!r} after create")
        environment_uuid = _record_uuid(matches[0]) or environment_uuid

    body = build_service_body(
        controller_config=controller_config,
        environment_name=environment_name,
        environment_uuid=environment_uuid,
        service_name=service_name,
        compose=compose,
    )
    created_service = api.request("POST", "/api/v1/services", body)
    if not created_service.ok:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_CREATE_FAILED", f"service create failed: HTTP {created_service.status} {created_service.payload}")
    service_uuid = _extract_uuid(created_service.payload)
    if not service_uuid:
        raise _fail("MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_UUID_MISSING", "Coolify service create returned no unambiguous UUID")

    start = api.request("POST", f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/start")
    if not start.ok:
        deploy = api.request("POST", f"/api/v1/deploy?{urllib.parse.urlencode({'uuid': service_uuid, 'force': 'true'})}")
        if not deploy.ok:
            raise _fail(
                "MOTHER_QEMU_COOLIFY_SMOKE_SERVICE_START_FAILED",
                f"service start failed (HTTP {start.status}) and deploy fallback failed (HTTP {deploy.status})",
            )

    receipt = {
        "kind": KIND,
        "created_at": _utc_now(),
        "binding_scope": "runtime-main-computer-private-yaml",
        "private_state_path": str(private_state_path),
        "private_state_sha256": private_state_sha256,
        "host_slot": controller_config.get("host_slot"),
        "controller_id": controller_id,
        "coolify_url": controller.base_url,
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
        "environment_name": environment_name,
        "environment_uuid": environment_uuid or None,
        "created_environment": created_environment,
        "service_name": service_name,
        "service_uuid": service_uuid,
        "host_port": int(args.host_port),
    }
    _write_private_json(_receipt_path(REPO_ROOT, service_name), receipt)

    probe_url = _probe_url(controller, host_port=args.host_port, probe_host=args.probe_host)
    deadline = time.monotonic() + float(args.wait_seconds)
    last_probe = {"ok": False, "status": 0, "error": "not attempted"}
    while True:
        ok, status, error = _probe_http(probe_url, min(float(args.api_timeout), 8.0))
        last_probe = {"ok": ok, "status": status, "error": error}
        if ok:
            break
        if time.monotonic() >= deadline:
            # Coolify's service-detail endpoint is diagnostic only.  In
            # particular it may itself time out while the heavy QEMU service is
            # starting, so query it only after the route deadline and never let
            # that diagnostic failure replace the actual route-timeout result.
            last_service_status = _best_effort_service_status(api, service_uuid)
            logs = _service_logs(api, service_uuid, service_name)
            tail = "\n".join(logs.splitlines()[-120:])
            raise _fail(
                "MOTHER_QEMU_COOLIFY_SMOKE_ROUTE_TIMEOUT",
                f"inner Coolify did not become reachable at {probe_url} before timeout; "
                f"last Coolify service status={last_service_status!r}; last probe={last_probe}; logs tail:\n{tail}",
            )
        time.sleep(float(args.poll_seconds))

    result = {
        "ok": True,
        "kind": KIND,
        "checks": {
            "motherPrivateStateRead": True,
            "motherProjectServerBindingUsed": True,
            "separateEnvironment": environment_name.lower() != "mainnet",
            "coolifyServiceCreated": True,
            "kvmDeviceRequested": True,
            "qemuGuestConfigured": True,
            "innerDockerAndCoolifyBootstrapConfigured": True,
            "innerCoolifyRoutable": True,
        },
        "mother_binding": {
            "scope": "runtime-main-computer-private-yaml",
            "private_state_path": str(private_state_path),
            "private_state_sha256": private_state_sha256,
            "host_slot": controller_config.get("host_slot"),
            "controller_id": controller_id,
            "coolify_url": controller.base_url,
            "project_uuid": project_uuid,
            "server_uuid": server_uuid,
        },
        "environment": {
            "name": environment_name,
            "uuid": environment_uuid or None,
            "created_by_smoke": created_environment,
        },
        "service": {"name": service_name, "uuid": service_uuid, "status": last_service_status},
        "nested_coolify": {
            "url": probe_url,
            "credentials_file": str(_credentials_path(REPO_ROOT, service_name)),
        },
        "cleanup_command": f"python tools/mother_qemu_coolify_smoke.py --controller-id {controller_id} --cleanup",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy Docker -> QEMU/KVM -> Docker -> Coolify through Mother's bound Coolify state.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repository root (normally auto-detected).")
    parser.add_argument("--runtime-state-root", default=str(REPO_ROOT / "runtime" / "state"), help="Runtime state root containing main_computer.private.yaml.")
    parser.add_argument("--private-state", default="", help="Explicit main_computer.private.yaml path. Defaults to runtime/state/main_computer.private.yaml.")
    parser.add_argument("--controller-id", default=DEFAULT_CONTROLLER)
    parser.add_argument("--environment-name", default=DEFAULT_ENVIRONMENT)
    parser.add_argument("--service-name", default=DEFAULT_SERVICE)
    parser.add_argument("--host-port", type=int, default=DEFAULT_HOST_PORT)
    parser.add_argument("--probe-host", default="", help="Override only the host used for the final routable-port probe; placement still comes only from Mother state.")
    parser.add_argument("--vm-cpus", type=int, default=DEFAULT_VM_CPUS)
    parser.add_argument("--vm-ram-mib", type=int, default=DEFAULT_VM_RAM_MIB)
    parser.add_argument("--vm-disk-gib", type=int, default=DEFAULT_VM_DISK_GIB)
    parser.add_argument("--ubuntu-image-url", default=DEFAULT_UBUNTU_IMAGE)
    parser.add_argument("--api-timeout", type=float, default=30.0)
    parser.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--dry-run", action="store_true", help="Read Mother state and render/validate the deployment without mutating Coolify.")
    parser.add_argument("--replace", action="store_true", help="Delete an existing exact-name smoke service before recreating it.")
    parser.add_argument("--cleanup", action="store_true", help="Delete the smoke service from its recorded separate environment; delete the environment only if this smoke created it.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.vm_cpus < 2:
        raise SystemExit("--vm-cpus must be >= 2")
    if args.vm_ram_mib < 2048:
        raise SystemExit("--vm-ram-mib must be >= 2048")
    if args.vm_disk_gib < 10:
        raise SystemExit("--vm-disk-gib must be >= 10")
    if args.api_timeout <= 0 or args.wait_seconds <= 0 or args.poll_seconds <= 0:
        raise SystemExit("timeouts/poll interval must be positive")
    try:
        return run(args)
    except SmokeError as exc:
        print(json.dumps({"ok": False, "error": f"{exc.code}: {exc}"}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
