"""MCEL semantic SLH: local status and explicitly authenticated Worker Bank.

No SLH auto-start, database bypass, arbitrary proxy or hand-authored dashboard.
The host owns only the authorization affordance; MCEL synthesizes the workspace.
"""
from __future__ import annotations

import json
import ipaddress
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
    "main_computer/web/applications/scripts/mcel-semantic-slh-ports.js",
)
STYLE = "main_computer/web/applications/styles/mcel-semantic-workbench.css"
ROUTES = {
    "/applications/slh": "overview", "/applications/slh/local": "overview",
    "/applications/slh/workers": "worker-bank",
    "/applications/slh/local/workers": "worker-bank",
    "/applications/slh/network": "network"
}
SCHEMA = "mcel.slh.first-slice-observation.v1"
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
            surface is None or surface.get("purpose") != "monitor" or
            surface.get("interfaceIntent") or surface.get("htmlBindings")):
        raise SlhApplicationError("SLH source cannot prescribe presentation or host authority")
    if {x["id"] for x in ir.get("intents", ())} != {
        "intent:refresh-overview", "intent:refresh-health", "intent:refresh-workers",
        "intent:refresh-worker-summary", "intent:select-view", "intent:choose-worker",
        "intent:rename-worker", "intent:start-local", "intent:bootstrap-administrator",
        "intent:configure-organization", "intent:activate-organization", "intent:login-administrator",
        "intent:logout-administrator"
    } or {x["id"] for x in ir.get("capabilities", ())} != {
        "capability:slh-observer", "capability:slh-worker-admin", "capability:slh-first-run"
    }:
        raise SlhApplicationError("SLH operation allowlist changed")
    if {x["id"]: x.get("risk") for x in ir.get("capabilities", ())} != {
        "capability:slh-observer": "external-read", "capability:slh-worker-admin": "consequential",
        "capability:slh-first-run": "consequential"
    } or {x["id"]: x.get("risk") for x in ir.get("effects", ())} != {
        "effect:refresh-overview.request": "external-read", "effect:refresh-health.request": "external-read",
        "effect:refresh-workers.request": "external-read", "effect:refresh-worker-summary.request": "external-read",
        "effect:rename-worker.request": "consequential",
        "effect:start-local.request": "consequential",
        "effect:bootstrap-administrator.request": "consequential",
        "effect:configure-organization.request": "consequential",
        "effect:activate-organization.request": "consequential",
        "effect:login-administrator.request": "consequential",
        "effect:logout-administrator.request": "consequential"
    }:
        raise SlhApplicationError("SLH effects differ from exact Worker Bank allowlist")
    composition = synthesize_composition(ir, policy="mcel.semantic-structure.p3")
    verify_composition(ir, composition)
    return ir, composition


def _script(repo: Path, relative: str) -> str:
    content = (repo / relative).read_text(encoding="utf-8")
    if "</script" in content.lower():
        raise SlhApplicationError("Script contains an HTML closing tag")
    return "<script>\n" + content + "\n</script>"


def render_slh_page(view: str = "overview", repo: Path = ROOT) -> str:
    # mcel.slh.target-tabs.v2: target selection precedes local service lifecycle.
    if view == "network":
        from main_computer.mcel_slh_network_host import render_network_slh_page
        return render_network_slh_page(repo)
    if view not in ROUTES.values():
        raise SlhApplicationError("Unknown semantic SLH destination")
    ir, composition = compile_slh_application(repo)
    payload = json.dumps({"ir": ir, "composition": composition},
                         separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    css = (repo / STYLE).read_text(encoding="utf-8")
    if "</style" in css.lower():
        raise SlhApplicationError("Stylesheet contains an HTML closing tag")
    # Header links are browser-native addresses, not a hand-authored app layout.
    # The entire operational workspace is generated from the semantic IR.
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Secure Local Hub · MCEL</title><style>{css}\n{TAB_STYLE}</style></head>
<body class="mcel-workbench-page" data-mcel-slh-initial-view="{view}">
<header><h1>Secure Local Hub</h1>{target_tabs("local")}<p>Start SLH on demand, establish the local administrator and configure the Organization Service. No Windows boot startup.</p>
<nav aria-label="SLH destinations"><a href="/applications/slh">Overview</a> · <a href="/applications/slh/workers">Worker Bank</a></nav>
<p id="mcel-slh-auth-status" role="status">Organization session not established</p></header>
<div id="mcel-workbench" aria-label="MCEL generated Secure Local Hub workspace"></div>
<p id="mcel-workbench-status" role="status">Compiling SLH semantics</p>
<script type="application/json" id="mcel-generated-data">{payload}</script>
{''.join(_script(repo, asset) for asset in ASSETS)}
<script>(function(){{"use strict";
const message=document.getElementById("mcel-workbench-status");
try{{const data=JSON.parse(document.getElementById("mcel-generated-data").textContent);
const port=McelSemanticSlhPorts.create({{appId:data.ir.application.appId,initialView:"{view}"}});
const app=McelSemanticOperationalRuntime.mount(document.getElementById("mcel-workbench"),data.ir,
  {{capabilities:port,composition:data.composition,presentation:"expert"}});
window.__mcelSlh=Object.freeze({{app,composition:data.composition}});
const authStatus=document.getElementById("mcel-slh-auth-status");
port.onSetupChanged(()=>{{
  Promise.all([app.execute("intent:refresh-overview"),app.execute("intent:refresh-health")]);
}});
port.onAuthenticationChanged(authorized=>{{
  Promise.all([app.execute("intent:refresh-worker-summary"),app.execute("intent:refresh-workers")])
    .then(()=>{{authStatus.textContent=authorized?
      "Organization administrator authenticated":"Organization session not established";}})
    .catch(()=>{{authStatus.textContent="Organization session verification unavailable";}});
}});
const chosen=document.body.dataset.mcelSlhInitialView;
const initial=chosen==="worker-bank" ? "worker-bank" : "overview";
Promise.resolve(initial==="worker-bank" ? app.execute("intent:select-view",{{choice:"worker-bank"}}) : null)
.then(()=>Promise.all(initial==="worker-bank" ?
 [app.execute("intent:refresh-worker-summary"),app.execute("intent:refresh-workers")] :
 [app.execute("intent:refresh-overview"),app.execute("intent:refresh-health"),
  app.execute("intent:refresh-worker-summary")]))
.then(async receipts=>{{if(!receipts.every(r=>r && r.disposition==="completed"))
  throw Error("observation-not-complete");
  if(initial==="worker-bank"){{
    const context=await app.execute("intent:refresh-overview");
    if(!context || context.disposition!=="completed")throw Error("overview-context-unavailable");
  }}
  message.textContent="SLH observation complete · "+initial;}})
.catch(()=>{{message.textContent="SLH observation refused; inspect operation receipts";}});
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
        next_step = "Administrator recovery required. Setup already exists but there is no active administrator device; do not rebootstrap."
    elif init == "UNCONFIGURED" and admin == "NOT_ESTABLISHED":
        next_step = "Establish the first administrator for the local SLH organization."
    elif established == "UNVERIFIED":
        next_step = "Organization or administrator establishment is unverified. Diagnose authority records before administering."
    elif organization == "not_configured":
        next_step = "Configure the Organization Service binding with an enrolled administrator identity."
    elif organization == "disabled":
        next_step = "Activate the explicitly configured Organization Service with an enrolled administrator identity."
    elif organization == "running" and tls == "VERIFIED_LOCAL" and admin == "ENROLLED":
        next_step = "Sign in using an enrolled administrator device. Local key possession is NOT verified until authentication."
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
            {"id": "administrator", "subject": "Administrator device enrollment", "state": admin,
             "source": "Local authority; current computer's credential NOT VERIFIED"},
            {"id": "organization", "subject": "Organization Service", "state": organization,
             "source": "Configured TLS listener: " + endpoint},
            {"id": "network", "subject": "Organization network binding", "state": network,
             "source": "LAN reachability is not verified by this local status check"},
        ],
        "workers": workers,
        "workerSummary": worker_summary,
    }
