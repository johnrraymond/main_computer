from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ClusterIdentity, CoordinatorEndpoint

_TOKEN = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class ParsedClusterFile:
    cluster: ClusterIdentity
    coordinator_addresses: tuple[str, ...]


def render_cluster_file(cluster: ClusterIdentity, coordinators: tuple[CoordinatorEndpoint, ...]) -> str:
    _require_cluster_token(cluster.description, "cluster description")
    _require_cluster_token(cluster.cluster_id, "cluster id")
    if not coordinators:
        raise ValueError("at least one coordinator is required")
    endpoints = tuple(sorted((coordinator.endpoint for coordinator in coordinators), key=lambda item: item.encode("utf-8")))
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("coordinator endpoints must be unique")
    return f"{cluster.description}:{cluster.cluster_id}@{','.join(endpoints)}"


def parse_cluster_file(contents: str) -> ParsedClusterFile:
    clean = str(contents or "").strip()
    if not clean or "@" not in clean or ":" not in clean.split("@", 1)[0]:
        raise ValueError("invalid FoundationDB cluster connection string")
    left, right = clean.split("@", 1)
    description, cluster_id = left.split(":", 1)
    _require_cluster_token(description, "cluster description")
    _require_cluster_token(cluster_id, "cluster id")
    endpoints = tuple(sorted((_normalize_endpoint(item) for item in right.split(",") if item.strip()), key=lambda item: item.encode("utf-8")))
    if not endpoints:
        raise ValueError("cluster connection string has no coordinators")
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("cluster connection string has duplicate coordinators")
    return ParsedClusterFile(
        cluster=ClusterIdentity(description=description, cluster_id=cluster_id),
        coordinator_addresses=endpoints,
    )


def _normalize_endpoint(value: str) -> str:
    clean = str(value or "").strip()
    if clean.count(":") != 1:
        raise ValueError(f"unsupported coordinator endpoint {clean!r}")
    host, raw_port = clean.rsplit(":", 1)
    if not host:
        raise ValueError("coordinator host must be non-empty")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError(f"invalid coordinator port {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"coordinator port outside 1..65535: {port}")
    return f"{host}:{port}"


def _require_cluster_token(value: str, field: str) -> None:
    if not _TOKEN.fullmatch(str(value or "")):
        raise ValueError(f"{field} must contain only ASCII letters, digits, and underscores")
