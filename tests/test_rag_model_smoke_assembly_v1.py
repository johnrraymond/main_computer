from __future__ import annotations

import json
from pathlib import Path

from main_computer.rag_model_smoke_assembly_v1 import (
    ASSEMBLY_VERSION,
    SCRIPT_MODULES,
    list_contracts,
    run_assembly,
)


def test_rag_model_smoke_assembly_v1_wraps_existing_ai_smoke_with_fake_provider(tmp_path: Path) -> None:
    result = run_assembly(
        repo_dir=Path.cwd(),
        output_dir=tmp_path / "rag_model_smoke_assembly_v1",
        provider_mode="fake",
    )

    assert result.ok, [step.as_dict() for step in result.steps if step.status != "ok"]
    assert result.version == ASSEMBLY_VERSION
    assert [step.step_id for step in result.steps] == [
        "target_inventory",
        "provider_gate",
        "csv_audit_model_run",
        "report_contract",
    ]

    trace_path = Path(result.trace_path)
    assert trace_path.exists()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["schema_version"] == 1
    assert trace["ok"] is True
    assert trace["provider_mode"] == "fake"

    final_state = trace["final_state"]
    assert final_state["target_smoke_module"] == "main_computer.rag_model_smoke"
    assert final_state["target_smoke_file"] == "main_computer/rag_model_smoke.py"
    assert final_state["target_test_file"] == "tests/test_rag_model_smoke.py"
    assert final_state["target_scenario"] == "csv-audit"
    assert final_state["provider_mode"] == "fake"
    assert final_state["provider_ready"] is True
    assert final_state["provider_is_real_ai"] is False
    assert final_state["model_smoke_ok"] is True
    assert final_state["model_smoke_used_model"] is True
    assert final_state["model_smoke_retrieved_path_count"] > 0
    assert final_state["ai_smoke_contract_proven"] is True
    assert final_state["ai_smoke_real_provider_run"] is False
    assert final_state["model_call_count"] >= 2

    for step in trace["steps"]:
        contract = step["contract"]
        result_payload = step["result"]
        assert step["status"] == "ok"
        assert result_payload["status"] == "ok"
        assert result_payload["step_id"] == contract["step_id"]
        assert set(result_payload["provided_state"]) == {item["key"] for item in contract["provides"]}
        for evidence_key in contract["evidence_required"]:
            assert result_payload["evidence"][evidence_key]


def test_rag_model_smoke_assembly_v1_lists_versioned_contracts() -> None:
    contracts = list_contracts(Path.cwd())

    assert len(contracts) == len(SCRIPT_MODULES)
    assert {contract["version"] for contract in contracts} == {ASSEMBLY_VERSION}
    assert [contract["step_id"] for contract in contracts] == [
        "target_inventory",
        "provider_gate",
        "csv_audit_model_run",
        "report_contract",
    ]
    assert contracts[0]["requires"] == []
    assert contracts[-1]["requires"]
    assert all(contract["description"] for contract in contracts)
    assert all(contract["provides"] for contract in contracts)
