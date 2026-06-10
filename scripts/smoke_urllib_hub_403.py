# test_hub_status_headers.py
from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


URL = "https://mainnet-hub.greatlibrary.io/api/hub/status"


def try_request(label: str, headers: dict[str, str] | None = None) -> None:
    print(f"\n=== {label} ===")
    req = Request(URL, headers=headers or {})

    try:
        with urlopen(req, timeout=10) as response:
            body = response.read(800).decode("utf-8", errors="replace")
            print("status:", response.status)
            print("content-type:", response.headers.get("content-type"))
            print("body:", body)

            try:
                parsed = json.loads(body)
                print("json ok:", parsed.get("ok"))
                print("network:", parsed.get("network_key") or parsed.get("network", {}).get("key"))
            except Exception:
                print("json parse: skipped")

    except HTTPError as exc:
        body = exc.read(800).decode("utf-8", errors="replace")
        print("HTTPError:", exc.code, exc.reason)
        print("headers:")
        for key, value in exc.headers.items():
            print(f"  {key}: {value}")
        print("body:", body)

    except URLError as exc:
        print("URLError:", exc)

    except Exception as exc:
        print(type(exc).__name__ + ":", exc)


def main() -> int:
    try_request("plain urllib")

    try_request(
        "worker headers",
        {
            "User-Agent": "MainComputerWorker/0.1",
            "Accept": "application/json",
        },
    )

    try_request(
        "browser-like headers",
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        },
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())