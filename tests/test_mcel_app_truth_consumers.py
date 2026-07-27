from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
LAB_HTML = WEB / "apps" / "mcel-lab.html"
LAB_JS = WEB / "scripts" / "mcel-lab.js"
FLOG = ROOT / "main_computer" / "flog_mcel_runtime_smoke.py"
DOC = ROOT / "pretty_docs" / "mcel-app-truth-gate.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL Lab truth-consumer runtime test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_mcel_lab_exposes_selected_app_truth_card() -> None:
    html = LAB_HTML.read_text(encoding="utf-8")

    assert 'id="mcel-blueprint-app-truth-card"' in html
    assert 'data-mcel-element="element.inspection.app-truth"' in html
    assert 'id="mcel-blueprint-app-truth-status"' in html
    assert 'id="mcel-blueprint-app-truth-source"' in html
    assert 'id="mcel-blueprint-app-truth-findings"' in html

    for fact in ("requirements", "adapter", "runtime", "acceptance", "semantic"):
        assert f'data-mcel-app-truth-fact="{fact}"' in html


def test_mcel_lab_consumes_truth_gate_without_reimplementing_findings() -> None:
    script = LAB_JS.read_text(encoding="utf-8")

    assert "window.McelAppTruthGate || window.MCEL?.appTruthGate" in script
    assert "gate.evaluateAppTruth(candidateId" in script
    assert "mcelBlueprintTruthLiveRuntimeEvidence" in script
    assert "mcelBlueprintTruthCandidateIds" in script
    assert "mcelBlueprintTruthCandidateScore" in script
    assert "Truth ID ${result.truthAppId} is the registered alias" in script

    # Lab findings retain the gate's code and message instead of inventing
    # a parallel local finding taxonomy.
    assert "`[${item.code}] ${item.message}`" in script
    assert "`[truth:${item.code}] ${item.message}`" in script
    assert "truthFindingMessages" in script


def test_mcel_lab_exposes_evidence_injection_consumer_api() -> None:
    script = LAB_JS.read_text(encoding="utf-8")

    assert "function installMcelLabAppTruthConsumer()" in script
    assert "window.McelLabAppTruthConsumer = api" in script
    assert "labAppTruthConsumer: api" in script

    for method in (
        "setRuntimeEvidence(runtimeEvidence)",
        "setAcceptanceEvidence(acceptanceEvidence)",
        "clearEvidence()",
        "refresh()",
        "selectedAppTruth()",
    ):
        assert method in script


def test_flog_asks_browser_truth_gate_for_per_app_and_repository_truth() -> None:
    script = FLOG.read_text(encoding="utf-8")

    assert "window.McelAppTruthGate" in script
    assert "truthGate.evaluateAppTruth(appId, {runtimeEvidence})" in script
    assert "truthGate.buildTruthSnapshot({runtimeEvidence})" in script
    assert '"appTruthAvailable"' in script
    assert '"appTruthSnapshot"' in script
    assert '"truthSource": "window.McelAppTruthGate evaluateAppTruth/buildTruthSnapshot"' in script
    assert "Truth findings do not rewrite the FLOG surface verdict" in script


def test_truth_gate_documentation_declares_consumer_authority_boundaries() -> None:
    doc = DOC.read_text(encoding="utf-8")

    assert "## Patch 25b consumers" in doc
    assert "The snapshot is produced by `McelAppTruthGate.buildTruthSnapshot`" in doc
    assert "Truth-gate findings are copied into the Lab findings list" in doc
    assert "FLOG runtime pass/fail remains separate from broader truth status" in doc
    assert "consumers do not create a second registry or recompute truth findings" in doc


def test_mcel_lab_truth_consumer_prefers_registered_blueprint_alias() -> None:
    result = run_node_json(
        textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");
            const sandbox = {{console, setTimeout: () => 0, clearTimeout: () => {{}}}};
            sandbox.window = sandbox;
            sandbox.globalThis = sandbox;
            sandbox.document = {{
              getElementById: () => null,
              querySelectorAll: () => [],
              createElement: (tag) => ({{
                tagName: tag.toUpperCase(),
                dataset: {{}},
                setAttribute() {{}},
                append() {{}},
                appendChild() {{}},
                addEventListener() {{}},
                classList: {{add() {{}}, remove() {{}}, toggle() {{}}}}
              }})
            }};
            sandbox.createDefaultMcelLabState = () => ({{}});
            sandbox.McelAppBlueprintsCore = {{
              listInspectableAppBlueprints: () => [{{
                appId: "document-editor",
                aliases: ["document"],
                label: "Document Editor"
              }}],
              inspectableBlueprintFor: () => ({{
                appId: "document-editor",
                aliases: ["document"],
                label: "Document Editor"
              }})
            }};
            sandbox.McelAppTruthGate = {{
              evaluateAppTruth(appId) {{
                return {{
                  appId,
                  overallStatus: appId === "document" ? "runtime-proven" : "untracked",
                  requirements: {{
                    present: false,
                    schemaValid: true,
                    contractComplete: false,
                    acceptanceContractCount: 0
                  }},
                  adapter: {{
                    registered: false,
                    runtimeCoreReady: false,
                    fullApplicationSemanticReady: false
                  }},
                  surface: {{
                    registered: appId === "document",
                    conformanceRequired: appId === "document"
                  }},
                  evidence: {{
                    runtime: {{
                      present: appId === "document",
                      policyPassed: appId === "document"
                    }},
                    acceptance: {{present: false}}
                  }},
                  claims: {{
                    runtimeSurfaceProven: appId === "document",
                    acceptanceProven: true,
                    semanticRuntimeProven: false
                  }},
                  findings: [{{
                    code: "missing-domain-adapter",
                    message: "Missing adapter."
                  }}]
                }};
              }}
            }};
            sandbox.MCEL = {{
              diagnose: (appId) => ({{
                appId,
                verdict: "pass",
                schema: "diag",
                appSurfaceConformance: {{appId, status: "pass"}}
              }})
            }};
            vm.runInNewContext(
              fs.readFileSync({json.dumps(str(LAB_JS))}, "utf8"),
              sandbox,
              {{filename: "mcel-lab.js"}}
            );
            const selected = sandbox.McelLabAppTruthConsumer.selectedAppTruth();
            console.log(JSON.stringify({{
              truthAppId: selected.truthAppId,
              overallStatus: selected.truth.overallStatus,
              semanticRuntimeProven: selected.truth.claims.semanticRuntimeProven,
              consumerOnMcel: Boolean(sandbox.MCEL.labAppTruthConsumer)
            }}));
            """
        )
    )

    assert result == {
        "truthAppId": "document",
        "overallStatus": "runtime-proven",
        "semanticRuntimeProven": False,
        "consumerOnMcel": True,
    }

