from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


def _text(value: str, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field} must be non-empty")
    return clean


def _port(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("port must be an integer in 1..65535")
    return value


def _sorted_unique(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    clean = tuple(sorted((_text(v, field) for v in values), key=lambda item: item.encode("utf-8")))
    if len(set(clean)) != len(clean):
        raise ValueError(f"{field} must not contain duplicates")
    return clean


@dataclass(frozen=True, slots=True)
class FdbContext:
    repo_root: Path
    state_root: Path
    private_state_path: Path

    @classmethod
    def from_repo(cls, repo_root: Path) -> "FdbContext":
        root = Path(repo_root).resolve()
        return cls(
            repo_root=root,
            state_root=root / "runtime" / "state" / "fdb",
            private_state_path=root / "runtime" / "state" / "mother" / "identity.private.yaml",
        )


@dataclass(frozen=True, slots=True)
class ContentHash:
    algorithm: str
    digest: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise ValueError("ContentHash.algorithm must be sha256")
        if len(self.digest) != 64 or any(c not in "0123456789abcdef" for c in self.digest):
            raise ValueError("ContentHash.digest must be 64 lowercase hexadecimal characters")


@dataclass(frozen=True, slots=True)
class ClusterIdentity:
    description: str
    cluster_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "description", _text(self.description, "description"))
        object.__setattr__(self, "cluster_id", _text(self.cluster_id, "cluster_id"))


@dataclass(frozen=True, slots=True)
class ServicePlacement:
    service_id: str
    host_id: str
    address: str
    port: int
    machine_id: str
    zone_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "service_id", _text(self.service_id, "service_id"))
        object.__setattr__(self, "host_id", _text(self.host_id, "host_id"))
        object.__setattr__(self, "address", _text(self.address, "address"))
        object.__setattr__(self, "port", _port(self.port))
        object.__setattr__(self, "machine_id", _text(self.machine_id, "machine_id"))
        object.__setattr__(self, "zone_id", _text(self.zone_id, "zone_id"))

    @property
    def endpoint(self) -> str:
        return f"{self.address}:{self.port}"


@dataclass(frozen=True, slots=True)
class CoordinatorEndpoint:
    service_id: str
    host_id: str
    address: str
    port: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "service_id", _text(self.service_id, "service_id"))
        object.__setattr__(self, "host_id", _text(self.host_id, "host_id"))
        object.__setattr__(self, "address", _text(self.address, "address"))
        object.__setattr__(self, "port", _port(self.port))

    @property
    def endpoint(self) -> str:
        return f"{self.address}:{self.port}"


@dataclass(frozen=True, slots=True)
class AcceptedClusterState:
    network: str
    generation: int
    cluster: ClusterIdentity
    services: tuple[ServicePlacement, ...]
    coordinators: tuple[CoordinatorEndpoint, ...]
    redundancy_mode: str
    storage_engine: str
    retired: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "network", _text(self.network, "network"))
        if isinstance(self.generation, bool) or self.generation < 1:
            raise ValueError("generation must be a positive integer")
        object.__setattr__(self, "redundancy_mode", _text(self.redundancy_mode, "redundancy_mode"))
        object.__setattr__(self, "storage_engine", _text(self.storage_engine, "storage_engine"))


@dataclass(frozen=True, slots=True)
class ServiceObservation:
    service_id: str
    host_id: str
    endpoint: str
    deployment_present: bool
    runtime_running: bool | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "service_id", _text(self.service_id, "service_id"))
        object.__setattr__(self, "host_id", _text(self.host_id, "host_id"))
        object.__setattr__(self, "endpoint", _text(self.endpoint, "endpoint"))


@dataclass(frozen=True, slots=True)
class DeploymentObservation:
    services: tuple[ServiceObservation, ...]


@dataclass(frozen=True, slots=True)
class FdbStatusObservation:
    database_available: bool | None
    database_healthy: bool | None
    connection_string: str | None
    process_addresses: tuple[str, ...]
    coordinator_addresses: tuple[str, ...]
    coordinator_quorum_reachable: bool | None
    redundancy_mode: str | None
    storage_engine: str | None
    recovery_state: str | None
    full_replication: bool | None
    max_zone_failures_without_losing_availability: int | None
    raw: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "process_addresses", _sorted_unique(self.process_addresses, "process_addresses"))
        object.__setattr__(self, "coordinator_addresses", _sorted_unique(self.coordinator_addresses, "coordinator_addresses"))


@dataclass(frozen=True, slots=True)
class DriftReport:
    missing_accepted_services: tuple[str, ...]
    unexpected_deployed_services: tuple[str, ...]
    accepted_services_not_participating: tuple[str, ...]
    unknowns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InspectionRequest:
    network: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "network", _text(self.network, "network"))


@dataclass(frozen=True, slots=True)
class InspectionResult:
    network: str
    accepted: AcceptedClusterState | None
    deployment: DeploymentObservation | None
    fdb: FdbStatusObservation | None
    drift: DriftReport
    transaction_probe: str
    blocked_capabilities: tuple[str, ...]
    allowed_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CreateClusterRequest:
    network: str
    cluster: ClusterIdentity
    services: tuple[ServicePlacement, ...]
    coordinator_service_ids: tuple[str, ...]
    redundancy_mode: str
    storage_engine: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "network", _text(self.network, "network"))
        if not self.services:
            raise ValueError("services must be non-empty")
        object.__setattr__(self, "coordinator_service_ids", _sorted_unique(self.coordinator_service_ids, "coordinator_service_ids"))
        if not self.coordinator_service_ids:
            raise ValueError("coordinator_service_ids must be non-empty")
        object.__setattr__(self, "redundancy_mode", _text(self.redundancy_mode, "redundancy_mode"))
        object.__setattr__(self, "storage_engine", _text(self.storage_engine, "storage_engine"))




@dataclass(frozen=True, slots=True)
class RemoveServiceRequest:
    network: str
    service_id: str
    allow_full_deletion: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "network", _text(self.network, "network"))
        object.__setattr__(self, "service_id", _text(self.service_id, "service_id"))
        if not isinstance(self.allow_full_deletion, bool):
            raise ValueError("allow_full_deletion must be boolean")


@dataclass(frozen=True, slots=True)
class RemoveServicePlan:
    network: str
    cluster: ClusterIdentity
    services: tuple[ServicePlacement, ...]
    coordinators: tuple[CoordinatorEndpoint, ...]
    source_coordinators: tuple[CoordinatorEndpoint, ...]
    redundancy_mode: str
    storage_engine: str
    cluster_file_contents: str
    source_cluster_file_contents: str
    removed_service: ServicePlacement


@dataclass(frozen=True, slots=True)
class RemoveServiceDeployment:
    project_uuid: str = ""
    project_name: str = ""
    environment_name: str = ""
    environment_uuid: str = ""
    server_uuid: str = ""
    server_name: str = ""
    destination_uuid: str = ""
    image: str = "foundationdb/foundationdb:7.4.6"
    force_deploy: bool = False


@dataclass(frozen=True, slots=True)
class AddServiceRequest:
    network: str
    service: ServicePlacement

    def __post_init__(self) -> None:
        object.__setattr__(self, "network", _text(self.network, "network"))


@dataclass(frozen=True, slots=True)
class AddServicePlan:
    network: str
    cluster: ClusterIdentity
    services: tuple[ServicePlacement, ...]
    coordinators: tuple[CoordinatorEndpoint, ...]
    source_coordinators: tuple[CoordinatorEndpoint, ...]
    redundancy_mode: str
    storage_engine: str
    cluster_file_contents: str
    source_cluster_file_contents: str
    added_service: ServicePlacement


@dataclass(frozen=True, slots=True)
class AddServiceDeployment:
    project_uuid: str = ""
    project_name: str = ""
    environment_name: str = ""
    environment_uuid: str = ""
    server_uuid: str = ""
    server_name: str = ""
    destination_uuid: str = ""
    image: str = "foundationdb/foundationdb:7.4.6"
    data_root: str = "/data/main-computer/fdb"
    force_deploy: bool = False


@dataclass(frozen=True, slots=True)
class BirthPlan:
    network: str
    cluster: ClusterIdentity
    services: tuple[ServicePlacement, ...]
    coordinators: tuple[CoordinatorEndpoint, ...]
    redundancy_mode: str
    storage_engine: str
    cluster_file_contents: str


@dataclass(frozen=True, slots=True)
class BirthObservationCheck:
    cluster_identity_matches: bool | None
    all_services_observed: bool | None
    coordinator_set_matches: bool | None
    database_available: bool | None
    missing_service_endpoints: tuple[str, ...]
    mismatches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationCommandResult:
    operation: str
    stage: str
    status: str
    details: Mapping[str, Any]

@dataclass(frozen=True, slots=True)
class CreateClusterDeployment:
    project_uuid: str = ""
    project_name: str = ""
    environment_name: str = ""
    environment_uuid: str = ""
    server_uuid: str = ""
    server_name: str = ""
    destination_uuid: str = ""
    image: str = "foundationdb/foundationdb:7.4.6"
    data_root: str = "/data/main-computer/fdb"
    force_deploy: bool = False
