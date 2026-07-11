from __future__ import annotations

import argparse
from dataclasses import replace

from main_computer import cli as main_cli


def test_data_god_mode_preserves_prompt_and_count_math_options() -> None:
    args = argparse.Namespace(provider="ollama", model="gemma4:26b")
    argv = main_cli._data_god_mode_smoke_argv(
        args,
        ["Apply", "the", "safe", "edit", "--god-mode", "--count", "5", "--work-root", ".smoke-runs"],
    )

    assert argv[argv.index("--real-agent-prompt") + 1] == "Apply the safe edit"
    assert "--god-mode" in argv
    assert argv[argv.index("--real-agent-worker-count") + 1] == "5"
    assert argv[argv.index("--real-agent-reviewer-count") + 1] == "5"


def test_data_parser_accepts_god_mode_before_prompt() -> None:
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "--god-mode", "--scripted-ai-smoke", "--count", "5", "Apply", "now"])

    assert args.command == "data"
    assert args.data_god_mode is True
    assert args.data_scripted_ai_smoke is True
    assert args.data_count == 5
    assert args.data_args == ["Apply", "now"]


def test_data_god_mode_invokes_smoke_main(monkeypatch) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    captured: list[list[str]] = []

    def fake_smoke_main(argv: list[str]) -> int:
        captured.append(list(argv))
        return 0

    monkeypatch.setattr(smoke, "main", fake_smoke_main)
    parser = main_cli.build_parser()
    args = parser.parse_args([
        "data",
        "Apply",
        "now",
        "--god-mode",
        "--scripted-ai-smoke",
        "--count",
        "5",
    ])

    assert args.func(args) == 0
    assert captured
    smoke_argv = captured[0]
    assert smoke_argv[smoke_argv.index("--real-agent-prompt") + 1] == "Apply now"
    assert "--god-mode" in smoke_argv
    assert "--scripted-ai-smoke" in smoke_argv
    assert smoke_argv[smoke_argv.index("--real-agent-worker-count") + 1] == "5"
    assert smoke_argv[smoke_argv.index("--real-agent-reviewer-count") + 1] == "5"


def test_data_god_mode_does_not_force_expected_apply_defaults() -> None:
    args = argparse.Namespace(provider="ollama", model="gemma4:26b")
    argv = main_cli._data_god_mode_smoke_argv(
        args,
        ["What", "is", "4", "+", "8", "--god-mode"],
    )

    assert "--god-mode" in argv
    assert "--real-agent-expected-endstate" not in argv
    assert "--real-agent-expected-changed-files" not in argv


def test_data_god_mode_prints_compact_summary(monkeypatch, tmp_path, capsys) -> None:
    import json
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    report_path = tmp_path / "report.json"
    trace_path = tmp_path / "ai_calls.jsonl"

    def fake_smoke_main(argv: list[str]) -> int:
        report = {
            "ok": True,
            "report_path": str(report_path),
            "ai_trace_path": str(trace_path),
            "final_endstate": "answer_only",
            "real_agent_worker_count": 4,
            "real_agent_reviewer_count": 4,
            "expected_byzantine_ai_phase_call_floor": 8,
            "decision": {"answer": "12"},
            "ai_call_summary": {"finished_live_call_count": 8},
            "full_byzantine_reference_path": {
                "full_byzantine_reference_path": False,
                "single_ai_trust_points": [],
            },
            "failed_contracts": [],
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        print(json.dumps({"event": "real_agent_prompt_smoke_finished", "report_path": str(report_path)}))
        return 0

    monkeypatch.setattr(smoke, "main", fake_smoke_main)
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "What", "is", "4", "+", "8", "--god-mode"])

    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert "Data god-mode: OK" in output
    assert "answer: 12" in output
    assert '"event":' not in output


def test_data_agent_forces_mainnet_hub_provider() -> None:
    args = argparse.Namespace(provider="ollama", model="gemma4:26b")
    argv = main_cli._data_god_mode_smoke_argv(
        args,
        ["What", "is", "4", "+", "8", "--god-mode", "--agent"],
    )

    assert argv[argv.index("--ai-provider") + 1] == "hub"
    assert argv[argv.index("--ai-model") + 1] == "gemma4:26b"
    assert argv[argv.index("--ai-hub-url") + 1] == "https://mainnet-hub.greatlibrary.io"
    assert argv[argv.index("--ai-hub-client-node-id") + 1] == "main-computer-data-agent-cli"
    assert argv[argv.index("--ai-hub-transport") + 1] == "worker-pull"


def test_data_parser_accepts_agent_before_prompt() -> None:
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "--agent", "--god-mode", "What", "is", "4", "+", "8"])

    assert args.command == "data"
    assert args.data_agent is True
    assert args.data_god_mode is True
    assert args.data_args == ["What", "is", "4", "+", "8"]


def test_data_agent_flag_is_not_passed_to_captain_fallback(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run_captain(argv, *, config=None, cwd=None):
        captured.append(list(argv))
        return 0

    monkeypatch.setattr(main_cli, "run_captain", fake_run_captain)
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "--agent", "Make", "it", "so"])

    assert args.func(args) == 0
    assert captured == [["smoke", "o3", "Make", "it", "so"]]


def test_smoke_parser_accepts_hub_provider_and_hub_routing_options() -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    parser = smoke.build_parser()
    args = parser.parse_args(
        [
            "--real-agent-prompt",
            "What is 4 + 8",
            "--god-mode",
            "--ai-provider",
            "hub",
            "--ai-hub-url",
            "https://mainnet-hub.greatlibrary.io",
            "--ai-hub-client-node-id",
            "main-computer-data-agent-cli",
            "--ai-hub-transport",
            "worker-pull",
        ]
    )

    assert args.ai_provider == "hub"
    assert args.ai_hub_url == "https://mainnet-hub.greatlibrary.io"
    assert args.ai_hub_client_node_id == "main-computer-data-agent-cli"
    assert args.ai_hub_transport == "worker-pull"


def test_hub_provider_source_has_no_command_specific_data_or_captain_labels() -> None:
    from pathlib import Path
    import main_computer.providers.hub as hub_provider
    import main_computer.hub as hub_module
    import main_computer.hub_plex_service as hub_plex_service

    for module in (hub_provider, hub_module, hub_plex_service):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        lowered = source.lower()
        assert "data-god" not in lowered
        assert "data_o3" not in lowered
        assert "data/o3" not in lowered
        assert "main-computer-data" not in lowered
        assert "captain-engage" not in lowered


def test_data_god_mode_hub_403_prints_compact_failure(monkeypatch, capsys) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    def fake_smoke_main(argv: list[str]) -> int:
        raise RuntimeError(
            "Hub request failed for https://mainnet-hub.greatlibrary.io/api/hub/sessions/start "
            "with HTTP 403: error code: 1010"
        )

    monkeypatch.setattr(smoke, "main", fake_smoke_main)
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "What", "is", "4", "+", "9", "--god-mode", "--agent", "--no-bridge"])

    assert args.func(args) == 2
    output = capsys.readouterr().out
    assert "Data god-mode: FAILED" in output
    assert "HTTP 403 / Cloudflare 1010" in output
    assert "mainnet Hub rejected this client" in output
    assert "Traceback" not in output


def test_data_agent_infers_model_from_config_without_model_flag(monkeypatch) -> None:
    from main_computer.config import MainComputerConfig

    base = MainComputerConfig.from_env()
    inferred = replace(base, provider="ollama", model="gemma4:26b")
    monkeypatch.setattr(main_cli, "_config_from_args", lambda args: inferred)

    args = argparse.Namespace(
        workspace=None,
        provider=None,
        model=None,
        hub_url=None,
        hub_client_node_id=None,
    )
    argv = main_cli._data_god_mode_smoke_argv(
        args,
        ["What", "is", "4", "+", "9", "--god-mode", "--agent"],
    )

    assert argv[argv.index("--ai-provider") + 1] == "hub"
    assert argv[argv.index("--ai-model") + 1] == "gemma4:26b"
    assert argv[argv.index("--ai-hub-url") + 1]


def test_data_engage_computer_agent_infers_lane_options(monkeypatch, capsys) -> None:
    from main_computer.config import MainComputerConfig

    base = MainComputerConfig.from_env()
    inferred = replace(
        base,
        provider="ollama",
        model="gemma4:26b",
        hub_url="https://ignored-config-hub.example",
        hub_worker_node_id="data-o3-worker-test",
        hub_worker_endpoint="",
    )

    class FakeRegistry:
        def get(self, name: str):
            assert name == "mainnet"

            class Profile:
                hub_url = "https://mainnet-hub.example"

            return Profile()

    from main_computer.models import ChatResponse

    class FakeComputer:
        class Provider:
            def chat(self, messages):
                return ChatResponse(
                    content="{\"answer\":\"13\"}",
                    provider="fake",
                    model="gemma4:26b",
                    metadata={},
                )

        provider = Provider()

    captured: dict[str, object] = {}

    def fake_serve_hub_worker_pull(config, chat, **kwargs):
        captured["model"] = config.model
        captured["hub_url"] = kwargs["hub_url"]
        captured["assigned_ring"] = kwargs["assigned_ring"]
        captured["max_requests"] = kwargs["max_requests"]
        response = chat([])
        captured["answering_ring"] = response.metadata.get("answering_ring")
        captured["worker_assigned_ring"] = response.metadata.get("worker_assigned_ring")
        captured["ring3_answer_verified"] = response.metadata.get("ring3_answer_verified")
        return None

    monkeypatch.setattr(main_cli, "_config_from_args", lambda args: inferred)
    monkeypatch.setattr(main_cli, "load_hub_network_registry", lambda: FakeRegistry())
    monkeypatch.setattr(main_cli.MainComputer, "build", staticmethod(lambda config: FakeComputer()))
    monkeypatch.setattr(main_cli, "serve_hub_worker_pull", fake_serve_hub_worker_pull)

    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "engage", "computer", "--agent", "--max-requests", "1"])

    assert args.func(args) == 0
    assert captured == {
        "model": "gemma4:26b",
        "hub_url": "https://mainnet-hub.example",
        "assigned_ring": 3,
        "max_requests": 1,
        "answering_ring": 3,
        "worker_assigned_ring": 3,
        "ring3_answer_verified": True,
    }
    output = capsys.readouterr().out
    assert "Engaging Data/O3" in output
    assert "Model: gemma4:26b" in output
    assert "Hub URL: https://mainnet-hub.example" in output


def test_data_god_mode_hub_no_workers_suggests_inferred_engage(monkeypatch, capsys) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    def fake_smoke_main(argv: list[str]) -> int:
        raise RuntimeError(
            "Hub request failed for https://mainnet-hub.greatlibrary.io/api/hub/sessions/start "
            'with HTTP 400: {"error": "No hub workers or upstream hubs are registered or available."}'
        )

    monkeypatch.setattr(smoke, "main", fake_smoke_main)
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "What", "is", "4", "+", "9", "--god-mode", "--agent", "--no-bridge"])

    assert args.func(args) == 2
    output = capsys.readouterr().out
    assert "Data god-mode: FAILED" in output
    assert "no matching Data/O3 agent worker" in output
    assert "main-computer data engage computer --agent" in output
    assert "--model gemma4:26b" not in output
    assert "--hub-url https://mainnet-hub.greatlibrary.io" not in output


def test_data_agent_hub_worker_pull_uses_request_lane(monkeypatch) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    calls: list[dict[str, object]] = []

    def fake_open_hub_ai_json(hub_url, path, *, method="GET", payload=None, query=None, timeout_seconds=30.0):
        calls.append({"hub_url": hub_url, "path": path, "method": method, "payload": payload, "query": query})
        assert "/api/hub/sessions/start" not in path
        if path == "/api/hub/v1/requests":
            assert method == "POST"
            assert isinstance(payload, dict)
            assert payload["execution_mode"] == "worker_pull_v0"
            assert payload["requested_ring"] == 3
            assert payload["required_ring"] == 3
            assert payload["metadata"]["worker_pull_v0"] is True
            assert payload["metadata"]["requested_ring"] == 3
            assert payload["metadata"]["required_ring"] == 3
            assert payload["metadata"]["officer"] == "data"
            assert payload["metadata"]["officer_number"] == 3
            assert payload["model"] == "gemma4:26b"
            return {"ok": True, "request": {"request_id": "req_data_001", "state": "queued"}}
        if path == "/api/hub/v1/requests/req_data_001":
            return {"ok": True, "request": {"request_id": "req_data_001", "state": "completed"}}
        if path == "/api/hub/v1/requests/req_data_001/result":
            return {
                "ok": True,
                "result": {
                    "status": "success",
                    "response": {
                        "content": "{\"answer\":\"13\"}",
                        "provider": "ollama",
                        "model": "gemma4:26b",
                        "metadata": {
                            "worker_pull_v0": True,
                            "requested_ring": 3,
                            "required_ring": 3,
                            "worker_assigned_ring": 3,
                            "answering_ring": 3,
                            "ring3_answer_verified": True,
                        },
                    },
                },
            }
        raise AssertionError(f"unexpected hub path: {path}")

    monkeypatch.setattr(smoke, "_open_hub_ai_json", fake_open_hub_ai_json)
    result = smoke.call_hub_ai_json(
        stage="real_agent_byzantine_worker_001",
        system_prompt="return json",
        user_prompt="What is 4 + 9?",
        model="gemma4:26b",
        hub_url="https://mainnet-hub.greatlibrary.io",
        client_node_id="main-computer-data-agent-cli",
        timeout_seconds=30,
        transport="worker-pull",
    )

    assert result.provider == "hub"
    assert result.model == "gemma4:26b"
    assert result.content == "{\"answer\":\"13\"}"
    assert result.metadata["hub_transport"] == "worker_pull_v0"
    assert result.metadata["hub_request_id"] == "req_data_001"
    assert result.metadata["requested_ring"] == 3
    assert result.metadata["answering_ring"] == 3
    assert result.metadata["ring3_answer_verified"] is True
    assert [call["path"] for call in calls] == [
        "/api/hub/v1/requests",
        "/api/hub/v1/requests/req_data_001",
        "/api/hub/v1/requests/req_data_001/result",
    ]


def test_data_verbose_events_hub_failure_does_not_traceback(monkeypatch, capsys) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    def fake_smoke_main(argv: list[str]) -> int:
        print("{\"event\":\"ai_call_started\",\"ai_stage\":\"real_agent_byzantine_worker_001\"}")
        raise RuntimeError(
            "Hub worker-pull request did not complete successfully: state=failed "
            "request_id=req_data_001 error=no matching worker"
        )

    monkeypatch.setattr(smoke, "main", fake_smoke_main)
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "What", "is", "4", "+", "9", "--god-mode", "--agent", "--no-bridge", "--verbose-events"])

    assert args.func(args) == 2
    output = capsys.readouterr().out
    assert '"event":"ai_call_started"' in output
    assert "Data god-mode: FAILED" in output
    assert "main-computer data engage computer --agent" in output
    assert "Traceback" not in output


def test_data_agent_hub_worker_pull_rejects_non_ring3_answer(monkeypatch) -> None:
    import pytest
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    def fake_open_hub_ai_json(hub_url, path, *, method="GET", payload=None, query=None, timeout_seconds=30.0):
        if path == "/api/hub/v1/requests":
            return {"ok": True, "request": {"request_id": "req_data_bad_ring", "state": "queued"}}
        if path == "/api/hub/v1/requests/req_data_bad_ring":
            return {"ok": True, "request": {"request_id": "req_data_bad_ring", "state": "completed"}}
        if path == "/api/hub/v1/requests/req_data_bad_ring/result":
            return {
                "ok": True,
                "result": {
                    "status": "success",
                    "response": {
                        "content": "{\"answer\":\"13\"}",
                        "provider": "ollama",
                        "model": "gemma4:26b",
                        "metadata": {
                            "worker_pull_v0": True,
                            "requested_ring": 3,
                            "required_ring": 3,
                            "worker_assigned_ring": 2,
                            "answering_ring": 2,
                        },
                    },
                },
            }
        raise AssertionError(f"unexpected hub path: {path}")

    monkeypatch.setattr(smoke, "_open_hub_ai_json", fake_open_hub_ai_json)
    with pytest.raises(RuntimeError, match="did not prove Data/O3 Ring 3"):
        smoke.call_hub_ai_json(
            stage="real_agent_byzantine_worker_001",
            system_prompt="return json",
            user_prompt="What is 4 + 9?",
            model="gemma4:26b",
            hub_url="https://mainnet-hub.greatlibrary.io",
            client_node_id="main-computer-data-agent-cli",
            timeout_seconds=30,
            transport="worker-pull",
        )


def test_data_agent_ring3_is_command_request_metadata_not_hub_semantics(monkeypatch) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    captured_payloads: list[dict[str, object]] = []

    def fake_open_hub_ai_json(hub_url, path, *, method="GET", payload=None, query=None, timeout_seconds=30.0):
        if path == "/api/hub/v1/requests":
            assert isinstance(payload, dict)
            captured_payloads.append(payload)
            return {"ok": True, "request": {"request_id": "req_data_ring3", "state": "queued"}}
        if path == "/api/hub/v1/requests/req_data_ring3":
            return {"ok": True, "request": {"request_id": "req_data_ring3", "state": "completed"}}
        if path == "/api/hub/v1/requests/req_data_ring3/result":
            return {
                "ok": True,
                "result": {
                    "status": "success",
                    "response": {
                        "content": "{\"answer\":\"13\"}",
                        "provider": "ollama",
                        "model": "gemma4:26b",
                        "metadata": {
                            "worker_pull_v0": True,
                            "requested_ring": 3,
                            "required_ring": 3,
                            "worker_assigned_ring": 3,
                            "answering_ring": 3,
                            "ring3_answer_verified": True,
                        },
                    },
                },
            }
        raise AssertionError(f"unexpected hub path: {path}")

    monkeypatch.setattr(smoke, "_open_hub_ai_json", fake_open_hub_ai_json)
    smoke.call_hub_ai_json(
        stage="real_agent_byzantine_worker_001",
        system_prompt="return json",
        user_prompt="What is 4 + 9?",
        model="gemma4:26b",
        hub_url="https://mainnet-hub.greatlibrary.io",
        client_node_id="main-computer-data-agent-cli",
        timeout_seconds=30,
        transport="worker-pull",
    )

    assert captured_payloads
    payload = captured_payloads[0]
    assert payload["requested_ring"] == 3
    assert payload["required_ring"] == 3
    assert payload["metadata"]["requested_ring"] == 3
    assert payload["metadata"]["required_ring"] == 3
    assert payload["metadata"]["officer"] == "data"
    assert payload["metadata"]["officer_number"] == 3


def test_data_engage_computer_agent_rejects_non_ring3_override(monkeypatch, capsys) -> None:
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "engage", "computer", "--agent", "--ring", "2"])

    assert args.func(args) == 2
    output = capsys.readouterr().out
    assert "must engage on ring 3" in output
    assert "main-computer data engage computer --agent" in output


def test_data_agent_uses_o3_wallet_account_from_mainnet_deployment(tmp_path, monkeypatch) -> None:
    from main_computer.hub_credit_indexer import wallet_account_id

    o3_address = "0xDb8b11DC5fD60c05764920E280eE41102dFC65F7"
    deployment_dir = tmp_path / "runtime" / "deployments" / "mainnet"
    deployment_dir.mkdir(parents=True)
    (deployment_dir / "latest.json").write_text(
        '{"offices": ['
        '{"office": "O0", "address": "0x0000000000000000000000000000000000000001"},'
        '{"office": "O1", "address": "0x0000000000000000000000000000000000000002"},'
        '{"office": "O2", "address": "0x0000000000000000000000000000000000000003"},'
        '{"office": "O3", "address": "%s"}'
        ']}'
        % o3_address,
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        workspace=None,
        provider="ollama",
        model="gemma4:26b",
        hub_url=None,
        hub_client_node_id=None,
    )

    argv = main_cli._data_god_mode_smoke_argv(
        args,
        ["What", "is", "4", "+", "9", "--god-mode", "--agent"],
    )

    assert argv[argv.index("--ai-hub-client-node-id") + 1] == "main-computer-data-agent-cli"
    assert argv[argv.index("--ai-hub-account-id") + 1] == wallet_account_id(o3_address)


def test_data_agent_worker_pull_spends_o3_account_not_client_node(monkeypatch) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    calls: list[dict[str, object]] = []
    o3_account_id = "0xdb8b11dc5fd60c05764920e280ee41102dfc65f7"

    def fake_open_hub_ai_json(hub_url, path, *, method="GET", payload=None, query=None, timeout_seconds=30.0):
        calls.append({"path": path, "method": method, "payload": payload, "query": query})
        if path == "/api/hub/v1/requests":
            assert payload["client_node_id"] == "main-computer-data-agent-cli"
            assert payload["account_id"] == o3_account_id
            assert payload["metadata"]["client_node_id"] == "main-computer-data-agent-cli"
            assert payload["metadata"]["account_id"] == o3_account_id
            return {"ok": True, "request": {"request_id": "req_o3_account", "state": "queued"}}
        if path == "/api/hub/v1/requests/req_o3_account":
            return {"ok": True, "request": {"request_id": "req_o3_account", "state": "completed"}}
        if path == "/api/hub/v1/requests/req_o3_account/result":
            assert query["account_id"] == o3_account_id
            assert query["client_node_id"] == "main-computer-data-agent-cli"
            return {
                "ok": True,
                "result": {
                    "status": "success",
                    "response": {
                        "content": "{\"answer\":\"13\"}",
                        "provider": "ollama",
                        "model": "gemma4:26b",
                        "metadata": {
                            "worker_pull_v0": True,
                            "requested_ring": 3,
                            "required_ring": 3,
                            "worker_assigned_ring": 3,
                            "answering_ring": 3,
                        },
                    },
                },
            }
        raise AssertionError(f"unexpected hub path: {path}")

    monkeypatch.setattr(smoke, "_open_hub_ai_json", fake_open_hub_ai_json)
    result = smoke.call_hub_ai_json(
        stage="real_agent_byzantine_worker_001",
        system_prompt="return json",
        user_prompt="What is 4 + 9?",
        model="gemma4:26b",
        hub_url="https://mainnet-hub.greatlibrary.io",
        client_node_id="main-computer-data-agent-cli",
        account_id=o3_account_id,
        timeout_seconds=30,
        transport="worker-pull",
    )

    assert result.metadata["hub_client_node_id"] == "main-computer-data-agent-cli"
    assert result.metadata["hub_account_id"] == o3_account_id
    assert [call["path"] for call in calls] == [
        "/api/hub/v1/requests",
        "/api/hub/v1/requests/req_o3_account",
        "/api/hub/v1/requests/req_o3_account/result",
    ]


def test_data_agent_auto_bridge_credit_floor_uses_full_reference_math() -> None:
    smoke_argv = [
        "--real-agent-prompt",
        "Apply now",
        "--god-mode",
        "--ai-provider",
        "hub",
        "--ai-hub-transport",
        "worker-pull",
    ]

    assert main_cli._data_god_mode_auto_bridge_credits(smoke_argv) == 32

    smoke_argv.extend(["--real-agent-worker-count", "5", "--real-agent-reviewer-count", "5"])
    assert main_cli._data_god_mode_auto_bridge_credits(smoke_argv) == 40


def test_data_agent_prefund_runs_before_live_agent_smoke(monkeypatch, tmp_path, capsys) -> None:
    import json
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    report_path = tmp_path / "report.json"
    calls: list[str] = []

    def fake_prefund(args, data_args, smoke_argv):
        calls.append("prefund")
        assert "--scripted-ai-smoke" not in smoke_argv
        assert smoke_argv[smoke_argv.index("--ai-provider") + 1] == "hub"
        return {
            "enabled": True,
            "skipped": False,
            "bridge_credits_display": "32",
            "completion_mode": "complete",
            "transaction_hash": "0x" + "1" * 64,
        }

    def fake_smoke_main(argv: list[str]) -> int:
        calls.append("smoke")
        report = {
            "ok": True,
            "report_path": str(report_path),
            "final_endstate": "answer_only",
            "real_agent_worker_count": 4,
            "real_agent_reviewer_count": 4,
            "expected_byzantine_ai_phase_call_floor": 8,
            "decision": {"answer": "13"},
            "ai_call_summary": {"finished_live_call_count": 8},
            "failed_contracts": [],
            "ai_provider": "hub",
            "ai_hub_url": "https://mainnet-hub.greatlibrary.io",
            "ai_hub_account_id": "0xdb8b11dc5fd60c05764920e280ee41102dfc65f7",
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        print(json.dumps({"event": "real_agent_prompt_smoke_finished", "report_path": str(report_path)}))
        return 0

    monkeypatch.setattr(main_cli, "_data_god_mode_bridge_prefund", fake_prefund)
    monkeypatch.setattr(smoke, "main", fake_smoke_main)
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "What", "is", "4", "+", "9", "--god-mode", "--agent"])

    assert args.func(args) == 0
    assert calls == ["prefund", "smoke"]
    output = capsys.readouterr().out
    assert "bridge: pre-funded 32 credits for Data/O3 via complete" in output
    assert "answer: 13" in output


def test_data_agent_scripted_smoke_does_not_prefund_bridge(monkeypatch) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    calls: list[str] = []

    def fake_prefund(args, data_args, smoke_argv):
        calls.append("prefund")
        return None

    def fake_smoke_main(argv: list[str]) -> int:
        calls.append("smoke")
        return 0

    monkeypatch.setattr(main_cli, "_data_god_mode_bridge_prefund", fake_prefund)
    monkeypatch.setattr(smoke, "main", fake_smoke_main)
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "What", "is", "4", "+", "9", "--god-mode", "--agent", "--scripted-ai-smoke"])

    assert args.func(args) == 0
    assert calls == ["prefund", "smoke"]


def test_captain_engage_computer_stamps_ring_metadata_on_worker_pull_answers(monkeypatch) -> None:
    from main_computer.config import MainComputerConfig
    from main_computer.models import ChatResponse

    base = MainComputerConfig.from_env()
    inferred = replace(
        base,
        provider="ollama",
        model="gemma4:26b",
        hub_worker_node_id="captain-worker-test",
        hub_worker_endpoint="",
    )

    class FakeComputer:
        class Provider:
            def chat(self, messages):
                return ChatResponse(
                    content="{\"answer\":\"13\"}",
                    provider="fake",
                    model="gemma4:26b",
                    metadata={},
                )

        provider = Provider()

    captured: dict[str, object] = {}

    def fake_serve_hub_worker_pull(config, chat, **kwargs):
        response = chat([])
        captured["assigned_ring"] = kwargs["assigned_ring"]
        captured["answering_ring"] = response.metadata.get("answering_ring")
        captured["worker_assigned_ring"] = response.metadata.get("worker_assigned_ring")
        captured["ring3_answer_verified"] = response.metadata.get("ring3_answer_verified")
        captured["command_identity"] = response.metadata.get("command_identity")
        return None

    monkeypatch.setattr(main_cli, "_config_from_args", lambda args: inferred)
    monkeypatch.setattr(main_cli.MainComputer, "build", staticmethod(lambda config: FakeComputer()))
    monkeypatch.setattr(main_cli, "serve_hub_worker_pull", fake_serve_hub_worker_pull)

    parser = main_cli.build_parser()
    args = parser.parse_args(["captain", "smoke", "john", "luc", "picard", "engage", "computer", "--max-requests", "1"])

    assert args.func(args) == 0
    assert captured == {
        "assigned_ring": 3,
        "answering_ring": 3,
        "worker_assigned_ring": 3,
        "ring3_answer_verified": True,
        "command_identity": "captain-engage-computer",
    }
