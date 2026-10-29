#!/usr/bin/env python3
"""Offline Chromium acceptance for guarded normal-host Task Manager notebook lab mount.

No Task Manager operational JS, HTTP endpoints, or host actions are executed.
Loads the exact checked-in host HTML/CSS, bundled notebook scripts, and pinned
independent IR witness. Does not certify the missing original Phase 3 DSL.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'main_computer/web/applications/apps/task-manager.html'
STYLE = ROOT / 'main_computer/web/applications/styles'
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'
OUTPUT = ROOT / 'runtime/reports/mcel-task-manager-notebook-lab'
WIDTHS = (320,390,560,1100)


def html_fixture() -> str:
    css = '\n'.join((STYLE / path).read_text(encoding='utf-8') for path in
        ('task-manager.css', 'mcel-tab-workspace.css', 'mcel-semantic-collection-workspace.css', 'mcel-task-manager-notebook-lab.css'))
    js = '\n'.join('<script>\n'+(SCRIPTS / path).read_text(encoding='utf-8')+'\n</script>' for path in
        ('mcel-view-transition.js', 'mcel-element-registry.js', 'mcel-elements-core.js',
         'mcel-semantic-layout-inference.js', 'mcel-semantic-value-contract.js', 'mcel-selection-continuity.js', 'mcel-element-retirement.js', 'mcel-observation-integrity.js', 'mcel-semantic-collection-workspace.js',
         'mcel-task-manager-process-contract.js', 'mcel-tab-workspace.js',
         'mcel-task-manager-tab-witness.js', 'mcel-task-manager-notebook-bridge.js'))
    return ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>*{box-sizing:border-box}body{margin:0;background:#050605;color:#fff}' + css +
        '\n#task-manager-app{display:block!important;visibility:visible!important;width:100%;height:760px}</style>'
        '</head><body>'+APP.read_text(encoding='utf-8') + js + '</body></html>')


def run(output: Path = OUTPUT, *, browser: bool = True) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    result = {'schema':'mcel.task-manager-notebook-lab-acceptance.v1',
        'generatedUtc':datetime.now(timezone.utc).isoformat(),
        'status':'UNVERIFIED', 'cases': [], 'errors': [],
        'sourceContinuity':'UNVERIFIED: pinned independent IR witness, original Phase 3 source absent',
        'normalRouteHttpNavigation':'UNVERIFIED: offline real host HTML, not hosted URL',
        'operatorActionsExecuted': False}
    if not browser:
        result['reason'] = 'Browser explicitly disabled'
    else:
        try:
            from playwright.sync_api import sync_playwright
            import shutil
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
            with sync_playwright() as playwright:
                kwargs={'headless':True}
                if executable: kwargs.update(executable_path=executable,args=['--no-sandbox','--disable-dev-shm-usage'])
                chrom = playwright.chromium.launch(**kwargs)
                try:
                    for width in WIDTHS:
                        page=chrom.new_page(viewport={'width':width,'height':800},reduced_motion='reduce')
                        requests=[]; errors=[]
                        page.on('request',lambda req: requests.append(req.url))
                        page.on('pageerror',lambda exc: errors.append(str(exc)))
                        page.set_content(html_fixture(),wait_until='load')
                        base=page.evaluate('''() => {
                          const root=document.querySelector('#task-manager-app');
                          return {html:root.querySelector('.task-notebook').outerHTML,
                            buttons: [...root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze')].map(b=>b.outerHTML),
                            count:root.querySelectorAll('.task-mcel-notebook-lab-host').length};
                        }''')
                        # Query opt-in never follows from the passive MCEL flag or a default URL.
                        denied=page.evaluate('''() => McelTaskManagerNotebookLab.optInHost(
                          document.querySelector('#task-manager-app'), '?mcel=on')''')
                        tampered=page.evaluate('''() => {
                          const bad=structuredClone(McelTaskManagerTabWitness);bad.effects.push({kind:'effect'});
                          return McelTaskManagerNotebookLab.mount({root:document.querySelector('#task-manager-app'),
                            ir:bad,search:'?mcel_task_notebook=lab'});
                        }''')
                        mounted=page.evaluate('''() => McelTaskManagerNotebookLab.optInHost(
                          document.querySelector('#task-manager-app'), '?mcel_task_notebook=lab')''')
                        duplicate=page.evaluate('''() => McelTaskManagerNotebookLab.optInHost(
                          document.querySelector('#task-manager-app'), '?mcel_task_notebook=lab')''')
                        # Same shape as taskManagerSnapshotCache after runTaskManagerRefresh().
                        publish=page.evaluate('''() => McelTaskManagerNotebookLab.publish({
                          processes:[{pid:123,name:'lab-server',cpu:3}],
                          all_processes:[{pid:123,name:'lab-server'}],
                          connections:[{local:'127.0.0.1:7777',state:'LISTEN'}],
                          hardware:{cpu:{overall_percent:2},gpu:{overall_percent:1}},
                          injected:{operator:'MUST NEVER CROSS'}},'available',8)''')
                        page.locator('.task-mcel-notebook-lab-host [data-mcel-tab="connections"]').click()
                        selection=page.locator('.task-mcel-notebook-lab-host [data-mcel-tab="connections"]').get_attribute('aria-selected')
                        page.locator('.task-mcel-notebook-lab-host [data-mcel-tab="connections"]').focus()
                        page.keyboard.press('ArrowRight')
                        selection_keyboard=page.locator('.task-mcel-notebook-lab-host [data-mcel-tab="hardware"]').get_attribute('aria-selected')
                        stale=page.evaluate('''() => McelTaskManagerNotebookLab.publish({processes:[{pid:999}]},'available',7)''')
                        invalid=page.evaluate('''() => McelTaskManagerNotebookLab.publish(null,'available',9)''')
                        before_error=page.evaluate('''() => McelTaskManagerNotebookLab.status()''')
                        failure=page.evaluate('''() => McelTaskManagerNotebookLab.publish(null,'failed',9)''')
                        geo=page.evaluate('''() => {
                          const root=document.querySelector('#task-manager-app');
                          const host=root.querySelector('.task-mcel-notebook-lab-host');
                          const detail=root.querySelector('.task-manager-detail');
                          const sidebar=root.querySelector('.task-manager-sidebar');
                          return {width:innerWidth,documentWidth:document.documentElement.scrollWidth,
                            labTop:Math.round(host.getBoundingClientRect().top),
                            sidebarTop:Math.round(sidebar.getBoundingClientRect().top),
                            detailTop:Math.round(detail.getBoundingClientRect().top),
                            labPanels:host.querySelectorAll('[role=tabpanel]').length,
                            labTabs:host.querySelectorAll('[role=tab]').length,
                            labVisible:host.getBoundingClientRect().height>50,
                            originalControls:root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').length,
                            extraFieldExposed:host.innerText.includes('MUST NEVER CROSS'),
                            dataStatus:McelTaskManagerNotebookLab.status().dataStatus,
                            selected:McelTaskManagerNotebookLab.status().selected};
                        }''')
                        img=output/f'lab-task-manager-{width}.png'
                        page.screenshot(path=str(img),animations='disabled')
                        rollback=page.evaluate('''() => {
                          const root=document.querySelector('#task-manager-app');
                          const result=McelTaskManagerNotebookLab.unmount(root);
                          return {result,remaining:root.querySelectorAll('.task-mcel-notebook-lab-host').length,
                            legacy:root.querySelector('.task-notebook').outerHTML,
                            buttons:[...root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze')].map(b=>b.outerHTML),
                            active:root.hasAttribute('data-mcel-notebook-lab')};
                        }''')
                        good=(base['count']==0 and denied['code']=='not-opted-in' and tampered['code']=='invalid-or-missing-ir'
                            and mounted['ok'] is True and duplicate['code']=='already-mounted'
                            and publish['ok'] is True and selection=='true' and selection_keyboard=='true'
                            and stale['code']=='stale-snapshot' and invalid['code']=='invalid-snapshot'
                            and before_error['version']==8 and failure['ok'] is True
                            and geo['dataStatus']=='failed' and geo['labPanels']==4 and geo['labTabs']==4
                            and geo['labVisible'] and geo['originalControls']==5 and not geo['extraFieldExposed']
                            and geo['documentWidth'] <= width+2
                            and (width>980 or geo['detailTop']<=geo['sidebarTop'])
                            and rollback['result']['ok'] is True and rollback['remaining']==0
                            and rollback['legacy']==base['html'] and rollback['buttons']==base['buttons']
                            and not rollback['active'] and not requests and not errors)
                        result['cases'].append({'width':width,'status':'PASS' if good else 'FAIL',
                          'denied':denied,'tampered':tampered,'mounted':mounted,'duplicate':duplicate,
                          'snapshot':publish,'stale':stale,'invalid':invalid,'failure':failure,
                          'geometry':geo,'rollback':{'status':rollback['result'],'exactLegacy':rollback['legacy']==base['html'],
                            'exactControls':rollback['buttons']==base['buttons'],'remaining':rollback['remaining']},
                          'requests':requests,'pageErrors':errors,'screenshot':str(img)})
                        page.close()
                finally:
                    chrom.close()
            result['status']='PASS' if len(result['cases'])==4 and all(c['status']=='PASS' for c in result['cases']) else 'FAIL'
        except Exception as exc:
            result['errors'].append(f'{type(exc).__name__}: {exc}')
            result['status']='FAIL'
    (output/'report.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--no-browser',action='store_true')
    a=parser.parse_args()
    report=run(a.output,browser=not a.no_browser)
    print('MCEL_TASK_MANAGER_NOTEBOOK_LAB_'+report['status'])
    for case in report['cases']:
        print('width',case['width'],case['status'])
    print('report:',(a.output/'report.json').resolve())
    return 1 if report['status']=='FAIL' else 0

if __name__=='__main__':
    raise SystemExit(main())
