from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "dev_native_mint_forge.py"
spec = importlib.util.spec_from_file_location("dev_native_mint_forge", MODULE_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_amount_native_to_wei_exact() -> None:
    assert mod.amount_native_to_wei("20") == 20 * 10**18
    assert mod.amount_native_to_wei("0.000000000000000001") == 1


def test_amount_native_to_wei_rejects_nonpositive_and_subwei() -> None:
    for value in ("0", "-1", "0.0000000000000000001"):
        try:
            mod.amount_native_to_wei(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected rejection for {value}")


def test_forge_model_has_no_coolify_genesis_or_live_mint_rpc_path() -> None:
    tool = MODULE_PATH.read_text(encoding="utf-8")
    rules = (ROOT / "contracts" / "src" / "MotherNativeMintRules.sol").read_text(encoding="utf-8")
    test = (ROOT / "contracts" / "test" / "MotherNativeMintRules.t.sol").read_text(encoding="utf-8")
    combined = "\n".join((tool, rules, test)).lower()

    assert "/api/v1/services" not in combined
    assert "genesis.json" not in combined
    assert "eth_sendrawtransaction" not in combined
    assert "mother_mint" not in combined
    assert "vm.deal" in combined


def test_rules_require_strict_two_thirds_quorum_and_replay_nonce() -> None:
    text = (ROOT / "contracts" / "src" / "MotherNativeMintRules.sol").read_text(encoding="utf-8")
    assert "return requiredApprovalsFor(_validators.length);" in text
    assert "return ((validatorCount_ * 2) / 3) + 1;" in text
    assert "approvals.length < required" in text
    assert "mapping(uint256 => bool) public consumedNonce;" in text
    assert "NonValidatorSigner" in text
    assert "DuplicateSigner" in text
