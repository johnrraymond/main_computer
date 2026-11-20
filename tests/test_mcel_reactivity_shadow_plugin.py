"""Opt-in, fail-open MCEL reactivity shadow diagnostic infrastructure."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "main_computer/web/applications/scripts"
PLUGIN = JS / "mcel-reactivity-shadow-plugin.js"
REGISTRY = JS / "mcel-diagnostic-plugins.js"
RUNTIME = JS / "mcel-semantic-operational-runtime.js"


def test_existing_registry_and_semantic_hosts_are_wired():
    assert "plugins.register({id:ID" in PLUGIN.read_text(encoding="utf-8")
    assert "mcel-reactivity-shadow-plugin.js" in (ROOT / "main_computer/web/applications.html").read_text()
    for name in ("mcel_slh_application.py", "mcel_semantic_other_apps_host.py", "mcel_calculator_expanded_host.py"):
        assert "mcel-reactivity-shadow-plugin.js" in (ROOT / "main_computer" / name).read_text()
    runtime = RUNTIME.read_text()
    assert "?.beforeNotify?." in runtime
    assert "?.afterNotify?." in runtime
    assert "?.release?." in runtime
    assert "shadowObserve('display'" in runtime
    assert "if(!observation)return render()" in runtime


def test_real_registry_owns_shadow_evidence_with_no_operational_authority(tmp_path: Path):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    script = r"""
const vm=require('node:vm');
const fs=require('node:fs');
const root=JSON.parse(process.env.MCEL_TEST_ROOT);
const diagnostic=fs.readFileSync(root+'/main_computer/web/applications/scripts/mcel-diagnostic-plugins.js','utf8');
const oracle=fs.readFileSync(root+'/main_computer/web/applications/scripts/mcel-reactivity-shadow-plugin.js','utf8');
const mutations=[];
class Observer {
 constructor(cb){this.cb=cb;this.active=false;}
 observe(){this.active=true;}
 takeRecords(){return this.active?mutations.splice(0):[];}
 disconnect(){this.active=false;}
}
const window={MutationObserver:Observer,
  document:{readyState:'complete'},
  addEventListener(){},dispatchEvent(){},setTimeout,clearTimeout,
  performance,location:{pathname:'/applications/slh'}};
const globals={window,document:window.document,performance,setTimeout,clearTimeout,
  CustomEvent:class CustomEvent{constructor(type,data){this.type=type;this.detail=data.detail}}};
vm.runInNewContext(diagnostic,globals);
vm.runInNewContext(oracle,globals);
(async()=>{
 const api=window.MCELDiagnosticPlugins;
 const diag=window.MCELReactivityShadowOracle;
 const mount={};
 const state=new Map([['state:status','idle'],['state:other',0]]);
 // Default-off trace must not create any session or touch DOM.
 diag.beforeNotify(mount,'slh',state,0);
 const defaultOff=diag.capture(mount,'display','status')===null;
 const start=await api.enable('mcel-reactivity-shadow');
 diag.beforeNotify(mount,'slh',state,0);
 let observed=diag.capture(mount,'display','status');
 observed.read('state:status');observed.finish();diag.afterNotify(mount);
 // Known dependency, unrelated state changed: predicted skip and no mutation.
 state.set('state:other',1);
 diag.beforeNotify(mount,'slh',state,0);
 observed=diag.capture(mount,'display','status');observed.read('state:status');observed.finish();diag.afterNotify(mount);
 // Simulate a missed dependency: incumbent unexpectedly mutates DOM despite
 // prediction of skipping the consumer.
 state.set('state:other',2);
 diag.beforeNotify(mount,'slh',state,0);
 observed=diag.capture(mount,'display','status');observed.read('state:status');
 mutations.push({type:'characterData'});observed.finish();diag.afterNotify(mount);
 const result=(await api.run('mcel-reactivity-shadow')).result;
 const finding=api.findings().find(x=>x.pluginId==='mcel-reactivity-shadow'&&x.code==='predicted-skip-mutated');
 await api.disable('mcel-reactivity-shadow');
 diag.release(mount);
 console.log(JSON.stringify({defaultOff,start:start.status,listed:api.list().find(x=>x.id==='mcel-reactivity-shadow'),
   status:result.status,certified:result.certified,ops:result.applicationOperationsInvoked,
   statistics:result.apps[0].stats,finding:!!finding,events:result.apps[0].events,
   disabledNoCapture:diag.capture(mount,'display','status')===null}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    path = tmp_path / "shadow.cjs"
    path.write_text(script)
    result = subprocess.run(["node", str(path)], cwd=ROOT, capture_output=True,
                            text=True, timeout=15, env={**__import__("os").environ,
                            "MCEL_TEST_ROOT":json.dumps(str(ROOT))})
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["defaultOff"] is True
    assert report["start"] == "enabled"
    assert report["listed"]["enabled"] is False
    assert report["status"] == "shadow-observations-incomplete"
    assert report["certified"] is False and report["ops"] == 0
    assert report["statistics"]["predictedSkips"] == 2
    assert report["statistics"]["contradictions"] == 1
    assert report["finding"] is True
    assert report["disabledNoCapture"] is True
    assert all("idle" not in str(e) for e in report["events"])


def test_real_slh_shadow_observes_without_altering_working_display():
    from playwright.sync_api import sync_playwright
    from tests.test_mcel_seh_two_action_lifecycle_ui import _setup
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=shutil.which("chromium") or None, args=["--no-sandbox"])
        try:
            page, notebook = _setup(browser, "PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            enabled = page.evaluate("async()=>await MCELDiagnosticPlugins.enable('mcel-reactivity-shadow')")
            assert enabled['status'] == 'enabled', enabled
            field = notebook.locator('[data-mcel-operation-field="deploymentStatus"]')
            assert field.inner_text() == 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'
            for next_state in ('PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED', 'SEH_UNDEPLOYED'):
                page.evaluate('state=>{window.sehUiPhase=state}',next_state)
                completed = page.evaluate("async()=>await __mcelSlh.app.execute('intent:read-seh-deployment')")
                assert completed['disposition'] == 'completed'
                assert field.inner_text() == next_state
            report = page.evaluate("async()=>(await MCELDiagnosticPlugins.run('mcel-reactivity-shadow')).result")
            assert report['schema'] == 'mcel.reactivity.shadow.v1'
            assert report['status'] == 'shadow-observations-incomplete'
            assert report['certified'] is False
            assert report['productionSchedulerChanged'] is False
            slh, = (app for app in report['apps'] if app['appId'] == 'slh')
            assert slh['notifications'] >= 2, slh
            assert slh['consumers'] >= 1, slh
            assert slh['stats']['executions'] >= 2, slh
            assert slh['mutationSupported'] is True
            assert not errors, errors
        finally:
            browser.close()
