"""Experimental, application-neutral multi-region MCEL composition negotiation.

No production renderer imports this module. Regions come from compiled semantic IR;
geometry is trialed against live, un-cloned browser elements by the battery.
The contract deliberately refuses undocumented or structurally unsafe reflow.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping


class NegotiationRefusal(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    id: str
    slot: str
    purpose: str
    kinds: tuple[str, ...]
    min_width: int
    preferred_width: int
    growth: str
    ordinal: int


def semantic_regions(ir: Mapping, *, mode: str | None = None) -> list[Region]:
    """Normalize purpose/role DSLs and nested HTML hierarchy without app names.

    Only leaf content regions enter the allocation. Parent layout containers
    retain their hierarchy; reparenting is not an acceptable shortcut.
    """
    if ir.get('schema') != 'mcel.application-ir.v1' or len(ir.get('surfaces', [])) != 1:
        raise NegotiationRefusal('not a canonical single-surface application IR')
    surface = ir['surfaces'][0]
    interface = surface.get('interfaceIntent') or {}
    if interface.get('schema') != 'mcel.interface-intent.v1':
        raise NegotiationRefusal('missing semantic interface intent')
    regions = interface.get('regions') or []
    if not regions or len({r.get('id') for r in regions}) != len(regions):
        raise NegotiationRefusal('missing or duplicate semantic region identifiers')
    nodes = {n['id']:n for n in surface.get('nodes', [])}
    ids={r['id'] for r in regions}
    for r in regions:
        if r.get('parentId') and r['parentId'] not in ids:
            raise NegotiationRefusal('region has an unknown semantic parent')
        if any(m.get('ref') not in nodes for m in r.get('members',[])):
            raise NegotiationRefusal('region contains an unbound surface-node member')
        visited={r['id']}
        parent=r.get('parentId')
        while parent:
            if parent in visited:
                raise NegotiationRefusal('cyclic semantic-region hierarchy')
            visited.add(parent)
            parent=next(x.get('parentId') for x in regions if x['id']==parent)
    for rel in interface.get('relations', []):
        if rel.get('from',{}).get('ref') not in ids or rel.get('to',{}).get('ref') not in ids:
            raise NegotiationRefusal('semantic relationship points to a missing region')
    parents = {r.get('parentId') for r in regions if r.get('parentId')}
    result=[]
    for index,r in enumerate(regions):
        conditional=r.get('visibleWhen') or {}
        if conditional and (mode is None or conditional.get('equals') != mode):
            continue
        members=r.get('members') or []
        if not members and r['id'] in parents:
            continue
        # Root shells / empty contentless leaf nodes are not negotiable slots.
        purpose=(r.get('purpose') or r.get('role') or '').lower()
        if purpose in {'application','root'} or (r.get('parentId') is None and not members):
            continue
        kinds=tuple(sorted({nodes[m['ref']]['nodeKind'] for m in members if m.get('ref') in nodes}))
        # A leaf in a nested, authored application (chat) can have no surface
        # nodes. Its explicit purpose is still authoritative semantic data.
        if not kinds and not purpose:
            raise NegotiationRefusal('untyped region with no semantic role')
        if purpose in {'navigation','mode-control'}:
            slot='navigation'
        elif purpose in {'status','inspection','inspector','evidence','details'}:
            slot='delta'
        elif purpose=='results' and ('plot' in kinds or 'collection' in kinds) and not any(k in kinds for k in ('form','control')):
            slot='delta'
        else:
            slot='main'
        if slot=='navigation':
            minimum,preferred,growth=(165,215,'saturating')
        elif 'plot' in kinds or 'document-editor' in kinds:
            minimum,preferred,growth=(270,720,'visual')
        elif 'collection' in kinds:
            minimum,preferred,growth=(245,480,'visual')
        elif 'form' in kinds:
            minimum,preferred,growth=(250,350,'saturating')
        elif purpose in {'navigation','mode-control'}:
            minimum,preferred,growth=(165,210,'saturating')
        elif slot=='delta':
            minimum,preferred,growth=(190,330,'saturating')
        else:
            minimum,preferred,growth=(210,340,'saturating')
        # An editor that also has forms preserves the larger editor minimum;
        # arbitrary region names and English labels never influence the result.
        result.append(Region(r['id'],slot,purpose,kinds,minimum,preferred,growth,index))
    if not result or not any(r.slot=='main' for r in result):
        raise NegotiationRefusal('semantic model has no working region')
    return result


def _demand(regions: list[Region], slot: str):
    group=[r for r in regions if r.slot==slot]
    if not group:
        return None
    # Siblings in one region can scroll vertically; their horizontal needs
    # combine by maximum, never by summing widths.
    return dict(min=max(r.min_width for r in group),
                preferred=max(r.preferred_width for r in group),
                growth='visual' if any(r.growth=='visual' for r in group) else 'saturating',
                members=[r.id for r in group])


def _utility(width: int, demand: dict) -> float:
    if width < demand['min']:
        return -1000.0
    excess=max(0,width-demand['min'])
    span=max(1,demand['preferred']-demand['min'])
    if demand['growth']=='visual':
        return 32+34*(1-math.exp(-excess/max(220,span)))
    return 28+21*min(1,excess/span)+min(1.5,max(0,excess-span)/300)


def negotiate_application(ir: Mapping, width: int, height: int = 800, *, mode: str | None = None) -> dict:
    """Enumerate alternatives for ANY canonical semantic region graph.

    Navigation/main/delta are resource offers, not imposed coordinates.
    The winning layout is scored against the same complete application.
    """
    if width < 240 or height < 280:
        raise NegotiationRefusal('viewport below tested lower bounds')
    regions=semantic_regions(ir,mode=mode)
    d={slot:_demand(regions,slot) for slot in ('navigation','main','delta')}
    # Status-only delta can share a compact strip; it does not force a third
    # column for a form or document. This is a property of the semantic tree.
    meaningful_delta=any(r.slot=='delta' and r.purpose not in {'status','evidence'} for r in regions)
    candidates=[]
    def offer(arrangement,nav,main,delta,score,reason):
        if main<d['main']['min']:
            return
        if arrangement!='stacked' and meaningful_delta and d['delta'] and delta<d['delta']['min']:
            return
        candidates.append({'arrangement':arrangement,'navigationWidth':nav,
            'mainWidth':main,'deltaWidth':delta,'score':round(score,4),'reason':reason})
    # Single-column option, constrained only by horizontal feasibility; its
    # vertical cost depends on how many semantic regions must share the fold.
    fold=max(0, sum(270 if 'document-editor' in r.kinds else 200 if 'collection' in r.kinds or 'plot' in r.kinds else 155 if 'form' in r.kinds else 90 for r in regions)-height+95)
    stack_score=_utility(width,d['main']) - 17 - fold/12
    if d['delta']:
        stack_score+=_utility(width,d['delta']) - 23
    if d['navigation']:
        stack_score+=_utility(width,d['navigation']) - 18
    offer('stacked',0,width,width,stack_score,'preserves regions in semantic reading order')
    if d['delta'] and meaningful_delta:
        for main in range(d['main']['min'],width-12-d['delta']['min']+1,10):
            delta=width-12-main
            score=_utility(main,d['main'])+_utility(delta,d['delta'])+14
            if d['navigation']:
                score+=10 # navigation can be a compact header above both
            offer('two-region',0,main,delta,score,'navigation/top band with two working surfaces')
        if d['navigation']:
            for nav in (max(165,d['navigation']['min']),max(185,d['navigation']['min']),max(210,d['navigation']['min'])):
                available=width-nav-24
                for main in range(d['main']['min'],available-d['delta']['min']+1,10):
                    delta=available-main
                    score=_utility(nav,d['navigation'])+_utility(main,d['main'])+_utility(delta,d['delta'])+22
                    offer('three-region',nav,main,delta,score,'simultaneous navigation, work and delta')
    elif d['navigation']:
        for nav in (max(165,d['navigation']['min']),max(185,d['navigation']['min']),max(210,d['navigation']['min'])):
            main=width-nav-12
            if main<d['main']['min']:
                continue
            score=_utility(nav,d['navigation'])+_utility(main,d['main'])+13
            offer('two-region',nav,main,0,score,'navigation and primary workspace')
    else:
        offer('compact',0,min(width,d['main']['preferred']),0,
            _utility(min(width,d['main']['preferred']),d['main'])+8,
            'single bounded working surface')
    if not candidates:
        raise NegotiationRefusal('no usable composition candidate')
    candidates.sort(key=lambda x:(-x['score'],x['arrangement'],x['navigationWidth'],x['mainWidth']))
    return {'schema':'mcel.general-negotiation-trial.v1','experimental':True,
        'productionPromotion':False,'width':width,'height':height,
        'regions':[asdict(r) for r in regions], 'demands':d,
        'winner':candidates[0], 'candidateCount':len(candidates),
        'alternatives':[{**next(c for c in candidates if c['arrangement']==k)}
          for k in sorted(set(c['arrangement'] for c in candidates))]}


# Layout binding is structural: the existing host declares grid zones or a
# nested semantic grid. It is NOT selected from application names, routes,
# headings, or semantic node IDs.
TRIAL_GRID_JS = r'''(decision) => {
  const layout=document.querySelector('[data-mcel-workspace-layout], [data-mcel-natural-layout]');
  const semantic=document.querySelectorAll('[data-mcel-region-id]');
  let grid=layout, type='zones', areas=null;
  if(!grid) {
    const candidates=[...semantic].filter(e=>getComputedStyle(e).display==='grid');
    const semanticPairs=candidates.map(el=>({el, kids:[...el.children].filter(e=>e.dataset.mcelRegionId)}))
      .filter(x=>x.kids.length>=2);
    const suitable=semanticPairs.find(x=>{
      const purposes=x.kids.map(e=>decision.roles[e.dataset.mcelRegionId]||'');
      return purposes.includes('navigation') && purposes.includes('main');
    });
    if(suitable) {grid=suitable.el;type='semantic-siblings';areas=suitable.kids;}
  }
  if(!grid) return {status:'UNSUPPORTED_HOST',reason:'no validated grid layout host'};
  const rect=grid.getBoundingClientRect();
  const style=getComputedStyle(grid);
  const current=style.gridTemplateColumns.split(' ').filter(Boolean).length;
  const before=style.gridTemplateColumns;
  const zones=layout?[...layout.children].filter(e=>e.dataset.mcelZone):areas;
  const visibleZones=zones.map(z=>({name:z.dataset.mcelZone||z.dataset.mcelRegionId,
    x:z.getBoundingClientRect().x,w:z.getBoundingClientRect().width,
    y:z.getBoundingClientRect().y}));
  const desired=decision.arrangement;
  if(desired==='stacked' || desired==='compact')
    return {status:'BASELINE_RETAINED',reason:'single-column composition already supported',before,after:before,
      layoutType:type,current,visibleZones};
  // Where the existing semantic zones permit it, trial an actual change
  // in topology. Only named SEMANTIC ZONES are used; no application IDs or
  // selectors for individual child controls may influence this decision.
  if(current!==(desired==='three-region'?3:2)) {
    if(type!=='zones')
      return {status:'UNSUPPORTED_TOPOLOGY',reason:'nested renderer does not advertise an alternative topology',
        layoutType:type,current,visibleZones};
    const byName=Object.fromEntries(zones.map(z=>[z.dataset.mcelZone,z]));
    const put=(name,column,row)=>{
      const el=byName[name]; if(!el) return;
      el.style.setProperty('grid-column',column,'important');
      el.style.setProperty('grid-row',row,'important');
    };
    if(desired==='three-region' && current===2 &&
       ['navigator','workspace','tools'].every(x=>byName[x]) &&
       zones.length===3) {
      grid.style.setProperty('grid-template-columns',
        `${decision.navigationWidth}px ${decision.mainWidth}px minmax(0,1fr)`,'important');
      grid.style.setProperty('grid-template-rows','minmax(0,1fr)','important');
      put('navigator','1','1');put('workspace','2','1');put('tools','3','1');
      return {status:'TRIALED',layoutType:type,topologyChange:true,before,
        after:getComputedStyle(grid).gridTemplateColumns,current,visibleZones};
    }
    if(desired==='two-region' && current===1 &&
       ['navigator','controls','workspace','tools','details'].every(x=>byName[x])) {
      // Toolbar and workflows must remain full width; only inspection and
      // the primary workspace share the negotiated working row.
      const h=name=>Math.max(36,Math.round(byName[name].getBoundingClientRect().height));
      grid.style.setProperty('grid-template-columns',
        `${decision.mainWidth}px minmax(0,1fr)`,'important');
      grid.style.setProperty('grid-template-rows',
        `${h('navigator')}px ${h('controls')}px minmax(0,1fr) ${h('tools')}px`,'important');
      put('navigator','1 / -1','1');put('controls','1 / -1','2');
      put('workspace','1','3');put('details','2','3');put('tools','1 / -1','4');
      return {status:'TRIALED',layoutType:type,topologyChange:true,before,
        after:getComputedStyle(grid).gridTemplateColumns,current,visibleZones};
    }
    return {status:'UNSUPPORTED_TOPOLOGY',reason:`${desired} cannot be constructed from current semantic zones`,
      layoutType:type,current,visibleZones};
  }
  let columns=[];
  if(desired==='three-region') {
    if(type!=='zones' || !['navigator','workspace','tools'].every(name=>zones.some(z=>z.dataset.mcelZone===name)))
      return {status:'UNSUPPORTED_TOPOLOGY',reason:'three-column semantic zones not present',current};
    const nav=decision.navigationWidth;
    const total=decision.navigationWidth+decision.mainWidth+decision.deltaWidth;
    const gap=rect.width-total;
    if(gap<0 || gap>80) return {status:'UNSUPPORTED_TOPOLOGY',reason:'grid gap not feasible',gap};
    columns=[nav+'px',decision.mainWidth+'px', 'minmax(0, 1fr)'];
  } else if(type==='semantic-siblings') {
    if(decision.deltaWidth) return {status:'UNSUPPORTED_TOPOLOGY',reason:'nested sibling layout is nav/main, not result/output'};
    columns=[decision.navigationWidth+'px','minmax(0,1fr)'];
  } else {
    const names=zones.map(z=>z.dataset.mcelZone);
    if(names.includes('details') && names.includes('workspace')) {
      if(!decision.deltaWidth) return {status:'UNSUPPORTED_TOPOLOGY',reason:'details track requires delta demand'};
      columns=[decision.mainWidth+'px','minmax(0,1fr)'];
    } else if(names.includes('navigator') && names.includes('workspace')) {
      if(decision.deltaWidth) return {status:'UNSUPPORTED_TOPOLOGY',reason:'two-column host has navigation but no side details track'};
      columns=[decision.navigationWidth+'px','minmax(0,1fr)'];
    } else return {status:'UNSUPPORTED_TOPOLOGY',reason:'two-column host has no known semantic tracks'};
  }
  grid.style.setProperty('grid-template-columns',columns.join(' '),'important');
  const after=getComputedStyle(grid).gridTemplateColumns;
  return {status:'TRIALED',layoutType:type,columns, before,after,current,visibleZones};
}'''


TRIAL_GEOMETRY_JS = r'''() => {
  const root=document.querySelector('#mcel-expanded-root,#mcel-workbench,#chat-console-app');
  const regions=[...root.querySelectorAll('[data-mcel-region],[data-mcel-region-id]')];
  const interactive=[...root.querySelectorAll('button,input,textarea,select,a[href],[role="tab"]')];
  const inspect=e=>{const r=e.getBoundingClientRect(),c=getComputedStyle(e);
    return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,
      display:c.display,visibility:c.visibility,hidden:e.hidden};};
  const visible=e=>{const r=inspect(e);return r.w>0 && r.h>0 && r.display!=='none' && r.visibility!=='hidden' && !r.hidden;};
  const clipped=interactive.filter(visible).filter(e=>{
    const b=inspect(e);
    if(b.w<1 || b.h<1) return true;
    let parent=e.parentElement;
    while(parent && parent!==root) {
      const s=getComputedStyle(parent);
      if(['hidden','clip'].includes(s.overflowX)) {
        const p=inspect(parent);
        if(b.x < p.x-4 || b.right>p.right+4) return true;
      }
      parent=parent.parentElement;
    }
    return false;
  }).map(e=>e.outerHTML.slice(0,160));
  const regionBoxes=regions.map(e=>({id:e.dataset.mcelRegion||e.dataset.mcelRegionId,
    ...inspect(e)}));
  const leafRegions=regions.filter(visible);
  const regionOverlaps=[];
  for(let i=0;i<leafRegions.length;i++) for(let j=i+1;j<leafRegions.length;j++) {
    const a=leafRegions[i],b=leafRegions[j];
    if(a.contains(b)||b.contains(a)) continue;
    const ra=a.getBoundingClientRect(),rb=b.getBoundingClientRect();
    const dx=Math.min(ra.right,rb.right)-Math.max(ra.left,rb.left);
    const dy=Math.min(ra.bottom,rb.bottom)-Math.max(ra.top,rb.top);
    if(dx>4 && dy>4) regionOverlaps.push([a.dataset.mcelRegion||a.dataset.mcelRegionId,
                                              b.dataset.mcelRegion||b.dataset.mcelRegionId]);
  }
  return {regionOverlaps,documentWidth:document.documentElement.scrollWidth,innerWidth:innerWidth,
    regionBoxes,visibleRegions:regions.filter(visible).map(e=>e.dataset.mcelRegion||e.dataset.mcelRegionId),
    interactiveCount:interactive.length,visibleInteractive:interactive.filter(visible).length,
    hiddenLeaks:regions.filter(e=>e.hidden&&getComputedStyle(e).display!=='none').map(e=>e.id||e.dataset.mcelRegion),
    clipped,root:inspect(root)};
}'''


def trial_browser_geometry(page, ir: Mapping, width: int, *, mode: str | None = None) -> dict:
    """Test-only CSS reflow of original live DOM. Refuse unknown topologies.

    An accepted trial must preserve the set of visible semantic regions and
    interactive controls, without increasing document overflow or clipping.
    Recovery restores the original layout when a trial fails. No fake mount.
    """
    initial=page.evaluate(TRIAL_GEOMETRY_JS)
    initial_collisions=page.evaluate(FORM_COLLISIONS_JS)
    if not initial['regionBoxes']:
        return {'status':'UNSUPPORTED_HOST','reason':'no visible semantic regions','baseline':initial}
    # Exact container width matters; viewport padding is not usable space.
    available=page.evaluate('''() => {
      const grid=document.querySelector('[data-mcel-workspace-layout],[data-mcel-natural-layout]');
      return grid?Math.round(grid.getBoundingClientRect().width):innerWidth;
    }''')
    plan=negotiate_application(ir,available,800,mode=mode)
    roles={r['id']:r['slot'] for r in plan['regions']}
    # Structural parents inherit a homogeneous child role. This is the same
    # hierarchy normalization for ANY nested application, not a chat rule.
    all_regions=ir['surfaces'][0]['interfaceIntent']['regions']
    for _ in range(len(all_regions)):
        for region in reversed(all_regions):
            identifier=region['id']
            if identifier in roles: continue
            direct={roles[r['id']] for r in all_regions if r.get('parentId')==identifier and r['id'] in roles}
            if len(direct)==1: roles[identifier]=next(iter(direct))
    nested_width=page.evaluate('''roles => {
      if(document.querySelector('[data-mcel-workspace-layout],[data-mcel-natural-layout]')) return null;
      const grids=[...document.querySelectorAll('[data-mcel-region-id]')].filter(e=>getComputedStyle(e).display==='grid');
      const grid=grids.find(el=>{
        const kids=[...el.children].filter(c=>c.dataset.mcelRegionId);
        const kinds=kids.map(c=>roles[c.dataset.mcelRegionId]);
        return kinds.includes('navigation')&&kinds.includes('main');
      });
      return grid?Math.round(grid.getBoundingClientRect().width):null;
    }''',roles)
    if nested_width is not None:
        plan=negotiate_application(ir,nested_width,800,mode=mode)
    decision={**plan['winner'],'roles':roles}
    result=page.evaluate(TRIAL_GRID_JS,decision)
    children={'moved':[],'forms':[]}
    if result['status']=='TRIALED':
        fields={}
        if any('plot' in r['kinds'] for r in plan['regions']):
            nodes={n['id']:n for n in ir['surfaces'][0].get('nodes',[])}
            for region in ir['surfaces'][0]['interfaceIntent']['regions']:
                fields[region['id']]={key:val.get('kind') for m in region.get('members',[])
                    for node in [nodes.get(m['ref'],{})]
                    for key,val in ((node.get('fields') or {}) if isinstance(node.get('fields'),dict) else {}).items()}
        children=page.evaluate(CHILD_COMPOSITION_JS,{'regions':plan['regions'],'fields':fields})
    after=page.evaluate(TRIAL_GEOMETRY_JS)
    after_collisions=page.evaluate(FORM_COLLISIONS_JS)
    # Keep the original baseline defects visible in the evidence. A trial may
    # not be credited with fixing unrelated pre-existing hidden-display bugs.
    baseline_visible=set(initial['visibleRegions'])
    final_visible=set(after['visibleRegions'])
    regressions=[]
    if baseline_visible!=final_visible: regressions.append('semantic region visibility changed')
    if initial['interactiveCount']!=after['interactiveCount']: regressions.append('interactive elements were removed')
    if after['visibleInteractive']<initial['visibleInteractive']: regressions.append('visible interactive controls lost')
    if len(after['clipped'])>len(initial['clipped']): regressions.append('new clipped controls')
    if len(after['regionOverlaps'])>len(initial['regionOverlaps']):
        regressions.append('new overlapping independent semantic regions')
    if len(after_collisions)>len(initial_collisions): regressions.append('new overlapping form children')
    if any(r['slot']=='delta' and 'plot' in r['kinds'] for r in plan['regions']) and result['status']=='TRIALED':
        # A visual result is the point of this negotiation. Resizing columns
        # without moving its existing DOM region is a false-positive trial.
        placement=page.evaluate('''ids => ids.map(id=>{
          const region=[...document.querySelectorAll('[data-mcel-region]')].find(e=>e.dataset.mcelRegion===id);
          return region?.closest('[data-mcel-zone]')?.dataset.mcelZone||null;
        })''',[r['id'] for r in plan['regions'] if 'plot' in r['kinds']])
        if any(x not in ('tools','details') for x in placement):
            regressions.append('visual result not placed in negotiated output zone')
    if after['documentWidth']>max(initial['documentWidth'],width)+3: regressions.append('new horizontal overflow')
    if result['status']=='TRIALED':
        if result['before']==result['after']: regressions.append('layout allocation did not change')
        # Semantic regions must remain attached to the same DOM and operation.
        if sorted(r['id'] for r in initial['regionBoxes']) != sorted(r['id'] for r in after['regionBoxes']):
            regressions.append('semantic region identity changed')
    # Each browser trial is isolated in a newly created page; rejected DOM
    # changes never escape the disposable fixture.
    return {'status': 'REJECTED' if regressions else result['status'],
        'reason':'; '.join(regressions) if regressions else result.get('reason',''),
        'plan':plan, 'layout':result, 'baseline':initial,'after':after,
        'regressions':regressions, 'childComposition':children,
        'formCollisionsBefore':len(initial_collisions),'formCollisionsAfter':len(after_collisions),
        'regionOverlapsBefore':initial['regionOverlaps'],'regionOverlapsAfter':after['regionOverlaps'],
        'evidenceScope':'CSS grid-width trial only; not operation-level functional parity'}


CHILD_COMPOSITION_JS = r'''(contract) => {
  const root=document.querySelector('#mcel-expanded-root,#mcel-workbench,#chat-console-app');
  const areas=[...root.querySelectorAll('[data-mcel-region]')];
  const outputZone=root.querySelector('[data-mcel-zone="details"], [data-mcel-zone="tools"]');
  let moved=[],forms=[];
  for(const unit of contract.regions) {
    if(unit.slot!=='delta' || !unit.kinds.includes('plot')) continue;
    const region=areas.find(el=>el.dataset.mcelRegion===unit.id);
    if(!region || !outputZone || region.hidden) continue;
    if(!outputZone.contains(region)) {
      outputZone.prepend(region);moved.push(unit.id);
    }
  }
  // Typed field constraints are attached to form kinds, not field names.
  // Text expressions can span both tracks; numeric controls share two tracks.
  if(moved.length) {
    for(const unit of contract.regions) {
      if(unit.slot!=='main' || !unit.kinds.includes('form')) continue;
      const region=areas.find(el=>el.dataset.mcelRegion===unit.id);
      if(!region || region.hidden) continue;
      const kindMap=contract.fields[unit.id] || {};
      region.querySelectorAll('form').forEach(form=>{
        form.style.setProperty('display','grid','important');
        form.style.setProperty('grid-template-columns','repeat(2,minmax(0,1fr))','important');
        form.style.setProperty('gap','8px','important');
        form.style.setProperty('align-items','start','important');
        [...form.children].forEach(child=>{
          child.style.setProperty('grid-row','auto','important');
          if(child.matches('label[data-field]')) {
            const kind=kindMap[child.dataset.field];
            child.style.setProperty('grid-column',kind==='string'?'1 / -1':'auto','important');
          } else child.style.setProperty('grid-column','1 / -1','important');
        });
        forms.push(unit.id);
      });
    }
  }
  return {moved,forms};
}'''


FORM_COLLISIONS_JS = r'''() => {
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
    return !e.closest('[hidden]') && r.width>0 && r.height>0 && s.display!=='none';};
  const out=[];
  for(const form of document.querySelectorAll('form')) {
    if(!visible(form)) continue;
    const els=[...form.children].filter(visible).map((el,i)=>({i,r:el.getBoundingClientRect()}));
    for(let i=0;i<els.length;i++) for(let j=i+1;j<els.length;j++) {
      const a=els[i],b=els[j];
      if(Math.min(a.r.right,b.r.right)-Math.max(a.r.left,b.r.left)>3 &&
         Math.min(a.r.bottom,b.r.bottom)-Math.max(a.r.top,b.r.top)>3)
        out.push([a.i,b.i]);
    }
  }
  return out;
}'''
