from __future__ import annotations

from types import SimpleNamespace

from main_computer.network_safety import (
    authority_status,
    classify_network_safety,
    manifest_contract_address_map,
    manifest_contract_entries,
    manifest_identity_warnings,
)


def _profile(network_key: str = "mainnet", *, kind: str = "mainnet", chain_id: int = 42424240) -> SimpleNamespace:
    return SimpleNamespace(network_key=network_key, kind=kind, chain_id=chain_id)


def test_authority_status_flags_default_anvil_offices_for_remote_networks() -> None:
    manifest = {
        "offices": [
            {"office": "O0", "title": "Captain", "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"},
            {"office": "O1", "title": "First Officer", "address": "0x1111111111111111111111111111111111111111"},
        ]
    }

    result = authority_status(_profile(), manifest)

    assert result["authority_status"] == "unsafe"
    assert result["authority_unsafe"] is True
    assert "default Anvil" in str(result["authority_warning"])


def test_authority_status_allows_rotated_remote_offices() -> None:
    manifest = {
        "offices": [
            {"office": "O0", "title": "Captain", "address": "0x1111111111111111111111111111111111111111"},
            {"office": "O1", "title": "First Officer", "address": "0x2222222222222222222222222222222222222222"},
            {"office": "O2", "title": "Second Officer", "address": "0x3333333333333333333333333333333333333333"},
            {"office": "O3", "title": "Third Officer", "address": "0x4444444444444444444444444444444444444444"},
        ]
    }

    result = authority_status(_profile(), manifest)

    assert result["authority_status"] == "rotated"
    assert result["authority_unsafe"] is False
    assert result["authority_warning"] == ""


def test_manifest_identity_warnings_report_environment_and_chain_mismatch() -> None:
    manifest = {
        "environment": "testnet",
        "chain": {"chain_id": 42424241},
    }

    warnings = manifest_identity_warnings(_profile("mainnet", kind="mainnet", chain_id=42424240), manifest)

    assert any("environment" in warning for warning in warnings)
    assert any("chain id" in warning.lower() for warning in warnings)


def test_contract_entries_merge_deployments_and_contracts_with_address_filter() -> None:
    manifest = {
        "deployments": {
            "alpha-beta-lockout": {"address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
            "empty": {"address": ""},
        },
        "contracts": {
            "xlag-bridge-reserve": {"address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
        },
    }

    entries = manifest_contract_entries(manifest, require_address=True)

    assert set(entries) == {"alpha-beta-lockout", "xlag-bridge-reserve"}
    assert manifest_contract_address_map(entries)["alpha-beta-lockout"].startswith("0xaaaa")


def test_classify_network_safety_prioritizes_unsafe_over_degraded() -> None:
    assert classify_network_safety(unsafe=["authority"], degraded=["rpc"]) == "unsafe"
    assert classify_network_safety(degraded=["rpc"]) == "degraded"
    assert classify_network_safety() == "healthy"
