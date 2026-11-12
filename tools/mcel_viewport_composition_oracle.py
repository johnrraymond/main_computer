"""Independent MCEL viewport-*composition* oracle, not a scrollbar test.

No application-name switches: application semantics and actual browser geometry
are the inputs. Ordinary cards do not become independent scroll viewports.
Specialized internal scrollers must be declared and contain matching content.
"""
from __future__ import annotations

# This probe observes normal application content BEFORE the viewport frob adds
# any synthetic overflow. It does not make service requests or mutate layout.
DOM_OBSERVATION = r'''() => {
  const app=document.querySelector('#mcel-workbench,#mcel-expanded-root');
  if(!app)return {error:'semantic-application-not-mounted'};
  const visible=e=>e&&getComputedStyle(e).display!=='none'&&e.getClientRects().length>0;
  const measure=e=>{if(!e)return null;const r=e.getBoundingClientRect(),s=getComputedStyle(e);
    return {x:r.x,y:r.y,w:r.width,h:r.height,scrollHeight:e.scrollHeight,
      clientHeight:e.clientHeight,overflowY:s.overflowY};};
  const root=[...app.querySelectorAll('[data-mcel-region]')].find(visible);
  if(!root)return {error:'semantic-root-not-mounted'};
  const regions=[...app.querySelectorAll('[data-mcel-region]')].filter(visible);
  const zones=[...app.querySelectorAll('[data-mcel-zone]')].filter(visible);
  const nonroot=regions.filter(e=>e!==root);
  const workZone=zones.find(e=>e.dataset.mcelZone==='workspace');
  const workCandidates=nonroot.filter(e=>e.dataset.mcelPurpose==='workspace');
  const primary=workZone||workCandidates.find(e=>e.dataset.mcelPriority==='primary')||
    workCandidates.find(e=>['document','collection'].includes(e.dataset.mcelContentKind))||
    workCandidates[0]||(root.dataset.mcelPurpose==='workspace'?root:null);
  const nav=zones.find(e=>e.dataset.mcelZone==='navigator')||
    nonroot.find(e=>e.dataset.mcelPurpose==='navigation');
  const leaf=e=>({id:e.dataset.mcelRegion||e.dataset.mcelZone||e.id||'',
    purpose:e.dataset.mcelPurpose||e.dataset.mcelZone||'',
    contentKind:e.dataset.mcelContentKind||'',
    scrollOwner:e.getAttribute('data-mcel-scroll-owner')||'',
    zoneKind:e.getAttribute('data-mcel-zone-kind')||'',
    zonePlacement:e.getAttribute('data-mcel-placement')||'',
    isSpecialized:!!e.querySelector('textarea,.monaco-editor,[role="grid"],pre,[data-collection],'+
      '[data-mcel-content-kind="document"]'),
    rect:measure(e)});
  const panels=nonroot.filter(e=>e.parentElement===root).map(leaf);
  const scrollRegions=[...new Set([...nonroot,...zones])].map(leaf);
  // Semantic navigation is more than an element named navigator. Verify that
  // authored navigation/mode regions actually occupy the navigation scroll
  // owner and that mode choices form a vertical list, not a wrapping toolbar.
  const authoredNav=nonroot.filter(e=>['navigation','mode-control'].includes(e.dataset.mcelPurpose));
  const modeGroups=authoredNav.flatMap(e=>[...e.querySelectorAll('[data-mcel-mode-tabs]')].filter(visible));
  const modeButtons=modeGroups.flatMap(e=>[...e.querySelectorAll('button,a[data-value]')].filter(visible));
  const requiredChrome=document.body.classList.contains('mcel-workbench-page') ||
    document.body.classList.contains('mcel-expert-page');
  const title=app.querySelector('[data-mcel-application-title]');
  const chrome=requiredChrome?{
    titleInNavigation:!!(nav && title && nav.contains(title)),
    titleCount:app.querySelectorAll('[data-mcel-application-title]').length,
    titleText:title?.textContent?.trim()||'',
    titleRect:measure(title),
    externalHeader:!!document.body.querySelector(':scope > header'),
    workspaceTopGap:primary?(primary.getBoundingClientRect().top-app.getBoundingClientRect().top):null,
  }:null;
  const navigationSemantics={authoredCount:authoredNav.length,
    owned:authoredNav.every(e=>nav && (nav===e || nav.contains(e))),
    noGenericQueryControls:!authoredNav.length || !nonroot.some(e=>
      e.dataset.mcelPurpose==='query-controls' && nav && nav.contains(e)),
    modeCount:modeGroups.length,
    modeButtons:modeButtons.length,
    verticalModes:modeGroups.every(g=>{
      const buttons=[...g.querySelectorAll('button,a[data-value]')].filter(visible);
      if(buttons.length<2)return false;
      const rectangles=buttons.map(b=>b.getBoundingClientRect());
      return rectangles.every((r,i)=>i===0 ||
        r.top >= rectangles[i-1].bottom-1 && Math.abs(r.left-rectangles[0].left)<=2);
    })};
  // Control composition is a different invariant from scroll containment:
  // generated sibling actions flow by intrinsic width, whereas a four-column
  // compute keypad stays bounded even when its workspace becomes very wide.
  const actionGroups=nonroot.filter(e=>e.dataset.mcelRegion?.endsWith('generated-controls'))
    .map(region=>{
      const flow=region.querySelector(':scope > [data-mcel-action-flow]');
      const nodes=[...region.querySelectorAll('[data-node-kind="control"]')].filter(visible);
      const buttons=nodes.map(n=>n.querySelector('button[data-intent]')).filter(visible);
      const rects=buttons.map(b=>b.getBoundingClientRect());
      const style=flow?getComputedStyle(flow):null;
      const gap=parseFloat(style?.columnGap)||8;
      const available=flow?.getBoundingClientRect().width||region.clientWidth;
      const total=rects.reduce((acc,r)=>acc+r.width,0)+gap*Math.max(0,rects.length-1);
      const tops=rects.map(r=>Math.round(r.top));
      const oneRow=tops.every(y=>Math.abs(y-tops[0])<=2);
      const pairFits=rects.length>=2&&rects[0].width+rects[1].width+gap<=available+2;
      return {count:buttons.length,owned:!!flow&&nodes.every(n=>n.parentElement===flow),
        display:style?.display||'',wrap:style?.flexWrap||'',
        oneRow,oneRowFits:total<=available+2,
        firstPairOnSameRow:rects.length>=2&&Math.abs(rects[1].top-rects[0].top)<=2,
        firstPairFits:pairFits, availableWidth:available};
    }).filter(g=>g.count>=2);
  const keypads=[...app.querySelectorAll('[data-mcel-expression-pad]')].filter(visible).map(pad=>{
    const rect=pad.getBoundingClientRect();
    const keys=[...pad.querySelectorAll('button')].filter(visible);
    const r=keys.map(b=>b.getBoundingClientRect());
    const submit=pad.closest('form')?.querySelector(':scope > button[type="submit"]');
    const submitRect=submit?.getBoundingClientRect();
    const first=r.slice(0,4);
    return {width:rect.width,keyCount:r.length,
      fourColumns:first.length===4&&first.every((b,i)=>i===0||
        Math.abs(b.top-first[0].top)<=2&&b.left>first[i-1].left),
      maxKeyWidth:r.length?Math.max(...r.slice(0,20).map(b=>b.width)):0,
      minKeyHeight:r.length?Math.min(...r.map(b=>b.height)):0,
      submitWidth:submitRect?.width??null,
      submitAligned:!!submitRect&&Math.abs(submitRect.left-rect.left)<=2};
  });
  return {app:measure(app),root:measure(root),primary:primary?leaf(primary):null,
    navigation:nav?leaf(nav):null,panels,scrollRegions,navigationSemantics,
    applicationChrome:chrome,controlComposition:{actionGroups,keypads},
    viewport:{width:innerWidth,height:innerHeight}};
}'''


SHELL_OBSERVATION = r'''() => {
 const app=document.querySelector('.mcel-applications-shell');
 const nav=document.querySelector('.mcel-shell-launcher-panel');
 const work=document.querySelector('.mcel-shell-workspace');
 if(!app||!nav||!work)return {error:'applications-shell-not-mounted'};
 const r=e=>{const b=e.getBoundingClientRect(),s=getComputedStyle(e);
   return {x:b.x,y:b.y,w:b.width,h:b.height,scrollHeight:e.scrollHeight,
     clientHeight:e.clientHeight,overflowY:s.overflowY};};
 const leaf=(e,id,purpose)=>({id,purpose,contentKind:'',scrollOwner:'',
   isSpecialized:false,rect:r(e)});
 return {app:r(app),root:r(app),primary:leaf(work,'shell-workspace','workspace'),
   navigation:leaf(nav,'shell-launcher','navigation'),panels:[],
   scrollRegions:[leaf(nav,'shell-launcher','navigation'),leaf(work,'shell-workspace','workspace')],
   viewport:{width:innerWidth,height:innerHeight}};
}'''


CHAT_OBSERVATION = r'''() => {
 const app=document.querySelector('#chat-console-app');
 const shell=app?.querySelector('.chat-console-shell');
 const nav=app?.querySelector('.chat-thread-rail');
 const work=app?.querySelector('.chat-thread-workspace');
 if(!app||!shell||!work)return {error:'chat-console-workspace-not-mounted'};
 const r=e=>{const b=e.getBoundingClientRect(),s=getComputedStyle(e);
   return {x:b.x,y:b.y,w:b.width,h:b.height,scrollHeight:e.scrollHeight,
     clientHeight:e.clientHeight,overflowY:s.overflowY};};
 const leaf=(e,id,purpose)=>({id,purpose,contentKind:'',scrollOwner:'',
   isSpecialized:false,rect:r(e)});
 const cards=[...app.querySelectorAll('.chat-cell,.chat-console-header,.chat-thread-current-bar')]
   .filter(e=>e.getClientRects().length && getComputedStyle(e).display!=='none')
   .map((e,i)=>leaf(e,'chat-card-'+i,'card'));
 return {app:r(app),root:r(shell),primary:leaf(work,'chat-workspace','workspace'),
   navigation:nav&&nav.getClientRects().length?leaf(nav,'chat-thread-rail','navigation'):null,
   panels:cards,scrollRegions:cards,
   viewport:{width:innerWidth,height:innerHeight}};
}'''


def assess(observation: dict) -> dict:
    """Adjudicate browser-observed normal content without consulting a layout plan."""
    if observation.get('error'):
        return {'status': 'FAIL', 'failures': [observation['error']], 'checks': {}}
    app = observation.get('app') or {}
    primary = observation.get('primary')
    nav = observation.get('navigation')
    panels = observation.get('panels') or []
    scroll_regions = observation.get('scrollRegions') or []
    viewport = observation.get('viewport') or {}
    width = viewport.get('width', 0)
    failures: list[str] = []
    checks: dict[str, bool] = {}

    def require(name: str, ok: bool) -> None:
        checks[name] = bool(ok)
        if not ok:
            failures.append(name)

    if not app.get('w') or not app.get('h') or not width:
        return {'status': 'FAIL', 'failures': ['invalid-application-geometry'], 'checks': {}}
    compact = width < 680
    main_rect = (primary or {}).get('rect') or {}
    require('primary-workspace-present', bool(primary and main_rect.get('w', 0) > 0 and main_rect.get('h', 0) > 0))
    # An explicitly composed inspector can consume a dedicated height track
    # on narrow screens. Do not require the primary region to occupy the
    # inspector's pixels as well as its own.
    inspector_zone=next((r for r in scroll_regions if r.get('purpose')=='tools'
                         and r.get('zoneKind')=='inspector'), None)
    stacked_inspector=bool(inspector_zone and inspector_zone.get('zonePlacement')
                           in ('dock-bottom','disclosure-after-anchor'))
    if checks['primary-workspace-present']:
        require('primary-workspace-dominant-width',
                main_rect['w'] >= app['w'] * (.80 if compact else .45))
        require('primary-workspace-usable-height',
                main_rect['h'] >= app['h'] * (.30 if compact and stacked_inspector else .45 if compact else
                                           .48 if stacked_inspector else .60))
        require('primary-workspace-bounded',
                main_rect['y'] >= app['y']-2 and
                main_rect['y']+main_rect['h'] <= app['y']+app['h']+2)
        require('primary-workspace-owns-overflow',
                main_rect.get('overflowY') in ('auto','scroll') or
                main_rect['scrollHeight'] <= main_rect['clientHeight']+2)

    chrome = observation.get('applicationChrome')
    if chrome is not None:
        title_rect = chrome.get('titleRect') or {}
        require('application-title-in-navigation',
                bool(chrome.get('titleInNavigation')) and chrome.get('titleCount') == 1 and
                bool(chrome.get('titleText')))
        require('application-title-compact',
                0 < title_rect.get('h', 0) <= 52 and
                title_rect.get('w', 0) <= (nav or {}).get('rect', {}).get('w', float('inf')) + 2)
        require('no-separate-application-header', not chrome.get('externalHeader'))
        nav_rect=(nav or {}).get('rect') or {}
        top_limit=(max(min(152,app['h']*.3), min(app['h']*.4,nav_rect.get('h',0)+24))
                   if compact and nav and nav.get('zonePlacement')=='disclosure-before-anchor'
                   else min(152,app['h']*.3) if compact else max(30, app['h']*.15))
        require('workspace-starts-near-application-top',
                chrome.get('workspaceTopGap') is not None and
                -2 <= chrome['workspaceTopGap'] <= top_limit)

    navigation_semantics = observation.get('navigationSemantics')
    if navigation_semantics and navigation_semantics.get('authoredCount', 0):
        require('authored-navigation-owned', bool(navigation_semantics.get('owned')))
        require('navigation-excludes-generic-controls',
                bool(navigation_semantics.get('noGenericQueryControls')))
        if navigation_semantics.get('modeCount', 0):
            require('mode-selector-vertical',
                    bool(navigation_semantics.get('verticalModes')) and
                    navigation_semantics.get('modeButtons', 0) >= 2)

    controls = observation.get('controlComposition') or {}
    for group in controls.get('actionGroups', []):
        if group.get('count', 0) < 2:
            continue
        require('action-group-owns-buttons', bool(group.get('owned')))
        require('action-group-is-wrapping-flow',
                group.get('display') == 'flex' and group.get('wrap') == 'wrap')
        if group.get('oneRowFits'):
            require('action-group-no-wasted-rows', bool(group.get('oneRow')))
        elif group.get('firstPairFits'):
            require('action-group-no-wasted-rows', bool(group.get('firstPairOnSameRow')))
    for pad in controls.get('keypads', []):
        require('keypad-bounded-width', 0 < pad.get('width', 0) <= 422)
        require('keypad-four-regular-columns',
                pad.get('keyCount') == 21 and bool(pad.get('fourColumns')) and
                44 <= pad.get('minKeyHeight', 0) and 0 < pad.get('maxKeyWidth', 0) <= 121)
        require('keypad-submit-bounded', pad.get('submitWidth') is not None and
                0 < pad['submitWidth'] <= 422 and bool(pad.get('submitAligned')))

    if nav and not compact:
        nav_rect = nav['rect']
        require('navigation-uses-allocated-height',
                nav_rect['h'] >= app['h']*(.50 if stacked_inspector else .65))
        require('navigation-bounded',
                nav_rect['y'] >= app['y']-2 and
                nav_rect['y']+nav_rect['h'] <= app['y']+app['h']+2)

    # The failure pictured in SLH: ordinary grid cards arbitrarily receive
    # equal fractional heights, each with its own scrollbar.
    visible_panels=[p for p in panels if p['rect']['h'] > 0 and p['rect']['w'] > 0]
    height_buckets: dict[int, list] = {}
    for panel in visible_panels:
        bucket=round(panel['rect']['h']/4)
        height_buckets.setdefault(bucket,[]).append(panel)
    crowded=any(len(items)>=3 and items[0]['rect']['h'] < app['h']*.56
                for items in height_buckets.values())
    require('no-equal-height-card-strips',not crowded)

    # Cards and status/command/form sections must flow inside a real owner;
    # computed overflow:auto on every card is itself an invalid contract even
    # if today's content has not yet become long enough to scroll.
    for panel in scroll_regions:
        kind=panel['purpose']
        owner=panel['scrollOwner']
        rect=panel['rect']
        if rect['overflowY'] not in ('auto','scroll'):
            if (rect['overflowY']=='hidden' and rect['scrollHeight']>rect['clientHeight']+3
                    and panel['id'] != (primary or {}).get('id')):
                failures.append('clipped-ordinary-region:'+panel['id'])
            continue
        # Verified semantic zones are intentional independent owners, not
        # arbitrary cards granted overflow:auto. The kind must agree with the
        # declared zone role and a concrete planner placement.
        planned_owner=(
            kind=='tools' and panel.get('zoneKind') in ('inspector','tab-group') and
            panel.get('zonePlacement') in ('dock-end','dock-bottom','disclosure-after-anchor')
        ) or (
            kind=='status' and panel.get('zoneKind')=='pinned-compact' and
            panel.get('zonePlacement')=='bottom'
        )
        allowed = kind in ('workspace','navigation','navigator') or planned_owner
        # A specialized scroller must be explicitly labeled AND contain
        # an editor, document, table or log. Labeling an arbitrary card is
        # not sufficient to turn it into a scroll owner.
        specialized = owner in ('editor','log','table') and panel['isSpecialized']
        if not (allowed or specialized):
            failures.append('unowned-scroll-region:'+panel['id'])

    return {'status':'PASS' if not failures else 'FAIL',
            'failures':list(dict.fromkeys(failures)), 'checks':checks,
            'primary':primary, 'navigation':nav,
            'panelCount':len(visible_panels)}
