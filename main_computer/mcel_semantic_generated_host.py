"""P1 independent-composition read-only Task Manager development route.

No application.html, authored hierarchy, app-specific renderer or ambient actions.
An exact allowlist binds the candidate's one existing overview operation.
"""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, verify_composition

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("tools/fixtures/mcel_p1_task_monitor/application.js")
ASSETS = (
    "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js",
    "main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js",
    "main_computer/web/applications/scripts/mcel-semantic-read-ports.js",
)
STYLE = "main_computer/web/applications/styles/mcel-semantic-workbench.css"


class GeneratedHostRefusal(ValueError):
    pass


def compile_generated_candidate(repo: Path = ROOT) -> tuple[dict, dict]:
    source = repo / SOURCE
    if (source.with_name("application.html").exists()):
        raise GeneratedHostRefusal("P1 forbids silently paired HTML")
    text = source.read_text(encoding="utf-8")
    # Refuse the authoring shortcuts in this experiment, including hidden host
    # hierarchy and geometry. This is a slice-only restriction, not a DSL ban.
    import re
    if re.search(r'\b(?:bindHtml|layout\.define|surface\.region\s*\(\s*["\'](?:navigation|workspace)|layoutGrammar|parentId|selector)\b', text):
        raise GeneratedHostRefusal("P1 source must not author structural layout")
    compiled = compile_dsl_application(source)
    if not compiled.valid or not compiled.normalized_ir:
        raise GeneratedHostRefusal("Invalid DSL: " + str(compiled.diagnostics))
    ir = compiled.normalized_ir
    surface = ir.get("surfaces", [{}])[0]
    if (ir.get("application", {}).get("appId") != "semantic-task-monitor-generated" or
        len(ir.get("surfaces", [])) != 1 or ir.get("layouts") or
        surface.get("interfaceIntent") or surface.get("htmlBindings") or
        surface.get("purpose") != "monitor"):
        raise GeneratedHostRefusal("P1 candidate is not the agreed layout-free monitor")
    intents = {i["id"]: i for i in ir.get("intents", [])}
    caps = {c["id"]: c for c in ir.get("capabilities", [])}
    effects = {e["id"]: e for e in ir.get("effects", [])}
    read_lanes = {
        "refresh": ("observe", "observeProcesses"),
        "refresh-all": ("all", "observeAllProcesses"),
        "refresh-connections": ("connections", "observeConnections"),
        "refresh-hardware": ("hardware", "observeHardware"),
    }
    if (set(intents) != {"intent:" + name for name in read_lanes} |
            {"intent:choose-process", "intent:switch-view"} or
        set(caps) != {"capability:monitor"} or
        set(effects) != {"effect:" + name + ".request" for name in read_lanes}):
        raise GeneratedHostRefusal("P1 operation allowlist has changed")
    choice = intents["intent:choose-process"]
    mode = intents["intent:switch-view"]
    operations = {op.get("name"): op for op in caps["capability:monitor"].get("operations", [])}
    if (caps["capability:monitor"].get("risk") != "external-read" or
        set(operations) != {operation for operation, _ in read_lanes.values()} or
        len(caps["capability:monitor"].get("operations", [])) != len(read_lanes) or
        choice.get("risk") != "local-state" or choice.get("operationKind") != "interaction" or
        choice.get("effectRefs") or not choice.get("collectionSelection") or
        mode.get("risk") != "local-state" or mode.get("operationKind") != "interaction" or
        not mode.get("viewTransition") or mode.get("effectRefs")):
        raise GeneratedHostRefusal("P2 operation schema/authority is not the reviewed contract")
    for name, (operation, method) in read_lanes.items():
        intent = intents["intent:" + name]
        effect = effects["effect:" + name + ".request"]
        op = operations[operation]
        if (intent.get("risk") != "external-read" or intent.get("operation") != operation or
            intent.get("runtimeMethod") != method or
            intent.get("effectRefs") != [{"ref": "effect:" + name + ".request"}] or
            len(intent.get("resultBindings", [])) != 1 or
            effect.get("risk") != "external-read" or effect.get("authority") != {"ref": "capability:monitor"} or
            effect.get("target") != {"ref": "capability:monitor"} or
            effect.get("owner") != {"ref": "intent:" + name} or
            op.get("name") != operation or op.get("runtimeMethod") != method or
            op.get("risk") != "external-read"):
            raise GeneratedHostRefusal("P2 read operation authority mismatch: " + name)
    composition = synthesize_composition(ir)
    verify_composition(ir, composition)
    return ir, composition


def render_generated_page(repo: Path = ROOT) -> str:
    ir, composition = compile_generated_candidate(repo)
    payload = json.dumps({"ir": ir, "composition": composition}, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    def asset(path: str) -> str:
        content = (repo / path).read_text(encoding="utf-8")
        if "</script" in content.lower():
            raise GeneratedHostRefusal("Untrusted script closing tag")
        return "<script>\n" + content + "\n</script>"
    css = (repo / STYLE).read_text(encoding="utf-8")
    if "</style" in css.lower():
        raise GeneratedHostRefusal("Invalid style payload")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCEL Generated Task Manager Monitor</title><style>{css}</style></head>
<body class="mcel-workbench-page"><header><h1>Generated Task Manager Monitor</h1><p>Four read-only monitoring views · MCEL-authored interface · production Task Manager unchanged</p></header>
<div id="mcel-workbench" aria-label="Generated process monitor"></div><p id="mcel-workbench-status" role="status"></p>
<script type="application/json" id="mcel-generated-data">{payload}</script>
{''.join(asset(x) for x in ASSETS)}
<script>(function(){{'use strict'; const status=document.getElementById('mcel-workbench-status');
try{{const data=JSON.parse(document.getElementById('mcel-generated-data').textContent);
const port=McelSemanticReadPorts.create({{appId:data.ir.application.appId}});
const app=McelSemanticOperationalRuntime.mount(document.getElementById('mcel-workbench'),data.ir,
 {{capabilities:port,composition:data.composition,presentation:'expert'}});
window.__mcelP1=Object.freeze({{app,appId:data.ir.application.appId,composition:data.composition}});window.__mcelP2=window.__mcelP1;
status.textContent='P2 generated monitor ready';
}}catch(error){{status.dataset.error='true';status.textContent='P1 candidate refused: '+String(error?.message||error);}}
}})();</script></body></html>'''
