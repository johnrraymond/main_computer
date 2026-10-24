"""MCEL executable CSS contract: nested layout planning, isolated trial, verified execution.

Application CSS is deliberately left defective; the generic planner is responsible
for producing and proving a runtime layout, never for editing source CSS.
"""
from __future__ import annotations

from main_computer.mcel_calculator_host_projection import project as project_calculator_host

import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
APP_HTML = ROOT / "main_computer/web/applications/apps/calculator.html"
APP_CSS = ROOT / "main_computer/web/applications/styles/calculator.css"
PLANNER = SCRIPTS / "mcel-semantic-layout-inference.js"


def _browser():
    sync = pytest.importorskip("playwright.sync_api")
    binary = (os.environ.get("MCEL_CHROMIUM_EXECUTABLE") or os.environ.get("CHROMIUM_EXECUTABLE")
              or shutil.which("chromium") or shutil.which("chromium-browser"))
    with sync.sync_playwright() as playwright:
        kwargs = {"headless": True}
        if binary:
            kwargs.update({"executable_path": binary, "args": ["--no-sandbox", "--disable-dev-shm-usage"]})
        browser = playwright.chromium.launch(**kwargs)
        try:
            yield browser
        finally:
            browser.close()


def test_real_calculator_css_is_input_not_silently_rewritten():
    css = APP_CSS.read_text(encoding="utf-8")
    html = project_calculator_host(ROOT)
    assert "repeat(3, minmax(0, 1fr))" in css
    assert "overflow-wrap: anywhere" in css
    assert 'data-mcel-layout-role="adaptive-control-grid"' in html
    assert 'data-mcel-max-columns="3"' in html
    assert 'data-mcel-min-target-width="40"' in html


def test_browser_proves_candidate_selection_and_diagnostic_closure_on_real_calculator_markup():
    for browser in _browser():
        page = browser.new_page(viewport={"width": 1465, "height": 882})
        page.set_content("<style>" + APP_CSS.read_text(encoding="utf-8") + "</style>" +
                         project_calculator_host(ROOT))
        page.add_script_tag(path=str(PLANNER))
        page.add_script_tag(path=str(SCRIPTS / "mcel-self-diagnosis.js"))
        result = page.evaluate("""() => {
          const root = document.querySelector('#calculator-app');
          root.style.display = 'grid'; root.style.width = '1150px';
          document.querySelector('#calculator-graphing-panel').hidden = false;
          const container = root.querySelector('.calculator-scientific-functions');
          container.style.boxSizing = 'border-box'; container.style.width = '198px';
          const engine = window.McelSemanticLayoutInference;
          const diagnostic = () => window.McelSelfDiagnosis._private
            .detectAtomicControlLabelSplits(root,{appId:'calculator'}).map(x=>x.controlId);
          const broken = diagnostic();
          const original = container.style.cssText;
          const htmlBefore = container.outerHTML;
          const evaluated = engine.evaluateAdaptiveControlGrid(container);
          const remainedReadOnly = container.style.cssText === original && container.outerHTML === htmlBefore;
          const accepted = engine.applyAdaptiveControlGrid(container,evaluated);
          const after = diagnostic();
          const columns = getComputedStyle(container).gridTemplateColumns;
          engine.clearAdaptiveControlGrids(root);
          const restored = container.style.cssText === original;
          return {broken,remainedReadOnly,proposal:evaluated.acceptedColumns,
            candidates:evaluated.candidates.map(c=>({columns:c.columns,accepted:c.accepted})),
            applied:accepted.status,after,columns,restored,
            authorized:evaluated.applicationAuthorized,sourceMutate:evaluated.sourceMutationAuthorized};
        }""")
        assert "calculator.graphing-token-comma" in result["broken"], result
        assert "calculator.graphing-function-round" in result["broken"], result
        assert result["remainedReadOnly"], result
        assert result["proposal"] == 2, result
        assert result["candidates"][0] == {"columns": 3, "accepted": False}, result
        assert result["applied"] == "pass", result
        assert not any("graphing-token-comma" in identifier or "graphing-function-round" in identifier
                       for identifier in result["after"]), result
        assert result["restored"], result
        assert not result["authorized"] and not result["sourceMutate"]


def test_generic_control_grids_reflow_and_refuse_unsatisfied_constraints():
    for browser in _browser():
        page = browser.new_page()
        page.set_content('''<style>
          .grid {display:grid; grid-template-columns: repeat(3,minmax(0,1fr)); width:192px;
            gap:8px; padding:8px; box-sizing:border-box;}
          .grid button {font: 700 14px Arial; padding:8px; white-space:normal;
            overflow-wrap:anywhere; overflow:hidden; min-width:0;}
        </style>
        <div id="other-app">
          <div id="workflows" class="grid" data-mcel-layout-role="adaptive-control-grid"
              data-mcel-max-columns="3" data-mcel-min-target-width="40">
            <button>execute</button><button>verification</button><button>review</button>
          </div>
        </div>''')
        page.add_script_tag(path=str(PLANNER))
        info = page.evaluate("""() => {
          const root = document.querySelector('#other-app');
          const grid = root.querySelector('#workflows');
          const engine = window.McelSemanticLayoutInference;
          const proposed = engine.evaluateAdaptiveControlGrid(grid);
          const first = engine.reflowAdaptiveControlGrids(root);
          const after = [...grid.children].map(x=>engine.adaptiveContract(grid).controls.length);
          grid.style.width = '18px';
          const small = engine.reflowAdaptiveControlGrids(root);
          const afterRefusal = grid.dataset.mcelLayoutProof;
          return {proposed:proposed.acceptedColumns, first:first[0].status,
            after, small:small[0].status, afterRefusal,
            finalProof:root.dataset.mcelNestedLayoutProof};
        }""")
        assert info["proposed"] in (1, 2), info
        assert info["first"] == "pass", info
        assert info["small"] == "refused", info
        assert info["finalProof"] == "fail", info


def test_invalid_contract_fails_without_applying_any_structural_css():
    for browser in _browser():
        page = browser.new_page()
        page.set_content('''<div id="root"><div class="grid" style="display:grid"
          data-mcel-layout-role="adaptive-control-grid" data-mcel-max-columns="500">
          <button>example</button></div></div>''')
        page.add_script_tag(path=str(PLANNER))
        result = page.evaluate("""() => {
          const root = document.querySelector('#root');
          const grid = root.querySelector('.grid');
          const before=grid.style.cssText;
          const findings=window.McelSemanticLayoutInference.reflowAdaptiveControlGrids(root);
          return {status:findings[0].status,reason:findings[0].reason,
            cssUnchanged:before===grid.style.cssText,proof:root.dataset.mcelNestedLayoutProof};
        }""")
        assert result["status"] == "refused"
        assert "invalid" in result["reason"]
        assert result["cssUnchanged"] and result["proof"] == "fail"


def test_source_stays_generic_and_infers_real_workspace_minimums():
    javascript = PLANNER.read_text(encoding="utf-8")
    assert '"calculator"' not in javascript
    assert '"round"' not in javascript
    assert "minmax(${Math.min(min, Number(width))}px, 1fr)" in javascript
    node = subprocess.run(["node", "--check", str(PLANNER)], capture_output=True, text=True, timeout=20)
    assert node.returncode == 0, node.stderr


def test_workspace_plan_recursively_executes_nested_grid_without_calculator_branch():
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    app = compile_dsl_application(ROOT / "mcel_apps/calculator/application.js", write_candidate=False)
    assert app.valid and app.normalized_ir
    surface = app.normalized_ir["surfaces"][0]["semanticSurface"]
    grammar = app.normalized_ir["layouts"][0]["layoutGrammar"]
    for browser in _browser():
        page = browser.new_page(viewport={"width": 1465, "height": 882})
        page.set_content("<style>" + APP_CSS.read_text(encoding="utf-8") + "</style>" +
                         project_calculator_host(ROOT))
        page.add_script_tag(path=str(PLANNER))
        page.add_script_tag(path=str(SCRIPTS / "mcel-self-diagnosis.js"))
        proof = page.evaluate("""({surface,grammar}) => {
          const root=document.querySelector('#calculator-app');
          root.style.width='1150px'; root.style.display='grid';
          root.querySelector('#calculator-graphing-panel').hidden=false;
          root.querySelector('#calculator-mathics-panel').hidden=false;
          root.querySelector('#calculator-chat-panel').hidden=false;
          const grid=root.querySelector('.calculator-scientific-functions');
          grid.style.boxSizing='border-box'; grid.style.width='198px';
          const api=window.McelSemanticLayoutInference;
          const inferred=api.inferWorkspaceLayout({semanticSurface:surface,
            layoutGrammar:grammar, mode:'graphing',width:1142,
            visibleRegions:['arithmetic','graphing','mathics','chat']});
          api.applyWorkspaceLayout(root,inferred);
          return {workspace:root.dataset.mcelLayoutAuthority,
            nested:root.dataset.mcelNestedLayoutProof,
            grid: grid.dataset.mcelLayoutAuthority,
            columns:grid.dataset.mcelLayoutColumns,
            areas:root.querySelector('.calculator-workspace').style.gridTemplateAreas};
        }""", {"surface": surface, "grammar": grammar})
        assert proof["workspace"] == "inferred", proof
        assert proof["nested"] == "pass", proof
        assert proof["grid"] == "mcel-structural-css", proof
        assert proof["columns"] == "2", proof
        assert "graphing" in proof["areas"], proof


def test_explicit_multiline_policy_remains_usable_in_generic_layout():
    for browser in _browser():
        page=browser.new_page()
        page.set_content('''<style>.adaptive {display:grid;gap:8px;width:180px;
          grid-template-columns:repeat(2,minmax(0,1fr))}
          .adaptive button {min-width:0;padding:8px;word-break:break-all;}</style>
          <div id="root"><div class="adaptive" data-mcel-layout-role="adaptive-control-grid"
            data-mcel-max-columns="2" data-mcel-min-target-width="40">
            <button data-mcel-text-fit="allow-intraword-wrap">IntentionalWrapping</button>
            <button>Go</button></div></div>''')
        page.add_script_tag(path=str(PLANNER))
        result=page.evaluate('''() => {
          const el=document.querySelector('.adaptive');
          const api=window.McelSemanticLayoutInference;
          const evalResult=api.evaluateAdaptiveControlGrid(el);
          const applied=api.applyAdaptiveControlGrid(el,evalResult);
          return {contract:evalResult.contract.controls.length, status:applied.status,
            allowedWhiteSpace:getComputedStyle(el.firstElementChild).whiteSpace,
            requiredWhiteSpace:getComputedStyle(el.lastElementChild).whiteSpace};
        }''')
        assert result["contract"] == 2 and result["status"] == "pass", result
        assert result["requiredWhiteSpace"] == "nowrap", result
        assert result["allowedWhiteSpace"] != "nowrap", result
