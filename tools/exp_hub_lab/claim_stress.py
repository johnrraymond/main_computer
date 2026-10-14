"""Isolated, real-FoundationDB, cross-process settlement-ownership stress test.

This harness neither broadcasts chain transactions nor touches the configured
Experimental namespace. Every invocation generates a unique FDB namespace.
Run from repository root, with a live FoundationDB cluster and native client.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import random
import sys
from typing import Any
from uuid import uuid4

from main_computer.exp_fdb_credit_ledger import (
    ExperimentalFoundationDbConfig,
    ExperimentalFoundationDbCreditLedger,
)

_store: ExperimentalFoundationDbCreditLedger | None = None


def _initialize(cluster_file: str, namespace: str, repo_root: str) -> None:
    global _store
    _store = ExperimentalFoundationDbCreditLedger(
        ExperimentalFoundationDbConfig(
            cluster_file=Path(cluster_file), namespace=namespace, repo_root=Path(repo_root)
        )
    )


def _attempt(task: tuple[int, str, str, str]) -> dict[str, Any]:
    if _store is None:
        raise RuntimeError("Stress worker has no initialized FDB ledger")
    iteration, earning_id, kind, wallet = task
    key = f"stress-{iteration}"
    try:
        if kind == "claim":
            outcome = _store.record_worker_claim(
                worker_node_id="p2b-stress-worker", earning_ids=[earning_id], idempotency_key=key
            )
            owned = outcome.get("claim") is not None and not outcome.get("idempotent")
            ref = (outcome.get("claim") or {}).get("claim_id", "")
        else:
            outcome = _store.request_bridge_payout(
                wallet_address=wallet, worker_node_id="p2b-stress-worker",
                earning_ids=[earning_id], idempotency_key=key,
            )
            owned = not outcome.get("idempotent")
            ref = (outcome.get("payout") or {}).get("payout_id", "")
        return {"iteration": iteration, "kind": kind, "accepted": bool(owned), "ref": ref}
    except (ValueError, KeyError) as exc:
        return {"iteration": iteration, "kind": kind, "accepted": False, "rejected": str(exc)}


def _seed(store: ExperimentalFoundationDbCreditLedger, request_id: str) -> str:
    # One credit per earning; unique request IDs preserve real ledger semantics.
    store.issue(account_id="p2b-stress-requester", credits=1)
    hold = store.create_hold(account_id="p2b-stress-requester", request_id=request_id, credits=1)
    charge = store.charge_hold(
        hold_id=hold["hold"]["hold_id"], charged_credits=1,
        worker_node_id="p2b-stress-worker",
    )
    return charge["worker_earning"]["earning_id"]


def _owners(store: ExperimentalFoundationDbCreditLedger, earning_id: str) -> list[dict[str, str]]:
    snapshot = store._snapshot()
    result = []
    for claim in snapshot["worker_claims"]:
        if earning_id in claim.get("earning_ids", []) and claim.get("status") in {"claimed", "settled"}:
            result.append({"type": "claim", "id": claim["claim_id"]})
    for payout in snapshot["bridge_payouts"]:
        if earning_id in payout.get("earning_ids", []):
            result.append({"type": "bridge", "id": payout["payout_id"]})
    return result


def run(*, cluster_file: Path, repo_root: Path, clients: int, attempts: int, rounds: int, seed: int) -> dict[str, Any]:
    cluster_file = cluster_file.resolve()
    if not cluster_file.is_file():
        raise FileNotFoundError(f"FDB cluster file not found: {cluster_file}")
    namespace = f"mc-exp-claim-stress-{uuid4().hex}"
    store = ExperimentalFoundationDbCreditLedger(
        ExperimentalFoundationDbConfig(cluster_file=cluster_file, namespace=namespace, repo_root=repo_root)
    )
    wallet = "0x" + "ab" * 20
    rng = random.Random(seed)
    results: list[dict[str, Any]] = []
    # Spawned processes each open their own FDB connection; FDB conflict
    # detection, not an in-process lock, decides competing transactions.
    with ProcessPoolExecutor(
        max_workers=clients, initializer=_initialize,
        initargs=(str(cluster_file), namespace, str(repo_root)),
    ) as pool:
        for round_idx in range(rounds):
            eid = _seed(store, f"stress-{round_idx}-{uuid4().hex}")
            tasks = [(round_idx * attempts + i, eid, "claim" if rng.randrange(2) else "bridge", wallet)
                     for i in range(attempts)]
            rng.shuffle(tasks)
            futures = [pool.submit(_attempt, task) for task in tasks]
            outcomes = [future.result() for future in as_completed(futures)]
            owners = _owners(store, eid)
            new_accepted = [outcome for outcome in outcomes if outcome["accepted"]]
            good = len(new_accepted) == 1 and len(owners) == 1 and (
                new_accepted[0]["ref"] == owners[0]["id"] if new_accepted and owners else False
            )
            results.append({
                "round": round_idx, "earning_id": eid, "ok": good,
                "success_count": len(new_accepted), "owner_records": owners,
                "rejection_count": len(outcomes) - len(new_accepted),
                "unexpected_results": [x for x in outcomes if x.get("accepted") and not x.get("ref")],
            })
    return {
        "ok": all(row["ok"] for row in results),
        "backend": "foundationdb", "isolation": "new_namespace_per_invocation",
        "namespace": namespace, "cluster_file": str(cluster_file),
        "clients": clients, "attempts_per_round": attempts, "rounds": rounds,
        "seed": seed, "round_results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster-file", default=".foundationdb/docker.cluster")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--clients", type=int, default=16)
    parser.add_argument("--attempts", type=int, default=64)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--seed", type=int, default=424242)
    parser.add_argument("--report-path", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not (1 <= args.clients <= 128 and 2 <= args.attempts <= 10000 and 1 <= args.rounds <= 100):
        parser.error("clients must be 1..128, attempts 2..10000, rounds 1..100")
    try:
        report = run(
            cluster_file=Path(args.cluster_file), repo_root=Path(args.repo_root).resolve(),
            clients=args.clients, attempts=args.attempts, rounds=args.rounds, seed=args.seed,
        )
    except Exception as exc:
        report = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if args.report_path:
        path = Path(args.report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2 if args.json else None))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
