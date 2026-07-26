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
FIT_CONTRACT_JS = SCRIPTS / "mcel-surface-fit-contract.js"
SELF_DIAGNOSIS_JS = SCRIPTS / "mcel-self-diagnosis.js"
CONFORMANCE_JS = SCRIPTS / "mcel-app-surface-conformance.js"
DOCUMENT_SURFACE_JS = SCRIPTS / "mcel-document-editor-surface.js"
FILE_EXPLORER_SURFACE_JS = SCRIPTS / "mcel-file-explorer-surface.js"
DOCUMENT_HTML = WEB / "apps" / "document.html"
DOC = ROOT / "pretty_docs" / "mcel-surface-fit-contract.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL surface fit contract smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_fit_contract_stack(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(FIT_CONTRACT_JS))}, "utf8"), sandbox, {{filename: "mcel-surface-fit-contract.js"}});
        const fit = sandbox.McelSurfaceFitContract;
        {body}
        """
    )


def test_fit_contract_script_is_loaded_before_surface_and_diagnostics_modules() -> None:
    shell = APP_SHELL.read_text(encoding="utf-8")

    assert "applications/scripts/mcel-surface-fit-contract.js" in shell
    assert shell.index("applications/scripts/mcel-surface-fit-contract.js") < shell.index("applications/scripts/mcel-document-editor-surface.js")
    assert shell.index("applications/scripts/mcel-surface-fit-contract.js") < shell.index("applications/scripts/mcel-app-surface-conformance.js")
    assert shell.index("applications/scripts/mcel-surface-fit-contract.js") < shell.index("applications/scripts/mcel-self-diagnosis.js")


def test_fit_contract_defines_shared_policy_tokens_and_no_silent_clip_rule() -> None:
    result = run_node_json(
        load_fit_contract_stack(
            """
            const normalized = fit.normalizePolicy("wrap truncate compact-icon");
            console.log(JSON.stringify({
              version: fit.contractVersion,
              policies: Object.keys(fit.POLICY_DEFINITIONS).sort(),
              normalized,
              rule: fit.HARD_NO_SILENT_CLIP.join("\\n")
            }));
            """
        )
    )

    assert result["version"] == "mcel.surface-fit-contract.v1"
    for token in ["wrap", "truncate", "scroll", "compact", "compact-icon", "collapse-optional", "overlay", "decorative", "ignore-hidden"]:
        assert token in result["policies"]
    assert result["normalized"]["valid"] is True
    assert result["normalized"]["knownTokens"] == ["wrap", "truncate", "compact-icon"]
    assert "Silent clipping is never an allowed fit policy" in result["rule"]


def test_fit_contract_allows_only_declared_accessible_overflow_modes() -> None:
    result = run_node_json(
        load_fit_contract_stack(
            """
            function element(policy, attrs = {}) {
              return {
                id: attrs.id || "",
                className: attrs.className || "",
                tagName: "BUTTON",
                getAttribute(name) {
                  if (name === "data-mcel-fit-policy") return policy;
                  return attrs[name] || "";
                },
                closest() { return null; }
              };
            }
            const compactIcon = element("compact-icon", {"aria-label": "Refresh"});
            const badCompactIcon = element("compact-icon");
            const truncate = element("truncate");
            const scroll = element("scroll");
            console.log(JSON.stringify({
              compactIcon: fit.allowsContentOverflow(compactIcon, {
                horizontalClipped: true,
                styles: {fontSize: "0px"}
              }),
              badCompactIcon: fit.allowsContentOverflow(badCompactIcon, {
                horizontalClipped: true,
                styles: {fontSize: "0px"}
              }),
              truncate: fit.allowsContentOverflow(truncate, {
                horizontalClipped: true,
                styles: {overflowX: "hidden", textOverflow: "ellipsis"}
              }),
              badTruncate: fit.allowsContentOverflow(truncate, {
                horizontalClipped: true,
                styles: {overflowX: "visible", textOverflow: "clip"}
              }),
              scrollY: fit.allowsContentOverflow(scroll, {
                verticalClipped: true,
                styles: {overflowY: "auto"}
              }),
              badScrollY: fit.allowsContentOverflow(scroll, {
                verticalClipped: true,
                styles: {overflowY: "hidden"}
              })
            }));
            """
        )
    )

    assert result == {
        "compactIcon": True,
        "badCompactIcon": False,
        "truncate": True,
        "badTruncate": False,
        "scrollY": True,
        "badScrollY": False,
    }


def test_self_diagnosis_delegates_content_fit_policy_to_shared_contract() -> None:
    diagnosis = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")
    conformance = CONFORMANCE_JS.read_text(encoding="utf-8")

    assert "surfaceFitApi" in diagnosis
    assert "McelSurfaceFitContract" in diagnosis
    assert "contentFitPolicyInfoFor(el)" in diagnosis
    assert "surfaceFitApi.policyForElement(el)" in diagnosis
    assert "surfaceFitApi.allowsContentOverflow" in diagnosis
    assert "summarizeSurfaceFitContract()" in diagnosis
    assert "report.measurements.fitContract = snapshot.fitContract" in diagnosis
    assert "fitPolicyKnownTokens" in diagnosis
    assert "fitPolicyUnknownTokens" in diagnosis
    assert "fitContractVersion" in conformance


def test_document_and_file_explorer_surfaces_use_shared_fit_policy_helper() -> None:
    document_surface = DOCUMENT_SURFACE_JS.read_text(encoding="utf-8")
    file_surface = FILE_EXPLORER_SURFACE_JS.read_text(encoding="utf-8")
    document_html = DOCUMENT_HTML.read_text(encoding="utf-8")

    for text in [document_surface, file_surface]:
        assert "McelSurfaceFitContract" in text
        assert "function applyFitPolicy" in text
        assert "fitContractApi.applyFitPolicy" in text

    assert 'data-mcel-fit-role="document-library-header-control"' in document_html
    assert 'data-mcel-fit-required="true"' in document_html
    assert 'applyFitPolicy(scope.querySelector("#file-explorer-path"), "wrap"' in file_surface
    assert 'applyFitPolicy(scope.querySelector(".file-explorer-roots-panel"), "scroll"' in file_surface
    assert 'applyFitPolicy(decorated, "truncate"' in file_surface


def test_fit_contract_documentation_exists() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "MCEL surface fit contract" in text
    assert "micro fit" in text
    assert "Required readable/control content may not silently clip" in text
    assert "wrap" in text
    assert "compact-icon" in text
