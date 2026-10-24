"""Disposable MCEL tab-workspace preview from official compiled DSL/IR.

Produces standalone offline HTML with shared MCEL runtime/style defaults. No host
application files are modified, and no host operational authority is available.
"""
from __future__ import annotations

import hashlib
import json
from html import escape
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_dsl_compiler import compile_dsl_application

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STYLES = REPOSITORY_ROOT / "main_computer/web/applications/styles/mcel-tab-workspace.css"
TRANSITIONS = REPOSITORY_ROOT / "main_computer/web/applications/scripts/mcel-view-transition.js"
RENDERER = REPOSITORY_ROOT / "main_computer/web/applications/scripts/mcel-tab-workspace.js"


class NotebookProjectionRefused(ValueError):
    """Invalid notebook schema or proof/authority boundary."""


def _embed_json(value: Any) -> str:
    # Prevent JSON text from ending the inert script element.
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")


def _validate_projection_scope(ir: Mapping[str, Any]) -> None:
    report = validate_application_ir(ir)
    if not report.valid:
        raise NotebookProjectionRefused("IR invalid: " + ", ".join(d.code for d in report.diagnostics))
    surfaces = [s for s in ir.get("surfaces", []) if s.get("semanticSurface", {}).get("tabGroup")]
    if len(surfaces) != 1:
        raise NotebookProjectionRefused("Exactly one declared tab workspace is required")
    group = surfaces[0]["semanticSurface"]["tabGroup"]
    if not group.get("tabs") or group.get("role") != "single-selection-view-state":
        raise NotebookProjectionRefused("No supported tab group")
    if group.get("inactivePanelPolicy") != "hidden-but-model-preserved":
        raise NotebookProjectionRefused("Unsupported inactive panel policy")
    if ir.get("effects") or ir.get("capabilities") or any(
        intent.get("writes") or intent.get("effectRefs") or intent.get("lane") != "local-view-state"
        for intent in ir.get("intents", [])
    ):
        raise NotebookProjectionRefused("Preview cannot project operational authority")
    if len(ir.get("intents", [])) != 1 or len(ir.get("states", [])) < 1:
        raise NotebookProjectionRefused("Unsupported notebook transition state")


def render_notebook_html(ir: Mapping[str, Any], *, fixture: Mapping[str, Any] | None = None) -> str:
    """Project a checked IR using only shared library-owned UI and style code."""
    _validate_projection_scope(ir)
    if fixture is not None and not isinstance(fixture, Mapping):
        raise NotebookProjectionRefused("Sample fixture must be a read-only object")
    options = {"snapshot": dict(fixture or {}), "status": "available" if fixture is not None else "not-requested",
               "instanceId": "mcel-generated-notebook", "syncRoute": True}
    js = "\n".join((TRANSITIONS.read_text(encoding="utf-8"),
                    RENDERER.read_text(encoding="utf-8")))
    css = STYLES.read_text(encoding="utf-8")
    if "</script" in js.lower() or "</style" in css.lower():
        raise NotebookProjectionRefused("Shared runtime/styles contain unsafe inline closing tags")
    title = str(ir.get("application", {}).get("title") or "MCEL Notebook")
    digest = hashlib.sha256(_embed_json(ir).encode("utf-8")).hexdigest()
    return f'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'">
<title>{escape(title)} · MCEL generated preview</title>
<style>body {{margin:0;background:#091322;min-height:100vh;padding:clamp(12px,3vw,32px);box-sizing:border-box}}
#preview {{max-width:1220px;margin:0 auto}}\n{css}</style></head><body>
<main id="preview"></main>
<script type="application/json" id="mcel-ir">{_embed_json(ir)}</script>
<script type="application/json" id="mcel-fixture">{_embed_json(options)}</script>
<script>{js}</script>
<script>
"use strict";
// No host-global runtime: this standalone page has no task-manager backend access.
const ir = JSON.parse(document.getElementById("mcel-ir").textContent);
const config = JSON.parse(document.getElementById("mcel-fixture").textContent);
window.McelNotebookPreview = McelTabWorkspace.mount(document.getElementById("preview"), ir, config);
</script>
<!-- MCEL generated notebook preview; IR SHA256: {digest}; read-only and not release-certified -->
</body></html>'''


def compile_notebook_preview(source: Path, *, fixture_path: Path | None = None) -> str:
    report = compile_dsl_application(source)
    if not report.valid or not report.normalized_ir:
        diagnostics = ", ".join(d.get("code", "unknown") for d in report.diagnostics)
        raise NotebookProjectionRefused("DSL compilation failed: " + diagnostics)
    fixture = None
    if fixture_path is not None:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise NotebookProjectionRefused("Fixture must be a JSON object")
        fixture = data
    return render_notebook_html(report.normalized_ir, fixture=fixture)
