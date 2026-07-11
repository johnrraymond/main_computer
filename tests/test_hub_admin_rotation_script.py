from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_rotation():
    spec = importlib.util.spec_from_file_location("hub_admin_rotation_tests", ROOT / "tools" / "hub_admin_rotation.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_private_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "networks": {
                    "dev": {
                        "hubs": {
                            "dev-hub1": {
                                "hub_admin_keys": {
                                    "address1": {
                                        "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                                        "private_key": "0x" + "a" * 64,
                                        "state": "active",
                                        "chain_authorized": True,
                                        "deployed_to_hub": True,
                                    }
                                }
                            }
                        },
                        "wallets": {
                            "captain": {
                                "address": "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
                                "private_key": "0x" + "c" * 64,
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def read_keys(private_file: Path) -> dict:
    state = yaml.safe_load(private_file.read_text(encoding="utf-8"))
    return state["networks"]["dev"]["hubs"]["dev-hub1"]["hub_admin_keys"]


def test_rotate_first_call_creates_session_and_stages_key(tmp_path, monkeypatch, capsys) -> None:
    rotation = load_rotation()
    monkeypatch.setattr(rotation, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        rotation,
        "generate_private_key_and_address",
        lambda: ("0x" + "b" * 64, "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    )
    private_file = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    write_private_state(private_file)

    code = rotation.main(
        [
            "rotate",
            "--network",
            "dev",
            "--hub",
            "dev-hub1",
            "--office",
            "O0",
            "--session",
            "first-dev-hub1-local",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert output.splitlines() == [
        "rotation first-dev-hub1-local: created",
        "stage: staged address2 0xbbbbbb...",
        "next: run rotate again to authorize",
    ]
    keys = read_keys(private_file)
    assert keys["address2"]["address"] == "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    session = json.loads((tmp_path / "runtime" / "rotations" / "hub-admin" / "first-dev-hub1-local" / "session.json").read_text())
    assert session["stage"] == "staged"
    assert session["network"] == "dev"
    assert session["hub"] == "dev-hub1"
    assert session["new_slot"] == "address2"


def test_rotate_resume_rejects_conflicting_args(tmp_path, monkeypatch, capsys) -> None:
    rotation = load_rotation()
    monkeypatch.setattr(rotation, "repo_root", lambda: tmp_path)
    session_dir = tmp_path / "runtime" / "rotations" / "hub-admin" / "first-dev-hub1-local"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session": "first-dev-hub1-local",
                "network": "dev",
                "hub": "dev-hub1",
                "office": "O0",
                "old_slot": "address1",
                "old_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "new_slot": "address2",
                "new_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "stage": "staged",
            }
        ),
        encoding="utf-8",
    )

    code = rotation.main(
        [
            "rotate",
            "--network",
            "testnet",
            "--hub",
            "testnet-hub1",
            "--session",
            "first-dev-hub1-local",
        ]
    )

    assert code == 1
    assert "session is for network=dev hub=dev-hub1 office=O0" in capsys.readouterr().err


def test_rotate_authorize_uses_quiet_output_and_advances_session(tmp_path, monkeypatch, capsys) -> None:
    rotation = load_rotation()
    monkeypatch.setattr(rotation, "repo_root", lambda: tmp_path)
    private_file = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    write_private_state(private_file)
    keys = read_keys(private_file)
    keys["address2"] = {
        "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "private_key": "0x" + "b" * 64,
        "state": "staged",
        "chain_authorized": False,
        "deployed_to_hub": False,
    }
    state = yaml.safe_load(private_file.read_text(encoding="utf-8"))
    state["networks"]["dev"]["hubs"]["dev-hub1"]["hub_admin_keys"] = keys
    private_file.write_text(yaml.safe_dump(state), encoding="utf-8")
    session_dir = tmp_path / "runtime" / "rotations" / "hub-admin" / "first-dev-hub1-local"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session": "first-dev-hub1-local",
                "network": "dev",
                "hub": "dev-hub1",
                "office": "O0",
                "private_file": None,
                "deployment": None,
                "old_slot": "address1",
                "old_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "new_slot": "address2",
                "new_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "stage": "staged",
            }
        ),
        encoding="utf-8",
    )

    def fake_authorize(args):
        assert args.network == "dev"
        state = yaml.safe_load(private_file.read_text(encoding="utf-8"))
        item = state["networks"]["dev"]["hubs"]["dev-hub1"]["hub_admin_keys"]["address2"]
        item["state"] = "chain_authorized"
        item["chain_authorized"] = True
        private_file.write_text(yaml.safe_dump(state), encoding="utf-8")
        print("noisy low-level output")
        return 0

    monkeypatch.setattr(rotation, "command_authorize_staged", fake_authorize)

    code = rotation.main(["rotate", "--session", "first-dev-hub1-local"])

    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        "rotation first-dev-hub1-local",
        "stage: authorized address2",
        "next: run rotate again to switch hub config",
    ]
    session = json.loads((session_dir / "session.json").read_text())
    assert session["stage"] == "authorized"


def test_rotate_blocks_after_switch_until_hub_reports_new_signer(tmp_path, monkeypatch, capsys) -> None:
    rotation = load_rotation()
    monkeypatch.setattr(rotation, "repo_root", lambda: tmp_path)
    private_file = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    write_private_state(private_file)
    state = yaml.safe_load(private_file.read_text(encoding="utf-8"))
    state["networks"]["dev"]["hubs"]["dev-hub1"]["hub_admin_keys"]["address1"]["state"] = "chain_revocation_pending"
    state["networks"]["dev"]["hubs"]["dev-hub1"]["hub_admin_keys"]["address1"]["deployed_to_hub"] = False
    state["networks"]["dev"]["hubs"]["dev-hub1"]["hub_admin_keys"]["address2"] = {
        "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "private_key": "0x" + "b" * 64,
        "state": "active",
        "chain_authorized": True,
        "deployed_to_hub": True,
    }
    private_file.write_text(yaml.safe_dump(state), encoding="utf-8")
    session_dir = tmp_path / "runtime" / "rotations" / "hub-admin" / "first-dev-hub1-local"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session": "first-dev-hub1-local",
                "network": "dev",
                "hub": "dev-hub1",
                "office": "O0",
                "private_file": None,
                "deployment": None,
                "old_slot": "address1",
                "old_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "new_slot": "address2",
                "new_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "stage": "switched",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        rotation,
        "hub_reports_expected_signer",
        lambda **_kwargs: (False, "hub is not reporting 0xbbbbbb..."),
    )

    code = rotation.main(["rotate", "--session", "first-dev-hub1-local"])

    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        "rotation first-dev-hub1-local",
        "blocked: hub is not reporting 0xbbbbbb...",
        "next: start/restart hub, then run rotate again",
        '  .\\scripts\\main-computer-start-stop.ps1 dev-hub-start -Root "$PWD"',
    ]


def test_rotate_finished_archives_completed_session(tmp_path, monkeypatch, capsys) -> None:
    rotation = load_rotation()
    monkeypatch.setattr(rotation, "repo_root", lambda: tmp_path)
    session_dir = tmp_path / "runtime" / "rotations" / "hub-admin" / "first-dev-hub1-local"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session": "first-dev-hub1-local",
                "network": "dev",
                "hub": "dev-hub1",
                "office": "O0",
                "old_slot": "address1",
                "old_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "new_slot": "address2",
                "new_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "stage": "complete",
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text('{"stage":"complete"}\n', encoding="utf-8")

    code = rotation.main(["rotate", "--session", "first-dev-hub1-local", "--finished"])

    assert code == 0
    output = capsys.readouterr().out
    assert "rotation first-dev-hub1-local" in output
    assert "archived: runtime" in output
    assert "done" in output
    assert not session_dir.exists()
    archives = list((tmp_path / "runtime" / "rotations" / "hub-admin" / "archive").glob("*-first-dev-hub1-local"))
    assert len(archives) == 1
    assert (archives[0] / "session.json").exists()


def test_dev_authorize_dry_run_uses_foundry_container_cast_and_says_would(tmp_path, monkeypatch, capsys) -> None:
    rotation = load_rotation()
    monkeypatch.setattr(rotation, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(rotation, "docker_executable", lambda: "docker")

    private_file = tmp_path / "state.yaml"
    deployment_file = tmp_path / "runtime" / "deployments" / "dev" / "latest.json"
    private_file.write_text(
        yaml.safe_dump(
            {
                "networks": {
                    "dev": {
                        "hubs": {
                            "dev-hub1": {
                                "hub_admin_keys": {
                                    "address1": {
                                        "address": "0x1111111111111111111111111111111111111111",
                                        "private_key": "0x" + "1" * 64,
                                        "state": "staged",
                                        "chain_authorized": False,
                                        "deployed_to_hub": False,
                                    }
                                }
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    deployment_file.parent.mkdir(parents=True)
    deployment_file.write_text(
        json.dumps(
            {
                "chain": {
                    "container_rpc_url": "http://main-computer-dev-chain-demo:8545",
                    "network": "main-computer-dev-soft-demo",
                },
                "offices": [
                    {
                        "office": "O0",
                        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                        "private_key": "0x" + "a" * 64,
                    }
                ],
                "contracts": {
                    "hub_credit_bridge_escrow": {
                        "address": "0x3333333333333333333333333333333333333333",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    code = rotation.main(
        [
            "authorize-staged",
            "--network",
            "dev",
            "--hub",
            "dev-hub1",
            "--private-file",
            str(private_file),
            "--deployment",
            str(deployment_file),
            "--dry-run",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "docker run --rm --network main-computer-dev-soft-demo" in output
    assert "--entrypoint cast ghcr.io/foundry-rs/foundry:latest send" in output
    assert "--private-key <redacted>" in output
    assert "dry-run: would authorize staged key" in output


def test_rotate_remote_not_ready_does_not_create_session_or_stage_key(tmp_path, monkeypatch, capsys) -> None:
    rotation = load_rotation()
    monkeypatch.setattr(rotation, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        rotation,
        "check_network_ready_for_hub_admin_rotation",
        lambda **_kwargs: (
            False,
            "deployed escrow does not support authorizedBridgeControllers/proposeAuthorizeBridgeController",
        ),
    )
    private_file = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    private_file.parent.mkdir(parents=True, exist_ok=True)
    private_file.write_text(
        yaml.safe_dump(
            {
                "networks": {
                    "testnet": {
                        "hubs": {
                            "testnet-hub1": {
                                "hub_admin_keys": {
                                    "address1": {
                                        "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                                        "private_key": "0x" + "a" * 64,
                                        "state": "active",
                                        "chain_authorized": True,
                                        "deployed_to_hub": True,
                                    }
                                }
                            }
                        },
                        "wallets": {
                            "captain": {
                                "address": "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
                                "private_key": "0x" + "c" * 64,
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    code = rotation.main(
        [
            "rotate",
            "--network",
            "testnet",
            "--hub",
            "testnet-hub1",
            "--office",
            "O0",
            "--session",
            "testnet-hub1-rotation",
        ]
    )

    assert code == 1
    assert capsys.readouterr().out.splitlines() == [
        "rotation testnet-hub1-rotation",
        "error: testnet is not ready for hub-admin rotation",
        "reason: deployed escrow does not support authorizedBridgeControllers/proposeAuthorizeBridgeController",
        "session: not created",
        "action required: deploy the new HubCreditBridgeEscrow shape, update deployment metadata, then run this command again",
    ]
    assert not (tmp_path / "runtime" / "rotations" / "hub-admin" / "testnet-hub1-rotation").exists()
    state = yaml.safe_load(private_file.read_text(encoding="utf-8"))
    keys = state["networks"]["testnet"]["hubs"]["testnet-hub1"]["hub_admin_keys"]
    assert sorted(keys) == ["address1"]
