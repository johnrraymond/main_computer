#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_STATE_FILE = Path("runtime/deployments/current.json")
LEGACY_STATE_FILE = Path("runtime/dev-chain/latest.json")

DEFAULT_DEV_OFFICES = [
    {
        "office": "O0",
        "title": "Captain",
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "private_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    },
    {
        "office": "O1",
        "title": "First Officer",
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "private_key": "0x59c6995e998f97a5a0044966f094538c9e4361d023d65a14d6007a1df0863d9",
    },
    {
        "office": "O2",
        "title": "Second Officer",
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "private_key": "0x5de4111afa1a4b582f56a49c1b5f05b7ec3a943b11f071d72da14ef03ea64d35",
    },
    {
        "office": "O3",
        "title": "Third Officer",
        "address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        "private_key": "0x7c85211829461a5d643dad689a8271d5cf81952292a5c7e8929659f978a1d6b2",
    },
]

ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
BYTES32_RE = re.compile(r"0x[0-9a-fA-F]{64}")


def smoke_id(value: str | None = None) -> str:
    """Return a deterministic bytes32 id for wallet smoke tests.

    Explicit 32-byte hex ids are preserved. Other labels are hashed so operators
    can pass friendly smoke labels without risking malformed calldata.
    """

    raw = str(value or "main-computer-wallet-smoke").strip()
    if BYTES32_RE.fullmatch(raw):
        return raw
    return "0x" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_address(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if ADDRESS_RE.fullmatch(text) else None


def office_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return O0-O3 office records, falling back to Anvil's default dev keys."""

    offices = state.get("offices")
    if not isinstance(offices, list) or not offices:
        return [dict(item) for item in DEFAULT_DEV_OFFICES]

    records: list[dict[str, Any]] = []
    for index, raw in enumerate(offices[:4]):
        if not isinstance(raw, dict):
            continue
        fallback = DEFAULT_DEV_OFFICES[index] if index < len(DEFAULT_DEV_OFFICES) else {}
        address = normalize_address(raw.get("address")) or normalize_address(raw.get("account")) or fallback.get("address")
        record = {
            "office": str(raw.get("office") or fallback.get("office") or f"O{index}"),
            "title": str(raw.get("title") or fallback.get("title") or f"Office {index}"),
            "address": address,
        }
        private_key = str(raw.get("private_key") or raw.get("privateKey") or fallback.get("private_key") or "").strip()
        if private_key:
            record["private_key"] = private_key
        records.append(record)

    while len(records) < 4:
        records.append(dict(DEFAULT_DEV_OFFICES[len(records)]))
    return records


def deployment_address(state: dict[str, Any], *names: str) -> str | None:
    lowered_names = {name.lower() for name in names}
    for container_name in ("contracts", "deployments"):
        container = state.get(container_name)
        if not isinstance(container, dict):
            continue
        for key, value in container.items():
            key_text = str(key).lower()
            target = ""
            address: Any = value
            if isinstance(value, dict):
                target = str(value.get("target") or value.get("contract") or value.get("name") or "").lower()
                address = value.get("address")
            if key_text in lowered_names or any(name in target for name in lowered_names):
                found = normalize_address(address)
                if found:
                    return found
    return None


def chain_rpc_url(state: dict[str, Any]) -> str:
    chain = state.get("chain") if isinstance(state.get("chain"), dict) else {}
    return str(chain.get("rpc_url") or chain.get("host_rpc_url") or "http://127.0.0.1:18545")


def chain_id(state: dict[str, Any]) -> Any:
    chain = state.get("chain") if isinstance(state.get("chain"), dict) else {}
    return chain.get("chain_id", 42424242)


def redact_private_key(value: str) -> str:
    text = str(value or "")
    if len(text) <= 12:
        return "<hidden>"
    return text[:8] + "…" + text[-4:]


def render_guide(state: dict[str, Any], *, smoke: str, reveal_keys: bool = False) -> str:
    reserve = deployment_address(state, "xlag-bridge-reserve", "XLagBridgeReserve")
    offices = office_records(state)
    lines = [
        "Native ENG wallet smoke guide",
        "=============================",
        "",
        f"RPC URL: {chain_rpc_url(state)}",
        f"Chain ID: {chain_id(state)}",
        f"XLagBridgeReserve: {reserve or 'missing'}",
        f"Smoke ID: {smoke}",
        "",
        "Contract call:",
        "  finalizeWalletSmokeTest(bytes32,string)",
        "",
        "Suggested wallet payload:",
        f"  smokeId: {smoke}",
        "  note: local wallet smoke",
        "",
        "Offices:",
    ]
    for office in offices:
        key = str(office.get("private_key") or "")
        key_text = redact_private_key(key) if reveal_keys else "private keys hidden"
        lines.append(f"- {office.get('office')}: {office.get('title')} {office.get('address')} ({key_text})")
    lines.append("")
    lines.append("Use a browser wallet or cast send from the selected office account; this guide does not sign or send transactions.")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print a browser-wallet smoke-test guide for the local X-LAG reserve.")
    parser.add_argument("--state", default=None, help="Deployment state JSON. Defaults to current.json, then legacy latest.json.")
    parser.add_argument("--smoke-id", default=None, help="Explicit bytes32 id or label to hash into bytes32.")
    parser.add_argument("--show-private-keys", action="store_true", help="Display dev private keys. Hidden by default.")
    return parser


def default_state_path() -> Path:
    if DEFAULT_STATE_FILE.exists():
        return DEFAULT_STATE_FILE
    return LEGACY_STATE_FILE


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    state_path = Path(args.state) if args.state else default_state_path()
    try:
        state = load_state(state_path)
    except Exception as exc:  # noqa: BLE001 - operator diagnostic script
        print(f"ERROR: could not read deployment state {state_path}: {exc}")
        return 1

    print(render_guide(state, smoke=smoke_id(args.smoke_id), reveal_keys=args.show_private_keys))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
