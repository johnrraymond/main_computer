from __future__ import annotations

import json
import sys
from urllib.error import URLError
from urllib.request import urlopen


ENDPOINTS = [
    "http://0.0.0.0:18080/",
    "http://0.0.0.0:18080/api/site/status",
    "http://0.0.0.0:18080/api/hub/status",
    "http://0.0.0.0:18081/",
    "http://0.0.0.0:18081/api/site/status",
    "http://0.0.0.0:18082/",
    "http://0.0.0.0:18082/api/site/status",
    "http://0.0.0.0:18082/api/hub/status",
    "http://0.0.0.0:18083/",
    "http://0.0.0.0:18083/api/site/status",
]


def check_url(url: str) -> bool:
    try:
        with urlopen(url, timeout=8) as response:
            body = response.read(4096)
            if response.status != 200:
                print(f"[FAIL] {url} status={response.status}")
                return False
            if url.endswith("/api/site/status") or url.endswith("/api/hub/status"):
                payload = json.loads(body.decode("utf-8"))
                if not payload.get("ok"):
                    print(f"[FAIL] {url} payload not ok")
                    return False
            print(f"[PASS] {url}")
            return True
    except (OSError, URLError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {url} {exc}")
        return False


def main() -> int:
    ok = all(check_url(url) for url in ENDPOINTS)
    if ok:
        print("PASS: local-platform direct-port endpoints are healthy.")
        return 0
    print("FAIL: one or more local-platform endpoints failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
