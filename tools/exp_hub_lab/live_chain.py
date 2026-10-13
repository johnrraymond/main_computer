"""Fail-closed local dev-chain preflight for opt-in Experimental Hub lab.

No chain writes are performed here. The actual end-to-end payout runs through
existing XLagBridgeReserve smoke code only after authenticated Docker work.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass(frozen=True)
class LocalDevChain:
    state_path: Path
    rpc_url: str
    chain_id: int
    escrow_address: str
    payout_contract_address: str

    def public_status(self) -> dict[str, object]:
        # Never include private key material from the deployment state.
        return {
            "state": str(self.state_path), "rpc": self.rpc_url,
            "chain_id": self.chain_id, "escrow": self.escrow_address,
            "payout_contract": self.payout_contract_address,
            "backend": "dev-chain", "scope": "local-development-only",
        }


def _contract_address(document: dict, key: str) -> str:
    contracts = document.get("contracts") or document.get("deployments") or {}
    raw = contracts.get(key) if isinstance(contracts, dict) else None
    address = raw.get("address") if isinstance(raw, dict) else raw
    if not isinstance(address, str) or ADDRESS_RE.fullmatch(address) is None:
        raise ValueError(f"local dev chain deployment missing valid {key} address")
    return address


def _rpc(url: str, method: str, params: list[object]) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as response:  # noqa: S310 - loopback-only URL checked by preflight
        result = json.load(response)
    if not isinstance(result, dict) or result.get("error") or "result" not in result:
        raise RuntimeError(f"local dev chain RPC {method} failed: {result!r}")
    return result["result"]


def preflight_local_dev_chain(state_path: Path, *, rpc_override: str = "") -> LocalDevChain:
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / state_path).resolve() if not state_path.is_absolute() else state_path.resolve()
    # No arbitrary signer/deployment files from outside the checkout.
    if not source.is_relative_to(repo_root.resolve()):
        raise ValueError("--live-chain-state must be inside the repository")
    if not source.is_file():
        raise ValueError(f"local dev chain deployment does not exist: {source}")
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "main-computer.deployment.v1" or data.get("environment") != "dev":
        raise ValueError("--live-chain requires a local dev deployment state (schema main-computer.deployment.v1; environment dev)")
    chain = data.get("chain")
    if not isinstance(chain, dict):
        raise ValueError("local dev deployment lacks chain metadata")
    chain_id = int(chain.get("chain_id") or 0)
    if chain_id != 42424242:
        raise ValueError("Experimental P1 live-session MSK lab requires local dev chain id 42424242")
    rpc_url = (rpc_override or str(chain.get("host_rpc_url") or chain.get("rpc_url") or "")).strip()
    parsed = urlparse(rpc_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or not parsed.port or (parsed.path not in {"", "/"}) or parsed.query or parsed.fragment:
        raise ValueError("--live-chain RPC must be an explicit loopback HTTP endpoint with port (no remote/mainnet RPC)")
    escrow = _contract_address(data, "hub_credit_bridge_escrow")
    payout = _contract_address(data, "xlag-bridge-reserve")
    try:
        observed_chain_id = int(str(_rpc(rpc_url, "eth_chainId", [])), 16)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid local chain id response") from exc
    if observed_chain_id != chain_id:
        raise RuntimeError(f"local chain id mismatch: deployment={chain_id} rpc={observed_chain_id}")
    for name, address in (("HubCreditBridgeEscrow", escrow), ("XLagBridgeReserve", payout)):
        code = str(_rpc(rpc_url, "eth_getCode", [address, "latest"])).lower()
        if not code.startswith("0x") or int(code[2:] or "0", 16) == 0:
            raise RuntimeError(f"{name} has no contract bytecode at {address} on local dev chain {chain_id}")
    if not isinstance(data.get("smoke_client"), dict) or not isinstance(data.get("hub_admin"), dict):
        raise ValueError("local dev deployment missing smoke-client/bridge-controller wallet metadata")
    return LocalDevChain(state_path=source, rpc_url=rpc_url, chain_id=chain_id,
                         escrow_address=escrow, payout_contract_address=payout)
