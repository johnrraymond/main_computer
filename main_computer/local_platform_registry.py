from __future__ import annotations

import json
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
REGISTRY_RELATIVE_PATH = PurePosixPath("runtime/local-platform/sites.json")
BUILTIN_SITE_PORT_START = 18080
BUILTIN_SITE_PORT_END = 18099
GENERATED_SITE_PORT_START = 18100
GENERATED_SITE_PORT_END = 18999
ENV_REGISTRY_PATH = "MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH"
ENV_BUILTIN_PORT_START = "MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START"
ENV_GENERATED_PORT_START = "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START"
ENV_GENERATED_PORT_END = "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END"
PROD_LANE_ALIASES = {"local", "local-prod", "prod", "production"}
SUPPORTED_LANES = {"prod", "dev"}


class LocalPlatformRegistryError(ValueError):
    """Raised when local platform registry data is missing or invalid."""


@dataclass(frozen=True)
class LocalPlatformLane:
    lane: str
    service: str
    port: int
    url: str
    status_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "port": self.port,
            "url": self.url,
            "status_url": self.status_url,
        }


@dataclass(frozen=True)
class LocalPlatformSite:
    id: str
    name: str
    kind: str
    repo_relative_path: str
    lanes: dict[str, LocalPlatformLane]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "repo_relative_path": self.repo_relative_path,
            "lanes": {lane_name: lane.to_dict() for lane_name, lane in sorted(self.lanes.items())},
        }


@dataclass(frozen=True)
class LocalPlatformRegistry:
    schema_version: int
    sites: dict[str, LocalPlatformSite]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sites": {site_id: site.to_dict() for site_id, site in sorted(self.sites.items())},
        }

    def list_sites(self) -> list[LocalPlatformSite]:
        return [self.sites[site_id] for site_id in sorted(self.sites)]

    def resolve(self, site_id: object, lane: object = "prod") -> LocalPlatformLane:
        clean_site_id = _clean_required_string(site_id, "site id")
        if clean_site_id not in self.sites:
            raise LocalPlatformRegistryError(f"Website is not registered with the local platform: {clean_site_id}")
        lane_name = normalize_registry_lane(lane)
        site = self.sites[clean_site_id]
        try:
            return site.lanes[lane_name]
        except KeyError as exc:
            raise LocalPlatformRegistryError(
                f"Website {clean_site_id} does not have a local platform lane: {lane_name}"
            ) from exc


def normalize_registry_lane(lane: object, default_lane: str = "prod") -> str:
    value = str(lane or default_lane or "prod").strip().lower()
    if value in PROD_LANE_ALIASES:
        return "prod"
    if value == "dev":
        return "dev"
    raise LocalPlatformRegistryError(f"Unsupported local platform lane: {value}")


def registry_lane_to_publish_lane(lane: object) -> str:
    lane_name = normalize_registry_lane(lane)
    return "local" if lane_name == "prod" else lane_name


def _env_path(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _env_int(name: str, default: int) -> int:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise LocalPlatformRegistryError(f"Environment variable {name} must be an integer.") from exc
    if parsed < 1 or parsed > 65535:
        raise LocalPlatformRegistryError(f"Environment variable {name} must be a TCP port in 1-65535.")
    return parsed


def builtin_site_port_start() -> int:
    return _env_int(ENV_BUILTIN_PORT_START, BUILTIN_SITE_PORT_START)


def builtin_site_port_end() -> int:
    return builtin_site_port_start() + (BUILTIN_SITE_PORT_END - BUILTIN_SITE_PORT_START)


def generated_site_port_start() -> int:
    return _env_int(ENV_GENERATED_PORT_START, GENERATED_SITE_PORT_START)


def generated_site_port_end() -> int:
    return _env_int(ENV_GENERATED_PORT_END, GENERATED_SITE_PORT_END)


def registry_path(repo_root: Path) -> Path:
    override = _env_path(ENV_REGISTRY_PATH)
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = repo_root / path
        return path.resolve()
    return repo_root / REGISTRY_RELATIVE_PATH


def default_registry_data() -> dict[str, Any]:
    builtins_start = builtin_site_port_start()
    return {
        "schema_version": SCHEMA_VERSION,
        "sites": {
            "hub-site": {
                "id": "hub-site",
                "name": "Hub Site",
                "kind": "hub-site",
                "repo_relative_path": "runtime/websites/hub-site",
                "lanes": {
                    "prod": {
                        "service": "hub-local",
                        "port": builtins_start,
                        "url": f"http://localhost:{builtins_start}/",
                        "status_url": f"http://localhost:{builtins_start}/api/site/status",
                    },
                    "dev": {
                        "service": "hub-dev",
                        "port": builtins_start + 2,
                        "url": f"http://localhost:{builtins_start + 2}/",
                        "status_url": f"http://localhost:{builtins_start + 2}/api/site/status",
                    },
                },
            },
            "blog-site": {
                "id": "blog-site",
                "name": "Blog Site",
                "kind": "blog-site",
                "repo_relative_path": "runtime/websites/blog-site",
                "lanes": {
                    "prod": {
                        "service": "blog-local",
                        "port": builtins_start + 1,
                        "url": f"http://localhost:{builtins_start + 1}/",
                        "status_url": f"http://localhost:{builtins_start + 1}/api/site/status",
                    },
                    "dev": {
                        "service": "blog-dev",
                        "port": builtins_start + 3,
                        "url": f"http://localhost:{builtins_start + 3}/",
                        "status_url": f"http://localhost:{builtins_start + 3}/api/site/status",
                    },
                },
            },
        },
    }


def load_local_platform_registry(repo_root: Path, *, create_if_missing: bool = True) -> LocalPlatformRegistry:
    path = registry_path(repo_root)
    if not path.exists():
        if not create_if_missing:
            raise LocalPlatformRegistryError(f"Local platform registry is missing: {REGISTRY_RELATIVE_PATH}")
        save_local_platform_registry(repo_root, default_registry_data())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalPlatformRegistryError(f"Local platform registry is not valid JSON: {REGISTRY_RELATIVE_PATH}") from exc
    return parse_local_platform_registry(repo_root, payload)


def save_local_platform_registry(repo_root: Path, data: LocalPlatformRegistry | dict[str, Any]) -> LocalPlatformRegistry:
    registry = data if isinstance(data, LocalPlatformRegistry) else parse_local_platform_registry(repo_root, data)
    path = registry_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(registry.to_dict(), indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp_name = handle.name
    os.replace(tmp_name, path)
    return registry


def list_managed_sites(repo_root: Path) -> list[LocalPlatformSite]:
    return load_local_platform_registry(repo_root).list_sites()


def resolve_site_lane(repo_root: Path, site_id: object, lane: object = "prod") -> LocalPlatformLane:
    return load_local_platform_registry(repo_root).resolve(site_id, lane)


def allocate_site_ports(
    registry: LocalPlatformRegistry,
    *,
    extra_reserved_ports: set[int] | None = None,
    probe_host_ports: bool = True,
) -> dict[str, int]:
    """Return the next generated Local Server / Deploy port pair.

    The default dev install uses 18080-18099 for built-in sites and
    18100-18999 for generated sites. Bootstrap can override the generated range
    per mode so Debug and Safe local servers do not collide with dev.

    Allocation is deliberately non-destructive: an occupied host port is treated
    as unavailable and the allocator chooses another pair. It never kills or
    removes the process/container that owns a port.
    """

    used_ports = _used_registry_ports(registry)
    if extra_reserved_ports:
        used_ports.update(_clean_reserved_ports(extra_reserved_ports))
    range_start = generated_site_port_start()
    range_end = generated_site_port_end()
    first_prod_port = range_start
    if first_prod_port % 2:
        first_prod_port += 1
    for prod_port in range(first_prod_port, range_end + 1, 2):
        dev_port = prod_port + 1
        if dev_port > range_end:
            break
        if prod_port in used_ports or dev_port in used_ports:
            continue
        if probe_host_ports and (not _tcp_port_can_bind(prod_port) or not _tcp_port_can_bind(dev_port)):
            continue
        return {"prod": prod_port, "dev": dev_port}
    raise LocalPlatformRegistryError(
        f"No generated website port pair is available in {range_start}-{range_end}."
    )


def parse_local_platform_registry(repo_root: Path, payload: object) -> LocalPlatformRegistry:
    if not isinstance(payload, dict):
        raise LocalPlatformRegistryError("Local platform registry must be a JSON object.")
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise LocalPlatformRegistryError(f"Unsupported local platform registry schema_version: {schema_version!r}")
    raw_sites = payload.get("sites")
    if not isinstance(raw_sites, dict):
        raise LocalPlatformRegistryError("Local platform registry must contain a sites object.")
    sites: dict[str, LocalPlatformSite] = {}
    for site_id, raw_site in raw_sites.items():
        site = _parse_site(repo_root, site_id, raw_site)
        sites[site.id] = site
    _validate_unique_registry_ports(sites)
    return LocalPlatformRegistry(schema_version=SCHEMA_VERSION, sites=sites)


def _parse_site(repo_root: Path, site_id: object, payload: object) -> LocalPlatformSite:
    clean_id = _clean_required_string(site_id, "site id")
    if not isinstance(payload, dict):
        raise LocalPlatformRegistryError(f"Registry site entry must be an object: {clean_id}")
    embedded_id = _clean_required_string(payload.get("id", clean_id), "site id")
    if embedded_id != clean_id:
        raise LocalPlatformRegistryError(f"Registry site id mismatch: {clean_id}")
    repo_relative_path = _normalize_repo_relative_path(payload.get("repo_relative_path"), repo_root)
    raw_lanes = payload.get("lanes")
    if not isinstance(raw_lanes, dict):
        raise LocalPlatformRegistryError(f"Registry site must contain lanes: {clean_id}")
    lanes: dict[str, LocalPlatformLane] = {}
    for lane_name, raw_lane in raw_lanes.items():
        clean_lane = normalize_registry_lane(lane_name)
        if clean_lane in lanes:
            raise LocalPlatformRegistryError(f"Duplicate registry lane for site {clean_id}: {clean_lane}")
        lanes[clean_lane] = _parse_lane(clean_lane, clean_id, raw_lane)
    return LocalPlatformSite(
        id=clean_id,
        name=str(payload.get("name") or clean_id),
        kind=str(payload.get("kind") or "static-site"),
        repo_relative_path=repo_relative_path,
        lanes=lanes,
    )


def _parse_lane(lane_name: str, site_id: str, payload: object) -> LocalPlatformLane:
    if not isinstance(payload, dict):
        raise LocalPlatformRegistryError(f"Registry lane entry must be an object: {site_id}/{lane_name}")
    service = _clean_required_string(payload.get("service"), f"{site_id}/{lane_name} service")
    port = _clean_port(payload.get("port"), f"{site_id}/{lane_name} port")
    url = _clean_required_string(payload.get("url"), f"{site_id}/{lane_name} url")
    status_url = _clean_required_string(payload.get("status_url", url), f"{site_id}/{lane_name} status_url")
    return LocalPlatformLane(lane=lane_name, service=service, port=port, url=url, status_url=status_url)


def _used_registry_ports(registry: LocalPlatformRegistry) -> set[int]:
    return {lane.port for site in registry.sites.values() for lane in site.lanes.values()}


def _clean_reserved_ports(values: set[int]) -> set[int]:
    ports: set[int] = set()
    for value in values:
        try:
            port = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535:
            ports.add(port)
    return ports


def _tcp_port_can_bind(port: int) -> bool:
    """Return True when a generated website may safely claim an IPv4 host port."""

    try:
        clean_port = int(port)
    except (TypeError, ValueError):
        return False
    if clean_port < 1 or clean_port > 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            sock.bind(("0.0.0.0", clean_port))
        except OSError:
            return False
    return True


def _validate_unique_registry_ports(sites: dict[str, LocalPlatformSite]) -> None:
    seen: dict[int, str] = {}
    for site in sites.values():
        for lane_name, lane in site.lanes.items():
            owner = f"{site.id}/{lane_name}"
            previous_owner = seen.get(lane.port)
            if previous_owner:
                raise LocalPlatformRegistryError(
                    f"Duplicate local platform registry port {lane.port}: {previous_owner} and {owner}"
                )
            seen[lane.port] = owner


def _clean_required_string(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LocalPlatformRegistryError(f"Missing registry value: {field}")
    return text


def _clean_port(value: object, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise LocalPlatformRegistryError(f"Invalid registry port: {field}") from exc
    if port < 1 or port > 65535:
        raise LocalPlatformRegistryError(f"Registry port is outside 1-65535: {field}")
    return port


def _normalize_repo_relative_path(value: object, repo_root: Path) -> str:
    text = _clean_required_string(value, "repo_relative_path").replace("\\", "/")
    pure_path = PurePosixPath(text)
    if pure_path.is_absolute():
        raise LocalPlatformRegistryError("Registry repo_relative_path must not be absolute.")
    parts = [part for part in pure_path.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise LocalPlatformRegistryError("Registry repo_relative_path must be safe and repo-relative.")
    normalized = "/".join(parts)
    target = (repo_root / normalized).resolve()
    root = repo_root.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise LocalPlatformRegistryError("Registry repo_relative_path escapes the repo root.") from exc
    return normalized


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Inspect the Main Computer local platform website registry.")
    parser.add_argument("action", choices=["list", "resolve", "allocate-ports"])
    parser.add_argument("site_id", nargs="?")
    parser.add_argument("--lane", default="prod")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if args.action == "list":
        print(json.dumps(load_local_platform_registry(repo_root).to_dict(), indent=2, sort_keys=True))
        return 0
    if args.action == "resolve":
        if not args.site_id:
            raise SystemExit("site_id is required for resolve")
        print(json.dumps(resolve_site_lane(repo_root, args.site_id, args.lane).to_dict(), indent=2, sort_keys=True))
        return 0
    if args.action == "allocate-ports":
        print(json.dumps(allocate_site_ports(load_local_platform_registry(repo_root)), indent=2, sort_keys=True))
        return 0
    raise SystemExit("Unsupported action.")


if __name__ == "__main__":
    raise SystemExit(main())
