from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
APP_SHELL = ROOT / "main_computer" / "web" / "applications.html"
PREVIEW = SCRIPTS / "mcel-surface-preview-contract.js"
DOC = ROOT / "pretty_docs" / "mcel-surface-preview-contract.md"


def run_node(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; surface preview contract smoke test cannot run")
    result = subprocess.run([node, "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def load_preview_stack(body: str) -> str:
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
          "mcel-surface-roundtrip.js",
          "mcel-surface-renderer-interface.js",
          "mcel-authored-surface-document.js",
          "mcel-neutral-surface-demo.js",
          "mcel-surface-preview-contract.js"
        ]) {{
          vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"), sandbox, {{filename: name}});
        }}
        const demo = sandbox.McelNeutralSurfaceDemo;
        const preview = sandbox.McelSurfacePreviewContract;
        {body}
        """
    )


def test_surface_preview_contract_module_exists_and_is_domain_neutral() -> None:
    script = PREVIEW.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")
    assert "mcel.surface-preview-contract.v1" in script
    assert "renderPreview" in script
    assert "renderPreviewPair" in script
    assert "McelAuthoredSurfaceDocument" in script
    assert "McelSurfaceRendererInterface" in script
    assert "Health" not in script
    assert "BIO_HEALTH" not in script
    assert "surface preview contract" in doc.lower()


def test_app_shell_loads_preview_contract_after_dependencies() -> None:
    html = APP_SHELL.read_text(encoding="utf-8")
    order = [
        "mcel-surface-roundtrip.js",
        "mcel-surface-renderer-interface.js",
        "mcel-authored-surface-document.js",
        "mcel-surface-preview-contract.js",
        "mcel-code-editor-surface-diagnostics.js",
    ]
    positions = [html.index(name) for name in order]
    assert positions == sorted(positions)


def test_plain_text_is_not_applicable_not_failure() -> None:
    script = load_preview_stack(
        """
        const report = preview.renderPreview({sourceText: "const x = 1;"});
        process.stdout.write(JSON.stringify({
          status: report.status,
          valid: report.valid,
          previewable: report.previewable,
          renderedText: report.renderedText,
          summary: report.summary
        }));
        """
    )
    data = run_node(script)
    assert data == {
        "status": "not-applicable",
        "valid": True,
        "previewable": False,
        "renderedText": "",
        "summary": "MCEL Preview: not applicable",
    }


def test_preview_contract_renders_explicit_surface_ir_to_html() -> None:
    script = load_preview_stack(
        """
        const surfaceIR = demo.createNeutralDemoSurfaceIR();
        const layoutGrammar = demo.createNeutralDemoLayoutGrammar();
        const report = preview.renderPreview({
          surfaceIR,
          layoutGrammar,
          renderer: demo.htmlRenderer(),
          surfaceKind: "html"
        });
        process.stdout.write(JSON.stringify({
          status: report.status,
          valid: report.valid,
          surfaceKind: report.surfaceKind,
          previewable: report.previewable,
          hasRendererRidge: report.renderedText.includes('data-mcel-renderer="mcel.neutral-demo.html-renderer.v1"'),
          hasProjectionRidge: report.renderedText.includes('data-mcel-projection="html"'),
          summary: report.summary
        }));
        """
    )
    data = run_node(script)
    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["surfaceKind"] == "html"
    assert data["previewable"] is True
    assert data["hasRendererRidge"] is True
    assert data["hasProjectionRidge"] is True
    assert data["summary"].startswith("MCEL Preview: PASS")


def test_preview_contract_can_repreview_authored_html_as_svg() -> None:
    script = load_preview_stack(
        """
        const sourceHtml = demo.renderNeutralDemoHtml({
          surfaceIR: demo.createNeutralDemoSurfaceIR(),
          layoutGrammar: demo.createNeutralDemoLayoutGrammar(),
          profile: demo.htmlProfile(),
          surfaceKind: "html"
        });
        const report = preview.renderPreview({
          sourceText: sourceHtml,
          renderer: demo.svgRenderer(),
          surfaceKind: "svg"
        });
        process.stdout.write(JSON.stringify({
          status: report.status,
          valid: report.valid,
          documentKind: report.analysis.documentKind,
          analysisValid: report.analysis.valid,
          surfaceKind: report.surfaceKind,
          hasSvg: report.renderedText.startsWith("<svg"),
          hasProjectionRidge: report.renderedText.includes('data-mcel-projection="svg"')
        }));
        """
    )
    data = run_node(script)
    assert data == {
        "status": "pass",
        "valid": True,
        "documentKind": "html",
        "analysisValid": True,
        "surfaceKind": "svg",
        "hasSvg": True,
        "hasProjectionRidge": True,
    }


def test_preview_contract_verifies_html_svg_pair_agreement() -> None:
    script = load_preview_stack(
        """
        const report = preview.renderPreviewPair({
          surfaceIR: demo.createNeutralDemoSurfaceIR(),
          layoutGrammar: demo.createNeutralDemoLayoutGrammar(),
          htmlRenderer: demo.htmlRenderer(),
          svgRenderer: demo.svgRenderer()
        });
        process.stdout.write(JSON.stringify({
          status: report.status,
          valid: report.valid,
          htmlStatus: report.htmlResult.status,
          svgStatus: report.svgResult.status,
          agreementStatus: report.agreement.status,
          htmlContains: report.htmlResult.renderedText.includes("data-mcel-projection=\\\"html\\\""),
          svgContains: report.svgResult.renderedText.includes("data-mcel-projection=\\\"svg\\\"")
        }));
        """
    )
    data = run_node(script)
    assert data == {
        "status": "pass",
        "valid": True,
        "htmlStatus": "pass",
        "svgStatus": "pass",
        "agreementStatus": "pass",
        "htmlContains": True,
        "svgContains": True,
    }


def test_previewable_surface_without_renderer_reports_structured_failure() -> None:
    script = load_preview_stack(
        """
        const report = preview.renderPreview({
          surfaceIR: demo.createNeutralDemoSurfaceIR(),
          layoutGrammar: demo.createNeutralDemoLayoutGrammar(),
          surfaceKind: "html"
        });
        process.stdout.write(JSON.stringify({
          status: report.status,
          valid: report.valid,
          codes: report.diagnostics.map((item) => item.code)
        }));
        """
    )
    data = run_node(script)
    assert data["status"] == "fail"
    assert data["valid"] is False
    assert "surface-preview-missing-renderer" in data["codes"]
