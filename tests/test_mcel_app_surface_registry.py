from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB / "scripts"
APP_SHELL = ROOT / "main_computer" / "web" / "applications.html"
REGISTRY_JS = SCRIPTS / "mcel-app-surface-registry.js"
CONFORMANCE_JS = SCRIPTS / "mcel-app-surface-conformance.js"
DOC = ROOT / "pretty_docs" / "mcel-app-surface-registry.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL app-surface registry smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_registry_stack(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(REGISTRY_JS))}, "utf8"), sandbox, {{filename: "mcel-app-surface-registry.js"}});
        const registry = sandbox.McelAppSurfaceRegistry;
        {body}
        """
    )


def load_conformance_stack(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        for (const name of [
          "mcel-semantic-surface-ridges.js",
          "mcel-semantic-surface-ir.js",
          "mcel-shared-layout-grammar.js",
          "mcel-surface-extractors.js",
          "mcel-app-surface-registry.js",
          "mcel-app-surface-conformance.js"
        ]) {{
          vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"), sandbox, {{filename: name}});
        }}
        const conformance = sandbox.McelAppSurfaceConformance;
        {body}
        """
    )


def healthy_runtime_report(app_id: str, surface_id: str, selector: str) -> dict:
    return {
        "schema": "mcel-self-diagnosis-report-v2",
        "version": "mcel-self-diagnosis-v2",
        "appId": app_id,
        "verdict": "pass",
        "summary": {
            "critical": 0,
            "warning": 0,
            "info": 0,
            "primarySurface": {
                "expected": surface_id,
                "usable": True,
                "exactlyOneAuthoritativeSurface": True,
                "host": {
                    "exists": True,
                    "visible": True,
                    "selector": selector,
                    "width": 760,
                    "height": 520,
                },
            },
        },
        "findings": [],
        "measurements": {
            "viewport": {"width": 1280, "height": 720},
            "requiredRegions": {},
            "surfaces": {
                "primaryHost": {
                    "exists": True,
                    "visible": True,
                    "selector": selector,
                    "width": 760,
                    "height": 520,
                }
            },
            "layoutCollisions": [],
            "contentFitViolations": [],
            "visualIntegrityViolations": [],
        },
        "contract": {
            "primarySurface": {
                "id": surface_id,
                "minWidth": 420,
                "minHeight": 320,
            }
        },
    }


def test_app_surface_registry_is_wired_before_conformance_and_diagnostics() -> None:
    assert REGISTRY_JS.exists()
    assert DOC.exists()

    app_shell = APP_SHELL.read_text(encoding="utf-8")
    assert "mcel-app-surface-registry.js" in app_shell
    assert app_shell.index("mcel-app-surface-registry.js") < app_shell.index("mcel-app-surface-conformance.js")
    assert app_shell.index("mcel-app-surface-registry.js") < app_shell.index("mcel-self-diagnosis.js")
    assert app_shell.index("mcel-app-surface-registry.js") < app_shell.index("mcel-diagnostics-counter-widget.js")


def test_registry_declares_the_first_required_surface_aware_apps() -> None:
    script = load_registry_stack(
        """
        process.stdout.write(JSON.stringify({
          version: registry.registryVersion,
          required: registry.listConformanceRequiredApps(),
          legacy: registry.listLegacyApps(),
          summary: registry.summarizeRegistry()
        }));
        """
    )
    data = run_node_json(script)

    assert data["version"] == "mcel.app-surface-registry.v1"
    assert data["required"] == [
        "calculator",
        "code-editor",
        "document",
        "file-explorer",
        "website-builder",
    ]
    for legacy_app in [
        "ai-control",
        "git-tools",
        "mcel-lab",
        "terminal",
        "wallet",
    ]:
        assert legacy_app in data["legacy"]
    assert data["summary"]["requiredCount"] == 5
    assert data["summary"]["legacyCount"] >= 9


def test_registry_distinguishes_full_semantic_runtime_from_runtime_baseline() -> None:
    script = load_registry_stack(
        """
        const fileExplorer = registry.getAppPolicy("file-explorer");
        const documentEditor = registry.getAppPolicy("document");
        const calculator = registry.getAppPolicy("calculator");
        const legacy = registry.getAppPolicy("git-tools");
        const unknown = registry.getAppPolicy("made-up-app");
        process.stdout.write(JSON.stringify({
          fileExplorer,
          documentEditor,
          calculator,
          legacy,
          unknown
        }));
        """
    )
    data = run_node_json(script)

    assert data["fileExplorer"]["conformanceRequired"] is True
    assert data["fileExplorer"]["maturity"] == "semantic-runtime"
    assert data["fileExplorer"]["requiredLayerIds"] == [
        "semantic-surface",
        "layout-grammar",
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]

    assert data["documentEditor"]["conformanceRequired"] is True
    assert data["documentEditor"]["maturity"] == "semantic-runtime"
    assert data["documentEditor"]["surfaceId"] == "document-editor.surface.primary"
    assert data["documentEditor"]["requiredLayerIds"] == [
        "semantic-surface",
        "layout-grammar",
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]

    assert data["calculator"]["conformanceRequired"] is True
    assert data["calculator"]["maturity"] == "runtime-baseline"
    assert data["calculator"]["requiredLayerIds"] == [
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]

    assert data["legacy"]["conformanceRequired"] is False
    assert data["legacy"]["state"] == "legacy"
    assert data["unknown"]["conformanceRequired"] is False
    assert data["unknown"]["state"] == "unregistered"


def test_required_runtime_baseline_apps_can_pass_without_static_surface_yet() -> None:
    report = healthy_runtime_report("calculator", "calculator.surface.workspace", ".calculator-workspace")
    script = load_conformance_stack(
        f"""
        const result = conformance.evaluateAppSurfaceConformance({{
          appId: "calculator",
          report: {json.dumps(report)}
        }});
        process.stdout.write(JSON.stringify({{
          status: result.status,
          valid: result.valid,
          conformanceRequired: result.conformanceRequired,
          registryState: result.registryState,
          requiredLayerIds: result.requiredLayerIds,
          unavailableLayerIds: result.unavailableLayerIds,
          policyFailedLayerIds: result.policyFailedLayerIds,
          policyUnavailableLayerIds: result.policyUnavailableLayerIds
        }}));
        """
    )
    data = run_node_json(script)

    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["conformanceRequired"] is True
    assert data["registryState"] == "surface-aware"
    assert data["requiredLayerIds"] == [
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]
    assert data["unavailableLayerIds"] == ["semantic-surface", "layout-grammar"]
    assert data["policyFailedLayerIds"] == []
    assert data["policyUnavailableLayerIds"] == []


def test_legacy_apps_are_not_marked_broken_only_because_static_surface_is_absent() -> None:
    report = healthy_runtime_report("git-tools", "git-tools.surface.workflow", "#git-project-workflow-surface")
    script = load_conformance_stack(
        f"""
        const result = conformance.evaluateAppSurfaceConformance({{
          appId: "git-tools",
          report: {json.dumps(report)}
        }});
        process.stdout.write(JSON.stringify({{
          status: result.status,
          valid: result.valid,
          conformanceRequired: result.conformanceRequired,
          registryState: result.registryState,
          unavailableLayerIds: result.unavailableLayerIds,
          policyUnavailableLayerIds: result.policyUnavailableLayerIds
        }}));
        """
    )
    data = run_node_json(script)

    assert data["status"] == "not-required"
    assert data["valid"] is True
    assert data["conformanceRequired"] is False
    assert data["registryState"] == "legacy"
    assert data["unavailableLayerIds"] == ["semantic-surface", "layout-grammar"]
    assert data["policyUnavailableLayerIds"] == []


def test_file_explorer_full_policy_requires_all_five_layers() -> None:
    html = (WEB / "apps" / "file-explorer.html").read_text(encoding="utf-8")
    report = healthy_runtime_report("file-explorer", "file-explorer.surface.primary", ".file-explorer-main")
    script = load_conformance_stack(
        f"""
        const result = conformance.evaluateAppSurfaceConformance({{
          appId: "file-explorer",
          surfaceHtml: {json.dumps(html)},
          report: {json.dumps(report)}
        }});
        process.stdout.write(JSON.stringify({{
          status: result.status,
          valid: result.valid,
          conformanceRequired: result.conformanceRequired,
          maturity: result.registryPolicy.maturity,
          requiredLayerIds: result.requiredLayerIds,
          policyFailedLayerIds: result.policyFailedLayerIds,
          policyUnavailableLayerIds: result.policyUnavailableLayerIds
        }}));
        """
    )
    data = run_node_json(script)

    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["conformanceRequired"] is True
    assert data["maturity"] == "semantic-runtime"
    assert data["requiredLayerIds"] == [
        "semantic-surface",
        "layout-grammar",
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]
    assert data["policyFailedLayerIds"] == []
    assert data["policyUnavailableLayerIds"] == []


def test_registry_documentation_is_domain_neutral() -> None:
    text = DOC.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "file-explorer" in lowered
    assert "document" in lowered
    assert "website-builder" in lowered
    assert "code-editor" in lowered
    assert "calculator" in lowered
    assert "legacy / not converted yet" in lowered
    for forbidden in ["health app", "bio", "patient", "clinical"]:
        assert forbidden not in lowered
