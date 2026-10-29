"""Patch 14.7.4: independent cross-lifecycle integrity verification.

Run in a checkout with playwright installed:
  python -m tools.mcel_patch14_7_4_lifecycle_integrity_frob
Read-only offline Task Manager fixtures; never mutates production applications.
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument('--output',type=Path,default=Path('runtime/reports/mcel-patch14-7-4-lifecycle-integrity/report.json'))
    args=ap.parse_args()
    root=args.repo.resolve()
    sys.path.insert(0,str(root))
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_6_promise_verification_frob import scenario
    from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS,_chrome
    from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
    plan=scenario('normal')['plan']
    results=[]
    def case(name,expected,observed,details):
        results.append({'name':name,'expected':expected,'observed':observed,'pass':expected==observed,'details':details})
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            def new_page():
                page=browser.new_page(viewport={'width':1440,'height':850},reduced_motion='reduce')
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content(html_fixture(),wait_until='load')
                for name in ('mcel-presentation-negotiation.js','mcel-presentation-hosts.js',
                             'mcel-layout-continuity.js','mcel-universal-semantic-layout-lab.js'):
                    page.add_script_tag(path=str(SCRIPTS/name))
                return page,errors
            # Unchanged fixture, no generationFrom: a source membership event MUST NOT be invented.
            page,errors=new_page()
            same=page.evaluate('''({plan,verified})=>{
                const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
                const data=structuredClone(plan);
                if(verified){data.collection.spec.sourceFields.push('verifiedGen');
                    data.collection.spec.reconciliation={generationFrom:'verifiedGen'};
                    data.collection.rows.forEach((row,i)=>row.verifiedGen='G'+i);}
                const policy={schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'timed',
                    delayMs:10000,pauseWhen:[],onPause:'reset',maxRetained:40};
                const m=McelUniversalSemanticLayoutLab.mount(host,data,{lab:true,presentationNegotiation:true,
                    sourceRetirement:true,retirementPolicy:policy});
                const before=m.collection.ids.length;
                const update=m.update(structuredClone(data));
                const r={ok:update.ok,before,live:m.collection.ids.length,retired:m.collection.retirement.ids.length,
                    domRows:host.querySelectorAll('mcel-row').length,layout:!!m.collection.layout,
                    tree:m.tree.refresh().status};
                m.destroy();host.remove();return r;
            }''',{'plan':plan,'verified':False})
            case('unchanged_snapshot_without_generation_does_not_invent_retirements',True,
                 same['retired']==0 and same['domRows']==same['live'] and same['ok'],{'reproduction':same,'browserErrors':errors})
            page.close()
            # Stable generation-token control.
            page,errors=new_page()
            stable=page.evaluate('''({plan})=>{
                const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
                const data=structuredClone(plan);data.collection.spec.sourceFields.push('verifiedGen');
                data.collection.spec.reconciliation={generationFrom:'verifiedGen'};
                data.collection.rows.forEach((row,i)=>row.verifiedGen='G'+i);
                const policy={schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'timed',
                    delayMs:10000,pauseWhen:[],onPause:'reset',maxRetained:40};
                const m=McelUniversalSemanticLayoutLab.mount(host,data,{lab:true,presentationNegotiation:true,
                    sourceRetirement:true,retirementPolicy:policy});
                const update=m.update(structuredClone(data));
                const r={ok:update.ok,live:m.collection.ids.length,retired:m.collection.retirement.ids.length,
                    domRows:host.querySelectorAll('mcel-row').length};
                m.destroy();host.remove();return r;
            }''',{'plan':plan})
            case('stable_verified_generation_preserves_live_rows',True,
                 stable['ok'] and stable['retired']==0 and stable['domRows']==stable['live'],
                 {'reproduction':stable,'browserErrors':errors})
            page.close()
            # Pointer inside actual floating card counts as *hover on logical element* when policy requests it.
            page,errors=new_page()
            initial=page.evaluate('''({plan})=>{
                const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
                const data=structuredClone(plan);data.collection.spec.sourceFields.push('verifiedGen');
                data.collection.spec.reconciliation={generationFrom:'verifiedGen'};
                data.collection.rows.forEach((row,i)=>row.verifiedGen='G'+i);
                const policy={schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'timed',
                    delayMs:150,pauseWhen:['hovered'],onPause:'reset',maxRetained:40};
                const m=McelUniversalSemanticLayoutLab.mount(host,data,{lab:true,presentationNegotiation:true,
                    sourceRetirement:true,retirementPolicy:policy});
                window.__audit={host,data,m};
                const id=m.collection.ids[0],opened=m.collection.request(id,'onHover'),pane=m.collection.floatingCard;
                const r=pane.getBoundingClientRect();
                return {opened:opened.ok,x:r.x+Math.min(35,r.width/2),y:r.y+Math.min(30,r.height/2)};
            }''',{'plan':plan})
            page.mouse.move(initial['x'],initial['y'])
            hovered=page.evaluate('''()=>{
                const a=__audit,pane=a.m.collection.floatingCard,row=a.host.querySelector('mcel-row');
                return {paneHover:pane.matches(':hover'),rowHover:row.matches(':hover')};
            }''')
            disappear=page.evaluate('''()=>{
                const a=__audit,missing=structuredClone(a.data);missing.collection.rows.shift();
                const change=a.m.update(missing);
                return {ok:change.ok,atStart:a.m.collection.retirement.entries};
            }''')
            page.wait_for_timeout(245)
            finished=page.evaluate('''()=>{
                const a=__audit;
                const result={retired:a.m.collection.retirement.ids.length,
                    panePresent:!!a.m.collection.floatingCard,layout:!!a.m.collection.layout};
                a.m.destroy();a.host.remove();return result;
            }''')
            case('hover_on_floating_card_protects_retired_element',True,
                 bool(initial['opened'] and hovered['paneHover'] and disappear['ok'] and finished['retired']==1),
                 {'initial':initial,'hover':hovered,'disappearance':disappear,
                  'after245ms':finished,'browserErrors':errors})
            page.close()
            # Browser native text selection protects a retired value past the timer in the same runtime.
            page,errors=new_page()
            protected=page.evaluate('''async ({plan})=>{
                const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
                const data=structuredClone(plan);data.collection.spec.sourceFields.push('verifiedGen');
                data.collection.spec.reconciliation={generationFrom:'verifiedGen'};
                data.collection.rows.forEach((row,i)=>row.verifiedGen='G'+i);
                const policy={schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'timed',
                    delayMs:140,pauseWhen:['text-selected'],onPause:'reset',maxRetained:40};
                const m=McelUniversalSemanticLayoutLab.mount(host,data,{lab:true,presentationNegotiation:true,
                    sourceRetirement:true,retirementPolicy:policy});
                const row=host.querySelector('mcel-row'),span=row.querySelector('.field-value');
                const selection=document.getSelection(),range=document.createRange();range.selectNodeContents(span);
                selection.removeAllRanges();selection.addRange(range);
                const missing=structuredClone(data);missing.collection.rows.shift();const updated=m.update(missing);
                await new Promise(resolve=>setTimeout(resolve,230));
                const r={ok:updated.ok,retired:m.collection.retirement.ids.length,
                    connected:row.isConnected,selectionPreserved:String(selection)===span.textContent,
                    layout:!!m.collection.layout};
                selection.removeAllRanges();m.destroy();host.remove();return r;
            }''',{'plan':plan})
            case('native_selection_protects_retired_record',True,
                 protected['ok'] and protected['retired']==1 and protected['connected'] and protected['selectionPreserved'],
                 {'reproduction':protected,'browserErrors':errors})
            page.close()
            # Repeated identical updates without generation must never accumulate
            # retired ghosts, even after replacing all DOM nodes repeatedly.
            page,errors=new_page()
            repeated=page.evaluate('''({plan})=>{
              const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
              const data=structuredClone(plan),m=McelUniversalSemanticLayoutLab.mount(host,data,
                {lab:true,presentationNegotiation:true,sourceRetirement:true});
              const initial=m.collection.ids.length,history=[];
              for(let i=0;i<7;i++){
                const u=m.update(structuredClone(data));
                history.push({ok:u.ok,removed:u.collection?.removed,unverified:u.collection?.continuityUnverified,
                  live:m.collection.ids.length,retired:m.collection.retirement.ids.length,
                  dom:host.querySelectorAll('mcel-row').length,collectionLayout:!!m.collection.layout,
                  outerLayout:m.tree.refresh().status});
              }
              m.destroy();host.remove();return {initial,history};
            }''',{'plan':plan})
            case('repeated_unverified_snapshots_never_create_retirement_ghosts',True,
                 all(x['ok'] and x['removed']==0 and x['retired']==0 and
                     x['live']==repeated['initial'] and x['dom']==x['live'] and
                     x['collectionLayout'] and x['outerLayout']=='pass' for x in repeated['history']),
                 {'reproduction':repeated,'browserErrors':errors})
            page.close()
            # Identity replacement on the same source key must invalidate old
            # presentation state but must not assert source disappearance.
            page,errors=new_page()
            changed=page.evaluate('''({plan})=>{
              const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
              const data=structuredClone(plan);data.collection.spec.sourceFields.push('verifiedGen');
              data.collection.spec.reconciliation={generationFrom:'verifiedGen'};
              data.collection.rows.forEach((row,i)=>row.verifiedGen='generation-'+i);
              const m=McelUniversalSemanticLayoutLab.mount(host,data,{lab:true,
                presentationNegotiation:true,sourceRetirement:true});
              const key=m.collection.ids[0],old=host.querySelector('mcel-row');
              const open=m.collection.request(key,'onHover');
              const next=structuredClone(data);next.collection.rows[0].verifiedGen='replacement-generation';
              const update=m.update(next),newRow=[...host.querySelectorAll('mcel-row')]
                .find(n=>n.dataset.rowId===key);
              const r={opened:open.ok,updated:update.ok,receipt:update.collection,
                stillPresent:m.collection.ids.includes(key),nodeReplaced:newRow!==old,
                oldDisconnected:!old.isConnected,retired:m.collection.retirement.ids.length,
                floatingClosed:!m.collection.floatingCard,layout:!!m.collection.layout,
                tree:m.tree.refresh().status};m.destroy();host.remove();return r;
            }''',{'plan':plan})
            case('same_key_new_generation_is_replacement_not_disappearance',True,
                 changed['opened'] and changed['updated'] and changed['stillPresent'] and
                 changed['nodeReplaced'] and changed['oldDisconnected'] and changed['retired']==0 and
                 changed['floatingClosed'] and changed['layout'] and changed['tree']=='pass' and
                 changed['receipt']['identityReplaced']==1 and changed['receipt']['removed']==0,
                 {'reproduction':changed,'browserErrors':errors})
            page.close()
            # Source actually disappears while pointer is on a floating card.
            # Hover should protect, leaving the region should release the
            # protection, and a reset timer should then complete normally.
            page,errors=new_page()
            opened=page.evaluate('''({plan})=>{
              const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
              const data=structuredClone(plan);data.collection.spec.sourceFields.push('verifiedGen');
              data.collection.spec.reconciliation={generationFrom:'verifiedGen'};
              data.collection.rows.forEach((row,i)=>row.verifiedGen='generation-'+i);
              const policy={schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'timed',
                delayMs:160,pauseWhen:['hovered'],onPause:'reset',maxRetained:40};
              const m=McelUniversalSemanticLayoutLab.mount(host,data,{lab:true,
                presentationNegotiation:true,sourceRetirement:true,retirementPolicy:policy});
              window.__lifecycle={m,host,data};const id=m.collection.ids[0];
              const open=m.collection.request(id,'onHover');
              const rect=m.collection.floatingCard.getBoundingClientRect();
              return {ok:open.ok,x:rect.x+Math.min(32,rect.width/2),y:rect.y+Math.min(24,rect.height/2)};
            }''',{'plan':plan})
            page.mouse.move(opened['x'],opened['y'])
            removal=page.evaluate('''()=>{
              const {m,data}=window.__lifecycle,missing=structuredClone(data);
              missing.collection.rows.shift();const update=m.update(missing);
              return {ok:update.ok,entries:m.collection.retirement.entries};
            }''')
            page.wait_for_timeout(240)
            held=page.evaluate('''()=>({hovered:!!__lifecycle.m.collection.floatingCard?.matches(':hover'),
              protected:__lifecycle.m.collection.retirement.entries.some(e=>e.reasons.includes('hovered')),
              retained:__lifecycle.m.collection.retirement.ids.length===1})''')
            page.mouse.move(2,2)
            page.wait_for_timeout(60)
            released=page.evaluate('''()=>__lifecycle.m.collection.retirement.entries.map(e=>e.state)''')
            page.wait_for_timeout(230)
            finished=page.evaluate('''()=>{
              const {m,host}=window.__lifecycle;
              const result={retired:m.collection.retirement.ids.length,layout:!!m.collection.layout,
                tree:m.tree.refresh().status};m.destroy();host.remove();return result;
            }''')
            case('floating_hover_release_resets_retirement_and_eventually_removes',True,
                 opened['ok'] and removal['ok'] and held['hovered'] and held['protected'] and
                 held['retained'] and released==['retiring'] and finished['retired']==0 and
                 finished['layout'] and finished['tree']=='pass',
                 {'opened':opened,'removal':removal,'held':held,'released':released,'finished':finished,'browserErrors':errors})
            page.close()
            # A refused canonical layout must roll back membership AND cancel
            # retirement entries created by the rejected observation.
            page,errors=new_page()
            rolled=page.evaluate('''({plan})=>{
              const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
              const data=structuredClone(plan);let attempt=0;
              const authority={mountSemanticCollectionLayout(...args){
                attempt++;
                if(attempt===2)return {status:'refused',reason:'INJECTED_FAILURE'};
                return McelSemanticLayoutInference.mountSemanticCollectionLayout(...args);
              }};
              const policy={schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'timed',
                delayMs:10000,pauseWhen:[],onPause:'reset',maxRetained:40};
              const m=McelSemanticCollectionWorkspace.mount(host,data.collection.rows,
                data.collection.spec,{authority,humanFirst:true,presentationNegotiation:true,
                sourceRetirement:true,retirementPolicy:policy});
              const before=m.ids.length,next=structuredClone(data.collection.rows);next.shift();
              const failed=m.update(next);
              const result={before,attempt,rejected:failed.ok===false,
                stillLive:m.ids.length,retired:m.retirement.ids.length,
                domRows:host.querySelectorAll('mcel-row').length,
                hasLayout:!!m.layout};m.destroy();host.remove();return result;
            }''',{'plan':plan})
            case('failed_layout_update_rolls_back_new_retirement_entries',True,
                 rolled['rejected'] and rolled['attempt']==3 and rolled['retired']==0 and
                 rolled['before']==rolled['stillLive']==rolled['domRows'] and rolled['hasLayout'],
                 {'reproduction':rolled,'browserErrors':errors})
            page.close()
        finally: browser.close()
    report={'schema':'mcel.patch14.7.4-lifecycle-integrity.v1','root':str(root),'cases':results,
            'passed':sum(x['pass'] for x in results),'failed':sum(not x['pass'] for x in results),
            'conclusion':'NOT READY FOR GENERAL DEPLOYMENT' if any(not x['pass'] for x in results) else 'NO FAILURES IN THESE PROBES'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
    for x in results:print(('PASS' if x['pass'] else 'FAIL'),x['name'])
    print('MCEL_PATCH14_7_4_'+('ENGINEERING_PASS' if report['failed']==0 else 'ENGINEERING_FAIL'));print('report:',args.output)
    return 0 if report['failed']==0 else 2

if __name__=='__main__':raise SystemExit(main())
