"""Native MCEL Worker migration candidate with legacy wallet compatibility.

The canonical /applications/worker remains authoritative until the full
marketplace registration and Worker execution contracts have
been ported and accepted. The wallet/MSK compatibility bridge reuses existing
handlers and is not a native semantic wallet implementation. No user-facing claim of full cutover is made here.
"""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
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
                             "capability:worker-slh-target-control"}:
        raise WorkerSemanticApplicationError("Worker migration may not invent external authorities")
    risks = {"capability:worker-observer": "external-read",
             "capability:worker-hub-choice-control": "consequential",
             "capability:worker-destination-control": "consequential",
             "capability:worker-policy-control": "consequential",
             "capability:worker-slh-target-control": "consequential"}
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


def render_worker_page(repo: Path = ROOT) -> str:
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
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Worker · MCEL candidate</title><style>{css}
{wallet_css}
.mcel-worker-wallet-compat {{ display:none; color:var(--mcel-fg, #e4edf2); --ink:var(--mcel-fg,#e4edf2); --gold:#f6c75b; --muted:#b6c6d0; --sky:#8bcff2; width:100%; min-width:0; }}
.mcel-worker-wallet-compat.is-visible {{display:block}}
.mcel-worker-wallet-compat .worker-wallet-inner {{display:grid;gap:12px;min-width:0;}}
.mcel-worker-wallet-compat .worker-wallet-card-grid {{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,270px),1fr));gap:10px}}
.mcel-worker-wallet-compat .worker-wallet-compat-header {{padding:8px 0;}}
.mcel-worker-wallet-compat .worker-wallet-compat-header p {{margin:4px 0;font-size:12px;}}
.mcel-worker-wallet-compat .worker-wallet-session {{color:var(--mcel-fg,#e4edf2)}}
.mcel-worker-wallet-compat .worker-card {{min-width:0;max-width:100%;}}
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
      complete=complete && runtime?.disposition==="completed" && policy?.disposition==="completed";
    }} else if(view==="hub-choice" && app.readState("state:selected-network")==="slh"){{
      const slh=await app.execute("intent:refresh-slh-target");
      complete=complete && slh?.disposition==="completed";
    }}
    status.textContent=complete
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
    if(["intent:switch-view","intent:save-policy","intent:save-slh-target"].includes(intent))
      refreshFromSelection();
    // The ring form binds its verified capability result directly to destination.
  }});
  void refresh({{resetSelection:true}}).catch(()=>{{
    status.textContent="Worker observation unavailable; no Worker mutation attempted.";
  }});
}}catch(error){{
  status.textContent="Worker MCEL mount refused: "+String(error?.message||error);
  document.body.dataset.mcelWorkerPhase="refused";
}}
}})();</script>
</body></html>'''
