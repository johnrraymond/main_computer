"""No-network contract-owner authorization tests for add-hub."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tools.hub_control.common import bridge_controller_authorization as auth
from tools.hub_control.common.deployment import _build_bridge_signer_for_deployment
from tools.hub_control.common.errors import HubControlError
from tools.hub_control.common.models import HubContext
from tools.mother.common.ethereum_identity import private_key_to_address, keccak256

KEY = "0x" + "01".zfill(64)
ADMIN_KEY = "0x" + "02".zfill(64)
DEPLOYER = private_key_to_address(KEY)
ADMIN = private_key_to_address(ADMIN_KEY)
ESCROW = "0x000000000000000000000000000000000000e5c0"


def target():
    return {"network": "mainnet", "hub_id": "mainneta-hub1", "hub_admin_address": ADMIN,
            "bridge_signer_required": True, "runtime_dir": "/data/hub/mainneta-hub1",
            "_hub_admin_wallet": {"address": ADMIN, "private_key": ADMIN_KEY},
            "hub_admin_private_state_path": "networks.mainnet.hub_admin_assignments.mainneta-hub1",
            "chain_contract": {"chain_id": 42424240, "rpc_url": "https://rpc.test.invalid",
                               "contracts": {"hub_credit_bridge_escrow": ESCROW}},
            "_local_repo_root": str(Path(__file__).resolve().parents[2])}


def ctx(tmp_path):
    result = HubContext.from_repo(tmp_path)
    result.mother_private_path.parent.mkdir(parents=True, exist_ok=True)
    result.mother_private_path.write_text(
        "networks:\n  mainnet:\n    wallets:\n      deployer:\n"
        + f"        address: '{DEPLOYER}'\n        private_key: '{KEY}'\n", encoding="utf-8")
    return result


def fake_rpc_factory(*, initially_allowed=False, owner=DEPLOYER, tx_status="0x1"):
    calls=[]
    granted=[initially_allowed]
    def rpc(url, method, params, **kw):
        calls.append((method, params))
        if method == "eth_chainId": return hex(42424240)
        if method == "eth_getCode": return "0x6000"
        if method == "eth_call":
            data = params[0]["data"]
            if data == "0x" + keccak256(b"owner()")[:4].hex():
                return "0x" + owner[2:].rjust(64,"0")
            return hex(int(granted[0]))
        if method == "eth_getTransactionReceipt":
            granted[0] = tx_status == "0x1"
            return {"status": tx_status}
        raise AssertionError(method)
    return rpc, calls


def test_authorized_admin_skips_owner_identity_and_transactions(tmp_path, monkeypatch):
    rpc,calls=fake_rpc_factory(initially_allowed=True)
    monkeypatch.setattr(auth,"_hub_chain_rpc",rpc)
    monkeypatch.setattr(auth,"_send_owner_authorization",lambda **kw: pytest.fail("must not submit"))
    result=auth.ensure_bridge_controller(ctx(tmp_path),network="mainnet",target=target(),admin_address=ADMIN)
    assert result["already_authorized"] is True
    assert result["verified"] is True
    assert not any(x[0].startswith("eth_send") for x in calls)


def test_not_authorized_uses_deployer_once_and_verifies_after_receipt(tmp_path, monkeypatch):
    rpc,calls=fake_rpc_factory()
    seen=[]
    def submit(**kw):
        seen.append(kw)
        return "0x"+"11"*32
    monkeypatch.setattr(auth,"_hub_chain_rpc",rpc)
    monkeypatch.setattr(auth,"_send_owner_authorization",submit)
    result=auth.ensure_bridge_controller(ctx(tmp_path),network="mainnet",target=target(),admin_address=ADMIN)
    assert result["verified"] and result["already_authorized"] is False
    assert seen[0]["deployer_address"].lower()==DEPLOYER.lower()
    assert seen[0]["admin"].lower()==ADMIN.lower()
    assert seen[0]["escrow"].lower()==ESCROW.lower()
    assert calls[-1][0]=="eth_call"
    assert KEY not in json.dumps(result)
    assert len(seen)==1


def test_wrong_owner_refuses_transaction(tmp_path, monkeypatch):
    rpc,_=fake_rpc_factory(owner="0x"+"ab"*20)
    monkeypatch.setattr(auth,"_hub_chain_rpc",rpc)
    monkeypatch.setattr(auth,"_send_owner_authorization",lambda **kw: pytest.fail("wrong owner"))
    with pytest.raises(HubControlError) as error:
        auth.ensure_bridge_controller(ctx(tmp_path),network="mainnet",target=target(),admin_address=ADMIN)
    assert error.value.code=="HUB_BRIDGE_OWNER_MISMATCH"


def test_failed_receipt_refuses_deploy(tmp_path, monkeypatch):
    rpc,_=fake_rpc_factory(tx_status="0x0")
    monkeypatch.setattr(auth,"_hub_chain_rpc",rpc)
    monkeypatch.setattr(auth,"_send_owner_authorization",lambda **kw:"0x"+"11"*32)
    with pytest.raises(HubControlError) as error:
        auth.ensure_bridge_controller(ctx(tmp_path),network="mainnet",target=target(),admin_address=ADMIN)
    assert error.value.code=="HUB_BRIDGE_AUTHORIZATION_REVERTED"


def test_bridge_signer_uses_exact_assigned_wallet_not_old_manifest():
    tgt=target()
    result=_build_bridge_signer_for_deployment(tgt)
    bundle=json.loads(base64.b64decode(result["bundle_b64"]))
    assert bundle["bridge_controller"]["address"]==ADMIN
    assert bundle["bridge_controller"]["private_key"]==ADMIN_KEY
    assert bundle["contracts"]["hub_credit_bridge_escrow"]["address"]==ESCROW
    assert bundle["source"]["manifest_path"]=="mother-private-state"
    assert result["bridge_controller_address"]==ADMIN
    assert ADMIN_KEY not in str({k:v for k,v in result.items() if k!="bundle_b64"})


def test_signer_does_not_fallback_to_legacy_manifest():
    tgt=target()
    del tgt["_hub_admin_wallet"]
    with pytest.raises(HubControlError) as error:
        _build_bridge_signer_for_deployment(tgt)
    assert error.value.code=="HUB_BRIDGE_SIGNER_SOURCE_INVALID"


def test_owner_signed_transaction_abi_and_nonce_are_correct(monkeypatch):
    import sys
    from types import SimpleNamespace
    captured = {}
    class FakeAccount:
        @staticmethod
        def sign_transaction(tx, key):
            captured["tx"] = tx
            captured["key"] = key
            return SimpleNamespace(raw_transaction=b"raw", hash=bytes.fromhex("11"*32))
    monkeypatch.setitem(sys.modules, "eth_account", SimpleNamespace(Account=FakeAccount))
    def rpc(_url, method, params):
        if method == "eth_getTransactionCount": return "0x5"
        if method == "eth_gasPrice": return "0x3b9aca00"
        if method == "eth_estimateGas":
            assert params[0]["from"] == DEPLOYER
            assert params[0]["to"] == ESCROW
            assert params[0]["data"] == "0x"+keccak256(b"addBridgeController(address)")[:4].hex()+ADMIN[2:].lower().rjust(64,"0")
            return "0x10000"
        if method == "eth_sendRawTransaction":
            assert params == ["0x"+b"raw".hex()]
            return "0x"+"11"*32
        raise AssertionError(method)
    monkeypatch.setattr(auth, "_hub_chain_rpc", rpc)
    tx_hash = auth._send_owner_authorization(
        rpc="https://rpc.test.invalid", chain_id=42424240, escrow=ESCROW, admin=ADMIN,
        deployer_address=DEPLOYER, deployer_key=KEY)
    assert tx_hash == "0x"+"11"*32
    assert captured["key"] == KEY
    assert captured["tx"]["chainId"] == 42424240
    assert captured["tx"]["nonce"] == 5
    assert captured["tx"]["gas"] >= 75_000
