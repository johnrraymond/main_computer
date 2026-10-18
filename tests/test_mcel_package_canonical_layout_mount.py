"""Patch 13: authoritative DSL -> generated layout module -> host mount -> Chromium.

The existing HTML remains the host surface. Only a declared nested subtree is
opted into canonical structural layout authority in this migration.
"""
from __future__ import annotations

import json
import os
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set

ROOT = Path(__file__).resolve().parents[1]
BROWSER = ROOT / 'main_computer/web/applications'
ENGINE = BROWSER / 'scripts/mcel-semantic-layout-inference.js'
DIAGNOSTICS = BROWSER / 'scripts/mcel-self-diagnosis.js'
HOST_RUNTIME = BROWSER / 'scripts/mcel-host-bound-application-runtime.js'


@lru_cache(maxsize=1)
def _projections():
    return build_runtime_projection_set(ROOT)


@lru_cache(maxsize=1)
def _catalog():
    return build_repository_browser_catalog_payload(ROOT)


def payload(app_id: str):
    projection = next(p for p in _projections().projections if p.app_id == app_id)
    record = next(p for p in _catalog()['packages'] if p['appId'] == app_id)
    manifest = projection.manifest
    modules = {key: projection.files[entry['path']].decode('utf8')
               for key, entry in manifest['modules'].items()}
    tree = json.loads((ROOT / f'mcel_apps/{app_id}/generated/mcel.application.normalized.json').read_text(encoding='utf8')) if (ROOT / f'mcel_apps/{app_id}/generated/mcel.application.normalized.json').exists() else None
    # Virtual generated files are authoritative, not a repository-stale file.
    module_name = 'contracts/layout.js'
    assert '"layoutTree"' in modules['layout'], modules['layout'][:500]
    assert '"layoutTree"' in projection.files[module_name].decode('utf8')
    from main_computer.mcel_application_packages import build_application_package_catalog
    source = next(x for x in build_application_package_catalog(ROOT).packages if x.app_id == app_id)
    ir = json.loads(source.files['generated/mcel.application.normalized.json'])
    methods = [intent['runtimeMethod'] for intent in ir['intents']]
    return {'record': record, 'manifest': manifest, 'sources': modules, 'methods': methods,
            'fingerprint': _catalog()['catalogFingerprint'], 'appId': app_id}


def _browser():
    sync = pytest.importorskip('playwright.sync_api')
    binary = (os.getenv('MCEL_CHROMIUM_EXECUTABLE') or os.getenv('CHROMIUM_EXECUTABLE')
              or shutil.which('chromium') or shutil.which('chromium-browser'))
    with sync.sync_playwright() as playwright:
        kwargs = {'headless': True}
        if binary: kwargs.update({'executable_path': binary, 'args': ['--no-sandbox', '--disable-dev-shm-usage']})
        browser = playwright.chromium.launch(**kwargs)
        try: yield browser
        finally: browser.close()


def _page(browser, app_id):
    page = browser.new_page(viewport={'width': 1465, 'height': 882})
    html = (BROWSER / f'apps/{app_id}.html').read_text(encoding='utf8')
    css = (BROWSER / f'styles/{app_id}.css').read_text(encoding='utf8')
    page.set_content('<style>' + css + '</style>' + html)
    page.add_script_tag(path=str(DIAGNOSTICS))
    page.add_script_tag(path=str(ENGINE))
    page.add_script_tag(path=str(HOST_RUNTIME))
    return page


async_mount = '''async (data) => {
  const facade = {};
  for (const method of data.methods) facade[method] = (...args) => ({ok:true, method, args});
  window[data.manifest.runtime.facade] = Object.freeze(facade);
  const catalog = {catalogFingerprint:data.fingerprint,
    getPackage:(id)=>id===data.appId ? data.record : null,
    listPackages:()=>[data.record]};
  const namespace = async (_url, entry) => {
    const text = data.sources[Object.keys(data.manifest.modules).find(
      k => data.manifest.modules[k].path===entry.path)];
    if (!text) throw Error('Missing projected ESM '+entry.path);
    const binary = btoa(unescape(encodeURIComponent(text)));
    return import('data:text/javascript;base64,'+binary);
  };
  const mount=await window.McelHostBoundApplicationRuntime.mountApplication({
    appId:data.appId,packageCatalog:catalog,manifest:data.manifest,moduleLoader:namespace
  });
  return {status:mount.layoutStatus,active:mount.active,
    source:mount.modules.layout.layoutTree?.schema, rootStatus:mount.root.dataset.mcelHostBoundStatus};
}'''


def test_both_real_packages_project_canonical_ir_without_html_attribute_authority():
    for app_id, selector in [('calculator','.calculator-shell'),
                             ('code-editor','.code-studio-shell')]:
        projected=payload(app_id)
        assert projected['manifest']['runtime']['mode']=='host-bound'
        assert projected['manifest']['source']['packageFingerprint']==projected['record']['fingerprint']
        assert selector in projected['sources']['layout']
        assert 'layoutTree' in projected['sources']['layout']
        # No new canonical HTML attributes or copied presentation sources.
        assert not any(x.startswith('src/') for x in next(p for p in _projections().projections
                                                        if p.app_id==app_id).files)


def test_calculator_real_host_defers_hidden_graph_then_mounts_and_unmounts_canonical_layout():
    for browser in _browser():
        page=_page(browser,'calculator')
        page.evaluate('''() => {
          const app=document.querySelector('#calculator-app');
          app.style.display='grid'; app.style.width='1150px';
          const shell=app.querySelector('.calculator-shell'); shell.hidden=true; shell.style.setProperty('display','none','important');
        }''')
        before=page.evaluate('''() => {
          const e=document.querySelector('#calculator-scientific-keypad');
          return {css:document.querySelector('style').textContent, style:e.getAttribute('style')};
        }''')
        mounted=page.evaluate(async_mount,payload('calculator'))
        assert mounted['status']=='deferred' and mounted['active'],mounted
        visible=page.evaluate('''() => {
          const app=document.querySelector('#calculator-app'), p=app.querySelector('#calculator-graphing-panel');
          const shell=app.querySelector('.calculator-shell');shell.hidden=false; shell.style.removeProperty('display');
          p.hidden=false;
          const mount=McelHostBoundApplicationRuntime.getMount('calculator');
          const result=mount.refreshLayout();
          return {width:app.querySelector('#calculator-scientific-keypad').getBoundingClientRect().width, status:result?.status};
        }''')
        assert visible['width'] > 0 and visible['status']=='pass',visible
        page.wait_for_function("document.querySelector('#calculator-app').dataset.mcelHostBoundLayoutStatus === 'pass'", timeout=20000)
        state=page.evaluate('''() => {
          const r=document.querySelector('#calculator-app');
          const host=r.querySelector('#calculator-scientific-keypad');
          const mount=McelHostBoundApplicationRuntime.getMount('calculator');
          const grid=host.querySelector('.calculator-scientific-functions');
          return {status:mount.layoutStatus, independent:mount.layoutReport?.initial?.independent,
            authority:r.querySelector('.calculator-shell').dataset.mcelCanonicalLayoutAuthority,
            columns:getComputedStyle(grid).gridTemplateColumns.split(' ').length,
            bad:McelSelfDiagnosis._private.detectAtomicControlLabelSplits(host,{appId:'calculator'}).length,
            children:[...grid.children].length,
            layoutStatus:r.dataset.mcelHostBoundLayoutStatus};
        }''')
        assert state['status']=='pass' and state['layoutStatus']=='pass',state
        assert state['authority']=='mcel-layout-tree.v1' and state['columns']==3,state
        assert state['bad']==0 and state['children']==21,state
        after=page.evaluate('''() => {
          const r=document.querySelector('#calculator-app');
          const host=r.querySelector('#calculator-scientific-keypad');
          McelHostBoundApplicationRuntime.getMount('calculator').unmount();
          return {layoutStatus:r.dataset.mcelHostBoundLayoutStatus || null,
            claim:r.querySelector('.calculator-shell').dataset.mcelCanonicalLayoutClaimed || null,
            authority:r.querySelector('.calculator-shell').dataset.mcelCanonicalLayoutAuthority || null,
            css:document.querySelector('style').textContent};
        }''')
        assert after['layoutStatus'] is None and after['claim'] is None and after['authority'] is None,after
        assert after['css']==before['css']


def test_code_editor_real_host_mounts_bounded_nested_surface_after_visibility():
    for browser in _browser():
        page=_page(browser,'code-editor')
        mounted=page.evaluate(async_mount,payload('code-editor'))
        assert mounted['status']=='deferred',mounted
        state=page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app');
          root.style.setProperty('display','block','important');
          root.style.width='1220px';root.style.height='800px';root.dataset.mcelLayoutLive='true';
          const rail=root.querySelector('.code-studio-activitybar');
          rail.style.setProperty('display','grid','important');
          rail.style.width='52px';rail.style.height='370px';
          rail.style.padding='2px';
          const width=rail.getBoundingClientRect().width;
          return {width};
        }''')
        assert state['width']>0,state
        page.wait_for_function("document.querySelector('#code-editor-app').dataset.mcelHostBoundLayoutStatus === 'pass'", timeout=20000)
        proof=page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app');
          const mount=McelHostBoundApplicationRuntime.getMount('code-editor');
          root.style.width='1220px';root.style.height='800px';root.dataset.mcelLayoutLive='true';
          const rail=root.querySelector('.code-studio-activitybar');
          const result={status:mount.layoutStatus,authority:root.querySelector('.code-studio-shell').dataset.mcelCanonicalLayoutAuthority,
            columns:getComputedStyle(rail).gridTemplateColumns.split(' ').length,
            controls:rail.querySelectorAll('button').length};
          mount.unmount();
          result.unmounted=!root.querySelector('.code-studio-shell').dataset.mcelCanonicalLayoutAuthority;
          return result;
        }''')
        assert proof=={'status':'pass','authority':'mcel-layout-tree.v1','columns':1,'controls':5,'unmounted':True},proof


def test_no_canonical_layout_engine_fails_closed_before_host_mount():
    for browser in _browser():
        page=_page(browser,'calculator')
        page.evaluate('delete window.McelSemanticLayoutInference')
        outcome=page.evaluate('''async data => {
          const facade={};for(const method of data.methods)facade[method]=()=>({ok:true});
          window[data.manifest.runtime.facade]=facade;
          const catalog={catalogFingerprint:data.fingerprint,getPackage:()=>data.record,listPackages:()=>[data.record]};
          try {
            await McelHostBoundApplicationRuntime.mountApplication({
              appId:data.appId,manifest:data.manifest,packageCatalog:catalog,
              moduleLoader:async (_u,entry)=> {
                const key=Object.keys(data.manifest.modules).find(k=>data.manifest.modules[k].path===entry.path);
                return import('data:text/javascript;base64,'+btoa(unescape(encodeURIComponent(data.sources[key]))));
              }
            });return {accepted:true};
          } catch (error) {
            return {accepted:false,message:error.message,rootStatus:document.querySelector('#calculator-app').dataset.mcelHostBoundStatus};
          }
        }''',payload('calculator'))
        assert outcome['accepted'] is False and 'layout runtime is unavailable' in outcome['message'],outcome
        assert outcome['rootStatus']=='error',outcome
