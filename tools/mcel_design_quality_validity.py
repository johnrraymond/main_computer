"""Evidence validity for the offline MCEL design-quality stress experiment (v3).

Never part of application generation. Does not tune weights or read an AI critique.
Witnesses must show *rendered* perturbations; effectiveness/score integrity is
assessed separately from feasibility and design attractiveness.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json

SCHEMA = 'mcel.design-quality-measurement-validity.v3'

# Observe actual painted DOM, not dataset flags alone or hidden textContent.
RENDER_WITNESS_JS = r'''()=>{
 const root=document.querySelector('#generated-root');
 const visible=e=>{if(!e||e.hidden||!e.getClientRects().length)return false;
  const s=getComputedStyle(e),b=e.getBoundingClientRect();
  if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||b.width<1||b.height<1)return false;
  for(let p=e.parentElement;p;p=p.parentElement){if(p.hidden)return false;const q=getComputedStyle(p);if(q.display==='none'||q.visibility==='hidden')return false;}
  return true;
 };
 const text=e=>visible(e)?(e.innerText||'').trim():'';
 const workspace=root?.querySelector('#mcel-workspace-composite');
 const tools=root?.querySelector('[data-role="supporting-tools"]');
 const inspector=root?.querySelector('#selected-entity');
 const rows=[...(root?.querySelectorAll('.record')||[])].filter(visible);
 const recordTexts=rows.map(text);
 const scaledText=[...(root?.querySelectorAll('.record,h1,h2,h3,button,label')||[])].filter(visible)
   .slice(0,60).map(x=>+(parseFloat(getComputedStyle(x).fontSize)||0).toFixed(2));
 const toolsFixture=root?.querySelector('[data-stress-fixture="dense-rail"]');
 const box=e=>{if(!visible(e))return null;const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height].map(n=>Math.round(n*10)/10);};
 return {workspace_text:text(workspace).slice(0,25000),record_texts:recordTexts.map(s=>s.slice(0,3000)),
  tools_text:text(tools).slice(0,12000),tools_rect:box(tools),
  injected_rail_visible:visible(toolsFixture),injected_rail_text:text(toolsFixture),
  inspector_visible:visible(inspector),inspector_text:text(inspector).slice(0,12000),
  inspector_rect:box(inspector),inspector_field_count:visible(inspector)?inspector.querySelectorAll('dt').length:0,
  font_samples_px:scaledText,visible_record_count:rows.length,
  selected_row_count:rows.filter(r=>r.getAttribute('aria-current')==='true').length};
}'''


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()


def _geom(rect):
    if not rect:return None
    return tuple(round(float(rect.get(k,0)),1) for k in ('x','y','width','height'))


def spatial_signature(row):
    """Candidate-independent, measured geometry (no declared A/B/C strategy)."""
    m=row['observed']
    regions=m['regions']
    return _hash({'viewport':m['viewport_width'],
                  'regions':{key:_geom(regions.get(key)) for key in ('root','status','workspace','tools','feedback','search')},
                  'first_record':_geom(m.get('first_record')),
                  'record_widths':[round(x,1) for x in m['record_widths']],
                  'visible_records':m['visible_row_count'],
                  'document_height':round(m['document_height'],1)})


def rendered_digest(witness):
    """Content digest independent of potentially inert fixture-state markers."""
    return _hash({key:witness.get(key) for key in ('record_texts','tools_text','inspector_text')})


def _median(values):
    values=sorted(v for v in values if v>0)
    if not values:return 0
    n=len(values);return (values[(n-1)//2]+values[n//2])/2


def _close_rect(a,b,eps=0.6):
    if a is None or b is None:return a==b
    return all(abs(x-y)<=eps for x,y in zip(a,b))


def check_fixture(row,control):
    """Each declared mutation needs an independently observed, visible effect.

    Controls must come from the same source candidate and viewport, not from
    a different candidate. A missing control is an audit limitation, not PASS.
    """
    w=row['render_witness'];condition=row
    checks=[]
    def add(kind,pass_,observed,expected):
        checks.append({'dimension':kind,'status':'PASS' if pass_ else 'INEFFECTIVE',
                       'observed':observed,'expected':expected})
    if row['fixture']=='long_text':
        if control is None:checks.append({'dimension':'long_text','status':'CONTROL_UNAVAILABLE'})
        else:
            before=control['render_witness']['record_texts'];after=w['record_texts']
            # The record is the target of the requested long-text readability test;
            # altering a hidden semantic field does not count.
            changed=(before!=after and max(map(len,after),default=0)>max(map(len,before),default=0)+20
                     and any('Synthetic long command:' in text for text in after))
            add('long_text',changed,
                {'visible_record_text_changed':changed,'before_max_length':max(map(len,before),default=0),
                 'after_max_length':max(map(len,after),default=0)},
                'Visible record contains the longer synthetic command, with more than 20 additional characters')
    if row['fixture']=='crowded':
        if control is None:checks.append({'dimension':'crowded','status':'CONTROL_UNAVAILABLE'})
        else:
            baseline=control['render_witness']['visible_record_count'];now=w['visible_record_count']
            add('crowded',now>baseline,{'baseline':baseline,'rendered':now},'More rendered records than same-width control')
    if row['rail']=='dense':
        base=control['render_witness']['tools_text'] if control else None
        difference=len(w['tools_text'])-(len(base) if base is not None else 0)
        add('rail',w['injected_rail_visible'] and bool(w['injected_rail_text']) and (base is None or difference>0),
            {'fixture_visible':w['injected_rail_visible'],'new_visible_characters':difference},
            'Injected tool information is rendered and adds visible content')
    if row['font_scale']>1:
        if control is None:checks.append({'dimension':'font_scale','status':'CONTROL_UNAVAILABLE'})
        else:
            base=_median(control['render_witness']['font_samples_px']);now=_median(w['font_samples_px'])
            ratio=now/base if base else 0
            add('font_scale',ratio>=row['font_scale']*0.80 and now>base,
                {'control_font_px':round(base,2),'actual_font_px':round(now,2),'ratio':round(ratio,3)},
                'Rendered text visibly grows relative to same-candidate control')
    if row['selection']=='selected':
        add('selection',w['inspector_visible'] and w['inspector_field_count']>0 and w['selected_row_count']==1,
            {'inspector_visible':w['inspector_visible'],'fields':w['inspector_field_count'],
             'selected_rows':w['selected_row_count']},'Visible inspector fields, exactly one selected record')
    if row['fixture']=='operation_verified_failure':
        add('verified_failure',row['observed'].get('operation_state')=='failed' and row['observed'].get('failure_text_visible'),
            {'operation_state':row['observed'].get('operation_state'),'visible_failure':row['observed'].get('failure_text_visible')},
            'Operation proof failed and verified failure visibly represented')
    if row['fixture']=='unknown':
        add('unknown',row['observed'].get('operation_state')=='unknown' and not row['observed'].get('failure_text_visible'),
            {'operation_state':row['observed'].get('operation_state'),'visible_verified_failure':row['observed'].get('failure_text_visible')},
            'Unresolved operation must not be rendered as a verified failure')
    status='VALID' if all(c['status']=='PASS' for c in checks) else 'INVALID'
    return {'status':status,'checks':checks}


def task_witness(row):
    """Expose independent task-target access; does not invent an inspect score."""
    m=row['observed'];w=row['render_witness'];task=row['task']
    viewport=m['viewport_height']
    if task=='inspect':
        box=w['inspector_rect']
        return {'task':'inspect','objective_supported':False,'target':'selected-entity',
                'target_visible':w['inspector_visible'],'detail_fields':w['inspector_field_count'],
                'target_top_px':box[1] if box else None,
                'scroll_px_to_target':max(0,box[1]-viewport) if box else None,
                'reason':'No preregistered inspection utility; browsing score cannot certify inspection'}
    if task=='recover':
        box=m['regions'].get('feedback')
        return {'task':'recover','objective_supported':True,'target':'verified-failure-feedback',
                'target_visible':m.get('feedback_visible') and m.get('failure_text_visible'),
                'target_top_px':box['y'] if box else None,
                'scroll_px_to_target':max(0,box['y']-viewport) if box else None}
    if task=='browse':
        box=m.get('first_record')
        return {'task':'browse','objective_supported':True,'target':'first-collection-record',
                'target_visible':bool(box),'target_top_px':box['y'] if box else None,
                'scroll_px_to_target':max(0,box['y']-viewport) if box else None}
    return {'task':task,'objective_supported':False,'target':'unresolved-operation',
            'target_visible':None,'reason':'Unknown-state objective not specified'}


def check_score_causality(current,control):
    """Metamorphic check: unrelated record growth cannot change rail penalty.

    Ignore absolute tools Y (the page gets longer); width, height and actual
    tool text must remain the same. Do not rely on any particular metric name,
    so the regression catches the old root-area leak if reintroduced.
    """
    if not control or current['utility'] is None or control['utility'] is None:return []
    now=current['quality_dimensions'];before=control['quality_dimensions']
    if current['fixture']!='crowded':return []
    if current['render_witness']['visible_record_count']<=control['render_witness']['visible_record_count']:return []
    same_primary=all(abs(now[k]-before[k])<=0.0002 for k in
        ('workspace_width_fraction','workspace_top_viewport_fraction','first_record_top_viewport_fraction'))
    curr_tools=current['render_witness']['tools_rect']
    base_tools=control['render_witness']['tools_rect']
    tools_static=(curr_tools is not None and base_tools is not None
                  and all(abs(x-y)<=0.6 for x,y in zip(curr_tools[2:],base_tools[2:]))
                  and current['render_witness']['tools_text']==control['render_witness']['tools_text'])
    if same_primary and tools_static and abs(current['utility']-control['utility'])>0.00001:
        return [{'code':'UNRELATED_RECORD_GROWTH_SCORE_CHANGE','status':'CONFOUNDING',
                 'utility_delta':round(current['utility']-control['utility'],6),
                 'tools_area_fraction_before':before.get('tools_area_fraction_of_viewport'),
                 'tools_area_fraction_after':now.get('tools_area_fraction_of_viewport'),
                 'reason':'Visible record growth changed utility while primary access and tools size/content stayed the same'}]
    return []


def audit(measurements,conditions):
    """Audit the existing stress run without changing its weight function."""
    by={(r['condition'],r['candidate']):r for r in measurements}
    controls={}
    for c in conditions:
        if c.fixture=='default' and c.rail=='original' and c.font_scale==1 and c.selection=='none':
            controls.setdefault(c.width,c.id)
    groups=[];failures=Counter()
    for c in conditions:
        cases=[by[c.id,cid] for cid in 'ABC']
        feasible=[r for r in cases if r['hard_status']=='PASS']
        groups_by_sig=defaultdict(list)
        for r in feasible:groups_by_sig[spatial_signature(r)].append(r['candidate'])
        equivalences=[sorted(v) for v in groups_by_sig.values() if len(v)>1]
        pixel_groups=defaultdict(list)
        for r in feasible:pixel_groups[r['screenshot_sha256']].append(r['candidate'])
        pixel_equivalences=[sorted(v) for v in pixel_groups.values() if len(v)>1]
        ineffective=[];score_flags=[]
        for row in cases:
            base_id=controls.get(c.width)
            control=by.get((base_id,row['candidate'])) if base_id else None
            if control is row:control=None  # a control does not need its own control
            evidence=check_fixture(row,control)
            row['fixture_validity']=evidence
            row['spatial_signature']=spatial_signature(row)
            row['rendered_content_sha256']=rendered_digest(row['render_witness'])
            row['task_access_witness']=task_witness(row)
            score_problems=check_score_causality(row,control)
            row['score_causality_findings']=score_problems
            if evidence['status']!='VALID':
                ineffective.append({'candidate':row['candidate'],'checks':[x for x in evidence['checks'] if x['status']!='PASS']})
            for item in score_problems:score_flags.append({'candidate':row['candidate'],**item})
        groups.append({'condition':c.id,'width':c.width,'fixture':c.fixture,
                       'fixture_status':'INVALID' if ineffective else 'VALID',
                       'ineffective_fixtures':ineffective,'score_causality_findings':score_flags,
                       'spatial_equivalence_groups':equivalences,'pixel_equivalence_groups':pixel_equivalences,
                       'feasible_candidates':[r['candidate'] for r in feasible],
                       'unsupported_objective':c.selection=='selected' or c.fixture=='unknown'})
        if ineffective:failures['INVALID_FIXTURE']+=1
        if score_flags:failures['SCORE_CAUSALITY_FINDING']+=1
        if equivalences:failures['SPATIAL_EQUIVALENCE']+=1
        if c.selection=='selected':failures['INSPECTION_OBJECTIVE_UNSUPPORTED']+=1
    return {'schema':SCHEMA,'status':'AUDIT_WITH_FINDINGS' if failures else 'AUDIT_CLEAN',
            'controls':{str(k):v for k,v in controls.items()},
            'scenarios':groups,'counts':dict(failures),
            'limitations':['No human visual preference or aesthetics certified',
                           'Rendered text/geometry witnesses are local browser observations, not semantic proof',
                           'No quality policy weights or production presentation behavior changed']}


def guarded_selection(original,scenario,measured):
    """A measured winner is reportable only if its evidence is valid and distinct."""
    # A hard-gate refusal outranks an unsupported objective. A missing utility
    # function must never hide the fact that no candidate is feasible.
    if original['status']=='REFUSED':
        return {**original}
    if scenario['fixture_status']!='VALID':
        return {'status':'INVALID_FIXTURE','winner':None,'reason':'Perturbation not demonstrated in visible browser output'}
    if scenario['score_causality_findings']:
        return {'status':'SCORE_CAUSALITY_FINDING','winner':None,'reason':'Utility improvement has a detected confounder'}
    if scenario['unsupported_objective']:
        return {'status':'UNSUPPORTED_TASK','winner':None,'reason':'No preregistered objective for this task'}
    if original['status'] in ('DECISIVE_EXPERIMENTAL','SINGLE_FEASIBLE'):
        sigs={r['candidate']:r.get('spatial_signature') or spatial_signature(r) for r in measured if r['hard_status']=='PASS'}
        if original.get('winner') in sigs and any(sigs[x]==sigs[original['winner']] for x in sigs if x!=original['winner']):
            return {'status':'EQUIVALENT_LAYOUT','winner':None,'reason':'Selected composition shares identical measured spatial arrangement'}
    if original['status']=='NEAR_TIE':
        top=original.get('top_candidates',[])
        sigs={r['candidate']:r.get('spatial_signature') or spatial_signature(r) for r in measured}
        if len(top)==2 and sigs[top[0]]==sigs[top[1]]:
            return {'status':'EQUIVALENT_LAYOUT','winner':None,'reason':'Top two candidates have identical measured spatial arrangement'}
    return {**original}
