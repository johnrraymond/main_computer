"""Structural and real-browser acceptance for the MCEL Lab lifecycle acid panel."""
from pathlib import Path
import json
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
JS=ROOT/'main_computer/web/applications/scripts/mcel-lifecycle-acid-lab.js'
APP=ROOT/'main_computer/web/applications/apps/mcel-lab.html'
INCLUDES=ROOT/'main_computer/web/applications.html'


def test_taguchi_array_is_pairwise_orthogonal():
    node=shutil.which('node')
    if not node:pytest.skip('Node.js unavailable')
    code=("global.window=global;require("+json.dumps(str(JS))+");"
          "process.stdout.write(JSON.stringify({valid:McelLifecycleAcidLab.validateArray(),"
          "rows:McelLifecycleAcidLab.EXPERIMENTS.length," 
          "factors:McelLifecycleAcidLab.FACTORS.map(f=>f.levels.length)}))")
    result=subprocess.run([node,'-e',code],capture_output=True,text=True,check=True)
    data=json.loads(result.stdout)
    assert data=={'valid':True,'rows':18,'factors':[2,3,3,3,3,3,3,3]}


def test_lifecycle_acid_is_visible_in_mcel_lab_and_loads_real_runtime():
    html=APP.read_text('utf-8')
    includes=INCLUDES.read_text('utf-8')
    assert html.index('id="mcel-lifecycle-acid"')<html.index('class="mcel-element-acid"')
    assert all(f'id="mcel-lifecycle-acid-{part}"' in html for part in
               ('screen','matrix','factors','preview','trace','pair-run','export'))
    assert 'mcel-lifecycle-acid-lab.js' in includes and 'mcel-lifecycle-acid-fixtures.js' in includes
    assert includes.index('mcel-lifecycle-acid-fixtures.js')<includes.index('mcel-lifecycle-acid-lab.js')
    assert 'McelUniversalSemanticLayoutLab.mount' in JS.read_text('utf-8')


def test_actual_browser_screening_and_drill_in(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from tools.mcel_lifecycle_taguchi_acid_frob import verify
    report=verify(tmp_path, widths=(390,1440), repetitions=2)
    assert report['status']=='PASS',report['errors']
    assert len(report['cases'])==2
    assert all(len(case['runs'])==18 for case in report['cases'])
    assert all(case['rollbackInjected']['ok'] and case['nativeSelection']['ok'] for case in report['cases'])


def test_lifecycle_acid_is_first_class_workspace():
    """Regression: the acid test must not be buried in the legacy proof drawer."""
    html=APP.read_text('utf-8')
    assert html.index('id="mcel-lab-workspace-lifecycle"') < html.index('class="mcel-lab-blueprint-topbar"')
    assert html.index('id="mcel-lifecycle-acid"') < html.index('id="mcel-lab-blueprint-workspace"')
    assert html.index('id="mcel-lifecycle-acid"') < html.index('class="mcel-lab-advanced-legacy"')
    assert 'data-mcel-workspace-view="blueprint"' in html


def test_lifecycle_workspace_switch_is_clickable_in_browser():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    css=ROOT/'main_computer/web/applications/styles/mcel-lab.css'
    with sync_playwright() as p:
        browser=_chrome(p,None)
        for width in (390,1440):
            page=browser.new_page(viewport={'width':width,'height':900})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.set_content('<!DOCTYPE html><html><body>'+APP.read_text('utf-8')+'</body></html>')
            page.add_style_tag(content=css.read_text('utf-8'))
            page.evaluate("document.getElementById('mcel-lab-app').style.display='block'")
            page.add_script_tag(path=str(JS))
            assert page.locator('.mcel-lab-blueprint-topbar').is_visible()
            assert not page.locator('#mcel-lifecycle-acid').is_visible()
            page.locator('#mcel-lab-workspace-lifecycle').click()
            assert page.locator('#mcel-lifecycle-acid').is_visible()
            assert page.locator('#mcel-lifecycle-acid-factors select').count()==8
            assert not page.locator('.mcel-lab-blueprint-topbar').is_visible()
            assert not page.locator('.mcel-lab-advanced-legacy').is_visible()
            page.locator('#mcel-lab-workspace-blueprint').click()
            assert page.locator('.mcel-lab-blueprint-topbar').is_visible()
            assert not page.locator('#mcel-lifecycle-acid').is_visible()
            assert not errors,errors
            page.close()
        browser.close()
