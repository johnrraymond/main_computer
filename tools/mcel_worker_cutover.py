"""Immediately promote or roll back only the Worker browser route.

Promoted: /applications/worker serves the native MCEL Worker in the MCEL shell.
Legacy: original SPA serves the same canonical URL. The existing service,
backend, keys, and active Work Now jobs are deliberately not modified.
"""
from __future__ import annotations

import argparse
from urllib.request import Request, urlopen

from main_computer.mcel_worker_route_promotion import (
    ROOT, canonical_worker_mode, set_canonical_worker_mode,
)
from main_computer.mcel_worker_application import render_worker_page

CANDIDATE = "http://127.0.0.1:8765/applications/mcel-semantic-worker"


def preflight() -> None:
    # Compile locally before touching the route. This does not write settings,
    # register an offer, sign a wallet request, or spend credits.
    html = render_worker_page(ROOT, canonical=True)
    if ('data-mcel-worker-phase="promoted"' not in html or
            'McelSemanticWorkerPorts.create' not in html or
            'function initWorkerWalletOnly()' not in html):
        raise RuntimeError("MCEL Worker canonical compilation failed")
    # Verify the currently running host is actually serving the native
    # candidate, not an unrelated service or an already-failed app page.
    with urlopen(Request(CANDIDATE, headers={"Accept": "text/html"}), timeout=8) as response:
        document = response.read(3_000_000).decode("utf-8", "replace")
        if response.status != 200 or 'data-mcel-worker-phase="candidate"' not in document:
            raise RuntimeError("Running local MCEL Worker candidate is not healthy")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("promote", "rollback", "status"))
    args = parser.parse_args(argv)
    if args.action == "status":
        print("WORKER_CANONICAL_ROUTE", canonical_worker_mode().upper())
        return 0
    if args.action == "rollback":
        set_canonical_worker_mode("legacy")
        print("WORKER_CANONICAL_ROUTE LEGACY rollback-applied next-request")
        return 0
    try:
        preflight()
    except Exception as exc:
        print("WORKER_CANONICAL_ROUTE REFUSED preflight=" + type(exc).__name__)
        return 2
    set_canonical_worker_mode("promoted")
    print("WORKER_CANONICAL_ROUTE PROMOTED next-request backend-unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
