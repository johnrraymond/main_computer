#!/usr/bin/env python3
"""Production Stable Hub launcher for Hub Control's accepted topology projection.

The Hub Control bootstrap writes the FDB cluster and topology files before
this entrypoint starts.  The concrete Hub identity must be explicit: never
choose a Hub based on an unspecified port or a development topology.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

from main_computer.stable_hub import main as stable_hub_main


def _install_authoritative_bridge_signer() -> None:
    """Materialize the verified Coolify signer into the private Hub runtime.

    This is a startup-only projection of the Hub mutation harness's secret
    environment value, never an independent mutation of Mother or Coolify.
    Public health may not accept a writes-enabled Hub lacking its signer.
    """
    enabled = os.environ.get("MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    encoded = os.environ.get("MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64", "").strip()
    path_text = os.environ.get("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", "").strip()
    runtime_dir = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_RUNTIME_DIR", "").strip()
    network = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_NETWORK", "").strip()
    chain_id = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_CHAIN_ID", "").strip()
    if not all((encoded, path_text, runtime_dir, network, chain_id)):
        raise RuntimeError("Hub bridge writes require a verified signer bundle, private destination, network and chain")
    root = Path(runtime_dir).resolve()
    path = Path(path_text).resolve()
    if not path.is_relative_to(root) or path == root:
        raise RuntimeError("Hub signer destination must be inside the Hub private runtime")
    try:
        raw = base64.b64decode(encoded, validate=True)
        bundle = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Invalid authoritative Hub signer bundle") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != "main-computer.bridge-signer.v1":
        raise RuntimeError("Invalid authoritative Hub signer schema")
    if str(bundle.get("network") or "") != network or str(bundle.get("chain_id") or "") != chain_id:
        raise RuntimeError("Authoritative Hub signer network or chain mismatch")
    if not isinstance(bundle.get("bridge_controller"), dict):
        raise RuntimeError("Authoritative Hub bridge-controller wallet is missing")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".bridge-signer-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(raw)
        os.chmod(temporary, 0o600)
        # Verify this exact file (and its key) before adopting it into the
        # persistent runtime. The contract registry is the escrow authority.
        from main_computer.config import MainComputerConfig
        from main_computer.hub_credit_bridge_completion import load_bridge_deployment, load_hub_admin_private_key
        from eth_account import Account
        deployment = load_bridge_deployment(
            MainComputerConfig.from_env(), deployment_manifest_path=Path(temporary)
        )
        key = load_hub_admin_private_key(deployment)
        if str(Account.from_key(key).address).lower() != deployment.bridge_controller_address.lower():
            raise RuntimeError("Hub signer private key does not match the authorized bridge controller")
        if str(deployment.chain_id) != chain_id:
            raise RuntimeError("Hub signer deployment chain does not match accepted topology")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    topology = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH", "").strip()
    hub_id = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_HUB_ID", "").strip()
    port = os.environ.get("MAIN_COMPUTER_HUB_PORT", os.environ.get("PORT", "8790")).strip()
    if not topology or not hub_id:
        print("Stable Hub requires Hub Control topology path and concrete Hub ID", file=sys.stderr)
        return 2
    if not Path(topology).is_file():
        print(f"Stable Hub topology not found: {topology}", file=sys.stderr)
        return 2
    try:
        parsed_port = int(port)
        if not 1 <= parsed_port <= 65535:
            raise ValueError("port outside TCP range")
    except ValueError:
        print(f"Stable Hub port is invalid: {port}", file=sys.stderr)
        return 2
    try:
        _install_authoritative_bridge_signer()
    except Exception as exc:
        # Never reveal private signer bundle contents or user-controlled paths.
        print(f"Stable Hub signer startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return stable_hub_main([
        "--topology", topology, "--hub-id", hub_id,
        "--bind-host", "0.0.0.0", "--bind-port", str(parsed_port),
    ])

if __name__ == "__main__":
    raise SystemExit(main())
