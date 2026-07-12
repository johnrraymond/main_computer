from __future__ import annotations

from pathlib import Path

from tools import allfather_private_state as private_state


def test_migrate_coolify_also_copies_mainnet_testnet_wallet_bootstrap_slots(tmp_path: Path) -> None:
    source = tmp_path / "main_computer.private.yaml"
    out = tmp_path / "all_father.private.yaml"
    source.write_text(
        """
schema_version: 1
coolify:
  hosts:
    A:
      name: coolify-a
      api_token: token-a
networks:
  testnet:
    hub:
      instances:
        testnet-hub1:
          public_url: https://old.example.invalid
    wallets:
      hub_admin:
        address: "0x1111111111111111111111111111111111111111"
        private_key: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      deployer:
        address: "0x2222222222222222222222222222222222222222"
        private_key: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  mainnet:
    wallets:
      hub_admin:
        address: "0x3333333333333333333333333333333333333333"
        private_key: "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
wallets:
  defaults:
    deployer:
      private_key: "0xdddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
""".lstrip(),
        encoding="utf-8",
    )

    result = private_state.migrate_coolify_private_state(source=source, out=out, force=True)

    assert result["ok"] is True
    migrated = private_state.load_yaml_mapping(out)
    assert migrated["coolify"]["hosts"]["A"]["name"] == "coolify-a"
    assert migrated["networks"]["testnet"]["wallets"]["hub_admin"]["private_key"].startswith("0xaaaa")
    assert migrated["networks"]["testnet"]["wallets"]["deployer"]["private_key"].startswith("0xbbbb")
    assert migrated["networks"]["mainnet"]["wallets"]["hub_admin"]["private_key"].startswith("0xcccc")
    assert "hub" not in migrated["networks"]["testnet"]
    assert migrated["networks"]["testnet"]["foundationdb"]["cluster_description"] == "main-computer-testnet-allfather"
    assert migrated["networks"]["testnet"]["foundationdb"]["cluster_id"] is None
    assert result["network_wallet_summary"]["testnet"]["hub_admin_slot_present"] is True
    assert result["network_wallet_summary"]["testnet"]["deployer_slot_present"] is True
    assert result["network_wallet_summary"]["testnet"]["private_key_count"] == 2
    assert result["network_wallet_summary"]["testnet"]["fdb_cluster_description"] == "main-computer-testnet-allfather"


def test_migrate_coolify_copies_direct_top_level_wallet_defaults(tmp_path: Path) -> None:
    source = tmp_path / "main_computer.private.yaml"
    out = tmp_path / "all_father.private.yaml"
    source.write_text(
        """
schema_version: 1
coolify:
  hosts:
    A:
      name: coolify-a
      api_token: token-a
wallets:
  deployer:
    address: "0x2222222222222222222222222222222222222222"
    private_key: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
networks:
  testnet:
    wallets: {}
  mainnet:
    wallets: {}
""".lstrip(),
        encoding="utf-8",
    )

    result = private_state.migrate_coolify_private_state(source=source, out=out, force=True)

    assert result["ok"] is True
    migrated = private_state.load_yaml_mapping(out)
    assert migrated["wallets"]["defaults"]["deployer"]["private_key"].startswith("0xbbbb")
    assert migrated["networks"]["testnet"]["wallets"]["hub_admin"]["private_key"] is None
    assert migrated["networks"]["testnet"]["wallets"]["deployer"]["private_key"] is None
    assert migrated["networks"]["testnet"]["foundationdb"]["coordinator_policy"] == "first-node-then-expand"
