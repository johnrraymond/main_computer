from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from main_computer import mcel_acceptance_runner as runner
from main_computer import mcel_truth_audit as audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "main_computer" / "mcel_acceptance_runner.py"
CATALOG = ROOT / "main_computer" / "mcel_acceptance_bindings.json"
DOC = ROOT / "pretty_docs" / "mcel-acceptance-evidence.md"
AUDIT_DOC = ROOT / "pretty_docs" / "mcel-repository-truth-audit.md"


@dataclass
class FakeBlock:
    block_id: str
    app: str
    status: str
    fields: dict[str, Any]
    source_file: str = "pretty_docs/demo.md"
    start_line: int = 1
    end_line: int = 10
    block_type: str = "mcel-acceptance"


def binding(contract_id: str = "demo.acceptance.one") -> runner.Binding:
    return runner.Binding(
        binding_id="demo.binding.one",
        app_id="demo",
        contract_id=contract_id,
        runner="pytest",
        selectors=("tests/test_demo.py",),
        notes="",
    )


def test_current_catalog_references_real_requirements_contracts() -> None:
    registry = runner.load_requirements_registry(ROOT)
    contracts = runner.acceptance_contracts(registry)
    bindings, metadata = runner.load_bindings(runner.DEFAULT_BINDINGS, ROOT)

    runner.validate_bindings(contracts, bindings)

    by_id = {block.block_id: block for block in contracts}
    assert metadata["schema"] == runner.BINDING_SCHEMA
    assert metadata["bindingCount"] == 16
    assert set(bindings) <= set(by_id)
    assert all(
        str(by_id[contract_id].status).lower() in runner.ENFORCEABLE_STATUSES
        for contract_id in bindings
    )
    assert "calculator.acceptance.no-hidden-mutation" in bindings
    assert bindings["calculator.acceptance.no-hidden-mutation"].selectors == (
        "tests/test_mcel_calculator_acceptance.py",
    )
    assert "code-editor.acceptance.full-semantic-runtime" in bindings
    assert bindings["code-editor.acceptance.full-semantic-runtime"].selectors == (
        "tests/test_mcel_code_editor_semantic_adapter.py",
    )
    for contract_id in (
        "website-builder.acceptance.website-project-model",
        "website-builder.acceptance.save-preview-publish-separated",
        "website-builder.acceptance.publish-lanes-separated",
    ):
        selectors = bindings[contract_id].selectors
        assert selectors
        assert all("::test_" in selector for selector in selectors)
        assert "tests/test_website_builder_app.py" not in selectors
    assert "website-builder.acceptance.semantic-runtime" in bindings
    assert bindings["website-builder.acceptance.semantic-runtime"].selectors == (
        "tests/test_mcel_website_builder_semantic_adapter.py",
    )
    assert "mcel-lab.acceptance.semantic-runtime" in bindings
    assert bindings["mcel-lab.acceptance.semantic-runtime"].selectors == (
        "tests/test_mcel_lab_semantic_adapter.py",
    )


def test_planned_contract_is_visible_but_not_due() -> None:
    block = FakeBlock(
        block_id="demo.acceptance.future",
        app="demo",
        status="planned",
        fields={"requires": ["future behavior"]},
    )
    result = runner.contract_result(
        block=block,
        binding=None,
        repo=ROOT,
        extra_pytest_args=(),
        timeout_seconds=10,
        execution_cache={},
    )
    app = runner.app_result("demo", [result])

    assert result["status"] == "not-due"
    assert result["executed"] is False
    assert app["status"] == "pass"
    assert app["enforceableContractCount"] == 0
    assert app["notDueContractCount"] == 1


def test_enforceable_contract_without_binding_is_not_proven() -> None:
    block = FakeBlock(
        block_id="demo.acceptance.required",
        app="demo",
        status="specified",
        fields={"requires": ["current behavior"]},
    )
    result = runner.contract_result(
        block=block,
        binding=None,
        repo=ROOT,
        extra_pytest_args=(),
        timeout_seconds=10,
        execution_cache={},
    )
    app = runner.app_result("demo", [result])

    assert result["status"] == "missing-binding"
    assert result["passed"] is False
    assert app["status"] == "fail"
    assert app["missingBindingContractIds"] == ["demo.acceptance.required"]


def test_pytest_binding_passes_only_after_collecting_a_passing_test(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "test_demo.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    bound = runner.Binding(
        binding_id="demo.binding",
        app_id="demo",
        contract_id="demo.acceptance",
        runner="pytest",
        selectors=("tests/test_demo.py",),
        notes="",
    )

    result = runner.run_pytest_binding(
        binding=bound,
        repo=tmp_path,
        timeout_seconds=30,
    )

    assert result["status"] == "pass"
    assert result["passed"] is True
    assert result["testCount"] == 1
    assert result["summary"]["passed"] == 1


def test_pytest_environment_prepends_resolved_node(tmp_path: Path) -> None:
    bin_dir = tmp_path / "node-bin"
    bin_dir.mkdir()
    node_name = "node.exe" if os.name == "nt" else "node"
    fake_node = bin_dir / node_name
    fake_node.write_text("", encoding="utf-8")
    if os.name != "nt":
        fake_node.chmod(0o755)

    env, resolved = runner.build_pytest_environment(
        base_env={"PATH": ""},
        node_executable=str(fake_node),
    )

    assert resolved == str(fake_node.resolve())
    assert env["MCEL_NODE_EXECUTABLE"] == str(fake_node.resolve())
    assert env["PATH"].split(os.pathsep)[0] == str(bin_dir.resolve())


def test_pytest_binding_does_not_treat_zero_tests_as_pass(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "test_empty.py"
    test_file.parent.mkdir()
    test_file.write_text("VALUE = 1\n", encoding="utf-8")
    bound = runner.Binding(
        binding_id="demo.binding",
        app_id="demo",
        contract_id="demo.acceptance",
        runner="pytest",
        selectors=("tests/test_empty.py",),
        notes="",
    )

    result = runner.run_pytest_binding(
        binding=bound,
        repo=tmp_path,
        timeout_seconds=30,
    )

    assert result["status"] == "no-tests"
    assert result["passed"] is False
    assert result["testCount"] == 0


def test_binding_catalog_rejects_path_traversal(tmp_path: Path) -> None:
    catalog = tmp_path / "bindings.json"
    catalog.write_text(
        json.dumps(
            {
                "schema": runner.BINDING_SCHEMA,
                "bindings": [
                    {
                        "id": "bad",
                        "appId": "demo",
                        "acceptanceContractId": "demo.acceptance",
                        "runner": "pytest",
                        "selectors": ["../outside.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(runner.McelAcceptanceError, match="Unsafe"):
        runner.load_bindings(catalog, tmp_path)


def test_report_schema_is_accepted_by_truth_audit_discovery(tmp_path: Path) -> None:
    report_dir = tmp_path / "runtime" / "reports" / "mcel-acceptance"
    report_dir.mkdir(parents=True)
    valid = report_dir / "mcel-acceptance-report.json"
    valid.write_text(
        json.dumps(
            {
                "schema": runner.REPORT_SCHEMA,
                "generatedAt": "2026-07-27T12:00:00Z",
                "results": [],
            }
        ),
        encoding="utf-8",
    )
    misleading = report_dir / "other-acceptance-ish.json"
    misleading.write_text(
        json.dumps(
            {
                "schema": "not-an-mcel-acceptance-report",
                "generatedAt": "2027-07-27T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    discovered = audit.discover_latest_evidence_path(
        repo=tmp_path,
        search_root=Path("runtime/reports/mcel-acceptance"),
        label="acceptance",
    )

    assert discovered == valid


def test_cli_lists_contracts_without_running_pytest() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--list-contracts"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "file-explorer.acceptance.read-only-browse-preview" in completed.stdout
    assert "calculator.acceptance.no-hidden-mutation" in completed.stdout
    assert "unbound" in completed.stdout
    assert "contract-counter.acceptance.operation-control" in completed.stdout


def test_documentation_defines_release_sequence_and_no_overclaim_rules() -> None:
    source = DOC.read_text(encoding="utf-8")
    audit_source = AUDIT_DOC.read_text(encoding="utf-8")

    assert "mcel-acceptance-evidence-report-v1" in source
    assert "mcel-acceptance-bindings-v1" in source
    assert "A passing test file cannot prove an acceptance contract" in source
    assert "missing-binding" in source
    assert "not-due" in source
    assert "python main_computer/mcel_acceptance_runner.py" in audit_source
    assert "python main_computer/mcel_truth_audit.py --release-gate" in audit_source



def test_app_scoped_output_defaults_away_from_canonical_report(tmp_path: Path) -> None:
    scope = runner.build_evidence_scope(
        selected_apps=["mcel-lab"],
        covered_apps=["mcel-lab"],
        all_apps=["calculator", "mcel-lab"],
    )

    output_dir = runner.resolve_output_dir(
        requested_output_dir=None,
        evidence_scope=scope,
        overwrite_canonical=False,
    )
    assert output_dir == runner.DEFAULT_OUTPUT_DIR / "apps" / "mcel-lab"

    canonical = tmp_path / runner.DEFAULT_OUTPUT_DIR / "mcel-acceptance-report.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_text('{"sentinel": "canonical"}\n', encoding="utf-8")
    runner.write_report(
        {
            "schema": runner.REPORT_SCHEMA,
            "evidenceScope": scope,
            "repositoryProvenance": {},
            "summary": {},
            "results": [],
        },
        output_dir,
        tmp_path,
    )

    assert json.loads(canonical.read_text(encoding="utf-8")) == {
        "sentinel": "canonical"
    }
    assert (
        tmp_path
        / runner.DEFAULT_OUTPUT_DIR
        / "apps"
        / "mcel-lab"
        / "mcel-acceptance-report.json"
    ).exists()


def test_app_scoped_output_requires_explicit_canonical_overwrite() -> None:
    scope = runner.build_evidence_scope(
        selected_apps=["mcel-lab"],
        covered_apps=["mcel-lab"],
        all_apps=["calculator", "mcel-lab"],
    )

    with pytest.raises(runner.McelAcceptanceError, match="--overwrite-canonical"):
        runner.resolve_output_dir(
            requested_output_dir=runner.DEFAULT_OUTPUT_DIR,
            evidence_scope=scope,
            overwrite_canonical=False,
        )
    with pytest.raises(runner.McelAcceptanceError, match="--overwrite-canonical"):
        runner.resolve_output_dir(
            requested_output_dir=(ROOT / runner.DEFAULT_OUTPUT_DIR).resolve(),
            evidence_scope=scope,
            overwrite_canonical=False,
            repo=ROOT,
        )

    assert runner.resolve_output_dir(
        requested_output_dir=None,
        evidence_scope=scope,
        overwrite_canonical=True,
    ) == runner.DEFAULT_OUTPUT_DIR


def test_package_local_acceptance_is_discovered_with_package_provenance() -> None:
    contracts, bindings, metadata = runner.load_package_acceptance(ROOT)

    assert [block.block_id for block in contracts] == [
        "contract-counter.acceptance.operation-control"
    ]
    bound = bindings["contract-counter.acceptance.operation-control"]
    assert bound.source_kind == "package"
    assert bound.source_path == "mcel_apps/contract-counter/tests/mcel_acceptance_bindings.json"
    assert bound.declared_selectors == (
        "tests/test_acceptance.py::test_package_acceptance_operation_control",
    )
    assert bound.selectors == (
        "mcel_apps/contract-counter/tests/test_acceptance.py::test_package_acceptance_operation_control",
    )
    assert bound.package_fingerprint.startswith("sha256:")
    assert metadata["packageCount"] == 1
    assert metadata["bindingCount"] == 1
    assert metadata["packages"][0]["packageFingerprint"] == bound.package_fingerprint


def test_central_and_package_binding_identity_collision_is_refused() -> None:
    block = FakeBlock(
        block_id="demo.acceptance.one",
        app="demo",
        status="specified",
        fields={"requires": ["demo"]},
    )
    central = binding()
    package = runner.Binding(
        binding_id=central.binding_id,
        app_id="package-demo",
        contract_id="package-demo.acceptance.one",
        runner="pytest",
        selectors=("mcel_apps/package-demo/tests/test_acceptance.py",),
        notes="",
        source_kind="package",
    )
    package_block = FakeBlock(
        block_id="package-demo.acceptance.one",
        app="package-demo",
        status="specified",
        fields={"requires": ["demo"]},
        source_file="mcel_apps/package-demo/requirements.md",
    )

    with pytest.raises(runner.McelAcceptanceError, match="Duplicate acceptance binding ids"):
        runner.combine_acceptance_sources(
            central_contracts=[block],
            central_bindings={central.contract_id: central},
            central_metadata={},
            package_contracts=[package_block],
            package_bindings={package.contract_id: package},
            package_metadata={},
        )


def test_contract_counter_app_scoped_acceptance_cli_passes_with_package_fingerprint(tmp_path: Path) -> None:
    output = tmp_path / "acceptance"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--app",
            "contract-counter",
            "--output-dir",
            str(output),
            "--check",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=90,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "status: pass" in completed.stdout
    assert "evidence_scope: app-scoped" in completed.stdout
    assert "enforceable_contracts: 1" in completed.stdout
    assert "passed_contracts: 1" in completed.stdout

    report = json.loads((output / "mcel-acceptance-report.json").read_text(encoding="utf-8"))
    contract = report["results"][0]["contracts"][0]
    assert contract["bindingSource"] == "package"
    assert contract["packageFingerprint"].startswith("sha256:")
    assert report["applicationPackages"][0]["appId"] == "contract-counter"
    assert report["applicationPackages"][0]["packageFingerprint"] == contract["packageFingerprint"]
