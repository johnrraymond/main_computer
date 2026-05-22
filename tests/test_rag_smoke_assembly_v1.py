from __future__ import annotations

import json
from pathlib import Path

from main_computer.rag_smoke_assembly_v1 import (
    ASSEMBLY_VERSION,
    SCRIPT_MODULES,
    list_contracts,
    run_assembly,
)


def test_rag_smoke_assembly_v1_runs_subscripts_with_explicit_contracts(tmp_path: Path) -> None:
    result = run_assembly(repo_dir=Path.cwd(), output_dir=tmp_path / "assembly")

    assert result.ok, [step.as_dict() for step in result.steps if step.status != "ok"]
    assert result.version == ASSEMBLY_VERSION
    assert [step.step_id for step in result.steps] == [
        "repo_shape",
        "deterministic_retriever",
        "framework_goldset",
        "harness_no_model",
    ]

    trace_path = Path(result.trace_path)
    assert trace_path.exists()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["schema_version"] == 1
    assert trace["ok"] is True
    assert trace["version"] == ASSEMBLY_VERSION

    final_state = trace["final_state"]
    assert final_state["assembly_version"] == ASSEMBLY_VERSION
    assert final_state["deterministic_retriever_proven"] is True
    assert final_state["framework_goldset_proven"] is True
    assert final_state["no_model_harness_proven"] is True
    assert any(path.startswith("main_computer/rag") for path in final_state["rag_source_files"])
    assert any(path.startswith("tests/test_rag") for path in final_state["rag_test_files"])

    for step in trace["steps"]:
        contract = step["contract"]
        result_payload = step["result"]
        provided = result_payload["provided_state"]
        evidence = result_payload["evidence"]
        assert step["status"] == "ok"
        assert result_payload["status"] == "ok"
        assert result_payload["step_id"] == contract["step_id"]
        assert set(provided) == {item["key"] for item in contract["provides"]}
        for evidence_key in contract["evidence_required"]:
            assert evidence[evidence_key]


def test_rag_smoke_assembly_v1_lists_versioned_subscript_contracts() -> None:
    contracts = list_contracts(Path.cwd())

    assert len(contracts) == len(SCRIPT_MODULES)
    assert {contract["version"] for contract in contracts} == {ASSEMBLY_VERSION}
    assert contracts[0]["step_id"] == "repo_shape"
    assert contracts[0]["requires"] == []
    assert contracts[-1]["requires"]
    assert all(contract["description"] for contract in contracts)
    assert all(contract["provides"] for contract in contracts)
