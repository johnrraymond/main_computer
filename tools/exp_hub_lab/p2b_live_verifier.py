"""Bounded subprocess entry for the existing Experimental full-E2E verifier.

Do not route this via run_lab.main(): its public default selects Stable Hub.
The parent lab must durably mark the payout outcome unknown before invoking
this command, which can send real LOCAL development-chain transactions.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Experimental local development-chain payout verification")
    parser.add_argument("--hub-urls", required=True)
    parser.add_argument("--dev-chain-state", required=True, type=Path)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--chain-id", required=True, type=int)
    parser.add_argument("--stress-requests", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--report-path", required=True, type=Path)
    # The integrated launcher requests structured output with --json.
    # This entrypoint already emits JSON unconditionally; accepting the
    # flag keeps the parent/child CLI contract explicit and compatible.
    parser.add_argument("--json", action="store_true", help="Emit structured JSON verification summary")
    args = parser.parse_args(argv)
    parsed_rpc = urlparse(args.rpc_url)
    if args.chain_id != 42424242 or parsed_rpc.scheme != "http" or parsed_rpc.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed_rpc.port or parsed_rpc.username or parsed_rpc.password:
        parser.error("P2-B lab permits only local development chain 42424242 over loopback RPC")
    from tools.exp_hub_lab.run_lab import verify_full_e2e
    try:
        report = verify_full_e2e(
            hub_urls=[url.strip() for url in args.hub_urls.split(",") if url.strip()],
            dev_chain_state=args.dev_chain_state, rpc_url=args.rpc_url,
            chain_id=args.chain_id, stress_requests=args.stress_requests,
            timeout=args.timeout, require_multisession_auth=True,
            report_path=args.report_path,
        )
    except Exception as exc:
        print(f"Experimental P2-B chain verifier failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": report.get("ok") is True, "report": str(args.report_path),
                      "dev_chain_tx_hash": report.get("dev_chain_tx_hash"),
                      "error": report.get("error")}, sort_keys=True), flush=True)
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
