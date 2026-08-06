from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from main_computer.mcel_node_runtime import resolve_node_executable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
TOOLKIT = SCRIPTS / "mcel-semantic-adapter-toolkit.js"
REGISTRY = SCRIPTS / "mcel-domain-adapter-registry.js"
FILE_EXPLORER_ADAPTER = SCRIPTS / "file-explorer-semantic-adapter.js"
SHELL = ROOT / "main_computer" / "web" / "applications.html"
TOOLKIT_DOC = ROOT / "pretty_docs" / "mcel-shared-semantic-adapter-toolkit.md"


def run_node_json(script: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; MCEL semantic-adapter toolkit tests cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_toolkit_is_loaded_before_proven_semantic_adapters() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    registry_include = "<!-- @include applications/scripts/mcel-domain-adapter-registry.js -->"
    toolkit_include = "<!-- @include applications/scripts/mcel-semantic-adapter-toolkit.js -->"
    file_explorer_include = "<!-- @include applications/scripts/file-explorer-semantic-adapter.js -->"

    for include in (
        registry_include,
        toolkit_include,
        file_explorer_include,
    ):
        assert include in shell

    assert "calculator-semantic-adapter.js" not in shell
    assert shell.index(registry_include) < shell.index(toolkit_include)
    assert shell.index(toolkit_include) < shell.index(file_explorer_include)


def test_toolkit_exports_shared_adapter_primitives() -> None:
    result = run_node_json(
        textwrap.dedent(
            f"""
            const toolkit = require({json.dumps(str(TOOLKIT))});
            const ledger = [];
            const receipt = toolkit.appendBoundedReceipt(
              ledger,
              {{receiptId: "r1", nested: {{value: 1}}, fn() {{ return false; }}}},
              {{maxReceipts: 1}}
            );
            const preflight = toolkit.preflightResult({{
              schema: "demo-preflight-v1",
              appId: "demo",
              intentId: "inspect",
              allowed: true,
              blockers: [],
              options: {{now: "2026-07-27T00:00:00Z"}},
              checks: {{knownIntent: true}}
            }});
            toolkit.appendBoundedReceipt(ledger, {{receiptId: "r2"}}, {{maxReceipts: 1}});
            const dispatched = [];
            toolkit.dispatchAction(
              {{run(payload, options) {{ dispatched.push({{payload, options}}); return {{ok: true, value: payload.value}}; }}}},
              "runIntent",
              {{value: 7}},
              {{methodName: "run", adapterId: "adapter-test"}}
            ).then((dispatchResult) => {{
              process.stdout.write(JSON.stringify({{
                version: toolkit.VERSION,
                statuses: toolkit.INTENT_STATUSES,
                clonedFunctionRemoved: receipt.fn === undefined,
                originalLedgerTrimmed: ledger.map((item) => item.receiptId),
                listedLedger: toolkit.listBoundedReceipts(ledger),
                preflight,
                dispatchResult,
                dispatchOptions: dispatched[0].options
              }}));
            }});
            """
        )
    )

    assert result["version"] == "mcel-semantic-adapter-toolkit-v1"
    assert "executable" in result["statuses"]
    assert "prohibited" in result["statuses"]
    assert result["clonedFunctionRemoved"] is True
    assert result["originalLedgerTrimmed"] == ["r2"]
    assert result["listedLedger"] == [{"receiptId": "r2"}]
    assert result["preflight"]["allowed"] is True
    assert result["preflight"]["decision"] == "allow"
    assert result["preflight"]["observedAt"] == "2026-07-27T00:00:00Z"
    assert result["dispatchResult"] == {"ok": True, "value": 7}
    assert result["dispatchOptions"]["adapterId"] == "adapter-test"


def test_file_explorer_semantic_adapter_still_uses_toolkit_after_calculator_retirement() -> None:
    result = run_node_json(
        textwrap.dedent(
            f"""
            const toolkit = require({json.dumps(str(TOOLKIT))});
            const registry = require({json.dumps(str(REGISTRY))});
            const fileExplorer = require({json.dumps(str(FILE_EXPLORER_ADAPTER))});
            const calculatorReadiness = registry.evaluateAdapterReadiness("calculator");
            const fileExplorerReadiness = registry.evaluateAdapterReadiness("file-explorer");
            process.stdout.write(JSON.stringify({{
              toolkitVersion: toolkit.VERSION,
              fileExplorerToolkitVersion: fileExplorer.TOOLKIT_VERSION,
              calculatorReadiness,
              fileExplorerReadiness,
              fileExplorerIntentCoverage: fileExplorer.getIntentCoverage(),
              fileExplorerIntents: fileExplorer.listIntents().map((intent) => [intent.id, intent.semanticStatus])
            }}));
            """
        )
    )

    assert result["fileExplorerToolkitVersion"] == result["toolkitVersion"]

    calculator = result["calculatorReadiness"]
    assert calculator["semanticRuntimeReady"] is False

    file_explorer = result["fileExplorerReadiness"]
    assert file_explorer["semanticRuntimeReady"] is True
    assert file_explorer["semanticRuntimeScope"] == "bounded-read-only-file-explorer-v1"
    assert file_explorer["executableIntentCount"] == 7
    assert file_explorer["prohibitedIntentCount"] == 3

    assert result["fileExplorerIntentCoverage"]["fullApplicationSemanticReady"] is True
    assert ["openInOwningApp", "preflight-only"] in result["fileExplorerIntents"]

def test_remaining_proven_adapter_delegates_common_work_to_toolkit() -> None:
    file_explorer_source = FILE_EXPLORER_ADAPTER.read_text(encoding="utf-8")

    assert "McelSemanticAdapterToolkit" in file_explorer_source
    assert "ADAPTER_TOOLKIT.clonePlain" in file_explorer_source
    assert "ADAPTER_TOOLKIT.listIntentDefinitions" in file_explorer_source
    assert "ADAPTER_TOOLKIT.recoveryCoverageAudit" in file_explorer_source
    assert "ADAPTER_TOOLKIT.appendBoundedReceipt" in file_explorer_source
    assert "ADAPTER_TOOLKIT.listBoundedReceipts" in file_explorer_source


def test_toolkit_conformance_contract_is_explicit_and_self_validating() -> None:
    result = run_node_json(
        textwrap.dedent(
            f"""
            const toolkit = require({json.dumps(str(TOOLKIT))});
            const contract = toolkit.buildConformanceContract();
            const report = toolkit.validateToolkitConformance();
            const intentionallyIncomplete = {{
              ...toolkit,
              INTENT_STATUSES: ["executable"],
              dispatchAction: undefined
            }};
            const incompleteReport = toolkit.validateToolkitConformance(intentionallyIncomplete);
            process.stdout.write(JSON.stringify({{
              contract,
              report,
              incompleteReport
            }}));
            """
        )
    )

    contract = result["contract"]
    report = result["report"]
    incomplete = result["incompleteReport"]

    assert contract["id"] == "mcel.semantic-adapter-toolkit.conformance.v1"
    assert contract["version"] == "mcel-semantic-adapter-toolkit-conformance-v1"
    assert contract["toolkitVersion"] == "mcel-semantic-adapter-toolkit-v1"
    assert "dispatchAction" in contract["requiredPublicApi"]
    assert "validateToolkitConformance" in contract["requiredPublicApi"]
    assert {clause["id"] for clause in contract["clauses"]} == {
        "mcel.semantic-adapter-toolkit.clone-plain.v1",
        "mcel.semantic-adapter-toolkit.intent-declaration.v1",
        "mcel.semantic-adapter-toolkit.preflight-receipt.v1",
        "mcel.semantic-adapter-toolkit.dispatch.v1",
        "mcel.semantic-adapter-toolkit.recovery-coverage.v1",
    }

    assert report["schema"] == "mcel-semantic-adapter-toolkit-conformance-report-v1"
    assert report["contractId"] == contract["id"]
    assert report["passed"] is True
    assert report["missingPublicApi"] == []
    assert report["missingIntentStatuses"] == []
    assert report["failedClauseIds"] == []
    assert all(clause["status"] == "pass" for clause in report["clauses"])

    assert incomplete["passed"] is False
    assert "dispatchAction" in incomplete["missingPublicApi"]
    assert "planned" in incomplete["missingIntentStatuses"]
    assert "mcel.semantic-adapter-toolkit.dispatch.v1" in incomplete["failedClauseIds"]


def test_toolkit_contract_documentation_matches_exported_contract() -> None:
    result = run_node_json(
        textwrap.dedent(
            f"""
            const toolkit = require({json.dumps(str(TOOLKIT))});
            process.stdout.write(JSON.stringify(toolkit.buildConformanceContract()));
            """
        )
    )
    doc = TOOLKIT_DOC.read_text(encoding="utf-8")

    assert "# MCEL Shared Semantic Adapter Toolkit" in doc
    assert result["id"] in doc
    assert result["version"] in doc
    assert result["toolkitVersion"] in doc
    assert "does not grant semantic-runtime proof by itself" in doc
    assert "replace runtime FLOG evidence" in doc
    assert "replace acceptance evidence" in doc
    assert "python main_computer/mcel_truth_audit.py --release-gate" in doc

    for api_name in result["requiredPublicApi"]:
        assert api_name in doc

    for status in result["intentStatuses"]:
        assert status in doc

    for clause in result["clauses"]:
        assert clause["id"] in doc or clause["category"] in doc
