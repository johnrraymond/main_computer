"""Canonical, standalone MCEL Chat Console host.

The MCEL-authored hierarchy is authoritative for application composition.
Existing Chat Console markup and operational adapters remain shared with
Calculator's embedded mount, avoiding a new notebook implementation or store.
No provider or local effects are exercised while constructing this page.
"""
from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_phase2c_candidate_host import ROOT, CandidateHostError

SOURCE = Path("tools/fixtures/mcel_semantic_chat_console/application.js")
TEMPLATE = Path("main_computer/web/applications/apps/chat-console.html")
STYLES = (
    "main_computer/web/applications/styles/base.css",
    "main_computer/web/applications/styles/chat-console.css",
    "main_computer/web/applications/styles/mcel-semantic-chat-console.css",
)
SCRIPTS = (
    "main_computer/web/applications/scripts/calculator-core.js",
    "main_computer/web/applications/scripts/dom-bindings/chat-console.js",
    "main_computer/web/applications/scripts/chat-thread-store.js",
    "main_computer/web/applications/scripts/chat-thread-controller.js",
    "main_computer/web/applications/scripts/chat-console-state.js",
    "main_computer/web/applications/scripts/chat-console-code-runtime.js",
    "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js",
    "main_computer/web/applications/scripts/mcel-semantic-chat-console.js",
    "main_computer/web/applications/scripts/chat-console.js",
)
REQUIRED_REGIONS = frozenset({"application", "shell", "header", "status", "thread-layout", "threads", "workspace", "notebook"})
# Bind the MCEL-authored region IDs to the actual, previously accepted
# notebook controls. Presentation is semantic; execution stays in the shared
# Chat Console library used by embedded Calculator.
REGION_CLASSES = {
    "application": "chat-console-app",
    "shell": "chat-console-shell",
    "header": "chat-console-header",
    "status": "chat-console-save-status",
    "thread-layout": "chat-console-thread-layout",
    "threads": "chat-thread-rail",
    "workspace": "chat-thread-workspace",
    "notebook": "chat-console-notebook",
}
WORKERS = (
    "chat-console-js-worker.js",
    "chat-console-python-worker.js",
    "chat-console-basic-worker.js",
)


def compile_chat_console(repo: Path = ROOT) -> dict:
    result = compile_dsl_application(repo / SOURCE)
    if not result.valid or result.normalized_ir is None:
        raise CandidateHostError(f"Chat Console semantic source invalid: {result.diagnostics}")
    ir = result.normalized_ir
    if ir.get("application", {}).get("appId") != "semantic-chat-console":
        raise CandidateHostError("Chat Console identity changed")
    # P3 authors the layout; P4 will mount it. Reject unrelated layouts and
    # any attempt to grant new notebook execution authority from the source.
    layouts = ir.get("layouts") or []
    if (len(layouts) != 1 or layouts[0].get("surface") != {"ref": ir["surfaces"][0]["id"]}
        or layouts[0].get("layoutTree", {}).get("schema") != "mcel.layout-tree.v1"
        or layouts[0]["layoutTree"].get("root", {}).get("selector") != ".chat-console-shell"):
        raise CandidateHostError("Chat Console canonical layout tree is missing or unrelated")
    if any(ir.get(key) for key in ("capabilities", "intents", "effects")):
        raise CandidateHostError("Chat Console layout must not grant execution authority")
    surfaces = ir.get("surfaces") or []
    if len(surfaces) != 1 or not surfaces[0].get("interfaceIntent", {}).get("hierarchy"):
        raise CandidateHostError("Missing semantic Chat Console hierarchy")
    actual = {r["id"].split(".")[-1] for r in surfaces[0]["interfaceIntent"]["regions"]}
    if actual != REQUIRED_REGIONS:
        raise CandidateHostError("Chat Console required semantic regions changed")
    return ir


def _inline_script(source: str) -> str:
    if "</script" in source.lower():
        raise CandidateHostError("Unsafe inline Chat Console script")
    return "<script>\n" + source + "\n</script>"


def render_chat_console_page(repo: Path = ROOT, *, promoted: bool = False) -> str:
    ir = compile_chat_console(repo)
    template = (repo / TEMPLATE).read_text(encoding="utf-8")
    # The existing shared notebook markup is intentionally retained for exact
    # thread IDs, cell controls, worker sources and persistent storage keys.
    region_ids = {region["id"].split(".")[-1]: region["id"]
                  for region in ir["surfaces"][0]["interfaceIntent"]["regions"]}
    for name, css_class in REGION_CLASSES.items():
        pattern = re.compile(r'<([a-z][a-z0-9]*)\b([^>]*\bclass="[^"]*\b' + re.escape(css_class) + r'(?=\s|")[^"]*"[^>]*)>', re.I)
        matches = list(pattern.finditer(template))
        if len(matches) != 1 or name not in region_ids:
            raise CandidateHostError(f"Chat Console region did not bind uniquely: {name}")
        match = matches[0]
        replacement = f'<{match.group(1)}{match.group(2)} data-mcel-region-id="{region_ids[name]}">'
        template = template[:match.start()] + replacement + template[match.end():]
    for worker in WORKERS:
        marker = f"<!-- @include applications/scripts/{worker} -->"
        if template.count(marker) != 1:
            raise CandidateHostError(f"Chat Console worker template changed: {worker}")
        worker_js = (repo / "main_computer/web/applications/scripts" / worker).read_text(encoding="utf-8")
        if "</script" in worker_js.lower():
            raise CandidateHostError(f"Unsafe Chat Console worker: {worker}")
        template = template.replace(marker, worker_js)
    if "<!-- @include" in template:
        raise CandidateHostError("Unexpanded Chat Console template includes")
    if template.count('id="chat-console-app"') != 1 or template.count('id="chat-console-notebook"') != 1:
        raise CandidateHostError("Chat Console template mount identity changed")
    css = "\n".join((repo / p).read_text(encoding="utf-8") for p in STYLES)
    if "</style" in css.lower():
        raise CandidateHostError("Unsafe inline Chat Console stylesheet")
    # In the legacy applications shell this adapter comes from calculator.js.
    # The standalone semantic host needs only the exact shared arithmetic core,
    # not the Calculator app and its unrelated document bindings.
    calculator_adapter = _inline_script('''function evaluateCalculatorArithmeticExpression(expression) {
      return window.MainComputerCalculatorCore.evaluateCalculatorExpression(expression);
    }''')
    scripts = calculator_adapter + "\n" + "\n".join(_inline_script((repo / p).read_text(encoding="utf-8")) for p in SCRIPTS)
    payload = json.dumps(ir, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    title = "Chat Console" if promoted else "Semantic Chat Console · Candidate"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{css}
body.mcel-chat-page {{margin:0;min-width:0;background:#050605;}}
body.mcel-chat-page #chat-console-app {{display:grid;min-height:100dvh;max-width:100%;}}
body.mcel-chat-page #chat-console-app .chat-console-shell {{min-height:0;}}
</style></head><body class="mcel-chat-page">
{template}
<div id="mcel-chat-canonical-status" role="status" aria-live="polite" hidden></div>
<script id="mcel-chat-canonical-ir" type="application/json">{payload}</script>
{scripts}
<script>(function(){{"use strict";
 const status=document.getElementById("mcel-chat-canonical-status");
 try {{
   const ir=JSON.parse(document.getElementById("mcel-chat-canonical-ir").textContent);
   if(ir.application.appId!=="semantic-chat-console")throw Error("semantic-app-identity-mismatch");
   document.body.dataset.mcelShellEmbedded=window.frameElement?.id==="mcel-shell-frame"?"true":"false";
   initChatConsoleApp();
   const host=document.getElementById("chat-console-app");
   const result=window.MainComputerMCELChatConsole?.compose(host);
   if(!result || result.contract!=="mcel.chat-console.composition.v1")throw Error("semantic-chat-composition-incomplete");
   const layout=window.MainComputerMCELChatConsole.mountCanonical(host,ir);
   if(layout?.status!=="pass")throw Error("canonical-chat-layout-refused: "+String(layout?.reason||"unknown"));
   document.body.dataset.mcelChatReady="true";
   window.__mcelChatConsole=Object.freeze({{appId:ir.application.appId,contract:result.contract,
     layoutAuthority:"mcel-layout-tree.v1",semanticRegions:ir.surfaces[0].interfaceIntent.regions.length}});
 }}catch(error){{status.hidden=false;status.dataset.error="true";status.textContent="Chat Console refused: "+String(error?.message||error);}}
}})();</script></body></html>'''
