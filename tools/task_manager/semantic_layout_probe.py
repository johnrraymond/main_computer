"""Read-only Task Manager design -> MCEL canonical layout proof.

Prototype only. Does not enroll, replace, deploy, or operate Task Manager.
Run: python tools/task_manager/semantic_layout_probe.py [--output PATH]
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    import yaml
except ImportError as exc:
    raise SystemExit("Task Manager layout probe requires PyYAML (python -m pip install PyYAML)") from exc

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "mcel_designs/task-manager/design.yaml"
RESOLVER = ROOT / "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js"
DEFAULT_OUTPUT = ROOT / "runtime/reports/task-manager-semantic-layout"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# General semantic-region responsibilities, not CSS coordinates or app-name selectors.
ROLE_ALIASES = {
    "status-and-attention": "attention",
    "global-context-and-search": "search",
    "primary-content": "primary",
    "selection-details": "inspector",
    "supporting-tools": "secondary",
    "persistent-nonblocking-feedback": "feedback",
}


def load_design(path: Path) -> dict:
    design = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(design, dict) or design.get("schema") != "main-computer.mcel.application-design.v1":
        raise ValueError("Unsupported or missing Task Manager design schema")
    root = design.get("hierarchy")
    if not isinstance(root, dict) or not isinstance(root.get("children"), list):
        raise ValueError("Design hierarchy missing children")
    seen, roles = set(), {}
    for item in root["children"]:
        key, role = item.get("id"), item.get("role")
        if not key or key in seen or role not in ROLE_ALIASES:
            raise ValueError(f"Duplicate/missing/unrecognized top-level region: {key!r} {role!r}")
        seen.add(key)
        semantic = ROLE_ALIASES[role]
        if semantic in roles:
            raise ValueError(f"Ambiguous application hierarchy role: {semantic}")
        roles[semantic] = item
    if set(roles) != set(ROLE_ALIASES.values()):
        raise ValueError("Required semantic roles missing: " + str(sorted(set(ROLE_ALIASES.values())-set(roles))))
    if roles["attention"].get("placement") != "upper-left" or roles["primary"].get("role") != "primary-content":
        raise ValueError("Attention placement or primary-content declaration has drifted")
    entities = set(design.get("semantics", {}).get("entities", {}))
    for name, row in design.get("rows", {}).get("types", {}).items():
        if row.get("entity") not in entities:
            raise ValueError(f"Row {name} refers to unknown entity {row.get('entity')}")
    for item in root["children"]:
        for view in item.get("views", []):
            for section in view.get("sections", []):
                if section.get("row") and section["row"] not in design.get("rows", {}).get("types", {}):
                    raise ValueError(f"View {view.get('id')} refers to missing row {section['row']}")
    return design


def semantic_projection(design: dict) -> tuple[dict, dict]:
    """Adapter experiment: surface regions and grammar from one hierarchy level."""
    children = design["hierarchy"]["children"]
    semantic = {"id": "task-manager.probe.surface", "regions": [
        {"id": "root", "selector": "#task-manager-probe", "role": "application-root"},
        *({"id": item["id"], "selector": "#"+item["id"], "role": item["role"],
            "presentationRole": "work" if item["role"] == "primary-content" else "context",
            "primary": item["role"] == "primary-content"} for item in children),
    ]}
    grammar = {"regions": [
        {"id":"root", "selector":"#task-manager-probe", "children":[item["id"] for item in children]},
        *({"id": item["id"], "selector":"#"+item["id"]} for item in children),
    ], "constraints": [
        {"id": item["id"]+"-width", "selector":"#"+item["id"],
         "minWidth": 320 if item["role"] == "primary-content" else 220}
        for item in children
    ]}
    return semantic, grammar


def canonical_tree(design: dict) -> dict:
    """Proposed bridge: choose two bounded variants using semantic responsibilities.

    Neither these variants nor this adapter currently belong to production MCEL.
    The existing canonical tree evaluator owns candidate selection and DOM application.
    """
    by_role = {ROLE_ALIASES[item["role"]]:item["id"] for item in design["hierarchy"]["children"]}
    a, s, p, i, x, f = (by_role[k] for k in ("attention", "search", "primary", "inspector", "secondary", "feedback"))
    wide = {"tracks": ["minmax(0, 1fr)"]*3,
            "areas": [f"{a} {s} {s}",
                      f"{p} {p} {i}",
                      f"{p} {p} {i}",
                      f"{f} {f} {f}",
                      f"{x} {x} {x}"]}
    narrow = {"tracks": ["minmax(0, 1fr)"],
              "areas": [a, s, f, p, i, x]}
    return {"schema":"mcel.layout-tree.v1", "root": {
        "id":"task-manager-probe-root", "selector":"#task-manager-probe", "role":"group",
        "gap":12, "variants":[wide,narrow],
        "children":[{"id":item["id"], "selector":"#"+item["id"], "role":"region",
                     "verifyControls":False} for item in design["hierarchy"]["children"]],
    }}


def compile_through_dsl(tree: dict, out: Path) -> dict:
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    # The compiler receives a real, generated MCEL DSL rather than a synthetic compiled IR.
    # The probe is intentionally excluded from mcel_apps/ and live route registration.
    source = '''"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"task-manager-design-probe",title:"Task Manager Layout Probe"},
  ({application}) => application.hostBound(app => {
    app.presentation.hostBound("workspace", {route:"/applications/task-manager-design-probe",
      root:"#task-manager-probe",presentationAuthority:"existing-host-html"});
    app.layout.zones(["primary-content"]);
    app.layout.tree(''' + json.dumps(tree, separators=(",", ":")) + ''');
    app.proof.semanticRuntimeProven(); /* DSL prerequisite, not a browser proof */
  }));
'''
    path = out / "probe-application.js"
    path.write_text(source, encoding="utf-8")
    result = compile_dsl_application(path, write_candidate=False)
    summary = {"valid":result.valid,"diagnostics":result.diagnostics,
               "semanticFingerprint":result.semantic_fingerprint,
               "compiledTreeSame": bool(result.normalized_ir) and
                   result.normalized_ir["layouts"][0].get("layoutTree") == tree}
    if not summary["valid"] or not summary["compiledTreeSame"]:
        raise RuntimeError("MCEL DSL compilation or layout tree source equality failed: " + str(summary))
    return summary


def infer_direct(semantic: dict, grammar: dict, out: Path) -> dict:
    input_path=out/"inference-input.json"
    input_path.write_text(json.dumps({"semantic":semantic,"grammar":grammar}),encoding="utf-8")
    js='''const vm=require("node:vm"),fs=require("node:fs");
const scope={};vm.runInNewContext(fs.readFileSync(process.argv[1],"utf8"),scope);
const {semantic,grammar}=JSON.parse(fs.readFileSync(process.argv[2],"utf8"));
const result={};for(const width of [1440,390]){
result[String(width)]=scope.McelSemanticLayoutInference.inferWorkspaceLayout({
  semanticSurface:semantic,layoutGrammar:grammar,width,mode:"overview"});
}process.stdout.write(JSON.stringify(result));'''
    completed=subprocess.run(["node","-e",js,str(RESOLVER),str(input_path)],capture_output=True,text=True,timeout=30)
    if completed.returncode:raise RuntimeError("MCEL inference refused: "+completed.stderr)
    return json.loads(completed.stdout)


def region_label(item: dict) -> str:
    return item["id"].replace("-"," ").title()


def probe_html(design:dict, tree:dict, resolver: str) -> str:
    """Generic probe-only DOM, no service data, no app-specific CSS positioning."""
    escaped = html.escape
    contents=[]
    rows=design.get("rows",{}).get("types",{})
    for item in design["hierarchy"]["children"]:
        details=[]
        for entry in item.get("elements",[]):
            details.append(f'<li><strong>{escaped(entry["id"])}</strong> <small>{escaped(entry.get("status","unspecified"))}</small></li>')
        for view in item.get("views",[]):
            row_refs=[]
            for section in view.get("sections",[]):
                if section.get("row") in rows:row_refs.append(section["row"])
            details.append('<li><strong>'+escaped(view["title"])+"</strong> <small>"+
                           escaped(view.get("status","defined"))+"</small>"+
                           ("<div>"+" ".join('<span class="tag">'+escaped(x)+"</span>" for x in row_refs)+"</div>" if row_refs else "")+"</li>")
        if item.get("drawers"):
            details.extend(f'<li><strong>{escaped(x["id"])}</strong> <small>{escaped(x.get("status","defined"))}</small></li>' for x in item["drawers"])
        if not details:details.append('<li>Defined in design YAML</li>')
        contents.append(f'<section id="{escaped(item["id"])}" class="region" data-semantic-role="{escaped(item["role"])}">'+
            f'<h2>{escaped(region_label(item))}</h2><ul>'+"".join(details)+"</ul></section>")
    state = {'tree':tree,'sourceSchema':design['schema'],'resolver':'mcel-semantic-layout-inference.js'}
    script = '''<script>const proofData='''+json.dumps(state).replace('<','\\u003c')+''';
window.addEventListener("DOMContentLoaded",()=>{
try{const result=McelSemanticLayoutInference.mountCanonicalLayoutTree(document.getElementById("task-manager-probe"),proofData.tree,{observeResize:true});
document.getElementById("probe-result").textContent="MCEL canonical resolver: "+result.status;
window.__mcelProbeResult=result;
}catch(e){document.getElementById("probe-result").textContent="MCEL layout REFUSED: "+String(e);window.__mcelProbeError=String(e);}});</script>'''
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Task Manager — MCEL layout-only prototype</title><style>
*{box-sizing:border-box}body{margin:0;padding:16px;background:#111723;color:#e9eef6;font:14px/1.45 system-ui,sans-serif}
header{margin:0 0 16px}header h1{font-size:20px;margin:0 0 4px}small,.note{color:#a0aebd;font-size:12px}
#task-manager-probe{width:100%;max-width:100%;}section.region{border:1px solid #3b4d60;border-radius:10px;background:#1a2533;padding:12px;min-width:0}
.region h2{font-size:15px;text-transform:capitalize;margin:0 0 8px}.region ul{margin:0;padding-left:18px}.region li{padding:3px 0;overflow-wrap:anywhere}
.tag{display:inline-block;font:11px system-ui;border:1px solid #697c90;border-radius:5px;padding:2px 5px;margin:4px 3px 0 0}
#probe-result{font-weight:700}.region[data-semantic-role="primary-content"]{min-height:250px}.region[data-semantic-role="status-and-attention"]{background:#263649}
</style><header><h1>Task Manager — semantic layout probe</h1><div class="note">Read-only layout stub generated from design.yaml. No telemetry, interaction, or production activation.</div>
<div id="probe-result" role="status">Layout not evaluated</div></header><main id="task-manager-probe">'''+''.join(contents)+'''</main>
<script>'''+resolver+'''</script>'''+script+'''</html>'''


def browser_proof(out:Path, design:dict, tree:dict) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status":"unavailable", "reason":"playwright not installed"}
    attention_id=next(item["id"] for item in design["hierarchy"]["children"] if item["role"]=="status-and-attention")
    html_path=out/"preview.html"
    html_path.write_text(probe_html(design,tree,RESOLVER.read_text(encoding="utf-8")),encoding="utf-8")
    browser_path=os.environ.get("MCEL_CHROMIUM_EXECUTABLE") or shutil.which("chromium") or shutil.which("chromium-browser")
    result={}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=browser_path,headless=True,args=["--no-sandbox","--disable-dev-shm-usage"])
            try:
                for width,height in ((1440,900),(390,844)):
                    page=browser.new_page(viewport={"width":width,"height":height},device_scale_factor=1)
                    page.set_content(html_path.read_text(encoding="utf-8"),wait_until="load")
                    proof=page.evaluate('''() => {
const root=document.querySelector('#task-manager-probe');const regions=[...root.children];
const coordinates=Object.fromEntries(regions.map(el=>{const r=el.getBoundingClientRect();return [el.id,{x:r.x,y:r.y,width:r.width,height:r.height}]}));
const status=window.__mcelProbeResult?.status||"error";const layout=window.__mcelProbeResult?.initial?.evaluation;
return {status,error:window.__mcelProbeError||null,
  selected:layout?.selected?.choices?.[0]?.areas||null,
  columns:getComputedStyle(root).gridTemplateColumns.split(' ').filter(Boolean).length,
  viewportOverflow:document.documentElement.scrollWidth>innerWidth+1,
  authority:root.dataset.mcelCanonicalLayoutAuthority||null,coordinates};}''')
                    coordinates=proof["coordinates"]
                    p=coordinates[attention_id]
                    proof["priorityUpperLeft"]=(p["x"]<=min(x["x"] for x in coordinates.values())+1 and
                         p["y"]<=min(x["y"] for x in coordinates.values())+1)
                    page.screenshot(path=str(out/f"layout-{width}.png"),full_page=True)
                    result[str(width)]=proof
                    page.close()
            finally:browser.close()
    except Exception as exc:
        return {"status":"unavailable", "reason":f"{type(exc).__name__}: {exc}"}
    return result


def run(design_path:Path, out:Path) -> dict:
    out.mkdir(parents=True,exist_ok=True)
    design=load_design(design_path)
    surface,grammar=semantic_projection(design)
    inferred=infer_direct(surface,grammar,out)
    tree=canonical_tree(design)
    compile_result=compile_through_dsl(tree,out)
    (out/"canonical-layout-tree.json").write_text(json.dumps(tree,indent=2),encoding="utf-8")
    browser=browser_proof(out,design,tree)
    primary=next(item["id"] for item in design["hierarchy"]["children"] if item["role"]=="primary-content")
    attention=next(item["id"] for item in design["hierarchy"]["children"] if item["role"]=="status-and-attention")
    results={
        "schema":"task-manager.semantic-layout-probe.v1",
        "sourceDesign":design_path.name,
        "sourceSchema":design["schema"],
        "referenceResolver":"main_computer/web/applications/scripts/mcel-semantic-layout-inference.js",
        "inferredWorkspace":{
            str(width):{
                "rows":plan["rows"],"unmetMinWidth":plan["unmetMinWidth"],
                "primaryFirst":plan["order"][0]==primary,
                "attentionFirst":plan["order"][0]==attention,
            } for width,plan in inferred.items()},
        "canonicalCompiler":compile_result,
        "canonicalBrowser":browser,
        "limitations":[
            "Adapter is a prototype; design.yaml is not a native MCEL input.",
            "Canonical variants are generated by this prototype bridge, not inferred by MCEL from priority semantics.",
            "Preview DOM is generated as read-only stubs; no live data, row operations, worker/AI feeds or app runtime.",
            "No production mount, interaction, appearance-quality, or accessibility certification.",
            "The generated DSL has a required proof declaration, not a certified runtime proof.",
        ]}
    (out/"report.json").write_text(json.dumps(results,indent=2,default=str)+"\n",encoding="utf-8")
    return results


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design",type=Path,default=DESIGN)
    parser.add_argument("--output",type=Path,default=DEFAULT_OUTPUT)
    args=parser.parse_args(argv)
    try:report=run(args.design.resolve(),args.output.resolve())
    except Exception as exc:
        print("TASK_MANAGER_LAYOUT_PROBE_REFUSED:",str(exc),file=sys.stderr)
        return 2
    browser=report["canonicalBrowser"]
    ok=(report["canonicalCompiler"]["valid"] and
        all(isinstance(browser.get(key),dict) and browser[key].get("status")=="pass" and
            browser[key].get("priorityUpperLeft") and not browser[key].get("viewportOverflow")
            for key in ("1440","390")))
    print("TASK_MANAGER_LAYOUT_ONLY_PROBE_"+("PASS" if ok else "INCOMPLETE"))
    print("auto_inference_priority_first:",{k:v["attentionFirst"] for k,v in report["inferredWorkspace"].items()})
    print("canonical_browser:",{k:{name:v.get(name) for name in ("status","columns","priorityUpperLeft","viewportOverflow")} for k,v in browser.items() if isinstance(v,dict)})
    print("report:",args.output.resolve()/"report.json")
    return 0 if ok else 1

if __name__=="__main__":
    sys.exit(main())
