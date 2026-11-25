"""Native canonical MCEL Wallet; no authored HTML layout or wallet secrets."""
from __future__ import annotations
import json
from pathlib import Path
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_applications_registry import attach_inert_presentation
from main_computer.mcel_knowledge.semantic_composition import P3_POLICY,synthesize_composition,verify_composition

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('mcel_apps/wallet/application.js')
BASE=Path('main_computer/web/applications')
ASSETS=('mcel-diagnostic-early-bootstrap.js','mcel-semantic-layout-inference.js',
        'mcel-semantic-value-updaters.js','mcel-semantic-operational-runtime.js','mcel-semantic-wallet-ports.js')


def compile_wallet_application(repo=ROOT):
    report=compile_dsl_application(repo/SOURCE)
    if not report.valid or not report.normalized_ir:
        raise ValueError('Invalid Wallet semantics: '+str(report.diagnostics))
    ir=report.normalized_ir
    if ir['application']['appId']!='wallet' or ir.get('layouts'):
        raise ValueError('Wallet must use native MCEL composition')
    composition=synthesize_composition(ir,policy=P3_POLICY)
    verify_composition(ir,composition)
    return ir,composition


def render_wallet_page(repo=ROOT):
    ir,composition=compile_wallet_application(repo)
    payload=json.dumps({'ir':ir,'composition':composition},separators=(',',':')).replace('<','\\u003c')
    css=(repo/BASE/'styles/mcel-semantic-workbench.css').read_text(encoding='utf-8')
    if '</style' in css.lower(): raise ValueError('Unsafe style')
    scripts=[]
    for name in ASSETS:
        code=(repo/BASE/'scripts'/name).read_text(encoding='utf-8')
        if '</script' in code.lower(): raise ValueError('Unsafe script')
        scripts.append('<script>\n'+code+'\n</script>')
    page='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Wallet · MCEL</title><style>'''+css+'''</style></head>
<body class="mcel-workbench-page"><div id="mcel-workbench" aria-label="MCEL Wallet workspace"></div><p id="mcel-workbench-status" role="status">Loading Wallet</p>
<script type="application/json" id="mcel-generated-data">'''+payload+'''</script>'''+''.join(scripts)+'''
<script>(function(){"use strict";
 const status=document.getElementById("mcel-workbench-status");
 try {
   const data=JSON.parse(document.getElementById("mcel-generated-data").textContent);
   const port=McelSemanticWalletPorts.create();
   const app=McelSemanticOperationalRuntime.mount(document.getElementById("mcel-workbench"),data.ir,{capabilities:port,composition:data.composition,presentation:"expert"});
   window.__mcelWallet=Object.freeze({app,appId:"wallet",composition:data.composition});
   status.textContent="User and ORG-SLH portfolio. Available credits are spendable; cumulative bridged credits are not an additional balance.";
   const refresh=async()=>{try{await app.execute("intent:refresh-portfolio");}catch(error){status.textContent="Portfolio refresh: "+String(error?.message||error);}};
   void (async()=>{try{await app.execute("intent:discover-wallets");}catch(error){status.textContent="Wallet discovery: "+String(error?.message||error);} await refresh(); try{await app.execute("intent:refresh-identities");}catch(_){} })();
   let refreshing=false;
   document.getElementById("mcel-workbench").addEventListener("mcel:parity-operation",event=>{
     if(event.detail?.phase!=="settled")return;
     const intent=event.detail.intentId;
     if(intent==="intent:choose-wallet"||intent==="intent:authorize-read"){
       if(intent==="intent:choose-wallet")void app.execute("intent:inspect-wallet").catch(()=>{});
       else void refresh();
     }
     if(["intent:send-credits","intent:retry-transfer"].includes(intent))void refresh();
   });
   const timer=setInterval(async()=>{if(document.hidden||refreshing)return;refreshing=true;try{await refresh();}finally{refreshing=false;}},60000);
   window.addEventListener("pagehide",()=>clearInterval(timer),{once:true});
 }catch(error){status.textContent="MCEL Wallet mount refused: "+String(error?.message||error);}
})();</script></body></html>'''
    return attach_inert_presentation(page,repo,'wallet','mcel-workbench',promoted=True)
