"""Independent, browser-observed geometry oracles for the three semantic apps.

These are human-authored expectations, not outputs of workspace_plan or the
compositor. Only the isolated MCEL paired-workspace Frob uses these assertions.
No application is registered, migrated, or granted operational authority.
"""
from __future__ import annotations

# Each expected ownership decision is authored here, independent of the plan.
# Region names come from the four reference semantic application's source IR.
EXPECTED_REGION_ZONES = {
    'file-manager': {
        'locations': 'navigator', 'directory': 'workspace',
        'inspection': 'tools', 'status': 'status',
    },
    'task-manager': {
        'query': 'controls', 'processes': 'workspace', 'all': 'workspace',
        'connections': 'workspace', 'hardware': 'workspace',
        'selected': 'tools', 'schedules': 'tools', 'assistant': 'tools',
        'operations': 'tools', 'status': 'status',
    },
    'code-editor': {
        'navigation': 'navigator', 'document': 'workspace',
        'aider': 'tools', 'review': 'tools', 'documentation': 'tools',
        'studio': 'tools', 'status': 'status',
    },
}
TASK_VIEWS = {
    'server-processes': 'processes', 'all-processes': 'all',
    'connections': 'connections', 'cpu-gpu': 'hardware',
}
TOOL_TABS = {
    'task-manager': ('selected', 'schedules', 'assistant', 'operations'),
    'code-editor': ('aider', 'review', 'documentation', 'studio'),
}

# Browser-owned observations, not the plan's declared placements.
DOM_OBSERVATION = r'''() => {
 const rect=el=>{if(!el)return null;const r=el.getBoundingClientRect();
   return {x:r.x,y:r.y,w:r.width,h:r.height};};
 const root=document.querySelector('[data-mcel-presentation]');
 const layout=document.querySelector('[data-mcel-workspace-layout]');
 const zoneNodes=[...(root?.querySelectorAll('[data-mcel-zone]')||[])];
 const regionNodes=[...(root?.querySelectorAll('[data-mcel-region]')||[])];
 const zone=Object.fromEntries(zoneNodes.map(el=>[el.dataset.mcelZone,rect(el)]));
 const regions=Object.fromEntries(regionNodes.map(el=>[
   el.dataset.mcelRegion.split('.').pop(),
   {zone:el.closest('[data-mcel-zone]')?.dataset.mcelZone||null,
    rect:rect(el),visible:!!el.getClientRects().length&&!el.hidden}]));
 const tabs=[...document.querySelectorAll('[data-mcel-tool-tabs] [role="tab"]')];
 return {layout:!!layout,rung:layout?.dataset.mcelLayoutRung||null,
   zone,regions,tabCount:tabs.length,
   selectedTabs:tabs.filter(el=>el.getAttribute('aria-selected')==='true').length,
   visiblePanels:[...document.querySelectorAll('[data-mcel-tool-panel]')].filter(el=>!el.hidden).length,
   overflow:document.documentElement.scrollWidth>innerWidth+1,
   errors:[...(window.__pairedErrors||[])],ready:document.querySelector('#mcel-workbench-status')?.textContent||'',
   viewport:innerWidth,height:innerHeight};
}'''


def _overlap(a, b):
    return max(0, min(a['x'] + a['w'], b['x'] + b['w']) - max(a['x'], b['x'])) * max(
        0, min(a['y'] + a['h'], b['y'] + b['h']) - max(a['y'], b['y']))


def check_layout(app: str, observed: dict, width: int, *, interactions: dict | None = None) -> dict:
    """Reject a visually incorrect compositor even if its signed plan is valid."""
    if app not in EXPECTED_REGION_ZONES:
        raise ValueError('Unsupported oracle application: '+app)
    errors=[]
    zones=observed.get('zone') or {}
    region_map=observed.get('regions') or {}
    expected=EXPECTED_REGION_ZONES[app]
    rung='wide' if width>=980 else 'medium' if width>=680 else 'narrow'
    if not observed.get('layout'): errors.append('no-composed-layout')
    if observed.get('rung') != rung: errors.append('incorrect-responsive-rung')
    if observed.get('ready') != 'Semantic candidate ready':errors.append('candidate-not-ready')
    if observed.get('overflow'):errors.append('horizontal-overflow')
    if observed.get('errors'):errors.append('uncaught-browser-error')
    if set(region_map) != set(expected)|{'application'}: errors.append('missing-or-extra-semantic-regions')
    for region, zone_id in expected.items():
        if (region_map.get(region) or {}).get('zone') != zone_id:
            errors.append('region-not-in-expected-zone:'+region)
        if zone_id not in zones:errors.append('missing-zone:'+zone_id)
    active={key:zones[key] for key in {'controls','navigator','workspace','tools','status'} if key in zones}
    for key,r in active.items():
        if not isinstance(r,dict) or min(r.get('w',0),r.get('h',0))<=0:
            errors.append('collapsed-zone:'+key)
        elif r['x']<0 or r['x']+r['w']>width+1:
            errors.append('out-of-bounds-zone:'+key)
    # Geometric expectations are independent of both authored order and plan.
    work=zones.get('workspace')
    tools=zones.get('tools')
    status=zones.get('status')
    nav=zones.get('navigator')
    controls=zones.get('controls')
    if work and work['y']>=observed.get('height',900):errors.append('anchor-below-fold')
    if work and work['w'] < width*(.50 if rung=='wide' else .46 if rung=='medium' else .82):
        errors.append('anchor-insufficient-width')
    if status and work and status['y'] < work['y']+work['h']-2:
        errors.append('status-before-workspace-end')
    if status and tools and status['y'] < tools['y']+tools['h']-2:
        errors.append('status-before-tools-end')
    if app=='task-manager':
        if controls and work and not (controls['y']+controls['h']<=work['y']+2):
            errors.append('task-controls-not-before-workspace')
    elif nav and work:
        if rung=='narrow':
            if not (nav['y']+nav['h']<=work['y']+2):errors.append('navigation-not-above-workspace')
        elif not (nav['x']+nav['w']<=work['x']+2):
            errors.append('navigation-not-left-of-workspace')
    if tools and work:
        if rung=='wide':
            if not (work['x']+work['w']<=tools['x']+2 and abs(work['y']-tools['y'])<10):
                errors.append('tools-not-adjacent-right')
        elif tools['y'] < work['y']+work['h']-2:
            errors.append('tools-not-after-workspace')
    # Reject positional overlaps even when all regions are technically present.
    for i,first in enumerate(('controls','navigator','workspace','tools')):
        for second in ('controls','navigator','workspace','tools')[i+1:]:
            if first in active and second in active and _overlap(active[first],active[second])>1:
                errors.append('zones-overlap:'+first+':'+second)
    if app in TOOL_TABS:
        expected_count=len(TOOL_TABS[app])
        if observed.get('tabCount') != expected_count:
            errors.append('missing-tool-tabs')
        if observed.get('selectedTabs') != 1 or observed.get('visiblePanels') != 1:
            errors.append('tool-panels-not-exclusive')
    if interactions is not None:
        if interactions.get('status')!='PASS':
            errors.extend('interaction:'+x for x in interactions.get('failures', ['unverified']))
    return {'status':'PASS' if not errors else 'FAIL', 'reason':
            'Independent layout and interaction expectations satisfied' if not errors else
            'Independent semantic layout oracle mismatch',
            'failures':sorted(set(errors)), 'rung':rung, 'zones':active,
            'regionOwnership':{key:region_map.get(key,{}).get('zone') for key in expected},
            'interactions':interactions}


def browser_oracle(browser_page, app: str, width: int) -> dict:
    """Read the real rendered DOM and exercise only nonconsequential mode/tab UI."""
    observed=browser_page.evaluate(DOM_OBSERVATION)
    failures=[]
    interaction={'status':'PASS','failures':failures}
    try:
        if app=='task-manager':
            readings={}
            for mode, region in TASK_VIEWS.items():
                browser_page.locator('select[data-intent="intent:switch-view"]').select_option(mode,timeout=5000)
                browser_page.wait_for_function('(m)=>window.__mcelWorkbench.app.readState("state:view")===m',arg=mode)
                names=browser_page.evaluate('''() => [...document.querySelectorAll('[data-mcel-region]')]
                  .filter(el=>['processes','all','connections','hardware'].includes(el.dataset.mcelRegion.split('.').pop())
                        && el.getClientRects().length>0 && !el.hidden)
                  .map(el=>el.dataset.mcelRegion.split('.').pop())''')
                readings[mode]=names
                if names!=[region]:failures.append('incorrect-active-task-view:'+mode)
            interaction['taskViews']=readings
        if app=='code-editor':
            # Preserve the original editor DOM node through every tool switch.
            browser_page.evaluate('window.__oracleEditor=document.querySelector("[data-mcel-document-surface]")')
            if not browser_page.evaluate('window.__oracleEditor !== null'):
                failures.append('document-surface-missing')
        if app in TOOL_TABS:
            tabs=browser_page.locator('[data-mcel-tool-tabs] [role="tab"]')
            # Every tab must reveal the expected region; no other tool panel may stay visible.
            seen={}
            for idx,expected_region in enumerate(TOOL_TABS[app]):
                tabs.nth(idx).click(timeout=5000)
                selected=browser_page.locator('[data-mcel-tool-tabs] [role="tab"][aria-selected="true"]')
                panels=browser_page.locator('[data-mcel-tool-panel]:not([hidden])')
                matches=panels.locator('[data-mcel-region]').evaluate_all('''els=>els.filter(e=>e.getClientRects().length>0)
                    .map(e=>e.dataset.mcelRegion.split('.').pop())''') if panels.count()==1 else []
                seen[expected_region]=matches
                if selected.count()!=1 or panels.count()!=1 or matches!=[expected_region]:
                    failures.append('tool-tab-not-exclusive:'+expected_region)
            interaction['toolTabs']=seen
        if app=='code-editor':
            if not browser_page.evaluate('window.__oracleEditor !== null && window.__oracleEditor === document.querySelector("[data-mcel-document-surface]")'):
                failures.append('document-surface-replaced')
        if app=='file-manager':
            # The independent geometry oracle binds all three source-defined surfaces.
            if not browser_page.locator('[data-mcel-region$=".locations"]').is_visible():
                failures.append('navigation-not-visible')
            if not browser_page.locator('[data-mcel-region$=".directory"]').is_visible():
                failures.append('directory-not-visible')
            if not browser_page.locator('[data-mcel-region$=".inspection"]').is_visible():
                failures.append('inspection-not-visible')
    except Exception as exc:
        failures.append(type(exc).__name__+':'+str(exc)[:180])
    if failures:interaction['status']='FAIL'
    return check_layout(app,observed,width,interactions=interaction)
