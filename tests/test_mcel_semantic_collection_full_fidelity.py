"""Patch 06a: delivered collection data cannot be silently capped at 100."""
from pathlib import Path
import shutil
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHELL_JS = ROOT / "main_computer/web/applications/scripts/mcel-semantic-application-shell.js"


@pytest.mark.parametrize("count", [99, 100, 101, 181, 300])
def test_generated_collection_preserves_all_rows_and_keyed_focus(count):
    js = SHELL_JS.read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        kw = {"executable_path": chromium, "args": ["--no-sandbox", "--disable-dev-shm-usage"]} if chromium else {}
        browser = playwright.chromium.launch(headless=True, **kw)
        try:
            page = browser.new_page()
            page.set_content('<!doctype html><html><body><main id="host"></main></body></html>')
            page.add_script_tag(content=js)
            result = page.evaluate('''(count) => {
              const ir={application:{title:'Generic Collection'},intents:[],surfaces:[
                {semanticSurface:{controls:[{id:'select',role:'capability-action'}],applicationShell:{
                  schema:'mcel.semantic-application-shell.v1',layout:'sidebar-workspace',id:'collection-proof',
                  regions:[{id:'region:overview',zone:'sidebar',kind:'summary',title:'Overview',items:[]},
                    {id:'region:entries',zone:'main',kind:'collection',title:'Entries',path:'entries',
                     columns:['name'],rowKey:'id',selectControl:'select'}]
                }}}]};
              const selected=[];
              const runtime={available:()=>true,dispatch:async()=>({ok:true}),
                subscribe:fn=>{fn({phase:'idle',pending:false,receipt:null});return ()=>{};}};
              const host=document.getElementById('host');
              const app=McelSemanticApplicationShell.mount(host,ir,{effectRuntime:runtime,
                onCollectionSelect:(_region,record)=>{selected.push(record.id);return true;}});
              const values=Array.from({length:count},(_,i)=>({id:'entry-'+i,name:'Visible '+i}));
              app.update({entries:values});
              const rows=()=>[...host.querySelectorAll('[data-mcel-region="region:entries"] tbody tr')];
              const initial=rows();
              const beyond=initial[Math.min(count-1, 150)];
              const action=beyond.querySelector('button');
              action.click();
              action.focus();
              app.update({entries:values.map((r,i)=>i===count-1?{...r,name:'Updated last row'}:r)});
              const after=rows();
              const retained=after[Math.min(count-1, 150)]===beyond && document.activeElement===action;
              const unchangedKeys=after.every((r,i)=>r.dataset.mcelRowKey==='entry-'+i);
              const lastLabel=after[count-1].textContent;
              const changed=values.slice();
              changed[Math.min(count-1,150)]={id:'changed-key',name:values[Math.min(count-1,150)].name};
              app.update({entries:changed});
              const replacement=rows()[Math.min(count-1,150)];
              const changedIdentity=replacement!==beyond && replacement.dataset.mcelRowKey==='changed-key';
              app.destroy();
              return {initialCount:initial.length,afterCount:after.length,
                retained,unchangedKeys,lastLabel,selected,changedIdentity,unmounted:!host.firstElementChild};
            }''', count)
            assert result == {
                "initialCount": count,
                "afterCount": count,
                "retained": True,
                "unchangedKeys": True,
                "lastLabel": "Updated last row",
                "selected": [f"entry-{min(count-1,150)}"],
                "changedIdentity": True,
                "unmounted": True,
            }
        finally:
            browser.close()
