from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_prod_command():
    spec = importlib.util.spec_from_file_location("prod_command", ROOT / "prod-command.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def valid_deployment(environment: str = "prod-local", *, dry_run: bool = False) -> dict:
    return {
        "schema": "main-computer.deployment.v1",
        "environment": environment,
        "run_id": "unit-prod-local",
        "dry_run": dry_run,
        "created_at": "2026-05-10T00:00:00+00:00",
        "chain": {
            "chain_id": 42424242,
            "rpc_url": "http://127.0.0.1:18547",
        },
        "contracts": {
            "xlag-bridge-reserve": {
                "address": "0x1111111111111111111111111111111111111111",
            },
            "alpha-beta-lockout": {
                "address": "0x2222222222222222222222222222222222222222",
            },
        },
    }


def test_parser_exposes_status_deploy_local_and_lock_only() -> None:
    prod = load_prod_command()
    parser = prod.build_parser()

    subparsers_actions = [
        action
        for action in parser._actions  # noqa: SLF001 - argparse has no public subparser listing
        if action.__class__.__name__ == "_SubParsersAction"
    ]
    choices = set(subparsers_actions[0].choices)

    assert {"status", "deploy-local", "lock"}.issubset(choices)
    assert "unlock" not in choices
    assert "destroy" not in choices


def test_status_reports_missing_deployment_without_failing(tmp_path: Path, monkeypatch, capsys) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)

    code = prod.main(["status"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Production lock: absent" in out
    assert "Deployment: missing" in out


def test_deploy_local_requires_yes_or_dry_run(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)

    code = prod.main(["deploy-local", "--run-id", "unit"])

    assert code == 1


def test_deploy_local_refuses_when_prod_lock_exists(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    (tmp_path / ".prod.lock").write_text('{"deployment":"prod","protected":true}\n', encoding="utf-8")

    code = prod.main(["deploy-local", "--dry-run", "--run-id", "unit"])

    assert code == 1
    assert not (tmp_path / "runtime" / "deployments" / "current.json").exists()


def test_deploy_local_forwards_prod_local_defaults_to_reset(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    captured: dict[str, list[str]] = {}

    class FakeReset:
        @staticmethod
        def main(argv: list[str]) -> int:
            captured["argv"] = argv
            return 0

    monkeypatch.setattr(prod, "load_dev_chain_reset_module", lambda root: FakeReset)

    code = prod.main(["deploy-local", "--yes", "--run-id", "unit-prod-local"])

    assert code == 0
    argv = captured["argv"]
    assert "--environment" in argv
    assert argv[argv.index("--environment") + 1] == "prod-local"
    assert "--project-name" in argv
    assert argv[argv.index("--project-name") + 1] == "main-computer-prod-local"
    assert "--port-strategy" in argv
    assert argv[argv.index("--port-strategy") + 1] == "auto"
    assert "--yes" in argv
    assert "--run-id" in argv
    assert argv[argv.index("--run-id") + 1] == "unit-prod-local"


def test_lock_writes_prod_lock_from_current_deployment(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(valid_deployment()) + "\n", encoding="utf-8")

    code = prod.main(["lock"])

    assert code == 0
    lock_path = tmp_path / ".prod.lock"
    assert lock_path.exists()
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "main-computer.prod-lock.v1"
    assert payload["deployment"] == "prod-local"
    assert payload["protected"] is True
    assert payload["deployment_manifest"] == "runtime/deployments/current.json"
    assert payload["chain_id"] == 42424242
    assert payload["contracts"]["XLagBridgeReserve"] == "0x1111111111111111111111111111111111111111"


def test_lock_refuses_to_overwrite_existing_lock(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(valid_deployment()) + "\n", encoding="utf-8")
    lock_path = tmp_path / ".prod.lock"
    lock_path.write_text('{"deployment":"prod","protected":true}\n', encoding="utf-8")

    code = prod.main(["lock"])

    assert code == 1
    assert json.loads(lock_path.read_text(encoding="utf-8"))["deployment"] == "prod"


def test_lock_refuses_dry_run_deployment(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(valid_deployment(dry_run=True)) + "\n", encoding="utf-8")

    code = prod.main(["lock"])

    assert code == 1
    assert not (tmp_path / ".prod.lock").exists()


def test_status_check_runs_read_only_rpc_and_code_checks(tmp_path: Path, monkeypatch, capsys) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(valid_deployment()) + "\n", encoding="utf-8")

    calls: list[tuple[str, list | None]] = []

    def fake_json_rpc(url: str, method: str, params: list | None = None, *, timeout: float = 5.0):
        calls.append((method, params))
        if method == "eth_chainId":
            return hex(42424242)
        if method == "eth_getCode":
            return "0x60016001"
        raise AssertionError(method)

    monkeypatch.setattr(prod, "json_rpc", fake_json_rpc)

    code = prod.main(["status", "--check"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Read-only deployment checks" in out
    assert "PASS: chain-id" in out
    assert "PASS: XLagBridgeReserve.code" in out
    assert "PASS: AlphaBetaLockout.code" in out
    assert [method for method, _params in calls] == ["eth_chainId", "eth_getCode", "eth_getCode"]


def test_status_check_reports_unreachable_rpc_cleanly(tmp_path: Path, monkeypatch, capsys) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(valid_deployment()) + "\n", encoding="utf-8")

    def fake_json_rpc(url: str, method: str, params: list | None = None, *, timeout: float = 5.0):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(prod, "json_rpc", fake_json_rpc)

    code = prod.main(["status", "--check"])

    assert code == 1
    out = capsys.readouterr().out
    assert "FAIL: rpc: connection refused" in out
    assert "FAIL: current deployment did not pass read-only checks." in out


def test_lock_dry_run_prints_payload_without_creating_lock(tmp_path: Path, monkeypatch, capsys) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(valid_deployment()) + "\n", encoding="utf-8")

    code = prod.main(["lock", "--dry-run"])

    assert code == 0
    assert not (tmp_path / ".prod.lock").exists()
    out = capsys.readouterr().out
    assert "Dry run: would write" in out
    assert '"schema": "main-computer.prod-lock.v1"' in out
    assert '"deployment": "prod-local"' in out


def test_lock_dry_run_refuses_bad_manifest_without_creating_lock(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment = valid_deployment()
    deployment["contracts"].pop("xlag-bridge-reserve")
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(deployment) + "\n", encoding="utf-8")

    code = prod.main(["lock", "--dry-run"])

    assert code == 1
    assert not (tmp_path / ".prod.lock").exists()


def test_lock_refuses_dev_environment(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(valid_deployment(environment="dev")) + "\n", encoding="utf-8")

    code = prod.main(["lock"])

    assert code == 1
    assert not (tmp_path / ".prod.lock").exists()


def test_lock_refuses_manifest_with_missing_chain_id(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment = valid_deployment()
    deployment["chain"].pop("chain_id")
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(deployment) + "\n", encoding="utf-8")

    code = prod.main(["lock"])

    assert code == 1
    assert not (tmp_path / ".prod.lock").exists()


def test_lock_refuses_ambiguous_contract_addresses(tmp_path: Path, monkeypatch) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment = valid_deployment()
    deployment["deployments"] = {
        "xlag-bridge-reserve": {
            "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
            "address": "0x3333333333333333333333333333333333333333",
        },
        "alpha-beta-lockout": {
            "target": "AlphaBetaLockout.sol:AlphaBetaLockout",
            "address": "0x2222222222222222222222222222222222222222",
        },
    }
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps(deployment) + "\n", encoding="utf-8")

    code = prod.main(["lock"])

    assert code == 1
    assert not (tmp_path / ".prod.lock").exists()


def test_status_warns_when_lock_and_current_deployment_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    prod = load_prod_command()
    monkeypatch.setattr(prod, "repo_root", lambda: tmp_path)
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    deployment_path.parent.mkdir(parents=True)
    deployment = valid_deployment()
    deployment_path.write_text(json.dumps(deployment) + "\n", encoding="utf-8")

    assert prod.main(["lock"]) == 0

    deployment["chain"]["chain_id"] = 31337
    deployment_path.write_text(json.dumps(deployment) + "\n", encoding="utf-8")

    code = prod.main(["status"])

    assert code == 0
    out = capsys.readouterr().out
    assert "WARNING: production lock drift" in out
    assert "chain_id" in out
