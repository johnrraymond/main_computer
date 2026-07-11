from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "sync_private_state.py"
FAKE_PRIVATE_STATE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "fake-main-computer.private.yaml"


@pytest.fixture()
def sync_private_state():
    spec = importlib.util.spec_from_file_location("sync_private_state_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def key(char: str) -> str:
    return "0x" + (char * 64)


def test_sensitive_local_secret_values_collects_wallet_keys_and_public_coolify_ips(sync_private_state):
    state = {
        "coolify": {
            "hosts": {
                "A": {
                    "name": "coolify-a",
                    "public_ip": "203.0.113.10",
                    "vpn_ip": "10.0.0.10",
                    "url": "http://203.0.113.10:8000/",
                },
                "B": {
                    "name": "coolify-b",
                    "public_ip": "203.0.113.11",
                    "vpn_ip": "10.0.0.11",
                    "url": "https://coolify-b.example.test",
                },
            },
            "local_test": {
                "host": "127.0.0.1",
                "coolify_url": "http://127.0.0.1:8000",
            },
        },
        "wallets": {
            "defaults": {
                "deployer": {"private_key": key("1")},
            },
        },
        "networks": {
            "dev": {
                "wallets": {
                    "deployer": {"private_key": key("2")},
                },
            },
            "test": {
                "wallets": {
                    "deployer": {"private_key": key("3")},
                },
            },
            "testnet": {
                "wallets": {
                    "deployer": {"private_key": key("4")},
                    "captain": {"private_key": key("5")},
                    "placeholder": {"private_key": "<redacted>"},
                },
                "hubs": {
                    "testnet-hub1": {
                        "hub_admin_keys": {
                            "address1": {"private_key": key("7")},
                        },
                    },
                },
            },
            "mainnet": {
                "wallets": {
                    "deployer": {"private_key": key("6")},
                    "duplicate": {"private_key": key("4")},
                },
                "hubs": {
                    "mainnet-hub1": {
                        "hub_admin_keys": {
                            "address1": {"private_key": key("8")},
                            "address2": {"private_key": key("7")},
                        },
                    },
                },
            },
        },
    }

    assert sync_private_state.sensitive_wallet_private_keys(state) == [
        key("4"),
        key("5"),
        key("7"),
        key("6"),
        key("8"),
    ]
    assert sync_private_state.manual_coolify_host_ip_values(state) == [
        "203.0.113.10",
        "203.0.113.11",
    ]
    assert sync_private_state.sensitive_local_secret_values(state) == [
        key("4"),
        key("5"),
        key("7"),
        key("6"),
        key("8"),
        "203.0.113.10",
        "203.0.113.11",
    ]


def test_ten_dot_vpn_ips_are_not_local_secrets(sync_private_state):
    state = {
        "coolify": {
            "hosts": {
                "A": {
                    "public_ip": "198.51.100.10",
                    "vpn_ip": "10.1.2.3",
                    "url": "http://198.51.100.10:8000/",
                    "coolify_url": "http://10.1.2.3:8000/",
                },
            },
        },
    }

    assert sync_private_state.manual_coolify_host_ip_values(state) == ["198.51.100.10"]
    assert "10.1.2.3" not in sync_private_state.sensitive_local_secret_values(state)


def test_ensure_local_secrets_appends_missing_sensitive_values(sync_private_state, tmp_path):
    (tmp_path / ".gitignore").write_text("local.secrets\n", encoding="utf-8")
    local_secrets = tmp_path / "local.secrets"
    local_secrets.write_text("# existing denylist\n" + key("4") + "\n203.0.113.10\n", encoding="utf-8")

    state = {
        "coolify": {
            "hosts": {
                "A": {
                    "public_ip": "203.0.113.10",
                    "vpn_ip": "10.0.0.10",
                },
                "B": {
                    "public_ip": "203.0.113.11",
                    "vpn_ip": "10.0.0.11",
                },
            },
        },
        "networks": {
            "testnet": {
                "wallets": {
                    "deployer": {"private_key": key("4")},
                    "captain": {"private_key": key("5")},
                },
            },
            "mainnet": {
                "wallets": {
                    "deployer": {"private_key": key("6")},
                },
                "hubs": {
                    "mainnet-hub1": {
                        "hub_admin_keys": {
                            "address1": {"private_key": key("8")},
                        },
                    },
                },
            },
            "dev": {
                "wallets": {
                    "deployer": {"private_key": key("7")},
                },
            },
        },
    }

    added = sync_private_state.ensure_local_secrets_for_sensitive_values(tmp_path, state)

    assert added == 4
    lines = local_secrets.read_text(encoding="utf-8").splitlines()
    assert key("4") in lines
    assert key("5") in lines
    assert key("6") in lines
    assert key("7") not in lines
    assert key("8") in lines
    assert "203.0.113.10" in lines
    assert "10.0.0.10" not in lines
    assert "203.0.113.11" in lines
    assert "10.0.0.11" not in lines


def test_ensure_local_secrets_is_idempotent(sync_private_state, tmp_path):
    state = {
        "coolify": {
            "hosts": {
                "A": {
                    "public_ip": "203.0.113.20",
                    "vpn_ip": "10.0.0.20",
                },
            },
        },
        "networks": {
            "testnet": {
                "wallets": {
                    "deployer": {"private_key": key("8")},
                },
            },
        },
    }

    assert sync_private_state.ensure_local_secrets_for_sensitive_values(tmp_path, state) == 2
    assert sync_private_state.ensure_local_secrets_for_sensitive_values(tmp_path, state) == 0
    assert (tmp_path / "local.secrets").read_text(encoding="utf-8").splitlines() == [
        key("8"),
        "203.0.113.20",
    ]


def test_write_updates_local_secrets_without_live_checks(sync_private_state, tmp_path, monkeypatch, capsys):
    (tmp_path / "tools").mkdir()
    (tmp_path / "contracts").mkdir()
    (tmp_path / ".gitignore").write_text("local.secrets\n", encoding="utf-8")
    state_path = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "coolify:",
                "  hosts:",
                "    A:",
                "      public_ip: '203.0.113.30'",
                "      vpn_ip: '10.0.0.30'",
                "networks:",
                "  testnet:",
                "    wallets:",
                "      deployer:",
                f"        private_key: '{key('9')}'",
                "  dev:",
                "    wallets:",
                "      deployer:",
                f"        private_key: '{key('a')}'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    assert sync_private_state.main(["--state", str(state_path), "--write", "--no-live-check"]) == 0

    captured = capsys.readouterr()
    assert "Added 2 sensitive value(s) to local.secrets" in captured.out
    local_secrets = (tmp_path / "local.secrets").read_text(encoding="utf-8").splitlines()
    assert key("9") in local_secrets
    assert key("a") not in local_secrets
    assert "203.0.113.30" in local_secrets
    assert "10.0.0.30" not in local_secrets


def test_fake_private_template_local_secrets_coverage_is_test_only(sync_private_state):
    state = yaml.safe_load(FAKE_PRIVATE_STATE_FIXTURE.read_text(encoding="utf-8"))

    values = sync_private_state.sensitive_local_secret_values(state)

    testnet_wallet_keys = [
        wallet["private_key"]
        for wallet in state["networks"]["testnet"]["wallets"].values()
        if wallet.get("private_key")
    ]
    testnet_hub_admin_keys = [
        key_payload["private_key"]
        for hub in state["networks"]["testnet"]["hubs"].values()
        for key_payload in hub["hub_admin_keys"].values()
        if key_payload.get("private_key")
    ]
    assert testnet_wallet_keys
    assert testnet_hub_admin_keys
    for private_key in [*testnet_wallet_keys, *testnet_hub_admin_keys]:
        assert private_key in values

    assert all(wallet["private_key"] is None for wallet in state["networks"]["mainnet"]["wallets"].values())
    assert all(
        key_payload["private_key"] is None
        for hub in state["networks"]["mainnet"]["hubs"].values()
        for key_payload in hub["hub_admin_keys"].values()
    )
    assert "198.51.100.10" in values
    assert "198.51.100.11" in values
    assert "10.42.0.10" not in values
    assert "10.42.0.11" not in values

    # Stage 4 is deliberately test-only: local.secrets is still a denylist for
    # wallet private keys and public Coolify IP coordinates, not the source of
    # truth for fake or real Coolify API tokens.
    assert "fake-coolify-token-a-not-secret" not in values
    assert "fake-coolify-token-b-not-secret" not in values
