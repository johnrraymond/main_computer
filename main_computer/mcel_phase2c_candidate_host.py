"""Opt-in hosted semantic Calculator candidate.

Uses the *same* compiled paired-source IR and domain Calculator core as the
existing app, without authoring a second UI. Never replaces /applications/calculator.
No capability discovery, arbitrary external execution, or filesystem access.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("tools/fixtures/mcel_phase2_reference_apps/calculator/application.js")
CORE = Path("main_computer/web/applications/scripts/calculator-core.js")
RUNTIME = Path("main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js")
PORT = Path("main_computer/web/applications/scripts/mcel-calculator-core-capability-port.js")
ROUTE = "/applications/mcel-semantic-calculator-candidate"


class CandidateHostError(RuntimeError):
    pass


def compile_candidate(repo: Path = ROOT) -> dict:
    """Compile the real two-file DSL candidate; reject authority drift."""
    result = compile_dsl_application(repo / SOURCE)
    if not result.valid or result.normalized_ir is None:
        raise CandidateHostError(f"Paired Calculator source invalid: {result.diagnostics}")
    ir = result.normalized_ir
    if ir.get("application", {}).get("appId") != "phase2-calculator":
        raise CandidateHostError("unexpected candidate app identity")
    if {i.get("id") for i in ir.get("intents", [])} != {"intent:compute", "intent:switch-mode"}:
        raise CandidateHostError("candidate intent allowlist changed")
    if (len(ir.get("capabilities", [])) != 1 or
        ir["capabilities"][0].get("id") != "capability:arithmetic" or
        ir["capabilities"][0].get("risk") != "external-read"):
        raise CandidateHostError("candidate capability authority changed")
    if len(ir.get("effects", [])) != 1 or ir["effects"][0].get("risk") != "external-read":
        raise CandidateHostError("candidate effect authority changed")
    compute = next(i for i in ir["intents"] if i["id"] == "intent:compute")
    cap = ir["capabilities"][0]
    effect = ir["effects"][0]
    if (compute.get("operationKind") != "capability" or
        compute.get("operation") != "compute" or
        compute.get("runtimeMethod") != "evaluateExpression" or
        compute.get("risk") != "external-read" or
        compute.get("effectRefs") != [{"ref": "effect:compute.request"}] or
        effect.get("id") != "effect:compute.request" or
        effect.get("effectKind") != "capability-request" or
        effect.get("owner") != {"ref": "intent:compute"} or
        effect.get("authority") != {"ref": "capability:arithmetic"} or
        effect.get("target") != {"ref": "capability:arithmetic"} or
        len(cap.get("operations", [])) != 1 or
        cap["operations"][0].get("name") != "compute" or
        cap["operations"][0].get("runtimeMethod") != "evaluateExpression" or
        cap["operations"][0].get("risk") != "external-read"):
        raise CandidateHostError("candidate operation/effect authority changed")
    if not ir.get("surfaces", [{}])[0].get("interfaceIntent", {}).get("hierarchy"):
        raise CandidateHostError("candidate has no authored hierarchy")
    if ir.get("layouts"):
        raise CandidateHostError("candidate must not supply a layout tree")
    return ir


def render_candidate_page(repo: Path = ROOT) -> str:
    """Deterministic, self-contained local candidate page for real viewport GET."""
    ir = compile_candidate(repo)
    def asset(path: Path) -> str:
        data = (repo / path).read_text(encoding="utf-8")
        # Do not allow an inline script close tag even from a changed repository asset.
        if "</script" in data.lower():
            raise CandidateHostError("inline asset contains script termination")
        return "<script>\n" + data + "\n</script>"

    payload = json.dumps(ir, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    # HTML is not used as a second app layout: just a separate test mount host.
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCEL Semantic Calculator Candidate</title>
<style>
body{font-family:system-ui,sans-serif;background:Canvas;color:CanvasText;margin:1rem auto;padding:0 1rem;max-width:52rem}
form{display:flex;gap:.6rem;flex-wrap:wrap;align-items:end}label{display:grid;gap:.3rem}
button,input,select{font:inherit;padding:.4rem}button{cursor:pointer}
[data-mcel-region]{margin:.4rem 0;padding:.55rem;border:1px solid #8885;border-radius:.4rem}
[data-mcel-purpose="input"]{min-height:5rem} output{font-size:1.4rem} [data-mcel-node]{margin:.35rem}
</style></head><body>
<header><h1>MCEL Semantic Calculator Candidate</h1><p>Opt-in compiler/runtime integration test; production Calculator unchanged.</p></header>
<div id="mcel-phase2c-app" aria-label="MCEL generated Calculator"></div>
<div id="mcel-phase2c-diagnostic" role="status" aria-live="polite"></div>
<script type="application/json" id="mcel-phase2c-ir">""" + payload + "</script>\n" +         asset(CORE) + "\n" + asset(RUNTIME) + "\n" + asset(PORT) + "\n" +         """<script>
(function(){"use strict";
  const status=document.getElementById("mcel-phase2c-diagnostic");
  try {
    const ir=JSON.parse(document.getElementById("mcel-phase2c-ir").textContent);
    const port=McelCalculatorCoreCapabilityPort.create({core:MainComputerCalculatorCore});
    const app=McelSemanticOperationalRuntime.mount(document.getElementById("mcel-phase2c-app"),ir,{capabilities:port});
    window.__mcelPhase2CCandidate=Object.freeze({app,appId:ir.application.appId});
    status.textContent="Semantic Calculator candidate mounted";
  } catch(error) {
    status.textContent="Candidate refused: "+String(error?.message||error);
    status.dataset.error="true";
  }
})();
</script></body></html>"""
