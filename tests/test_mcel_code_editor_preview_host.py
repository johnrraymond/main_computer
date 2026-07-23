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
HTML = WEB / "apps" / "code-editor.html"
CSS = WEB / "styles" / "code-editor.css"
PREVIEW_HOST = SCRIPTS / "mcel-code-editor-preview-host.js"
DOC = ROOT / "pretty_docs" / "mcel-code-editor-preview-host.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; Code Editor MCEL preview host smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_preview_host_stack(body: str) -> str:
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
          "mcel-surface-preview-contract.js",
          "mcel-code-editor-preview-host.js"
        ]) {{
          vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"), sandbox, {{filename: name}});
        }}
        const demo = sandbox.McelNeutralSurfaceDemo;
        const host = sandbox.McelCodeEditorPreviewHost;
        {body}
        """
    )


def test_code_editor_preview_host_files_are_wired() -> None:
    assert PREVIEW_HOST.exists()
    assert DOC.exists()

    app_shell = APP_SHELL.read_text(encoding="utf-8")
    assert "mcel-surface-preview-contract.js" in app_shell
    assert "mcel-code-editor-authoring-status.js" in app_shell
    assert "mcel-code-editor-preview-host.js" in app_shell

    preview_idx = app_shell.index("mcel-surface-preview-contract.js")
    host_idx = app_shell.index("mcel-code-editor-preview-host.js")
    assert preview_idx < host_idx


def test_code_editor_preview_status_chip_is_hidden_by_default() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert 'id="code-editor-mcel-preview-status"' in html
    assert 'hidden aria-live="polite"' in html
    assert 'data-mcel-preview-status="selected-file"' in html
    assert 'data-mcel-node-id="code-editor.node.mcel-preview-status-chip"' in html
    assert 'data-mcel-provenance="patch:mcel-safe-16-preview-host"' in html


def test_code_editor_preview_css_is_bounded_and_lazy_opened() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert ".code-editor-mcel-preview-status[hidden]" in css
    assert "display: none !important" in css
    assert ".code-editor-mcel-preview-popover" in css
    assert "width: min(520px, calc(100vw - 32px))" in css
    assert "max-height: min(540px, calc(100vh - 96px))" in css
    assert ".code-editor-mcel-preview-popover__frame" in css


def test_code_editor_preview_host_uses_reusable_mcel_preview_contract() -> None:
    script = PREVIEW_HOST.read_text(encoding="utf-8")
    assert "McelSurfacePreviewContract" in script
    assert ".renderPreview(" in script
    assert "McelAuthoredSurfaceDocument" in script
    assert "McelCodeEditorAuthoringStatus" in script
    assert "createPreviewRenderer" in script
    assert "data-mcel-renderer" in script
    assert "data-mcel-projection" in script


def test_code_editor_preview_host_round_trips_neutral_authored_surface() -> None:
    script = load_preview_host_stack(
        """
        const sourceText = demo.renderNeutralDemoHtml();
        const report = host.renderPreviewForSource({sourceText});
        process.stdout.write(JSON.stringify({
          status: report.status,
          valid: report.valid,
          previewable: report.previewable,
          summary: report.summary,
          renderer: report.renderResult && report.renderResult.profile && report.renderResult.profile.id,
          includesAttribution: (report.renderedText || "").includes('data-mcel-renderer="code-editor.preview.debug-html"'),
          includesProjection: (report.renderedText || "").includes('data-mcel-projection="html"'),
          diagnosticCodes: (report.diagnostics || []).map((item) => item.code)
        }));
        """
    )
    result = run_node_json(script)
    assert result["status"] == "pass"
    assert result["valid"] is True
    assert result["previewable"] is True
    assert result["renderer"] == "code-editor.preview.debug-html"
    assert result["includesAttribution"] is True
    assert result["includesProjection"] is True
    assert result["diagnosticCodes"] == []


def test_code_editor_preview_host_hides_for_non_mcel_source() -> None:
    script = load_preview_host_stack(
        """
        const report = host.renderPreviewForSource({sourceText: "function add(a, b) { return a + b; }"});
        const model = host.normalizePreviewReport(report);
        process.stdout.write(JSON.stringify({
          status: report.status,
          valid: report.valid,
          previewable: report.previewable,
          visible: model.visible,
          value: model.value
        }));
        """
    )
    result = run_node_json(script)
    assert result["status"] == "not-applicable"
    assert result["valid"] is True
    assert result["previewable"] is False
    assert result["visible"] is False
    assert result["value"] == "no preview"
