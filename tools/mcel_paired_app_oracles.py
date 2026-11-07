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
        if app=='task-manager':
            # A multi-field governed workflow cannot be assigned to a skinny
            # inspection dock. It belongs below the primary monitoring workspace.
            if tools['y'] < work['y']+work['h']-2:
                errors.append('governed-tools-before-workspace-end')
            if tools['w'] < work['w']*.85:
                errors.append('governed-tools-width-deficit')
        elif rung=='wide':
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


# Executed on both the composed candidate and the actual hosted (unplanned)
# presentation. This is independent of source plans and CSS selectors for layout
# authority; it measures populated records against their containing workspace.
def populated_monitor_collection_oracle(browser_page, width: int) -> dict:
    failures=[]
    try:
        browser_page.locator('select[data-intent="intent:switch-view"]').select_option('connections',timeout=5000)
        browser_page.locator('form[data-intent="intent:refresh-connections"] button[type="submit"]').click(timeout=5000)
        browser_page.wait_for_function("""() => Array.isArray(window.__mcelWorkbench?.app?.readState('state:connections')) &&
          window.__mcelWorkbench.app.readState('state:connections').length >= 2 &&
          document.querySelectorAll('[data-mcel-region$=".connections"] [data-record-key]').length >= 2""",timeout=7000)
        observed=browser_page.evaluate("""() => {
          const region=document.querySelector('[data-mcel-region$=".connections"]');
          const root=document.querySelector('[data-mcel-presentation="expert"]');
          const collection=region?.querySelector('[data-collection]');
          const rows=[...(collection?.querySelectorAll(':scope > [data-record-key]')||[])];
          const rect=el=>{if(!el)return null;const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
          const inside=(a,b)=>a&&b&&a.x>=b.x-2&&a.x+a.w<=b.x+b.w+2;
          return {root:rect(root),region:rect(region),collection:rect(collection),
            count:rows.length,fields:rows.slice(0,2).map(row=>[...row.querySelectorAll('[data-record-field]')].map(field=>({
              key:field.dataset.recordField,text:field.textContent,rect:rect(field),
              label:field.getAttribute('data-field-label')}))),
            rowRects:rows.slice(0,2).map(rect),visible:!!region?.getClientRects().length && !region?.hidden,
            collectionWithinRegion:inside(rect(collection),rect(region)),
            overflow:document.documentElement.scrollWidth>innerWidth+1};
        }""")
        root,region,collection=observed['root'],observed['region'],observed['collection']
        if not observed['visible'] or not root or not region or not collection:
            failures.append('connections-not-visible')
        else:
            # A primary collection must not be rendered as a sidebar merely
            # because its authored region omitted a priority attribute.
            min_ratio=.50 if width>=680 else .82
            if region['w']<root['w']*min_ratio:
                failures.append('connections-primary-width-deficit')
            if collection['w']<region['w']*.86 or not observed['collectionWithinRegion']:
                failures.append('connections-collection-does-not-fill-region')
        if observed['count']<2:failures.append('connections-no-populated-rows')
        expected={'pid','name','status','local','remote'}
        for fields in observed['fields']:
            got={field['key'] for field in fields}
            if got!=expected:failures.append('connections-required-fields-not-visible');continue
            if any(not field['text'] or field['rect']['w']<43 for field in fields):
                failures.append('connections-unreadable-field-width')
            if any(field['rect']['w']<=0 or field['rect']['h']<=0 for field in fields):
                failures.append('connections-collapsed-field')
        if observed['overflow']:failures.append('connections-horizontal-overflow')
        return {'status':'PASS' if not failures else 'FAIL', 'failures':sorted(set(failures)),
                'observed':observed, 'scenario':'populated-connections-readable'}
    except Exception as exc:
        return {'status':'FAIL','failures':['connections-observation-error'],
                'error':f'{type(exc).__name__}: {exc}'[:350],
                'scenario':'populated-connections-readable'}



def governed_multifield_workflow_oracle(browser_page, width: int) -> dict:
    # Rendered schedule authoring must be a real usable tool workarea.
    # Human-authored Task Manager reference: browser evidence, not source plan.
    # No consequential forms are ever submitted.
    failures=[]
    try:
        region=browser_page.locator('[data-mcel-region$=".schedules"]')
        tabs=browser_page.locator('[data-mcel-tool-tabs] [role="tab"]')
        if tabs.count()!=4:
            failures.append('governed-workflow-missing-tool-tabs')
        else:
            panel_id=region.locator('xpath=..').get_attribute('id')
            for i in range(tabs.count()):
                tab=tabs.nth(i)
                if tab.get_attribute('aria-controls')==panel_id:
                    tab.click(timeout=5000)
                    break
            else:
                failures.append('governed-workflow-unreachable-tab')
        observed=browser_page.evaluate('''() => {
          const find=s=>document.querySelector(s);
          const region=find('[data-mcel-region$=".schedules"]');
          const form=region?.querySelector('form[data-intent="intent:create-schedule"]');
          const list=region?.querySelector('[data-collection]');
          const zone=region?.closest('[data-mcel-zone="tools"]');
          const panel=region?.closest('[data-mcel-tool-panel]');
          const root=find('[data-mcel-presentation="expert"]');
          const rect=el=>{if(!el)return null;const r=el.getBoundingClientRect();
            return {x:r.x,y:r.y,w:r.width,h:r.height};};
          const visible=el=>!!el&&!!el.getClientRects().length&&!el.closest('[hidden]');
          const fields=[...(form?.querySelectorAll('label')||[])].map(label=>({
            text:label.textContent,rect:rect(label),input:!!label.querySelector('input')}));
          return {region:rect(region),form:rect(form),list:rect(list),zone:rect(zone),
            panel:rect(panel),root:rect(root),fields,
            visible:visible(region),panelVisible:visible(panel),
            selectedTabs:document.querySelectorAll('[data-mcel-tool-tabs] [role="tab"][aria-selected="true"]').length,
            visiblePanels:[...document.querySelectorAll('[data-mcel-tool-panel]')].filter(visible).length,
            create:!!form?.querySelector('button[type="submit"]'),
            deleteButton:!!region?.querySelector('button[data-intent="intent:delete-schedule"]'),
            runButton:!!region?.querySelector('button[data-intent="intent:run-schedule"]'),
            overflow:document.documentElement.scrollWidth>innerWidth+1};
        }''')
        region,form,zone=observed['region'],observed['form'],observed['zone']
        if not (observed['visible'] and observed['panelVisible'] and zone and region and form):
            failures.append('governed-workflow-not-visible-in-tool-zone')
        else:
            if region['x'] < zone['x']-2 or region['x']+region['w'] > zone['x']+zone['w']+2:
                failures.append('governed-workflow-outside-zone')
            if form['w'] < region['w']*.80:
                failures.append('governed-form-width-deficit')
            if width>=980 and (region['w']<observed['root']['w']*.55 or form['w']<450):
                failures.append('governed-workflow-confined-to-sidebar')
            if observed['list'] and form['y']-observed['list']['y']-observed['list']['h']>24:
                failures.append('governed-form-disconnected-from-selection')
        if len(observed['fields'])!=3 or not all(f['input'] and f['rect']['w']>=115 for f in observed['fields']):
            failures.append('governed-form-inputs-not-readable')
        if not all(observed[k] for k in ('create','deleteButton','runButton')):
            failures.append('governed-form-missing-operations')
        if observed['selectedTabs']!=1 or observed['visiblePanels']!=1:
            failures.append('governed-tool-panels-not-exclusive')
        if observed['overflow']:failures.append('governed-workflow-horizontal-overflow')
        return {'status':'PASS' if not failures else 'FAIL','failures':sorted(set(failures)),
                'observed':observed,'scenario':'governed-multifield-workflow-readable'}
    except Exception as exc:
        return {'status':'FAIL','failures':['governed-workflow-observation-error'],
                'error':f'{type(exc).__name__}: {exc}'[:350],
                'scenario':'governed-multifield-workflow-readable'}


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
            populated=populated_monitor_collection_oracle(browser_page,width)
            interaction['populatedConnections']=populated
            if populated['status']!='PASS':failures.extend(populated['failures'])
            governed=governed_multifield_workflow_oracle(browser_page,width)
            interaction['governedSchedule']=governed
            if governed['status']!='PASS':failures.extend(governed['failures'])
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
