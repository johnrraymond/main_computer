from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_wallet_smoke_guide():
    spec = importlib.util.spec_from_file_location("dev_chain_wallet_smoke_guide", ROOT / "tools" / "dev-chain-wallet-smoke-guide.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_smoke_id_accepts_explicit_32_byte_hex() -> None:
    guide = load_wallet_smoke_guide()
    assert guide.smoke_id("0x" + "ab" * 32) == "0x" + "ab" * 32


def test_office_records_fall_back_to_default_dev_keys() -> None:
    guide = load_wallet_smoke_guide()
    offices = guide.office_records({})
    assert offices[0]["office"] == "O0"
    assert offices[0]["address"] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    assert str(offices[0]["private_key"]).startswith("0xac0974")


def test_guide_output_redacts_private_keys_by_default(tmp_path, capsys) -> None:
    guide = load_wallet_smoke_guide()
    state = tmp_path / "latest.json"
    state.write_text(
        json.dumps(
            {
                "chain": {"host_rpc_url": "http://127.0.0.1:18545", "chain_id": 42424242},
                "deployments": {
                    "xlag-bridge-reserve": {"address": "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"}
                },
                "offices": [
                    {
                        "office": "O0",
                        "title": "Captain",
                        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                        "private_key": "0x" + "1" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    code = guide.main(["--state", str(state), "--smoke-id", "0x" + "cd" * 32])
    output = capsys.readouterr().out

    assert code == 0
    assert "finalizeWalletSmokeTest(bytes32,string)" in output
    assert "private keys hidden" in output
    assert "0xcdcd" in output
