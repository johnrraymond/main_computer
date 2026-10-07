#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    selector = os.environ.get("MAIN_COMPUTER_NANOJEV_CHECKPOINT", "champion").strip() or "champion"
    port = os.environ.get("MAIN_COMPUTER_NANOJEV_PORT", "9765").strip() or "9765"
    if selector == "unified-games-v1":
        command = [
            sys.executable,
            "/opt/nanojev/source/scripts/serve_decisions.py",
            "--checkpoint-dir",
            "/opt/nanojev",
            "--web-root",
            "/opt/nanojev-web",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--precision",
            "bf16",
        ]
    else:
        command = [
            sys.executable,
            "/opt/nanojev-clef-service/clef_service.py",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--repo-id",
            os.environ.get("MAIN_COMPUTER_NANOJEV_HF_REPO", "johnrraymond/NanoJev-CLEF"),
            "--revision",
            selector,
        ]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
