"""Browser acceptance of bounded, independently scrollable MCEL applications.

This frob renders production MCEL host HTML with all external effects disabled.
It refuses a PASS if page/root scroll, if sibling content moves when another
region scrolls, or if a filled child region becomes inaccessible. No real SLH,
process-manager, or file operation is performed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'runtime/reports/mcel-layout-frob/viewport-scroll'
VIEWPORTS = ((390, 650), (1040, 768), (1440, 900))


def hosts():
    # Fail closed when a new packaged MCEL app is added without an isolated
    # browser host in this gate. Do not silently claim all-app certification.
    packaged={p.name for p in (ROOT/'mcel_apps').iterdir()
              if p.is_dir() and (p/'application.js').is_file()}
    uncovered=packaged-{'calculator','code-editor','slh','task-manager'}
    if uncovered:
        raise ValueError('MCEL viewport frob lacks package host: '+', '.join(sorted(uncovered)))
    from main_computer.mcel_slh_application import render_slh_page
    from main_computer.mcel_slh_network_host import render_network_slh_page
    from main_computer.mcel_semantic_other_apps_host import render_semantic_page
    from main_computer.mcel_calculator_expanded_host import render_expanded_page
    from main_computer.mcel_semantic_generated_host import render_generated_page
    from main_computer.mcel_applications_shell_host import render_applications_shell_candidate
    from main_computer.mcel_chat_console_host import render_chat_console_page
    return {
        'slh-local': render_slh_page(),
        'slh-network': render_network_slh_page(),
        'file-manager': render_semantic_page('file-manager'),
        'task-manager': render_semantic_page('task-manager'),
        'code-editor': render_semantic_page('code-editor'),
        'calculator': render_expanded_page(),
        'generated-monitor': render_generated_page(),
        'applications-shell': render_applications_shell_candidate(),
        'chat-console': render_chat_console_page(),
    }


# The probe operates on semantic role/zone attributes, never app-specific CSS.
# It exercises both the native document and the assigned MCEL viewport.
PROBE = r'''() => {
  const app = document.querySelector('#mcel-workbench, #mcel-expanded-root');
  if (!app || !app.querySelector('[data-mcel-region]') ||
      !app.hasAttribute('data-mcel-semantic-operational'))
    return {ok:false, reason:'semantic-application-not-mounted'};
  const region = app.querySelector('[data-mcel-region]');
  const planned = app.querySelector('[data-mcel-workspace-layout]');
  const pick = selectors => selectors.map(s => app.querySelector(s)).find(Boolean);
  const nav = pick([
    '[data-mcel-zone="navigator"]', '[data-mcel-purpose="navigation"]',
    '[data-mcel-zone="controls"]', '[data-mcel-purpose="query-controls"]',
    '[data-mcel-purpose="mode-control"]', '[data-mcel-purpose="input"]']);
  const work = pick([
    '[data-mcel-zone="workspace"]',
    '[data-mcel-purpose="workspace"][data-mcel-content-kind="collection"]',
    '[data-mcel-content-kind="document"][data-mcel-region]',
    '[data-mcel-purpose="results"][data-mcel-region]',
    '[data-mcel-purpose="input"][data-mcel-region]',
    '[data-mcel-purpose="workspace"][data-mcel-region]']);
  if (!nav || !work || nav === work || nav.contains(work) || work.contains(nav))
    return {ok:false, reason:'distinct-navigation-and-workspace-regions-not-found',
      nav:nav?.getAttribute('data-mcel-purpose'),work:work?.getAttribute('data-mcel-purpose')};
  const viewHeight = innerHeight;
  const css = x=>getComputedStyle(x);
  const mid = x=>{const r=x.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
  const before={document:document.documentElement.scrollHeight,body:document.body.scrollHeight,
    app:mid(app),root:mid(region),nav:mid(nav),work:mid(work)};
  function addOverflow(host,label){
    const group=document.createElement('div');group.setAttribute('data-mcel-frob-overflow',label);
    group.style.cssText='display:block;min-width:0;';
    for(let i=0;i<55;i++){
      const line=document.createElement('div');line.textContent=label+' item '+i;
      line.style.cssText='height:32px;min-height:32px;padding:4px;box-sizing:border-box;';
      group.appendChild(line);
    }
    host.appendChild(group);
  }
  addOverflow(nav,'navigation');addOverflow(work,'workspace');
  // Force geometry and test actual scroll ownership, not just CSS declarations.
  const after={app:mid(app),root:mid(region),nav:mid(nav),work:mid(work),
    docHeight:document.documentElement.scrollHeight,bodyHeight:document.body.scrollHeight,
    appScrollHeight:app.scrollHeight,appClientHeight:app.clientHeight,
    rootScrollHeight:region.scrollHeight,rootClientHeight:region.clientHeight,
    navHeight:nav.clientHeight,navScrollHeight:nav.scrollHeight,
    workHeight:work.clientHeight,workScrollHeight:work.scrollHeight,
    navOverflow:css(nav).overflowY,workOverflow:css(work).overflowY};
  window.scrollTo(0, 2000);app.scrollTop=10000;region.scrollTop=10000;
  const pageScroll=scrollY,appScroll=app.scrollTop,rootScroll=region.scrollTop;
  const workBefore=mid(work); const navBefore=mid(nav);
  nav.scrollTop=120;const navDelta=nav.scrollTop;
  const workAfterNav=mid(work);
  work.scrollTop=160;const workDelta=work.scrollTop;
  const navAfterWork=mid(nav);
  const epsilon=1.5;
  const checks={
    document_bounded:after.docHeight <= viewHeight+1 && after.bodyHeight<=viewHeight+1 && pageScroll===0,
    app_bounded:after.app.h>0 && after.app.y>=-epsilon && after.app.y+after.app.h<=viewHeight+epsilon && appScroll===0,
    no_root_clipping:after.appScrollHeight<=after.appClientHeight+1 &&
      after.rootScrollHeight<=after.rootClientHeight+1,
    root_bounded:after.root.h>0 && after.root.y>=-epsilon && after.root.y+after.root.h<=viewHeight+epsilon && rootScroll===0,
    nav_bounded:after.nav.h>0 && after.nav.y>=-epsilon && after.nav.y+after.nav.h<=viewHeight+epsilon,
    workspace_bounded:after.work.h>0 && after.work.y>=-epsilon && after.work.y+after.work.h<=viewHeight+epsilon,
    navigation_scrollable:['auto','scroll'].includes(after.navOverflow) &&
      after.navScrollHeight>after.navHeight+50 && navDelta>0,
    workspace_scrollable:['auto','scroll'].includes(after.workOverflow) &&
      after.workScrollHeight>after.workHeight+50 && workDelta>0,
    independent_scroll:Math.abs(workBefore.y-workAfterNav.y)<epsilon && Math.abs(navBefore.y-navAfterWork.y)<epsilon,
    no_horizontal_page_overflow:document.documentElement.scrollWidth<=innerWidth+1,
  };
  return {ok:Object.values(checks).every(Boolean),checks,before,after,
    appliedScroll:{pageScroll,appScroll,rootScroll,navDelta,workDelta},planned:!!planned};
}'''


# Chat Console has its own canonical semantic renderer; it is not a
# [data-mcel-region] workbench and needs a distinct host-level containment
# probe while sharing the same independently authored composition adjudicator.
CHAT_PROBE = r'''() => {
 const app=document.querySelector('#chat-console-app');
 const nav=app?.querySelector('.chat-thread-rail');
 const work=app?.querySelector('.chat-thread-workspace');
 if(!app||!work)return {ok:false,reason:'chat-console-workspace-not-mounted'};
 const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
 const start={app:rect(app),work:rect(work),nav:nav?rect(nav):null};
 const add=e=>{const fixture=document.createElement('div');fixture.dataset.mcelFrobOverflow='true';
   for(let i=0;i<60;i++){const n=document.createElement('div');n.textContent='Scroll test '+i;
     n.style.cssText='min-height:32px';fixture.appendChild(n);}e.appendChild(fixture);};
 if(nav&&nav.getClientRects().length)add(nav);
 add(work);app.scrollTop=900;window.scrollTo(0,900);
 const appScroll=app.scrollTop,docScroll=scrollY;
 const navBefore=nav?rect(nav):null,workBefore=rect(work);
 if(nav)nav.scrollTop=160;
 const workAfterNav=rect(work);
 work.scrollTop=200;
 const navAfterWork=nav?rect(nav):null;
 const checks={
  document_bounded:document.documentElement.scrollHeight<=innerHeight+1 &&
    document.body.scrollHeight<=innerHeight+1 && docScroll===0,
  app_bounded:app.scrollHeight<=app.clientHeight+1 && appScroll===0 &&
    start.app.y>=-1 && start.app.y+start.app.h<=innerHeight+1,
  workspace_scrollable:work.scrollHeight>work.clientHeight+50 && work.scrollTop>0 &&
    ['auto','scroll'].includes(getComputedStyle(work).overflowY),
  independent_scroll:Math.abs(workBefore.y-workAfterNav.y)<=1 &&
    (!nav||Math.abs(navBefore.y-navAfterWork.y)<=1),
  navigation_scrollable:!nav||nav.getClientRects().length===0||
    (nav.scrollHeight>nav.clientHeight+50 && nav.scrollTop>0 &&
     ['auto','scroll'].includes(getComputedStyle(nav).overflowY)),
  no_horizontal_page_overflow:document.documentElement.scrollWidth<=innerWidth+1
 };
 return {ok:Object.values(checks).every(Boolean),checks,before:start,
   after:{appScroll,docScroll,navDelta:nav?.scrollTop,workDelta:work.scrollTop}};
}'''


# The applications launcher is already viewport-bounded, but the frob must
# enforce that invariant alongside mounted semantic apps, not rely on a CSS
# declaration that could regress independently.
SHELL_PROBE = r'''() => {
  const shell=document.querySelector('.mcel-applications-shell');
  const panel=document.querySelector('.mcel-shell-launcher-panel');
  const workspace=document.querySelector('.mcel-shell-workspace');
  if(!shell||!panel||!workspace)return {ok:false,reason:'applications-shell-not-mounted'};
  const before=workspace.getBoundingClientRect();
  const fixture=document.createElement('div');fixture.setAttribute('data-mcel-frob-overflow','launcher');
  for(let i=0;i<100;i++){
    const row=document.createElement('div');row.textContent='Application '+i;
    row.style.cssText='height:30px;min-height:30px';fixture.append(row);
  }
  panel.append(fixture);
  const navScrollHeight=panel.scrollHeight,navHeight=panel.clientHeight;
  panel.scrollTop=350;
  shell.scrollTop=900;window.scrollTo(0,900);
  const after=workspace.getBoundingClientRect();
  const style=getComputedStyle(panel);
  const checks={
    document_bounded:document.documentElement.scrollHeight<=innerHeight+1 &&
      document.body.scrollHeight<=innerHeight+1 && scrollY===0,
    shell_bounded:shell.getBoundingClientRect().height<=innerHeight+1 && shell.scrollTop===0,
    launcher_scrollable:['auto','scroll'].includes(style.overflowY) &&
      navScrollHeight>navHeight+50 && panel.scrollTop>0,
    workspace_stationary:Math.abs(after.y-before.y)<=1 && Math.abs(after.x-before.x)<=1,
    no_horizontal_page_overflow:document.documentElement.scrollWidth<=innerWidth+1,
  };
  return {ok:Object.values(checks).every(Boolean),checks,
    after:{navScrollHeight,navHeight,panelScroll:panel.scrollTop,windowScroll:scrollY}};
}'''


def run(*, output=DEFAULT_OUTPUT, viewports=VIEWPORTS, chromium_executable=None):
    from playwright.sync_api import sync_playwright
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    cases=[]
    from tools import mcel_viewport_composition_oracle as composition
    try:
        pages=hosts()
        with sync_playwright() as p:
            executable=chromium_executable or shutil.which('chromium') or shutil.which('chromium-browser')
            browser=p.chromium.launch(headless=True,executable_path=executable,
                                      args=['--no-sandbox','--disable-dev-shm-usage'])
            try:
                for name, html in pages.items():
                    for w,h in viewports:
                        case={'app':name,'width':w,'height':h,'status':'UNVERIFIED'}
                        page=browser.new_page(viewport={'width':w,'height':h})
                        try:
                            # Never call a live service, including localhost.
                            # about:blank has no storage access in isolated Chromium.
                            # A no-op stub avoids a false layout failure in the calculator.
                            page.evaluate('''() => {
                              const store={getItem:()=>null,setItem:()=>{},removeItem:()=>{},clear:()=>{}};
                              Object.defineProperty(window,'localStorage',{value:store,configurable:true});
                              Object.defineProperty(window,'sessionStorage',{value:store,configurable:true});
                            }''')
                            page.evaluate('''window.fetch=async()=>({ok:false,status:503,
                              json:async()=>({error:{code:'MCEL_FROB_NO_EXTERNAL_EFFECTS'}})})''')
                            page.set_content(html,wait_until='load',timeout=20000)
                            page.wait_for_selector('.mcel-applications-shell' if name=='applications-shell'
                                                   else '#chat-console-app' if name=='chat-console'
                                                   else '[data-mcel-region]',timeout=2000)
                            observed=page.evaluate(composition.SHELL_OBSERVATION if name=='applications-shell'
                                                   else composition.CHAT_OBSERVATION if name=='chat-console'
                                                   else composition.DOM_OBSERVATION)
                            composed=composition.assess(observed)
                            # Original containment test still adds synthetic overflow.
                            # Composition is judged on normal, unmodified content first.
                            result=page.evaluate(SHELL_PROBE if name=='applications-shell' else
                                                 CHAT_PROBE if name=='chat-console' else PROBE)
                            good=result['ok'] and composed['status']=='PASS'
                            case.update({'status':'PASS' if good else 'FAIL',
                                         'evidence':result, 'composition':composed})
                            if not good:
                                folder=output/'failures';folder.mkdir(exist_ok=True)
                                screenshot=folder/f'{name}-{w}x{h}.png'
                                page.screenshot(path=str(screenshot),full_page=False)
                                case['screenshot']=str(screenshot)
                        except Exception as exc:
                            case.update({'status':'FAIL','reason':f'{type(exc).__name__}: {exc}'})
                        finally:
                            page.close()
                        cases.append(case)
                        print('MCEL_VIEWPORT_SCROLL',name,f'{w}x{h}',case['status'],
                              ','.join(case.get('composition',{}).get('failures',[])[:3]),flush=True)
            finally:
                browser.close()
    except Exception as exc:
        cases.append({'status':'FAIL','reason':f'{type(exc).__name__}: {exc}'})
    report={'schema':'mcel.viewport-composition-frob.v2',
            'status':'PASS' if cases and all(x['status']=='PASS' for x in cases) else 'FAIL',
            'scope':'Isolated browser: normal-content composition and synthetic-overflow containment; no operational effects',
            'containment_passed':sum(x.get('evidence',{}).get('ok',False) for x in cases),
            'composition_passed':sum(x.get('composition',{}).get('status')=='PASS' for x in cases),
            'cases':cases}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_VIEWPORT_SCROLL_FROB_'+report['status'],f'{sum(x["status"]=="PASS" for x in cases)}/{len(cases)}',
          'report:',output/'report.json',flush=True)
    return 0 if report['status']=='PASS' else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--widths',default='390,1040,1440')
    parser.add_argument('--chromium-executable')
    args=parser.parse_args(argv)
    dimensions={w:h for w,h in VIEWPORTS}
    widths=tuple(int(x) for x in args.widths.split(','))
    if any(not (240<=w<=4000) for w in widths):parser.error('Width outside supported range')
    return run(output=args.output,viewports=tuple((w,dimensions.get(w,768)) for w in widths),
               chromium_executable=args.chromium_executable)

if __name__=='__main__':sys.exit(main())
