#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from urllib.request import urlopen


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:41414/healthz"
    with urlopen(url, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return 0 if payload.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
