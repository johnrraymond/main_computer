"""MCEL semantic SLH: explicit local configure/start/disable and persistent identity.

Page mount observes only. Mutations require a local, same-origin explicit
consequential action, the selected LMS signer, and the existing SLH authority.
Remote TLS observation remains isolated from local lifecycle authority.
"""
from __future__ import annotations

import json
import os
import ipaddress
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from main_computer.mcel_applications_registry import attach_inert_presentation
from main_computer.mcel_signal_scheduler_rollout import start_certified
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_slh_target_navigation import TAB_STYLE, target_tabs
from main_computer.mcel_slh_worker_bridge import (
    OrganizationWorkerClient, WorkerBridgeError, project_inventory,
)
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, verify_composition
from main_computer.slh.ports import DEFAULT_SLH_API_PORT

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("mcel_apps/slh/application.js")
ASSETS = (
    "main_computer/web/applications/scripts/mcel-diagnostic-early-bootstrap.js",
    "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js",
    "main_computer/web/applications/scripts/mcel-semantic-value-updaters.js",
    "main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js",
    "main_computer/web/applications/scripts/mcel-semantic-seh-deployment-ports.js",
    "main_computer/web/applications/scripts/mcel-semantic-slh-ports.js",
    "main_computer/web/applications/scripts/mcel-semantic-slh-network-ports.js",
    "main_computer/web/applications/scripts/mcel-semantic-modal-subprogram.js",
    "main_computer/web/applications/scripts/mcel-semantic-slh-management-modal.js",
    "main_computer/web/applications/scripts/mcel-semantic-seh-remote-ports.js",
    "main_computer/web/applications/scripts/mcel-diagnostic-plugins.js",
    "main_computer/web/applications/scripts/mcel-reactivity-shadow-plugin.js",
    "main_computer/web/applications/scripts/mcel-self-diagnosis.js",
    "main_computer/web/applications/scripts/mcel-diagnostic-evidence.js",
    "main_computer/web/applications/scripts/mcel-diagnostics-counter-widget.js",
)
STYLE = "main_computer/web/applications/styles/mcel-semantic-workbench.css"
MODAL_STYLE = "main_computer/web/applications/styles/mcel-semantic-modal-subprogram.css"
ROUTES = {
    "/applications/slh": "overview", "/applications/slh/local": "overview",
    "/applications/slh/workers": "worker-bank",
    "/applications/slh/local/workers": "worker-bank",
    "/applications/slh/network": "network",
    "/applications/slh/network/workers": "network-worker-bank",
    "/applications/slh/remote-seh": "remote-seh"
}
SCHEMA = "mcel.slh.first-slice-observation.v1"
IDENTITIES_SCHEMA = "mcel.slh.identities.v4b"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
OBSERVATION_VIEWS = frozenset(("overview", "workers"))


class SlhApplicationError(ValueError):
    pass


def compile_slh_application(repo: Path = ROOT) -> tuple[dict, dict]:
    source = repo / SOURCE
    if source.with_name("application.html").exists():
        raise SlhApplicationError("SLH semantic source may not have a paired HTML layout")
    compiled = compile_dsl_application(source)
    if not compiled.valid or not compiled.normalized_ir:
        raise SlhApplicationError("SLH semantic source does not compile: " + str(compiled.diagnostics))
    ir = compiled.normalized_ir
    surface, = ir.get("surfaces") or [None]
    if (ir.get("application", {}).get("appId") != "slh" or ir.get("layouts") or
            surface is None or surface.get("purpose") != "manage" or
            surface.get("interfaceIntent", {}).get("hierarchy") or surface.get("htmlBindings")):
        raise SlhApplicationError("SLH source cannot prescribe presentation or host authority")
    # An authored semantic navigation membership is permitted. It prescribes
    # neither HTML hierarchy nor CSS geometry nor any administrator authority.
    authored = surface.get("interfaceIntent") or {}
    navigation = [x for x in authored.get("regions", []) if x.get("role") == "navigation"]
    workspaces = [x for x in authored.get("regions", []) if x.get("role") == "task"]
    commands = [x for x in authored.get("regions", []) if x.get("role") == "commands"]
    inspectors = [x for x in authored.get("regions", []) if x.get("role") == "inspector"]
    activity = [x for x in authored.get("regions", []) if x.get("role") == "status"]
    membership = lambda region: {member["ref"] for member in region["members"]}
    if (len(navigation) != 1 or navigation[0]["id"] != "interface-region:slh.identities" or
        membership(navigation[0]) != {"surface-node:slh.available-local-identities"} or
        navigation[0].get("prefer") != {"side":"start"} or
        len(workspaces) != 3 or {frozenset(membership(r)) for r in workspaces} != {
          frozenset({"surface-node:slh.configure-org-slh","surface-node:slh.organization-enablement",
                     "surface-node:slh.start-stop-org-slh","surface-node:slh.first-administrator-identity"}),
          frozenset({"surface-node:slh.connect-network-slh","surface-node:slh.network-selected-identity"}),
          frozenset({"surface-node:slh.connect-remote-seh","surface-node:slh.remote-seh-selected-identity"})} or
        len(commands) != 2 or {frozenset(membership(r)) for r in commands} != {
          frozenset({"surface-node:slh.disconnect-network-slh"}),
          frozenset({"surface-node:slh.disconnect-remote-seh"})} or
        len([x for x in authored.get("regions",[]) if x.get("id")=="interface-region:slh.organization-identities"]) != 1 or
        len([x for x in authored.get("regions",[]) if x.get("id")=="interface-region:slh.seh-lifecycle" and
            membership(x)=={"surface-node:slh.local-seh-lifecycle","surface-node:slh.network-seh-lifecycle"}]) != 1 or
        len(inspectors) != 1 or membership(inspectors[0]) != {
          "surface-node:slh.observed-service","surface-node:slh.last-successful-operation",
          "surface-node:slh.network-connection-details","surface-node:slh.remote-seh-connection-details",
          "surface-node:slh.seh-connection-details","surface-node:slh.network-seh-connection-details"} or
        len(activity) != 1 or membership(activity[0]) != {"surface-node:slh.activity"}):
        raise SlhApplicationError("SLH semantic target and inspector ownership invalid")
    nodes = {x["id"]: x for x in surface["nodes"]}
    identity_collection = nodes.get("surface-node:slh.available-local-identities", {})
    if (identity_collection.get("credentialDisclosure") != {
            "intent":{"ref":"intent:software-authenticator-reveal"},
            "identityField":"deviceId"} or
        "fingerprint" in identity_collection.get("fields",[]) or
        "deviceId" in identity_collection.get("fields",[])):
        raise SlhApplicationError("SLH identity credential disclosure authority invalid")
    local_form = nodes.get("surface-node:slh.configure-org-slh", {})
    remote_form = nodes.get("surface-node:slh.connect-network-slh", {})
    seh_form = nodes.get("surface-node:slh.connect-remote-seh", {})
    seh_notebooks = [nodes.get("surface-node:slh."+target+"-seh-lifecycle", {})
                     for target in ("local","network")]
    def declared_updates(tab: dict) -> bool:
        contract = tab.get("updates") or {}
        return (contract.get("fields") == tab.get("fields") and
                set(contract.get("actions", [])) == set(tab.get("actions", {})) and
                contract.get("status") is True and
                contract.get("forms") is bool(tab.get("forms")))
    if (nodes.get("surface-node:slh.organization-identity-notebook", {}).get("updates") !=
            {"rows": True, "status": True, "retention": True}):
        raise SlhApplicationError("SLH identity notebook semantic update contract missing")
    if (seh_form.get("nodeKind") != "form" or
        seh_form.get("intent",{}).get("ref") != "intent:connect-remote-seh" or
        seh_form.get("fields") != {"sehUrl":{"kind":"string"}} or
        any(n.get("nodeKind") != "operation-notebook" or n.get("title") != "SEH Lifecycle" or
            n.get("visibleWhen",{}).get("equals") != target or
            n.get("authentication") is not None or
            n.get("tabs",[])[0].get("forms",[])[1].get("choicesFrom",{}).get("name",{}).get("selectedPath") != "selectedController" or
            [tab.get("id") for tab in n.get("tabs",[])] !=
                ["configuration","deployment","connection","status","diagnostics"] or
            any(not declared_updates(tab) for tab in n.get("tabs",[])) or
            set(n.get("tabs",[])[2].get("actions",{})) != {"Start","Stop","Restart"} or
            set(n.get("tabs",[])[1].get("actions",{})) != {
                "Deploy / Redeploy","Undeploy"} or
            n.get("tabs",[])[1]["actions"].get("Deploy / Redeploy",{}).get("alwaysEnabled") is not True or
            len(n.get("tabs",[])[0].get("forms",[])) != 2 or
            n.get("tabs",[])[0].get("actions")
            for target,n in zip(("local","network"),seh_notebooks))):
        raise SlhApplicationError("SEH lifecycle or remote relay contract invalid")
    if (local_form.get("nodeKind") != "form" or
        local_form.get("intent",{}).get("ref") != "intent:configure-and-enable-organization" or
        local_form.get("fields") != {"bindAddress":{"kind":"string"},"port":{"kind":"integer"}} or
        local_form.get("resetControl") is not False or
        nodes.get("surface-node:slh.organization-enablement",{}).get("intent",{}).get("ref") !=
            "intent:toggle-organization-enablement" or
        nodes.get("surface-node:slh.organization-enablement",{}).get("labelByState",{}).get("labels") !=
            {"running":"Disable","stopped":"Disable","disabled":"Enable"} or
        nodes.get("surface-node:slh.start-stop-org-slh",{}).get("labelByState",{}).get("labels") !=
            {"running":"Stop","stopped":"Start"} or
        remote_form.get("nodeKind") != "form" or
        remote_form.get("intent",{}).get("ref") != "intent:connect-network-slh" or
        remote_form.get("fields") != {"address":{"kind":"string"},"port":{"kind":"integer"}} or
        remote_form.get("resetControl") is not False or
        "tlsSha256" in remote_form.get("fields",{}) or
        "surface-node:slh.worker-bank" in nodes):
        raise SlhApplicationError("SLH target workspace authority invalid")
    expected_intents = {
        "intent:refresh-identities", "intent:refresh-identity-status", "intent:refresh-lifecycle",
        "intent:choose-identity", "intent:select-target", "intent:connect-network-slh",
        "intent:disconnect-network-slh", "intent:configure-and-enable-organization", "intent:toggle-organization-enablement",
        "intent:toggle-organization",
        "intent:connect-remote-seh", "intent:disconnect-remote-seh", "intent:read-seh-connection",
        "intent:read-seh-deployment", *{f"intent:seh-deployment-{operation}" for operation in
          ("register","select","deploy","undeploy")},
        *{f"intent:seh-connection-{action}" for action in ("start","stop","restart")},
        *{f"intent:read-organization-{name}" for name in ("pending","enrolled","trash","retention")},
        *{f"intent:organization-{name}" for name in ("accept","reject","delete","reactivate","remove")},
        "intent:set-organization-trash-retention", "intent:empty-organization-trash",
        "intent:software-authenticator-reveal"
    }
    expected_capabilities = {
        "capability:slh-observer":"external-read", "capability:network-slh-connection":"consequential",
        "capability:slh-software-authenticator-control":"consequential",
        "capability:slh-first-run":"consequential",
        "capability:organization-identity-administration":"consequential",
        "capability:organization-identity-observer":"external-read",
        "capability:remote-seh-connection":"consequential",
        "capability:seh-connection-observer":"external-read",
        "capability:seh-connection-control":"consequential",
        "capability:seh-deployment-observer":"external-read",
        "capability:seh-deployment-control":"consequential"
    }
    expected_effects = {
        "effect:refresh-identities.request":"external-read",
        "effect:refresh-identity-status.request":"external-read",
        "effect:refresh-lifecycle.request":"external-read",
        "effect:software-authenticator-reveal.request":"consequential",
        "effect:connect-network-slh.request":"consequential",
        "effect:disconnect-network-slh.request":"consequential",
        "effect:configure-and-enable-organization.request":"consequential",
        "effect:toggle-organization-enablement.request":"consequential",
        "effect:toggle-organization.request":"consequential",
        "effect:connect-remote-seh.request":"consequential",
        "effect:disconnect-remote-seh.request":"consequential",
        "effect:read-seh-connection.request":"external-read",
        "effect:read-seh-deployment.request":"external-read",
        **{f"effect:seh-deployment-{operation}.request":"consequential" for operation in
          ("register","select","deploy","undeploy")},
        **{f"effect:seh-connection-{action}.request":"consequential" for action in ("start","stop","restart")},
        **{f"effect:read-organization-{name}.request":"external-read" for name in ("pending","enrolled","trash","retention")},
        **{f"effect:organization-{name}.request":"consequential" for name in ("accept","reject","delete","reactivate","remove")},
        "effect:set-organization-trash-retention.request":"consequential",
        "effect:empty-organization-trash.request":"consequential"
    }
    if ({x["id"] for x in ir.get("intents",[])} != expected_intents or
        {x["id"]:x.get("risk") for x in ir.get("capabilities",[])} != expected_capabilities or
        {x["id"]:x.get("risk") for x in ir.get("effects",[])} != expected_effects):
        raise SlhApplicationError("SLH semantic authority boundary refused")
    # SLH owns its retention and destructive-action text. Generic MCEL
    # notebooks may omit either feature entirely.
    identity_notebook = nodes["surface-node:slh.organization-identity-notebook"]
    identity_notebook["recordLabel"] = "organizational identities"
    identity_notebook["messages"] = {
        "unavailable": "Select an identity and establish the target connection to access protected records",
        "errorPrefix": "Organization records unavailable: ",
    }
    identity_notebook["retention"].update({
        "label": " Remove items after 30 days",
        "failureLabel": "Retention change refused: ",
        "loadFailureLabel": "Organization retention policy unavailable: ",
    })
    identity_notebook["empty"].update({
        "label": "Empty Trash",
        "confirm": "Permanently remove ALL trashed identities? This cannot be undone.",
        "failureLabel": "Empty Trash refused: ",
    })
    composition = synthesize_composition(ir, policy="mcel.semantic-structure.p3")
    verify_composition(ir, composition)
    return ir, composition


def _script(repo: Path, relative: str) -> str:
    content = (repo / relative).read_text(encoding="utf-8")
    if "</script" in content.lower():
        raise SlhApplicationError("Script contains an HTML closing tag")
    return "<script>\n" + content + "\n</script>"


def render_slh_page(view: str = "overview", repo: Path = ROOT) -> str:
    # Both canonical targets mount exactly the same semantic SLH application.
    # The target selects pre-auth context, never a new signing identity.
    if view not in ROUTES.values():
        raise SlhApplicationError("Unknown semantic SLH destination")
    ir, composition = compile_slh_application(repo)
    payload = json.dumps({"ir": ir, "composition": composition},
                         separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    css = (repo / STYLE).read_text(encoding="utf-8") + "\n" + (repo / MODAL_STYLE).read_text(encoding="utf-8")
    # The same semantic diagnostic colors and geometry as the Calculator.
    css += "\n" + (repo / "main_computer/web/applications/styles/mcel-semantic-color-diagnostics.css").read_text(encoding="utf-8")
    css += "\n" + (repo / "main_computer/web/applications/styles/mcel-diagnostics-counter-widget.css").read_text(encoding="utf-8")
    if "</style" in css.lower():
        raise SlhApplicationError("Stylesheet contains an HTML closing tag")
    target = "remote-seh" if view == "remote-seh" else "network" if view.startswith("network") else "local"
    initial_view = "worker-bank" if view.endswith("worker-bank") else "overview"
    # Header links are browser-native addresses, not a hand-authored app layout.
    # The entire operational workspace is generated from the semantic IR.
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Secure Local Hub · MCEL</title><style>{css}\n{TAB_STYLE}</style></head>
<body class="mcel-workbench-page" data-mcel-slh-initial-view="{initial_view}" data-mcel-slh-target="{target}">
<template id="mcel-slh-navigation-context">{target_tabs(target)}</template>
<div id="mcel-workbench" aria-label="MCEL generated Secure Local Hub workspace"></div>
<p id="mcel-workbench-status" role="status">Compiling SLH semantics</p>
<details id="mcel-slh-org-failure-details" hidden>
<summary>ORG-SLH failure details</summary>
<p id="mcel-slh-org-failure-description"></p>
</details>
<script type="application/json" id="mcel-generated-data">{payload}</script>
{''.join(_script(repo, asset) for asset in ASSETS)}
<script>(function(){{"use strict";
const message=document.getElementById("mcel-workbench-status");
// This bootstrap status is outside the semantic workspace; it also must not
// replace a text node merely because an identical health poll completed.
const setStatusMessage=value=>{{if(message.textContent!==value)message.textContent=value;}};
const orgFailureDetails=document.getElementById("mcel-slh-org-failure-details");
const orgFailureDescription=document.getElementById("mcel-slh-org-failure-description");
try{{const data=JSON.parse(document.getElementById("mcel-generated-data").textContent);
const selectedTarget=document.body.dataset.mcelSlhTarget;
const port=McelSemanticSlhPorts.create({{appId:data.ir.application.appId,initialView:"{initial_view}"}});
const networkPort=McelSemanticSlhNetworkPorts.create({{appId:data.ir.application.appId}});
const sehPort=McelSemanticSehRemotePorts.create({{appId:data.ir.application.appId}});
// The shared UI uses one semantic IR, but two independent bounded transports.
// Network connection cannot invoke local setup, login, or Worker mutation.
const adapter=Object.freeze({{
  authorize:req=>req.capabilityId==="capability:organization-identity-observer" ||
    req.capabilityId==="capability:organization-identity-administration"
    ? (selectedTarget==="remote-seh"?sehPort.authorize(req):selectedTarget==="network"?networkPort.authorize(req):port.authorize(req))
    : req.capabilityId==="capability:remote-seh-connection"
    ? (selectedTarget==="remote-seh" && sehPort.authorize(req))
    : req.capabilityId==="capability:seh-connection-control" ||
      req.capabilityId==="capability:seh-connection-observer" ||
      req.capabilityId==="capability:seh-deployment-control" ||
      req.capabilityId==="capability:seh-deployment-observer"
    ? (selectedTarget==="local"?port.authorize(req):selectedTarget==="network"?networkPort.authorize(req):false)
    : req.capabilityId==="capability:network-slh-connection"
    ? (selectedTarget==="network" && networkPort.authorize(req))
    : ((selectedTarget==="local" || ["intent:refresh-identities","intent:refresh-identity-status",
        "intent:refresh-lifecycle"].includes(req.intentId)) && port.authorize(req)),
  invoke:req=>req.capabilityId==="capability:organization-identity-observer" ||
    req.capabilityId==="capability:organization-identity-administration"
    ? (selectedTarget==="remote-seh"?sehPort.invoke(req):selectedTarget==="network"?networkPort.invoke(req):port.invoke(req))
    : req.capabilityId==="capability:remote-seh-connection"
    ? (selectedTarget==="remote-seh" ? sehPort.invoke(req) : Promise.reject(Error("REMOTE_SEH_TARGET_NOT_SELECTED")))
    : req.capabilityId==="capability:seh-connection-control" ||
      req.capabilityId==="capability:seh-connection-observer" ||
      req.capabilityId==="capability:seh-deployment-control" ||
      req.capabilityId==="capability:seh-deployment-observer"
    ? (selectedTarget==="local"?port.invoke(req):selectedTarget==="network"?networkPort.invoke(req):Promise.reject(Error("SEH_LIFECYCLE_FORBIDDEN")))
    : req.capabilityId==="capability:network-slh-connection"
    ? (selectedTarget==="network" ? networkPort.invoke(req) : Promise.reject(Error("NETWORK_TARGET_NOT_SELECTED")))
    : ((selectedTarget==="local" || ["intent:refresh-identities","intent:refresh-identity-status",
        "intent:refresh-lifecycle"].includes(req.intentId)) ? port.invoke(req)
        : Promise.reject(Error("LOCAL_AUTHORITY_NOT_SELECTED")))
}});
// MCEL operational diagnostics: observational only. Neither this trace nor its
// session-local history is an identity selection, a service request, or a retry.
// Never include identity names, bearer tokens, fingerprints or request bodies.
const diagnosticsKey="mcel.slh.ewg.timeline.v1."+selectedTarget;
const identityKey="mcel.slh.selected-identity.v2."+selectedTarget;
const explicitNoneMarker="__EXPLICIT_NONE_V3__";
const history=(()=>{{
  try{{const stored=JSON.parse(globalThis.sessionStorage?.getItem(diagnosticsKey)||"[]");
    return Array.isArray(stored)?stored.slice(-48).filter(item=>item?.schema==="mcel.slh.diagnostic-event.v1"):[];}}
  catch{{return [];}}
}})();
const diagnosticState={{mount:"pending",identityRefresh:"not-attempted",identityStatus:"not-attempted",
  identityDirectory:"not-observed",restoration:"not-attempted",lifecycleRefresh:"not-attempted",
  identityAttempts:0,lifecycleAttempts:0}};
let diagnosticSequence=history.reduce((max,item)=>Math.max(max,Number(item.sequence)||0),0);
const recordDiagnostic=(stage,outcome,facts={{}})=>{{
  const entry={{schema:"mcel.slh.diagnostic-event.v1",sequence:++diagnosticSequence,
    time:new Date().toISOString(),stage,outcome,...facts}};
  history.push(entry);if(history.length>60)history.splice(0,history.length-60);
  try{{globalThis.sessionStorage?.setItem(diagnosticsKey,JSON.stringify(history));}}catch{{}}
}};
recordDiagnostic("semantic-mount","started");
let managementOverlay=null;
const app=McelSemanticOperationalRuntime.mount(document.getElementById("mcel-workbench"),data.ir,
  {{capabilities:adapter,composition:data.composition,presentation:"expert",
    navigationChrome:document.getElementById("mcel-slh-navigation-context"),
    signalSchedulerIntegration:true,
    signalSchedulerStartEnabled:{json.dumps(start_certified("slh"))}}});
diagnosticState.mount="completed";
recordDiagnostic("semantic-mount","completed");
const scopeKey=identityKey;
// SLH owns the conditions under which its protected records are requested.
// MCEL owns only the declared operation, stale-response arbitration, and DOM
// reconciliation. In particular, mount and auth callbacks cannot issue reads.
const identityNotebookId="surface-node:slh.organization-identity-notebook";
const identityNotebookElement=document.querySelector(
  '[data-mcel-notebook="'+identityNotebookId+'"]');
let identityNotebookVisible=false,identityNotebookTargetRestored=false;
let identityNotebookRequestedContext="";
let identityNotebookAttemptState="idle";
let identityNotebookAttemptSequence=0;
let remoteNotebookAuthenticated=false;
// SLH alone classifies recoverability; MCEL returns the operation result.
const retryableIdentityRead=reason=>
  /(?:TRANSPORT_UNAVAILABLE|SERVICE_UNAVAILABLE|INSPECTION_REQUIRED|NETWORK_UNAVAILABLE|TEMPORARILY_UNAVAILABLE|READ_ALREADY_IN_FLIGHT)/.test(String(reason||"").toUpperCase());
// A transient protected-read failure must not become sticky while the page is
// visible. One application-owned, bounded-backoff timer is sufficient; MCEL's
// generic renderer still never initiates authentication or protected reads.
const identityNotebookRetryDelays=[1500,3000,6000,12000,30000];
let identityNotebookRetryTimer=null,identityNotebookRetryAttempts=0;
const cancelIdentityNotebookRetry=()=>{{
  if(identityNotebookRetryTimer!==null)globalThis.clearTimeout(identityNotebookRetryTimer);
  identityNotebookRetryTimer=null;
}};
const resetIdentityNotebookRetry=()=>{{
  cancelIdentityNotebookRetry();
  identityNotebookRetryAttempts=0;
}};
const scheduleIdentityNotebookRetry=()=>{{
  if(leaving || identityNotebookAttemptState!=="retryable-failure" ||
     identityNotebookRetryTimer!==null)return;
  const delay=identityNotebookRetryDelays[Math.min(identityNotebookRetryAttempts,
    identityNotebookRetryDelays.length-1)];
  ++identityNotebookRetryAttempts;
  const context=identityNotebookRequestedContext;
  recordDiagnostic("identity-notebook-recovery","retry-scheduled",{{
    attempt:identityNotebookRetryAttempts,delayMs:delay}});
  identityNotebookRetryTimer=globalThis.setTimeout(()=>{{
    identityNotebookRetryTimer=null;
    if(leaving || identityNotebookRequestedContext!==context ||
       identityNotebookAttemptState!=="retryable-failure")return;
    // A hidden page must not repeatedly authenticate in the background.
    // Focus or visibility restoration will resume the retry lane.
    if(document.visibilityState==="hidden")return;
    requestVisibleIdentityRecords(true);
    // If readiness has not yet returned, no request was started; retain a
    // single retry timer to detect the transition without user interaction.
    if(identityNotebookAttemptState==="retryable-failure")
      scheduleIdentityNotebookRetry();
  }},delay);
}};
const requestVisibleIdentityRecords=(reactivation=false)=>{{
  if(leaving || !identityNotebookTargetRestored ||
     (selectedTarget==="local" && !identityNotebookVisible))return;
  const selected=app.readState("state:selected-local-identity")||"";
  const observed=app.readState("state:organization-lifecycle");
  const identityDirectory=app.readState("state:local-identities");
  const ready=selectedTarget==="local"
    ? diagnosticState.identityRefresh==="completed" &&
      diagnosticState.identityStatus==="completed" &&
      diagnosticState.identityDirectory==="AVAILABLE" &&
      Array.isArray(identityDirectory) && identityDirectory.some(row=>row.deviceId===selected) &&
      diagnosticState.lifecycleRefresh==="completed" && observed?.state==="running"
    : remoteNotebookAuthenticated;
  // A periodic readiness inspection may briefly be pending; that is not a
  // changed identity or a grant to retry a real authorization refusal.
  if(!ready)return;
  const context=selectedTarget+":"+(selectedTarget==="local"?selected:"connected");
  if(identityNotebookRequestedContext===context &&
     !(reactivation && identityNotebookAttemptState==="retryable-failure"))return;
  if(identityNotebookRequestedContext!==context)resetIdentityNotebookRetry();
  else cancelIdentityNotebookRetry();
  identityNotebookRequestedContext=context;
  identityNotebookAttemptState="loading";
  const attempt=++identityNotebookAttemptSequence;
  // App policy: hydrate Pending, Enrolled, Trash and retention as one ordered
  // read flight. Auth callbacks may not spawn competing protected reads.
  void app.activateNotebooks({{scope:"records",notebookId:identityNotebookId,all:true}})
    .then(results=>{{
      if(leaving || identityNotebookRequestedContext!==context ||
         attempt!==identityNotebookAttemptSequence)return;
      const failure=results.find(result=>result?.disposition!=="completed");
      identityNotebookAttemptState=!failure?"loaded":
        retryableIdentityRead(failure.reason)?"retryable-failure":"refused";
      if(failure)recordDiagnostic("identity-notebook-load",identityNotebookAttemptState,{{
        disposition:String(failure.disposition||"unknown").slice(0,64)}});
      if(identityNotebookAttemptState==="retryable-failure")scheduleIdentityNotebookRetry();
      else resetIdentityNotebookRetry();
    }},error=>{{
      if(leaving || identityNotebookRequestedContext!==context ||
         attempt!==identityNotebookAttemptSequence)return;
      identityNotebookAttemptState=retryableIdentityRead(error?.message)?
        "retryable-failure":"refused";
      recordDiagnostic("identity-notebook-load",identityNotebookAttemptState,{{
        errorClass:String(error?.name||"Error").slice(0,80)}});
      if(identityNotebookAttemptState==="retryable-failure")scheduleIdentityNotebookRetry();
      else resetIdentityNotebookRetry();
    }});
}};
if(identityNotebookElement && typeof IntersectionObserver==="function"){{
  const observer=new IntersectionObserver(entries=>{{
    const wasVisible=identityNotebookVisible;
    identityNotebookVisible=entries.some(entry=>entry.target===identityNotebookElement && entry.isIntersecting);
    if(identityNotebookVisible)requestVisibleIdentityRecords(!wasVisible);
  }});
  observer.observe(identityNotebookElement);
  window.addEventListener("pagehide",()=>observer.disconnect(),{{once:true}});
}}else if(identityNotebookElement){{
  const checkVisible=()=>{{
    const wasVisible=identityNotebookVisible;
    const bounds=identityNotebookElement.getBoundingClientRect();
    identityNotebookVisible=bounds.bottom>0 && bounds.top<window.innerHeight;
    if(identityNotebookVisible)requestVisibleIdentityRecords(!wasVisible);
  }};
  window.addEventListener("scroll",checkVisible,{{passive:true}});
  window.addEventListener("resize",checkVisible);
  checkVisible();
}}
const observeSlhDiagnostics=()=>{{
  const selected=app.readState("state:selected-local-identity")||"";
  const directory=app.readState("state:local-identities");
  const identityStatus=app.readState("state:local-identity-status");
  const lifecycle=app.readState("state:organization-lifecycle");
  const available=Array.isArray(directory)?directory:[];
  let remembered=null;
  try{{remembered=globalThis.sessionStorage?.getItem(scopeKey)??null;}}catch{{}}
  const form=document.querySelector('form[data-intent="intent:configure-and-enable-organization"]');
  const formAddress=form?.querySelector('[name="bindAddress"]')?.value||null;
  const formPort=form?.querySelector('[name="port"]')?.value||null;
  const verified=diagnosticState.identityDirectory==="AVAILABLE";
  const rawLifecycle=selectedTarget==="local"?port.lifecycleObservation():null;
  const lifecycleObserved=diagnosticState.lifecycleRefresh==="completed" &&
    rawLifecycle?.classification==="observed" &&
    !!lifecycle?.bindAddress && Number.isInteger(lifecycle?.port) && lifecycle.port>0;
  const findings=[];
  if(["failed","refused"].includes(diagnosticState.identityRefresh) ||
      ["failed","refused"].includes(diagnosticState.identityStatus) ||
      diagnosticState.identityDirectory==="UNAVAILABLE")
    findings.push({{code:"slh-startup-identity-observation-unavailable",severity:"warning",status:diagnosticState.identityDirectory}});
  if(["failed","refused"].includes(diagnosticState.lifecycleRefresh))
    findings.push({{code:"slh-startup-lifecycle-observation-unavailable",severity:"warning",status:diagnosticState.lifecycleRefresh}});
  if(remembered==="__NONE__")
    findings.push({{code:"slh-legacy-none-marker-ambiguous",severity:"warning",status:"ignored-on-restoration"}});
  if(rawLifecycle && rawLifecycle.classification!=="observed")
    findings.push({{code:"slh-lifecycle-config-unverified",severity:"warning",
      status:rawLifecycle.classification}});
  if(formAddress==="0.0.0.0" && !lifecycleObserved)
    findings.push({{code:"slh-wildcard-bind-not-observed",severity:"warning",status:"draft-or-unknown"}});
  const orgFailure=selectedTarget==="local"?port.organizationFailure():null;
  if(orgFailure)findings.push({{code:orgFailure.errorCode==="ORG_SLH_BIND_ADDRESS_UNAVAILABLE"?
    "org-slh-bind-address-unavailable":"org-slh-listener-bind-failed",severity:"error",
    status:orgFailure.stage,osError:orgFailure.osError}});
  const sehManagement=managementOverlay?.diagnostics() ||
    {{schema:"mcel.slh.management-evidence.v1",status:"not-observed",
      operationId:null,causalDiagnostic:null}};
  if(sehManagement.causalDiagnostic)
    findings.push({{code:"slh-seh-operation-causal-failure",severity:"warning",
      status:sehManagement.causalDiagnostic.error_code}});
  if(sehManagement.preflight?.errorCode)
    findings.push({{code:"slh-seh-preflight-unavailable",severity:"warning",
      status:sehManagement.preflight.errorCode}});
  return {{schema:"mcel.slh.operational-evidence.v1",target:selectedTarget,
    capturesConfigurationInputs:true,
    startup:{{...diagnosticState}},
    identity:{{directoryState:identityStatus?.state||diagnosticState.identityDirectory,
      directoryVerified:verified,count:verified?available.length:null,
      selected:!!selected,selectedInDirectory:verified?available.some(row=>row.deviceId===selected):null,
      remembered:remembered===null?"absent":remembered===explicitNoneMarker?"explicit-none-marker":
        remembered==="__NONE__"?"legacy-none-marker":"saved-identity",
      rememberedInDirectory:verified&&remembered!==null&&
        ![explicitNoneMarker,"__NONE__"].includes(remembered)?
        available.some(row=>row.deviceId===remembered):null}},
    lifecycle:{{refresh:diagnosticState.lifecycleRefresh,
      state:lifecycle?.state||"not-observed",observedBindAddress:lifecycleObserved?lifecycle.bindAddress:null,
      observedPort:lifecycleObserved?lifecycle.port:null,
      rawObservation:rawLifecycle,configurationVerified:lifecycleObserved,
      renderedBindAddress:formAddress,renderedPort:formPort,
      renderedDefaultWhileUnobserved:formAddress==="0.0.0.0" && !lifecycleObserved}},
    organizationService:{{state:lifecycle?.state||"not-observed",failure:orgFailure}},
    sehManagement,timeline:history.slice(),findings}};
}};
window.MCELDiagnosticEvidence?.registerOperational("slh",observeSlhDiagnostics);
window.MCELDiagnosticsCounterWidget?.mountSemantic(data.ir.application.appId,
  document.getElementById("mcel-workbench"),data.ir,null);
const observedRead=async intent=>{{
  recordDiagnostic("observation-dispatch","started",{{intent}});
  try{{
    const receipt=await app.execute(intent);
    const outcome=receipt?.disposition==="completed"?"completed":
      receipt?.disposition==="refused-before-attempt"?"refused":
      receipt?.disposition==="failed"?"failed":"incomplete";
    recordDiagnostic("observation-settled",outcome,{{intent,
      disposition:String(receipt?.disposition||"unknown").slice(0,80)}});
    return receipt;
  }}catch(error){{
    recordDiagnostic("observation-settled","failed",{{intent,
      errorClass:String(error?.name||"Error").slice(0,80)}});
    throw error;
  }}
}};
// Changing signer invalidates both local proof and target-specific tickets.
// Selection itself is renderer-local: no authentication request is issued.
// A "None" preference is persisted only from the operator's clear button.
let operatorDeselection=false;
const workbench=document.getElementById("mcel-workbench");
workbench.addEventListener("click",event=>{{
  if(event.isTrusted && event.target?.closest?.("[data-mcel-deselect]"))
    operatorDeselection=true;
}},true);
const identitySelected=value=>{{
  port.setIdentity(value);
  networkPort.forget();sehPort.forget();
  // Signer changes invalidate protected organization records, but must not
  // withdraw local LMS management authority or erase its Coolify projection.
  if(selectedTarget==="local"){{
    app.setNotebookAuthorization(false,{{scope:"records",refresh:false}});
    app.setNotebookAuthorization(!!value,{{scope:"records",refresh:false}});
    identityNotebookRequestedContext="";
    identityNotebookAttemptState="idle";
    resetIdentityNotebookRetry();
    ++identityNotebookAttemptSequence;
    requestVisibleIdentityRecords();
  }}
  try{{
    if(value)globalThis.sessionStorage?.setItem(scopeKey,value);
    else if(operatorDeselection)globalThis.sessionStorage?.setItem(scopeKey,explicitNoneMarker);
  }}catch{{}}
  recordDiagnostic("identity-selection","changed",{{selected:!!value,
    explicitDeselection:!value&&operatorDeselection,
    directoryVerified:diagnosticState.identityDirectory==="AVAILABLE"}});
  operatorDeselection=false;
}};
document.getElementById("mcel-workbench").addEventListener("mcel:collection-selection",event=>{{
  if(event.detail?.stateId==="state:selected-local-identity"){{
    recordDiagnostic("identity-selection","user-selection",{{selected:!!event.detail.value}});
    identitySelected(event.detail.value||"");
  }}
}});
let sehConnectionAuthenticated=false;
port.onAuthenticationChanged(authenticated=>{{
  sehConnectionAuthenticated=!!authenticated;
  if(selectedTarget==="local")
    app.setNotebookAuthorization(!!authenticated,{{scope:"records",refresh:false}});
}});
networkPort.onConnectionChanged(authenticated=>{{
  if(selectedTarget==="network"){{
    app.setNotebookAuthorization(!!authenticated,{{scope:"operations"}});
    app.setNotebookAuthorization(!!authenticated,{{scope:"records",refresh:false}});
    remoteNotebookAuthenticated=!!authenticated;
    if(!authenticated){{
      identityNotebookRequestedContext="";
      identityNotebookAttemptState="idle";
      resetIdentityNotebookRetry();
      ++identityNotebookAttemptSequence;
    }}
    requestVisibleIdentityRecords();
    if(authenticated)void managementOverlay?.start();
    else managementOverlay?.pause();
  }}
}});
sehPort.onConnectionChanged(authenticated=>{{
  if(selectedTarget==="remote-seh"){{
    app.setNotebookAuthorization(!!authenticated,{{scope:"operations"}});
    app.setNotebookAuthorization(!!authenticated,{{scope:"records",refresh:false}});
    remoteNotebookAuthenticated=!!authenticated;
    if(!authenticated){{
      identityNotebookRequestedContext="";
      identityNotebookAttemptState="idle";
      resetIdentityNotebookRetry();
      ++identityNotebookAttemptSequence;
    }}
    requestVisibleIdentityRecords();
  }}
}});
if(selectedTarget!=="remote-seh"){{
  const managementPort=selectedTarget==="local"?port:networkPort;
  managementOverlay=McelSlhManagementModal.create({{
    host:document.getElementById("mcel-workbench"),
    request:(operation,args)=>managementPort.managementRequest(operation,args),
    onSettled:receipt=>{{managementPort.releaseRecordedOperation(receipt);void app.execute("intent:read-seh-deployment");}}
  }});
  managementPort.onManagementChanged(receipt=>{{
    if(receipt?.operationId)managementOverlay.track(receipt.operationId);
    void managementOverlay.refresh();
  }});
  // This event is synchronous with MCEL execution dispatch, before any
  // backend preflight or HTTP request. The LMS snapshot later takes over.
  document.getElementById("mcel-workbench").addEventListener("mcel:parity-operation",event=>{{
    const intent=event.detail?.intentId;
    const action=[
      ["intent:seh-deployment-deploy","Deploy / Redeploy SEH",false],
      ["intent:seh-deployment-undeploy","Undeploy SEH",false]
    ].find(row=>row[0]===intent);
    if(!action)return;
    if(event.detail.phase==="dispatch")managementOverlay.begin(intent,action[1],action[2]);
    if(event.detail.phase==="settled"){{
      const receipt=app.receipts().at(-1);
      managementOverlay.settled(intent,event.detail.disposition,
        receipt?.intentId===intent?receipt.reason:"",
        action[2]&&event.detail.disposition==="completed"?
          app.readState("state:seh-deployment-receipt"):null);
    }}
  }});
  if(selectedTarget==="local")void managementOverlay.start();
}}
window.__mcelSlh=Object.freeze({{app,appId:data.ir.application.appId,
  composition:data.composition,managementOverlay,
  diagnostics:observeSlhDiagnostics}});
const refreshIdentities=async()=>{{
  // Identity discovery and Coolify management both use the resident LMS.
  // Complete the identity observation first; an empty UNAVAILABLE response
  // must never be misrepresented as a verified empty identity directory.
  diagnosticState.identityRefresh="pending";
  const identities=await observedRead("intent:refresh-identities");
  diagnosticState.identityRefresh=identities?.disposition==="completed"?"completed":
    identities?.disposition==="refused-before-attempt"?"refused":"failed";
  diagnosticState.identityStatus="pending";
  const status=await observedRead("intent:refresh-identity-status");
  diagnosticState.identityStatus=status?.disposition==="completed"?"completed":
    status?.disposition==="refused-before-attempt"?"refused":"failed";
  const summary=app.readState("state:local-identity-status");
  diagnosticState.identityDirectory=String(summary?.state||"not-observed");
  requestVisibleIdentityRecords();
  recordDiagnostic("identity-directory","observed",{{directoryState:diagnosticState.identityDirectory,
    directoryCount:Array.isArray(app.readState("state:local-identities"))?
      app.readState("state:local-identities").length:null}});
  if(identities?.disposition!=="completed" || status?.disposition!=="completed"){{
    message.textContent="Local identity directory unavailable · "+
      String(summary?.explanation||identities?.reason||status?.reason||"observation failed");
    return false;
  }}
  if(summary?.state!=="AVAILABLE"){{
    message.textContent="Local identity directory "+String(summary?.state||"UNVERIFIED")+
      " · "+String(summary?.explanation||"Inspect the resident LMS identity service");
    return false;
  }}
  const selected=app.readState("state:selected-local-identity");
  const available=app.readState("state:local-identities")||[];
  if(available.some(row=>row.deviceId===selected)){{
    if(selectedTarget==="local")message.textContent=message.textContent.replace(
      " · Local identity directory unavailable","");
    diagnosticState.restoration="already-selected";
    recordDiagnostic("identity-restoration","already-selected");
    return true;
  }}
  let remembered=null;
  try{{remembered=globalThis.sessionStorage?.getItem(scopeKey)??null;}}catch{{}}
  // Explicit deselection is durable for this target; do not resurrect default.
  if(remembered===explicitNoneMarker){{
    diagnosticState.restoration="explicit-none-marker";
    recordDiagnostic("identity-restoration","none-marker");
    identitySelected("");return true;
  }}
  // Old __NONE__ entries were also written on failed page exits, so they
  // cannot prove intentional deselection. Migrate to the observed default.
  const saved=available.some(row=>row.deviceId===remembered)?remembered:null;
  const chosen=saved||port.preferredIdentity();
  diagnosticState.restoration=remembered==="__NONE__"?"legacy-none-migrated":
    chosen?"selection-requested":"not-selected";
  recordDiagnostic("identity-restoration",diagnosticState.restoration,{{
    remembered:remembered===null?"absent":remembered==="__NONE__"?"legacy-none-marker":"saved-identity",
    rememberedInDirectory:!!saved}});
  if(chosen)await app.execute("intent:choose-identity",{{key:chosen}});
  else identitySelected("");
  if(selectedTarget==="local")message.textContent=message.textContent.replace(
    " · Local identity directory unavailable","");
  return true;
}};
// Page exit is not an operator deselection. Failed discovery must not erase
// the previously remembered signer or create a no-selection preference.
let leaving=false;
window.addEventListener("pagehide",()=>{{
  leaving=true;
  const selected=app.readState("state:selected-local-identity");
  const verified=diagnosticState.identityDirectory==="AVAILABLE" &&
    (app.readState("state:local-identities")||[]).some(row=>row.deviceId===selected);
  recordDiagnostic("pagehide-selection-persistence",verified?"saved-selection":"preserved-existing",{{
    directoryVerified:diagnosticState.identityDirectory==="AVAILABLE"}});
  if(verified)try{{globalThis.sessionStorage?.setItem(scopeKey,selected);}}catch{{}}
}});
// Only an observed endpoint (or an explicit, complete operator-entered
// configuration) may be submitted. These guards have no effect on service
// mutation authority, which remains in the SLH backend.
const configForm=document.querySelector('form[data-intent="intent:configure-and-enable-organization"]');
const draftFields=new Map();
const observedConfiguration=()=>{{
  const raw=port.lifecycleObservation();
  const current=app.readState("state:organization-lifecycle");
  return diagnosticState.lifecycleRefresh==="completed" &&
    raw?.classification==="observed" && !!current?.bindAddress &&
    Number.isInteger(current?.port) && current.port>0;
}};
if(selectedTarget==="local" && configForm){{
  configForm.addEventListener("input",event=>{{
    const key=event.target?.name;
    if(event.isTrusted && ["bindAddress","port"].includes(key))
      draftFields.set(key,event.target.value);
  }},true);
  configForm.addEventListener("submit",event=>{{
    const address=configForm.elements.namedItem("bindAddress")?.value||"";
    const portValue=configForm.elements.namedItem("port")?.value||"";
    const completeDraft=["bindAddress","port"].every(key=>draftFields.has(key));
    if((!observedConfiguration() && !completeDraft) || !address.trim() ||
      !/^[0-9]+$/.test(portValue) || Number(portValue)<1 || Number(portValue)>65535){{
      event.preventDefault();event.stopImmediatePropagation();
      message.textContent="ORG-SLH configuration unverified · enter both Bind Address and Port explicitly before configuring";
      recordDiagnostic("configuration-submit","blocked-unverified",{{
        observed:observedConfiguration(),completeDraft}});
    }}
  }},true);
}}
let lastHealthFailureKey="";
const displayServiceHealth=()=>{{
  const failure=selectedTarget==="local"?port.organizationFailure():null;
  const key=failure?failure.errorCode+":"+failure.bindAddress+":"+failure.port+":"+failure.osError:"";
  if(key!==lastHealthFailureKey){{
    recordDiagnostic("organization-service-health",failure?"failure-observed":"recovered",
      failure?{{errorCode:failure.errorCode,stage:failure.stage,osError:failure.osError,
        bindAddress:failure.bindAddress,port:failure.port,
        restartCount:failure.restartCount,restartLimitReached:failure.restartLimitReached}}:{{}});
    lastHealthFailureKey=key;
  }}
  if(orgFailureDetails.hidden!==!failure)orgFailureDetails.hidden=!failure;
  const errorState=failure?"true":"false";
  if(message.dataset.error!==errorState)message.dataset.error=errorState;
  if(!failure){{if(orgFailureDescription.textContent)orgFailureDescription.textContent="";return false;}}
  const osLabel=failure.osError===null?"OS error unavailable":
    (failure.osError===10049?"WinError 10049":"OS error "+failure.osError);
  setStatusMessage("ORG-SLH failed to start · Listener bind failed ("+osLabel+") · "+
    failure.bindAddress+":"+failure.port);
  orgFailureDescription.textContent=failure.explanation+" Stage: "+failure.stage+
    ". Configured listener: "+failure.bindAddress+":"+failure.port+
    ". "+osLabel+". Restart attempts: "+(failure.restartCount??"unverified")+
    (failure.restartLimitReached?"; restart limit reached.":".")+
    " Check the computer's currently assigned IPv4 addresses; change the binding explicitly if needed.";
  return true;
}};
const refreshLifecycle=async()=>{{
  ++diagnosticState.lifecycleAttempts;
  diagnosticState.lifecycleRefresh="pending";
  let receipt;
  try{{receipt=await observedRead("intent:refresh-lifecycle");}}
  catch(error){{diagnosticState.lifecycleRefresh="failed";
    recordDiagnostic("lifecycle-hydration","failed",{{errorClass:String(error?.name||"Error").slice(0,80)}});
    throw error;
  }}
  diagnosticState.lifecycleRefresh=receipt?.disposition==="completed"?"completed":
    receipt?.disposition==="refused-before-attempt"?"refused":"failed";
  const observedLifecycle=app.readState("state:organization-lifecycle");
  const raw=port.lifecycleObservation();
  const serviceFailed=displayServiceHealth();
  requestVisibleIdentityRecords();
  recordDiagnostic("lifecycle-hydration",diagnosticState.lifecycleRefresh,{{
    state:String(observedLifecycle?.state||"not-observed").slice(0,80),
    rawState:raw?.organizationState||"not-observed",
    serviceState:raw?.serviceState||"not-observed",
    classification:raw?.classification||"no-response",
    rawEndpoint:raw?.endpoint||"not-observed",
    observedBindAddress:observedConfiguration()?observedLifecycle?.bindAddress:null,
    observedPort:observedConfiguration()?observedLifecycle?.port:null}});
  if(selectedTarget!=="local")return false;
  const identityCaveat=diagnosticState.identityDirectory!=="AVAILABLE" ||
    diagnosticState.identityRefresh!=="completed" ||
    diagnosticState.identityStatus!=="completed"?
    " · Local identity directory unavailable":"";
  if(receipt?.disposition!=="completed" || !observedConfiguration()){{
    if(!serviceFailed)setStatusMessage("ORG-SLH configuration unverified · "+
      String(raw?.classification||"observation-unavailable")+identityCaveat);
    return false;
  }}
  // The generic MCEL acceptResult renderer applies authoritative values.
  // Restore only fields the operator explicitly edited while a read ran.
  for(const [key,value] of draftFields){{
    const input=configForm?.elements.namedItem(key);
    if(input && input.value!==String(value))input.value=value;
  }}
  if(!serviceFailed)setStatusMessage("ORG-SLH "+observedLifecycle.state+" · "+
    observedLifecycle.bindAddress+":"+observedLifecycle.port+identityCaveat);
  return true;
}};
// Independent, bounded recovery lanes. Neither identity nor lifecycle
// availability is an authorization prerequisite for the other's read.
const retryDelays=[400,1300,3400];
let identityRecoveryRun=0,lifecycleRecoveryRun=0;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const recoverIdentities=async(firstAttempt=false)=>{{
  const run=++identityRecoveryRun;
  for(let attempt=0;attempt<=retryDelays.length;attempt++){{
    if(attempt>0 || !firstAttempt)await sleep(retryDelays[Math.min(attempt===0?0:attempt-1,retryDelays.length-1)]);
    if(leaving || run!==identityRecoveryRun)return false;
    ++diagnosticState.identityAttempts;
    try{{if(await refreshIdentities())return true;}}
    catch(error){{recordDiagnostic("identity-recovery","failed",{{
      attempt:attempt+1,errorClass:String(error?.name||"Error").slice(0,80)}});}}
    recordDiagnostic("identity-recovery","retry-scheduled",{{attempt:attempt+1}});
  }}
  return false;
}};
const recoverLifecycle=async()=>{{
  const run=++lifecycleRecoveryRun;
  for(let attempt=0;attempt<=retryDelays.length;attempt++){{
    if(attempt)await sleep(retryDelays[attempt-1]);
    if(leaving || run!==lifecycleRecoveryRun)return false;
    try{{if(await refreshLifecycle())return true;}}
    catch(error){{recordDiagnostic("lifecycle-recovery","failed",{{
      attempt:attempt+1,errorClass:String(error?.name||"Error").slice(0,80)}});}}
    recordDiagnostic("lifecycle-recovery","retry-scheduled",{{attempt:attempt+1}});
  }}
  return false;
}};
port.onSetupChanged(action=>{{
  if(action==="configure-and-enable")draftFields.clear();
  void recoverLifecycle();
}});
// One read-only SEH observation flight for both focus and periodic refresh.
// Skip overlapping scheduled reads and stop scheduling when the page exits.
let sehObservationPending=false;
const refreshSehObservations=async()=>{{
  if(leaving || selectedTarget!=="local" || document.visibilityState==="hidden" ||
     sehObservationPending)return;
  sehObservationPending=true;
  try{{
    const intents=["intent:read-seh-deployment"];
    // Read connection status only after an explicit administrator authentication.
    // Polling must never trigger a new signer challenge or login.
    if(sehConnectionAuthenticated)intents.push("intent:read-seh-connection");
    const results=await Promise.allSettled(intents.map(intent=>app.execute(intent)));
    results.forEach((result,index)=>{{
      if(result.status==="rejected" || result.value?.disposition!=="completed")
        recordDiagnostic("seh-observation","unavailable",{{intent:intents[index],
          errorClass:result.status==="rejected"?
            String(result.reason?.name||"Error").slice(0,80):
            String(result.value?.disposition||"unknown").slice(0,80)}});
    }});
  }}finally{{sehObservationPending=false;}}
}};
const observeOnReturn=()=>{{
  if(leaving)return;
  requestVisibleIdentityRecords(true);
  if(diagnosticState.identityDirectory!=="AVAILABLE" ||
    diagnosticState.identityRefresh!=="completed" || diagnosticState.identityStatus!=="completed")
    void recoverIdentities();
  if(selectedTarget==="local"){{void recoverLifecycle();void refreshSehObservations();}}
}};
window.addEventListener("focus",observeOnReturn);
window.addEventListener("online",observeOnReturn);
window.addEventListener("visibilitychange",()=>{{
  if(document.visibilityState==="visible" &&
     identityNotebookAttemptState==="retryable-failure")
    scheduleIdentityNotebookRetry();
}});
window.addEventListener("pagehide",cancelIdentityNotebookRetry,{{once:true}});
const healthPoll=selectedTarget==="local"?window.setInterval(()=>{{
  if(!leaving && document.visibilityState!=="hidden"){{
    if(diagnosticState.lifecycleRefresh!=="pending")
      void refreshLifecycle().catch(()=>{{}});
    void refreshSehObservations();
  }}
}},15000):null;
window.addEventListener("pagehide",()=>{{if(healthPoll!==null)window.clearInterval(healthPoll);}});
// Initial identity observation first, without delaying lifecycle hydration
// behind its *retries*. SEH inspection remains independent of signer status.
void refreshIdentities().then(identitiesAvailable=>{{
  diagnosticState.identityAttempts=1;
  if(!identitiesAvailable)void recoverIdentities();
  if(selectedTarget==="local")void recoverLifecycle();
  else message.textContent=selectedTarget==="remote-seh"?
    "Remote SEH ready to connect · no administrator session":
    "Network SLH ready to connect · no remote session established";
}},error=>{{
  diagnosticState.identityRefresh="failed";
  recordDiagnostic("startup-identity-observation","failed",{{errorClass:String(error?.name||"Error").slice(0,80)}});
  void recoverIdentities();
  if(selectedTarget==="local")void recoverLifecycle();
}})
.then(()=>app.execute("intent:select-target",{{choice:selectedTarget}}))
.then(receipt=>{{
  if(receipt?.disposition==="completed"){{
    identityNotebookTargetRestored=true;
    if(selectedTarget==="local"){{
      app.setNotebookAuthorization(true,{{scope:"operations"}});
      void refreshSehObservations();
    }}
    requestVisibleIdentityRecords();
  }}
}}).catch(error=>{{message.dataset.error="true";
  recordDiagnostic("startup-initialization","failed",{{errorClass:String(error?.name||"Error").slice(0,80)}});
  message.textContent="SLH initialization unavailable: "+String(error?.message||error);
}});
}}catch(error){{message.dataset.error="true";message.textContent="SLH semantic mount refused: "+String(error?.message||error);}}
}})();</script></body></html>'''
    return attach_inert_presentation(page, repo, "slh", "mcel-workbench", promoted=True)


def _local_status(*, port: int = DEFAULT_SLH_API_PORT, timeout: float = 1.5) -> tuple[str, dict]:
    """Fixed local origin, no proxies, no user-supplied URL, no credentials."""
    if type(port) is not int or not (1 <= port <= 65535):
        raise ValueError("invalid local SLH port")
    request = Request(f"http://127.0.0.1:{port}/v1/local/status",
                      headers={"Accept": "application/json"}, method="GET")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            if response.status != 200:
                return "UNVERIFIED", {}
            data = json.loads(response.read(65537).decode("utf-8"))
            if (not isinstance(data, dict) or data.get("schema") != "main-computer.slh-local-status.v1" or
                    not isinstance(data.get("local_service"), dict) or
                    data["local_service"].get("binding") != "loopback" or
                    data["local_service"].get("state") != "running"):
                return "UNVERIFIED", {}
            return "RUNNING", data
    except (OSError, ValueError, UnicodeError, HTTPError, URLError):
        return "UNREACHABLE", {}


def observe_lms_slh_identities(*, port: int = DEFAULT_SLH_API_PORT,
                                 timeout: float = 1.5) -> dict:
    """Read identity descriptors from LMS-SLH, never from viewport key storage.

    An unreachable or incompatible LMS-SLH is not an empty identity store.
    No private key, bearer, or signing material crosses into the browser.
    """
    def unavailable(state: str, explanation: str) -> dict:
        return {"schema": IDENTITIES_SCHEMA, "state": state,
                "defaultDeviceId": "", "identities": [], "explanation": explanation}

    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid LMS-SLH port")
    request = Request(f"http://127.0.0.1:{port}/v1/local/authenticator/identities",
                      headers={"Accept": "application/json"}, method="GET")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            if response.status != 200:
                return unavailable("UNVERIFIED", "LMS-SLH identity API was not verified")
            raw = response.read(65537)
            if len(raw) > 65536:
                return unavailable("UNVERIFIED", "LMS-SLH identity response exceeds its limit")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError, HTTPError, URLError):
        return unavailable("UNAVAILABLE", "Start or inspect LMS-SLH to discover local identities")
    if (not isinstance(payload, dict) or not isinstance(payload.get("identities"), list) or
            not isinstance(payload.get("invalid_identities"), list) or
            len(payload["identities"]) > 128 or payload["invalid_identities"]):
        return unavailable("UNVERIFIED", "LMS-SLH identity state requires inspection")
    default = payload.get("default_device_id")
    if not isinstance(default, str) or not _ID.fullmatch(default):
        return unavailable("UNVERIFIED", "LMS-SLH has no verified default identity")
    identities, seen = [], set()
    for record in payload["identities"]:
        if not isinstance(record, dict):
            return unavailable("UNVERIFIED", "Invalid LMS-SLH identity record")
        device_id, label, fingerprint = (record.get("device_id"), record.get("label"),
                                         record.get("fingerprint"))
        if (not isinstance(device_id, str) or not _ID.fullmatch(device_id) or device_id in seen or
                not isinstance(label, str) or len(label) > 120 or
                not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint) or
                record.get("usable") is not True):
            return unavailable("UNVERIFIED", "Invalid LMS-SLH identity record")
        seen.add(device_id)
        identities.append({"deviceId": device_id, "label": label,
                           "fingerprint": fingerprint,
                           "defaultStatus": "DEFAULT" if device_id == default else "ADDITIONAL"})
    if default not in seen:
        return unavailable("UNVERIFIED", "Designated identity was not present in LMS-SLH")
    return {"schema": IDENTITIES_SCHEMA, "state": "AVAILABLE",
            "defaultDeviceId": default, "identities": identities,
            "explanation": "Local identities discovered; organizational authority not yet verified"}


def _state(raw: object, *, default: str = "NOT_OBSERVED") -> str:
    if not isinstance(raw, dict):
        return default
    value = raw.get("state", default)
    return str(value)[:80] if isinstance(value, str) and value else default


def _project_organization_failure(org: dict) -> dict | None:
    """Allowlisted local health evidence; never pass raw stderr or exceptions to MCEL."""
    failure = org.get("failure")
    if not isinstance(failure, dict) or org.get("state") not in ("failed", "stopped_unexpectedly"):
        return None
    if (failure.get("error_code") not in
        ("ORG_SLH_BIND_ADDRESS_UNAVAILABLE", "ORG_SLH_LISTENER_BIND_FAILED") or
        failure.get("stage") != "LISTENER_BIND" or
        failure.get("bind_host") != org.get("bind_host") or
        failure.get("api_port") != org.get("api_port")):
        return None
    code = failure["error_code"]
    number = failure.get("os_error")
    if number is not None and (type(number) is not int or number < 0 or number > 65535):
        number = None
    count = failure.get("restart_count")
    if type(count) is not int or count < 0 or count > 10000:
        count = None
    return {
        "errorCode": code,
        "stage": "LISTENER_BIND",
        "bindAddress": str(org["bind_host"])[:45],
        "port": org["api_port"],
        "osError": number,
        "explanation": ("The configured bind address is not assigned to this computer."
                        if code == "ORG_SLH_BIND_ADDRESS_UNAVAILABLE"
                        else "The configured listener could not bind."),
        "restartCount": count,
        "restartLimitReached": failure.get("restart_limit_reached") is True,
        "observedAt": str(failure.get("observed_at") or "")[:40],
    }


def observe_slh(view: str, *, probe=_local_status, admin_token: str = "",
                worker_client: OrganizationWorkerClient | None = None) -> dict:
    """Observe one fixed loopback service. A missing service is not an empty fleet."""
    if view not in OBSERVATION_VIEWS:
        raise SlhApplicationError("Unknown observation view")
    from datetime import datetime, timezone
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    condition, payload = probe()
    if condition not in {"RUNNING", "UNREACHABLE", "UNVERIFIED"} or not isinstance(payload, dict):
        raise SlhApplicationError("Invalid SLH probe result")
    source = "GET http://127.0.0.1:8897/v1/local/status"
    # Distinct authorities: local control-plane status, durable organization
    # establishment, enrolled administrator device and configured TLS listener.
    # Nothing here assumes a LAN peer can reach a locally verified listener.
    admin = "UNVERIFIED"
    established = "UNVERIFIED"
    endpoint = "NOT_OBSERVED"
    network = "UNVERIFIED"
    tls = "UNVERIFIED"
    credential = "NOT_VERIFIED"
    organization_failure = None
    if condition == "RUNNING":
        init = _state(payload.get("initialization"), default="NOT_OBSERVED")
        if init == "NOT_OBSERVED" and isinstance(payload.get("initialization"), dict):
            init = _state({"state": payload["initialization"].get("setup_phase")})
        org = payload.get("organization_service")
        organization = _state(org)
        info = payload.get("administrator_enrollment")
        if isinstance(info, dict) and info.get("state") in (
                "ENROLLED", "MISSING", "NO_ACTIVE_DEVICE", "INCONSISTENT", "NOT_ESTABLISHED"):
            admin = info["state"]
        if init == "UNCONFIGURED" and admin == "NOT_ESTABLISHED":
            established = "NOT_ESTABLISHED"
        elif init == "INITIALIZED" and admin == "ENROLLED":
            established = "ESTABLISHED"
        elif init == "INITIALIZED" and admin in ("MISSING", "NO_ACTIVE_DEVICE", "INCONSISTENT"):
            established = "REQUIRES_RECOVERY"
        if isinstance(org, dict):
            organization_failure = _project_organization_failure(org)
            if org.get("configured") is False and organization == "not_configured":
                endpoint, network = "NOT_CONFIGURED", "NOT_CONFIGURED"
            elif org.get("configured") is True:
                host, port = org.get("bind_host"), org.get("api_port")
                try:
                    address = ipaddress.IPv4Address(host)
                    # 0.0.0.0 is a valid wildcard bind, but never an address
                    # that a remote administrator should connect to.
                    if not (type(port) is int and 1 <= port <= 65535) or address.is_multicast:
                        raise ValueError("invalid listener")
                except (ValueError, TypeError, ipaddress.AddressValueError):
                    pass
                else:
                    endpoint = f"{host}:{port}"
                    network = ("LOOPBACK_ONLY" if address.is_loopback else
                               "BIND_CONFIGURED_REACHABILITY_UNVERIFIED")
                    if org.get("network_access") not in (network, None):
                        # Contradictory evidence must not produce optimistic readiness.
                        endpoint, network = "UNVERIFIED", "UNVERIFIED"
            if (organization == "running" and org.get("listening") is True and
                    endpoint not in ("NOT_OBSERVED", "UNVERIFIED", "NOT_CONFIGURED")):
                tls = "VERIFIED_LOCAL"
            elif org.get("listening") is False:
                tls = "NOT_LISTENING"
        explanation = ("Local control-plane status only. Organization establishment, "
                       "administrator enrollment and configured TLS binding are separate. "
                       "A local TLS check does not prove network reachability. "
                       "This computer's administrator credential has not been verified.")
    else:
        init = organization = "NOT_OBSERVED"
        explanation = ("SLH loopback is not reachable. This is not evidence that the organization has zero Workers."
                       if condition == "UNREACHABLE" else
                       "Port answered without the expected SLH service identity; data was rejected.")
    if condition != "RUNNING":
        next_step = "Refresh or start the local management service on demand; organization state is not verified."
    elif established == "REQUIRES_RECOVERY":
        next_step = "Administrator recovery required. Setup already exists but there is no active administrator identity; do not rebootstrap."
    elif init == "UNCONFIGURED" and admin == "NOT_ESTABLISHED":
        next_step = "Select an LMS-SLH identity and click Login Administrator to establish the local organization and sign in."
    elif established == "UNVERIFIED":
        next_step = "Organization or administrator establishment is unverified. Diagnose authority records before administering."
    elif organization_failure:
        next_step = ("Check the computer's currently assigned IPv4 addresses and the configured ORG-SLH listener; "
                     "update the binding explicitly if it has changed.")
    elif organization == "not_configured":
        next_step = "Configure the Organization Service binding with an enrolled administrator identity."
    elif organization == "disabled":
        next_step = "Activate the explicitly configured Organization Service with an enrolled administrator identity."
    elif organization == "running" and tls == "VERIFIED_LOCAL" and admin == "ENROLLED":
        next_step = "Sign in using an enrolled administrator identity. Local key possession is NOT verified until authentication."
    else:
        next_step = "Inspect Organization Service health and identity before attempting administration."
    network_guidance = ("Loopback-only binding: the Organization Service cannot be reached from another computer."
        if network == "LOOPBACK_ONLY" else
        "Non-loopback bind configured; LAN reachability is NOT verified. Check through the Network SLH tab."
        if network == "BIND_CONFIGURED_REACHABILITY_UNVERIFIED" else
        "Network binding is not configured or has not been verified.")
    # Never infer Worker counts from scheduler, generic /v1/workers 501 stubs,
    # initialization state, or a missing authentication context.
    inventory = "AUTH_REQUIRED" if condition == "RUNNING" else "NOT_OBSERVED"
    workers = []
    worker_summary = {"observationState": inventory,
        "explanation": ("Worker inventory requires an authenticated SLH administrator session; "
                        "the generic /v1/workers route is not implemented. "
                        "An empty display is not evidence of zero enrolled Workers."),
        "observedAt": observed_at, "source": "No authenticated Organization Service read",
        "enrolledCount": "NOT_OBSERVED", "connectedCount": "NOT_OBSERVED"}
    if view == "workers" and condition == "RUNNING" and admin_token:
        try:
            raw = (worker_client or OrganizationWorkerClient()).inventory(admin_token)
            workers, worker_summary = project_inventory(raw, observed_at)
        except WorkerBridgeError as exc:
            inventory = "AUTH_REQUIRED" if exc.status in (401, 403) else "UNAVAILABLE"
            worker_summary = {**worker_summary, "observationState": inventory,
                "explanation": "Authenticated Worker observation refused: " + exc.code}
    return {
        "ok": True, "schema": SCHEMA, "view": view,
        "overview": {"serviceState": condition, "initializationState": init,
                     "organizationState": organization,
                     "administratorEnrollmentState": admin,
                     "organizationEstablishedState": established,
                     "organizationEndpoint": endpoint,
                     "organizationNetworkState": network,
                     "organizationTlsState": tls,
                     "localCredentialState": credential,
                     "managementEndpoint": "127.0.0.1:8897" if condition == "RUNNING" else "NOT_OBSERVED",
                     "nextStep": next_step, "networkGuidance": network_guidance,
                     "observationState": "OBSERVED" if condition == "RUNNING" else "UNAVAILABLE",
                     "observedAt": observed_at, "source": source, "explanation": explanation},
        "organizationFailure": organization_failure,
        "health": [
            {"id": "local", "subject": "Local management service", "state": condition,
             "source": "Control plane: 127.0.0.1:8897 (loopback only)"},
            {"id": "establishment", "subject": "SLH organization established", "state": established,
             "source": "Organization setup and enrolled administrator evidence"},
            {"id": "administrator", "subject": "Administrator identity enrollment", "state": admin,
             "source": "Local authority; current computer's credential NOT VERIFIED"},
            {"id": "organization", "subject": "Organization Service", "state": organization,
             "source": "Configured TLS listener: " + endpoint},
            {"id": "network", "subject": "Organization network binding", "state": network,
             "source": "LAN reachability is not verified by this local status check"},
        ],
        "workers": workers,
        "workerSummary": worker_summary,
    }
