from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.mcel_requirements_registry import (  # noqa: E402
    REGISTRY_VERSION,
    build_app_contract_summaries,
    build_lab_payload,
    build_registry,
    build_runtime_diagnostic_contracts,
    render_markdown_report,
)


EXPECTED_APP_CONTRACTS = {
    "calculator",
    "code-editor",
    "file-explorer",
    "git-tools",
    "mcel-lab",
    "website-builder",
}


def test_registry_parses_current_mcel_docs_without_hard_errors() -> None:
    registry = build_registry(ROOT)
    summary = registry.summary()

    assert registry.valid
    assert summary["registry_version"] == REGISTRY_VERSION
    assert set(summary["app_contracts"]) == EXPECTED_APP_CONTRACTS
    assert summary["total_blocks"] >= 300
    assert summary["block_type_counts"]["mcel-app"] == 6
    assert summary["block_type_counts"]["mcel-grammar"] >= 18
    assert summary["block_type_counts"]["mcel-requirement"] >= 55
    assert summary["block_type_counts"]["mcel-intent"] >= 57
    assert summary["block_type_counts"]["mcel-region"] >= 50
    assert summary["block_type_counts"]["mcel-form-primitive"] >= 40
    assert summary["block_type_counts"]["mcel-runtime-check"] >= 19


def test_registry_preserves_source_locations_and_expected_use_cases() -> None:
    registry = build_registry(ROOT)
    blocks = registry.by_id()

    expected_ids = {
        "calculator.use-case.compare-monthly-costs",
        "file-explorer.use-case.inspect-project-file-safely",
        "file-explorer.use-case.browse-mounted-windows-drive",
        "website-builder.use-case.edit-preview-saved-site",
        "website-builder.use-case.configure-blog-runtime",
        "website-builder.use-case.publish-selected-lane",
        "website-builder.use-case.git-tools-handoff",
        "code-editor.use-case.review-apply-ai-source-change",
        "code-editor.use-case.edit-save-source-file",
        "git-tools.use-case.push-current-branch-local-gitea",
        "git-tools.use-case.add-ignore-rule",
        "git-tools.use-case.switch-branch-safely",
        "git-tools.use-case.select-files-stage-commit",
        "mcel-lab.use-case.inspect-blueprint-from-doc-contract",
        "mcel-lab.use-case.self-host-refactor-context",
    }
    assert expected_ids <= set(blocks)

    calculator_case = blocks["calculator.use-case.compare-monthly-costs"]
    assert calculator_case.block_type == "mcel-use-case"
    assert calculator_case.app == "calculator"
    assert calculator_case.source_file == "pretty_docs/mcel-calculator-requirements.md"
    assert calculator_case.start_line > 0
    assert calculator_case.end_line >= calculator_case.start_line
    assert "acceptance" in calculator_case.fields


def test_registry_loads_grammar_and_is_strict_schema_ready() -> None:
    registry = build_registry(ROOT)
    warning_codes = Counter(issue.code for issue in registry.warnings)

    assert registry.valid
    assert registry.strict_schema_ready
    assert registry.warnings == []

    assert registry.grammar_required_fields["mcel-intent"] == [
        "id",
        "app",
        "status",
        "intent",
        "risk",
        "requires",
        "produces",
    ]
    assert registry.grammar_required_fields["mcel-runtime-check"] == [
        "id",
        "app",
        "status",
        "mode",
        "contract",
        "check",
        "severity",
        "observes",
        "expects",
    ]
    assert registry.grammar_required_fields["mcel-form-primitive"] == [
        "id",
        "app",
        "status",
        "primitive",
        "meaning",
        "relationships",
        "constraints",
    ]
    assert "missing-required-field" not in warning_codes
    assert "custom-risk-alias" not in warning_codes
    assert "unknown-adapter-status" not in warning_codes
    assert "missing-app-use-case" not in warning_codes
    assert "current-plus-planned" not in warning_codes

    strict_registry = build_registry(ROOT, strict_schema=True)
    assert strict_registry.valid
    assert strict_registry.errors == []
    assert strict_registry.warnings == []
    assert strict_registry.strict_schema_ready


def test_runtime_checks_compile_into_browser_diagnosis_contracts() -> None:
    registry = build_registry(ROOT)
    contracts = build_runtime_diagnostic_contracts(registry)
    for app_id in EXPECTED_APP_CONTRACTS:
        assert app_id in contracts
        mode_contracts = contracts[app_id]["mode_contracts"]
        assert mode_contracts

    code_editor = contracts["code-editor"]["mode_contracts"]["authoring"]

    assert code_editor["contractId"] == "code-editor.contract.authoring.monaco-golden-path"
    assert code_editor["source"] == "mcel-runtime-check"
    assert code_editor["primarySurface"]["hostSelector"] == "#code-studio-runtime-monaco"
    assert code_editor["primarySurface"]["editorSelector"] == ".monaco-editor"
    assert code_editor["primarySurface"]["minWidth"] == 800
    assert code_editor["primarySurface"]["minHeight"] == 600

    required_selectors = {entry["selector"] for entry in code_editor["requiredRegions"]}
    assert "#code-editor-app" in required_selectors
    assert ".code-studio-sidebar" in required_selectors
    assert ".code-studio-editor-group" in required_selectors
    assert ".code-studio-statusbar" in required_selectors

    optional_selectors = {entry["selector"] for entry in code_editor["optionalRegions"]}
    assert ".code-studio-inspector" in optional_selectors
    optional_ids = {entry["id"] for entry in code_editor["optionalRegions"]}
    assert "code-editor.region.inspector" in optional_ids

    allowed_selectors = {entry["selector"] for entry in code_editor["allowedRegions"]}
    assert "#code-editor-mcel-tools-toggle" in allowed_selectors
    assert "#code-editor-diagnostics-counter" in allowed_selectors

    forbidden_selectors = {entry["selector"] for entry in code_editor["forbiddenRegions"]}
    assert "#code-studio-runtime-draft, .code-studio-runtime-fallback" in forbidden_selectors
    assert "#mc-widget-editor-pane.open, .mc-widget-selection:not([hidden]), .mc-widget-dock-preview:not([hidden])" in forbidden_selectors
    assert "#mc-widget-editor-root" not in forbidden_selectors

    assert "file-click-keeps-one-primary-editor" in code_editor["lifecycleAssertions"]
    assert "supporting-projection-must-collapse-before-primary-breaks" in code_editor["geometryPolicies"]
    assert "diagnostics-covering-primary-editor-are-forbidden" in code_editor["overlayPolicy"]
    assert {"surface", "layout", "overlays", "lifecycle", "form"} <= set(code_editor["checkCategories"])
    assert {check["check"] for check in code_editor["checks"]} >= {
        "primary-surface",
        "required-regions-visible",
        "secondary-surface-policy",
        "forbidden-surfaces-hidden",
        "lifecycle-contract-preserved",
    }

    calculator = contracts["calculator"]["mode_contracts"]["default"]
    assert calculator["contractId"] == "calculator.contract.default.app-health"
    assert calculator["primarySurface"]["hostSelector"] == ".calculator-workspace"

    file_explorer = contracts["file-explorer"]["mode_contracts"]["default"]
    assert file_explorer["primarySurface"]["hostSelector"] == ".file-explorer-main"

    git_tools = contracts["git-tools"]["mode_contracts"]["default"]
    assert git_tools["primarySurface"]["hostSelector"] == "#git-project-workflow-surface"

    website_builder = contracts["website-builder"]["mode_contracts"]["default"]
    assert website_builder["primarySurface"]["hostSelector"] == "[data-mcel-surface-id='website-builder.surface.preview']"

    lab = contracts["mcel-lab"]["mode_contracts"]["default"]
    assert lab["contractId"] == "mcel-lab.contract.default.blueprint-studio-health"
    assert lab["primarySurface"]["hostSelector"] == ".mcel-lab-blueprint-primary"
    assert lab["primarySurface"]["editorSelector"] == "#mcel-blueprint-work-surface"
    assert "form" in lab["checkCategories"]
    assert "#mcel-blueprint-app-select" in {entry["selector"] for entry in lab["requiredRegions"]}

    payload = build_lab_payload(registry)
    payload_contract = payload["runtime_diagnostic_contracts"]["code-editor"]["mode_contracts"]["authoring"]
    assert payload_contract["contractId"] == code_editor["contractId"]
    assert payload["app_contracts"]["code-editor"]["form_primitive_count"] >= 7
    assert "code-editor.form.feedback.integrity-and-activity" in {
        entry["id"] for entry in payload["app_contracts"]["code-editor"]["form_primitives"]
    }
    assert payload["app_contracts"]["calculator"]["form_primitive_count"] >= 6
    assert payload["app_contracts"]["file-explorer"]["form_primitive_count"] >= 6
    assert payload["app_contracts"]["git-tools"]["form_primitive_count"] >= 6
    assert payload["app_contracts"]["website-builder"]["form_primitive_count"] >= 6
    assert "calculator.form.feedback.validation-and-compute-state" in {
        entry["id"] for entry in payload["app_contracts"]["calculator"]["form_primitives"]
    }
    assert "file-explorer.form.feedback.boundary-and-preview-state" in {
        entry["id"] for entry in payload["app_contracts"]["file-explorer"]["form_primitives"]
    }
    assert "git-tools.form.feedback.risk-and-operation-state" in {
        entry["id"] for entry in payload["app_contracts"]["git-tools"]["form_primitives"]
    }
    assert "website-builder.form.feedback.save-preview-publish-state" in {
        entry["id"] for entry in payload["app_contracts"]["website-builder"]["form_primitives"]
    }
    assert "calculator" in payload["runtime_diagnostic_contracts"]
    assert "mcel-lab" in payload["runtime_diagnostic_contracts"]
    assert payload["app_contracts"]["mcel-lab"]["form_primitive_count"] >= 8


def test_intent_blocks_use_canonical_risk_and_io_fields() -> None:
    registry = build_registry(ROOT)
    blocks = registry.by_id()

    representative_risks = {
        "calculator.intent.ask-model-expression": "read-only",
        "git-tools.intent.refresh-status": "read-only",
        "code-editor.intent.run-code": "execution",
        "file-explorer.intent.delete-file": "prohibited",
    }

    for block_id, expected_risk in representative_risks.items():
        block = blocks[block_id]
        assert block.fields["risk"] == expected_risk
        assert block.canonical_risk == expected_risk

    for block in registry.blocks:
        if block.block_type != "mcel-intent":
            continue
        assert isinstance(block.fields.get("requires"), list) and block.fields["requires"]
        assert isinstance(block.fields.get("produces"), list) and block.fields["produces"]


def test_registry_groups_blocks_by_app_contract() -> None:
    registry = build_registry(ROOT)
    by_app = registry.by_app()

    assert set(by_app) == EXPECTED_APP_CONTRACTS

    for app_id in EXPECTED_APP_CONTRACTS:
        block_types = Counter(block.block_type for block in by_app[app_id])
        assert block_types["mcel-app"] == 1
        assert block_types["mcel-region"] >= 7
        assert block_types["mcel-requirement"] >= 7
        assert block_types["mcel-intent"] >= 7
        assert block_types["mcel-acceptance"] >= 1
        assert block_types["mcel-finding"] >= 1

    calculator_counts = Counter(block.block_type for block in by_app["calculator"])
    assert calculator_counts["mcel-use-case"] >= 1
    assert calculator_counts["mcel-form-primitive"] >= 6
    code_editor_counts = Counter(block.block_type for block in by_app["code-editor"])
    assert code_editor_counts["mcel-use-case"] >= 1
    assert code_editor_counts["mcel-form-primitive"] >= 7
    file_explorer_counts = Counter(block.block_type for block in by_app["file-explorer"])
    assert file_explorer_counts["mcel-use-case"] >= 1
    assert file_explorer_counts["mcel-form-primitive"] >= 6
    git_tools_counts = Counter(block.block_type for block in by_app["git-tools"])
    assert git_tools_counts["mcel-use-case"] >= 1
    assert git_tools_counts["mcel-form-primitive"] >= 6
    website_builder_counts = Counter(block.block_type for block in by_app["website-builder"])
    assert website_builder_counts["mcel-use-case"] >= 1
    assert website_builder_counts["mcel-form-primitive"] >= 6
    lab_counts = Counter(block.block_type for block in by_app["mcel-lab"])
    assert lab_counts["mcel-use-case"] >= 2
    assert lab_counts["mcel-form-primitive"] >= 8
    assert lab_counts["mcel-runtime-check"] >= 3


def test_region_blocks_are_responsibility_normalized() -> None:
    registry = build_registry(ROOT)

    region_missing_required = [
        issue
        for issue in registry.warnings
        if issue.block_type == "mcel-region" and issue.code == "missing-required-field"
    ]
    assert region_missing_required == []

    for block in registry.blocks:
        if block.block_type != "mcel-region":
            continue
        assert isinstance(block.fields.get("region"), str) and block.fields["region"].strip()
        assert isinstance(block.fields.get("role"), str) and block.fields["role"].strip()
        assert isinstance(block.fields.get("responsibility"), str) and block.fields["responsibility"].strip()


def test_code_editor_form_primitives_define_semantic_anatomy() -> None:
    registry = build_registry(ROOT)
    blocks = registry.by_id()

    expected_ids = {
        "code-editor.form.subject.source-workspace",
        "code-editor.form.action.edit-source",
        "code-editor.form.work-surface.selected-source-editor",
        "code-editor.form.context.project-selection",
        "code-editor.form.context.reasoning-evidence",
        "code-editor.form.feedback.integrity-and-activity",
        "code-editor.form.transient.widget-structure-editing",
    }
    assert expected_ids <= set(blocks)

    primitive_types = {
        blocks[block_id].fields["primitive"]
        for block_id in expected_ids
    }
    assert {"subject", "action", "work-surface", "context", "feedback", "transient"} <= primitive_types

    editor_surface = blocks["code-editor.form.work-surface.selected-source-editor"]
    assert editor_surface.fields["primitive"] == "work-surface"
    assert "authoritative stable surface" in editor_surface.fields["meaning"]
    assert any("visible and usable" in item for item in editor_surface.fields["constraints"])

    feedback = blocks["code-editor.form.feedback.integrity-and-activity"]
    assert feedback.fields["primitive"] == "feedback"
    assert any("not interrupt or cover" in item for item in feedback.fields["constraints"])



def test_core_apps_have_semantic_form_coverage() -> None:
    registry = build_registry(ROOT)
    blocks = registry.by_id()

    expected_by_app = {
        "calculator": {
            "calculator.form.subject.calculation-session",
            "calculator.form.action.evaluate-and-explain",
            "calculator.form.work-surface.deterministic-compute",
            "calculator.form.context.result-evidence",
            "calculator.form.feedback.validation-and-compute-state",
            "calculator.form.transient.explicit-helper-evaluation",
        },
        "file-explorer": {
            "file-explorer.form.subject.browse-scope",
            "file-explorer.form.action.inspect-entry-safely",
            "file-explorer.form.work-surface.entry-inspection",
            "file-explorer.form.context.selection-and-classification",
            "file-explorer.form.feedback.boundary-and-preview-state",
            "file-explorer.form.transient.search-and-selection-evidence",
        },
        "git-tools": {
            "git-tools.form.subject.repository-project",
            "git-tools.form.action.governed-repository-change",
            "git-tools.form.work-surface.repository-workflow",
            "git-tools.form.context.evidence-and-preflight",
            "git-tools.form.feedback.risk-and-operation-state",
            "git-tools.form.transient.confirmation-and-recovery",
        },
        "website-builder": {
            "website-builder.form.subject.website-project",
            "website-builder.form.action.author-preview-publish",
            "website-builder.form.work-surface.site-authoring",
            "website-builder.form.context.runtime-and-publish-evidence",
            "website-builder.form.feedback.save-preview-publish-state",
            "website-builder.form.transient.setup-publish-and-handoff",
        },
    }

    for app_id, expected_ids in expected_by_app.items():
        assert expected_ids <= set(blocks)
        primitive_types = {blocks[block_id].fields["primitive"] for block_id in expected_ids}
        assert {"subject", "action", "work-surface", "context", "feedback", "transient"} <= primitive_types

        work_surface = next(
            blocks[block_id]
            for block_id in expected_ids
            if blocks[block_id].fields["primitive"] == "work-surface"
        )
        assert "primary" in work_surface.fields["meaning"]

        feedback = next(
            blocks[block_id]
            for block_id in expected_ids
            if blocks[block_id].fields["primitive"] == "feedback"
        )
        assert any("must not cover or replace" in item for item in feedback.fields["constraints"])


def test_mcel_lab_form_primitives_register_self_hosting_contract() -> None:
    registry = build_registry(ROOT)
    blocks = registry.by_id()
    payload = build_lab_payload(registry)

    expected_ids = {
        "mcel-lab.form.subject.app-blueprint",
        "mcel-lab.form.action.inspect-blueprint",
        "mcel-lab.form.work-surface.blueprint-inspection",
        "mcel-lab.form.context.app-and-aspect-selection",
        "mcel-lab.form.context.implementation-evidence",
        "mcel-lab.form.feedback.validation-and-mount-state",
        "mcel-lab.form.constraint.self-hosting-safety",
        "mcel-lab.form.transient.point-inspection",
        "mcel-lab.form.interruption.unsafe-repair-boundary",
    }
    assert expected_ids <= set(blocks)

    primitive_types = {blocks[block_id].fields["primitive"] for block_id in expected_ids}
    assert {
        "subject",
        "action",
        "work-surface",
        "context",
        "feedback",
        "constraint",
        "transient",
        "interruption",
    } <= primitive_types

    safety = blocks["mcel-lab.form.constraint.self-hosting-safety"]
    assert any("must not directly rewrite" in item for item in safety.fields["constraints"])

    lab_contract = payload["app_contracts"]["mcel-lab"]
    assert lab_contract["contract_complete"] is True
    assert lab_contract["dominant_object"] == "AppBlueprint"
    assert lab_contract["form_primitive_count"] >= 8


def test_registry_cli_emits_machine_readable_json_summary(tmp_path: Path) -> None:
    output = tmp_path / "registry-summary.json"

    result = subprocess.run(
        [
            sys.executable,
            "tools/mcel_requirements_registry.py",
            "--json",
            "--no-blocks",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert output.exists()

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["registry_version"] == REGISTRY_VERSION
    assert data["valid"] is True
    assert data["strict_schema_ready"] is True
    assert set(data["app_contracts"]) == EXPECTED_APP_CONTRACTS
    assert data["error_count"] == 0
    assert data["warning_count"] == 0
    assert "blocks" not in data


def test_registry_report_and_lab_payload_are_useful_app_contract_views() -> None:
    registry = build_registry(ROOT, strict_schema=True)

    report = render_markdown_report(registry)
    assert "# MCEL Requirements Registry Report" in report
    assert "Strict schema ready: `true`" in report
    assert "## App contracts" in report
    assert "Calculator" in report
    assert "`git-tools.use-case.push-current-branch-local-gitea`" in report
    assert "## MCEL Lab handoff" in report

    contracts = build_app_contract_summaries(registry)
    contracts_by_app = {contract["app"]: contract for contract in contracts}
    assert set(contracts_by_app) == EXPECTED_APP_CONTRACTS
    assert contracts_by_app["calculator"]["contract_complete"] is True
    assert contracts_by_app["git-tools"]["block_type_counts"]["mcel-use-case"] >= 4
    assert contracts_by_app["git-tools"]["mutation_intents"]
    assert contracts_by_app["file-explorer"]["intent_risk_counts"]["read-only"] >= 5

    payload = build_lab_payload(registry)
    assert payload["payload_version"] == "mcel-requirements-lab-payload-v1"
    assert payload["strict_schema_ready"] is True
    assert payload["summary"]["total_blocks"] >= 270
    assert set(payload["app_contracts"]) == EXPECTED_APP_CONTRACTS
    assert payload["app_comparison_seeds"]["git-tools"]["required_use_case_count"] >= 4
    assert payload["app_comparison_seeds"]["calculator"]["runtime_comparison_status"] == "pending-live-adapter-snapshot"


def test_registry_cli_emits_report_and_lab_payload(tmp_path: Path) -> None:
    report_output = tmp_path / "mcel-requirements-report.md"
    lab_output = tmp_path / "mcel-requirements-lab.json"

    report_result = subprocess.run(
        [
            sys.executable,
            "tools/mcel_requirements_registry.py",
            "--report",
            "--output",
            str(report_output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert report_result.returncode == 0
    report = report_output.read_text(encoding="utf-8")
    assert "# MCEL Requirements Registry Report" in report
    assert "## MCEL Lab handoff" in report

    lab_result = subprocess.run(
        [
            sys.executable,
            "tools/mcel_requirements_registry.py",
            "--lab-json",
            "--output",
            str(lab_output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert lab_result.returncode == 0
    payload = json.loads(lab_output.read_text(encoding="utf-8"))
    assert payload["payload_version"] == "mcel-requirements-lab-payload-v1"
    assert payload["strict_schema_ready"] is True
    assert set(payload["app_contracts"]) == EXPECTED_APP_CONTRACTS


def test_browser_requirements_registry_api_feeds_lab_comparison_snapshot() -> None:
    script_path = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-requirements-registry.js"
    script = f"""
    const fs = require("fs");
    const vm = require("vm");
    const sandbox = {{console}};
    sandbox.window = sandbox;
    vm.runInNewContext(fs.readFileSync({json.dumps(str(script_path))}, "utf8"), sandbox, {{filename: "mcel-requirements-registry.js"}});
    const api = sandbox.McelRequirementsRegistry;
    const gitContract = api.getAppContract("git-tools");
    const labContract = api.getAppContract("mcel-lab");
    const codeEditorDiagnosis = api.getRuntimeDiagnosisContract("code-editor", "authoring");
    const missingComparison = api.compareAppToRuntime("calculator", {{}});
    const gitComparison = api.compareAppToRuntime("git-tools", {{
      registryAdapterPresent: true,
      runtimeCoreReady: true,
      fullApplicationSemanticReady: false,
      executableIntentCount: 2
    }});
    const snapshot = api.buildLabComparisonSnapshot({{
      evaluateAdapterReadiness(appId) {{
        if (appId === "git-tools") {{
          return {{
            registryAdapterPresent: true,
            runtimeCoreReady: true,
            fullApplicationSemanticReady: false,
            executableIntentCount: 2
          }};
        }}
        return {{registryAdapterPresent: false, runtimeCoreReady: false, fullApplicationSemanticReady: false}};
      }}
    }});
    process.stdout.write(JSON.stringify({{
      strictSchemaReady: api.strictSchemaReady,
      appCount: api.listAppContracts().length,
      totalBlocks: api.getSummary().total_blocks,
      gitUseCaseCount: gitContract.use_cases.length,
      labFormPrimitiveCount: labContract.form_primitive_count,
      labDominantObject: labContract.dominant_object,
      codeEditorOptionalSelectors: codeEditorDiagnosis.optionalRegions.map((entry) => entry.selector),
      codeEditorAllowedSelectors: codeEditorDiagnosis.allowedRegions.map((entry) => entry.selector),
      codeEditorForbiddenSelectors: codeEditorDiagnosis.forbiddenRegions.map((entry) => entry.selector),
      missingStatus: missingComparison.comparisonStatus,
      missingGaps: missingComparison.gaps,
      gitStatus: gitComparison.comparisonStatus,
      snapshotAppCount: snapshot.appCount,
      snapshotStatusCounts: snapshot.comparisonStatusCounts
    }}));
    """
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)

    assert data["strictSchemaReady"] is True
    assert data["appCount"] == 6
    assert data["totalBlocks"] >= 270
    assert data["gitUseCaseCount"] >= 4
    assert data["labFormPrimitiveCount"] >= 8
    assert data["labDominantObject"] == "AppBlueprint"
    assert ".code-studio-inspector" in data["codeEditorOptionalSelectors"]
    assert "#code-editor-mcel-tools-toggle" in data["codeEditorAllowedSelectors"]
    assert "#mc-widget-editor-pane.open, .mc-widget-selection:not([hidden]), .mc-widget-dock-preview:not([hidden])" in data["codeEditorForbiddenSelectors"]
    assert "#mc-widget-editor-root" not in data["codeEditorForbiddenSelectors"]
    assert data["missingStatus"] == "requirements-runtime-gap"
    assert "No live domain adapter snapshot is available." in data["missingGaps"]
    assert data["gitStatus"] == "requirements-runtime-aligned-or-unverified"
    assert data["snapshotAppCount"] == 6
    assert data["snapshotStatusCounts"]["requirements-runtime-gap"] >= 1


def test_mcel_lab_loads_requirements_registry_surface() -> None:
    applications_html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    mcel_lab_html = (
        ROOT / "main_computer" / "web" / "applications" / "apps" / "mcel-lab.html"
    ).read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-requirements-registry.js -->" in applications_html
    assert applications_html.index("mcel-domain-adapter-registry.js") < applications_html.index(
        "mcel-requirements-registry.js"
    ) < applications_html.index("mcel-lab.js")

    assert 'aria-label="Requirements registry"' in mcel_lab_html
    assert "mcel-requirements-registry-v1" in mcel_lab_html
    assert "requirements → adapter readiness" in mcel_lab_html
