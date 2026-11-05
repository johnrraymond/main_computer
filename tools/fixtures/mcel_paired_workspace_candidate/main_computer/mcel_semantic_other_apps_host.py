"""Opt-in semantic candidates backed by existing governed backend endpoints.

The source of UI structure and authority is the paired HTML/JavaScript IR.
These are bounded operational candidates, NOT complete production migrations.
"""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.workspace_plan import plan_workspace, verify_workspace_plan
from main_computer.mcel_phase2c_candidate_host import ROOT, CandidateHostError

APPS = {
    "file-manager": ("semantic-file-manager", {"load-roots", "choose-root", "browse", "enter-directory", "search", "choose-entry", "preview-entry", "open-with"},
                     {"filesystem-read", "application-navigation"}),
    "task-manager": ("semantic-task-manager", {"refresh", "choose-process", "choose-all-process", "choose-schedule", "server-status", "switch-view", "refresh-all", "refresh-connections", "refresh-hardware", "list-schedules", "task-ai", "terminate-process", "kill-process", "start-server", "restart-server", "shutdown-server", "create-schedule", "delete-schedule", "run-schedule"}, {"monitor", "task-control", "task-scheduling"}),
    "code-editor": ("semantic-code-editor", {"list-files", "select-file", "open-source", "browse-directory", "save-source", "prepare-reviewed-change", "apply-reviewed-change", "preview-aider-plan", "aider-context", "aider-jobs", "run-aider", "archive-aider-context", "reset-aider-context", "load-aider-archive", "list-component-docs", "choose-component-doc", "read-component-doc", "read-component-doc-viewport-config", "run-component-doc-script", "inspect-scm-component-contract", "inspect-scm-route-contract", "check-studio-draft", "preview-studio-serialization", "studio-runtime-mount", "studio-runtime-validate", "studio-runtime-serialize", "studio-runtime-repair", "studio-runtime-stage", "studio-runtime-commit", "studio-runtime-route-enter", "studio-runtime-route-leave", "studio-runtime-evidence", "studio-runtime-replay", "studio-proof-list", "studio-proof-select", "studio-proof-inspect", "studio-proof-export", "studio-regression-run", "studio-repair-packet"}, {"source-reader", "source-writer", "reviewer", "review-applier", "aider-preview", "aider-context", "aider-control", "component-docs", "sandboxed-viewport", "scm-definition", "studio-source-tools", "scm-runtime", "scm-proof"}),
}
ROUTE_PREFIX = "/applications/mcel-semantic-"
SOURCE_ROOT = Path("tools/fixtures/mcel_semantic_other_apps")
DIAGNOSTIC_ASSETS = (
    "main_computer/web/applications/scripts/mcel-diagnostic-early-bootstrap.js",
    "main_computer/web/applications/scripts/mcel-diagnostic-plugins.js",
    "main_computer/web/applications/scripts/mcel-app-parity-trace-plugin.js",
)
ASSETS = (
    "main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js",
    "main_computer/web/applications/scripts/mcel-semantic-read-ports.js",
    "main_computer/web/applications/scripts/code-editor-monaco-adapter.js",
)
WORKSPACE_LAYOUT_ASSET = "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js"
CSS = "main_computer/web/applications/styles/mcel-semantic-workbench.css"


def compile_semantic_app(name: str, repo: Path = ROOT) -> dict:
    if name not in APPS:
        raise CandidateHostError("Unknown semantic application")
    app_id, expected_intents, expected_caps = APPS[name]
    source = repo / SOURCE_ROOT / name / "application.js"
    report = compile_dsl_application(source)
    if not report.valid or report.normalized_ir is None:
        raise CandidateHostError(f"Invalid paired source for {name}: {report.diagnostics}")
    ir = report.normalized_ir
    if ir.get("application", {}).get("appId") != app_id or ir.get("layouts"):
        raise CandidateHostError("Unexpected application identity or authored layout")
    surface = ir.get("surfaces", [])
    if len(surface) != 1 or not surface[0].get("interfaceIntent", {}).get("hierarchy"):
        raise CandidateHostError("Missing semantic HTML hierarchy")
    intents = {x["id"]: x for x in ir.get("intents", [])}
    caps = {x["id"]: x for x in ir.get("capabilities", [])}
    effects = {x["id"]: x for x in ir.get("effects", [])}
    if set(intents) != {"intent:" + x for x in expected_intents} or set(caps) != {"capability:" + x for x in expected_caps}:
        raise CandidateHostError("Declared intents/capabilities diverge from candidate allowance")
    if any(cap.get("risk") != ("consequential" if (name=="task-manager" and cap["id"] in {"capability:task-control","capability:task-scheduling"}) or (name=="code-editor" and cap["id"] == "capability:aider-control") else "source-write" if name=="code-editor" and cap["id"] in {"capability:source-writer","capability:review-applier"} else "external-read") for cap in caps.values()):
        raise CandidateHostError("Candidate elevated capability risk")
    for intent in intents.values():
        if intent.get("operationKind") != "capability":
            if intent.get("operationKind") != "interaction" or intent.get("risk") != "local-state" or intent.get("effectRefs"):
                raise CandidateHostError("Unexpected non-read operation")
            continue
        expected_risk = "consequential" if (name=="task-manager" and intent["id"] in {"intent:terminate-process","intent:kill-process","intent:start-server","intent:restart-server","intent:shutdown-server","intent:create-schedule","intent:delete-schedule","intent:run-schedule"}) or (name=="code-editor" and intent["id"] in {"intent:run-aider","intent:archive-aider-context","intent:reset-aider-context","intent:load-aider-archive"}) else "source-write" if name=="code-editor" and intent["id"] in {"intent:save-source","intent:apply-reviewed-change"} else "external-read"
        if intent.get("risk") != expected_risk or len(intent.get("effectRefs", [])) != 1:
            raise CandidateHostError("Operation risk/effect invalid")
        effect = effects.get(intent["effectRefs"][0]["ref"])
        if not effect or effect.get("effectKind") != "capability-request" or effect.get("risk") != expected_risk or effect.get("owner") != {"ref": intent["id"]}:
            raise CandidateHostError("Invalid effect authority")
        cap = caps.get(effect.get("authority", {}).get("ref"))
        if not cap or effect.get("target") != {"ref": cap["id"]} or not any(
            op.get("name") == intent.get("operation") and op.get("runtimeMethod") == intent.get("runtimeMethod") and op.get("risk") == expected_risk
            for op in cap.get("operations", [])
        ):
            raise CandidateHostError("Capability operation missing or mismatched")
    if len(effects) != sum(i.get("operationKind") == "capability" for i in intents.values()):
        raise CandidateHostError("Undeclared effects detected")
    return ir


def render_semantic_page(name: str, repo: Path = ROOT) -> str:
    ir = compile_semantic_app(name, repo)
    payload = json.dumps(ir, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    # First integrated rollout: one paired candidate consumes the shared
    # deterministic composition policy; other candidates remain observational.
    workspace_enabled = name == "code-editor"
    plan = plan_workspace(ir) if workspace_enabled else None
    if plan is not None:
        verify_workspace_plan(ir, plan)
    plan_payload = json.dumps(plan, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c") if plan else "null"
    def script(path):
        content = (repo / path).read_text(encoding="utf-8")
        if "</script" in content.lower():
            raise CandidateHostError("Unsafe script asset")
        return "<script>\n" + content + "\n</script>"
    style = (repo / CSS).read_text(encoding="utf-8")
    if "</style" in style.lower():
        raise CandidateHostError("Unsafe style asset")
    title = {"file-manager":"File Manager", "task-manager":"Task Manager", "code-editor":"Code Editor"}[name]
    assets = (*DIAGNOSTIC_ASSETS, *((WORKSPACE_LAYOUT_ASSET,) if workspace_enabled else ()), *ASSETS, *(("main_computer/web/applications/scripts/mcel-scm.js", "main_computer/web/applications/scripts/code-editor-scm-manifest.js", "main_computer/web/applications/scripts/mcel-sandboxed-viewport.js") if name == "code-editor" else ()))
    scripts = "\n".join(script(x) for x in assets)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCEL Semantic {title}</title><style>{style}</style></head><body class="mcel-workbench-page">
<header><h1>Semantic {title}</h1><p>Semantic candidate · Original application unchanged</p></header>
<div id="mcel-workbench" aria-label="Generated semantic {title}"></div>
<div id="mcel-workbench-status" role="status" aria-live="polite"></div>
<script type="application/json" id="mcel-workbench-ir">{payload}</script>
<script type="application/json" id="mcel-workbench-plan">{plan_payload}</script>
{scripts}
<script>(function(){{"use strict";
 const status=document.getElementById("mcel-workbench-status");
 try{{const ir=JSON.parse(document.getElementById("mcel-workbench-ir").textContent);
 const port=McelSemanticReadPorts.create({{appId:ir.application.appId}});
 const workspacePlan=JSON.parse(document.getElementById("mcel-workbench-plan").textContent);
 const app=McelSemanticOperationalRuntime.mount(document.getElementById("mcel-workbench"),ir,{{capabilities:port,presentation:"expert",workspacePlan:workspacePlan||undefined}});
 window.__mcelWorkbench=Object.freeze({{app,appId:ir.application.appId}});
 status.textContent="Semantic candidate ready";
 }}catch(e){{status.dataset.error="true";status.textContent="Semantic candidate refused: "+String(e?.message||e);}}
 }})();</script></body></html>'''
