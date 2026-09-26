from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class HubContext:
    repo_root: Path
    hub_state_root: Path
    fdb_state_root: Path
    chain_state_root: Path
    mother_private_path: Path
    mother_metadata_path: Path

    @classmethod
    def from_repo(cls, repo_root: Path) -> "HubContext":
        root = Path(repo_root).resolve()
        return cls(
            repo_root=root,
            hub_state_root=root / "runtime" / "state" / "hub",
            fdb_state_root=root / "runtime" / "state" / "fdb",
            chain_state_root=root / "runtime" / "state" / "chain",
            mother_private_path=root / "runtime" / "state" / "mother" / "identity.private.yaml",
            mother_metadata_path=root / "runtime" / "state" / "mother" / "identity.private.meta.json",
        )


@dataclass(frozen=True, slots=True)
class HubPlacement:
    hub_id: str
    controller_id: str
    host_id: str
    public_url: str
    runtime_dir: str
    cluster_file_path: str
    topology_path: str
    application_name: str


@dataclass(frozen=True, slots=True)
class DependencyContract:
    kind: str
    network: str
    generation: int
    sha256: str
    payload: dict[str, Any]

    def reference(self) -> dict[str, Any]:
        return {"generation": self.generation, "sha256": self.sha256}
