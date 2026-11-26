"""Native MCEL workbook application; privileged host never loads DocsAPI."""
from pathlib import Path
import json
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_applications_registry import attach_inert_presentation
from main_computer.mcel_chat_console_host import SCRIPTS as CHAT_SCRIPTS, WORKERS as CHAT_WORKERS

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('main_computer/web/applications')
SCRIPTS = ('mcel-semantic-operational-runtime.js','mcel-onlyoffice-port.js','mcel-onlyoffice-workspace.js')
STYLES = ('mcel-semantic-workbench.css','chat-console.css','mcel-semantic-chat-console.css','mcel-onlyoffice-workspace.css')


def compile_onlyoffice(repo=ROOT):
    report = compile_dsl_application(repo/'mcel_apps/onlyoffice/application.js')
    if not report.valid or report.normalized_ir is None or report.app_id != 'onlyoffice':
        raise ValueError(f'ONLYOFFICE compilation failed: {report.diagnostics}')
    return report.normalized_ir


def render_onlyoffice_page(repo=ROOT):
    ir = compile_onlyoffice(repo)
    payload = json.dumps(ir,ensure_ascii=True).replace('<','\\u003c')
    def asset(kind,name):
        tag = 'script' if kind=='scripts' else 'style'
        source = (repo/WEB/kind/name).read_text(encoding='utf-8')
        if f'</{tag}' in source.lower(): raise ValueError('Unsafe inline asset '+name)
        return f'<{tag}>\n{source}\n</{tag}>'
    # Reuse the exact shared Chat Console controller and worker sources.
    chat = '<script>function evaluateCalculatorArithmeticExpression(expression){return window.MainComputerCalculatorCore.evaluateCalculatorExpression(expression);}</script>'
    for name in CHAT_WORKERS:
        source = (repo/WEB/'scripts'/name).read_text(encoding='utf-8')
        if '</script' in source.lower(): raise ValueError('Unsafe Chat Console worker '+name)
        chat += f'<script type="text/plain" id="{name.removesuffix(".js")}-source">{source}</script>'
    for path in CHAT_SCRIPTS:
        name = str(Path(path).relative_to(WEB/'scripts')).replace('\\','/')
        chat += asset('scripts',name)
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ONLYOFFICE</title>''' + '\n'.join(asset('styles',f) for f in STYLES) + '''</head><body class="mcel-workbench-page mcel-onlyoffice-page" data-active-app="onlyoffice"><main id="mcel-onlyoffice-root" aria-label="Native MCEL ONLYOFFICE"></main><script type="application/json" id="mcel-onlyoffice-ir">''' + payload + '</script>\n' + chat + '\n'.join(asset('scripts',f) for f in SCRIPTS) + '''
<script>(function(){"use strict";
const root=document.getElementById('mcel-onlyoffice-root');let workspace,app;
try{const ir=JSON.parse(document.getElementById('mcel-onlyoffice-ir').textContent);
const port=McelOnlyOfficePort.create(()=>workspace?.host,()=>workspace?.picker.files[0],{onStatus:message=>{if(workspace)workspace.notice.textContent=message;}});
app=McelSemanticOperationalRuntime.mount(root,ir,{capabilities:port,presentation:'expert'});
workspace=McelOnlyOfficeWorkspace.mount(root,()=>app.execute('intent:upload-workbook'));
// Follow the MCEL selection receipt, not a DOM click or a private state write.
// A selection made while a capability is pending is opened when it settles.
let pendingOpen=false,draining=false,pendingWritten='';
async function drain(){
  if(draining||port.isBusy())return;
  draining=true;
  try{
    while(!port.isBusy()&&(pendingOpen||pendingWritten)){
      if(pendingWritten){const path=pendingWritten;pendingWritten='';
        const selected=await app.execute('intent:select-workbook',{key:path});
        if(selected.disposition!=='selected')workspace.notice.textContent='Workbook saved; select '+path+' to open. '+String(selected.reason||'');
      }
      if(pendingOpen&&!port.isBusy()){pendingOpen=false;
        const opened=await app.execute('intent:open-workbook');
        if(opened.disposition!=='completed')workspace.notice.textContent='Open failed: '+String(opened.reason||opened.disposition);
      }
    }
  }finally{draining=false;}
}
root.addEventListener('mcel:parity-operation',event=>{
  const d=event.detail;if(d.appId!=='onlyoffice'||d.phase!=='settled')return;
  if(d.intentId==='intent:select-workbook'&&d.disposition==='selected')pendingOpen=true;
  if(d.disposition==='completed'){
    if(d.intentId==='intent:create-workbook'||d.intentId==='intent:upload-workbook'){
      pendingWritten=port.takeWrittenPath();
      if(d.intentId==='intent:create-workbook')workspace.name.value='';
    }
  }
  queueMicrotask(()=>{void drain();});
});
window.__mcelWorkbench=Object.freeze({app,appId:'onlyoffice'});
document.body.dataset.mcelOnlyofficeReady='true';
window.addEventListener('pagehide',()=>port.closeDocument(),{once:true});
(async()=>{await app.execute('intent:refresh-workbooks');await app.execute('intent:refresh-server-status');})();
}catch(error){root.textContent='ONLYOFFICE unavailable: '+String(error.message||error);throw error;}
})();</script></body></html>'''
    return attach_inert_presentation(page,repo,'onlyoffice','mcel-onlyoffice-root',promoted=True)
