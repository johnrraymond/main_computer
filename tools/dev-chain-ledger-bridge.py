#!/usr/bin/env python3
"""
Bridge a local energy-payout entitlement into a native ENG reserve payout.

Run after a successful soft dev-chain deploy/smoke/flow:

  python .\\dev-chain-ledger-bridge.py --register-node --node-id gpu-worker-01 --queue-eng 0.125

This script treats ENG as native to the energy chain:

  1 ENG = 10^18 base units

The EnergyCreditLedger still stores integer "credits"; this bridge records those
credits as native ENG base units for settlement. It intentionally does not deploy
an ENG token contract.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from main_computer.energy import EnergyCreditLedger
from main_computer.prod_lock import require_unlocked_production_state


DEFAULT_NODE_ID = "gpu-worker-01"
DEFAULT_NODE_ROLE = "gpu-worker"
DEFAULT_ENDPOINT = "local://gpu-worker-01"
DEFAULT_QUEUE_ENG = "0.125"
DEFAULT_FUND_ENG = "1"
DEFAULT_DEPLOYMENT_FILE = Path("runtime/deployments/current.json")
LEGACY_DEV_CHAIN_STATE_FILE = Path("runtime/dev-chain/latest.json")
DEFAULT_STATE_FILE = DEFAULT_DEPLOYMENT_FILE
DEFAULT_REPORT_FILE = Path("runtime/dev-chain/ledger-bridge-latest.json")


def log(message: str = "") -> None:
    print(message, flush=True)


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "docker-compose.dev.yml").exists()
            or (candidate / ".git").exists()
        ):
            return candidate
    return current


def resolve_state_file(root: Path, requested: Path) -> Path:
    """Resolve the deployment state path used by the ledger bridge.

    Prefer the production-shaped deployment publication. Keep the legacy
    dev-chain latest.json as a compatibility fallback when the new publication
    has not been written yet.
    """

    path = requested if requested.is_absolute() else root / requested
    if requested == DEFAULT_DEPLOYMENT_FILE and not path.exists():
        legacy_path = root / LEGACY_DEV_CHAIN_STATE_FILE
        if legacy_path.exists():
            return legacy_path
    return path


def load_dev_chain_flow(repo: Path):
    script = repo / "dev-chain-flow.py"
    if not script.exists():
        raise FileNotFoundError(f"missing {script}")
    spec = importlib.util.spec_from_file_location("dev_chain_flow_runtime", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def latest_balance(status: dict[str, Any], node_id: str) -> int:
    balances = status.get("balances", {})
    if not isinstance(balances, dict):
        return 0
    return int(balances.get(node_id, 0) or 0)


def recent_transaction(status: dict[str, Any], kind: str) -> dict[str, Any] | None:
    transactions = status.get("transactions", [])
    if not isinstance(transactions, list):
        return None
    for tx in reversed(transactions):
        if isinstance(tx, dict) and tx.get("kind") == kind:
            return tx
    return None


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log()
    log(f"Wrote report: {path}")


def run_bridge(
    *,
    repo: Path,
    ledger_root: Path,
    state_file: Path,
    report_file: Path,
    node_id: str,
    node_role: str,
    endpoint: str,
    register_node: bool,
    queue_wei: int,
    fund_wei: int,
    recipient: str | None,
    memo: str,
    expected_chain_id: int | None,
    rpc_url: str | None,
    timeout_s: float,
    poll_s: float,
    rpc_func=None,
) -> tuple[bool, dict[str, Any]]:
    flow = load_dev_chain_flow(repo)
    state, env = flow.load_state(state_file)

    ledger = EnergyCreditLedger(ledger_root)
    clean_node_id = ledger._clean_id(node_id)  # local operator script; keep bridge target canonical.

    if register_node:
        ledger.register_node(clean_node_id, node_role, endpoint)

    before_status = ledger.status()
    before_balance = latest_balance(before_status, clean_node_id)

    ledger.queue_worker_payout(
        clean_node_id,
        queue_wei,
        memo=f"native ENG reserve bridge intent: {flow.format_eng(queue_wei)}",
        request_id=f"native-eng-{dt.datetime.now(dt.UTC).isoformat()}",
    )
    queued_status = ledger.status()

    claim = ledger.claim_payouts(
        clean_node_id,
        memo=f"claim native ENG reserve payout entitlement: {flow.format_eng(queue_wei)}",
    )
    claimed_credits = int(claim.get("claimed_credits", 0) or 0)
    if claimed_credits != queue_wei:
        raise RuntimeError(f"claimed {claimed_credits} credits, expected {queue_wei}")

    ok, chain_summary, chain_steps = flow.run_flow(
        state=state,
        env=env,
        rpc_url=rpc_url,
        expected_chain_id=expected_chain_id,
        fund_wei=fund_wei,
        payout_wei=queue_wei,
        memo=memo,
        recipient=recipient,
        expires_blocks=100,
        mine_extra_blocks=1,
        timeout=timeout_s,
        poll_s=poll_s,
        rpc_func=rpc_func or flow.rpc,
    )

    if not ok:
        payload = {
            "ok": False,
            "reason": "native ENG reserve flow failed",
            "node_id": clean_node_id,
            "claimed_credits": claimed_credits,
            "chain_summary": chain_summary,
            "chain_steps": [step.__dict__ for step in chain_steps],
            "ledger_status": ledger.status(),
        }
        write_report(report_file, payload)
        return False, payload

    ledger.spend(
        clean_node_id,
        queue_wei,
        memo=f"reconcile local ENG claim to native reserve proposal {chain_summary['proposal_id']}",
    )
    after_spend_status = ledger.status()

    audit_status = ledger.record_native_eng_reserve_payout(
        clean_node_id,
        queue_wei,
        memo=f"native ENG reserve payout executed: proposal {chain_summary['proposal_id']}",
        amount_eng_wei=int(chain_summary["payout_wei"]),
        recipient=str(chain_summary["recipient"]),
        contract_address=str(chain_summary["reserve"]),
        chain_id=int(chain_summary["chain_id"]),
        proposal_id=int(chain_summary["proposal_id"]),
        tx_hashes=dict(chain_summary.get("transactions", {})),
    )

    after_balance = latest_balance(audit_status, clean_node_id)
    audit_tx = recent_transaction(audit_status, "native_eng_reserve_payout_executed")
    payload = {
        "ok": True,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "node_id": clean_node_id,
        "ledger_root": str(ledger_root),
        "state_file": str(state_file),
        "queue_wei": queue_wei,
        "queue_eng": flow.format_eng(queue_wei),
        "fund_wei": fund_wei,
        "fund_eng": flow.format_eng(fund_wei),
        "balance_before": before_balance,
        "balance_after": after_balance,
        "claimed_credits": claimed_credits,
        "chain_summary": chain_summary,
        "chain_steps": [step.__dict__ for step in chain_steps],
        "audit_transaction": audit_tx,
        "ledger": {
            "queued": queued_status,
            "claim": claim,
            "after_spend": after_spend_status,
            "after_audit": audit_status,
        },
    }
    write_report(report_file, payload)
    return True, payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge a local energy payout into a native ENG reserve payout.")
    parser.add_argument("--ledger-root", type=Path, default=Path("energy_credits"))
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_FILE)
    parser.add_argument("--node-id", default=DEFAULT_NODE_ID)
    parser.add_argument("--node-role", default=DEFAULT_NODE_ROLE)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--register-node", action="store_true")
    parser.add_argument("--queue-eng", default=DEFAULT_QUEUE_ENG, help="Native ENG payout entitlement to queue and settle.")
    parser.add_argument("--fund-eng", default=DEFAULT_FUND_ENG, help="Native ENG amount used to fund the reserve for this flow.")
    parser.add_argument("--recipient", default=None, help="Recipient address. Defaults to O3 from latest deploy state.")
    parser.add_argument("--memo", default="native ENG ledger bridge payout")
    parser.add_argument("--chain-id", type=int, default=None)
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--timeout-s", type=float, default=20.0)
    parser.add_argument("--poll-s", type=float, default=0.25)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo = find_repo_root(Path.cwd())

    ledger_root = args.ledger_root if args.ledger_root.is_absolute() else repo / args.ledger_root
    state_file = resolve_state_file(repo, args.state_file)
    report_file = args.report if args.report.is_absolute() else repo / args.report

    log(f"Repository root: {repo}")
    log(f"Ledger root: {ledger_root}")
    log(f"Deploy state: {state_file}")
    log()
    log("Native ENG ledger bridge")
    log("========================")

    try:
        require_unlocked_production_state(
            repo,
            ledger_root,
            state_file,
            report_file,
            action="run dev-chain ledger bridge",
        )
        flow = load_dev_chain_flow(repo)
        queue_wei = flow.parse_eng(args.queue_eng)
        fund_wei = flow.parse_eng(args.fund_eng)

        if queue_wei > fund_wei:
            parser.error("--queue-eng must be less than or equal to --fund-eng")

        ok, payload = run_bridge(
            repo=repo,
            ledger_root=ledger_root,
            state_file=state_file,
            report_file=report_file,
            node_id=args.node_id,
            node_role=args.node_role,
            endpoint=args.endpoint,
            register_node=args.register_node,
            queue_wei=queue_wei,
            fund_wei=fund_wei,
            recipient=args.recipient,
            memo=args.memo,
            expected_chain_id=args.chain_id,
            rpc_url=args.rpc_url,
            timeout_s=args.timeout_s,
            poll_s=args.poll_s,
        )
        if ok:
            log("PASS: local energy payout was reconciled through native ENG reserve execution.")
            return 0
        log(f"FAIL: bridge did not complete: {payload.get('reason', 'unknown error')}")
        return 1
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        log()
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
