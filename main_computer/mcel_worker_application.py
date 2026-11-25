"""Native MCEL Worker with existing marketplace wallet compatibility.

The canonical Worker UI now selects this semantic view by default. Marketplace
signing, wallet/MSK management, and runtime APIs retain the established backend.
The original Worker SPA remains available through the explicit local rollback.
"""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_applications_registry import attach_inert_presentation
from main_computer.mcel_knowledge.semantic_composition import (
    P3_POLICY, synthesize_composition, verify_composition,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("mcel_apps/worker/application.js")
STYLE = Path("main_computer/web/applications/styles/mcel-semantic-workbench.css")
ASSETS = (
    "main_computer/web/applications/scripts/mcel-diagnostic-early-bootstrap.js",
    "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js",
    "main_computer/web/applications/scripts/mcel-semantic-value-updaters.js",
    "main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js",
    "main_computer/web/applications/scripts/mcel-semantic-worker-ports.js",
    "main_computer/web/applications/scripts/dom-bindings/worker.js",
    "main_computer/web/applications/scripts/worker.js",
    "main_computer/web/applications/scripts/dom-bindings/wallet.js",
    "main_computer/web/applications/scripts/wallet.js",
)


class WorkerSemanticApplicationError(ValueError):
    pass


def compile_worker_application(repo: Path = ROOT) -> tuple[dict, dict]:
    if (repo / SOURCE.with_name("application.html")).exists():
        raise WorkerSemanticApplicationError("Native Worker must not supply an authored HTML layout")
    report = compile_dsl_application(repo / SOURCE)
    if not report.valid or report.normalized_ir is None:
        raise WorkerSemanticApplicationError("Invalid Worker semantic source: " + str(report.diagnostics))
    ir = report.normalized_ir
    surface, = ir.get("surfaces") or [None]
    if (ir.get("application", {}).get("appId") != "worker" or ir.get("layouts") or
            not surface or surface.get("purpose") != "manage" or
            surface.get("interfaceIntent", {}).get("hierarchy") or surface.get("htmlBindings")):
        raise WorkerSemanticApplicationError("Worker must use native generated MCEL presentation")
    capabilities = {cap["id"]: cap for cap in ir.get("capabilities", [])}
    if set(capabilities) != {"capability:worker-observer", "capability:worker-hub-choice-control",
                             "capability:worker-destination-control", "capability:worker-policy-control",
                             "capability:worker-slh-target-control", "capability:worker-marketplace-observer",
                             "capability:worker-marketplace-settings", "capability:worker-marketplace-actions", "capability:worker-enrollment"}:
        raise WorkerSemanticApplicationError("Worker migration may not invent external authorities")
    risks = {"capability:worker-observer": "external-read",
             "capability:worker-hub-choice-control": "consequential",
             "capability:worker-destination-control": "consequential",
             "capability:worker-policy-control": "consequential",
             "capability:worker-slh-target-control": "consequential",
             "capability:worker-marketplace-observer": "external-read",
             "capability:worker-marketplace-settings": "consequential",
             "capability:worker-marketplace-actions": "consequential",
             "capability:worker-enrollment": "consequential"}
    if any(capabilities[key].get("risk") != risk for key, risk in risks.items()):
        raise WorkerSemanticApplicationError("Worker capability risk mismatch")
    try:
        composition = synthesize_composition(ir, policy=P3_POLICY)
        verify_composition(ir, composition)
    except ValueError as exc:
        raise WorkerSemanticApplicationError("Worker semantic composition rejected: " + str(exc)) from exc
    return ir, composition


def _script(repo: Path, relative: str) -> str:
    source = (repo / relative).read_text(encoding="utf-8")
    if "</script" in source.lower():
        raise WorkerSemanticApplicationError("Unsafe Worker script asset")
    return "<script>\n" + source + "\n</script>"


def render_worker_page(repo: Path = ROOT, *, canonical: bool = False) -> str:
    ir, composition = compile_worker_application(repo)
    payload = json.dumps({"ir": ir, "composition": composition},
                         separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    css = (repo / STYLE).read_text(encoding="utf-8")
    if "</style" in css.lower():
        raise WorkerSemanticApplicationError("Unsafe Worker stylesheet")
    # Reuse the existing Wallet, MSK, recovery, faucet and bridge controls
    # verbatim. Their click handlers are the accepted legacy implementation;
    # the full legacy Worker initialization never runs in this route.
    wallet_html = (repo / "main_computer/web/applications/apps/worker-wallet-compat.html").read_text(encoding="utf-8")
    if "</template" in wallet_html.lower() or "<script" in wallet_html.lower():
        raise WorkerSemanticApplicationError("Wallet compatibility template must be inert")
    wallet_css = (repo / "main_computer/web/applications/styles/worker.css").read_text(encoding="utf-8")
    if "</style" in wallet_css.lower():
        raise WorkerSemanticApplicationError("Unsafe Wallet compatibility stylesheet")
    scripts = "".join(_script(repo, path) for path in ASSETS)
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Worker · MCEL candidate</title><style>{css}
{wallet_css}
.mcel-worker-wallet-compat {{ display:none; color:var(--mcel-theme-ink,#e4edf2); --ink:var(--mcel-theme-ink,#e4edf2); --gold:var(--mcel-theme-accent,#f6c75b); --muted:var(--mcel-theme-muted,#b6c6d0); --sky:var(--mcel-theme-accent-2,#8bcff2); width:100%; min-width:0; }}
.mcel-worker-wallet-compat.is-visible {{display:block}}
.mcel-worker-wallet-compat .worker-wallet-inner {{display:grid;gap:12px;min-width:0;}}
.mcel-worker-wallet-compat .worker-wallet-card-grid {{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,270px),1fr));gap:10px}}
.mcel-worker-wallet-compat .worker-wallet-compat-header {{padding:8px 0;}}
.mcel-worker-wallet-compat .worker-wallet-compat-header p {{margin:4px 0;font-size:12px;}}
.mcel-worker-wallet-compat .worker-wallet-session {{color:var(--mcel-fg,#e4edf2)}}
.mcel-worker-wallet-compat .worker-card {{min-width:0;max-width:100%;background:var(--mcel-theme-card,#090b08);border-color:var(--mcel-theme-line,#355062);color:var(--mcel-theme-ink,#e4edf2);border-radius:var(--mcel-form-panel-radius,10px);}}
.mcel-worker-wallet-compat .worker-card :is(input,select,button) {{border-radius:var(--mcel-form-control-radius,8px);}}
.mcel-worker-wallet-compat .worker-card h4 {{color:var(--mcel-theme-accent,#f6c75b);}}
.mcel-worker-wallet-compat input,.mcel-worker-wallet-compat button {{max-width:100%;}}
.mcel-worker-wallet-compat #worker-save-status {{display:block;white-space:normal;overflow-wrap:anywhere;padding:9px;}}
.mcel-worker-hub-selection-status {{display:block;font-size:12px;padding:5px 0;overflow-wrap:anywhere;}}
.mcel-worker-hub-selection-status[data-error="true"] {{color:var(--mcel-error,#f0a5a5);}}
</style></head>
<body class="mcel-workbench-page" data-mcel-worker-phase="candidate">
<div id="mcel-workbench" aria-label="MCEL generated Worker workspace"></div>
<section id="mcel-wallet-staging" style="display:none" aria-label="Legacy wallet controls">{wallet_html}</section>
<p id="mcel-workbench-status" role="status">Compiling Worker semantics</p>
<script type="application/json" id="mcel-generated-data">{payload}</script>
{scripts}
<script>(function(){{"use strict";
const status=document.getElementById("mcel-workbench-status");
try{{
  const data=JSON.parse(document.getElementById("mcel-generated-data").textContent);
  const port=McelSemanticWorkerPorts.create({{appId:data.ir.application.appId}});
  const app=McelSemanticOperationalRuntime.mount(document.getElementById("mcel-workbench"),
    data.ir,{{capabilities:port,composition:data.composition,presentation:"expert"}});
  window.__mcelWorker=Object.freeze({{app,appId:data.ir.application.appId,
    composition:data.composition}});
  // Mount original wallet controls into the semantic Worker slot. This is a
  // narrow compatibility island, not a second Worker runtime or Hub selector.
  const walletSlot=document.querySelector('[data-mcel-node="surface-node:worker.wallet-compat-anchor"]');
  if (!walletSlot) throw Error("MCEL_WORKER:wallet-slot-missing");
  const walletPanel=document.getElementById("mcel-wallet-staging");
  walletPanel.removeAttribute("style");
  walletPanel.className="mcel-worker-wallet-compat";
  walletPanel.setAttribute("aria-label","Legacy wallet and multi-session key controls");
  // Hub Choice is the owner of wallet/MSK setup for marketplace hubs.
  // Do not mount the island in the Worker ring task or a read-only inspector.
  const hubTask=document.querySelector('[data-mcel-node="surface-node:worker.hub-radio-group"]')
    ?.closest('[data-mcel-region]');
  if(!hubTask)throw Error("MCEL_WORKER:hub-task-missing");
  walletSlot.hidden=true;
  const hubChoiceStatus=document.createElement("output");
  hubChoiceStatus.className="mcel-worker-hub-selection-status";
  hubChoiceStatus.setAttribute("role","status");
  hubChoiceStatus.setAttribute("aria-live","polite");
  hubTask.append(hubChoiceStatus,walletPanel);
  const reportChoice=(message,failed=false)=>{{
    hubChoiceStatus.textContent=message;
    hubChoiceStatus.dataset.error=String(failed);
    status.textContent=message;
  }};
  if(typeof initWorkerWalletOnly!=="function")throw Error("MCEL_WORKER:wallet-controller-missing");
  initWorkerWalletOnly();
  const showWallet=()=>{{
    const choice=app.readState("state:selected-network");
    const active=app.readState("state:worker-view")==="hub-choice" &&
      ["mainnet","testnet","test","dev"].includes(choice);
    walletPanel.classList.toggle("is-visible",active);
    updateWorkerWalletSessionContext();
    if(active)void refreshWorkerWalletOnly().catch(()=>{{}});
  }};
  showWallet();
  // A Hub radio choice is both the local view selection and its explicit
  // canonical operation. Initial observation replays without a mutation.
  let syncingObserved=false;
  let committing=false;
  let desiredChoice=null;
  let localChoiceRevision=0;
  const chooseObserved=async(network,expectedRevision=null)=>{{
    if(expectedRevision!==null && localChoiceRevision!==expectedRevision)return;
    const available=port.lastObservation()?.destinations||[];
    if(!available.some(row=>row.network===network))return;
    if(expectedRevision!==null && (committing||desiredChoice!==null))return;
    syncingObserved=true;
    try{{ await app.execute("intent:choose-network",{{key:network}}); showWallet(); }}
    finally{{ syncingObserved=false; }}
  }};
  const identityPreferenceKey="mcel.worker.selected-identity.v1";
  // Identity inventory observation is independent of Hub observation and
  // must recover from startup races. Never silently turn a failed read into
  // an empty/verified identity directory.
  const refreshIdentities=async()=>{{
    const statusRead=await app.execute("intent:refresh-identity-status");
    if(statusRead?.disposition!=="completed")
      throw Error("identity-status-"+String(statusRead?.reason||statusRead?.disposition||"unverified"));
    const inventory=await app.execute("intent:refresh-identities");
    if(inventory?.disposition!=="completed")
      throw Error("identity-inventory-"+String(inventory?.reason||inventory?.disposition||"unverified"));
    const observed=port.lastIdentityObservation();
    const records=app.readState("state:local-identities");
    const state=app.readState("state:local-identity-status");
    if(observed?.state!=="AVAILABLE" || state?.state!=="AVAILABLE" ||
       !Array.isArray(records) || records.length!==observed.identities.length)
      throw Error("identity-state-not-published");
    const selected=app.readState("state:selected-local-identity")||"";
    const valid=observed.identities.some(row=>row.deviceId===selected);
    if(selected && !valid){{
      await app.execute("intent:choose-identity",{{key:""}});
    }}
    if(!selected){{
      let remembered="";
      try{{remembered=window.sessionStorage?.getItem(identityPreferenceKey)||"";}}catch(_){{}}
      if(remembered && observed.identities.some(row=>row.deviceId===remembered))
        await app.execute("intent:choose-identity",{{key:remembered}});
    }}
    return true;
  }};
  const identityRetryDelays=[250,750,1500,3000,6000,12000];
  let identityRetryCount=0;
  let identityRetryTimer=null;
  let identityReadInFlight=false;
  let identityReady=false;
  const identityDiagnostic=(reason)=>{{
    const category=String(reason?.message||reason||"unknown")
      .replace(/[^A-Za-z0-9:_-]/g,"_").slice(0,160);
    console.warn("MCEL_WORKER_IDENTITY_REFRESH",{{attempt:identityRetryCount+1,category}});
    status.dataset.error="true";
    status.textContent="Worker identity inventory unavailable ("+category+"); retrying. No authentication assumed.";
  }};
  const attemptIdentityRefresh=async()=>{{
    if(identityReadInFlight)return;
    identityReadInFlight=true;
    if(identityRetryTimer!==null){{clearTimeout(identityRetryTimer);identityRetryTimer=null;}}
    try{{
      await refreshIdentities();
      identityReady=true;
      identityRetryCount=0;
      status.dataset.error="false";
      status.textContent="Worker identities observed. SLH/SEH access is not authenticated.";
    }}catch(error){{
      identityReady=false;
      identityDiagnostic(error);
      if(identityRetryCount<identityRetryDelays.length){{
        const delay=identityRetryDelays[identityRetryCount++];
        identityRetryTimer=setTimeout(()=>{{identityRetryTimer=null;void attemptIdentityRefresh();}},delay);
      }}else{{
        status.textContent="Worker identity refresh exhausted retries. Reopen the Worker or return to this tab to retry.";
      }}
    }}finally{{identityReadInFlight=false;}}
  }};
  const resumeIdentityRefresh=()=>{{
    if(!identityReady && !identityReadInFlight && identityRetryTimer===null){{
      identityRetryCount=0;
      void attemptIdentityRefresh();
    }}
  }};
  window.addEventListener("pageshow",resumeIdentityRefresh);
  document.addEventListener("visibilitychange",()=>{{
    if(document.visibilityState==="visible")resumeIdentityRefresh();
  }});
  const refresh=async({{resetSelection=false}}={{}})=>{{
    let complete=true;
    const revisionAtStart=localChoiceRevision;
    const view=app.readState("state:worker-view");
    if(resetSelection && view==="hub-choice"){{
      const choices=await app.execute("intent:refresh-destinations");
      const destination=await app.execute("intent:refresh-destination");
      complete=choices?.disposition==="completed" && destination?.disposition==="completed";
      const observed=port.lastObservation();
      const network=observed?.hubChoice;
      if(complete && revisionAtStart===localChoiceRevision &&
          observed?.destinations?.some(row=>row.network===network))
        await chooseObserved(network,revisionAtStart);
    }}
    if(view==="worker"){{
      const runtime=await app.execute("intent:refresh-runtime");
      const policy=await app.execute("intent:refresh-policy");
      const seller=await app.execute("intent:refresh-seller");
      const marketplace=await app.execute("intent:refresh-marketplace-status");
      complete=complete && runtime?.disposition==="completed" && policy?.disposition==="completed" &&
        seller?.disposition==="completed" && marketplace?.disposition==="completed";
    }} else if(view==="requester"){{
      const requester=await app.execute("intent:refresh-requester");
      complete=complete && requester?.disposition==="completed";
    }} else if(view==="hub-choice" && app.readState("state:selected-network")==="slh"){{
      const slh=await app.execute("intent:refresh-slh-target");
      complete=complete && slh?.disposition==="completed";
    }}
    // A Hub observation must not erase an identity-startup failure diagnostic.
    if(status.dataset.error!=="true") status.textContent=complete
      ? "Worker context observed. Hub selection is not authentication or enrollment."
      : "Worker observation incomplete; no authorization assumed.";
  }};
  let refreshing=false;
  const refreshFromSelection=()=>{{
    if(refreshing || committing)return;
    refreshing=true;
    void refresh().catch(()=>{{status.textContent="Worker observation unavailable; no Worker mutation attempted.";}})
      .finally(()=>{{refreshing=false;}});
  }};
  const commitChoice=async()=>{{
    if(committing)return;
    committing=true;
    let failed=false;
    try{{
      while(desiredChoice!==null){{
        const choice=desiredChoice;
        desiredChoice=null;
        const saved=port.lastObservation();
        if(saved?.hubChoice===choice)continue;
        reportChoice("Saving Hub choice: "+choice+"…");
        const receipt=await app.execute("intent:commit-hub-choice");
        if(receipt?.disposition!=="completed"){{
          failed=true;
          if(desiredChoice===null){{
            await chooseObserved(port.lastObservation()?.hubChoice||"none");
            reportChoice("Hub change refused; previous choice retained: "+
              String(receipt?.reason||receipt?.disposition||"unverified"),true);
          }}
          continue;
        }}
        // The capability port verifies the canonical write's postcondition.
        // A stale result cannot overwrite a newer queued radio selection.
        failed=false;
        if(desiredChoice===null){{
          reportChoice("Hub choice saved. Connection and identity must be verified separately.");
        }}
      }}
    }}catch(error){{
      failed=true;
      reportChoice("Hub choice failed: "+String(error?.message||error),true);
      if(desiredChoice===null){{
        try{{await chooseObserved(port.lastObservation()?.hubChoice||"none");}}catch(_){{}}
      }}
    }}finally{{
      committing=false;
      if(desiredChoice!==null)void commitChoice();
      else if(!failed && app.readState("state:selected-network")==="slh")refreshFromSelection();
    }}
  }};
  document.getElementById("mcel-workbench").addEventListener("mcel:collection-selection",event=>{{
    if(event.detail?.intentId==="intent:choose-identity"){{
      const deviceId=app.readState("state:selected-local-identity")||"";
      const known=port.lastIdentityObservation();
      if(deviceId && (known?.state!=="AVAILABLE" ||
          !known.identities.some(row=>row.deviceId===deviceId))){{
        void app.execute("intent:choose-identity",{{key:""}});
        status.textContent="Identity selection refused: local identity not verified.";
        return;
      }}
      try{{
        if(deviceId)window.sessionStorage?.setItem(identityPreferenceKey,deviceId);
        else window.sessionStorage?.removeItem(identityPreferenceKey);
      }}catch(_){{}}
      status.textContent=deviceId
        ? "Identity selected. SLH/SEH access still requires a separate enrollment and authentication."
        : "No Worker identity selected; access remains unverified.";
      return;
    }}
    if(event.detail?.intentId!=="intent:choose-network")return;
    showWallet(); // project the selected Hub immediately, before persistence settles
    if(syncingObserved)return;
    ++localChoiceRevision;
    const choice=event.detail?.value;
    if(!port.lastObservation()?.destinations?.some(row=>row.network===choice))return;
    desiredChoice=choice;
    void commitChoice();
  }});
  document.getElementById("mcel-workbench").addEventListener("mcel:parity-operation",event=>{{
    if(event.detail?.phase!=="settled" || !( ["completed","selected"].includes(event.detail?.disposition)) || refreshing)return;
    const intent=event.detail?.intentId;
    if(intent==="intent:switch-view") showWallet();
    if(intent==="intent:select-destination") void refreshWorkerWalletOnly().catch(()=>{{}});
    if(["intent:switch-view","intent:save-policy","intent:save-slh-target",
        "intent:save-seller","intent:save-requester","intent:register-offer",
        "intent:work-now","intent:finish-work-now","intent:pause-selling",
        "intent:retry-hub","intent:disconnect"].includes(intent))
      refreshFromSelection();
    // The ring form binds its verified capability result directly to destination.
  }});
  // Live Work Now countdown and supervisor evidence are observed through the
  // existing read-only MCEL capability. Refresh a running countdown every five
  // seconds, otherwise sample the Worker once per thirty seconds. A hidden
  // frame never polls. This neither starts work nor changes marketplace state.
  let monitorTick=0;
  let marketplaceReadBusy=false;
  const monitorMarketplace=()=>{{
    if(document.visibilityState==="hidden"||
       app.readState("state:worker-view")!=="worker"||marketplaceReadBusy)return;
    const current=app.readState("state:marketplace-status");
    if(current?.workNowState!=="ACTIVE" && ++monitorTick%6!==0)return;
    marketplaceReadBusy=true;
    void app.execute("intent:refresh-marketplace-status")
      .catch(()=>{{}}).finally(()=>{{marketplaceReadBusy=false;}});
  }};
  const marketplaceMonitor=setInterval(monitorMarketplace,5000);
  window.addEventListener("pagehide",()=>clearInterval(marketplaceMonitor),{{once:true}});
  // Run after the semantic mount has committed its DOM and state bindings.
  // Do not chain the Hub observation behind identity initialization: either
  // reader can fail independently and both retain their own recovery path.
  const launchInitialReads=()=>{{
    void attemptIdentityRefresh();
    void refresh({{resetSelection:true}}).catch(()=>{{
      status.textContent="Worker observation unavailable; no Worker mutation attempted.";
    }});
  }};
  if(typeof window.requestAnimationFrame==="function")
    window.requestAnimationFrame(()=>window.requestAnimationFrame(launchInitialReads));
  else setTimeout(launchInitialReads,0);
}}catch(error){{
  status.textContent="Worker MCEL mount refused: "+String(error?.message||error);
  document.body.dataset.mcelWorkerPhase="refused";
}}
}})();</script>
</body></html>'''
    if canonical:
        page = page.replace('<title>Worker · MCEL candidate</title>',
                            '<title>Worker · MCEL</title>', 1)
        page = page.replace('data-mcel-worker-phase="candidate"',
                            'data-mcel-worker-phase="promoted"', 1)
    return attach_inert_presentation(page, repo, 'worker', 'mcel-workbench',
                                     promoted=True, semantic_candidate=not canonical)
