"""Read-only P5.2 Hub routing acceptance: local profiles and optional Viewport state.

    python -m tools.hub_interface_verify
    python -m tools.hub_interface_verify --live

Neither mode performs remote compute, Hub probes, Worker mutations or payments.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from main_computer.hub_interface import resolve_hub_destination


NETWORKS = ("dev", "test", "testnet", "mainnet", "none")


def verify_profiles() -> dict[str, Any]:
    routes = [resolve_hub_destination({"selectedNetwork": n}).diagnostics() for n in NETWORKS]
    expected = ("dev-test", "dev-test", "stable-hub-paid", "stable-hub-paid", "none")
    if any(route["protocol"] != protocol for route, protocol in zip(routes, expected)):
        raise RuntimeError("Hub network profile-to-protocol boundary mismatch")
    return {"ok": True, "scope": "profiles", "routes": routes}


def verify_live_viewport(viewport_url: str = "http://127.0.0.1:8765") -> dict[str, Any]:
    parsed = urlsplit(viewport_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Live verification requires a local-only Viewport URL")
    root = viewport_url.rstrip("/")
    request = Request(root + "/api/applications/worker/settings", headers={"Accept": "application/json"})
    with urlopen(request, timeout=5.0) as response:
        payload = json.loads(response.read(4_000_000).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("settings"), dict):
        raise RuntimeError("Viewport did not return the canonical Worker settings record")
    settings = payload["settings"]
    destination = resolve_hub_destination(settings)
    return {
        "ok": True,
        "scope": "live-viewport-settings",
        "routing": destination.diagnostics(),
        "remote_enabled": bool(settings.get("remoteEnabled", False)),
        "runtime_auto_connect_network": str(settings.get("workerAutoConnectNetwork") or "none"),
        "note": "Read-only. No live Hub request or paid computation was attempted.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only P5.2 Hub interface and Worker selection verification")
    parser.add_argument("--live", action="store_true", help="Read current Worker settings from local Viewport without modifying them")
    parser.add_argument("--viewport-url", default="http://127.0.0.1:8765")
    args = parser.parse_args(argv)
    try:
        report = verify_live_viewport(args.viewport_url) if args.live else verify_profiles()
    except Exception as exc:
        print(f"HUB_INTERFACE_VERIFY_FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
