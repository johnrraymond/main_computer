from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import (
    build_repository_browser_catalog_payload,
    render_browser_catalog_javascript,
)

from mcel_dsl_authoring_harness import (
    assert_surface_bundle_contract_shape,
    browser_surface_bundles_by_app,
    discover_dsl_semantic_runtime_app_cases,
    missing_surface_bundle_cases,
    runtime_projections_by_app,
    surface_bundle_cases,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
SELF_DIAGNOSIS_JS = SCRIPTS / "mcel-self-diagnosis.js"


def _run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL DSL authoring audit smoke cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_generic_harness_discovers_dsl_semantic_runtime_apps_without_fixture_names() -> None:
    cases = discover_dsl_semantic_runtime_app_cases(ROOT)

    assert cases
    assert all(case.record.valid for case in cases)
    assert all(case.manifest_authoring["status"] == "dsl-authoritative" for case in cases)
    assert all(case.manifest_authoring["source"].endswith("application.js") for case in cases)
    assert all(
        "semantic-runtime-proven"
        in {
            case.record.conformance.get("currentMode"),
            case.record.conformance.get("targetMode"),
        }
        for case in cases
    )

    declared_cases = surface_bundle_cases(ROOT)
    assert declared_cases
    for case in declared_cases:
        bundle = assert_surface_bundle_contract_shape(case)
        assert case.record.contracts["surfaceBundle"].endswith("/contracts/surface-bundle.json")
        assert case.record.files["contracts/surface-bundle.json"]
        assert bundle["semanticSurface"]["surfaceId"] == bundle["surfaceId"]


def test_generic_surface_bundle_harness_reaches_runtime_projection_and_browser_catalog() -> None:
    declared_cases = surface_bundle_cases(ROOT)
    projections = runtime_projections_by_app(ROOT)
    browser_bundles = browser_surface_bundles_by_app(ROOT)

    assert declared_cases
    assert set(browser_bundles) == {case.app_id for case in declared_cases}

    for case in declared_cases:
        projection = projections[case.app_id]
        bundle = assert_surface_bundle_contract_shape(case)

        assert projection.surface_bundle_url == (
            f"applications/mcel-packages/{case.app_id}/contracts/surface-bundle.json"
        )
        assert projection.surface_bundle == bundle
        assert projection.manifest["surfaceBundle"]["path"] == "contracts/surface-bundle.json"
        assert projection.manifest["surfaceBundle"]["schema"] == "mcel.application-surface-bundle.v1"
        assert browser_bundles[case.app_id] == bundle

    for case in missing_surface_bundle_cases(ROOT):
        projection = projections[case.app_id]
        assert projection.surface_bundle is None
        assert case.app_id not in browser_bundles


def test_generic_self_diagnosis_audit_matches_package_surface_bundle_coverage() -> None:
    cases = discover_dsl_semantic_runtime_app_cases(ROOT)
    expected_declared = {case.app_id: case.has_surface_bundle_contract for case in cases}
    catalog_js = render_browser_catalog_javascript(build_repository_browser_catalog_payload(ROOT))

    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext({json.dumps(catalog_js)}, sandbox, {{filename: "mcel-application-package-catalog.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(SELF_DIAGNOSIS_JS))}, "utf8"), sandbox, {{filename: "mcel-self-diagnosis.js"}});
        const audit = sandbox.McelSelfDiagnosis.auditDslAuthoringDeclarationCoverage();
        process.stdout.write(JSON.stringify({{
          appCount: audit.appCount,
          warningCount: audit.warningCount,
          greenCount: audit.greenCount,
          apps: Object.fromEntries(audit.apps.map((app) => [app.appId, {{
            status: app.status,
            warningCount: app.warningCount,
            semanticSurfaceDeclared: app.semanticSurfaceDeclared,
            layoutGrammarDeclared: app.layoutGrammarDeclared,
            codes: app.findings.map((finding) => finding.code)
          }}]))
        }}));
        """
    )
    audit = _run_node_json(script)

    assert set(audit["apps"]) == set(expected_declared)
    expected_warning_count = 0
    for app_id, has_declared_bundle in expected_declared.items():
        app = audit["apps"][app_id]
        if has_declared_bundle:
            assert app == {
                "status": "green",
                "warningCount": 0,
                "semanticSurfaceDeclared": True,
                "layoutGrammarDeclared": True,
                "codes": [],
            }
        else:
            expected_warning_count += 2
            assert app == {
                "status": "warning",
                "warningCount": 2,
                "semanticSurfaceDeclared": False,
                "layoutGrammarDeclared": False,
                "codes": [
                    "dsl-semantic-surface-missing",
                    "dsl-layout-grammar-missing",
                ],
            }

    assert audit["appCount"] == len(expected_declared)
    assert audit["warningCount"] == expected_warning_count
    assert audit["greenCount"] == sum(1 for declared in expected_declared.values() if declared)
