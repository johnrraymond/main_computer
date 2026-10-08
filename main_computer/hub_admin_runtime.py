"""Hub's own genesis-funded operator wallet (not the bridge-controller signer)."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from tools.mother.common.ethereum_identity import private_key_to_address

HUB_ADMIN_BUNDLE_ENV = "MAIN_COMPUTER_HUB_ADMIN_BUNDLE_B64"
HUB_ADMIN_BUNDLE_SCHEMA = "main-computer.hub-admin-wallet.v1"


def install_hub_admin_bundle(*, hub_id: str, network: str, runtime_dir: Path) -> dict[str, Any] | None:
    """Validate signer material at runtime; persist it with restrictive permissions.

    Return only a public attestation, never the private key or encoded bundle.
    """
    encoded = str(os.environ.get(HUB_ADMIN_BUNDLE_ENV) or "").strip()
    if not encoded:
        return None
    try:
        data = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
        if not isinstance(data, dict) or data.get("schema") != HUB_ADMIN_BUNDLE_SCHEMA:
            raise ValueError("unsupported Hub admin bundle schema")
        if data.get("hub_id") != hub_id or data.get("network") != network:
            raise ValueError("Hub admin bundle does not belong to this Hub/network")
        address = str(data["address"])
        key = str(data["private_key"])
        derived = private_key_to_address(key)
        if address.lower() != derived.lower():
            raise ValueError("Hub admin address does not match the signing key")
        destination = runtime_dir / "private" / "hub-admin" / "admin-wallet.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".tmp")
        temp.write_text(json.dumps({"address": derived, "private_key": key}, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(destination)
        os.environ.pop(HUB_ADMIN_BUNDLE_ENV, None)
        return {"address": derived, "wallet_loaded": True}
    except Exception as exc:
        raise RuntimeError(f"Hub administrator bundle is unusable: {type(exc).__name__}") from exc
