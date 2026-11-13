"""MCEL semantic SLH: explicit local configure/start/disable and persistent identity.

Page mount observes only. Mutations require a local, same-origin explicit
consequential action, the selected LMS signer, and the existing SLH authority.
Remote TLS observation remains isolated from local lifecycle authority.
"""
from __future__ import annotations

import json
import ipaddress
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

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
    "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js",
    "main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js",
    "main_computer/web/applications/scripts/mcel-semantic-seh-deployment-ports.js",
    "main_computer/web/applications/scripts/mcel-semantic-slh-ports.js",
    "main_computer/web/applications/scripts/mcel-semantic-slh-network-ports.js",
    "main_computer/web/applications/scripts/mcel-semantic-seh-remote-ports.js",
)
STYLE = "main_computer/web/applications/styles/mcel-semantic-workbench.css"
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
    local_form = nodes.get("surface-node:slh.configure-org-slh", {})
    remote_form = nodes.get("surface-node:slh.connect-network-slh", {})
    seh_form = nodes.get("surface-node:slh.connect-remote-seh", {})
    seh_notebooks = [nodes.get("surface-node:slh."+target+"-seh-lifecycle", {})
                     for target in ("local","network")]
    if (seh_form.get("nodeKind") != "form" or
        seh_form.get("intent",{}).get("ref") != "intent:connect-remote-seh" or
        seh_form.get("fields") != {"sehUrl":{"kind":"string"}} or
        any(n.get("nodeKind") != "operation-notebook" or n.get("title") != "SEH Lifecycle" or
            n.get("visibleWhen",{}).get("equals") != target or
            [tab.get("id") for tab in n.get("tabs",[])] !=
                ["configuration","deployment","connection","status","diagnostics"] or
            set(n.get("tabs",[])[2].get("actions",{})) != {"Start","Stop","Restart"} or
            set(n.get("tabs",[])[1].get("actions",{})) != {
                "Preflight Deploy","Deploy","Preflight Undeploy","Undeploy"} or
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
        "intent:login-administrator", "intent:logout-administrator",
        "intent:connect-remote-seh", "intent:disconnect-remote-seh", "intent:read-seh-connection",
        "intent:read-seh-deployment", *{f"intent:seh-deployment-{operation}" for operation in
          ("register","select","preview-deploy","deploy","preview-undeploy","undeploy")},
        *{f"intent:seh-connection-{action}" for action in ("start","stop","restart")},
        *{f"intent:read-organization-{name}" for name in ("pending","enrolled","trash","retention")},
        *{f"intent:organization-{name}" for name in ("accept","reject","delete","reactivate","remove")},
        "intent:set-organization-trash-retention", "intent:empty-organization-trash"
    }
    expected_capabilities = {
        "capability:slh-observer":"external-read", "capability:network-slh-connection":"consequential",
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
        "effect:connect-network-slh.request":"consequential",
        "effect:disconnect-network-slh.request":"consequential",
        "effect:configure-and-enable-organization.request":"consequential",
        "effect:toggle-organization-enablement.request":"consequential",
        "effect:toggle-organization.request":"consequential",
        "effect:login-administrator.request":"consequential",
        "effect:logout-administrator.request":"consequential",
        "effect:connect-remote-seh.request":"consequential",
        "effect:disconnect-remote-seh.request":"consequential",
        "effect:read-seh-connection.request":"external-read",
        "effect:read-seh-deployment.request":"external-read",
        **{f"effect:seh-deployment-{operation}.request":"consequential" for operation in
          ("register","select","preview-deploy","deploy","preview-undeploy","undeploy")},
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
    css = (repo / STYLE).read_text(encoding="utf-8")
    if "</style" in css.lower():
        raise SlhApplicationError("Stylesheet contains an HTML closing tag")
    target = "remote-seh" if view == "remote-seh" else "network" if view.startswith("network") else "local"
    initial_view = "worker-bank" if view.endswith("worker-bank") else "overview"
    # Header links are browser-native addresses, not a hand-authored app layout.
    # The entire operational workspace is generated from the semantic IR.
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Secure Local Hub · MCEL</title><style>{css}\n{TAB_STYLE}</style></head>
<body class="mcel-workbench-page" data-mcel-slh-initial-view="{initial_view}" data-mcel-slh-target="{target}">
<template id="mcel-slh-navigation-context">{target_tabs(target)}</template>
<div id="mcel-workbench" aria-label="MCEL generated Secure Local Hub workspace"></div>
<p id="mcel-workbench-status" role="status">Compiling SLH semantics</p>
<script type="application/json" id="mcel-generated-data">{payload}</script>
{''.join(_script(repo, asset) for asset in ASSETS)}
<script>(function(){{"use strict";
const message=document.getElementById("mcel-workbench-status");
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
const app=McelSemanticOperationalRuntime.mount(document.getElementById("mcel-workbench"),data.ir,
  {{capabilities:adapter,composition:data.composition,presentation:"expert",
    navigationChrome:document.getElementById("mcel-slh-navigation-context")}});
port.onAuthenticationChanged(authenticated=>{{
  if(selectedTarget==="local")app.setNotebookAuthorization(authenticated);
}});
networkPort.onConnectionChanged(authenticated=>{{
  if(selectedTarget==="network")app.setNotebookAuthorization(authenticated);
}});
sehPort.onConnectionChanged(authenticated=>{{
  if(selectedTarget==="remote-seh")app.setNotebookAuthorization(authenticated);
}});
void app.execute("intent:select-target",{{choice:selectedTarget}});
window.__mcelSlh=Object.freeze({{app,composition:data.composition}});
const refreshIdentities=async()=>{{
  const observations=await Promise.all([app.execute("intent:refresh-identities"),
    app.execute("intent:refresh-identity-status")]);
  if(!observations.every(r=>r && r.disposition==="completed"))return;
  // Preserve the operator's existing selection when it remains available.
  // Select the LMS-SLH default only on first discovery or if selection vanished.
  const selected=app.readState("state:selected-local-identity");
  const available=app.readState("state:local-identities")||[];
  if(available.some(row=>row.deviceId===selected))return;
  // A device ID is nonsecret, unlike its signing key or a bearer. Persist only
  // the selection for the lifetime of this browser tab across canonical URLs.
  let remembered="";
  try{{remembered=globalThis.sessionStorage?.getItem("mcel.slh.selected-identity.v1")||"";}}catch{{}}
  const chosen=available.some(row=>row.deviceId===remembered)?remembered:port.preferredIdentity();
  if(chosen)await app.execute("intent:choose-identity",{{key:chosen}});
}};
// Page navigation performs read-only observations ONLY. Both consequential
// lifecycle actions require explicit interactions and are never autoplayed.
const saveSelectedIdentity=()=>{{
  try{{
    const value=app.readState("state:selected-local-identity")||"";
    if(/^[A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}$/.test(value))
      globalThis.sessionStorage?.setItem("mcel.slh.selected-identity.v1",value);
    else globalThis.sessionStorage?.removeItem("mcel.slh.selected-identity.v1");
  }}catch{{}}
}};
window.addEventListener("pagehide",saveSelectedIdentity);
const refreshLifecycle=async()=>{{
  const receipt=await app.execute("intent:refresh-lifecycle");
  if(selectedTarget!=="local" || receipt?.disposition!=="completed")return;
  const observed=app.readState("state:organization-lifecycle");
  if(observed?.state)message.textContent="ORG-SLH "+observed.state+" · "+
    observed.bindAddress+":"+observed.port;
}};
port.onSetupChanged(()=>{{void refreshLifecycle();}});
const initialIdentityRefresh=refreshIdentities();
initialIdentityRefresh.then(()=>{{
  if(selectedTarget==="local")void refreshLifecycle();
  else message.textContent=selectedTarget==="remote-seh"?"Remote SEH ready to connect · no administrator session":"Network SLH ready to connect · no remote session established";
}}).catch(()=>{{message.textContent="Local signing identities unavailable · configuration cannot proceed";}});
}}catch(error){{message.dataset.error="true";message.textContent="SLH semantic mount refused: "+String(error?.message||error);}}
}})();</script></body></html>'''


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
