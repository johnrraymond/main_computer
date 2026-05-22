from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


@dataclass
class EnergyChainClient:
    rpc_url: str | None
    expected_chain_id: int | None = None
    timeout_s: float = 2.0
    rpc_url_source: str = "default"
    expected_chain_id_source: str = "default"

    def status(self) -> dict[str, Any]:
        metadata = self._status_metadata()
        if not self.rpc_url:
            return {
                "enabled": False,
                "connected": False,
                "rpc_url": None,
                "chain_id": None,
                "block_number": None,
                "peer_count": None,
                "peer_count_error": None,
                "expected_chain_id": self.expected_chain_id,
                "chain_id_ok": None,
                "error": None,
                **metadata,
            }

        try:
            chain_id = self._hex_to_int(self._rpc("eth_chainId"))
            block_number = self._hex_to_int(self._rpc("eth_blockNumber"))
        except Exception as exc:
            return {
                "enabled": True,
                "connected": False,
                "rpc_url": self.rpc_url,
                "chain_id": None,
                "block_number": None,
                "peer_count": None,
                "peer_count_error": None,
                "expected_chain_id": self.expected_chain_id,
                "chain_id_ok": False,
                "error": str(exc),
                **metadata,
            }

        peer_count = None
        peer_count_error = None
        try:
            peer_count = self._hex_to_int(self._rpc("net_peerCount"))
        except Exception as exc:
            peer_count_error = str(exc)

        return {
            "enabled": True,
            "connected": True,
            "rpc_url": self.rpc_url,
            "chain_id": chain_id,
            "block_number": block_number,
            "peer_count": peer_count,
            "peer_count_error": peer_count_error,
            "expected_chain_id": self.expected_chain_id,
            "chain_id_ok": self.expected_chain_id is None or chain_id == self.expected_chain_id,
            "error": None,
            **metadata,
        }

    def _status_metadata(self) -> dict[str, Any]:
        defaults_used: list[str] = []
        if self.rpc_url_source.startswith("default"):
            defaults_used.append("rpc_url")
        if self.expected_chain_id_source.startswith("default"):
            defaults_used.append("expected_chain_id")
        return {
            "using_defaults": bool(defaults_used),
            "defaults_used": defaults_used,
            "config_source": {
                "rpc_url": self.rpc_url_source,
                "expected_chain_id": self.expected_chain_id_source,
            },
        }

    def rpc(self, method: str, params: list[Any] | None = None) -> Any:
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}).encode("utf-8")
        request = Request(
            self.rpc_url or "",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_s) as response:
            data = json.loads(response.read().decode("utf-8"))
        if "error" in data:
            raise RuntimeError(data["error"])
        return data.get("result")

    def eth_call(self, to: str, data: str) -> str:
        return str(self.rpc("eth_call", [{"to": to, "data": data}, "latest"]))

    def get_code(self, address: str) -> str:
        return str(self.rpc("eth_getCode", [address, "latest"]))

    def get_balance(self, address: str) -> int:
        return self._hex_to_int(self.rpc("eth_getBalance", [address, "latest"]))

    def _rpc(self, method: str) -> Any:
        return self.rpc(method)

    @staticmethod
    def _hex_to_int(value: Any) -> int:
        if not isinstance(value, str):
            raise ValueError(f"Expected hex string result, got {type(value).__name__}")
        return int(value, 16)
