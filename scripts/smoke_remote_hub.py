from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_HUB_URL = "http://127.0.0.1:8770"


class SmokeError(RuntimeError):
    pass


def _join_url(base: str, path: str) -> str:
    clean_base = base.rstrip("/")
    clean_path = "/" + path.lstrip("/")
    return clean_base + clean_path


def _get_json(base_url: str, path: str, *, timeout: float) -> dict[str, Any]:
    url = _join_url(base_url, path)
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "main-computer-remote-hub-smoke/1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise SmokeError(f"GET {path} failed with HTTP {exc.code}: {body[:500]}") from exc
    except URLError as exc:
        raise SmokeError(f"GET {path} failed: {exc}") from exc
    except TimeoutError as exc:
        raise SmokeError(f"GET {path} timed out after {timeout} seconds") from exc

    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise SmokeError(f"GET {path} did not return JSON: {payload[:200]!r}") from exc
    if not isinstance(data, dict):
        raise SmokeError(f"GET {path} returned a non-object JSON payload")
    return data


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def run_smoke(*, hub_url: str, timeout: float, require_https: bool) -> dict[str, Any]:
    parsed = urlparse(hub_url)
    if require_https:
        _require(parsed.scheme == "https", "--require-https was set but --hub-url is not https://")
    _require(parsed.scheme in {"http", "https"}, "--hub-url must start with http:// or https://")
    _require(bool(parsed.netloc), "--hub-url must include a host")

    health = _get_json(hub_url, "/api/hub/v1/health", timeout=timeout)
    _require(health.get("ok") is True, "health endpoint did not return ok=true")
    _require(health.get("service") == "main-computer-hub", "health endpoint is not the Main Computer hub")

    status = _get_json(hub_url, "/api/hub/v1/status", timeout=timeout)
    _require(isinstance(status, dict), "status endpoint did not return an object")

    indexer = _get_json(hub_url, "/api/hub/v1/credits/indexer", timeout=timeout)
    _require(indexer.get("ok") is True, "credit indexer status did not return ok=true")

    security = status.get("security") if isinstance(status.get("security"), dict) else {}
    return {
        "ok": True,
        "hub_url": hub_url.rstrip("/"),
        "health": {
            "service": health.get("service"),
            "api_version": health.get("api_version"),
            "security_profile": health.get("security_profile"),
        },
        "status": {
            "api_version": status.get("api_version"),
            "worker_count": status.get("worker_count"),
            "available_worker_count": status.get("available_worker_count"),
            "stale_worker_count": status.get("stale_worker_count"),
            "high_security_default": security.get("high_security_default"),
            "transport": security.get("transport"),
            "allow_insecure_dev_network": security.get("allow_insecure_dev_network"),
        },
        "indexer": {
            "phase": indexer.get("phase"),
            "mode": indexer.get("mode"),
            "rpc_sync_supported": indexer.get("rpc_sync_supported"),
            "credit_card_supported": indexer.get("credit_card_supported"),
            "request_charging_supported": indexer.get("request_charging_supported"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a deployed Main Computer remote hub.")
    parser.add_argument(
        "--hub-url",
        default=os.environ.get("MAIN_COMPUTER_HUB_URL", DEFAULT_HUB_URL),
        help="Remote hub base URL. Defaults to MAIN_COMPUTER_HUB_URL or local hub.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--require-https",
        action="store_true",
        help="Fail unless --hub-url uses https://. Use this for Coolify/domain smoke tests.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        result = run_smoke(hub_url=args.hub_url, timeout=max(1.0, args.timeout), require_https=args.require_https)
    except SmokeError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"Remote hub smoke failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Remote hub smoke passed.")
        print(f"  hub_url: {result['hub_url']}")
        print(f"  api_version: {result['health'].get('api_version')}")
        print(f"  security_profile: {result['health'].get('security_profile')}")
        print(f"  high_security_default: {result['status'].get('high_security_default')}")
        print(f"  allow_insecure_dev_network: {result['status'].get('allow_insecure_dev_network')}")
        print(f"  worker_count: {result['status'].get('worker_count')}")
        print(f"  available_worker_count: {result['status'].get('available_worker_count')}")
        print(f"  indexer_phase: {result['indexer'].get('phase')}")
        print(f"  indexer_mode: {result['indexer'].get('mode')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
