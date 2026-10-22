"""Opt-in V2 RPC browser-compatibility path for the official Mother harness.

No existing route renderer, writer, CLI, or automatic deployment behavior changes.
This module is invoked only by `mother_mutate_harness.py rpc-browser-compat-v2`.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import shlex
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = (
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn",
)


class RpcBrowserCompatV2Error(RuntimeError):
    pass


def render_initial_v2(target: Mapping[str, Any]) -> str:
    """Add V2 middleware to a render of Mother's original route; change no upstream."""
    from tools.mother.common import deployment_validator_rpc_canary_funding as writer

    original = yaml.safe_load(writer._render_shared_rpc_traefik_dynamic_config(target))
    if not isinstance(original, dict) or not isinstance(original.get("http"), dict):
        raise RpcBrowserCompatV2Error("legacy RPC route is malformed")
    http = original["http"]
    routers, services = http.get("routers"), http.get("services")
    if not isinstance(routers, dict) or len(routers) != 2 or not isinstance(services, dict) or len(services) != 1:
        raise RpcBrowserCompatV2Error("unexpected RPC route topology")
    if "middlewares" in http:
        raise RpcBrowserCompatV2Error("existing route includes middleware; refuse V2")
    service_id = next(iter(services))
    if services[service_id].get("loadBalancer", {}).get("servers") != [{"url": str(target["target_url"])}]:
        raise RpcBrowserCompatV2Error("legacy route backend differs from Mother-selected node")
    middleware_id = writer._traefik_resource_id("mother", target["network"], "rpc", "cors-v2", target["controller_id"])
    strip_id = writer._traefik_resource_id("mother", target["network"], "rpc", "strip-origin-v2", target["controller_id"])
    for router in routers.values():
        if router.get("service") != service_id or router.get("rule") != f"Host(`{target['route_host']}`)":
            raise RpcBrowserCompatV2Error("legacy route identity mismatch")
        if "middlewares" in router:
            raise RpcBrowserCompatV2Error("existing router has middleware; refuse V2")
        router["middlewares"] = [middleware_id, strip_id]
    http["middlewares"] = {
        middleware_id: {"headers": {
            "accessControlAllowMethods": ["POST", "OPTIONS"],
            "accessControlAllowHeaders": ["Content-Type"],
            "accessControlAllowOriginList": list(ORIGINS),
            "accessControlMaxAge": 600,
            "addVaryHeader": True,
        }},
        strip_id: {"headers": {"customRequestHeaders": {"Origin": ""}}},
    }
    return "# Mother RPC browser compatibility V2 -- opt in only.\n" + yaml.safe_dump(original, sort_keys=False)



def render_v2(target: Mapping[str, Any]) -> str:
    """V2.1: match each approved Origin *before* stripping it for Besu.

    A single headers middleware for each matched Origin handles the OPTIONS
    preflight, removes Origin only on forwarded POST requests, and sets that
    origin's exact Access-Control-Allow-Origin response header. Unmatched
    origins use Mother's unmodified fallback routes (no CORS grant).
    """
    from tools.mother.common import deployment_validator_rpc_canary_funding as writer

    original = yaml.safe_load(writer._render_shared_rpc_traefik_dynamic_config(target))
    if not isinstance(original, dict) or not isinstance(original.get("http"), dict):
        raise RpcBrowserCompatV2Error("legacy RPC route is malformed")
    http = original["http"]
    routers, services = http.get("routers"), http.get("services")
    if not isinstance(routers, dict) or len(routers) != 2 or not isinstance(services, dict) or len(services) != 1:
        raise RpcBrowserCompatV2Error("unexpected RPC route topology")
    if "middlewares" in http:
        raise RpcBrowserCompatV2Error("existing route includes middleware; refuse V2")
    service_id = next(iter(services))
    if services[service_id].get("loadBalancer", {}).get("servers") != [{"url": str(target["target_url"])}]:
        raise RpcBrowserCompatV2Error("legacy route backend differs from Mother-selected node")

    http["middlewares"] = {}
    for name, router in list(routers.items()):
        if router.get("service") != service_id or router.get("rule") != f"Host(`{target['route_host']}`)":
            raise RpcBrowserCompatV2Error("legacy route identity mismatch")
        if "middlewares" in router:
            raise RpcBrowserCompatV2Error("existing router has middleware; refuse V2")
        if router.get("entryPoints") not in (["http"], ["https"]):
            raise RpcBrowserCompatV2Error("unknown router entrypoint; refuse V2")
        for suffix, origin in zip(("worker-ip", "worker-localhost", "metamask"), ORIGINS):
            # Explicit Origin-qualified routes take priority over Mother's unchanged
            # fallback Host routes. Do not grant wildcard CORS access.
            special = dict(router)
            special["rule"] = f"Host(`{target['route_host']}`) && Header(`Origin`, `{origin}`)"
            special["priority"] = 100
            middleware_id = writer._traefik_resource_id(
                "mother", target["network"], "rpc", "cors-v2", suffix, target["controller_id"]
            )
            special["middlewares"] = [middleware_id]
            routers[f"{name}-{suffix}"] = special
            http["middlewares"][middleware_id] = {"headers": {
                "accessControlAllowMethods": ["POST", "OPTIONS"],
                "accessControlAllowHeaders": ["Content-Type"],
                "accessControlAllowOriginList": [origin],
                "accessControlMaxAge": 600,
                "addVaryHeader": True,
                "customRequestHeaders": {"Origin": ""},
                # Traefik checks response CORS using the *mutated* request.
                # Provide the matched origin explicitly; do not use '*'.
                "customResponseHeaders": {"Access-Control-Allow-Origin": origin},
            }}
    return "# Mother RPC browser compatibility V2.1 -- opt in only.\n" + yaml.safe_dump(original, sort_keys=False)

def build_v2_writer_script(
    target: Mapping[str, Any], *, legacy_script: Callable[[Mapping[str, Any]], str] | None = None,
    rollback: bool = False,
) -> str:
    """Create the ephemeral route writer; accept only byte-exact known route content.

    Guard and rollback are injected into a *copy* of the generated helper.
    Mother's existing writer is never modified in source or persisted state.
    """
    from tools.mother.common import deployment_validator_rpc_canary_funding as writer

    legacy_render = writer._render_shared_rpc_traefik_dynamic_config(target)
    v2_render = render_v2(target)
    previous_v2_render = render_initial_v2(target)
    old_b64 = base64.b64encode(legacy_render.encode()).decode("ascii")
    new_b64 = base64.b64encode(v2_render.encode()).decode("ascii")
    previous_v2_b64 = base64.b64encode(previous_v2_render.encode()).decode("ascii")
    script = (legacy_script or writer._route_writer_script)(target)
    assignment = f"DYNAMIC_CONFIG_B64 = {old_b64!r}"
    if script.count(assignment) != 1:
        raise RpcBrowserCompatV2Error("Mother route-writer layout changed; V2 disabled")
    script = script.replace(assignment, f"DYNAMIC_CONFIG_B64 = {(old_b64 if rollback else new_b64)!r}", 1)

    # The ordinary Mother writer may delete an obsolete route file. V2 must not.
    delete_lines = (
        '    f"rm -f \'{LEGACY_TARGET_PATH}\'\\n"\n'
        '    f"test ! -e \'{LEGACY_TARGET_PATH}\'\\n"\n'
    )
    if target.get("legacy_dynamic_file"):
        if script.count(delete_lines) != 1:
            raise RpcBrowserCompatV2Error("Mother legacy route deletion layout changed; V2 disabled")
        script = script.replace(delete_lines, "", 1)

    path = f"/traefik/dynamic/{target['dynamic_file']}"
    guard = (
        f"test -f '{path}'\n"
        + (f"test ! -e '/traefik/dynamic/{target['legacy_dynamic_file']}'\n" if target.get("legacy_dynamic_file") else "")
        + f"(printf '%s' '{old_b64}' | base64 -d | cmp -s - '{path}') || "
          f"(printf '%s' '{previous_v2_b64}' | base64 -d | cmp -s - '{path}') || "
          f"(printf '%s' '{new_b64}' | base64 -d | cmp -s - '{path}') || exit 65\n"
        + f"v2_backup='{path}.v2-backup-'$$\n"
        + f"cp '{path}' \"$v2_backup\"\n"
        + f"trap 'cp \"$v2_backup\" \"{path}\"; rm -f \"$v2_backup\"' EXIT\n"
    )
    needle = '    f"printf \'%s\' \'{DYNAMIC_CONFIG_B64}\' | base64 -d > \'{TARGET_PATH}.tmp\'\\n"\n'
    if script.count(needle) != 1:
        raise RpcBrowserCompatV2Error("Mother route-write location changed; V2 disabled")
    script = script.replace(needle, f"    {guard!r}\n" + needle, 1)

    # Only declare success after the original writer's chain-ID verification.
    # On failure the shell EXIT trap restores the route's byte-exact preimage.
    end = '    + EXPECTED_CHAIN_ID_HEX\n    + "\\\"\'\\n"\n)'
    if script.count(end) != 1:
        raise RpcBrowserCompatV2Error("Mother route verification layout changed; V2 disabled")
    # Probe *POST response* CORS before committing the changed public route.
    # The original health proof checks only an Origin-free eth_chainId request.
    # Any probe failure triggers the existing shell EXIT trap, restoring the
    # byte-exact previously installed V2 or legacy route.
    probe = ""
    if not rollback:
        payload = '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
        for origin in ORIGINS:
            probe += (
                "cors_out=$(wget -S -T 8 -O- "
                f"--header={shlex.quote('Host: ' + str(target['route_host']))} "
                f"--header={shlex.quote('Origin: ' + origin)} "
                "--header='Content-Type: application/json' "
                f"--post-data={shlex.quote(payload)} 'http://127.0.0.1/' 2>&1)\n"
                + "printf '%s' \"$cors_out\" | grep -iqF "
                + shlex.quote("Access-Control-Allow-Origin: " + origin)
                + "\n"
                + "printf '%s' \"$cors_out\" | grep -q "
                + shlex.quote('"result"[[:space:]]*:[[:space:]]*"' + hex(int(target['expected_chain_id'])) + '"')
                + "\n"
            )
    success = f"trap - EXIT\nrm -f \"$v2_backup\"\n"
    script = script.replace(end, '    + EXPECTED_CHAIN_ID_HEX\n    + "\\\"\'\\n"\n' + f"    + {probe!r}\n" + f"    + {success!r}\n" + ')', 1)
    compile(script, "mother-rpc-browser-compat-v2-helper.py", "exec")
    return script


def execute_v2(private_state: Any, *, network: str, node: str, controller_id: str,
               timeout: float, max_wait_seconds: float, rollback: bool = False) -> dict[str, Any]:
    """Mutate only under a single, explicit Mother-harness invocation.

    Reuse Mother's authorized Coolify writer, deployment/health proof, and cleanup.
    Process-local scoped hook is restored even if deployment fails.
    """
    from tools.mother.common import deployment_validator_rpc_canary_funding as writer
    from tools.mother_rpc_route_ensure import ensure_route

    original_writer = writer._route_writer_script

    def v2_writer(target: Mapping[str, Any]) -> str:
        return build_v2_writer_script(target, legacy_script=original_writer, rollback=rollback)

    with patch.object(writer, "_route_writer_script", v2_writer):
        result = ensure_route(private_state, network=network, node=node,
                              controller_id=controller_id, execute=True,
                              timeout=timeout, max_wait_seconds=max_wait_seconds)
    result["mode"] = "rpc-browser-compat-v2-rollback" if rollback else "rpc-browser-compat-v2-opt-in"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mother_mutate_harness.py rpc-browser-compat-v2")
    parser.add_argument("--network", required=True, choices=["mainnet", "testnet"])
    parser.add_argument("--node", required=True)
    parser.add_argument("--host", required=True, choices=["coolify-a", "coolify-b", "coolify-c"])
    parser.add_argument("--runtime-state-root", default=str(ROOT / "runtime" / "state"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-wait-seconds", type=float, default=300.0)
    parser.add_argument("--execute-mutations", action="store_true")
    parser.add_argument("--rollback", action="store_true", help="reinstall the byte-exact legacy route under the same guard")
    parser.add_argument("--yes-i-know-this-mutates-target-host", action="store_true")
    args = parser.parse_args(argv)
    if args.execute_mutations != args.yes_i_know_this_mutates_target_host:
        parser.error("execution requires both --execute-mutations and --yes-i-know-this-mutates-target-host")
    if args.timeout <= 0 or args.max_wait_seconds <= 0:
        parser.error("timeouts must be positive")

    from tools.mother.common.models import OperationIdentity
    from tools.mother.common.paths import MotherPaths
    from tools.mother.common.private_state import read_private_state
    from tools.mother_rpc_route_ensure import route_plan
    try:
        operation = OperationIdentity(
            operation_id=f"mother-rpc-browser-v2-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            request_id="mother-rpc-browser-v2", network=args.network,
            operation_kind="MOTHER-OP-RPC-PROPAGATE",
        )
        paths = MotherPaths(runtime_state_root=Path(args.runtime_state_root)).resolve_private_state_paths()
        state = read_private_state(paths, operation=operation)
        plan = route_plan(state, network=args.network, node=args.node, controller_id=args.host)
        # Generate a deterministic preview using the exact route identity.
        route_target = {
            "route_host": plan["host"], "target_url": plan["backend_url"],
            "network": args.network, "controller_id": args.host,
        }
        v2_preview = render_v2(route_target)
        if not args.execute_mutations:
            print(json.dumps({"status": "preview", "version": "v2", "mutated": False,
                              "route": plan, "policy_sha256": hashlib.sha256(v2_preview.encode()).hexdigest(),
                              "allowed_origins": ORIGINS, "action": "rollback" if args.rollback else "upgrade"}, indent=2, sort_keys=True))
            return 0
        result = execute_v2(state, network=args.network, node=args.node, controller_id=args.host,
                            timeout=args.timeout, max_wait_seconds=args.max_wait_seconds, rollback=args.rollback)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "version": "v2", "mutated": "unknown",
                          "error": str(exc)}, indent=2, sort_keys=True))
        return 2
