"""Independent, deterministic MCEL design-quality measurement and bounded selection.

Research-only; never mutates MCEL generation rules or uses ChatGPT/Gemma critiques.
Runs A/B/C *production MCEL canonical renders* and measures browser geometry,
semantic integrity, task access, rail utility, card readability and failure prominence.

  python tools/mcel_design_quality_frob.py --mode run
  python tools/mcel_design_quality_frob.py --mode evaluate --review-root runtime/reports/mcel-layout-frob/design-review
  python tools/mcel_layout_frob.py --mode design-quality

Review candidates remain unapproved by humans. The report contains explicit
metric definitions, candidate-level trade-offs and deterministic tie-breaks.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import mcel_design_critic_frob as critic
from tools.mcel_browser_launch import launch_chromium

POLICY_VERSION='mcel.experimental-design-quality.v2-viewport-normalized-tools'
SCHEMA='mcel.design-quality-experiment.v1'
MIN_COMPARABLE_RECORD_WIDTH=240  # preregistered *experimental* readable-card floor, not a general CSS law
VIEWPORT_HEIGHT=900
WEIGHTS={
    'browse': {'workspace_top':5.0,'workspace_width':2.5,'tools_area':1.0,'record_access':2.0},
    'recover':{'failure_width':3.0,'failure_position':1.0,'workspace_access':0.25},
}


def jwrite(path, obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')


def digest_bytes(data:bytes)->str:return hashlib.sha256(data).hexdigest()


def canonical_hash(obj)->str:
    return digest_bytes(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())


def normalized_task(state):
    if state=='default':return 'browse'
    if state=='operation_verified_failure':return 'recover'
    raise ValueError('This bounded experiment only certifies default and operation_verified_failure states')


# Measurements are extracted from actual Chromium DOM after the current MCEL
# production canonical layout has mounted. No engine-declared ranking is trusted.
_BROWSER_PROBE=r'''({expectedCount})=>{
 const root=document.querySelector('#generated-root');
 const select=q=>document.querySelector(q);
 const visible=e=>!!e&&!e.hidden&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
 const rect=e=>{if(!visible(e))return null;const b=e.getBoundingClientRect();return {x:+b.left.toFixed(2),y:+b.top.toFixed(2),width:+b.width.toFixed(2),height:+b.height.toFixed(2)};};
 const status=select('[data-role="status-and-attention"]');
 const workspace=select('#mcel-workspace-composite');
 const tools=select('[data-role="supporting-tools"]');
 const feedback=select('[data-role="persistent-nonblocking-feedback"]');
 const search=select('#global-search');
 const items=[...document.querySelectorAll('.record')].filter(visible);
 const rows=items.map(x=>({rect:rect(x),textLength:(x.innerText||'').length}));
 const rroot=rect(root),rwork=rect(workspace),rstatus=rect(status),rtools=rect(tools),rfeedback=rect(feedback);
 // Visual overflow must be measured AFTER browser line breaking. A canvas
 // measurement of the longest unwrapped token is not evidence of overflow:
 // overflow-wrap:anywhere can preserve and display that token over many lines.
 let wordOverflow=[],wrappedTokens=[];
 for(const item of items){
  const box=item.getBoundingClientRect();
  const padLeft=parseFloat(getComputedStyle(item).paddingLeft)||0;
  const padRight=parseFloat(getComputedStyle(item).paddingRight)||0;
  const left=box.left+padLeft,right=box.right-padRight;
  const ctx=document.createElement('canvas').getContext('2d');
  const fields=[...item.querySelectorAll('.title,.record-entity,.semantic-record-field')]
    .filter(el=>el.getClientRects().length&&getComputedStyle(el).display!=='none');
  for(const field of fields){
   const st=getComputedStyle(field);ctx.font=st.font;
   const raw=field.textContent||'';
   const widest=Math.max(0,...raw.split(/\s+/).filter(Boolean).map(t=>ctx.measureText(t).width));
   const range=document.createRange();range.selectNodeContents(field);
   const fragments=[...range.getClientRects()];
   const visualOverflow=fragments.some(r=>r.width>0&&(r.left<left-2||r.right>right+2))
      ||field.scrollWidth>field.clientWidth+2;
   if(visualOverflow) wordOverflow.push({item:items.indexOf(item),
       field:field.className,measuredWordWidth:+widest.toFixed(2),cardWidth:+box.width.toFixed(2),
       fieldScrollWidth:field.scrollWidth,fieldClientWidth:field.clientWidth});
   else if(widest>Math.max(1,right-left))wrappedTokens.push({item:items.indexOf(item),
       field:field.className,intrinsicTokenWidth:+widest.toFixed(2),recordContentWidth:+(right-left).toFixed(2)});
  }
 }

 const count=root.querySelectorAll('[data-mcel-element-id]').length + Number(root.hasAttribute('data-mcel-element-id'));
 const controls=[...document.querySelectorAll('input,button,[role="tab"]')].filter(visible)
   .map(e=>({type:e.tagName.toLowerCase(),width:+e.getBoundingClientRect().width.toFixed(2),height:+e.getBoundingClientRect().height.toFixed(2)}));
 return {ready:window.__MCEL_GENERATED_READY===true,layout_status:window.__MCEL_LAYOUT_PROOF?.status||null,
  component_count:count,expected_components:expectedCount,
  active_task:root.dataset.activeTask||null,visual_state:root.dataset.visualFixtureState||'default',
  operation_state:window.__MCEL_STATE_PROOF?.operation?.state||null,
  regions:{root:rroot,status:rstatus,workspace:rwork,tools:rtools,feedback:rfeedback,search:rect(search)},
  first_record:rows.length?rows[0].rect:null,record_widths:rows.map(r=>r.rect.width),record_text_lengths:rows.map(r=>r.textLength),
  visible_row_count:rows.length,longest_token_violations:wordOverflow,wrapped_long_tokens:wrappedTokens,controls,
  search_colocated:!!search&&!!workspace&&workspace.contains(search),
  horizontal_overflow:document.documentElement.scrollWidth>innerWidth+1,
  viewport_width:innerWidth, viewport_height:innerHeight,
  feedback_visible:visible(feedback),failure_text_visible:visible(status)&&/operation failed/i.test(status.innerText||''),
  tools_text_length:visible(tools)?(tools.innerText||'').trim().length:0,
  document_height:document.documentElement.scrollHeight
 };
}'''


def browser_measure(page,expected_count:int):
    return page.evaluate(_BROWSER_PROBE,{'expectedCount':expected_count})


def feasibility(m,state):
    """Hard constraints, assessed independently of any policy utility score."""
    reasons=[]
    if not m.get('ready') or m.get('layout_status')!='pass':reasons.append('canonical_runtime_not_ready')
    if m.get('component_count')!=m.get('expected_components'):reasons.append('registered_component_count_changed')
    if not m.get('search_colocated'):reasons.append('search_detached_from_collection')
    if m.get('horizontal_overflow'):reasons.append('horizontal_overflow')
    if not m.get('regions',{}).get('workspace'):reasons.append('missing_primary_workspace')
    if not m.get('record_widths'):reasons.append('no_visible_collection_records')
    if m.get('record_widths') and min(m['record_widths'])<MIN_COMPARABLE_RECORD_WIDTH:
        reasons.append('record_below_readable_width_floor')
    if m.get('longest_token_violations'):reasons.append('unbroken_content_exceeds_card_width')
    if state=='operation_verified_failure':
        if m.get('operation_state')!='failed':reasons.append('verified_failure_not_in_operation_state')
        if not m.get('feedback_visible') or not m.get('failure_text_visible'):
            reasons.append('verified_failure_not_prominent_and_visible')
    return reasons


def _clip01(x):return max(0.0,min(1.0,x))


def quality_metrics(m,state):
    """Report separate interpretable dimensions; no invented aesthetic score."""
    rr=m['regions']['root']; rw=m['regions']['workspace']; rt=m['regions']['tools']; rf=m['regions']['feedback']
    root_width=max(rr['width'],1)
    # A growing collection may expand the document, but cannot make the same
    # supporting tools less costly. Normalize against a fixed viewport area.
    viewport_area=max(root_width*max(m['viewport_height'],1),1)
    viewport_height=max(m['viewport_height'],1)
    record=m.get('first_record')
    first_target_y=record['y'] if record else m['document_height']
    metrics={
      'workspace_width_fraction':round(_clip01(rw['width']/root_width),4),
      'workspace_top_viewport_fraction':round(_clip01(rw['y']/viewport_height),4),
      'first_record_top_viewport_fraction':round(_clip01(first_target_y/viewport_height),4),
      'scroll_px_to_first_record':round(max(0.0,first_target_y-viewport_height),2),
      'tools_area_fraction_of_viewport':round(_clip01((rt['width']*rt['height'])/viewport_area),4) if rt else 0.0,
      'tools_characters':m.get('tools_text_length',0),
      'minimum_visible_record_width_px':round(min(m['record_widths']),2) if m['record_widths'] else 0.0,
      'visible_record_count':m['visible_row_count'],
      'long_token_violations':len(m['longest_token_violations']),
      'wrapped_long_tokens':len(m.get('wrapped_long_tokens',[])),
    }
    if state=='operation_verified_failure' and rf:
        metrics['failure_width_fraction']=round(_clip01(rf['width']/root_width),4)
        metrics['failure_top_viewport_fraction']=round(_clip01(rf['y']/viewport_height),4)
    else:
        metrics['failure_width_fraction']=0.0
        metrics['failure_top_viewport_fraction']=None
    return metrics


def score_candidate(metrics,task):
    """Preregistered task-conditional experiment weights; no candidate identity input."""
    if task=='browse':
        return round(WEIGHTS[task]['workspace_width']*metrics['workspace_width_fraction']
               -WEIGHTS[task]['workspace_top']*metrics['workspace_top_viewport_fraction']
               -WEIGHTS[task]['tools_area']*metrics['tools_area_fraction_of_viewport']
               -WEIGHTS[task]['record_access']*metrics['first_record_top_viewport_fraction'],6)
    if task=='recover':
        return round(WEIGHTS[task]['failure_width']*metrics['failure_width_fraction']
               +WEIGHTS[task]['failure_position']*(1.0-metrics['failure_top_viewport_fraction'])
               -WEIGHTS[task]['workspace_access']*metrics['first_record_top_viewport_fraction'],6)
    raise ValueError(f'Unsupported experimental task: {task}')


def choose(cases):
    """Choose among feasible measured candidates; deterministic hash tie-break.

    Refuse a group with no feasible design. Input ordering is not a tiebreak.
    """
    valid=[x for x in cases if x['hard_status']=='PASS']
    if not valid:return {'status':'REFUSED','reason':'all_candidates_failed_hard_constraints','candidate':None}
    selected=sorted(valid,key=lambda r:(-r['experimental_utility'],r['candidate_fingerprint']))[0]
    return {'status':'EXPERIMENTAL_SELECTION','candidate':selected['candidate'],
            'experimental_utility':selected['experimental_utility'],
            'candidate_fingerprint':selected['candidate_fingerprint'],
            'reason':'Hard requirements first; task-conditioned, preregistered utility; fingerprint tiebreak',
            'human_approval':'PENDING'}


def _find_review_root(root):
    root=Path(root).resolve()
    if not (root/'packet.json').is_file() or not all((root/f'generated-{cid}'/'preview.html').is_file() for cid in 'ABC'):
        raise ValueError('Review root must contain packet.json and generated-A/B/C/preview.html. A portable packet ZIP alone lacks browser DOM sources; rebuild with --mode run.')
    return root


def experiment(review_root,output,*,chromium_executable=None):
    """Measure frozen A/B/C sources in a fresh independent browser session."""
    from playwright.sync_api import sync_playwright
    review_root=_find_review_root(review_root)
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    packet=json.loads((review_root/'packet.json').read_text(encoding='utf-8'))
    frozen,_=critic.open_packet(review_root)
    if canonical_hash(packet)!=canonical_hash(frozen):raise ValueError('Packet changed since validation')
    images={(e['candidate'],e['viewport'],e['state']):e for e in packet['screenshots']}
    source={};source_hash={}
    for cid in 'ABC':
        f=review_root/f'generated-{cid}'/'preview.html'
        source[cid]=f.read_text(encoding='utf-8')
        source_hash[cid]=digest_bytes(f.read_bytes())
    rows=[]
    with sync_playwright() as pw:
        browser,launch=launch_chromium(pw,executable=chromium_executable,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            browser_version=browser.version
            for shot in packet['screenshots']:
                cid,width,state=shot['candidate'],shot['viewport'],shot['state']
                if state not in ('default','operation_verified_failure'):
                    raise ValueError('Unsupported state in preregistered quality experiment')
                page=browser.new_page(viewport={'width':width,'height':VIEWPORT_HEIGHT},device_scale_factor=1)
                errors=[]
                page.on('pageerror',lambda e,errors=errors:errors.append(str(e)))
                try:
                    page.set_content(source[cid],wait_until='load',timeout=30000)
                    page.wait_for_function('window.__MCEL_GENERATED_READY === true',timeout=16000)
                    if state!='default':page.evaluate('(s)=>window.__MCEL_SET_VISUAL_FIXTURE(s)',state)
                    page.wait_for_timeout(160)
                    measurement=browser_measure(page,packet['component_count'])
                    hard=feasibility(measurement,state)
                    if errors:hard+=['browser_javascript_error']
                    metrics=quality_metrics(measurement,state)
                    task=normalized_task(state)
                    # All evidential measurements and the exact screenshot hash retained.
                    row={'candidate':cid,'viewport':width,'state':state,'task':task,
                         'screenshot_id':shot['id'],'screenshot_path':shot['path'],
                         'screenshot_sha256':shot['sha256'],'candidate_fingerprint':source_hash[cid],
                         'hard_status':'PASS' if not hard else 'FAIL','hard_reasons':hard,
                         'quality_dimensions':metrics,'experimental_utility':score_candidate(metrics,task),
                         'browser_measurement':measurement,'browser_errors':errors}
                    rows.append(row)
                    print('QUALITY_CASE',cid,width,state,row['hard_status'],row['experimental_utility'],flush=True)
                finally:page.close()
        finally:browser.close()
    groups={}
    for case in rows:groups.setdefault((case['viewport'],case['state']),[]).append(case)
    selected=[]
    for (width,state),cases in sorted(groups.items()):
        if {c['candidate'] for c in cases}!={'A','B','C'}:
            raise ValueError('Incomplete candidate comparisons cannot select a winner')
        res=choose(cases)
        selected.append({'viewport':width,'state':state,'task':normalized_task(state),**res,
                         'candidates':{c['candidate']:{'hard':c['hard_status'],'utility':c['experimental_utility'],
                                                   'hard_reasons':c['hard_reasons']} for c in cases}})
    status='BOUNDED_EXPERIMENT_PASS' if all(s['status']=='EXPERIMENTAL_SELECTION' for s in selected) else 'REFUSED'
    report={'schema':SCHEMA,'status':status,'policy_version':POLICY_VERSION,
            'measurement_contract':{
                'source':'independent fresh Chromium DOM observations, not MCEL planner scores',
                'hard_record_width_floor_px':MIN_COMPARABLE_RECORD_WIDTH,
                'dimensions':['workspace_width_fraction','workspace_top_viewport_fraction','first_record_top_viewport_fraction',
                              'scroll_px_to_first_record','tools_area_fraction_of_viewport',
                              'minimum_visible_record_width_px','failure_width_fraction','failure_top_viewport_fraction'],
                'experimental_task_weights':WEIGHTS,'viewport_height_px':VIEWPORT_HEIGHT,
                'tools_penalty_denominator':'root_width_times_viewport_height_not_document_height',
                'quality_boundary':'geometry/task proxies; NOT human visual quality or accessibility certification',
                'candidate_id_used_as_score_input':False,
            },
            'browser':{'version':browser_version,'launch':launch},'packet_sha256':canonical_hash(packet),
            'component_count':packet['component_count'],'candidate_source_hashes':source_hash,
            'candidate_measurements':rows,'selections':selected,
            'human_approval':'PENDING','production_policy_changed':False,
            'limitations':['Small and nonindependent A/B/C composition family',
                           'Experimental weights must be independently validated on unseen applications and states',
                           'Card width floor is an experiment assumption, not an accessibility guarantee',
                           'Screenshot review required; appearance cannot be certified from geometry',
                           'Synthetic fixture only; no live service or mutation proof']}
    jwrite(output/'quality-report.json',report)
    generate_comparison_gallery(review_root,output,report)
    write_evidence_archive(output,report)
    print('MCEL_DESIGN_QUALITY_'+status)
    print('quality_report:',output/'quality-report.json')
    print('gallery:',output/'quality-gallery.html')
    print('evidence_archive:',output/'quality-review.zip')
    return report


def generate_comparison_gallery(review_root,output,report):
    """Do not modify original images; link to source PNG evidence in review-root."""
    rr=Path(review_root).resolve();output=Path(output).resolve()
    image_dir=output/'screenshots'
    image_dir.mkdir(parents=True,exist_ok=True)
    for candidate in report['candidate_measurements']:
        filename=candidate['screenshot_path']
        if Path(filename).name!=filename:raise ValueError('Unsafe screenshot name')
        source=rr/filename
        if not source.is_file() or digest_bytes(source.read_bytes())!=candidate['screenshot_sha256']:
            raise ValueError('Screenshot altered or missing while preparing review gallery: '+filename)
        shutil.copyfile(source,image_dir/filename)
    rel='screenshots'
    cards=[]
    cases={(c['viewport'],c['state'],c['candidate']):c for c in report['candidate_measurements']}
    for selected in report['selections']:
        width,state=selected['viewport'],selected['state']
        labels=[]
        for cid in 'ABC':
            row=cases[(width,state,cid)];name=row['screenshot_path']
            if Path(name).name!=name:raise ValueError('Unsafe screenshot path')
            img=f'{rel}/{name}'
            winner=cid==selected['candidate']
            labels.append('<article'+(' class="selected"' if winner else '')+'><h3>Candidate '+cid+
                          (' · experimental selection' if winner else '')+'</h3><p>Hard: '+row['hard_status']+
                          ' · utility: '+str(row['experimental_utility'])+
                          ' · min card: '+str(row['quality_dimensions']['minimum_visible_record_width_px'])+
                          'px</p><a href="'+html.escape(img,quote=True)+'"><img src="'+html.escape(img,quote=True)+
                          '" alt="'+cid+' '+str(width)+' '+html.escape(state)+' screenshot"></a><p>'+html.escape(', '.join(row['hard_reasons']) or 'No measured hard failures')+'</p></article>')
        cards.append(f'<section><h2>{width}px · {html.escape(state)} · {html.escape(selected["task"])}'+
                     '</h2><div class="cols">'+''.join(labels)+'</div></section>')
    content='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MCEL deterministic quality experiment</title>
<style>body{font:15px/1.45 system-ui,sans-serif;background:#f5f7fa;color:#172432;margin:24px auto;max-width:1600px;padding:0 20px}.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,355px),1fr));gap:15px}article{padding:15px;background:#fff;border:1px solid #cdd6e0;border-radius:12px;min-width:0}article.selected{border:2px solid #4c78a8}h3{font-size:16px;margin:0}article p{font-size:12px;color:#4a5d73}article img{width:100%;height:auto;max-height:450px;object-fit:contain;object-position:top}section{margin:28px 0}a{display:block}footer{margin-top:30px}</style>
<h1>Deterministic MCEL quality experiment</h1><p>Fresh independent browser measurements and task-specific ranking of unchanged MCEL canonical A/B/C candidates. Highlight = experimental selection, NOT human visual approval.</p>'''+''.join(cards)+'''<footer>Human visual approval pending. Open images at full resolution. No production layout policy was changed.</footer></html>'''
    (output/'quality-gallery.html').write_text(content,encoding='utf-8')


def write_evidence_archive(output,report):
    """Self-contained evidence ZIP; review requires no running MCEL instance."""
    output=Path(output)
    files=['quality-report.json','quality-gallery.html']+[str(p.relative_to(output)).replace(os.sep,'/') for p in sorted((output/'screenshots').glob('*.png'))]
    for name in files:
        if not (output/name).is_file():raise ValueError('Evidence archive missing '+name)
    with zipfile.ZipFile(output/'quality-review.zip','w',zipfile.ZIP_DEFLATED,compresslevel=8) as z:
        for name in files:z.write(output/name,name)
    with zipfile.ZipFile(output/'quality-review.zip') as z:
        if z.testzip():raise ValueError('Broken quality review archive')
        if len([n for n in z.namelist() if n.startswith('screenshots/')])!=len(report['candidate_measurements']):
            raise ValueError('Evidence archive is missing candidate PNGs')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['run','evaluate'],default='run')
    p.add_argument('--design',type=Path,default=critic.generator.DEFAULT_DESIGN)
    p.add_argument('--output',type=Path,default=ROOT/'runtime/reports/mcel-layout-frob/design-quality')
    p.add_argument('--review-root',type=Path,default=None)
    p.add_argument('--chromium-executable',default=None)
    ns=p.parse_args(argv)
    output=ns.output.resolve();root=(ns.review_root or output/'design-review').resolve()
    try:
        if ns.mode=='run':
            built=critic.build(ns.design,root,chromium_executable=ns.chromium_executable)
            if built['status']!='PASS':raise ValueError('Design Critic packet did not pass browser acceptance')
        report=experiment(root,output,chromium_executable=ns.chromium_executable)
        return 0 if report['status']=='BOUNDED_EXPERIMENT_PASS' else 1
    except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
        print('MCEL_DESIGN_QUALITY_REFUSED:',str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':sys.exit(main())
