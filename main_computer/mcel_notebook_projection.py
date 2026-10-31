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
    # Offline preview may display a declared external-read control, but it
    # injects NO capability adapter, so no host operation is executable.
    local = [i for i in ir.get("intents", []) if i.get("lane") == "local-view-state"
             and not i.get("writes") and not i.get("effectRefs")]
    if len(local) != 1 or len(ir.get("states", [])) < 1:
        raise NotebookProjectionRefused("Unsupported notebook transition state")
    operational = [i for i in ir.get("intents", []) if i not in local]
    if len(operational) != len(ir.get("effects", [])) or len(operational) != len(ir.get("capabilities", [])):
        raise NotebookProjectionRefused("Unbounded operational authority")
    controls = [c for c in surfaces[0]["semanticSurface"].get("controls", [])
                if c.get("role") == "capability-action"]
    if len(controls) != len(operational):
        raise NotebookProjectionRefused("Missing capability-action declarations")
    for intent in operational:
        if (intent.get("operationKind") != "capability" or intent.get("lane") != "external-read"
            or intent.get("risk") != "external-read" or intent.get("writes") or intent.get("input")
            or intent.get("viewWrites") or len(intent.get("effectRefs", [])) != 1
            or len([c for c in controls if c.get("intent") == intent.get("sourceName")
                    and c.get("risk") == "external-read"]) != 1):
            raise NotebookProjectionRefused("Preview cannot project unsafe operational authority")
        effect = next((e for e in ir["effects"] if e.get("id") == intent["effectRefs"][0]["ref"]), None)
        capability = next((c for c in ir["capabilities"] if c.get("id") == (effect or {}).get("target", {}).get("ref")), None)
        if (not effect or effect.get("effectKind") != "capability-request" or effect.get("risk") != "external-read"
            or effect.get("owner", {}).get("ref") != intent.get("id") or not capability
            or effect.get("authority", {}).get("ref") != capability.get("id")
            or capability.get("risk") != "external-read" or len(capability.get("operations", [])) != 1
            or capability["operations"][0].get("name") != intent.get("runtimeMethod")):
            raise NotebookProjectionRefused("Invalid preview effect graph")



def render_notebook_html(ir: Mapping[str, Any], *, fixture: Mapping[str, Any] | None = None) -> str:
    """Project a checked IR using only shared library-owned UI and style code."""
    _validate_projection_scope(ir)
    if fixture is not None and not isinstance(fixture, Mapping):
        raise NotebookProjectionRefused("Sample fixture must be a read-only object")
    options = {"snapshot": dict(fixture or {}), "status": "available" if fixture is not None else "not-requested",
               "instanceId": "mcel-generated-notebook", "syncRoute": True}
    js = "\n".join((TRANSITIONS.read_text(encoding="utf-8"),
                    (REPOSITORY_ROOT / "main_computer/web/applications/scripts/mcel-intent-effect-runtime.js").read_text(encoding="utf-8"),
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
