"""Fail-closed cross-application blast-radius gate for MCEL negotiation (experimental).

Enumerates the canonical 21-destination inventory and runs the real semantic
hosts in Chromium, in addition to the canonical parent shell. It explicitly
separates a genuine negotiation trial from a structural no-touch guard. An app
with no general solver contract is UNSUPPORTED, never silently declared PASS.

No production files are modified and no consequential service actions are run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from main_computer.mcel_applications_shell_host import compile_applications_shell, render_applications_shell_candidate
from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from main_computer.mcel_chat_console_host import compile_chat_console, render_chat_console_page
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_composition_negotiation_smoke import SmokeRefusal, negotiate
from tools.mcel_general_negotiation_smoke import NegotiationRefusal, negotiate_application, trial_browser_geometry

VIEWPORTS = (390, 800, 1260)
SEMANTIC_LOADERS = {
    'calculator': (compile_expanded, render_expanded_page),
    'file-explorer': (lambda: compile_semantic_app('file-manager'), lambda: render_semantic_page('file-manager')),
    'task-manager': (lambda: compile_semantic_app('task-manager'), lambda: render_semantic_page('task-manager')),
    'code-editor': (lambda: compile_semantic_app('code-editor'), lambda: render_semantic_page('code-editor')),
    'chat-console': (compile_chat_console, render_chat_console_page),
}
READY = {
    'calculator': ('#mcel-expanded-diagnostic', 'Semantic Calculator ready'),
    'file-explorer': ('#mcel-workbench-status', 'Semantic candidate ready'),
    'task-manager': ('#mcel-workbench-status', 'Semantic candidate ready'),
    'code-editor': ('#mcel-workbench-status', 'Semantic candidate ready'),
    'chat-console': ('body[data-mcel-chat-ready="true"]', None),
    'slh-local': ('#mcel-workbench-status', None),
    'slh-network': ('#mcel-workbench-status', None),
}
LEGACY_FRAGMENT_ALIASES = {'game-editor': 'layout-builder', 'webgl': None}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def _storage(page) -> None:
    page.evaluate('''() => {
      const memory=new Map();
      Object.defineProperty(window,'localStorage',{configurable:true,value:{
        getItem:k=>memory.get(String(k))??null,
        setItem:(k,v)=>memory.set(String(k),String(v)),
        removeItem:k=>memory.delete(String(k)), clear:()=>memory.clear(),
        key:i=>[...memory.keys()][i]??null,get length(){return memory.size}
      }});
    }''')


def _no_effect_network(page) -> None:
    """Explicitly refuse all browser network effects in this smoke."""
    page.evaluate("""() => {
      window.fetch = async () => ({ok:false,status:503,
        json: async()=>({error:{code:'MCEL_SMOKE_NETWORK_DISABLED'}}),
        text: async()=>''});
    }""")


GEOMETRY_JS = '''() => {
  const root=document.querySelector('#mcel-expanded-root, #mcel-workbench, #chat-console-app');
  if (!root) return {error:'missing-application-root'};
  const measured=e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);
    return {tag:e.tagName, id:e.id, role:e.getAttribute('data-mcel-purpose')||'',
      zone:e.getAttribute('data-mcel-zone')||'',
      x:Math.round(r.x), y:Math.round(r.y), width:Math.round(r.width),
      height:Math.round(r.height), visible:!e.hidden && s.display!=='none' &&
      s.visibility!=='hidden' && r.width>0 && r.height>0,
      scrollable:e.scrollHeight>e.clientHeight+2 && ['auto','scroll'].includes(s.overflowY)};};
  const regions=[...root.querySelectorAll('[data-mcel-region], [data-mcel-region-id]')];
  const zones=[...root.querySelectorAll('[data-mcel-zone]')];
  const all=[...new Set([...regions,...zones])];
  return {viewport:innerWidth, documentWidth:document.documentElement.scrollWidth,
    root:measured(root), regionCount:regions.length, zoneCount:zones.length,
    regions:all.map(e=>({name:e.dataset.mcelRegion||e.dataset.mcelRegionId||e.dataset.mcelZone||'', ...measured(e)})),
    interactive:root.querySelectorAll('button,input,select,textarea,[role="tab"],a[href]').length,
    scriptErrors:document.querySelectorAll('[data-error="true"]').length,
    hiddenLeaks:regions.filter(e=>e.hidden && getComputedStyle(e).display!=='none').map(e=>e.dataset.mcelRegion||e.id)
  };
}'''


def _root_shape(observation: dict) -> dict:
    """Only stable layout dimensions/roles enter the no-change comparison."""
    return {'root': observation['root'], 'regions': observation['regions'],
            'regionCount': observation['regionCount'], 'zoneCount': observation['zoneCount'],
            'interactive': observation['interactive'], 'documentWidth': observation['documentWidth']}


def _semantic_browser(browser, app: str, html: str, width: int, *, screenshots: Path | None, ir: dict | None = None, mode: str | None = None) -> dict:
    page = browser.new_page(viewport={'width':width, 'height':800}, reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda err: errors.append(str(err)[:350]))
    try:
        _storage(page)
        _no_effect_network(page)
        page.set_content(html, wait_until='domcontentloaded', timeout=20000)
        selector, expected = READY[app]
        ready = page.locator(selector).count() == 1 and (expected is None or page.locator(selector).inner_text() == expected)
        if app.startswith('slh-'):
            ready = ready and page.evaluate('Boolean(window.__mcelSlh)') and page.locator(selector).get_attribute('data-error') != 'true'
        graph_before=None
        if mode == 'graphing':
            page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
            graph_form=page.locator('form[data-intent="intent:draw-graph"]')
            graph_form.locator('input[name="expression"]').fill('sin(x)')
            graph_form.locator('button[type="submit"]').click()
            page.locator('[data-mcel-plot] svg path').wait_for(timeout=10000)
            graph_before=page.locator('[data-mcel-plot] svg path').get_attribute('d')
        before = page.evaluate(GEOMETRY_JS)
        if 'error' in before:
            return {'app':app,'width':width,'status':'FAIL','reason':before['error'],'errors':errors}
        # Isolated trial against the SAME live elements, with no production
        # renderer mutation. An unsupported host must remain unsupported.
        trial = trial_browser_geometry(page, ir, width, mode=mode) if ir is not None else None
        after = page.evaluate(GEOMETRY_JS)
        graph_updated=True
        if graph_before is not None and trial is not None and trial['status']=='TRIALED':
            graph_form.locator('input[name="expression"]').fill('cos(x)')
            graph_form.locator('button[type="submit"]').click()
            page.wait_for_function('''() => window.__mcelExpanded.app.readState('state:graph-expression')==='cos(x)' ''')
            graph_after=page.locator('[data-mcel-plot] svg path').get_attribute('d')
            graph_updated=bool(graph_after) and graph_after!=graph_before
        same = _root_shape(before) == _root_shape(after) if trial is None else             trial['status'] in {'TRIALED','BASELINE_RETAINED','UNSUPPORTED_TOPOLOGY'}
        if screenshots is not None and width in (800,1260):
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / f'negotiation-trial-{app}-{width}.png'))
        return {'app':app,'width':width,'status':'GUARD_PASS' if ready and same and graph_updated else 'FAIL',
                'ready':ready,'geometryGuardAccepted':same,'negotiationTrial':trial,'graphUpdatedAfterReflow':graph_updated,'baseline':before,
                'baselineIssues':{'horizontalOverflow':before['documentWidth']>width+2,
                                  'hiddenLeaks':before['hiddenLeaks'],
                                  'javascriptErrors':errors},
                'errors':errors}
    except Exception as exc:
        return {'app':app,'width':width,'status':'FAIL',
                'reason':f'{type(exc).__name__}: {str(exc)[:400]}','errors':errors}
    finally:
        page.close()


def _legacy_markup_browser(browser, app: str, fragment: str, width: int) -> dict:
    """Browser-parse the actual authored fragment without running legacy effects.

    This is an inert markup guard, not a live execution or layout certification.
    """
    page=browser.new_page(viewport={'width':width,'height':800}, reduced_motion='reduce')
    try:
        page.set_content('<!doctype html><html><body><main id="legacy-probe"></main></body></html>')
        observation=page.evaluate('''fragment=>{
          const parsed=new DOMParser().parseFromString(fragment,'text/html');
          const host=document.getElementById('legacy-probe');
          [...parsed.body.children].filter(el=>el.tagName!=='SCRIPT' && el.tagName!=='STYLE')
            .forEach(el=>host.appendChild(document.importNode(el,true)));
          const before={html:host.innerHTML, children:host.children.length,
            interactive:host.querySelectorAll('a[href],button,input,select,textarea,[role="tab"]').length,
            ids:[...host.querySelectorAll('[id]')].map(el=>el.id)};
          const after={html:host.innerHTML, children:host.children.length,
            interactive:host.querySelectorAll('a[href],button,input,select,textarea,[role="tab"]').length,
            ids:[...host.querySelectorAll('[id]')].map(el=>el.id)};
          return {rootNodes:before.children, interactive:before.interactive,
            uniqueIds:new Set(before.ids).size===before.ids.length,
            unchanged:JSON.stringify(before)===JSON.stringify(after)};
        }''',fragment)
        ok=(observation['rootNodes']>0 and observation['uniqueIds'] and observation['unchanged'])
        return {'app':app,'width':width,'status':'GUARD_PASS' if ok else 'FAIL',
                'scope':'inert authored markup only; execution/geometry not certified',**observation}
    except Exception as exc:
        return {'app':app,'width':width,'status':'FAIL','reason':f'{type(exc).__name__}: {str(exc)[:350]}'}
    finally:
        page.close()


def _shell_browser(browser, apps: list[dict], width: int) -> dict:
    page = browser.new_page(viewport={'width':width,'height':800}, reduced_motion='reduce')
    errors=[]
    page.on('pageerror', lambda err: errors.append(str(err)[:350]))
    try:
        page.set_content(render_applications_shell_candidate(canonical_promoted=True),wait_until='domcontentloaded')
        ready=page.locator('body').get_attribute('data-mcel-shell-ready')=='true'
        # about:blank cannot push the canonical route. The user-facing href and
        # iframe src remain unchanged and are checked for every destination.
        page.evaluate('history.pushState = function() {}')
        outcomes=[]
        for app in apps:
            slug=app['id']
            selected=page.evaluate('(id) => window.__mcelApplicationsShell.select(id, true)', slug)
            attr=page.locator('body').get_attribute('data-mcel-shell-active-app')
            expected=app['route']+'?mcel-shell-child=1' if slug!='desktop' else None
            actual=page.locator('#mcel-shell-frame').get_attribute('src')
            link=page.locator(f'[data-shell-launch="{slug}"]')
            # On desktop the launcher can use a desktop-icon link; every item
            # in the inventory must also have its stable canonical route.
            link_href=link.first.get_attribute('href') if link.count() else None
            title=page.locator('#mcel-shell-active-title').inner_text()
            ok=(bool(selected) and attr==slug and actual==expected and
                (slug=='desktop' or link_href==app['route']) and title==app['title'])
            outcomes.append({'app':slug,'status':'GUARD_PASS' if ok else 'FAIL',
                             'selected':bool(selected),'active':attr,'frameSrc':actual,
                             'linkHref':link_href,'title':title})
        return {'width':width,'ready':ready,'status':'GUARD_PASS' if ready and not errors and
                all(x['status']=='GUARD_PASS' for x in outcomes) else 'FAIL',
                'routes':outcomes,'errors':errors}
    except Exception as exc:
        return {'width':width,'status':'FAIL','reason':f'{type(exc).__name__}: {str(exc)[:400]}',
                'errors':errors}
    finally:
        page.close()


def run_battery(*, browser: bool = False, screenshots: Path | None = None) -> dict:
    """Inventory-closed blast-radius smoke. A guard pass is not solver coverage."""
    _, registry = compile_applications_shell()
    apps = registry['apps']
    if len(apps) != 21 or len({a['id'] for a in apps}) != 21:
        raise AssertionError('canonical shell inventory changed; do not skip destinations')
    if {a['id'] for a in apps if a['mount']=='semantic'} != set(SEMANTIC_LOADERS):
        raise AssertionError('semantic implementation inventory has changed; extend smoke first')
    report = {'schema':'mcel.negotiation-full-battery.v2','productionPromotion':False,
              'coverage':{'inventory':len(apps),'viewports':list(VIEWPORTS)},
              'apps':[], 'auxiliary':[], 'shell':[], 'semanticBrowser':[], 'legacyBrowser':[], 'limitations':[]}
    pages={}
    legacy_fragments={}
    from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
    slh_ir, _slh_composition = compile_slh_application()
    for name, view in (('slh-local','overview'),('slh-network','network')):
        page=render_slh_page(view)
        report['auxiliary'].append({'app':name,'mount':'semantic-generated','negotiation':'UNSUPPORTED',
            'runtime':'NOT_EXERCISED', 'route':'/applications/slh/local' if view=='overview' else '/applications/slh/network',
            'evidence':{'irSHA256':_fingerprint(slh_ir),'htmlSHA256':hashlib.sha256(page.encode()).hexdigest()},
            'promotionBlockers':[]})
        pages[name]=page
    irs={'slh-local':slh_ir,'slh-network':slh_ir}
    for app in apps:
        slug, mount=app['id'],app['mount']
        item={'app':slug,'route':app['route'],'mount':mount,
              'negotiation':'NOT_EXERCISED','runtime':'NOT_EXERCISED',
              'evidence':{},'promotionBlockers':[]}
        if mount=='semantic':
            compiler, render = SEMANTIC_LOADERS[slug]
            ir=compiler();html=render();pages[slug]=html;irs[slug]=ir
            item['evidence']['irSHA256']=_fingerprint(ir)
            item['evidence']['htmlSHA256']=hashlib.sha256(html.encode()).hexdigest()
            # One generalized region negotiator. No per-app layout tables.
            mode='graphing' if slug=='calculator' else None
            try:
                choices=[negotiate_application(ir,w,800,mode=mode) for w in VIEWPORTS]
                item['negotiation']='ELIGIBLE_TRIAL'
                item['evidence']['candidateDecisions']=[{'width':x['width'],'winner':x['winner'],
                    'semanticRegions':len(x['regions']),'candidateCount':x['candidateCount']} for x in choices]
            except (SmokeRefusal,NegotiationRefusal) as exc:
                item['negotiation']='UNSUPPORTED'
                item['promotionBlockers'].append(f'no verified general composition candidate: {exc}')
        elif mount=='legacy':
            alias=LEGACY_FRAGMENT_ALIASES.get(slug,slug)
            if alias is None:
                item['promotionBlockers'].append('game bundle excluded from main export; live game surface unavailable')
            else:
                fragment=Path('main_computer/web/applications/apps')/(alias+'.html')
                if not fragment.is_file() or not fragment.read_text(encoding='utf-8').strip():
                    item['promotionBlockers'].append('legacy application fragment missing from source export')
                else:
                    item['evidence']['legacyFragmentSHA256']=hashlib.sha256(fragment.read_bytes()).hexdigest()
                    legacy_fragments[slug]=fragment.read_text(encoding='utf-8')
            item['negotiation']='BYPASS_REQUIRED'
            item['promotionBlockers'].append('legacy runtime not represented by negotiated semantic IR; must remain unchanged')
        else:
            item['negotiation']='BYPASS_REQUIRED'
            item['evidence']['overviewOnly']=True
        report['apps'].append(item)
    for extra in report['auxiliary']:
        try:
            extra['evidence']['candidateDecisions']=[negotiate_application(slh_ir,w,800)['winner'] for w in VIEWPORTS]
            extra['negotiation']='ELIGIBLE_TRIAL'
        except NegotiationRefusal as exc:
            extra['promotionBlockers'].append(f'general negotiation refused: {exc}')
    if browser:
        from playwright.sync_api import sync_playwright
        from tools.mcel_phase2b_browser import launch_semantic_browser
        with sync_playwright() as p:
            chrome, label=launch_semantic_browser(p)
            report['browserBackend']=label
            try:
                for width in VIEWPORTS:
                    report['shell'].append(_shell_browser(chrome,apps,width))
                    for extra in report['auxiliary']:
                        report['semanticBrowser'].append(_semantic_browser(chrome,extra['app'],pages[extra['app']],width, screenshots=screenshots, ir=irs[extra['app']]))
                    for app in apps:
                        if app['mount']=='semantic':
                            report['semanticBrowser'].append(_semantic_browser(chrome,app['id'],pages[app['id']],width,
                                                                             screenshots=screenshots, ir=irs[app['id']],
                                                                             mode='graphing' if app['id']=='calculator' else None))
                        elif app['mount']=='legacy' and app['id'] in legacy_fragments:
                            report['legacyBrowser'].append(_legacy_markup_browser(
                                chrome,app['id'],legacy_fragments[app['id']],width))
            finally:
                chrome.close()
        for app in report['apps'] + report['auxiliary']:
            slug=app['app']
            observations=[x for x in report['semanticBrowser'] if x['app']==slug]
            if app['mount'] in {'semantic','semantic-generated'}:
                trial_states=[(x.get('negotiationTrial') or {}).get('status') for x in observations]
                if len(observations)!=len(VIEWPORTS) or any(x['status']!='GUARD_PASS' for x in observations):
                    app['runtime']='FAIL'
                elif any(state=='UNSUPPORTED_TOPOLOGY' for state in trial_states):
                    app['runtime']='PARTIAL_TRIAL'
                    app['promotionBlockers'].append('one or more viewport arrangements cannot be realized by current host topology')
                elif 'TRIALED' in trial_states:
                    app['runtime']='TRIAL_PASS'
                else:
                    app['runtime']='UNSUPPORTED'
                    app['promotionBlockers'].append('no observable allocation trial occurred')
                if app['runtime']!='GUARD_PASS':
                    app['promotionBlockers'].append('semantic runtime failed baseline mount or geometry guard')
            elif app['mount']=='legacy':
                observations=[x for x in report['legacyBrowser'] if x['app']==slug]
                app['runtime']='MARKUP_GUARD_PASS' if len(observations)==len(VIEWPORTS) and all(
                    x['status']=='GUARD_PASS' for x in observations) else 'NOT_VERIFIED'
                if app['runtime']!='MARKUP_GUARD_PASS':
                    app['promotionBlockers'].append('legacy markup unavailable or failed inert browser guard')
            else:
                app['runtime']='SHELL_ONLY'
        if any(x['status']=='FAIL' for x in report['shell']):
            report['limitations'].append('canonical shell browser selection failed at one or more widths')
    else:
        report['limitations'].append('browser not requested; no live layout or route interaction measured')
    report['coverage']['additionalMCELTargets']=len(report['auxiliary'])
    report['coverage']['semanticApps']=sum(a['mount']=='semantic' for a in apps)
    report['coverage']['legacyApps']=sum(a['mount']=='legacy' for a in apps)
    report['coverage']['negotiationEligible']=sum(a['negotiation']=='ELIGIBLE_TRIAL' for a in (report['apps']+report['auxiliary']))
    report['coverage']['negotiationUnsupported']=sum(a['negotiation']=='UNSUPPORTED' for a in (report['apps']+report['auxiliary']))
    report['coverage']['browserObservations']=len(report['semanticBrowser'])
    report['coverage']['legacyMarkupObservations']=len(report['legacyBrowser'])
    report['coverage']['shellRoutesChecked']=sum(len(x.get('routes',[])) for x in report['shell'])
    report['coverage']['guardFailures']=sum(x['status']=='FAIL' for x in report['semanticBrowser']) + sum(
        x['status']=='FAIL' for x in report['legacyBrowser']) + sum(
        x['status']=='FAIL' for x in report['shell'])
    report['coverage']['liveTrials']=sum(x.get('negotiationTrial',{}).get('status')=='TRIALED'
                                        for x in report['semanticBrowser'])
    report['coverage']['unsupportedTopologies']=sum(x.get('negotiationTrial',{}).get('status')=='UNSUPPORTED_TOPOLOGY'
                                        for x in report['semanticBrowser'])
    report['coverage']['fullyTrialedTargets']=sum(a['runtime']=='TRIAL_PASS' for a in report['apps']+report['auxiliary'])
    report['coverage']['partiallyTrialedTargets']=sum(a['runtime']=='PARTIAL_TRIAL' for a in report['apps']+report['auxiliary'])
    report['coverage']['rejectedTrials']=sum(x.get('negotiationTrial',{}).get('status')=='REJECTED'
                                        for x in report['semanticBrowser'])
    # A green experimental gate is not production certification. We have not
    # changed the production renderer, and inert legacy markup is not live
    # operational parity. Missing source-export fragments are also blockers.
    all_semantic=[a for a in report['apps']+report['auxiliary']
                  if a['mount'] in {'semantic','semantic-generated'}]
    complete=(browser and len(all_semantic)==7 and
              all(a['runtime']=='TRIAL_PASS' for a in all_semantic) and
              report['coverage']['liveTrials']>=14 and
              report['coverage']['unsupportedTopologies']==0 and
              report['coverage']['rejectedTrials']==0 and
              report['coverage']['guardFailures']==0 and
              all(y.get('graphUpdatedAfterReflow',True) for y in report['semanticBrowser']))
    report['experimentalGate']='PASS' if complete else 'FAIL'
    report['productionGate']='BLOCKED'
    report['limitations'].append('production renderer does not use the experimental negotiator')
    report['limitations'].append('legacy markup inspection is inert, not execution or operation parity')
    if any(a['mount']=='legacy' and a['runtime']=='NOT_VERIFIED' for a in report['apps']):
        report['limitations'].append('at least one legacy app asset is unavailable in this export')
    return report


def main() -> None:
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',action='store_true')
    parser.add_argument('--report',type=Path)
    parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args()
    report=run_battery(browser=args.browser,screenshots=args.screenshots)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    for item in report['apps']:
        print(f"{item['app']:<18} {item['mount']:<9} solver={item['negotiation']:<18} runtime={item['runtime']}")
    print('coverage:',json.dumps(report['coverage'],sort_keys=True))
    print('EXPERIMENTAL GATE:',report['experimentalGate'])
    print('PRODUCTION GATE:',report['productionGate'])
    # Fail closed if requested as promotion authority, but return success for
    # completed diagnostics (unambiguous BLOCKED result is a valid smoke result).
    if report['coverage']['guardFailures']:
        raise SystemExit(1)
    if args.browser and report['productionGate']!='READY':
        raise SystemExit(2)


if __name__=='__main__':
    main()
