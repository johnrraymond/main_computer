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
        ]
    )

    assert args.ai_provider == "hub"
    assert args.ai_hub_url == "https://mainnet-hub.greatlibrary.io"
    assert args.ai_hub_client_node_id == "main-computer-data-agent-cli"


def test_hub_provider_sends_explicit_api_headers(monkeypatch) -> None:
    import json
    from main_computer.providers import hub as hub_provider

    captured: dict[str, str] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["user_agent"] = request.get_header("User-agent") or request.get_header("User-Agent") or ""
        captured["client"] = request.get_header("X-main-computer-client") or request.get_header("X-Main-Computer-Client") or ""
        captured["accept"] = request.get_header("Accept") or ""
        return FakeResponse()

    monkeypatch.setattr(hub_provider, "urlopen", fake_urlopen)
    provider = hub_provider.HubProvider(hub_url="https://mainnet-hub.greatlibrary.io")
    provider._post_json("/api/hub/sessions/start", {"model": "hub-auto"})

    assert captured["user_agent"].startswith("main-computer-data-agent-cli/")
    assert captured["client"] == "data-god-mode-agent"
    assert captured["accept"] == "application/json"


def test_data_god_mode_hub_403_prints_compact_failure(monkeypatch, capsys) -> None:
    import main_computer.rag_code_edit_agent_guidance_smoke as smoke

    def fake_smoke_main(argv: list[str]) -> int:
        raise RuntimeError(
            "Hub request failed for https://mainnet-hub.greatlibrary.io/api/hub/sessions/start "
            "with HTTP 403: error code: 1010"
        )

    monkeypatch.setattr(smoke, "main", fake_smoke_main)
    parser = main_cli.build_parser()
    args = parser.parse_args(["data", "What", "is", "4", "+", "9", "--god-mode", "--agent"])

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

    class FakeComputer:
        class Provider:
            def chat(self, messages):
                return None

        provider = Provider()

    captured: dict[str, object] = {}

    def fake_serve_hub_worker_pull(config, chat, **kwargs):
        captured["model"] = config.model
        captured["hub_url"] = kwargs["hub_url"]
        captured["assigned_ring"] = kwargs["assigned_ring"]
        captured["max_requests"] = kwargs["max_requests"]
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
    args = parser.parse_args(["data", "What", "is", "4", "+", "9", "--god-mode", "--agent"])

    assert args.func(args) == 2
    output = capsys.readouterr().out
    assert "Data god-mode: FAILED" in output
    assert "no matching Data/O3 agent worker" in output
    assert "main-computer data engage computer --agent" in output
    assert "--model gemma4:26b" not in output
    assert "--hub-url https://mainnet-hub.greatlibrary.io" not in output
