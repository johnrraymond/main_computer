"""MCEL 14.7.7b browser gate: 20 independent records, one continuous card.

Run from repository root:
    python -m tools.mcel_row_atomic_hover_20_frob
This test operates only on offline semantic fixtures in Chromium.
"""
from __future__ import annotations
import argparse
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
from tools.mcel_lifecycle_taguchi_acid_frob import install
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'runtime/reports/mcel-row-atomic-hover-20'


def verify(output=OUTPUT,widths=(390,1100)):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.row-atomic-hover-20.v1','generatedUtc':datetime.now(timezone.utc).isoformat(),
            'readOnly':True,'cases':[],'errors':[]}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in widths:
                page=browser.new_page(viewport={'width':width,'height':450},reduced_motion='reduce')
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                case={'width':width,'checks':{},'errors':errors};report['cases'].append(case)
                def check(name,condition):case['checks'][name]=bool(condition)
                try:
                    install(page)
                    page.evaluate('''()=>document.querySelectorAll('.mcel-lifecycle-acid__gallery-options input[type="checkbox"]').forEach(x=>x.checked=false)''')
                    counts=page.evaluate('''()=>[...document.querySelectorAll('#mcel-lifecycle-acid-gallery > [data-case-id]')].map(c=>{
                      const l=c.querySelector('mcel-collection'),rs=[...l.querySelectorAll('mcel-row')];
                      return {n:rs.length,unique:new Set(rs.map(r=>r.dataset.rowId)).size,owners:rs.every(r=>r.dataset.mcelHoverOwner==='row')};})''')
                    check('one-case-20-distinct-individually-hoverable-records',len(counts)==1 and all(c['n']==c['unique']==20 and c['owners'] for c in counts))
                    card=page.locator('[data-case-id="L18-01"]')
                    card.scroll_into_view_if_needed()
                    initial=page.evaluate('''()=>{
                      const l=document.querySelector('[data-case-id="L18-01"] mcel-collection');
                      document.scrollingElement.scrollTop+=l.getBoundingClientRect().top-30;
                      window.__mcelList=l;window.__mcelRows=[...l.querySelectorAll('mcel-row')];
                      return {fields:[...window.__mcelRows[0].querySelectorAll('mcel-field:not([hidden])')].map(f=>f.getBoundingClientRect().toJSON()),
                        row:window.__mcelRows[0].getBoundingClientRect().toJSON(),
                        sibling:window.__mcelRows[1].getBoundingClientRect().toJSON(),
                        list:l.getBoundingClientRect().toJSON(),originalScroll:l.scrollTop};
                    }''')
                    page.wait_for_timeout(100)
                    row=initial['row'];x=row['left']+15;y=row['top']+18
                    page.mouse.move(1,1);page.mouse.move(x,y)
                    page.wait_for_timeout(150)
                    before=np.asarray(Image.open(io.BytesIO(page.screenshot())).convert('RGB'),dtype=np.int16)
                    check('delayed-activation-not-immediate',not page.evaluate('window.__mcelRows.some(r=>r.matches(":popover-open"))'))
                    page.evaluate('''()=>{
                      window.__mcelFirstFrames=[];
                      const started=performance.now();
                      (function sample(){
                        const l=window.__mcelList,r=window.__mcelRows[0],p=r.querySelector('.mcel-unclip-card');
                        if(r.matches(':popover-open')&&p){const rect=r.getBoundingClientRect(),tr=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
                          window.__mcelFirstFrames.push({x:rect.x,y:rect.y,width:rect.width,height:rect.height,
                            railVisible:!tr.hidden,railX:tr.getBoundingClientRect().x,
                            railHeight:tr.getBoundingClientRect().height,
                            rowWidth:r.getBoundingClientRect().width,rowHeight:r.getBoundingClientRect().height});}
                        if(performance.now()-started<1600)requestAnimationFrame(sample);
                      })();
                    }''')
                    page.wait_for_timeout(1220)
                    after=np.asarray(Image.open(io.BytesIO(page.screenshot())).convert('RGB'),dtype=np.int16)
                    state=page.evaluate('''()=>{
                      const l=window.__mcelList,rows=window.__mcelRows,r=rows[0],p=r.querySelector('.mcel-unclip-card');
                      const tr=document.querySelector('.mcel-unclip-row-scroll-track:popover-open'),v=r;
                      return {opened:r.matches(':popover-open'),listOpen:l.matches(':popover-open'),openCount:l.querySelectorAll('mcel-row:popover-open').length,
                        same:r===l.querySelector('mcel-row'),siblingSame:rows[1]===l.querySelectorAll('mcel-row')[1],
                        rect:r.getBoundingClientRect().toJSON(),sibling:rows[1].getBoundingClientRect().toJSON(),
                        list:l.getBoundingClientRect().toJSON(),p:p?.getBoundingClientRect().toJSON(),
                        own:p?.parentElement===r,track:tr?.getBoundingClientRect().toJSON(),trackHidden:tr?.hidden,
                        viewport:v?.getBoundingClientRect().toJSON(),scrollHeight:v?.scrollHeight,
                        frameLog:window.__mcelFirstFrames,
                        fields:[...r.querySelectorAll('mcel-field:not([hidden])')].map(f=>f.getBoundingClientRect().toJSON()),
                        panelStyle:p?{position:getComputedStyle(p).position,background:getComputedStyle(p).backgroundColor,
                          borderLeft:getComputedStyle(p).borderLeftWidth,borderRight:getComputedStyle(p).borderRightWidth}:null,
                        rowBackground:getComputedStyle(r).backgroundColor};
                    }''')
                    check('only-original-row-promoted',state['opened'] and not state['listOpen'] and state['openCount']==1 and state['same'] and state['own'])
                    check('sibling-stays-in-original-scroll-container',state['siblingSame'] and abs(state['sibling']['y']-initial['sibling']['y'])<.5)
                    check('collection-scrollport-unchanged',abs(state['list']['height']-initial['list']['height'])<.5 and abs(state['list']['width']-initial['list']['width'])<.5)
                    check('original-row-anchor-and-width-preserved',all(abs(state['rect'][key]-row[key])<.5 for key in ['x','y','width']))
                    check('original-card-actually-expands-downward',state['rect']['height']>row['height']+24 and
                          state['p']['top']>=row['bottom']-3 and state['scrollHeight']>state['rect']['height'])
                    check('all-original-fields-unchanged',len(initial['fields'])==len(state['fields']) and
                          all(all(abs(a[k]-b[k])<.5 for k in ['x','y','width','height'])
                              for a,b in zip(initial['fields'],state['fields'])))
                    check('extension-in-flow-without-independent-card',state['viewport'] and state['viewport']['height']==state['rect']['height'] and state['panelStyle']['position']=='relative' and
                          state['panelStyle']['borderLeft']=='0px' and state['panelStyle']['borderRight']=='0px' and
                          state['panelStyle']['background'] in ('rgba(0, 0, 0, 0)','transparent'))
                    check('expanded-card-opaque',state['rowBackground'].startswith('rgb('))
                    x1,y1=int(row['left']+3),int(max(0,row['top']+3))
                    x2,y2=int(min(width,row['right']-3)),int(min(450,row['bottom']-3))
                    pixel=np.abs(before[y1:y2,x1:x2]-after[y1:y2,x1:x2]);case['pixels']={'mean':float(pixel.mean()),'max':int(pixel.max())}
                    check('original-visible-pixels-preserved',case['pixels']['mean']<.25 and case['pixels']['max']<=3)
                    frames=state['frameLog'];case['frameCount']=len(frames)
                    def delta(key):return max(f[key] for f in frames)-min(f[key] for f in frames) if frames else float('inf')
                    check('complete-geometry-on-first-visible-frame',len(frames)>=3 and max(delta(k) for k in ('width','height','rowWidth','rowHeight','railX','railHeight'))<.6)
                    check('external-scrollbar-present-first-frame',len(frames)>=3 and all(f['railVisible'] for f in frames) and
                          state['track'] and state['track']['left']>=state['rect']['right']-2)
                    page.screenshot(path=str(output/f'row-hover-{width}.png'))
                    # A pointer crossing into the child extension remains inside the real record.
                    pane=state['p']
                    page.mouse.move(state['rect']['left']+20,min(state['rect']['bottom']-90,state['rect']['top']+state['rect']['height']-100))
                    page.wait_for_timeout(1150)
                    check('extension-is-same-hover-owner',page.evaluate('window.__mcelRows[0].matches(":popover-open")'))
                    # Independently scroll extension; then transfer after quantified overscroll.
                    scrollBefore=page.evaluate('''()=>{const l=__mcelList,v=__mcelRows[0];
                      return {parent:l.scrollTop,child:v.scrollTop};}''')
                    page.mouse.wheel(0,65);page.wait_for_timeout(90)
                    scrollAfter=page.evaluate('''()=>{const l=__mcelList,v=__mcelRows[0];
                      return {parent:l.scrollTop,child:v.scrollTop};}''')
                    check('extension-consumes-wheel-not-parent',scrollAfter['child']>scrollBefore['child'] and scrollAfter['parent']==scrollBefore['parent'])
                    page.evaluate('''()=>{const v=__mcelRows[0];v.scrollTop=v.scrollHeight-v.clientHeight;}''')
                    page.wait_for_timeout(180)
                    holdBefore=page.evaluate('__mcelList.scrollTop')
                    page.mouse.wheel(0,12);page.wait_for_timeout(180)
                    held=page.evaluate('__mcelList.scrollTop')
                    page.mouse.wheel(0,225);page.wait_for_timeout(130)
                    transferred=page.evaluate('__mcelList.scrollTop')
                    check('quantified-scroll-handoff',held==holdBefore and transferred>held)
                    case['handoff']={'before':holdBefore,'held':held,'after':transferred}
                    page.mouse.move(1,1);page.wait_for_timeout(1230)
                    check('one-second-leave-dismissal',not page.evaluate('window.__mcelRows.some(r=>r.matches(":popover-open"))'))
                    # Hover a different row; source update concerns row 1, so this
                    # independently hovered row must retain identity and presentation.
                    page.evaluate('''()=>{const l=__mcelList;l.scrollTop=__mcelRows[0].offsetHeight*3;}''')
                    pos=page.evaluate('''()=>__mcelRows[3].getBoundingClientRect().toJSON()''')
                    if 0<=pos['top']<400 and pos['bottom']>0:
                        page.mouse.move(pos['left']+17,max(12,pos['top']+24));page.wait_for_timeout(1170)
                        other=page.evaluate('''()=>({one:__mcelRows[0].matches(':popover-open'),other:__mcelRows[3].matches(':popover-open'),count:__mcelList.querySelectorAll('mcel-row:popover-open').length})''')
                        check('fourth-record-independent-hover',other['other'] and not other['one'] and other['count']==1)
                        page.evaluate('''()=>document.querySelector('[data-case-id="L18-01"] [data-gallery-action="source update"]').click()''')
                        page.wait_for_timeout(50)
                        updated=page.evaluate('''()=>({same:__mcelRows[3]===__mcelList.querySelectorAll('mcel-row')[3],
                          open:__mcelRows[3].matches(':popover-open'),n:__mcelList.querySelectorAll('mcel-row').length,
                          status:document.querySelector('[data-case-id="L18-01"] .mcel-lifecycle-acid__gallery-status').textContent})''')
                        check('unverified-source-replaced-safely',not updated['same'] and not updated['open'] and updated['n']==20 and 'integrity checks pass' in updated['status'])
                        case['updated']=updated
                    else:
                        check('fourth-record-independent-hover',False)
                        check('unverified-source-replaced-safely',False)
                        case['rowFourPosition']=pos
                    page.mouse.move(1,1);page.wait_for_timeout(1250)
                    check('no-late-popup-after-exit',not page.evaluate('window.__mcelList.querySelector("mcel-row:popover-open")'))
                    # Only L18-01 remains in the live gallery. Verified identity
                    # and orthogonal capability permutations are not operator
                    # examples here; test them in dedicated non-gallery Frobs.
                    case['scope']='L18-01 only; other 17 live cases removed intentionally'
                    check('browser-errors-free',not errors)
                    case['initial']=initial;case['open']=state
                except Exception as error:
                    report['errors'].append(f'{width}: {type(error).__name__}: {error}')
                finally:
                    page.close()
        finally:browser.close()
    report['status']='PASS' if not report['errors'] and all(all(c['checks'].values()) for c in report['cases']) else 'FAIL'
    (output/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
    print('MCEL_ROW_ATOMIC_HOVER_20_'+report['status'])
    for c in report['cases']:
        print(c['width'],{k:v for k,v in c['checks'].items() if not v},'pixels',c.get('pixels'),
              'frames',c.get('frameCount'),'handoff',c.get('handoff'))
    for error in report['errors']:print('ERROR',error)
    print('report',output/'report.json')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args();raise SystemExit(0 if verify(args.output)['status']=='PASS' else 1)
