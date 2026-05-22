from __future__ import annotations

from pathlib import Path
from typing import Any

from main_computer.rag_retriever import DeterministicRagRetriever, RagRetrieverConfig
from main_computer.rag_smoke_subscripts_v1.contract import (
    StateProvision,
    StateRequirement,
    StepContract,
    StepResult,
    require_state,
    step_cli,
    write_json,
)

CONTRACT = StepContract(
    step_id="deterministic_retriever",
    version="rag_smoke_assembly_v1",
    description=(
        "Run a deterministic retriever fixture in an isolated repo and record the selected evidence. "
        "This proves retrieval can be smoke-tested without a model or hidden parent state."
    ),
    requires=(
        StateRequirement("repo_root", "Repository root inventoried by repo_shape."),
        StateRequirement("rag_source_files", "Existing RAG source crop found by repo_shape."),
    ),
    provides=(
        StateProvision("deterministic_retriever_proven", "True when the fixture retrieves expected source evidence."),
        StateProvision("retriever_trace_path", "JSON trace artifact for the deterministic retrieval boundary."),
    ),
    evidence_required=("retriever_trace_path",),
)


def _write_fixture(fixture_repo: Path) -> None:
    (fixture_repo / "main_computer").mkdir(parents=True, exist_ok=True)
    (fixture_repo / "tests").mkdir(parents=True, exist_ok=True)
    (fixture_repo / "README.md").write_text(
        "# Fixture RAG Repo\nThis fixture proves deterministic retrieval can locate contract assembly evidence.\n",
        encoding="utf-8",
    )
    (fixture_repo / "main_computer" / "rag_contract_fixture.py").write_text(
        "BOUNDARY_TOKEN = 'RAG_ASSEMBLY_CONTRACT_SENTINEL'\n"
        "def explain_boundary_contract():\n"
        "    return 'subscripts declare required state and produced state'\n",
        encoding="utf-8",
    )
    (fixture_repo / "tests" / "test_rag_contract_fixture.py").write_text(
        "def test_contract_fixture():\n"
        "    assert 'required state'\n",
        encoding="utf-8",
    )


def run(repo_dir: Path, output_dir: Path, state: dict[str, Any]) -> StepResult:
    require_state(state, "repo_root", "rag_source_files")
    if Path(state["repo_root"]).resolve() != repo_dir:
        raise ValueError("input repo_root does not match --repo-dir")
    if not state["rag_source_files"]:
        raise ValueError("repo_shape did not provide any RAG source files")

    fixture_repo = output_dir / "fixture_repo"
    _write_fixture(fixture_repo)

    retriever = DeterministicRagRetriever(
        RagRetrieverConfig(
            repo_dir=fixture_repo,
            max_context_chars=4000,
            max_candidates=8,
            max_chunks=4,
        )
    )
    result = retriever.retrieve(
        [
            "RAG_ASSEMBLY_CONTRACT_SENTINEL",
            "subscripts required produced state",
            "contract assembly evidence",
        ]
    )
    payload = result.as_dict()
    top_paths = [candidate["path"] for candidate in payload["candidates"]]
    chunk_paths = [chunk["path"] for chunk in payload["chunks"]]
    expected = "main_computer/rag_contract_fixture.py"
    if expected not in top_paths and expected not in chunk_paths:
        raise ValueError(f"expected fixture source was not retrieved; got candidates={top_paths!r}")

    trace = {
        "schema_version": 1,
        "step_id": CONTRACT.step_id,
        "query_contract": {
            "expected_path": expected,
            "must_scan_files": True,
            "must_emit_chunks": True,
        },
        "retrieval": payload,
    }
    trace_path = output_dir / "deterministic_retriever_trace.json"
    write_json(trace_path, trace)

    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "deterministic_retriever_proven": True,
            "retriever_trace_path": str(trace_path),
        },
        evidence={"retriever_trace_path": str(trace_path)},
        details={
            "scanned_files": payload["scanned_files"],
            "candidate_count": len(payload["candidates"]),
            "chunk_count": len(payload["chunks"]),
            "top_paths": top_paths[:5],
        },
    )


def main(argv: list[str] | None = None) -> int:
    return step_cli(CONTRACT, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
