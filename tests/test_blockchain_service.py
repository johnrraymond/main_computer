from __future__ import annotations

import json
import subprocess
from pathlib import Path

from main_computer.blockchain_service import (
    DEFAULT_CHAIN_ID,
    DEFAULT_RPC_URL,
    BlockchainService,
    load_blockchain_service_state,
)


BRIDGE_ADDRESS = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"


class FakeBlockchainRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        return subprocess.CompletedProcess(command, 99, stdout="", stderr=f"unexpected command: {command!r}")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.dev.yml").write_text("services:\n  main-computer:\n    image: main-computer-dev:latest\n", encoding="utf-8")
    return repo


def write_deployment_current(
    repo: Path,
    *,
    rpc_url: str = DEFAULT_RPC_URL,
    chain_id: int = DEFAULT_CHAIN_ID,
    bridge_address: str = BRIDGE_ADDRESS,
) -> tuple[Path, str]:
    current_path = repo / "runtime" / "deployments" / "current.json"
    current_path.parent.mkdir(parents=True)
    payload = {
        "schema": "main-computer.deployment.v1",
        "environment": "dev",
        "run_id": "test-machine-dev",
        "source": {"kind": "dev-chain-reset", "project_name": "main-computer-dev"},
        "chain": {
            "chain_id": chain_id,
            "rpc_url": rpc_url,
            "host_rpc_url": rpc_url,
            "container": "main-computer-dev-chain-test-machine-dev",
        },
        "contracts": {
            "hub_credit_bridge_escrow": {
                "target": "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow",
                "address": bridge_address,
                "bridge_controller_address": "0x6bef896c6Cbe2a89DC3508c31Ab8a2723153A0a4",
            }
        },
        "offices": [
            {
                "office": "O0",
                "title": "Captain",
                "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            }
        ],
        "hub_admin": {
            "address": "0x6bef896c6Cbe2a89DC3508c31Ab8a2723153A0a4",
            "wallet_path": "runtime/deployments/hub-admin-wallet.json",
        },
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    current_path.write_text(text, encoding="utf-8")
    return current_path, text


def ok_rpc_probe(rpc_url: str, expected_chain_id: int) -> dict[str, object]:
    return {
        "ok": True,
        "state": "ready",
        "message": "fake rpc ready",
        "rpc_url": rpc_url,
        "expected_chain_id": expected_chain_id,
        "chain_id": expected_chain_id,
    }


def down_rpc_probe(rpc_url: str, expected_chain_id: int) -> dict[str, object]:
    return {
        "ok": False,
        "state": "down",
        "message": "fake rpc down",
        "rpc_url": rpc_url,
        "expected_chain_id": expected_chain_id,
    }


def code_present_probe(rpc_url: str, address: str) -> dict[str, object]:
    return {
        "ok": True,
        "state": "code-present",
        "message": "fake contract code present",
        "rpc_url": rpc_url,
        "address": address,
        "code_size_hex_chars": 64,
    }


def missing_code_probe(rpc_url: str, address: str) -> dict[str, object]:
    return {
        "ok": False,
        "state": "missing-code",
        "message": "fake missing contract code",
        "rpc_url": rpc_url,
        "address": address,
    }


def test_boot_requires_deployment_current_json_when_env_is_missing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    missing_env = tmp_path / "home" / ".env.blockchain"
    runner = FakeBlockchainRunner()
    service = BlockchainService(
        root=repo,
        blockchain_env_path=missing_env,
        runner=runner,
        rpc_probe_func=ok_rpc_probe,
        contract_code_probe_func=code_present_probe,
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is False
    assert state["mode"] == "deployment-current"
    assert state["config"]["state"] == "missing-deployment-current"
    assert "tools\\dev-chain-reset.py" in state["config"]["reset_command"]
    assert state["docker"]["state"] == "not-required"
    assert state["compose"]["state"] == "removed"
    assert state["rpc"]["state"] == "blocked"
    assert runner.calls == []
    assert load_blockchain_service_state(repo)["ok"] is False


def test_boot_uses_deployment_current_json_and_does_not_edit_it(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    current_path, original_text = write_deployment_current(repo)
    runner = FakeBlockchainRunner()
    service = BlockchainService(
        root=repo,
        blockchain_env_path=tmp_path / "missing.env",
        runner=runner,
        rpc_probe_func=ok_rpc_probe,
        contract_code_probe_func=code_present_probe,
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is True
    assert state["mode"] == "deployment-current"
    assert state["config"]["deployment_path"] == str(current_path)
    assert state["config"]["rpc_url"] == DEFAULT_RPC_URL
    assert state["config"]["chain_id"] == DEFAULT_CHAIN_ID
    assert state["runtime"]["source"] == "runtime-deployments-current"
    assert state["runtime"]["deployment_source"] == "dev-chain-reset"
    assert state["runtime"]["offices"][0]["office"] == "O0"
    assert state["runtime"]["offices"][0]["address"] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    assert "private_key" not in state["runtime"]["offices"][0]
    assert state["contracts"]["state"] == "ready"
    assert state["contracts"]["checked_contracts"]["hub_credit_bridge_escrow"]["address"] == BRIDGE_ADDRESS
    assert state["docker"]["state"] == "not-required"
    assert state["compose"]["state"] == "removed"
    assert runner.calls == []
    assert current_path.read_text(encoding="utf-8") == original_text
    assert load_blockchain_service_state(repo)["ok"] is True


def test_boot_fails_loudly_when_deployment_contract_code_is_missing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write_deployment_current(repo)
    service = BlockchainService(
        root=repo,
        blockchain_env_path=tmp_path / "missing.env",
        runner=FakeBlockchainRunner(),
        rpc_probe_func=ok_rpc_probe,
        contract_code_probe_func=missing_code_probe,
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is False
    assert state["state"] == "down"
    assert state["contracts"]["state"] == "missing-contract-code"
    assert "configured deployment does not match the connected chain" in state["contracts"]["message"]
    assert state["contracts"]["checked_contracts"]["hub_credit_bridge_escrow"]["state"] == "missing-code"
    assert "dev-chain-diagnosis.py" in state["contracts"]["diagnosis_command"]


def test_boot_uses_external_blockchain_env_without_docker_or_contract_code_checks(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    env_path = tmp_path / ".env.blockchain"
    env_path.write_text(
        "MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL=http://127.0.0.1:9999\nMAIN_COMPUTER_ENERGY_CHAIN_ID=12345\n",
        encoding="utf-8",
    )
    runner = FakeBlockchainRunner()
    service = BlockchainService(
        root=repo,
        blockchain_env_path=env_path,
        runner=runner,
        rpc_probe_func=ok_rpc_probe,
        contract_code_probe_func=missing_code_probe,
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is True
    assert state["mode"] == "external"
    assert state["docker"]["state"] == "not-required"
    assert state["compose"]["state"] == "removed"
    assert state["contracts"]["state"] == "not-required"
    assert runner.calls == []
    assert not (repo / "runtime" / "deployments" / "current.json").exists()
    assert state["runtime"]["source"] == "blockchain-service-env"
    assert state["runtime"]["environment"] == "external"
    assert state["runtime"]["rpc_url"] == "http://127.0.0.1:9999"
    assert state["runtime"]["chain_id"] == 12345
    assert state["runtime"]["offices"] == []


def test_external_blockchain_env_can_publish_dev_office_addresses(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    env_path = tmp_path / ".env.blockchain"
    env_path.write_text(
        "\n".join(
            [
                "MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL=http://127.0.0.1:9999",
                "MAIN_COMPUTER_ENERGY_CHAIN_ID=12345",
                "MAIN_COMPUTER_DEV_OFFICE_0_ADDRESS=0x1111111111111111111111111111111111111111",
                "MAIN_COMPUTER_DEV_OFFICE_0_TITLE=Requester",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    service = BlockchainService(
        root=repo,
        blockchain_env_path=env_path,
        runner=FakeBlockchainRunner(),
        rpc_probe_func=ok_rpc_probe,
        contract_code_probe_func=missing_code_probe,
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is True
    assert state["contracts"]["state"] == "not-required"
    assert not (repo / "runtime" / "deployments" / "current.json").exists()
    assert state["runtime"]["environment"] == "dev"
    assert state["runtime"]["offices"] == [
        {
            "office": "O0",
            "title": "Requester",
            "address": "0x1111111111111111111111111111111111111111",
        }
    ]


def test_watch_retries_deployment_current_rpc_until_ready(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write_deployment_current(repo)
    attempts = {"count": 0}
    sleep_calls: list[float] = []

    def rpc_ready_on_third_attempt(rpc_url: str, expected_chain_id: int) -> dict[str, object]:
        attempts["count"] += 1
        if attempts["count"] >= 3:
            return ok_rpc_probe(rpc_url, expected_chain_id)
        return down_rpc_probe(rpc_url, expected_chain_id)

    service = BlockchainService(
        root=repo,
        blockchain_env_path=tmp_path / "missing.env",
        runner=FakeBlockchainRunner(),
        rpc_probe_func=rpc_ready_on_third_attempt,
        contract_code_probe_func=code_present_probe,
        sleep_func=sleep_calls.append,
        output_func=None,
        heartbeat_interval_s=30,
        light_check_interval_s=999,
    )

    state = service.boot(watch=True, max_watch_loops=3)

    assert state["ok"] is True
    assert state["boot_proven"] is True
    assert attempts["count"] == 3
    assert [value for value in sleep_calls if value == 30] == [30, 30]


def test_legacy_compose_environment_does_not_start_a_fallback_chain(monkeypatch, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    monkeypatch.setenv("MAIN_COMPUTER_DEV_COMPOSE_PROJECT", "main-computer-dev-main-computer-test-debug")
    monkeypatch.setenv("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL", "http://127.0.0.1:28545")
    monkeypatch.setenv("MAIN_COMPUTER_ENERGY_CHAIN_ID", "42424242")
    runner = FakeBlockchainRunner()
    service = BlockchainService(
        root=repo,
        blockchain_env_path=tmp_path / "missing.env",
        runner=runner,
        rpc_probe_func=ok_rpc_probe,
        contract_code_probe_func=code_present_probe,
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is False
    assert state["mode"] == "deployment-current"
    assert state["config"]["state"] == "missing-deployment-current"
    assert runner.calls == []
