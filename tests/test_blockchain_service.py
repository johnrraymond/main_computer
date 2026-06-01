from __future__ import annotations

import json
import subprocess
from pathlib import Path

from main_computer.blockchain_service import (
    DEFAULT_CHAIN_ID,
    DEFAULT_RPC_URL,
    DEV_COMPOSE_SERVICE,
    BlockchainService,
    load_blockchain_service_state,
)


class FakeBlockchainRunner:
    def __init__(self, *, docker_failures_before_ready: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.docker_failures_before_ready = docker_failures_before_ready
        self.docker_version_attempts = 0

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command[:2] == ["docker", "version"]:
            self.docker_version_attempts += 1
            if self.docker_version_attempts <= self.docker_failures_before_ready:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="docker still starting")
            return subprocess.CompletedProcess(command, 0, stdout="Docker version ok\n", stderr="")
        if command[:3] == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="Docker Compose version ok\n", stderr="")
        if command[:2] == ["docker", "compose"] and "up" in command and DEV_COMPOSE_SERVICE in command:
            return subprocess.CompletedProcess(command, 0, stdout="started ethereum-dev\n", stderr="")
        if command[:2] == ["docker", "compose"] and "ps" in command:
            return subprocess.CompletedProcess(command, 0, stdout=f"{DEV_COMPOSE_SERVICE}\n", stderr="")
        return subprocess.CompletedProcess(command, 99, stdout="", stderr=f"unexpected command: {command!r}")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.dev.yml").write_text(
        "services:\n  ethereum-dev:\n    image: ghcr.io/foundry-rs/foundry:latest\n",
        encoding="utf-8",
    )
    return repo


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


def rpc_ready_after_compose_up(runner: FakeBlockchainRunner):
    def probe(rpc_url: str, expected_chain_id: int) -> dict[str, object]:
        if any(call[:2] == ["docker", "compose"] and "up" in call and DEV_COMPOSE_SERVICE in call for call in runner.calls):
            return ok_rpc_probe(rpc_url, expected_chain_id)
        return down_rpc_probe(rpc_url, expected_chain_id)

    return probe


def test_boot_reuses_running_default_dev_rpc_without_starting_compose(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    missing_env = tmp_path / "home" / ".env.blockchain"
    runner = FakeBlockchainRunner()
    service = BlockchainService(
        root=repo,
        blockchain_env_path=missing_env,
        runner=runner,
        rpc_probe_func=ok_rpc_probe,
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is True
    assert state["mode"] == "dev-compose"
    assert state["config"]["env_path"] == str(missing_env)
    assert state["config"]["rpc_url"] == DEFAULT_RPC_URL
    assert state["config"]["chain_id"] == DEFAULT_CHAIN_ID
    assert state["docker"]["state"] == "not-touched"
    assert state["compose"]["state"] == "already-running"
    assert state["compose"]["compose_action"] == "skipped"
    assert state["compose"]["reused_existing_rpc"] is True
    assert runner.calls == []

    current = json.loads((repo / "runtime" / "deployments" / "current.json").read_text(encoding="utf-8"))
    assert current["source"] == "blockchain-service-dev-compose"
    assert current["environment"] == "dev"
    assert current["chain"]["rpc_url"] == DEFAULT_RPC_URL
    assert current["chain"]["chain_id"] == DEFAULT_CHAIN_ID
    assert current["offices"][0]["office"] == "O0"
    assert current["offices"][0]["address"] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    assert "private_key" not in current["offices"][0]
    assert load_blockchain_service_state(repo)["ok"] is True


def test_boot_starts_dev_compose_when_default_rpc_is_not_up(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    missing_env = tmp_path / "home" / ".env.blockchain"
    runner = FakeBlockchainRunner()
    service = BlockchainService(
        root=repo,
        blockchain_env_path=missing_env,
        runner=runner,
        rpc_probe_func=rpc_ready_after_compose_up(runner),
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is True
    assert state["docker"]["state"] == "ready"
    assert state["compose"]["state"] == "ready"
    compose_up_calls = [call for call in runner.calls if call[:2] == ["docker", "compose"] and "up" in call]
    assert compose_up_calls == [["docker", "compose", "-f", str(repo / "docker-compose.dev.yml"), "up", "-d", "ethereum-dev"]]


def test_boot_uses_external_blockchain_env_without_starting_dev_compose(tmp_path: Path) -> None:
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
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is True
    assert state["mode"] == "external"
    assert state["docker"]["state"] == "not-required"
    assert state["compose"]["state"] == "not-required"
    assert all("up" not in call for call in runner.calls)
    current = json.loads((repo / "runtime" / "deployments" / "current.json").read_text(encoding="utf-8"))
    assert current["source"] == "blockchain-service-env"
    assert current["environment"] == "external"
    assert current["chain"]["rpc_url"] == "http://127.0.0.1:9999"
    assert current["chain"]["chain_id"] == 12345
    assert current["offices"] == []


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
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    assert state["ok"] is True
    current = json.loads((repo / "runtime" / "deployments" / "current.json").read_text(encoding="utf-8"))
    assert current["environment"] == "dev"
    assert current["offices"] == [
        {
            "office": "O0",
            "title": "Requester",
            "address": "0x1111111111111111111111111111111111111111",
        }
    ]


def test_watch_retries_dev_chain_boot_on_heartbeat_until_ready(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeBlockchainRunner(docker_failures_before_ready=2)
    sleep_calls: list[float] = []
    service = BlockchainService(
        root=repo,
        blockchain_env_path=tmp_path / "missing.env",
        runner=runner,
        rpc_probe_func=rpc_ready_after_compose_up(runner),
        sleep_func=sleep_calls.append,
        output_func=None,
        heartbeat_interval_s=30,
        light_check_interval_s=999,
    )

    state = service.boot(watch=True, max_watch_loops=3)

    assert state["ok"] is True
    assert state["boot_proven"] is True
    assert runner.docker_version_attempts == 3
    assert [value for value in sleep_calls if value == 30] == [30, 30]


def test_blockchain_dev_compose_uses_profile_scoped_project_and_rpc(monkeypatch, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    missing_env = tmp_path / "home" / ".env.blockchain"
    monkeypatch.setenv("MAIN_COMPUTER_DEV_COMPOSE_PROJECT", "main-computer-dev-main-computer-test-debug")
    monkeypatch.setenv("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL", "http://127.0.0.1:28545")
    monkeypatch.setenv("MAIN_COMPUTER_ENERGY_CHAIN_ID", "42424242")
    runner = FakeBlockchainRunner()
    service = BlockchainService(
        root=repo,
        blockchain_env_path=missing_env,
        runner=runner,
        rpc_probe_func=rpc_ready_after_compose_up(runner),
        sleep_func=lambda _: None,
        output_func=None,
    )

    state = service.boot()

    expected = [
        "docker",
        "compose",
        "--project-name",
        "main-computer-dev-main-computer-test-debug",
        "-f",
        str(repo / "docker-compose.dev.yml"),
        "up",
        "-d",
        DEV_COMPOSE_SERVICE,
    ]
    assert expected in runner.calls
    assert state["config"]["rpc_url"] == "http://127.0.0.1:28545"
    assert state["config"]["compose_project"] == "main-computer-dev-main-computer-test-debug"
    assert state["compose"]["compose_project"] == "main-computer-dev-main-computer-test-debug"
