from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EnergyNode:
    node_id: str
    role: str
    endpoint: str
    status: str = "configured"
    guarded: bool = True


@dataclass(frozen=True)
class EnergyTransaction:
    tx_id: str
    kind: str
    node_id: str
    credits: int
    memo: str
    created_at: str


@dataclass(frozen=True)
class PendingEnergyPayout:
    payout_id: str
    kind: str
    node_id: str
    credits: int
    memo: str
    request_id: str
    created_at: str


class EnergyCreditLedger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "ledger.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        data = self._load()
        return {
            "network": data["network"],
            "head": data["head"],
            "nodes": data["nodes"],
            "balances": self._balances(data),
            "payout_queue": self._payout_queue_status(data),
            "transactions": data["transactions"][-25:],
        }

    def register_node(self, node_id: str, role: str, endpoint: str) -> dict[str, Any]:
        node_id = self._clean_id(node_id)
        data = self._load()
        if node_id == data["head"]["node_id"]:
            raise ValueError("The head node is already registered.")
        nodes = [node for node in data["nodes"] if node["node_id"] != node_id]
        nodes.append(asdict(EnergyNode(node_id=node_id, role=role.strip() or "worker", endpoint=endpoint.strip())))
        data["nodes"] = sorted(nodes, key=lambda node: node["node_id"])
        self._save(data)
        return self.status()

    def issue(self, node_id: str, credits: int, memo: str = "") -> dict[str, Any]:
        if credits <= 0:
            raise ValueError("Credits must be positive.")
        data = self._load()
        node_id = self._clean_id(node_id)
        self._ensure_node(data, node_id)
        tx = self._transaction("issue", node_id, credits, memo)
        data["transactions"].append(asdict(tx))
        self._save(data)
        return self.status()

    def queue_worker_payout(self, node_id: str, credits: int, memo: str = "", request_id: str = "") -> dict[str, Any]:
        """Queue a hub GPU-worker payout instead of settling immediately.

        The queue gives workers a private local tally they can inspect and claim
        in batches. The eventual on-chain bridge can aggregate these entries so
        individual prompts do not map one-to-one to public settlement events.
        """
        return self._queue_payout("hub_worker_payout_queued", node_id, credits, memo, request_id)

    def queue_upstream_hub_payout(self, node_id: str, credits: int, memo: str = "", request_id: str = "") -> dict[str, Any]:
        """Queue a payout to an upstream hub that fulfilled local hub work."""
        return self._queue_payout("hub_upstream_payout_queued", node_id, credits, memo, request_id)

    def payout_worker(self, node_id: str, credits: int, memo: str = "") -> dict[str, Any]:
        """Record an immediate hub GPU-worker payout.

        New hub flows use queue_worker_payout() and worker-initiated claims.
        This method remains for explicit admin settlement and older callers.
        """
        if credits <= 0:
            raise ValueError("Credits must be positive.")
        data = self._load()
        node_id = self._clean_id(node_id)
        self._ensure_node(data, node_id)
        tx = self._transaction("hub_worker_payout", node_id, credits, memo)
        data["transactions"].append(asdict(tx))
        self._save(data)
        return self.status()

    def payout_upstream_hub(self, node_id: str, credits: int, memo: str = "") -> dict[str, Any]:
        """Record an immediate payout to an upstream hub."""
        if credits <= 0:
            raise ValueError("Credits must be positive.")
        data = self._load()
        node_id = self._clean_id(node_id)
        self._ensure_node(data, node_id)
        tx = self._transaction("hub_upstream_payout", node_id, credits, memo)
        data["transactions"].append(asdict(tx))
        self._save(data)
        return self.status()

    def payout_summary(self, node_id: str) -> dict[str, Any]:
        data = self._load()
        node_id = self._clean_id(node_id)
        self._ensure_node(data, node_id)
        pending = [item for item in data["pending_payouts"] if item.get("node_id") == node_id]
        return {
            "ok": True,
            "node_id": node_id,
            "pending_credits": sum(int(item.get("credits", 0) or 0) for item in pending),
            "pending_count": len(pending),
            "pending_payouts": pending[-100:],
            "ledger": self.status(),
        }

    def claim_payouts(self, node_id: str, memo: str = "") -> dict[str, Any]:
        """Bridge all queued credits for a worker/upstream node as one claim."""
        data = self._load()
        node_id = self._clean_id(node_id)
        self._ensure_node(data, node_id)
        selected = [item for item in data["pending_payouts"] if item.get("node_id") == node_id]
        if not selected:
            return {
                "ok": True,
                "node_id": node_id,
                "claimed_credits": 0,
                "claimed_count": 0,
                "ledger": self.status(),
            }

        credits = sum(int(item.get("credits", 0) or 0) for item in selected)
        kinds = {str(item.get("kind", "")) for item in selected}
        if kinds == {"hub_upstream_payout_queued"}:
            kind = "hub_upstream_payout_claim"
        elif kinds == {"hub_worker_payout_queued"}:
            kind = "hub_worker_payout_claim"
        else:
            kind = "hub_payout_claim"
        memo_text = memo.strip() or f"claimed {len(selected)} queued hub payout(s)"
        tx = self._transaction(kind, node_id, credits, memo_text)
        data["transactions"].append(asdict(tx))
        selected_ids = {str(item.get("payout_id", "")) for item in selected}
        data["pending_payouts"] = [item for item in data["pending_payouts"] if str(item.get("payout_id", "")) not in selected_ids]
        self._save(data)
        return {
            "ok": True,
            "node_id": node_id,
            "claimed_credits": credits,
            "claimed_count": len(selected),
            "transaction": asdict(tx),
            "ledger": self.status(),
        }

    def spend(self, node_id: str, credits: int, memo: str = "") -> dict[str, Any]:
        if credits <= 0:
            raise ValueError("Credits must be positive.")
        data = self._load()
        node_id = self._clean_id(node_id)
        self._ensure_node(data, node_id)
        balances = self._balances(data)
        if balances.get(node_id, 0) < credits:
            raise ValueError("Insufficient local energy credits.")
        tx = self._transaction("spend", node_id, -credits, memo)
        data["transactions"].append(asdict(tx))
        self._save(data)
        return self.status()

    def record_compute_credit_reserve_payout(
        self,
        node_id: str,
        credits: int,
        memo: str = "",
        *,
        amount_base_units: int,
        recipient: str,
        contract_address: str,
        chain_id: int,
        proposal_id: int,
        tx_hashes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Append an audit-only record for a Compute Credits reserve payout.

        The local credit balance has already been reconciled by a matching
        spend() call before this audit record is written. This transaction
        therefore carries zero credits so balances are not changed twice, while
        the extra ``compute_credit_reserve`` payload keeps the on-chain
        settlement details machine-readable in ledger.json.
        """

        if credits <= 0:
            raise ValueError("Credits must be positive.")
        if amount_base_units <= 0:
            raise ValueError("Compute Credits amount must be positive.")
        data = self._load()
        node_id = self._clean_id(node_id)
        self._ensure_node(data, node_id)
        tx = self._transaction(
            "compute_credit_reserve_payout_executed",
            node_id,
            0,
            memo or f"reconciled {credits} local credit base units to reserve payout",
        )
        tx_data = asdict(tx)
        tx_data["compute_credit_reserve"] = {
            "credits_reconciled": int(credits),
            "amount_base_units": int(amount_base_units),
            "recipient": str(recipient),
            "contract_address": str(contract_address),
            "chain_id": int(chain_id),
            "proposal_id": int(proposal_id),
            "tx_hashes": dict(tx_hashes or {}),
        }
        data["transactions"].append(tx_data)
        self._save(data)
        return self.status()

    def record_native_eng_reserve_payout(
        self,
        node_id: str,
        credits: int,
        memo: str = "",
        *,
        amount_eng_wei: int,
        recipient: str,
        contract_address: str,
        chain_id: int,
        proposal_id: int,
        tx_hashes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Deprecated compatibility alias for pre-C0 reserve payout records."""
        return self.record_compute_credit_reserve_payout(
            node_id,
            credits,
            memo,
            amount_base_units=amount_eng_wei,
            recipient=recipient,
            contract_address=contract_address,
            chain_id=chain_id,
            proposal_id=proposal_id,
            tx_hashes=tx_hashes,
        )

    def _queue_payout(self, kind: str, node_id: str, credits: int, memo: str, request_id: str) -> dict[str, Any]:
        if credits <= 0:
            raise ValueError("Credits must be positive.")
        data = self._load()
        node_id = self._clean_id(node_id)
        self._ensure_node(data, node_id)
        created_at = datetime.now(tz=timezone.utc).isoformat()
        clean_request_id = str(request_id or "").strip()
        seed = f"{kind}|{node_id}|{credits}|{memo}|{clean_request_id}|{created_at}".encode("utf-8")
        payout = PendingEnergyPayout(
            payout_id="payout_" + hashlib.sha256(seed).hexdigest()[:20],
            kind=kind,
            node_id=node_id,
            credits=credits,
            memo=memo.strip(),
            request_id=clean_request_id,
            created_at=created_at,
        )
        data["pending_payouts"].append(asdict(payout))
        self._save(data)
        return self.status()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return self._normalize(data)
        return self._normalize({})

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(self._normalize(data), ensure_ascii=False, indent=2), encoding="utf-8")

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        created_at = datetime.now(tz=timezone.utc).isoformat()
        network = data.get("network") if isinstance(data.get("network"), dict) else {}
        head = data.get("head") if isinstance(data.get("head"), dict) else {}
        pending_payouts = data.get("pending_payouts") if isinstance(data.get("pending_payouts"), list) else []
        normalized_pending: list[dict[str, Any]] = []
        for item in pending_payouts:
            if not isinstance(item, dict):
                continue
            try:
                credits = int(item.get("credits", 0) or 0)
            except (TypeError, ValueError):
                continue
            node_id = self._clean_id(str(item.get("node_id", "")))
            if credits <= 0:
                continue
            payout_id = str(item.get("payout_id") or "")
            if not payout_id:
                seed = f"{item.get('kind', '')}|{node_id}|{credits}|{item.get('memo', '')}|{item.get('created_at', created_at)}"
                payout_id = "payout_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
            normalized_pending.append(
                {
                    "payout_id": payout_id,
                    "kind": str(item.get("kind") or "hub_worker_payout_queued"),
                    "node_id": node_id,
                    "credits": credits,
                    "memo": str(item.get("memo") or ""),
                    "request_id": str(item.get("request_id") or ""),
                    "created_at": str(item.get("created_at") or created_at),
                }
            )
        return {
            "network": {
                "name": str(network.get("name", "main-computer-local-energy")),
                "chain": str(network.get("chain", "local-ethereum-style")),
                "currency": str(network.get("currency", "Compute Credits")),
                "created_at": str(network.get("created_at", created_at)),
            },
            "head": {
                "node_id": str(head.get("node_id", "main-computer-head")),
                "role": "head",
                "endpoint": str(head.get("endpoint", "local://main-computer")),
                "guarded": True,
            },
            "nodes": list(data.get("nodes", [])) if isinstance(data.get("nodes", []), list) else [],
            "pending_payouts": sorted(normalized_pending, key=lambda item: item["created_at"]),
            "transactions": list(data.get("transactions", [])) if isinstance(data.get("transactions", []), list) else [],
        }

    def _balances(self, data: dict[str, Any]) -> dict[str, int]:
        balances: dict[str, int] = {data["head"]["node_id"]: 0}
        for node in data["nodes"]:
            balances[str(node["node_id"])] = 0
        for tx in data["transactions"]:
            node_id = str(tx.get("node_id", ""))
            balances[node_id] = balances.get(node_id, 0) + int(tx.get("credits", 0))
        return balances

    def _payout_queue_status(self, data: dict[str, Any]) -> dict[str, Any]:
        balances: dict[str, int] = {}
        counts: dict[str, int] = {}
        for item in data["pending_payouts"]:
            node_id = str(item.get("node_id", ""))
            credits = int(item.get("credits", 0) or 0)
            balances[node_id] = balances.get(node_id, 0) + credits
            counts[node_id] = counts.get(node_id, 0) + 1
        return {
            "pending_count": len(data["pending_payouts"]),
            "balances": balances,
            "counts": counts,
            "recent": data["pending_payouts"][-25:],
            "settlement": "batched-worker-claim",
        }

    def _ensure_node(self, data: dict[str, Any], node_id: str) -> None:
        if node_id == data["head"]["node_id"]:
            return
        if not any(node.get("node_id") == node_id for node in data["nodes"]):
            raise ValueError(f"Unknown energy node: {node_id}")

    def _transaction(self, kind: str, node_id: str, credits: int, memo: str) -> EnergyTransaction:
        created_at = datetime.now(tz=timezone.utc).isoformat()
        seed = f"{kind}|{node_id}|{credits}|{memo}|{created_at}".encode("utf-8")
        tx_id = hashlib.sha256(seed).hexdigest()[:16]
        return EnergyTransaction(
            tx_id=f"0x{tx_id}",
            kind=kind,
            node_id=node_id,
            credits=credits,
            memo=memo.strip(),
            created_at=created_at,
        )

    def _clean_id(self, node_id: str) -> str:
        clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in node_id.strip().lower())
        if not clean:
            raise ValueError("Node id is required.")
        return clean
