"""Fresh, fail-closed MCEL canonical layout certification (host-bound scope).

Verifies generated package identity and browser-executed structural layout. It
intentionally does not certify host application behavior, real runtime facades,
or the distinct full semantic package-host truth gate.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_dsl_compiler import compile_dsl_application

SCHEMA = "mcel.canonical-layout-certification.v2"
SCOPE = "host-bound-canonical-layout-only"
SUPPORTED_APPS = ("calculator", "code-editor")
SCRIPTS = (
    "scripts/mcel-self-diagnosis.js",
    "scripts/mcel-semantic-layout-inference.js",
    "scripts/mcel-host-bound-application-runtime.js",
)
SCENARIOS = {
    "calculator": (
        {"id": "graph-wide", "width": 1140, "mode": "graphing"},
        {"id": "basic-wide", "width": 1140, "mode": "basic"},
        {"id": "graph-compact", "width": 750, "mode": "graphing"},
    ),
    "code-editor": (
        {"id": "dock-wide", "width": 1220, "mode": "dock"},
        {"id": "dock-compact", "width": 1050, "mode": "dock"},
    ),
}


class CertificationError(RuntimeError):
    pass


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read_repo_file(root: Path, relative: str) -> bytes:
    path = root / relative
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise CertificationError(f"Unavailable or unsafe source: {relative}")
    return path.read_bytes()


def prepare_application(repo: Path, app_id: str) -> dict[str, Any]:
    """Rebuild each authority from source; do not trust saved generated artifacts."""
    if app_id not in SUPPORTED_APPS:
        raise CertificationError(f"Unsupported certification application: {app_id}")
    catalog = build_application_package_catalog(repo)
    if not catalog.ok or catalog.invalid_count or catalog.errors:
        raise CertificationError("Package catalog is invalid")
    matches = [p for p in catalog.packages if p.app_id == app_id and p.valid]
    if len(matches) != 1:
        raise CertificationError("Exactly one valid application package is required")
    package = matches[0]
    source = (package.authoring or {}).get("source")
    if not isinstance(source, str) or not source:
        raise CertificationError("Canonical DSL source is missing")
    compiled = compile_dsl_application(repo / source, write_candidate=False)
    if not compiled.valid or compiled.app_id != app_id or not compiled.semantic_fingerprint:
        raise CertificationError(f"Invalid DSL/IR compilation for {app_id}: {compiled.diagnostics}")
    trees = [layout.get("layoutTree") for layout in compiled.normalized_ir.get("layouts", ())
             if isinstance(layout, dict) and layout.get("layoutTree")]
    if len(trees) != 1 or trees[0].get("schema") != "mcel.layout-tree.v1":
        raise CertificationError("Expected exactly one canonical IR layout tree")
    projection_set = build_runtime_projection_set(repo)
    projected = [p for p in projection_set.projections if p.app_id == app_id]
    if len(projected) != 1 or projected[0].mount_mode != "host-bound":
        raise CertificationError("Expected exactly one host-bound runtime projection")
    projection = projected[0]
    browser_catalog = build_repository_browser_catalog_payload(repo)
    records = [record for record in browser_catalog["packages"] if record["appId"] == app_id]
    if len(records) != 1:
        raise CertificationError("Expected exactly one projected browser package")
    record = records[0]
    manifest = projection.manifest
    if (manifest.get("source", {}).get("packageFingerprint") != package.fingerprint
            or manifest.get("source", {}).get("catalogFingerprint") != browser_catalog["catalogFingerprint"]
            or manifest.get("projection", {}).get("fingerprint") != projection.fingerprint
            or record.get("fingerprint") != package.fingerprint
            or record.get("runtimeProjection", {}).get("fingerprint") != projection.fingerprint):
        raise CertificationError("Package/projection/catalog fingerprint mismatch")
    modules = {name: projection.files[entry["path"]].decode("utf-8")
               for name, entry in manifest["modules"].items()}
    if '"layoutTree"' not in modules.get("layout", ""):
        raise CertificationError("Projected layout contract lacks canonical tree")
    web = "main_computer/web/applications/"
    files = [source, f"{web}apps/{app_id}.html", f"{web}styles/{app_id}.css"]
    files.extend(f"{web}{script}" for script in SCRIPTS)
    digests = {name: _digest(_read_repo_file(repo, name)) for name in files}
    return {
        "appId": app_id,
        "semanticFingerprint": compiled.semantic_fingerprint,
        "packageFingerprint": package.fingerprint,
        "projectionFingerprint": projection.fingerprint,
        "catalogFingerprint": browser_catalog["catalogFingerprint"],
        "layoutTree": trees[0],
        "record": record,
        "manifest": manifest,
        "modules": modules,
        "methods": [intent["runtimeMethod"] for intent in compiled.normalized_ir["intents"]],
        "inputs": digests,
        "html": _read_repo_file(repo, f"{web}apps/{app_id}.html").decode("utf-8"),
        "css": _read_repo_file(repo, f"{web}styles/{app_id}.css").decode("utf-8"),
    }


_BROWSER_OBSERVE = r'''async ({package:pkg, scenario}) => {
  document.body.dataset.activeApp=pkg.appId;
  const root=document.querySelector(pkg.manifest.runtime.rootSelector);
  if (!root) return {status:'fail',reason:'missing-host-root'};
  const beforeStyle=document.querySelector('#mcel-cert-authored-css')?.textContent;
  // A responsive certificate is valid only at the actual requested browser width.
  // In particular, Code Editor's !important 100vw CSS defeats an inline width.
  const measuredViewport=()=>({innerWidth:window.innerWidth,
    documentClientWidth:document.documentElement.clientWidth,
    visualViewportWidth:window.visualViewport?.width??null});
  const measuredBox=el=>{if(!el)return null;const r=el.getBoundingClientRect();
    return {left:+r.left.toFixed(2),right:+r.right.toFixed(2),
      width:+r.width.toFixed(2),height:+r.height.toFixed(2),
      clientWidth:el.clientWidth,scrollWidth:el.scrollWidth};};
  const facade={};
  for (const method of pkg.methods) facade[method]=()=>{throw new Error('certification does not execute host intents')};
  window[pkg.manifest.runtime.facade]=Object.freeze(facade);
  const catalog={catalogFingerprint:pkg.catalogFingerprint,
    getPackage:id=>id===pkg.appId?pkg.record:null,
    listPackages:()=>[pkg.record]};
  const loader=async (_url,entry)=>{
    const name=Object.keys(pkg.manifest.modules).find(key=>pkg.manifest.modules[key].path===entry.path);
    if (!name || !pkg.modules[name]) throw new Error('module-source-missing:'+entry.path);
    return import('data:text/javascript;base64,'+btoa(unescape(encodeURIComponent(pkg.modules[name]))));
  };
  if (pkg.appId==='calculator') {
    root.style.setProperty('display','grid','important');
    root.style.width='auto';root.style.height='890px';
    const graph=scenario.mode==='graphing';
    root.querySelector('#calculator-graphing-panel').hidden=!graph;
    root.querySelector('#calculator-mathics-panel').hidden=!graph;
    root.querySelector('#calculator-chat-panel').hidden=false;
  } else {
    root.style.setProperty('display','block','important');
    root.style.height='800px';
    root.dataset.mcelLayoutLive='true';
    root.querySelector('.code-studio-activitybar').style.setProperty('display','grid','important');
  }
  const originalTreeHost=root.querySelector(pkg.layoutTree.root.selector);
  const ownedHtmlBefore=originalTreeHost?.outerHTML;
  let mount;
  try {
    mount=await McelHostBoundApplicationRuntime.mountApplication({
      appId:pkg.appId,packageCatalog:catalog,manifest:pkg.manifest,moduleLoader:loader
    });
  } catch (error) {return {status:'fail',reason:'mount-exception',detail:String(error),
    viewport:measuredViewport(),hostBox:measuredBox(root),
    canonicalBox:measuredBox(originalTreeHost)};}
  try {
    const layout=mount.layoutReport?.initial;
    const tree=mount.modules?.layout?.layoutTree;
    const structuralRoot=root.querySelector(pkg.layoutTree.root.selector);
    const declaredEqual=JSON.stringify(tree)===JSON.stringify(pkg.layoutTree);
    const viewport=measuredViewport(),hostBox=measuredBox(root),
      canonicalBox=measuredBox(structuralRoot);
    const tolerance=1.5;
    const code=[];
    const independentNodes=[];
    if(Math.abs(viewport.innerWidth-scenario.width)>tolerance)
      code.push('requested-browser-viewport-not-established');
    if(Math.abs(viewport.documentClientWidth-viewport.innerWidth)>20 ||
       viewport.documentClientWidth>viewport.innerWidth+tolerance)
      code.push('document-viewport-inconsistent');
    if(hostBox && hostBox.scrollWidth>hostBox.clientWidth+tolerance)
      code.push('host-horizontal-scroll-overflow');
    if(!hostBox||Math.abs(hostBox.width-scenario.width)>tolerance ||
       hostBox.left< -tolerance || hostBox.right>viewport.innerWidth+tolerance)
      code.push('host-viewport-width-or-containment-failed');
    if(!canonicalBox || !hostBox || canonicalBox.width<=0 ||
       canonicalBox.left<hostBox.left-tolerance ||
       canonicalBox.right>hostBox.right+tolerance ||
       canonicalBox.left< -tolerance ||
       canonicalBox.right>viewport.innerWidth+tolerance)
      code.push('canonical-structural-viewport-overflow');
    function checkNode(node,parentEl=null){
      const matches=[...structuralRoot.querySelectorAll(node.selector)];
      if (structuralRoot.matches(node.selector)) matches.unshift(structuralRoot);
      if (matches.length!==1){code.push('selector-cardinality:'+node.id+':'+matches.length);return;}
      const el=matches[0],css=getComputedStyle(el),rect=el.getBoundingClientRect();
      if(parentEl && el.parentElement!==parentEl)code.push('non-direct-layout-child:'+node.id);
      const hidden=el.hidden||css.display==='none'||css.visibility==='hidden'||
        [...(function*(e){for(let p=e.parentElement;p&&p!==structuralRoot.parentElement;p=p.parentElement)yield p})(el)]
          .some(p=>p.hidden||getComputedStyle(p).display==='none');
      const visible=!hidden&&rect.width>0&&rect.height>0;
      if(node.requireVisible&&!visible)code.push('required-node-hidden:'+node.id);
      if(visible && !Number.isFinite(rect.width+rect.height))code.push('nonfinite-geometry:'+node.id);
      if(visible && (rect.left< -tolerance || rect.right>viewport.innerWidth+tolerance))
        code.push('declared-region-viewport-overflow:'+node.id);
      independentNodes.push({id:node.id,visible,left:+rect.left.toFixed(2),right:+rect.right.toFixed(2),
        width:+rect.width.toFixed(2),height:+rect.height.toFixed(2),
        display:css.display,columns:css.gridTemplateColumns,areas:css.gridTemplateAreas});
      (node.children||[]).forEach(child=>checkNode(child,el));
    }
    if(structuralRoot)checkNode(pkg.layoutTree.root);
    else code.push('canonical-host-missing');
    if(!declaredEqual)code.push('compiled-projected-tree-mismatch');
    if(mount.layoutStatus!=='pass')code.push('layout-not-passed:'+mount.layoutStatus);
    if(layout?.independent!=='pass')code.push('engine-independent-proof-not-pass');
    if((layout?.observed?.failures||[]).length)code.push('engine-live-failures');
    const candidates=layout?.evaluation?.candidates||[];
    const ranked=candidates.filter(candidate=>candidate.accepted).sort((a,b)=>
      a.evidence.quality.height-b.evidence.quality.height||
      a.evidence.quality.emptyCells-b.evidence.quality.emptyCells||
      a.evidence.quality.gridRows-b.evidence.quality.gridRows||
      JSON.stringify(a.choices).localeCompare(JSON.stringify(b.choices)));
    const selected=layout?.evaluation?.selected;
    if(!selected)code.push('candidate-selection-missing');
    if(!ranked.length||JSON.stringify(selected?.choices)!==JSON.stringify(ranked[0].choices))
      code.push('candidate-not-best-ranked-feasible');
    if(selected?.evidence?.failures?.length)code.push('selected-candidate-hard-failures');
    if(layout?.evaluation?.searchTruncated)code.push('bounded-search-incomplete');
    if(structuralRoot?.dataset.mcelCanonicalLayoutAuthority!=='mcel-layout-tree.v1')
      code.push('authority-not-claimed');
    if(root.dataset.mcelHostBoundStatus!=='mounted')code.push('host-not-mounted');
    const splits=McelSelfDiagnosis?._private?.detectAtomicControlLabelSplits?.(structuralRoot,{appId:pkg.appId});
    if(!Array.isArray(splits))code.push('independent-text-probe-unavailable');
    else if(splits.length)code.push('atomic-label-split:'+splits.length);
    if(pkg.appId==='calculator'){
      const graph=root.querySelector('#calculator-graphing-panel');
      const math=root.querySelector('#calculator-mathics-panel');
      const areas=root.querySelector('.calculator-workspace').style.getPropertyValue('grid-template-areas');
      if(scenario.mode==='basic'){
        if(!graph.hidden||!math.hidden||getComputedStyle(graph).display!=='none'||
          getComputedStyle(math).display!=='none'||areas.includes('graphing'))code.push('basic-mode-integrity');
      }else if(!areas.includes('graphing'))code.push('graphing-mode-integrity');
      if(root.querySelectorAll('.calculator-scientific-functions button').length!==21)
        code.push('scientific-controls-not-preserved');
    }else{
      const areas=root.querySelector('.code-studio-body').style.getPropertyValue('grid-template-areas');
      const expected=scenario.id==='dock-compact'?'rail sidebar editor':'rail sidebar editor inspector';
      const actual=areas.replace(/["']/g,'').trim();
      const inspector=root.querySelector('.code-studio-inspector');
      const inspectorVisible=!!inspector && getComputedStyle(inspector).display!=='none' &&
        inspector.getBoundingClientRect().width>0;
      const declared=pkg.layoutTree.root.children.find(child=>child.id==='workbench');
      if(!declared?.variants?.some(v=>v.areas.length===1 && v.areas[0]===expected))
        code.push('dock-responsive-variant-not-declared');
      if(actual!==expected)code.push('dock-areas-do-not-match-scenario');
      if(inspectorVisible!==(scenario.id!=='dock-compact'))
        code.push('dock-inspector-visibility-mismatch');
      if(root.querySelectorAll('.code-studio-activitybar button').length!==5)
        code.push('activity-controls-not-preserved');
    }
    const structuralCSS=independentNodes.map(n=>({id:n.id,columns:n.columns,areas:n.areas}));
    const quality=layout?.evaluation?.selected?.evidence?.quality || null;
    const evidence={status:code.length?'fail':'pass',issues:code,
      layoutStatus:mount.layoutStatus,hostStatus:root.dataset.mcelHostBoundStatus,
      independent:layout?.independent||null,treeMatch:declaredEqual,
      viewport,hostBox,canonicalBox,
      nodeCount:independentNodes.length,nodes:independentNodes,
      selectedQuality:quality,candidateCount:layout?.evaluation?.candidates?.length||0,
      selectedCount:(selected?1:0),feasibleCandidates:ranked.length,
      searchTruncated:layout?.evaluation?.searchTruncated??null,
      layoutFailureCount:layout?.observed?.failures?.length??null,
      atomicSplitCount:Array.isArray(splits)?splits.length:null,
      generatedStructuralCSS:structuralCSS};
    mount.unmount();
    evidence.restored=structuralRoot?.outerHTML===ownedHtmlBefore;
    evidence.authoredCSSUnchanged=document.querySelector('#mcel-cert-authored-css')?.textContent===beforeStyle;
    if(!evidence.restored)evidence.issues.push('unmount-does-not-restore-dom');
    if(!evidence.authoredCSSUnchanged)evidence.issues.push('authored-css-mutated');
    evidence.status=evidence.issues.length?'fail':'pass';
    return evidence;
  }catch(error){mount.unmount();return {status:'fail',reason:'observation-exception',detail:String(error)};}
}'''


def validate_browser_observation(observed: Mapping[str, Any], tree: Mapping[str, Any],
                                 scenario: Mapping[str, Any]) -> list[str]:
    """Independent Python adjudication; browser's self-reported PASS is insufficient."""
    if not isinstance(observed, Mapping):
        return ["browser-evidence-not-an-object"]
    failures = []
    requested = scenario.get("width")
    viewport = observed.get("viewport")
    host = observed.get("hostBox")
    canonical = observed.get("canonicalBox")
    # Independent adjudication binds the browser's actual measurements to the
    # Python-origin scenario, even if the JS observation incorrectly says PASS.
    if type(requested) is not int or not 320 <= requested <= 2500:
        failures.append("invalid-requested-viewport")
    if not isinstance(viewport, Mapping) or type(viewport.get("innerWidth")) not in (float, int) or (
            type(requested) is int and abs(viewport["innerWidth"] - requested) > 1.5):
        failures.append("browser-viewport-width-mismatch")
    if not isinstance(viewport, Mapping) or type(viewport.get("documentClientWidth")) not in (float, int) or (
            type(viewport.get("innerWidth")) in (float, int) and not
            viewport["innerWidth"] - 20 <= viewport["documentClientWidth"] <= viewport["innerWidth"] + 1.5):
        failures.append("document-viewport-inconsistent")
    def valid_box(box: Any) -> bool:
        return isinstance(box, Mapping) and all(
            type(box.get(k)) in (float, int) and float("-inf") < box[k] < float("inf")
            for k in ("left", "right", "width", "height")) and (
                box["width"] > 0 and abs(box["right"] - box["left"] - box["width"]) <= 2)
    if not valid_box(host) or type(requested) is not int or (
            abs(host["width"] - requested) > 1.5 or host["left"] < -1.5 or host["right"] > requested + 1.5):
        failures.append("host-viewport-containment-unproven")
    if (not isinstance(host, Mapping) or type(host.get("scrollWidth")) not in (int, float)
            or type(host.get("clientWidth")) not in (int, float)
            or host["scrollWidth"] > host["clientWidth"] + 1.5):
        failures.append("host-horizontal-overflow")
    if not valid_box(canonical) or not valid_box(host) or type(requested) is not int or (
            canonical["left"] < max(-1.5, host["left"] - 1.5) or
            canonical["right"] > min(requested + 1.5, host["right"] + 1.5)):
        failures.append("canonical-root-viewport-overflow")
    if observed.get("status") != "pass" or observed.get("issues") != []:
        failures.append("browser-declared-failure")
    for field, expected in (("layoutStatus", "pass"), ("hostStatus", "mounted"),
                            ("independent", "pass"), ("treeMatch", True),
                            ("selectedCount", 1), ("layoutFailureCount", 0),
                            ("atomicSplitCount", 0), ("searchTruncated", False),
                            ("restored", True), ("authoredCSSUnchanged", True)):
        if observed.get(field) != expected or type(observed.get(field)) is not type(expected):
            failures.append("missing-or-invalid-evidence:" + field)
    for field in ("candidateCount", "feasibleCandidates"):
        if type(observed.get(field)) is not int or observed[field] < 1:
            failures.append("missing-or-invalid-evidence:" + field)
    quality = observed.get("selectedQuality")
    if not isinstance(quality, Mapping) or not all(
        type(quality.get(name)) in (int, float) and quality[name] >= 0
        for name in ("height", "emptyCells", "gridRows")
    ):
        failures.append("selected-layout-quality-unavailable")
    nodes = observed.get("nodes")
    expected: dict[str, Mapping[str, Any]] = {}
    def walk(node: Mapping[str, Any]) -> None:
        expected[node["id"]] = node
        for child in node.get("children", ()):
            walk(child)
    walk(tree["root"])
    if not isinstance(nodes, list) or len(nodes) != len(expected) or observed.get("nodeCount") != len(expected):
        failures.append("layout-tree-coverage-incomplete")
    else:
        ids = [node.get("id") for node in nodes if isinstance(node, Mapping)]
        if len(set(ids)) != len(expected) or set(ids) != set(expected):
            failures.append("layout-tree-identity-mismatch")
        for node in nodes:
            if not isinstance(node, Mapping):
                failures.append("layout-node-evidence-invalid")
                continue
            if node.get("visible") is True and type(requested) is int and (
                not valid_box(node) or node["left"] < -1.5 or
                node["right"] > requested + 1.5):
                failures.append("visible-layout-node-outside-viewport:" + str(node.get("id")))
            if node.get("visible") is True and type(requested) is int and (
                type(node.get("width")) not in (float, int) or node["width"] <= 0
                or type(node.get("height")) not in (float, int) or node["height"] <= 0):
                failures.append("visible-layout-node-unmeasured:" + str(node.get("id")))
            definition = expected.get(node.get("id"))
            if definition and definition.get("requireVisible") and node.get("visible") is not True:
                failures.append("required-layout-region-not-visible:" + node["id"])
    generated = observed.get("generatedStructuralCSS")
    if not isinstance(generated, list) or len(generated) != len(expected):
        failures.append("computed-css-observation-incomplete")
    # Code Editor certifies two distinct declared workspace modes. A hidden
    # inspector is conformant only in the compact three-area variant; the
    # wide scenario must retain its visible four-area workbench.
    if tree.get("root", {}).get("id") == "editor-shell" and scenario.get("mode") == "dock":
        required = ("rail sidebar editor" if scenario.get("id") == "dock-compact"
                    else "rail sidebar editor inspector")
        actual_workbench = (next((node for node in generated if isinstance(node, Mapping)
                                  and node.get("id") == "workbench"), None)
                            if isinstance(generated, list) else None)
        if not isinstance(actual_workbench, Mapping) or actual_workbench.get("areas", "").replace('"', "").strip() != required:
            failures.append("code-editor-workbench-responsive-areas-unproven")
        inspector = (next((node for node in nodes if isinstance(node, Mapping)
                           and node.get("id") == "inspector"), None)
                     if isinstance(nodes, list) else None)
        if not isinstance(inspector, Mapping) or inspector.get("visible") is not (scenario.get("id") != "dock-compact"):
            failures.append("code-editor-inspector-responsive-visibility-unproven")
        workbench_def = next((node for node in tree.get("root", {}).get("children", ())
                              if node.get("id") == "workbench"), None)
        if not isinstance(workbench_def, Mapping) or not any(
                v.get("areas") == [required] for v in workbench_def.get("variants", ())):
            failures.append("code-editor-workbench-responsive-variant-unavailable")
    return failures


def enforce_distinct_widths(items: list[dict[str, Any]]) -> None:
    """A compact certification cannot reuse the geometry of a wide scenario."""
    for i, first in enumerate(items):
        a = first.get("observed")
        if not isinstance(a, Mapping) or a.get("status") != "pass":
            continue
        for second in items[i + 1:]:
            b = second.get("observed")
            if not isinstance(b, Mapping) or b.get("status") != "pass":
                continue
            requested_delta = abs(first["width"] - second["width"])
            if not requested_delta:
                continue
            a_view, b_view = a.get("viewport"), b.get("viewport")
            a_host, b_host = a.get("hostBox"), b.get("hostBox")
            if not all(isinstance(x, Mapping) for x in (a_view, b_view, a_host, b_host)):
                second["observed"]["status"] = "fail"
                second["observed"].setdefault("certificationRefusals", []).append(
                    "distinct-viewport-evidence-missing")
                continue
            actual_delta = abs(a_host["width"] - b_host["width"])
            viewport_delta = abs(a_view["innerWidth"] - b_view["innerWidth"])
            if viewport_delta < requested_delta - 1.5 or actual_delta < requested_delta - 3:
                second["observed"]["status"] = "fail"
                second["observed"].setdefault("certificationRefusals", []).append(
                    "distinct-scenarios-have-identical-effective-width")


def run_certification(repo_root: Path, *, app_ids: tuple[str, ...] = SUPPORTED_APPS,
                      scenarios: Mapping[str, tuple[Mapping[str, Any], ...]] = SCENARIOS,
                      chromium_executable: str | None = None,
                      with_intent_proof: bool = False) -> dict[str, Any]:
    """No previous report or saved browser observation can satisfy this gate."""
    repo = repo_root.resolve()
    report: dict[str, Any] = {"schema": SCHEMA, "scope": SCOPE, "status": "fail", "passed": False,
                              "fullSemanticRuntimeProven": False, "applications": []}
    try:
        if not app_ids or len(set(app_ids)) != len(app_ids):
            raise CertificationError("Application selection is empty or duplicated")
        prepared = [prepare_application(repo, app) for app in app_ids]
        for package in prepared:
            app_id = package["appId"]
            specified = list(scenarios.get(app_id) or ())
            required = {(s["id"], s["width"], s["mode"]) for s in SCENARIOS[app_id]}
            actual = [(s.get("id"), s.get("width"), s.get("mode")) for s in specified]
            if len(actual) != len(set(actual)) or not required.issubset(set(actual)):
                raise CertificationError(f"Required distinct certification scenarios missing for {app_id}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CertificationError("Playwright is required for a fresh Chromium observation") from exc
        binary = (chromium_executable or os.getenv("MCEL_CHROMIUM_EXECUTABLE")
                  or os.getenv("CHROMIUM_EXECUTABLE") or shutil.which("chromium")
                  or shutil.which("chromium-browser"))
        web = repo / "main_computer/web/applications"
        with sync_playwright() as playwright:
            kwargs: dict[str, Any] = {"headless": True}
            if binary:
                kwargs.update({"executable_path": binary, "args": ["--no-sandbox", "--disable-dev-shm-usage"]})
            browser = playwright.chromium.launch(**kwargs)
            try:
                for pkg in prepared:
                    app_id = pkg["appId"]
                    app_result: dict[str, Any] = {
                        "appId": app_id, "status": "fail", "semanticFingerprint": pkg["semanticFingerprint"],
                        "packageFingerprint": pkg["packageFingerprint"],
                        "projectionFingerprint": pkg["projectionFingerprint"],
                        "inputDigests": pkg["inputs"], "scenarios": [],
                    }
                    report["applications"].append(app_result)
                    wire = {key: pkg[key] for key in ("appId", "layoutTree", "record", "manifest",
                            "modules", "methods", "catalogFingerprint")}
                    for scenario in scenarios[app_id]:
                        scenario_id = str(scenario.get("id") or "")
                        if (not scenario_id or type(scenario.get("width")) is not int
                                or scenario["width"] < 320 or scenario["width"] > 2500):
                            raise CertificationError(f"Invalid certification scenario: {scenario}")
                        page = browser.new_page(viewport={"width": scenario["width"], "height": 920})
                        try:
                            page.set_content('<style id="mcel-cert-fixture">html,body{margin:0;padding:0}</style>' + '<style id="mcel-cert-authored-css">' + pkg["css"]
                                             + '</style>' + pkg["html"])
                            for script in SCRIPTS:
                                page.add_script_tag(path=str(web / script))
                            observed = page.evaluate(_BROWSER_OBSERVE, {"package": wire, "scenario": scenario})
                        except Exception as exc:
                            observed = {"status": "fail", "reason": "browser-observation-exception", "detail": str(exc)[:1200]}
                        finally:
                            page.close()
                        adjudication = validate_browser_observation(observed, pkg["layoutTree"], scenario)
                        if adjudication:
                            observed = {**observed, "status": "fail", "certificationRefusals": adjudication}
                        app_result["scenarios"].append({"id": scenario_id, "width": scenario["width"],
                            "mode": scenario.get("mode"), "observed": observed})
                    enforce_distinct_widths(app_result["scenarios"])
                    # Certifier independently confirms every source is unchanged after browser execution.
                    actual = {name: _digest(_read_repo_file(repo, name)) for name in pkg["inputs"]}
                    if actual != pkg["inputs"]:
                        raise CertificationError(f"Application source changed during certification: {app_id}")
                    app_result["status"] = ("pass" if all(x["observed"].get("status") == "pass"
                                                            for x in app_result["scenarios"]) else "fail")
            finally:
                browser.close()
        # Recreate compiler/package/projector evidence to guard against stale source/projection.
        for pkg in prepared:
            fresh = prepare_application(repo, pkg["appId"])
            for name in ("semanticFingerprint", "packageFingerprint", "projectionFingerprint", "inputs"):
                if fresh[name] != pkg[name]:
                    raise CertificationError(f"Fresh {name} changed during certification: {pkg['appId']}")
        if with_intent_proof:
            report["scope"] = SCOPE + "+host-bound-ir-native-intents"
            # Reuse the existing independent acceptance + intent parity authority;
            # do not infer such proof from the layout-only browser facade.
            from tools.mcel_phase1_host_bound_prove import run_host_bound_proof
            for app_result in report["applications"]:
                try:
                    prior = run_host_bound_proof(repo=repo, app_id=app_result["appId"])
                except Exception as error:
                    app_result["intentProof"] = {"status": "fail", "reason": str(error)[:500]}
                    app_result["status"] = "fail"
                    raise CertificationError(f"Independent host-bound intent proof unavailable: {app_result['appId']}: {error}") from error
                if (prior.get("passed") is not True or prior.get("truthStatus") !=
                        "host-bound-ir-native-authoritative" or
                        prior.get("semanticFingerprint") != app_result["semanticFingerprint"] or
                        prior.get("packageFingerprint") != app_result["packageFingerprint"] or
                        prior.get("runtimeProjectionFingerprint") != app_result["projectionFingerprint"]):
                    app_result["intentProof"] = {"status": "fail", "reason": "identity or intent scope mismatch"}
                    app_result["status"] = "fail"
                    raise CertificationError(f"Independent host-bound intent proof failed: {app_result['appId']}")
                app_result["intentProof"] = {"status": "pass", "truthStatus": prior["truthStatus"],
                    "repositoryFingerprint": prior["repositoryFingerprint"],
                    "intentCoverage": prior["intentCoverage"]}
        report["passed"] = all(app["status"] == "pass" for app in report["applications"])
        report["status"] = "pass" if report["passed"] else "fail"
    except Exception as exc:
        report["error"] = str(exc)[:3000]
        report["status"] = "fail"
        report["passed"] = False
    # Both successful and refused certificates carry an evidence digest.
    report["evidenceFingerprint"] = _digest(_canonical(report))
    return report
