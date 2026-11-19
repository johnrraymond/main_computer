"""MCEL Lab Code Editor: live, populated source-bound mount and Vulcan Monaco parity."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
from main_computer.mcel_semantic_other_apps_host import render_semantic_page

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer/web/applications"


def test_code_editor_mount_in_lab_is_live_and_monaco_follows_selected_chrome():
    # The Code Editor already has a canonical route/IR in Lab. Prove that
    # opening a real document binds Monaco and that chrome changes reach it.
    lab_markup = (WEB / "apps/mcel-lab.html").read_text()
    lab_script = (WEB / "scripts/mcel-lab.js").read_text()
    assert '<option value="code-editor">Code Editor</option>' in lab_markup
    assert 'route: "/applications/code-editor"' in lab_script
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.evaluate("""() => {
              window.__codeEditorRequests=[];
              window.fetch=async (url, options={}) => {
                const path=String(url), body=JSON.parse(options.body||'{}');
                window.__codeEditorRequests.push({path,body});
                if (path.endsWith('/editor/files')) return {ok:true,status:200,
                  json:async()=>({path:body.path,entries:[
                    {path:'README.md',name:'README.md',kind:'file'},
                    {path:'src',name:'src',kind:'dir'}]})};
                if (path.endsWith('/editor/read')) return {ok:true,status:200,
                  json:async()=>({files:[{path:body.files[0],
                    content:'# Live source\\nconst value = 42;\\n'}]})};
                throw new Error('Unexpected endpoint in read-only mount test: '+path);
              };
              // Monaco is mocked at the rendering API boundary; the operational
              // file selection/read lifecycle and Code Editor DOM are real.
              const models=new Map();
              window.__monacoEvidence={themes:[],creations:[]};
              window.monaco={
                Uri:{parse:x=>x},
                editor:{
                  getModel:uri=>models.get(uri)||null,
                  createModel:(value,language,uri)=>{
                    const model={value,language,uri,getValue(){return this.value;},
                      setValue(v){this.value=v;},onDidChangeContent:()=>({dispose(){}})};
                    models.set(uri,model);return model;
                  },
                  create:(host,options)=>{
                    window.__monacoEvidence.creations.push({theme:options.theme,model:options.model.uri});
                    host.textContent='Monaco mounted: '+options.model.getValue();
                    return {layout(){},dispose(){},setModel(){},updateOptions(){}};
                  },
                  setTheme:theme=>window.__monacoEvidence.themes.push(theme)
                }
              };
            }""")
            page.set_content(render_semantic_page("code-editor", ROOT, promoted=True))
            for script in ("mcel-semantic-presentation-bridge.js",
                           "mcel-semantic-chrome-presentation-law.js"):
                page.add_script_tag(path=str(WEB / "scripts" / script))
            report = page.evaluate("""() => {
              const root=document.getElementById('mcel-workbench');
              const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
              window.__vulcanEditorParams={root,ir,
                bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine'};
              return {
                source:McelSemanticPresentationBridge.observeRuntimePresentationTargets({root,ir}).status,
                chrome:McelSemanticChromePresentationLaw.apply({...window.__vulcanEditorParams,
                  profile:'chrome-vulcan'}).status
              };
            }""")
            assert report == {"source":"source-bound", "chrome":"applied"},report
            page.get_by_role("button",name="List Files",exact=True).click()
            page.locator('[data-record-key="README.md"] [data-select-key]').click()
            page.wait_for_function("""() => window.__codeEditorRequests.some(x=>
              x.path.endsWith('/editor/read'))""")
            page.get_by_role("button",name="Open Source",exact=True).click()
            page.wait_for_function("""() =>
              document.querySelector('[data-mcel-document-surface]')?.dataset.monacoOutcome==='pass'""")
            data=page.evaluate("""() => {
              const root=document.getElementById('mcel-workbench');
              const css=x=>getComputedStyle(root.querySelector(x)).backgroundColor;
              return {created:window.__monacoEvidence.creations,
                fileReads:window.__codeEditorRequests.filter(r=>r.path.endsWith('/editor/read')).length,
                surface:css('[data-mcel-document-surface]'),
                navigation:css('[data-mcel-purpose="navigation"]'),
                workspace:css('[data-mcel-purpose="workspace"]'),
                readValue:root.querySelector('[data-mcel-document-surface]').textContent,
                source:window.__mcelWorkbench.appId};
            }""")
            assert data['created'][0]['theme']=='vs', data
            assert data['source']=='semantic-code-editor',data
            assert data['fileReads']>=1,data
            assert '# Live source' in data['readValue'],data
            assert data['surface']==data['navigation']==data['workspace']=='rgb(255, 255, 255)',data
            # The open editor must switch without remounting or discarding text.
            # Selecting a file may already open it, so preserve the observed
            # baseline instead of assuming the Open Source button is the first mount.
            baseline_mounts = len(data['created'])
            page.evaluate("""() => McelSemanticChromePresentationLaw.apply({
              ...window.__vulcanEditorParams,profile:'chrome-tng-side-rails'})""")
            page.wait_for_function("() => window.__monacoEvidence.themes.includes('vs-dark')")
            page.evaluate("""() => McelSemanticChromePresentationLaw.apply({
              ...window.__vulcanEditorParams,profile:'chrome-vulcan'})""")
            page.wait_for_function("() => window.__monacoEvidence.themes.at(-1)==='vs'")
            page.evaluate("""() => McelSemanticChromePresentationLaw.apply({
              ...window.__vulcanEditorParams,profile:'chrome-strict-hierarchy'})""")
            page.wait_for_function("() => window.__monacoEvidence.themes.at(-1)==='vs-dark'")
            assert page.evaluate('window.__monacoEvidence.creations.length')==baseline_mounts
            assert page.evaluate("window.__codeEditorRequests.some(x => x.path.includes('/save'))") is False
        finally:
            browser.close()
