"""Executable MCEL group planning: nested grid and sibling must be judged together."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'
HTML = ROOT / 'main_computer/web/applications/apps/calculator.html'
CSS = ROOT / 'main_computer/web/applications/styles/calculator.css'
PLANNER = SCRIPTS / 'mcel-semantic-layout-inference.js'


def _browser():
    sync = pytest.importorskip('playwright.sync_api')
    binary = (os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or os.environ.get('CHROMIUM_EXECUTABLE')
              or shutil.which('chromium') or shutil.which('chromium-browser'))
    with sync.sync_playwright() as playwright:
        args = {'headless': True}
        if binary:
            args.update({'executable_path': binary, 'args': ['--no-sandbox', '--disable-dev-shm-usage']})
        browser = playwright.chromium.launch(**args)
        try:
            yield browser
        finally:
            browser.close()


def test_real_calculator_group_recovers_dense_layout_without_css_source_edits():
    assert 'data-mcel-layout-role="adaptive-control-group"' in HTML.read_text(encoding="utf-8")
    assert 'grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr)' in CSS.read_text(encoding="utf-8")
    for browser in _browser():
        page = browser.new_page(viewport={'width':1465,'height':882})
        page.set_content('<style>' + CSS.read_text(encoding="utf-8") + '</style>' + HTML.read_text(encoding="utf-8"))
        page.add_script_tag(path=str(PLANNER))
        page.add_script_tag(path=str(SCRIPTS / 'mcel-self-diagnosis.js'))
        result = page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');root.style.display='grid';root.style.width='1150px';
          root.querySelector('#calculator-graphing-panel').hidden=false;
          const group=root.querySelector('#calculator-scientific-keypad');
          group.style.width='406.6px';group.style.boxSizing='border-box';
          const grid=group.querySelector('.calculator-scientific-functions');
          const api=window.McelSemanticLayoutInference;
          const original=group.style.cssText,gridOriginal=grid.style.cssText;
          const independent=()=>window.McelSelfDiagnosis._private.detectAtomicControlLabelSplits(root,{appId:'calculator'}).map(x=>x.controlId);
          const before=independent();
          const trial=api.evaluateAdaptiveControlGroup(group);
          const trialReadonly=group.style.cssText===original && grid.style.cssText===gridOriginal;
          const valid=trial.candidates.filter(c=>c.accepted);
          const applied=api.applyAdaptiveControlGroup(group,trial);
          const after=independent();
          const selected={columns:applied.columns,share:applied.share,quality:applied.quality,
            innerColumns:grid.dataset.mcelLayoutColumns,groupProof:group.dataset.mcelLayoutProof,
            groupCols:getComputedStyle(group).gridTemplateColumns};
          api.clearAdaptiveControlGrids(root);
          return {before,after,trialReadonly,valid:valid.length,
            status:applied.status,selected,restored:group.style.cssText===original && grid.style.cssText===gridOriginal};
        }''')
        assert 'calculator.graphing-token-comma' in result['before'], result
        assert result['trialReadonly'], result
        assert result['valid'] >= 1 and result['status']=='pass', result
        assert result['selected']['columns'] == 3, result
        assert result['selected']['quality']['rows'] == 7, result
        assert result['selected']['quality']['emptyCells'] == 0, result
        assert result['selected']['groupProof'] == 'pass', result
        assert result['after'] == [], result
        assert result['restored'], result


def test_shared_engine_handles_unrelated_two_region_group():
    for browser in _browser():
        page=browser.new_page()
        page.set_content('''<style>
          .pair {display:grid;width:395px;gap:12px;padding:12px;box-sizing:border-box;
            grid-template-columns:minmax(0,0.9fr) minmax(0,1.1fr)}
          .pair > div {display:grid;min-width:0;gap:8px;padding:10px;border:1px solid gray;box-sizing:border-box}
          .actions {grid-template-columns:repeat(3,minmax(0,1fr))}
          button {min-width:0;min-height:40px;padding:8px;font:700 13px Arial;
            white-space:normal;overflow-wrap:anywhere;overflow:hidden}
        </style><div id="app"><div class="pair" data-mcel-layout-role="adaptive-control-group">
          <div class="side"><button>Start</button><button>Inspect</button></div>
          <div class="actions" data-mcel-layout-role="adaptive-control-grid" data-mcel-max-columns="3" data-mcel-min-target-width="40">
           <button>gamma</button><button>delta</button><button>epsilon</button>
           <button>alpha</button><button>omega</button><button>sigma</button>
          </div>
        </div></div>''')
        page.add_script_tag(path=str(PLANNER))
        result=page.evaluate('''() => {
          const root=document.querySelector('#app'),group=root.querySelector('.pair');
          const api=window.McelSemanticLayoutInference;
          const e=api.evaluateAdaptiveControlGroup(group),applied=api.applyAdaptiveControlGroup(group,e);
          return {status:applied.status,cols:applied.columns,rows:applied.quality?.rows,
            results:api.reflowAdaptiveControlGrids(root).map(r=>({status:r.status,columns:r.columns})),
            proof:root.dataset.mcelNestedLayoutProof};
        }''')
        assert result['status']=='pass', result
        assert result['cols'] in (2,3), result
        assert result['results'][0]['status']=='pass', result
        assert result['proof'] in ('pass','observed-only'), result


def test_group_refuses_invalid_contract_without_mutation():
    for browser in _browser():
        page=browser.new_page()
        page.set_content('''<div id="app"><div data-mcel-layout-role="adaptive-control-group"
          style="display:grid;grid-template-columns:1fr 1fr"><button>A</button><button>B</button></div></div>''')
        page.add_script_tag(path=str(PLANNER))
        result=page.evaluate('''() => {
          const app=document.querySelector('#app'),el=app.firstElementChild;
          const original=el.style.cssText;
          const result=window.McelSemanticLayoutInference.reflowAdaptiveControlGrids(app);
          return {status:result[0].status,reason:result[0].reason,unchanged:el.style.cssText===original,proof:app.dataset.mcelNestedLayoutProof};
        }''')
        assert result['status']=='refused', result
        assert 'adaptive grid' in result['reason'], result
        assert result['unchanged'] and result['proof']=='fail', result


def test_planner_remains_generic_and_has_no_source_repair_authority():
    source=PLANNER.read_text(encoding="utf-8")
    assert 'calculator.graphing' not in source
    assert 'round' not in source
    assert 'sourceMutationAuthorized:false' in source
    assert 'candidateGroupEvidence' in source


def test_group_responsive_candidates_reflow_and_recover_without_editing_source():
    for browser in _browser():
        page=browser.new_page()
        page.set_content('<style>' + CSS.read_text(encoding="utf-8") + '</style>' + HTML.read_text(encoding="utf-8"))
        page.add_script_tag(path=str(PLANNER))
        result=page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');root.style.display='grid';root.style.width='1150px';
          const group=root.querySelector('#calculator-scientific-keypad');group.style.boxSizing='border-box';
          const api=window.McelSemanticLayoutInference;
          const results=[];
          for (const width of [406.6,310,260,406.6]) {
            group.style.width=width+'px';
            const one=api.reflowAdaptiveControlGrids(root)[0];
            results.push({width,status:one.status,columns:one.columns,
              emptyCells:one.quality?.emptyCells,rows:one.quality?.rows,mode:one.groupMode});
          }
          return {results,proof:root.dataset.mcelNestedLayoutProof};
        }''')
        assert [x['status'] for x in result['results']] == ['pass'] * 4, result
        assert [x['columns'] for x in result['results']] == [3,3,3,3], result
        assert [x['mode'] for x in result['results']] == ['side-by-side','stacked','stacked','side-by-side'], result
        assert result['results'][0]['rows'] == 7, result
        assert result['results'][0]['emptyCells'] == 0, result
        # The independent diagnosis script is not present in this test: never
        # misrepresent isolated planner evidence as independent certification.
        assert result['proof']=='observed-only', result
