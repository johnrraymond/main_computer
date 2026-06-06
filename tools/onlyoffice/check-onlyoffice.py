#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.request
from urllib.parse import urlparse


def probe(url: str, timeout: float) -> dict[str, object]:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "main-computer-onlyoffice-check"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read(512)
            return {
                "ok": 200 <= int(response.status) < 400,
                "url": url,
                "status": int(response.status),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "bytes": len(content),
                "error": "",
            }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "status": getattr(exc, "code", None),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "bytes": 0,
            "error": str(exc),
        }


def port_open(url: str, timeout: float) -> dict[str, object]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "ok": True,
                "host": host,
                "port": port,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": "",
            }
    except OSError as exc:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }


def collect_checks(base: str, timeout: float) -> dict[str, dict[str, object]]:
    return {
        "socket": port_open(base, timeout),
        "root": probe(base + "/", timeout),
        "healthcheck": probe(base + "/healthcheck", timeout),
        "editor_api": probe(base + "/web-apps/apps/api/documents/api.js", timeout),
    }


def checks_ready(checks: dict[str, dict[str, object]]) -> bool:
    return bool(
        checks["socket"]["ok"]
        and checks["healthcheck"]["ok"]
        and checks["editor_api"]["ok"]
    )


def print_checks(base: str, checks: dict[str, dict[str, object]], prefix: str = "") -> None:
    print(f"{prefix}ONLYOFFICE check: {base}")
    for name, result in checks.items():
        status = result.get("status")
        state = "ok" if result.get("ok") else "fail"
        extra = ""
        if "bytes" in result:
            extra = f" bytes={result.get('bytes')}"
        print(f"{prefix}  {name}: {state} status={status}{extra} error={result.get('error') or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a local ONLYOFFICE Docs endpoint.")
    parser.add_argument("--url", default="http://127.0.0.1:18085", help="ONLYOFFICE public URL")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--wait-seconds", type=float, default=0.0, help="poll until ready for this many seconds")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="delay between readiness polls")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    deadline = time.monotonic() + max(0.0, args.wait_seconds)
    attempts: list[dict[str, object]] = []
    attempt = 0

    while True:
        attempt += 1
        checks = collect_checks(base, args.timeout)
        ok = checks_ready(checks)
        payload = {"ok": ok, "base_url": base, "checks": checks, "attempt": attempt}
        attempts.append(payload)

        if ok or time.monotonic() >= deadline or args.wait_seconds <= 0:
            break

        if not args.json:
            print_checks(base, checks, prefix=f"[wait attempt {attempt}] ")
            print(f"[wait attempt {attempt}] ONLYOFFICE is not ready yet; retrying in {args.poll_seconds:g}s.")
        time.sleep(max(0.1, args.poll_seconds))

    final_payload = attempts[-1]
    if args.json:
        print(json.dumps({**final_payload, "attempts": attempts}, indent=2))
    else:
        print_checks(base, final_payload["checks"])  # type: ignore[arg-type]
        if final_payload["ok"]:
            print(f"ONLYOFFICE ready after {final_payload['attempt']} check attempt(s).")
        elif args.wait_seconds > 0:
            print(f"ONLYOFFICE did not become ready within {args.wait_seconds:g} seconds.")

    return 0 if final_payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
