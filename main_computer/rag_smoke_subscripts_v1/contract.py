from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class StateRequirement:
    key: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class StateProvision:
    key: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class StepContract:
    step_id: str
    version: str
    description: str
    requires: tuple[StateRequirement, ...] = ()
    provides: tuple[StateProvision, ...] = ()
    evidence_required: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "version": self.version,
            "description": self.description,
            "requires": [item.as_dict() for item in self.requires],
            "provides": [item.as_dict() for item in self.provides],
            "evidence_required": list(self.evidence_required),
        }


@dataclass(frozen=True)
class StepResult:
    step_id: str
    status: str
    provided_state: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "provided_state": self.provided_state,
            "evidence": self.evidence,
            "details": self.details,
        }


def read_state(path: Path | str | None) -> dict[str, Any]:
    if path is None:
        return {}
    state_path = Path(path)
    if not state_path.exists():
        return {}
    data = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"state file must contain a JSON object: {state_path}")
    return data


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require_state(state: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if key not in state]
    if missing:
        raise ValueError(f"missing required state: {missing}")


def json_stdout(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def step_cli(
    contract: StepContract,
    runner: Callable[[Path, Path, dict[str, Any]], StepResult],
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=contract.description)
    parser.add_argument("--contract", action="store_true", help="Print this subscript contract and exit.")
    parser.add_argument("--repo-dir", default=".", help="Repository root for this smoke boundary.")
    parser.add_argument("--state", default=None, help="Input JSON state emitted by prior boundaries.")
    parser.add_argument("--output-dir", default=None, help="Directory where this boundary writes evidence.")
    args = parser.parse_args(argv)

    if args.contract:
        json_stdout(contract.as_dict())
        return 0

    repo_dir = Path(args.repo_dir).resolve()
    output_dir = Path(args.output_dir or (repo_dir / "diagnostics_output" / contract.version / contract.step_id)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = runner(repo_dir, output_dir, read_state(args.state))
    except Exception as exc:  # pragma: no cover - converted to data for the assembly trace
        result = StepResult(
            step_id=contract.step_id,
            status="fail",
            provided_state={},
            evidence={},
            details={"error": f"{type(exc).__name__}: {exc}"},
        )
    json_stdout(result.as_dict())
    return 0 if result.ok else 1
