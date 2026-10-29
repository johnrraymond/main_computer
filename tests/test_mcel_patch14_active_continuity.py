"""Patch 14.1 experimental continuity: browser measurements, not just a layout score."""
from __future__ import annotations
import json
import subprocess
import os
from pathlib import Path

from tools.mcel_representation_layout_contract import project_layout, STYLES
from tools.mcel_information_quality_frob import page_for
from tools.mcel_text_shrinker_adapter import load_flat_file

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'
PURE = ROOT/'main_computer/web/applications/scripts/mcel-layout-continuity.js'


def test_pure_commit_replays_1000_data_changes_and_row_order():
    script = r'''
const assert=require('node:assert/strict');
const api=require(process.argv[1]);
const fields=[{key:'pid',tier:'primary',required:true,semanticPropertyId:'mc.process.pid'},
 {key:'command',tier:'primary',required:true,semanticPropertyId:'mc.process.command_line'},
 {key:'memory',tier:'secondary',semanticPropertyId:'mc.quantity.bytes'}];
const link={required_keys:['pid','command'],compact_breakpoint_px:500,min_visible_cell_px:56};
function measured(width,commandSize){return {status:'candidate-verified',host_width_px:width,
 plan:{mode:width<500?'stacked':'table',available_px:width-32,
 content_minimum_px:{pid:56,command:commandSize,memory:80},
 allocated_px:{pid:100,command:220,memory:90},visible_keys:['pid','command','memory'],
 disclosed_keys:[]}};}
function run(){let state=null,log=[],switched=0,trackChanges=0;
 for(let i=0;i<1000;i++){
  const rowIds=i%2?['row2','row1','row3']:['row1','row2','row3'];
  const next=api.planCollection({evaluation:measured(800,56+(i*13)%900),columns:fields,
    link,previous:state,rowIds});
  if(state && state.decision.mode!==next.state.decision.mode)switched++;
  if(state && api.canonical(state.tracks)!==api.canonical(next.state.tracks))trackChanges++;
  assert.equal(next.plan.unresolved_deficit_px,0);
  assert.deepEqual(next.state.rowOrder,['row1','row2','row3']);
  state=next.state;log.push({decision:api.decisionKey(state.decision),
    tracks:state.tracks,order:state.rowOrder});
 }
 assert.equal(switched,0);assert.equal(trackChanges,0);
 assert.equal(state.decision.name,'mcel.layout.local/semantic-collection');
 assert.equal(state.commit,1000);
 const a=api.planCollection({evaluation:measured(490,800),columns:fields,link,previous:state,rowIds:['row2','row1']});
 assert.equal(a.plan.mode,'table');
 const b=api.planCollection({evaluation:measured(450,800),columns:fields,link,previous:a.state,rowIds:['row1']});
 assert.equal(b.plan.mode,'stacked');
 const c=api.planCollection({evaluation:measured(505,80),columns:fields,link,previous:b.state,rowIds:['row1']});
 assert.equal(c.plan.mode,'stacked');
 const d=api.planCollection({evaluation:measured(550,80),columns:fields,link,previous:c.state,rowIds:['row1']});
 assert.equal(d.plan.mode,'table');
 assert.throws(()=>api.planCollection({evaluation:measured(800,80),columns:fields,link,
 rowIds:['row1','row1']}),/unique row identities/);
 return api.canonical({log,switched,trackChanges});}
const first=run(),second=run();assert.equal(first,second);
console.log(JSON.stringify({replayed:1000,switched:0,trackChanges:0,
 hysteresis:['table','table','stacked','stacked','table'],sameReplay:first===second}));
'''
    result=subprocess.run(['node','-e',script,str(PURE)],cwd=ROOT,capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stderr
    proof=json.loads(result.stdout)
    assert proof['sameReplay'] and proof['switched']==0 and proof['trackChanges']==0


def _browser_doc(width=800):
    fixture=load_flat_file(ROOT/'tools/fixtures/mcel_text_shrinker_widgets.json')
    data=project_layout(fixture['widgets']['downloads'],fixture['value_sets'],viewport_px=width,enabled=True)
    assert len(data['source_rows'])==4
    html=page_for(data,'downloads','after')
    scripts='\n'.join('<script>'+p.read_text(encoding='utf-8')+'</script>' for p in (PURE,ENGINE))
    boot='''<script>
      window.__lab=window.McelSemanticLayoutInference.mountSemanticCollectionLayout(
       document.querySelector('#panel'),window.MCEL_INFORMATION_CONTRACT,
       window.McelElementRegistry,{observeResize:false,continuity:'lab'});
      if(window.__lab.status!=='pass')throw Error(window.__lab.reason);
    </script>'''
    return html.replace('</html>','<style>'+STYLES+'</style>'+scripts+boot+'</html>')


def test_chromium_active_continuity_displacement_focus_and_hysteresis():
    from playwright.sync_api import sync_playwright
    from tools.mcel_browser_launch import launch_chromium
    with sync_playwright() as pw:
        browser,_=launch_chromium(pw,headless=True)
        try:
            page=browser.new_page(viewport={'width':800,'height':800})
            errors=[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            page.set_content(_browser_doc(),wait_until='load')
            before=page.evaluate('''() => {
              const rows=[...document.querySelectorAll('mcel-row')];
              const rects=rows.map(r=>({id:r.dataset.rowId,top:r.getBoundingClientRect().top,
                x:r.querySelector('mcel-field').getBoundingClientRect().x}));
              window.__row0=rows[0];
              return {mode:document.querySelector('#panel').dataset.layoutMode,
                authority:document.querySelector('#panel').dataset.mcelCollectionLayoutAuthority,
                tracks:rows[0].style.gridTemplateColumns,rects,
                state:window.__lab.getCommittedState()};
            }''')
            assert before['mode']=='table',before
            assert before['authority']=='mcel-semantic-layout-continuity.lab.v1'
            page.locator('mcel-row').first.focus()
            page.keyboard.press('Enter')
            assert page.locator('#mcel-inspector').inner_text()
            # Exact producer/renderer parity is updated together; no fake zero.
            after=page.evaluate('''() => {
              const data=JSON.parse(JSON.stringify(window.MCEL_INFORMATION_CONTRACT));
              const rows=[...document.querySelectorAll('mcel-row')];
              const i=1,key='name';
              const full='an-extremely-long-filename-'+('x'.repeat(150));
              data.source_rows[i][key]=full;
              data.projection.rows[i].cells[key].display=full;
              data.projection.rows[i].cells[key].full=full;
              const field=[...rows[i].children].find(f=>f.dataset.semanticField===key);
              field.querySelector('.field-value').textContent=full;
              field.dataset.full=full;
              const receipt=window.__lab.refresh(data);
              window.__updatedContract=data;
              const focusAfterContent=document.activeElement===rows[0];
              // Producer reorders DOM; stable order is a CSS decision only.
              // Browser DOM moves can themselves drop focus; the layout engine
              // does not own the producer's DOM mutation or focus restoration.
              const collection=document.querySelector('mcel-collection');
              collection.append(rows[0]);
              const orderReceipt=window.__lab.refresh();
              const now=[...document.querySelectorAll('mcel-row')];
              const rects=now.map(r=>({id:r.dataset.rowId,top:r.getBoundingClientRect().top,
                x:r.querySelector('mcel-field').getBoundingClientRect().x}));
              return {receipt:receipt.status,action:receipt.record?.action,
                order:orderReceipt.record?.action,tracks:now[0].style.gridTemplateColumns,
                state:window.__lab.getCommittedState(),rects,
                sameNode:window.__row0===rows[0],focus:document.activeElement===rows[0],
                focusAfterContent,inspector:document.querySelector('#mcel-inspector').textContent.length>0,
                overflow:document.documentElement.scrollWidth>innerWidth+1};
            }''')
            assert after['receipt']=='pass',after
            assert after['tracks']==before['tracks'],(before,after)
            assert after['sameNode'] and after['focusAfterContent'] and after['inspector'],after
            assert not after['overflow'],after
            ordered_before=sorted(before['rects'],key=lambda r:r['top'])
            ordered_after=sorted(after['rects'],key=lambda r:r['top'])
            assert [r['id'] for r in ordered_before]==[r['id'] for r in ordered_after],(before,after)
            assert all(abs(a['x']-b['x'])<=0.25 for a,b in zip(ordered_before,ordered_after))
            # The browser's live viewport can cross the nominal breakpoint
            # without oscillating between configurations.
            mode_sequence=[]
            for width in (520,505,480,520,560):
                page.set_viewport_size({'width':width,'height':800})
                result=page.evaluate('''() => ({
                  refresh:window.__lab.refresh().status,
                  mode:document.querySelector('#panel').dataset.layoutMode,
                  width:document.querySelector('#panel').clientWidth,
                  overflow:document.documentElement.scrollWidth>innerWidth+1})''')
                assert result['refresh']=='pass',result
                assert not result['overflow'],result
                mode_sequence.append(result['mode'])
            assert mode_sequence[:2]==['table','table'],mode_sequence
            assert mode_sequence[2]=='stacked',mode_sequence
            assert mode_sequence[-2:]==['stacked','table'],mode_sequence
            previous=page.evaluate('window.__lab.getCommittedState()')
            # Serialized incumbent survives unmount/remount, restoring CSS.
            restored=page.evaluate('''(previous) => {
              window.__lab.unmount();
              const cleaned=document.querySelector('#panel').dataset.mcelCollectionLayoutAuthority;
              window.__lab=window.McelSemanticLayoutInference.mountSemanticCollectionLayout(
                document.querySelector('#panel'),window.__updatedContract,
                window.McelElementRegistry,{observeResize:false,continuity:'lab',previousState:previous});
              return {cleaned:cleaned||null,status:window.__lab.status,
                state:window.__lab.getCommittedState()};
            }''',previous)
            assert restored['cleaned'] is None,restored
            assert restored['status']=='pass',restored
            assert restored['state']['rowOrder']==previous['rowOrder']
            # A user-authorized sort must override sticky order. This is an
            # explicit commit, never an invisible reaction to data churn.
            source_order=page.evaluate('''() => {
              const rows=[...document.querySelector('mcel-collection').children];
              const receipt=window.__lab.commitSourceRowOrder();
              const byVisual=[...rows].sort((a,b)=>a.getBoundingClientRect().top-b.getBoundingClientRect().top);
              return {status:receipt.status,source:rows.map(r=>r.dataset.rowId),
                displayed:byVisual.map(r=>r.dataset.rowId)};
            }''')
            assert source_order['status']=='pass',source_order
            assert source_order['source']==source_order['displayed'],source_order
            assert not errors,errors
            evidence_path=os.environ.get('MCEL_PATCH14_BROWSER_EVIDENCE')
            if evidence_path:
                Path(evidence_path).parent.mkdir(parents=True,exist_ok=True)
                Path(evidence_path).write_text(json.dumps({
                    'schema':'mcel.patch14.1-browser-displacement-evidence.v1',
                    'status':'PASS','measuredWidths':[800,520,505,480,520,560],
                    'baselineTracks':before['tracks'],'afterContentTracks':after['tracks'],
                    'layoutModeSequence':mode_sequence,
                    'visualOrderRetainedAfterSourceReorder':True,
                    'maxColumnXDisplacementPx':max(abs(a['x']-b['x']) for a,b in zip(ordered_before,ordered_after)),
                    'focusedNodeRetainedOnDataOnlyUpdate':after['focusAfterContent'],
                    'focusAfterExternalDomMove':after['focus'],
                    'browserErrorCount':len(errors),
                    'noHorizontalOverflow':not after['overflow'],
                    'explicitUserOrderCommit':source_order['source']==source_order['displayed'],
                    'serverHosted':False,'productionPromotion':False
                },indent=2)+'\n',encoding='utf-8')
        finally:
            browser.close()
