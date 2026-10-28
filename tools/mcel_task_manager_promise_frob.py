#!/usr/bin/env python3
"""MCEL Promise Frob 6: existing Task Manager, read-only and fail-closed.

This is an evidence collector, not a production mount. No Task Manager scripts,
operational endpoints, action buttons, or destructive controls are executed.
The frozen Phase 3 notebook is optional in exports; absence stays UNVERIFIED.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.mcel_notebook_projection import (
    NotebookProjectionRefused, compile_notebook_preview, render_notebook_html,
)
from main_computer.mcel_dsl_compiler import compile_dsl_application
from tools.mcel_task_manager_notebook_lab_acceptance import run as run_lab_acceptance
from tools.mcel_task_manager_semantic_pipeline_acceptance import run as run_semantic_acceptance
from tools.mcel_semantic_generality_frob import run as run_generality_frob
from tools.mcel_task_manager_live_lifecycle_frob import run as run_lifecycle_frob

SCHEMA = "mcel.task-manager-promise.v1"
HOST = ROOT / "main_computer/web/applications/apps/task-manager.html"
HOST_STYLE = ROOT / "main_computer/web/applications/styles/task-manager.css"
HOST_ENTRY = ROOT / "main_computer/web/applications/scripts/task-manager.js"
HOST_ADAPTER = ROOT / "main_computer/web/applications/scripts/task-manager-mcel.js"
HOST_BINDINGS = ROOT / "main_computer/web/applications/scripts/dom-bindings/task-manager.js"
HOST_TABS = ROOT / "main_computer/web/applications/scripts/terminal.js"
NOTEBOOK = ROOT / "mcel_experiments/task-manager-notebook/application.js"
FIXTURE = ROOT / "mcel_experiments/task-manager-notebook/read-only-sample.json"
IR_WITNESS = ROOT / "tools/fixtures/mcel_acid_tab_ir.json"
RENDERER = ROOT / "main_computer/web/applications/scripts/mcel-tab-workspace.js"
SHARED_LAYOUT = ROOT / "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js"
OUTPUT = ROOT / "runtime/reports/mcel-task-manager-promise"
VIEWPORTS = (320, 390, 560, 1100)
TAB_IDS = ("server-processes", "all-processes", "connections", "hardware")
OPERATIONS = ("task-server-shutdown", "task-server-start", "task-server-restart",
              "task-schedule-create", "task-ai-analyze")


class TagInventory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id: dict[str, dict[str, str]] = {}
        self.by_tab: dict[str, dict[str, str]] = {}
        self.duplicate_ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if "id" in data:
            if data["id"] in self.by_id:
                self.duplicate_ids.append(data["id"])
            self.by_id[data["id"]] = {"tag": tag, **data}
        if "data-task-tab" in data:
            self.by_tab[data["data-task-tab"]] = {"tag": tag, **data}


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def check_host() -> dict[str, Any]:
    paths = [HOST, HOST_STYLE, HOST_ENTRY, HOST_ADAPTER, HOST_BINDINGS, HOST_TABS]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        return {"status": "BLOCKED", "missing": missing}
    html = HOST.read_text(encoding="utf-8")
    tree = TagInventory()
    tree.feed(html)
    errors = list("duplicate-id:" + x for x in tree.duplicate_ids)
    for tab_id in TAB_IDS:
        tab = tree.by_tab.get(tab_id)
        if not tab:
            errors.append("missing-tab:" + tab_id)
            continue
        panel = tree.by_id.get(tab.get("aria-controls", ""))
        if tab.get("role") != "tab" or panel is None or panel.get("role") != "tabpanel" or (
            panel.get("aria-labelledby") != tab.get("id")
        ):
            errors.append("invalid-tab-panel:" + tab_id)
    for name in OPERATIONS:
        if name not in tree.by_id:
            errors.append("missing-operator-control:" + name)
    entry = HOST_ENTRY.read_text(encoding="utf-8")
    tab_runtime = HOST_TABS.read_text(encoding="utf-8")
    adapter = HOST_ADAPTER.read_text(encoding="utf-8")
    behavior = {
        "legacyTabListeners": 'setTaskNotebookTab(button.dataset.taskTab' in entry,
        "legacyTabController": "function setTaskNotebookTab(" in tab_runtime,
        "passiveSemanticAdapter": "applyTaskManagerMcelSemantics" in adapter,
        "phase3GeneratedMountInHostEntry": "McelTabWorkspace.mount" in entry,
        "phase3GeneratedMountInAdapter": "McelTabWorkspace.mount" in adapter,
        "guardedLabHostHook": "McelTaskManagerNotebookLab.optInHost" in entry,
        "operatorControlsPresent": all(x in tree.by_id for x in OPERATIONS),
        "notebookAfterOperatorControls": html.find('class="task-pane task-notebook"') > html.find('id="task-schedule-create"'),
    }
    return {
        "status": "PASS" if not errors and all(behavior[k] for k in (
            "legacyTabListeners", "legacyTabController", "passiveSemanticAdapter", "operatorControlsPresent")) else "FAIL",
        "errors": errors, "tabs": list(tree.by_tab), "operationControls": list(OPERATIONS),
        "implementation": behavior,
        "sha256": {str(p.relative_to(ROOT)): sha(p) for p in paths},
        "scope": "Static existing-app structure only; no host JavaScript executed or live operation proven",
    }


def notebook_candidate(source: Path, fixture: Path | None) -> tuple[dict[str, Any], str | None]:
    if not source.is_file():
        return ({"status": "UNVERIFIED", "reason": "Frozen notebook DSL absent from this repository/export",
                 "source": str(source), "sourcePresent": False}, None)
    report = compile_dsl_application(source, write_candidate=False)
    if not report.valid or not report.normalized_ir:
        return ({"status": "FAIL", "reason": "Present DSL refused by public compiler",
                 "diagnostics": [d.get("code") for d in report.diagnostics]}, None)
    ir = report.normalized_ir
    tabs = ir["surfaces"][0].get("semanticSurface", {}).get("tabGroup", {}).get("tabs", [])
    actual = tuple(t["id"] for t in tabs)
    if actual != TAB_IDS or ir["application"]["appId"] != "task-manager-notebook":
        return ({"status": "FAIL", "reason": "Authoritative notebook identities changed",
                 "actual": list(actual)}, None)
    if fixture is not None and not fixture.is_file():
        return ({"status": "PARTIAL_VERIFIED", "reason": "DSL compiles, optional sample fixture unavailable",
                 "sourceSha256": sha(source), "fixturePresent": False},
                compile_notebook_preview(source))
    try:
        rendered = compile_notebook_preview(source, fixture_path=fixture)
    except (OSError, ValueError, NotebookProjectionRefused) as exc:
        return ({"status": "FAIL", "reason": "Present notebook failed to render: " + str(exc)}, None)
    return ({"status": "PASS", "reason": "Original public DSL compiled and rendered, offline and read-only",
             "sourceSha256": sha(source), "fixtureSha256": sha(fixture) if fixture else None,
             "emittedHtmlSha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest()}, rendered)


def shared_witness() -> tuple[dict[str, Any], str | None]:
    if not IR_WITNESS.is_file():
        return ({"status": "UNVERIFIED", "reason": "No independent tab IR witness"}, None)
    try:
        ir = json.loads(IR_WITNESS.read_text(encoding="utf-8"))
        html = render_notebook_html(ir, fixture={
            "processes": [{"pid": "synthetic-101", "name": "sample-server", "cpu": 2}],
            "all_processes": [{"pid": "synthetic-101", "name": "sample-server"}],
            "connections": [{"local": "127.0.0.1:1000", "state": "LISTEN"}],
            "hardware": {"cpu": "5%", "gpu": "not-requested"},
        })
    except (ValueError, OSError, KeyError) as exc:
        return ({"status": "FAIL", "reason": str(exc)}, None)
    return ({"status": "PASS", "identity": "pinned independent IR witness (not authored Task Manager source)",
             "sha256": sha(IR_WITNESS)}, html)


def check_browser(output: Path, host_ok: bool, candidate_html: str | None,
                  witness_html: str | None) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {"status": "UNVERIFIED", "reason": "Playwright unavailable: " + str(exc)}
    exe = shutil.which("chromium") or shutil.which("chromium-browser")
    images = output / "screenshots"
    images.mkdir(parents=True, exist_ok=True)
    runs, errors = [], []
    try:
        with sync_playwright() as pw:
            opts = {"headless": True}
            if exe:
                opts.update(executable_path=exe, args=["--no-sandbox", "--disable-dev-shm-usage"])
            browser = pw.chromium.launch(**opts)
            try:
                if host_ok:
                    # Static DOM/CSS reference only. No operational JS or app URLs loaded.
                    host_markup = HOST.read_text(encoding="utf-8")
                    css = HOST_STYLE.read_text(encoding="utf-8")
                    host_html = ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                                 '<style>body{margin:0;background:#050605;color:#e6e9ea}*{box-sizing:border-box}' + css + '\n.task-manager-app{display:block!important;visibility:visible!important;height:760px;width:100%}</style></head><body>'
                                 + host_markup + '</body></html>')
                    for width in VIEWPORTS:
                        page = browser.new_page(viewport={"width": width, "height": 800}, reduced_motion="reduce")
                        request_urls = []
                        page.on("request", lambda r: request_urls.append(r.url))
                        page.set_content(host_html, wait_until="load")
                        geo = page.evaluate("""() => ({width: innerWidth, documentWidth: document.documentElement.scrollWidth,
                            tabs: document.querySelectorAll('[data-task-tab]').length,
                            buttons: document.querySelectorAll('button').length,
                            selected: document.querySelectorAll('[role=tab][aria-selected=true]').length,
                            rootVisible: getComputedStyle(document.getElementById('task-manager-app')).display !== 'none',
                            notebookHeight: Math.round(document.querySelector('.task-notebook').getBoundingClientRect().height),
                            notebookTop: Math.round(document.querySelector('.task-notebook').getBoundingClientRect().top),
                            viewportHeight: innerHeight,
                            bodyText: document.querySelector('.task-manager-app').innerText.length})""")
                        img = images / f"existing-task-manager-static-{width}.png"
                        page.screenshot(path=str(img), animations="disabled")
                        runs.append({"case": "existing-app-static", "width": width, "status": "PASS" if (
                            geo["tabs"] == 4 and geo["selected"] == 1 and geo["rootVisible"] and
                            geo["notebookHeight"] > 60 and geo["bodyText"] > 150 and not request_urls) else "FAIL",
                            "geometry": geo, "requests": request_urls, "screenshot": str(img.relative_to(output)),
                            "scope": "Inert existing HTML/CSS reference; no operational JS or live telemetry"})
                        page.close()
                for kind, html in (("authored-notebook", candidate_html), ("generic-tab-witness", witness_html)):
                    if not html:
                        continue
                    page = browser.new_page(viewport={"width": 1100, "height": 800}, reduced_motion="reduce")
                    request_urls, page_errors = [], []
                    page.on("request", lambda r: request_urls.append(r.url))
                    page.on("pageerror", lambda e: page_errors.append(str(e)))
                    page.set_content(html, wait_until="load")
                    for width in VIEWPORTS:
                        page.set_viewport_size({"width": width, "height": 800})
                        page.locator('[data-mcel-tab="connections"]').click()
                        selected = page.locator('[role=tab][aria-selected="true"]').count()
                        visible = page.locator('[role=tabpanel]:visible').count()
                        page.locator('[data-mcel-tab="connections"]').focus()
                        page.keyboard.press("ArrowRight")
                        keyboard_ok = page.locator('[data-mcel-tab="hardware"]').get_attribute("aria-selected") == "true"
                        before = page.evaluate("McelNotebookPreview.getState()")
                        rejected = page.evaluate("McelNotebookPreview.select('not-a-tab')")
                        state_same = before == page.evaluate("McelNotebookPreview.getState()")
                        updated = page.evaluate("""() => McelNotebookPreview.updateSnapshot({
                            processes:[{pid:123,name:'synthetic'}],all_processes:[],
                            connections:[{local:'127.0.0.1'}],hardware:{cpu:'synthetic'}} ,'available')""")
                        page.locator('[data-mcel-tab="connections"]').click()
                        shown = page.locator('[role=tabpanel]:visible table tbody tr').count()
                        geo = page.evaluate("""() => ({width:innerWidth, documentWidth:document.documentElement.scrollWidth,
                            panels:document.querySelectorAll('[role=tabpanel]').length,
                            effects:McelNotebookPreview.getState().effects})""")
                        good = (selected == visible == 1 and keyboard_ok and state_same and
                                rejected == {"ok": False, "code": "invalid-selection", "effects": []} and
                                updated == {"ok": True, "effects": []} and shown == 1 and
                                geo["documentWidth"] <= width + 2 and geo["panels"] == 4 and
                                geo["effects"] == [] and not request_urls and not page_errors)
                        img = images / f"{kind}-{width}.png"
                        page.screenshot(path=str(img), animations="disabled")
                        runs.append({"case": kind, "width": width, "status": "PASS" if good else "FAIL",
                                     "geometry": geo, "invalidRefused": state_same, "keyboard": keyboard_ok,
                                     "syntheticRows": shown, "requests": request_urls, "pageErrors": page_errors,
                                     "screenshot": str(img.relative_to(output)),
                                     "scope": "Real Chromium offline synthetic data; no host task operations"})
                    page.close()
            finally:
                browser.close()
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))
    return {"status": "FAIL" if errors or any(x["status"] == "FAIL" for x in runs) else
            "PASS" if runs else "UNVERIFIED", "cases": runs, "errors": errors,
            "boundary": "No operational scripts, endpoints, production bindings or task mutations executed"}


def barriers(host: dict, source: dict, witness: dict, browser: dict, lab: dict, semantic: dict | None = None, generality: dict | None = None, lifecycle: dict | None = None) -> list[dict[str, Any]]:
    def entry(name, status, evidence, repair, *, depends=()):
        return {"id": name, "status": status, "evidence": evidence,
                "dependsOn": list(depends), "nextGeneralRepair": repair}
    semantic = semantic or {"status":"UNVERIFIED"}
    generality = generality or {"status":"UNVERIFIED"}
    lifecycle = lifecycle or {"offline":"UNVERIFIED","hosted":"UNVERIFIED"}
    implementation = host.get("implementation", {})
    mobile = [x for x in browser.get("cases", [])
              if x.get("case") == "existing-app-static" and x.get("width") in (320, 390)]
    below_fold = [x["width"] for x in mobile
                  if x.get("geometry", {}).get("notebookTop", 0) >= 0.8 * x.get("geometry", {}).get("viewportHeight", 800)]
    return [
        entry("frozen-notebook-source", "PASS" if source["status"] == "PASS" else source["status"],
              [source.get("reason", ""), str(source.get("source", ""))],
              "Recover/point to the existing Phase 3 DSL and sample fixture; do not reconstruct or certify a guessed replacement"),
        entry("real-app-mount", "PARTIAL_VERIFIED" if lab["status"] == "PASS" else
              "GAP_OBSERVED" if host["status"] == "PASS" and implementation.get("legacyTabController") and not implementation.get("guardedLabHostHook") else "UNVERIFIED",
              ["Explicit lab-only normal-host mount, with exact legacy rollback, verified in Chromium" if lab["status"] == "PASS" else
               "Legacy tab controller remains the only verified host renderer",
               "Pinned IR witness is not the recovered Phase 3 DSL; local HTTP route remains unverified"],
              "Verify the recovered Phase 3 DSL in the hosted route before considering notebook promotion",
              depends=("frozen-notebook-source",)),
        entry("live-snapshot-parity", "PASS" if lifecycle.get("hosted")=="PASS" else
              "PARTIAL_VERIFIED" if lab["status"]=="PASS" else "UNVERIFIED",
              ["Live HTTP identity-set comparison: "+lifecycle.get("hosted","UNVERIFIED"),
               "Guarded synthetic snapshot delivery: "+lab["status"],
               "Unvisited hosted URL does NOT qualify as a live PASS"],
              "Exercise real telemetry lifecycle and state transitions without acquiring operational authority",
              depends=("real-app-mount",)),
        entry("semantic-architecture-generality", "PASS" if generality["status"] == "PASS" and
              generality.get("architecturalGeneralityVerified") is True else
              "FAIL" if generality["status"] == "FAIL" else "UNVERIFIED",
              ["Independent same-code process/parcel substitution, adversarial source renaming, typed value-state distinctions and registered Chromium layout: " +
               generality.get("status", "UNVERIFIED"),
               "Generality source fingerprint and fresh test observations are in semanticGenerality report",
               "Authoring cost savings and full live application correctness remain unverified"],
              "Keep source-bound independent generality gate and measure real authoring economy; preserve v1 compatibility"),
        entry("semantic-collection-pipeline", "PARTIAL_VERIFIED" if semantic["status"] == "PASS" else "UNVERIFIED",
              ["Real registered MCEL collection and shared layout handle 22 synthetic process records, exact inspector, tiers and responsive disclosure"
               if semantic["status"] == "PASS" else "No verified end-to-end semantic collection allocation",
               "Live telemetry values and the full application remain unverified"],
              "Verify identical semantic behavior on the hosted Task Manager's real snapshot lifecycle",
              depends=("real-app-mount",)),
        entry("information-fidelity", "PARTIAL_VERIFIED" if lifecycle.get("offline")=="PASS" else "UNVERIFIED",
              ["Offline measured-zero/unavailable producer-bound states and exact inspector: "+lifecycle.get("offline","UNVERIFIED"),
               "Hosted Windows producer telemetry still requires HTTP verification"],
              "Verify real producer meaning and correct availability on hosted route; do not collapse missing to zero",
              depends=("semantic-architecture-generality",)),
        entry("state-preserving-live-lifecycle", "PASS" if lifecycle.get("hosted")=="PASS" and lifecycle.get("offline")=="PASS" else
              "PARTIAL_VERIFIED" if lifecycle.get("offline")=="PASS" else "UNVERIFIED",
              ["Chromium keyed update, inspector/focus persistence, reuse refusal, partial failure, stale and duplicate refusal: "+lifecycle.get("offline","UNVERIFIED"),
               "Actual HTTP cold-load identity parity: "+lifecycle.get("hosted","BLOCKED")],
              "Run hosted Chromium cold-load and repeated refresh, preserving read-only authority",
              depends=("semantic-architecture-generality","real-app-mount")),
        entry("shared-layout-authority", "PARTIAL_VERIFIED" if semantic["status"] == "PASS" else "UNVERIFIED",
              ["The shared authority selects and allocates registered process collection fields across four widths"
               if semantic["status"] == "PASS" else "Shared field allocation is not proven",
               "Complete Task Manager hierarchy still uses scoped lab CSS rather than MCEL layout authority"],
              "Apply shared layout governance to the notebook and operator-control hierarchy",
              depends=("real-app-mount", "semantic-collection-pipeline")),
        entry("monitoring-first-presentation", "PARTIAL_VERIFIED" if lab["status"] == "PASS" else
              "GAP_OBSERVED" if below_fold else "UNVERIFIED",
              ["In explicit lab mode at narrow widths the monitor precedes operator controls" if lab["status"] == "PASS" else
               "Existing notebook begins low on 320/390px static view at " + str(below_fold),
               "Host default presentation remains unchanged"],
              "Transfer monitoring priority to MCEL shared layout, not lab-only CSS",
              depends=("shared-layout-authority",)),
        entry("governed-operational-effects", "UNVERIFIED", ["Offline notebook intentionally has no operational capabilities"],
              "Verify existing operator actions retain independent authority and no view transition can trigger them",
              depends=("real-app-mount",)),
        entry("default-design-and-flexibility", "UNVERIFIED", ["No independent visual design review or contrasting authored variants"],
              "Assess uncorrected first render, then test alternatives through the same public MCEL contract",
              depends=("shared-layout-authority",)),
        entry("authoring-economy", "UNVERIFIED", ["No equivalent-correctness baseline and total-work comparison"],
              "Measure authored decisions, code, wiring, CSS and repair effort only after application correctness",
              depends=("live-snapshot-parity", "default-design-and-flexibility")),
    ]


def run(*, output: Path = OUTPUT, source: Path = NOTEBOOK,
        fixture: Path | None = FIXTURE, browser: bool = True,
        host_url: str | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    host = check_host()
    candidate, candidate_html = notebook_candidate(source, fixture)
    witness, witness_html = shared_witness()
    browser_report = (check_browser(output, host["status"] == "PASS", candidate_html, witness_html)
                      if browser else {"status": "UNVERIFIED", "reason": "Browser disabled"})
    lab_report = (run_lab_acceptance(output / "lab-mount", browser=True) if browser else
                  {"status": "UNVERIFIED", "reason": "Browser disabled"})
    semantic_report = (run_semantic_acceptance(output / "semantic-pipeline") if browser else
                       {"status":"UNVERIFIED", "reason":"Browser disabled"})
    # Compute fresh architectural evidence in this run. Never trust a stale
    # report.json or permit a runtime-source hash mismatch to certify generality.
    generality_report = run_generality_frob(output=output / "semantic-generality", browser=browser)
    lifecycle_report=(run_lifecycle_frob(output=output / "live-lifecycle", host_url=host_url) if browser else
                      {"offline":"UNVERIFIED","hosted":"UNVERIFIED", "hostReason":"Browser disabled"})
    blocks = barriers(host, candidate, witness, browser_report, lab_report, semantic_report, generality_report, lifecycle_report)
    status = "FAIL" if (host["status"] in ("FAIL", "BLOCKED") or candidate["status"] == "FAIL"
                        or witness["status"] == "FAIL" or browser_report["status"] == "FAIL" or lab_report["status"] == "FAIL"
                        or semantic_report["status"] == "FAIL" or generality_report["status"] == "FAIL" or
                        lifecycle_report["offline"]=="FAIL" or lifecycle_report["hosted"]=="FAIL") else "INCOMPLETE"
    result = {
        "schema": SCHEMA, "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "status": status, "promiseFulfilled": False,
        "primaryApplication": "existing-task-manager", "secondaryApplication": "existing-file-manager-if-required",
        "existingApplication": host, "notebookCandidate": candidate,
        "independentGenericRendererWitness": witness, "browser": browser_report,
        "guardedLabMount": lab_report,
        "semanticCollectionPipeline": semantic_report,
        "semanticGenerality": generality_report,
        "liveLifecycle": lifecycle_report,
        "orderedBarriers": blocks,
        "releaseAuthority": {"taskManagerModified": True, "websiteBuilderModified": False,
                             "fileManagerModified": False, "operationalActionsExecuted": False,
                             "candidatePromoted": False, "canonicalPromiseStatusChanged": False},
        "historicalPromiseGapPacket": "tools/fixtures/mcel_promise_gap/tasks.v1.json (retained, unchanged)",
        "limitations": ["Static host HTML/CSS cannot prove application runtime behavior",
                        "The generic IR witness is not the original authored Phase 3 notebook",
                        "A lab-only host mount is not a production application-route or original DSL continuity proof",
                        "Screenshots are review evidence, not independent beauty certification",
                        "Offline acceptance executes no real telemetry requests or operator actions",
                        "Hosted route is BLOCKED unless an explicit host URL was reached and verified"],
    }
    (output / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Task Manager — MCEL Promise Barrier Report", "",
             f"**Promise: {status}.** No production certification or Task Manager activation.", "",
             "Primary: existing Task Manager. Secondary only if needed: existing File Manager.", "",
             "## Verification boundaries", "",
             f"- Existing host contract: {host['status']}",
             f"- Frozen Phase 3 DSL candidate: {candidate['status']}",
             f"- Independent generic tab renderer witness: {witness['status']} (not Task Manager certification)",
             f"- Offline Chromium observations: {browser_report['status']}",
             f"- Guarded lab host mount: {lab_report['status']} (bounded witness; not original DSL)",
             f"- Semantic collection and shared layout: {semantic_report['status']} (22 synthetic processes, not live telemetry)",
             f"- Independent semantic architecture generality: {generality_report['status']} (source-bound process/parcel fixtures)",
             f"- State-preserving lifecycle offline: {lifecycle_report['offline']}; hosted: {lifecycle_report['hosted']}", "",
             "## Ordered barriers", ""]
    for i, b in enumerate(blocks, 1):
        lines.extend([f"{i}. **{b['id']} — {b['status']}**. {b['nextGeneralRepair']}",
                      "   Evidence: " + "; ".join(str(x) for x in b["evidence"] if x), ""])
    lines += ["## Guardrails", "", "No live telemetry, PID, server, scheduling or AI request was executed."
              " Website Builder and legacy operational controls are unchanged.",
              "The historical Inventory/Work Orders promise packet is retained only for benchmark continuity.", ""]
    (output / "barriers.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--notebook-source", type=Path, default=NOTEBOOK)
    parser.add_argument("--notebook-fixture", type=Path, default=FIXTURE)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host-url",help="Opt-in actual hosted Task Manager read-only browser acceptance URL")
    parser.add_argument("--require-promise", action="store_true")
    args = parser.parse_args(argv)
    report = run(output=args.output, source=args.notebook_source, fixture=args.notebook_fixture,
                 browser=not args.no_browser,host_url=args.host_url)
    print("MCEL_TASK_MANAGER_PROMISE_" + report["status"])
    print("primary: existing-task-manager; secondary: existing-file-manager-if-needed")
    print("host:", report["existingApplication"]["status"], "phase3:", report["notebookCandidate"]["status"],
          "generic_renderer:", report["independentGenericRendererWitness"]["status"],
          "chromium:", report["browser"]["status"], "lab_mount:", report["guardedLabMount"]["status"],
          "semantic:", report["semanticCollectionPipeline"]["status"],
          "generality:", report["semanticGenerality"]["status"],
          "lifecycle:", report["liveLifecycle"]["offline"], "hosted:",report["liveLifecycle"]["hosted"])
    for b in report["orderedBarriers"]:
        print("barrier", b["id"], b["status"])
    print("report:", (args.output / "report.json").resolve())
    print("barriers:", (args.output / "barriers.md").resolve())
    return 1 if report["status"] == "FAIL" or args.require_promise else 0


if __name__ == "__main__":
    raise SystemExit(main())
