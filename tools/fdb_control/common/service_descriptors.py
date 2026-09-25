from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
from dataclasses import dataclass

from .models import BirthPlan, ServicePlacement

DEFAULT_FDB_IMAGE = "foundationdb/foundationdb:7.4.6"
DEFAULT_DATA_ROOT = "/data/main-computer/fdb"
BIRTH_PROOF_PREFIX = "FDB_BIRTH_PROOF_V1"


@dataclass(frozen=True, slots=True)
class ServiceDescriptor:
    service_name: str
    description: str
    compose: str

    @property
    def compose_b64(self) -> str:
        return base64.b64encode(self.compose.encode("utf-8")).decode("ascii")


def birth_observer_subservice_name(service_id: str) -> str:
    return _key(f"{service_id}-observer")


def birth_proof_marker(plan: BirthPlan, service: ServicePlacement) -> str:
    payload = json.dumps(
        {
            "cluster_file": plan.cluster_file_contents,
            "coordinators": [item.endpoint for item in plan.coordinators],
            "endpoint": service.endpoint,
            "service_id": service.service_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{BIRTH_PROOF_PREFIX} {hashlib.sha256(payload).hexdigest()}"


def render_birth_service_descriptor(
    plan: BirthPlan,
    service: ServicePlacement,
    *,
    image: str = DEFAULT_FDB_IMAGE,
    data_root: str = DEFAULT_DATA_ROOT,
) -> ServiceDescriptor:
    if len(plan.services) != 1:
        raise ValueError("initial birth descriptor currently requires exactly one FDB service")
    if plan.services[0].service_id != service.service_id:
        raise ValueError("service is not the frozen birth service")
    if plan.redundancy_mode != "single":
        raise ValueError("single-service cluster birth requires redundancy_mode='single'")
    if len(plan.coordinators) != 1 or plan.coordinators[0].service_id != service.service_id:
        raise ValueError("single-service cluster birth requires that service to be the sole coordinator")

    root = _posix(f"{data_root.rstrip('/')}/{plan.network}/{service.service_id}")
    cluster_file = f"{root}/fdb.cluster"
    data_dir = f"{root}/data"
    log_dir = f"{root}/logs"
    service_key = _key(service.service_id)
    observer_key = birth_observer_subservice_name(service.service_id)
    service_name = _key(f"main-computer-{service.service_id}")
    cluster_contents = plan.cluster_file_contents

    server_script = "\n".join(
        [
            "set -eu",
            f"mkdir -p {_q(root)} {_q(data_dir)} {_q(log_dir)}",
            f"printf '%s\\n' {_q(cluster_contents)} > {_q(cluster_file)}",
            f"echo 'Starting {service.service_id} at {service.endpoint}'",
            "exec /usr/bin/fdbserver \\",
            f"  --cluster-file {_q(cluster_file)} \\",
            f"  --public-address {_q(service.endpoint)} \\",
            f"  --listen-address {_q(f'0.0.0.0:{service.port}')} \\",
            f"  --datadir {_q(data_dir)} \\",
            f"  --logdir {_q(log_dir)} \\",
            f"  --locality-machineid {_q(service.machine_id)} \\",
            f"  --locality-zoneid {_q(service.zone_id)} \\",
            "  --class storage \\",
            "  --knob_disable_posix_kernel_aio 1",
        ]
    )
    configure = f"configure new {plan.redundancy_mode} {plan.storage_engine}"
    health_python = _health_python(cluster_contents, service.endpoint)
    proof_marker = birth_proof_marker(plan, service)
    observer_script = "\n".join(
        [
            "set -eu",
            f"mkdir -p {_q(root)}",
            f"printf '%s\n' {_q(cluster_contents)} > {_q(cluster_file)}",
            "for attempt in $(seq 1 120); do",
            f"  fdbcli -C {_q(cluster_file)} --exec {_q(configure)} --timeout 10 >/tmp/fdb-configure.log 2>&1 || true",
            f"  if fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 >/tmp/fdb-status.json 2>/tmp/fdb-status.err; then",
            f"    if python3 -c {_q(health_python)} </tmp/fdb-status.json; then",
            f"      printf '%s\n' {_q(proof_marker)}",
            "      break",
            "    fi",
            "  fi",
            "  if [ \"$attempt\" = 120 ]; then",
            "    cat /tmp/fdb-configure.log >&2 || true",
            "    cat /tmp/fdb-status.err >&2 || true",
            "    cat /tmp/fdb-status.json >&2 || true",
            "    exit 1",
            "  fi",
            "  sleep 2",
            "done",
            "tail -f /dev/null",
        ]
    )
    socket_health = f"python3 -c \"import socket; s=socket.create_connection((\'127.0.0.1\',{service.port}),timeout=3); s.close()\""
    observer_health = (
        f"fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 2>/dev/null "
        f"| python3 -c {_q(health_python)}"
    )

    lines = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {service_key}:",
        f"    image: {json.dumps(image)}",
        "    restart: unless-stopped",
        "    ports:",
        f"      - {json.dumps(f'{service.address}:{service.port}:{service.port}/tcp')}",
        "    volumes:",
        f"      - {json.dumps(f'{root}:{root}')}",
        "    entrypoint:",
        "      - /bin/sh",
        "      - -euc",
        f"      - {_compose_string(server_script)}",
        "    healthcheck:",
        f"      test: [\"CMD-SHELL\", {json.dumps(socket_health)}]",
        "      interval: 15s",
        "      timeout: 5s",
        "      start_period: 10s",
        "      retries: 8",
        "",
        f"  {observer_key}:",
        f"    image: {json.dumps(image)}",
        "    restart: unless-stopped",
        "    depends_on:",
        f"      {service_key}:",
        "        condition: service_healthy",
        "    volumes:",
        f"      - {json.dumps(f'{root}:{root}')}",
        "    entrypoint:",
        "      - /bin/sh",
        "      - -euc",
        f"      - {_compose_string(observer_script)}",
        "    healthcheck:",
        f"      test: [\"CMD-SHELL\", {json.dumps(observer_health)}]",
        "      interval: 15s",
        "      timeout: 12s",
        "      start_period: 15s",
        "      retries: 12",
        "",
    ]
    return ServiceDescriptor(
        service_name=service_name,
        description=f"Main Computer {plan.network} FoundationDB service {service.service_id}",
        compose="\n".join(lines),
    )


def _health_python(cluster_contents: str, endpoint: str) -> str:
    expected_cluster = cluster_contents
    expected_endpoint = endpoint
    # One line so it is safe to pass to python -c through the shell.
    return (
        "import json,sys;d=json.load(sys.stdin);"
        "c=d.get('cluster') or {};cl=d.get('client') or {};"
        "db=cl.get('database_status') or {};co=cl.get('coordinators') or {};"
        "ps=c.get('processes') or {};"
        f"assert db.get('available') is True;assert c.get('connection_string')=={expected_cluster!r};"
        f"assert {expected_endpoint!r} in [str((v or {{}}).get('address') or '') for v in ps.values()];"
        f"assert [{expected_endpoint!r}]==sorted([str((v or {{}}).get('address') or '') for v in (co.get('coordinators') or [])]);"
        "assert co.get('quorum_reachable') is True"
    )


def _key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    if not clean:
        raise ValueError("service key must be non-empty")
    return clean


def _posix(value: str) -> str:
    clean = str(value or "").replace("\\", "/").strip()
    if not clean.startswith("/") or "/../" in f"{clean}/":
        raise ValueError(f"invalid absolute POSIX path: {value!r}")
    return clean.rstrip("/") or "/"


def _q(value: str) -> str:
    return shlex.quote(str(value))


def _compose_string(value: str) -> str:
    return json.dumps(str(value).replace("$", "$$"))

ADD_SERVICE_PROOF_PREFIX = "FDB_ADD_SERVICE_PROOF_V1"
CLUSTER_STATE_PROOF_PREFIX = "FDB_CLUSTER_STATE_PROOF_V1"
COORDINATOR_TRANSITION_PROOF_PREFIX = "FDB_COORDINATOR_TRANSITION_PROOF_V1"
COORDINATOR_CONNECTION_PREFIX = "FDB_COORDINATOR_CONNECTION_V1"
COORDINATOR_GUARDIAN_PROOF_PREFIX = "FDB_COORDINATOR_GUARDIAN_PROOF_V1"


def cluster_state_proof_marker(plan: object) -> str:
    services = tuple(getattr(plan, "services"))
    coordinators = tuple(getattr(plan, "coordinators"))
    payload = json.dumps(
        {
            "cluster_file": str(getattr(plan, "cluster_file_contents")),
            "coordinators": [item.endpoint for item in coordinators],
            "services": [
                {"service_id": item.service_id, "endpoint": item.endpoint}
                for item in sorted(services, key=lambda value: value.service_id.encode("utf-8"))
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{CLUSTER_STATE_PROOF_PREFIX} {hashlib.sha256(payload).hexdigest()}"


def _source_coordinators(plan: object):
    value = getattr(plan, "source_coordinators", None)
    return tuple(value) if value is not None else tuple(getattr(plan, "coordinators"))


def _source_cluster_file_contents(plan: object) -> str:
    value = getattr(plan, "source_cluster_file_contents", None)
    return str(value) if value is not None else str(getattr(plan, "cluster_file_contents"))


def source_cluster_state_proof_marker(plan: object) -> str:
    services = tuple(getattr(plan, "services"))
    coordinators = _source_coordinators(plan)
    payload = json.dumps(
        {
            "cluster_file": _source_cluster_file_contents(plan),
            "coordinators": [item.endpoint for item in coordinators],
            "services": [
                {"service_id": item.service_id, "endpoint": item.endpoint}
                for item in sorted(services, key=lambda value: value.service_id.encode("utf-8"))
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{CLUSTER_STATE_PROOF_PREFIX} {hashlib.sha256(payload).hexdigest()}"


def add_service_proof_marker(plan: object, service: ServicePlacement) -> str:
    payload = json.dumps(
        {
            "cluster_state_proof": source_cluster_state_proof_marker(plan),
            "added_endpoint": service.endpoint,
            "added_service_id": service.service_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{ADD_SERVICE_PROOF_PREFIX} {hashlib.sha256(payload).hexdigest()}"


def render_add_service_descriptor(
    plan: object,
    service: ServicePlacement,
    *,
    image: str = DEFAULT_FDB_IMAGE,
    data_root: str = DEFAULT_DATA_ROOT,
) -> ServiceDescriptor:
    """Render one FDB service that joins an already accepted cluster.

    The descriptor never runs `configure new` and never changes coordinators.
    Its observer proves the new service joined through the frozen pre-mutation
    coordinator set.  A separate coordinator guardian performs and proves any
    topology-derived coordinator transition after participation is established.
    """

    services = tuple(getattr(plan, "services"))
    coordinators = _source_coordinators(plan)
    added = getattr(plan, "added_service")
    if added.service_id != service.service_id:
        raise ValueError("service is not the frozen add-service target")
    if not any(item.service_id == service.service_id for item in services):
        raise ValueError("added service is not present in the target service set")

    root = _posix(f"{data_root.rstrip('/')}/{getattr(plan, 'network')}/{service.service_id}")
    cluster_file = f"{root}/fdb.cluster"
    data_dir = f"{root}/data"
    log_dir = f"{root}/logs"
    service_key = _key(service.service_id)
    observer_key = birth_observer_subservice_name(service.service_id)
    service_name = _key(f"main-computer-{service.service_id}")
    cluster_contents = _source_cluster_file_contents(plan)

    server_script = "\n".join(
        [
            "set -eu",
            f"mkdir -p {_q(root)} {_q(data_dir)} {_q(log_dir)}",
            f"printf '%s\\n' {_q(cluster_contents)} > {_q(cluster_file)}",
            f"echo 'Joining {service.service_id} at {service.endpoint}'",
            "exec /usr/bin/fdbserver \\",
            f"  --cluster-file {_q(cluster_file)} \\",
            f"  --public-address {_q(service.endpoint)} \\",
            f"  --listen-address {_q(f'0.0.0.0:{service.port}')} \\",
            f"  --datadir {_q(data_dir)} \\",
            f"  --logdir {_q(log_dir)} \\",
            f"  --locality-machineid {_q(service.machine_id)} \\",
            f"  --locality-zoneid {_q(service.zone_id)} \\",
            "  --class storage \\",
            "  --knob_disable_posix_kernel_aio 1",
        ]
    )

    expected_endpoints = tuple(sorted((item.endpoint for item in services), key=lambda item: item.encode("utf-8")))
    expected_coordinators = tuple(sorted((item.endpoint for item in coordinators), key=lambda item: item.encode("utf-8")))
    health_python = _cluster_health_python(cluster_contents, expected_endpoints, expected_coordinators)
    add_marker = add_service_proof_marker(plan, service)
    cluster_marker = source_cluster_state_proof_marker(plan)
    observer_script = "\n".join(
        [
            "set -eu",
            f"mkdir -p {_q(root)}",
            f"printf '%s\\n' {_q(cluster_contents)} > {_q(cluster_file)}",
            "for attempt in $(seq 1 120); do",
            f"  if fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 >/tmp/fdb-status.json 2>/tmp/fdb-status.err; then",
            f"    if python3 -c {_q(health_python)} </tmp/fdb-status.json; then",
            f"      printf '%s\\n' {_q(add_marker)}",
            f"      printf '%s\\n' {_q(cluster_marker)}",
            "      break",
            "    fi",
            "  fi",
            "  if [ \"$attempt\" = 120 ]; then",
            "    cat /tmp/fdb-status.err >&2 || true",
            "    cat /tmp/fdb-status.json >&2 || true",
            "    exit 1",
            "  fi",
            "  sleep 2",
            "done",
            "tail -f /dev/null",
        ]
    )
    socket_health = f"python3 -c \"import socket; s=socket.create_connection(('127.0.0.1',{service.port}),timeout=3); s.close()\""
    observer_health = (
        f"fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 2>/dev/null "
        f"| python3 -c {_q(health_python)}"
    )

    lines = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {service_key}:",
        f"    image: {json.dumps(image)}",
        "    restart: unless-stopped",
        "    ports:",
        f"      - {json.dumps(f'{service.address}:{service.port}:{service.port}/tcp')}",
        "    volumes:",
        f"      - {json.dumps(f'{root}:{root}')}",
        "    entrypoint:",
        "      - /bin/sh",
        "      - -euc",
        f"      - {_compose_string(server_script)}",
        "    healthcheck:",
        f"      test: [\"CMD-SHELL\", {json.dumps(socket_health)}]",
        "      interval: 15s",
        "      timeout: 5s",
        "      start_period: 10s",
        "      retries: 8",
        "",
        f"  {observer_key}:",
        f"    image: {json.dumps(image)}",
        "    restart: unless-stopped",
        "    depends_on:",
        f"      {service_key}:",
        "        condition: service_healthy",
        "    volumes:",
        f"      - {json.dumps(f'{root}:{root}')}",
        "    entrypoint:",
        "      - /bin/sh",
        "      - -euc",
        f"      - {_compose_string(observer_script)}",
        "    healthcheck:",
        f"      test: [\"CMD-SHELL\", {json.dumps(observer_health)}]",
        "      interval: 15s",
        "      timeout: 12s",
        "      start_period: 15s",
        "      retries: 12",
        "",
    ]
    return ServiceDescriptor(
        service_name=service_name,
        description=f"Main Computer {getattr(plan, 'network')} FoundationDB service {service.service_id}",
        compose="\n".join(lines),
    )


def _cluster_health_python(
    cluster_contents: str,
    expected_endpoints: tuple[str, ...],
    expected_coordinators: tuple[str, ...],
) -> str:
    # One line so it remains safe to pass through python -c in Compose.
    return (
        "import json,sys;d=json.load(sys.stdin);"
        "c=d.get('cluster') or {};cl=d.get('client') or {};"
        "db=cl.get('database_status') or {};co=cl.get('coordinators') or {};"
        "ps=c.get('processes') or {};"
        "obs=sorted([str((v or {}).get('address') or '') for v in ps.values()]);"
        "coords=sorted([str((v or {}).get('address') or '') for v in (co.get('coordinators') or [])]);"
        f"assert db.get('available') is True;assert c.get('connection_string')=={cluster_contents!r};"
        f"assert all(x in obs for x in {expected_endpoints!r});"
        f"assert coords==list({expected_coordinators!r});"
        "assert co.get('quorum_reachable') is True"
    )



def coordinator_guardian_service_name(network: str) -> str:
    return _key(f"main-computer-{network}-fdb-coordinator-guardian")


def coordinator_guardian_subservice_name() -> str:
    return "coordinator-guardian"


def coordinator_transition_proof_marker(plan: object) -> str:
    payload = json.dumps(
        {
            "source_cluster_file": _source_cluster_file_contents(plan),
            "source_coordinators": [item.endpoint for item in _source_coordinators(plan)],
            "target_coordinators": [item.endpoint for item in tuple(getattr(plan, "coordinators"))],
            "target_services": [
                {"service_id": item.service_id, "endpoint": item.endpoint}
                for item in sorted(tuple(getattr(plan, "services")), key=lambda value: value.service_id.encode("utf-8"))
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{COORDINATOR_TRANSITION_PROOF_PREFIX} {hashlib.sha256(payload).hexdigest()}"


def coordinator_guardian_proof_marker(plan: object, cluster_contents: str) -> str:
    payload = json.dumps(
        {
            "cluster_file": str(cluster_contents),
            "coordinators": [item.endpoint for item in tuple(getattr(plan, "coordinators"))],
            "services": [
                {"service_id": item.service_id, "endpoint": item.endpoint}
                for item in sorted(tuple(getattr(plan, "services")), key=lambda value: value.service_id.encode("utf-8"))
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{COORDINATOR_GUARDIAN_PROOF_PREFIX} {hashlib.sha256(payload).hexdigest()}"


def render_coordinator_transition_descriptor(
    plan: object,
    *,
    service_name: str,
    image: str = DEFAULT_FDB_IMAGE,
) -> ServiceDescriptor:
    """Render a bounded helper that moves FDB coordinator authority first."""

    source_contents = _source_cluster_file_contents(plan)
    services = tuple(getattr(plan, "services"))
    coordinators = tuple(getattr(plan, "coordinators"))
    if not coordinators:
        raise ValueError("coordinator transition requires at least one target coordinator")
    target_endpoints = tuple(sorted((item.endpoint for item in coordinators), key=lambda item: item.encode("utf-8")))
    expected_endpoints = tuple(sorted((item.endpoint for item in services), key=lambda item: item.encode("utf-8")))
    cluster_file = "/tmp/main-computer-fdb-coordinator-transition.cluster"
    coordinator_command = "coordinators " + " ".join(target_endpoints)
    target_health_python = _topology_health_python(expected_endpoints, target_endpoints)
    marker = coordinator_transition_proof_marker(plan)
    key = coordinator_guardian_subservice_name()
    script = "\n".join(
        [
            "set -eu",
            f"printf '%s\\n' {_q(source_contents)} > {_q(cluster_file)}",
            f"fdbcli -C {_q(cluster_file)} --exec {_q(coordinator_command)} --timeout 60",
            "for attempt in $(seq 1 120); do",
            f"  if fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 >/tmp/fdb-coordinator-status.json 2>/tmp/fdb-coordinator-status.err; then",
            f"    if python3 -c {_q(target_health_python)} </tmp/fdb-coordinator-status.json; then",
            f"      printf '%s\\n' {_q(marker)}",
            f"      printf '%s %s\\n' {_q(COORDINATOR_CONNECTION_PREFIX)} \"$(cat {_q(cluster_file)})\"",
            "      break",
            "    fi",
            "  fi",
            "  if [ \"$attempt\" = 120 ]; then",
            "    cat /tmp/fdb-coordinator-status.err >&2 || true",
            "    cat /tmp/fdb-coordinator-status.json >&2 || true",
            "    exit 1",
            "  fi",
            "  sleep 2",
            "done",
            "tail -f /dev/null",
        ]
    )
    health = (
        f"fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 2>/dev/null "
        f"| python3 -c {_q(target_health_python)}"
    )
    lines = [
        f"name: {_key(service_name)}",
        "",
        "services:",
        f"  {key}:",
        f"    image: {json.dumps(image)}",
        "    restart: unless-stopped",
        "    entrypoint:",
        "      - /bin/sh",
        "      - -euc",
        f"      - {_compose_string(script)}",
        "    healthcheck:",
        f"      test: [\"CMD-SHELL\", {json.dumps(health)}]",
        "      interval: 15s",
        "      timeout: 12s",
        "      start_period: 15s",
        "      retries: 20",
        "",
    ]
    return ServiceDescriptor(
        service_name=_key(service_name),
        description=f"Main Computer {getattr(plan, 'network')} FoundationDB coordinator transition guardian",
        compose="\n".join(lines),
    )


def render_coordinator_guardian_descriptor(
    plan: object,
    cluster_contents: str,
    *,
    service_name: str,
    image: str = DEFAULT_FDB_IMAGE,
) -> ServiceDescriptor:
    """Render the persistent proof surface for accepted FDB coordinator topology."""

    services = tuple(getattr(plan, "services"))
    coordinators = tuple(getattr(plan, "coordinators"))
    expected_endpoints = tuple(sorted((item.endpoint for item in services), key=lambda item: item.encode("utf-8")))
    expected_coordinators = tuple(sorted((item.endpoint for item in coordinators), key=lambda item: item.encode("utf-8")))
    cluster_file = "/tmp/main-computer-fdb-coordinator-guardian.cluster"
    health_python = _cluster_health_python(str(cluster_contents), expected_endpoints, expected_coordinators)
    marker = coordinator_guardian_proof_marker(plan, str(cluster_contents))
    key = coordinator_guardian_subservice_name()
    script = "\n".join(
        [
            "set -eu",
            f"printf '%s\\n' {_q(str(cluster_contents))} > {_q(cluster_file)}",
            "for attempt in $(seq 1 120); do",
            f"  if fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 >/tmp/fdb-guardian-status.json 2>/tmp/fdb-guardian-status.err; then",
            f"    if python3 -c {_q(health_python)} </tmp/fdb-guardian-status.json; then",
            f"      printf '%s\\n' {_q(marker)}",
            "      break",
            "    fi",
            "  fi",
            "  if [ \"$attempt\" = 120 ]; then",
            "    cat /tmp/fdb-guardian-status.err >&2 || true",
            "    cat /tmp/fdb-guardian-status.json >&2 || true",
            "    exit 1",
            "  fi",
            "  sleep 2",
            "done",
            "tail -f /dev/null",
        ]
    )
    health = (
        f"fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 2>/dev/null "
        f"| python3 -c {_q(health_python)}"
    )
    lines = [
        f"name: {_key(service_name)}",
        "",
        "services:",
        f"  {key}:",
        f"    image: {json.dumps(image)}",
        "    restart: unless-stopped",
        "    entrypoint:",
        "      - /bin/sh",
        "      - -euc",
        f"      - {_compose_string(script)}",
        "    healthcheck:",
        f"      test: [\"CMD-SHELL\", {json.dumps(health)}]",
        "      interval: 15s",
        "      timeout: 12s",
        "      start_period: 15s",
        "      retries: 20",
        "",
    ]
    return ServiceDescriptor(
        service_name=_key(service_name),
        description=f"Main Computer {getattr(plan, 'network')} FoundationDB coordinator topology guardian",
        compose="\n".join(lines),
    )


def _topology_health_python(
    expected_endpoints: tuple[str, ...],
    expected_coordinators: tuple[str, ...],
) -> str:
    return (
        "import json,sys;d=json.load(sys.stdin);"
        "c=d.get('cluster') or {};cl=d.get('client') or {};"
        "db=cl.get('database_status') or {};co=cl.get('coordinators') or {};"
        "ps=c.get('processes') or {};"
        "obs=sorted([str((v or {}).get('address') or '') for v in ps.values()]);"
        "coords=sorted([str((v or {}).get('address') or '') for v in (co.get('coordinators') or [])]);"
        f"assert db.get('available') is True;assert all(x in obs for x in {expected_endpoints!r});"
        f"assert coords==list({expected_coordinators!r});"
        "assert co.get('quorum_reachable') is True;assert str(c.get('connection_string') or '')"
    )


def remove_helper_subservice_name() -> str:
    return "remove-helper"


def render_remove_service_helper_descriptor(
    plan: object,
    *,
    helper_service_name: str,
    image: str = DEFAULT_FDB_IMAGE,
) -> ServiceDescriptor:
    """Render the temporary management helper for one safe service removal.

    The helper performs an FDB `exclude <ip:port>` and waits for that command to
    return before emitting the drain proof.  FoundationDB documents that a
    successful blocking exclude has moved the database state away and made the
    process safe to remove.  After the control surface deletes the target
    Coolify service, the helper waits until status no longer reports the target
    process, proves the surviving topology and unchanged coordinators, and emits
    the terminal removal proof.
    """

    from .evacuation import removal_complete_proof_marker, removal_drain_proof_marker

    target = getattr(plan, "removed_service")
    services = tuple(getattr(plan, "services"))
    coordinators = tuple(getattr(plan, "coordinators"))
    cluster_contents = str(getattr(plan, "cluster_file_contents"))
    cluster_file = "/tmp/main-computer-fdb-remove.cluster"
    helper_key = remove_helper_subservice_name()

    expected_endpoints = tuple(sorted((item.endpoint for item in services), key=lambda item: item.encode("utf-8")))
    expected_coordinators = tuple(sorted((item.endpoint for item in coordinators), key=lambda item: item.encode("utf-8")))
    base_health_python = _cluster_health_python(cluster_contents, expected_endpoints, expected_coordinators)
    post_health_python = _remove_post_health_python(
        cluster_contents,
        expected_endpoints,
        expected_coordinators,
        target.endpoint,
    )
    drain_marker = removal_drain_proof_marker(plan)
    complete_marker = removal_complete_proof_marker(plan)
    exclude_command = f"exclude {target.endpoint}"
    include_command = f"include {target.endpoint}"
    exclude_python = (
        "import subprocess,sys;"
        f"cmd=['fdbcli','-C',{cluster_file!r},'--exec',{exclude_command!r}];"
        "p=subprocess.run(cmd,text=True,capture_output=True,timeout=300);"
        "sys.stdout.write(p.stdout or '');sys.stderr.write(p.stderr or '');"
        "raise SystemExit(p.returncode)"
    )
    include_python = (
        "import subprocess,sys;"
        f"cmd=['fdbcli','-C',{cluster_file!r},'--exec',{include_command!r}];"
        "p=subprocess.run(cmd,text=True,capture_output=True,timeout=60);"
        "sys.stdout.write(p.stdout or '');sys.stderr.write(p.stderr or '');"
        "raise SystemExit(p.returncode)"
    )

    helper_script = "\n".join(
        [
            "set -eu",
            f"printf '%s\\n' {_q(cluster_contents)} > {_q(cluster_file)}",
            f"python3 -c {_q(exclude_python)}",
            f"printf '%s\\n' {_q(drain_marker)}",
            "for attempt in $(seq 1 150); do",
            f"  if fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 >/tmp/fdb-remove-status.json 2>/tmp/fdb-remove-status.err; then",
            f"    if python3 -c {_q(post_health_python)} </tmp/fdb-remove-status.json; then",
            "      break",
            "    fi",
            "  fi",
            "  if [ \"$attempt\" = 150 ]; then",
            "    cat /tmp/fdb-remove-status.err >&2 || true",
            "    cat /tmp/fdb-remove-status.json >&2 || true",
            "    exit 1",
            "  fi",
            "  sleep 2",
            "done",
            f"python3 -c {_q(include_python)}",
            "for attempt in $(seq 1 30); do",
            f"  if fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 >/tmp/fdb-remove-final.json 2>/tmp/fdb-remove-final.err; then",
            f"    if python3 -c {_q(post_health_python)} </tmp/fdb-remove-final.json; then",
            f"      printf '%s\\n' {_q(complete_marker)}",
            "      break",
            "    fi",
            "  fi",
            "  if [ \"$attempt\" = 30 ]; then",
            "    cat /tmp/fdb-remove-final.err >&2 || true",
            "    cat /tmp/fdb-remove-final.json >&2 || true",
            "    exit 1",
            "  fi",
            "  sleep 2",
            "done",
            "tail -f /dev/null",
        ]
    )
    helper_health = (
        f"fdbcli -C {_q(cluster_file)} --exec 'status json' --timeout 10 2>/dev/null "
        f"| python3 -c {_q(base_health_python)}"
    )
    lines = [
        f"name: {_key(helper_service_name)}",
        "",
        "services:",
        f"  {helper_key}:",
        f"    image: {json.dumps(image)}",
        "    restart: unless-stopped",
        "    entrypoint:",
        "      - /bin/sh",
        "      - -euc",
        f"      - {_compose_string(helper_script)}",
        "    healthcheck:",
        f"      test: [\"CMD-SHELL\", {json.dumps(helper_health)}]",
        "      interval: 15s",
        "      timeout: 12s",
        "      start_period: 15s",
        "      retries: 20",
        "",
    ]
    return ServiceDescriptor(
        service_name=_key(helper_service_name),
        description=(
            f"Main Computer {getattr(plan, 'network')} FoundationDB removal helper for "
            f"{target.service_id}"
        ),
        compose="\n".join(lines),
    )


def _remove_post_health_python(
    cluster_contents: str,
    expected_endpoints: tuple[str, ...],
    expected_coordinators: tuple[str, ...],
    removed_endpoint: str,
) -> str:
    # A blocking `exclude` has already completed before this check runs.  The
    # terminal proof additionally requires the removed process address to have
    # disappeared from live status after its Coolify service is deleted.
    return (
        "import json,sys;d=json.load(sys.stdin);"
        "c=d.get('cluster') or {};cl=d.get('client') or {};"
        "db=cl.get('database_status') or {};co=cl.get('coordinators') or {};"
        "ps=c.get('processes') or {};"
        "obs=sorted([str((v or {}).get('address') or '') for v in ps.values()]);"
        "coords=sorted([str((v or {}).get('address') or '') for v in (co.get('coordinators') or [])]);"
        f"assert db.get('available') is True;assert c.get('connection_string')=={cluster_contents!r};"
        f"assert all(x in obs for x in {expected_endpoints!r});"
        f"assert {removed_endpoint!r} not in obs;"
        f"assert coords==list({expected_coordinators!r});"
        "assert co.get('quorum_reachable') is True"
    )
