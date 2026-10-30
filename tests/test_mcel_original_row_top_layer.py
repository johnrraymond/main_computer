"""The *original* row must become the unclipped interactive surface (not a mirror)."""
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS, _chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_patch14_6_promise_verification_frob import scenario


def test_original_row_is_the_top_layer_hover_owner():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1440):
                page=browser.new_page(viewport={'width':width,'height':850},reduced_motion='reduce')
                errors=[]
                page.on('pageerror',lambda exc:errors.append(str(exc)))
                page.set_content(html_fixture(),wait_until='load')
                for script in ('mcel-presentation-negotiation.js','mcel-presentation-hosts.js',
                               'mcel-layout-continuity.js','mcel-universal-semantic-layout-lab.js'):
                    page.add_script_tag(path=str(SCRIPTS/script))
                start=page.evaluate('''({plan})=>{
                  const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
                  plan.collection.spec.sourceFields.push('verifiedGen');
                  plan.collection.spec.reconciliation={generationFrom:'verifiedGen'};
                  plan.collection.rows.forEach((r,i)=>r.verifiedGen='g-'+i);
                  const m=McelUniversalSemanticLayoutLab.mount(host,plan,{lab:true,presentationNegotiation:true});
                  window.__verifiedPlan=plan;
                  const row=host.querySelector('mcel-row'),list=host.querySelector('mcel-collection');
                  row.scrollIntoView({block:'center'});
                  window.__toplayer={m,host,row,list};
                  const rect=row.getBoundingClientRect();
                  return {rect:rect.toJSON(),firstText:row.querySelector('mcel-field').textContent,
                    neighbors:[...list.querySelectorAll(':scope > mcel-row')].map(el=>el.getBoundingClientRect().toJSON())};
                }''',{'plan':scenario('normal')['plan']})
                # Real pointer activation. No programmatic forged hover.
                row=page.locator('#task-manager-app mcel-row').first
                row.hover()
                page.wait_for_timeout(1250)
                current=page.evaluate('''()=>{
                  const {m,host,row,list}=__toplayer,pane=m.collection.floatingCard;
                  const bounds=row.getBoundingClientRect();
                  const sourceAt=document.elementFromPoint(bounds.left+30,bounds.top+20);
                  const spacer=list.querySelector('[data-mcel-popover-anchor="true"]');
                  const p=pane?.getBoundingClientRect();
                  return {sameNode:row===window.__toplayer.row,realPopover:row.matches(':popover-open'),
                    rowHover:row.matches(':hover'),inList:row.parentElement===list,
                    actualHit:row.contains(sourceAt),
                    noMirror:!pane?.querySelector('.mcel-unclip-mirror'),
                    spacerEmpty:!!spacer && spacer.childElementCount===0 && getComputedStyle(spacer).visibility==='hidden',
                    rowRect:bounds.toJSON(),pane:p?.toJSON(),
                    listValidated:!!m.collection.layout && m.tree.refresh().status==='pass'};
                }''')
                assert all(current[k] for k in ('sameNode','realPopover','rowHover','inList','actualHit','noMirror','spacerEmpty','listValidated')),current
                assert abs(current['rowRect']['left']-start['rect']['left'])<.6,current
                assert abs(current['rowRect']['top']-start['rect']['top'])<.6,current
                # Empty anchor must preserve *every other row* at its original coordinates.
                neighbors=page.evaluate('''()=>[...__toplayer.list.querySelectorAll(':scope > mcel-row')]
                  .map(el=>el.getBoundingClientRect().toJSON())''')
                assert all(abs(a['top']-b['top'])<.6 for a,b in zip(neighbors,start['neighbors'])),(neighbors,start['neighbors'])
                # The real top-layer row follows the invisible anchor when the scroll host moves.
                scrolling=page.evaluate('''()=>{
                  const {row,list}=__toplayer,anchor=list.querySelector('[data-mcel-popover-anchor]');
                  list.scrollTop+=18;
                  return {before:anchor.getBoundingClientRect().top,rowBefore:row.getBoundingClientRect().top};
                }''')
                page.wait_for_timeout(90)
                attached=page.evaluate('''()=>({row:__toplayer.row.getBoundingClientRect().top,
                  anchor:__toplayer.list.querySelector('[data-mcel-popover-anchor]').getBoundingClientRect().top})''')
                assert abs(attached['row']-attached['anchor'])<.75,(scrolling,attached)
                # Move pointer into content on the same real original row.
                ext=current['pane'];page.mouse.move(ext['left']+min(40,ext['width']/2),ext['top']+min(25,ext['height']/2))
                page.wait_for_timeout(1500)
                assert page.evaluate("__toplayer.row.matches(':hover') && __toplayer.row.matches(':popover-open')")
                # While a selection is active, a telemetry update must not remount the original node.
                updated=page.evaluate('''()=>{
                  const {m,row}=__toplayer;
                  const pane=m.collection.floatingCard;
                  const value=pane.querySelector('[data-mcel-selection-key="heading"]');
                  const selection=document.getSelection(),range=document.createRange();range.selectNodeContents(value);
                  selection.removeAllRanges();selection.addRange(range);
                  const previous=selection.toString(),change=structuredClone(__verifiedPlan);
                  change.collection.rows[0].name='Observed new name';
                  change.collection.rows[0].mcelCanonicalRecord.properties.name.raw='Observed new name';
                  const result=m.update(change);
                  return {ok:result.ok,sameNode:row===__toplayer.row,
                    originalHeading:selection.toString()===previous,
                    stillTopLayer:row.matches(':popover-open'),
                    selected:!!selection.toString(),layout:!!m.collection.layout};
                }''')
                assert all(updated.values()),updated
                page.evaluate('document.getSelection().removeAllRanges()')
                # Exit the actual original element and verify restoration.
                page.mouse.move(1,1)
                page.wait_for_timeout(1200)
                closed=page.evaluate('''()=>{
                  const {m,row,list}=__toplayer;
                  const result={sameNode:row===__toplayer.row,noPopover:!row.matches(':popover-open'),
                    noSpacer:!list.querySelector('[data-mcel-popover-anchor]'),
                    stillInList:row.parentElement===list,
                    layout:!!m.collection.layout && m.tree.refresh().status==='pass'};
                  m.destroy();return result;
                }''')
                assert all(closed.values()),closed
                assert not errors,errors
                page.close()
        finally:browser.close()
