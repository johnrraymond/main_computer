from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from main_computer.rag_smoke_subscripts_v1.contract import (
    StateProvision,
    StepContract,
    StepResult,
    step_cli,
    write_json,
)

CONTRACT = StepContract(
    step_id="repo_shape",
    version="rag_smoke_assembly_v1",
    description=(
        "Inventory the repository RAG smoke/test crop without modifying it. "
        "This proves the assembly starts from explicit repo evidence."
    ),
    provides=(
        StateProvision("repo_root", "Absolute repository root inspected by later boundaries."),
        StateProvision("rag_source_files", "Repo-relative main_computer RAG smoke/source files found."),
        StateProvision("rag_test_files", "Repo-relative pytest RAG test files found."),
        StateProvision("assembly_version", "Version label for this differentiated assembly path."),
    ),
    evidence_required=("inventory_path",),
)


def _rel(path: Path, repo_dir: Path) -> str:
    return path.relative_to(repo_dir).as_posix()


def _collect(repo_dir: Path) -> tuple[list[str], list[str]]:
    source_root = repo_dir / "main_computer"
    test_root = repo_dir / "tests"
    rag_source_files = sorted(
        _rel(path, repo_dir)
        for path in source_root.glob("rag*.py")
        if path.is_file()
    )
    rag_test_files = sorted(
        _rel(path, repo_dir)
        for path in test_root.glob("test_rag*.py")
        if path.is_file()
    )
    return rag_source_files, rag_test_files


def run(repo_dir: Path, output_dir: Path, state: dict[str, Any]) -> StepResult:
    del state
    if not (repo_dir / "main_computer").is_dir():
        raise ValueError(f"main_computer package not found under {repo_dir}")
    if not (repo_dir / "tests").is_dir():
        raise ValueError(f"tests directory not found under {repo_dir}")

    rag_source_files, rag_test_files = _collect(repo_dir)
    if not rag_source_files:
        raise ValueError("no main_computer/rag*.py source files found")
    if not rag_test_files:
        raise ValueError("no tests/test_rag*.py test files found")

    inventory = {
        "repo_root": str(repo_dir),
        "assembly_version": CONTRACT.version,
        "rag_source_count": len(rag_source_files),
        "rag_test_count": len(rag_test_files),
        "rag_source_files": rag_source_files,
        "rag_test_files": rag_test_files,
    }
    inventory_path = output_dir / "rag_repo_inventory.json"
    write_json(inventory_path, inventory)

    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "repo_root": str(repo_dir),
            "rag_source_files": rag_source_files,
            "rag_test_files": rag_test_files,
            "assembly_version": CONTRACT.version,
        },
        evidence={"inventory_path": str(inventory_path)},
        details={
            "rag_source_count": len(rag_source_files),
            "rag_test_count": len(rag_test_files),
            "sample_sources": rag_source_files[:8],
            "sample_tests": rag_test_files[:8],
        },
    )


def main(argv: list[str] | None = None) -> int:
    return step_cli(CONTRACT, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
